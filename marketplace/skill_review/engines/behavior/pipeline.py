from __future__ import annotations

import re
from dataclasses import dataclass

from skill_review.domain.behavior_facts import ReviewBehaviorFact, ReviewBehaviorInventory
from skill_review.domain.types import PackageFileEntry
from skill_review.engines.common.network_signatures import (
    CODE_NETWORK_SINK_PATTERN_SOURCE,
    ENDPOINT_COMMAND_PATTERN_SOURCE,
    URL_TRANSFER_COMMAND_PATTERN_SOURCE,
)
from skill_review.engines.facet.fact_id import normalize_token
from skill_review.runtime.package_access.base import PackageAccess, PackageTextReadResult

TEXT_FILE_PATTERN = re.compile(
    r"\.(md|mdx|txt|js|jsx|ts|tsx|mjs|cjs|py|sh|bash|zsh|ps1|cmd|bat|yaml|yml|json|toml)$", re.I
)
READ_BUDGET_BYTES = 256 * 1024
MAX_SCANNED_FILES = 200
MAX_TOTAL_READ_BYTES = 8 * 1024 * 1024
MAX_EVIDENCE_PER_BEHAVIOR_FACT = 2
LOCAL_ARTIFACT_PATTERN = re.compile(r"\.(?:sh|bash|zsh|py|js|mjs|cjs|ts|ps1|cmd|bat)$", re.I)
CODE_NETWORK_LITERAL_PATTERN = re.compile(
    rf"\b{CODE_NETWORK_SINK_PATTERN_SOURCE}\s*\(\s*['\"]?(https?://[^'\")\s]+)",
    re.I,
)
CODE_NETWORK_DYNAMIC_PATTERN = re.compile(
    rf"\b({CODE_NETWORK_SINK_PATTERN_SOURCE})\s*\(\s*(?!['\"]https?://)",
    re.I,
)
ENDPOINT_COMMAND_PATTERN = re.compile(
    rf"\b{ENDPOINT_COMMAND_PATTERN_SOURCE}\b[^\n`]*?(https?://[^\s'\"`)]+)",
    re.I,
)
URL_TRANSFER_COMMAND_PATTERN = re.compile(
    rf"\b{URL_TRANSFER_COMMAND_PATTERN_SOURCE}\b[^\n`]*?(https?://[^\s'\"`)]+)",
    re.I,
)
BEHAVIOR_PATTERNS = [
    ("network_access", "code", "high", CODE_NETWORK_LITERAL_PATTERN),
    ("network_access", "code", "medium", CODE_NETWORK_DYNAMIC_PATTERN),
    ("network_access", "command", "high", ENDPOINT_COMMAND_PATTERN),
    (
        "package_or_artifact_fetch",
        "command",
        "high",
        re.compile(
            r"\b(?:npm\s+(?:install|i|add)|pnpm\s+(?:add|install|dlx)|yarn\s+(?:add|install|dlx)|"
            r"pip3?\s+install|python\s+-m\s+pip\s+install|uv\s+(?:add|pip\s+install|run\s+--with)|npx)\b[^\n`]*",
            re.I,
        ),
    ),
    ("package_or_artifact_fetch", "command", "medium", URL_TRANSFER_COMMAND_PATTERN),
    (
        "filesystem_access",
        "command",
        "high",
        re.compile(
            r"\b(?:rm\s+-rf|rm\s+-fr|del\s+/[sq]|rmdir\s+/[sq]|chmod\s+(?:-R\s+)?[0-7]{3,4}|chown\s+-R)\b[^\n`]*", re.I
        ),
    ),
    ("filesystem_access", "command", "high", re.compile(r"\bfind\b[^\n`]*\s-delete\b[^\n`]*", re.I)),
    (
        "filesystem_access",
        "code",
        "medium",
        re.compile(
            r"\b(?:fs\.(?:writeFile|rm|unlink|rmdir)|os\.remove|shutil\.rmtree|Path\([^)]*\)\.unlink)\s*\(", re.I
        ),
    ),
    (
        "filesystem_boundary_access",
        "command",
        "high",
        re.compile(r"(?:\.\./|\.\.\\|\b(?:cp|mv|cat|tar|unzip|read|write|open)\b[^\n`]{0,80}(?:\.\./|\.\.\\))", re.I),
    ),
    (
        "filesystem_boundary_access",
        "code",
        "medium",
        re.compile(
            r"\b(?:open|readFile|writeFile|Path|os\.path\.join)\b[^\n]{0,120}(?:\.\./|\.\.\\|/etc/|/root/|[A-Za-z]:\\)",
            re.I,
        ),
    ),
    (
        "environment_access",
        "code",
        "high",
        re.compile(r"\b(?:process\.env|os\.environ|os\.getenv|dotenv\.load_dotenv)\b", re.I),
    ),
    ("dynamic_code_execution", "code", "high", re.compile(r"\b(?:eval|exec|Function|compile)\s*\(", re.I)),
    (
        "unsafe_object_loading",
        "code",
        "high",
        re.compile(
            r"\b(?:pickle\.loads|pickle\.load|yaml\.load\s*\(|marshal\.loads|"
            r"unserialize\s*\(|BinaryFormatter|joblib\.load\s*\()",
            re.I,
        ),
    ),
    (
        "process_execution",
        "code",
        "high",
        re.compile(
            r"\b(?:child_process\.(?:exec|spawn|execFile)|subprocess\.(?:run|Popen|call)|os\.system)\s*\(", re.I
        ),
    ),
    (
        "resource_exhaustion",
        "command",
        "high",
        re.compile(
            r"\b(?:while\s+true|for\s*\(\s*;\s*;\s*\)|yes\b|dd\s+if=/dev/(?:zero|urandom)|"
            r"fallocate\s+-l|truncate\s+-s)\b[^\n`]*",
            re.I,
        ),
    ),
    (
        "resource_exhaustion",
        "code",
        "medium",
        re.compile(
            r"\b(?:while\s+True|while\s*\(\s*true\s*\)|for\s*\(\s*;\s*;\s*\)|"
            r"setrecursionlimit\s*\(|Array\s*\(\s*10\*\*\d+|bytearray\s*\(\s*10\*\*\d+|"
            r"new\s+Array\s*\(\s*10\*\*\d+|Buffer\.alloc\s*\(\s*10\*\*\d+)",
            re.I,
        ),
    ),
    (
        "bulk_remote_requests",
        "command",
        "high",
        re.compile(
            r"\b(?:parallel\s+requests?|burst\s+requests?|flood\s+(?:the\s+)?api|"
            r"bypass\s+rate\s+limit|retry\s+until\s+success|rotate\s+(?:api\s+)?keys?)\b",
            re.I,
        ),
    ),
    (
        "bulk_remote_requests",
        "code",
        "medium",
        re.compile(
            r"\b(?:Promise\.all|asyncio\.gather|ThreadPoolExecutor|ProcessPoolExecutor)\b"
            r"[^\n]{0,120}\b(?:fetch|requests\.|axios\.|httpx\.)",
            re.I,
        ),
    ),
    (
        "query_or_template_construction",
        "code",
        "medium",
        re.compile(
            r"\b(?:cursor\.execute|db\.query|sequelize\.query|render_template_string|"
            r"jinja2\.Template|XMLParser|DocumentBuilderFactory)\b[^\n`]*",
            re.I,
        ),
    ),
    (
        "remote_state_mutation",
        "command",
        "high",
        re.compile(r"\b(?:git\s+push|npm\s+publish|gh\s+release\s+upload)\b[^\n`]*", re.I),
    ),
]


@dataclass
class FileScanStats:
    eligible_file_count: int
    scanned_file_count: int = 0
    skipped_file_count: int = 0
    truncated_file_count: int = 0
    unreadable_file_count: int = 0
    read_bytes: int = 0


def run_behavior_facts_pipeline(package_access: PackageAccess) -> ReviewBehaviorInventory:
    selected_files = [file for file in package_access.list_files() if is_behavior_relevant_file(file)]
    stats = FileScanStats(eligible_file_count=len(selected_files))
    facts: list[ReviewBehaviorFact] = []

    for file in selected_files:
        if has_exceeded_scan_budget(stats):
            stats.skipped_file_count += 1
            continue
        read_budget = min(READ_BUDGET_BYTES, MAX_TOTAL_READ_BYTES - stats.read_bytes)
        read_result = package_access.read_text_file(file.path, max_bytes=read_budget)
        stats.read_bytes += len((read_result.content or "").encode("utf-8"))
        read_gap = build_read_gap_fact(file.path, read_result)
        if read_gap:
            facts.append(read_gap)
        if is_unreadable_read(read_result):
            stats.unreadable_file_count += 1
            continue
        stats.scanned_file_count += 1
        stats.truncated_file_count += 1 if read_result.truncated else 0
        facts.extend(extract_behavior_facts(file.path, read_result.content or ""))

    if stats.skipped_file_count > 0:
        facts.append(build_budget_gap_fact(stats))
    derived_facts = derive_invoked_artifact_effect_facts(facts, selected_files)
    return {
        "facts": merge_behavior_facts(dedupe_behavior_facts([*facts, *derived_facts])),
        "coverage": [build_coverage_record(stats)],
        "findings": [],
    }


def is_behavior_relevant_file(file: PackageFileEntry) -> bool:
    return bool(TEXT_FILE_PATTERN.search(file.path))


def has_exceeded_scan_budget(stats: FileScanStats) -> bool:
    return (
        stats.scanned_file_count + stats.unreadable_file_count >= MAX_SCANNED_FILES
        or stats.read_bytes >= MAX_TOTAL_READ_BYTES
    )


def is_unreadable_read(read_result: PackageTextReadResult) -> bool:
    return read_result.status != "success" or read_result.content is None


def build_budget_gap_fact(stats: FileScanStats) -> ReviewBehaviorFact:
    return build_behavior_fact(
        kind="analyzability_gap",
        source="coverage",
        confidence="high",
        subject="scan_budget_exceeded",
        file_path="package",
        line=1,
        text=(
            f"behavior fact scan skipped {stats.skipped_file_count} files "
            f"after scanning {stats.scanned_file_count} files"
        ),
    )


def build_read_gap_fact(file_path: str, read_result: PackageTextReadResult) -> ReviewBehaviorFact | None:
    if not read_result.truncated and read_result.status == "success":
        return None
    subject = "truncated_file" if read_result.truncated else read_result.status or "read_error"
    reason = f"{subject}: {read_result.error}" if read_result.error else subject
    return build_behavior_fact(
        kind="analyzability_gap",
        source="coverage",
        confidence="high",
        subject=subject,
        file_path=file_path,
        line=1,
        text=reason,
    )


def extract_behavior_facts(file_path: str, content: str) -> list[ReviewBehaviorFact]:
    facts: list[ReviewBehaviorFact] = []
    for kind, source, confidence, pattern in BEHAVIOR_PATTERNS:
        for match in pattern.finditer(content):
            facts.append(
                build_behavior_fact(
                    kind=kind,
                    source=source,
                    confidence=confidence,
                    subject=extract_subject(match),
                    file_path=file_path,
                    line=line_of(content, match.start()),
                    text=trim_evidence(match.group(0)),
                    source_context=build_source_context(content, match.start()),
                )
            )
    facts.extend(extract_documented_invocation_facts(file_path, content))
    return facts


def extract_documented_invocation_facts(file_path: str, content: str) -> list[ReviewBehaviorFact]:
    if not re.search(r"\.(md|mdx)$", file_path, re.I):
        return []
    pattern = re.compile(
        r"\b(?:run|execute|invoke|call|launch|start)\s+(?:the\s+)?(?:local\s+)?(?:script\s+)?"
        r"[`'\"]?((?:[\w./-]+/)?[\w.-]+\.(?:sh|bash|zsh|py|js|mjs|cjs|ts|ps1|cmd|bat))[`'\"]?",
        re.I,
    )
    return [
        build_behavior_fact(
            kind="process_execution",
            source="documentation",
            confidence="medium",
            subject=match.group(1),
            file_path=file_path,
            line=line_of(content, match.start()),
            text=trim_evidence(match.group(0)),
            source_context=build_source_context(content, match.start()),
        )
        for match in pattern.finditer(content)
    ]


def build_behavior_fact(
    *,
    kind: str,
    source: str,
    confidence: str,
    subject: str,
    file_path: str,
    line: int,
    text: str,
    source_context: dict | None = None,
) -> ReviewBehaviorFact:
    evidence = {"file": file_path, "line": line, "text": text}
    if source_context:
        evidence["source_context"] = source_context
    return {
        "fact_id": build_behavior_fact_id(kind, subject, file_path, line),
        "kind": kind,
        "source": source,
        "confidence": confidence,
        "subject": subject,
        "evidence": [evidence],
    }


def derive_invoked_artifact_effect_facts(
    facts: list[ReviewBehaviorFact],
    files: list[PackageFileEntry],
) -> list[ReviewBehaviorFact]:
    artifact_path_by_token = build_artifact_path_lookup(files)
    derived_facts: list[ReviewBehaviorFact] = []
    for invocation_fact in find_documented_process_execution_facts(facts):
        artifact_path = artifact_path_by_token.get(normalize_path_token(invocation_fact["subject"]))
        if not artifact_path:
            continue
        side_effect_facts = find_artifact_side_effect_facts(facts, artifact_path)
        derived_facts.extend(
            build_invoked_artifact_effect_fact(invocation_fact, effect_fact, artifact_path)
            for effect_fact in side_effect_facts
        )
    return derived_facts


def find_documented_process_execution_facts(facts: list[ReviewBehaviorFact]) -> list[ReviewBehaviorFact]:
    return [fact for fact in facts if fact["kind"] == "process_execution" and fact["source"] == "documentation"]


def find_artifact_side_effect_facts(
    facts: list[ReviewBehaviorFact],
    artifact_path: str,
) -> list[ReviewBehaviorFact]:
    side_effect_facts: list[ReviewBehaviorFact] = []
    for fact in facts:
        if fact["kind"] not in {
            "filesystem_access",
            "network_access",
            "remote_state_mutation",
            "dynamic_code_execution",
            "resource_exhaustion",
            "bulk_remote_requests",
            "filesystem_boundary_access",
            "unsafe_object_loading",
            "query_or_template_construction",
        }:
            continue
        if any(evidence.get("file") == artifact_path for evidence in fact["evidence"]):
            side_effect_facts.append(fact)
    return side_effect_facts


def build_artifact_path_lookup(files: list[PackageFileEntry]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for file in files:
        if not LOCAL_ARTIFACT_PATTERN.search(file.path):
            continue
        lookup[normalize_path_token(file.path)] = file.path
        lookup[normalize_path_token(file.path.rsplit("/", 1)[-1])] = file.path
    return lookup


def build_invoked_artifact_effect_fact(
    invocation_fact: ReviewBehaviorFact,
    effect_fact: ReviewBehaviorFact,
    artifact_path: str,
) -> ReviewBehaviorFact:
    invocation_evidence = invocation_fact["evidence"][0]
    effect_evidence = next(
        (item for item in effect_fact["evidence"] if item.get("file") == artifact_path),
        effect_fact["evidence"][0],
    )
    derived_kind = (
        "execution_amplification_chain"
        if effect_fact["kind"] in {"resource_exhaustion", "bulk_remote_requests"}
        else effect_fact["kind"]
    )
    return {
        "fact_id": build_behavior_fact_id(
            derived_kind,
            f"invoked_artifact:{artifact_path}",
            invocation_evidence.get("file", artifact_path),
            invocation_evidence.get("line", 1),
        ),
        "kind": derived_kind,
        "source": "documentation",
        "confidence": lower_confidence(invocation_fact["confidence"], effect_fact["confidence"]),
        "subject": f"invoked_artifact:{artifact_path}",
        "evidence": [invocation_evidence, effect_evidence],
    }


def dedupe_behavior_facts(facts: list[ReviewBehaviorFact]) -> list[ReviewBehaviorFact]:
    fact_by_id = {fact["fact_id"]: fact for fact in facts}
    return list(fact_by_id.values())


def merge_behavior_facts(facts: list[ReviewBehaviorFact]) -> list[ReviewBehaviorFact]:
    merged_facts: dict[str, ReviewBehaviorFact] = {}
    passthrough_facts: list[ReviewBehaviorFact] = []
    for fact in facts:
        merge_key = build_behavior_fact_merge_key(fact)
        if not merge_key:
            passthrough_facts.append(fact)
            continue
        if merge_key not in merged_facts:
            merged_facts[merge_key] = {**fact, "evidence": select_behavior_evidence(fact["evidence"])}
            continue
        merge_behavior_fact_into(merged_facts[merge_key], fact)
    return [*merged_facts.values(), *passthrough_facts]


def build_behavior_fact_merge_key(fact: ReviewBehaviorFact) -> str | None:
    if fact["kind"] == "analyzability_gap":
        return None
    primary_file = fact["evidence"][0].get("file") if fact.get("evidence") else None
    if not primary_file:
        return None
    return ":".join([fact["kind"], fact["source"], normalize_path_token(primary_file)])


def merge_behavior_fact_into(target: ReviewBehaviorFact, source: ReviewBehaviorFact) -> None:
    evidence_before = len(target["evidence"])
    target["confidence"] = higher_confidence(target["confidence"], source["confidence"])
    target["evidence"] = select_behavior_evidence([*target["evidence"], *source["evidence"]])
    omitted_count = len(source["evidence"]) - max(0, MAX_EVIDENCE_PER_BEHAVIOR_FACT - evidence_before)
    target["omitted_evidence_count"] = target.get("omitted_evidence_count", 0) + max(0, omitted_count)


def select_behavior_evidence(evidence: list[dict]) -> list[dict]:
    selected: list[dict] = []
    seen: set[str] = set()
    for item in evidence:
        key = ":".join([str(item.get("file", "")), str(item.get("line", "")), str(item.get("text", ""))])
        if key in seen:
            continue
        seen.add(key)
        if len(selected) < MAX_EVIDENCE_PER_BEHAVIOR_FACT:
            selected.append(item)
    return selected


def build_coverage_record(stats: FileScanStats) -> dict:
    has_incomplete_read = (
        stats.unreadable_file_count > 0 or stats.truncated_file_count > 0 or stats.skipped_file_count > 0
    )
    return {
        "analyzer_id": "behavior-facts",
        "status": "partial" if has_incomplete_read else "completed",
        **({"reason": build_coverage_reason(stats)} if has_incomplete_read else {}),
        "eligible_file_count": stats.eligible_file_count,
        "scanned_file_count": stats.scanned_file_count,
        "skipped_file_count": stats.skipped_file_count,
        "truncated_file_count": stats.truncated_file_count,
        "unreadable_file_count": stats.unreadable_file_count,
    }


def build_coverage_reason(stats: FileScanStats) -> str:
    reasons = [
        "unreadable_files" if stats.unreadable_file_count > 0 else "",
        "truncated_files" if stats.truncated_file_count > 0 else "",
        "scan_budget_exceeded" if stats.skipped_file_count > 0 else "",
    ]
    return ", ".join([reason for reason in reasons if reason])


def build_source_context(content: str, index: int) -> dict:
    lines = content.splitlines()
    line_number = line_of(content, index)
    line_index = max(0, line_number - 1)
    code_block = find_code_block_context(lines, line_index)
    return {
        "heading_path": collect_heading_path(lines, line_index),
        "in_code_block": code_block["in_code_block"],
        **({"code_block_language": code_block["code_block_language"]} if code_block.get("code_block_language") else {}),
        "before": nearby_lines(lines, max(0, line_index - 2), line_index),
        "after": nearby_lines(lines, line_index + 1, min(len(lines), line_index + 3)),
    }


def collect_heading_path(lines: list[str], end_line_index: int) -> list[str]:
    headings: list[str] = []
    for index in range(end_line_index):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", lines[index])
        if not match:
            continue
        level = len(match.group(1))
        del headings[level - 1:]
        headings.extend([""] * (level - len(headings)))
        headings[level - 1] = trim_evidence(match.group(2))
    return [heading for heading in headings if heading]


def find_code_block_context(lines: list[str], line_index: int) -> dict:
    in_code_block = False
    code_block_language: str | None = None
    for index in range(line_index):
        match = re.match(r"^\s*```([A-Za-z0-9_-]*)", lines[index])
        if not match:
            continue
        in_code_block = not in_code_block
        code_block_language = match.group(1).lower() if in_code_block and match.group(1) else None
    return {
        "in_code_block": in_code_block,
        **({"code_block_language": code_block_language} if code_block_language else {}),
    }


def nearby_lines(lines: list[str], start_index: int, end_index: int) -> list[str]:
    return [line.strip() for line in lines[start_index:end_index] if line.strip()]


def extract_subject(match: re.Match[str]) -> str:
    for group in match.groups():
        if group:
            return trim_evidence(group)
    return trim_evidence(match.group(0)) or "unknown"


def trim_evidence(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def line_of(content: str, index: int) -> int:
    return content[: max(0, index)].count("\n") + 1


def normalize_path_token(value: str) -> str:
    return value.strip().replace("\\", "/").removeprefix("./").lower()


def build_behavior_fact_id(kind: str, subject: str, file_path: str, line: int) -> str:
    return ":".join([kind, normalize_token(subject), normalize_token(file_path), str(line)])


def lower_confidence(left: str, right: str) -> str:
    order = ["low", "medium", "high"]
    return order[min(order.index(left), order.index(right))]


def higher_confidence(left: str, right: str) -> str:
    order = ["low", "medium", "high"]
    return order[max(order.index(left), order.index(right))]
