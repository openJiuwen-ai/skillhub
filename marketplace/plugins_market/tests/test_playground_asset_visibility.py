# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("STORE_DB_URL", "mysql+pymysql://test:test@127.0.0.1:3306/test")

from fastapi import HTTPException

from plugins_market.core.auth import AuthContext
from plugins_market.core.viewer_context import ViewerContext
from plugins_market.routers.playground_proxy import (
    _asset_visible_to_viewer,
    _inject_skill_content,
)


def _viewer(user_id: str | None = None, login: str | None = None, admin: bool = False) -> ViewerContext:
    return ViewerContext(user_id=user_id, user_login=login, is_system_admin=admin)


def _asset(**overrides) -> SimpleNamespace:
    base = {
        "asset_id": "a1",
        "status": "ONLINE",
        "plugin_type": "skill",
        "visibility": "public",
        "publisher_id": "pub1",
        "publish_result": "approved",
        "moderation_status": "APPROVED",
        "public_latest_version": "1.0.0",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class AssetVisibleToViewerTests(unittest.TestCase):
    def test_none_asset_is_invisible(self) -> None:
        self.assertFalse(_asset_visible_to_viewer(None, _viewer(), None))

    def test_offline_asset_is_invisible_even_for_owner_and_admin(self) -> None:
        offline = _asset(status="OFFLINE")
        self.assertFalse(_asset_visible_to_viewer(offline, _viewer("pub1"), None))
        self.assertFalse(_asset_visible_to_viewer(offline, _viewer(admin=True), None))

    def test_public_approved_skill_visible_to_stranger(self) -> None:
        self.assertTrue(_asset_visible_to_viewer(_asset(), _viewer("u1"), None))

    def test_unapproved_skill_hidden_from_stranger_but_visible_to_owner(self) -> None:
        pending = _asset(moderation_status="PENDING", public_latest_version="")
        self.assertFalse(_asset_visible_to_viewer(pending, _viewer("u1"), None))
        self.assertTrue(_asset_visible_to_viewer(pending, _viewer("pub1"), None))

    def test_private_skill_hidden_from_stranger_but_visible_to_owner_and_admin(self) -> None:
        private = _asset(visibility="private")
        self.assertFalse(_asset_visible_to_viewer(private, _viewer("u1"), None))
        self.assertTrue(_asset_visible_to_viewer(private, _viewer("pub1"), None))
        self.assertTrue(_asset_visible_to_viewer(private, _viewer(admin=True), None))


class InjectSkillContentVisibilityTests(unittest.TestCase):
    def test_inject_raises_404_for_offline_asset(self) -> None:
        offline = _asset(asset_id="s1", status="OFFLINE")
        repo_cls = MagicMock()
        repo_cls.return_value.get_by_asset_id.return_value = offline
        db_cm = MagicMock()
        db_cm.__enter__.return_value = MagicMock()
        body = json.dumps({"skill_id": "s1"}).encode("utf-8")
        auth = AuthContext(is_admin=False, acting_user_id="u1", acting_user_name="u1")
        with patch(
            "plugins_market.core.database.SessionLocal", return_value=db_cm
        ), patch(
            "plugins_market.core.s3_storage_client.get_storage_client", return_value=MagicMock()
        ), patch(
            "plugins_market.repositories.market_assets_repository.MarketAssetRepository", repo_cls
        ):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(_inject_skill_content(body, auth))
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
