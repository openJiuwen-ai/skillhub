# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""M3：Agent 待审队列、人工审核与关键词检索回归。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.auth import AuthContext
from plugins_market.core.moderation import MODERATION_APPROVED, MODERATION_PENDING
from plugins_market.core.publish_result import PUBLISH_RESULT_PENDING_MODERATION, PUBLISH_RESULT_SUCCESS
from plugins_market.core.viewer_context import ViewerContext
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.repositories.market_assets_repository import MarketAssetRepository
from plugins_market.schemas.plugin import PluginListQuery
from plugins_market.services.plugin import (
    _should_use_retrieval_search,
    moderate_skill_asset_service,
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


def _agent_asset(
    *,
    asset_id: str = "agent-pending",
    publisher_id: str = "publisher-1",
    name: str = "demo-agent",
    display_name: str = "Demo Agent",
    moderation_status: str = MODERATION_APPROVED,
    publish_result: str = PUBLISH_RESULT_SUCCESS,
    public_latest_version: str | None = "1.0.0",
) -> MarketAssetDB:
    return MarketAssetDB(
        asset_id=asset_id,
        asset_type="agent-plugin",
        name=name,
        display_name=display_name,
        short_desc="keyword-search-target",
        publisher_id=publisher_id,
        publisher_name=publisher_id,
        status="PUBLISHED",
        plugin_type="agent-plugin",
        publish_result=publish_result,
        public_latest_version=public_latest_version,
        moderation_status=moderation_status,
        latest_version="2.0.0",
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
) -> MarketAssetVersionDB:
    return MarketAssetVersionDB(
        version_id=f"ver-{asset_id}-{version}",
        asset_id=asset_id,
        version=version,
        status="ACTIVE",
        create_time=1,
        file_path=f"path/{asset_id}/{version}/pkg.zip",
        moderation_status=moderation_status,
        publish_result=publish_result,
        has_icon=False,
    )


def test_agent_tab_skips_semantic_retrieval():
    assert _should_use_retrieval_search("agent-plugin") is False
    assert _should_use_retrieval_search("skill,swarmskill") is True


def test_pending_queue_finds_agent_with_new_pending_version_while_asset_approved():
    """Agent-only 待审列表须按版本行判定，不能只看主表 moderation_status。"""
    db = _db()
    asset = _agent_asset(
        moderation_status=MODERATION_APPROVED,
        publish_result=PUBLISH_RESULT_SUCCESS,
        public_latest_version="1.0.0",
    )
    db.add(asset)
    db.add(
        _version(
            asset_id=asset.asset_id,
            version="1.0.0",
            moderation_status=MODERATION_APPROVED,
            publish_result=PUBLISH_RESULT_SUCCESS,
        )
    )
    db.add(
        _version(
            asset_id=asset.asset_id,
            version="2.0.0",
            moderation_status=MODERATION_PENDING,
            publish_result=PUBLISH_RESULT_PENDING_MODERATION,
        )
    )
    db.commit()

    repo = MarketAssetRepository(db)
    viewer = ViewerContext(user_id="system_admin", user_login="system_admin", is_system_admin=True)

    rows, total = repo.list_plugins(
        PluginListQuery(
            page=1,
            page_size=20,
            plugin_type="agent-plugin",
            moderation_status="PENDING",
            order_by="update_time",
            desc=True,
        ),
        viewer=viewer,
    )

    assert total == 1
    assert rows[0][0].asset_id == asset.asset_id


def test_pending_queue_keyword_search_on_agent_display_name():
    db = _db()
    asset = _agent_asset(
        asset_id="agent-search",
        name="searchable-agent",
        display_name="M3 Searchable Agent",
        moderation_status=MODERATION_APPROVED,
    )
    db.add(asset)
    db.add(
        _version(
            asset_id=asset.asset_id,
            version="1.0.0",
            moderation_status=MODERATION_APPROVED,
            publish_result=PUBLISH_RESULT_SUCCESS,
        )
    )
    db.commit()

    repo = MarketAssetRepository(db)
    viewer = ViewerContext(user_id=None, user_login=None, is_system_admin=False)
    rows, total = repo.list_plugins(
        PluginListQuery(
            page=1,
            page_size=20,
            plugin_type="agent-plugin",
            search_keyword="Searchable",
            order_by="update_time",
            desc=True,
        ),
        viewer=viewer,
    )
    assert total == 1
    assert rows[0][0].name == "searchable-agent"


@patch("plugins_market.services.plugin._refresh_skill_asset_listing_fields_from_public_artifact")
def test_moderate_agent_plugin_pending_to_approved(mock_refresh):
    db = _db()
    asset = _agent_asset(
        asset_id="agent-mod",
        publisher_id="publisher-1",
        moderation_status=MODERATION_PENDING,
        publish_result=PUBLISH_RESULT_PENDING_MODERATION,
        public_latest_version=None,
    )
    version = _version(
        asset_id=asset.asset_id,
        version="1.0.0",
        moderation_status=MODERATION_PENDING,
        publish_result=PUBLISH_RESULT_PENDING_MODERATION,
    )
    db.add(asset)
    db.add(version)
    db.commit()
    old_update_time = asset.update_time

    result = moderate_skill_asset_service(
        asset_id=asset.asset_id,
        action="approve",
        reason=None,
        version="1.0.0",
        auth=_mod_admin(),
        db=db,
        storage=None,
    )

    mock_refresh.assert_called_once()
    assert result.publish_result == PUBLISH_RESULT_SUCCESS
    assert result.moderation_status == MODERATION_APPROVED
    db.refresh(version)
    db.refresh(asset)
    assert version.publish_result == PUBLISH_RESULT_SUCCESS
    assert version.moderation_status == MODERATION_APPROVED
    assert asset.update_time is not None
    assert int(asset.update_time) > int(old_update_time or 0)


def test_moderate_agent_self_publish_forbidden_message():
    db = _db()
    asset = _agent_asset(
        asset_id="agent-self",
        publisher_id="mod-admin",
        moderation_status=MODERATION_PENDING,
        publish_result=PUBLISH_RESULT_PENDING_MODERATION,
        public_latest_version=None,
    )
    version = _version(
        asset_id=asset.asset_id,
        version="1.0.0",
        moderation_status=MODERATION_PENDING,
        publish_result=PUBLISH_RESULT_PENDING_MODERATION,
    )
    db.add(asset)
    db.add(version)
    db.commit()

    with pytest.raises(Exception) as exc:
        moderate_skill_asset_service(
            asset_id=asset.asset_id,
            action="approve",
            reason=None,
            version="1.0.0",
            auth=_mod_admin("mod-admin"),
            db=db,
            storage=None,
        )

    from plugins_market.core.errors import BusinessError

    assert isinstance(exc.value, BusinessError)
    assert exc.value.error == "self_moderation_forbidden"
    assert "Agent 插件" in exc.value.message


def test_version_detail_update_time_uses_newer_asset_or_version_timestamp():
    from plugins_market.services.plugin import _version_detail_update_time_ms

    assert _version_detail_update_time_ms(
        SimpleNamespace(update_time=200),
        SimpleNamespace(create_time=100),
    ) == 200
    assert _version_detail_update_time_ms(
        SimpleNamespace(update_time=50),
        SimpleNamespace(create_time=80),
    ) == 80
    assert _version_detail_update_time_ms(
        SimpleNamespace(update_time=None),
        SimpleNamespace(create_time=12),
    ) == 12
