# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validation for JiuwenSwarm MCP packages (manifest.json driven)."""

from __future__ import annotations

import json
import posixpath
import re
import urllib.parse
import zipfile
from typing import Any, NoReturn

from plugins_market.core.errors import PublishError
from plugins_market.validation.constants import (
    DISPLAY_NAME_MAX_LEN,
    MAX_JSON_BYTES,
    PLUGIN_TAGS_MAX_COUNT,
    PLUGIN_TAG_MAX_LEN,
    PLUGIN_YAML_DESCRIPTION_MAX_LEN,
    RUNTIME_AGENT_MCP,
)
from plugins_market.validation.content_security import find_dangerous_zip_script
from plugins_market.validation.localized_manifest import (
    localized_manifest_tags,
    localized_manifest_text,
)
from plugins_market.validation.types.wrapped_asset import validate_wrapped_outer_layout
from plugins_market.validation.zip_utils import DecompressCounter, safe_read_zip_member, validate_png_icon_bytes


_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_PLATFORMS = ("darwin", "linux", "win32")
_TRANSPORTS = {
    "",
    "streamablehttp",
    "streamable-http",
    "streamable_http",
    "http",
    "sse",
    "stdio",
}
_SECRET_KEY_RE = re.compile(r"(?:token|secret|password|api[_-]?key|authorization|credential)", re.I)
_INTEGRATION_TYPES = frozenset({"stdio-mcp", "remote-mcp", "cli", "skill-only"})


def _invalid(message: str) -> NoReturn:
    raise PublishError(
        code=400,
        error="invalid_agent_mcp",
        message=message,
        error_code="SKILLHUB_AGENT_MCP_VALIDATION_FAILED",
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


def _required_string(data: dict[str, Any], field: str) -> str:
    value: Any = data
    for part in field.split("."):
        if not isinstance(value, dict):
            value = None
            break
        value = value.get(part)
    if not isinstance(value, str) or not value.strip():
        _invalid(f"manifest.{field} 必填且必须为非空字符串")
    return value.strip()


def _safe_relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(f"manifest.{field} 必须为非空相对路径")
    raw = value.strip().replace("\\", "/")
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
        _invalid(f"manifest.{field} 不得使用绝对路径")
    if ".." in raw.split("/"):
        _invalid(f"manifest.{field} 不得包含 '..' 路径段")
    normalized = posixpath.normpath(raw)
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        _invalid(f"manifest.{field} 不得越出内层资产目录")
    return normalized.removeprefix("./")


def _member_exists(members: dict[str, str], path: str) -> bool:
    original = members.get(path)
    return original is not None and not original.replace("\\", "/").endswith("/")


def _read_manifest(
    zf: zipfile.ZipFile,
    original_path: str,
    counter: DecompressCounter,
) -> dict[str, Any]:
    raw = safe_read_zip_member(zf, original_path, counter)
    if len(raw) > MAX_JSON_BYTES:
        _invalid(f"manifest.json 超过大小上限（最大 {MAX_JSON_BYTES // 1024 // 1024} MB）")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _invalid(f"manifest.json 不是合法 UTF-8 JSON：{exc}")
    if not isinstance(value, dict):
        _invalid("manifest.json 根结构必须为对象")
    return value


def _read_json(
    zf: zipfile.ZipFile,
    original_path: str,
    counter: DecompressCounter,
    label: str,
) -> dict[str, Any]:
    raw = safe_read_zip_member(zf, original_path, counter)
    if len(raw) > MAX_JSON_BYTES:
        _invalid(f"{label} 超过大小上限")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _invalid(f"{label} 不是合法 UTF-8 JSON：{exc}")
    if not isinstance(value, dict):
        _invalid(f"{label} 根结构必须为对象")
    return value


def _scan_command_string(command: str, label: str) -> None:
    from plugins_market.validation.content_security import find_dangerous_command

    reason = find_dangerous_command(command)
    if reason:
        _dangerous(f"{label} 包含危险命令（{reason}）")


def _validate_platform_commands(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        _invalid(f"{label} 必须为平台命令对象")
    for platform in _PLATFORMS:
        command = value.get(platform)
        if not isinstance(command, str) or not command.strip():
            _invalid(f"{label}.{platform} 必须为非空字符串")
        if "\x00" in command or "\n" in command or "\r" in command:
            _invalid(f"{label}.{platform} 包含不安全的控制字符")
        _scan_command_string(command, f"{label}.{platform}")


def _validate_command(value: Any, label: str) -> None:
    if isinstance(value, str):
        has_control_chars = any(ch in value for ch in ("\x00", "\n", "\r"))
        if not value.strip() or has_control_chars:
            _invalid(f"{label} 必须为不含控制字符的非空字符串")
        _scan_command_string(value, label)
        return
    _validate_platform_commands(value, label)


def _validate_cli(data: dict[str, Any]) -> None:
    _validate_platform_commands(data.get("init"), "cli.json.init")
    version_check = data.get("versionCheck")
    if not isinstance(version_check, dict):
        _invalid("cli.json.versionCheck 必须为对象")
    _validate_platform_commands(version_check.get("command"), "cli.json.versionCheck.command")
    min_version = version_check.get("minVersion")
    if not isinstance(min_version, str) or not min_version.strip():
        _invalid("cli.json.versionCheck.minVersion 必须为非空字符串")
    _validate_platform_commands(data.get("status"), "cli.json.status")

    auth = data.get("auth", [])
    if not isinstance(auth, (dict, list)):
        _invalid("cli.json.auth 必须为对象或数组")
    if isinstance(auth, dict) and all(platform in auth for platform in _PLATFORMS):
        _validate_platform_commands(auth, "cli.json.auth")
        auth_steps: list[Any] = []
    else:
        auth_steps = [auth] if isinstance(auth, dict) else auth
    for index, step in enumerate(auth_steps):
        if not isinstance(step, dict):
            _invalid(f"cli.json.auth[{index}] 必须为对象")
        if "command" in step:
            _validate_command(step.get("command"), f"cli.json.auth[{index}].command")
        elif step:
            _invalid(f"cli.json.auth[{index}].command 必填")

    status_match = data.get("statusMatch")
    status_match_json = data.get("statusMatchJson")
    has_regex = isinstance(status_match, str) and bool(status_match.strip())
    has_json = isinstance(status_match_json, dict) and bool(status_match_json)
    if not has_regex and not has_json:
        _invalid("cli.json 必须提供 statusMatch 或 statusMatchJson")


def _validate_string_map(value: Any, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        _invalid(f"{label} 必须为对象")
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(item, str):
            _invalid(f"{label} 的键和值必须为字符串")
        if _SECRET_KEY_RE.search(key) and item.strip() and not _PLACEHOLDER_RE.search(item):
            _invalid(f"{label}.{key} 禁止硬编码凭据，必须使用 ${{VAR}} 占位符")


def _validate_mcp(data: dict[str, Any]) -> str:
    """Return inferred integration type from mcp.json first server."""
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        _invalid("mcp.json.mcpServers 必须为非空对象")

    first: dict[str, Any] | None = None
    for server_name, config in servers.items():
        if not isinstance(server_name, str) or not server_name.strip() or not isinstance(config, dict):
            _invalid("mcp.json.mcpServers 的名称必须非空且配置必须为对象")
        if first is None:
            first = config

        command = config.get("command")
        url = config.get("url")
        if command is not None and (not isinstance(command, str) or not command.strip()):
            _invalid(f"mcp server {server_name!r} 的 command 必须为非空字符串")
        if isinstance(command, str):
            _scan_command_string(command, f"mcpServers.{server_name}.command")
        if command is None:
            if not isinstance(url, str) or not url.strip():
                _invalid(f"mcp server {server_name!r} 必须提供 command 或 url")
            parsed = urllib.parse.urlparse(url.strip())
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                _invalid(f"mcp server {server_name!r} 的 url 必须为 http/https URL")

        args = config.get("args")
        if args is not None and not isinstance(args, list):
            _invalid(f"mcp server {server_name!r} 的 args 必须为数组")
        if isinstance(args, list) and any(not isinstance(item, (str, int, float, bool)) for item in args):
            _invalid(f"mcp server {server_name!r} 的 args 只能包含标量值")
        if isinstance(args, list):
            for arg_index, arg in enumerate(args):
                if isinstance(arg, str):
                    _scan_command_string(arg, f"mcpServers.{server_name}.args[{arg_index}]")
        _validate_string_map(config.get("env"), f"mcpServers.{server_name}.env")
        _validate_string_map(config.get("headers"), f"mcpServers.{server_name}.headers")

        transport = config.get("type", "")
        if transport is not None and (not isinstance(transport, str) or transport.strip().lower() not in _TRANSPORTS):
            _invalid(f"mcp server {server_name!r} 的 type 不受支持")
        timeout = config.get("timeout")
        timeout_invalid = (
            not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0
        )
        if timeout is not None and timeout_invalid:
            _invalid(f"mcp server {server_name!r} 的 timeout 必须为正数毫秒")

    if first is None:
        _invalid("mcp.json.mcpServers 必须为非空对象")
    if isinstance(first.get("command"), str) and first["command"].strip():
        return "stdio-mcp"
    if isinstance(first.get("url"), str) and first["url"].strip():
        return "remote-mcp"
    _invalid("mcp.json 首个 server 没有可用的 command 或 url")


def _collect_placeholders(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, str):
        result.update(_PLACEHOLDER_RE.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            result.update(_collect_placeholders(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_collect_placeholders(item))
    return result


def _validate_token_schema(data: dict[str, Any]) -> set[str]:
    fields = data.get("fields")
    if not isinstance(fields, list):
        _invalid("token-schema.json.fields 必须为数组")
    keys: set[str] = set()
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            _invalid(f"token-schema.json.fields[{index}] 必须为对象")
        key = field.get("key")
        if not isinstance(key, str) or not _PLACEHOLDER_RE.fullmatch("${" + key.strip() + "}"):
            _invalid(f"token-schema.json.fields[{index}].key 格式无效")
        key = key.strip()
        if key in keys:
            _invalid(f"token-schema.json.fields[].key 重复：{key}")
        keys.add(key)
        for forbidden in ("value", "default", "defaultValue"):
            if field.get(forbidden) not in (None, ""):
                _invalid(f"token-schema.json 不得保存凭据值：fields[{index}].{forbidden}")
    return keys


def _validate_market_fields(display_name: str, short_desc: str, tags: list[str]) -> None:
    if len(display_name) > DISPLAY_NAME_MAX_LEN:
        _invalid(f"内层 manifest 派生的展示名不得超过 {DISPLAY_NAME_MAX_LEN} 个字符")
    if len(short_desc) > PLUGIN_YAML_DESCRIPTION_MAX_LEN:
        _invalid(f"内层 manifest 派生的描述不得超过 {PLUGIN_YAML_DESCRIPTION_MAX_LEN} 个字符")
    if len(tags) > PLUGIN_TAGS_MAX_COUNT:
        _invalid(f"内层 manifest 派生的标签不得超过 {PLUGIN_TAGS_MAX_COUNT} 个")
    for index, tag in enumerate(tags):
        if len(tag) > PLUGIN_TAG_MAX_LEN:
            _invalid(f"内层 manifest.tags[{index}] 派生值不得超过 {PLUGIN_TAG_MAX_LEN} 个字符")


def _validate_declared_skills(
    zf: zipfile.ZipFile,
    members: dict[str, str],
    payload_prefix: str,
    skills: Any,
    counter: DecompressCounter,
) -> int:
    if skills is None:
        return 0
    if not isinstance(skills, list):
        _invalid("manifest.skills 必须为数组")
    count = 0
    for index, item in enumerate(skills):
        if not isinstance(item, dict):
            _invalid(f"manifest.skills[{index}] 必须为对象")
        relative = _safe_relative_path(item.get("dir"), f"skills[{index}].dir")
        skill_name = posixpath.basename(relative.rstrip("/"))
        skill_path = f"{payload_prefix}{relative}/SKILL.md"
        flat_path = f"{payload_prefix}{relative.rstrip('/')}"
        if relative.rstrip("/").endswith("SKILL.md") or relative == "SKILL.md":
            _invalid(f"manifest.skills[{index}].dir 应指向 Skill 目录而非 SKILL.md 文件")
        original = members.get(skill_path)
        if original is None and flat_path.endswith("/SKILL.md"):
            original = members.get(flat_path)
        if original is None and relative == "skills":
            flat_skill = f"{payload_prefix}skills/SKILL.md"
            original = members.get(flat_skill)
            if original is not None:
                skill_path = flat_skill
                skill_name = "skills"
        if original is None:
            _invalid(f"manifest.skills[{index}] 声明的目录缺少 SKILL.md：{relative}")
        count += 1
    return count


def _count_skill_only_md(
    members: dict[str, str],
    payload_prefix: str,
) -> int:
    count = 0
    for normalized in members:
        if not normalized.startswith(payload_prefix) or not normalized.endswith("/SKILL.md"):
            continue
        relative = normalized[len(payload_prefix):]
        parts = relative.split("/")
        is_flat = parts == ["skills", "SKILL.md"]
        is_nested = len(parts) == 3 and parts[0] == "skills" and parts[-1] == "SKILL.md"
        if is_flat or is_nested:
            count += 1
    return count


def _validate_payload_scripts(
    zf: zipfile.ZipFile,
    members: dict[str, str],
    payload_prefix: str,
    counter: DecompressCounter,
) -> None:
    hit = find_dangerous_zip_script(zf, members, payload_prefix, counter)
    if hit:
        _dangerous(f"{hit[0]} 包含危险脚本内容（{hit[1]}）")


def _scan_mcp_json_security(
    zf: zipfile.ZipFile,
    members: dict[str, str],
    payload_prefix: str,
    mcp_relative: str,
    counter: DecompressCounter,
) -> None:
    from plugins_market.validation.content_security import find_dangerous_mcp_json

    original = members.get(f"{payload_prefix}{mcp_relative}")
    if original is None:
        return
    raw = safe_read_zip_member(zf, original, counter)
    reason = find_dangerous_mcp_json(raw, label=mcp_relative)
    if reason:
        _dangerous(f"{mcp_relative} 包含危险命令（{reason}）")


def validate_agent_mcp_layout(
    zf: zipfile.ZipFile,
    prefix: str,
    asset_name: str,
    counter: DecompressCounter,
) -> dict[str, Any]:
    """Validate one MCP payload declared by manifest.json."""
    members = validate_wrapped_outer_layout(zf, prefix, asset_name)
    outer = prefix.rstrip("/")
    payload_prefix = f"{outer}/{asset_name}/"
    manifest_path = f"{payload_prefix}manifest.json"
    manifest_original = members.get(manifest_path)
    if manifest_original is None or not _member_exists(members, manifest_path):
        _invalid(f"内层目录 {asset_name!r} 缺少 manifest.json")

    manifest = _read_manifest(zf, manifest_original, counter)
    package_type = _required_string(manifest, "package_type")
    if package_type != "mcp":
        _invalid(f"manifest.package_type 必须为 'mcp'，实际为 {package_type!r}")

    identity = _required_string(manifest, "id")
    if identity != asset_name:
        _invalid(
            f"manifest.id {identity!r} 必须与 plugin.yaml.name {asset_name!r} 一致"
        )
    version = _required_string(manifest, "version")
    name = _required_string(manifest, "name")
    description = _required_string(manifest, "description")

    integration = manifest.get("integration")
    if not isinstance(integration, dict):
        _invalid("manifest.integration 必须为对象")
    integration_type = integration.get("type")
    if not isinstance(integration_type, str) or integration_type.strip() not in _INTEGRATION_TYPES:
        _invalid(
            "manifest.integration.type 必须为 stdio-mcp、remote-mcp、cli 或 skill-only"
        )
    integration_type = integration_type.strip()

    integration_file: str | None = None
    if integration_type in ("stdio-mcp", "remote-mcp", "cli"):
        integration_file = _safe_relative_path(integration.get("file"), "integration.file")
        if not _member_exists(members, f"{payload_prefix}{integration_file}"):
            _invalid(f"manifest.integration.file 指向的文件不存在：{integration_file}")
    elif integration.get("file") not in (None, ""):
        _invalid("manifest.integration.file 仅适用于 stdio-mcp、remote-mcp 或 cli")

    credentials_type: str | None = None
    credentials = manifest.get("credentials")
    token_schema_keys: set[str] = set()
    if credentials is not None:
        if not isinstance(credentials, dict):
            _invalid("manifest.credentials 必须为对象")
        cred_type = credentials.get("type")
        if not isinstance(cred_type, str) or not cred_type.strip():
            _invalid("manifest.credentials.type 必填且必须为非空字符串")
        credentials_type = cred_type.strip()
        cred_file = credentials.get("file")
        if credentials_type == "token":
            token_relative = _safe_relative_path(cred_file, "credentials.file")
            token_path = f"{payload_prefix}{token_relative}"
            if not _member_exists(members, token_path):
                _invalid(f"manifest.credentials.file 指向的文件不存在：{token_relative}")
            schema = _read_json(zf, members[token_path], counter, "token-schema.json")
            token_schema_keys = _validate_token_schema(schema)
        elif cred_file not in (None, ""):
            _invalid("manifest.credentials.file 仅适用于 credentials.type=token")

    icon_bytes = b""
    icon_field = manifest.get("icon")
    if isinstance(icon_field, str) and icon_field.strip():
        icon_relative = _safe_relative_path(icon_field, "icon")
        icon_member = f"{payload_prefix}{icon_relative}"
        if not _member_exists(members, icon_member):
            _invalid(f"manifest.icon 指向的文件不存在：{icon_relative}")
        raw_icon = safe_read_zip_member(zf, members[icon_member], counter)
        if icon_relative.lower().endswith(".png"):
            validate_png_icon_bytes(raw_icon, path=icon_member)
        icon_bytes = raw_icon

    skill_count = _validate_declared_skills(
        zf, members, payload_prefix, manifest.get("skills"), counter
    )

    mcp_data: dict[str, Any] | None = None
    cli_data: dict[str, Any] | None = None
    placeholders: set[str] = set()

    if integration_type in ("stdio-mcp", "remote-mcp"):
        assert integration_file is not None
        mcp_data = _read_json(
            zf,
            members[f"{payload_prefix}{integration_file}"],
            counter,
            integration_file,
        )
        inferred = _validate_mcp(mcp_data)
        if inferred != integration_type:
            _invalid(
                f"manifest.integration.type 为 {integration_type!r}，"
                f"但 {integration_file} 内容对应 {inferred!r}"
            )
        _scan_mcp_json_security(zf, members, payload_prefix, integration_file, counter)
        placeholders.update(_collect_placeholders(mcp_data))
    elif integration_type == "cli":
        assert integration_file is not None
        if not integration_file.lower().endswith("cli.json"):
            _invalid("manifest.integration.file 在 cli 类型下应指向 cli.json")
        cli_data = _read_json(
            zf,
            members[f"{payload_prefix}{integration_file}"],
            counter,
            integration_file,
        )
        _validate_cli(cli_data)
        placeholders.update(_collect_placeholders(cli_data))
    elif integration_type == "skill-only":
        if skill_count == 0:
            skill_count = _count_skill_only_md(members, payload_prefix)
        if skill_count < 1:
            _invalid("skill-only MCP 至少必须包含一个 skills/**/SKILL.md")

    if placeholders and credentials_type != "cli-oauth":
        missing = sorted(placeholders - token_schema_keys)
        if missing:
            _invalid(f"agent-mcp 凭据占位符没有 token schema 录入项：{', '.join(missing)}")

    _validate_payload_scripts(zf, members, payload_prefix, counter)

    readme_path = f"{payload_prefix}README.md"
    detail_desc = ""
    if _member_exists(members, readme_path):
        detail_desc = safe_read_zip_member(zf, members[readme_path], counter).decode(
            "utf-8", errors="replace"
        )

    if not icon_bytes:
        outer_icon = f"{outer}/icon.png"
        if outer_icon in members:
            icon_bytes = safe_read_zip_member(zf, members[outer_icon], counter)
            validate_png_icon_bytes(icon_bytes, path=outer_icon)

    display_name = localized_manifest_text(manifest.get("display_name")) or name
    short_desc = localized_manifest_text(manifest.get("display_description")) or description
    tags = localized_manifest_tags(manifest.get("tags"))
    _validate_market_fields(display_name or asset_name, short_desc, tags)

    return {
        "asset_type": RUNTIME_AGENT_MCP,
        "integration_type": integration_type,
        "credentials_type": credentials_type,
        "version": version,
        "display_name": display_name or asset_name,
        "short_desc": short_desc,
        "tags": tags,
        "detail_desc": detail_desc,
        "icon_bytes": icon_bytes,
    }
