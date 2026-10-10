# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""GitHub REST：使用 Bearer access_token 拉取当前用户信息。"""

from __future__ import annotations

from typing import Any

import httpx

from plugins_market.core.config import DEFAULT_GITHUB_USER_API_URL, settings
from plugins_market.core.logging import get_logger

logger = get_logger(__name__)


async def fetch_github_profile(access_token: str) -> dict[str, Any] | None:
    """
    GET settings.github_auth_user_api_url，Header Authorization: Bearer。
    成功返回 dict（id 为 str）；失败返回 None。
    """
    token = (access_token or "").strip()
    if not token:
        return None
    url = (settings.github_auth_user_api_url or DEFAULT_GITHUB_USER_API_URL).strip()
    if not url:
        logger.warning("github_auth_user_api_url is not configured")
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if res.status_code != 200:
            return None
        try:
            data = res.json()
        except ValueError:
            logger.warning("GitHub user API non-JSON: %s", res.text[:200])
            return None
        if not isinstance(data, dict):
            return None
        gid = data.get("id")
        if gid is None:
            return None
        out = dict(data)
        out["id"] = str(gid).strip()
        if not out["id"]:
            return None
        return out
    except httpx.RequestError as e:
        logger.warning("fetch_github_profile request error: %s", e)
        return None
    except Exception as e:
        logger.warning("fetch_github_profile failed: %s", e)
        return None
