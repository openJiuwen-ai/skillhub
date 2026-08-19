"""Online recommender helpers used by API + plugin list."""

from __future__ import annotations

import logging
import uuid

from recommender.online.service import (
    recommend_by_ids,
    recommend_by_queries,
    recommend_for_user,
    rerank_mmr,
)
from recommender.online.types import RecommendItem

logger = logging.getLogger(__name__)


def run_recommend_for_user(
    *,
    user_id: str,
    top_k: int,
    request_id: str = "",
    timestamp: int | float | None = None,
    category_id: str = "",
    plugin_type: str = "",
) -> tuple[list[RecommendItem], str]:
    rid = (request_id or "").strip() or str(uuid.uuid4())
    return recommend_for_user(
        user_id=user_id or "",
        top_k=top_k,
        request_id=rid,
        timestamp=timestamp,
        category_id=(category_id or "").strip() or None,
        plugin_type=(plugin_type or "").strip() or None,
    )


def run_recommend_by_ids(
    asset_ids: list[str],
    top_k: int,
    *,
    category_id: str = "",
    plugin_type: str = "",
) -> list[RecommendItem]:
    return recommend_by_ids(
        asset_ids,
        top_k,
        category_id=(category_id or "").strip() or None,
        plugin_type=(plugin_type or "").strip() or None,
    )


def run_recommend_by_queries(
    queries: list[str],
    top_k: int,
    *,
    category_id: str = "",
    plugin_type: str = "",
) -> list[RecommendItem]:
    return recommend_by_queries(
        queries,
        top_k,
        category_id=(category_id or "").strip() or None,
        plugin_type=(plugin_type or "").strip() or None,
    )


def run_rerank_mmr(items: list[dict], top_k: int | None) -> list[RecommendItem]:
    return rerank_mmr(items, top_k=top_k)
