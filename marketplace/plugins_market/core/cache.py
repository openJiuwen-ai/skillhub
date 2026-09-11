# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""通用 Redis 缓存客户端；复用 settings 里已有的 Redis 配置。无 Redis 时静默降级（不缓存）。"""

from __future__ import annotations

from typing import Optional

from plugins_market.core.config import settings
from plugins_market.core.logging import get_logger

logger = get_logger(__name__)

_client: Optional[object] = None
_init_attempted = False


def _make_client():
    global _client, _init_attempted
    if _init_attempted:
        return _client
    _init_attempted = True
    host = (settings.redis_host or "").strip()
    if not host:
        return None
    try:
        import redis  # type: ignore[import-not-found]

        from plugins_market.core.redis_client import redis_connection_kwargs, resolve_redis_ssl

        kwargs = redis_connection_kwargs(decode_responses=True)
        r = redis.Redis(**kwargs)
        r.ping()
        _client = r
        logger.info(
            "Cache: Redis OK host=%s port=%s db=%s ssl=%s backend=%s",
            host,
            settings.redis_port,
            settings.redis_db,
            resolve_redis_ssl(),
            settings.cache_backend,
        )
    except Exception as e:
        logger.warning("Cache: Redis init failed (%s), caching disabled", e)
    return _client


def cache_get(key: str) -> str | None:
    r = _make_client()
    if r is None:
        return None
    try:
        v = r.get(key)  # type: ignore[union-attr]
        return v if isinstance(v, str) else None
    except Exception as e:
        logger.debug("cache_get failed key=%s: %s", key, e)
        return None


def cache_set(key: str, value: str, ttl: int = 86400 * 7) -> None:
    r = _make_client()
    if r is None:
        return
    try:
        r.setex(key, max(1, ttl), value)  # type: ignore[union-attr]
    except Exception as e:
        logger.debug("cache_set failed key=%s: %s", key, e)


def cache_set_nx(key: str, ttl: int) -> bool:
    """SET NX EX：key 不存在时写入并返回 True（当天首次）；已存在返回 False。

    用于下载计量去重闸门：无 Redis 或出错时返回 True（fail-open）——
    可用性优先于精确性，宁可偶尔多计，不因 Redis 故障漏计或阻断下载。
    """
    r = _make_client()
    if r is None:
        return True
    try:
        return bool(r.set(key, "1", nx=True, ex=max(1, ttl)))  # type: ignore[union-attr]
    except Exception as e:
        logger.debug("cache_set_nx failed key=%s: %s", key, e)
        return True


def cache_delete(key: str) -> None:
    r = _make_client()
    if r is None:
        return
    try:
        r.delete(key)  # type: ignore[union-attr]
    except Exception as e:
        logger.debug("cache_delete failed key=%s: %s", key, e)


def cache_set_persistent(key: str, value: str) -> None:
    """写入不带 TTL 的永久 key（用于需长期保留的状态，如标星记录）。

    与 cache_set 的区别：cache_set 用 SETEX 强制带 TTL；本函数用 SET 不设过期。
    无 Redis 或出错时静默跳过。
    """
    r = _make_client()
    if r is None:
        return
    try:
        r.set(key, value)  # type: ignore[union-attr]
    except Exception as e:
        logger.debug("cache_set_persistent failed key=%s: %s", key, e)


def cache_incr(key: str, ttl: int | None = None) -> int | None:
    """原子自增计数器（Redis INCR）。无 Redis 或出错时返回 None，不影响业务。

    Args:
        key: 计数器 key
        ttl: 首次创建时设置的过期时间（秒）；None = 永不过期。已存在的 key 不重设 TTL。
    Returns:
        自增后的值，或 None（Redis 不可用）
    """
    r = _make_client()
    if r is None:
        return None
    try:
        new_val = r.incr(key)  # type: ignore[union-attr]
        if ttl is not None and new_val == 1:
            r.expire(key, max(1, ttl))  # type: ignore[union-attr]
        return new_val
    except Exception as e:
        logger.debug("cache_incr failed key=%s: %s", key, e)
        return None
