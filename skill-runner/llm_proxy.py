# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""控制面 LLM 代理：pod 用 per-session token 调本代理，代理换上真 key 转发上游。"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Protocol

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .config import settings

logger = logging.getLogger("skill_runner.llm_proxy")


_llm_semaphore: asyncio.Semaphore | None = None


def _get_llm_semaphore() -> asyncio.Semaphore | None:
    global _llm_semaphore
    n = settings.llm_max_concurrency
    if n <= 0:
        return None
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(n)
    return _llm_semaphore


class TokenRegistry:
    """token <-> (session_id, user_id) 映射，进程内（控制面单副本运行）。"""

    def __init__(self) -> None:
        self._token_to_session: dict[str, str] = {}
        self._token_to_user: dict[str, str] = {}

    def issue(self, session_id: str, user_id: str = "") -> str:
        token = "pod-" + secrets.token_urlsafe(24)
        self._token_to_session[token] = session_id
        if user_id:
            self._token_to_user[token] = user_id
        return token

    def resolve(self, token: str) -> str | None:
        return self._token_to_session.get(token)

    def resolve_user(self, token: str) -> str:
        return self._token_to_user.get(token, "")

    def revoke_for_session(self, session_id: str) -> None:
        for tok in [t for t, s in self._token_to_session.items() if s == session_id]:
            self._token_to_session.pop(tok, None)
            self._token_to_user.pop(tok, None)

    # 池复用：per-pod token 不重发，按 session 重新绑定 / 解绑
    def bind(self, token: str, session_id: str, user_id: str = "") -> None:
        self._token_to_session[token] = session_id
        if user_id:
            self._token_to_user[token] = user_id

    def unbind(self, token: str) -> None:
        self._token_to_session.pop(token, None)
        self._token_to_user.pop(token, None)


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# 每用户每日 token 计量：单实例进程内存，多实例 Redis。近似限流（转发前查、流
# 结束后累加），高并发下可能小幅超限。


class UserBudgetStore(Protocol):
    async def check(self, user_id: str, limit: int) -> bool:
        """True=仍在预算内（可继续调 LLM）。"""
        ...

    async def add(self, user_id: str, tokens: int) -> None:
        ...


class MemoryUserBudgetStore:
    """单实例：进程内 {user_id: {"date","tokens"}}，跨 UTC 日替换旧条目防无界增长。"""

    def __init__(self) -> None:
        self._tokens: dict[str, dict] = {}

    async def check(self, user_id: str, limit: int) -> bool:
        if not user_id or limit <= 0:
            return True
        entry = self._tokens.get(user_id)
        if entry and entry["date"] != _utc_today():
            self._tokens.pop(user_id, None)  # 旧日期条目过期即清
            return True
        return not (entry and entry["tokens"] >= limit)

    async def add(self, user_id: str, tokens: int) -> None:
        if not user_id or tokens <= 0:
            return
        today = _utc_today()
        entry = self._tokens.get(user_id)
        if entry and entry["date"] == today:
            entry["tokens"] += tokens
        else:
            self._tokens[user_id] = {"date": today, "tokens": tokens}


class RedisUserBudgetStore:
    """多实例：计量外置 Redis，key=srun:token:{uid}:{utc-date}；跨日靠日期 key+TTL 自动重置。

    计量尽力而为：Redis 超时/异常不得阻塞 LLM 数据面——check 放行、add 丢弃，仅记 warning。
    """

    _TTL_SECONDS = 90000  # 25h：够覆盖一个 UTC 日，过期即自动回收
    _OP_TIMEOUT = 2.0     # 单次 Redis 操作硬上限，兜底连接被静默丢弃的场景

    def __init__(self, client) -> None:
        self._r = client

    @staticmethod
    def _key(user_id: str) -> str:
        return f"srun:token:{user_id}:{_utc_today()}"

    async def check(self, user_id: str, limit: int) -> bool:
        if not user_id or limit <= 0:
            return True
        try:
            used = await asyncio.wait_for(self._r.get(self._key(user_id)), self._OP_TIMEOUT)
        except Exception as exc:
            logger.warning("budget check redis error, fail-open: %s: %s", type(exc).__name__, exc)
            return True
        return int(used or 0) < limit

    async def add(self, user_id: str, tokens: int) -> None:
        if not user_id or tokens <= 0:
            return
        key = self._key(user_id)
        try:
            new_total = await asyncio.wait_for(self._r.incrby(key, tokens), self._OP_TIMEOUT)
            if new_total == tokens:  # 当日首次写入，设 TTL
                await asyncio.wait_for(self._r.expire(key, self._TTL_SECONDS), self._OP_TIMEOUT)
        except Exception as exc:
            logger.warning("budget add redis error, drop %d tokens for %s: %s: %s",
                           tokens, user_id, type(exc).__name__, exc)


_budget_store: UserBudgetStore | None = None


def get_user_budget_store() -> UserBudgetStore:
    """按 multi_instance 选实现（进程内单例）；多实例但未配 Redis 时 fail-fast。"""
    global _budget_store
    if _budget_store is None:
        if settings.multi_instance:
            host = (settings.redis_host or "").strip()
            if not host:
                raise RuntimeError(
                    "SKILL_RUNNER_MULTI_INSTANCE=true 需要配置 SKILL_RUNNER_REDIS_HOST；"
                    "否则每用户每日 token 预算会按副本各算一份、全局限额失效"
                )
            from redis import asyncio as aioredis

            kwargs: dict = {
                "host": host,
                "port": int(settings.redis_port),
                "db": int(settings.redis_db),
                "decode_responses": True,
                # 云上 LB/DCS 会静默丢弃空闲连接：不设超时会在坏连接上永久阻塞；
                # health_check 让连接池复用前先探活
                "socket_connect_timeout": 2.0,
                "socket_timeout": 2.0,
                "health_check_interval": 30,
            }
            if settings.redis_password:
                kwargs["password"] = settings.redis_password
            _budget_store = RedisUserBudgetStore(aioredis.Redis(**kwargs))
            logger.info("user budget store: redis (host=%s port=%s db=%s)",
                        host, settings.redis_port, settings.redis_db)
        else:
            _budget_store = MemoryUserBudgetStore()
            logger.info("user budget store: in-process memory (single instance)")
    return _budget_store


def reset_user_budget_store_for_testing(store: "UserBudgetStore | None" = None) -> None:
    """测试钩子：注入/清空预算 store 单例。生产代码不应调用。"""
    global _budget_store
    _budget_store = store


def _extract_total_tokens(data: bytes) -> int:
    """从完整响应字节提取 usage.total_tokens；兼容纯 JSON 体与 SSE data: 行两种形态。

    供非流式响应（StreamConsumed 分支）计量用——它不经过逐行扫描，
    不提取会绕过用户/会话 token 预算。解析失败返回 0，计量尽力而为不影响转发。
    """
    if not data:
        return 0
    candidates: list[bytes] = []
    stripped = data.strip()
    if stripped.startswith(b"{"):
        candidates.append(stripped)
    for ln in data.split(b"\n"):
        ln = ln.strip()
        if ln.startswith(b"data: ") and ln != b"data: [DONE]":
            candidates.append(ln[6:])
    best = 0
    for raw in candidates:
        try:
            obj = json.loads(raw)
            tt = (obj.get("usage") or {}).get("total_tokens")
            if tt:
                best = max(best, int(tt))
        except (ValueError, TypeError, AttributeError):
            continue
    return best


# ── 每会话 token 累计────────────────────────────────────────
# 控制面代理看得见每一次真实 LLM 调用，是权威计数源
_session_tokens: dict[str, int] = {}


def _add_session_tokens(session_id: str, tokens: int) -> int:
    """累加并返回该会话当前累计 token。"""
    total = _session_tokens.get(session_id, 0) + max(tokens, 0)
    _session_tokens[session_id] = total
    return total


def clear_session_tokens(session_id: str) -> None:
    """会话结束时清理，防止字典无界增长。"""
    _session_tokens.pop(session_id, None)


token_registry = TokenRegistry()

proxy_router = APIRouter(tags=["internal-llm-proxy"])

# 懒建；测试可替换本模块的 _forward_client
_forward_client: httpx.AsyncClient | None = None


def get_forward_client() -> httpx.AsyncClient:
    global _forward_client
    if _forward_client is None:
        _forward_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=5.0, read=settings.llm_timeout_seconds, write=10.0, pool=10.0
            ),
            verify=settings.llm_verify_ssl,
        )
    return _forward_client


async def aclose() -> None:
    global _forward_client
    if _forward_client is not None:
        await _forward_client.aclose()
        _forward_client = None


def set_forward_client_for_testing(client: httpx.AsyncClient) -> None:
    """测试钩子：注入 mock 的上游 HTTP client。生产代码不应调用。"""
    global _forward_client
    _forward_client = client


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


# 逐跳头部：转发时两侧都必须剔除，由各自 HTTP 连接自行协商。
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
})
_REQUEST_STRIP = _HOP_BY_HOP | {"host", "authorization"}


@proxy_router.api_route("/internal/llm/{subpath:path}", methods=["POST", "GET"])
async def llm_proxy(subpath: str, request: Request) -> StreamingResponse:
    token = _bearer(request)
    session_id = token_registry.resolve(token) if token else None
    if token is None or session_id is None:
        raise HTTPException(status_code=401, detail="invalid or missing pod token")

    # 每日 token 预算检查，在 key 校验后避免 auth bypass，在转发前避免多余 I/O
    user_id = token_registry.resolve_user(token)
    if user_id and not await get_user_budget_store().check(user_id, settings.user_daily_token_limit):
        raise HTTPException(
            status_code=429,
            detail=f"daily token limit {settings.user_daily_token_limit} exceeded",
        )

    if not settings.llm_api_key:
        raise HTTPException(status_code=503, detail="control-plane LLM key not configured")

    upstream = settings.llm_api_base.rstrip("/") + "/" + subpath
    body = await request.body()
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _REQUEST_STRIP
    }
    fwd_headers["authorization"] = f"Bearer {settings.llm_api_key}"

    # Detect if this is a tool_call phase (has 'tools' or 'tool_choice' in request)
    is_tool_call = False
    try:
        body_dict = json.loads(body) if body else {}
        is_tool_call = bool(body_dict.get('tools') or body_dict.get('tool_choice'))
    except (json.JSONDecodeError, AttributeError):
        pass

    sem = _get_llm_semaphore()
    if sem is not None:
        await sem.acquire()
    _sem_released = False

    def _release_sem() -> None:
        nonlocal _sem_released
        if sem is not None and not _sem_released:
            _sem_released = True
            sem.release()

    client = get_forward_client()
    upstream_req = client.build_request(
        request.method,
        upstream,
        params=request.query_params,
        content=body,
        headers=fwd_headers,
    )

    # 上游 SSL/连接错误重试一次；二次失败统一返回显式 error 数据行 + [DONE]，
    # OpenAI 流式客户端遇 error 行会抛错，turn 以明确失败收场，可重试可感知。
    import ssl
    resp = None
    for attempt in range(1, 3):
        try:
            resp = await client.send(upstream_req, stream=True)
            break
        except (httpx.HTTPError, ssl.SSLError) as exc:
            if attempt == 1:
                logger.warning(
                    "upstream LLM send failed (attempt 1): %s: %s; retrying once",
                    type(exc).__name__, exc
                )
            else:
                logger.warning(
                    "upstream LLM send failed (attempt 2, giving up): %s: %s; "
                    "returning error stream to worker (phase=%s)",
                    type(exc).__name__, exc,
                    "tool_call" if is_tool_call else "answer",
                )

                _release_sem()  # 未建立上游连接，立即释放槽位

                async def _error_stream():
                    yield b"data: {\"error\": \"upstream LLM connection failed\"}\n\n"
                    yield b"data: [DONE]\n\n"
                return StreamingResponse(
                    _error_stream(),
                    status_code=200,
                    headers={"content-type": "text/event-stream"},
                )

    if resp is None:
        _release_sem()
        raise RuntimeError("proxy send unexpectedly None after retries")

    async def _relay_and_count():
        # 逐行扫描 SSE 流，提取最后一个 usage.total_tokens。
        line_buf = b""
        best_total_tokens = 0
        has_usage = False
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
                line_buf += chunk
                while b"\n" in line_buf:
                    ln, line_buf = line_buf.split(b"\n", 1)
                    ln = ln.strip()
                    if ln.startswith(b"data: ") and ln != b"data: [DONE]":
                        try:
                            obj = json.loads(ln[6:])
                            tt = (obj.get("usage") or {}).get("total_tokens")
                            if tt is not None:
                                best_total_tokens = int(tt)
                                has_usage = True
                        except (ValueError, TypeError, AttributeError) as exc:
                            # 非 JSON 数据行（心跳/注释等）只跳过计数，不影响转发
                            logger.debug("usage scan skip non-JSON SSE line: %s", exc)
        except httpx.StreamConsumed:
            # 上游为即时内容响应，非流式 / 测试桩：直接吐已就绪的字节。
            # 该分支不经逐行扫描，须单独提取 usage，否则绕过 token 预算计量
            ext = _extract_total_tokens(resp.content)
            if ext > best_total_tokens:
                best_total_tokens = ext
            if best_total_tokens > 0:
                has_usage = True
            yield resp.content
        except httpx.HTTPError as exc:
            logger.warning("upstream LLM stream broke mid-flight: %s: %s", type(exc).__name__, exc)
            if "text/event-stream" in resp.headers.get("content-type", ""):
                yield b"data: [DONE]\n\n"
        finally:
            # 先归还并发槽位再做计量：计量涉及 Redis I/O，一旦变慢会占住
            # 槽位拖停其他 LLM 调用（llm_max_concurrency 默认仅 3）
            await resp.aclose()
            _release_sem()
            if has_usage:
                if user_id:
                    try:
                        await get_user_budget_store().add(user_id, best_total_tokens)
                    except Exception as exc:
                        logger.warning("user budget accounting skipped: %s: %s",
                                       type(exc).__name__, exc)
                # 推权威 usage 事件到本会话 SSE 流，供前端实时累计展示。
                if session_id:
                    cumulative = _add_session_tokens(session_id, best_total_tokens)
                    try:
                        from .models import sse_event
                        from .session_store import store
                        sess = await store.get(session_id)
                        if sess is not None and sess.events is not None:
                            sess.events.put_nowait(sse_event(
                                "usage",
                                total_tokens=best_total_tokens,
                                session_total=cumulative,
                            ))
                    except Exception as exc:
                        # usage 事件推送是尽力而为，失败不能打断 finally 里的转发收尾
                        logger.debug(
                            "usage event push skipped for session %s: %s: %s",
                            session_id, type(exc).__name__, exc,
                        )
                logger.debug(
                    "token usage: user=%s session=%s call=%d session_total=%s",
                    user_id, session_id, best_total_tokens,
                    _session_tokens.get(session_id),
                )

    resp_headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    return StreamingResponse(
        _relay_and_count(),
        status_code=resp.status_code,
        headers=resp_headers,
        media_type=resp.headers.get("content-type"),
    )
