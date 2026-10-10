"""Offline recommender job runners for APScheduler."""

from __future__ import annotations

import logging
from typing import Any

from recommender.offline.milvus_index.pipeline import run_full_rebuild, run_incremental_index
from recommender.offline.package_sync.job import run_offline_sync
from recommender.offline.redis_sync.job import run_redis_sync
from recommender.shared.config import AppConfig, load_config

logger = logging.getLogger(__name__)


def _cfg() -> AppConfig:
    return load_config()


def run_rec_package_sync() -> Any:
    cfg = _cfg()
    logger.info("recommender job: package_sync begin download_dir=%s", cfg.download_dir)
    summary = run_offline_sync(cfg)
    logger.info(
        "recommender job: package_sync done total=%s downloaded=%s failed=%s",
        summary.total,
        summary.downloaded,
        summary.failed,
    )
    return summary


def run_rec_milvus_incremental() -> Any:
    cfg = _cfg()
    logger.info("recommender job: milvus_incremental begin")
    stats = run_incremental_index(app_cfg=cfg)
    logger.info("recommender job: milvus_incremental done stats=%s", stats)
    return stats


def run_rec_milvus_full() -> Any:
    cfg = _cfg()
    logger.info("recommender job: milvus_full begin")
    stats = run_full_rebuild(app_cfg=cfg)
    logger.info("recommender job: milvus_full done stats=%s", stats)
    return stats


def run_rec_redis_sync() -> Any:
    cfg = _cfg()
    logger.info("recommender job: redis_sync begin")
    results = run_redis_sync(app_cfg=cfg, redis_cfg=cfg.redis)
    logger.info("recommender job: redis_sync done keys=%s", list(results.keys()))
    return results
