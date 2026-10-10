"""Redis task: per-user download / like / star asset_id sequences."""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from recommender.shared.config import DATA_ROOT, AppConfig, DatabaseConfig, RedisConfig, UserSeqSettings

from ..client import create_redis_client
from ..writer import write_json_snapshots_pipeline

logger = logging.getLogger(__name__)

TASK_NAME = "user_sequences"

# Suffixes appended to `{prefix}:{user_id}`
KIND_DOWNLOAD = "download"
KIND_LIKE = "like"
KIND_STAR = "star"
KIND_SUFFIXES = (KIND_DOWNLOAD, KIND_LIKE, KIND_STAR)

INDEX_SUFFIX = "_index"

SUMMARY_PATH = DATA_ROOT / "last_redis_user_sequences.json"


@dataclass
class UserSequencesSummary:
    task: str
    started_at: str
    finished_at: str
    key_prefix: str
    max_len: int
    ttl_seconds: int
    users: int
    download_users: int
    like_users: int
    star_users: int
    keys_written: int
    keys_deleted: int


def user_seq_key(prefix: str, user_id: str, kind: str) -> str:
    """Build lookup key, e.g. skill_rec:user:{uid}:download."""
    return f"{prefix}:{user_id}:{kind}"


def user_seq_index_key(prefix: str) -> str:
    """SET of user_ids currently materialised under this prefix."""
    return f"{prefix}:{INDEX_SUFFIX}"


def fetch_download_sequences(
    db: DatabaseConfig,
    *,
    max_len: int,
) -> dict[str, list[str]]:
    """
    Chronological asset_id download sequences per user (oldest -> newest).

    Keeps at most the latest ``max_len`` downloads per user. Anonymous rows
    (NULL/empty fetch_user_id) are skipped.
    """
    sql = """
        SELECT fetch_user_id, asset_id, create_time
        FROM plugin_fetch_records
        WHERE fetch_user_id IS NOT NULL
          AND TRIM(fetch_user_id) <> ''
        ORDER BY fetch_user_id ASC, create_time ASC, id ASC
    """
    rows = _query(db, sql, [])
    return _tail_by_user(rows, user_field="fetch_user_id", max_len=max_len)


def fetch_interaction_sequences(
    db: DatabaseConfig,
    *,
    action_type: str,
    max_len: int,
) -> dict[str, list[str]]:
    """
    Chronological active like/star asset_id sequences per user.

    Interactions are toggle-state rows; ordering uses create_time then id.
    """
    sql = """
        SELECT user_id, asset_id, create_time
        FROM market_asset_interactions
        WHERE action_type = %s
          AND user_id IS NOT NULL
          AND TRIM(user_id) <> ''
        ORDER BY user_id ASC, create_time ASC, id ASC
    """
    rows = _query(db, sql, [action_type])
    return _tail_by_user(rows, user_field="user_id", max_len=max_len)


def _tail_by_user(
    rows: list[dict[str, Any]],
    *,
    user_field: str,
    max_len: int,
) -> dict[str, list[str]]:
    limit = max(1, max_len)
    grouped: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=limit))
    for row in rows:
        uid = str(row.get(user_field) or "").strip()
        asset_id = str(row.get("asset_id") or "").strip()
        if not uid or not asset_id:
            continue
        grouped[uid].append(asset_id)
    return {uid: list(seq) for uid, seq in grouped.items()}


def run_user_sequences(
    app_cfg: AppConfig,
    redis_cfg: RedisConfig | None = None,
    *,
    settings: UserSeqSettings | None = None,
) -> UserSequencesSummary:
    redis_cfg = redis_cfg or app_cfg.redis
    settings = settings or redis_cfg.user_seq
    started = datetime.now(timezone.utc)

    downloads = fetch_download_sequences(app_cfg.database, max_len=settings.max_len)
    likes = fetch_interaction_sequences(
        app_cfg.database, action_type=KIND_LIKE, max_len=settings.max_len
    )
    stars = fetch_interaction_sequences(
        app_cfg.database, action_type=KIND_STAR, max_len=settings.max_len
    )

    prefix = settings.key_prefix.rstrip(":")
    active_users: set[str] = set(downloads) | set(likes) | set(stars)
    payloads: dict[str, list[str]] = {}
    for uid in active_users:
        payloads[user_seq_key(prefix, uid, KIND_DOWNLOAD)] = downloads.get(uid, [])
        payloads[user_seq_key(prefix, uid, KIND_LIKE)] = likes.get(uid, [])
        payloads[user_seq_key(prefix, uid, KIND_STAR)] = stars.get(uid, [])

    client = create_redis_client(redis_cfg)
    client.ping()

    keys_written = write_json_snapshots_pipeline(
        client,
        items=payloads,
        ttl_seconds=settings.ttl_seconds,
    )
    keys_deleted = _gc_stale_users(client, prefix=prefix, active_users=active_users)

    # Refresh index SET of materialised users (no TTL; GC above keeps it in sync).
    index_key = user_seq_index_key(prefix)
    pipe = client.pipeline(transaction=False)
    pipe.delete(index_key)
    if active_users:
        pipe.sadd(index_key, *sorted(active_users))
    pipe.execute()

    summary = UserSequencesSummary(
        task=TASK_NAME,
        started_at=started.isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        key_prefix=prefix,
        max_len=settings.max_len,
        ttl_seconds=settings.ttl_seconds,
        users=len(active_users),
        download_users=len(downloads),
        like_users=len(likes),
        star_users=len(stars),
        keys_written=keys_written,
        keys_deleted=keys_deleted,
    )
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "Redis task %s done: users=%s keys_written=%s keys_deleted=%s prefix=%s",
        TASK_NAME,
        summary.users,
        summary.keys_written,
        summary.keys_deleted,
        summary.key_prefix,
    )
    return summary


def _gc_stale_users(client, *, prefix: str, active_users: set[str]) -> int:
    """Delete sequence keys for users present in the previous index but not now."""
    index_key = user_seq_index_key(prefix)
    previous = {str(u) for u in (client.smembers(index_key) or set())}
    stale = previous - active_users
    if not stale:
        return 0
    keys: list[str] = []
    for uid in stale:
        for kind in KIND_SUFFIXES:
            keys.append(user_seq_key(prefix, uid, kind))
    if keys:
        client.delete(*keys)
    logger.info("Redis GC stale user_seq users=%s keys=%s", len(stale), len(keys))
    return len(keys)


def _query(db: DatabaseConfig, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    conn = pymysql.connect(
        host=db.host,
        port=db.port,
        user=db.user,
        password=db.password,
        database=db.name,
        charset="utf8mb4",
        cursorclass=DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()
