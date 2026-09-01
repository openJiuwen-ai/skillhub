# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PR-A：moderated 市场资产类型助手与 Viewer 可见性。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from plugins_market.core.moderation import (
    AGENT_ASSET_PLUGIN_TYPES,
    MODERATED_MARKET_ASSET_TYPES,
    MODERATION_APPROVED,
    MODERATION_PENDING,
    SKILL_LIKE_PLUGIN_TYPES,
    is_moderated_market_asset_type,
    is_skill_like_plugin_type,
    is_wrapped_agent_asset_type,
)
from plugins_market.core.viewer_context import ANONYMOUS_VIEWER, ViewerContext
from plugins_market.services.plugin import _moderation_for_publish


def _asset(**kwargs):
    base = {
        "asset_id": "a1",
        "plugin_type": "agent-plugin",
        "publisher_id": "u1",
        "visibility": "public",
        "publish_result": "publish_success",
        "moderation_status": MODERATION_APPROVED,
        "public_latest_version": "1.0.0",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _version(**kwargs):
    base = {
        "version": "1.0.0",
        "publish_result": "publish_success",
        "moderation_status": MODERATION_APPROVED,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestModeratedTypeHelpers(unittest.TestCase):
    def test_type_sets(self):
        self.assertEqual(SKILL_LIKE_PLUGIN_TYPES, frozenset({"skill", "swarmskill"}))
        self.assertEqual(
            AGENT_ASSET_PLUGIN_TYPES,
            frozenset({"agent-plugin", "agent-template", "agent-mcp"}),
        )
        self.assertEqual(MODERATED_MARKET_ASSET_TYPES, SKILL_LIKE_PLUGIN_TYPES | AGENT_ASSET_PLUGIN_TYPES)

    def test_predicates(self):
        self.assertTrue(is_skill_like_plugin_type("swarmskill"))
        self.assertTrue(is_skill_like_plugin_type("teamskills"))
        self.assertFalse(is_skill_like_plugin_type("agent-plugin"))
        self.assertTrue(is_wrapped_agent_asset_type("agent-template"))
        self.assertTrue(is_moderated_market_asset_type("agent-mcp"))
        self.assertTrue(is_moderated_market_asset_type("skill"))
        self.assertFalse(is_moderated_market_asset_type("tools"))
        self.assertFalse(is_moderated_market_asset_type("mcp-stdio"))


class TestModerationForPublish(unittest.TestCase):
    @patch("plugins_market.services.plugin.settings")
    def test_agent_pending_for_normal_user(self, settings):
        settings.system_admin_user = "system_admin"
        st, reason = _moderation_for_publish(user_id="u1", plugin_type="agent-plugin")
        self.assertEqual(st, MODERATION_PENDING)
        self.assertIsNone(reason)

    @patch("plugins_market.services.plugin.settings")
    def test_agent_approved_for_system_admin(self, settings):
        settings.system_admin_user = "system_admin"
        st, _ = _moderation_for_publish(user_id="system_admin", plugin_type="agent-mcp")
        self.assertEqual(st, MODERATION_APPROVED)

    @patch("plugins_market.services.plugin.settings")
    def test_legacy_tools_still_auto_approved(self, settings):
        settings.system_admin_user = "system_admin"
        st, _ = _moderation_for_publish(user_id="u1", plugin_type="tools")
        self.assertEqual(st, MODERATION_APPROVED)


class TestViewerAgentModeration(unittest.TestCase):
    def test_anonymous_cannot_see_pending_agent(self):
        asset = _asset(
            moderation_status=MODERATION_PENDING,
            publish_result="pending_moderation",
            public_latest_version=None,
        )
        self.assertFalse(ANONYMOUS_VIEWER.can_view_skill_asset(asset))

    def test_anonymous_can_see_approved_agent(self):
        asset = _asset()
        self.assertTrue(ANONYMOUS_VIEWER.can_view_skill_asset(asset))

    def test_owner_can_see_pending_agent(self):
        asset = _asset(
            moderation_status=MODERATION_PENDING,
            publish_result="pending_moderation",
            public_latest_version=None,
        )
        owner = ViewerContext(user_id="u1", user_login="alice", is_system_admin=False)
        self.assertTrue(owner.can_view_skill_asset(asset))

    def test_anonymous_cannot_see_pending_agent_version(self):
        asset = _asset(
            moderation_status=MODERATION_APPROVED,
            public_latest_version="1.0.0",
        )
        pending = _version(
            version="1.1.0",
            moderation_status=MODERATION_PENDING,
            publish_result="pending_moderation",
        )
        self.assertFalse(ANONYMOUS_VIEWER.can_see_skill_version_row(asset, pending))

    def test_agent_does_not_use_group_acl(self):
        asset = _asset(visibility="private")
        outsider = ViewerContext(user_id="u2", user_login="bob", is_system_admin=False)
        # private agent：非 owner/admin 不可见；即使有 grant 查询也不应走到 group（无 db 时亦 False）
        self.assertFalse(outsider.can_view_skill_asset(asset, db=None))


if __name__ == "__main__":
    unittest.main()
