# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AuthAdapter contract and Keycloak OIDC profile mapping tests."""

from __future__ import annotations

import base64
import hashlib
import json
import unittest
from functools import partial
from unittest.mock import patch
from urllib.parse import parse_qs, parse_qsl, urlsplit

import httpx

from plugins_market.core.auth_adapters import (
    KeycloakAuthAdapter,
    create_pkce_verifier,
    get_auth_adapter,
)
from plugins_market.core.config import settings


def userinfo_test_token(**changes):
    claims = {"iss": settings.keycloak_issuer_url, "sub": "user-123", "azp": "marketplace", **changes}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


class AuthAdapterRegistryTests(unittest.TestCase):
    def test_registered_adapter(self) -> None:
        self.assertIsNotNone(get_auth_adapter("keycloak"))
        self.assertIsNone(get_auth_adapter("unknown"))


class KeycloakAuthAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original = (
            settings.keycloak_issuer_url,
            settings.keycloak_oauth_enabled,
            settings.keycloak_client_id,
            settings.keycloak_client_secret,
            settings.keycloak_redirect_uri,
            settings.keycloak_oauth_scope,
            settings.oauth_frontend_origin,
        )
        settings.keycloak_issuer_url = "https://iam.example/realms/demo"
        settings.keycloak_oauth_enabled = True
        settings.keycloak_client_id = "marketplace"
        settings.keycloak_client_secret = ""
        settings.keycloak_redirect_uri = "https://hub.example/api/v1/auth/oauth/keycloak/callback"
        settings.keycloak_oauth_scope = "openid profile email"

        self.userinfo = {
            "sub": "user-123",
            "preferred_username": "sso_user",
            "name": "SSO User",
            "picture": "https://iam.example/avatar.png",
        }
        self.logout_endpoint = "https://iam.example/logout"
        self.token_form = {}
        transport = httpx.MockTransport(self.oidc_response)
        client_factory = partial(httpx.AsyncClient, transport=transport)
        client_patch = patch.object(httpx, "AsyncClient", client_factory)
        client_patch.start()
        self.addCleanup(client_patch.stop)

    def oidc_response(self, request: httpx.Request) -> httpx.Response:
        issuer = settings.keycloak_issuer_url
        if str(request.url) == f"{issuer}/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": issuer,
                "authorization_endpoint": "https://iam.example/authorize",
                "token_endpoint": "https://iam.example/token",
                "userinfo_endpoint": "https://iam.example/userinfo",
                "end_session_endpoint": self.logout_endpoint,
            })
        if str(request.url) == "https://iam.example/userinfo":
            self.assertTrue(request.headers["Authorization"].startswith("Bearer "))
            return httpx.Response(200, json=self.userinfo)
        if str(request.url) == "https://iam.example/token":
            self.assertEqual(request.method, "POST")
            self.token_form = dict(parse_qsl(request.content.decode(), keep_blank_values=True))
            return httpx.Response(200, json={"access_token": "access-token", "token_type": "Bearer"})
        raise ValueError(f"Unexpected OIDC request: {request.method} {request.url}")

    def tearDown(self) -> None:
        (
            settings.keycloak_issuer_url,
            settings.keycloak_oauth_enabled,
            settings.keycloak_client_id,
            settings.keycloak_client_secret,
            settings.keycloak_redirect_uri,
            settings.keycloak_oauth_scope,
            settings.oauth_frontend_origin,
        ) = self.original

    async def test_site_config_preserves_existing_buttons_and_gates_keycloak(self) -> None:
        from plugins_market.routers.site_public import get_site_config

        with (
            patch.object(settings, "gitcode_oauth_enabled", True),
            patch.object(settings, "github_oauth_enabled", True),
        ):
            for enabled, issuer, client_id, callback, expected in (
                (False, "https://iam.example", "client", "https://hub.example/cb", False),
                (True, "", "client", "https://hub.example/cb", False),
                (True, "https://iam.example", " ", "https://hub.example/cb", False),
                (True, "https://iam.example", "client", "", False),
                (True, "https://iam.example", "client", "https://hub.example/cb", True),
            ):
                settings.keycloak_oauth_enabled = enabled
                settings.keycloak_issuer_url = issuer
                settings.keycloak_client_id = client_id
                settings.keycloak_redirect_uri = callback
                cfg = await get_site_config()
                self.assertTrue(cfg["gitcode_oauth_enabled"])
                self.assertTrue(cfg["github_oauth_enabled"])
                self.assertEqual(cfg["keycloak_oauth_enabled"], expected)
                self.assertEqual(KeycloakAuthAdapter().enabled(), expected)

    async def test_authorization_url_uses_discovery_and_pkce_s256(self) -> None:
        adapter = KeycloakAuthAdapter()
        verifier = create_pkce_verifier()
        url = await adapter.authorization_url("state-value", verifier)

        query = parse_qs(urlsplit(url).query)
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
        self.assertEqual(query["client_id"], ["marketplace"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["state"], ["state-value"])
        self.assertEqual(query["code_challenge"], [expected])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["scope"], ["openid profile email"])

    async def test_code_exchange_sends_pkce_and_omits_empty_public_client_secret(self) -> None:
        adapter = KeycloakAuthAdapter()
        verifier = create_pkce_verifier()
        async with httpx.AsyncClient() as client:
            token_data = await adapter.exchange_code(client, "auth-code", verifier)

        self.assertEqual(token_data["access_token"], "access-token")
        self.assertEqual(self.token_form["grant_type"], "authorization_code")
        self.assertEqual(self.token_form["code_verifier"], verifier)
        self.assertNotIn("client_secret", self.token_form)

    async def test_userinfo_is_normalized_to_oauth_profile(self) -> None:
        issuer = settings.keycloak_issuer_url
        adapter = KeycloakAuthAdapter()

        profile = await adapter.fetch_user_profile(userinfo_test_token())
        for token in (
            userinfo_test_token(azp="different-client"),
            userinfo_test_token(iss=issuer + "2"),
            userinfo_test_token(sub="other"),
            "opaque",
        ):
            with self.subTest(token_type=token[:6]):
                self.assertIsNone(await adapter.fetch_user_profile(token))
        self.assertEqual((await adapter.fetch_user_profile(userinfo_test_token()))["id"], profile["id"])
        issuer += "-other"
        settings.keycloak_issuer_url = issuer
        other_profile = await KeycloakAuthAdapter().fetch_user_profile(userinfo_test_token())
        self.assertNotEqual(other_profile["id"], profile["id"])

        self.assertTrue(profile["id"].startswith("kc:"))
        self.assertLessEqual(len(profile["id"]), 64)
        self.assertEqual(profile["login"], "sso_user")
        self.assertEqual(profile["username"], "sso_user")
        self.assertEqual(profile["name"], "SSO User")
        self.assertEqual(profile["avatar_url"], "https://iam.example/avatar.png")

    async def test_userinfo_requires_subject(self) -> None:
        adapter = KeycloakAuthAdapter()
        self.userinfo.pop("sub")
        self.assertIsNone(await adapter.fetch_user_profile(userinfo_test_token()))

    async def test_disabled_provider_rejects_bearer_authentication(self) -> None:
        settings.keycloak_oauth_enabled = False
        adapter = KeycloakAuthAdapter()

        self.assertIsNone(await adapter.fetch_user_profile("access-token"))

    async def test_logout_config_uses_discovered_secure_endpoint_and_configured_redirect(self) -> None:
        adapter = KeycloakAuthAdapter()
        settings.oauth_frontend_origin = "https://hub.example/subpath/"

        self.assertEqual(
            await adapter.logout_config(),
            {
                "end_session_endpoint": "https://iam.example/logout",
                "client_id": "marketplace",
                "post_logout_redirect_uri": "https://hub.example/subpath/login?logged_out=1",
            },
        )

    async def test_logout_config_rejects_insecure_or_credentialed_endpoint(self) -> None:
        for endpoint in ("http://iam.example/logout", "https://attacker@iam.example/logout"):
            with self.subTest(endpoint=endpoint):
                self.logout_endpoint = endpoint
                self.assertIsNone(await KeycloakAuthAdapter().logout_config())


if __name__ == "__main__":
    unittest.main()
