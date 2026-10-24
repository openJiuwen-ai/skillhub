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
