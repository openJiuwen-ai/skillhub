from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

MAX_OBSERVATIONS = 10
MAX_EVIDENCE_PER_OBSERVATION = 3
MAX_EVIDENCE_TEXT_LENGTH = 180
DIMENSION_WEIGHTS = {
    "local_secret_or_state": 5,
    "external_boundary": 4,
    "execution_or_mutation": 4,
    "coverage_uncertainty": 3,
    "supply_chain": 2,
}


def build_semantic_observation_plan(
    *,
    behavior_facts: list[dict[str, Any]] | None = None,
    system_facets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidates = [
        *[candidate for fact in behavior_facts or [] for candidate in candidates_from_behavior_fact(fact)],
        *candidates_from_system_facets(system_facets),
    ]
    clusters = cluster_observation_candidates(candidates)
    observations = [
        build_observation(cluster)
        for cluster in sorted(clusters, key=score_observation_cluster, reverse=True)[:MAX_OBSERVATIONS]
    ]
    return {
        "behavior_fact_count": len(behavior_facts or []),
        "system_tags": system_facets.get("system_tags", []) if system_facets else [],
        "omitted_observation_count": max(0, len(clusters) - len(observations)),
        "observations": observations,
        "behavior_chains": build_behavior_chains(clusters),
    }


def candidates_from_behavior_fact(fact: dict[str, Any]) -> list[dict[str, Any]]:
    dimensions = dimensions_for_behavior_fact(fact)
    if not dimensions:
        return []
    evidence = [build_observation_evidence("behavior_fact", fact["fact_id"], item) for item in fact.get("evidence", [])]
    return [
        {
            "ref": fact["fact_id"],
            "source": "behavior_fact",
            "dimensions": dimensions,
            "confidence": fact.get("confidence", "low"),
            "evidence": evidence,
            "cluster_key": build_cluster_key(evidence),
            "file_label": first_evidence_file(evidence),
        }
    ]


def candidates_from_system_facets(facets: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not facets:
        return []
    candidates: list[dict[str, Any]] = []
    for index, detail in enumerate(facets.get("external_network", {}).get("details", []), start=1):
        candidates.extend(
            candidate_from_single_evidence(
                ref=f"external_network:{index}",
                dimensions=["external_boundary"],
                confidence=detail.get("confidence", "low"),
                evidence=build_observation_evidence("system_facet", f"external_network:{index}", evidence),
            )
            for evidence in detail.get("evidence", [])
        )
    for index, detail in enumerate(facets.get("third_party_dependencies", {}).get("details", []), start=1):
        ref = f"third_party_dependency:{detail.get('package_name', 'unknown')}:{index}"
        candidates.extend(
            candidate_from_single_evidence(
                ref=ref,
                dimensions=["supply_chain"],
                confidence=detail.get("confidence", "low"),
                evidence=build_observation_evidence("system_facet", ref, evidence),
            )
            for evidence in detail.get("evidence", [])
        )
    candidates.extend(coverage_candidates_from_facets(facets))
    return candidates


def coverage_candidates_from_facets(facets: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for record in [item for item in facets.get("coverage", []) if item.get("status") != "completed"]:
        ref = f"facet_coverage:{record.get('analyzer_id', 'unknown')}"
        candidates.append(
            {
                "ref": ref,
                "source": "system_facet",
                "dimensions": ["coverage_uncertainty"],
                "confidence": "high",
                "evidence": [
                    {
                        "source": "system_facet",
                        "ref": ref,
                        "text": trim_evidence_text(record.get("reason") or f"{record.get('analyzer_id')} coverage"),
                    }
                ],
                "cluster_key": "package",
                "file_label": "package",
            }
        )
    return candidates


def candidate_from_single_evidence(
    *,
    ref: str,
    dimensions: list[str],
    confidence: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ref": ref,
        "source": "system_facet",
        "dimensions": dimensions,
        "confidence": confidence,
        "evidence": [evidence],
        "cluster_key": build_cluster_key([evidence]),
        "file_label": first_evidence_file([evidence]),
    }


def dimensions_for_behavior_fact(fact: dict[str, Any]) -> list[str]:
    kind = fact.get("kind")
    if kind == "environment_access":
        return ["local_secret_or_state"]
    if kind == "network_access":
        return ["external_boundary"]
    if kind == "package_or_artifact_fetch":
        return ["external_boundary", "supply_chain"]
    if kind == "bulk_remote_requests":
        return ["external_boundary", "execution_or_mutation"]
    if kind == "query_or_template_construction":
        return ["execution_or_mutation"]
    if kind == "resource_exhaustion":
        return ["execution_or_mutation"]
    if kind == "filesystem_boundary_access":
        return ["local_secret_or_state", "execution_or_mutation"]
    if kind == "unsafe_object_loading":
        return ["execution_or_mutation"]
    if kind == "execution_amplification_chain":
        return ["external_boundary", "execution_or_mutation"]
    if kind in {"process_execution", "dynamic_code_execution", "filesystem_access", "remote_state_mutation"}:
        return ["execution_or_mutation"]
    if kind == "analyzability_gap":
        return ["coverage_uncertainty"]
    return []


def build_observation_evidence(source: str, ref: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": source,
        "ref": ref,
        **({"file": evidence["file"]} if evidence.get("file") else {}),
        **({"line": evidence["line"]} if isinstance(evidence.get("line"), int) else {}),
        "text": trim_evidence_text(str(evidence.get("text", ""))),
        **({"source_context": evidence["source_context"]} if evidence.get("source_context") else {}),
    }


def cluster_observation_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        candidates_by_key[candidate["cluster_key"]].append(candidate)
    return [
        {"key": key, "file_label": group[0]["file_label"], "candidates": group}
        for key, group in candidates_by_key.items()
    ]


def build_observation(cluster: dict[str, Any]) -> dict[str, Any]:
    risk_dimensions = collect_risk_dimensions(cluster["candidates"])
    evidence = select_representative_evidence(cluster["candidates"], risk_dimensions)
    return {
        "observation_id": f"obs_{cluster['key']}",
        "summary": f"{cluster['file_label']} 聚合到 {len(cluster['candidates'])} 条事实，涉及 {'、'.join(risk_dimensions)}。",
        "risk_dimensions": risk_dimensions,
        "fact_count": len(cluster["candidates"]),
        "omitted_fact_count": max(0, len(cluster["candidates"]) - len(evidence)),
        "evidence": evidence,
        "risk_questions": [question_for_risk_dimension(dimension) for dimension in risk_dimensions],
    }


def build_behavior_chains(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chains: list[dict[str, Any]] = []
    for cluster in clusters:
        chain = build_behavior_chain(cluster)
        if chain:
            chains.append(chain)
    return sorted(chains, key=score_behavior_chain, reverse=True)[:MAX_OBSERVATIONS]


def build_behavior_chain(cluster: dict[str, Any]) -> dict[str, Any] | None:
    risk_dimensions = collect_risk_dimensions(cluster["candidates"])
    if len(risk_dimensions) < 2:
        return None
    evidence = select_representative_evidence(cluster["candidates"], risk_dimensions)
    if len(evidence) < 2:
        return None
    return {
        "chain_id": f"chain_{cluster['key']}_{'_'.join(risk_dimensions)}",
        "summary": f"{cluster['file_label']} 同时出现 {'、'.join(risk_dimensions)} 事实，可能形成跨边界行为链。",
        "risk_dimensions": risk_dimensions,
        "fact_count": len(cluster["candidates"]),
        "omitted_fact_count": max(0, len(cluster["candidates"]) - len(evidence)),
        "evidence": evidence,
        "risk_questions": [question_for_risk_dimension(dimension) for dimension in risk_dimensions],
    }


def select_representative_evidence(
    candidates: list[dict[str, Any]], risk_dimensions: list[str]
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_refs: set[str] = set()
    for dimension in risk_dimensions:
        candidate = select_best_candidate_for_dimension(candidates, dimension, selected_refs)
        if candidate:
            append_candidate_evidence(selected, candidate)
            selected_refs.add(candidate["ref"])
    for candidate in candidates:
        if len(selected) >= MAX_EVIDENCE_PER_OBSERVATION:
            break
        if candidate["ref"] not in selected_refs:
            append_candidate_evidence(selected, candidate)
            selected_refs.add(candidate["ref"])
    return selected


def select_best_candidate_for_dimension(
    candidates: list[dict[str, Any]],
    dimension: str,
    selected_refs: set[str],
) -> dict[str, Any] | None:
    eligible = [item for item in candidates if dimension in item["dimensions"] and item["ref"] not in selected_refs]
    return sorted(eligible, key=score_observation_candidate, reverse=True)[0] if eligible else None


def append_candidate_evidence(selected: list[dict[str, Any]], candidate: dict[str, Any]) -> None:
    remaining_slots = MAX_EVIDENCE_PER_OBSERVATION - len(selected)
    if remaining_slots > 0:
        selected.extend(candidate["evidence"][:remaining_slots])


def collect_risk_dimensions(candidates: list[dict[str, Any]]) -> list[str]:
    dimensions = {dimension for candidate in candidates for dimension in candidate["dimensions"]}
    return sorted(dimensions, key=lambda item: (-DIMENSION_WEIGHTS.get(item, 0), item))


def score_observation_cluster(cluster: dict[str, Any]) -> int:
    dimensions = collect_risk_dimensions(cluster["candidates"])
    dimension_score = sum(DIMENSION_WEIGHTS.get(dimension, 0) for dimension in dimensions)
    confidence_score = max(confidence_weight(candidate["confidence"]) for candidate in cluster["candidates"])
    diversity_bonus = max(0, len(dimensions) - 1) * 2
    volume_score = min(5, len(cluster["candidates"]))
    return dimension_score + confidence_score + diversity_bonus + volume_score


def score_observation_candidate(candidate: dict[str, Any]) -> int:
    linked_evidence_bonus = min(3, len(candidate["evidence"]))
    cross_file_bonus = 2 if count_distinct_evidence_files(candidate["evidence"]) > 1 else 0
    return confidence_weight(candidate["confidence"]) + linked_evidence_bonus + cross_file_bonus


def score_behavior_chain(chain: dict[str, Any]) -> int:
    dimension_score = sum(DIMENSION_WEIGHTS.get(dimension, 0) for dimension in chain["risk_dimensions"])
    return dimension_score + min(3, len(chain["evidence"])) + min(5, chain["fact_count"])


def confidence_weight(confidence: str) -> int:
    return {"high": 3, "medium": 2}.get(confidence, 1)


def build_cluster_key(evidence: list[dict[str, Any]]) -> str:
    return normalize_cluster_key(first_evidence_file(evidence))


def first_evidence_file(evidence: list[dict[str, Any]]) -> str:
    return next((item["file"] for item in evidence if item.get("file")), "package")


def count_distinct_evidence_files(evidence: list[dict[str, Any]]) -> int:
    return len({item.get("file") for item in evidence if item.get("file")})


def question_for_risk_dimension(dimension: str) -> str:
    questions = {
        "local_secret_or_state": "这些事实是否涉及本地状态、凭证、环境变量、用户数据或持久化文件？",
        "external_boundary": "这些事实是否显示数据、命令或依赖跨出了本地或企业授权边界？",
        "execution_or_mutation": "这些事实是否会执行代码、启动进程、删除/修改文件或改变远端状态？",
        "supply_chain": "这些事实是否引入了第三方依赖、远程资源或安装阶段供应链风险？",
        "coverage_uncertainty": "这些事实是否说明审查覆盖不完整，导致风险不能被稳定排除？",
    }
    return questions.get(dimension, f"这些事实的 {dimension} 维度是否构成真实风险？")


def trim_evidence_text(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip())
    if len(normalized) <= MAX_EVIDENCE_TEXT_LENGTH:
        return normalized
    return f"{normalized[: MAX_EVIDENCE_TEXT_LENGTH - 1].rstrip()}..."


def normalize_cluster_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.:/-]+", "_", value.strip().lower()).strip("_")
    return normalized or "package"
