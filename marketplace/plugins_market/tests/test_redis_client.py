# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Test the Redis boundary, including real sync/async driver integration.

Set SKILLHUB_TEST_REDIS_URL to enable integration against a disposable Redis.
Only UUID-scoped test keys are removed; no FLUSH commands are used.
"""
from __future__ import annotations

import ast
import asyncio
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from common.redis_client import RedisClient


def test_business_modules_do_not_construct_raw_clients_or_prefix_keys():
    root = Path(__file__).resolve().parents[2]
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name.startswith("test_") or path == root / "common/redis_client.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] != "redis" for alias in node.names), str(path)
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "redis", str(path)
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                assert name not in {
                    "redis_key", "redis_channel", "redis.Redis", "aioredis.Redis", "redis_lib.Redis"
                }, str(path)


@pytest.mark.parametrize("prefix", ["", "app", "app:", "app::"])
@pytest.mark.parametrize("method,args,kwargs,keys", [
    ("get", ("key",), {}, (0,)),
    ("getdel", ("key",), {}, (0,)),
    ("set", ("key", "value"), {"nx": True, "ex": 10}, (0,)),
    ("setex", ("key", 10, "value"), {}, (0,)),
    ("delete", ("key", b"other"), {}, (0, 1)),
    ("exists", ("key", "other"), {}, (0, 1)),
    ("incr", ("key",), {"amount": 2}, (0,)),
    ("decr", ("key",), {}, (0,)),
    ("expire", ("key", 10), {"nx": True}, (0,)),
    ("ttl", ("key",), {}, (0,)),
    ("hset", ("key",), {"mapping": {"field": "value"}}, (0,)),
    ("hgetall", ("key",), {}, (0,)),
    ("sadd", ("key", "member", b"other"), {}, (0,)),
    ("srem", ("key", "member"), {}, (0,)),
    ("smembers", ("key",), {}, (0,)),
    ("sismember", ("key", "member"), {}, (0,)),
    ("xadd", ("key", {"field": "value"}), {"id": "1-0"}, (0,)),
    ("xgroup_create", ("key", "group"), {"id": "0", "mkstream": True}, (0,)),
    ("xack", ("key", "group", b"1-0"), {}, (0,)),
    ("publish", ("key", "message"), {}, (0,)),
])
def test_only_name_arguments_are_transformed(prefix, method, args, kwargs, keys):
    raw = MagicMock()
    client = RedisClient(raw, prefix)
    result = getattr(client, method)(*args, **kwargs)
    normalized = prefix + ":" if prefix and not prefix.endswith(":") else prefix
    expected = list(args)
    for index in keys:
        name = args[index]
        expected[index] = normalized.encode() + name if isinstance(name, bytes) else normalized + name
    getattr(raw, method).assert_called_once_with(*expected, **kwargs)
    assert result is getattr(raw, method).return_value


@pytest.mark.parametrize("method", ["eval", "evalsha"])
@pytest.mark.parametrize("numkeys", [0, 1, 2])
def test_lua_only_transforms_declared_keys(method, numkeys):
    raw = MagicMock()
    client = RedisClient(raw, "app")
    args = ("first", b"second", "owner", 60)
    getattr(client, method)("script", numkeys, *args)
    expected = list(args)
    for index in range(numkeys):
        name = args[index]
        expected[index] = b"app:" + name if isinstance(name, bytes) else "app:" + name
    getattr(raw, method).assert_called_once_with("script", numkeys, *expected)


def test_stream_mapping_changes_names_but_not_input_or_identifiers():
    raw = MagicMock()
    streams = {"one": ">", b"two": b"0-0"}
    RedisClient(raw, "app").xreadgroup("group", "consumer", streams, count=2)
    raw.xreadgroup.assert_called_once_with("group", "consumer", {"app:one": ">", b"app:two": b"0-0"}, count=2)
    assert streams == {"one": ">", b"two": b"0-0"}


def test_empty_prefix_forwards_stream_mapping_and_lua_arguments_verbatim():
    raw = MagicMock()
    client = RedisClient(raw)
    streams = {b"stream": b">"}
    client.xreadgroup("group", "consumer", streams)
    assert raw.xreadgroup.call_args.args[2] is streams
    for method in ("eval", "evalsha"):
        getattr(client, method)("script", "1", b"key", b"value")
        getattr(raw, method).assert_called_once_with("script", "1", b"key", b"value")


@pytest.mark.parametrize("container", [bytes, bytearray, memoryview])
def test_pattern_namespace_supports_bytes_like_names(container):
    raw = MagicMock()
    RedisClient(raw, "app[*]").pubsub().psubscribe(container(b"events:*"))
    raw.pubsub.return_value.psubscribe.assert_called_once_with(rb"app\[\*\]:events:*")


def test_pipeline_keeps_facade_when_chaining_and_preserves_options():
    raw = MagicMock()
    pipe = raw.pipeline.return_value
    pipe.set.return_value = pipe
    pipe.get.return_value = pipe
    client = RedisClient(raw, "app")
    with client.pipeline(transaction=False) as wrapped:
        assert wrapped.set("key", "app:value").get("key") is wrapped
        assert wrapped.execute() is pipe.execute.return_value
    raw.pipeline.assert_called_once_with(transaction=False)
    pipe.set.assert_called_once_with("app:key", "app:value")
    pipe.get.assert_called_once_with("app:key")


def test_async_client_pipeline_and_lifecycle_preserve_awaitables():
    async def run():
        raw = MagicMock()
        raw.get = AsyncMock(return_value=b"value")
        raw.aclose = AsyncMock()
        pipe = raw.pipeline.return_value
        pipe.set.return_value = pipe
        pipe.execute = AsyncMock(return_value=[True])
        client = RedisClient(raw, "app")
        assert await client.get("key") == b"value"
        async with client.pipeline() as wrapped:
            assert wrapped.set("key", "value") is wrapped
            assert await wrapped.execute() == [True]
        await client.aclose()
        raw.get.assert_awaited_once_with("app:key")
        pipe.set.assert_called_once_with("app:key", "value")
        raw.aclose.assert_awaited_once()
    asyncio.run(run())


def test_async_close_supports_redis_py_5_without_aclose():
    async def run():
        raw = SimpleNamespace(close=AsyncMock())
        await RedisClient(raw, "app").aclose()
        raw.close.assert_awaited_once()
    asyncio.run(run())


def test_namespace_is_not_inferred_from_logical_key_and_nested_wrapping_fails():
    raw = MagicMock()
    client = RedisClient(raw, "app")
    client.get("app:key")
    raw.get.assert_called_once_with("app:app:key")
    with pytest.raises(TypeError, match="already namespaced"):
        RedisClient(client, "app")
    with pytest.raises(AttributeError):
        client.execute_command("FLUSHDB")


def test_pubsub_prefixes_callbacks_patterns_and_unsubscriptions():
    raw = MagicMock()
    pubsub = RedisClient(raw, "app[*]?").pubsub(ignore_subscribe_messages=True)
    sub = raw.pubsub.return_value

    def callback(_message):
        return None

    pubsub.subscribe("events", alerts=callback)
    sub.subscribe.assert_called_once_with("app[*]?:events", **{"app[*]?:alerts": callback})
    pubsub.psubscribe("events:*", **{"alerts:*": callback})
    sub.psubscribe.assert_called_once_with(r"app\[\*\]\?:events:*", **{r"app\[\*\]\?:alerts:*": callback})
    pubsub.unsubscribe("events")
    sub.unsubscribe.assert_called_once_with("app[*]?:events")
    pubsub.punsubscribe(b"events:*")
    sub.punsubscribe.assert_called_once_with(rb"app\[\*\]\?:events:*")
    pubsub.unsubscribe()
    assert sub.unsubscribe.call_args.args == ()
    assert pubsub.get_message(timeout=1) is sub.get_message.return_value


@pytest.mark.parametrize("method", ["subscribe", "unsubscribe", "psubscribe", "punsubscribe"])
@pytest.mark.parametrize("prefix", ["", "app"])
@pytest.mark.parametrize("container", [list, tuple, iter])
def test_pubsub_accepts_iterable_names_and_empty_prefix_preserves_driver_arguments(method, prefix, container):
    raw = MagicMock()
    names = container(["one", b"two"])
    sub = RedisClient(raw, prefix).pubsub()
    getattr(sub, method)(names)
    target = getattr(raw.pubsub.return_value, method)
    if prefix:
        target.assert_called_once_with("app:one", b"app:two")
    else:
        assert target.call_args.args == (names,)


def test_pubsub_preserves_subscription_handlers_when_driver_supports_them():
    from redis import typing as redis_typing

    subscription_type = getattr(redis_typing, "Subscription", None)
    if subscription_type is None:
        pytest.skip("This driver predates Subscription objects")
    raw = MagicMock()

    def handler(_message):
        return None

    item = subscription_type("events", handler)
    RedisClient(raw, "app").pubsub().subscribe(item)
    raw.pubsub.return_value.subscribe.assert_called_once_with(subscription_type("app:events", handler))
    RedisClient(raw).pubsub().subscribe(item)
    assert raw.pubsub.return_value.subscribe.call_args.args[0] is item


@pytest.fixture
def redis_server():
    url = os.getenv("SKILLHUB_TEST_REDIS_URL")
    if not url:
        pytest.skip("SKILLHUB_TEST_REDIS_URL is not configured")
    import redis

    raw = redis.Redis.from_url(url, decode_responses=True)
    raw.ping()
    scope = "skillhub-test:" + uuid.uuid4().hex
    try:
        yield url, raw, scope
    finally:
        keys = list(raw.scan_iter(match=scope + "*"))
        if keys:
            raw.delete(*keys)
        raw.close()


def test_real_redis_isolation_pipeline_lua_streams_and_pubsub(redis_server):
    _, raw, scope = redis_server
    left = RedisClient(raw, scope + ":a")
    right = RedisClient(raw, scope + ":b:")
    legacy = RedisClient(raw)
    legacy.set(scope + ":legacy", "unchanged")
    assert raw.get(scope + ":legacy") == "unchanged"
    with left.pipeline() as pipe:
        pipe.set("key", "left", ex=60).sadd("members", "plain-member").hset("hash", mapping={"field": "value"})
        assert pipe.execute() == [True, 1, 1]
    right.set("key", "right")
    assert left.get("key") == "left" and right.get("key") == "right"
    assert left.smembers("members") == {"plain-member"}
    assert left.hgetall("hash") == {"field": "value"}
    assert left.ttl("key") > 0
    assert left.eval("return {redis.call('GET', KEYS[1]), ARGV[1]}", 1, "key", "unchanged") == ["left", "unchanged"]
    left.xgroup_create("stream", "group", id="0", mkstream=True)
    message_id = left.xadd("stream", {"field": "message"})
    messages = left.xreadgroup("group", "consumer", {"stream": ">"}, count=1)
    assert messages == [[scope + ":a:stream", [(message_id, {"field": "message"})]]]
    assert left.xack("stream", "group", message_id) == 1
    assert left.getdel("key") == "left" and left.getdel("key") is None
    assert right.get("key") == "right"
    with left.pubsub() as sub:
        sub.subscribe("events")
        assert sub.get_message(timeout=2)["type"] == "subscribe"
        assert right.publish("events", "wrong") == 0
        assert left.publish("events", "unchanged") == 1
        message = sub.get_message(timeout=2)
        assert message["channel"] == scope + ":a:events" and message["data"] == "unchanged"
    assert not raw.exists(scope + ":a:" + scope + ":a:key")


def test_real_async_redis_pipeline_atomic_getdel_and_lua(redis_server):
    url, raw, scope = redis_server

    async def run():
        from redis import asyncio as aioredis

        client = RedisClient(aioredis.Redis.from_url(url, decode_responses=False), scope + ":async")
        try:
            async with client.pipeline() as pipe:
                pipe.set(b"key", b"value").hset("hash", mapping={"field": "value"})
                assert await pipe.execute() == [True, 1]
            assert await client.get("key") == b"value"
            assert await client.hgetall("hash") == {b"field": b"value"}
            assert await client.eval(
                "return {redis.call('GET', KEYS[1]), ARGV[1]}", 1, "key", "owner"
            ) == [b"value", b"owner"]
            values = await asyncio.gather(client.getdel("key"), client.getdel("key"))
            assert values.count(b"value") == 1 and values.count(None) == 1
        finally:
            await client.aclose()
    asyncio.run(run())
    assert raw.hgetall(scope + ":async:hash") == {"field": "value"}


def test_real_pattern_subscription_treats_namespace_globs_as_literals(redis_server):
    _, raw, scope = redis_server
    client = RedisClient(raw, scope + "[*]?")
    received = []
    with client.pubsub() as sub:
        sub.psubscribe(**{"events:*": received.append})
        assert sub.get_message(timeout=2)["type"] == "psubscribe"
        assert client.publish("events:one", "plain") == 1
        assert sub.get_message(timeout=2) is None
    assert len(received) == 1 and received[0]["data"] == "plain"
    assert received[0]["channel"] == scope + "[*]?:events:one"


def test_real_async_pubsub_namespaces_channels(redis_server):
    url, _, scope = redis_server

    async def run():
        from redis import asyncio as aioredis

        client = RedisClient(aioredis.Redis.from_url(url, decode_responses=True), scope + ":pub")
        try:
            async with client.pubsub() as sub:
                await sub.subscribe("events")
                assert (await sub.get_message(timeout=2))["type"] == "subscribe"
                assert await client.publish("events", "plain") == 1
                message = await sub.get_message(timeout=2)
                assert message["data"] == "plain" and message["channel"] == scope + ":pub:events"
                await sub.unsubscribe("events")
        finally:
            await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("prefix", ["", "app"])
def test_real_sync_and_async_pubsub_iterable_names(redis_server, prefix):
    url, raw, scope = redis_server
    namespace = scope + ":" + prefix if prefix else ""
    channels = [scope + ":events:one", scope + ":events:two"]
    client = RedisClient(raw, namespace)
    with client.pubsub() as sub:
        sub.psubscribe([scope + ":events:*"])
        assert sub.get_message(timeout=2)["type"] == "psubscribe"
        assert client.publish(channels[0], "sync") == 1
        assert sub.get_message(timeout=2)["data"] == "sync"
        sub.punsubscribe([scope + ":events:*"])
        assert sub.get_message(timeout=2)["type"] == "punsubscribe"
        sub.subscribe(channels)
        for _ in channels:
            assert sub.get_message(timeout=2)["type"] == "subscribe"
        sub.unsubscribe(channels)
        for _ in channels:
            assert sub.get_message(timeout=2)["type"] == "unsubscribe"

    async def run():
        from redis import asyncio as aioredis

        async_client = RedisClient(aioredis.Redis.from_url(url, decode_responses=True), namespace)
        pattern_args = ([scope + ":events:*"],) if prefix else (scope + ":events:*",)
        channel_args = (channels,) if prefix else tuple(channels)
        try:
            async with async_client.pubsub() as sub:
                await sub.psubscribe(*pattern_args)
                assert (await sub.get_message(timeout=2))["type"] == "psubscribe"
                assert await async_client.publish(channels[1], "async") == 1
                assert (await sub.get_message(timeout=2))["data"] == "async"
                # Some redis-py versions reject iterable PUNSUBSCRIBE on the
                # native async client; keep the baseline behavior unchanged.
                await sub.punsubscribe(scope + ":events:*")
                assert (await sub.get_message(timeout=2))["type"] == "punsubscribe"
                await sub.subscribe(*channel_args)
                for _ in channels:
                    assert (await sub.get_message(timeout=2))["type"] == "subscribe"
                await sub.unsubscribe(*channel_args)
                for _ in channels:
                    assert (await sub.get_message(timeout=2))["type"] == "unsubscribe"
        finally:
            await async_client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("method", ["subscribe", "unsubscribe", "psubscribe", "punsubscribe"])
@pytest.mark.parametrize("container", [list, tuple, iter])
def test_empty_prefix_matches_native_async_pubsub_arguments(redis_server, method, container):
    from redis.exceptions import RedisError

    url, _, scope = redis_server

    async def run():
        from redis import asyncio as aioredis

        raw = aioredis.Redis.from_url(url, decode_responses=True)
        client = RedisClient(raw)
        outcomes = []
        try:
            for sub in (raw.pubsub(), client.pubsub()):
                try:
                    async with sub:
                        await getattr(sub, method)(container([scope + ":events:*"]))
                        outcomes.append(None)
                except (TypeError, ValueError, RedisError) as exc:
                    outcomes.append((type(exc), str(exc)))
            assert outcomes[0] == outcomes[1]
        finally:
            await client.aclose()
    asyncio.run(run())
