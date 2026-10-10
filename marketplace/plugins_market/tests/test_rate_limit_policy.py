# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""单元测试：滑动窗口限流器、限流档位/规则匹配、客户端标识推导（issue #90）。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from plugins_market.core.rate_limit import (
    SlidingWindowRateLimiter,
    build_rate_limit_policy,
    client_ip_from_scope,
    user_key_from_scope,
)


class SlidingWindowRateLimiterTests(unittest.TestCase):
    """滑动窗口计数语义：放行直到限额、剩余额度递减、超限拒绝、窗口过期恢复。"""

    def test_allowed_until_limit_then_denied(self) -> None:
        limiter = SlidingWindowRateLimiter()
        key = "ip:1.2.3.4"
        for i in range(5):
            check = limiter.check(key, limit=5, window_sec=60.0)
            self.assertTrue(check.allowed)
            self.assertEqual(check.remaining, 5 - i - 1)
        denied = limiter.check(key, limit=5, window_sec=60.0)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.remaining, 0)
        self.assertEqual(denied.limit, 5)
        self.assertGreater(denied.retry_after, 0)

    def test_limit_zero_is_disabled(self) -> None:
        limiter = SlidingWindowRateLimiter()
        check = limiter.check("k", limit=0, window_sec=60.0)
        self.assertTrue(check.allowed)
        self.assertEqual(check.limit, 0)
        # 不计数：连续调用仍放行
        self.assertTrue(limiter.check("k", limit=0, window_sec=60.0).allowed)

    def test_window_expiry_allows_again(self) -> None:
        limiter = SlidingWindowRateLimiter()
        key = "ip:x"
        now = 1_000_000.0
        with patch("plugins_market.core.rate_limit.time.monotonic", side_effect=lambda: now):
            self.assertTrue(limiter.check(key, limit=2, window_sec=60.0).allowed)
            self.assertTrue(limiter.check(key, limit=2, window_sec=60.0).allowed)
            self.assertFalse(limiter.check(key, limit=2, window_sec=60.0).allowed)
            now += 61.0  # 最老计数离开窗口
            self.assertTrue(limiter.check(key, limit=2, window_sec=60.0).allowed)

    def test_allow_compat_delegates_to_check(self) -> None:
        limiter = SlidingWindowRateLimiter()
        self.assertTrue(limiter.allow("k", limit=1, window_sec=60.0))
        self.assertFalse(limiter.allow("k", limit=1, window_sec=60.0))

    def test_keys_isolated(self) -> None:
        limiter = SlidingWindowRateLimiter()
        self.assertTrue(limiter.check("ip:a", limit=1).allowed)
        self.assertTrue(limiter.check("ip:b", limit=1).allowed)
        self.assertFalse(limiter.check("ip:a", limit=1).allowed)

    def test_stale_buckets_swept(self) -> None:
        """全过期的一次性 key 桶应被清扫，避免内存堆积。"""
        limiter = SlidingWindowRateLimiter()
        now = 1_000_000.0
        with patch("plugins_market.core.rate_limit.time.monotonic", side_effect=lambda: now):
            limiter.check("ip:one-shot", limit=10)  # 留下 1 条计数
        self.assertEqual(limiter.bucket_count(), 1)
        # 窗口过期后主动清扫：one-shot 桶全过期应被回收
        with patch("plugins_market.core.rate_limit.time.monotonic", side_effect=lambda: now + 61):
            evicted = limiter.sweep()
        self.assertEqual(evicted, 1)
        self.assertEqual(limiter.bucket_count(), 0)


class RateLimitPolicyMatchTests(unittest.TestCase):
    """规则匹配：方法+路径 → 档位；先豁免、后分级、首条命中。"""

    def test_public_read_endpoints(self) -> None:
        policy = build_rate_limit_policy()
        self.assertEqual(policy.match("GET", "/api/v1/plugins").name, "public_read")
        self.assertEqual(policy.match("GET", "/api/v1/plugins/skill-1").name, "public_read")
        self.assertEqual(policy.match("GET", "/api/v1/plugins/skill-1/versions/1.0.0").name, "public_read")
        self.assertEqual(policy.match("GET", "/api/v1/artifacts/xyz").name, "public_read")
        self.assertEqual(policy.match("GET", "/api/v1/site/config").name, "public_read")
        self.assertEqual(policy.match("GET", "/api/v1/groups/discover").name, "public_read")
        self.assertEqual(policy.match("GET", "/api/v1/audit/logs").name, "public_read")

    def test_publish_endpoint(self) -> None:
        policy = build_rate_limit_policy()
        self.assertEqual(policy.match("POST", "/api/v1/plugins").name, "publish")
        # 尾斜杠归一化（匹配前已 rstrip）
        self.assertEqual(policy.match("POST", "/api/v1/plugins/").name, "publish")

    def test_batch_endpoints(self) -> None:
        policy = build_rate_limit_policy()
        self.assertEqual(policy.match("GET", "/api/v1/plugins/interactions/batch").name, "batch")
        self.assertEqual(policy.match("POST", "/api/v1/recommend").name, "batch")
        self.assertEqual(policy.match("POST", "/api/v1/recommend/by_queries").name, "batch")
        self.assertEqual(policy.match("POST", "/api/v1/recommend/rerank_mmr").name, "batch")

    def test_auth_endpoints(self) -> None:
        policy = build_rate_limit_policy()
        self.assertEqual(policy.match("GET", "/api/v1/auth/oauth/gitcode/start").name, "auth")
        self.assertEqual(policy.match("GET", "/api/v1/auth/oauth/gitcode/callback").name, "auth")
        self.assertEqual(policy.match("POST", "/api/v1/auth/oauth/github/session").name, "auth")
        self.assertEqual(policy.match("GET", "/api/v1/auth/me").name, "auth")

    def test_default_write_fallback(self) -> None:
        policy = build_rate_limit_policy()
        self.assertEqual(policy.match("POST", "/api/v1/plugins/skill-1/interact").name, "default_write")
        self.assertEqual(policy.match("DELETE", "/api/v1/groups/g-1").name, "default_write")
        self.assertEqual(policy.match("POST", "/api/v1/groups/g-1/members").name, "default_write")
        self.assertEqual(policy.match("POST", "/api/v1/notifications/read-all").name, "default_write")
        self.assertEqual(policy.match("POST", "/api/v1/github/watch").name, "default_write")
        # 兜底：未显式列出的 /api 路径
        self.assertEqual(policy.match("GET", "/api/v1/whatever").name, "default_write")

    def test_exempt_endpoints(self) -> None:
        policy = build_rate_limit_policy()
        self.assertEqual(policy.match("GET", "/api/health").name, "exempt")
        self.assertEqual(policy.match("GET", "/api/v1/playground/quota").name, "exempt")
        self.assertEqual(policy.match("POST", "/api/v1/playground/sessions").name, "exempt")
        self.assertEqual(policy.match("GET", "/api/v1/search").name, "exempt")
        self.assertEqual(policy.match("GET", "/api/v1/skills").name, "exempt")
        self.assertEqual(policy.match("GET", "/api/v1/skills/foo").name, "exempt")
        self.assertEqual(policy.match("GET", "/api/v1/skills/foo/versions/1.0.0/files").name, "exempt")
        self.assertEqual(policy.match("GET", "/api/v1/download").name, "exempt")
        self.assertEqual(policy.match("GET", "/api/v1/resolve").name, "exempt")
        self.assertEqual(policy.match("POST", "/api/v1/plugins/skill-import").name, "exempt")
        self.assertEqual(policy.match("POST", "/api/v1/plugins/git-sources").name, "exempt")
        self.assertEqual(policy.match("POST", "/api/v1/plugins/git-sources/1/sync").name, "exempt")
        self.assertEqual(policy.match("DELETE", "/api/v1/plugins/git-sources/1").name, "exempt")

    def test_skills_exempt_does_not_swallow_sibling_paths(self) -> None:
        """回归：收窄 /api/v1/skills* 后，同名前缀的企业接口不再被误豁免。"""
        policy = build_rate_limit_policy()
        # 缺少 / 边界的兄弟路径不被 /api/v1/skills + /api/v1/skills/* 命中
        self.assertEqual(policy.match("GET", "/api/v1/skills-admin").name, "default_write")
        self.assertEqual(policy.match("GET", "/api/v1/skillsexport").name, "default_write")

    def test_head_treated_as_get(self) -> None:
        policy = build_rate_limit_policy()
        self.assertEqual(policy.match("HEAD", "/api/v1/plugins/skill-1").name, "public_read")

    def test_disabled_policy_matches_nothing(self) -> None:
        policy = build_rate_limit_policy(enabled=False)
        self.assertIsNone(policy.match("GET", "/api/v1/plugins/skill-1"))

    def test_zero_limit_tier_still_matches(self) -> None:
        # 档位关闭（limit=0）仍可被匹配到，由中间件决定直通 —— 保证"关闭"语义不改变路由归属
        policy = build_rate_limit_policy(public_read_per_minute=0)
        self.assertEqual(policy.match("GET", "/api/v1/plugins/skill-1").name, "public_read")


class RateLimitPolicyKeysTests(unittest.TestCase):
    """维度推导：ip 档按 IP；user 档有凭证按用户、无凭证回退 IP。"""

    def test_ip_scope(self) -> None:
        policy = build_rate_limit_policy()
        tier = policy.tier("public_read")
        # key 含档位名，确保不同档位独立计数桶（IP 档不因 user_key 改变维度）
        self.assertEqual(policy.keys_for(tier, client_ip="1.1.1.1", user_key=None), ["ip:1.1.1.1:public_read"])
        self.assertEqual(policy.keys_for(tier, client_ip="1.1.1.1", user_key="tok_abc"), ["ip:1.1.1.1:public_read"])

    def test_user_scope_with_and_without_credential(self) -> None:
        policy = build_rate_limit_policy()
        tier = policy.tier("publish")
        self.assertEqual(policy.keys_for(tier, client_ip="1.1.1.1", user_key="tok_abc"), ["user:tok_abc:publish"])
        self.assertEqual(policy.keys_for(tier, client_ip="1.1.1.1", user_key=None), ["ip:1.1.1.1:publish"])

    def test_cross_tier_same_ip_buckets_isolated(self) -> None:
        """同一 IP 的 public_read 流量不得耗尽 auth 档位预算。"""
        policy = build_rate_limit_policy()
        limiter = SlidingWindowRateLimiter()
        ip = "203.0.113.7"
        pub_tier = policy.tier("public_read")
        auth_tier = policy.tier("auth")
        # 用满 public_read 预算（默认 300/min，远超 auth 的 20/min）
        pub_key = policy.keys_for(pub_tier, client_ip=ip, user_key=None)[0]
        for _ in range(pub_tier.limit):
            self.assertTrue(
                limiter.check(pub_key, limit=pub_tier.limit, window_sec=pub_tier.window_sec).allowed
            )
        # auth 档位 key 不同 → 独立桶，仍可放行
        auth_key = policy.keys_for(auth_tier, client_ip=ip, user_key=None)[0]
        self.assertNotEqual(pub_key, auth_key)
        self.assertTrue(
            limiter.check(auth_key, limit=auth_tier.limit, window_sec=auth_tier.window_sec).allowed
        )


class ClientKeyDerivationTests(unittest.TestCase):
    """客户端 IP 与用户 key 推导。"""

    def test_client_ip_forwarded_for_first(self) -> None:
        scope = {
            "headers": [(b"x-forwarded-for", b"203.0.113.9, 10.0.0.1"), (b"x-real-ip", b"10.0.0.2")],
            "client": ("127.0.0.1", 1234),
        }
        self.assertEqual(client_ip_from_scope(scope), "203.0.113.9")

    def test_client_ip_real_ip_fallback(self) -> None:
        scope = {"headers": [(b"x-real-ip", b"10.0.0.2")], "client": ("127.0.0.1", 1234)}
        self.assertEqual(client_ip_from_scope(scope), "10.0.0.2")

    def test_client_ip_peer_fallback(self) -> None:
        scope = {"headers": [], "client": ("127.0.0.1", 1234)}
        self.assertEqual(client_ip_from_scope(scope), "127.0.0.1")
        self.assertEqual(client_ip_from_scope({"headers": []}), "unknown")

    def test_client_ip_trust_forwarded_toggle(self) -> None:
        """trust_forwarded=False 时忽略可伪造的 XFF/X-Real-IP，回退 peer。"""
        scope = {
            "headers": [(b"x-forwarded-for", b"203.0.113.9"), (b"x-real-ip", b"10.0.0.2")],
            "client": ("127.0.0.1", 1234),
        }
        self.assertEqual(client_ip_from_scope(scope), "203.0.113.9")
        self.assertEqual(client_ip_from_scope(scope, trust_forwarded=False), "127.0.0.1")

    def test_user_key_bearer_stable_hash(self) -> None:
        scope = {"headers": [(b"authorization", b"Bearer secret-token-123")]}
        key = user_key_from_scope(scope)
        self.assertTrue(key.startswith("tok_"))
        self.assertEqual(len(key), 4 + 16)
        # 同 token 稳定；不同 token 不同 key
        same = user_key_from_scope({"headers": [(b"authorization", b"Bearer secret-token-123")]})
        other = user_key_from_scope({"headers": [(b"authorization", b"Bearer another")]})
        self.assertEqual(key, same)
        self.assertNotEqual(key, other)
        # 不泄露明文 token
        self.assertNotIn("secret-token-123", key)

    def test_user_key_system_token(self) -> None:
        key = user_key_from_scope({"headers": [(b"x-system-token", b"adm-secret")]})
        self.assertTrue(key.startswith("sys_"))
        # 不同 token 值落入不同桶，避免伪造值与真实系统通道共享配额
        other = user_key_from_scope({"headers": [(b"x-system-token", b"forged-value")]})
        self.assertNotEqual(key, other)
        # 不泄露明文
        self.assertNotIn("adm-secret", key)

    def test_user_key_none_without_credentials(self) -> None:
        self.assertIsNone(user_key_from_scope({"headers": []}))
        # 非 Bearer 的 Authorization 不派生用户 key
        self.assertIsNone(user_key_from_scope({"headers": [(b"authorization", b"Basic dXNlcjpwYXNz")]}))


if __name__ == "__main__":
    unittest.main()
