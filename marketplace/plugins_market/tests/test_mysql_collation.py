# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""建表不再写死 MySQL 8 专有排序规则 utf8mb4_0900_ai_ci。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from plugins_market.models.groups import (
    MarketGroupDB,
    MarketGroupJoinRequestDB,
    MarketGroupMemberDB,
    MarketGroupSkillGrantDB,
)
from plugins_market.models.market_assets import MarketAssetInteractionDB

_MYSQL8_COLLATION = "utf8mb4_0900_ai_ci"


def test_orm_create_table_omits_mysql8_collation() -> None:
    dialect = mysql.dialect()
    tables = (
        MarketGroupDB.__table__,
        MarketGroupMemberDB.__table__,
        MarketGroupJoinRequestDB.__table__,
        MarketGroupSkillGrantDB.__table__,
        MarketAssetInteractionDB.__table__,
    )
    for table in tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert _MYSQL8_COLLATION not in ddl
        assert "utf8mb4" in ddl.lower()


def test_sql_ddl_omits_mysql8_collation() -> None:
    sql_root = Path(__file__).resolve().parents[3] / "sql"
    hits = []
    for path in sql_root.rglob("*.sql"):
        text = path.read_text(encoding="utf-8")
        if _MYSQL8_COLLATION in text:
            hits.append(str(path))
    assert hits == []
