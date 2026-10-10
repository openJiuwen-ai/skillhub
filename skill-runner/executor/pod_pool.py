# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Worker Pod 预热池：warm pod 跨 session 复用。
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from ..config import settings
from .pod_manager import PodManager

_log = logging.getLogger(__name__)

EnvBuilder = Callable[[str, str], dict]


@dataclass
class PodLease:
    name: str
    ip: str
    worker_token: str
    llm_token: str
    # pod 创建时刻（monotonic）；activeDeadlineSeconds 从创建计时，acquire 据此估算剩余寿命
    created_at: float = field(default_factory=time.monotonic)

    @property
    def endpoint(self) -> str:
        return f"{self.ip}:{settings.pod_port}"

    def remaining_lifetime(self) -> float:
        """距 activeDeadlineSeconds 被 K8s 强杀的剩余秒数。"""
        return settings.session_max_lifetime_seconds - (time.monotonic() - self.created_at)


class PodPoolManager:
    """空闲 warm pod 池。控制面单副本、asyncio 单线程事件循环下无需加锁。"""

    def __init__(
        self,
        pod_manager: PodManager,
        env_builder: EnvBuilder,
        pool_size: int,
        max_idle: int,
    ) -> None:
        self._pods = pod_manager
        self._env_builder = env_builder
        self._pool_size = pool_size
        self._max_idle = max(max_idle, pool_size)
        self._idle: list[PodLease] = []
        # 探活复用同一个 client，避免每次 acquire 都新建连接池
        self._probe_client: httpx.AsyncClient | None = None

    def idle_count(self) -> int:
        return len(self._idle)

    async def _spawn(self) -> PodLease:
        worker_token = secrets.token_urlsafe(32)
        llm_token = "pod-" + secrets.token_urlsafe(24)
        env = self._env_builder(worker_token, llm_token)
        pod_id = f"pool-{uuid.uuid4().hex[:10]}"
        name = await self._pods.create_pod(pod_id, env)
        ip = await self._pods.wait_ready(name, settings.pod_ready_timeout_seconds)
        return PodLease(name=name, ip=ip, worker_token=worker_token, llm_token=llm_token)

    async def warm(self) -> None:
        """补到 pool_size 个空闲 warm pod。"""
        while len(self._idle) < self._pool_size:
            self._idle.append(await self._spawn())

    async def _is_alive(self, lease: PodLease, timeout: float = 2.0) -> bool:
        """轻量探活：acquire 前剔除死 lease（pod 被外部删 / OOM / 集群重启等场景，
        内存 idle 里残留死引用）。复用 _probe_client。"""
        if self._probe_client is None:
            self._probe_client = httpx.AsyncClient(timeout=timeout)
        try:
            resp = await self._probe_client.get(f"http://{lease.endpoint}/healthz")
            return resp.status_code < 500
        except Exception:
            return False

    async def acquire(self, timeout: float = 180) -> PodLease:
        """取一个 warm pod；池空或 idle pod 已死则按需新建。

        死 lease 自愈：pop 出来先探活，活的返回，死的丢弃 + best-effort 删 pod。
        超时抛 TimeoutError，防止全部 pod 死亡 + spawn 失败时无限阻塞。
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while self._idle:
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("acquire timed out: idle pods all dead")
            lease = self._idle.pop()
            remaining = lease.remaining_lifetime()
            if remaining < settings.pool_min_lease_lifetime_seconds:
                # 剩余寿命不足：预热等待耗掉了太多 activeDeadline 预算，交给会话会中途被强杀。
                # 弃用换新（下方 _spawn），避免用户体验到"会话跑到一半 pod 被杀"。
                _log.info(
                    "discarding pool lease %s: only %.0fs lifetime left (< %ds)",
                    lease.name, remaining, settings.pool_min_lease_lifetime_seconds,
                )
                await self._pods.delete_pod(lease.name)
                continue
            if await self._is_alive(lease):
                return lease
            _log.warning(
                "evicting dead lease from pool: name=%s ip=%s endpoint=%s",
                lease.name, lease.ip, lease.endpoint,
            )
            try:
                await self._pods.delete_pod(lease.name)
            except Exception as exc:
                # warning 而非 debug：删除失败该 pod 即成孤儿、持续占集群配额，
                # 生产日志必须可见，运维才有机会巡检清理
                _log.warning("delete of dead pod %s failed, pod may be orphaned: %s", lease.name, exc)
        remaining_timeout = max(0, deadline - asyncio.get_event_loop().time())
        return await asyncio.wait_for(self._spawn(), timeout=remaining_timeout)

    async def release(self, lease: PodLease, *, healthy: bool = True) -> None:
        """归还：健康且未超 max_idle 则回池，否则销毁。"""
        if healthy and len(self._idle) < self._max_idle:
            self._idle.append(lease)
            return
        await self._pods.delete_pod(lease.name)

    async def shutdown(self) -> None:
        leases, self._idle = self._idle[:], []
        for lease in leases:
            await self._pods.delete_pod(lease.name)
        if self._probe_client is not None:
            await self._probe_client.aclose()
            self._probe_client = None
