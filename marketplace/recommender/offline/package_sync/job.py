"""One-shot offline sync: SQL -> item_paths -> MinIO zip download."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from recommender.shared.config import AppConfig, load_config
from recommender.offline.package_sync.db import ActiveSkillVersion, build_item_paths, fetch_active_latest_skills
from recommender.offline.package_sync.storage import DownloadResult, download_all

logger = logging.getLogger(__name__)


@dataclass
class SyncSummary:
    started_at: str
    finished_at: str
    total: int
    downloaded: int
    skipped: int
    failed: int
    item_paths: list[str]
    download_dir: str
    failures: list[dict]


def run_offline_sync(
    cfg: AppConfig | None = None,
    *,
    force: bool = False,
    limit: int | None = None,
) -> SyncSummary:
    cfg = cfg or load_config()
    started = datetime.now(timezone.utc)

    skills = fetch_active_latest_skills(cfg)
    if limit is not None:
        skills = skills[: max(0, limit)]

    item_paths = build_item_paths(skills)
    logger.info(
        "Found %s active latest skills (%s unique item_paths); download_dir=%s",
        len(skills),
        len(item_paths),
        cfg.download_dir,
    )

    cfg.download_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(cfg.download_dir, skills, item_paths)

    results = download_all(cfg, skills, force=force)
    summary = _summarize(started, cfg, item_paths, results)
    _write_summary(cfg.download_dir, summary)
    logger.info(
        "Sync done: total=%s downloaded=%s skipped=%s failed=%s",
        summary.total,
        summary.downloaded,
        summary.skipped,
        summary.failed,
    )
    return summary


def _write_manifest(
    download_dir: Path,
    skills: list[ActiveSkillVersion],
    item_paths: list[str],
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(skills),
        "item_paths": item_paths,
        "skills": [
            {
                "asset_id": s.asset_id,
                "name": s.name,
                "display_name": s.display_name,
                "plugin_type": s.plugin_type,
                "status": s.status,
                "latest_version": s.latest_version,
                "item_path": s.item_path,
                "artifact_sha256": s.artifact_sha256,
            }
            for s in skills
        ],
    }
    path = download_dir / "item_paths_manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote manifest: %s", path)


def _summarize(
    started: datetime,
    cfg: AppConfig,
    item_paths: list[str],
    results: list[DownloadResult],
) -> SyncSummary:
    downloaded = sum(1 for r in results if r.local_path and not r.skipped and not r.error)
    skipped = sum(1 for r in results if r.skipped)
    failures = [
        {
            "asset_id": r.asset_id,
            "name": r.name,
            "version": r.version,
            "item_path": r.item_path,
            "error": r.error,
        }
        for r in results
        if r.error
    ]
    return SyncSummary(
        started_at=started.isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        total=len(results),
        downloaded=downloaded,
        skipped=skipped,
        failed=len(failures),
        item_paths=item_paths,
        download_dir=str(cfg.download_dir),
        failures=failures,
    )


def _write_summary(download_dir: Path, summary: SyncSummary) -> None:
    path = download_dir / "last_sync_summary.json"
    path.write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2), encoding="utf-8")
