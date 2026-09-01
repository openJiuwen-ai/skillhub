# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from plugins_market.core.errors import PublishError
from plugins_market.imports.yaml_util import (
    dump_plugin_yaml,
    load_json_object_file,
    load_plugin_yaml,
    split_skill_frontmatter,
)
from plugins_market.validation.constants import (
    MARKET_VERSION_MAX_LEN,
    NAME_PATTERN,
    PLUGIN_YAML_DESCRIPTION_MAX_LEN,
    RUNTIME_AGENT_MCP,
    RUNTIME_AGENT_PLUGIN,
    RUNTIME_AGENT_TEMPLATE,
    RUNTIME_SKILL,
    SKILL_DESC_MAX_LEN,
    SKILL_NAME_MAX_LEN,
    SKILL_NAME_PATTERN,
    is_valid_market_version,
)
from plugins_market.validation.localized_manifest import (
    localized_manifest_tags,
    localized_manifest_text,
)
from plugins_market.validation.zip_utils import validate_png_icon_bytes


def _validate_disk_icon_png_if_present(icon_file: Path) -> None:
    if not icon_file.is_file():
        return
    try:
        validate_png_icon_bytes(icon_file.read_bytes(), path="icon.png")
    except PublishError as e:
        raise ValueError(str(e.detail.get("message") or e)) from e


def _is_valid_market_version_within_length(version: str | None) -> bool:
    v = (version or "").strip()
    return len(v) <= MARKET_VERSION_MAX_LEN and is_valid_market_version(v)


def _validate_plugin_skill_name(name: str) -> None:
    if not name or len(name) > SKILL_NAME_MAX_LEN:
        raise ValueError(f"skill name invalid or longer than {SKILL_NAME_MAX_LEN}")
    if not NAME_PATTERN.match(name):
        raise ValueError("skill name must match ^[a-z][a-z0-9-]*$")
    if not SKILL_NAME_PATTERN.match(name):
        raise ValueError(
            "skill name must use lowercase, digits, single hyphens between segments (Agent Skills rules)"
        )


def _normalize_description(desc: str) -> str:
    s = desc.strip()
    if not s:
        raise ValueError("description must be non-empty")
    if len(s) > SKILL_DESC_MAX_LEN:
        raise ValueError(f"description must be at most {SKILL_DESC_MAX_LEN} characters")
    return s


def _yaml_quote_string(s: str) -> str:
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_skill_md(name: str, description: str, body: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {_yaml_quote_string(description)}\n"
        "---\n\n"
        f"{body.lstrip()}"
    )


_ADMIN_IMPORT_RUNTIME_TYPES = {
    RUNTIME_SKILL,
    RUNTIME_AGENT_PLUGIN,
    RUNTIME_AGENT_TEMPLATE,
    RUNTIME_AGENT_MCP,
}


def _validate_wrapped_asset_name(name: str) -> None:
    if not name or not NAME_PATTERN.match(name):
        raise ValueError("asset name must match ^[a-z][a-z0-9-]*$")


def _resolved_override_text(overrides: dict[str, Any], key: str, fallback: str) -> str:
    value = overrides.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _is_raw_agent_mcp_entry(entry: Path) -> bool:
    if (entry / "mcp.json").is_file() or (entry / "cli.json").is_file():
        return True
    skills = entry / "skills"
    if not skills.is_dir():
        return False
    return (skills / "SKILL.md").is_file() or any(skills.glob("*/SKILL.md"))


def _patch_native_agent_manifest(
    payload_dir: Path,
    *,
    runtime_type: str,
    name: str,
    version: str,
) -> None:
    manifest_path = payload_dir / "manifest.json"
    if not manifest_path.is_file():
        return
    manifest = load_json_object_file(manifest_path, label="manifest.json")
    manifest["version"] = version
    if runtime_type == RUNTIME_AGENT_PLUGIN:
        manifest["id"] = name
    elif runtime_type == RUNTIME_AGENT_TEMPLATE:
        card = manifest.get("agentCard")
        card = dict(card) if isinstance(card, dict) else {}
        card["id"] = name
        manifest["agentCard"] = card
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_native_agent_staging(
    entry: Path,
    staging: Path,
    *,
    runtime_type: str,
    entry_overrides: dict[str, Any],
    version_fallback: str,
    default_author: str,
    default_tags: list[str],
    manifest: dict[str, Any] | None = None,
    allow_publish_overrides: bool = False,
) -> tuple[str, str]:
    if runtime_type in (RUNTIME_AGENT_PLUGIN, RUNTIME_AGENT_TEMPLATE):
        if manifest is None:
            raise ValueError(
                "manifest.json is required for agent-plugin/agent-template entries"
            )
        card = manifest.get("agentCard")
        card = card if isinstance(card, dict) else {}
        plugin_manifest = runtime_type == RUNTIME_AGENT_PLUGIN
        identity = manifest.get("id") if plugin_manifest else card.get("id")
        name = str(identity or "").strip()
        version = str(manifest.get("version") or "").strip()
        fallback_name = manifest.get("name") if plugin_manifest else card.get("name")
        fallback_desc = manifest.get("description") if plugin_manifest else card.get("description")
        display_name = localized_manifest_text(
            manifest.get("displayName")
        ) or localized_manifest_text(fallback_name)
        description = localized_manifest_text(
            manifest.get("displayDescription")
        ) or localized_manifest_text(fallback_desc)
        manifest_tags = localized_manifest_tags(manifest.get("tags"))
        override_name = (
            _resolved_override_text(entry_overrides, "name", "")
            if allow_publish_overrides
            else ""
        )
        override_version = entry_overrides.get("version")
        if allow_publish_overrides and override_name:
            name = override_name
        if allow_publish_overrides and override_version not in (None, ""):
            version = str(override_version).strip()
    else:
        name = _resolved_override_text(entry_overrides, "name", entry.name)
        raw_version = entry_overrides.get("version")
        version = str(raw_version).strip() if raw_version is not None else version_fallback
        display_name = name
        description = ""
        manifest_tags = []

    _validate_wrapped_asset_name(name)
    if not _is_valid_market_version_within_length(version):
        raise ValueError("resolved asset version must be semver x.y.z or git commit hex")

    display_name = _resolved_override_text(entry_overrides, "display_name", display_name or name)
    description = _resolved_override_text(entry_overrides, "description", description)
    override_tags = entry_overrides.get("tags")
    if isinstance(override_tags, list) and override_tags:
        tags = [str(tag).strip() for tag in override_tags if str(tag).strip()]
    else:
        tags = list(default_tags) or manifest_tags

    staging.mkdir(parents=True, exist_ok=True)
    shutil.copytree(entry, staging / name, dirs_exist_ok=True)
    if runtime_type in (RUNTIME_AGENT_PLUGIN, RUNTIME_AGENT_TEMPLATE):
        _patch_native_agent_manifest(
            staging / name,
            runtime_type=runtime_type,
            name=name,
            version=version,
        )
    plugin_data: dict[str, Any] = {
        "name": name,
        "version": version,
        "display_name": display_name,
        "description": description[:PLUGIN_YAML_DESCRIPTION_MAX_LEN],
        "runtime": {"type": runtime_type},
        "metadata": {"author": default_author.strip(), "tags": tags},
    }
    (staging / "plugin.yaml").write_text(dump_plugin_yaml(plugin_data), encoding="utf-8")
    return name, version


def detect_import_entry_type(entry: Path) -> str | None:
    """Identify one standard or raw admin-import entry without validating its payload."""
    plugin_yaml = entry / "plugin.yaml"
    if plugin_yaml.is_file():
        data = load_plugin_yaml(str(plugin_yaml))
        runtime = data.get("runtime")
        runtime_type = (
            str(runtime.get("type") or "").strip().lower()
            if isinstance(runtime, dict)
            else ""
        )
        return runtime_type if runtime_type in _ADMIN_IMPORT_RUNTIME_TYPES else f"unsupported:{runtime_type}"
    manifest_path = entry / "manifest.json"
    if manifest_path.is_file():
        package_type = load_json_object_file(manifest_path, label="manifest.json").get(
            "packageType"
        )
        manifest_type = {
            "plugin": RUNTIME_AGENT_PLUGIN,
            "agent_template": RUNTIME_AGENT_TEMPLATE,
        }.get(package_type)
        if manifest_type is not None:
            return manifest_type
    if _is_raw_agent_mcp_entry(entry):
        return RUNTIME_AGENT_MCP
    return RUNTIME_SKILL if is_simple_skill_entry(entry) else None


# 固定 ZIP 成员时间戳，避免克隆后 mtime 波动导致同一内容多次同步产生不同 zip 字节
_DETERMINISTIC_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def build_skill_plugin_zip_to_path(
    staging_root: Path, plugin_name: str, version: str, out_path: Path
) -> None:
    """将 staging 打成 `{name}-{version}/...` 布局的 skill 插件 ZIP，写入 ``out_path``（流式写入，不落整包于内存）。"""
    prefix = f"{plugin_name}-{version}"
    members = sorted(
        (fpath for fpath in staging_root.rglob("*") if fpath.is_file()),
        key=lambda p: p.relative_to(staging_root).as_posix(),
    )
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in members:
            rel = fpath.relative_to(staging_root).as_posix()
            info = zipfile.ZipInfo(f"{prefix}/{rel}", date_time=_DETERMINISTIC_ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, fpath.read_bytes())


def is_standard_skill_entry(entry: Path) -> bool:
    """标准包：plugin.yaml、{name}/SKILL.md；icon.png 可选。"""
    if not (entry / "plugin.yaml").is_file():
        return False
    try:
        data = load_plugin_yaml(str(entry / "plugin.yaml"))
    except ValueError:
        return False
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return False
    name = name.strip()
    return (entry / name / "SKILL.md").is_file()


def is_simple_skill_entry(entry: Path) -> bool:
    """简单包：条目根目录有 SKILL.md。"""
    return (entry / "SKILL.md").is_file()


def validate_standard_skill_staging(staging: Path) -> tuple[str, str]:
    """标准包 staging 校验（只读，不写 yaml）。"""
    data = load_plugin_yaml(str(staging / "plugin.yaml"))
    name = str(data.get("name") or "").strip()
    _validate_plugin_skill_name(name)

    skill_md = staging / name / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    fm, _body = split_skill_frontmatter(text)
    fm_name = fm.get("name")
    if not isinstance(fm_name, str) or fm_name.strip() != name:
        raise ValueError("SKILL.md frontmatter name must equal plugin.yaml name")

    version = str(data.get("version") or "").strip()
    if not _is_valid_market_version_within_length(version):
        raise ValueError("plugin.yaml version must be semver x.y.z or git commit hex")

    _validate_disk_icon_png_if_present(staging / "icon.png")

    return name, version


def read_simple_skill_md_declared_version(entry: Path) -> str | None:
    """简单包：SKILL.md frontmatter 中的 version（须符合市场版本规则）。"""
    skill_md = entry / "SKILL.md"
    if not skill_md.is_file():
        return None
    text = skill_md.read_text(encoding="utf-8")
    fm, _body = split_skill_frontmatter(text)
    raw = fm.get("version")
    if isinstance(raw, str):
        version = raw.strip()
        if _is_valid_market_version_within_length(version):
            return version
    return None


def read_standard_skill_md_declared_version(entry: Path) -> str | None:
    """标准包：{name}/SKILL.md frontmatter 中的 version（须符合市场版本规则）。"""
    if not (entry / "plugin.yaml").is_file():
        return None
    try:
        data = load_plugin_yaml(str(entry / "plugin.yaml"))
    except ValueError:
        return None
    name = str(data.get("name") or "").strip()
    if not name:
        return None
    skill_md = entry / name / "SKILL.md"
    if not skill_md.is_file():
        return None
    text = skill_md.read_text(encoding="utf-8")
    fm, _body = split_skill_frontmatter(text)
    raw = fm.get("version")
    if isinstance(raw, str):
        version = raw.strip()
        if _is_valid_market_version_within_length(version):
            return version
    return None


def resolve_skill_entry_declared_version(entry: Path) -> str | None:
    if is_simple_skill_entry(entry):
        return read_simple_skill_md_declared_version(entry)
    if is_standard_skill_entry(entry):
        return read_standard_skill_md_declared_version(entry)
    return None


def build_simple_skill_staging(
    entry: Path,
    staging: Path,
    *,
    default_version: str,
    default_author: str,
    default_tags: list[str],
    display_name: str | None = None,
    preserve_frontmatter: bool = False,
) -> tuple[str, str]:
    """简单包：根 SKILL.md -> 补全为标准包目录树（生成 plugin.yaml；无占位 icon）。"""
    text = (entry / "SKILL.md").read_text(encoding="utf-8")
    fm, body = split_skill_frontmatter(text)
    raw_name = fm.get("name")
    if not isinstance(raw_name, str):
        raise ValueError("SKILL.md frontmatter name is required and must be a string")
    name = raw_name.strip()
    _validate_plugin_skill_name(name)

    raw_desc = fm.get("description")
    if not isinstance(raw_desc, str):
        raise ValueError("SKILL.md frontmatter description is required and must be a string")
    description = _normalize_description(raw_desc)

    staging.mkdir(parents=True, exist_ok=True)
    skill_dir = staging / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_md = text if preserve_frontmatter else _render_skill_md(name, description, body)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    for child in entry.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_dir() and child.name in {
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            ".eggs",
            "dist",
            "out",
        }:
            continue
        if child.name == "SKILL.md":
            continue
        dest = skill_dir / child.name
        if child.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(child, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(child, dest)

    ver = default_version.strip()
    if not _is_valid_market_version_within_length(ver):
        raise ValueError("default_version must be semver x.y.z or git commit hex")

    disp = (display_name or "").strip() or name
    plugin_data: dict[str, Any] = {
        "name": name,
        "version": ver,
        "display_name": disp,
        "description": description[:PLUGIN_YAML_DESCRIPTION_MAX_LEN],
        "runtime": {"type": "skill"},
        "metadata": {"author": default_author.strip(), "tags": list(default_tags)},
    }
    (staging / "plugin.yaml").write_text(dump_plugin_yaml(plugin_data), encoding="utf-8")
    return name, ver


def entry_to_publish_zip(
    entry: Path,
    *,
    entry_key: str,
    entry_overrides: dict[str, Any],
    version_fallback: str,
    default_author: str,
    default_tags: list[str],
    entry_name_hint: str | None = None,
    allow_multi_asset: bool = False,
    allow_publish_overrides: bool = False,
) -> tuple[Path, str, str]:
    """归一化条目并打成临时 ZIP。返回 ``(zip_path, name, version)``；调用方须在完成后 ``unlink`` 该路径。

    ``entry_overrides`` 来自 ``manifest.json`` 根级对应**顶层目录名**的配置对象。
    """
    tmp = Path(tempfile.mkdtemp(prefix=f"oj_import_{entry_key}_"))
    zip_fd, zip_name = tempfile.mkstemp(prefix=f"oj_import_{entry_key}_pkg_", suffix=".zip")
    os.close(zip_fd)
    zip_path = Path(zip_name)
    try:
        staging = tmp / "staging"
        staging.mkdir()

        entry_type = detect_import_entry_type(entry) if allow_multi_asset else None
        if entry_type and entry_type.startswith("unsupported:"):
            raise ValueError(
                f"asset_type_not_supported: {entry_type.removeprefix('unsupported:') or 'missing'}"
            )

        if is_standard_skill_entry(entry) and (
            not allow_multi_asset or entry_type == RUNTIME_SKILL
        ):
            shutil.copytree(entry, staging, dirs_exist_ok=True)
            name, version = validate_standard_skill_staging(staging)
            vo = entry_overrides.get("version")
            if vo:
                ov = str(vo).strip()
                if _is_valid_market_version_within_length(ov):
                    py = staging / "plugin.yaml"
                    data = load_plugin_yaml(str(py))
                    data["version"] = ov
                    py.write_text(dump_plugin_yaml(data), encoding="utf-8")
                    version = ov
        elif is_simple_skill_entry(entry):
            vo = entry_overrides.get("version")
            manifest_ver = str(vo).strip() if vo else None
            fm_ver = read_simple_skill_md_declared_version(entry)
            v = manifest_ver or fm_ver or version_fallback
            if not _is_valid_market_version_within_length(v):
                raise ValueError("resolved simple skill version must be semver x.y.z or git commit hex")
            simple_options = {"preserve_frontmatter": True} if allow_multi_asset else {}
            name, version = build_simple_skill_staging(
                entry,
                staging,
                default_version=v,
                default_author=default_author,
                default_tags=default_tags,
                display_name=None,
                **simple_options,
            )
        elif not allow_multi_asset:
            raise ValueError("skill_layout_unrecognized")
        else:
            if (entry / "plugin.yaml").is_file():
                if entry_type is None:
                    raise ValueError("asset_layout_unrecognized")
                if entry_type != RUNTIME_SKILL and entry_overrides.get("version") not in (
                    None,
                    "",
                ):
                    raise ValueError(
                        "manifest entries.version 仅支持 Skill 和裸 agent-mcp 条目"
                    )
                shutil.copytree(entry, staging, dirs_exist_ok=True)
                if entry_type == RUNTIME_SKILL:
                    name, version = validate_standard_skill_staging(staging)
                else:
                    data = load_plugin_yaml(str(staging / "plugin.yaml"))
                    name = str(data.get("name") or "").strip()
                    version = str(data.get("version") or "").strip()
                    _validate_wrapped_asset_name(name)
                    if not _is_valid_market_version_within_length(version):
                        raise ValueError(
                            "plugin.yaml version must be semver x.y.z or git commit hex"
                        )
                vo = entry_overrides.get("version")
                if vo and entry_type == RUNTIME_SKILL:
                    ov = str(vo).strip()
                    if _is_valid_market_version_within_length(ov):
                        py = staging / "plugin.yaml"
                        data = load_plugin_yaml(str(py))
                        data["version"] = ov
                        py.write_text(dump_plugin_yaml(data), encoding="utf-8")
                        version = ov
            elif entry_type in (RUNTIME_AGENT_PLUGIN, RUNTIME_AGENT_TEMPLATE):
                if entry_overrides.get("version") not in (None, ""):
                    if not allow_publish_overrides:
                        raise ValueError(
                            "manifest entries.version 仅支持 Skill 和裸 agent-mcp 条目"
                        )
                manifest = load_json_object_file(
                    entry / "manifest.json", label="manifest.json"
                )
                name, version = _build_native_agent_staging(
                    entry,
                    staging,
                    runtime_type=entry_type,
                    entry_overrides=entry_overrides,
                    version_fallback=version_fallback,
                    default_author=default_author,
                    default_tags=default_tags,
                    manifest=manifest,
                    allow_publish_overrides=allow_publish_overrides,
                )
            elif entry_type == RUNTIME_AGENT_MCP:
                mcp_overrides = entry_overrides
                if entry_name_hint and not mcp_overrides.get("name"):
                    mcp_overrides = {**entry_overrides, "name": entry_name_hint}
                name, version = _build_native_agent_staging(
                    entry,
                    staging,
                    runtime_type=RUNTIME_AGENT_MCP,
                    entry_overrides=mcp_overrides,
                    version_fallback=version_fallback,
                    default_author=default_author,
                    default_tags=default_tags,
                    allow_publish_overrides=allow_publish_overrides,
                )
            else:
                raise ValueError("asset_layout_unrecognized")

        build_skill_plugin_zip_to_path(staging, name, version, zip_path)
        return zip_path, name, version
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
