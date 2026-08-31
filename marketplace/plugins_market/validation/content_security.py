# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""发布期静态内容安全扫描。

多资产（agent-plugin / agent-template / agent-mcp）由管理员发布、免人工审核，
但"免人工审核"不等于"免静态安全扫描"：结构合法的包仍可能携带命令注入、
远程脚本下载执行等危险内容。本模块提供保守、低误报的规则集：

- 命令字符串（mcp.json / cli.json）：下载后立即执行的命令链；
- 脚本文件（.py/.sh 等）：os.system / os.popen / subprocess shell=True / 下载执行链。
"""

from __future__ import annotations

import re

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

_OS_SYSTEM_RE = re.compile(r"\bos\.system\s*\(")
_OS_POPEN_RE = re.compile(r"\bos\.popen\s*\(")
_SUBPROCESS_SHELL_RE = re.compile(
    r"\bsubprocess\.(?:run|Popen|call|check_call|check_output|getoutput|getstatusoutput)"
    r"\s*\([\s\S]{0,800}?\bshell\s*=\s*True\b"
)
_SCRIPT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_PIPE_TO_SHELL_RE, "下载后直接执行的命令链"),
    (_SHELL_WRAPPED_DOWNLOAD_RE, "shell 包裹的下载执行命令"),
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


def find_dangerous_zip_script(
    zf,
    members: dict[str, str],
    payload_prefix: str,
    counter,
) -> tuple[str, str] | None:
    """扫描 ZIP 内层目录中的脚本文件，返回 (相对路径, 原因) 或 None。

    ``members`` 为 归一化路径 -> 原始路径 的映射（见 wrapped_asset.normalized_member_map）。
    """
    from plugins_market.validation.zip_utils import safe_read_zip_member

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
            return normalized[len(payload_prefix):], reason
    return None
