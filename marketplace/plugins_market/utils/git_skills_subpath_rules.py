# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Git 接入 skills_subpath 规则：禁止路径穿越与逃出克隆根目录。"""

from __future__ import annotations

import re
from pathlib import Path

from plugins_market.core.errors import PublishError

_WINDOWS_DRIVE_PREFIX = re.compile(r"^[a-zA-Z]:")


def assert_git_skills_subpath(skills_subpath: str | None) -> str | None:
    """校验技能根相对路径；空则返回 None（仓库根）。非法则抛 PublishError invalid_skills_subpath。"""
    if skills_subpath is None or not str(skills_subpath).strip():
        return None

    raw = str(skills_subpath).strip()
    if len(raw) > 512:
        raise PublishError(code=400, error="invalid_skills_subpath", message="skills_subpath 过长。")
    if "\x00" in raw:
        raise PublishError(code=400, error="invalid_skills_subpath", message="skills_subpath 包含非法字符。")
    if raw.startswith(("/", "\\")):
        raise PublishError(code=400, error="invalid_skills_subpath", message="skills_subpath 不能为绝对路径。")
    if _WINDOWS_DRIVE_PREFIX.match(raw):
        raise PublishError(code=400, error="invalid_skills_subpath", message="skills_subpath 不能为绝对路径。")

    rel = raw.replace("\\", "/").strip("/")
    if not rel:
        return None

    for segment in rel.split("/"):
        if segment == "..":
            raise PublishError(
                code=400,
                error="invalid_skills_subpath",
                message="skills_subpath 不能包含 ..",
            )

    dummy_root = Path("/__git_clone_root__").resolve()
    try:
        (dummy_root / rel).resolve().relative_to(dummy_root)
    except ValueError as e:
        raise PublishError(
            code=400,
            error="invalid_skills_subpath",
            message="skills_subpath 必须位于克隆目录内",
        ) from e

    # 归一化后再参与 dedup：统一正斜杠、去掉首尾斜杠，避免 skills/ 与 skills 被当成不同源
    return rel
