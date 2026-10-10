# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""鉴权：Authorization Bearer 与 X-System-Token 二选一，不能同时传也不能都不传。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, Request, status

from common.security.security_utils import SecurityUtils
from plugins_market.core.config import settings
from plugins_market.core.errors import auth_error_payload, http_error_payload
from plugins_market.core.logging import get_logger
from plugins_market.core.context import set_user_id, set_user_name
from plugins_market.core.oauth_user_profile import fetch_oauth_user_profile
from plugins_market.core.review_admins import is_market_moderation_username
from plugins_market.core.viewer_context import ViewerContext

logger = get_logger(__name__)


def _http_exception(
    status_code: int,
    message: str,
    *,
    error: str | None = None,
    auth_error_code: str | None = None,
) -> HTTPException:
    payload = (
        auth_error_payload(
            status_code=status_code,
            message=message,
            error=error or "auth_error",
            error_code=auth_error_code,
        )
        if status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        else http_error_payload(
            status_code=status_code,
            message=message,
            error=error,
        )
    )
    return HTTPException(status_code=status_code, detail=payload)


def _is_market_moderation_admin(*, is_system_admin: bool, acting_user_name: Optional[str]) -> bool:
    if is_system_admin:
        return True
    return is_market_moderation_username(acting_user_name)


@dataclass(frozen=True, slots=True)
class AuthContext:
    is_admin: bool
    acting_user_id: str
    acting_user_name: Optional[str] = None
    is_market_moderation_admin: bool = False
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


def _has_bearer(authorization: Optional[str]) -> bool:
    return bool(
        authorization
        and authorization.strip().lower().startswith("bearer ")
        and authorization[7:].strip()
    )


def _has_system_token(x_system_token: Optional[str]) -> bool:
    return bool(x_system_token is not None and x_system_token.strip())


def _resolved_system_admin_token() -> str:
    return SecurityUtils.get_decrypt_secret("SYSTEM_ADMIN_TOKEN", default="") or ""


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not _has_bearer(authorization):
        return None
    return authorization[7:].strip()


def normalize_oauth_provider_header(raw: Optional[str]) -> str:
    """请求头 X-OAuth-Provider：缺省 gitcode；非法值抛 400。"""
    if raw is None or not str(raw).strip():
        return "gitcode"
    p = str(raw).strip().lower()
    if p in ("gitcode", "github", "agentos"):
        return p
    raise _http_exception(
        status.HTTP_400_BAD_REQUEST,
        "Invalid X-OAuth-Provider; allowed: gitcode, github, agentos",
        error="invalid_oauth_provider",
    )


async def get_oauth_user_id_and_login(token: str, provider: str) -> tuple[str, str]:
    """返回 (OAuth 厂商用户 id, 展示用发布者名)。token 无效则抛 401。"""
    profile = await fetch_oauth_user_profile(provider, token)
    if not profile:
        raise _http_exception(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired access token",
            error="auth_token_invalid",
            auth_error_code="SKILLHUB_AUTH_TOKEN_INVALID",
        )
    gid = str(profile["id"]).strip()
    if not gid:
        raise _http_exception(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired access token",
            error="auth_token_invalid",
            auth_error_code="SKILLHUB_AUTH_TOKEN_INVALID",
        )
    login = (profile.get("login") or profile.get("username") or "").strip() or gid
    return gid, login


async def get_oauth_user_id(token: str, provider: str) -> str:
    """返回 OAuth 厂商用户 id（字符串）。token 无效则抛 401。"""
    uid, _ = await get_oauth_user_id_and_login(token, provider)
    return uid


async def require_auth(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_system_token: Optional[str] = Header(None, alias="X-System-Token"),
    x_oauth_provider: Optional[str] = Header(None, alias="X-OAuth-Provider"),
) -> AuthContext:
    """
    接口鉴权：Authorization 与 X-System-Token 二选一。
    - Bearer：按 ``X-OAuth-Provider``（缺省 gitcode）调用对应厂商用户接口校验，带 ``is_market_moderation_admin``。
    - X-System-Token：与 `SYSTEM_ADMIN_TOKEN` 比对成功后，`is_admin=True` 且 ``is_market_moderation_admin=True``。
    """
    has_bearer = _has_bearer(authorization)
    has_system = _has_system_token(x_system_token)

    if has_bearer and has_system:
        raise _http_exception(
            status.HTTP_401_UNAUTHORIZED,
            "Cannot use both Authorization and X-System-Token; provide exactly one",
            error="auth_headers_conflict",
            auth_error_code="SKILLHUB_AUTH_HEADERS_CONFLICT",
        )
    if not has_bearer and not has_system:
        raise _http_exception(
            status.HTTP_401_UNAUTHORIZED,
            "Missing authorization: provide Authorization: Bearer <token> or X-System-Token (exactly one)",
            error="auth_header_missing",
            auth_error_code="SKILLHUB_AUTH_HEADER_MISSING",
        )

    client_ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")

    if has_system:
        system_admin_token = _resolved_system_admin_token()
        if system_admin_token and x_system_token.strip() == system_admin_token:
            set_user_id(settings.system_admin_user)
            set_user_name(settings.system_admin_user)
            return AuthContext(
                is_admin=True,
                acting_user_id=settings.system_admin_user,
                acting_user_name=settings.system_admin_user,
                is_market_moderation_admin=True,
                ip_address=client_ip,
                user_agent=ua,
            )
        raise _http_exception(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid X-System-Token",
            error="system_token_invalid",
            auth_error_code="SKILLHUB_SYSTEM_TOKEN_INVALID",
        )

    token = _extract_bearer_token(authorization) or ""
    oauth_provider = normalize_oauth_provider_header(x_oauth_provider)
    oauth_user_id, oauth_user_name = await get_oauth_user_id_and_login(token, oauth_provider)
    set_user_id(oauth_user_id)
    set_user_name(oauth_user_name)
    return AuthContext(
        is_admin=False,
        acting_user_id=oauth_user_id,
        acting_user_name=oauth_user_name,
        is_market_moderation_admin=_is_market_moderation_admin(
            is_system_admin=False,
            acting_user_name=oauth_user_name,
        ),
        ip_address=client_ip,
        user_agent=ua,
    )


async def resolve_viewer_context(
    _request: Request,
    authorization: Optional[str] = Header(None),
    x_system_token: Optional[str] = Header(None, alias="X-System-Token"),
    x_oauth_provider: Optional[str] = Header(None, alias="X-OAuth-Provider"),
) -> ViewerContext:
    """可选鉴权：无请求头、无效 token、或同时传两种凭证时视为匿名；否则解析用户并写入 context user_id。"""
    has_bearer = _has_bearer(authorization)
    has_system = _has_system_token(x_system_token)
    if has_bearer and has_system:
        return ViewerContext(user_id=None, user_login=None, is_system_admin=False)
    if has_system:
        system_admin_token = _resolved_system_admin_token()
        if system_admin_token and x_system_token.strip() == system_admin_token:
            u = settings.system_admin_user
            set_user_id(u)
            set_user_name(u)
            return ViewerContext(user_id=u, user_login=u, is_system_admin=True)
        return ViewerContext(user_id=None, user_login=None, is_system_admin=False)
    if has_bearer:
        token = _extract_bearer_token(authorization) or ""
        if not token:
            return ViewerContext(user_id=None, user_login=None, is_system_admin=False)
        try:
            prov = normalize_oauth_provider_header(x_oauth_provider)
        except HTTPException:
            return ViewerContext(user_id=None, user_login=None, is_system_admin=False)
        try:
            uid, login = await get_oauth_user_id_and_login(token, prov)
            set_user_id(uid)
            set_user_name(login)
            return ViewerContext(user_id=uid, user_login=login, is_system_admin=False)
        except HTTPException:
            return ViewerContext(user_id=None, user_login=None, is_system_admin=False)
    return ViewerContext(user_id=None, user_login=None, is_system_admin=False)


async def optional_auth(
    authorization: Optional[str] = Header(None),
    x_system_token: Optional[str] = Header(None, alias="X-System-Token"),
    x_oauth_provider: Optional[str] = Header(None, alias="X-OAuth-Provider"),
) -> None:
    if _has_system_token(x_system_token):
        system_admin_token = _resolved_system_admin_token()
        if system_admin_token and x_system_token.strip() == system_admin_token:
            set_user_id(settings.system_admin_user)
            set_user_name(settings.system_admin_user)
        return

    if _has_bearer(authorization):
        token = _extract_bearer_token(authorization) or ""
        if token:
            try:
                prov = normalize_oauth_provider_header(x_oauth_provider)
                oauth_user_id = await get_oauth_user_id(token, prov)
                set_user_id(oauth_user_id)
            except HTTPException:
                pass
