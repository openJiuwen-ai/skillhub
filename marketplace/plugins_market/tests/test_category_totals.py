# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""category_totals 聚合测试：口径与 list_plugins 的 total 一致（匿名访客市场口径）。

覆盖：分类分组、未分类计入 all 不计入 totals、OFFLINE/私有/待审剔除、
asset_type/plugin_type 过滤（含逗号分隔多值）。
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


def test_category_totals_groups_and_excludes():
    session = _db()
    _asset(session, "a1", category="finance-wealth")
    _asset(session, "a2", category="finance-wealth")
    _asset(session, "a3", category="office-productivity")
    _asset(session, "a4", category=None)  # 未分类：计入 all，不进 totals
    _asset(session, "a5", category="office-productivity", status="OFFLINE")  # 下架剔除
    _asset(session, "a6", category="office-productivity", visibility="private")  # 私有剔除
    _asset(session, "a7", category="office-productivity", moderation_status="PENDING")  # 待审剔除
    _asset(session, "a8", category="science-research", moderation_status="APPROVED")  # 保留
    session.commit()

    totals, total_all = MarketAssetRepository(session).category_totals(plugin_type="skill")

    assert totals == {"finance-wealth": 2, "office-productivity": 1, "science-research": 1}
    assert total_all == 5  # a1 a2 a3 a8 + a4（未分类）


def test_category_totals_filters_by_type_params():
    session = _db()
    _asset(session, "b1", category="office-productivity", plugin_type="skill")
    _asset(session, "b2", category="office-productivity", plugin_type="agent-group")
    _asset(session, "b3", category="science-research", asset_type="other")
    session.commit()

    repo = MarketAssetRepository(session)
    totals, total_all = repo.category_totals(plugin_type="skill", asset_type="market")
    assert totals == {"office-productivity": 1}
    assert total_all == 1

    # 逗号分隔多值与列表接口 plugin_type in (...) 同语义
    multi_totals, multi_all = repo.category_totals(plugin_type="skill,agent-group", asset_type="market")
    assert multi_totals == {"office-productivity": 2}
    assert multi_all == 2
