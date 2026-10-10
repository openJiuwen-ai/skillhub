# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from time import perf_counter
from typing import Dict, Iterable, List, Sequence

from indexing.bm25.index import BM25Index
from retrieval.truncation import ScoreTruncationConfig, truncate_sorted_by_score


_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_CJK_BLOCK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_CAMEL_CASE_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


@dataclass(frozen=True)
class BM25Document:
    choice_id: str
    payload: str
    text: str
    description: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class BM25Hit:
    rank: int
    choice_id: str
    payload: str
    score: float
    text: str
    description: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class BM25FinderConfig:
    top_k: int = 10
    k1: float = 1.5
    b: float = 0.75
    delta: float = 0.5
    min_score: float | None = None
    relative_min_score: float | None = None
    autocut_jumps: int = 0
    autocut_min_relative_drop: float = 0.0
    min_query_term_matches: int = 0
    min_query_term_match_ratio: float = 0.0


@dataclass
class BM25FinderResult:
    hits: List[BM25Hit]
    query_text: str
    elapsed_ms: float
    truncation: Dict[str, object] = field(default_factory=dict)


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


def split_identifier_terms(value: str) -> List[str]:
    text = str(value or "").strip()
    if not text:
        return []
    expanded = _CAMEL_CASE_RE.sub(" ", text)
    expanded = expanded.replace(".", " ").replace("_", " ").replace("-", " ").replace("/", " ")
    return [token.lower() for token in _ASCII_TOKEN_RE.findall(expanded)]


def lexical_tokenize(text: str) -> List[str]:
    raw = str(text or "").strip().lower()
    if not raw:
        return []
    tokens: List[str] = []
    tokens.extend(_ASCII_TOKEN_RE.findall(raw))
    for match in _CJK_BLOCK_RE.finditer(raw):
        block = match.group(0)
        if not block:
            continue
        tokens.append(block)
        if len(block) == 1:
            continue
        tokens.extend(block[index: index + 2] for index in range(len(block) - 1))
    return tokens


def build_bm25_document_text(*, choice_id: str, payload: str, description: str = "", text: str = "") -> str:
    parts: List[str] = []
    clean_choice_id = str(choice_id or "").strip()
    clean_payload = str(payload or "").strip()
    clean_description = str(description or "").strip()
    clean_text = str(text or "").strip()
    if clean_choice_id:
        parts.append(clean_choice_id)
        parts.extend(split_identifier_terms(clean_choice_id))
    if clean_payload:
        parts.append(clean_payload)
        parts.extend(split_identifier_terms(clean_payload))
    if clean_description:
        parts.append(clean_description)
    if clean_text:
        parts.append(clean_text)
    return "\n".join(part for part in parts if str(part).strip())


class BM25Finder:
    def __init__(
        self,
        *,
        documents: Sequence[BM25Document],
        config: BM25FinderConfig | None = None,
    ) -> None:
        self._documents = list(documents)
        self._config = config or BM25FinderConfig()
        self._doc_term_freqs: List[Counter[str]] = []
        self._doc_lengths: List[int] = []
        self._doc_freqs: Counter[str] = Counter()
        self._avg_doc_length = 0.0
        self._build_index()

    @classmethod
    def from_choices(cls, *, choices: Sequence[object], config: BM25FinderConfig | None = None) -> "BM25Finder":
        documents = [
            BM25Document(
                choice_id=str(getattr(choice, "choice_id", "") or ""),
                payload=str(getattr(choice, "payload", "") or ""),
                description=str(getattr(choice, "description", "") or ""),
                text=build_bm25_document_text(
                    choice_id=str(getattr(choice, "choice_id", "") or ""),
                    payload=str(getattr(choice, "payload", "") or ""),
                    description=str(getattr(choice, "description", "") or ""),
                ),
            )
            for choice in choices
            if str(getattr(choice, "choice_id", "") or "").strip() and str(getattr(choice, "payload", "") or "").strip()
        ]
        return cls(documents=documents, config=config)

    @classmethod
    def from_index(cls, *, index: BM25Index, config: BM25FinderConfig | None = None) -> "BM25Finder":
        resolved_config = config or BM25FinderConfig(
            top_k=int((index.config or {}).get("top_k", BM25FinderConfig.top_k)),
            k1=float((index.config or {}).get("k1", BM25FinderConfig.k1)),
            b=float((index.config or {}).get("b", BM25FinderConfig.b)),
            delta=float((index.config or {}).get("delta", BM25FinderConfig.delta)),
            min_score=(float((index.config or {}).get("min_score")) if (
                index.config or {}).get("min_score") is not None else None),
            relative_min_score=(
                float((index.config or {}).get("relative_min_score"))
                if (index.config or {}).get("relative_min_score") is not None
                else None
            ),
            autocut_jumps=int((index.config or {}).get("autocut_jumps", BM25FinderConfig.autocut_jumps)),
            autocut_min_relative_drop=float(
                (index.config or {}).get("autocut_min_relative_drop", BM25FinderConfig.autocut_min_relative_drop)
            ),
            min_query_term_matches=int(
                (index.config or {}).get(
                    "min_query_term_matches",
                    BM25FinderConfig.min_query_term_matches)),
            min_query_term_match_ratio=float(
                (index.config or {}).get("min_query_term_match_ratio", BM25FinderConfig.min_query_term_match_ratio)
            ),
        )
        instance = cls.__new__(cls)
        instance._documents = [
            BM25Document(
                choice_id=document.choice_id,
                payload=document.payload,
                text=document.text,
                description=document.description,
                metadata=dict(document.metadata or {}),
            )
            for document in index.documents
        ]
        instance._config = resolved_config
        instance._doc_term_freqs = [Counter(document.term_freqs or {}) for document in index.documents]
        instance._doc_lengths = [int(document.doc_length) for document in index.documents]
        instance._doc_freqs = Counter({str(term): int(freq) for term, freq in dict(index.doc_freqs or {}).items()})
        instance._avg_doc_length = float(index.avg_doc_length or 0.0)
        return instance

    def retrieve_top_k(
        self,
        *,
        query: str | Sequence[Dict[str, str]],
        top_k: int | None = None,
        exclude_payloads: Iterable[str] = (),
    ) -> BM25FinderResult:
        started = perf_counter()
        query_text = normalize_query_text(query)
        resolved_top_k = max(1, int(top_k if top_k is not None else self._config.top_k))
        if not self._documents:
            return BM25FinderResult(hits=[], query_text=query_text,
                                    elapsed_ms=round((perf_counter() - started) * 1000, 2))
        query_terms = lexical_tokenize(query_text)
        if not query_terms:
            return BM25FinderResult(hits=[], query_text=query_text,
                                    elapsed_ms=round((perf_counter() - started) * 1000, 2))
        unique_query_terms = list(dict.fromkeys(query_terms))
        required_term_matches = _resolve_required_term_matches(
            unique_query_term_count=len(unique_query_terms),
            min_matches=self._config.min_query_term_matches,
            min_ratio=self._config.min_query_term_match_ratio,
        )
        excluded = {str(value) for value in exclude_payloads}
        scored: List[tuple[float, int, BM25Document]] = []
        term_match_rejected_count = 0
        for index, document in enumerate(self._documents):
            if document.payload in excluded:
                continue
            score, matched_terms = self._score_document(index=index, query_terms=query_terms)
            if score <= 0.0:
                continue
            if matched_terms < required_term_matches:
                term_match_rejected_count += 1
                continue
            scored.append((score, matched_terms, document))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2].choice_id, item[2].payload))
        truncated, decision = truncate_sorted_by_score(
            scored,
            score_getter=lambda item: float(item[0]),
            config=ScoreTruncationConfig(
                min_score=self._config.min_score,
                relative_min_score=self._config.relative_min_score,
                autocut_jumps=self._config.autocut_jumps,
                autocut_min_relative_drop=self._config.autocut_min_relative_drop,
            ),
        )
        hits = [
            BM25Hit(
                rank=rank,
                choice_id=document.choice_id,
                payload=document.payload,
                score=float(score),
                text=document.text,
                description=document.description,
                metadata=dict(document.metadata or {}),
            )
            for rank, (score, _, document) in enumerate(truncated[:resolved_top_k], start=1)
        ]
        return BM25FinderResult(
            hits=hits,
            query_text=query_text,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
            truncation={
                **decision.to_dict(),
                "requested_top_k": resolved_top_k,
                "required_query_term_matches": required_term_matches,
                "query_terms": unique_query_terms,
                "term_match_rejected_count": term_match_rejected_count,
                # Only sample the highest-scoring rejects; BM25 may score the entire corpus.
                "score_rejected_samples": [
                    {"choice_id": doc.choice_id, "payload": doc.payload, "score": score}
                    for score, _, doc in scored[len(truncated):len(truncated) + 10]
                ],
                "score_rejected_omitted_count": max(0, len(scored) - len(truncated) - 10),
            },
        )

    def _build_index(self) -> None:
        if not self._documents:
            self._avg_doc_length = 0.0
            return
        total_length = 0
        for document in self._documents:
            terms = lexical_tokenize(document.text)
            term_freqs = Counter(terms)
            self._doc_term_freqs.append(term_freqs)
            doc_length = sum(term_freqs.values())
            self._doc_lengths.append(doc_length)
            total_length += doc_length
            self._doc_freqs.update(term_freqs.keys())
        self._avg_doc_length = total_length / max(1, len(self._documents))

    def _score_document(self, *, index: int, query_terms: Sequence[str]) -> tuple[float, int]:
        term_freqs = self._doc_term_freqs[index]
        doc_length = self._doc_lengths[index]
        score = 0.0
        matched_terms = 0
        query_counts = Counter(query_terms)
        for term, qf in query_counts.items():
            tf = term_freqs.get(term, 0)
            if tf <= 0:
                continue
            matched_terms += 1
            idf = self._idf(term)
            denom = tf + self._config.k1 * (1.0 - self._config.b + self._config.b *
                                            (doc_length / max(self._avg_doc_length, 1e-9)))
            score += qf * idf * (((tf * (self._config.k1 + 1.0)) / max(denom, 1e-9)) + self._config.delta)
        return score, matched_terms

    def _idf(self, term: str) -> float:
        doc_freq = self._doc_freqs.get(term, 0)
        total_docs = len(self._documents)
        return math.log1p((total_docs - doc_freq + 0.5) / (doc_freq + 0.5))


def _resolve_required_term_matches(*, unique_query_term_count: int, min_matches: int, min_ratio: float) -> int:
    if unique_query_term_count <= 0:
        return 0
    required = max(0, int(min_matches))
    if min_ratio > 0.0:
        required = max(required, math.ceil(unique_query_term_count * float(min_ratio)))
    return min(unique_query_term_count, required)


__all__ = [
    "BM25Document",
    "BM25Finder",
    "BM25FinderConfig",
    "BM25FinderResult",
    "BM25Hit",
    "build_bm25_document_text",
    "lexical_tokenize",
    "normalize_query_text",
    "split_identifier_terms",
]
