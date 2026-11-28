# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Redis storage boundary for logical keys and channels.

The same facade wraps synchronous and asyncio redis-py clients: driver results
(including awaitables) are returned unchanged. Only names are transformed, never
values, hash fields, set members, stream IDs/groups/messages or Lua ARGV.
Raw clients stay private. Unsupported commands are deliberately not forwarded.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from common.redis_namespace import normalize_redis_key_prefix


def create_redis_client(*, key_prefix: str = "", **kwargs) -> RedisClient:
    import redis

    return RedisClient(redis.Redis(**kwargs), key_prefix)


def create_async_redis_client(*, key_prefix: str = "", **kwargs) -> RedisClient:
    from redis import asyncio as aioredis

    return RedisClient(aioredis.Redis(**kwargs), key_prefix)


class RedisClient:
    """Bind an application namespace once, when constructing a Redis client."""

    def __init__(self, client: Any, key_prefix: str = "") -> None:
        if isinstance(client, RedisClient):
            raise TypeError("Redis client is already namespaced")
        self._client = client
        self.key_prefix = normalize_redis_key_prefix(key_prefix)

    def _key(self, name):
        if not self.key_prefix:
            return name
        if isinstance(name, (bytes, bytearray, memoryview)):
            return self.key_prefix.encode("utf-8") + bytes(name)
        return self.key_prefix + name

    def _call(self, method, *args, **kwargs):
        result = getattr(self._client, method)(*args, **kwargs)
        # Pipeline commands must keep returning the facade for safe chaining.
        return self if result is self._client else result

    def get(self, name):
        return self._call("get", self._key(name))

    def getdel(self, name):
        return self._call("getdel", self._key(name))

    def set(self, name, value, *args, **kwargs):
        return self._call("set", self._key(name), value, *args, **kwargs)

    def setex(self, name, time, value):
        return self._call("setex", self._key(name), time, value)

    def delete(self, *names):
        return self._call("delete", *(self._key(name) for name in names))

    def exists(self, *names):
        return self._call("exists", *(self._key(name) for name in names))

    def incr(self, name, *args, **kwargs):
        return self._call("incr", self._key(name), *args, **kwargs)

    def decr(self, name, *args, **kwargs):
        return self._call("decr", self._key(name), *args, **kwargs)

    def expire(self, name, time, *args, **kwargs):
        return self._call("expire", self._key(name), time, *args, **kwargs)

    def ttl(self, name):
        return self._call("ttl", self._key(name))

    def hset(self, name, *args, **kwargs):
        return self._call("hset", self._key(name), *args, **kwargs)

    def hgetall(self, name):
        return self._call("hgetall", self._key(name))

    def sadd(self, name, *values):
        return self._call("sadd", self._key(name), *values)

    def srem(self, name, *values):
        return self._call("srem", self._key(name), *values)

    def smembers(self, name):
        return self._call("smembers", self._key(name))

    def sismember(self, name, value):
        return self._call("sismember", self._key(name), value)

    def xadd(self, name, fields, *args, **kwargs):
        return self._call("xadd", self._key(name), fields, *args, **kwargs)

    def xgroup_create(self, name, groupname, *args, **kwargs):
        return self._call("xgroup_create", self._key(name), groupname, *args, **kwargs)

    def xgroup_destroy(self, name, groupname):
        return self._call("xgroup_destroy", self._key(name), groupname)

    def xreadgroup(self, groupname, consumername, streams, *args, **kwargs):
        if not self.key_prefix:
            return self._call("xreadgroup", groupname, consumername, streams, *args, **kwargs)
        names = {self._key(name): identifier for name, identifier in streams.items()}
        return self._call("xreadgroup", groupname, consumername, names, *args, **kwargs)

    def xack(self, name, groupname, *ids):
        return self._call("xack", self._key(name), groupname, *ids)

    def eval(self, script, numkeys, *keys_and_args):
        if not self.key_prefix:
            return self._call("eval", script, numkeys, *keys_and_args)
        keys = tuple(self._key(name) for name in keys_and_args[:numkeys])
        return self._call("eval", script, numkeys, *keys, *keys_and_args[numkeys:])

    def evalsha(self, sha, numkeys, *keys_and_args):
        if not self.key_prefix:
            return self._call("evalsha", sha, numkeys, *keys_and_args)
        keys = tuple(self._key(name) for name in keys_and_args[:numkeys])
        return self._call("evalsha", sha, numkeys, *keys, *keys_and_args[numkeys:])

    def publish(self, channel, message):
        return self._call("publish", self._key(channel), message)

    def pipeline(self, *args, **kwargs):
        return RedisPipeline(self._client.pipeline(*args, **kwargs), self.key_prefix)

    def pubsub(self, *args, **kwargs):
        return RedisPubSub(self._client.pubsub(*args, **kwargs), self.key_prefix)

    def ping(self, *args, **kwargs):
        return self._call("ping", *args, **kwargs)

    def close(self, *args, **kwargs):
        return self._call("close", *args, **kwargs)

    def aclose(self, *args, **kwargs):
        # redis-py 5.0 exposes async close(); later releases use aclose().
        method = "aclose" if hasattr(self._client, "aclose") else "close"
        return self._call(method, *args, **kwargs)

    def __enter__(self):
        self._client.__enter__()
        return self

    def __exit__(self, *args):
        return self._client.__exit__(*args)

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self._client.__aexit__(*args)


class RedisPipeline(RedisClient):
    """Namespace commands before queuing; preserve transaction/execute semantics."""

    def execute(self, *args, **kwargs):
        return self._call("execute", *args, **kwargs)

    def reset(self):
        return self._call("reset")


class RedisPubSub(RedisClient):
    """Prefix subscriptions before redis-py registers channel callbacks."""

    def _pattern(self, pattern):
        # Namespace text is literal; only the caller's pattern contains globs.
        escaped = "".join("\\" + char if char in "\\*?[]" else char for char in self.key_prefix)
        if isinstance(pattern, (bytes, bytearray, memoryview)):
            return escaped.encode("utf-8") + bytes(pattern)
        return escaped + pattern

    def _subscribe(self, method, names, callbacks):
        if not self.key_prefix:
            # Preserve the driver's accepted argument forms verbatim, including
            # iterable names and version-specific Subscription objects.
            return self._call(method, *names, **callbacks)
        from redis import typing as redis_typing
        from redis.client import list_or_args

        transform = self._pattern if method in {"psubscribe", "punsubscribe"} else self._key
        if names and isinstance(names[0], (str, bytes, bytearray, memoryview)):
            items = names
        else:
            items = list_or_args(names[0], names[1:]) if names else []
        subscription_type = getattr(redis_typing, "Subscription", ())
        converted = (
            replace(item, name=transform(item.name))
            if isinstance(item, subscription_type) else transform(item)
            for item in items
        )
        return self._call(method, *converted,
                          **{transform(name): callback for name, callback in callbacks.items()})

    def subscribe(self, *channels, **callbacks):
        return self._subscribe("subscribe", channels, callbacks)

    def unsubscribe(self, *channels):
        return self._subscribe("unsubscribe", channels, {})

    def psubscribe(self, *patterns, **callbacks):
        return self._subscribe("psubscribe", patterns, callbacks)

    def punsubscribe(self, *patterns):
        return self._subscribe("punsubscribe", patterns, {})

    def get_message(self, *args, **kwargs):
        return self._call("get_message", *args, **kwargs)

    def listen(self):
        return self._call("listen")
