# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Dict, List, Sequence

from models.retrieval import FinderCandidate, FinderTrace
from retrieval.lexical.bm25 import BM25FinderResult
from retrieval.semantic.embedding import EmbeddingFinderResult
from retrieval.service.models import HybridFusionConfig, HybridFusionMethod
from retrieval.tree.progressive import ProgressiveFinderResult

from ..service.models import RetrieverSearchResult


def adapt_bm25_result(*, method: str, result: BM25FinderResult) -> ProgressiveFinderResult:
    candidates = [
        FinderCandidate(
            rank=hit.rank,
            item_id=hit.choice_id,
            payload=hit.payload,
            branch_path=(method,),
            label=hit.choice_id,
            description=hit.description,
        )
        for hit in result.hits
    ]
    return ProgressiveFinderResult(
        candidates=candidates,
        trace=FinderTrace(),
        candidate_records=[
            {
                "rank": hit.rank,
                "raw_output": hit.choice_id,
                "resolved_payload": hit.payload,
                "valid": True,
                "selected": hit.rank == 1,
                "choice_id": hit.choice_id,
                "score": hit.score,
                "source": method,
            }
            for hit in result.hits
        ],
        summary_lines=[
            (
                f"{hit.rank}. {hit.choice_id} -> {hit.payload} "
                f"(score={hit.score:.4f}, source={method})"
            )
            for hit in result.hits
        ],
        selected_payload=result.hits[0].payload if result.hits else None,
        selected_rank=result.hits[0].rank if result.hits else -1,
        raw_outputs=[],
        request_messages=[{"role": "user", "content": result.query_text}],
        elapsed_ms=result.elapsed_ms,
    )


def adapt_embedding_result(*, method: str, result: EmbeddingFinderResult) -> ProgressiveFinderResult:
    candidates = [
        FinderCandidate(
            rank=hit.rank,
            item_id=hit.choice_id,
            payload=hit.payload,
            branch_path=(method,),
            label=hit.choice_id,
            description=hit.description,
        )
        for hit in result.hits
    ]
    return ProgressiveFinderResult(
        candidates=candidates,
        trace=FinderTrace(),
        candidate_records=[
            {
                "rank": hit.rank,
                "raw_output": hit.choice_id,
                "resolved_payload": hit.payload,
                "valid": True,
                "selected": hit.rank == 1,
                "choice_id": hit.choice_id,
                "score": hit.score,
                "source": method,
            }
            for hit in result.hits
        ],
        summary_lines=[
            (
                f"{hit.rank}. {hit.choice_id} -> {hit.payload} "
                f"(score={hit.score:.4f}, source={method})"
            )
            for hit in result.hits
        ],
        selected_payload=result.hits[0].payload if result.hits else None,
        selected_rank=result.hits[0].rank if result.hits else -1,
        raw_outputs=[],
        request_messages=[{"role": "user", "content": result.query_text}],
        elapsed_ms=result.elapsed_ms,
    )


def merge_progressive_with_backfill(
    *,
    progressive_result: ProgressiveFinderResult,
    embedding_result: ProgressiveFinderResult | None,
    bm25_result: ProgressiveFinderResult | None,
    top_k: int,
    hybrid_config: HybridFusionConfig | None = None,
) -> ProgressiveFinderResult:
    hybrid_secondary = _fuse_hybrid_candidate_records(
        top_k=top_k,
        record_sets=[
            embedding_result.candidate_records if embedding_result is not None else [],
            bm25_result.candidate_records if bm25_result is not None else [],
        ],
        hybrid_config=hybrid_config,
    )
    merged_records = _merge_candidate_records(
        top_k=top_k,
        primary_records=progressive_result.candidate_records,
        secondary_record_sets=[hybrid_secondary] if hybrid_secondary else [],
    )
    if not merged_records:
        return progressive_result

    candidates = [
        FinderCandidate(
            rank=index,
            item_id=str(record.get("choice_id") or record.get("raw_output") or ""),
            payload=str(record.get("resolved_payload") or ""),
            branch_path=(str(record.get("source") or "unknown"),),
            label=str(record.get("choice_id") or record.get("raw_output") or ""),
            description="",
        )
        for index, record in enumerate(merged_records, start=1)
    ]
    summary_lines = [
        (
            f"{index}. {record.get('choice_id') or record.get('raw_output') or ''} -> "
            f"{record.get('resolved_payload') or ''} "
            f"(source={record.get('source') or 'unknown'})"
        )
        for index, record in enumerate(merged_records, start=1)
    ]
    trace = progressive_result.trace
    trace.record(
        "result_backfill",
        node_id="ROOT",
        depth=0,
        detail={
            "progressive_candidates": len(progressive_result.candidate_records),
            "embedding_candidates": len(embedding_result.candidate_records) if embedding_result is not None else 0,
            "bm25_candidates": len(bm25_result.candidate_records) if bm25_result is not None else 0,
            "hybrid_backfill_candidates": len(hybrid_secondary),
            "final_candidates": len(candidates),
        },
    )
    return ProgressiveFinderResult(
        candidates=candidates,
        trace=trace,
        candidate_records=merged_records,
        summary_lines=summary_lines,
        selected_payload=candidates[0].payload if candidates else None,
        selected_rank=1 if candidates else -1,
        raw_outputs=list(progressive_result.raw_outputs),
        request_messages=list(progressive_result.request_messages),
        elapsed_ms=float(progressive_result.elapsed_ms)
        + float(embedding_result.elapsed_ms if embedding_result is not None else 0.0)
        + float(bm25_result.elapsed_ms if bm25_result is not None else 0.0),
    )


def hits_to_search_result(
    *,
    method: str,
    source: str,
    elapsed_ms: float,
    trace_events: List[Dict[str, object]],
    candidate_records: List[Dict[str, object]],
) -> RetrieverSearchResult:
    payloads = [str(record.get("resolved_payload") or "") for record in candidate_records]
    return RetrieverSearchResult(
        method=method,
        payloads=payloads,
        candidate_records=candidate_records,
        summary_lines=[
            (
                f"{index}. {record.get('choice_id') or record.get('raw_output') or ''} -> "
                f"{record.get('resolved_payload') or ''} (source={source})"
            )
            for index, record in enumerate(candidate_records, start=1)
        ],
        selected_payload=payloads[0] if payloads else None,
        selected_rank=1 if payloads else -1,
        elapsed_ms=float(elapsed_ms),
        trace_events=trace_events,
    )


def merge_search_results(
    *,
    method: str,
    top_k: int,
    primary_result: RetrieverSearchResult,
    secondary_results: Sequence[RetrieverSearchResult],
    hybrid_config: HybridFusionConfig | None = None,
) -> RetrieverSearchResult:
    if _is_pure_embedding_bm25_fusion(primary_result, secondary_results):
        merged_records = _fuse_hybrid_candidate_records(
            top_k=top_k,
            record_sets=[primary_result.candidate_records, *[result.candidate_records for result in secondary_results]],
            hybrid_config=hybrid_config,
        )
    elif primary_result.method == "progressive":
        hybrid_secondary = _fuse_hybrid_candidate_records(
            top_k=top_k,
            record_sets=[result.candidate_records for result in secondary_results],
            hybrid_config=hybrid_config,
        )
        merged_records = _merge_candidate_records(
            top_k=top_k,
            primary_records=primary_result.candidate_records,
            secondary_record_sets=[hybrid_secondary] if hybrid_secondary else [],
        )
    else:
        merged_records = _merge_candidate_records(
            top_k=top_k,
            primary_records=primary_result.candidate_records,
            secondary_record_sets=[result.candidate_records for result in secondary_results],
        )
    payloads = [str(record.get("resolved_payload") or "") for record in merged_records]
    summary_lines = [
        (
            f"{index}. {record.get('choice_id') or record.get('raw_output') or ''} -> "
            f"{record.get('resolved_payload') or ''} "
            f"(source={record.get('source') or 'unknown'})"
        )
        for index, record in enumerate(merged_records, start=1)
    ]
    trace_events = list(primary_result.trace_events)
    for result in secondary_results:
        trace_events.extend(result.trace_events)
    trace_events.append(
        {
            "event_type": "retrieval_plan",
            "node_id": "ROOT",
            "depth": 0,
            "detail": {
                "method": method,
                "top_k": top_k,
                "primary_count": len(primary_result.candidate_records),
                "secondary_counts": [len(result.candidate_records) for result in secondary_results],
                "hybrid_fusion_method": str(
                    hybrid_config.method
                    if hybrid_config is not None
                    else HybridFusionMethod.RRF.value
                ),
                "final_count": len(merged_records),
            },
        }
    )
    return RetrieverSearchResult(
        method=method,
        payloads=payloads,
        candidate_records=merged_records,
        summary_lines=summary_lines,
        selected_payload=payloads[0] if payloads else None,
        selected_rank=1 if payloads else -1,
        elapsed_ms=float(primary_result.elapsed_ms) + sum(float(result.elapsed_ms) for result in secondary_results),
        trace_events=trace_events,
    )


def _merge_candidate_records(
    *,
    top_k: int,
    primary_records: Sequence[Dict[str, object]],
    secondary_record_sets: Sequence[Sequence[Dict[str, object]]],
) -> List[Dict[str, object]]:
    merged_records: List[Dict[str, object]] = []
    seen: set[str] = set()

    def append_records(records: Sequence[Dict[str, object]]) -> None:
        for raw_record in records:
            if not raw_record.get("valid", True):
                continue
            payload = str(raw_record.get("resolved_payload") or "").strip()
            if not payload or payload in seen:
                continue
            seen.add(payload)
            record = dict(raw_record)
            record["rank"] = len(merged_records) + 1
            record["selected"] = len(merged_records) == 0
            merged_records.append(record)
            if len(merged_records) >= top_k:
                return

    append_records(primary_records)
    for records in secondary_record_sets:
        if len(merged_records) >= top_k:
            break
        append_records(records)
    return merged_records[:top_k]


def _is_pure_embedding_bm25_fusion(
    primary_result: RetrieverSearchResult,
    secondary_results: Sequence[RetrieverSearchResult],
) -> bool:
    methods = {str(primary_result.method or "").strip()}
    methods.update(str(result.method or "").strip() for result in secondary_results)
    return methods.issubset({"embedding", "bm25"}) and "embedding" in methods and "bm25" in methods


def _fuse_hybrid_candidate_records(
    *,
    top_k: int,
    record_sets: Sequence[Sequence[Dict[str, object]]],
    hybrid_config: HybridFusionConfig | None,
) -> List[Dict[str, object]]:
    config = hybrid_config or HybridFusionConfig()
    # Zero-weight sources must not contribute candidates, including the single-source shortcut.
    weighted_record_sets = [
        [
            record
            for record in records
            if _hybrid_weight_for_source(str(record.get("source") or "unknown"), config) > 0.0
        ]
        for records in record_sets
    ]
    valid_record_sets = [records for records in weighted_record_sets if records]
    if not valid_record_sets:
        return []
    if len(valid_record_sets) == 1:
        return _merge_candidate_records(top_k=top_k, primary_records=valid_record_sets[0], secondary_record_sets=[])

    method = str(config.method or HybridFusionMethod.RRF.value).strip().lower()
    if method == HybridFusionMethod.RELATIVE_SCORE.value:
        return _relative_score_fusion(top_k=top_k, record_sets=valid_record_sets, config=config)
    return _rrf_fusion(top_k=top_k, record_sets=valid_record_sets, config=config)


def _rrf_fusion(
    *,
    top_k: int,
    record_sets: Sequence[Sequence[Dict[str, object]]],
    config: HybridFusionConfig,
) -> List[Dict[str, object]]:
    aggregate: Dict[str, Dict[str, object]] = {}
    for records in record_sets:
        for raw_record in records:
            if not raw_record.get("valid", True):
                continue
            payload = str(raw_record.get("resolved_payload") or "").strip()
            source = str(raw_record.get("source") or "unknown").strip() or "unknown"
            rank = int(raw_record.get("rank") or 0)
            if not payload or rank <= 0:
                continue
            weight = _hybrid_weight_for_source(source, config)
            score = weight / (max(1, int(config.rrf_k)) + rank)
            bucket = aggregate.setdefault(
                payload,
                {
                    "record": dict(raw_record),
                    "fusion_score": 0.0,
                    "hybrid_sources": set(),
                    "source_ranks": {},
                },
            )
            bucket["fusion_score"] = float(bucket["fusion_score"]) + float(score)
            bucket["hybrid_sources"].add(source)
            bucket["source_ranks"][source] = rank
            chosen_record = dict(bucket["record"])
            if rank < int(chosen_record.get("rank") or 10**9):
                bucket["record"] = dict(raw_record)
    return _finalize_fused_records(top_k=top_k, aggregate=aggregate, fusion_method=HybridFusionMethod.RRF.value)


def _relative_score_fusion(
    *,
    top_k: int,
    record_sets: Sequence[Sequence[Dict[str, object]]],
    config: HybridFusionConfig,
) -> List[Dict[str, object]]:
    aggregate: Dict[str, Dict[str, object]] = {}
    for records in record_sets:
        valid_records = [
            raw_record
            for raw_record in records
            if raw_record.get("valid", True) and str(raw_record.get("resolved_payload") or "").strip()
        ]
        if not valid_records:
            continue
        scores = [float(raw_record.get("score") or 0.0) for raw_record in valid_records]
        min_score = min(scores)
        max_score = max(scores)
        for raw_record in valid_records:
            payload = str(raw_record.get("resolved_payload") or "").strip()
            source = str(raw_record.get("source") or "unknown").strip() or "unknown"
            rank = int(raw_record.get("rank") or 0)
            raw_score = float(raw_record.get("score") or 0.0)
            normalized_score = 1.0 if max_score <= min_score else (
                raw_score - min_score) / max(max_score - min_score, 1e-9)
            weight = _hybrid_weight_for_source(source, config)
            score = weight * normalized_score
            bucket = aggregate.setdefault(
                payload,
                {
                    "record": dict(raw_record),
                    "fusion_score": 0.0,
                    "hybrid_sources": set(),
                    "source_ranks": {},
                },
            )
            bucket["fusion_score"] = float(bucket["fusion_score"]) + float(score)
            bucket["hybrid_sources"].add(source)
            bucket["source_ranks"][source] = rank
            chosen_record = dict(bucket["record"])
            if rank > 0 and rank < int(chosen_record.get("rank") or 10**9):
                bucket["record"] = dict(raw_record)
    return _finalize_fused_records(top_k=top_k, aggregate=aggregate,
                                   fusion_method=HybridFusionMethod.RELATIVE_SCORE.value)


def _finalize_fused_records(
    *,
    top_k: int,
    aggregate: Dict[str, Dict[str, object]],
    fusion_method: str,
) -> List[Dict[str, object]]:
    ranked = sorted(
        aggregate.items(),
        key=lambda item: (
            -float(item[1].get("fusion_score") or 0.0),
            _best_source_rank(item[1]),
            item[0],
        ),
    )
    fused_records: List[Dict[str, object]] = []
    for index, (payload, info) in enumerate(ranked[:top_k], start=1):
        record = dict(info["record"])
        record["rank"] = index
        record["selected"] = index == 1
        record["source"] = "hybrid"
        record["fusion_method"] = fusion_method
        record["fusion_score"] = float(info.get("fusion_score") or 0.0)
        record["hybrid_sources"] = sorted(str(item) for item in set(info.get("hybrid_sources") or set()))
        record["source_ranks"] = {str(key): int(value) for key, value in dict(info.get("source_ranks") or {}).items()}
        record["resolved_payload"] = payload
        fused_records.append(record)
    return fused_records


def _best_source_rank(info: Dict[str, object]) -> int:
    source_ranks = dict(info.get("source_ranks") or {})
    if not source_ranks:
        return 10**9
    best_rank = 10**9
    for rank in source_ranks.values():
        best_rank = min(best_rank, int(rank))
    return best_rank


def _hybrid_weight_for_source(source: str, config: HybridFusionConfig) -> float:
    normalized = str(source or "").strip().lower()
    if normalized == "embedding":
        return max(0.0, float(config.embedding_weight))
    if normalized == "bm25":
        return max(0.0, float(config.bm25_weight))
    return 1.0


__all__ = [
    "adapt_bm25_result",
    "adapt_embedding_result",
    "hits_to_search_result",
    "merge_progressive_with_backfill",
    "merge_search_results",
]
