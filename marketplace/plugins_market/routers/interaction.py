# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from plugins_market.core.auth import AuthContext, require_auth, resolve_viewer_context
from plugins_market.core.database import get_db
from plugins_market.core.errors import http_error_payload
from plugins_market.core.viewer_context import ANONYMOUS_VIEWER, ViewerContext
from plugins_market.core.moderation import is_skill_like_plugin_type
from plugins_market.repositories import MarketAssetRepository, MarketAssetInteractionRepository
from plugins_market.schemas.common import ResponseModel
from plugins_market.schemas.interaction import (
    AssetInteractionState,
    BatchInteractionResult,
    InteractRequest,
    InteractionToggleResult,
    InteractionViewResult,
    UserInteractionState,
)
from plugins_market.schemas.plugin import PluginListItem, PluginListResponse


_VALID_ACTION_TYPES = {"like", "star"}

interaction_router = APIRouter(prefix="/plugins", tags=["interactions"])


def _http_exception(status_code: int, message: str, *, error: str) -> HTTPException:
    resolved_error = {
        "not_found": "not_found",
        "skill_not_approved": "skill_not_approved",
        "invalid_action_type": "invalid_action_type",
        "self_interaction_forbidden": "self_interaction_forbidden",
        "too_many_ids": "too_many_ids",
    }.get(error, error)
    return HTTPException(
        status_code=status_code,
        detail=http_error_payload(
            status_code=status_code,
            message=message,
            error=resolved_error,
        ),
    )


def _asset_not_found(asset_id: str) -> HTTPException:
    return _http_exception(status.HTTP_404_NOT_FOUND, f"Asset {asset_id!r} not found", error="not_found")


def _skill_not_approved(asset_id: str) -> HTTPException:
    return _http_exception(
        status.HTTP_400_BAD_REQUEST,
        f"Skill {asset_id!r} is not approved for interactions",
        error="skill_not_approved",
    )


def _is_skill_asset(asset) -> bool:
    return is_skill_like_plugin_type(getattr(asset, "plugin_type", None))


def _viewer_from_auth(auth: AuthContext) -> ViewerContext:
    return ViewerContext(
        user_id=auth.acting_user_id,
        user_login=auth.acting_user_name,
        is_system_admin=auth.is_admin,
    )


def _is_asset_offline(asset) -> bool:
    """资产是否已下架（OFFLINE）。下架资产对外应视为不可见/不存在。"""
    return (getattr(asset, "status", "") or "").upper() == "OFFLINE"


def _asset_visible_to_viewer(asset, viewer: ViewerContext, db: Session) -> bool:
    """读接口统一可见性判定：资产存在、未下架(OFFLINE)，且 skill-like 须对 viewer 可见（F-11/F-25/F-33）。"""
    # 未通过审核的 skill 仅发布者/审核管理员可见；非 skill 资产仅受 OFFLINE 约束。
    if asset is None:
        return False
    if _is_asset_offline(asset):
        return False
    if _is_skill_asset(asset):
        return viewer.can_view_skill_asset(asset, db)
    return True


def _is_self_skill(asset, user_id: str | None) -> bool:
    if not user_id or not _is_skill_asset(asset):
        return False
    publisher_id = getattr(asset, "publisher_id", None)
    if not publisher_id:
        return False
    return str(user_id) == str(publisher_id)


def _build_list_item(asset) -> PluginListItem:
    return PluginListItem(
        asset_id=asset.asset_id,
        asset_type=asset.asset_type,
        name=asset.name,
        display_name=asset.display_name,
        short_desc=asset.short_desc,
        publisher_id=asset.publisher_id,
        publisher_name=asset.publisher_name,
        tags=asset.tags,
        category_id=asset.category_id,
        category_name=asset.category_name,
        certification=asset.certification,
        plugin_type=asset.plugin_type,
        moderation_status=asset.moderation_status,
        latest_version=asset.latest_version,
        public_latest_version=asset.public_latest_version,
        view_count=asset.view_count,
        install_count=asset.install_count,
        like_count=asset.like_count,
        star_count=asset.star_count,
        review_count=asset.review_count,
        average_rating=float(asset.average_rating),
        create_time=asset.create_time,
        update_time=asset.update_time,
        pin_order=asset.pin_order,
    )


@interaction_router.get(
    "/my/stars",
    response_model=ResponseModel[PluginListResponse],
)
async def get_my_stars(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[PluginListResponse]:
    interaction_repo = MarketAssetInteractionRepository(db)
    assets, total = interaction_repo.list_assets_by_user_action(
        user_id=auth.acting_user_id,
        action_type="star",
        page=page,
        page_size=page_size,
    )
    items = [_build_list_item(a) for a in assets]
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=PluginListResponse(page=page, page_size=page_size, total=total, items=items),
    )


@interaction_router.get(
    "/my/likes",
    response_model=ResponseModel[PluginListResponse],
)
async def get_my_likes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[PluginListResponse]:
    interaction_repo = MarketAssetInteractionRepository(db)
    assets, total = interaction_repo.list_assets_by_user_action(
        user_id=auth.acting_user_id,
        action_type="like",
        page=page,
        page_size=page_size,
    )
    items = [_build_list_item(a) for a in assets]
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=PluginListResponse(page=page, page_size=page_size, total=total, items=items),
    )


@interaction_router.post(
    "/{asset_id}/view",
    response_model=ResponseModel[InteractionViewResult],
)
async def post_view(
    asset_id: str,
    db: Session = Depends(get_db),
) -> ResponseModel[InteractionViewResult]:
    asset_repo = MarketAssetRepository(db)
    # 先校验可见性：隐藏/下架资产不应通过 view 计数被探测或刷量（F-11）。
    # /view 为匿名接口，按匿名可见性判定。
    existing = asset_repo.get_by_asset_id(asset_id)
    if not _asset_visible_to_viewer(existing, ANONYMOUS_VIEWER, db):
        raise _asset_not_found(asset_id)
    affected = asset_repo.increase_view_count_atomic(asset_id)
    if affected == 0:
        raise _asset_not_found(asset_id)
    db.commit()
    asset = asset_repo.get_by_asset_id(asset_id)
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=InteractionViewResult(view_count=asset.view_count if asset else 0),
    )


@interaction_router.post(
    "/{asset_id}/interact",
    response_model=ResponseModel[InteractionToggleResult],
)
async def post_interact(
    asset_id: str,
    body: InteractRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[InteractionToggleResult]:
    if body.action_type not in _VALID_ACTION_TYPES:
        raise _http_exception(
            status.HTTP_400_BAD_REQUEST,
            f"action_type must be one of: {', '.join(sorted(_VALID_ACTION_TYPES))}",
            error="invalid_action_type",
        )

    user_id = auth.acting_user_id
    viewer = _viewer_from_auth(auth)
    asset_repo = MarketAssetRepository(db)
    interaction_repo = MarketAssetInteractionRepository(db)

    if body.action_type == "like":
        asset = asset_repo.lock_for_update(asset_id)
        if not asset:
            raise _asset_not_found(asset_id)
        # 下架(OFFLINE)资产对外不可见，禁止对其点赞/收藏写入（F-38/F-39/F-40）；返回 404 不泄露存在性。
        if _is_asset_offline(asset):
            raise _asset_not_found(asset_id)
        if not _asset_visible_to_viewer(asset, viewer, db):
            raise _skill_not_approved(asset_id)
        if _is_self_skill(asset, user_id):
            raise _http_exception(
                status.HTTP_403_FORBIDDEN,
                "Cannot interact with your own skill",
                error="self_interaction_forbidden",
            )
        existing = interaction_repo.get_interaction(asset_id, user_id, "like")
        if existing:
            interaction_repo.remove_interaction(asset_id, user_id, "like")
            asset.like_count = max(0, asset.like_count - 1)
            active = False
        else:
            interaction_repo.add_interaction(asset_id, user_id, "like")
            asset.like_count += 1
            active = True
        db.commit()
        db.refresh(asset)
        return ResponseModel(
            code=status.HTTP_200_OK,
            message="ok",
            data=InteractionToggleResult(
                action_type="like",
                active=active,
                like_count=asset.like_count,
            ),
        )

    # star
    asset = asset_repo.lock_for_update(asset_id)
    if not asset:
        raise _asset_not_found(asset_id)
    # 下架(OFFLINE)资产对外不可见，禁止对其点赞/收藏写入（F-38/F-39/F-40）；返回 404 不泄露存在性。
    if _is_asset_offline(asset):
        raise _asset_not_found(asset_id)
    if not _asset_visible_to_viewer(asset, viewer, db):
        raise _skill_not_approved(asset_id)
    if _is_self_skill(asset, user_id):
        raise _http_exception(
            status.HTTP_403_FORBIDDEN,
            "Cannot interact with your own skill",
            error="self_interaction_forbidden",
        )
    existing = interaction_repo.get_interaction(asset_id, user_id, "star")
    if existing:
        interaction_repo.remove_interaction(asset_id, user_id, "star")
        asset.star_count = max(0, asset.star_count - 1)
        active = False
    else:
        interaction_repo.add_interaction(asset_id, user_id, "star")
        asset.star_count += 1
        active = True
    db.commit()
    db.refresh(asset)
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=InteractionToggleResult(action_type="star", active=active, star_count=asset.star_count),
    )


@interaction_router.get(
    "/interactions/batch",
    response_model=ResponseModel[BatchInteractionResult],
)
async def get_interactions_batch(
    asset_ids: List[str] = Query(default=[]),
    db: Session = Depends(get_db),
    viewer: ViewerContext = Depends(resolve_viewer_context),
) -> ResponseModel[BatchInteractionResult]:
    if not asset_ids:
        return ResponseModel(
            code=status.HTTP_200_OK,
            message="ok",
            data=BatchInteractionResult(items=[]),
        )
    if len(asset_ids) > 50:
        raise _http_exception(
            status.HTTP_400_BAD_REQUEST,
            "asset_ids must not exceed 50 items",
            error="too_many_ids",
        )

    asset_repo = MarketAssetRepository(db)
    # 仅保留对 viewer 可见的资产，过滤掉隐藏/下架资产，避免通过批量计数泄露其存在性与热度（F-11/F-25）。
    visible_ids = asset_repo.filter_visible_asset_ids(asset_ids, viewer)
    visible_ordered = [aid for aid in asset_ids if aid in visible_ids]
    if not visible_ordered:
        return ResponseModel(
            code=status.HTTP_200_OK,
            message="ok",
            data=BatchInteractionResult(items=[]),
        )
    counts = asset_repo.get_counts_batch(visible_ordered)

    user_interaction_map: dict = {}
    if viewer.user_id:
        interaction_repo = MarketAssetInteractionRepository(db)
        for asset_id, action_type in interaction_repo.get_user_interactions_batch(visible_ordered, viewer.user_id):
            user_interaction_map.setdefault(asset_id, set()).add(action_type)

    items = [
        AssetInteractionState(
            asset_id=aid,
            liked="like" in user_interaction_map.get(aid, set()),
            starred="star" in user_interaction_map.get(aid, set()),
            like_count=int(counts.get(aid, {}).get("like_count") or 0),
            star_count=int(counts.get(aid, {}).get("star_count") or 0),
        )
        for aid in visible_ordered
    ]

    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=BatchInteractionResult(items=items),
    )


@interaction_router.get(
    "/{asset_id}/interactions",
    response_model=ResponseModel[UserInteractionState],
)
async def get_interactions(
    asset_id: str,
    db: Session = Depends(get_db),
    viewer: ViewerContext = Depends(resolve_viewer_context),
) -> ResponseModel[UserInteractionState]:
    asset_repo = MarketAssetRepository(db)
    asset = asset_repo.get_by_asset_id(asset_id)
    # 隐藏/下架资产不应通过本接口被原始 asset_id 探测出计数与状态（F-33）；不可见即返回 404。
    if not _asset_visible_to_viewer(asset, viewer, db):
        raise _asset_not_found(asset_id)
    like_count = asset.like_count if asset else None
    star_count = asset.star_count if asset else None

    if not viewer.user_id:
        return ResponseModel(
            code=status.HTTP_200_OK,
            message="ok",
            data=UserInteractionState(liked=False, starred=False, like_count=like_count, star_count=star_count),
        )

    interaction_repo = MarketAssetInteractionRepository(db)
    rows = interaction_repo.get_user_interactions(asset_id, viewer.user_id)
    action_types = {r.action_type for r in rows}
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=UserInteractionState(
            liked="like" in action_types,
            starred="star" in action_types,
            like_count=like_count,
            star_count=star_count,
        ),
    )
