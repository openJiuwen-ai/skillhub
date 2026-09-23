# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""category_totals_by_type 聚合测试：口径与 list_plugins 的 total 一致（匿名访客市场口径）。

覆盖：按 tab 类型切片（agent 四类要求 asset_type == plugin_type == tab，
skill 两类只按 plugin_type）、未分类计入 all 不计入 totals、
OFFLINE/私有/待审剔除。
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB
from plugins_market.repositories.market_assets_repository import MarketAssetRepository


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _asset(
    session,
    asset_id: str,
    *,
    category: str | None = None,
    status: str = "PUBLISHED",
    visibility: str = "public",
    moderation_status: str | None = None,
    plugin_type: str = "skill",
    asset_type: str = "market",
) -> None:
    session.add(
        MarketAssetDB(
            asset_id=asset_id,
            asset_type=asset_type,
            name=asset_id,
            display_name=asset_id,
            publisher_id="pub",
            publisher_name="pub",
            plugin_type=plugin_type,
            category_id=category,
            status=status,
            visibility=visibility,
            moderation_status=moderation_status,
            public_latest_version="1.0.0",
        )
    )


def test_category_totals_by_type_slices_and_excludes():
    session = _db()
    _asset(session, "a1", category="finance-wealth", plugin_type="skill")
    _asset(session, "a2", category="finance-wealth", plugin_type="skill")
    _asset(session, "a3", category=None, plugin_type="skill")  # 未分类：计入 all，不进 totals
    _asset(session, "a4", category="office-productivity", plugin_type="skill", status="OFFLINE")
    _asset(session, "a5", category="office-productivity", plugin_type="skill", visibility="private")
    _asset(session, "a6", category="office-productivity", plugin_type="skill", moderation_status="PENDING")
    # agent tab 与列表一致：asset_type 必须 == plugin_type == tab
    _asset(session, "b1", category="office-productivity", plugin_type="agent-plugin", asset_type="agent-plugin")
    _asset(session, "b2", category="science-research", plugin_type="agent-plugin", asset_type="market")
    session.commit()

    result = MarketAssetRepository(session).category_totals_by_type()

    assert result["skill"] == ({"finance-wealth": 2}, 3)  # a1 a2 + 未分类 a3
    assert result["agent-plugin"] == ({"office-productivity": 1}, 1)  # 仅 b1
    assert result["swarmskill"] == ({}, 0)


def test_category_totals_by_type_uncategorized_and_agent_group():
    session = _db()
    _asset(session, "g1", category="hot", plugin_type="agent-group", asset_type="agent-group")
    _asset(session, "g2", category=None, plugin_type="agent-group", asset_type="agent-group")
    _asset(session, "g3", category="hot", plugin_type="agent-group", asset_type="agent-group",
           moderation_status="APPROVED")
    session.commit()

    result = MarketAssetRepository(session).category_totals_by_type()
    assert result["agent-group"] == ({"hot": 2}, 3)  # g3 计入（APPROVED），g2 未分类只进 all
