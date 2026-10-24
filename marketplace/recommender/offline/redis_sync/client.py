"""Redis connection helpers (generic; not tied to any one snapshot)."""

from __future__ import annotations

from recommender.shared.config import RedisConfig


def create_redis_client(cfg: RedisConfig):
    import redis

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
    return redis.Redis(**kwargs)
