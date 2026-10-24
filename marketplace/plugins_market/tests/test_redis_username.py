# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Redis ACL 用户名可配置；留空时不传 username。"""

from __future__ import annotations

from unittest.mock import patch

from plugins_market.core import redis_client
from recommender.offline.redis_sync.client import create_redis_client
from recommender.shared.config import RedisConfig, TopKInstallSettings, UserSeqSettings


def _patch_redis_settings(monkeypatch, username: str) -> None:
    monkeypatch.setattr(redis_client.settings, "redis_username", username)
    monkeypatch.setattr(redis_client.settings, "redis_host", "127.0.0.1")
    monkeypatch.setattr(redis_client.settings, "redis_port", 6379)
    monkeypatch.setattr(redis_client.settings, "redis_db", 0)
    monkeypatch.setattr(
        redis_client.SecurityUtils,
        "get_decrypt_secret",
        staticmethod(lambda env_key, default="": ""),
    )


def test_connection_kwargs_include_username(monkeypatch) -> None:
    _patch_redis_settings(monkeypatch, "skillhub")
    kwargs = redis_client.redis_connection_kwargs()
    assert kwargs["username"] == "skillhub"
    assert "password" not in kwargs


def test_connection_kwargs_omit_blank_username(monkeypatch) -> None:
    _patch_redis_settings(monkeypatch, "  ")
    kwargs = redis_client.redis_connection_kwargs()
    assert "username" not in kwargs


def test_oauth_store_passes_acl_username(monkeypatch) -> None:
    import plugins_market.core.oauth_session_store as store

    monkeypatch.setattr(store, "_redis_store", None)
    monkeypatch.setattr(store, "_redis_init_attempted", False)
    monkeypatch.setattr(store.settings, "redis_host", "127.0.0.1")
    monkeypatch.setattr(store.settings, "redis_port", 6379)
    monkeypatch.setattr(store.settings, "redis_db", 2)
    monkeypatch.setattr(store.settings, "redis_username", "acl-user")
    monkeypatch.setattr(store, "_resolve_redis_password", lambda: "pw")
    monkeypatch.setattr("plugins_market.core.redis_client.resolve_redis_ssl", lambda: False)

    with patch("redis.Redis") as client:
        got = store.get_oauth_str_store()

    assert type(got).__name__ == "_RedisOAuthStore"
    assert client.call_args.kwargs["username"] == "acl-user"
    assert client.call_args.kwargs["password"] == "pw"


def test_oauth_store_omits_blank_username(monkeypatch) -> None:
    import plugins_market.core.oauth_session_store as store

    monkeypatch.setattr(store, "_redis_store", None)
    monkeypatch.setattr(store, "_redis_init_attempted", False)
    monkeypatch.setattr(store.settings, "redis_host", "127.0.0.1")
    monkeypatch.setattr(store.settings, "redis_port", 6379)
    monkeypatch.setattr(store.settings, "redis_db", 2)
    monkeypatch.setattr(store.settings, "redis_username", "  ")
    monkeypatch.setattr(store, "_resolve_redis_password", lambda: "pw")
    monkeypatch.setattr("plugins_market.core.redis_client.resolve_redis_ssl", lambda: False)

    with patch("redis.Redis") as client:
        store.get_oauth_str_store()

    assert "username" not in client.call_args.kwargs


def test_playground_store_passes_acl_username(monkeypatch) -> None:
    import plugins_market.core.playground_state as playground

    monkeypatch.setattr(playground, "_store", None)
    monkeypatch.setattr(playground.settings, "playground_multi_instance", True)
    monkeypatch.setattr(playground.settings, "redis_host", "127.0.0.1")
    monkeypatch.setattr(playground.settings, "redis_port", 6379)
    monkeypatch.setattr(playground.settings, "redis_db", 3)
    monkeypatch.setattr(playground.settings, "redis_username", "acl-user")
    monkeypatch.setattr(playground, "_resolve_redis_password", lambda: "pw")
    monkeypatch.setattr("plugins_market.core.redis_client.resolve_redis_ssl", lambda: True)

    with patch("redis.asyncio.Redis") as client:
        playground.get_playground_state_store()

    kwargs = client.call_args.kwargs
    assert kwargs["username"] == "acl-user"
    assert kwargs["password"] == "pw"
    assert kwargs["ssl"] is True


def test_recommender_client_passes_username() -> None:
    cfg = RedisConfig(
        host="redis.internal",
        port=6379,
        db=0,
        password="secret",
        ssl=False,
        topk_install=TopKInstallSettings(key="k", k=0, ttl_seconds=1, interval_minutes=1),
        user_seq=UserSeqSettings(key_prefix="p", max_len=1, ttl_seconds=1),
        username="acl-user",
    )
    with patch("redis.Redis") as client:
        create_redis_client(cfg)
    assert client.call_args.kwargs["username"] == "acl-user"
