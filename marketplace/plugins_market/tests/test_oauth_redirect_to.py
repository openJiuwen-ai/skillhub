# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OAuth redirect_to（loopback 回跳）白名单与回跳 URL 构造测试。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, parse_qsl, urlsplit

from plugins_market.routers.oauth_provider import (
    OAuthProvider,
    _append_query_params,
    _load_state_record,
    _login_redirect_target,
    _pending_key,
    _state_key,
    _validate_loopback_redirect,
    oauth_callback,
    oauth_start,
)


class ValidateLoopbackRedirectTests(unittest.TestCase):
    """白名单必须基于 URL 解析精确匹配，禁止字符串前缀判断。"""

    def test_accepts_loopback_http_with_path(self) -> None:
        for raw in (
            "http://127.0.0.1:3000/callback",
            "http://[::1]:8080/cb",
            "http://127.0.0.1:3000/callback?client_state=abc",
        ):
            with self.subTest(raw=raw):
                self.assertIsNotNone(_validate_loopback_redirect(raw))

    def test_rejects_prefix_tricks(self) -> None:
        # 这些都以 "127" / "localhost" 子串开头，字符串前缀判断会放行
        for raw in (
            "http://127.0.0.1.evil.com/callback",
            "http://127.0.0.1@evil.com/callback",
            "http://localhost.evil.com/callback",
            "http://evil.com#@127.0.0.1/",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(_validate_loopback_redirect(raw))

    def test_rejects_localhost_per_rfc8252(self) -> None:
        # localhost 依赖本机 hosts/DNS 解析，可被重定向到非回环地址（RFC 8252 §7.3），
        # 只允许字面 IP
        self.assertIsNone(_validate_loopback_redirect("http://localhost:5173/callback"))

    def test_rejects_non_loopback_or_non_http(self) -> None:
        for raw in (
            "https://127.0.0.1:3000/callback",
            "https://evil.com/callback",
            "http://192.168.1.5:3000/callback",
            "http://0.0.0.0:3000/",
            "file:///etc/passwd",
            "http://127.0.0.1:3000",  # 无 path
            "",
            None,
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(_validate_loopback_redirect(raw))

    def test_roundtrip_preserves_query(self) -> None:
        raw = "http://127.0.0.1:3000/callback?client_state=x"
        self.assertEqual(_validate_loopback_redirect(raw), raw)


class AppendQueryParamsTests(unittest.TestCase):
    def test_merges_without_duplicating_separator(self) -> None:
        merged = _append_query_params(
            "http://127.0.0.1:3000/callback?client_state=abc",
            {"oauth_session": "s1", "oauth_provider": "gitcode"},
        )
        parts = urlsplit(merged)
        self.assertEqual(parts.hostname, "127.0.0.1")
        query = dict(parse_qsl(parts.query))
        self.assertEqual(query, {"client_state": "abc", "oauth_session": "s1", "oauth_provider": "gitcode"})

    def test_appends_when_no_existing_query(self) -> None:
        merged = _append_query_params("http://host/login", {"oauth_error": "msg"})
        self.assertEqual(dict(parse_qsl(urlsplit(merged).query)), {"oauth_error": "msg"})


class StateRecordTests(unittest.TestCase):
    def test_legacy_value_resolves_to_none(self) -> None:
        # 旧实现存的是 "1"；滚动升级期间可能读到，须安全降级为无 redirect_to
        self.assertIsNone(_load_state_record("1"))
        self.assertIsNone(_load_state_record(None))

    def test_json_value_roundtrip(self) -> None:
        record = _load_state_record(json.dumps({"redirect_to": "http://127.0.0.1:3000/cb"}))
        self.assertEqual(record, {"redirect_to": "http://127.0.0.1:3000/cb"})

    def test_login_redirect_target_falls_back_to_frontend(self) -> None:
        target = _login_redirect_target(None)
        self.assertTrue(target.endswith("/login"))

    def test_login_redirect_target_uses_validated_loopback(self) -> None:
        target = _login_redirect_target({"redirect_to": "http://127.0.0.1:3000/cb"})
        self.assertEqual(target, "http://127.0.0.1:3000/cb")

    def test_login_redirect_target_ignores_non_loopback_record(self) -> None:
        # state 记录被篡改成外网地址时仍回退前端 /login（纵深防御）
        target = _login_redirect_target({"redirect_to": "https://evil.com/cb"})
        self.assertTrue(target.endswith("/login"))


class KeycloakPkceRoundtripTests(unittest.IsolatedAsyncioTestCase):
    async def test_verifier_is_bound_to_state_and_used_by_callback(self) -> None:
        class MemoryStore:
            def __init__(self) -> None:
                self.values: dict[str, str] = {}

            def set_ex(self, key: str, value: str, _ttl: int) -> None:
                self.values[key] = value

            def get(self, key: str) -> str | None:
                return self.values.get(key)

            def get_del(self, key: str) -> str | None:
                return self.values.pop(key, None)

            def delete(self, key: str) -> None:
                self.values.pop(key, None)

        class FakeKeycloakAdapter:
            async def authorization_url(self, state: str, code_verifier: str | None = None) -> str:
                return f"https://iam.example/authorize?state={state}&verifier={code_verifier}"

            async def logout_config(self) -> dict[str, str]:
                return {
                    "end_session_endpoint": "https://iam.example/logout",
                    "client_id": "marketplace",
                    "post_logout_redirect_uri": "https://hub.example/login?logged_out=1",
                }

        verifier = "test-pkce-verifier"
        store = MemoryStore()
        adapter = FakeKeycloakAdapter()
        with (
            patch("plugins_market.routers.oauth_provider.get_oauth_str_store", return_value=store),
            patch("plugins_market.routers.oauth_provider._assert_oauth_ready"),
            patch("plugins_market.routers.oauth_provider.create_pkce_verifier", return_value=verifier),
            patch("plugins_market.routers.oauth_provider.get_auth_adapter", return_value=adapter),
            patch(
                "plugins_market.routers.oauth_provider._exchange_code_for_token_json",
                new=AsyncMock(return_value={
                    "access_token": "access-token",
                    "id_token": "id-token-hint",
                    "token_type": "Bearer",
                }),
            ) as exchange,
            patch(
                "plugins_market.routers.oauth_provider.fetch_oauth_user_profile",
                new=AsyncMock(return_value={"id": "subject-1", "login": "user-1", "name": "User"}),
            ),
        ):
            start_response = await oauth_start(OAuthProvider.keycloak)
            start_query = parse_qs(urlsplit(start_response.headers["location"]).query)
            state = start_query["state"][0]
            state_record = json.loads(store.get(_state_key(OAuthProvider.keycloak, state)))
            self.assertEqual(state_record["code_verifier"], verifier)

            callback_response = await oauth_callback(OAuthProvider.keycloak, code="auth-code", state=state)
            exchange.assert_awaited_once()
            self.assertEqual(exchange.await_args.args[3], verifier)

            callback_query = parse_qs(urlsplit(callback_response.headers["location"]).query)
            pending_id = callback_query["oauth_session"][0]
            pending = json.loads(store.get(_pending_key(OAuthProvider.keycloak, pending_id)))
            self.assertEqual(pending["logout"]["id_token_hint"], "id-token-hint")
            self.assertEqual(pending["logout"]["end_session_endpoint"], "https://iam.example/logout")

if __name__ == "__main__":
    unittest.main()
