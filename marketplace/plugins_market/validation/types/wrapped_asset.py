# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared layout validation for newly wrapped JiuwenSwarm market assets."""

from __future__ import annotations

import zipfile

from plugins_market.core.errors import PublishError


def _invalid(message: str) -> None:
    raise PublishError(
        code=400,
        error="invalid_plugin_structure",
        message=message,
        error_code="SKILLHUB_PLUGIN_STRUCTURE_INVALID",
        error_class="validation",
    )


def normalized_member_map(zf: zipfile.ZipFile) -> dict[str, str]:
    """Map normalized member paths to original names and reject aliases."""
    result: dict[str, str] = {}
    for original in zf.namelist():
        normalized = original.replace("\\", "/").strip("/")
        if not normalized:
            continue
        if normalized in result:
            _invalid(f"ZIP 包含重复路径：{normalized}")
        result[normalized] = original
    return result


def validate_wrapped_outer_layout(
    zf: zipfile.ZipFile,
    prefix: str,
    asset_name: str,
) -> dict[str, str]:
    """Require ``plugin.yaml``, optional icon and exactly one named payload."""
    members = normalized_member_map(zf)
    outer = prefix.replace("\\", "/").strip("/")
    if not outer:
        _invalid("新增资产必须使用 <outer>/plugin.yaml 市场外层")

    outer_prefix = f"{outer}/"
    payload_prefix = f"{outer_prefix}{asset_name}/"
    has_payload_file = False

    for normalized, original in members.items():
        if not normalized.startswith(outer_prefix):
            _invalid(f"市场包包含外层目录之外的条目：{original}")

        relative = normalized[len(outer_prefix):]
        if relative in {"plugin.yaml", "icon.png"}:
            continue
        if relative.startswith(f"{asset_name}/"):
            if normalized.startswith(payload_prefix) and not original.replace("\\", "/").endswith("/"):
                has_payload_file = True
            continue
        _invalid(
            "市场外层只允许 plugin.yaml、可选 icon.png 和唯一内层载荷目录 "
            f"{asset_name!r}；发现兄弟载荷或未声明条目：{relative}"
        )

    if not has_payload_file:
        _invalid(f"唯一内层载荷目录 {asset_name!r} 不存在或为空")
    return members
