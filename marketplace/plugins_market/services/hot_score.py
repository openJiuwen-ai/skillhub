# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""火爆值（hot_score）离线重算。

聚合近期下载流水 + 现有统计列 -> 加权对数公式 -> 批量回写 market_assets.hot_score。
由 APScheduler 定时调用（不受 recommender_enabled 控制，属于核心市场功能）。
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import sessionmaker

from plugins_market.models.market_assets import MarketAssetDB, PluginFetchRecordDB

logger = logging.getLogger(__name__)

# 近期下载统计窗口（天）：业界 marketplace trending 标准约 7 天，长到能平滑日级噪声、短到反映本周热度
RECENT_DAYS = 7
# 评分基线：average_rating 默认 8.0，只取超出部分
RATING_BASELINE = 8.0

# 加权对数公式权重（可按需调整）
W_RECENT_DL = 100.0  # 近期下载（最强趋势信号）
W_TOTAL_DL = 10.0  # 累计下载（已建立的人气底座）
W_VIEW = 5.0  # 累计浏览（关注度）
W_SOCIAL = 2.0  # 点赞 + 收藏（社交互动）
W_RATING = 1.0  # 评分超出基线的质量信号


def compute_hot_score(
    *,
    recent_downloads: int,
    install_count: int,
    view_count: int,
    like_count: int,
    star_count: int,
    average_rating: float,
) -> float:
    """加权对数综合分：近期下载 + 累计下载 + 浏览 + 互动 + 评分。"""
    return round(
        W_RECENT_DL * math.log10(1 + recent_downloads)
        + W_TOTAL_DL * math.log10(1 + install_count)
        + W_VIEW * math.log10(1 + view_count)
        + W_SOCIAL * math.log10(1 + like_count + star_count)
        + W_RATING * max(0.0, average_rating - RATING_BASELINE),
        2,
    )


def recompute_hot_scores(db_factory: sessionmaker) -> dict[str, Any]:
    """重算全部非 OFFLINE 资产的 hot_score 并回写。

    仅更新 hot_score 列，不触碰 update_time。返回统计摘要。
    """
    started = time.monotonic()
    db = db_factory()
    try:
        cutoff_ms = int((time.time() - RECENT_DAYS * 86400) * 1000)

        # 1. 聚合近期下载量（近 RECENT_DAYS 天），按 asset_id 分组
        recent_rows = (
            db.query(
                PluginFetchRecordDB.asset_id,
                func.count().label("cnt"),
            )
            .filter(PluginFetchRecordDB.create_time > cutoff_ms)
            .group_by(PluginFetchRecordDB.asset_id)
            .all()
        )
        recent_dl_map: dict[str, int] = {row[0]: int(row[1]) for row in recent_rows}

        # 2. 读取全部非 OFFLINE 资产的统计列
        assets = (
            db.query(
                MarketAssetDB.asset_id,
                MarketAssetDB.install_count,
                MarketAssetDB.view_count,
                MarketAssetDB.like_count,
                MarketAssetDB.star_count,
                MarketAssetDB.average_rating,
            )
            .filter(or_(MarketAssetDB.status.is_(None), MarketAssetDB.status != "OFFLINE"))
            .all()
        )

        # 3. 逐条计算 hot_score
        mappings: list[dict[str, Any]] = []
        for asset_id, install_count, view_count, like_count, star_count, average_rating in assets:
            score = compute_hot_score(
                recent_downloads=recent_dl_map.get(asset_id, 0),
                install_count=int(install_count or 0),
                view_count=int(view_count or 0),
                like_count=int(like_count or 0),
                star_count=int(star_count or 0),
                average_rating=float(average_rating or RATING_BASELINE),
            )
            mappings.append({"asset_id": asset_id, "hot_score": score})

        # 4. 分批回写（仅更新 hot_score，不触碰 update_time），避免单条 SQL 过长
        batch_size = 1000
        for i in range(0, len(mappings), batch_size):
            db.bulk_update_mappings(MarketAssetDB, mappings[i:i + batch_size])
            db.flush()
        db.commit()

        elapsed = time.monotonic() - started
        logger.info(
            "hot_score recompute done: assets=%d recent_dl_assets=%d updated=%d elapsed=%.1fs",
            len(assets),
            len(recent_dl_map),
            len(mappings),
            elapsed,
        )
        return {
            "updated": len(mappings),
            "assets": len(assets),
            "recent_dl_assets": len(recent_dl_map),
            "elapsed": round(elapsed, 1),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
