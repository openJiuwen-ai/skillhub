# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""导入用：SKILL.md frontmatter、plugin.yaml 读写。

与 marketplace 发布校验（validation 包）共用 bounded YAML / frontmatter 规则，
错误在导入路径上转换为 ValueError，供 skill_entries 归一化逻辑使用。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from plugins_market.core.errors import PublishError
from plugins_market.validation.plugin_yaml import (
    safe_load_yaml,
    validate_plugin_yaml_bytes,
)
from plugins_market.validation.types.skill import parse_skill_frontmatter


def _publish_error_to_value_error(exc: PublishError) -> ValueError:
    return ValueError(str(exc.detail.get("message") or exc))


def split_skill_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    try:
        return parse_skill_frontmatter(text.encode("utf-8"))
    except PublishError as e:
        ve = _publish_error_to_value_error(e)
        raise ve from e


def load_plugin_yaml(path: str) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    try:
        yaml_text = validate_plugin_yaml_bytes(raw)
        data = safe_load_yaml(yaml_text, context="plugin.yaml")
    except PublishError as e:
        ve = _publish_error_to_value_error(e)
        raise ve from e
    if not isinstance(data, dict):
        raise ValueError("plugin.yaml must be a mapping")
    return data


def load_json_object_file(
    path: Path, *, label: str, max_bytes: int | None = None
) -> dict[str, Any]:
    """Read one bounded UTF-8 JSON object from an extracted import directory."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{label} cannot be read: {exc}") from exc
    if max_bytes is not None and len(raw) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} bytes")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be valid UTF-8 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} root must be an object")
    return data


def dump_plugin_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
