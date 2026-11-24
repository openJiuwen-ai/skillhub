"""HTTP coverage for the stable download redirect, using SQLite and fake storage."""

from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from plugins_market.core.viewer_context import ANONYMOUS_VIEWER, ViewerContext
from plugins_market.core.http_error_logging import register_exception_handlers
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.repositories import PluginFetchRecordRepository
from plugins_market.routers import plugin
from plugins_market.services import plugin as service


ASSET_ID = "skill-public-then-pending"
DOWNLOAD_PATH = f"/api/v1/plugins/{ASSET_ID}/download"


def _skill_with_pending_new_version():
    asset = MarketAssetDB(
        asset_id=ASSET_ID, asset_type="plugin", name="demo-skill", display_name="demo-skill",
        publisher_id="owner", publisher_name="owner", status="PUBLISHED", plugin_type="skill",
        latest_version="2.0.0", public_latest_version="1.0.0", moderation_status="APPROVED",
        publish_result="pending_moderation", visibility="public", install_count=0, create_time=1, update_time=2,
    )
    versions = []
    for version, moderation, result in (
        ("1.0.0", "APPROVED", "publish_success"),
        ("2.0.0", "PENDING", "pending_moderation"),
    ):
        version_row = MarketAssetVersionDB(
            version_id=f"{ASSET_ID}-{version}", asset_id=ASSET_ID, version=version,
            create_time=1, moderation_status=moderation, publish_result=result, status="ACTIVE",
        )
        versions.append(version_row)
    return asset, *versions


@pytest.fixture
def endpoint(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    db.add_all(_skill_with_pending_new_version())
    db.commit()
    storage = Mock()
    storage.head_object.return_value = {"success": True, "size": 4, "metadata": {"sha256": "abcd", "size": "4"}}
    storage.presigned_get_url.return_value = "https://storage.example/package.zip?X-Amz-Expires=1800&signature=test"
    gate = Mock(return_value=True)
    release = Mock()
    audit = Mock()
    monkeypatch.setattr(plugin, "cache_set_nx", gate)
    monkeypatch.setattr(plugin, "cache_delete", release)
    monkeypatch.setattr(plugin, "audit_log", audit)
    monkeypatch.setattr(plugin, "get_user_id_from_context", lambda: None)
    monkeypatch.setattr(plugin, "get_user_id_or_none", lambda: None)
    monkeypatch.setattr(PluginFetchRecordRepository, "create_fetch_record", Mock())
    monkeypatch.setattr(service, "_ensure_non_cli_raw_artifact", Mock(return_value=("raw.zip", 4, "abcd")))
    app = FastAPI()
    register_exception_handlers(app, logger=plugin.logger)
    app.include_router(plugin.router, prefix="/api/v1")

    def override_db():
        return db

    def override_storage():
        return storage

    def override_viewer():
        return ANONYMOUS_VIEWER

    app.dependency_overrides[plugin.get_db] = override_db
    app.dependency_overrides[plugin.get_storage_client] = override_storage
    app.dependency_overrides[plugin.resolve_viewer_context] = override_viewer
    with TestClient(app, follow_redirects=False) as client:
        yield client, storage, gate, release, audit
    db.close()
    engine.dispose()


@pytest.mark.parametrize("cli", [True, False])
def test_redirect_uses_existing_signed_url(endpoint, cli):
    client, storage, gate, release, audit = endpoint
    response = client.get(DOWNLOAD_PATH, params={"is_cli_download": str(cli).lower()})
    assert response.status_code == 302, response.text
    assert response.headers["location"] == storage.presigned_get_url.return_value
    assert response.headers["x-skill-version"] == "1.0.0"
    assert response.headers["x-skill-name"] == "demo-skill"
    assert response.headers["x-checksum-sha256"] == "abcd"
    assert response.headers["content-disposition"] == 'attachment; filename="demo-skill_1.0.0.zip"'
    storage.presigned_get_url.assert_called_once()
    assert "expires_in" not in storage.presigned_get_url.call_args.kwargs
    assert gate.call_args.args[0].startswith(f"dlcnt:{ASSET_ID}:")
    release.assert_not_called()
    audit.assert_called_once()
    assert audit.call_args.kwargs["resource_id"] == ASSET_ID


@pytest.mark.parametrize("identifier,version,expected", [
    ("missing", None, 404),
    ("demo-skill", None, 404),
    (ASSET_ID, "9.0.0", 404),
    (ASSET_ID, "2.0.0", 404),
    (ASSET_ID, "invalid", 400),
])
def test_failed_download_releases_only_its_gate(endpoint, identifier, version, expected):
    client, storage, gate, release, audit = endpoint
    params = {"version": version} if version else {}
    response = client.get(f"/api/v1/plugins/{identifier}/download", params=params)
    assert response.status_code == expected, response.text
    release.assert_called_once_with(gate.call_args.args[0])
    storage.presigned_get_url.assert_not_called()
    audit.assert_not_called()


def test_redirect_and_metadata_share_daily_download_count(endpoint):
    client, storage, gate, release, audit = endpoint
    gate.side_effect = [True, False, False]
    params = {"version": "1.0.0"}
    metadata = client.get(f"/api/v1/artifacts/{ASSET_ID}", params=params)
    assert metadata.status_code == 200, metadata.text
    data = metadata.json()["data"]
    assert data["asset_id"] == ASSET_ID
    assert data["version"] == "1.0.0"
    assert data["download_url"] == storage.presigned_get_url.return_value

    response = client.get(DOWNLOAD_PATH, params=params)
    assert response.status_code == 302, response.text
    assert response.headers["location"] == data["download_url"]
    response = client.get(DOWNLOAD_PATH, params={"version": "2.0.0"})
    assert response.status_code == 404, response.text
    release.assert_not_called()
    assert gate.call_args_list == [gate.call_args_list[0]] * 3
    assert audit.call_count == 2
    db = client.app.dependency_overrides[plugin.get_db]()
    assert db.get(MarketAssetDB, ASSET_ID).install_count == 1


def test_redirect_respects_private_skill_visibility(endpoint):
    client, storage, gate, release, audit = endpoint
    db = client.app.dependency_overrides[plugin.get_db]()
    asset = db.get(MarketAssetDB, ASSET_ID)
    asset.visibility = "private"
    db.commit()
    response = client.get(DOWNLOAD_PATH)
    assert response.status_code == 404
    storage.presigned_get_url.assert_not_called()

    def owner_viewer():
        return ViewerContext(user_id="owner", user_login="owner", is_system_admin=False)

    client.app.dependency_overrides[plugin.resolve_viewer_context] = owner_viewer
    response = client.get(DOWNLOAD_PATH, params={"version": "2.0.0"})
    assert response.status_code == 302, response.text
    assert response.headers["x-skill-version"] == "2.0.0"
