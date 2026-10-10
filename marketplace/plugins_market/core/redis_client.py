# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared Redis / DCS connection kwargs from marketplace Settings."""

from __future__ import annotations

from typing import Any

from common.security.security_utils import SecurityUtils
from plugins_market.core.config import settings


def resolve_redis_ssl() -> bool:
    """
    SSL for Redis clients.

    - Explicit MARKET_REDIS_SSL / REDIS_SSL wins when set.
    - Else CACHE_BACKEND=dcs (or REDIS_BACKEND=dcs) defaults to True (Huawei DCS).
    - Else False (local Redis / docker).
    """
    raw = (settings.redis_ssl_env or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    backend = (settings.cache_backend or "redis").strip().lower()
    return backend in {"dcs", "huawei_dcs", "huaweicloud_dcs"}


def redis_connection_kwargs(*, decode_responses: bool = True) -> dict[str, Any]:
    """Build kwargs for redis.Redis / redis.asyncio.Redis."""
    host = (settings.redis_host or "").strip()
    password = (SecurityUtils.get_decrypt_secret("MARKET_REDIS_PASSWORD", default="") or "").strip()
    kwargs: dict[str, Any] = {
        "host": host,
        "port": int(settings.redis_port),
        "db": int(settings.redis_db),
        "decode_responses": decode_responses,
        "socket_connect_timeout": 2.0,
        "socket_timeout": 2.0,
        "health_check_interval": 30,
    }
    if password:
        kwargs["password"] = password
    if resolve_redis_ssl():
        kwargs["ssl"] = True
    return kwargs
