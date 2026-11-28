"""Application namespaces must isolate reads, writes, locks and notifications."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from common.redis_client import RedisClient
from common.redis_namespace import normalize_redis_key_prefix
from plugins_market.core.config import Settings, settings
from recommender.shared.config import load_config


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, ""),
        ("", ""),
        ("  ", ""),
        ("app", "app:"),
        (" app:prod ", "app:prod:"),
        ("app:prod:", "app:prod:"),
        ("app::", "app::"),
    ],
)
def test_namespace_normalization(value, expected):
    assert normalize_redis_key_prefix(value) == expected


def test_settings_and_standalone_recommender_agree(monkeypatch):
    monkeypatch.delenv("REDIS_KEY_PREFIX", raising=False)
    assert Settings(_env_file=None).redis_key_prefix == ""
    assert load_config().redis.key_prefix == ""
    monkeypatch.setenv("REDIS_KEY_PREFIX", " app:prod ")
    assert Settings(_env_file=None).redis_key_prefix == "app:prod:"
    assert load_config().redis.key_prefix == "app:prod:"


def test_bootstrap_propagates_normalized_prefix_including_empty(monkeypatch):
    from plugins_market.recommender.bootstrap import apply_recommender_settings_to_env

    monkeypatch.setenv("REDIS_KEY_PREFIX", "stale:")
    for prefix in ("app:", ""):
        monkeypatch.setattr(settings, "redis_key_prefix", prefix)
        apply_recommender_settings_to_env()
        assert load_config().redis.key_prefix == prefix


@pytest.mark.parametrize("prefix", ["", "app:"])
def test_cache_operations_share_namespace_without_changing_values(monkeypatch, prefix):
    from plugins_market.core import cache

    client = MagicMock()
    client.get.return_value = "value"
    client.incr.return_value = 1
    monkeypatch.setattr(cache, "_make_client", lambda: RedisClient(client, prefix))
    monkeypatch.setattr(settings, "redis_key_prefix", prefix)
    assert cache.cache_get("key") == "value"
    cache.cache_set("key", "value", 30)
    cache.cache_set_nx("key", 60)
    cache.cache_set_persistent("key", "value")
    cache.cache_incr("key", 90)
    cache.cache_delete("key")
    client.get.assert_called_once_with(prefix + "key")
    client.setex.assert_called_once_with(prefix + "key", 30, "value")
    assert client.set.call_args_list[0].args == (prefix + "key", "1")
    assert client.set.call_args_list[1].args == (prefix + "key", "value")
    client.incr.assert_called_once_with(prefix + "key")
    client.expire.assert_called_once_with(prefix + "key", 90)
    client.delete.assert_called_once_with(prefix + "key")


@pytest.mark.parametrize("prefix", ["", "app:", "app::"])
def test_oauth_getdel_fallback_pipeline_uses_same_namespace(monkeypatch, prefix):
    from plugins_market.core import oauth_session_store

    monkeypatch.setattr(settings, "redis_key_prefix", prefix)
    monkeypatch.setattr(settings, "redis_host", "redis.test")
    monkeypatch.setattr(oauth_session_store, "_redis_store", None)
    monkeypatch.setattr(oauth_session_store, "_redis_init_attempted", False)
    client = MagicMock()
    monkeypatch.setattr("redis.Redis", MagicMock(return_value=client))
    client.getdel.side_effect = RuntimeError("old Redis")
    pipe = client.pipeline.return_value
    pipe.execute.return_value = ["token", 1]
    store = oauth_session_store.get_oauth_str_store()
    store.set_ex("pending", "token", 60)
    assert store.get_del("pending") == "token"
    client.setex.assert_called_once_with(prefix + "pending", 60, "token")
    client.getdel.assert_called_once_with(prefix + "pending")
    pipe.get.assert_called_once_with(prefix + "pending")
    pipe.delete.assert_called_once_with(prefix + "pending")


@pytest.mark.parametrize("prefix", ["", "app:", "app::"])
def test_playground_pipeline_lua_and_message_counter(monkeypatch, prefix):
    from plugins_market.core.playground_state import (
        PlaygroundSessionRecord,
        RedisPlaygroundStateStore,
    )

    monkeypatch.setattr(settings, "redis_key_prefix", prefix)
    client = MagicMock()
    pipe = client.pipeline.return_value
    pipe.execute = AsyncMock()
    client.hgetall = AsyncMock(
        return_value={"user_id": "u", "token": "t", "runner": "url"}
    )
    client.smembers = AsyncMock(return_value={"s", "stale"})
    client.exists = AsyncMock(side_effect=[True, False])
    client.srem = AsyncMock()
    client.set = AsyncMock(return_value=True)
    client.eval = AsyncMock()
    client.incr = AsyncMock(return_value=1)
    client.expire = AsyncMock()
    monkeypatch.setattr("redis.asyncio.Redis", MagicMock(return_value=client))
    store = RedisPlaygroundStateStore(host="redis.test", port=6379, db=0, password="", key_prefix=prefix)

    async def run():
        await store.register_session("s", PlaygroundSessionRecord("u", "t", "url"), 100)
        assert await store.get_session("s") == PlaygroundSessionRecord("u", "t", "url")
        await store.list_user_sessions("u")
        async with store.create_lock("u"):
            pass
        assert await store.allow_message("u", 10, 60)
        await store.release_session("s")

    asyncio.run(run())
    pipe.hset.assert_called_once_with(
        (prefix + "pg:sess:s"), mapping={"user_id": "u", "token": "t", "runner": "url"}
    )
    pipe.sadd.assert_called_once_with((prefix + "pg:usess:u"), "s")
    pipe.delete.assert_called_once_with(prefix + "pg:sess:s")
    assert client.set.call_args.args[0] == (prefix + "pg:clk:u")
    assert client.eval.call_args.args[2:] == (
        (prefix + "pg:clk:u"),
        client.set.call_args.args[1],
    )
    assert client.incr.call_args.args[0].startswith(prefix + "pg:msg:u:")
    assert client.expire.call_args.args[0] == client.incr.call_args.args[0]


@pytest.mark.parametrize("prefix", ["", "app:", "app::"])
@pytest.mark.parametrize(
    "business_prefix", ["skill_rec:user", "skill_rec:user::", "", ":", "app:user"]
)
def test_recommendation_writer_reader_and_gc_use_same_namespace(
    monkeypatch, tmp_path, prefix, business_prefix
):
    from recommender.offline.redis_sync.tasks import topk_install, user_sequences
    from recommender.online import redis_seeds

    cfg = replace(
        load_config(),
        redis=replace(
            load_config().redis,
            key_prefix=prefix,
            user_seq=replace(load_config().redis.user_seq, key_prefix=business_prefix),
        ),
    )
    logical_prefix = business_prefix.rstrip(":")
    client = MagicMock()
    stored = {}

    def store_value(key, value, **_kwargs):
        stored[key] = value

    client.set.side_effect = store_value
    client.get.side_effect = stored.get
    pipe = client.pipeline.return_value
    pipe.set.side_effect = client.set.side_effect
    client.smembers.return_value = {"old-user"}
    client.sismember.return_value = True
    monkeypatch.setattr(topk_install, "create_redis_client", lambda _: RedisClient(client, prefix))
    monkeypatch.setattr(user_sequences, "create_redis_client", lambda _: RedisClient(client, prefix))
    monkeypatch.setattr(
        topk_install, "fetch_ranked_by_install_count", lambda *a, **k: []
    )
    monkeypatch.setattr(
        user_sequences, "fetch_download_sequences", lambda *a, **k: {"u": ["asset"]}
    )
    monkeypatch.setattr(
        user_sequences, "fetch_interaction_sequences", lambda *a, **k: {}
    )
    monkeypatch.setattr(topk_install, "SUMMARY_PATH", tmp_path / "topk.json")
    monkeypatch.setattr(user_sequences, "SUMMARY_PATH", tmp_path / "users.json")
    monkeypatch.setattr(
        redis_seeds, "_execute_redis", lambda cfg_arg, op: op(RedisClient(client, prefix), cfg_arg)
    )
    topk_install.run_topk_install(cfg)
    user_sequences.run_user_sequences(cfg)
    assert prefix + cfg.redis.topk_install.key in stored
    download_sequence = stored.get(prefix + logical_prefix + ":u:download")
    assert download_sequence is not None
    assert json.loads(download_sequence) == ["asset"]
    assert redis_seeds.load_user_seed_ids("u", redis_cfg=cfg.redis) == ["asset"]
    redis_seeds.load_topk_install_items(5, redis_cfg=cfg.redis)
    assert client.get.call_args.args[0] == prefix + cfg.redis.topk_install.key
    pipe.sadd.assert_called_once_with((prefix + logical_prefix + ":_index"), "u")
    assert client.delete.call_args.args == tuple(
        prefix + logical_prefix + ":old-user:" + kind
        for kind in ("download", "like", "star")
    )
    assert set(stored) == {prefix + cfg.redis.topk_install.key} | {
        prefix + logical_prefix + ":u:" + kind for kind in ("download", "like", "star")
    }


@pytest.mark.parametrize(
    "change",
    [
        {"host": "other-host"},
        {"port": 6380},
        {"db": 1},
        {"ssl": True},
        {"password": "other-password"},
        {"username": "other-user"},
        {"key_prefix": "other-prefix"},
    ],
)
def test_recommender_client_cache_separates_connection_and_namespace(monkeypatch, change):
    from recommender.online import redis_seeds

    client = MagicMock()
    other_client = MagicMock()
    create = MagicMock(side_effect=[client, other_client])
    monkeypatch.setattr(redis_seeds, "create_redis_client", create)
    cfg = replace(
        load_config().redis,
        host="redis.test",
        port=6379,
        db=0,
        ssl=False,
        password="",
        username="",
        key_prefix="",
    )
    redis_seeds.reset_redis_clients()
    try:
        first, _ = redis_seeds.get_redis_client(cfg)
        same, _ = redis_seeds.get_redis_client(replace(cfg))
        different, _ = redis_seeds.get_redis_client(replace(cfg, **change))
        assert first is same is client
        assert different is other_client
        assert create.call_count == 2
    finally:
        redis_seeds.reset_redis_clients()


@pytest.mark.parametrize("prefix", ["", "app:", "app::"])
def test_milvus_lock_acquire_renew_release_use_same_key(monkeypatch, prefix):
    from recommender.offline.milvus_index import lock

    cfg = replace(load_config(), redis=replace(load_config().redis, key_prefix=prefix))
    client = MagicMock()
    monkeypatch.setattr(lock, "create_redis_client", lambda _: RedisClient(client, prefix))
    stop = MagicMock()
    stop.wait.side_effect = [False, True]
    monkeypatch.setattr(lock.threading, "Event", MagicMock(return_value=stop))

    def make_renewer(*, target, args, **_kwargs):
        renewer = MagicMock()

        def start_renewer():
            target(*args)

        renewer.start.side_effect = start_renewer
        return renewer

    monkeypatch.setattr(lock.threading, "Thread", make_renewer)
    with lock.milvus_index_lock(cfg) as handle:
        assert handle.proceed
    key = prefix + lock.LOCK_KEY
    assert client.set.call_args.args[0] == key
    owner = client.set.call_args.args[1]
    renew_call, release_call = client.eval.call_args_list
    assert renew_call.args[2:] == (key, owner, str(lock.LOCK_TTL_SECONDS))
    assert release_call.args[2:] == (key, owner)
    stop.set.assert_called_once()


@pytest.mark.parametrize("prefix", ["", "app:", "app::"])
def test_shared_index_state_does_not_read_other_namespace(tmp_path, prefix):
    from recommender.offline.milvus_index import state

    data = {}
    client = MagicMock()
    client.set.side_effect = data.__setitem__
    client.get.side_effect = data.get
    expected = state.save_state_shared({}, tmp_path / "state.json", RedisClient(client, prefix))
    assert set(data) == {prefix + state.MILVUS_INDEX_STATE_KEY}
    remote_only_path = tmp_path / "remote-only.json"
    assert state.load_state_shared(remote_only_path, RedisClient(client, "other")) == state.IndexState.empty()
    assert state.load_state_shared(remote_only_path, RedisClient(client, prefix)) == expected


@pytest.mark.parametrize("prefix", ["", "app:", "app::"])
def test_stream_group_read_and_ack_use_namespaced_stream(monkeypatch, prefix):
    from plugins_market.retrieval import reload_consumer as reload

    monkeypatch.setattr(settings, "redis_key_prefix", prefix)
    monkeypatch.setattr(reload, "_apply_reload", lambda *args: None)
    client = MagicMock()
    client.xreadgroup.side_effect = [
        [
            (
                (prefix + "index:reload").encode(),
                [(b"1-0", {b"group": b"skill", b"index_path": b"path"})],
            )
        ],
        asyncio.CancelledError(),
    ]
    asyncio.run(reload.run_reload_consumer(object(), RedisClient(client, prefix)))
    assert client.xgroup_create.call_args.args[0] == (prefix + "index:reload")
    assert client.xreadgroup.call_args.args[2] == {(prefix + "index:reload"): ">"}
    assert client.xack.call_args.args[0] == (prefix + "index:reload")
    assert client.xack.call_args.args[2] == b"1-0"


def test_channel_namespaces_are_distinct(monkeypatch):
    raw = MagicMock()
    RedisClient(raw, "app").publish("events", "value")
    RedisClient(raw, "other").publish("events", "value")
    assert raw.publish.call_args_list[0].args == ("app:events", "value")
    assert raw.publish.call_args_list[1].args == ("other:events", "value")


def test_cache_namespaces_isolate_values_and_deletes(monkeypatch):
    prefix = "app:"
    from plugins_market.core import cache

    data = {"key": "legacy", "other:key": "other app"}
    client = MagicMock()
    client.get.side_effect = data.get
    client.set.side_effect = data.__setitem__

    def delete_value(key):
        return data.pop(key, None)

    client.delete.side_effect = delete_value
    monkeypatch.setattr(cache, "_make_client", lambda: RedisClient(client, prefix))
    monkeypatch.setattr(settings, "redis_key_prefix", "app:")
    assert cache.cache_get("key") is None
    cache.cache_set_persistent("key", "our app")
    assert cache.cache_get("key") == "our app"
    cache.cache_delete("key")
    assert data == {"key": "legacy", "other:key": "other app"}


@pytest.mark.parametrize("prefix", ["", "app:", "app::"])
def test_embedding_counter_uses_same_key_for_increment_expire_and_release(
    monkeypatch, prefix
):
    import retrieval  # noqa: F401 -- initializes the canonical indexing package path
    from indexing.embedding import index

    client = MagicMock()
    client.incr.side_effect = [1, index.OFFLINE_EMBED_MAX_PER_MINUTE + 1, 1]
    monkeypatch.setattr(index, "_slot_redis", client)
    monkeypatch.setattr(index.time, "time", lambda: 120.0)
    monkeypatch.setattr(index.time, "sleep", lambda _: None)
    index.configure_offline_embedding_redis(RedisClient(client, prefix))
    model = MagicMock()
    model.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(embedding=[1.0, 0.0])]
    )
    embedding = index.OpenAIEmbeddingClient(client=model, model="test-model", min_interval_s=0.8)
    assert embedding.embed_text("first") == [1.0, 0.0]
    assert embedding.embed_text("second") == [1.0, 0.0]
    assert all(
        call.args == ((prefix + "embedding:offline:rpm:2"),)
        for call in client.incr.call_args_list
    )
    assert all(
        call.args == ((prefix + "embedding:offline:rpm:2"), 120)
        for call in client.expire.call_args_list
    )
    client.decr.assert_called_once_with(prefix + "embedding:offline:rpm:2")


@pytest.mark.parametrize("prefix", ["", "app:", "app::"])
def test_retrieval_broadcast_and_lock_are_namespaced(monkeypatch, prefix):
    import retrieval  # noqa: F401 -- initializes the canonical indexing package path
    from indexing.workflows.index_builder import IndexBuilder
    from plugins_market.retrieval import daily_rebuild as rebuild

    monkeypatch.setattr(settings, "redis_key_prefix", prefix)
    monkeypatch.setattr(
        rebuild,
        "_fetch_valid_item_records",
        lambda *a, **kw: [
            SimpleNamespace(item_path="obs://bucket/skill.zip", metadata={}),
        ],
    )
    monkeypatch.setattr(
        IndexBuilder, "build", lambda *a, **kw: "obs://bucket/index/new"
    )
    monkeypatch.setattr(rebuild, "_gc_old_indexes", lambda *a, **kw: None)
    client = MagicMock()
    rebuild.rebuild_one_group(
        group="skill",
        db=MagicMock(),
        group_prefix="index",
        storage=SimpleNamespace(config=SimpleNamespace(bucket_name="bucket")),
        redis_client=RedisClient(client, prefix),
        run_skill_tag=False,
    )
    client.xadd.assert_called_once_with(
        (prefix + "index:reload"),
        {
            "group": "skill",
            "index_path": "obs://bucket/index/new",
        },
    )
    monkeypatch.setattr(rebuild, "rebuild_one_group", lambda **kw: None)
    rebuild.rebuild_all(
        db_factory=MagicMock(),
        storage=MagicMock(),
        skill_prefix="skill",
        plugin_prefix="plugin",
        redis_client=RedisClient(client, prefix),
    )
    assert client.set.call_args.args[0] == (prefix + "retrieval:rebuild:lock")
    assert client.set.call_args.kwargs.get("nx") is True
    assert client.set.call_args.kwargs.get("ex") == rebuild._REBUILD_LOCK_TTL_SECONDS
    owner = client.set.call_args.args[1]
    assert client.eval.call_args.args == (
        rebuild._RELEASE_LOCK_LUA,
        1,
        (prefix + "retrieval:rebuild:lock"),
        owner,
    )
    client.delete.assert_not_called()


@pytest.mark.parametrize("prefix", ["", "app:", "app::"])
@pytest.mark.parametrize("kind", ["skill", "agent"])
def test_tag_refresh_locks_use_namespace_on_acquire_and_release(
    monkeypatch, prefix, kind
):
    from plugins_market.retrieval import daily_rebuild as rebuild

    monkeypatch.setattr(settings, "redis_key_prefix", prefix)
    monkeypatch.setattr(rebuild, "_fetch_valid_item_paths", lambda *a, **kw: [])
    monkeypatch.setattr(rebuild, "_fetch_valid_item_records", lambda *a, **kw: [])
    client = MagicMock()
    common = {"db_factory": MagicMock(), "storage": MagicMock(), "redis_client": RedisClient(client, prefix)}
    if kind == "skill":
        rebuild.refresh_skill_tags(
            rebuild.SkillTagRefreshOptions(skill_prefix="skills", **common)
        )
    else:
        rebuild.refresh_agent_tags(
            rebuild.AgentTagRefreshOptions(agent_prefixes={"agent": "agents"}, **common)
        )
    expected = prefix + "retrieval:" + kind + "-tag:lock"
    assert client.set.call_args.args[0] == expected
    assert client.set.call_args.args[1] != "1"
    assert client.set.call_args.kwargs.get("nx") is True
    assert client.set.call_args.kwargs.get("ex") == rebuild._REBUILD_LOCK_TTL_SECONDS
    assert client.eval.call_args.args == (
        rebuild._RELEASE_LOCK_LUA,
        1,
        expected,
        client.set.call_args.args[1],
    )
    client.delete.assert_not_called()


@pytest.mark.parametrize("value", [None, "", "  ", "app", "app:", "app::"])
def test_config_sources_normalize_identically(monkeypatch, tmp_path, value):
    monkeypatch.delenv("REDIS_KEY_PREFIX", raising=False)
    env_file = tmp_path / "marketplace.env"
    env_file.write_text(
        "" if value is None else f'REDIS_KEY_PREFIX="{value}"\n', encoding="utf-8"
    )
    expected = normalize_redis_key_prefix(value)
    assert Settings(_env_file=env_file).redis_key_prefix == expected
    if value is not None:
        monkeypatch.setenv("REDIS_KEY_PREFIX", value)
    assert Settings(_env_file=None).redis_key_prefix == expected
    assert load_config().redis.key_prefix == expected
