# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Skillhub 待审核：批量通过/驳回/删除与检索索引旁路回归。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.auth import AuthContext
from plugins_market.core.errors import BusinessError
from plugins_market.core.moderation import MODERATION_APPROVED, MODERATION_PENDING
from plugins_market.core.publish_result import (
    PUBLISH_RESULT_PENDING_MODERATION,
    PUBLISH_RESULT_SUCCESS,
)
from plugins_market.core.viewer_context import ViewerContext
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.repositories.market_assets_repository import (
    MarketAssetRepository,
    MarketAssetVersionRepository,
)
from plugins_market.schemas.plugin import (
    AssetVersionBatchDeleteItem,
    PluginListQuery,
    SkillModerationBatchItem,
)
from plugins_market.services.plugin import (
    delete_plugin_versions_batch_service,
    list_plugins_service,
    moderate_skill_assets_batch_service,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_cls = sessionmaker(bind=engine, autoflush=False)
    return session_cls()


def _mod_admin(user_id: str = "mod-admin") -> AuthContext:
    return AuthContext(
        is_admin=False,
        acting_user_id=user_id,
        acting_user_name=user_id,
        is_market_moderation_admin=True,
    )


def _asset(
    *,
    asset_id: str,
    publisher_id: str = "publisher-1",
    name: str = "demo-skill",
    display_name: str = "Demo Skill",
    moderation_status: str = MODERATION_PENDING,
    publish_result: str = PUBLISH_RESULT_PENDING_MODERATION,
    public_latest_version: str | None = None,
    plugin_type: str = "skill",
) -> MarketAssetDB:
    return MarketAssetDB(
        asset_id=asset_id,
        asset_type=plugin_type,
        name=name,
        display_name=display_name,
        short_desc="short-desc-target",
        publisher_id=publisher_id,
        publisher_name=publisher_id,
        status="PUBLISHED",
        plugin_type=plugin_type,
        publish_result=publish_result,
        public_latest_version=public_latest_version,
        moderation_status=moderation_status,
        latest_version="1.0.0",
        visibility="public",
        view_count=0,
        install_count=0,
        like_count=0,
        star_count=0,
        review_count=0,
        average_rating=8.0,
        create_time=1,
        update_time=1,
    )


def _version(
    *,
    asset_id: str,
    version: str,
    moderation_status: str,
    publish_result: str,
    create_time: int = 1,
) -> MarketAssetVersionDB:
    return MarketAssetVersionDB(
        version_id=f"ver-{asset_id}-{version}",
        asset_id=asset_id,
        version=version,
        status="ACTIVE",
        create_time=create_time,
        file_path=f"path/{asset_id}/{version}/pkg.zip",
        moderation_status=moderation_status,
        publish_result=publish_result,
        has_icon=False,
    )


class _FakeStorage:
    def __init__(self, success: bool = True):
        self._success = success
        self.deleted_prefixes: list[str] = []

    def delete_prefix(self, prefix: str) -> dict:
        self.deleted_prefixes.append(prefix)
        return {"success": self._success, "errors": []}


# ----- 批量审核 -----


@patch("plugins_market.services.plugin._refresh_skill_asset_listing_fields_from_public_artifact")
def test_moderation_batch_approve_per_item_independence(mock_refresh):
    """一条自审被禁 + 一条正常：前者失败、后者成功，互不影响。"""
    db = _db()
    db.add(
        _asset(
            asset_id="agent-self",
            publisher_id="mod-admin",
            name="self-skill",
        )
    )
    db.add(
        _asset(
            asset_id="agent-ok",
            publisher_id="publisher-1",
            name="ok-skill",
        )
    )
    db.add(_version(asset_id="agent-self", version="1.0.0", moderation_status=MODERATION_PENDING, publish_result=PUBLISH_RESULT_PENDING_MODERATION))
    db.add(_version(asset_id="agent-ok", version="1.0.0", moderation_status=MODERATION_PENDING, publish_result=PUBLISH_RESULT_PENDING_MODERATION))
    db.commit()

    result = moderate_skill_assets_batch_service(
        action="approve",
        reason=None,
        items=[
            SkillModerationBatchItem(asset_id="agent-self", version="1.0.0"),
            SkillModerationBatchItem(asset_id="agent-ok", version="1.0.0"),
        ],
        auth=_mod_admin("mod-admin"),
        db=db,
        storage=None,
    )

    assert len(result.succeeded) == 1
    assert result.succeeded[0].asset_id == "agent-ok"
    assert result.succeeded[0].moderation_status == MODERATION_APPROVED
    assert len(result.failed) == 1
    assert result.failed[0].asset_id == "agent-self"
    assert result.failed[0].error == "self_moderation_forbidden"


def test_moderation_batch_reject_requires_reason():
    """驳回缺原因：整批不发出。"""
    db = _db()
    with pytest.raises(BusinessError) as exc:
        moderate_skill_assets_batch_service(
            action="reject",
            reason="   ",
            items=[SkillModerationBatchItem(asset_id="x", version="1.0.0")],
            auth=_mod_admin(),
            db=db,
            storage=None,
        )
    assert exc.value.error == "reason_required"


def test_moderation_batch_requires_admin():
    """非审核管理员：403。"""
    db = _db()
    non_admin = AuthContext(
        is_admin=False,
        acting_user_id="user",
        acting_user_name="user",
        is_market_moderation_admin=False,
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        moderate_skill_assets_batch_service(
            action="approve",
            reason=None,
            items=[SkillModerationBatchItem(asset_id="x", version="1.0.0")],
            auth=non_admin,
            db=db,
            storage=None,
        )
    assert exc.value.status_code == 403


# ----- 批量删除 -----


def test_delete_batch_removes_whole_asset_when_last_version():
    """唯一版本被删：整资产删除，asset_deleted=True。"""
    db = _db()
    db.add(_asset(asset_id="only-pending", name="only"))
    db.add(_version(asset_id="only-pending", version="1.0.0", moderation_status=MODERATION_PENDING, publish_result=PUBLISH_RESULT_PENDING_MODERATION))
    db.commit()
    storage = _FakeStorage()

    result = delete_plugin_versions_batch_service(
        items=[AssetVersionBatchDeleteItem(asset_id="only-pending", version="1.0.0")],
        auth=_mod_admin(),
        db=db,
        storage=storage,
    )

    assert len(result.succeeded) == 1
    assert result.succeeded[0].asset_deleted is True
    assert len(result.failed) == 0
    asset_repo = MarketAssetRepository(db)
    assert asset_repo.get_by_asset_id("only-pending") is None
    assert storage.deleted_prefixes


def test_delete_batch_keeps_approved_version():
    """已有通过版本 + 新版本待审：只删待审版本，通过版本保留。"""
    db = _db()
    db.add(
        _asset(
            asset_id="mixed",
            moderation_status=MODERATION_APPROVED,
            publish_result=PUBLISH_RESULT_SUCCESS,
            public_latest_version="1.0.0",
        )
    )
    db.add(_version(asset_id="mixed", version="1.0.0", moderation_status=MODERATION_APPROVED, publish_result=PUBLISH_RESULT_SUCCESS))
    db.add(_version(asset_id="mixed", version="2.0.0", moderation_status=MODERATION_PENDING, publish_result=PUBLISH_RESULT_PENDING_MODERATION))
    db.commit()

    result = delete_plugin_versions_batch_service(
        items=[AssetVersionBatchDeleteItem(asset_id="mixed", version="2.0.0")],
        auth=_mod_admin(),
        db=db,
        storage=_FakeStorage(),
    )

    assert len(result.succeeded) == 1
    assert result.succeeded[0].asset_deleted is False
    assert len(result.failed) == 0
    version_repo = MarketAssetVersionRepository(db)
    assert version_repo.get_version(asset_id="mixed", version="2.0.0") is None
    assert version_repo.get_version(asset_id="mixed", version="1.0.0") is not None


def test_delete_batch_approved_version_fails():
    """已通过版本不可删除：逐条失败。"""
    db = _db()
    db.add(
        _asset(
            asset_id="approved-only",
            moderation_status=MODERATION_APPROVED,
            publish_result=PUBLISH_RESULT_SUCCESS,
            public_latest_version="1.0.0",
        )
    )
    db.add(_version(asset_id="approved-only", version="1.0.0", moderation_status=MODERATION_APPROVED, publish_result=PUBLISH_RESULT_SUCCESS))
    db.commit()

    result = delete_plugin_versions_batch_service(
        items=[AssetVersionBatchDeleteItem(asset_id="approved-only", version="1.0.0")],
        auth=_mod_admin(),
        db=db,
        storage=_FakeStorage(),
    )

    assert len(result.succeeded) == 0
    assert len(result.failed) == 1
    assert result.failed[0].error == "version_not_deletable"


def test_delete_batch_rejects_version_all():
    """version=all 被拒（schema 校验）。"""
    with pytest.raises(Exception):
        AssetVersionBatchDeleteItem(asset_id="x", version="all")


# ----- 检索索引旁路 -----


@patch("plugins_market.services.plugin.retrieval_search", return_value=[])
def test_pending_queue_search_bypasses_retrieval_for_moderation_admin(mock_search):
    """审核管理员 + moderation_status=PENDING + 单类型 + 关键词：不走公开检索索引。"""
    db = _db()
    asset = _asset(asset_id="pending-search", name="pending-search", display_name="Pending Search")
    db.add(asset)
    db.add(_version(asset_id="pending-search", version="1.0.0", moderation_status=MODERATION_PENDING, publish_result=PUBLISH_RESULT_PENDING_MODERATION))
    db.commit()

    viewer = ViewerContext(user_id="system_admin", user_login="system_admin", is_system_admin=True)
    result = list_plugins_service(
        PluginListQuery(
            page=1,
            page_size=20,
            plugin_type="skill",
            moderation_status="PENDING",
            search_keyword="Pending",
            order_by="update_time",
            desc=True,
        ),
        db,
        SimpleNamespace(),
        viewer=viewer,
    )

    mock_search.assert_not_called()
    assert result.total == 1
    assert result.items[0].asset_id == "pending-search"


@patch("plugins_market.services.plugin.retrieval_search", return_value=[])
def test_non_admin_search_still_uses_retrieval(mock_search):
    """普通用户搜索：仍走检索索引（旁路只对审核待办生效）。"""
    db = _db()
    viewer = ViewerContext(user_id=None, user_login=None, is_system_admin=False)
    list_plugins_service(
        PluginListQuery(
            page=1,
            page_size=20,
            plugin_type="skill",
            search_keyword="Pending",
            order_by="update_time",
        ),
        db,
        SimpleNamespace(),
        viewer=viewer,
    )
    mock_search.assert_called_once()


def test_publisher_name_literal_underscore():
    """用户名中的 `_` 按字面量匹配，不当作 LIKE 通配符。"""
    db = _db()
    db.add(_asset(asset_id="u-a", publisher_id="user_a", name="a"))
    db.add(_asset(asset_id="u-b", publisher_id="userXa", name="b"))
    db.add(_version(asset_id="u-a", version="1.0.0", moderation_status=MODERATION_PENDING, publish_result=PUBLISH_RESULT_PENDING_MODERATION))
    db.add(_version(asset_id="u-b", version="1.0.0", moderation_status=MODERATION_PENDING, publish_result=PUBLISH_RESULT_PENDING_MODERATION))
    db.commit()

    repo = MarketAssetRepository(db)
    viewer = ViewerContext(user_id="system_admin", user_login="system_admin", is_system_admin=True)
    rows, total = repo.list_plugins(
        PluginListQuery(
            page=1,
            page_size=20,
            plugin_type="skill",
            moderation_status="PENDING",
            publisher_name="user_a",
            order_by="update_time",
        ),
        viewer=viewer,
    )
    assert total == 1
    assert rows[0][0].asset_id == "u-a"