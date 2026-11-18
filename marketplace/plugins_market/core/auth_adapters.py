# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider adapters used by OAuth login and bearer-token authentication.

Add a new IAM by implementing ``AuthAdapter`` and registering it in
``get_auth_adapter``. Provider-specific endpoints and profile mapping stay out
of the auth dependencies and OAuth routes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from plugins_market.core.config import settings


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

    async def logout_config(self) -> dict[str, str] | None:
        """Return RP-initiated logout details when the provider supports them."""
        return None


class KeycloakAuthAdapter(AuthAdapter):
    """OIDC authorization code flow with PKCE and discovery."""

    provider = "keycloak"

    def __init__(self) -> None:
        self._discovery: dict[str, Any] | None = None

    def enabled(self) -> bool:
        return settings.keycloak_login_ready

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
        if not code_verifier:
            raise ValueError("PKCE code verifier is required")
        metadata = await self._metadata()
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        scopes = settings.keycloak_oauth_scope.split()
        if "openid" not in scopes:
            scopes.insert(0, "openid")
        params = {
            "client_id": settings.keycloak_client_id,
            "redirect_uri": settings.keycloak_redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii"),
            "code_challenge_method": "S256",
        }
        return f"{metadata['authorization_endpoint']}?{urlencode(params)}"

    async def exchange_code(
        self, client: httpx.AsyncClient, code: str, code_verifier: str | None = None
    ) -> dict[str, Any]:
        if not code_verifier:
            raise ValueError("PKCE code verifier is required")
        metadata = await self._metadata(client)
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": settings.keycloak_client_id,
            "redirect_uri": settings.keycloak_redirect_uri,
        }
        if settings.keycloak_client_secret:
            body["client_secret"] = settings.keycloak_client_secret
        response = await client.post(metadata["token_endpoint"], data=body, headers={"Accept": "application/json"})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("OAuth token response must be an object")
        return data

    async def fetch_user_profile(self, access_token: str) -> dict[str, Any] | None:
        if not self.enabled():
            return None
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
            subject = data.get("sub") if isinstance(data, dict) else None
            if not isinstance(subject, str) or not subject.strip():
                return None
            # UserInfo has authenticated this exact bearer token. Bind its claims to
            # this client and the returned user before accepting the realm identity.
            header, payload, signature = token.split(".")
            if not (header and payload and signature):
                return None
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if not isinstance(claims, dict):
                return None
            if (
                claims.get("iss") != metadata["issuer"]
                or claims.get("azp") != settings.keycloak_client_id
                or claims.get("sub") != subject
            ):
                return None
            identity = json.dumps([metadata["issuer"], subject], ensure_ascii=False).encode()
            digest = base64.urlsafe_b64encode(hashlib.sha256(identity).digest()).rstrip(b"=").decode("ascii")
            login = str(data.get("preferred_username") or data.get("email") or subject)
            return {
                **data,
                "id": f"kc:{digest}",
                "login": login,
                "username": login,
                "name": data.get("name") or login,
                "avatar_url": data.get("picture"),
            }
        except (httpx.HTTPError, ValueError):
            return None

    async def logout_config(self) -> dict[str, str] | None:
        """Build the browser logout handoff from trusted OIDC discovery metadata."""
        metadata = await self._metadata()
        endpoint = metadata.get("end_session_endpoint")
        if not isinstance(endpoint, str):
            return None
        parts = urlsplit(endpoint)
        issuer_host = urlsplit(settings.keycloak_issuer_url).hostname
        if parts.scheme != "https" or not parts.hostname or parts.hostname != issuer_host:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        frontend_login = f"{settings.oauth_frontend_origin.rstrip('/')}/login?logged_out=1"
        return {
            "end_session_endpoint": endpoint,
            "client_id": settings.keycloak_client_id,
            "post_logout_redirect_uri": frontend_login,
        }


_ADAPTERS: dict[str, AuthAdapter] = {"keycloak": KeycloakAuthAdapter()}


def get_auth_adapter(provider: str) -> AuthAdapter | None:
    """Return the registered adapter for a normalized provider name."""
    return _ADAPTERS.get((provider or "").strip().lower())


def create_pkce_verifier() -> str:
    """Create a high-entropy RFC 7636 verifier for OIDC authorization code flow."""
    return secrets.token_urlsafe(64)
