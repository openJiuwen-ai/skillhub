# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""功能测试：RateLimitMiddleware 端到端行为（issue #90）。

使用 FastAPI TestClient 走真实 ASGI 链路验证：
- 分级限流（public_read / publish / batch / auth / default_write）
- 429 响应体（标准错误信封）与 X-RateLimit-* / Retry-After 响应头
- IP 维度与凭证（Token）维度隔离、匿名回退 IP
- 豁免路径、OPTIONS 预检、档位关闭与全局开关关闭
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins_market.core.middleware.rate_limit import RateLimitMiddleware
from plugins_market.core.rate_limit import SlidingWindowRateLimiter, build_rate_limit_policy


def _build_app(**policy_kwargs) -> TestClient:
    """构建仅含限流中间件的最小应用，policy/limiter 均为测试实例（状态隔离）。"""
    policy = build_rate_limit_policy(**policy_kwargs)
    limiter = SlidingWindowRateLimiter()
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, policy=policy, limiter=limiter)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/plugins/{asset_id}")
    async def plugin_detail(asset_id: str):
        return {"id": asset_id}

    @app.post("/api/v1/plugins")
    async def publish():
        return {"ok": True}

    @app.get("/api/v1/plugins/interactions/batch")
    async def interactions_batch():
        return {"items": []}

    @app.get("/api/v1/auth/oauth/gitcode/start")
    async def oauth_start():
        return {"url": "https://gitcode.com/oauth/authorize"}

    @app.get("/api/v1/playground/quota")
    async def playground_quota():
        return {"quota": 1}

    @app.post("/api/v1/plugins/{asset_id}/interact")
    async def interact(asset_id: str):
        return {"ok": True}

    return TestClient(app)


class RateLimitMiddlewareFunctionalTests(unittest.TestCase):
    """端到端限流行为。审计写入（audit_failed_mutation）mock 掉以保持测试环境无关。"""

    def setUp(self) -> None:
        self._audit_patcher = patch("plugins_market.core.middleware.rate_limit.audit_failed_mutation")
        self._mock_audit = self._audit_patcher.start()

    def tearDown(self) -> None:
        self._audit_patcher.stop()

    # ── public_read（按 IP，默认 300/min） ───────────────────────────────

    def test_public_read_limited_to_default_300(self) -> None:
        client = _build_app()
        for _ in range(300):
            resp = client.get("/api/v1/plugins/skill-1")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("x-ratelimit-limit", resp.headers)
        resp = client.get("/api/v1/plugins/skill-1")
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.headers["x-ratelimit-limit"], "300")
        self.assertEqual(resp.headers["x-ratelimit-remaining"], "0")
        self.assertIn("x-ratelimit-reset", resp.headers)
        self.assertIn("retry-after", resp.headers)

    def test_429_standard_error_envelope(self) -> None:
        client = _build_app(public_read_per_minute=1)
        client.get("/api/v1/plugins/skill-1")
        resp = client.get("/api/v1/plugins/skill-1")
        self.assertEqual(resp.status_code, 429)
        detail = resp.json()["detail"]
        self.assertEqual(detail["error"], "rate_limited")
        self.assertEqual(detail["error_code"], "SKILLHUB_RATE_LIMITED")
        self.assertEqual(detail["http_status"], 429)
        self.assertTrue(detail["message"])

    def test_remaining_decrements_on_allowed_requests(self) -> None:
        client = _build_app(public_read_per_minute=5)
        first = client.get("/api/v1/plugins/skill-1")
        self.assertEqual(first.headers["x-ratelimit-remaining"], "4")
        second = client.get("/api/v1/plugins/skill-1")
        self.assertEqual(second.headers["x-ratelimit-remaining"], "3")

    def test_public_read_budget_shared_across_paths_per_ip(self) -> None:
        client = _build_app(public_read_per_minute=2)
        client.get("/api/v1/plugins/skill-1")
        client.get("/api/v1/plugins/skill-2")
        # 同一 IP 额度耗尽：第三条 GET 被拒（不同 asset 路径共享 IP 预算）
        self.assertEqual(client.get("/api/v1/plugins/skill-3").status_code, 429)

    # ── publish（按用户，默认 10/min） ──────────────────────────────────

    def test_publish_per_user_isolation(self) -> None:
        client = _build_app(publish_per_minute=2)
        headers_a = {"Authorization": "Bearer token-a"}
        headers_b = {"Authorization": "Bearer token-b"}
        self.assertEqual(client.post("/api/v1/plugins", headers=headers_a).status_code, 200)
        self.assertEqual(client.post("/api/v1/plugins", headers=headers_a).status_code, 200)
        self.assertEqual(client.post("/api/v1/plugins", headers=headers_a).status_code, 429)
        # 用户 B 额度独立，不受 A 影响
        self.assertEqual(client.post("/api/v1/plugins", headers=headers_b).status_code, 200)
        self.assertEqual(client.post("/api/v1/plugins", headers=headers_b).status_code, 200)
        self.assertEqual(client.post("/api/v1/plugins", headers=headers_b).status_code, 429)

    def test_publish_anonymous_falls_back_to_ip(self) -> None:
        client = _build_app(publish_per_minute=2)
        self.assertEqual(client.post("/api/v1/plugins").status_code, 200)
        self.assertEqual(client.post("/api/v1/plugins").status_code, 200)
        self.assertEqual(client.post("/api/v1/plugins").status_code, 429)
        # 带凭证用户与匿名 IP 相互独立
        self.assertEqual(
            client.post("/api/v1/plugins", headers={"Authorization": "Bearer user-tok"}).status_code, 200
        )

    def test_system_token_isolated_from_bearer_users(self) -> None:
        client = _build_app(publish_per_minute=2)
        user_headers = {"Authorization": "Bearer user-tok"}
        sys_headers = {"X-System-Token": "adm-secret"}
        self.assertEqual(client.post("/api/v1/plugins", headers=user_headers).status_code, 200)
        self.assertEqual(client.post("/api/v1/plugins", headers=user_headers).status_code, 200)
        self.assertEqual(client.post("/api/v1/plugins", headers=user_headers).status_code, 429)
        # system 通道独立计数
        self.assertEqual(client.post("/api/v1/plugins", headers=sys_headers).status_code, 200)
        self.assertEqual(client.post("/api/v1/plugins", headers=sys_headers).status_code, 200)

    def test_system_token_values_isolated(self) -> None:
        """回归（PR #226 P2）：不同 X-System-Token 值各自独立计数，伪造值不耗尽真实通道额度。"""
        client = _build_app(publish_per_minute=2)
        real = {"X-System-Token": "real-admin-token"}
        forged = {"X-System-Token": "forged-value"}
        # 真实 token 用满自身 2/min
        self.assertEqual(client.post("/api/v1/plugins", headers=real).status_code, 200)
        self.assertEqual(client.post("/api/v1/plugins", headers=real).status_code, 200)
        self.assertEqual(client.post("/api/v1/plugins", headers=real).status_code, 429)
        # 伪造值独立预算，不受真实通道耗尽影响（若共享 system 桶则首条即 429）
        self.assertEqual(client.post("/api/v1/plugins", headers=forged).status_code, 200)
        self.assertEqual(client.post("/api/v1/plugins", headers=forged).status_code, 200)
        self.assertEqual(client.post("/api/v1/plugins", headers=forged).status_code, 429)

    # ── batch（按用户，默认 5/min） ─────────────────────────────────────

    def test_batch_tier_limited(self) -> None:
        client = _build_app(batch_per_minute=2)
        self.assertEqual(client.get("/api/v1/plugins/interactions/batch").status_code, 200)
        self.assertEqual(client.get("/api/v1/plugins/interactions/batch").status_code, 200)
        self.assertEqual(client.get("/api/v1/plugins/interactions/batch").status_code, 429)

    # ── auth（按 IP，默认 5/min） ───────────────────────────────────────

    def test_auth_tier_limited_by_ip(self) -> None:
        client = _build_app(auth_per_minute=2)
        self.assertEqual(client.get("/api/v1/auth/oauth/gitcode/start").status_code, 200)
        self.assertEqual(client.get("/api/v1/auth/oauth/gitcode/start").status_code, 200)
        self.assertEqual(client.get("/api/v1/auth/oauth/gitcode/start").status_code, 429)

    # ── default_write（按用户，默认 30/min） ────────────────────────────

    def test_default_write_tier_limited(self) -> None:
        client = _build_app(default_write_per_minute=2)
        headers = {"Authorization": "Bearer user-tok"}
        self.assertEqual(
            client.post("/api/v1/plugins/skill-1/interact", headers=headers).status_code, 200
        )
        self.assertEqual(
            client.post("/api/v1/plugins/skill-1/interact", headers=headers).status_code, 200
        )
        self.assertEqual(
            client.post("/api/v1/plugins/skill-1/interact", headers=headers).status_code, 429
        )

    # ── 豁免 / 预检 / 开关 ──────────────────────────────────────────────

    def test_exempt_paths_never_limited(self) -> None:
        client = _build_app(public_read_per_minute=1)
        for _ in range(5):
            health = client.get("/api/health")
            self.assertEqual(health.status_code, 200)
            self.assertNotIn("x-ratelimit-limit", health.headers)
            playground = client.get("/api/v1/playground/quota")
            self.assertEqual(playground.status_code, 200)
            self.assertNotIn("x-ratelimit-limit", playground.headers)

    def test_options_preflight_not_counted(self) -> None:
        client = _build_app(public_read_per_minute=1)
        options = client.options("/api/v1/plugins/skill-1")
        self.assertNotEqual(options.status_code, 429)
        # OPTIONS 不消耗额度：紧接着的 GET 仍放行
        self.assertEqual(client.get("/api/v1/plugins/skill-1").status_code, 200)
        self.assertEqual(client.get("/api/v1/plugins/skill-1").status_code, 429)

    def test_disabled_tier_is_passthrough(self) -> None:
        client = _build_app(public_read_per_minute=0)
        for _ in range(70):
            resp = client.get("/api/v1/plugins/skill-1")
            self.assertEqual(resp.status_code, 200)
        self.assertNotIn("x-ratelimit-limit", resp.headers)

    def test_global_switch_off_is_passthrough(self) -> None:
        client = _build_app(enabled=False)
        for _ in range(70):
            self.assertEqual(client.get("/api/v1/plugins/skill-1").status_code, 200)


if __name__ == "__main__":
    unittest.main()
