# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""发布期静态内容安全扫描。

多资产（agent-plugin / agent-template / agent-mcp）由管理员发布、免审核，
但"免审核"不等于"免静态安全扫描"：结构合法的包仍可能携带命令注入、
远程脚本下载执行等危险内容。本模块提供保守、低误报的规则集：

- 命令字符串（mcp.json command/args/env/headers、cli.json）：下载即执行链等；
- 脚本文件（.py/.sh 等）：eval/exec、os.system、subprocess shell=True 等。

规则集有意保守，无法覆盖全部 RCE 变体；命中多条时一并汇总返回。
"""

from __future__ import annotations

import json
import re
from typing import Any

# 参与脚本内容扫描的扩展名（小写匹配）
SCRIPT_FILE_EXTENSIONS: tuple[str, ...] = (
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
)

# 下载后直接执行：curl/wget 管道进 shell，或 PowerShell 下载执行
_PIPE_TO_SHELL_RE = re.compile(
    r"\b(?:curl|wget|Invoke-WebRequest|iwr)\b[^\n|]*\|\s*(?:sudo\s+)?"
    r"(?:bash|sh|zsh|iex|Invoke-Expression)\b",
    re.IGNORECASE,
)
# sh -c "..." 包裹下载命令（未直接管道同样高危）
_SHELL_WRAPPED_DOWNLOAD_RE = re.compile(
    r"\b(?:bash|sh|zsh)\s+-c\s+[\"'][^\"']*\b(?:curl|wget|Invoke-WebRequest)\b",
    re.IGNORECASE,
)
_COMMAND_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_PIPE_TO_SHELL_RE, "下载后直接执行的命令链"),
    (_SHELL_WRAPPED_DOWNLOAD_RE, "shell 包裹的下载执行命令"),
)

_EVAL_RE = re.compile(r"\beval\s*\(")
_EXEC_RE = re.compile(r"\bexec\s*\(")

_OS_SYSTEM_RE = re.compile(r"\bos\.system\s*\(")
_OS_POPEN_RE = re.compile(r"\bos\.popen\s*\(")
_SUBPROCESS_SHELL_RE = re.compile(
    r"\bsubprocess\.(?:run|Popen|call|check_call|check_output|getoutput|getstatusoutput)"
    r"\s*\([\s\S]{0,800}?\bshell\s*=\s*True\b"
)
_SCRIPT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_PIPE_TO_SHELL_RE, "下载后直接执行的命令链"),
    (_SHELL_WRAPPED_DOWNLOAD_RE, "shell 包裹的下载执行命令"),
    (_EVAL_RE, "eval 动态代码执行"),
    (_EXEC_RE, "exec 动态代码执行"),
    (_OS_SYSTEM_RE, "os.system Shell 执行入口"),
    (_OS_POPEN_RE, "os.popen Shell 执行入口"),
    (_SUBPROCESS_SHELL_RE, "subprocess shell=True 执行入口"),
)


def find_dangerous_command(command: str) -> str | None:
    """命令字符串命中危险模式时返回原因描述，否则返回 None。"""
    for pattern, reason in _COMMAND_RULES:
        if pattern.search(command):
            return reason
    return None


def find_dangerous_script_content(text: str) -> str | None:
    """脚本内容命中危险模式时返回原因描述，否则返回 None。"""
    for pattern, reason in _SCRIPT_RULES:
        if pattern.search(text):
            return reason
    return None


def scan_mcp_servers_payload(servers: Any, *, label: str) -> str | None:
    """扫描 mcpServers 对象中的 command/args/env/headers，命中时返回原因。"""
    if not isinstance(servers, dict):
        return None
    for server_name, config in servers.items():
        if not isinstance(config, dict):
            continue
        command = config.get("command")
        if isinstance(command, str):
            reason = find_dangerous_command(command)
            if reason:
                return f"{label}.mcpServers.{server_name}.command（{reason}）"
        args = config.get("args")
        if isinstance(args, list):
            for index, arg in enumerate(args):
                if isinstance(arg, str):
                    reason = find_dangerous_command(arg)
                    if reason:
                        return f"{label}.mcpServers.{server_name}.args[{index}]（{reason}）"
        for map_label in ("env", "headers"):
            map_value = config.get(map_label)
            if not isinstance(map_value, dict):
                continue
            for key, item in map_value.items():
                if isinstance(item, str):
                    reason = find_dangerous_command(item)
                    if reason:
                        return (
                            f"{label}.mcpServers.{server_name}.{map_label}.{key}（{reason}）"
                        )
    return None


def find_dangerous_mcp_json(raw: bytes, *, label: str) -> str | None:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(value, dict):
        return scan_mcp_servers_payload(value.get("mcpServers"), label=label)
    return None


def _collect_dangerous_zip_scripts(
    zf,
    members: dict[str, str],
    payload_prefix: str,
    counter,
) -> list[tuple[str, str]]:
    from plugins_market.validation.zip_utils import safe_read_zip_member

    hits: list[tuple[str, str]] = []
    for normalized, original in sorted(members.items()):
        if not normalized.startswith(payload_prefix):
            continue
        if not normalized.lower().endswith(SCRIPT_FILE_EXTENSIONS):
            continue
        if original.replace("\\", "/").endswith("/"):
            continue
        raw = safe_read_zip_member(zf, original, counter)
        reason = find_dangerous_script_content(raw.decode("utf-8", errors="replace"))
        if reason:
            hits.append((normalized[len(payload_prefix):], reason))
    return hits


def find_dangerous_zip_script(
    zf,
    members: dict[str, str],
    payload_prefix: str,
    counter,
) -> tuple[str, str] | None:
    """扫描 ZIP 内层目录中的脚本文件，返回 (相对路径汇总, 原因) 或 None。

    命中多处时路径以逗号拼接（最多展示 5 处），避免只报第一处而漏报其余文件。
    """
    hits = _collect_dangerous_zip_scripts(zf, members, payload_prefix, counter)
    if not hits:
        return None
    paths = [path for path, _ in hits]
    reasons = {reason for _, reason in hits}
    if len(paths) <= 5:
        path_msg = ", ".join(paths)
    else:
        path_msg = ", ".join(paths[:5]) + f" 等{len(paths)}处"
    reason_msg = next(iter(reasons)) if len(reasons) == 1 else "危险脚本内容"
    return path_msg, reason_msg


def find_dangerous_manifest_mcp_files(
    zf,
    members: dict[str, str],
    payload_prefix: str,
    manifest: dict[str, Any],
    counter,
) -> tuple[str, str] | None:
    """扫描 manifest.mcps[].file 指向的 mcp.json 描述文件。"""
    from plugins_market.validation.zip_utils import safe_read_zip_member

    entries = manifest.get("mcps")
    if not isinstance(entries, list):
        return None
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            continue
        file_path = item.get("file")
        if not isinstance(file_path, str) or not file_path.strip():
            continue
        relative = file_path.strip().replace("\\", "/").removeprefix("./")
        original = members.get(f"{payload_prefix}{relative}")
        if original is None:
            continue
        raw = safe_read_zip_member(zf, original, counter)
        reason = find_dangerous_mcp_json(raw, label=f"manifest.mcps[{index}]")
        if reason:
            return relative, reason
    return None
