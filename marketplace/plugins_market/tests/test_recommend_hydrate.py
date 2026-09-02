"""POST /recommend market filter + card hydrate."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("STORE_DB_URL", "mysql+pymysql://test:test@127.0.0.1:3306/test")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.viewer_context import ANONYMOUS_VIEWER, ViewerContext
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB
from plugins_market.recommender.schemas import RecommendItemOut
from plugins_market.services.plugin import filter_recommend_ranked_ids, hydrate_plugin_list_items


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _viewer(user_id: str = "u1") -> ViewerContext:
    return ViewerContext(user_id=user_id, user_login=user_id, is_system_admin=False)


def _skill(
    asset_id: str,
    *,
    status: str = "PUBLISHED",
    plugin_type: str = "skill",
    pin_order: int | None = None,
    short_desc: str = "hello",
) -> MarketAssetDB:
    return MarketAssetDB(
        asset_id=asset_id,
        asset_type="plugin",
        name=asset_id,
        display_name=asset_id,
        publisher_id="owner",
        publisher_name="owner",
        status=status,
        plugin_type=plugin_type,
        latest_version="1.2.0",
        public_latest_version="1.2.0",
        moderation_status="APPROVED",
        view_count=0,
        install_count=0,
        like_count=0,
        star_count=0,
        review_count=0,
        average_rating=8.0,
        create_time=1,
        update_time=2,
        tags=["demo"],
        short_desc=short_desc,
        pin_order=pin_order,
    )


class RecommendHydrateTests(unittest.TestCase):
    def test_filter_drops_offline_and_unknown_keeps_recall_order(self) -> None:
        db = _db()
        db.add(_skill("s1"))
        db.add(_skill("s2", status="OFFLINE"))
        db.add(_skill("s3"))
        db.commit()

        visible = filter_recommend_ranked_ids(
            ["s2", "missing", "s3", "s1"],
            plugin_type="skill",
            db=db,
            viewer=_viewer(),
        )
        self.assertEqual(visible, ["s3", "s1"])

    def test_filter_plugin_type_and_pin_order(self) -> None:
        db = _db()
        db.add(_skill("skill-a", plugin_type="skill"))
        db.add(_skill("swarm-b", plugin_type="swarmskill"))
        db.add(_skill("skill-pin", plugin_type="skill", pin_order=1))
        db.commit()

        visible = filter_recommend_ranked_ids(
            ["skill-a", "swarm-b", "skill-pin"],
            plugin_type="skill",
            db=db,
            viewer=_viewer(),
        )
        self.assertEqual(visible, ["skill-pin", "skill-a"])

    def test_hydrate_fills_list_card_fields(self) -> None:
        db = _db()
        db.add(_skill("s1", short_desc="财报审阅"))
        db.commit()

        cards = hydrate_plugin_list_items(
            ["s1"],
            db=db,
            storage=None,
            viewer=ANONYMOUS_VIEWER,
            market_public_scoped=True,
        )
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card.asset_id, "s1")
        self.assertEqual(card.name, "s1")
        self.assertEqual(card.short_desc, "财报审阅")
        self.assertEqual(card.latest_version, "1.2.0")
        self.assertEqual(card.plugin_type, "skill")
        self.assertEqual(card.tags, ["demo"])

        item = RecommendItemOut.model_validate({**card.model_dump(), "score": 0.91})
        self.assertEqual(item.score, 0.91)
        self.assertEqual(item.short_desc, "财报审阅")
        self.assertEqual(item.asset_id, "s1")


if __name__ == "__main__":
    unittest.main()
