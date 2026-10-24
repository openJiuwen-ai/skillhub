# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
from pathlib import Path
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from common.security.security_utils import SecurityUtils
from .config import settings
from .db_pool import sqlalchemy_pool_kwargs


PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

_DEFAULT_MYSQL_PORT = 3306


def _mysql_port_from_env() -> int:
    """DB_PORT 缺省或空串用 3306。非法值直接点名环境变量，避免导入期 int('')。"""
    port_text = (os.getenv("DB_PORT", str(_DEFAULT_MYSQL_PORT)) or "").strip()
    if not port_text:
        return _DEFAULT_MYSQL_PORT
    try:
        return int(port_text)
    except ValueError:
        raise ValueError(f"DB_PORT 必须是整数，当前为: {port_text!r}") from None


def _build_database_url_from_shared_env() -> str | None:
    db_type = os.getenv("DB_TYPE")
    if not db_type:
        return None

    db_type = db_type.lower()

    if db_type == "mysql":
        host = os.getenv("DB_HOST", "localhost")
        user = os.getenv("DB_USER", "root")
        password = SecurityUtils.get_decrypt_secret("DB_PASSWORD", default="") or ""

        store_db_name = os.getenv("STORE_DB_NAME", "openjiuwen_market")

        # 密码里的 @、% 等必须编码，否则会被当成 URL 分隔符，连接串被拆开。
        url = URL.create(
            drivername="mysql+pymysql",
            username=user,
            password=password,
            host=host,
            port=_mysql_port_from_env(),
            database=store_db_name,
            query={"charset": "utf8mb4"},
        )
        return url.render_as_string(hide_password=False)

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
    # 同步 DB 在事件循环上执行：socket 读挂住会冻结整个服务，三超时保证秒级报错；
    # 有超过 30s 的合法大查询需另建 engine（read_timeout 是 engine 级参数）。
    connect_args={"connect_timeout": 5, "read_timeout": 30, "write_timeout": 30},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator:
    """用于 FastAPI 依赖注入的 DB Session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

