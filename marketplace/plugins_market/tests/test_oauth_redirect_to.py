# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OAuth redirect_to（loopback 回跳）白名单与回跳 URL 构造测试。"""

from __future__ import annotations

import base64
import hashlib
import json
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, parse_qsl, urlsplit

from plugins_market.core.errors import BusinessError
from plugins_market.core.oauth_session_store import _MemoryOAuthStore
from plugins_market.routers.oauth_provider import (
    OAuthProvider,
    OAuthSessionBody,
    _append_query_params,
    _load_state_record,
    _login_redirect_target,
    _pending_key,
    _state_key,
    _validate_loopback_redirect,
    oauth_callback,
    oauth_session_exchange,
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
        store = _MemoryOAuthStore()
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
            browser_verifier = "k" * 43
            digest = hashlib.sha256(browser_verifier.encode("ascii")).digest()
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
            start_response = await oauth_start(OAuthProvider.keycloak, browser_challenge=challenge)
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
            session_key = _pending_key(OAuthProvider.keycloak, pending_id)
            self.assertEqual(pending["browser_challenge"], challenge)
            for bad_verifier in (None, "x" * 43):
                with self.subTest(browser_verifier=bad_verifier):
                    store.set_ex(session_key, json.dumps(pending), 120)
                    with self.assertRaises(BusinessError) as rejected:
                        await oauth_session_exchange(
                            OAuthProvider.keycloak, OAuthSessionBody(session=pending_id, browser_verifier=bad_verifier)
                        )
                    self.assertEqual(rejected.exception.error_code, "SKILLHUB_OAUTH_SESSION_MISMATCH")
            store.set_ex(session_key, json.dumps(pending), 120)
            session = await oauth_session_exchange(
                OAuthProvider.keycloak, OAuthSessionBody(session=pending_id, browser_verifier=browser_verifier)
            )
            self.assertEqual(session.data["access_token"], "access-token")
            self.assertNotIn("browser_challenge", session.data)
            with self.assertRaises(BusinessError):
                await oauth_session_exchange(
                    OAuthProvider.keycloak, OAuthSessionBody(session=pending_id, browser_verifier=browser_verifier)
                )

            loopback = "http://127.0.0.1:30900/auth/callback"
            for redirect, invalid in ((None, None), (None, "short"), (loopback, challenge)):
                with self.subTest(redirect=redirect, challenge=invalid), self.assertRaises(BusinessError):
                    await oauth_start(OAuthProvider.keycloak, redirect, invalid)
            start_cli = await oauth_start(OAuthProvider.keycloak, redirect_to=loopback)
            cli_state = parse_qs(urlsplit(start_cli.headers["location"]).query)["state"][0]
            cli_callback = await oauth_callback(OAuthProvider.keycloak, code="cli-code", state=cli_state)
            self.assertTrue(cli_callback.headers["location"].startswith(loopback + "?"))
            cli_pending = parse_qs(urlsplit(cli_callback.headers["location"]).query)["oauth_session"][0]
            cli_key = _pending_key(OAuthProvider.keycloak, cli_pending)
            raw_cli = store.get(cli_key)
            with self.assertRaises(BusinessError):
                await oauth_session_exchange(
                    OAuthProvider.keycloak, OAuthSessionBody(session=cli_pending, browser_verifier=browser_verifier)
                )
            store.set_ex(cli_key, raw_cli, 120)
            cli = await oauth_session_exchange(OAuthProvider.keycloak, OAuthSessionBody(session=cli_pending))
            self.assertEqual(cli.data["access_token"], "access-token")


class ExistingOAuthProviderFlowRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_providers_keep_the_shared_start_callback_flow(self) -> None:
        store = _MemoryOAuthStore()
        exchanged_providers: list[OAuthProvider] = []
        profiled_tokens: list[tuple[str, str]] = []

        async def exchange(_client, provider, _code, code_verifier=None):
            exchanged_providers.append(provider)
            self.assertIsNone(code_verifier)
            return {"access_token": f"{provider.value}-access-token"}

        async def profile(provider: str, access_token: str):
            profiled_tokens.append((provider, access_token))
            return {"id": f"{provider}-user", "login": f"{provider}-login"}

        with (
            patch("plugins_market.routers.oauth_provider.get_oauth_str_store", return_value=store),
            patch("plugins_market.routers.oauth_provider._assert_oauth_ready"),
            patch("plugins_market.routers.oauth_provider.get_auth_adapter") as get_adapter,
            patch(
                "plugins_market.routers.oauth_provider._exchange_code_for_token_json",
                new=AsyncMock(side_effect=exchange),
            ),
            patch("plugins_market.routers.oauth_provider.fetch_oauth_user_profile", new=AsyncMock(side_effect=profile)),
        ):
            for provider in (OAuthProvider.gitcode, OAuthProvider.github, OAuthProvider.agentos):
                with self.subTest(provider=provider.value):
                    start = await oauth_start(provider)
                    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
                    callback = await oauth_callback(provider, code="auth-code", state=state)
                    callback_query = parse_qs(urlsplit(callback.headers["location"]).query)
                    pending_id = callback_query["oauth_session"][0]
                    self.assertEqual(callback_query["oauth_provider"], [provider.value])
                    pending = json.loads(store.get(_pending_key(provider, pending_id)))
                    self.assertEqual(pending["provider"], provider.value)
                    self.assertEqual(pending["user"]["id"], f"{provider.value}-user")
                    self.assertNotIn("logout", pending)

        self.assertEqual(exchanged_providers, [OAuthProvider.gitcode, OAuthProvider.github, OAuthProvider.agentos])
        self.assertEqual(
            profiled_tokens,
            [
                ("gitcode", "gitcode-access-token"),
                ("github", "github-access-token"),
                ("agentos", "agentos-access-token"),
            ],
        )
        get_adapter.assert_not_called()

if __name__ == "__main__":
    unittest.main()
