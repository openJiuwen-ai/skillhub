"""Redis task: MySQL install_count TopK -> JSON snapshot key."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from recommender.shared.config import DATA_ROOT, AppConfig, DatabaseConfig, RedisConfig, TopKInstallSettings
from recommender.offline.package_sync.db import OFFLINE_STATUS

from ..client import create_redis_client
from ..writer import write_json_snapshot

logger = logging.getLogger(__name__)

TASK_NAME = "topk_install"
RANK_METRIC = "install_count"

SUMMARY_PATH = DATA_ROOT / "last_redis_topk_install.json"


@dataclass(frozen=True)
class RankedSkill:
    rank: int
    asset_id: str
    name: str
    display_name: str
    short_desc: str
    plugin_type: str
    latest_version: str
    install_count: int
    category_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "asset_id": self.asset_id,
            "name": self.name,
            "display_name": self.display_name,
            "short_desc": self.short_desc,
            "plugin_type": self.plugin_type,
            "latest_version": self.latest_version,
            "install_count": self.install_count,
            "category_id": self.category_id,
        }


@dataclass
class TopKInstallSummary:
    task: str
    started_at: str
    finished_at: str
    redis_key: str
    top_k: int
    ttl_seconds: int
    written: int
    metric: str


def fetch_ranked_by_install_count(
    cfg: AppConfig,
    *,
    top_k: int,
) -> list[RankedSkill]:
    sql = """
        SELECT
            a.asset_id,
            a.name,
            a.display_name,
            a.short_desc,
            a.plugin_type,
            a.latest_version,
            a.install_count,
            a.category_id
        FROM market_assets AS a
        WHERE LOWER(COALESCE(a.status, '')) <> %s
          AND a.latest_version IS NOT NULL
          AND a.latest_version <> ''
    """
    params: list[Any] = [OFFLINE_STATUS]

    if cfg.plugin_types:
        placeholders = ", ".join(["%s"] * len(cfg.plugin_types))
        sql += f" AND a.plugin_type IN ({placeholders})"
        params.extend(cfg.plugin_types)

    sql += """
        ORDER BY a.install_count DESC, a.update_time DESC, a.name ASC
    """
    # top_k<=0: full catalog for homepage recommend fallback; else classic TopK
    if int(top_k) > 0:
        sql += " LIMIT %s"
        params.append(max(1, int(top_k)))

    rows = _query(cfg.database, sql, params)
    results: list[RankedSkill] = []
    for idx, row in enumerate(rows, start=1):
        results.append(
            RankedSkill(
                rank=idx,
                asset_id=str(row["asset_id"]),
                name=str(row.get("name") or ""),
                display_name=str(row.get("display_name") or row.get("name") or ""),
                short_desc=str(row.get("short_desc") or ""),
                plugin_type=str(row.get("plugin_type") or ""),
                latest_version=str(row.get("latest_version") or ""),
                install_count=int(row.get("install_count") or 0),
                category_id=str(row.get("category_id") or "").strip(),
            )
        )
    return results


def build_snapshot(items: list[RankedSkill], *, top_k: int) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metric": RANK_METRIC,
        "top_k": top_k,
        "count": len(items),
        "items": [item.to_dict() for item in items],
    }


def run_topk_install(
    app_cfg: AppConfig,
    redis_cfg: RedisConfig | None = None,
    *,
    settings: TopKInstallSettings | None = None,
) -> TopKInstallSummary:
    redis_cfg = redis_cfg or app_cfg.redis
    settings = settings or redis_cfg.topk_install
    started = datetime.now(timezone.utc)

    items = fetch_ranked_by_install_count(app_cfg, top_k=settings.k)
    client = create_redis_client(redis_cfg)
    client.ping()
    write_json_snapshot(
        client,
        key=settings.key,
        payload=build_snapshot(items, top_k=settings.k),
        ttl_seconds=settings.ttl_seconds,
    )

    summary = TopKInstallSummary(
        task=TASK_NAME,
        started_at=started.isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        redis_key=settings.key,
        top_k=settings.k,
        ttl_seconds=settings.ttl_seconds,
        written=len(items),
        metric=RANK_METRIC,
    )
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "Redis task %s done: key=%s written=%s ttl=%ss",
        TASK_NAME,
        summary.redis_key,
        summary.written,
        summary.ttl_seconds,
    )
    return summary


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
