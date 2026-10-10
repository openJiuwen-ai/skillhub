# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import math
from dataclasses import dataclass, field
from time import perf_counter
from typing import Dict, Iterable, List, Sequence

try:
    import numpy as np
except ModuleNotFoundError:
    np = None  # type: ignore[assignment]

from indexing.embedding.index import (
    EmbeddingClient,
    EmbeddingIndex,
    EmbeddingRecord,
    IndexedEmbeddingRecord,
    OpenAIEmbeddingClient,
    build_embedding_index,
    build_embedding_index_from_jsonl,
    create_openai_embedding_client,
    deserialize_faiss_index,
)
from indexing.embedding.io import load_embedding_index, load_embedding_records_jsonl, save_embedding_index
from retrieval.truncation import ScoreTruncationConfig, truncate_sorted_by_score


@dataclass(frozen=True)
class EmbeddingHit:
    rank: int
    choice_id: str
    payload: str
    score: float
    text: str
    description: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingFinderConfig:
    top_k: int = 10
    batch_size: int = 64
    min_score: float | None = None
    relative_min_score: float | None = None
    autocut_jumps: int = 0
    autocut_min_relative_drop: float = 0.0


@dataclass
class EmbeddingFinderResult:
    hits: List[EmbeddingHit]
    query_text: str
    elapsed_ms: float
    truncation: Dict[str, object] = field(default_factory=dict)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError(f"Vector dimensions must match, got {len(left)} and {len(right)}")
    left_norm = math.sqrt(sum(float(value) * float(value) for value in left))
    right_norm = math.sqrt(sum(float(value) * float(value) for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    dot = sum(float(lv) * float(rv) for lv, rv in zip(left, right))
    return dot / (left_norm * right_norm)


def normalize_query_text(query: str | Sequence[Dict[str, str]]) -> str:
    if isinstance(query, str):
        text = str(query).strip()
        return text if text else "(empty query)"
    lines: List[str] = []
    for item in query:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if not content or role == "system":
            continue
        lines.append(content)
    return "\n".join(lines) if lines else "(empty query)"


class EmbeddingFinder:
    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient,
        index: EmbeddingIndex,
        config: EmbeddingFinderConfig | None = None,
    ) -> None:
        self._embedding_client = embedding_client
        self._index = index
        self._config = config or EmbeddingFinderConfig()
        self._faiss_index = deserialize_faiss_index(index)

    def retrieve_top_k(
        self,
        *,
        query: str | Sequence[Dict[str, str]],
        top_k: int | None = None,
        exclude_payloads: Iterable[str] = (),
    ) -> EmbeddingFinderResult:
        started = perf_counter()
        query_text = normalize_query_text(query)
        if not self._index.records:
            return EmbeddingFinderResult(hits=[], query_text=query_text,
                                         elapsed_ms=round((perf_counter() - started) * 1000, 2))
        query_vector = self._embedding_client.embed_text(query_text)
        excluded = {str(value) for value in exclude_payloads}
        resolved_top_k = max(1, int(top_k if top_k is not None else self._config.top_k))
        hits, truncation = self._search_faiss(query_vector=query_vector, top_k=resolved_top_k, excluded=excluded)
        return EmbeddingFinderResult(
            hits=hits,
            query_text=query_text,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
            truncation=truncation,
        )

    def _search_faiss(self, *, query_vector: Sequence[float], top_k: int,
                      excluded: set[str]) -> tuple[List[EmbeddingHit], Dict[str, object]]:
        if self._faiss_index is None:
            return self._finalize_hits(
                self._fallback_linear_search(query_vector=query_vector, top_k=top_k, excluded=excluded),
                top_k=top_k,
            )
        query_values = [self._normalize_vector(query_vector)]
        query = np.asarray(query_values, dtype="float32") if np is not None else query_values
        search_k = min(len(self._index.records), max(top_k + len(excluded), top_k))
        scores, indices = self._faiss_index.search(query, search_k)
        hits: List[EmbeddingHit] = []
        seen: set[str] = set()
        for score, index in zip(scores[0], indices[0]):
            if index < 0 or index >= len(self._index.records):
                continue
            record = self._index.records[int(index)]
            if record.payload in excluded or record.payload in seen:
                continue
            seen.add(record.payload)
            hits.append(
                EmbeddingHit(
                    rank=len(hits) + 1,
                    choice_id=record.choice_id,
                    payload=record.payload,
                    score=float(score),
                    text=record.text,
                    description=record.description,
                    metadata=dict(record.metadata or {}),
                )
            )
            if len(hits) >= top_k:
                break
        if len(hits) >= top_k:
            return self._finalize_hits(hits, top_k=top_k)
        completed = hits + self._fallback_linear_search(
            query_vector=query_vector,
            top_k=top_k - len(hits),
            excluded=excluded.union({hit.payload for hit in hits}),
            rank_offset=len(hits),
        )
        return self._finalize_hits(completed, top_k=top_k)

    def _fallback_linear_search(
        self,
        *,
        query_vector: Sequence[float],
        top_k: int,
        excluded: set[str],
        rank_offset: int = 0,
    ) -> List[EmbeddingHit]:
        pairs: List[EmbeddingHit] = []
        for record in self._index.records:
            if record.payload in excluded:
                continue
            score = cosine_similarity(query_vector, record.vector)
            pairs.append(
                EmbeddingHit(
                    rank=0,
                    choice_id=record.choice_id,
                    payload=record.payload,
                    score=score,
                    text=record.text,
                    description=record.description,
                    metadata=dict(record.metadata or {}),
                )
            )
        pairs.sort(key=lambda item: (-item.score, item.choice_id))
        return [
            EmbeddingHit(
                rank=rank_offset + index,
                choice_id=hit.choice_id,
                payload=hit.payload,
                score=hit.score,
                text=hit.text,
                description=hit.description,
                metadata=dict(hit.metadata or {}),
            )
            for index, hit in enumerate(pairs[:top_k], start=1)
        ]

    @staticmethod
    def _normalize_vector(values: Sequence[float]) -> list[float]:
        raw = [float(value) for value in values]
        norm = math.sqrt(sum(value * value for value in raw))
        if norm > 0.0:
            return [value / norm for value in raw]
        return raw

    def _finalize_hits(self, hits: List[EmbeddingHit], *, top_k: int) -> tuple[List[EmbeddingHit], Dict[str, object]]:
        truncated, decision = truncate_sorted_by_score(
            hits,
            score_getter=lambda hit: float(hit.score),
            config=ScoreTruncationConfig(
                min_score=self._config.min_score,
                relative_min_score=self._config.relative_min_score,
                autocut_jumps=self._config.autocut_jumps,
                autocut_min_relative_drop=self._config.autocut_min_relative_drop,
            ),
        )
        normalized_hits = [
            EmbeddingHit(
                rank=index,
                choice_id=hit.choice_id,
                payload=hit.payload,
                score=hit.score,
                text=hit.text,
                description=hit.description,
                metadata=dict(hit.metadata or {}),
            )
            for index, hit in enumerate(truncated[:top_k], start=1)
        ]
        return normalized_hits, {
            **decision.to_dict(),
            "requested_top_k": top_k,
            # These are rejects within the vector top_k window, not the entire corpus.
            "score_rejected_samples": [
                {"choice_id": hit.choice_id, "payload": hit.payload, "score": hit.score}
                for hit in hits[len(truncated):len(truncated) + 10]
            ],
            "score_rejected_omitted_count": max(0, len(hits) - len(truncated) - 10),
        }


__all__ = [
    "EmbeddingClient",
    "EmbeddingFinder",
    "EmbeddingFinderConfig",
    "EmbeddingFinderResult",
    "EmbeddingHit",
    "EmbeddingIndex",
    "EmbeddingRecord",
    "IndexedEmbeddingRecord",
    "OpenAIEmbeddingClient",
    "build_embedding_index",
    "build_embedding_index_from_jsonl",
    "cosine_similarity",
    "create_openai_embedding_client",
    "deserialize_faiss_index",
    "load_embedding_index",
    "load_embedding_records_jsonl",
    "normalize_query_text",
    "save_embedding_index",
]
