from __future__ import annotations

import re

from skill_review.domain.types import Severity
from skill_review.engines.rule.patterns import RulePattern

DOCUMENTATION_EXTENSIONS = {".md", ".mdx", ".txt", ".rst", ".adoc"}
REFERENCE_PATH_PATTERN = re.compile(r"(^|/)(references?|docs?|examples?|samples?|guides?|tutorials?)(/|$)", re.I)
REFERENCE_FILE_NAMES = {
    "readme",
    "guide",
    "guides",
    "usage",
    "tutorial",
    "tutorials",
    "reference",
    "references",
    "examples",
    "security",
}
PLACEHOLDER_CONTEXT_PATTERN = re.compile(
    r"\b(mock|fake|dummy|placeholder|sample|demo|fixture|test(?:ing)?|sandbox)\b|示例|演示|占位|样例|模拟",
    re.I,
)
EXAMPLE_CONTEXT_PATTERN = re.compile(
    r"\b(for example|examples\b|example code|sample code|demo code|documentation|guide|tutorial|"
    r"do not execute|quoted attack|security guidance|security checklist|dangerous command|avoid)\b|"
    r"例如|比如|文档|指南|教程|不要执行|危险命令|安全指南|反面教材",
    re.I,
)
OPERATIONAL_BOOTSTRAP_CONTEXT_PATTERN = re.compile(
    r"\b(setup|install|installation|prerequisite|requirements?|getting started|authenticate|"
    r"configuration|configure)\b|安装|前置|依赖|认证|配置",
    re.I,
)
OPTIONAL_INSTALLATION_CONTEXT_PATTERN = re.compile(
    r"\b(if .{0,40}(?:not installed|missing|unavailable)|install if missing|"
    r"ask the user to run|not already authenticated)\b|如果.{0,30}(未安装|缺失|不可用)|让用户运行",
    re.I,
)


def resolve_rule_severity(
    pattern: RulePattern,
    file_path: str,
    lines: list[str],
    line_index: int,
) -> Severity:
    line = lines[line_index] if line_index < len(lines) else ""
    if is_read_only_search_command_line(line):
        return "low"
    if pattern.requires_external_endpoint and not has_external_endpoint_near(lines, line_index):
        return "low"
    weight = context_weight(file_path, lines, line_index)
    if pattern.check_id == "destructive_operation" and is_project_local_cleanup_command(line):
        return downgrade_severity("medium", weight)
    if not pattern.allow_context_downgrade:
        return pattern.severity
    return downgrade_severity(pattern.severity, weight)


def context_weight(file_path: str, lines: list[str], line_index: int) -> str:
    context_window = create_context_window(file_path, lines, line_index)
    if is_reference_documentation(file_path):
        return "reference"
    if OPTIONAL_INSTALLATION_CONTEXT_PATTERN.search(context_window):
        return "optional"
    if PLACEHOLDER_CONTEXT_PATTERN.search(context_window):
        return "placeholder"
    if EXAMPLE_CONTEXT_PATTERN.search(context_window):
        return "example"
    if is_executable_script(file_path):
        return "instructional"
    if OPERATIONAL_BOOTSTRAP_CONTEXT_PATTERN.search(context_window):
        return "bootstrap"
    return "instructional"


def downgrade_severity(severity: Severity, weight: str) -> Severity:
    if weight == "reference":
        return "medium" if severity == "high" else severity
    if weight == "optional":
        return "low"
    if weight == "bootstrap":
        return "medium" if severity == "high" else severity
    if weight == "instructional":
        return severity
    if severity == "high":
        return "medium"
    return "low"


def is_reference_documentation(file_path: str) -> bool:
    normalized = normalize_path(file_path)
    extension = path_extension(normalized)
    if extension not in DOCUMENTATION_EXTENSIONS:
        return False
    file_name = normalized.rsplit("/", 1)[-1]
    stem = file_name[: -len(extension)] if extension else file_name
    return stem in REFERENCE_FILE_NAMES or bool(REFERENCE_PATH_PATTERN.search(normalized))


def is_executable_script(file_path: str) -> bool:
    return bool(
        re.search(
            r"(^|/)(scripts?|bin|hooks?)/[^/]+\.(?:sh|bash|zsh|ps1|py|js|mjs|cjs|ts|tsx)$",
            normalize_path(file_path),
            re.I,
        )
    )


def is_read_only_search_command_line(line: str) -> bool:
    return bool(re.match(r"\s*(?:grep|egrep|fgrep|rg|ag|git\s+grep)\b", line, re.I))


def is_project_local_cleanup_command(line: str) -> bool:
    match = re.search(r"\brm\s+[^;&|`]*-(?:[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)[^;&|`]*\s+(.+)$", line, re.I)
    if not match:
        return False
    targets = [
        target.strip().strip("'\"") for target in re.split(r"\s+", match.group(1).split("&&")[0]) if target.strip()
    ]
    return bool(targets and all(is_project_local_cleanup_target(target) for target in targets))


def is_project_local_cleanup_target(target: str) -> bool:
    normalized = normalize_path(target).removeprefix("./")
    return bool(
        normalized
        and normalized not in {".", "/"}
        and not normalized.startswith(("/", "~", "-"))
        and ".." not in normalized
        and not re.search(r"[$`*?{}\[\]]", normalized)
    )


def has_external_endpoint_near(lines: list[str], line_index: int) -> bool:
    context_window = "\n".join(lines[line_index:min(len(lines), line_index + 4)])
    return any(not is_loopback_endpoint(endpoint) for endpoint in extract_http_endpoints(context_window))


def create_context_window(file_path: str, lines: list[str], line_index: int) -> str:
    start = max(0, line_index - 8)
    end = min(len(lines), line_index + 4)
    return "\n".join([file_path, *lines[start:end]])


def extract_http_endpoints(value: str) -> list[str]:
    return re.findall(r"https?://[^\s`\"'\\)]+", value, re.I)


def is_loopback_endpoint(endpoint: str) -> bool:
    match = re.match(r"https?://([^/:]+)", endpoint, re.I)
    if not match:
        return False
    hostname = match.group(1).lower()
    return hostname in {"localhost", "0.0.0.0", "::1"} or hostname.startswith("127.")


def normalize_path(value: str) -> str:
    return value.replace("\\", "/").lower()


def path_extension(file_path: str) -> str:
    file_name = file_path.rsplit("/", 1)[-1]
    dot_index = file_name.rfind(".")
    return file_name[dot_index:] if dot_index >= 0 else ""
