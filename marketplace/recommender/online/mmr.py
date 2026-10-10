"""Maximal Marginal Relevance (MMR) diversity rerank."""

from __future__ import annotations

import os

import numpy as np

from recommender.online.types import RecommendItem

# Fallback when MARKET_REC_MMR_LAMBDA is unset / invalid.
DEFAULT_MMR_LAMBDA = 0.5


def resolve_mmr_lambda(override: float | None = None) -> float:
    """λ in [0, 1]: higher → more relevance, lower → more diversity."""
    if override is not None:
        return float(np.clip(float(override), 0.0, 1.0))
    raw = (os.getenv("MARKET_REC_MMR_LAMBDA") or "").strip()
    if not raw:
        return DEFAULT_MMR_LAMBDA
    try:
        return float(np.clip(float(raw), 0.0, 1.0))
    except ValueError:
        return DEFAULT_MMR_LAMBDA


def _cosine_matrix(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    normalized = vectors / norms
    return normalized @ normalized.T


def mmr_rerank(
    items: list[RecommendItem],
    embeddings: dict[str, list[float] | np.ndarray],
    *,
    top_k: int | None = None,
    lambda_: float | None = None,
) -> list[RecommendItem]:
    """
    Greedy MMR over candidates that have embeddings.

    MMR(d) = λ * rel(d) - (1-λ) * max_{s in selected} sim(d, s)
    """
    if not items:
        return []

    lambda_ = resolve_mmr_lambda(lambda_)
    limit = len(items) if top_k is None else max(0, int(top_k))
    if limit == 0:
        return []

    with_emb: list[RecommendItem] = []
    without_emb: list[RecommendItem] = []
    for item in items:
        if item.asset_id in embeddings:
            with_emb.append(item)
        else:
            without_emb.append(item)

    if not with_emb:
        return items[:limit]

    ids = [it.asset_id for it in with_emb]
    rel = np.asarray([it.score for it in with_emb], dtype=np.float64)
    mat = np.asarray([np.asarray(embeddings[i], dtype=np.float64) for i in ids])
    sim = _cosine_matrix(mat)

    selected_idx: list[int] = []
    remaining = set(range(len(with_emb)))

    while remaining and len(selected_idx) < limit:
        best_i = None
        best_val = -1e18
        for i in remaining:
            if not selected_idx:
                diversity_pen = 0.0
            else:
                diversity_pen = float(np.max(sim[i, selected_idx]))
            val = lambda_ * float(rel[i]) - (1.0 - lambda_) * diversity_pen
            if val > best_val:
                best_val = val
                best_i = i
        if best_i is None:
            break
        selected_idx.append(best_i)
        remaining.remove(best_i)

    ranked = [with_emb[i] for i in selected_idx]
    if len(ranked) < limit:
        ranked.extend(without_emb[:limit - len(ranked)])
    return ranked
