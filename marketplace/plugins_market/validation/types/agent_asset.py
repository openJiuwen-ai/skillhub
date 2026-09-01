# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validation for wrapped JiuwenSwarm agent plugins and agent templates."""

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
from plugins_market.validation.types.skill import (
    parse_skill_frontmatter,
    validate_skill_frontmatter,
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


def _directory_has_file(members: dict[str, str], path: str, suffix: str | None = None) -> bool:
    prefix = path.rstrip("/") + "/"
    for member in members:
        if members[member].replace("\\", "/").endswith("/"):
            continue
        if member.startswith(prefix) and member != prefix:
            if suffix is None or member.lower().endswith(suffix.lower()):
                return True
    return False


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


def _validate_declared_skills(
    zf: zipfile.ZipFile,
    members: dict[str, str],
    payload_prefix: str,
    skills: Any,
    counter: DecompressCounter,
    *,
    error: str,
) -> set[str]:
    """校验 manifest 声明的 skill 目录，返回已校验的目录名集合。"""
    if skills is None:
        return set()
    if not isinstance(skills, list):
        _invalid(error, "manifest.skills 必须为数组")
    if not skills:
        _invalid(error, "manifest.skills 禁止为空数组，无 skill 时请整段省略")
    declared: set[str] = set()
    for index, item in enumerate(skills):
        if not isinstance(item, dict):
            _invalid(error, f"manifest.skills[{index}] 必须为对象")
        relative = _safe_relative_path(item.get("dir"), f"skills[{index}].dir", error=error)
        skill_name = posixpath.basename(relative.rstrip("/"))
        skill_path = f"{payload_prefix}{relative}/SKILL.md"
        original = members.get(skill_path)
        if original is None:
            _invalid(error, f"manifest.skills[{index}] 声明的目录缺少 SKILL.md：{relative}")
        raw = safe_read_zip_member(zf, original, counter)
        frontmatter, _ = parse_skill_frontmatter(raw)
        validate_skill_frontmatter(frontmatter, dir_name=skill_name, yaml_name=skill_name)
        declared.add(skill_name)
    return declared


def _validate_bundled_skill_dirs(
    zf: zipfile.ZipFile,
    members: dict[str, str],
    payload_prefix: str,
    counter: DecompressCounter,
    *,
    error: str,
    skip_dirs: set[str] | None = None,
) -> None:
    """校验内层 skills/ 下未在 manifest 声明的技能目录。

    manifest.skills 已校验的目录通过 ``skip_dirs`` 跳过，避免重复读取。
    """
    skills_prefix = f"{payload_prefix}skills/"
    skill_dirs: set[str] = set()
    for normalized in members:
        if not normalized.startswith(skills_prefix):
            continue
        parts = normalized[len(skills_prefix):].split("/")
        if len(parts) >= 2 and parts[0]:
            skill_dirs.add(parts[0])
    excluded = skip_dirs or set()
    for dir_name in sorted(skill_dirs - excluded):
        skill_path = f"{skills_prefix}{dir_name}/SKILL.md"
        original = members.get(skill_path)
        if original is None or not _member_exists(members, skill_path):
            _invalid(error, f"skills/{dir_name} 缺少 SKILL.md")
        raw = safe_read_zip_member(zf, original, counter)
        frontmatter, _ = parse_skill_frontmatter(raw)
        validate_skill_frontmatter(frontmatter, dir_name=dir_name, yaml_name=dir_name)


def _validate_declared_file_arrays(
    members: dict[str, str],
    payload_prefix: str,
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
            relative = _safe_relative_path(
                item.get("file"), f"{field}[{index}].file", error=error
            )
            if not _member_exists(members, f"{payload_prefix}{relative}"):
                _invalid(error, f"manifest.{field}[{index}] 声明的文件不存在：{relative}")


def _validate_declared_subagents(
    zf: zipfile.ZipFile,
    members: dict[str, str],
    payload_prefix: str,
    entries: Any,
    counter: DecompressCounter,
    *,
    error: str,
) -> None:
    if entries is None:
        return
    if not isinstance(entries, list):
        _invalid(error, "manifest.subagents 必须为数组")
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            _invalid(error, f"manifest.subagents[{index}] 必须为对象")
        relative = _safe_relative_path(
            item.get("dir"), f"subagents[{index}].dir", error=error
        )
        dir_prefix = f"{payload_prefix}{relative}".rstrip("/") + "/"
        subagent_files: list[str] = []
        for member_path in members:
            if (
                member_path.startswith(dir_prefix)
                and member_path.endswith(".subagent.json")
                and not members[member_path].replace("\\", "/").endswith("/")
            ):
                subagent_files.append(member_path)
        subagent_files.sort()
        if not subagent_files:
            _invalid(
                error,
                f"manifest.subagents[{index}] 声明的目录缺少 .subagent.json：{relative}",
            )
        for member_path in subagent_files:
            raw = safe_read_zip_member(zf, members[member_path], counter)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                _invalid(
                    error,
                    f"manifest.subagents[{index}] 的 .subagent.json 不是合法 JSON："
                    f"{relative}（{exc}）",
                )
            if not isinstance(parsed, dict):
                _invalid(
                    error,
                    f"manifest.subagents[{index}] 的 .subagent.json 根结构必须为对象：{relative}",
                )


def _validate_agent_plugin_capabilities(
    zf: zipfile.ZipFile,
    members: dict[str, str],
    payload_prefix: str,
    manifest: dict[str, Any],
    counter: DecompressCounter,
) -> None:
    error = "invalid_agent_plugin_capability"
    for forbidden in ("persona", "memories", "rubrics"):
        if forbidden in manifest:
            _invalid(error, f"agent-plugin 根 manifest 不允许声明 {forbidden}")

    capability_fields = ("skills", "tools", "mcps", "rails")
    if not any(isinstance(manifest.get(field), list) and manifest[field] for field in capability_fields):
        _invalid(error, "agent-plugin 至少必须声明一项 skills/tools/mcps/rails 能力")

    declared_skills = _validate_declared_skills(
        zf, members, payload_prefix, manifest.get("skills"), counter, error=error
    )
    _validate_declared_file_arrays(
        members,
        payload_prefix,
        manifest,
        ("tools", "mcps", "rails"),
        error=error,
    )
    _validate_declared_subagents(
        zf, members, payload_prefix, manifest.get("subagents"), counter, error=error
    )
    _validate_bundled_skill_dirs(
        zf, members, payload_prefix, counter, error=error, skip_dirs=declared_skills
    )


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
    if not _member_exists(members, readme_path):
        _invalid(manifest_error, f"内层目录 {asset_name!r} 缺少 README.md")

    manifest = _read_manifest(zf, manifest_original, counter, error=manifest_error)
    version = _required_string(manifest, "version", error=manifest_error)
    package_type = _required_string(manifest, "packageType", error=manifest_error)
    expected_package_type = "plugin" if runtime_type == RUNTIME_AGENT_PLUGIN else "agent_template"
    if package_type != expected_package_type:
        _invalid(
            manifest_error,
            f"manifest.packageType 必须为 {expected_package_type!r}，实际为 {package_type!r}",
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
        _validate_agent_plugin_capabilities(
            zf, members, payload_prefix, manifest, counter
        )
        display_name = localized_manifest_text(
            manifest.get("displayName")
        ) or localized_manifest_text(manifest.get("name"))
        short_desc = localized_manifest_text(
            manifest.get("displayDescription")
        ) or localized_manifest_text(
            manifest.get("description")
        )
        asset_type = RUNTIME_AGENT_PLUGIN
    elif runtime_type == RUNTIME_AGENT_TEMPLATE:
        identity = _required_string(manifest, "agentCard.id", error="invalid_agent_card")
        card_name = _required_string(manifest, "agentCard.name", error="invalid_agent_card")
        card_desc = _required_string(manifest, "agentCard.description", error="invalid_agent_card")
        if identity != asset_name:
            _invalid(
                "invalid_agent_card",
                f"manifest.agentCard.id {identity!r} 必须与 plugin.yaml.name {asset_name!r} 一致",
            )
        persona = manifest.get("persona")
        if not isinstance(persona, dict):
            _invalid("missing_persona", "manifest.persona.dir 必填")
        persona_dir = _safe_relative_path(
            persona.get("dir"), "persona.dir", error="missing_persona"
        )
        if not _directory_has_file(members, f"{payload_prefix}{persona_dir}", ".md"):
            _invalid("missing_persona", "persona 目录必须至少包含一个 .md 文件")
        declared_skills = _validate_declared_skills(
            zf,
            members,
            payload_prefix,
            manifest.get("skills"),
            counter,
            error="invalid_skill_md",
        )
        _validate_declared_file_arrays(
            members,
            payload_prefix,
            manifest,
            ("tools", "mcps", "rails", "memories", "rubrics"),
            error="invalid_manifest_json",
        )
        _validate_declared_subagents(
            zf,
            members,
            payload_prefix,
            manifest.get("subagents"),
            counter,
            error="invalid_manifest_json",
        )
        _validate_bundled_skill_dirs(
            zf,
            members,
            payload_prefix,
            counter,
            error="invalid_skill_md",
            skip_dirs=declared_skills,
        )
        display_name = (
            localized_manifest_text(manifest.get("displayName")) or card_name
        )
        short_desc = (
            localized_manifest_text(manifest.get("displayDescription")) or card_desc
        )
        asset_type = RUNTIME_AGENT_TEMPLATE
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

    avatar = manifest.get("avatar")
    if avatar is not None:
        avatar_path = _safe_relative_path(avatar, "avatar", error=manifest_error)
        if not _member_exists(members, f"{payload_prefix}{avatar_path}"):
            _invalid(manifest_error, f"manifest.avatar 指向的文件不存在：{avatar_path}")

    icon_path = f"{outer}/icon.png" if outer else "icon.png"
    icon_bytes = b""
    if icon_path in members:
        icon_bytes = safe_read_zip_member(zf, members[icon_path], counter)
        validate_png_icon_bytes(icon_bytes, path=icon_path)

    tags = localized_manifest_tags(manifest.get("tags"))
    _validate_market_fields(display_name or asset_name, short_desc, tags)
    readme_raw = safe_read_zip_member(zf, members[readme_path], counter)
    return {
        "asset_type": asset_type,
        "display_name": display_name or asset_name,
        "short_desc": short_desc,
        "tags": tags,
        "detail_desc": readme_raw.decode("utf-8", errors="replace"),
        "icon_bytes": icon_bytes,
    }
