"""Redis connection helpers (generic; not tied to any one snapshot)."""

from __future__ import annotations

from common.redis_client import create_redis_client as _create_redis_client
from recommender.shared.config import RedisConfig


def create_redis_client(cfg: RedisConfig):
    kwargs = {
        "host": cfg.host,
        "port": cfg.port,
        "db": cfg.db,
        "password": cfg.password or None,
        "ssl": cfg.ssl,
        "decode_responses": True,
        "socket_connect_timeout": 2.0,
        "socket_timeout": 2.0,
        "health_check_interval": 30,
    }
    username = (cfg.username or "").strip()
    if username:
        kwargs["username"] = username
    return _create_redis_client(key_prefix=cfg.key_prefix, **kwargs)
