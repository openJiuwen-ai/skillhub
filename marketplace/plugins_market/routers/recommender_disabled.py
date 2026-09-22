# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MARKET_RECOMMENDER_ENABLED=false 时仍挂 /api/v1/recommend*，避免客户端 404。

POST /recommend 与 GET /plugins 关闭推荐时一致：按 install_count 出卡片。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from plugins_market.core.auth import resolve_viewer_context
from plugins_market.core.database import get_db
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
from plugins_market.schemas.common import ResponseModel
from plugins_market.services.plugin import list_plugins_by_install_count

router = APIRouter(prefix="/recommend", tags=["recommend"])

_DISABLED_SOURCE = "install_count"


@router.post("", response_model=ResponseModel[RecommendData])
def recommend_disabled(
    body: RecommendRequest,
    viewer: ViewerContext = Depends(resolve_viewer_context),
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
) -> ResponseModel[RecommendData]:
    plugin_type = (body.plugin_type or "").strip()
    category_id = (body.category_id or "").strip()
    cards = list_plugins_by_install_count(
        top_k=int(body.top_k),
        category_id=category_id,
        plugin_type=plugin_type,
        db=db,
        storage=storage,
        viewer=viewer,
    )
    out_items = [
        RecommendItemOut.model_validate({**card.model_dump(), "score": 0.0})
        for card in cards
    ]
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=RecommendData(
            request_id=body.request_id or "",
            user_id=(viewer.user_id or "").strip(),
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
