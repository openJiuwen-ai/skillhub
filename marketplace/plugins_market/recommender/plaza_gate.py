# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plaza POST /recommend: Hub catalog types vs Swarm-local-only types."""

from __future__ import annotations

from fastapi import status

from plugins_market.core.moderation import AGENT_ASSET_PLUGIN_TYPES, SKILL_LIKE_PLUGIN_TYPES
from plugins_market.recommender.schemas import RecommendData
from plugins_market.schemas.common import ResponseModel
from recommender.online.search import parse_plugin_types

# Hub 上架类型；Swarm 本地 skillpack 不在市场，不必召回/hydrate。
MARKET_RECOMMEND_PLUGIN_TYPES = SKILL_LIKE_PLUGIN_TYPES | AGENT_ASSET_PLUGIN_TYPES


def plugin_type_on_market(plugin_type: str) -> bool:
    types = parse_plugin_types(plugin_type)
    if not types:
        return True
    return any(item in MARKET_RECOMMEND_PLUGIN_TYPES for item in types)


def empty_recommend_data(
    *,
    request_id: str,
    user_id: str,
    category_id: str,
    plugin_type: str,
    source: str = "topk_install",
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
            items=[],
        ),
    )
