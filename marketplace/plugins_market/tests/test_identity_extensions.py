"""Extension discovery, real signed OIDC exchange and built-in compatibility."""

import asyncio
import base64
import hashlib
import json
import time
from typing import Annotated
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import plugins_market.identity_extensions as extension_packages
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from plugins_market.core import oauth_user_profile
from plugins_market.core.auth import (
    AuthContext,
    normalize_oauth_provider_header,
    require_auth,
)
from plugins_market.core.config import settings
from plugins_market.core.oauth_session_store import _MemoryOAuthStore
from plugins_market.identity_extensions import registry
from plugins_market.identity_extensions import router as extension_routes
from plugins_market.identity_extensions.keycloak.config import KeycloakConfig
from plugins_market.identity_extensions.registry import IdentityRegistry
from plugins_market.routers.oauth_provider import OAuthProvider
from plugins_market.routers.oauth_provider import router as builtin_router


@pytest.fixture(autouse=True)
def isolated_registry():
    registry.get_registry.cache_clear()
    registry.get_runtime_registry.cache_clear()
    yield
    registry.get_registry.cache_clear()
    registry.get_runtime_registry.cache_clear()


@pytest.mark.parametrize(
    ("enable_config", "switch", "enabled"),
    [
        ({}, None, False),
        ({"enabled": True}, None, True),
        ({"enabled_env": "CUSTOM_LOGIN_ENABLED"}, None, False),
        ({"enabled_env": "CUSTOM_LOGIN_ENABLED"}, "false", False),
        ({"enabled_env": "CUSTOM_LOGIN_ENABLED"}, "1", False),
        ({"enabled_env": "CUSTOM_LOGIN_ENABLED"}, "true", True),
        ({"enabled": True, "enabled_env": "CUSTOM_LOGIN_ENABLED"}, "false", False),
    ],
)
def test_new_directory_and_configuration_are_sufficient(
    tmp_path, monkeypatch, enable_config, switch, enabled
):
    if switch is None:
        monkeypatch.delenv("CUSTOM_LOGIN_ENABLED", raising=False)
    else:
        monkeypatch.setenv("CUSTOM_LOGIN_ENABLED", switch)
    package = tmp_path / "custom_identity"
    package.mkdir()
    (package / "__init__.py").write_text(
        """
class Adapter:
    async def start_authorization(self, **kwargs): pass
    async def complete_authorization(self, **kwargs): pass
    async def fetch_profile(self, token):
        return {"id": "custom-id", "login": "custom", "name": "Custom"}
def create_adapter(*, provider_id, settings):
    return Adapter()
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(extension_packages, "__path__", [str(tmp_path)])
    configured = IdentityRegistry(
        {
            "providers": {
                "new-channel": {
                    **enable_config,
                    "adapter": "custom_identity",
                    "display_name": "Custom login",
                }
            }
        }
    )
    provider = configured.get("new-channel")
    if not enabled:
        assert provider is None
        assert configured.public_providers() == []
        return

    assert asyncio.run(provider.adapter.fetch_profile("token"))["id"] == "custom-id"
    assert configured.public_providers() == [
        {
            "id": "new-channel",
            "display_name": "Custom login",
            "adapter": "custom_identity",
        }
    ]


@pytest.mark.parametrize("name", ["github", "gitcode", "agentos", "../bad", "UPPER"])
def test_reject_reserved_or_invalid_provider_ids(name):
    with pytest.raises(ValueError):
        IdentityRegistry({"providers": {name: {"enabled": False}}})


def test_invalid_and_disabled_configuration(tmp_path, monkeypatch):
    assert (
        IdentityRegistry(
            {"providers": {"unused": {"enabled": False, "adapter": "missing"}}}
        ).get("unused")
        is None
    )
    with pytest.raises(ValueError):
        IdentityRegistry(
            {"providers": {"enterprise": {"enabled": True, "adapter": "missing"}}}
        )
    path = tmp_path / "duplicate.yaml"
    path.write_text("providers:\n  duplicate: {}\n  duplicate: {}\n", encoding="utf-8")
    monkeypatch.setattr(settings, "identity_extensions_config", str(path))
    with pytest.raises(ValueError):
        registry.get_registry()


@pytest.mark.parametrize("provider", ["github", "gitcode", "agentos"])
def test_builtin_profile_dispatch_is_unchanged(provider, monkeypatch):
    monkeypatch.setattr(settings, "identity_extensions_config", "")
    expected = {"id": "original", "login": "original"}
    functions = {
        "github": "fetch_github_profile",
        "gitcode": "fetch_gitcode_profile",
        "agentos": "_fetch_agentos_profile",
    }
    mocks = {}
    for name in functions.values():
        mocks[name] = AsyncMock(return_value=expected)
        monkeypatch.setattr(oauth_user_profile, name, mocks[name])
    assert normalize_oauth_provider_header(provider) == provider
    assert (
        asyncio.run(oauth_user_profile.fetch_oauth_user_profile(provider, "old-token"))
        is expected
    )
    mocks[functions[provider]].assert_awaited_once_with("old-token")
    assert sum(mock.await_count for mock in mocks.values()) == 1
    assert {p.value for p in OAuthProvider} == {"gitcode", "github", "agentos"}
    assert normalize_oauth_provider_header(None) == "gitcode"


@pytest.mark.parametrize("provider", ["github", "gitcode", "agentos"])
def test_builtin_auth_never_reads_extension_registry(provider, monkeypatch):
    def forbidden():
        raise AssertionError("Built-in OAuth must not consult extension configuration")

    monkeypatch.setattr(registry, "get_runtime_registry", forbidden)
    original_profile = {"id": "original-user", "login": "original-login"}
    names = {
        "github": "fetch_github_profile",
        "gitcode": "fetch_gitcode_profile",
        "agentos": "_fetch_agentos_profile",
    }
    original = AsyncMock(return_value=original_profile)
    monkeypatch.setattr(oauth_user_profile, names[provider], original)
    assert normalize_oauth_provider_header(provider) == provider
    assert (
        asyncio.run(oauth_user_profile.fetch_oauth_user_profile(provider, "old-token"))
        is original_profile
    )
    from starlette.requests import Request

    auth = asyncio.run(
        require_auth(
            Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)}),
            authorization="Bearer old-token",
            x_system_token=None,
            x_oauth_provider=provider,
        )
    )
    assert auth.acting_user_id == "original-user"
    assert auth.is_admin is False


@pytest.mark.parametrize(
    "broken", ["missing-file", "invalid-yaml", "missing-adapter", "missing-secret"]
)
def test_broken_extension_config_does_not_break_builtin_routes(
    tmp_path, monkeypatch, broken
):
    from plugins_market.routers.site_public import get_site_config

    path = tmp_path / "extensions.yaml"
    if broken == "invalid-yaml":
        path.write_text("providers: [invalid", encoding="utf-8")
    elif broken == "missing-adapter":
        path.write_text(
            "providers:\n  enterprise:\n    enabled: true\n    adapter: absent\n",
            encoding="utf-8",
        )
    elif broken == "missing-secret":
        monkeypatch.delenv("TEST_MISSING_EXTENSION_SECRET", raising=False)
        path.write_text(
            json.dumps(
                {
                    "providers": {
                        "enterprise": {
                            "enabled": True,
                            "adapter": "keycloak",
                            "settings": {
                                "issuer": "https://sso.test",
                                "client_id": "hub",
                                "client_secret_env": "TEST_MISSING_EXTENSION_SECRET",
                                "redirect_uri": "https://hub.test/cb",
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(settings, "identity_extensions_config", str(path))
    monkeypatch.setattr(settings, "github_oauth_enabled", True)
    monkeypatch.setattr(settings, "github_oauth_client_id", "original-client")
    monkeypatch.setattr(
        settings, "github_oauth_redirect_uri", "https://hub.test/original-callback"
    )
    config = asyncio.run(get_site_config())
    assert config["identity_extensions"] == []
    assert config["github_oauth_enabled"] is True
    app = FastAPI()
    app.include_router(builtin_router, prefix="/api/v1/auth")
    app.include_router(extension_routes.router, prefix="/api/v1")
    with TestClient(app) as client:
        original = client.get("/api/v1/auth/oauth/github/start", follow_redirects=False)
        assert original.status_code == 302
        assert parse_qs(urlsplit(original.headers["location"]).query)["client_id"] == [
            "original-client"
        ]
        assert client.get("/api/v1/auth/extensions/enterprise/start").status_code == 404


@pytest.fixture
def oidc_app(tmp_path, monkeypatch):
    issuer = "https://sso.example.test/realms/company"
    monkeypatch.setenv("TEST_IDENTITY_SECRET", "test-client-secret")
    monkeypatch.setenv("TEST_IDENTITY_EXTENSIONS_ENABLED", "true")
    monkeypatch.setattr(settings, "review_admin_usernames", "reviewer01")
    path = tmp_path / "providers.yaml"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    name: {
                        "enabled_env": "TEST_IDENTITY_EXTENSIONS_ENABLED",
                        "adapter": "keycloak",
                        "display_name": "Enterprise",
                        "settings": {
                            "issuer": issuer,
                            "client_id": "skillhub",
                            "client_secret_env": "TEST_IDENTITY_SECRET",
                            "redirect_uri": "https://hub.example.test/api/v1/auth/extensions/"
                            + name
                            + "/callback",
                        },
                    }
                    for name in ("enterprise", "other")
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "identity_extensions_config", str(path))
    store = _MemoryOAuthStore()
    monkeypatch.setattr(extension_routes, "get_oauth_str_store", lambda: store)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public.update(kid="key1", alg="RS256", use="sig")
    state = {"fault": None, "nonce": "", "challenge": "", "requests": []}

    def upstream(request):
        state["requests"].append(request)
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": issuer,
                    "authorization_endpoint": issuer + "/auth",
                    "token_endpoint": issuer + "/token",
                    "userinfo_endpoint": issuer + "/userinfo",
                    "jwks_uri": issuer + "/certs",
                },
            )
        if request.url.path.endswith("/certs"):
            if state["fault"] == "jwks":
                return httpx.Response(200, json={"keys": None})
            return httpx.Response(200, json={"keys": [public]})
        if request.url.path.endswith("/token"):
            form = parse_qs(request.content.decode())
            challenge = (
                base64.urlsafe_b64encode(
                    hashlib.sha256(form["code_verifier"][0].encode()).digest()
                )
                .rstrip(b"=")
                .decode()
            )
            assert challenge == state["challenge"]
            assert request.headers["authorization"].startswith("Basic ")
            now = int(time.time())
            claims = {
                "iss": issuer,
                "sub": "keycloak-user",
                "aud": "skillhub",
                "iat": now,
                "exp": now + 300,
                "nonce": state["nonce"],
            }
            fault = state["fault"]
            if fault == "nonce":
                claims["nonce"] = "wrong"
            if fault == "unicode-nonce":
                claims["nonce"] = "错误"
            if fault == "issuer":
                claims["iss"] = "https://wrong.test"
            if fault == "audience":
                claims["aud"] = "another-app"
            if fault == "expired":
                claims["exp"] = now - 120
            if fault == "subject":
                claims["sub"] = "different-user"
            if fault == "azp":
                claims["azp"] = "another-app"
            if fault == "hash":
                claims["at_hash"] = "invalid"
            signer = (
                rsa.generate_private_key(public_exponent=65537, key_size=2048)
                if fault == "signature"
                else private
            )
            return httpx.Response(
                200,
                json={
                    "access_token": "access-token",
                    "token_type": "Bearer",
                    "id_token": jwt.encode(
                        claims, signer, algorithm="RS256", headers={"kid": "key1"}
                    ),
                },
            )
        if request.url.path.endswith("/userinfo"):
            assert request.headers["authorization"] == "Bearer access-token"
            if state["fault"] == "invalid-token":
                return httpx.Response(401)
            sub = (
                settings.system_admin_user
                if state["fault"] == "reserved-id"
                else "keycloak-user"
            )
            return httpx.Response(
                200,
                json={
                    "sub": sub,
                    "preferred_username": "reviewer01",
                    "name": "Reviewer",
                },
            )
        raise AssertionError(str(request.url))

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(upstream), **kwargs
        ),
    )
    app = FastAPI()
    app.include_router(extension_routes.router, prefix="/api/v1")
    app.include_router(builtin_router, prefix="/api/v1/auth")

    @app.get("/business")
    async def business(auth: Annotated[AuthContext, Depends(require_auth)]):
        return {
            "publisher_id": auth.acting_user_id,
            "publisher_name": auth.acting_user_name,
            "is_admin": auth.is_admin,
            "reviewer": auth.is_market_moderation_admin,
        }

    return app, state, store


def begin(client, state, query=""):
    response = client.get(
        "/api/v1/auth/extensions/enterprise/start" + query, follow_redirects=False
    )
    assert response.status_code == 302
    params = parse_qs(urlsplit(response.headers["location"]).query)
    state["nonce"] = params["nonce"][0]
    state["challenge"] = params["code_challenge"][0]
    return params["state"][0]


@pytest.mark.parametrize("prompt", [None, "login"])
def test_optional_login_prompt(oidc_app, prompt):
    app, _, _ = oidc_app
    adapter = registry.get_registry().get("enterprise").adapter
    adapter.config.prompt = prompt
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/auth/extensions/enterprise/start", follow_redirects=False
        )
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params.get("prompt") == (["login"] if prompt else None)


def finish(client, login_state):
    return client.get(
        "/api/v1/auth/extensions/enterprise/callback",
        params={"state": login_state, "code": "code"},
        follow_redirects=False,
    )


def test_oidc_login_session_and_existing_business_identity(oidc_app):
    app, state, _ = oidc_app
    with TestClient(app, base_url="https://hub.example.test") as client:
        login_state = begin(client, state)
        response = finish(client, login_state)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["oauth_provider"] == ["enterprise"]
        pending = query["oauth_session"][0]
        wrong = client.post(
            "/api/v1/auth/extensions/other/session", json={"session": pending}
        )
        assert wrong.status_code == 400
        exchanged = client.post(
            "/api/v1/auth/extensions/enterprise/session", json={"session": pending}
        )
        assert exchanged.status_code == 200
        assert exchanged.headers["cache-control"] == "no-store"
        assert exchanged.json()["data"]["user"]["id"] == "keycloak-user"
        assert "refresh_token" not in exchanged.text
        assert (
            client.post(
                "/api/v1/auth/extensions/enterprise/session", json={"session": pending}
            ).status_code
            == 400
        )
        assert "oauth_error" in finish(client, login_state).headers["location"]
        headers = {
            "Authorization": "Bearer access-token",
            "X-OAuth-Provider": "enterprise",
        }
        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["data"]["is_market_moderation_admin"] is True
        identity = client.get("/business", headers=headers)
        assert identity.json() == {
            "publisher_id": "keycloak-user",
            "publisher_name": "reviewer01",
            "is_admin": False,
            "reviewer": True,
        }
        state["fault"] = "invalid-token"
        assert client.get("/business", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "fault",
    [
        "nonce",
        "unicode-nonce",
        "jwks",
        "issuer",
        "audience",
        "signature",
        "expired",
        "subject",
        "azp",
        "hash",
        "reserved-id",
    ],
)
def test_invalid_oidc_result_cannot_create_session(oidc_app, fault):
    app, state, _ = oidc_app
    state["fault"] = fault
    with TestClient(app, base_url="https://hub.example.test") as client:
        response = finish(client, begin(client, state))
        assert "oauth_error" in response.headers["location"]
        assert "oauth_session" not in response.headers["location"]


def test_browser_binding_loopback_and_expiry(oidc_app):
    app, state, store = oidc_app
    with TestClient(app, base_url="https://hub.example.test") as client:
        assert client.get("/api/v1/auth/extensions/missing/start").status_code == 404
        assert client.get("/api/v1/auth/extensions/github/start").status_code == 404
        assert (
            client.get(
                "/api/v1/auth/extensions/enterprise/start",
                params={"redirect_to": "https://evil.test/cb"},
            ).status_code
            == 400
        )
        login_state = begin(
            client, state, "?redirect_to=http://127.0.0.1:8080/cb?client_state=original"
        )
        with TestClient(app, base_url="https://hub.example.test") as other_browser:
            assert (
                "oauth_error" in finish(other_browser, login_state).headers["location"]
            )
        response = finish(client, login_state)
        parts = urlsplit(response.headers["location"])
        assert parts.hostname == "127.0.0.1"
        assert parse_qs(parts.query)["client_state"] == ["original"]
        expired = begin(client, state)
        store.delete(extension_routes._key("state", "enterprise", expired))
        assert "oauth_error" in finish(client, expired).headers["location"]


@pytest.mark.parametrize(
    "change",
    [
        {"scopes": ["profile"]},
        {"algorithms": ["HS256"]},
        {"issuer": "http://untrusted.test"},
    ],
)
def test_oidc_configuration_rejects_invalid_protocol_settings(change):
    with pytest.raises(ValueError):
        KeycloakConfig.model_validate(
            {
                "issuer": "https://sso.test",
                "client_id": "hub",
                "client_secret_env": "SECRET",
                "redirect_uri": "https://hub.test/cb",
                **change,
            }
        )


def test_extension_identity_reaches_existing_publish_and_moderation_services(
    oidc_app, monkeypatch
):
    """Use actual publish routing and actual moderation service, with local I/O fixtures."""
    import io

    from fastapi import BackgroundTasks, HTTPException, Request, UploadFile
    from plugins_market.core.errors import BusinessError
    from plugins_market.models.base import Base
    from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
    from plugins_market.routers import plugin
    from plugins_market.schemas.plugin import PluginPublishForm, PluginPublishResult
    from plugins_market.services import plugin as plugin_service
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app, state, _ = oidc_app
    # Initial /start obtains metadata; later userinfo requests use the same fixture.
    with TestClient(app, base_url="https://hub.example.test") as client:
        begin(client, state)

    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})

    def resolve_auth():
        return asyncio.run(
            require_auth(
                request,
                authorization="Bearer access-token",
                x_system_token=None,
                x_oauth_provider="enterprise",
            )
        )

    reviewer = resolve_auth()
    captured = {}

    def capture_publish(**kwargs):
        captured.update(kwargs)
        return PluginPublishResult(
            plugin_id="new-asset",
            name="demo",
            version="1.0.0",
            status="PUBLISHED",
            published_at="2026-11-17T00:00:00Z",
            storage_url="mock",
            plugin_type="agent-plugin",
            publish_result="pending_moderation",
        )

    monkeypatch.setattr(plugin, "plugin_publish", capture_publish)
    monkeypatch.setattr(plugin, "audit_log", lambda **kwargs: None)
    form = PluginPublishForm(
        file=UploadFile(filename="demo.zip", file=io.BytesIO(b"fixture")),
        checksum="checksum",
        plugin_id=None,
        plugin_version=None,
        version_desc=None,
        force=False,
    )
    publish_auth = plugin.get_publish_auth("Bearer access-token", None, "enterprise")
    result = asyncio.run(
        plugin.publish_plugin(
            request,
            BackgroundTasks(),
            form,
            plugin.PublishPluginDependencies(db=None, storage=None),
            publish_auth,
        )
    )
    assert result.data.plugin_id == "new-asset"
    assert captured["user_id"] == "keycloak-user"
    assert captured["publisher_name_override"] == "reviewer01"
    assert captured["is_system_token"] is False

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    asset = MarketAssetDB(
        asset_id="other",
        asset_type="agent-plugin",
        plugin_type="agent-plugin",
        name="demo",
        display_name="Demo",
        publisher_id="other-user",
        publisher_name="Other",
        status="PUBLISHED",
        latest_version="1.0.0",
        moderation_status="PENDING",
        publish_result="pending_moderation",
        create_time=1,
        update_time=1,
    )
    version = MarketAssetVersionDB(
        version_id="v1",
        asset_id="other",
        version="1.0.0",
        moderation_status="PENDING",
        publish_result="pending_moderation",
    )
    db.add_all([asset, version])
    db.commit()
    monkeypatch.setattr(settings, "review_admin_usernames", "")
    ordinary_user = resolve_auth()
    with pytest.raises(HTTPException) as denied:
        plugin_service.moderate_skill_asset_service(
            asset_id="other",
            action="approve",
            reason=None,
            version="1.0.0",
            auth=ordinary_user,
            db=db,
            storage=None,
        )
    assert denied.value.status_code == 403
    asset.publisher_id = reviewer.acting_user_id
    db.commit()
    monkeypatch.setattr(settings, "allow_self_moderation", False)
    with pytest.raises(BusinessError) as self_review:
        plugin_service.moderate_skill_asset_service(
            asset_id="other",
            action="approve",
            reason=None,
            version="1.0.0",
            auth=reviewer,
            db=db,
            storage=None,
        )
    assert self_review.value.error == "self_moderation_forbidden"
    asset.publisher_id = "other-user"
    db.commit()
    monkeypatch.setattr(
        plugin_service,
        "_refresh_skill_asset_listing_fields_from_public_artifact",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(plugin_service, "audit_log", lambda **kwargs: None)
    approved = plugin_service.moderate_skill_asset_service(
        asset_id="other",
        action="approve",
        reason=None,
        version="1.0.0",
        auth=reviewer,
        db=db,
        storage=None,
    )
    assert approved.moderation_status == "APPROVED"
    assert approved.publish_result == "publish_success"
    db.close()
    engine.dispose()
