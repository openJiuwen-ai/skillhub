# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Playground 状态存储：单实例=内存，多实例=Redis。"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from plugins_market.core.config import settings
from plugins_market.core.logging import get_logger
from plugins_market.core.rate_limit import SlidingWindowRateLimiter

logger = get_logger(__name__)


@dataclass
class PlaygroundSessionRecord:
    user_id: str
    token: str = ""        # stream/beacon 能力令牌（?pt=）
    runner_addr: str = ""  # 会话所在 skill-runner 实例基址；空 = 用统一 Service 地址


class CreateLockTimeout(Exception):
    """分布式创建锁等待超时（仅 Redis 实现会抛；内存实现无限等待与旧行为一致）。"""


class PlaygroundStateStore(Protocol):
    async def register_session(
        self, session_id: str, record: PlaygroundSessionRecord, ttl_seconds: int
    ) -> None:
        ...

    async def get_session(self, session_id: str) -> PlaygroundSessionRecord | None:
        ...

    async def release_session(self, session_id: str) -> None:
        ...

    async def list_user_sessions(self, user_id: str) -> list[str]:
        ...

    def create_lock(self, user_id: str) -> "AsyncIterator[None]":
        ...

    async def allow_message(self, user_id: str, limit: int, window_seconds: float) -> bool:
        ...


# ── 单实例：进程内存实现（与旧 playground_proxy 模块级 dict 语义一致） ────────────


class MemoryPlaygroundStateStore:
    def __init__(self) -> None:
        self._sessions: dict[str, PlaygroundSessionRecord] = {}
        self._user_sessions: dict[str, set[str]] = {}
        self._create_locks: dict[str, list] = {}  # user_id → [asyncio.Lock, 引用计数]
        self._msg_limiter = SlidingWindowRateLimiter()

    async def register_session(
        self, session_id: str, record: PlaygroundSessionRecord, ttl_seconds: int
    ) -> None:
        # 内存实现无 TTL：清理由 DELETE/beacon 负责
        self._sessions[session_id] = record
        self._user_sessions.setdefault(record.user_id, set()).add(session_id)

    async def get_session(self, session_id: str) -> PlaygroundSessionRecord | None:
        return self._sessions.get(session_id)

    async def release_session(self, session_id: str) -> None:
        record = self._sessions.pop(session_id, None)
        if record is None:
            return
        sids = self._user_sessions.get(record.user_id)
        if sids is not None:
            sids.discard(session_id)
            if not sids:
                self._user_sessions.pop(record.user_id, None)

    async def list_user_sessions(self, user_id: str) -> list[str]:
        return sorted(self._user_sessions.get(user_id, set()))

    @asynccontextmanager
    async def create_lock(self, user_id: str):
        """per-user 创建锁；引用计数保证最后一个使用者离开时清掉条目。"""
        entry = self._create_locks.setdefault(user_id, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0 and self._create_locks.get(user_id) is entry:
                self._create_locks.pop(user_id, None)

    async def allow_message(self, user_id: str, limit: int, window_seconds: float) -> bool:
        return self._msg_limiter.allow(
            f"pg-msg:{user_id}", limit=limit, window_sec=window_seconds
        )


# ── 多实例：Redis 实现 ─────────────────────────────────────────────────────────

_KEY_SESSION = "pg:sess:{sid}"       # hash: user_id / token / runner
_KEY_USER_SESSIONS = "pg:usess:{uid}"  # set of session_id
_KEY_CREATE_LOCK = "pg:clk:{uid}"    # 分布式创建锁（value = 持有者随机串）
_KEY_MSG_WINDOW = "pg:msg:{uid}:{win}"  # 固定窗口消息计数

# 创建锁参数：TTL 防实例崩溃死锁；等待超时防用户被慢请求长期阻塞。
# 锁只是 TOCTOU 缓解（配额本身由 MySQL FOR UPDATE 保证原子），拿不到锁快速失败即可。
_CREATE_LOCK_TTL_SECONDS = 60
_CREATE_LOCK_WAIT_SECONDS = 30.0
_CREATE_LOCK_POLL_SECONDS = 0.05

# 释放锁必须校验持有者，避免 TTL 过期后误删他人新锁
_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class RedisPlaygroundStateStore:
    def __init__(self, *, host: str, port: int, db: int, password: str, ssl: bool = False) -> None:
        from redis import asyncio as aioredis

        kwargs: dict = {
            "host": host,
            "port": port,
            "db": db,
            "decode_responses": True,
            # 云上 LB/DCS 会静默丢弃空闲连接：不设超时会在坏连接上永久阻塞
            "socket_connect_timeout": 2.0,
            "socket_timeout": 2.0,
            "health_check_interval": 30,
        }
        if password:
            kwargs["password"] = password
        if ssl:
            kwargs["ssl"] = True
        self._r = aioredis.Redis(**kwargs)

    async def register_session(
        self, session_id: str, record: PlaygroundSessionRecord, ttl_seconds: int
    ) -> None:
        skey = _KEY_SESSION.format(sid=session_id)
        ukey = _KEY_USER_SESSIONS.format(uid=record.user_id)
        pipe = self._r.pipeline()
        pipe.hset(skey, mapping={
            "user_id": record.user_id,
            "token": record.token,
            "runner": record.runner_addr,
        })
        pipe.expire(skey, ttl_seconds)
        pipe.sadd(ukey, session_id)
        pipe.expire(ukey, ttl_seconds)  # 每次注册刷新，集合比成员至少活得一样久
        await pipe.execute()

    async def get_session(self, session_id: str) -> PlaygroundSessionRecord | None:
        data = await self._r.hgetall(_KEY_SESSION.format(sid=session_id))
        if not data:
            return None
        return PlaygroundSessionRecord(
            user_id=data.get("user_id", ""),
            token=data.get("token", ""),
            runner_addr=data.get("runner", ""),
        )

    async def release_session(self, session_id: str) -> None:
        record = await self.get_session(session_id)
        pipe = self._r.pipeline()
        pipe.delete(_KEY_SESSION.format(sid=session_id))
        if record is not None and record.user_id:
            pipe.srem(_KEY_USER_SESSIONS.format(uid=record.user_id), session_id)
        await pipe.execute()

    async def list_user_sessions(self, user_id: str) -> list[str]:
        ukey = _KEY_USER_SESSIONS.format(uid=user_id)
        sids = await self._r.smembers(ukey)
        if not sids:
            return []
        alive: list[str] = []
        stale: list[str] = []
        for sid in sids:
            if await self._r.exists(_KEY_SESSION.format(sid=sid)):
                alive.append(sid)
            else:
                stale.append(sid)  # 会话记录 TTL 已过期，集合里的残留成员顺手清掉
        if stale:
            await self._r.srem(ukey, *stale)
        return sorted(alive)

    @asynccontextmanager
    async def create_lock(self, user_id: str):
        key = _KEY_CREATE_LOCK.format(uid=user_id)
        owner = uuid.uuid4().hex
        deadline = time.monotonic() + _CREATE_LOCK_WAIT_SECONDS
        while True:
            if await self._r.set(key, owner, nx=True, ex=_CREATE_LOCK_TTL_SECONDS):
                break
            if time.monotonic() >= deadline:
                raise CreateLockTimeout(f"create lock wait timeout for user {user_id}")
            await asyncio.sleep(_CREATE_LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            try:
                await self._r.eval(_RELEASE_LOCK_LUA, 1, key, owner)
            except Exception as exc:
                # 释放失败不影响正确性
                logger.warning("playground create lock release failed for %s: %s", user_id, exc)

    async def allow_message(self, user_id: str, limit: int, window_seconds: float) -> bool:
        if limit <= 0:
            return True
        window = max(1, int(window_seconds))
        win_id = int(time.time()) // window
        key = _KEY_MSG_WINDOW.format(uid=user_id, win=win_id)
        count = await self._r.incr(key)
        if count == 1:
            await self._r.expire(key, window + 1)
        return count <= limit


# ── 工厂：按开关选择实现（进程内单例） ─────────────────────────────────────────

_store: PlaygroundStateStore | None = None


def _resolve_redis_password() -> str:
    from common.security.security_utils import SecurityUtils

    return (SecurityUtils.get_decrypt_secret("MARKET_REDIS_PASSWORD", default="") or "").strip()


def get_playground_state_store() -> PlaygroundStateStore:
    global _store
    if _store is None:
        if settings.playground_multi_instance:
            host = (settings.redis_host or "").strip()
            if not host:
                # 显式开了多实例但没配 Redis：配置错误必须快速失败，
                # 静默退回内存会让并发/归属/令牌校验在多副本下悄悄失效
                raise RuntimeError(
                    "PLAYGROUND_MULTI_INSTANCE=true 需要配置 REDIS_HOST/MARKET_REDIS_HOST；"
                    "未配置时请保持开关关闭（单实例内存模式）"
                )
            from plugins_market.core.redis_client import resolve_redis_ssl

            ssl = resolve_redis_ssl()
            _store = RedisPlaygroundStateStore(
                host=host,
                port=int(settings.redis_port),
                db=int(settings.redis_db),
                password=_resolve_redis_password(),
                ssl=ssl,
            )
            logger.info(
                "playground state store: redis (host=%s port=%s db=%s ssl=%s)",
                host, settings.redis_port, settings.redis_db, ssl,
            )
        else:
            _store = MemoryPlaygroundStateStore()
            logger.info("playground state store: in-process memory (single instance)")
    return _store
