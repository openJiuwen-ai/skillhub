from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SectionKey = Literal["auditability", "instruction", "data_asset", "execution", "authorization", "supply_chain"]
Severity = Literal["high", "medium", "low", "info"]
Confidence = Literal["high", "medium", "low"]
GateRecommendation = Literal["block", "attention", "info"]
SourceType = Literal["system", "rule", "agent", "scanner"]


@dataclass(frozen=True)
class SkillReviewInput:
    skill_name: str
    category: str
    description: str
    tags: list[str]
    package_name: str
    package_size: int


@dataclass(frozen=True)
class PackageFileEntry:
    path: str
    size: int


@dataclass(frozen=True)
class ReviewFindingDraft:
    source_type: SourceType
    section_key: SectionKey
    check_id: str
    severity: Severity
    confidence: Confidence
    category: str
    capability: str
    title: str
    description: str
    recommendation: str
    gate_recommendation: GateRecommendation
    evidence: list[dict[str, Any]]
    finding_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillReviewExecution:
    review_result: dict[str, Any]
    raw_output: dict[str, Any]
    review_mode: str
    review_engine: str
    model_name: str | None
    policy_version: str
    trace_id: str
