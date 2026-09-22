# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
from pathlib import Path
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.security.security_utils import SecurityUtils
from .config import settings
from .db_pool import sqlalchemy_pool_kwargs


PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


def _build_database_url_from_shared_env() -> str | None:
    db_type = os.getenv("DB_TYPE")
    if not db_type:
        return None

    db_type = db_type.lower()

    if db_type == "mysql":
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "3306")
        user = os.getenv("DB_USER", "root")
        password = SecurityUtils.get_decrypt_secret("DB_PASSWORD", default="") or ""

        store_db_name = os.getenv("STORE_DB_NAME", "openjiuwen_market")

        return (
            f"mysql+pymysql://{user}:{password}@"
            f"{host}:{port}/{store_db_name}?charset=utf8mb4"
        )

    raise ValueError(f"DB_TYPE 当前仅支持 mysql，当前为: {db_type!r}")


def _get_effective_database_url() -> str:
    """环境变量整串 URL 优先，否则 DB_TYPE=mysql 拼装，再否则 settings.db_url。"""
    explicit = (os.getenv("STORE_DB_URL") or os.getenv("MARKET_DB_URL") or "").strip()
    if explicit:
        url = explicit
    else:
        shared = _build_database_url_from_shared_env()
        if shared:
            url = shared
        else:
            url = (settings.db_url or "").strip()

    if not url:
        raise RuntimeError(
            "未配置数据库：请设置 DB_TYPE=mysql 与 DB_HOST/DB_USER/DB_PASSWORD/STORE_DB_NAME，"
            "或设置 STORE_DB_URL / MARKET_DB_URL（完整 SQLAlchemy URL）"
        )
    if url.lower().startswith("sqlite"):
        raise RuntimeError("已移除 SQLite 支持，请使用 MySQL（mysql+pymysql://...）")
    return url


DATABASE_URL = _get_effective_database_url()

engine = create_engine(
    DATABASE_URL,
    **sqlalchemy_pool_kwargs(),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator:
    """用于 FastAPI 依赖注入的 DB Session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

