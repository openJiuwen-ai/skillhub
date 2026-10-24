# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DB_PASSWORD 中的 @ 和 % 必须编码进 SQLAlchemy URL。"""

from __future__ import annotations

from plugins_market.core.database import _build_database_url_from_shared_env


def test_password_at_and_percent_are_encoded(monkeypatch) -> None:
    monkeypatch.setenv("DB_TYPE", "mysql")
    monkeypatch.setenv("DB_HOST", "db.internal")
    monkeypatch.setenv("DB_PORT", "3306")
    monkeypatch.setenv("DB_USER", "skillhub")
    monkeypatch.setenv("STORE_DB_NAME", "openjiuwen_market")
    monkeypatch.setattr(
        "plugins_market.core.database.SecurityUtils.get_decrypt_secret",
        staticmethod(lambda env_key, default="": "p@ss%w0rd"),
    )

    url = _build_database_url_from_shared_env()

    assert url == (
        "mysql+pymysql://skillhub:p%40ss%25w0rd@db.internal:3306/openjiuwen_market?charset=utf8mb4"
    )
