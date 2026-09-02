# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Recommender HTTP API (external + SkillHub)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from plugins_market.core.auth import resolve_viewer_context
from plugins_market.core.config import settings
from plugins_market.core.database import get_db
from plugins_market.core.errors import auth_error_payload, http_error_payload
from plugins_market.core.logging import get_logger
from plugins_market.core.s3_storage_client import get_storage_client
from plugins_market.core.viewer_context import ViewerContext
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


@router.post("", response_model=ResponseModel[RecommendData])
def recommend(
    body: RecommendRequest,
    viewer: ViewerContext = Depends(resolve_viewer_context),
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
) -> ResponseModel[RecommendData]:
    """Personalized recommend, then market filter + card hydrate (same as list recommend)."""
    _ensure_enabled()
    user_id = _resolve_recommend_user_id(body, viewer)
    plugin_type = (body.plugin_type or "").strip()
    top_k = int(body.top_k)
    recall_k = min(500, max(top_k * 2, top_k))
    try:
        items, source = run_recommend_for_user(
            user_id=user_id,
            top_k=recall_k,
            request_id=body.request_id,
            timestamp=body.timestamp,
            category_id=body.category_id,
            plugin_type=plugin_type,
        )
        scores = {str(x.asset_id): float(x.score) for x in items if str(x.asset_id or "").strip()}
        ranked_ids = _dedupe_ranked_ids([x.asset_id for x in items])
        visible_ids = filter_recommend_ranked_ids(
            ranked_ids,
            plugin_type=plugin_type,
            db=db,
            viewer=viewer,
        )[:top_k]
        cards = hydrate_plugin_list_items(
            visible_ids,
            db=db,
            storage=storage,
            viewer=viewer,
            market_public_scoped=True,
        )
        logger.info(
            "recommend hydrate: source=%s user_id=%s ranked=%d visible=%d top_k=%s",
            source,
            user_id,
            len(ranked_ids),
            len(cards),
            top_k,
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

    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendData(
            request_id=body.request_id or "",
            user_id=user_id,
            source=source,
            category_id=(body.category_id or "").strip(),
            plugin_type=plugin_type,
            items=out_items,
        ),
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
