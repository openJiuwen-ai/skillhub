"""POST /recommend identity binding under optional auth."""

from __future__ import annotations

import unittest

from fastapi import HTTPException

from plugins_market.core.viewer_context import ANONYMOUS_VIEWER, ViewerContext
from plugins_market.recommender.schemas import RecommendRequest
from plugins_market.routers.recommender import _resolve_recommend_user_id


def _body(user_id: str = "") -> RecommendRequest:
    return RecommendRequest(user_id=user_id)


class RecommendOptionalAuthTests(unittest.TestCase):
    def test_system_admin_trusts_body_user_id(self) -> None:
        viewer = ViewerContext(user_id="system_admin", user_login="system_admin", is_system_admin=True)
        self.assertEqual(_resolve_recommend_user_id(_body("u1"), viewer), "u1")
        self.assertEqual(_resolve_recommend_user_id(_body(""), viewer), "")

    def test_valid_bearer_uses_token_user(self) -> None:
        viewer = ViewerContext(user_id="u1", user_login="alice", is_system_admin=False)
        self.assertEqual(_resolve_recommend_user_id(_body(""), viewer), "u1")
        self.assertEqual(_resolve_recommend_user_id(_body("u1"), viewer), "u1")

    def test_valid_bearer_rejects_mismatched_body_user_id(self) -> None:
        viewer = ViewerContext(user_id="u1", user_login="alice", is_system_admin=False)
        with self.assertRaises(HTTPException) as ctx:
            _resolve_recommend_user_id(_body("u2"), viewer)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_anonymous_or_invalid_token_cold_starts_and_ignores_body_user_id(self) -> None:
        self.assertEqual(_resolve_recommend_user_id(_body(""), ANONYMOUS_VIEWER), "")
        self.assertEqual(_resolve_recommend_user_id(_body("spoof-user"), ANONYMOUS_VIEWER), "")


if __name__ == "__main__":
    unittest.main()
