"""Read Redis snapshots used as recommendation seeds / fallbacks."""

from __future__ import annotations

import json
import logging
from typing import Any

from recommender.offline.redis_sync.client import create_redis_client
from recommender.offline.redis_sync.tasks.user_sequences import (
    KIND_DOWNLOAD,
    KIND_LIKE,
    KIND_STAR,
    user_seq_index_key,
    user_seq_key,
)
from recommender.online.search import parse_plugin_types
from recommender.online.types import RecommendItem
from recommender.shared.config import RedisConfig, load_config

logger = logging.getLogger(__name__)

_KIND_ORDER = (KIND_DOWNLOAD, KIND_LIKE, KIND_STAR)


def _redis(cfg: RedisConfig | None = None):
    cfg = cfg or load_config().redis
    client = create_redis_client(cfg)
    client.ping()
    return client, cfg


def load_user_seed_ids(
    user_id: str,
    *,
    redis_cfg: RedisConfig | None = None,
    max_seeds: int = 50,
) -> list[str]:
    uid = str(user_id or "").strip()
    if not uid:
        return []

    client, cfg = _redis(redis_cfg)
    prefix = cfg.user_seq.key_prefix.rstrip(":")
    if not client.sismember(user_seq_index_key(prefix), uid):
        return []

    seen: set[str] = set()
    out: list[str] = []

    for kind in _KIND_ORDER:
        raw = client.get(user_seq_key(prefix, uid, kind))
        if not raw:
            continue
        try:
            seq = json.loads(raw)
        except Exception:
            logger.warning("invalid user seq json key=%s", user_seq_key(prefix, uid, kind))
            continue
        if not isinstance(seq, list):
            continue
        for aid in reversed(seq):
            s = str(aid).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= max_seeds:
                return out

    return out


def load_topk_install_items(
    top_k: int,
    *,
    redis_cfg: RedisConfig | None = None,
    exclude_ids: set[str] | None = None,
    category_id: str | None = None,
    plugin_type: str | None = None,
) -> list[RecommendItem]:
    """Load install-count ranking from Redis.

    top_k<=0 means return the full snapshot (tests / explicit dump).
    Online recommend_for_user always passes a positive top_k.
    category_id / plugin_type filter items that carry those fields in the snapshot.
    """
    limit = int(top_k)
    exclude = exclude_ids or set()
    cid = (category_id or "").strip()
    plugin_types = parse_plugin_types(plugin_type)
    client, cfg = _redis(redis_cfg)
    key = cfg.topk_install.key
    raw = client.get(key)
    if not raw:
        logger.warning("topk_install key missing: %s", key)
        return []

    try:
        payload: dict[str, Any] = json.loads(raw)
    except Exception:
        logger.warning("invalid topk_install json key=%s", key)
        return []

    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return []

    out: list[RecommendItem] = []
    ranked_rows: list[tuple[int, str]] = []
    for i, row in enumerate(items):
        if not isinstance(row, dict):
            continue
        aid = str(row.get("asset_id") or "").strip()
        if not aid or aid in exclude:
            continue
        if cid:
            row_cid = str(row.get("category_id") or "").strip()
            if row_cid != cid:
                continue
        if plugin_types:
            row_pt = str(row.get("plugin_type") or "").strip().lower()
            if row_pt == "teamskills":
                row_pt = "swarmskill"
            if row_pt not in plugin_types:
                continue
        rank = int(row.get("rank") or (i + 1))
        ranked_rows.append((rank, aid))
        if limit > 0 and len(ranked_rows) >= limit:
            break

    n = len(ranked_rows)
    for i, (rank, aid) in enumerate(ranked_rows):
        score = 1.0 - (max(1, rank) - 1) / max(n, 1)
        out.append(RecommendItem(asset_id=aid, score=float(score)))
    return out
