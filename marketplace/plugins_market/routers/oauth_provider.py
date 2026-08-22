# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
OAuth2 授权码模式：统一路由 /oauth/{gitcode|github}/...，不落库用户表。

GitCode 文档：https://docs.gitcode.com/docs/apis/
GitHub：https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
"""

from __future__ import annotations

import json
import secrets
from enum import Enum
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Header, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from plugins_market.core.auth import normalize_oauth_provider_header
from plugins_market.core.config import settings
from plugins_market.core.errors import BusinessError
from plugins_market.core.oauth_session_store import get_oauth_str_store
from plugins_market.core.oauth_user_profile import fetch_oauth_user_profile
from plugins_market.core.operation_log import (
    bind_operation_actor,
    bind_operation_resource,
    complete_operation_result,
    operation_context,
    operation_log_fields,
    safe_error_summary,
)
from plugins_market.core.review_admins import is_market_moderation_username
from plugins_market.schemas.common import ResponseModel
from plugins_market.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


class OAuthProvider(str, Enum):
    gitcode = "gitcode"
    github = "github"
    agentos = "agentos"


class OAuthSessionBody(BaseModel):
    """一次性 pending id（由回调重定向的 oauth_session query 给出），勿用 GET 传参以免进访问日志。"""

    session: str = Field(..., min_length=8, max_length=256)


def _state_key(provider: OAuthProvider, state: str) -> str:
    return f"market_oauth_state:{provider.value}:{state}"


def _pending_key(provider: OAuthProvider, pending_id: str) -> str:
    return f"market_oauth_pending:{provider.value}:{pending_id}"


def _frontend_login_url() -> str:
    base = settings.oauth_frontend_origin.rstrip("/")
    return f"{base}/login"


def _redirect_error(
    message: str,
    *,
    status_code: int | None = None,
    error_code: str | None = None,
    error_class: str | None = None,
    error_name: str | None = None,
) -> RedirectResponse:
    params = {"oauth_error": message}
    if status_code is not None:
        params["oauth_status"] = str(status_code)
    if error_code:
        params["oauth_error_code"] = error_code
    if error_class:
        params["oauth_error_class"] = error_class
    if error_name:
        params["oauth_error_name"] = error_name
    return RedirectResponse(
        url=f"{_frontend_login_url()}?{urlencode(params, quote_via=quote)}",
        status_code=302,
    )


def _provider_label(provider: OAuthProvider) -> str:
    if provider == OAuthProvider.agentos:
        return "AgentOS"
    if provider == OAuthProvider.github:
        return "GitHub"
    return "GitCode"


def _safe_oauth_meta(provider: OAuthProvider, *, upstream_status: int | None = None) -> dict[str, str | int]:
    meta: dict[str, str | int] = {"provider": provider.value}
    if upstream_status is not None:
        meta["upstream_status"] = upstream_status
    return meta


def _oauth_log_fields(
    provider: OAuthProvider,
    *,
    stage: str,
    result: str,
    error_code: str | None = None,
    error_class: str | None = None,
    upstream_status: int | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    return operation_log_fields(
        stage=stage,
        result=result,
        error_code=error_code,
        error_class=error_class,
        error_message=error_message,
        provider=provider.value,
        upstream_status=upstream_status,
    )


def _assert_oauth_ready(provider: OAuthProvider) -> None:
    label = _provider_label(provider)
    if provider == OAuthProvider.agentos:
        if not settings.agentos_oauth_enabled:
            raise BusinessError(
                code=status.HTTP_404_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
                error="oauth_disabled",
                message="AgentOS OAuth 未启用",
                error_code="SKILLHUB_OAUTH_DISABLED",
                error_class="auth",
            )
        endpoint_urls_set = all([
            settings.agentos_oauth_authorize_url,
            settings.agentos_oauth_token_url,
            settings.agentos_auth_user_api_url,
        ])
        if not settings.agentos_oauth_client_id \
        or not settings.agentos_oauth_redirect_uri or not endpoint_urls_set:
            raise BusinessError(
                code=status.HTTP_503_SERVICE_UNAVAILABLE,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                error="oauth_not_configured",
                message="AgentOS OAuth 未正确配置",
                error_code="SKILLHUB_OAUTH_NOT_CONFIGURED",
                error_class="upstream",
            )
        return
    if provider == OAuthProvider.gitcode:
        if not settings.gitcode_oauth_enabled:
            raise BusinessError(
                code=status.HTTP_404_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
                error="oauth_disabled",
                message=f"{label} OAuth 未启用",
                error_code="SKILLHUB_OAUTH_DISABLED",
                error_class="auth",
            )
        if not settings.gitcode_oauth_client_id or not settings.gitcode_oauth_redirect_uri:
            raise BusinessError(
                code=status.HTTP_503_SERVICE_UNAVAILABLE,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                error="oauth_not_configured",
                message=f"{label} OAuth 未正确配置",
                error_code="SKILLHUB_OAUTH_NOT_CONFIGURED",
                error_class="upstream",
            )
        return

    if not settings.github_oauth_enabled:
        raise BusinessError(
            code=status.HTTP_404_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            error="oauth_disabled",
            message=f"{label} OAuth 未启用",
            error_code="SKILLHUB_OAUTH_DISABLED",
            error_class="auth",
        )
    if not settings.github_oauth_client_id or not settings.github_oauth_redirect_uri:
        raise BusinessError(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error="oauth_not_configured",
            message=f"{label} OAuth 未正确配置",
            error_code="SKILLHUB_OAUTH_NOT_CONFIGURED",
            error_class="upstream",
        )


def _redirect_from_publish_error(exc: BusinessError, *, provider: OAuthProvider | None = None) -> RedirectResponse:
    if provider is not None:
        logger.warning(
            "oauth callback redirect",
            **complete_operation_result(
                result="failure",
                error_code=exc.detail.get("error_code"),
                error_class=exc.detail.get("error_class"),
                error_message=safe_error_summary(
                    error_code=exc.detail.get("error_code"),
                    error_class=exc.detail.get("error_class"),
                    fallback=exc.detail.get("message") or "oauth redirect failed",
                ),
                provider=provider.value,
                http_status=exc.status_code,
            ),
        )
    return _redirect_error(
        exc.detail.get("message") or "登录处理失败",
        status_code=exc.status_code,
        error_code=exc.detail.get("error_code"),
        error_class=exc.detail.get("error_class"),
        error_name=exc.detail.get("error"),
    )


def _oauth_api_error(
    *,
    status_code: int,
    message: str,
    error: str,
    error_code: str,
    error_class: str | None = None,
) -> BusinessError:
    return BusinessError(
        code=status_code,
        status_code=status_code,
        error=error,
        message=message,
        error_code=error_code,
        error_class=error_class,
    )


def _oauth_redirect_error(
    message: str,
    error_code: str,
    error_class: str = "auth",
    *,
    provider: OAuthProvider | None = None,
    status_code: int = 400,
) -> RedirectResponse:
    exc = BusinessError(
        code=status_code,
        status_code=status_code,
        error="oauth_redirect_error",
        message=message,
        error_code=error_code,
        error_class=error_class,
    )
    return _redirect_from_publish_error(exc, provider=provider)


async def _exchange_code_for_token_json(client: httpx.AsyncClient, provider: OAuthProvider, code: str) -> dict:
    if provider == OAuthProvider.agentos:
        logger.info(
            "oauth token exchange request",
            url=settings.agentos_oauth_token_url,
            client_id=settings.agentos_oauth_client_id,
            redirect_uri=settings.agentos_oauth_redirect_uri,
            code_len=len(code),
        )
        token_res = await client.post(
            settings.agentos_oauth_token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.agentos_oauth_client_id,
                "client_secret": settings.agentos_oauth_client_secret,
                "redirect_uri": settings.agentos_oauth_redirect_uri,
            },
        )
        # 注意：响应头可能含 Set-Cookie 等敏感信息，响应正文可能含 access_token 等敏感字段，均不打印
        logger.info(
            "oauth token exchange response",
            status=token_res.status_code,
        )
    elif provider == OAuthProvider.gitcode:
        token_res = await client.post(
            settings.gitcode_oauth_token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.gitcode_oauth_client_id,
                "client_secret": settings.gitcode_oauth_client_secret,
                "redirect_uri": settings.gitcode_oauth_redirect_uri,
            },
        )
    else:
        token_res = await client.post(
            settings.github_oauth_token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.github_oauth_client_id,
                "client_secret": settings.github_oauth_client_secret,
                "redirect_uri": settings.github_oauth_redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
    if token_res.status_code != 200:
        logger.warning(
            "oauth token exchange failed",
            **_oauth_log_fields(
                provider,
                stage="token_exchange",
                result="failure",
                error_code="SKILLHUB_OAUTH_TOKEN_EXCHANGE_FAILED",
                error_class="upstream",
                upstream_status=token_res.status_code,
            ),
        )
        raise BusinessError(
            code=502,
            status_code=502,
            error="oauth_token_exchange_failed",
            message="换取访问令牌失败，请稍后重试",
            error_code="SKILLHUB_OAUTH_TOKEN_EXCHANGE_FAILED",
            error_class="upstream",
            meta=_safe_oauth_meta(provider, upstream_status=token_res.status_code),
        )
    return token_res.json()


@router.get("/oauth/{provider}/start")
async def oauth_start(provider: OAuthProvider):
    """浏览器访问：重定向到对应厂商授权页。"""
    with operation_context(operation_type="oauth_start"):
        bind_operation_actor(actor_type="anonymous")
        bind_operation_resource(resource_type="oauth_provider", resource_id=provider.value)
        _assert_oauth_ready(provider)
        state = secrets.token_urlsafe(32)
        store = get_oauth_str_store()
        store.set_ex(_state_key(provider, state), "1", 600)

        if provider == OAuthProvider.agentos:
            params = {
                "client_id": settings.agentos_oauth_client_id,
                "redirect_uri": settings.agentos_oauth_redirect_uri,
                "response_type": "code",
                "state": state,
            }
            authorize_url = settings.agentos_oauth_authorize_url
        elif provider == OAuthProvider.gitcode:
            params = {
                "client_id": settings.gitcode_oauth_client_id,
                "redirect_uri": settings.gitcode_oauth_redirect_uri,
                "response_type": "code",
                "scope": settings.gitcode_oauth_scope,
                "state": state,
            }
            authorize_url = settings.gitcode_oauth_authorize_url
        else:
            params = {
                "client_id": settings.github_oauth_client_id,
                "redirect_uri": settings.github_oauth_redirect_uri,
                "response_type": "code",
                "scope": settings.github_oauth_scope,
                "state": state,
            }
            authorize_url = settings.github_oauth_authorize_url

        logger.info("oauthstart", **complete_operation_result(result="success", provider=provider.value))
        url = f"{authorize_url}?{urlencode(params)}"
        return RedirectResponse(url=url, status_code=302)


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: OAuthProvider,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """用 code 换 access_token，拉用户信息，写入一次性 oauth_session 后重定向前端 /login。"""
    with operation_context(operation_type="oauth_callback"):
        bind_operation_actor(actor_type="anonymous")
        bind_operation_resource(resource_type="oauth_provider", resource_id=provider.value)
        label = _provider_label(provider)
        try:
            _assert_oauth_ready(provider)
        except BusinessError as exc:
            return _redirect_from_publish_error(exc, provider=provider)

        if error:
            msg = error_description or error or "授权已取消"
            return _oauth_redirect_error(msg, "SKILLHUB_OAUTH_AUTHORIZATION_REJECTED", provider=provider)

        if not code or not state:
            return _oauth_redirect_error(
                "缺少授权参数",
                "SKILLHUB_OAUTH_MISSING_CODE_OR_STATE",
                "validation",
                provider=provider,
            )

        store = get_oauth_str_store()
        state_key = _state_key(provider, state)
        if not store.get(state_key):
            return _oauth_redirect_error(
                "状态无效或已过期，请重新登录",
                "SKILLHUB_OAUTH_STATE_INVALID",
                provider=provider,
            )
        store.delete(state_key)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                token_json = await _exchange_code_for_token_json(client, provider, code)
                access_token = token_json.get("access_token")
                if not access_token:
                    return _oauth_redirect_error(
                        f"{label} 未返回 access_token",
                        "SKILLHUB_OAUTH_ACCESS_TOKEN_MISSING",
                        "upstream",
                        provider=provider,
                    )

                u = await fetch_oauth_user_profile(provider.value, access_token)
                if not u:
                    logger.warning(
                        "oauth profile fetch failed",
                        **_oauth_log_fields(
                            provider,
                            stage="profile_fetch",
                            result="failure",
                            error_code="SKILLHUB_OAUTH_PROFILE_FETCH_FAILED",
                            error_class="upstream",
                        ),
                    )
                    return _oauth_redirect_error(
                        f"获取 {label} 用户信息失败",
                        "SKILLHUB_OAUTH_PROFILE_FETCH_FAILED",
                        "upstream",
                        provider=provider,
                    )

                uid_raw = u.get("id") or ""
                uid = str(uid_raw).strip()
                if not uid:
                    logger.warning(
                        "oauth user id missing",
                        **_oauth_log_fields(
                            provider,
                            stage="profile_validate",
                            result="failure",
                            error_code="SKILLHUB_OAUTH_USER_ID_MISSING",
                            error_class="upstream",
                        ),
                    )
                    return _oauth_redirect_error(
                        f"获取 {label} 用户信息失败",
                        "SKILLHUB_OAUTH_USER_ID_MISSING",
                        "upstream",
                        provider=provider,
                    )

                login = (u.get("login") or u.get("username") or "").strip() or str(uid_raw).strip()
                display_name = (u.get("name") or "").strip() or login
                avatar = u.get("avatar_url") or u.get("avatar") or ""
                bind_operation_actor(actor_id=uid, actor_name=display_name, actor_type="oauth_user")
                result = {
                    "provider": provider.value,
                    "access_token": access_token,
                    "token_type": str(token_json.get("token_type") or "bearer"),
                    "user": {
                        "id": uid,
                        "name": display_name,
                        "login": login,
                        "avatar_url": (avatar or None) if avatar else None,
                    },
                }

                pending = secrets.token_urlsafe(24)
                store.set_ex(
                    _pending_key(provider, pending),
                    json.dumps(result, ensure_ascii=False),
                    120,
                )
                logger.info(
                    "oauthcallback",
                    **complete_operation_result(
                        result="success",
                        provider=provider.value,
                        user_id=uid,
                    ),
                )
                return RedirectResponse(
                    url=(
                        f"{_frontend_login_url()}?"
                        f"oauth_session={quote(pending, safe='')}"
                        f"&oauth_provider={quote(provider.value, safe='')}"
                    ),
                    status_code=302,
                )
        except BusinessError as exc:
            return _redirect_from_publish_error(exc, provider=provider)
        except httpx.RequestError:
            logger.exception(
                "oauth request failed",
                **_oauth_log_fields(
                    provider,
                    stage="request",
                    result="failure",
                    error_code="SKILLHUB_OAUTH_REQUEST_FAILED",
                    error_class="upstream",
                    error_message=safe_error_summary(
                        error_code="SKILLHUB_OAUTH_REQUEST_FAILED",
                        error_class="upstream",
                        fallback="oauth request failed",
                    ),
                ),
            )
            return _oauth_redirect_error(
                "连接 OAuth 服务失败，请检查网络",
                "SKILLHUB_OAUTH_REQUEST_FAILED",
                "upstream",
                provider=provider,
            )
        except Exception:
            logger.exception(
                "oauth callback failed",
                **_oauth_log_fields(
                    provider,
                    stage="callback",
                    result="failure",
                    error_code="SKILLHUB_INTERNAL_UNEXPECTED",
                    error_class="internal",
                    error_message=safe_error_summary(
                        error_code="SKILLHUB_INTERNAL_UNEXPECTED",
                        error_class="internal",
                        fallback="oauth callback failed",
                    ),
                ),
            )
            logger.warning(
                "oauth callback redirect",
                **complete_operation_result(
                    result="failure",
                    error_code="SKILLHUB_INTERNAL_UNEXPECTED",
                    error_class="internal",
                    error_message=safe_error_summary(
                        error_code="SKILLHUB_INTERNAL_UNEXPECTED",
                        error_class="internal",
                        fallback="oauth callback failed",
                    ),
                    provider=provider.value,
                    http_status=500,
                ),
            )
            return _redirect_error(
                "登录处理失败",
                status_code=500,
                error_code="SKILLHUB_INTERNAL_UNEXPECTED",
                error_class="internal",
                error_name="internal_error",
            )


@router.post("/oauth/{provider}/session", response_model=ResponseModel[dict])
async def oauth_session_exchange(provider: OAuthProvider, body: OAuthSessionBody):
    """前端用 oauth_session 一次性兑换 access_token 与展示用用户信息（不返回 refresh_token）。"""
    with operation_context(operation_type="oauth_session_exchange"):
        bind_operation_actor(actor_type="anonymous")
        bind_operation_resource(resource_type="oauth_provider", resource_id=provider.value)
        _assert_oauth_ready(provider)
        store = get_oauth_str_store()
        key = _pending_key(provider, body.session)
        # 原子取出并删除：pending 是一次性令牌，并发兑换时只有一个请求能拿到值，
        # 其余拿到 None 视为已过期/无效，消除 get-then-delete 的 TOCTOU 竞态（F-57）。
        raw = store.get_del(key)
        if not raw:
            raise _oauth_api_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                message="会话已过期或无效",
                error="oauth_session_expired",
                error_code="SKILLHUB_OAUTH_SESSION_EXPIRED",
                error_class="auth",
            )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise _oauth_api_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                message="会话数据无效",
                error="oauth_session_invalid",
                error_code="SKILLHUB_OAUTH_SESSION_INVALID",
                error_class="validation",
            ) from None
        if not isinstance(data, dict):
            raise _oauth_api_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                message="会话数据无效",
                error="oauth_session_invalid",
                error_code="SKILLHUB_OAUTH_SESSION_INVALID",
                error_class="validation",
            )
        if data.get("provider") != provider.value:
            raise _oauth_api_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                message="会话与登录渠道不一致",
                error="oauth_provider_mismatch",
                error_code="SKILLHUB_OAUTH_PROVIDER_MISMATCH",
                error_class="validation",
            )
        user = data.get("user") if isinstance(data.get("user"), dict) else {}
        bind_operation_actor(
            actor_id=str(user.get("id") or "").strip() or None,
            actor_name=(user.get("name") or None),
            actor_type="oauth_user",
        )
        logger.info(
            "oauthsession exchange",
            **complete_operation_result(
                result="success",
                provider=provider.value,
            ),
        )
        return ResponseModel(code=200, message="ok", data=data)


@router.get("/me", response_model=ResponseModel[dict])
async def auth_me(
    authorization: str | None = Header(None),
    x_oauth_provider: str | None = Header(None, alias="X-OAuth-Provider"),
):
    """校验当前 Bearer，按 X-OAuth-Provider 选择厂商用户接口（缺省 gitcode）。"""
    with operation_context(operation_type="oauth_me"):
        bind_operation_actor(actor_type="oauth_user")
        if not authorization or not authorization.strip().lower().startswith("bearer "):
            raise _oauth_api_error(
                status_code=status.HTTP_401_UNAUTHORIZED,
                message="Missing or invalid Authorization",
                error="auth_header_missing",
                error_code="SKILLHUB_AUTH_HEADER_MISSING",
            )
        token = authorization[7:].strip()
        prov = normalize_oauth_provider_header(x_oauth_provider)
        bind_operation_resource(resource_type="oauth_provider", resource_id=prov)
        profile = await fetch_oauth_user_profile(prov, token)
        if not profile:
            raise _oauth_api_error(
                status_code=status.HTTP_401_UNAUTHORIZED,
                message="Invalid or expired token",
                error="auth_token_invalid",
                error_code="SKILLHUB_AUTH_TOKEN_INVALID",
            )
        gid = str(profile.get("id") or "").strip()
        if not gid:
            raise _oauth_api_error(
                status_code=status.HTTP_401_UNAUTHORIZED,
                message="Invalid or expired token",
                error="auth_token_invalid",
                error_code="SKILLHUB_AUTH_TOKEN_INVALID",
            )
        login = (profile.get("login") or profile.get("username") or "").strip() or gid
        name = (profile.get("name") or "").strip() or login
        bind_operation_actor(actor_id=gid, actor_name=name, actor_type="oauth_user")
        logger.info("oauthme", **complete_operation_result(result="success", provider=prov, user_id=gid))
        return ResponseModel(
            code=200,
            message="ok",
            data={
                "id": gid,
                "name": name,
                "login": login,
                "avatar_url": profile.get("avatar_url") or profile.get("avatar"),
                "is_market_moderation_admin": is_market_moderation_username(login),
            },
        )
