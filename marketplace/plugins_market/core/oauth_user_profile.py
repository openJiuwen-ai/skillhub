# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""按 OAuth 提供方拉取用户资料（GitCode / GitHub / AgentOS）。"""

from __future__ import annotations

from typing import Any

import httpx

from plugins_market.core.config import settings
from plugins_market.core.github_user import fetch_github_profile
from plugins_market.core.gitcode_user import fetch_gitcode_profile
from plugins_market.core.logging import get_logger

logger = get_logger(__name__)


async def _fetch_agentos_profile(access_token: str) -> dict[str, Any] | None:
    """GET Control Panel /oauth2/userinfo with Bearer token."""
    token = (access_token or "").strip()
    if not token:
        logger.warning("_fetch_agentos_profile: empty token")
        return None
    url = (settings.agentos_auth_user_api_url or "").strip()
    if not url:
        logger.warning("_fetch_agentos_profile: AUTH_USER_API_URL not configured")
        return None
    try:
        logger.info("_fetch_agentos_profile: calling %s", url)
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        if res.status_code != 200:
            # 响应正文可能含 access_token / 用户敏感字段，不打印
            logger.warning("_fetch_agentos_profile: non-200 status=%s", res.status_code)
            return None
        data = res.json()
        if not isinstance(data, dict) or not data.get("id"):
            # 响应正文可能含敏感字段，不打印
            logger.warning("_fetch_agentos_profile: invalid response shape")
            return None
        logger.info("_fetch_agentos_profile: ok user_id=%s", data.get("id"))
        return data
    except Exception:
        logger.exception("_fetch_agentos_profile: request failed")
        return None


async def fetch_oauth_user_profile(provider: str, access_token: str) -> dict[str, Any] | None:
    if provider == "agentos":
        return await _fetch_agentos_profile(access_token)
    if provider == "github":
        return await fetch_github_profile(access_token)
    return await fetch_gitcode_profile(access_token)
