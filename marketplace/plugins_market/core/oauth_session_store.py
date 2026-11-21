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

# Lua 脚本：原子地取出并删除一个 key。
# Redis GETDEL（>=6.2）才是原生原子命令；旧版本不支持时用 EVAL 回退。
# Redis 执行 Lua 脚本时阻塞其它命令，天然消除 pipeline(GET+DEL) 的 TOCTOU 竞态：
# 并发兑换同一会话令牌时，两个请求各自执行脚本，但 Redis 串行化脚本执行，
# 先到者拿到值并删除，后到者 GET 到 nil 直接返回 None，一次性语义成立。
_GETDEL_LUA = """
local v = redis.call('GET', KEYS[1])
if v ~= false then
    redis.call('DEL', KEYS[1])
end
return v
"""


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
    def __init__(
        self,
        *,
        host: str,
        port: int,
        db: int,
        password: str,
        ssl: bool = False,
        username: str = "",
    ) -> None:
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
        # default 用户被禁用时，只发 AUTH <password> 会认证失败，必须带 ACL 用户名。
        username = (username or "").strip()
        if username:
            kwargs["username"] = username
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
        # 优先用 Redis 原生 GETDEL（>=6.2），原子且高效；
        # 旧版本（<6.2）服务器回退到 Lua 脚本——Redis 执行脚本时阻塞其它命令，
        # 天然避免 pipeline(GET+DEL) 两个独立命令之间的 TOCTOU 竞态。
        # 仅在 GETDEL 明确不可用（unknown command 或方法不存在）时才回退，
        # 网络/超时等真实错误直接向上抛出，不再静默走到回退里掩盖问题。
        try:
            v = self._r.getdel(key)
        except Exception as exc:
            exc_name = type(exc).__name__
            # ResponseError（服务端 unknown command） / AttributeError（客户端库过老）
            # 之外一律向上抛——这些是网络/认证/协议级错误，Lua 也救不了。
            if exc_name not in ("AttributeError", "ResponseError"):
                raise
            v = self._r.eval(_GETDEL_LUA, 1, key)
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
                username=(settings.redis_username or "").strip(),
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
