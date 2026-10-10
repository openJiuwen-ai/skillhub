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


def warm_recommend_online() -> None:
    """Open Redis (and hydrate a plaza-sized page) so the first client is not the warmup."""
    from plugins_market.core.database import SessionLocal
    from plugins_market.core.s3_storage_client import get_storage_client
    from plugins_market.core.viewer_context import ANONYMOUS_VIEWER
    from plugins_market.recommender import anon_card_cache
    from plugins_market.recommender.bootstrap import apply_recommender_settings_to_env
    from plugins_market.recommender.schemas import RecommendItemOut
    from plugins_market.services.plugin import filter_recommend_ranked_ids, hydrate_plugin_list_items

    apply_recommender_settings_to_env()
    storage = get_storage_client()
    plaza_top_k = anon_card_cache.plaza_cache_top_k()
    recall_k = min(500, max(plaza_top_k * 2, plaza_top_k))
    for plugin_type in ("swarmskill", "skill"):
        items, source = run_recommend_for_user(
            user_id="",
            top_k=recall_k,
            plugin_type=plugin_type,
        )
        ranked_ids = [
            str(x.asset_id).strip() for x in items if str(x.asset_id or "").strip()
        ]
        db = SessionLocal()
        try:
            visible_ids = filter_recommend_ranked_ids(
                ranked_ids,
                plugin_type=plugin_type,
                db=db,
                viewer=ANONYMOUS_VIEWER,
            )[:plaza_top_k]
            cards = hydrate_plugin_list_items(
                visible_ids,
                db=db,
                storage=storage,
                viewer=ANONYMOUS_VIEWER,
                market_public_scoped=True,
            )
        finally:
            db.close()
        scores = {str(x.asset_id): float(x.score) for x in items if str(x.asset_id or "").strip()}
        out_items = [
            RecommendItemOut.model_validate(
                {**card.model_dump(), "score": scores.get(card.asset_id, 0.0)}
            ).model_dump()
            for card in cards
        ]
        stored = anon_card_cache.put(
            anon_card_cache.cache_key(plugin_type, ""),
            source,
            out_items,
        )
        logger.info(
            "recommender online warm-start plugin_type=%s source=%s cards=%d cached=%s",
            plugin_type,
            source,
            len(out_items),
            stored,
        )
