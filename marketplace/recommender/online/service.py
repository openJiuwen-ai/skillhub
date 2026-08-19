"""Recommendation retrieval service (history -> Milvus -> MMR -> install TopK)."""

from __future__ import annotations

import logging

from recommender.online.mmr import mmr_rerank
from recommender.online.redis_seeds import load_topk_install_items, load_user_seed_ids
from recommender.online.search import (
    fetch_embeddings_by_ids,
    get_loaded_collection,
    merge_max_score,
    search_vectors,
)
from recommender.online.types import RecommendItem

logger = logging.getLogger(__name__)

_model = None

SOURCE_USER_HISTORY = "user_history"
SOURCE_TOPK_INSTALL = "topk_install"


def _get_model():
    global _model
    if _model is None:
        from recommender.offline.milvus_index.embedding import make_embedding_model

        _model = make_embedding_model()
    return _model


def recommend_by_ids(
    asset_ids: list[str],
    top_k: int,
    *,
    collection=None,
    exclude_ids: set[str] | None = None,
    category_id: str | None = None,
    plugin_type: str | None = None,
) -> list[RecommendItem]:
    """
    Use embeddings of seed asset_ids as queries, search Milvus,
    dedupe by max score, exclude history/seeds, return top_k.
    """
    seeds = [str(x).strip() for x in asset_ids if str(x).strip()]
    top_k = max(1, int(top_k))
    if not seeds:
        return []

    collection = collection or get_loaded_collection()
    emb_map = fetch_embeddings_by_ids(collection, seeds)
    missing = [aid for aid in seeds if aid not in emb_map]
    if missing:
        logger.warning("seed asset_ids missing in Milvus: %s", missing)
    vectors = [emb_map[aid] for aid in seeds if aid in emb_map]
    if not vectors:
        return []

    exclude = set(exclude_ids or ())
    exclude.update(seeds)

    search_limit = top_k + len(exclude)
    hits = search_vectors(
        collection,
        vectors,
        top_k=search_limit,
        category_id=category_id,
        plugin_type=plugin_type,
    )
    return merge_max_score(hits, exclude_ids=exclude, top_k=top_k)


def recommend_by_queries(
    queries: list[str],
    top_k: int,
    *,
    collection=None,
    model=None,
    category_id: str | None = None,
    plugin_type: str | None = None,
) -> list[RecommendItem]:
    from recommender.offline.milvus_index.embedding import embed_texts

    texts = [str(q).strip() for q in queries if str(q).strip()]
    top_k = max(1, int(top_k))
    if not texts:
        return []

    collection = collection or get_loaded_collection()
    model = model or _get_model()
    vectors = embed_texts(model, texts)
    hits = search_vectors(
        collection,
        vectors,
        top_k=top_k,
        category_id=category_id,
        plugin_type=plugin_type,
    )
    return merge_max_score(hits, exclude_ids=None, top_k=top_k)


def rerank_mmr(
    items: list[RecommendItem] | list[dict],
    *,
    top_k: int | None = None,
    collection=None,
    lambda_: float | None = None,
) -> list[RecommendItem]:
    normalized: list[RecommendItem] = []
    for raw in items:
        if isinstance(raw, RecommendItem):
            normalized.append(raw)
        else:
            aid = str(raw.get("asset_id", "")).strip()
            if not aid:
                continue
            normalized.append(RecommendItem(asset_id=aid, score=float(raw.get("score", 0.0))))

    if not normalized:
        return []

    collection = collection or get_loaded_collection()
    emb_map = fetch_embeddings_by_ids(collection, [it.asset_id for it in normalized])
    missing = [it.asset_id for it in normalized if it.asset_id not in emb_map]
    if missing:
        logger.warning("rerank candidates missing in Milvus: %s", missing)

    limit = len(normalized) if top_k is None else max(1, int(top_k))
    return mmr_rerank(normalized, emb_map, top_k=limit, lambda_=lambda_)


def recommend_for_user(
    *,
    user_id: str,
    top_k: int,
    request_id: str = "",
    timestamp: int | float | None = None,
    collection=None,
    category_id: str | None = None,
    plugin_type: str | None = None,
) -> tuple[list[RecommendItem], str]:
    """
    Online cascade:
      1) Redis user history -> Milvus by_ids (exclude full history) -> MMR
      2) else Redis install-count TopK (history filtered, optional category / plugin_type)
    """
    top_k = max(1, int(top_k))
    uid = str(user_id or "").strip()
    cid = (category_id or "").strip() or None
    ptype = (plugin_type or "").strip() or None
    logger.info(
        "recommend_for_user request_id=%s user_id=%s timestamp=%s top_k=%s category_id=%s plugin_type=%s",
        request_id or "",
        uid,
        timestamp,
        top_k,
        cid or "",
        ptype or "",
    )

    history = load_user_seed_ids(uid) if uid else []
    history_set = set(history)

    if history:
        # Cap over-fetch: 3x of list top_k (often 200) forces huge Milvus search + MMR
        # embedding fetches on first paint. Keep a modest diversify buffer instead.
        over_fetch = min(max(top_k + 64, top_k), top_k * 2)
        try:
            coll = collection or get_loaded_collection()
            candidates = recommend_by_ids(
                history,
                over_fetch,
                collection=coll,
                exclude_ids=history_set,
                category_id=cid,
                plugin_type=ptype,
            )
        except Exception:
            logger.exception("recommend_for_user: milvus recall failed; fallback topk_install")
            candidates = []
            coll = collection

        if candidates:
            try:
                items = rerank_mmr(candidates, top_k=top_k, collection=coll)
            except Exception:
                logger.exception("recommend_for_user: mmr failed; use recall order")
                items = candidates[:top_k]
            if items:
                return items, SOURCE_USER_HISTORY
        logger.info("recommend_for_user: user_history empty after recall/mmr; try topk_install")

    # Fallback: install-count ranking (Redis snapshot), capped to requested top_k.
    # Passing 0 used to dump the full catalog into the list hydrate path.
    return (
        load_topk_install_items(
            top_k,
            exclude_ids=history_set,
            category_id=cid,
            plugin_type=ptype,
        ),
        SOURCE_TOPK_INSTALL,
    )
