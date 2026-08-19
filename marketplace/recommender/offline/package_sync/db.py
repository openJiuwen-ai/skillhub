"""Query market_assets for non-offline latest versions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from recommender.shared.config import AppConfig, DatabaseConfig

# Asset-level status to exclude from recommendation sync.
OFFLINE_STATUS = "offline"


@dataclass(frozen=True)
class ActiveSkillVersion:
    asset_id: str
    name: str
    display_name: str
    short_desc: str
    plugin_type: str
    status: str
    latest_version: str
    version_id: str
    file_path: str  # MinIO object prefix (item path)
    artifact_sha256: str | None
    category_id: str = ""

    @property
    def item_path(self) -> str:
        """Normalized MinIO prefix used as the download root."""
        return self.file_path.rstrip("/") + "/"

    @property
    def normalized_category_id(self) -> str:
        return (self.category_id or "").strip()

    @property
    def normalized_plugin_type(self) -> str:
        raw = (self.plugin_type or "").strip().lower()
        return "swarmskill" if raw == "teamskills" else raw


def fetch_active_latest_skills(cfg: AppConfig) -> list[ActiveSkillVersion]:
    """
    Return assets whose status is not offline, joined to the latest_version row.

    item_paths are taken from market_asset_versions.file_path.
    """
    sql = """
        SELECT
            a.asset_id,
            a.name,
            a.display_name,
            a.short_desc,
            a.plugin_type,
            a.status,
            a.latest_version,
            a.category_id,
            v.version_id,
            v.file_path,
            v.artifact_sha256
        FROM market_assets AS a
        INNER JOIN market_asset_versions AS v
            ON a.asset_id = v.asset_id
           AND a.latest_version = v.version
        WHERE LOWER(COALESCE(a.status, '')) <> %s
          AND a.latest_version IS NOT NULL
          AND a.latest_version <> ''
          AND v.file_path IS NOT NULL
          AND TRIM(v.file_path) <> ''
    """
    params: list[Any] = [OFFLINE_STATUS]

    if cfg.plugin_types:
        placeholders = ", ".join(["%s"] * len(cfg.plugin_types))
        sql += f" AND a.plugin_type IN ({placeholders})"
        params.extend(cfg.plugin_types)

    sql += " ORDER BY a.name ASC"

    rows = _query(cfg.database, sql, params)
    results: list[ActiveSkillVersion] = []
    for row in rows:
        file_path = (row.get("file_path") or "").strip()
        if not file_path:
            continue
        results.append(
            ActiveSkillVersion(
                asset_id=str(row["asset_id"]),
                name=str(row.get("name") or ""),
                display_name=str(row.get("display_name") or row.get("name") or ""),
                short_desc=str(row.get("short_desc") or ""),
                plugin_type=str(row.get("plugin_type") or ""),
                status=str(row.get("status") or ""),
                latest_version=str(row["latest_version"]),
                version_id=str(row["version_id"]),
                file_path=file_path,
                artifact_sha256=(row.get("artifact_sha256") or None),
                category_id=str(row.get("category_id") or "").strip(),
            )
        )
    return results


def build_item_paths(skills: list[ActiveSkillVersion]) -> list[str]:
    """Deduplicated MinIO prefixes for the given skill versions."""
    seen: set[str] = set()
    paths: list[str] = []
    for skill in skills:
        path = skill.item_path
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


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
