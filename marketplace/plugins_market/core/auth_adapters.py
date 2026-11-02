# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider adapters used by OAuth login and bearer-token authentication.

Add a new IAM by implementing ``AuthAdapter`` and registering it in
``get_auth_adapter``. Provider-specific endpoints and profile mapping stay out
of the auth dependencies and OAuth routes.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from plugins_market.core.config import settings
from plugins_market.core.gitcode_user import fetch_gitcode_profile
from plugins_market.core.github_user import fetch_github_profile


class AuthAdapter(ABC):
    """Common contract for interactive login and access-token validation."""

    provider: str

    @abstractmethod
    def enabled(self) -> bool:
        """Whether this provider is available for interactive login."""

    @abstractmethod
    async def authorization_url(self, state: str, code_verifier: str | None = None) -> str:
        """Build the upstream authorization URL."""

    @abstractmethod
    async def exchange_code(
        self, client: httpx.AsyncClient, code: str, code_verifier: str | None = None
    ) -> dict[str, Any]:
        """Exchange an authorization code for tokens."""

    @abstractmethod
    async def fetch_user_profile(self, access_token: str) -> dict[str, Any] | None:
        """Validate a bearer token and return a normalized provider profile."""


class OAuth2AuthAdapter(AuthAdapter):
    """Configuration-driven adapter for the existing OAuth providers."""

    authorize_endpoint = ""
    token_endpoint = ""
    client_id = ""
    client_secret = ""
    redirect_uri = ""
    scope = ""
    use_scope = True
    pkce = False

    def enabled(self) -> bool:
        return bool(self.client_id and self.redirect_uri and self.authorize_endpoint and self.token_endpoint)

    def _authorize_params(self, state: str, code_verifier: str | None) -> dict[str, str]:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": state,
        }
        if self.scope and self.use_scope:
            params["scope"] = self.scope
        if self.pkce:
            if not code_verifier:
                raise ValueError("PKCE code verifier is required")
            digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
            params["code_challenge"] = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
            params["code_challenge_method"] = "S256"
        return params

    async def authorization_url(self, state: str, code_verifier: str | None = None) -> str:
        return f"{self.authorize_endpoint}?{urlencode(self._authorize_params(state, code_verifier))}"

    async def exchange_code(
        self, client: httpx.AsyncClient, code: str, code_verifier: str | None = None
    ) -> dict[str, Any]:
        body = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "code": code,
            "redirect_uri": self.redirect_uri,
        }
        if self.client_secret or self.provider != "keycloak":
            body["client_secret"] = self.client_secret
        if self.pkce:
            if not code_verifier:
                raise ValueError("PKCE code verifier is required")
            body["code_verifier"] = code_verifier
        response = await client.post(
            self.token_endpoint,
            data=body,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise ValueError("OAuth token response is not valid JSON") from exc
        if not isinstance(data, dict):
            raise TypeError("OAuth token response must be an object")
        return data


class GitCodeAuthAdapter(OAuth2AuthAdapter):
    provider = "gitcode"
    authorize_endpoint = settings.gitcode_oauth_authorize_url
    token_endpoint = settings.gitcode_oauth_token_url
    client_id = settings.gitcode_oauth_client_id
    client_secret = settings.gitcode_oauth_client_secret
    redirect_uri = settings.gitcode_oauth_redirect_uri
    scope = settings.gitcode_oauth_scope

    def enabled(self) -> bool:
        return settings.gitcode_oauth_enabled and super().enabled()

    async def fetch_user_profile(self, access_token: str) -> dict[str, Any] | None:
        return await fetch_gitcode_profile(access_token)


class GitHubAuthAdapter(OAuth2AuthAdapter):
    provider = "github"
    authorize_endpoint = settings.github_oauth_authorize_url
    token_endpoint = settings.github_oauth_token_url
    client_id = settings.github_oauth_client_id
    client_secret = settings.github_oauth_client_secret
    redirect_uri = settings.github_oauth_redirect_uri
    scope = settings.github_oauth_scope

    def enabled(self) -> bool:
        return settings.github_oauth_enabled and super().enabled()

    async def fetch_user_profile(self, access_token: str) -> dict[str, Any] | None:
        return await fetch_github_profile(access_token)


class AgentOSAuthAdapter(OAuth2AuthAdapter):
    provider = "agentos"
    authorize_endpoint = settings.agentos_oauth_authorize_url
    token_endpoint = settings.agentos_oauth_token_url
    client_id = settings.agentos_oauth_client_id
    client_secret = settings.agentos_oauth_client_secret
    redirect_uri = settings.agentos_oauth_redirect_uri
    scope = ""
    use_scope = False

    def enabled(self) -> bool:
        return (
            settings.agentos_oauth_enabled
            and bool(settings.agentos_auth_user_api_url)
            and super().enabled()
        )

    async def fetch_user_profile(self, access_token: str) -> dict[str, Any] | None:
        token = (access_token or "").strip()
        if not token or not settings.agentos_auth_user_api_url:
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    settings.agentos_auth_user_api_url,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
            if response.status_code != 200:
                return None
            data = response.json()
            return data if isinstance(data, dict) and data.get("id") else None
        except (httpx.HTTPError, ValueError):
            return None


class KeycloakAuthAdapter(OAuth2AuthAdapter):
    """OIDC adapter; provider endpoints are loaded from discovery metadata."""

    provider = "keycloak"
    pkce = True
    use_scope = True

    def __init__(self) -> None:
        self._discovery: dict[str, Any] | None = None

    def enabled(self) -> bool:
        return bool(settings.keycloak_oauth_enabled and settings.keycloak_issuer_url and settings.keycloak_client_id and settings.keycloak_redirect_uri)

    @property
    def client_id(self) -> str:  # type: ignore[override]
        return settings.keycloak_client_id

    @property
    def client_secret(self) -> str:  # type: ignore[override]
        return settings.keycloak_client_secret

    @property
    def redirect_uri(self) -> str:  # type: ignore[override]
        return settings.keycloak_redirect_uri

    @property
    def scope(self) -> str:  # type: ignore[override]
        scopes = settings.keycloak_oauth_scope.split()
        if "openid" not in scopes:
            scopes.insert(0, "openid")
        return " ".join(scopes)

    async def _metadata(self, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
        if self._discovery is not None:
            return self._discovery
        issuer = settings.keycloak_issuer_url.rstrip("/")
        issuer_parts = urlsplit(issuer)
        if issuer_parts.scheme != "https" or not issuer_parts.hostname:
            raise ValueError("Keycloak issuer URL must use HTTPS")
        own_client = client is None
        client = client or httpx.AsyncClient(timeout=15.0)
        try:
            response = await client.get(f"{issuer}/.well-known/openid-configuration")
            response.raise_for_status()
            metadata = response.json()
        finally:
            if own_client:
                await client.aclose()
        discovered_issuer = metadata.get("issuer") if isinstance(metadata, dict) else None
        if not isinstance(discovered_issuer, str) or discovered_issuer.rstrip("/") != issuer:
            raise ValueError("Keycloak OIDC discovery issuer mismatch")
        for endpoint_name in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
            endpoint = metadata.get(endpoint_name)
            endpoint_parts = urlsplit(endpoint) if isinstance(endpoint, str) else None
            if (
                endpoint_parts is None
                or endpoint_parts.scheme != "https"
                or endpoint_parts.hostname != issuer_parts.hostname
            ):
                raise ValueError("Keycloak OIDC discovery is missing secure required endpoints")
        self._discovery = metadata
        return metadata

    async def authorization_url(self, state: str, code_verifier: str | None = None) -> str:
        metadata = await self._metadata()
        # Share the same S256 parameter generation as the base class.
        self.authorize_endpoint = str(metadata["authorization_endpoint"])
        return await super().authorization_url(state, code_verifier)

    async def exchange_code(
        self, client: httpx.AsyncClient, code: str, code_verifier: str | None = None
    ) -> dict[str, Any]:
        metadata = await self._metadata(client)
        self.token_endpoint = str(metadata["token_endpoint"])
        return await super().exchange_code(client, code, code_verifier)

    async def fetch_user_profile(self, access_token: str) -> dict[str, Any] | None:
        token = (access_token or "").strip()
        if not token:
            return None
        try:
            metadata = await self._metadata()
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    str(metadata["userinfo_endpoint"]),
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
            if response.status_code != 200:
                return None
            data = response.json()
            if not isinstance(data, dict) or not data.get("sub"):
                return None
            subject = str(data["sub"])
            login = str(data.get("preferred_username") or data.get("email") or subject)
            return {
                **data,
                "id": subject,
                "login": login,
                "username": login,
                "name": data.get("name") or login,
                "avatar_url": data.get("picture"),
            }
        except (httpx.HTTPError, ValueError):
            return None


_ADAPTERS: dict[str, AuthAdapter] = {
    adapter.provider: adapter
    for adapter in (GitCodeAuthAdapter(), GitHubAuthAdapter(), AgentOSAuthAdapter(), KeycloakAuthAdapter())
}


def get_auth_adapter(provider: str) -> AuthAdapter | None:
    """Return the registered adapter for a normalized provider name."""
    return _ADAPTERS.get((provider or "").strip().lower())


def create_pkce_verifier() -> str:
    """Create a high-entropy RFC 7636 verifier for OIDC authorization code flow."""
    return secrets.token_urlsafe(64)
