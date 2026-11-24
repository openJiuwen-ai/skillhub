"""Keycloak authorization-code + PKCE, Discovery and userinfo authentication."""

import asyncio
import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

import httpx
import jwt
from common.security.security_utils import SecurityUtils
from plugins_market.core.config import settings

from ..contracts import (
    AuthorizationStart,
    IdentityError,
    LoginResult,
    validate_profile,
)
from .config import KeycloakConfig


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class KeycloakAdapter:
    def __init__(self, provider_id: str, config: KeycloakConfig):
        self.provider_id = provider_id
        self.config = config
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = asyncio.Lock()
        # Check secret availability at startup; never include its value in errors.
        self._secret()

    def _secret(self) -> str:
        secret = SecurityUtils.get_decrypt_secret(
            self.config.client_secret_env, default=""
        )
        if not secret:
            raise ValueError(
                f"Missing OIDC secret environment variable: {self.config.client_secret_env}"
            )
        return secret

    async def _cached_json(self, key: str, url: str, *, force: bool = False) -> dict:
        async with self._lock:
            cached = self._cache.get(key)
            if not force and cached and cached[0] > time.monotonic():
                return cached[1]
            self.config.validate_url(url)
            async with httpx.AsyncClient(
                timeout=self.config.timeout_seconds, follow_redirects=False
            ) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
                response.raise_for_status()
                data = response.json()
            if not isinstance(data, dict):
                raise IdentityError("Invalid OIDC server response")
            if key == "metadata":
                if data.get("issuer") != self.config.issuer:
                    raise IdentityError("OIDC issuer mismatch")
                for field in (
                    "authorization_endpoint",
                    "token_endpoint",
                    "userinfo_endpoint",
                    "jwks_uri",
                ):
                    if not isinstance(data.get(field), str):
                        raise IdentityError("Incomplete OIDC metadata")
                    self.config.validate_url(data[field])
            self._cache[key] = (
                time.monotonic() + self.config.metadata_ttl_seconds,
                data,
            )
            return data

    async def _metadata(self) -> dict:
        return await self._cached_json(
            "metadata",
            self.config.issuer.rstrip("/") + "/.well-known/openid-configuration",
        )

    async def start_authorization(self, *, state: str) -> AuthorizationStart:
        try:
            metadata = await self._metadata()
            verifier = secrets.token_urlsafe(48)
            nonce = secrets.token_urlsafe(32)
            params = {
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "response_type": "code",
                "scope": " ".join(self.config.scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": _base64url(
                    hashlib.sha256(verifier.encode("ascii")).digest()
                ),
                "code_challenge_method": "S256",
            }
            if self.config.prompt:
                params["prompt"] = self.config.prompt
            parts = urlsplit(metadata["authorization_endpoint"])
            query = dict(parse_qsl(parts.query))
            query.update(params)
            url = urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(query), "")
            )
            return AuthorizationStart(url, {"code_verifier": verifier, "nonce": nonce})
        except (httpx.HTTPError, ValueError) as exc:
            raise IdentityError("Cannot connect to identity service") from exc

    def _profile(self, data: dict[str, Any]) -> dict[str, Any]:
        def text(key: str) -> str:
            value = data.get(key)
            return value.strip() if isinstance(value, str) else ""

        subject = text("sub")
        login = text(self.config.login_claim) or subject
        return validate_profile(
            {
                "id": subject,
                "login": login,
                "name": text(self.config.name_claim) or login,
                "avatar_url": text(self.config.avatar_claim) or None,
            },
            system_user=settings.system_admin_user,
        )

    async def _userinfo(self, access_token: str) -> dict:
        metadata = await self._metadata()
        async with httpx.AsyncClient(
            timeout=self.config.timeout_seconds, follow_redirects=False
        ) as client:
            response = await client.get(
                metadata["userinfo_endpoint"],
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise IdentityError("Invalid OIDC userinfo")
        return self._profile(data)

    async def fetch_profile(self, access_token: str) -> dict | None:
        if not access_token.strip():
            return None
        try:
            return await self._userinfo(access_token)
        except (httpx.HTTPError, ValueError, IdentityError):
            return None

    async def _validate_id_token(
        self, tokens: dict, nonce: str, metadata: dict
    ) -> dict:
        encoded = tokens.get("id_token")
        if not isinstance(encoded, str) or not nonce:
            raise IdentityError("Missing OIDC authentication result")
        header = jwt.get_unverified_header(encoded)
        algorithm = header.get("alg")
        if algorithm not in self.config.algorithms:
            raise IdentityError("Unsupported OIDC signature algorithm")
        claims = None
        for force in (False, True):
            jwks = await self._cached_json("jwks", metadata["jwks_uri"], force=force)
            candidates = jwks.get("keys")
            if not isinstance(candidates, list):
                raise IdentityError("Invalid OIDC signing keys")
            keys = []
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                if candidate.get("kid") != header.get("kid"):
                    continue
                if candidate.get("use", "sig") != "sig":
                    continue
                if candidate.get("alg", algorithm) != algorithm:
                    continue
                keys.append(candidate)
            if len(keys) != 1:
                continue
            try:
                key = jwt.PyJWK.from_dict(keys[0], algorithm=algorithm)
                claims = jwt.decode(
                    encoded,
                    key=key.key,
                    algorithms=self.config.algorithms,
                    issuer=self.config.issuer,
                    audience=self.config.client_id,
                    leeway=30,
                    options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
                )
                break
            except jwt.InvalidSignatureError:
                if force:
                    raise
        if claims is None:
            raise IdentityError("OIDC signing key unavailable")
        if not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(
            claims["nonce"].encode("utf-8"), nonce.encode("utf-8")
        ):
            raise IdentityError("OIDC nonce mismatch")
        audience = claims["aud"]
        multiple_audiences = isinstance(audience, list) and len(audience) > 1
        requires_authorized_party = multiple_audiences or "azp" in claims
        if requires_authorized_party and claims.get("azp") != self.config.client_id:
            raise IdentityError("OIDC authorized party mismatch")
        if "at_hash" in claims:
            digest = hashlib.new(
                "sha" + algorithm[-3:], tokens["access_token"].encode("ascii")
            ).digest()
            if claims["at_hash"] != _base64url(digest[: len(digest) // 2]):
                raise IdentityError("OIDC access token hash mismatch")
        return claims

    async def complete_authorization(
        self, *, callback_params: dict[str, str], transaction: dict
    ) -> LoginResult:
        try:
            code = callback_params.get("code")
            verifier, nonce = transaction.get("code_verifier"), transaction.get("nonce")
            if not code or not isinstance(verifier, str) or not isinstance(nonce, str):
                raise IdentityError("Invalid OIDC transaction")
            metadata = await self._metadata()
            data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
                "code_verifier": verifier,
            }
            auth = None
            secret = self._secret()
            if self.config.token_endpoint_auth_method == "client_secret_basic":
                auth = httpx.BasicAuth(
                    quote_plus(self.config.client_id), quote_plus(secret)
                )
            else:
                data.update(client_id=self.config.client_id, client_secret=secret)
            async with httpx.AsyncClient(
                timeout=self.config.timeout_seconds, follow_redirects=False
            ) as client:
                response = await client.post(
                    metadata["token_endpoint"],
                    data=data,
                    auth=auth,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                tokens = response.json()
            if (
                not isinstance(tokens, dict)
                or not isinstance(tokens.get("access_token"), str)
                or tokens.get("error")
            ):
                raise IdentityError("Invalid OIDC token response")
            if str(tokens.get("token_type", "")).lower() != "bearer":
                raise IdentityError("Unsupported OIDC token type")
            claims = await self._validate_id_token(tokens, nonce, metadata)
            profile = await self._userinfo(tokens["access_token"])
            if profile["id"] != claims["sub"]:
                raise IdentityError("OIDC userinfo subject mismatch")
            return LoginResult(tokens["access_token"], profile)
        except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
            raise IdentityError("OIDC authentication failed") from exc
