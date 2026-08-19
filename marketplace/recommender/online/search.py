"""Milvus vector fetch / search helpers."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from recommender.offline.milvus_index.milvus_client import (
    connect_milvus,
    create_vector_index_if_needed,
    ensure_collection,
    load_collection_config,
)
from recommender.online.types import RecommendItem
from recommender.shared.config import load_config

logger = logging.getLogger(__name__)

_SEARCH_PARAMS = {"metric_type": "IP", "params": {"ef": 64}}
_collection_cache: Any | None = None


def get_loaded_collection(*, dim: int = 1024, force_reload: bool = False):
    """Return a process-cached Milvus collection (connect/load once per worker)."""
    global _collection_cache
    if _collection_cache is not None and not force_reload:
        return _collection_cache

    cfg = load_collection_config(dim=dim, recreate=False)
    # List/API path must not wait the offline rebuild's 30s connect timeout.
    connect_milvus(cfg, timeout=5.0)
    collection = ensure_collection(cfg)
    create_vector_index_if_needed(collection)
    _collection_cache = collection
    return collection


def clear_collection_cache() -> None:
    global _collection_cache
    _collection_cache = None


def fetch_embeddings_by_ids(collection: Any, asset_ids: list[str]) -> dict[str, list[float]]:
    ids = [str(x).strip() for x in asset_ids if str(x).strip()]
    if not ids:
        return {}

    out: dict[str, list[float]] = {}
    batch_size = 64
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i + batch_size]
        quoted = ", ".join(f'"{aid}"' for aid in batch)
        rows = collection.query(
            expr=f"asset_id in [{quoted}]",
            output_fields=["asset_id", "embedding"],
        )
        for row in rows:
            aid = str(row["asset_id"])
            emb = row.get("embedding")
            if emb is None:
                continue
            out[aid] = list(emb)
    return out


def _category_expr(category_id: str | None) -> str | None:
    cid = (category_id or "").strip()
    if not cid:
        return None
    return _varchar_eq("category_id", cid)


def parse_plugin_types(raw: str | None) -> list[str]:
    """Normalize comma-separated plugin_type; teamskills -> swarmskill."""
    out: list[str] = []
    for part in str(raw or "").split(","):
        normalized = part.strip().lower()
        if normalized == "teamskills":
            normalized = "swarmskill"
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def _varchar_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _varchar_eq(field: str, value: str) -> str:
    return f"{field} == {_varchar_quote(value)}"


def _plugin_type_expr(plugin_type: str | None) -> str | None:
    types = parse_plugin_types(plugin_type)
    if not types:
        return None
    if len(types) == 1:
        return _varchar_eq("plugin_type", types[0])
    quoted = ", ".join(_varchar_quote(t) for t in types)
    return f"plugin_type in [{quoted}]"


def _search_expr(
    *,
    category_id: str | None = None,
    plugin_type: str | None = None,
) -> str | None:
    parts: list[str] = []
    cat = _category_expr(category_id)
    if cat:
        parts.append(cat)
    pt = _plugin_type_expr(plugin_type)
    if pt:
        parts.append(pt)
    if not parts:
        return None
    return " and ".join(parts)


def search_vectors(
    collection: Any,
    vectors: np.ndarray | list[list[float]],
    *,
    top_k: int,
    category_id: str | None = None,
    plugin_type: str | None = None,
) -> list[list[tuple[str, float]]]:
    if isinstance(vectors, np.ndarray):
        data = vectors.astype(np.float32).tolist()
    else:
        data = vectors
    if not data:
        return []

    limit = max(1, int(top_k))
    expr = _search_expr(category_id=category_id, plugin_type=plugin_type)
    kwargs: dict[str, Any] = {
        "data": data,
        "anns_field": "embedding",
        "param": _SEARCH_PARAMS,
        "limit": limit,
        "output_fields": ["asset_id"],
    }
    if expr:
        kwargs["expr"] = expr
    results = collection.search(**kwargs)
    parsed: list[list[tuple[str, float]]] = []
    for hits in results:
        row: list[tuple[str, float]] = []
        for hit in hits:
            aid = hit.entity.get("asset_id") if hasattr(hit, "entity") else None
            if not aid:
                aid = getattr(hit, "id", None)
            if not aid:
                continue
            row.append((str(aid), float(hit.distance)))
        parsed.append(row)
    return parsed


def merge_max_score(
    hits_per_query: list[list[tuple[str, float]]],
    *,
    exclude_ids: set[str] | None = None,
    top_k: int,
) -> list[RecommendItem]:
    exclude = exclude_ids or set()
    best: dict[str, float] = {}
    for hits in hits_per_query:
        for asset_id, score in hits:
            if asset_id in exclude:
                continue
            prev = best.get(asset_id)
            if prev is None or score > prev:
                best[asset_id] = score

    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)[: max(0, int(top_k))]
    return [RecommendItem(asset_id=aid, score=score) for aid, score in ranked]


def default_milvus_host_port() -> tuple[str, int]:
    m = load_config().milvus
    return m.host, m.port
