# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Recommender HTTP API (external + SkillHub)."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, status

from plugins_market.core.auth import resolve_viewer_context
from plugins_market.core.config import settings
from plugins_market.core.database import SessionLocal
from plugins_market.core.errors import auth_error_payload, http_error_payload
from plugins_market.core.logging import get_logger
from plugins_market.core.s3_storage_client import get_storage_client
from plugins_market.core.viewer_context import ANONYMOUS_VIEWER, ViewerContext
from plugins_market.recommender import anon_card_cache
from plugins_market.recommender.plaza_gate import empty_recommend_data, plugin_type_on_market
from plugins_market.recommender.schemas import (
    ByIdsRequest,
    ByQueriesRequest,
    RecommendData,
    RecommendItemOut,
    RecommendItemsData,
    RecommendRequest,
    RerankMmrRequest,
)
from plugins_market.recommender.service import (
    run_recommend_by_ids,
    run_recommend_by_queries,
    run_recommend_for_user,
    run_rerank_mmr,
)
from plugins_market.schemas.common import ResponseModel
from plugins_market.services.plugin import filter_recommend_ranked_ids, hydrate_plugin_list_items

logger = get_logger(__name__)

router = APIRouter(prefix="/recommend", tags=["recommend"])

_plugin_type_on_market = plugin_type_on_market


def _ensure_enabled() -> None:
    if not settings.recommender_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=http_error_payload(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                message="recommender is disabled (set MARKET_RECOMMENDER_ENABLED=true)",
                error="recommender_disabled",
            ),
        )


def _recommend_service_error() -> HTTPException:
    """500 for clients: generic message only; details stay in server logs."""
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=http_error_payload(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="recommend service error",
            error="recommend_failed",
        ),
    )


def _resolve_recommend_user_id(body: RecommendRequest, viewer: ViewerContext) -> str:
    """
    Bind personalization to a verified identity; otherwise cold-start (empty user_id).

    - Valid Bearer: always use token user_id. Body user_id must be empty or match.
    - Valid X-System-Token: may assert any body.user_id (incl. empty = cold start).
    - Missing / invalid Bearer or System Token / header conflict: anonymous.
      Body user_id is ignored so callers cannot spoof another user.
    """
    requested = (body.user_id or "").strip()
    if viewer.is_system_admin:
        return requested
    token_uid = (viewer.user_id or "").strip()
    if token_uid:
        if requested and requested != token_uid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=auth_error_payload(
                    status_code=status.HTTP_403_FORBIDDEN,
                    message="body.user_id must match the authenticated user (or be omitted)",
                    error="recommend_user_mismatch",
                ),
            )
        return token_uid
    if requested:
        logger.info(
            "recommend: unauthenticated caller sent body.user_id=%s; ignoring (cold-start)",
            requested,
        )
    return ""


def _anon_recommend_cacheable(_viewer: ViewerContext, user_id: str) -> bool:
    """Public plaza cards: resolved user_id empty (anon or system-token cold-start).

    Swarm plaza sends X-System-Token with empty body.user_id. That is still the
    same public ranking; hydrate with ANONYMOUS_VIEWER so admin visibility
    never enters the shared cache. Personalized body.user_id is not cached.
    """
    return not (user_id or "").strip()


def _dedupe_ranked_ids(asset_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in asset_ids:
        asset_id = str(raw or "").strip()
        if not asset_id or asset_id in seen:
            continue
        seen.add(asset_id)
        out.append(asset_id)
    return out


def _recommend_data(
    *,
    request_id: str,
    user_id: str,
    source: str,
    category_id: str,
    plugin_type: str,
    items: list[RecommendItemOut],
) -> ResponseModel[RecommendData]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendData(
            request_id=request_id,
            user_id=user_id,
            source=source,
            category_id=category_id,
            plugin_type=plugin_type,
            items=items,
        ),
    )


def _recall_and_hydrate_cards(
    *,
    user_id: str,
    plugin_type: str,
    category_id: str,
    top_k: int,
    request_id: str,
    timestamp: int | float | None,
    storage,
    hydrate_viewer: ViewerContext,
) -> tuple[str, list[RecommendItemOut]]:
    recall_k = min(500, max(top_k * 2, top_k))
    started = time.perf_counter()
    try:
        items, source = run_recommend_for_user(
            user_id=user_id,
            top_k=recall_k,
            request_id=request_id,
            timestamp=timestamp,
            category_id=category_id,
            plugin_type=plugin_type,
        )
        recall_ms = int((time.perf_counter() - started) * 1000)
        scores = {str(x.asset_id): float(x.score) for x in items if str(x.asset_id or "").strip()}
        ranked_ids = _dedupe_ranked_ids([x.asset_id for x in items])
        hydrate_started = time.perf_counter()
        db = SessionLocal()
        try:
            visible_ids = filter_recommend_ranked_ids(
                ranked_ids,
                plugin_type=plugin_type,
                db=db,
                viewer=hydrate_viewer,
            )[:top_k]
            cards = hydrate_plugin_list_items(
                visible_ids,
                db=db,
                storage=storage,
                viewer=hydrate_viewer,
                market_public_scoped=True,
            )
        finally:
            db.close()
        hydrate_ms = int((time.perf_counter() - hydrate_started) * 1000)
        logger.info(
            "recommend hydrate: source=%s user_id=%s ranked=%d visible=%d top_k=%s "
            "recall_ms=%s hydrate_ms=%s",
            source,
            user_id,
            len(ranked_ids),
            len(cards),
            top_k,
            recall_ms,
            hydrate_ms,
        )
        out_items = [
            RecommendItemOut.model_validate(
                {**card.model_dump(), "score": scores.get(card.asset_id, 0.0)}
            )
            for card in cards
        ]
    except Exception as exc:
        logger.exception("recommend failed: %s", exc)
        raise _recommend_service_error() from exc
    return source, out_items


@router.post("", response_model=ResponseModel[RecommendData])
def recommend(
    body: RecommendRequest,
    viewer: ViewerContext = Depends(resolve_viewer_context),
    storage=Depends(get_storage_client),
) -> ResponseModel[RecommendData]:
    """Personalized recommend, then market filter + card hydrate (same as list recommend)."""
    _ensure_enabled()
    user_id = _resolve_recommend_user_id(body, viewer)
    plugin_type = (body.plugin_type or "").strip()
    category_id = (body.category_id or "").strip()
    top_k = int(body.top_k)
    if not _plugin_type_on_market(plugin_type):
        logger.info(
            "recommend skip unknown plugin_type=%s (not on Hub catalog)",
            plugin_type,
        )
        return empty_recommend_data(
            request_id=body.request_id or "",
            user_id=user_id,
            category_id=category_id,
            plugin_type=plugin_type,
        )
    cacheable = _anon_recommend_cacheable(viewer, user_id)
    if cacheable:
        plaza_key = anon_card_cache.cache_key(plugin_type, category_id)
        plaza_page = anon_card_cache.plaza_cache_top_k()
        if top_k <= plaza_page:
            taken = anon_card_cache.take(plaza_key, top_k)
            if taken is not None:
                source, raw_items = taken
                logger.info(
                    "recommend cache hit: source=%s plugin_type=%s category_id=%s "
                    "top_k=%s cached=%d items=%d",
                    source,
                    plugin_type,
                    category_id,
                    top_k,
                    plaza_page,
                    len(raw_items),
                )
            else:

                def _fill_plaza() -> tuple[str, list[dict]]:
                    hit = anon_card_cache.get(plaza_key)
                    if hit is not None:
                        return hit
                    source, out_items = _recall_and_hydrate_cards(
                        user_id="",
                        plugin_type=plugin_type,
                        category_id=category_id,
                        top_k=plaza_page,
                        request_id=body.request_id,
                        timestamp=body.timestamp,
                        storage=storage,
                        hydrate_viewer=ANONYMOUS_VIEWER,
                    )
                    raw_items = [item.model_dump() for item in out_items]
                    anon_card_cache.put(plaza_key, source, raw_items)
                    return source, raw_items

                try:
                    source, cached_items = anon_card_cache.singleflight(plaza_key, _fill_plaza)
                except TimeoutError as exc:
                    logger.exception("recommend plaza singleflight timeout")
                    raise _recommend_service_error() from exc
                raw_items = cached_items[:top_k]
        else:
            source, out_items = _recall_and_hydrate_cards(
                user_id="",
                plugin_type=plugin_type,
                category_id=category_id,
                top_k=top_k,
                request_id=body.request_id,
                timestamp=body.timestamp,
                storage=storage,
                hydrate_viewer=ANONYMOUS_VIEWER,
            )
            raw_items = [item.model_dump() for item in out_items]
            anon_card_cache.put(plaza_key, source, raw_items)
            return _recommend_data(
                request_id=body.request_id or "",
                user_id=user_id,
                source=source,
                category_id=category_id,
                plugin_type=plugin_type,
                items=out_items,
            )
        return _recommend_data(
            request_id=body.request_id or "",
            user_id=user_id,
            source=source,
            category_id=category_id,
            plugin_type=plugin_type,
            items=[RecommendItemOut.model_validate(row) for row in raw_items],
        )

    source, out_items = _recall_and_hydrate_cards(
        user_id=user_id,
        plugin_type=plugin_type,
        category_id=category_id,
        top_k=top_k,
        request_id=body.request_id,
        timestamp=body.timestamp,
        storage=storage,
        hydrate_viewer=viewer,
    )
    return _recommend_data(
        request_id=body.request_id or "",
        user_id=user_id,
        source=source,
        category_id=category_id,
        plugin_type=plugin_type,
        items=out_items,
    )


@router.post("/by_ids", response_model=ResponseModel[RecommendItemsData])
def recommend_by_ids_api(body: ByIdsRequest) -> ResponseModel[RecommendItemsData]:
    _ensure_enabled()
    try:
        items = run_recommend_by_ids(
            body.asset_ids,
            body.top_k,
            category_id=body.category_id,
            plugin_type=body.plugin_type,
        )
    except Exception as exc:
        logger.exception("recommend by_ids failed: %s", exc)
        raise _recommend_service_error() from exc
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendItemsData(items=[x.to_dict() for x in items]),
    )


@router.post("/by_queries", response_model=ResponseModel[RecommendItemsData])
def recommend_by_queries_api(body: ByQueriesRequest) -> ResponseModel[RecommendItemsData]:
    _ensure_enabled()
    try:
        items = run_recommend_by_queries(
            body.queries,
            body.top_k,
            category_id=body.category_id,
            plugin_type=body.plugin_type,
        )
    except Exception as exc:
        logger.exception("recommend by_queries failed: %s", exc)
        raise _recommend_service_error() from exc
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendItemsData(items=[x.to_dict() for x in items]),
    )


@router.post("/rerank_mmr", response_model=ResponseModel[RecommendItemsData])
def recommend_rerank_mmr_api(body: RerankMmrRequest) -> ResponseModel[RecommendItemsData]:
    _ensure_enabled()
    try:
        items = run_rerank_mmr([it.model_dump() for it in body.items], body.top_k)
    except Exception as exc:
        logger.exception("recommend rerank_mmr failed: %s", exc)
        raise _recommend_service_error() from exc
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendItemsData(items=[x.to_dict() for x in items]),
    )
