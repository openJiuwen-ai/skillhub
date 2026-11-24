"""Independent login routes for configured extensions; built-in routes are untouched."""

import hashlib
import json
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from plugins_market.core.config import settings
from plugins_market.core.errors import http_error_payload
from plugins_market.core.logging import get_logger
from plugins_market.core.oauth_session_store import get_oauth_str_store
from plugins_market.routers.oauth_provider import (
    _append_query_params,
    _validate_loopback_redirect,
)
from plugins_market.schemas.common import ResponseModel
from pydantic import BaseModel, Field

from .contracts import IdentityError, validate_profile
from .registry import get_runtime_registry

router = APIRouter(prefix="/auth/extensions", tags=["identity-extensions"])
logger = get_logger(__name__)


class SessionBody(BaseModel):
    session: str = Field(min_length=8, max_length=256)


def _provider(provider_id: str):
    provider = get_runtime_registry().get(provider_id)
    if provider is None:
        raise HTTPException(
            404,
            detail=http_error_payload(
                status_code=404,
                error="oauth_disabled",
                message="Unknown or disabled identity provider",
            ),
        )
    return provider


def _key(kind: str, provider: str, value: str) -> str:
    return f"market_identity_extension:{kind}:{provider}:{value}"


def _cookie_name(state: str) -> str:
    return "skillhub_identity_" + hashlib.sha256(state.encode()).hexdigest()[:24]


def _redirect(params: dict[str, str], target: str | None = None) -> RedirectResponse:
    destination = target or settings.oauth_frontend_origin.rstrip("/") + "/login"
    return RedirectResponse(
        _append_query_params(destination, params),
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/{provider_id}/start")
async def start(provider_id: str, request: Request, redirect_to: str | None = None):
    provider = _provider(provider_id)
    validated_redirect = _validate_loopback_redirect(redirect_to)
    if redirect_to and not validated_redirect:
        raise HTTPException(
            400,
            detail=http_error_payload(
                status_code=400,
                error="oauth_invalid_redirect_to",
                message="Invalid loopback redirect_to",
            ),
        )
    state, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    try:
        authorization = await provider.adapter.start_authorization(state=state)
    except IdentityError:
        logger.warning("identity extension start failed", provider=provider_id)
        raise HTTPException(
            502,
            detail=http_error_payload(
                status_code=502,
                error="oauth_request_failed",
                message="Cannot connect to identity service",
            ),
        ) from None
    get_oauth_str_store().set_ex(
        _key("state", provider_id, state),
        json.dumps(
            {
                "binding": hashlib.sha256(binding.encode()).hexdigest(),
                "redirect_to": validated_redirect,
                "transaction": authorization.transaction,
            }
        ),
        600,
    )
    response = RedirectResponse(
        authorization.url, 302, headers={"Cache-Control": "no-store"}
    )
    response.set_cookie(
        _cookie_name(state),
        binding,
        max_age=600,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path=f"/api/v1/auth/extensions/{provider_id}/callback",
    )
    return response


@router.get("/{provider_id}/callback")
async def callback(provider_id: str, request: Request):
    provider = _provider(provider_id)
    state = request.query_params.get("state", "")
    if not 8 <= len(state) <= 256:
        return _redirect({"oauth_error": "Invalid or expired login state"})
    store = get_oauth_str_store()
    state_key = _key("state", provider_id, state)
    raw = store.get(state_key)
    try:
        record = json.loads(raw) if raw else None
        binding = request.cookies.get(_cookie_name(state), "")
        valid = (
            isinstance(record, dict)
            and isinstance(record.get("binding"), str)
            and binding
            and secrets.compare_digest(
                record["binding"], hashlib.sha256(binding.encode()).hexdigest()
            )
        )
    except (ValueError, TypeError):
        valid = False
    if not valid or not store.get_del(state_key):
        return _redirect({"oauth_error": "Invalid or expired login state"})
    target = _validate_loopback_redirect(record.get("redirect_to"))
    try:
        if request.query_params.get("error"):
            raise IdentityError("Authorization rejected")
        result = await provider.adapter.complete_authorization(
            callback_params=dict(request.query_params),
            transaction=record.get("transaction", {}),
        )
        user = validate_profile(result.user, system_user=settings.system_admin_user)
        if not isinstance(result.access_token, str) or not result.access_token.strip():
            raise IdentityError("Missing access token")
        if (
            not isinstance(result.token_type, str)
            or result.token_type.lower() != "bearer"
        ):
            raise IdentityError("Unsupported access token type")
        pending = secrets.token_urlsafe(24)
        store.set_ex(
            _key("pending", provider_id, pending),
            json.dumps(
                {
                    "provider": provider_id,
                    "access_token": result.access_token,
                    "token_type": result.token_type,
                    "user": user,
                }
            ),
            120,
        )
        response = _redirect(
            {"oauth_session": pending, "oauth_provider": provider_id}, target
        )
    except IdentityError:
        logger.warning("identity extension callback failed", provider=provider_id)
        response = _redirect(
            {"oauth_error": "Identity authentication failed; please sign in again"},
            target,
        )
    response.delete_cookie(
        _cookie_name(state), path=f"/api/v1/auth/extensions/{provider_id}/callback"
    )
    return response


@router.post("/{provider_id}/session", response_model=ResponseModel[dict])
async def exchange_session(provider_id: str, body: SessionBody, response: Response):
    _provider(provider_id)
    raw = get_oauth_str_store().get_del(_key("pending", provider_id, body.session))
    try:
        data = json.loads(raw) if raw else None
        if not isinstance(data, dict) or data.get("provider") != provider_id:
            raise ValueError("Invalid session")
    except ValueError:
        raise HTTPException(
            400,
            detail=http_error_payload(
                status_code=400,
                error="oauth_session_expired",
                message="Session expired or invalid",
            ),
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return ResponseModel(code=200, message="ok", data=data)
