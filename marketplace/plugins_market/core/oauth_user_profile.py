# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""统一的 OAuth 用户资料入口；实现由 AuthAdapter 提供。"""

from __future__ import annotations

from typing import Any


async def fetch_oauth_user_profile(provider: str, access_token: str) -> dict[str, Any] | None:
    """Resolve a registered adapter and validate the token through that IAM."""
    from plugins_market.core.auth_adapters import get_auth_adapter

    adapter = get_auth_adapter(provider)
    if adapter is None:
        return None
    return await adapter.fetch_user_profile(access_token)
