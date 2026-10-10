"""Recommender configuration loaded from env (MARKET_* / bare aliases)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # marketplace/
DATA_ROOT = ROOT.parent / "data" / "skill_packages"

logger = logging.getLogger(__name__)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env(*names: str, default: str = "") -> str:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and str(raw).strip() != "":
            return str(raw).strip()
    return default


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    name: str


@dataclass(frozen=True)
class StorageConfig:
    bucket: str
    endpoint: str
    access_key: str
    secret_key: str
    region: str
    use_ssl: bool
    addressing_style: str
    storage_type: str = "MinIO"  # MinIO | OBS


@dataclass(frozen=True)
class TopKInstallSettings:
    key: str
    k: int
    ttl_seconds: int
    interval_minutes: int


@dataclass(frozen=True)
class UserSeqSettings:
    key_prefix: str
    max_len: int
    ttl_seconds: int


@dataclass(frozen=True)
class RedisConfig:
    host: str
    port: int
    db: int
    password: str
    ssl: bool
    topk_install: TopKInstallSettings
    user_seq: UserSeqSettings


@dataclass(frozen=True)
class MilvusSettings:
    host: str
    port: int
    collection: str
    batch_size: int
    incremental_interval_minutes: int
    full_rebuild_hour: int
    # Optional; required when Milvus common.security.authorizationEnabled=true
    user: str = ""
    password: str = ""


@dataclass(frozen=True)
class AppConfig:
    database: DatabaseConfig
    storage: StorageConfig
    redis: RedisConfig
    milvus: MilvusSettings
    download_dir: Path
    plugin_types: tuple[str, ...] | None
    sync_interval_minutes: int

    @property
    def interval_minutes(self) -> int:
        return self.sync_interval_minutes


def _resolve_storage_type() -> str:
    raw = _env("STORAGE_TYPE", "MARKET_STORAGE_TYPE", default="MinIO").strip()
    if raw.upper() == "OBS":
        return "OBS"
    return "MinIO"


def _resolve_storage_use_ssl(storage_type: str, endpoint: str) -> bool:
    raw = os.getenv("MARKET_S3_USE_SSL")
    if raw is not None and str(raw).strip() != "":
        return _bool(raw, default=False)
    if storage_type == "OBS":
        return True
    return endpoint.lower().startswith("https://")


def _resolve_addressing_style(storage_type: str) -> str:
    explicit = _env("MARKET_S3_ADDRESSING_STYLE", default="")
    if explicit:
        return explicit
    # Align with plugins_market.core.s3_storage_client
    return "virtual" if storage_type == "OBS" else "path"


def _resolve_redis_ssl() -> bool:
    raw = os.getenv("MARKET_REDIS_SSL")
    if raw is None or str(raw).strip() == "":
        raw = os.getenv("REDIS_SSL")
    if raw is not None and str(raw).strip() != "":
        return _bool(raw, default=False)
    backend = _env("CACHE_BACKEND", "MARKET_CACHE_BACKEND", "REDIS_BACKEND", default="redis").lower()
    return backend in {"dcs", "huawei_dcs", "huaweicloud_dcs"}


def load_config() -> AppConfig:
    plugin_raw = _env("OFFLINE_SYNC_PLUGIN_TYPES", "MARKET_REC_PLUGIN_TYPES", default="")
    plugin_types = (
        tuple(p.strip() for p in plugin_raw.split(",") if p.strip()) if plugin_raw else None
    )

    download_raw = _env(
        "OFFLINE_SYNC_DOWNLOAD_DIR",
        "MARKET_REC_DOWNLOAD_DIR",
        default=str(DATA_ROOT),
    )
    download_dir = Path(download_raw)
    if not download_dir.is_absolute():
        download_dir = ROOT / download_dir

    storage_type = _resolve_storage_type()
    endpoint = _env("MARKET_S3_ENDPOINT", default="http://localhost:9000")

    # Same decrypt path as plugins_market clients / milvus_client / embedding.
    # Offline CLI may lack security bootstrap; fall back to env plaintext.
    try:
        from common.security.security_utils import SecurityUtils

        db_password = (
            SecurityUtils.get_decrypt_secret("DB_PASSWORD", default="")
            or SecurityUtils.get_decrypt_secret("MARKET_DB_PASSWORD", default="")
            or ""
        )
        redis_password = (
            SecurityUtils.get_decrypt_secret("MARKET_REDIS_PASSWORD", default="")
            or SecurityUtils.get_decrypt_secret("REDIS_PASSWORD", default="")
            or ""
        )
        s3_access_key = SecurityUtils.get_decrypt_secret("MARKET_S3_ACCESS_KEY", default="") or ""
        s3_secret_key = SecurityUtils.get_decrypt_secret("MARKET_S3_SECRET_KEY", default="") or ""
    except Exception as exc:
        logger.warning(
            "decrypt recommender secrets failed (%s); fallback to env plaintext",
            exc,
        )
        db_password = _env("DB_PASSWORD", "MARKET_DB_PASSWORD", default="")
        redis_password = _env("MARKET_REDIS_PASSWORD", "REDIS_PASSWORD", default="")
        s3_access_key = _env("MARKET_S3_ACCESS_KEY", default="")
        s3_secret_key = _env("MARKET_S3_SECRET_KEY", default="")

    return AppConfig(
        database=DatabaseConfig(
            host=_env("DB_HOST", "MARKET_DB_HOST", default="localhost"),
            port=int(_env("DB_PORT", "MARKET_DB_PORT", default="3306")),
            user=_env("DB_USER", "MARKET_DB_USER", default="root"),
            password=db_password,
            name=_env("STORE_DB_NAME", "MARKET_STORE_DB_NAME", default="openjiuwen_market"),
        ),
        storage=StorageConfig(
            bucket=_env("MARKET_BUCKET_NAME", default="test"),
            endpoint=endpoint,
            access_key=s3_access_key,
            secret_key=s3_secret_key,
            region=_env("MARKET_S3_REGION", default="") or "",
            use_ssl=_resolve_storage_use_ssl(storage_type, endpoint),
            addressing_style=_resolve_addressing_style(storage_type),
            storage_type=storage_type,
        ),
        redis=RedisConfig(
            host=_env("REDIS_HOST", "MARKET_REDIS_HOST", default="127.0.0.1"),
            port=int(_env("REDIS_PORT", "MARKET_REDIS_PORT", default="6379")),
            db=int(_env("REDIS_DB", "MARKET_REDIS_DB", default="0")),
            password=redis_password,
            ssl=_resolve_redis_ssl(),
            topk_install=TopKInstallSettings(
                key=_env("REDIS_TOPK_INSTALL_KEY", "MARKET_REDIS_TOPK_INSTALL_KEY", default="skill_rec:topk:install"),
                # 0 = full ranked catalog (兜底「全部」); >0 = classic TopK
                k=int(_env("REDIS_TOPK_K", "MARKET_REDIS_TOPK_K", default="0")),
                ttl_seconds=int(_env("REDIS_TOPK_TTL_SECONDS", "MARKET_REDIS_TOPK_TTL_SECONDS", default="7200")),
                interval_minutes=int(
                    _env("REDIS_TOPK_INTERVAL_MINUTES", "MARKET_REDIS_TOPK_INTERVAL_MINUTES", default="60")
                ),
            ),
            user_seq=UserSeqSettings(
                key_prefix=_env(
                    "REDIS_USER_SEQ_KEY_PREFIX",
                    "MARKET_REDIS_USER_SEQ_KEY_PREFIX",
                    default="skill_rec:user",
                ),
                max_len=int(_env("REDIS_USER_SEQ_MAX_LEN", "MARKET_REDIS_USER_SEQ_MAX_LEN", default="200")),
                ttl_seconds=int(
                    _env("REDIS_USER_SEQ_TTL_SECONDS", "MARKET_REDIS_USER_SEQ_TTL_SECONDS", default="7200")
                ),
            ),
        ),
        milvus=MilvusSettings(
            host=_env("MILVUS_HOST", "MARKET_MILVUS_HOST", default="127.0.0.1"),
            port=int(_env("MILVUS_PORT", "MARKET_MILVUS_PORT", default="19530")),
            collection=_env("MILVUS_COLLECTION", "MARKET_MILVUS_COLLECTION", default="skill_index"),
            batch_size=int(_env("MILVUS_BATCH_SIZE", "MARKET_MILVUS_BATCH_SIZE", default="32")),
            incremental_interval_minutes=int(
                _env(
                    "MILVUS_INCREMENTAL_INTERVAL_MINUTES",
                    "MARKET_MILVUS_INCREMENTAL_INTERVAL_MINUTES",
                    default="60",
                )
            ),
            full_rebuild_hour=int(
                _env("MILVUS_FULL_REBUILD_HOUR", "MARKET_MILVUS_FULL_REBUILD_HOUR", default="3")
            ),
            user=_env("MILVUS_USER", "MARKET_MILVUS_USER", default=""),
            password=_env("MILVUS_PASSWORD", "MARKET_MILVUS_PASSWORD", default=""),
        ),
        download_dir=download_dir,
        plugin_types=plugin_types,
        sync_interval_minutes=int(
            _env("OFFLINE_SYNC_INTERVAL_MINUTES", "MARKET_REC_PACKAGE_SYNC_INTERVAL_MINUTES", default="60")
        ),
    )


OfflineSyncConfig = AppConfig


def load_redis_config() -> RedisConfig:
    return load_config().redis
