# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared OAuth transaction keys and optional browser binding, independent of IdP protocols."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request, Response
from plugins_market.identity_extensions.contracts import IdentityAdapter, IdentityError

STATE_TTL_SECONDS = 600
PENDING_TTL_SECONDS = 120


@dataclass(frozen=True)
class OAuthExtensionChannel:
    value: str
    label: str
    adapter: IdentityAdapter


def oauth_state_key(provider_id: str, state: str) -> str:
    return f"market_oauth_state:{provider_id}:{state}"


def oauth_pending_key(provider_id: str, pending: str) -> str:
    return f"market_oauth_pending:{provider_id}:{pending}"


def browser_cookie_name(state: str) -> str:
    digest = hashlib.sha256(state.encode()).hexdigest()[:24]
    return f"skillhub_identity_{digest}"


def create_browser_binding(
    callback_uri: str | None, callback_path: str
) -> tuple[str, dict]:
    """Use the configured external callback path, including any reverse-proxy prefix."""
    if not isinstance(callback_uri, str) or not callback_uri:
        raise IdentityError("Identity adapter must return its configured callback_uri")
    parts = urlsplit(callback_uri)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise IdentityError("Invalid identity callback URI")
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise IdentityError("Invalid identity callback URI")
    cookie_path = parts.path.rstrip("/")
    if not cookie_path.endswith(callback_path):
        raise IdentityError("Identity callback URI must use the shared OAuth callback")
    binding = secrets.token_urlsafe(32)
    return binding, {
        "binding": hashlib.sha256(binding.encode()).hexdigest(),
        # Cover the canonical route after a trailing-slash redirect. Do not
        # normalize the adapter's redirect_uri used for code exchange.
        "cookie_path": cookie_path,
    }


def browser_binding_valid(request: Request, state: str, record: dict | None) -> bool:
    if not 8 <= len(state) <= 256 or not isinstance(record, dict):
        return False
    binding_hash = record.get("binding")
    if not isinstance(binding_hash, str) or not isinstance(
        record.get("cookie_path"), str
    ):
        return False
    binding = request.cookies.get(browser_cookie_name(state), "")
    if not binding:
        return False
    return secrets.compare_digest(
        binding_hash, hashlib.sha256(binding.encode()).hexdigest()
    )


def set_browser_binding(
    response: Response, request: Request, state: str, binding: str, record: dict
) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        browser_cookie_name(state),
        binding,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path=record.get("cookie_path"),
    )


def clear_browser_binding(
    response: Response, state: str, record: dict | None
) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    if isinstance(record, dict) and isinstance(record.get("cookie_path"), str):
        response.delete_cookie(
            browser_cookie_name(state), path=record.get("cookie_path")
        )
    return response
