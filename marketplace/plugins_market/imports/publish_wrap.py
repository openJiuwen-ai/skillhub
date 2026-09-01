# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normalize bare or form-overridden publish uploads into standard market ZIP layout."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plugins_market.core.errors import PublishError
from plugins_market.imports.bundle_safe_extract import skill_import_extract_zip_to_dir
from plugins_market.imports.skill_entries import detect_import_entry_type, entry_to_publish_zip
from plugins_market.validation._pipeline import _find_plugin_yaml_path
from plugins_market.validation.constants import (
    RUNTIME_AGENT_MCP,
    RUNTIME_AGENT_PLUGIN,
    RUNTIME_AGENT_TEMPLATE,
)
from plugins_market.validation.plugin_yaml import (
    safe_load_yaml,
    validate_plugin_yaml_bytes,
    validate_plugin_yaml_public,
)
from plugins_market.validation.zip_utils import DecompressCounter, safe_read_zip_member


_PUBLISH_VERSION_FALLBACK = "0.0.1"


@dataclass(frozen=True)
class PublishMetadataOverrides:
    asset_name: str | None = None
    version: str | None = None
    display_name: str | None = None
    description: str | None = None
    tags: list[str] | None = None


def _has_publish_overrides(overrides: PublishMetadataOverrides) -> bool:
    if overrides.asset_name and overrides.asset_name.strip():
        return True
    if overrides.version and overrides.version.strip():
        return True
    if overrides.display_name and overrides.display_name.strip():
        return True
    if overrides.description and overrides.description.strip():
        return True
    return bool(overrides.tags)


def _overrides_entry_map(overrides: PublishMetadataOverrides) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    if overrides.asset_name and overrides.asset_name.strip():
        entry["name"] = overrides.asset_name.strip()
    if overrides.version and overrides.version.strip():
        entry["version"] = overrides.version.strip()
    if overrides.display_name and overrides.display_name.strip():
        entry["display_name"] = overrides.display_name.strip()
    if overrides.description and overrides.description.strip():
        entry["description"] = overrides.description.strip()
    if overrides.tags:
        entry["tags"] = list(overrides.tags)
    return entry


def _zip_has_market_plugin_yaml(content: bytes) -> bool:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return _find_plugin_yaml_path(zf) is not None


def _read_wrapped_plugin_yaml_fields(content: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        yaml_path = _find_plugin_yaml_path(zf)
        if not yaml_path:
            return {}
        raw = safe_read_zip_member(zf, yaml_path, DecompressCounter())
        yaml_text = validate_plugin_yaml_bytes(raw)
        yaml_data = safe_load_yaml(yaml_text, context="plugin.yaml")
        public = validate_plugin_yaml_public(yaml_data)
        metadata = yaml_data.get("metadata")
        tags: list[str] = []
        if isinstance(metadata, dict):
            raw_tags = metadata.get("tags")
            if isinstance(raw_tags, list):
                tags = [str(t).strip() for t in raw_tags if str(t).strip()]
        return {
            "name": public.name,
            "version": str(yaml_data.get("version") or "").strip(),
            "display_name": public.display_name,
            "description": str(yaml_data.get("description") or public.short_desc or "").strip(),
            "tags": tags,
        }


def _wrapped_overrides_differ(
    current: dict[str, Any],
    overrides: PublishMetadataOverrides,
) -> bool:
    entry = _overrides_entry_map(overrides)
    if not entry:
        return False
    if "name" in entry and entry["name"] != current.get("name"):
        return True
    if "version" in entry and entry["version"] != current.get("version"):
        return True
    if "display_name" in entry and entry["display_name"] != current.get("display_name"):
        return True
    if "description" in entry and entry["description"] != current.get("description"):
        return True
    if "tags" in entry and entry["tags"] != current.get("tags"):
        return True
    return False


def _extract_wrapped_native_entry(content: bytes, dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        yaml_path = _find_plugin_yaml_path(zf)
        if not yaml_path:
            raise PublishError(
                code=400,
                error="invalid_plugin_config",
                message="plugin.yaml 配置文件格式错误或缺失",
            )
        normalized = yaml_path.replace("\\", "/").strip("/")
        outer = normalized.rsplit("/", 1)[0]
        yaml_raw = safe_read_zip_member(zf, yaml_path, DecompressCounter())
        yaml_text = validate_plugin_yaml_bytes(yaml_raw)
        yaml_data = safe_load_yaml(yaml_text, context="plugin.yaml")
        asset_name = validate_plugin_yaml_public(yaml_data).name
        payload_prefix = f"{outer}/{asset_name}/"
        found_payload = False
        for info in zf.infolist():
            member = info.filename.replace("\\", "/").strip("/")
            if not member.startswith(payload_prefix):
                continue
            relative = member[len(payload_prefix):]
            if not relative or relative.endswith("/"):
                continue
            found_payload = True
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
        if not found_payload:
            raise PublishError(
                code=400,
                error="invalid_plugin_structure",
                message="市场外层内层载荷为空",
            )


def _resolve_extracted_entry_dir(extract_root: Path, filename: str | None) -> tuple[Path, str | None]:
    if detect_import_entry_type(extract_root) is not None:
        hint = Path(filename or "asset").stem.strip() or "asset"
        return extract_root, hint
    entry_dirs = sorted(
        [
            p
            for p in extract_root.iterdir()
            if p.is_dir() and not p.name.startswith(".") and p.name != "__MACOSX"
        ],
        key=lambda p: p.name,
    )
    if len(entry_dirs) == 1:
        return entry_dirs[0], entry_dirs[0].name
    raise PublishError(
        code=400,
        error="invalid_plugin_structure",
        message="无法识别资产包布局：请上传原生包（含 manifest.json 或 mcp.json）或标准市场包装包",
        error_code="SKILLHUB_PUBLISH_LAYOUT_UNRECOGNIZED",
        error_class="validation",
    )


def _normalize_entry_to_bytes(
    entry: Path,
    *,
    entry_key: str,
    entry_overrides: dict[str, Any],
    default_author: str,
    entry_name_hint: str | None,
) -> bytes:
    publish_zip, _name, _version = entry_to_publish_zip(
        entry,
        entry_key=entry_key,
        entry_overrides=entry_overrides,
        version_fallback=_PUBLISH_VERSION_FALLBACK,
        default_author=default_author,
        default_tags=[],
        entry_name_hint=entry_name_hint,
        allow_multi_asset=True,
        allow_publish_overrides=True,
    )
    try:
        return publish_zip.read_bytes()
    finally:
        publish_zip.unlink(missing_ok=True)


def prepare_publish_zip_content(
    content: bytes,
    *,
    filename: str | None,
    overrides: PublishMetadataOverrides,
    default_author: str,
) -> bytes:
    """Return market-layout ZIP bytes, wrapping bare uploads or applying publish overrides."""
    has_market_layout = _zip_has_market_plugin_yaml(content)
    entry_overrides = _overrides_entry_map(overrides)
    needs_wrap = not has_market_layout
    if has_market_layout and _has_publish_overrides(overrides):
        current = _read_wrapped_plugin_yaml_fields(content)
        needs_wrap = needs_wrap or _wrapped_overrides_differ(current, overrides)
    if not needs_wrap:
        return content

    tmp_root = Path(tempfile.mkdtemp(prefix="oj_publish_wrap_"))
    zip_path = tmp_root / "upload.zip"
    entry_dir = tmp_root / "entry"
    try:
        zip_path.write_bytes(content)
        if has_market_layout:
            entry_dir.mkdir()
            _extract_wrapped_native_entry(content, entry_dir)
            entry, name_hint = entry_dir, None
        else:
            extract_root = tmp_root / "extract"
            extract_root.mkdir()
            try:
                skill_import_extract_zip_to_dir(zip_path, extract_root)
            except ValueError as exc:
                raise PublishError(
                    code=400,
                    error="invalid_plugin_structure",
                    message=str(exc) or "资产包解压失败",
                    error_code="SKILLHUB_PUBLISH_LAYOUT_UNRECOGNIZED",
                    error_class="validation",
                ) from exc
            entry, name_hint = _resolve_extracted_entry_dir(
                extract_root,
                filename,
            )
        entry_type = detect_import_entry_type(entry)
        if entry_type not in (
            RUNTIME_AGENT_PLUGIN,
            RUNTIME_AGENT_TEMPLATE,
            RUNTIME_AGENT_MCP,
        ):
            raise PublishError(
                code=400,
                error="invalid_plugin_structure",
                message="当前仅支持裸 agent-plugin / agent-template / agent-mcp 自动包装",
                error_code="SKILLHUB_PUBLISH_LAYOUT_UNRECOGNIZED",
                error_class="validation",
            )
        if entry_type == RUNTIME_AGENT_MCP and not entry_overrides.get("version"):
            raise PublishError(
                code=400,
                error="invalid_plugin_config",
                message="裸 agent-mcp 发布须提供 version（plugin_version 或表单版本号）",
                error_code="SKILLHUB_PUBLISH_MCP_VERSION_REQUIRED",
                error_class="validation",
            )
        return _normalize_entry_to_bytes(
            entry,
            entry_key=entry.name,
            entry_overrides=entry_overrides,
            default_author=default_author,
            entry_name_hint=name_hint,
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
