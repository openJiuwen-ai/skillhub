# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""通用 Marketplace API 统一速率限制中间件（issue #90）。

实现要点：
- 基于进程内滑动窗口（``SlidingWindowRateLimiter``），与既有 ClawHub 兼容层
  限流实现一致，不引入 slowapi / fastapi-limiter 新依赖；
- 按 (方法, 路径) 匹配档位（``RateLimitPolicy``），先豁免已有限流端点，再分级限流；
- IP 维度 + 凭证（Bearer Token 哈希 / System Token）维度双维限流，用户维度
  无凭证时回退 IP 维度；
- 放行响应注入 ``X-RateLimit-Limit/Remaining/Reset`` 头；拒绝返回 ``429``，
  携带标准错误信封（error=rate_limited, SKILLHUB_RATE_LIMITED）与
  ``Retry-After`` 头，并复用审计失败补录与结构化日志；
- ``OPTIONS`` 预检、``/api/health`` 及豁免端点不计数。

注册位置（main.py::create_app）：在 ``RequestIDMiddleware`` 之后注册，
保证请求上下文（request_id/start_time）已就绪，且 429 响应仍经外层中间件
写入 interface.log 与 x-request-id。
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from plugins_market.core.audit_failed import audit_failed_mutation
from plugins_market.core.config import settings
from plugins_market.core.errors import as_json_response_body, ensure_standard_error_payload, http_error_payload
from plugins_market.core.logging import get_logger
from plugins_market.core.rate_limit import (
    RateLimitCheck,
    RateLimitPolicy,
    SlidingWindowRateLimiter,
    build_rate_limit_policy,
    client_ip_from_scope,
    user_key_from_scope,
)

logger = get_logger(__name__)

_RATE_LIMIT_MESSAGE = "请求过于频繁，请稍后再试"


def default_rate_limit_policy() -> RateLimitPolicy:
    """从全局配置构建默认限流策略（0 = 关闭对应档位）。"""
    return build_rate_limit_policy(
        enabled=settings.rate_limiting_enabled,
        public_read_per_minute=settings.rate_limit_public_read_per_minute,
        publish_per_minute=settings.rate_limit_publish_per_minute,
        batch_per_minute=settings.rate_limit_batch_per_minute,
        auth_per_minute=settings.rate_limit_auth_per_minute,
        default_write_per_minute=settings.rate_limit_default_write_per_minute,
    )


def _rate_limit_headers(check: RateLimitCheck) -> list[tuple[bytes, bytes]]:
    """标准限流响应头；``X-RateLimit-Reset`` 为 epoch 秒。"""
    reset_epoch = int(time.time()) + check.retry_after
    return [
        (b"x-ratelimit-limit", str(check.limit).encode("latin-1")),
        (b"x-ratelimit-remaining", str(check.remaining).encode("latin-1")),
        (b"x-ratelimit-reset", str(reset_epoch).encode("latin-1")),
    ]


class RateLimitMiddleware:
    """ASGI 中间件：按档位对 Marketplace API 请求限流。

    构造参数可注入（测试用）：``policy`` 与 ``limiter`` 缺省时分别取
    全局配置构建的策略与新建的进程内滑动窗口。
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        policy: Optional[RateLimitPolicy] = None,
        limiter: Optional[SlidingWindowRateLimiter] = None,
    ) -> None:
        self.app = app
        self._policy = policy if policy is not None else default_rate_limit_policy()
        self._limiter = limiter if limiter is not None else SlidingWindowRateLimiter()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        if method == "OPTIONS":
            # CORS 预检不计数
            await self.app(scope, receive, send)
            return

        path = (scope.get("path", "") or "").rstrip("/") or "/"
        tier = self._policy.match(method, path)
        if tier is None or tier.name == "exempt" or tier.limit <= 0:
            await self.app(scope, receive, send)
            return

        client_ip = client_ip_from_scope(scope, trust_forwarded=settings.rate_limit_trust_forwarded)
        user_key = user_key_from_scope(scope)
        keys = self._policy.keys_for(tier, client_ip=client_ip, user_key=user_key)

        denied: Optional[RateLimitCheck] = None
        best: Optional[RateLimitCheck] = None
        for key in keys:
            check = self._limiter.check(key, limit=tier.limit, window_sec=tier.window_sec)
            if not check.allowed:
                denied = check
                break
            if best is None or check.remaining < best.remaining:
                best = check

        if denied is not None:
            await self._reject(
                scope=scope,
                receive=receive,
                send=send,
                method=method,
                path=path,
                tier_name=tier.name,
                check=denied,
            )
            return

        if best is None:
            # keys 列表为空（理论上不会发生：keys_for 至少返回一个 key），
            # 安全兜底为直通，不注入限流头（避免 assert 在 -O 下被剥离）。
            await self.app(scope, receive, send)
            return
        headers_to_add = _rate_limit_headers(best)

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                merged = list(message.get("headers") or [])
                merged.extend(headers_to_add)
                message["headers"] = merged
            await send(message)

        await self.app(scope, receive, send_wrapper)

    async def _reject(
        self,
        *,
        scope: Scope,
        receive: Receive,
        send: Send,
        method: str,
        path: str,
        tier_name: str,
        check: RateLimitCheck,
    ) -> None:
        payload = ensure_standard_error_payload(
            http_error_payload(
                status_code=429,
                message=_RATE_LIMIT_MESSAGE,
                error="rate_limited",
            )
        )
        response = JSONResponse(status_code=429, content={"detail": payload})
        for key, value in _rate_limit_headers(check):
            response.headers[key.decode("latin-1")] = value.decode("latin-1")
        response.headers["Retry-After"] = str(check.retry_after)

        client_key = "unknown"
        try:
            request = Request(scope)
            client_key = request.client.host if request.client else "unknown"
            audit_failed_mutation(request, 429, as_json_response_body(payload))
        except Exception as exc:  # noqa: BLE001  审计失败不影响主链路
            logger.warning("rate_limit audit suppressed exception: %s", exc)

        logger.info(
            "rate_limit_rejected",
            method=method,
            path=path,
            tier=tier_name,
            limit=check.limit,
            client_key=client_key,
            retry_after=check.retry_after,
        )
        await response(scope, receive, send)
