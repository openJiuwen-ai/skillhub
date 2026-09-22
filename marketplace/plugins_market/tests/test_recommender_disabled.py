# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os

os.environ.setdefault("STORE_DB_URL", "mysql+pymysql://test:test@127.0.0.1:3306/test")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins_market.core.viewer_context import ANONYMOUS_VIEWER
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB
from plugins_market.routers.recommender_disabled import router as disabled_router
from plugins_market.services.plugin import list_plugins_by_install_count
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(disabled_router, prefix="/api/v1")
    return TestClient(app)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _skill(asset_id: str, *, install_count: int) -> MarketAssetDB:
    return MarketAssetDB(
        asset_id=asset_id,
        asset_type="plugin",
        name=asset_id,
        display_name=asset_id,
        publisher_id="owner",
        publisher_name="owner",
        status="PUBLISHED",
        plugin_type="skill",
        latest_version="1.0.0",
        public_latest_version="1.0.0",
        moderation_status="APPROVED",
        view_count=0,
        install_count=install_count,
        like_count=0,
        star_count=0,
        review_count=0,
        average_rating=8.0,
        create_time=1,
        update_time=2,
        tags=["demo"],
        short_desc=asset_id,
    )


def test_fallback_orders_by_install_count():
    db = _db()
    db.add(_skill("low", install_count=1))
    db.add(_skill("high", install_count=9))
    db.commit()
    items = list_plugins_by_install_count(
        top_k=10,
        db=db,
        storage=None,
        viewer=ANONYMOUS_VIEWER,
        plugin_type="skill",
    )
    assert [it.asset_id for it in items] == ["high", "low"]


def test_post_recommend_by_ids_returns_200_empty_when_disabled():
    resp = _client().post("/api/v1/recommend/by_ids", json={"asset_ids": ["a1"], "top_k": 5})
    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []


def test_post_recommend_skillpack_skips_db_when_disabled(monkeypatch):
    called = {"n": 0}

    def _should_not_list(**_kwargs):
        called["n"] += 1
        raise AssertionError("skillpack must not hydrate when recommender is disabled")

    monkeypatch.setattr(
        "plugins_market.routers.recommender_disabled.list_plugins_by_install_count",
        _should_not_list,
    )
    monkeypatch.setattr(
        "plugins_market.routers.recommender_disabled.SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("skillpack must not open SessionLocal")),
    )
    resp = _client().post(
        "/api/v1/recommend",
        json={"plugin_type": "skillpack", "top_k": 6, "user_id": "", "request_id": "warmup"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["items"] == []
    assert body["data"]["source"] == "install_count"
    assert body["data"]["plugin_type"] == "skillpack"
    assert called["n"] == 0
