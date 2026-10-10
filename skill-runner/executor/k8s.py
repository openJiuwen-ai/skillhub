# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""k8s executor：每 session 一个 pod，agent 在 pod 内运行。

控制面只做：起 pod / 等 Ready / 推 skill / 转发 /run_turn 事件流 / 删 pod。
agent 实例化与运行在 pod 内的 worker，真 LLM key 不进 pod（经 llm_proxy 代理）。
"""
from __future__ import annotations

import json
import logging
import secrets
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urljoin

import httpx

from ..config import instance_base_url, settings
from ..llm_proxy import token_registry
from ..models import Session, sse_event
from . import SandboxExecutor
from .pod_manager import K8sPodManager, PodManager

logger = logging.getLogger("skill_runner.k8s")

_POD_SCHEME = "k8spod://"


class K8sExecutor(SandboxExecutor):
    def __init__(
        self,
        pod_manager: PodManager | None = None,
        pod_client: httpx.AsyncClient | None = None,
        pool=None,
    ) -> None:
        self._pods = pod_manager or K8sPodManager()
        self._pod_client = pod_client
        # pool_size>0 → 预热复用池；=0 → 即用即弃。
        self._pool = pool
        if self._pool is None and settings.pool_size > 0:
            from .pod_pool import PodPoolManager
            self._pool = PodPoolManager(
                self._pods, self._pool_env, settings.pool_size, settings.pool_max_idle
            )

    def _client(self) -> httpx.AsyncClient:
        if self._pod_client is None:
            self._pod_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=settings.llm_timeout_seconds + 30, write=10.0, pool=10.0),
            )
        return self._pod_client

    # ------------------------------------------------------------------
    @staticmethod
    def _callback_base() -> str:
        """worker 回连控制面的基址。

        多实例时必须用本实例地址：per-pod token 只在签发它的实例内存里可解析，
        经统一 Service 轮询会打到别的副本被 401。单实例用 Service 地址（旧行为）。
        """
        return instance_base_url() or settings.proxy_base_url

    def _pod_env(self, session: Session, token: str) -> dict[str, str]:
        return {
            "SESSION_ID": session.session_id,
            "SESSION_TOKEN": token,
            "SKILL_TYPE": session.skill_type,
            "LLM_API_BASE": urljoin(self._callback_base(), "/internal/llm"),
            "LLM_API_KEY": token,
            "LLM_MODEL_NAME": settings.llm_model_name,
            "LLM_PROVIDER": settings.llm_provider,
            "SKILL_RUNNER_ALLOW_DEBUG_SHELL": "true" if settings.allow_debug_shell else "false",
        }

    def _pool_env(self, worker_token: str, llm_token: str) -> dict[str, str]:
        return {
            "SESSION_ID": "pooled",
            "LLM_API_BASE": urljoin(self._callback_base(), "/internal/llm"),
            "LLM_API_KEY": llm_token,
            "LLM_MODEL_NAME": settings.llm_model_name,
            "LLM_PROVIDER": settings.llm_provider,
            "WORKER_AUTH_TOKEN": worker_token,
            "SKILL_RUNNER_ALLOW_DEBUG_SHELL": "true" if settings.allow_debug_shell else "false",
        }

    @staticmethod
    def _skill_payload(session: Session) -> dict[str, Any]:
        bundle = session.extra.get("skill_bundle")
        if bundle is not None:
            import base64

            pkg = getattr(bundle, "package_bytes", b"") or b""
            return {
                "system_prompt": session.system_prompt,
                "skill_type": session.skill_type,
                "skill_md": getattr(bundle, "skill_md", ""),
                "workflow_md": getattr(bundle, "workflow_md", ""),
                "roles": getattr(bundle, "roles", {}),
                "team_mode": getattr(bundle, "team_mode", ""),
                "package_bytes_b64": base64.b64encode(pkg).decode("ascii") if pkg else "",
            }
        return {"system_prompt": session.system_prompt, "skill_type": session.skill_type}

    def _endpoint(self, session: Session) -> str | None:
        ws = session.workspace or ""
        if ws.startswith(_POD_SCHEME):
            return ws[len(_POD_SCHEME):] or None
        return None

    # ------------------------------------------------------------------
    async def _provision(self, session: Session, endpoint: str, worker_token: str) -> None:
        resp = await self._client().post(
            f"http://{endpoint}/provision",
            json=self._skill_payload(session),
            headers={"Authorization": f"Bearer {worker_token}"},
            timeout=120,
        )
        resp.raise_for_status()

    async def create(self, session: Session) -> None:
        if self._pool is not None:
            await self._create_pooled(session)
        else:
            await self._create_ephemeral(session)

    async def _create_ephemeral(self, session: Session) -> None:
        token = token_registry.issue(session.session_id, getattr(session, "user_id", ""))
        worker_token = secrets.token_urlsafe(32)
        session.extra["pod_token"] = token
        session.extra["worker_auth_token"] = worker_token
        env = self._pod_env(session, token)
        env["WORKER_AUTH_TOKEN"] = worker_token

        name = await self._pods.create_pod(session.session_id, env)
        session.extra["pod_name"] = name
        try:
            pod_ip = await self._pods.wait_ready(name, settings.pod_ready_timeout_seconds)
        except Exception:
            token_registry.revoke_for_session(session.session_id)
            try:
                await self._pods.delete_pod(name)
            except Exception:
                logger.warning("cleanup delete_pod after wait_ready failure also failed: %s", name)
            raise
        session.workspace = f"{_POD_SCHEME}{pod_ip}:{settings.pod_port}"
        logger.info("pod ready: session=%s pod=%s ip=%s", session.session_id, name, pod_ip)

        try:
            await self._provision(session, self._endpoint(session), worker_token)
        except Exception:
            logger.exception("provision skill to pod failed: session=%s", session.session_id)
            token_registry.revoke_for_session(session.session_id)
            try:
                await self._pods.delete_pod(name)
            except Exception:
                logger.warning("cleanup delete_pod after provision failure also failed: %s", name)
            raise

    async def _create_pooled(self, session: Session) -> None:
        lease = await self._pool.acquire()
        session.extra["pod_lease"] = lease
        session.extra["pod_name"] = lease.name
        session.extra["worker_auth_token"] = lease.worker_token
        session.extra["pod_token"] = lease.llm_token
        token_registry.bind(lease.llm_token, session.session_id, getattr(session, "user_id", ""))
        session.workspace = f"{_POD_SCHEME}{lease.endpoint}"
        logger.info("pod acquired: session=%s pod=%s ip=%s", session.session_id, lease.name, lease.ip)
        try:
            await self._provision(session, lease.endpoint, lease.worker_token)
        except Exception:
            logger.exception("provision (pooled) failed: session=%s", session.session_id)
            token_registry.unbind(lease.llm_token)
            session.extra.pop("pod_lease", None)
            await self._pool.release(lease, healthy=False)
            raise

    async def run_turn(self, session: Session, message: str) -> AsyncIterator[dict[str, Any]]:
        endpoint = self._endpoint(session)
        if endpoint is None:
            yield sse_event("error", code="no_pod", message="pod endpoint not ready")
            return
        client = self._client()
        try:
            async with client.stream(
                "POST",
                f"http://{endpoint}/run_turn",
                json={"query": message},
                headers={"Authorization": f"Bearer {session.extra.get('worker_auth_token', '')}"},
            ) as resp:
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode("utf-8", "replace")[:500]
                    yield sse_event("error", code="pod_http_error", status=resp.status_code, message=text)
                    return
                done_seen = False
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        yield sse_event("raw", payload=line[:500])
                        continue
                    if ev.get("type") == "done":
                        done_seen = True
                    # keepalive 透传保活每一跳
                    yield ev
        except httpx.RemoteProtocolError as exc:
            # 已收到 done 后的 RemoteProtocolError 视为正常截断，降级为 session_ended
            detail = str(exc) or type(exc).__name__
            if done_seen and ("incomplete" in detail.lower() or "peer closed" in detail.lower()):
                logger.warning("k8s stream truncated (likely max_tokens): %s", detail)
                yield sse_event("session_ended", reason="stream_truncated")
            else:
                logger.warning("k8s stream error before done: %s", detail)
                yield sse_event("error", code="pod_unreachable", message=f"pod stream error: {detail}")
        except httpx.HTTPError as exc:
            detail = str(exc) or type(exc).__name__
            yield sse_event("error", code="pod_unreachable", message=f"pod stream error: {detail}")

    async def put_file(self, session: Session, filename: str, content: bytes) -> dict[str, Any]:
        endpoint = self._endpoint(session)
        if endpoint is None:
            raise RuntimeError("pod endpoint not ready")
        import base64

        resp = await self._client().post(
            f"http://{endpoint}/upload",
            json={"filename": filename, "content_b64": base64.b64encode(content).decode("ascii")},
            headers={"Authorization": f"Bearer {session.extra.get('worker_auth_token', '')}"},
            timeout=60,
        )
        if resp.status_code == 400:
            raise ValueError((resp.text or "")[:200])
        resp.raise_for_status()
        return resp.json()

    async def destroy(self, session: Session) -> None:
        if session.extra.get("pod_lease") is not None:
            await self._destroy_pooled(session)
        else:
            await self._destroy_ephemeral(session)

    async def _destroy_ephemeral(self, session: Session) -> None:
        token_registry.revoke_for_session(session.session_id)
        name = session.extra.pop("pod_name", None)
        session.extra.pop("pod_token", None)
        session.extra.pop("worker_auth_token", None)
        if name is not None:
            await self._pods.delete_pod(name)

    async def _destroy_pooled(self, session: Session) -> None:
        lease = session.extra.pop("pod_lease", None)
        token = session.extra.pop("pod_token", None)
        session.extra.pop("worker_auth_token", None)
        session.extra.pop("pod_name", None)
        if token:
            token_registry.unbind(token)
        if lease is None:
            return
        healthy = True
        try:
            resp = await self._client().post(
                f"http://{lease.endpoint}/reset",
                headers={"Authorization": f"Bearer {lease.worker_token}"},
                timeout=30,
            )
            resp.raise_for_status()
        except Exception:
            logger.warning("pod reset failed, not reusing: session=%s pod=%s", session.session_id, lease.name)
            healthy = False
        await self._pool.release(lease, healthy=healthy)

    async def warm(self) -> None:
        if self._pool is not None:
            await self._pool.warm()

    async def start_pod_reaper(self, interval_seconds: int = 120) -> None:
        """定期清理孤儿 pod（裸 Pod 不支持 ttlSecondsAfterFinished）。"""
        import asyncio

        logger.info("pod reaper started: namespace=%s interval=%ds", settings.k8s_namespace, interval_seconds)
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await self._reap_pods()
            except Exception:
                logger.exception("pod reaper error (non-fatal)")

    async def _reap_pods(self) -> None:
        reaped = await self._pods.reap_finished_pods()
        if reaped:
            logger.info("pod reaper: reaped %d pod(s)", reaped)

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.shutdown()
        if self._pod_client is not None:
            await self._pod_client.aclose()
            self._pod_client = None
        pods_close = getattr(self._pods, "aclose", None)
        if pods_close is not None:
            await pods_close()
