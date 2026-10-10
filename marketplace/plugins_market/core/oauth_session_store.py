# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OAuth 一次性会话（state / pending）：优先 Redis，否则进程内内存（单 worker 可用）。"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Protocol

from common.security.security_utils import SecurityUtils
from plugins_market.core.config import settings
from plugins_market.core.logging import get_logger

logger = get_logger(__name__)


# 内存兜底 store 的容量上限：防止 Redis 不可用时，攻击者反复发起 OAuth 流程
# 无限堆积 state/pending 条目耗尽内存（CWE-770）。达到上限时按 FIFO 淘汰最旧条目。
_MEMORY_STORE_MAX_ENTRIES = 10000


class OAuthStrStore(Protocol):
    def get(self, key: str) -> str | None:
        ...

    def set_ex(self, key: str, value: str, ttl_seconds: int) -> None:
        ...

    def delete(self, key: str) -> None:
        ...

    def get_del(self, key: str) -> str | None:
        """原子地取出并删除 key 的值；不存在返回 None。用于一次性令牌的安全兑换。"""
        ...


class _MemoryOAuthStore:
    def __init__(self, max_entries: int = _MEMORY_STORE_MAX_ENTRIES) -> None:
        # 用 OrderedDict 维护插入顺序，便于达到上限时 FIFO 淘汰最旧条目。
        self._data: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max(1, int(max_entries))

    def _purge_expired_locked(self) -> None:
        now = time.monotonic()
        dead = [k for k, (exp_at, _) in self._data.items() if exp_at <= now]
        for k in dead:
            self._data.pop(k, None)

    def _evict_to_capacity_locked(self) -> None:
        # 先清理过期项；若仍超过上限，按插入顺序淘汰最旧的条目。
        while len(self._data) > self._max_entries:
            self._data.popitem(last=False)

    def get(self, key: str) -> str | None:
        with self._lock:
            self._purge_expired_locked()
            item = self._data.get(key)
            if not item:
                return None
            exp_at, val = item
            if exp_at <= time.monotonic():
                self._data.pop(key, None)
                return None
            return val

    def set_ex(self, key: str, value: str, ttl_seconds: int) -> None:
        with self._lock:
            self._purge_expired_locked()
            # 覆盖已有 key 时先删除以刷新其插入顺序，保证 FIFO 语义正确。
            self._data.pop(key, None)
            self._data[key] = (time.monotonic() + max(1, ttl_seconds), value)
            self._evict_to_capacity_locked()

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def get_del(self, key: str) -> str | None:
        # 在同一把锁内完成取值+删除，保证一次性令牌不会被并发兑换两次（消除 TOCTOU）。
        with self._lock:
            self._purge_expired_locked()
            item = self._data.pop(key, None)
            if not item:
                return None
            exp_at, val = item
            if exp_at <= time.monotonic():
                return None
            return val


class _RedisOAuthStore:
    def __init__(self, *, host: str, port: int, db: int, password: str, ssl: bool = False) -> None:
        import redis  # type: ignore[import-not-found]

        kwargs: dict = {
            "host": host,
            "port": port,
            "db": db,
            "decode_responses": True,
            # 空闲连接被静默丢弃时限时报错（登录路径快速失败，不挂死）
            "socket_connect_timeout": 2.0,
            "socket_timeout": 2.0,
            "health_check_interval": 30,
        }
        if password:
            kwargs["password"] = password
        if ssl:
            kwargs["ssl"] = True
        self._r = redis.Redis(**kwargs)
        # 首次建连：未 PING 则仅创建客户端对象，网络/密码错误要在第一次命令时才会暴露
        self._r.ping()

    def get(self, key: str) -> str | None:
        v = self._r.get(key)
        return v if isinstance(v, str) else None

    def set_ex(self, key: str, value: str, ttl_seconds: int) -> None:
        self._r.setex(key, max(1, ttl_seconds), value)

    def delete(self, key: str) -> None:
        self._r.delete(key)

    def get_del(self, key: str) -> str | None:
        # 优先用 Redis 原生 GETDEL（Redis >= 6.2）原子取出并删除；
        # 旧版本不支持时回退到 pipeline(GET+DEL)，单连接下两条命令顺序执行，
        # 同一 key 的并发兑换最多只有一个能拿到非空值（DEL 幂等）。
        try:
            v = self._r.getdel(key)
        except Exception:
            pipe = self._r.pipeline()
            pipe.get(key)
            pipe.delete(key)
            v = pipe.execute()[0]
        return v if isinstance(v, str) else None


_memory = _MemoryOAuthStore()
_redis_store: _RedisOAuthStore | None = None
_redis_init_attempted = False
_warned_memory_no_redis_host = False


def _resolve_redis_password() -> str:
    return (SecurityUtils.get_decrypt_secret("MARKET_REDIS_PASSWORD", default="") or "").strip()


def get_oauth_str_store() -> OAuthStrStore:
    global _redis_store, _redis_init_attempted, _warned_memory_no_redis_host
    host = (settings.redis_host or "").strip()
    if not host and not _warned_memory_no_redis_host:
        _warned_memory_no_redis_host = True
        logger.warning(
            "OAuth session store: no REDIS_HOST/MARKET_REDIS_HOST; using in-process memory only. "
            "GitCode OAuth breaks when /start and /callback hit different workers or pods — configure shared Redis."
        )
    if host and not _redis_init_attempted:
        _redis_init_attempted = True
        try:
            from plugins_market.core.redis_client import resolve_redis_ssl

            ssl = resolve_redis_ssl()
            _redis_store = _RedisOAuthStore(
                host=host,
                port=int(settings.redis_port),
                db=int(settings.redis_db),
                password=_resolve_redis_password(),
                ssl=ssl,
            )
            logger.info(
                "OAuth session store: Redis (PING OK, host=%s port=%s db=%s ssl=%s)",
                host,
                settings.redis_port,
                settings.redis_db,
                ssl,
            )
            return _redis_store
        except Exception as e:
            logger.warning("OAuth session store: Redis init failed (%s), using memory", e)
    if _redis_store is not None:
        return _redis_store
    return _memory
