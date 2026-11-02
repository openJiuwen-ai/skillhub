# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AuthAdapter contract and Keycloak OIDC profile mapping tests."""

from __future__ import annotations

import base64
import hashlib
import unittest
from urllib.parse import parse_qs, urlsplit

import httpx

from plugins_market.core.auth_adapters import (
    KeycloakAuthAdapter,
    create_pkce_verifier,
    get_auth_adapter,
)
from plugins_market.core.config import settings


class AuthAdapterRegistryTests(unittest.TestCase):
    def test_registered_adapters(self) -> None:
        for provider in ("gitcode", "github", "agentos", "keycloak"):
            with self.subTest(provider=provider):
                self.assertIsNotNone(get_auth_adapter(provider))
        self.assertIsNone(get_auth_adapter("unknown"))


class KeycloakAuthAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original = (
            settings.keycloak_issuer_url,
            settings.keycloak_client_id,
            settings.keycloak_client_secret,
            settings.keycloak_redirect_uri,
            settings.keycloak_oauth_scope,
        )
        settings.keycloak_issuer_url = "https://iam.example/realms/demo"
        settings.keycloak_client_id = "marketplace"
        settings.keycloak_client_secret = ""
        settings.keycloak_redirect_uri = "https://hub.example/api/v1/auth/oauth/keycloak/callback"
        settings.keycloak_oauth_scope = "openid profile email"

    def tearDown(self) -> None:
        (
            settings.keycloak_issuer_url,
            settings.keycloak_client_id,
            settings.keycloak_client_secret,
            settings.keycloak_redirect_uri,
            settings.keycloak_oauth_scope,
        ) = self.original

    async def test_authorization_url_uses_discovery_and_pkce_s256(self) -> None:
        issuer = settings.keycloak_issuer_url
        adapter = KeycloakAuthAdapter()
        adapter._discovery = {
            "issuer": issuer,
            "authorization_endpoint": "https://iam.example/authorize",
            "token_endpoint": "https://iam.example/token",
            "userinfo_endpoint": "https://iam.example/userinfo",
        }
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
        issuer = settings.keycloak_issuer_url
        adapter = KeycloakAuthAdapter()
        adapter._discovery = {
            "issuer": issuer,
            "authorization_endpoint": "https://iam.example/authorize",
            "token_endpoint": "https://iam.example/token",
            "userinfo_endpoint": "https://iam.example/userinfo",
        }
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update({key: values[0] for key, values in parse_qs(request.content.decode()).items()})
            return httpx.Response(200, json={"access_token": "access-token", "token_type": "Bearer"})

        verifier = create_pkce_verifier()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            token_data = await adapter.exchange_code(client, "auth-code", verifier)

        self.assertEqual(token_data["access_token"], "access-token")
        self.assertEqual(seen["grant_type"], "authorization_code")
        self.assertEqual(seen["code_verifier"], verifier)
        self.assertNotIn("client_secret", seen)

    async def test_userinfo_is_normalized_to_oauth_profile(self) -> None:
        issuer = settings.keycloak_issuer_url
        adapter = KeycloakAuthAdapter()
        adapter._discovery = {
            "issuer": issuer,
            "authorization_endpoint": "https://iam.example/authorize",
            "token_endpoint": "https://iam.example/token",
            "userinfo_endpoint": "https://iam.example/userinfo",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Authorization"], "Bearer access-token")
            return httpx.Response(
                200,
                json={"sub": "user-123", "preferred_username": "sso_user", "name": "SSO User", "picture": "https://iam.example/avatar.png"},
            )

        original_async_client = httpx.AsyncClient

        def client_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return original_async_client(*args, **kwargs)

        httpx.AsyncClient = client_factory  # type: ignore[assignment]
        try:
            profile = await adapter.fetch_user_profile("access-token")
        finally:
            httpx.AsyncClient = original_async_client  # type: ignore[assignment]

        self.assertEqual(profile["id"], "user-123")
        self.assertEqual(profile["login"], "sso_user")
        self.assertEqual(profile["username"], "sso_user")
        self.assertEqual(profile["name"], "SSO User")
        self.assertEqual(profile["avatar_url"], "https://iam.example/avatar.png")

    async def test_userinfo_requires_subject(self) -> None:
        adapter = KeycloakAuthAdapter()
        adapter._discovery = {
            "issuer": settings.keycloak_issuer_url,
            "authorization_endpoint": "https://iam.example/authorize",
            "token_endpoint": "https://iam.example/token",
            "userinfo_endpoint": "https://iam.example/userinfo",
        }
        original_async_client = httpx.AsyncClient

        def client_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda _: httpx.Response(200, json={"preferred_username": "no-sub"}))
            return original_async_client(*args, **kwargs)

        httpx.AsyncClient = client_factory  # type: ignore[assignment]
        try:
            self.assertIsNone(await adapter.fetch_user_profile("access-token"))
        finally:
            httpx.AsyncClient = original_async_client  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
