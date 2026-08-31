# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validation for independently wrapped JiuwenSwarm MCP packages."""

from __future__ import annotations

import json
import re
import urllib.parse
import zipfile
from typing import Any, NoReturn

from plugins_market.core.errors import PublishError
from plugins_market.validation.constants import MAX_JSON_BYTES, RUNTIME_AGENT_MCP
from plugins_market.validation.content_security import (
    find_dangerous_command,
    find_dangerous_zip_script,
)
from plugins_market.validation.types.skill import parse_skill_frontmatter, validate_skill_frontmatter
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


def _invalid(message: str) -> NoReturn:
    raise PublishError(
        code=400,
        error="invalid_agent_mcp",
        message=message,
        error_code="SKILLHUB_AGENT_MCP_VALIDATION_FAILED",
        error_class="validation",
    )


def _dangerous(message: str) -> NoReturn:
    """危险内容：单独错误码，便于调用方与安全审计区分结构错误。"""
    raise PublishError(
        code=400,
        error="dangerous_content",
        message=message,
        error_class="validation",
    )


def _scan_command_string(command: str, label: str) -> None:
    reason = find_dangerous_command(command)
    if reason:
        _dangerous(f"{label} 包含危险命令（{reason}）")


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


def _validate_mcp(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
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
        # servers 非空已在上文校验，此处仅为类型收窄的防御分支
        _invalid("mcp.json.mcpServers 必须为非空对象")
    if isinstance(first.get("command"), str) and first["command"].strip():
        return "stdio-mcp", first
    if isinstance(first.get("url"), str) and first["url"].strip():
        return "remote-mcp", first
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


def _validate_bundled_skills(
    zf: zipfile.ZipFile,
    members: dict[str, str],
    payload_prefix: str,
    counter: DecompressCounter,
    *,
    has_mcp: bool,
) -> int:
    count = 0
    for normalized, original in sorted(members.items()):
        if not normalized.startswith(payload_prefix) or not normalized.endswith("/SKILL.md"):
            continue
        relative = normalized[len(payload_prefix):]
        parts = relative.split("/")
        is_flat = parts == ["skills", "SKILL.md"]
        is_nested = len(parts) == 3 and parts[0] == "skills" and parts[-1] == "SKILL.md"
        is_special = parts == ["skill", "SKILL.md"]
        if not (is_flat or is_nested or is_special):
            continue
        if is_special and not has_mcp:
            _invalid("skill/SKILL.md 仅允许作为带 mcp.json 包的附属 Skill")
        raw = safe_read_zip_member(zf, original, counter)
        fm, _ = parse_skill_frontmatter(raw)
        fm_name = fm.get("name")
        expected_name = parts[1] if is_nested else fm_name
        if not isinstance(expected_name, str):
            _invalid(f"{relative} frontmatter name 必填")
        validate_skill_frontmatter(fm, dir_name=expected_name, yaml_name=expected_name)
        if not is_special:
            count += 1
    return count


def _validate_payload_scripts(
    zf: zipfile.ZipFile,
    members: dict[str, str],
    payload_prefix: str,
    counter: DecompressCounter,
) -> None:
    """扫描内层脚本文件（.py/.sh 等）中的危险执行内容。"""
    hit = find_dangerous_zip_script(zf, members, payload_prefix, counter)
    if hit:
        _dangerous(f"{hit[0]} 包含危险脚本内容（{hit[1]}）")


def validate_agent_mcp_layout(
    zf: zipfile.ZipFile,
    prefix: str,
    asset_name: str,
    counter: DecompressCounter,
) -> dict[str, Any]:
    """Validate one native MCP payload and derive its runtime integration form."""
    members = validate_wrapped_outer_layout(zf, prefix, asset_name)
    outer = prefix.rstrip("/")
    payload_prefix = f"{outer}/{asset_name}/"

    mcp_path = f"{payload_prefix}mcp.json"
    cli_path = f"{payload_prefix}cli.json"
    token_path = f"{payload_prefix}token-schema.json"
    has_mcp = mcp_path in members
    has_cli = cli_path in members

    mcp_data: dict[str, Any] | None = None
    cli_data: dict[str, Any] | None = None
    mcp_integration: str | None = None
    if has_mcp:
        mcp_data = _read_json(zf, members[mcp_path], counter, "mcp.json")
        mcp_integration, _ = _validate_mcp(mcp_data)

    if has_cli:
        cli_data = _read_json(zf, members[cli_path], counter, "cli.json")
        _validate_cli(cli_data)

    skill_count = _validate_bundled_skills(
        zf, members, payload_prefix, counter, has_mcp=has_mcp
    )
    _validate_payload_scripts(zf, members, payload_prefix, counter)
    if has_cli:
        integration_type = "cli"
    elif mcp_integration is not None:
        integration_type = mcp_integration
    elif skill_count:
        integration_type = "skill-only"
    else:
        _invalid("agent-mcp 内层缺少可用入口：mcp.json、cli.json 或 skills/**/SKILL.md")

    schema_keys: set[str] = set()
    if token_path in members:
        schema = _read_json(zf, members[token_path], counter, "token-schema.json")
        schema_keys = _validate_token_schema(schema)
    placeholders: set[str] = set()
    if mcp_data is not None:
        placeholders.update(_collect_placeholders(mcp_data))
    if cli_data is not None:
        placeholders.update(_collect_placeholders(cli_data))
    missing = sorted(placeholders - schema_keys)
    if missing:
        _invalid(f"agent-mcp 凭据占位符没有 token schema 录入项：{', '.join(missing)}")

    readme_path = f"{payload_prefix}README.md"
    detail_desc = ""
    if readme_path in members:
        detail_desc = safe_read_zip_member(zf, members[readme_path], counter).decode(
            "utf-8", errors="replace"
        )

    outer_icon = f"{outer}/icon.png"
    icon_bytes = b""
    if outer_icon in members:
        icon_bytes = safe_read_zip_member(zf, members[outer_icon], counter)
        validate_png_icon_bytes(icon_bytes, path=outer_icon)

    return {
        "asset_type": RUNTIME_AGENT_MCP,
        "integration_type": integration_type,
        "detail_desc": detail_desc,
        "icon_bytes": icon_bytes,
    }
