"""Resolve local zip path for an active skill version."""

from __future__ import annotations

from pathlib import Path

from recommender.shared.config import AppConfig
from recommender.offline.package_sync.db import ActiveSkillVersion
from recommender.offline.package_sync.storage import create_s3_client, download_skill_package


def resolve_local_zip(download_dir: Path, skill: ActiveSkillVersion) -> Path | None:
    version_dir = download_dir / skill.asset_id / skill.latest_version
    if not version_dir.is_dir():
        return None
    zips = [p for p in version_dir.glob("*.zip") if not p.name.lower().endswith(".raw.zip")]
    if not zips:
        return None
    preferred = version_dir / f"{skill.name}_{skill.latest_version}.zip"
    if preferred.exists():
        return preferred
    return sorted(zips)[0]


def ensure_skill_zip(cfg: AppConfig, skill: ActiveSkillVersion, *, force: bool = False) -> Path:
    existing = resolve_local_zip(cfg.download_dir, skill)
    if existing and existing.exists() and not force:
        return existing

    s3 = create_s3_client(cfg.storage)
    result = download_skill_package(s3, cfg, skill, force=force)
    if result.error or not result.local_path:
        raise RuntimeError(
            f"download failed asset_id={skill.asset_id} version={skill.latest_version}: {result.error}"
        )
    return result.local_path
