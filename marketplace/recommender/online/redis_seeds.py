"""Read Redis snapshots used as recommendation seeds / fallbacks."""

from __future__ import annotations

import json
import logging
import threading
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

_client_lock = threading.Lock()
_clients: dict[tuple, Any] = {}
_cfg_lock = threading.Lock()
_cached_cfg: RedisConfig | None = None


def _client_key(cfg: RedisConfig) -> tuple:
    return (cfg.host, int(cfg.port), int(cfg.db), bool(cfg.ssl), cfg.password or "")


def reset_redis_clients() -> None:
    global _cached_cfg
    with _client_lock:
        clients = list(_clients.values())
        _clients.clear()
    with _cfg_lock:
        _cached_cfg = None
    for client in clients:
        close = getattr(client, "close", None)
        if close is None:
            continue
        try:
            close()
        except Exception as exc:
            logger.warning("failed to close redis client: %s", exc)


def _forget_client(cfg: RedisConfig, client: Any | None = None) -> None:
    key = _client_key(cfg)
    with _client_lock:
        cached = _clients.get(key)
        if client is not None and cached is not client:
            stale = client
        else:
            stale = _clients.pop(key, None)
    if stale is None:
        return
    close = getattr(stale, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception as exc:
        logger.warning("failed to close stale redis client: %s", exc)


def _redis_cfg(cfg: RedisConfig | None = None) -> RedisConfig:
    """Return an explicit config, or the process-cached one.

    load_config() decrypts DB/Redis/S3 secrets. Online reads must not do that
    on every request once a Redis client is already open.
    """
    global _cached_cfg
    if cfg is not None:
        return cfg
    with _cfg_lock:
        if _cached_cfg is not None:
            return _cached_cfg
        loaded = load_config().redis
        _cached_cfg = loaded
        return loaded


def _redis(cfg: RedisConfig | None = None):
    cfg = _redis_cfg(cfg)
    key = _client_key(cfg)
    with _client_lock:
        client = _clients.get(key)
    if client is not None:
        return client, cfg
    created = create_redis_client(cfg)
    try:
        created.ping()
    except Exception:
        close = getattr(created, "close", None)
        if close is not None:
            try:
                close()
            except Exception as exc:
                logger.warning("failed to close failed redis client: %s", exc)
        raise
    with _client_lock:
        existing = _clients.get(key)
        if existing is not None:
            close = getattr(created, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:
                    logger.warning("failed to close duplicated redis client: %s", exc)
            return existing, cfg
        _clients[key] = created
    return created, cfg


def get_redis_client(cfg: RedisConfig | None = None):
    """Return a (client, cfg) pair, reusing cached clients for the same config."""
    return _redis(cfg)


def _execute_redis(cfg: RedisConfig | None, op):
    cfg = _redis_cfg(cfg)
    client, cfg = _redis(cfg)
    try:
        return op(client, cfg)
    except Exception as exc:
        logger.warning("redis client failed, reconnecting: %s", exc)
        _forget_client(cfg, client)
        client, cfg = _redis(cfg)
        return op(client, cfg)


def load_user_seed_ids(
    user_id: str,
    *,
    redis_cfg: RedisConfig | None = None,
    max_seeds: int = 50,
) -> list[str]:
    uid = str(user_id or "").strip()
    if not uid:
        return []

    def _load(client: Any, cfg: RedisConfig) -> list[str]:
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

    return _execute_redis(redis_cfg, _load)


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

    def _load(client: Any, cfg: RedisConfig):
        return client.get(cfg.topk_install.key), cfg.topk_install.key

    raw, key = _execute_redis(redis_cfg, _load)
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
