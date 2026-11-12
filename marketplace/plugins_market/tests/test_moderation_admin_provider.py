# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import unittest
from unittest.mock import patch

from plugins_market.core.review_admins import is_market_moderation_username
from plugins_market.core.viewer_context import ViewerContext


class ModerationAdminProviderTests(unittest.TestCase):
    @patch("plugins_market.core.review_admins.get_review_admin_usernames", return_value=frozenset({"trusted-admin"}))
    def test_keycloak_username_collision_does_not_grant_moderation_admin(self, _admins) -> None:
        self.assertFalse(is_market_moderation_username("trusted-admin", "keycloak"))

    @patch("plugins_market.core.review_admins.get_review_admin_usernames", return_value=frozenset({"trusted-admin"}))
    def test_existing_provider_allowlist_behavior_is_preserved(self, _admins) -> None:
        self.assertTrue(is_market_moderation_username("trusted-admin", "gitcode"))
        self.assertTrue(is_market_moderation_username("trusted-admin"))

    @patch("plugins_market.core.review_admins.get_review_admin_usernames", return_value=frozenset({"trusted-admin"}))
    def test_viewer_context_scopes_username_privilege_to_provider(self, _admins) -> None:
        keycloak_viewer = ViewerContext(
            user_id="subject-123",
            user_login="trusted-admin",
            is_system_admin=False,
            user_provider="keycloak",
        )
        gitcode_viewer = ViewerContext(
            user_id="42",
            user_login="trusted-admin",
            is_system_admin=False,
            user_provider="gitcode",
        )

        self.assertFalse(keycloak_viewer.is_market_moderation_admin)
        self.assertTrue(gitcode_viewer.is_market_moderation_admin)


if __name__ == "__main__":
    unittest.main()
