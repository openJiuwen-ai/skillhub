from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cmp_to_key

from skill_review.domain.check_catalog import get_review_check
from skill_review.domain.types import GateRecommendation, PackageFileEntry, ReviewFindingDraft, Severity
from skill_review.engines.rule.context import resolve_rule_severity
from skill_review.engines.rule.patterns import DEFAULT_RULE_PATTERNS, DOWNLOAD_THEN_EXECUTE_PATTERN, RulePattern
from skill_review.engines.types import ReviewEngine, ReviewEngineContext, ReviewEngineResult
from skill_review.runtime.package_access.file_catalog import (
    compare_review_relevant_paths,
    is_text_like_file,
)

MAX_RULE_SCAN_FILES = 200
MAX_RULE_SCAN_BYTES_PER_FILE = 256 * 1024
MAX_RULE_FINDINGS = 80


@dataclass(frozen=True)
class RuleFindingInput:
    pattern: RulePattern
    file_path: str
    line_number: int
    line: str
    severity: Severity


def _run_rule_engine(context: ReviewEngineContext) -> ReviewEngineResult:
    eligible_files = select_rule_scan_files(context.package_summary.files)
    findings: list[ReviewFindingDraft] = []
    scanned_file_count = 0
    truncated_file_count = 0
    unreadable_file_count = 0

    for file in eligible_files[:MAX_RULE_SCAN_FILES]:
        read_result = context.package_access.read_text_file(file.path, max_bytes=MAX_RULE_SCAN_BYTES_PER_FILE)
        if read_result.status != "success" or read_result.content is None:
            unreadable_file_count += 1
            continue
        scanned_file_count += 1
        if read_result.truncated:
            truncated_file_count += 1
        findings.extend(scan_rule_findings(file.path, read_result.content, DEFAULT_RULE_PATTERNS))
        findings.extend(scan_compound_rule_findings(file.path, read_result.content))
        if len(findings) >= MAX_RULE_FINDINGS:
            findings = findings[:MAX_RULE_FINDINGS]
            break

    return ReviewEngineResult(
        engine_id="rule",
        engine_name="Static Rule Engine",
        kind="rule",
        coverage=[
            {
                "engine_id": "rule",
                "engine_name": "Static Rule Engine",
                "status": "completed",
                "eligible_file_count": len(eligible_files),
                "scanned_file_count": scanned_file_count,
                "skipped_by_budget_count": max(0, len(eligible_files) - MAX_RULE_SCAN_FILES),
                "truncated_file_count": truncated_file_count,
                "unreadable_file_count": unreadable_file_count,
                "finding_count": len(findings),
            }
        ],
        findings=findings,
        raw_output={
            "rule_scan": {
                "finding_count": len(findings),
                "eligible_file_count": len(eligible_files),
                "scanned_file_count": scanned_file_count,
                "truncated_file_count": truncated_file_count,
                "unreadable_file_count": unreadable_file_count,
            }
        },
    )


def select_rule_scan_files(files: list[PackageFileEntry]) -> list[PackageFileEntry]:
    text_files = [file for file in files if is_text_like_file(file.path)]
    return sorted(
        text_files,
        key=cmp_to_key(lambda left, right: compare_review_relevant_paths(left.path, right.path)),
    )


def scan_rule_findings(
    file_path: str,
    content: str,
    patterns: list[RulePattern],
) -> list[ReviewFindingDraft]:
    findings: list[ReviewFindingDraft] = []
    seen_pattern_ids: set[str] = set()
    lines = content.splitlines()
    for line_number, line in enumerate(lines, start=1):
        for pattern in patterns:
            if pattern.pattern_id in seen_pattern_ids or not pattern.regex.search(line):
                continue
            findings.append(
                build_rule_finding(
                    RuleFindingInput(
                        pattern=pattern,
                        file_path=file_path,
                        line_number=line_number,
                        line=line,
                        severity=resolve_rule_severity(pattern, file_path, lines, line_number - 1),
                    )
                )
            )
            seen_pattern_ids.add(pattern.pattern_id)
    return findings


def scan_compound_rule_findings(file_path: str, content: str) -> list[ReviewFindingDraft]:
    findings: list[ReviewFindingDraft] = []
    lines = content.splitlines()
    for line_index, line in enumerate(lines):
        if not is_remote_download_line(line) or is_same_line_download_execute(line):
            continue
        execution_line_index = find_nearby_script_execution_line(lines, line_index)
        if execution_line_index < 0:
            continue
        excerpt = " && ".join(
            item.strip()
            for item in [line, lines[execution_line_index]]
            if item.strip()
        )
        findings.append(
            build_rule_finding(
                RuleFindingInput(
                    pattern=DOWNLOAD_THEN_EXECUTE_PATTERN,
                    file_path=file_path,
                    line_number=execution_line_index + 1,
                    line=excerpt,
                    severity=resolve_rule_severity(
                        DOWNLOAD_THEN_EXECUTE_PATTERN,
                        file_path,
                        lines,
                        execution_line_index,
                    ),
                )
            )
        )
    return findings


def build_rule_finding(input_value: RuleFindingInput) -> ReviewFindingDraft:
    pattern = input_value.pattern
    check = get_review_check(pattern.check_id)
    return ReviewFindingDraft(
        source_type="rule",
        section_key=pattern.section_key,
        check_id=pattern.check_id,
        severity=input_value.severity,
        confidence=pattern.confidence,
        category=pattern.category,
        capability=pattern.capability,
        title=pattern.title,
        description=pattern.description,
        recommendation=check["recommendation"],
        gate_recommendation=resolve_gate_recommendation(input_value.severity),
        evidence=[
            {
                "evidence_type": "source",
                "text": truncate_evidence_line(input_value.line),
                "location": {"file": input_value.file_path, "line": input_value.line_number},
            }
        ],
        metadata={
            "rule_pattern_id": pattern.pattern_id,
            "allow_semantic_downgrade": pattern.allow_semantic_downgrade,
        },
    )


def is_remote_download_line(line: str) -> bool:
    return bool(re.search(r"\b(?:curl|wget)\b.*https?://", line, re.I))


def is_same_line_download_execute(line: str) -> bool:
    return bool(re.search(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:bash|sh|zsh)\b", line, re.I))


def find_nearby_script_execution_line(lines: list[str], download_line_index: int) -> int:
    for line_index in range(download_line_index + 1, min(len(lines), download_line_index + 5)):
        if re.search(r"\b(?:bash|sh|zsh)\s+[\w./-]+\b", lines[line_index], re.I):
            return line_index
    return -1


def resolve_gate_recommendation(severity: Severity) -> GateRecommendation:
    if severity == "high":
        return "block"
    if severity == "low":
        return "info"
    return "attention"


def truncate_evidence_line(line: str, max_length: int = 500) -> str:
    normalized = " ".join(line.strip().split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[:max_length]}..."


rule_engine = ReviewEngine(
    engine_id="rule",
    engine_name="Static Rule Engine",
    kind="rule",
    failure_policy="abort_review",
    run=_run_rule_engine,
)
