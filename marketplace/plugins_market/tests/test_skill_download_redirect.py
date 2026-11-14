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


def _skill_with_pending_new_version(asset_id="skill-public-then-pending", publisher_id="owner"):
    asset = MarketAssetDB(
        asset_id=asset_id, asset_type="plugin", name="demo-skill", display_name="demo-skill",
        publisher_id=publisher_id, publisher_name=publisher_id, status="PUBLISHED", plugin_type="skill",
        latest_version="2.0.0", public_latest_version="1.0.0", moderation_status="APPROVED",
        publish_result="pending_moderation", visibility="public", install_count=0, create_time=1, update_time=2,
    )
    versions = []
    for version, moderation, result in (
        ("1.0.0", "APPROVED", "publish_success"),
        ("2.0.0", "PENDING", "pending_moderation"),
    ):
        version_row = MarketAssetVersionDB(
            version_id=f"{asset_id}-{version}", asset_id=asset_id, version=version,
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


@pytest.mark.parametrize("identifier", ["skill-public-then-pending", "demo-skill"])
@pytest.mark.parametrize("cli", [True, False])
def test_redirect_uses_existing_signed_url(endpoint, identifier, cli):
    client, storage, gate, release, audit = endpoint
    response = client.get(f"/api/v1/plugins/{identifier}/download", params={"is_cli_download": str(cli).lower()})
    assert response.status_code == 302, response.text
    assert response.headers["location"] == storage.presigned_get_url.return_value
    assert response.headers["x-skill-version"] == "1.0.0"
    assert response.headers["x-skill-name"] == "demo-skill"
    assert response.headers["x-checksum-sha256"] == "abcd"
    assert response.headers["content-disposition"] == 'attachment; filename="demo-skill_1.0.0.zip"'
    storage.presigned_get_url.assert_called_once()
    assert "expires_in" not in storage.presigned_get_url.call_args.kwargs
    assert gate.call_args.args[0].startswith("dlcnt:skill-public-then-pending:")
    release.assert_not_called()
    assert audit.call_args.kwargs["extra"]["download_mode"] == "302_redirect"


@pytest.mark.parametrize("identifier,version,expected", [
    ("missing", None, 404),
    ("demo-skill", "9.0.0", 404),
    ("demo-skill", "2.0.0", 404),
    ("demo-skill", "invalid", 400),
])
def test_failed_download_releases_only_its_gate(endpoint, identifier, version, expected):
    client, storage, gate, release, audit = endpoint
    params = {"version": version} if version else {}
    response = client.get(f"/api/v1/plugins/{identifier}/download", params=params)
    assert response.status_code == expected, response.text
    release.assert_called_once_with(gate.call_args.args[0])
    storage.presigned_get_url.assert_not_called()
    audit.assert_not_called()


def test_repeated_request_still_downloads_without_releasing_previous_gate(endpoint):
    client, storage, gate, release, audit = endpoint
    gate.return_value = False
    response = client.get("/api/v1/plugins/demo-skill/download?version=1.0.0")
    assert response.status_code == 302, response.text
    response = client.get("/api/v1/plugins/demo-skill/download?version=2.0.0")
    assert response.status_code == 404, response.text
    release.assert_not_called()


@pytest.mark.parametrize("excluded", ["private", "pending", "offline", "agent-plugin"])
def test_name_lookup_skips_ineligible_higher_ranked_assets(endpoint, excluded):
    client, storage, gate, release, audit = endpoint
    db = client.app.dependency_overrides[plugin.get_db]()
    asset, approved, pending = _skill_with_pending_new_version("other-asset", "other-owner")
    asset.install_count = 100
    if excluded == "private":
        asset.visibility = "private"
    elif excluded == "pending":
        asset.moderation_status = "PENDING"
        asset.public_latest_version = None
        approved.moderation_status = "PENDING"
        approved.publish_result = "pending_moderation"
    elif excluded == "offline":
        asset.status = "OFFLINE"
    else:
        asset.plugin_type = "agent-plugin"
    db.add_all([asset, approved, pending])
    db.commit()
    response = client.get("/api/v1/plugins/demo-skill/download")
    assert response.status_code == 302, response.text
    assert audit.call_args.kwargs["resource_id"] == "skill-public-then-pending"


def test_name_lookup_accepts_legacy_null_status(endpoint):
    client, storage, gate, release, audit = endpoint
    db = client.app.dependency_overrides[plugin.get_db]()
    asset = db.get(MarketAssetDB, "skill-public-then-pending")
    asset.status = None
    db.commit()
    response = client.get("/api/v1/plugins/demo-skill/download")
    assert response.status_code == 302, response.text


def test_private_name_not_exposed_and_owner_can_download(endpoint):
    client, storage, gate, release, audit = endpoint
    db = client.app.dependency_overrides[plugin.get_db]()
    asset = db.get(MarketAssetDB, "skill-public-then-pending")
    asset.visibility = "private"
    db.commit()
    response = client.get("/api/v1/plugins/demo-skill/download")
    assert response.status_code == 404
    assert asset.asset_id not in response.text

    def owner_viewer():
        return ViewerContext(user_id="owner", user_login="owner", is_system_admin=False)

    client.app.dependency_overrides[plugin.resolve_viewer_context] = owner_viewer
    response = client.get("/api/v1/plugins/demo-skill/download?version=2.0.0")
    assert response.status_code == 302, response.text
    assert response.headers["x-skill-version"] == "2.0.0"
