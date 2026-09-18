# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validation for wrapped JiuwenSwarm agent plugins, templates, and groups."""

from __future__ import annotations

import json
import posixpath
import zipfile
from dataclasses import dataclass
from typing import Any, NoReturn

from plugins_market.core.errors import PublishError
from plugins_market.validation.constants import (
    DISPLAY_NAME_MAX_LEN,
    MAX_JSON_BYTES,
    PLUGIN_TAGS_MAX_COUNT,
    PLUGIN_TAG_MAX_LEN,
    PLUGIN_YAML_DESCRIPTION_MAX_LEN,
    RUNTIME_AGENT_PLUGIN,
    RUNTIME_AGENT_GROUP,
    RUNTIME_AGENT_TEMPLATE,
)
from plugins_market.validation.content_security import (
    find_dangerous_manifest_mcp_files,
    find_dangerous_zip_script,
)
from plugins_market.validation.localized_manifest import (
    localized_manifest_tags,
    localized_manifest_text,
)
from plugins_market.validation.types.wrapped_asset import validate_wrapped_outer_layout
from plugins_market.validation.zip_utils import (
    DecompressCounter,
    safe_read_zip_member,
    validate_png_icon_bytes,
)


def _invalid(error: str, message: str) -> None:
    raise PublishError(
        code=400,
        error=error,
        message=message,
        error_code="SKILLHUB_PLUGIN_MANIFEST_VALIDATION_FAILED",
        error_class="validation",
    )


def _dangerous(message: str) -> NoReturn:
    raise PublishError(
        code=400,
        error="dangerous_content",
        message=message,
        error_code="SKILLHUB_DANGEROUS_CONTENT",
        error_class="validation",
    )


def _required_string(data: dict[str, Any], field: str, *, error: str) -> str:
    value: Any = data
    for part in field.split("."):
        if not isinstance(value, dict):
            value = None
            break
        value = value.get(part)
    if not isinstance(value, str) or not value.strip():
        _invalid(error, f"manifest.{field} 必填且必须为非空字符串")
    return value.strip()


def _safe_relative_path(value: Any, field: str, *, error: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(error, f"manifest.{field} 必须为非空相对路径")
    raw = value.strip().replace("\\", "/")
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
        _invalid(error, f"manifest.{field} 不得使用绝对路径")
    if ".." in raw.split("/"):
        _invalid(error, f"manifest.{field} 不得包含 '..' 路径段")
    normalized = posixpath.normpath(raw)
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        _invalid(error, f"manifest.{field} 不得越出内层资产目录")
    return normalized.removeprefix("./")


def _member_exists(members: dict[str, str], path: str) -> bool:
    original = members.get(path)
    return original is not None and not original.replace("\\", "/").endswith("/")


def _read_manifest(
    zf: zipfile.ZipFile,
    original_path: str,
    counter: DecompressCounter,
    *,
    error: str,
) -> dict[str, Any]:
    raw = safe_read_zip_member(zf, original_path, counter)
    if len(raw) > MAX_JSON_BYTES:
        _invalid(error, f"manifest.json 超过大小上限（最大 {MAX_JSON_BYTES // 1024 // 1024} MB）")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _invalid(error, f"manifest.json 不是合法 UTF-8 JSON：{exc}")
    if not isinstance(value, dict):
        _invalid(error, "manifest.json 根结构必须为对象")
    return value


def _validate_market_fields(display_name: str, short_desc: str, tags: list[str]) -> None:
    if len(display_name) > DISPLAY_NAME_MAX_LEN:
        _invalid(
            "invalid_plugin_config",
            f"内层 manifest 派生的展示名不得超过 {DISPLAY_NAME_MAX_LEN} 个字符",
        )
    if len(short_desc) > PLUGIN_YAML_DESCRIPTION_MAX_LEN:
        _invalid(
            "invalid_plugin_config",
            f"内层 manifest 派生的描述不得超过 {PLUGIN_YAML_DESCRIPTION_MAX_LEN} 个字符",
        )
    if len(tags) > PLUGIN_TAGS_MAX_COUNT:
        _invalid(
            "invalid_plugin_config",
            f"内层 manifest 派生的标签不得超过 {PLUGIN_TAGS_MAX_COUNT} 个",
        )
    for index, tag in enumerate(tags):
        if len(tag) > PLUGIN_TAG_MAX_LEN:
            _invalid(
                "invalid_plugin_config",
                f"内层 manifest.tags[{index}] 派生值不得超过 {PLUGIN_TAG_MAX_LEN} 个字符",
            )


def _validate_declared_skills(skills: Any, *, error: str) -> None:
    """只校验 skills 数组形态与路径安全，不因缺少 SKILL.md 拒发。"""
    if skills is None:
        return
    if not isinstance(skills, list):
        _invalid(error, "manifest.skills 必须为数组")
    for index, item in enumerate(skills):
        if not isinstance(item, dict):
            _invalid(error, f"manifest.skills[{index}] 必须为对象")
        raw_dir = item.get("dir")
        if isinstance(raw_dir, str) and raw_dir.strip():
            _safe_relative_path(raw_dir, f"skills[{index}].dir", error=error)


def _validate_declared_mcp_entries(manifest: dict[str, Any], *, error: str) -> None:
    entries = manifest.get("mcps")
    if entries is None:
        return
    if not isinstance(entries, list):
        _invalid(error, "manifest.mcps 必须为数组")
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            _invalid(error, f"manifest.mcps[{index}] 必须为对象")
        connector = item.get("connector")
        if isinstance(connector, str) and connector.strip():
            continue
        file_path = item.get("file")
        if isinstance(file_path, str) and file_path.strip():
            _safe_relative_path(file_path, f"mcps[{index}].file", error=error)
            continue
        dir_path = item.get("dir")
        if isinstance(dir_path, str) and dir_path.strip():
            _safe_relative_path(dir_path, f"mcps[{index}].dir", error=error)


def _validate_declared_model_file(manifest: dict[str, Any], *, error: str) -> None:
    model = manifest.get("model")
    if model is None:
        return
    if not isinstance(model, dict):
        _invalid(error, "manifest.model 必须为对象")
    file_path = model.get("file")
    if isinstance(file_path, str) and file_path.strip():
        _safe_relative_path(file_path, "model.file", error=error)


def _validate_declared_file_arrays(
    manifest: dict[str, Any],
    fields: tuple[str, ...],
    *,
    error: str,
) -> None:
    for field in fields:
        entries = manifest.get(field)
        if entries is None:
            continue
        if not isinstance(entries, list):
            _invalid(error, f"manifest.{field} 必须为数组")
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                _invalid(error, f"manifest.{field}[{index}] 必须为对象")
            raw_file = item.get("file")
            if isinstance(raw_file, str) and raw_file.strip():
                _safe_relative_path(raw_file, f"{field}[{index}].file", error=error)


def _validate_declared_subagents(entries: Any, *, error: str) -> None:
    if entries is None:
        return
    if not isinstance(entries, list):
        _invalid(error, "manifest.subagents 必须为数组")
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            _invalid(error, f"manifest.subagents[{index}] 必须为对象")
        raw_dir = item.get("dir")
        if isinstance(raw_dir, str) and raw_dir.strip():
            _safe_relative_path(raw_dir, f"subagents[{index}].dir", error=error)


def _validate_declared_agent_names(entries: Any, *, error: str) -> None:
    if entries is None:
        return
    if not isinstance(entries, list):
        _invalid(error, "manifest.agents 必须为数组")
    for index, item in enumerate(entries):
        if not isinstance(item, str) or not item.strip():
            _invalid(error, f"manifest.agents[{index}] 必须为非空字符串")
        name = item.strip()
        if name in {".", ".."} or "/" in name or "\\" in name:
            _invalid(error, f"manifest.agents[{index}] 不得包含路径分隔符")


def _validate_declared_group_skills(entries: Any, *, error: str) -> None:
    if entries is None:
        return
    if not isinstance(entries, list):
        _invalid(error, "manifest.skills 必须为数组")
    for index, item in enumerate(entries):
        if isinstance(item, str):
            name = item.strip()
            reserved_or_empty = not name or name in {".", ".."}
            has_path_sep = "/" in name or "\\" in name
            if reserved_or_empty or has_path_sep:
                _invalid(error, f"manifest.skills[{index}] 必须为非空技能名且不得含路径")
            continue
        if not isinstance(item, dict):
            _invalid(error, f"manifest.skills[{index}] 必须为字符串或对象")
        raw_dir = item.get("dir")
        if isinstance(raw_dir, str) and raw_dir.strip():
            _safe_relative_path(raw_dir, f"skills[{index}].dir", error=error)


def _validate_agent_plugin_capabilities(manifest: dict[str, Any]) -> None:
    error = "invalid_agent_plugin_capability"
    for forbidden in ("persona", "agent_card", "model", "subagents", "memories", "rubrics"):
        if forbidden in manifest:
            _invalid(error, f"agent-plugin 根 manifest 不允许声明 {forbidden}")

    _validate_declared_skills(manifest.get("skills"), error=error)
    _validate_declared_file_arrays(manifest, ("tools", "rails"), error=error)
    _validate_declared_mcp_entries(manifest, error=error)


@dataclass(frozen=True)
class AgentAssetOuterRef:
    """外层市场包装（plugin.yaml）派生的资产身份信息。"""

    prefix: str
    name: str
    version: str
    runtime_type: str


def validate_agent_asset_layout(
    zf: zipfile.ZipFile,
    outer_ref: AgentAssetOuterRef,
    counter: DecompressCounter,
) -> dict[str, Any]:
    """Validate exact inner payload root and return normalized marketplace metadata."""
    prefix = outer_ref.prefix
    asset_name = outer_ref.name
    outer_version = outer_ref.version
    runtime_type = outer_ref.runtime_type
    members = validate_wrapped_outer_layout(zf, prefix, asset_name)
    outer = prefix.rstrip("/")
    payload_prefix = f"{outer}/{asset_name}/" if outer else f"{asset_name}/"
    manifest_path = f"{payload_prefix}manifest.json"
    readme_path = f"{payload_prefix}README.md"
    manifest_original = members.get(manifest_path)

    manifest_error = (
        "invalid_agent_plugin_manifest"
        if runtime_type == RUNTIME_AGENT_PLUGIN
        else "invalid_manifest_json"
    )
    if manifest_original is None or not _member_exists(members, manifest_path):
        _invalid(manifest_error, f"内层目录 {asset_name!r} 缺少 manifest.json")

    manifest = _read_manifest(zf, manifest_original, counter, error=manifest_error)
    version = _required_string(manifest, "version", error=manifest_error)
    package_type = _required_string(manifest, "package_type", error=manifest_error)
    expected_package_type = {
        RUNTIME_AGENT_PLUGIN: "plugin",
        RUNTIME_AGENT_TEMPLATE: "agent_template",
        RUNTIME_AGENT_GROUP: "agent_group",
    }.get(runtime_type)
    if expected_package_type is None:
        _invalid("invalid_plugin_config", f"不支持的智能体资产类型：{runtime_type}")
    if package_type != expected_package_type:
        _invalid(
            manifest_error,
            f"manifest.package_type 必须为 {expected_package_type!r}，实际为 {package_type!r}",
        )
    if version != outer_version:
        _invalid(
            "agent_plugin_version_mismatch" if runtime_type == RUNTIME_AGENT_PLUGIN else "invalid_manifest_json",
            f"plugin.yaml 与 manifest.json 版本不一致：{outer_version!r} != {version!r}",
        )

    if runtime_type == RUNTIME_AGENT_PLUGIN:
        identity = _required_string(manifest, "id", error=manifest_error)
        if identity != asset_name:
            _invalid(
                "agent_plugin_identity_mismatch",
                f"manifest.id {identity!r} 必须与 plugin.yaml.name {asset_name!r} 一致",
            )
        _validate_agent_plugin_capabilities(manifest)
        display_name = localized_manifest_text(
            manifest.get("display_name")
        ) or localized_manifest_text(manifest.get("name"))
        short_desc = localized_manifest_text(
            manifest.get("display_description")
        ) or localized_manifest_text(
            manifest.get("description")
        )
        asset_type = RUNTIME_AGENT_PLUGIN
    elif runtime_type == RUNTIME_AGENT_TEMPLATE:
        identity = _required_string(manifest, "name", error="invalid_manifest_json")
        template_desc = _required_string(manifest, "description", error="invalid_manifest_json")
        if identity != asset_name:
            _invalid(
                "invalid_manifest_json",
                f"manifest.name {identity!r} 必须与 plugin.yaml.name {asset_name!r} 一致",
            )
        persona = manifest.get("persona")
        if persona is not None:
            if not isinstance(persona, dict):
                _invalid("missing_persona", "manifest.persona 必须为对象")
            raw_persona_dir = persona.get("dir")
            if isinstance(raw_persona_dir, str) and raw_persona_dir.strip():
                _safe_relative_path(
                    raw_persona_dir, "persona.dir", error="missing_persona"
                )
        _validate_declared_skills(manifest.get("skills"), error="invalid_skill_md")
        _validate_declared_file_arrays(
            manifest,
            ("tools", "rails", "memories", "rubrics"),
            error="invalid_manifest_json",
        )
        _validate_declared_mcp_entries(manifest, error="invalid_manifest_json")
        _validate_declared_model_file(manifest, error="invalid_manifest_json")
        _validate_declared_subagents(
            manifest.get("subagents"), error="invalid_manifest_json"
        )
        display_name = (
            localized_manifest_text(manifest.get("display_name")) or identity
        )
        short_desc = (
            localized_manifest_text(manifest.get("display_description")) or template_desc
        )
        asset_type = RUNTIME_AGENT_TEMPLATE
    elif runtime_type == RUNTIME_AGENT_GROUP:
        identity = _required_string(manifest, "name", error="invalid_manifest_json")
        group_desc = _required_string(manifest, "description", error="invalid_manifest_json")
        if identity != asset_name:
            _invalid(
                "invalid_manifest_json",
                f"manifest.name {identity!r} 必须与 plugin.yaml.name {asset_name!r} 一致",
            )
        _validate_declared_agent_names(manifest.get("agents"), error="invalid_manifest_json")
        _validate_declared_group_skills(manifest.get("skills"), error="invalid_skill_md")
        display_name = (
            localized_manifest_text(manifest.get("display_name")) or identity
        )
        short_desc = (
            localized_manifest_text(manifest.get("display_description")) or group_desc
        )
        asset_type = RUNTIME_AGENT_GROUP
    else:  # defensive guard
        _invalid("invalid_plugin_config", f"不支持的智能体资产类型：{runtime_type}")

    # 静态安全扫描：manifest 引用的 mcp.json 与内层脚本不得包含危险执行内容。
    mcp_hit = find_dangerous_manifest_mcp_files(
        zf, members, payload_prefix, manifest, counter
    )
    if mcp_hit:
        _dangerous(f"{mcp_hit[0]} 包含危险命令（{mcp_hit[1]}）")
    hit = find_dangerous_zip_script(zf, members, payload_prefix, counter)
    if hit:
        _dangerous(f"{hit[0]} 包含危险脚本内容（{hit[1]}）")

    icon_bytes = b""
    avatar = manifest.get("avatar")
    if isinstance(avatar, str) and avatar.strip():
        avatar_path = _safe_relative_path(avatar, "avatar", error=manifest_error)
        avatar_member = f"{payload_prefix}{avatar_path}"
        if _member_exists(members, avatar_member) and avatar_path.lower().endswith(".png"):
            raw_icon = safe_read_zip_member(zf, members[avatar_member], counter)
            validate_png_icon_bytes(raw_icon, path=avatar_member)
            icon_bytes = raw_icon

    if not icon_bytes:
        icon_path = f"{outer}/icon.png" if outer else "icon.png"
        if icon_path in members:
            icon_bytes = safe_read_zip_member(zf, members[icon_path], counter)
            validate_png_icon_bytes(icon_bytes, path=icon_path)

    tags = localized_manifest_tags(manifest.get("tags"))
    _validate_market_fields(display_name or asset_name, short_desc, tags)
    detail_desc = ""
    if _member_exists(members, readme_path):
        readme_raw = safe_read_zip_member(zf, members[readme_path], counter)
        detail_desc = readme_raw.decode("utf-8", errors="replace")
    return {
        "asset_type": asset_type,
        "display_name": display_name or asset_name,
        "short_desc": short_desc,
        "tags": tags,
        "detail_desc": detail_desc,
        "icon_bytes": icon_bytes,
    }
