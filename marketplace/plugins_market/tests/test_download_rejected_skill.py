# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""人工驳回的 Skill 不得签发下载链接。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.errors import PublishError
from plugins_market.core.viewer_context import ANONYMOUS_VIEWER, ViewerContext
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.services.plugin import get_download_info


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)()


def _rejected_skill() -> tuple[MarketAssetDB, MarketAssetVersionDB]:
    asset = MarketAssetDB(
        asset_id="skill-rejected",
        asset_type="plugin",
        name="skill-rejected",
        display_name="skill-rejected",
        publisher_id="owner",
        publisher_name="Owner",
        status="PUBLISHED",
        plugin_type="skill",
        latest_version="1.0.0",
        public_latest_version=None,
        moderation_status="REJECTED",
        publish_result="publish_failed",
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
    version = MarketAssetVersionDB(
        version_id="v1",
        asset_id="skill-rejected",
        version="1.0.0",
        create_time=1,
        moderation_status="REJECTED",
        publish_result="publish_failed",
        status="ACTIVE",
    )
    return asset, version


@pytest.mark.parametrize(
    "viewer",
    [
        ANONYMOUS_VIEWER,
        ViewerContext(user_id="owner", user_login="Owner", is_system_admin=False),
        ViewerContext(user_id="admin", user_login="admin", is_system_admin=True),
    ],
)
def test_get_download_info_rejects_manually_rejected_skill(viewer: ViewerContext) -> None:
    db = _db()
    asset, version = _rejected_skill()
    db.add_all([asset, version])
    db.commit()

    with pytest.raises(PublishError) as exc:
        get_download_info(
            asset_id=asset.asset_id,
            version="1.0.0",
            db=db,
            storage=None,
            viewer=viewer,
        )
    assert exc.value.status_code == 404
