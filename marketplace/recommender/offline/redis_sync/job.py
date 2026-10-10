"""Run one or all Redis sync writers."""

from __future__ import annotations

import logging
from typing import Any

from recommender.shared.config import AppConfig, RedisConfig, load_config, load_redis_config

from .tasks import REDIS_TASKS

logger = logging.getLogger(__name__)


def run_redis_task(
    name: str,
    app_cfg: AppConfig | None = None,
    redis_cfg: RedisConfig | None = None,
) -> Any:
    if name not in REDIS_TASKS:
        known = ", ".join(sorted(REDIS_TASKS))
        raise KeyError(f"Unknown redis task {name!r}. Known: {known}")
    app_cfg = app_cfg or load_config()
    redis_cfg = redis_cfg or app_cfg.redis
    task = REDIS_TASKS[name]
    logger.info("Redis task: %s (%s)", task.name, task.description)
    return task.run(app_cfg, redis_cfg)


def run_redis_sync(
    app_cfg: AppConfig | None = None,
    redis_cfg: RedisConfig | None = None,
) -> dict[str, Any]:
    """Run every registered Redis writer (hourly bundle entrypoint)."""
    app_cfg = app_cfg or load_config()
    redis_cfg = redis_cfg or load_redis_config()
    results: dict[str, Any] = {}
    for name in REDIS_TASKS:
        results[name] = run_redis_task(name, app_cfg, redis_cfg)
    return results
