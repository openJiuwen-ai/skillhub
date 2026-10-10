# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MARKET_RECOMMENDER_ENABLED=false 时仍挂 /api/v1/recommend*，避免客户端 404。

POST /recommend 与 GET /plugins 关闭推荐时一致：按 install_count 出卡片。
Hub 无货类型（如 skillpack）直接 200 空列表，不借库。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from plugins_market.core.auth import resolve_viewer_context
from plugins_market.core.database import SessionLocal
from plugins_market.core.logging import get_logger
from plugins_market.core.s3_storage_client import get_storage_client
from plugins_market.core.viewer_context import ViewerContext
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
from plugins_market.schemas.common import ResponseModel
from plugins_market.services.plugin import list_plugins_by_install_count

logger = get_logger(__name__)

router = APIRouter(prefix="/recommend", tags=["recommend"])

_DISABLED_SOURCE = "install_count"


@router.post("", response_model=ResponseModel[RecommendData])
def recommend_disabled(
    body: RecommendRequest,
    viewer: ViewerContext = Depends(resolve_viewer_context),
    storage=Depends(get_storage_client),
) -> ResponseModel[RecommendData]:
    plugin_type = (body.plugin_type or "").strip()
    category_id = (body.category_id or "").strip()
    user_id = (viewer.user_id or "").strip()
    if not plugin_type_on_market(plugin_type):
        logger.info(
            "recommend skip unknown plugin_type=%s (not on Hub catalog)",
            plugin_type,
        )
        return empty_recommend_data(
            request_id=body.request_id or "",
            user_id=user_id,
            category_id=category_id,
            plugin_type=plugin_type,
            source=_DISABLED_SOURCE,
        )
    db = SessionLocal()
    try:
        cards = list_plugins_by_install_count(
            top_k=int(body.top_k),
            category_id=category_id,
            plugin_type=plugin_type,
            db=db,
            storage=storage,
            viewer=viewer,
        )
    finally:
        db.close()
    out_items = [
        RecommendItemOut.model_validate({**card.model_dump(), "score": 0.0})
        for card in cards
    ]
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendData(
            request_id=body.request_id or "",
            user_id=user_id,
            source=_DISABLED_SOURCE,
            category_id=category_id,
            plugin_type=plugin_type,
            items=out_items,
        ),
    )


@router.post("/by_ids", response_model=ResponseModel[RecommendItemsData])
def recommend_by_ids_disabled(_body: ByIdsRequest) -> ResponseModel[RecommendItemsData]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendItemsData(items=[]),
    )


@router.post("/by_queries", response_model=ResponseModel[RecommendItemsData])
def recommend_by_queries_disabled(_body: ByQueriesRequest) -> ResponseModel[RecommendItemsData]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendItemsData(items=[]),
    )


@router.post("/rerank_mmr", response_model=ResponseModel[RecommendItemsData])
def recommend_rerank_mmr_disabled(_body: RerankMmrRequest) -> ResponseModel[RecommendItemsData]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendItemsData(items=[]),
    )
