from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from skill_review.domain.check_catalog import list_review_sections
from skill_review.engines.semantic.contract import (
    ALLOWED_CANDIDATE_REVIEW_DISPOSITIONS,
    ALLOWED_GATE_RECOMMENDATIONS,
    ALLOWED_SECTION_KEYS,
)
from skill_review.model.errors import SkillReviewSemanticRuntimeError
from skill_review.runtime.package_access.file_catalog import normalize_archive_path

ALLOWED_FINDING_SEVERITIES = {"high", "medium", "low"}
ALLOWED_CANDIDATE_SEVERITIES = {"high", "medium", "low", "info"}
ALLOWED_CONFIDENCES = {"high", "medium", "low"}
SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}
GATE_RANK = {"info": 0, "attention": 1, "block": 2}


@dataclass(frozen=True)
class SemanticValidationContext:
    package_paths: set[str]
    prompt_payload: dict[str, Any]
    response_body: Any
    full_response: Any


def validate_semantic_response(
    *,
    response: Any,
    package_paths: set[str],
    required_finding_refs: set[str],
    prompt_payload: dict[str, Any],
    response_body: Any,
) -> None:
    if not isinstance(response, dict):
        raise_validation_error("semantic response must be an object", prompt_payload, response_body, response)
    context = SemanticValidationContext(
        package_paths=package_paths,
        prompt_payload=prompt_payload,
        response_body=response_body,
        full_response=response,
    )
    validate_semantic_findings(response.get("findings"), context)
    validate_candidate_reviews(
        response.get("candidate_reviews"),
        required_finding_refs,
        context,
    )


def validate_semantic_findings(
    findings: Any,
    context: SemanticValidationContext,
) -> None:
    if not isinstance(findings, list):
        raise_validation_error(
            "semantic findings must be a list",
            context.prompt_payload,
            context.response_body,
            context.full_response,
        )
    check_section_by_id = build_check_section_map()
    for finding in findings:
        if not isinstance(finding, dict):
            raise_validation_error(
                "semantic finding must be an object",
                context.prompt_payload,
                context.response_body,
                context.full_response,
            )
        check_id = finding.get("check_id")
        section_key = finding.get("section_key")
        if check_section_by_id.get(check_id) != section_key:
            raise_validation_error(
                "semantic finding references an unknown or mismatched check",
                context.prompt_payload,
                context.response_body,
                context.full_response,
            )
        if section_key not in ALLOWED_SECTION_KEYS:
            raise_validation_error(
                "semantic finding section_key is invalid",
                context.prompt_payload,
                context.response_body,
                context.full_response,
            )
        if finding.get("severity") not in ALLOWED_FINDING_SEVERITIES:
            raise_validation_error(
                "semantic finding severity is invalid",
                context.prompt_payload,
                context.response_body,
                context.full_response,
            )
        if finding.get("confidence") not in ALLOWED_CONFIDENCES:
            raise_validation_error(
                "semantic finding confidence is invalid",
                context.prompt_payload,
                context.response_body,
                context.full_response,
            )
        if finding.get("gate_recommendation") not in ALLOWED_GATE_RECOMMENDATIONS:
            raise_validation_error(
                "semantic finding gate_recommendation is invalid",
                context.prompt_payload,
                context.response_body,
                context.full_response,
            )
        validate_required_string_fields(
            finding,
            ["title", "description", "recommendation", "semantic_evidence"],
            context.prompt_payload,
            context.response_body,
            context.full_response,
        )
        validate_response_paths(
            finding,
            context.package_paths,
            context.prompt_payload,
            context.response_body,
            context.full_response,
        )


def validate_candidate_reviews(
    candidate_reviews: Any,
    required_finding_refs: set[str],
    context: SemanticValidationContext,
) -> None:
    if not isinstance(candidate_reviews, list):
        raise_validation_error(
            "candidate_reviews must be a list",
            context.prompt_payload,
            context.response_body,
            context.full_response,
        )
    seen_refs: set[str] = set()
    for review in candidate_reviews:
        if not isinstance(review, dict):
            raise_validation_error(
                "candidate review must be an object",
                context.prompt_payload,
                context.response_body,
                context.full_response,
            )
        finding_ref = review.get("finding_ref")
        if not isinstance(finding_ref, str) or finding_ref not in required_finding_refs:
            raise_validation_error(
                "candidate review finding_ref is invalid",
                context.prompt_payload,
                context.response_body,
                context.full_response,
            )
        if finding_ref in seen_refs:
            raise_validation_error(
                "candidate review finding_ref is duplicated",
                context.prompt_payload,
                context.response_body,
                context.full_response,
            )
        seen_refs.add(finding_ref)
        if review.get("disposition") not in ALLOWED_CANDIDATE_REVIEW_DISPOSITIONS:
            raise_validation_error(
                "candidate review disposition is invalid",
                context.prompt_payload,
                context.response_body,
                context.full_response,
            )
        if review.get("final_severity") not in ALLOWED_CANDIDATE_SEVERITIES:
            raise_validation_error(
                "candidate review final_severity is invalid",
                context.prompt_payload,
                context.response_body,
                context.full_response,
            )
        if review.get("final_gate_recommendation") not in ALLOWED_GATE_RECOMMENDATIONS:
            raise_validation_error(
                "candidate review final_gate_recommendation is invalid",
                context.prompt_payload,
                context.response_body,
                context.full_response,
            )
        if review.get("confidence") not in ALLOWED_CONFIDENCES:
            raise_validation_error(
                "candidate review confidence is invalid",
                context.prompt_payload,
                context.response_body,
                context.full_response,
            )
        validate_required_string_fields(
            review,
            ["rationale", "semantic_evidence"],
            context.prompt_payload,
            context.response_body,
            context.full_response,
        )
        validate_non_downgradable_policy(review, context)
        validate_response_paths(
            review,
            context.package_paths,
            context.prompt_payload,
            context.response_body,
            context.full_response,
        )
    if seen_refs != required_finding_refs:
        raise_validation_error(
            "candidate reviews do not cover all required finding refs",
            context.prompt_payload,
            context.response_body,
            context.full_response,
        )


def validate_required_string_fields(
    item: dict[str, Any],
    field_names: list[str],
    prompt_payload: dict[str, Any],
    response_body: Any,
    full_response: Any,
) -> None:
    for field_name in field_names:
        if not isinstance(item.get(field_name), str) or not item.get(field_name, "").strip():
            raise_validation_error(
                f"semantic response field {field_name} is missing",
                prompt_payload,
                response_body,
                full_response,
            )


def validate_non_downgradable_policy(review: dict[str, Any], context: SemanticValidationContext) -> None:
    rule_findings = context.prompt_payload.get("rule_findings")
    if not isinstance(rule_findings, list):
        return
    prompt_finding = next(
        (
            finding
            for finding in rule_findings
            if isinstance(finding, dict) and finding.get("finding_ref") == review.get("finding_ref")
        ),
        None,
    )
    if not isinstance(prompt_finding, dict):
        return
    policy = prompt_finding.get("policy")
    if not isinstance(policy, dict) or policy.get("allow_semantic_downgrade") is not False:
        return
    original_severity = prompt_finding.get("severity")
    original_gate = prompt_finding.get("gate_recommendation")
    severity_downgraded = (
        original_severity in SEVERITY_RANK
        and SEVERITY_RANK.get(review.get("final_severity"), -1) < SEVERITY_RANK[original_severity]
    )
    gate_downgraded = (
        original_gate in GATE_RANK
        and GATE_RANK.get(review.get("final_gate_recommendation"), -1) < GATE_RANK[original_gate]
    )
    if severity_downgraded or gate_downgraded:
        raise_validation_error(
            "candidate review violates non-downgradable policy",
            context.prompt_payload,
            context.response_body,
            context.full_response,
        )


def validate_response_paths(
    item: dict[str, Any],
    package_paths: set[str],
    prompt_payload: dict[str, Any],
    response_body: Any,
    full_response: Any,
) -> None:
    related_files = item.get("related_files")
    if related_files is not None:
        if not isinstance(related_files, list):
            raise_validation_error(
                "semantic response related_files contains unknown paths",
                prompt_payload,
                response_body,
                full_response,
            )
        if any(not is_known_package_path(path, package_paths) for path in related_files):
            raise_validation_error(
                "semantic response related_files contains unknown paths",
                prompt_payload,
                response_body,
                full_response,
            )
    source_location = item.get("source_location")
    if source_location is None:
        return
    if not isinstance(source_location, dict):
        raise_validation_error(
            "semantic response source_location is invalid",
            prompt_payload,
            response_body,
            full_response,
        )
    if source_location.get("file") in {None, ""}:
        return
    if not is_known_package_path(source_location.get("file"), package_paths):
        raise_validation_error(
            "semantic response source_location contains an unknown path",
            prompt_payload,
            response_body,
            full_response,
        )


def is_known_package_path(value: Any, package_paths: set[str]) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return normalize_archive_path(value) in package_paths


def build_check_section_map() -> dict[str, str]:
    return {check["check_id"]: section["key"] for section in list_review_sections() for check in section["checks"]}


def raise_validation_error(
    message: str,
    prompt_payload: dict[str, Any],
    response_body: Any,
    full_response: Any,
) -> None:
    raise SkillReviewSemanticRuntimeError(
        message,
        request_body=prompt_payload,
        response_body=response_body,
        response_content=json.dumps(full_response, ensure_ascii=False),
    )
