"""Download skill zip packages from MinIO / S3-compatible storage."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from recommender.shared.config import AppConfig, StorageConfig
from recommender.offline.package_sync.db import ActiveSkillVersion

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadResult:
    asset_id: str
    name: str
    version: str
    item_path: str
    object_key: str | None
    local_path: Path | None
    skipped: bool
    error: str | None = None


def create_s3_client(storage: StorageConfig):
    return boto3.client(
        "s3",
        endpoint_url=storage.endpoint,
        aws_access_key_id=storage.access_key,
        aws_secret_access_key=storage.secret_key,
        region_name=storage.region or None,
        use_ssl=storage.use_ssl,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": storage.addressing_style},
        ),
    )


def resolve_zip_object_key(s3, bucket: str, item_path: str, skill_name: str, version: str) -> str | None:
    """
    Under an item_path prefix, pick the primary skill zip.

    Prefer `{name}_{version}.zip`; otherwise first `*.zip` that is not `*.raw.zip`.
    """
    prefix = item_path if item_path.endswith("/") else item_path + "/"
    preferred = f"{prefix}{skill_name}_{version}.zip"

    keys: list[str] = []
    token: str | None = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents") or []:
            key = obj["Key"]
            if key.lower().endswith(".zip") and not key.lower().endswith(".raw.zip"):
                keys.append(key)
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")

    if preferred in keys:
        return preferred
    if keys:
        return sorted(keys)[0]
    return None


def download_skill_package(
    s3,
    cfg: AppConfig,
    skill: ActiveSkillVersion,
    *,
    force: bool = False,
) -> DownloadResult:
    item_path = skill.item_path
    dest_dir = cfg.download_dir / skill.asset_id / skill.latest_version
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        object_key = resolve_zip_object_key(
            s3,
            cfg.storage.bucket,
            item_path,
            skill.name,
            skill.latest_version,
        )
    except ClientError as exc:
        return DownloadResult(
            asset_id=skill.asset_id,
            name=skill.name,
            version=skill.latest_version,
            item_path=item_path,
            object_key=None,
            local_path=None,
            skipped=False,
            error=f"list failed: {exc}",
        )

    if not object_key:
        return DownloadResult(
            asset_id=skill.asset_id,
            name=skill.name,
            version=skill.latest_version,
            item_path=item_path,
            object_key=None,
            local_path=None,
            skipped=False,
            error="no zip object under item_path",
        )

    local_path = dest_dir / Path(object_key).name
    if local_path.exists() and local_path.stat().st_size > 0 and not force:
        return DownloadResult(
            asset_id=skill.asset_id,
            name=skill.name,
            version=skill.latest_version,
            item_path=item_path,
            object_key=object_key,
            local_path=local_path,
            skipped=True,
        )

    try:
        s3.download_file(cfg.storage.bucket, object_key, str(local_path))
    except ClientError as exc:
        return DownloadResult(
            asset_id=skill.asset_id,
            name=skill.name,
            version=skill.latest_version,
            item_path=item_path,
            object_key=object_key,
            local_path=None,
            skipped=False,
            error=f"download failed: {exc}",
        )

    return DownloadResult(
        asset_id=skill.asset_id,
        name=skill.name,
        version=skill.latest_version,
        item_path=item_path,
        object_key=object_key,
        local_path=local_path,
        skipped=False,
    )


def download_all(
    cfg: AppConfig,
    skills: list[ActiveSkillVersion],
    *,
    force: bool = False,
) -> list[DownloadResult]:
    s3 = create_s3_client(cfg.storage)
    results: list[DownloadResult] = []
    for i, skill in enumerate(skills, start=1):
        result = download_skill_package(s3, cfg, skill, force=force)
        results.append(result)
        if result.error:
            logger.warning(
                "[%s/%s] FAIL %s@%s path=%s err=%s",
                i,
                len(skills),
                skill.name,
                skill.latest_version,
                skill.item_path,
                result.error,
            )
        elif result.skipped:
            logger.info(
                "[%s/%s] SKIP %s@%s -> %s",
                i,
                len(skills),
                skill.name,
                skill.latest_version,
                result.local_path,
            )
        else:
            logger.info(
                "[%s/%s] OK   %s@%s <- %s -> %s",
                i,
                len(skills),
                skill.name,
                skill.latest_version,
                result.object_key,
                result.local_path,
            )
    return results
