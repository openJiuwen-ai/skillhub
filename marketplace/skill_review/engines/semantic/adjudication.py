from __future__ import annotations

from dataclasses import replace
from typing import Any

from skill_review.domain.types import Confidence, GateRecommendation, ReviewFindingDraft, Severity
from skill_review.engines.semantic.candidate_groups import (
    SemanticCandidateGroupItem,
    build_semantic_candidate_review_alias_map,
)


def build_finding_ref(finding: ReviewFindingDraft, index: int) -> str:
    return f"{finding.source_type}:{finding.section_key}:{finding.check_id}:{index + 1}"


def select_findings_requiring_adjudication(findings: list[ReviewFindingDraft]) -> list[ReviewFindingDraft]:
    return [finding for finding in findings if finding.source_type == "rule"]


def build_required_finding_ref_map(findings: list[ReviewFindingDraft]) -> dict[int, str]:
    required_findings = select_findings_requiring_adjudication(findings)
    return {id(finding): build_finding_ref(finding, index) for index, finding in enumerate(required_findings)}


def normalize_semantic_candidate_review_dispositions(output: dict[str, Any] | None) -> dict[str, Any] | None:
    candidate_reviews = read_semantic_candidate_reviews(output)
    if not output or not candidate_reviews:
        return output
    normalized_reviews = [
        {
            **candidate_review,
            "disposition": (
                "needs_manual_review"
                if candidate_review.get("disposition") == "invalid_or_unreviewable"
                else candidate_review.get("disposition")
            ),
        }
        for candidate_review in candidate_reviews
    ]
    return {
        **output,
        "candidate_reviews": normalized_reviews,
        "adjudications": normalized_reviews,
    }


def apply_semantic_candidate_reviews(
    deterministic_findings: list[ReviewFindingDraft],
    output: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized_output = normalize_semantic_candidate_review_dispositions(output)
    candidate_reviews = read_semantic_candidate_reviews(normalized_output)
    candidate_review_by_ref = {
        str(candidate_review.get("finding_ref")): candidate_review for candidate_review in candidate_reviews
    }
    ref_by_finding = build_required_finding_ref_map(deterministic_findings)
    review_alias_by_ref = build_semantic_candidate_review_alias_map(
        build_semantic_candidate_group_items(deterministic_findings, ref_by_finding)
    )
    final_findings: list[ReviewFindingDraft] = []
    suppressed_findings: list[ReviewFindingDraft] = []

    for finding in deterministic_findings:
        finding_ref = ref_by_finding.get(id(finding))
        if not finding_ref:
            final_findings.append(finding)
            continue
        candidate_review = candidate_review_by_ref.get(finding_ref) or candidate_review_by_ref.get(
            review_alias_by_ref.get(finding_ref, "")
        )
        if not candidate_review:
            final_findings.append(finding)
            continue
        if should_suppress_finding(finding, candidate_review):
            suppressed_findings.append(finding)
            continue
        final_findings.append(apply_semantic_candidate_review(finding, candidate_review))

    return {
        "final_findings": final_findings,
        "suppressed_findings": suppressed_findings,
        "candidate_reviews": candidate_reviews,
        "normalized_output": normalized_output,
    }


def build_semantic_candidate_group_items(
    findings: list[ReviewFindingDraft],
    ref_by_finding: dict[int, str],
) -> list[SemanticCandidateGroupItem]:
    items: list[SemanticCandidateGroupItem] = []
    for finding in findings:
        finding_ref = ref_by_finding.get(id(finding))
        if finding_ref:
            items.append(SemanticCandidateGroupItem(finding=finding, finding_ref=finding_ref))
    return items


def read_semantic_candidate_reviews(output: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not output:
        return []
    reviews = output.get("candidate_reviews") or output.get("adjudications") or []
    return reviews if isinstance(reviews, list) else []


def should_suppress_finding(finding: ReviewFindingDraft, candidate_review: dict[str, Any]) -> bool:
    return (
        finding.source_type == "rule"
        and finding.metadata.get("allow_semantic_downgrade") is not False
        and candidate_review.get("disposition") == "benign_example"
    )


def apply_semantic_candidate_review(
    finding: ReviewFindingDraft,
    candidate_review: dict[str, Any],
) -> ReviewFindingDraft:
    final_severity = resolve_semantic_review_severity(finding, candidate_review)
    final_gate = resolve_semantic_review_gate(finding, candidate_review)
    return replace(
        finding,
        severity=final_severity,
        confidence=resolve_semantic_review_confidence(finding, candidate_review),
        gate_recommendation=final_gate,
        description=build_reviewed_description(finding, candidate_review, final_severity, final_gate),
        recommendation=build_reviewed_recommendation(finding, candidate_review, final_severity, final_gate),
        evidence=[*finding.evidence, build_semantic_review_evidence(candidate_review)],
    )


SEVERITY_RANK: dict[str, int] = {"info": 0, "low": 1, "medium": 2, "high": 3}
GATE_RANK: dict[str, int] = {"info": 0, "attention": 1, "block": 2}
CONFIDENCE_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


def resolve_semantic_review_severity(finding: ReviewFindingDraft, candidate_review: dict[str, Any]) -> Severity:
    candidate = candidate_review.get("final_severity")
    if candidate not in SEVERITY_RANK:
        return finding.severity
    if (
        finding.metadata.get("allow_semantic_downgrade") is False
        and SEVERITY_RANK[candidate] < SEVERITY_RANK[finding.severity]
    ):
        return finding.severity
    return candidate


def resolve_semantic_review_gate(finding: ReviewFindingDraft, candidate_review: dict[str, Any]) -> GateRecommendation:
    candidate = candidate_review.get("final_gate_recommendation")
    if candidate not in GATE_RANK:
        return finding.gate_recommendation
    if (
        finding.metadata.get("allow_semantic_downgrade") is False
        and GATE_RANK[candidate] < GATE_RANK[finding.gate_recommendation]
    ):
        return finding.gate_recommendation
    return candidate


def resolve_semantic_review_confidence(finding: ReviewFindingDraft, candidate_review: dict[str, Any]) -> Confidence:
    candidate = candidate_review.get("confidence")
    if candidate not in CONFIDENCE_RANK:
        return finding.confidence
    if (
        finding.metadata.get("allow_semantic_downgrade") is False
        and CONFIDENCE_RANK[candidate] < CONFIDENCE_RANK[finding.confidence]
    ):
        return finding.confidence
    return candidate


def build_reviewed_description(
    finding: ReviewFindingDraft,
    candidate_review: dict[str, Any],
    final_severity: Severity,
    final_gate: GateRecommendation,
) -> str:
    if final_severity == finding.severity and final_gate == finding.gate_recommendation:
        return finding.description
    if candidate_review.get("disposition") == "confirmed_risk":
        return finding.description
    return str(candidate_review.get("rationale") or "").strip() or finding.description


def build_reviewed_recommendation(
    finding: ReviewFindingDraft,
    candidate_review: dict[str, Any],
    final_severity: Severity,
    final_gate: GateRecommendation,
) -> str:
    if final_severity == finding.severity and final_gate == finding.gate_recommendation:
        return finding.recommendation
    disposition = candidate_review.get("disposition")
    if disposition == "confirmed_risk":
        return finding.recommendation
    if disposition == "capability_only":
        return "请确认该能力是否符合 Skill 目标、触发条件和权限边界；如符合预期，可作为需关注能力继续流转。"
    if disposition == "needs_manual_review":
        return "请人工确认该能力是否属于默认执行路径，以及是否需要补充用户确认或运行环境隔离。"
    return finding.recommendation


def build_semantic_review_evidence(candidate_review: dict[str, Any]) -> dict[str, Any]:
    disposition_label = {
        "confirmed_risk": "确认风险",
        "benign_example": "良性示例",
        "capability_only": "能力边界",
        "needs_manual_review": "需要人工确认",
        "invalid_or_unreviewable": "无法自动裁决",
    }.get(str(candidate_review.get("disposition")), "语义复核")
    text = "\n".join(build_semantic_review_evidence_lines(candidate_review, disposition_label))
    return {
        "evidence_type": "semantic",
        "text": text,
        "related_files": candidate_review.get("related_files", []),
    }


def build_semantic_review_evidence_lines(
    candidate_review: dict[str, Any],
    disposition_label: str,
) -> list[str]:
    lines = [f"AI 复核结论：{disposition_label}。"]
    rationale = str(candidate_review.get("rationale") or "").strip()
    if rationale:
        lines.append(rationale)
    semantic_evidence = str(candidate_review.get("semantic_evidence") or "").strip()
    if semantic_evidence:
        lines.append(f"复核证据：{semantic_evidence}")
    return lines
