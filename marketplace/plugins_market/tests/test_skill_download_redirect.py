"""HTTP coverage for the stable download redirect, using SQLite and fake storage."""

from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from plugins_market.core.viewer_context import ANONYMOUS_VIEWER
from plugins_market.core.http_error_logging import register_exception_handlers
from plugins_market.models.base import Base
from plugins_market.repositories import PluginFetchRecordRepository
from plugins_market.routers import plugin
from test_download_public_latest import _skill_with_pending_new_version


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
    from plugins_market.services import plugin as service
    monkeypatch.setattr(service, "_ensure_non_cli_raw_artifact", Mock(return_value=("raw.zip", 4, "abcd")))
    app = FastAPI()
    register_exception_handlers(app, logger=plugin.logger)
    app.include_router(plugin.router, prefix="/api/v1")
    app.dependency_overrides[plugin.get_db] = lambda: db
    app.dependency_overrides[plugin.get_storage_client] = lambda: storage
    app.dependency_overrides[plugin.resolve_viewer_context] = lambda: ANONYMOUS_VIEWER
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
