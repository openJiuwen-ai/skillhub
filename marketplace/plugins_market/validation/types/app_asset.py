# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validation for JiuwenSwarm application packages (``runtime.type: app``).

An application package reuses the wrapped market layout (``<outer>/plugin.yaml``
plus a single inner payload directory), but the payload is a static HTML
application instead of a ``manifest.json`` based agent asset:

    <outer>/plugin.yaml
    <outer>/icon.png                       # optional
    <outer>/<name>/index.html              # required entry
    <outer>/<name>/assets/...
    <outer>/<name>/scripts/app_server.py   # optional server-side entry

Hub validates distribution shape, entry, icon, version consistency and the
executable scripts only. Runtime contract checks stay in Swarm's
``SkillAdapter.validate`` after installation.
"""

from __future__ import annotations

import re
import zipfile
from html.parser import HTMLParser
from typing import Any, NoReturn

from plugins_market.core.errors import PublishError
from plugins_market.validation.constants import (
    APP_ALLOWED_PERMISSIONS,
    APP_HTML_HEAD_SCAN_BYTES,
    APP_SCRIPT_SCAN_BYTES,
    MAX_APP_PACKAGE_NAME,
    MAX_APP_SINGLE_FILE_SIZE,
    MAX_APP_SOURCE_SIZE,
    RUNTIME_APP,
)
from plugins_market.validation.content_security import (
    SCRIPT_FILE_EXTENSIONS,
    find_dangerous_script_content,
)
from plugins_market.validation.types.agent_asset import AgentAssetOuterRef
from plugins_market.validation.types.wrapped_asset import (
    validate_wrapped_outer_layout,
)
from plugins_market.validation.zip_utils import (
    DecompressCounter,
    safe_read_zip_member,
    validate_png_icon_bytes,
)

_SERVER_SCRIPT = "scripts/app_server.py"
_ENTRY_FILE = "index.html"
_README_FILE = "README.md"
_RESERVED_PAYLOAD_ROOTS = frozenset({"data", ".git", ".workswarm-app-release"})
_RESERVED_SCRIPTS = frozenset({
    "scripts/start.ps1", "scripts/start.sh", "scripts/stop.ps1", "scripts/stop.sh",
})
_WINDOWS_DEVICE_NAME = re.compile(r"^(?:con|prn|aux|nul|(?:com|lpt)[1-9])$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
_WINDOWS_INVALID_CHARS = frozenset('<>:"|?*')


def _portable_path_problem(path: str) -> str | None:
    """Return why a member path cannot be installed on Windows, if so."""
    if len(path.split("/")) > 32:
        return "路径超过 32 层"
    for segment in path.split("/"):
        if not segment:
            return "包含空路径段"
        if segment != segment.rstrip(" ."):
            return "路径段以空格或点结尾"
        if any(char in _WINDOWS_INVALID_CHARS or ord(char) < 32 for char in segment):
            return "包含 Windows 非法字符"
        if segment.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES:
            return "使用 Windows 保留设备名"
    return None


def _validate_portable_paths(members: dict[str, str]) -> None:
    """Reject paths or case-folded collisions that fail on a Windows install.

    Hub and Swarm both execute this contract so a package accepted here cannot
    fail to extract (or silently overwrite a sibling) on the install machine.
    """
    entries: dict[str, tuple[str, bool]] = {}
    for normalized, original in members.items():
        problem = _portable_path_problem(normalized)
        if problem:
            _invalid("invalid_plugin_structure", f"应用包路径 {normalized!r} 不可跨平台安装：{problem}")
        parts = normalized.split("/")
        for depth in range(1, len(parts) + 1):
            path = "/".join(parts[:depth])
            directory = depth < len(parts) or original.endswith(("/", "\\"))
            identity = (path, directory)
            if entries.setdefault(path.casefold(), identity) != identity:
                _invalid("invalid_plugin_structure", f"应用包目录或文件路径冲突：{path!r}")


def _invalid(error: str, message: str) -> NoReturn:
    raise PublishError(
        code=400,
        error=error,
        message=message,
        error_code="SKILLHUB_PLUGIN_STRUCTURE_INVALID",
        error_class="validation",
    )


def _member_exists(members: dict[str, str], path: str) -> bool:
    original = members.get(path)
    return original is not None and not original.replace("\\", "/").endswith("/")


def _read_member_prefix(
    zf: zipfile.ZipFile,
    path: str,
    limit: int,
    counter: DecompressCounter,
) -> bytes:
    """Read at most *limit* decompressed bytes from one member.

    Large front-end bundles are intentionally not read in full; the promised
    scan budget is a prefix, and the byte counter still bounds total work.
    """
    with zf.open(path) as stream:
        data = stream.read(max(1, int(limit)))
    counter.add(len(data))
    return data


class _HeadMetaParser(HTMLParser):
    """Extract ``<meta>`` values declared before the document body.

    HTML5 allows omitting ``<head>``; collecting meta tags until ``<body>``
    starts keeps version checks effective for documents that have no explicit
    head element.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self._in_head = False
        self._in_body = False
        self.head_complete = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "head":
            self._in_head = True
            return
        if lowered == "body":
            self.head_complete = True
            self._in_head = False
            self._in_body = True
            return
        if lowered != "meta" or self._in_body:
            return
        values = {str(key).lower(): (value or "") for key, value in attrs}
        name = values.get("name", "").strip().lower()
        if name:
            self.meta[name] = values.get("content", "").strip()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "head":
            self.head_complete = True
            self._in_head = False


def _declared_source_size(zf: zipfile.ZipFile) -> int:
    """Sum declared sizes of non-directory entries, i.e. the extracted payload."""
    total = 0
    for info in zf.infolist():
        if info.filename.replace("\\", "/").endswith("/"):
            continue
        if info.file_size > MAX_APP_SINGLE_FILE_SIZE:
            _invalid(
                "invalid_plugin_structure",
                f"应用包文件 {info.filename!r} 超过单文件大小上限 "
                f"{MAX_APP_SINGLE_FILE_SIZE // (1024 * 1024)} MB",
            )
        total += int(info.file_size or 0)
    return total


def _validate_source_size(zf: zipfile.ZipFile) -> int:
    source_size = _declared_source_size(zf)
    if source_size > MAX_APP_SOURCE_SIZE:
        raise PublishError(
            code=413,
            error="file_too_large",
            message=(
                "应用源文件总大小超过上限"
                f"（最大 {MAX_APP_SOURCE_SIZE // (1024 * 1024)} MB）"
            ),
            error_code="SKILLHUB_PLUGIN_FILE_TOO_LARGE",
            error_class="validation",
        )
    return source_size


def _validate_entry_version(
    zf: zipfile.ZipFile,
    members: dict[str, str],
    entry_path: str,
    outer_version: str,
    counter: DecompressCounter,
) -> None:
    """Reject a package whose HTML declares a different application version."""
    raw = _read_member_prefix(zf, members[entry_path], APP_HTML_HEAD_SCAN_BYTES, counter)
    parser = _HeadMetaParser()
    try:
        parser.feed(raw.decode("utf-8", errors="replace"))
    except (ValueError, AssertionError):
        _invalid("invalid_plugin_structure", "无法解析应用入口 HTML")
    if not parser.head_complete and zf.getinfo(members[entry_path]).file_size > len(raw):
        _invalid("invalid_plugin_structure", "入口 HTML 的 head 超过 64 KiB 解析上限")
    declared = parser.meta.get("application-version", "").strip()
    if not declared:
        return
    if declared != str(outer_version or "").strip():
        raise PublishError(
            code=400,
            error="app_version_mismatch",
            message=(
                "应用入口 index.html 的 application-version "
                f"({declared!r}) 与 plugin.yaml.version ({outer_version!r}) 不一致"
            ),
            error_code="SKILLHUB_PLUGIN_CONFIG_INVALID",
            error_class="validation",
        )


def _scan_executable_scripts(
    zf: zipfile.ZipFile,
    members: dict[str, str],
    payload_prefix: str,
    counter: DecompressCounter,
) -> list[tuple[str, str]]:
    """Scan only ``scripts/`` executables with a bounded per-file prefix."""
    hits: list[tuple[str, str]] = []
    for normalized, original in sorted(members.items()):
        if not normalized.startswith(payload_prefix):
            continue
        if original.replace("\\", "/").endswith("/"):
            continue
        relative = normalized[len(payload_prefix):]
        if not relative.startswith("scripts/"):
            continue
        if not relative.lower().endswith(SCRIPT_FILE_EXTENSIONS):
            continue
        info = zf.getinfo(original)
        if int(info.file_size or 0) > APP_SCRIPT_SCAN_BYTES:
            _invalid(
                "invalid_plugin_structure",
                f"应用脚本 {relative!r} 超过 {APP_SCRIPT_SCAN_BYTES // 1024} KiB 扫描上限",
            )
        raw = _read_member_prefix(
            zf, original, APP_SCRIPT_SCAN_BYTES, counter
        )
        reason = find_dangerous_script_content(
            raw.decode("utf-8", errors="replace"),
            filename=relative,
        )
        if reason:
            hits.append((relative, reason))
    return hits


def _raise_dangerous(hits: list[tuple[str, str]]) -> None:
    paths = [path for path, _ in hits]
    reasons = {reason for _, reason in hits}
    path_msg = ", ".join(paths[:5])
    if len(paths) > 5:
        path_msg += f" 等{len(paths)}处"
    reason_msg = next(iter(reasons)) if len(reasons) == 1 else "危险脚本内容"
    raise PublishError(
        code=400,
        error="dangerous_content",
        message=f"{path_msg} 包含危险脚本内容（{reason_msg}）",
        error_code="SKILLHUB_DANGEROUS_CONTENT",
        error_class="validation",
    )


def _app_options(metadata: dict[str, Any]) -> tuple[bool, list[str]]:
    """Read and validate optional ``metadata.app`` capability declarations."""
    raw = metadata.get("app")
    if raw is None:
        return False, []
    if not isinstance(raw, dict):
        _invalid("invalid_plugin_config", "plugin.yaml 中 metadata.app 必须为对象")

    requires_project = raw.get("requires_project", False)
    if not isinstance(requires_project, bool):
        _invalid("invalid_plugin_config", "metadata.app.requires_project 必须为布尔值")

    raw_permissions = raw.get("permissions", [])
    if not isinstance(raw_permissions, list):
        _invalid("invalid_plugin_config", "metadata.app.permissions 必须为数组")
    permissions: list[str] = []
    for index, item in enumerate(raw_permissions):
        if not isinstance(item, str) or not item.strip():
            _invalid(
                "invalid_plugin_config",
                f"metadata.app.permissions[{index}] 必须为非空字符串",
            )
        normalized = item.strip().lower()
        if normalized not in APP_ALLOWED_PERMISSIONS:
            _invalid(
                "invalid_plugin_config",
                f"不支持的 metadata.app.permissions[{index}]: {item!r}"
                f"（支持 {', '.join(sorted(APP_ALLOWED_PERMISSIONS))}）",
            )
        if normalized not in permissions:
            permissions.append(normalized)
    return requires_project, permissions


def validate_app_asset_layout(
    zf: zipfile.ZipFile,
    outer_ref: AgentAssetOuterRef,
    counter: DecompressCounter,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one wrapped application package and return market metadata."""
    prefix = outer_ref.prefix
    asset_name = outer_ref.name
    if len(asset_name) > MAX_APP_PACKAGE_NAME:
        _invalid("invalid_plugin_structure", f"应用包名称长度不得超过 {MAX_APP_PACKAGE_NAME} 个字符")
    if _WINDOWS_DEVICE_NAME.fullmatch(asset_name):
        _invalid("invalid_plugin_structure", f"应用包名称不能使用保留目录名 {asset_name!r}")
    members = validate_wrapped_outer_layout(zf, prefix, asset_name)
    _validate_portable_paths(members)
    outer = prefix.rstrip("/")
    payload_prefix = f"{outer}/{asset_name}/" if outer else f"{asset_name}/"
    for path, original in members.items():
        if path.startswith(payload_prefix):
            relative = path[len(payload_prefix):]
            parts = relative.split("/", 2)
            top_level = parts[0]
            reserved_script = "/".join(parts[:2]).casefold() in _RESERVED_SCRIPTS
            if top_level.casefold() in _RESERVED_PAYLOAD_ROOTS or reserved_script:
                _invalid("invalid_plugin_structure", f"应用包包含保留路径 {relative!r}")
            if top_level.casefold() == "scripts":
                is_scripts_file = relative == "scripts" and not original.endswith(("/", "\\"))
                if top_level != "scripts" or is_scripts_file:
                    _invalid("invalid_plugin_structure", "应用脚本必须放在 scripts/ 目录")

    source_size = _validate_source_size(zf)

    entry_path = f"{payload_prefix}{_ENTRY_FILE}"
    if not _member_exists(members, entry_path):
        _invalid(
            "invalid_plugin_structure",
            f"应用缺少入口文件 {entry_path}",
        )
    _validate_entry_version(
        zf, members, entry_path, outer_ref.version, counter
    )

    hits = _scan_executable_scripts(zf, members, payload_prefix, counter)
    if hits:
        _raise_dangerous(hits)

    detail_desc = ""
    readme_path = f"{payload_prefix}{_README_FILE}"
    if _member_exists(members, readme_path):
        raw = safe_read_zip_member(zf, members[readme_path], counter)
        detail_desc = raw.decode("utf-8", errors="replace")

    icon_bytes = b""
    icon_path = f"{outer}/icon.png" if outer else "icon.png"
    if _member_exists(members, icon_path):
        icon_bytes = safe_read_zip_member(zf, members[icon_path], counter)
        validate_png_icon_bytes(icon_bytes, path=icon_path)

    requires_project, permissions = _app_options(metadata or {})
    has_server_script = _member_exists(members, f"{payload_prefix}{_SERVER_SCRIPT}")

    return {
        "asset_type": RUNTIME_APP,
        # Hub market display fields come from plugin.yaml; keep these empty so
        # the pipeline merge does not overwrite public fields.
        "display_name": "",
        "short_desc": "",
        "tags": [],
        "detail_desc": detail_desc,
        "icon_bytes": icon_bytes,
        "source_size": source_size,
        "requires_project": requires_project,
        "permissions": permissions,
        "has_server_script": has_server_script,
    }


__all__ = ["validate_app_asset_layout"]
