# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""一键标星目标组织/仓库从配置读取，缺失时回退硬编码默认值。"""

from __future__ import annotations

from unittest.mock import patch

from plugins_market.routers.github_watch import (
    DEFAULT_STAR_REPO_NAMES,
    DEFAULT_WATCH_ORG,
    _get_star_repo_names,
    _get_watch_org,
)


def test_star_repo_names_fallback_when_config_blank() -> None:
    with patch("plugins_market.routers.github_watch.settings") as settings:
        settings.github_star_repos = "  "
        assert _get_star_repo_names() == DEFAULT_STAR_REPO_NAMES


def test_star_repo_names_from_comma_separated_config() -> None:
    with patch("plugins_market.routers.github_watch.settings") as settings:
        settings.github_star_repos = "jiuwenswarm, agent-core, "
        assert _get_star_repo_names() == ("jiuwenswarm", "agent-core")


def test_watch_org_from_config_and_fallback() -> None:
    with patch("plugins_market.routers.github_watch.settings") as settings:
        settings.github_star_org = "my-org"
        assert _get_watch_org() == "my-org"
        settings.github_star_org = ""
        assert _get_watch_org() == DEFAULT_WATCH_ORG
