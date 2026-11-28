# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import time

import pytest

import plugins_market.core.oauth_session_store as store


class FlakyRedisStore:
    """Fails on the first construction, succeeds afterwards."""

    attempts = 0

    def __init__(self, **kwargs) -> None:
        FlakyRedisStore.attempts += 1
        if FlakyRedisStore.attempts == 1:
            raise RuntimeError("connection refused")
        self.kwargs = kwargs

        self.data: dict[str, str] = {}

    def set_ex(self, key: str, value: str, ttl_seconds: int) -> None:
        self.data[key] = value


@pytest.fixture()
def redis_settings(monkeypatch) -> None:
    FlakyRedisStore.attempts = 0
    monkeypatch.setattr(store, "_redis_store", None)
    monkeypatch.setattr(store, "_redis_init_failed_at", None)
    monkeypatch.setattr(store.settings, "redis_host", "127.0.0.1")
    monkeypatch.setattr(store.settings, "redis_port", 6379)
    monkeypatch.setattr(store.settings, "redis_db", 2)
    monkeypatch.setattr(store.settings, "redis_username", "")
    monkeypatch.setattr(store.settings, "redis_key_prefix", "")
    monkeypatch.setattr(store, "_resolve_redis_password", lambda: "pw")
    monkeypatch.setattr("plugins_market.core.redis_client.resolve_redis_ssl", lambda: False)
    # 测试中把后台建连线程替换为同步执行，保证断言确定性。
    monkeypatch.setattr(
        store, "_spawn_redis_init_thread", lambda host: store._connect_redis_in_background(host)
    )


def test_retries_redis_after_cooldown(redis_settings, monkeypatch) -> None:
    monkeypatch.setattr(store, "_RedisOAuthStore", FlakyRedisStore)

    first = store.get_oauth_str_store()
    assert first is store._memory
    assert FlakyRedisStore.attempts == 1

    second = store.get_oauth_str_store()
    assert second is store._memory
    assert FlakyRedisStore.attempts == 1

    monkeypatch.setattr(
        store,
        "_redis_init_failed_at",
        time.monotonic() - store._REDIS_RETRY_INTERVAL_SECONDS - 1,
    )
    third = store.get_oauth_str_store()
    assert isinstance(third, FlakyRedisStore)
    assert FlakyRedisStore.attempts == 2
    assert store._redis_init_failed_at is None

    fourth = store.get_oauth_str_store()
    assert fourth is third
    assert FlakyRedisStore.attempts == 2


def test_memory_entries_migrate_to_redis_on_retry_success(
    redis_settings, monkeypatch
) -> None:
    monkeypatch.setattr(store, "_RedisOAuthStore", FlakyRedisStore)

    now = time.monotonic()
    with store._memory._lock:
        store._memory._data["k_live"] = (now + 100, "v_live")
        store._memory._data["k_dead"] = (now - 1, "v_dead")

    assert store.get_oauth_str_store() is store._memory

    monkeypatch.setattr(
        store,
        "_redis_init_failed_at",
        time.monotonic() - store._REDIS_RETRY_INTERVAL_SECONDS - 1,
    )
    got = store.get_oauth_str_store()

    assert isinstance(got, FlakyRedisStore)
    assert got.data["k_live"] == "v_live"
    assert "k_dead" not in got.data


def test_no_host_uses_memory_without_attempt(redis_settings, monkeypatch) -> None:
    monkeypatch.setattr(store, "_RedisOAuthStore", FlakyRedisStore)
    monkeypatch.setattr(store.settings, "redis_host", "")

    got = store.get_oauth_str_store()

    assert got is store._memory
    assert FlakyRedisStore.attempts == 0
