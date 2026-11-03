# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""从环境变量 MARKET_REVIEW_ADMIN_USERNAMES 或列表文件加载市场审核管理员用户名。"""

from __future__ import annotations

import os
import threading
from typing import FrozenSet

from plugins_market.core.config import parse_review_admin_usernames_value, settings
from plugins_market.core.logging import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_cache_path: str | None = None
_cache_mtime: float | None = None
_cache_names: FrozenSet[str] = frozenset()


def _read_usernames_from_file(path: str) -> FrozenSet[str]:
    names: set[str] = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                name = line.strip()
                if not name or name.startswith("#"):
                    continue
                names.add(name)
    except OSError as e:
        logger.warning("无法读取审核管理员配置文件 %s: %s", path, e)
        return frozenset()
    return frozenset(names)


def get_review_admin_usernames() -> FrozenSet[str]:
    """返回审核管理员登录名集合。若 MARKET_REVIEW_ADMIN_USERNAMES 非空则直接来自环境，否则读文件；文件变更后自动重新加载。"""
    from_env = parse_review_admin_usernames_value(settings.review_admin_usernames)
    if from_env:
        return frozenset(from_env)

    global _cache_path, _cache_mtime, _cache_names
    path = (settings.review_admin_usernames_file or "").strip()
    if not path:
        with _lock:
            _cache_path = ""
            _cache_mtime = None
            _cache_names = frozenset()
        return _cache_names

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        with _lock:
            _cache_path = path
            _cache_mtime = None
            _cache_names = frozenset()
        return _cache_names

    with _lock:
        if _cache_path == path and _cache_mtime is not None and abs(_cache_mtime - mtime) < 1e-6:
            return _cache_names
        _cache_path = path
        _cache_mtime = mtime
        _cache_names = _read_usernames_from_file(path)
        return _cache_names


def is_market_moderation_username(login: str | None, provider: str | None = None) -> bool:
    """Check the configured moderation username, without trusting Keycloak names.

    The existing allowlist is based on OAuth usernames. A Keycloak preferred
    username/email is realm-controlled and may collide with a trusted username
    from another IAM, so it must never grant moderation privileges by itself.
    ``provider=None`` preserves the legacy behavior for existing callers.
    """
    if (provider or "").strip().lower() == "keycloak":
        return False
    if not login or not str(login).strip():
        return False
    return str(login).strip() in get_review_admin_usernames()


def bump_review_admin_cache_for_tests() -> None:
    """测试用：清空缓存以便重读文件。"""
    global _cache_path, _cache_mtime, _cache_names
    with _lock:
        _cache_path = None
        _cache_mtime = None
        _cache_names = frozenset()
