# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Omit version on GET /artifacts: return public_latest, not unpublished latest."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.errors import PublishError
from plugins_market.core.viewer_context import ANONYMOUS_VIEWER, ViewerContext
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.models import groups as _groups  # noqa: F401 — grant lookup tables
from plugins_market.repositories import PluginFetchRecordRepository
from plugins_market.services.plugin import get_download_info


class _Storage:
    @staticmethod
    def head_object(key):
        return {"success": True, "size": 4, "metadata": {"sha256": "abcd", "size": "4"}}

    @staticmethod
    def presigned_get_url(key, download_filename=None):
        return f"http://example.test/{key}"


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)()


def _skill_with_pending_new_version() -> tuple[MarketAssetDB, MarketAssetVersionDB, MarketAssetVersionDB]:
    asset = MarketAssetDB(
        asset_id="skill-public-then-pending",
        asset_type="plugin",
        name="demo-skill",
        display_name="demo-skill",
        publisher_id="owner",
        publisher_name="Owner",
        status="PUBLISHED",
        plugin_type="skill",
        latest_version="2.0.0",
        public_latest_version="1.0.0",
        moderation_status="APPROVED",
        publish_result="pending_moderation",
        visibility="public",
        view_count=0,
        install_count=0,
        like_count=0,
        star_count=0,
        review_count=0,
        average_rating=8.0,
        create_time=1,
        update_time=2,
    )
    v1 = MarketAssetVersionDB(
        version_id="v1",
        asset_id="skill-public-then-pending",
        version="1.0.0",
        create_time=1,
        moderation_status="APPROVED",
        publish_result="publish_success",
        status="ACTIVE",
    )
    v2 = MarketAssetVersionDB(
        version_id="v2",
        asset_id="skill-public-then-pending",
        version="2.0.0",
        create_time=2,
        moderation_status="PENDING",
        publish_result="pending_moderation",
        status="ACTIVE",
    )
    return asset, v1, v2


def _seed_pending_new_version():
    db = _db()
    asset, v1, v2 = _skill_with_pending_new_version()
    db.add_all([asset, v1, v2])
    db.commit()
    return db


def _get_download_info(**kwargs):
    # sqlite BigInteger PK on plugin_fetch_records is NOT NULL without autoincrement
    with patch.object(PluginFetchRecordRepository, "create_fetch_record"):
        return get_download_info(**kwargs)


@pytest.mark.parametrize(
    "viewer",
    [
        ANONYMOUS_VIEWER,
        ViewerContext(user_id="other", user_login="Other", is_system_admin=False),
        ViewerContext(user_id="owner", user_login="Owner", is_system_admin=False),
        ViewerContext(user_id="admin", user_login="admin", is_system_admin=True),
    ],
)
def test_unspecified_version_returns_public_latest_not_pending(viewer: ViewerContext) -> None:
    db = _seed_pending_new_version()
    data = _get_download_info(
        asset_id="skill-public-then-pending",
        version=None,
        db=db,
        storage=_Storage(),
        viewer=viewer,
        is_cli_download=True,
    )
    assert data.version == "1.0.0"
    assert "/2.0.0/" not in data.download_url
    assert "/1.0.0/" in data.download_url


def test_owner_can_download_pending_when_version_specified() -> None:
    db = _seed_pending_new_version()
    owner = ViewerContext(user_id="owner", user_login="Owner", is_system_admin=False)
    data = _get_download_info(
        asset_id="skill-public-then-pending",
        version="2.0.0",
        db=db,
        storage=_Storage(),
        viewer=owner,
        is_cli_download=True,
    )
    assert data.version == "2.0.0"
    assert "/2.0.0/" in data.download_url


def test_ordinary_user_cannot_download_pending_when_version_specified() -> None:
    db = _seed_pending_new_version()
    with pytest.raises(PublishError) as exc:
        get_download_info(
            asset_id="skill-public-then-pending",
            version="2.0.0",
            db=db,
            storage=_Storage(),
            viewer=ANONYMOUS_VIEWER,
            is_cli_download=True,
        )
    assert exc.value.status_code == 404


def test_owner_first_pending_publish_without_public_latest_falls_back() -> None:
    db = _db()
    asset = MarketAssetDB(
        asset_id="skill-first-pending",
        asset_type="plugin",
        name="first-pending",
        display_name="first-pending",
        publisher_id="owner",
        publisher_name="Owner",
        status="PUBLISHED",
        plugin_type="skill",
        latest_version="1.0.0",
        public_latest_version=None,
        moderation_status="PENDING",
        publish_result="pending_moderation",
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
        asset_id="skill-first-pending",
        version="1.0.0",
        create_time=1,
        moderation_status="PENDING",
        publish_result="pending_moderation",
        status="ACTIVE",
    )
    db.add_all([asset, version])
    db.commit()
    owner = ViewerContext(user_id="owner", user_login="Owner", is_system_admin=False)
    data = _get_download_info(
        asset_id="skill-first-pending",
        version=None,
        db=db,
        storage=_Storage(),
        viewer=owner,
        is_cli_download=True,
    )
    assert data.version == "1.0.0"

    with pytest.raises(PublishError) as exc:
        get_download_info(
            asset_id="skill-first-pending",
            version=None,
            db=db,
            storage=_Storage(),
            viewer=ANONYMOUS_VIEWER,
            is_cli_download=True,
        )
    assert exc.value.status_code == 404
