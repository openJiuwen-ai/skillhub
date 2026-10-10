"""Registry of Redis writers. Add a new module + entry here to extend."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from recommender.shared.config import AppConfig, RedisConfig

from .topk_install import run_topk_install
from .user_sequences import run_user_sequences

RedisTaskFn = Callable[[AppConfig, RedisConfig | None], Any]


@dataclass(frozen=True)
class RedisTask:
    name: str
    description: str
    run: RedisTaskFn


REDIS_TASKS: dict[str, RedisTask] = {
    "topk_install": RedisTask(
        name="topk_install",
        description="install_count TopK snapshot -> Redis (SET + TTL overwrite)",
        run=lambda app_cfg, redis_cfg=None: run_topk_install(app_cfg, redis_cfg),
    ),
    "user_sequences": RedisTask(
        name="user_sequences",
        description="per-user download/like/star asset_id sequences -> Redis",
        run=lambda app_cfg, redis_cfg=None: run_user_sequences(app_cfg, redis_cfg),
    ),
}
