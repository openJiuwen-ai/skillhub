# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from plugins_market.core.auth import AuthContext
from plugins_market.core.review_admins import is_market_moderation_username
from plugins_market.core.viewer_context import ViewerContext
from plugins_market.models.site_notifications import SiteNotificationDB
from plugins_market.services import site_notifications as notifications


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

    def test_keycloak_can_only_read_and_acknowledge_its_personal_notifications(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        SiteNotificationDB.__table__.create(engine)
        user = AuthContext(False, "oidc-user", "trusted-admin", acting_user_provider="keycloak")
        with Session(engine) as db:
            with patch.object(notifications, "get_review_admin_usernames", return_value={"trusted-admin"}):
                notifications.notify_review_admins_new_skill_submission(db)
            notifications.notify_publisher_skill_manual_review_approved(db, publisher_id=user.acting_user_id)
            inbox = notifications.list_notifications(db, user)
            self.assertEqual(
                [item.template for item in inbox.items],
                [notifications.NOTIFICATION_TEMPLATE_USER_MANUAL_REVIEW_APPROVED],
            )
            self.assertEqual(notifications.mark_notifications_read_all(db, user), 1)
            for provider in ("gitcode", "github", "agentos"):
                with self.subTest(provider=provider):
                    reviewer = AuthContext(False, "reviewer", "trusted-admin", acting_user_provider=provider)
                    admin_inbox = notifications.list_notifications(db, reviewer)
                    self.assertEqual(
                        [item.template for item in admin_inbox.items],
                        [notifications.NOTIFICATION_TEMPLATE_ADMIN_PENDING],
                    )
                    self.assertEqual(admin_inbox.unread_count, 1)


if __name__ == "__main__":
    unittest.main()
