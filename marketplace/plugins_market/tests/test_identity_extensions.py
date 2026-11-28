"""Extension discovery, real signed OIDC exchange and built-in compatibility."""

import asyncio
import base64
import hashlib
import json
import time
from types import SimpleNamespace
from typing import Annotated
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import plugins_market.identity_extensions as extension_packages
import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from plugins_market.core import (
    http_error_logging,
    oauth_session_store,
    oauth_user_profile,
)
from plugins_market.core.auth import (
    AuthContext,
    normalize_oauth_provider_header,
    require_auth,
)
from plugins_market.core.config import Settings, settings
from plugins_market.core.http_error_logging import register_exception_handlers
from plugins_market.core.oauth_session_store import _MemoryOAuthStore
from plugins_market.identity_extensions import registry
from plugins_market.identity_extensions.keycloak.config import KeycloakConfig
from plugins_market.identity_extensions.registry import IdentityRegistry
from plugins_market.routers import oauth_provider as oauth_routes
from plugins_market.routers.oauth_provider import OAuthProvider
from plugins_market.routers.oauth_provider import router as builtin_router


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    monkeypatch.setattr(http_error_logging, "audit_failed_mutation", lambda *args, **kwargs: None)
    # Extension scenarios opt in explicitly; individual tests exercise the off state.
    monkeypatch.setattr(settings, "identity_extensions_enabled", True)
    registry.clear_registry_caches()
    yield
    registry.clear_registry_caches()


@pytest.mark.parametrize(
    ("switch", "expected"), [(None, False), ("false", False), ("true", True)]
)
def test_global_switch_defaults_off_and_reads_market_environment(
    monkeypatch, switch, expected
):
    monkeypatch.delenv("MARKET_IDENTITY_EXTENSIONS_ENABLED", raising=False)
    if switch is not None:
        monkeypatch.setenv("MARKET_IDENTITY_EXTENSIONS_ENABLED", switch)
    assert Settings(_env_file=None).identity_extensions_enabled is expected


@pytest.mark.parametrize(
    "channel",
    [{"enabled": True}, {"enabled_env": "CUSTOM_LOGIN_ENABLED"}],
)
def test_global_off_skips_configuration_discovery_and_plugin_import(
    tmp_path, monkeypatch, channel
):
    path = tmp_path / "extensions.yaml"
    path.write_text(
        yaml.safe_dump({"providers": {"keycloak": {**channel, "adapter": "keycloak"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CUSTOM_LOGIN_ENABLED", "true")
    monkeypatch.setattr(settings, "identity_extensions_config", str(path))
    monkeypatch.setattr(settings, "identity_extensions_enabled", False)
    forbidden = Mock(side_effect=AssertionError("Disabled extensions must not initialize"))
    with monkeypatch.context() as blocked:
        blocked.setattr(registry.Path, "open", forbidden)
        blocked.setattr(registry.pkgutil, "iter_modules", forbidden)
        blocked.setattr(registry.importlib, "import_module", forbidden)
        assert registry.get_registry().get("keycloak") is None
        assert registry.get_runtime_registry().public_providers() == []
    forbidden.assert_not_called()


@pytest.mark.parametrize("global_enabled", [False, True])
@pytest.mark.parametrize(
    "channel_case",
    [
        ({}, None, False),
        ({"enabled": False}, None, False),
        ({"enabled": True}, None, True),
        ({"enabled_env": "CUSTOM_LOGIN_ENABLED"}, None, False),
        ({"enabled_env": "CUSTOM_LOGIN_ENABLED"}, "true", True),
        ({"enabled": True, "enabled_env": "CUSTOM_LOGIN_ENABLED"}, "false", False),
    ],
)
def test_global_and_channel_switches_are_both_required(
    tmp_path, monkeypatch, global_enabled, channel_case
):
    from plugins_market.identity_extensions import keycloak

    channel, environment, channel_enabled = channel_case
    monkeypatch.delenv("CUSTOM_LOGIN_ENABLED", raising=False)
    if environment is not None:
        monkeypatch.setenv("CUSTOM_LOGIN_ENABLED", environment)
    monkeypatch.setattr(settings, "identity_extensions_enabled", global_enabled)
    adapter = SimpleNamespace(
        start_authorization=AsyncMock(),
        complete_authorization=AsyncMock(),
        fetch_profile=AsyncMock(),
    )
    factory = Mock(return_value=adapter)
    monkeypatch.setattr(keycloak, "create_adapter", factory)
    path = tmp_path / "extensions.yaml"
    path.write_text(
        yaml.safe_dump({"providers": {"keycloak": {**channel, "adapter": "keycloak"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "identity_extensions_config", str(path))
    expected = global_enabled and channel_enabled
    strict = registry.get_registry()
    runtime = registry.get_runtime_registry()
    assert (strict.get("keycloak") is not None) is expected
    assert bool(runtime.public_providers()) is expected
    assert runtime is strict
    assert factory.call_count == int(expected)
    if not global_enabled:
        # The off state must not cache an empty result that prevents later opt-in.
        monkeypatch.setattr(settings, "identity_extensions_enabled", True)
        assert (registry.get_registry().get("keycloak") is not None) is channel_enabled
        assert bool(registry.get_runtime_registry().public_providers()) is channel_enabled
        assert factory.call_count == int(channel_enabled)


@pytest.mark.parametrize("extensions_enabled", [False, True])
def test_unavailable_extension_token_never_falls_back_to_builtin(
    monkeypatch, extensions_enabled
):
    monkeypatch.setattr(settings, "identity_extensions_enabled", extensions_enabled)
    monkeypatch.setattr(settings, "identity_extensions_config", "")
    lookups = []
    for name in ("fetch_gitcode_profile", "fetch_github_profile", "_fetch_agentos_profile"):
        lookup = AsyncMock(side_effect=AssertionError("Extension token sent to built-in"))
        monkeypatch.setattr(oauth_user_profile, name, lookup)
        lookups.append(lookup)
    assert asyncio.run(
        oauth_user_profile.fetch_oauth_user_profile("keycloak", "extension-token")
    ) is None
    for lookup in lookups:
        lookup.assert_not_awaited()


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


def test_new_protocol_plugin_uses_shared_login_without_code_parameter(
    tmp_path, monkeypatch
):
    package = tmp_path / "ticket_identity"
    package.mkdir()
    (package / "__init__.py").write_text(
        """
from plugins_market.identity_extensions.contracts import AuthorizationStart, IdentityError, LoginResult

class Adapter:
    async def start_authorization(self, *, state):
        return AuthorizationStart(
            f"https://login.test/authorize?state={state}", {"ticket": "valid"},
            callback_uri="https://hub.test/api/v1/auth/oauth/tickets/callback",
        )

    async def complete_authorization(self, *, callback_params, transaction):
        if callback_params.get("ticket") != transaction.get("ticket"):
            raise IdentityError("Invalid ticket")
        return LoginResult("ticket-token", {"id": "ticket-user", "login": "alice", "name": "Alice"})

    async def fetch_profile(self, access_token):
        if access_token == "ticket-token":
            return {"id": "ticket-user", "login": "alice", "name": "Alice"}
        return None

def create_adapter(*, provider_id, settings):
    return Adapter()
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(extension_packages, "__path__", [str(tmp_path)])
    path = tmp_path / "identity.yaml"
    path.write_text(
        "providers:\n  tickets:\n    enabled: true\n    adapter: ticket_identity\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "identity_extensions_config", str(path))
    monkeypatch.setattr(oauth_routes, "get_oauth_str_store", lambda: store)
    store = _MemoryOAuthStore()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(builtin_router, prefix="/api/v1/auth")
    with TestClient(app, base_url="https://hub.test") as client:
        start = client.get("/api/v1/auth/oauth/tickets/start", follow_redirects=False)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        callback = client.get(
            "/api/v1/auth/oauth/tickets/callback",
            params={"state": state, "ticket": "valid"},
            follow_redirects=False,
        )
        pending = parse_qs(urlsplit(callback.headers["location"]).query)[
            "oauth_session"
        ][0]
        exchanged = client.post(
            "/api/v1/auth/oauth/tickets/session", json={"session": pending}
        )
        assert exchanged.json()["data"]["user"]["id"] == "ticket-user"
        me = client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": "Bearer ticket-token",
                "X-OAuth-Provider": "tickets",
            },
        )
        assert me.status_code == 200
        assert me.json()["data"]["login"] == "alice"


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
            {"providers": {"keycloak": {"enabled": True, "adapter": "missing"}}}
        )
    path = tmp_path / "duplicate.yaml"
    path.write_text("providers:\n  duplicate: {}\n  duplicate: {}\n", encoding="utf-8")
    monkeypatch.setattr(settings, "identity_extensions_config", str(path))
    with pytest.raises(ValueError):
        registry.get_registry()


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ("providers: {}\nproviders: {}\n", ValueError),
        (
            "providers:\n  keycloak:\n    enabled: false\n    enabled: true\n",
            ValueError,
        ),
        ("providers:\n  1: {}\n", ValueError),
        ("providers: !!python/tuple []\n", yaml.constructor.ConstructorError),
        ("providers: {}\n---\nproviders: {}\n", yaml.composer.ComposerError),
    ],
)
def test_extension_yaml_rejects_ambiguous_or_unsafe_input(
    tmp_path, monkeypatch, payload, error
):
    path = tmp_path / "extensions.yaml"
    path.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(settings, "identity_extensions_config", str(path))
    with pytest.raises(error):
        registry.get_registry()
    assert registry.get_runtime_registry().public_providers() == []


@pytest.fixture
def healthy_identity_channel(monkeypatch):
    monkeypatch.setenv("TEST_ISOLATION_SECRET", "test-client-secret")
    return {
        "enabled": True,
        "adapter": "keycloak",
        "settings": {
            "issuer": "https://sso.test/realms/company",
            "client_id": "hub",
            "client_secret_env": "TEST_ISOLATION_SECRET",
            "redirect_uri": "https://hub.test/api/v1/auth/oauth/healthy/callback",
        },
    }


def _configure_isolation_channels(tmp_path, monkeypatch, channels):
    path = tmp_path / "isolation.yaml"
    path.write_text(
        yaml.safe_dump({"providers": channels}, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setattr(settings, "identity_extensions_config", str(path))


def _assert_partial_registry_preserves_healthy_channel():
    # Strict validation must not discard or repeatedly initialize the runtime result.
    with pytest.raises(ValueError, match="Identity provider initialization failed"):
        registry.get_registry()
    runtime = registry.get_runtime_registry()
    assert [p["id"] for p in runtime.public_providers()] == ["healthy"]
    assert runtime.get("healthy") is not None
    assert registry.get_runtime_registry() is runtime
    with pytest.raises(ValueError, match="Identity provider initialization failed"):
        registry.get_registry()
    return runtime


@pytest.mark.parametrize("bad_first", [False, True])
@pytest.mark.parametrize(
    "bad_entry",
    [
        None,
        {"enabled": "true"},
        {"enabled_env": "invalid variable"},
        {"enabled": True, "unexpected": "sensitive-config-value"},
        {"enabled": True, "adapter": "absent"},
        {"enabled": True, "adapter": "keycloak", "display_name": ""},
        {"enabled": True, "adapter": "keycloak", "settings": []},
        {"enabled": True, "adapter": "keycloak", "settings": {}},
    ],
)
def test_bad_channel_configuration_preserves_healthy_channel(
    tmp_path, monkeypatch, healthy_identity_channel, bad_first, bad_entry
):
    channels = {"healthy": healthy_identity_channel, "broken": bad_entry}
    if bad_first:
        channels = dict(reversed(list(channels.items())))
    _configure_isolation_channels(tmp_path, monkeypatch, channels)
    log = Mock()
    monkeypatch.setattr(registry, "logger", log)
    runtime = _assert_partial_registry_preserves_healthy_channel()
    assert runtime.get("broken") is None
    assert log.error.call_count == 1
    assert log.error.call_args.kwargs["provider_id"] == "broken"
    assert "sensitive-config-value" not in str(log.mock_calls)


@pytest.mark.parametrize(
    "bad_id", ["github", "gitcode", "agentos", "../private\nvalue"]
)
def test_invalid_or_reserved_channel_id_does_not_disable_other_channels(
    tmp_path, monkeypatch, healthy_identity_channel, bad_id
):
    _configure_isolation_channels(
        tmp_path,
        monkeypatch,
        {bad_id: {"enabled": True}, "healthy": healthy_identity_channel},
    )
    log = Mock()
    monkeypatch.setattr(registry, "logger", log)
    runtime = _assert_partial_registry_preserves_healthy_channel()
    assert runtime.get(bad_id) is None
    assert log.error.call_args.kwargs["entry_index"] == 0
    if bad_id.startswith("../"):
        assert log.error.call_args.kwargs["provider_id"] == "<invalid>"
        assert bad_id not in str(log.mock_calls)


@pytest.mark.parametrize(
    "source",
    [
        "def invalid(\n",
        "raise ImportError('sensitive-client-secret')\n",
        "nothing = 1\n",
        "create_adapter = None\n",
        "def create_adapter(**kwargs): raise RuntimeError('sensitive-client-secret')\n",
        "def create_adapter(**kwargs): return object()\n",
        (
            "class Adapter:\n    def start_authorization(self, **kwargs): pass\n"
            "    async def complete_authorization(self, **kwargs): pass\n"
            "    async def fetch_profile(self, token): pass\n"
            "def create_adapter(**kwargs): return Adapter()\n"
        ),
    ],
)
def test_plugin_import_factory_or_contract_error_is_confined_to_its_channel(
    tmp_path, monkeypatch, healthy_identity_channel, source
):
    adapter_name = (
        "isolation_" + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    )
    package = tmp_path / adapter_name
    package.mkdir()
    (package / "__init__.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(
        extension_packages, "__path__", [*extension_packages.__path__, str(tmp_path)]
    )
    _configure_isolation_channels(
        tmp_path,
        monkeypatch,
        {
            "broken": {"enabled": True, "adapter": adapter_name},
            "healthy": healthy_identity_channel,
        },
    )
    log = Mock()
    monkeypatch.setattr(registry, "logger", log)
    runtime = _assert_partial_registry_preserves_healthy_channel()
    assert runtime.get("broken") is None
    assert log.error.call_count == 1
    assert log.error.call_args.kwargs["stage"] == "provider_init"
    assert "sensitive-client-secret" not in str(log.mock_calls)
    assert "exc_info" not in log.error.call_args.kwargs


@pytest.mark.parametrize("bad_first", [False, True])
def test_same_adapter_instances_are_isolated_and_initialization_is_cached(
    tmp_path, monkeypatch, healthy_identity_channel, bad_first
):
    from plugins_market.identity_extensions import keycloak

    monkeypatch.delenv("TEST_ISOLATION_MISSING_SECRET", raising=False)
    broken = {
        **healthy_identity_channel,
        "settings": {
            **healthy_identity_channel["settings"],
            "client_secret_env": "TEST_ISOLATION_MISSING_SECRET",
        },
    }
    channels = {"healthy": healthy_identity_channel, "broken": broken}
    if bad_first:
        channels = dict(reversed(list(channels.items())))
    _configure_isolation_channels(tmp_path, monkeypatch, channels)
    factory = Mock(wraps=keycloak.create_adapter)
    monkeypatch.setattr(keycloak, "create_adapter", factory)
    runtime = _assert_partial_registry_preserves_healthy_channel()
    healthy = runtime.get("healthy")
    for _ in range(3):
        assert registry.get_runtime_registry().get("healthy") is healthy
    assert factory.call_count == 2
    assert {call.kwargs["provider_id"] for call in factory.call_args_list} == {
        "healthy",
        "broken",
    }
    monkeypatch.setattr(settings, "identity_extensions_enabled", False)
    assert registry.get_registry().public_providers() == []
    assert registry.get_runtime_registry().public_providers() == []
    assert factory.call_count == 2


@pytest.mark.parametrize(
    "payload",
    ["null\n", "[]\n", "providers: []\n", "providers: {}\nunexpected: true\n"],
)
def test_file_level_structure_errors_still_disable_all_extensions(
    tmp_path, monkeypatch, payload
):
    path = tmp_path / "invalid.yaml"
    path.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(settings, "identity_extensions_config", str(path))
    with pytest.raises((ValueError, TypeError)):
        registry.get_registry()
    assert registry.get_runtime_registry().public_providers() == []


@pytest.mark.parametrize("extensions_enabled", [False, True])
@pytest.mark.parametrize("provider", ["github", "gitcode", "agentos"])
def test_builtin_profile_dispatch_is_unchanged(provider, monkeypatch, extensions_enabled):
    monkeypatch.setattr(settings, "identity_extensions_enabled", extensions_enabled)
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
    function_name = functions.get(provider)
    assert function_name is not None
    selected_mock = mocks.get(function_name)
    assert selected_mock is not None
    selected_mock.assert_awaited_once_with("old-token")
    assert sum(mock.await_count for mock in mocks.values()) == 1
    assert {p.value for p in OAuthProvider} == {"gitcode", "github", "agentos"}
    assert normalize_oauth_provider_header(None) == "gitcode"


@pytest.mark.parametrize("extensions_enabled", [False, True])
@pytest.mark.parametrize("provider", ["github", "gitcode", "agentos"])
def test_builtin_auth_never_reads_extension_registry(provider, monkeypatch, extensions_enabled):
    monkeypatch.setattr(settings, "identity_extensions_enabled", extensions_enabled)

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
    function_name = names.get(provider)
    assert function_name is not None
    monkeypatch.setattr(oauth_user_profile, function_name, original)
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


@pytest.mark.parametrize("extensions_enabled", [False, True])
@pytest.mark.parametrize("provider", ["github", "gitcode", "agentos"])
def test_builtin_full_oauth_contract_stays_compatible(provider, monkeypatch, extensions_enabled):
    monkeypatch.setattr(settings, "identity_extensions_enabled", extensions_enabled)

    def forbidden():
        raise AssertionError("Built-in login must not load identity extensions")

    monkeypatch.setattr(registry, "get_runtime_registry", forbidden)
    for suffix, value in {
        "enabled": True,
        "client_id": "original-client",
        "client_secret": "original-secret",
        "redirect_uri": "https://hub.test/original-callback",
        "authorize_url": "https://idp.test/authorize",
        "token_url": "https://idp.test/token",
    }.items():
        monkeypatch.setattr(settings, f"{provider}_oauth_{suffix}", value)
    monkeypatch.setattr(settings, "agentos_auth_user_api_url", "https://idp.test/user")
    store = _MemoryOAuthStore()
    monkeypatch.setattr(oauth_routes, "get_oauth_str_store", lambda: store)
    upstream = AsyncMock()
    upstream.__aenter__.return_value = upstream
    upstream.post.return_value = httpx.Response(
        200,
        json={
            "access_token": "original-token",
            "token_type": "Bearer",
            "refresh_token": "not-returned",
        },
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: upstream)
    profile = AsyncMock(
        return_value={
            "id": 17,
            "username": "alice",
            "name": "",
            "avatar": "https://images.test/a",
        }
    )
    monkeypatch.setattr(oauth_routes, "fetch_oauth_user_profile", profile)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(builtin_router, prefix="/api/v1/auth")
    with TestClient(app, base_url="https://hub.test") as client:
        start = client.get(
            f"/api/v1/auth/oauth/{provider}/start",
            params={"redirect_to": "http://127.0.0.1:8080/cb"},
            follow_redirects=False,
        )
        assert start.status_code == 302
        assert "set-cookie" not in start.headers
        query = parse_qs(urlsplit(start.headers["location"]).query)
        assert query["client_id"] == ["original-client"]
        assert query["redirect_uri"] == ["https://hub.test/original-callback"]
        state = query["state"][0]
        assert json.loads(store.get(f"market_oauth_state:{provider}:{state}")) == {
            "redirect_to": "http://127.0.0.1:8080/cb"
        }
        callback = client.get(
            f"/api/v1/auth/oauth/{provider}/callback",
            params={"state": state, "code": "original-code"},
            follow_redirects=False,
        )
        assert "set-cookie" not in callback.headers
        assert store.get(f"market_oauth_state:{provider}:{state}") is None
        pending = parse_qs(urlsplit(callback.headers["location"]).query)[
            "oauth_session"
        ][0]
        assert store.get(f"market_oauth_pending:{provider}:{pending}")
        response = client.post(
            f"/api/v1/auth/oauth/{provider}/session", json={"session": pending}
        )
        assert response.json() == {
            "code": 200,
            "message": "ok",
            "data": {
                "provider": provider,
                "access_token": "original-token",
                "token_type": "Bearer",
                "user": {
                    "id": "17",
                    "login": "alice",
                    "name": "alice",
                    "avatar_url": "https://images.test/a",
                },
            },
        }
        assert store.get(f"market_oauth_pending:{provider}:{pending}") is None
        assert (
            client.post(
                f"/api/v1/auth/oauth/{provider}/session", json={"session": pending}
            ).status_code
            == 400
        )
    profile.assert_awaited_once_with(provider, "original-token")


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
            "providers:\n  keycloak:\n    enabled: true\n    adapter: absent\n",
            encoding="utf-8",
        )
    elif broken == "missing-secret":
        monkeypatch.delenv("TEST_MISSING_EXTENSION_SECRET", raising=False)
        path.write_text(
            json.dumps(
                {
                    "providers": {
                        "keycloak": {
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
    register_exception_handlers(app)
    app.include_router(builtin_router, prefix="/api/v1/auth")
    with TestClient(app) as client:
        original = client.get("/api/v1/auth/oauth/github/start", follow_redirects=False)
        assert original.status_code == 302
        assert parse_qs(urlsplit(original.headers["location"]).query)["client_id"] == [
            "original-client"
        ]
        assert client.get("/api/v1/auth/oauth/keycloak/start").status_code == 404


@pytest.fixture
def oidc_app(tmp_path, monkeypatch, request):
    issuer = "https://sso.example.test/realms/company"
    configured_issuer = issuer + getattr(request, "param", "")
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
                        "display_name": "Keycloak",
                        "settings": {
                            "issuer": configured_issuer,
                            "client_id": "skillhub",
                            "client_secret_env": "TEST_IDENTITY_SECRET",
                            "redirect_uri": (
                                f"https://hub.example.test/api/v1/auth/oauth/{name}/callback"
                            ),
                        },
                    }
                    for name in ("keycloak", "other")
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "identity_extensions_config", str(path))
    store = _MemoryOAuthStore()
    monkeypatch.setattr(oauth_routes, "get_oauth_str_store", lambda: store)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public.update(kid="key1", alg="RS256", use="sig")
    state = {
        "fault": None,
        "nonce": "",
        "challenge": "",
        "requests": [],
        "tls_requests": [],
    }

    def upstream(request):
        state["requests"].append(request)
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": state.get("metadata_issuer", issuer),
                    "authorization_endpoint": f"{issuer}/auth",
                    "token_endpoint": f"{issuer}/token",
                    "userinfo_endpoint": f"{issuer}/userinfo",
                    "jwks_uri": f"{issuer}/certs",
                },
            )
        if request.url.path.endswith("/certs"):
            if state["fault"] == "jwks":
                return httpx.Response(200, json={"keys": None})
            keys = [public]
            if state["fault"] == "mixed-jwks":
                keys = [
                    None,
                    "not-a-key",
                    {**public, "kid": "other-key"},
                    {**public, "use": "enc"},
                    {**public, "alg": "RS512"},
                    public,
                ]
            if state["fault"] == "duplicate-jwks":
                keys = [public, public]
            return httpx.Response(200, json={"keys": keys})
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
                "iss": state.get("token_issuer", issuer),
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
            if fault in {
                "multi-audience-missing-azp",
                "multi-audience-valid-azp",
                "multi-audience-wrong-azp",
            }:
                claims["aud"] = ["skillhub", "another-app"]
            if fault in {"valid-azp", "multi-audience-valid-azp"}:
                claims["azp"] = "skillhub"
            if fault in {"azp", "multi-audience-wrong-azp"}:
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

    def oidc_client(**kwargs):
        verify = kwargs.get("verify", True)

        def tracked_upstream(request):
            state["tls_requests"].append((request.url.path, verify))
            return upstream(request)

        return original_client(transport=httpx.MockTransport(tracked_upstream), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", oidc_client)
    app = FastAPI()
    register_exception_handlers(app)
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
        f"/api/v1/auth/oauth/keycloak/start{query}", follow_redirects=False
    )
    assert response.status_code == 302
    params = parse_qs(urlsplit(response.headers["location"]).query)
    state["nonce"] = params["nonce"][0]
    state["challenge"] = params["code_challenge"][0]
    return params["state"][0]


@pytest.mark.parametrize("prompt", [None, "login"])
def test_optional_login_prompt(oidc_app, prompt):
    app, _, _ = oidc_app
    adapter = registry.get_registry().get("keycloak").adapter
    adapter.config.prompt = prompt
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/auth/oauth/keycloak/start", follow_redirects=False
        )
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params.get("prompt") == (["login"] if prompt else None)


def test_keycloak_ssl_verify_is_isolated_between_channels(oidc_app):
    from pathlib import Path

    _, state, _ = oidc_app
    path = Path(settings.identity_extensions_config)
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["providers"]["keycloak"]["settings"]["ssl_verify"] = False
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    providers = registry.get_registry()

    for channel, expected_verify in (("keycloak", False), ("other", True)):
        state["tls_requests"].clear()
        adapter = providers.get(channel).adapter
        assert adapter.config.ssl_verify is expected_verify
        assert asyncio.run(adapter.fetch_profile("access-token"))["login"] == "reviewer01"
        assert len(state["tls_requests"]) == 2
        assert all(verify is expected_verify for _, verify in state["tls_requests"])

    assert providers.get("keycloak").adapter.config.ssl_verify is False


def finish(client, login_state):
    return client.get(
        "/api/v1/auth/oauth/keycloak/callback",
        params={"state": login_state, "code": "code"},
        follow_redirects=False,
    )


@pytest.mark.parametrize("bad_first", [False, True])
def test_healthy_oidc_login_and_business_auth_survive_another_channel_failure(
    oidc_app, monkeypatch, bad_first
):
    from pathlib import Path

    from plugins_market.routers.site_public import get_site_config

    app, state, _ = oidc_app
    path = Path(settings.identity_extensions_config)
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    channels = config["providers"]
    channels["broken"] = {"enabled": True, "adapter": "absent"}
    if bad_first:
        channels = dict(reversed(list(channels.items())))
    path.write_text(
        yaml.safe_dump({"providers": channels}, sort_keys=False), encoding="utf-8"
    )
    log = Mock()
    monkeypatch.setattr(registry, "logger", log)
    with TestClient(app, base_url="https://hub.example.test") as client:
        callback = finish(client, begin(client, state))
        pending = parse_qs(urlsplit(callback.headers["location"]).query)[
            "oauth_session"
        ][0]
        exchanged = client.post(
            "/api/v1/auth/oauth/keycloak/session", json={"session": pending}
        )
        assert exchanged.status_code == 200
        assert exchanged.json()["data"]["user"]["id"] == "keycloak-user"
        headers = {
            "Authorization": "Bearer access-token",
            "X-OAuth-Provider": "keycloak",
        }
        assert (
            client.get("/api/v1/auth/me", headers=headers).json()["data"]["login"]
            == "reviewer01"
        )
        assert client.get("/business", headers=headers).json() == {
            "publisher_id": "keycloak-user",
            "publisher_name": "reviewer01",
            "is_admin": False,
            "reviewer": True,
        }
        assert {
            p["id"] for p in asyncio.run(get_site_config())["identity_extensions"]
        } == {
            "keycloak",
            "other",
        }
        upstream_count = len(state["requests"])
        assert client.get("/api/v1/auth/oauth/broken/start").status_code == 404
        assert (
            client.get(
                "/api/v1/auth/oauth/broken/callback?state=valid-state&code=code"
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/v1/auth/oauth/broken/session", json={"session": "valid-session"}
            ).status_code
            == 404
        )
        broken_headers = {**headers, "X-OAuth-Provider": "broken"}
        assert client.get("/business", headers=broken_headers).status_code == 400
        assert (
            asyncio.run(
                oauth_user_profile.fetch_oauth_user_profile("broken", "access-token")
            )
            is None
        )
        assert len(state["requests"]) == upstream_count
        with pytest.raises(ValueError, match="broken"):
            registry.get_registry()
        assert client.get("/business", headers=headers).status_code == 200
    assert log.error.call_count == 1


def test_global_off_blocks_cached_providers_pending_login_and_existing_token(
    oidc_app, monkeypatch
):
    from plugins_market.routers import plugin
    from plugins_market.routers.site_public import get_site_config

    app, state, store = oidc_app
    app.include_router(plugin.router, prefix="/api/v1")

    def no_io_dependency():
        return None

    # Keep real publish/moderation authentication; bypass only local I/O dependencies.
    app.dependency_overrides[plugin.build_publish_form] = no_io_dependency
    app.dependency_overrides[plugin.get_publish_plugin_dependencies] = no_io_dependency
    app.dependency_overrides[plugin.get_db] = no_io_dependency
    app.dependency_overrides[plugin.get_storage_client] = no_io_dependency
    publish = Mock(side_effect=AssertionError("Disabled user reached publishing"))
    moderate = Mock(side_effect=AssertionError("Disabled user reached moderation"))
    monkeypatch.setattr(plugin, "plugin_publish", publish)
    monkeypatch.setattr(plugin, "moderate_skill_asset_service", moderate)
    headers = {
        "Authorization": "Bearer access-token",
        "X-OAuth-Provider": "keycloak",
    }
    with TestClient(app, base_url="https://hub.example.test") as client:
        callback = finish(client, begin(client, state))
        session = parse_qs(urlsplit(callback.headers["location"]).query)["oauth_session"][0]
        exchanged = client.post(
            "/api/v1/auth/oauth/keycloak/session", json={"session": session}
        )
        assert exchanged.json()["data"]["access_token"] == "access-token"
        assert client.get("/business", headers=headers).json()["reviewer"] is True
        pending_callback = finish(client, begin(client, state))
        pending = parse_qs(urlsplit(pending_callback.headers["location"]).query)[
            "oauth_session"
        ][0]
        login_state = begin(client, state)
        loaded = registry.get_registry()
        assert registry.get_runtime_registry() is loaded
        assert loaded.get("keycloak") is not None
        upstream_count = len(state["requests"])

        monkeypatch.setattr(settings, "identity_extensions_enabled", False)
        assert registry.get_registry().get("keycloak") is None
        assert registry.get_runtime_registry().public_providers() == []
        assert asyncio.run(get_site_config())["identity_extensions"] == []
        assert client.get("/api/v1/auth/oauth/keycloak/start").status_code == 404
        assert finish(client, login_state).status_code == 404
        assert client.post(
            "/api/v1/auth/oauth/keycloak/session", json={"session": pending}
        ).status_code == 404
        for path in ("/api/v1/auth/me", "/business"):
            assert client.get(path, headers=headers).status_code == 400
        assert client.post("/api/v1/plugins", headers=headers).status_code == 400
        assert client.post(
            "/api/v1/plugins/asset/moderation",
            headers=headers,
            json={"action": "approve", "version": "1.0.0"},
        ).status_code == 400
        assert asyncio.run(
            oauth_user_profile.fetch_oauth_user_profile("keycloak", "access-token")
        ) is None
        assert len(state["requests"]) == upstream_count
        # Refused exchanges do not consume Redis entries; normal TTL handles expiry.
        assert store.get(f"market_oauth_state:keycloak:{login_state}")
        assert store.get(f"market_oauth_pending:keycloak:{pending}")
    publish.assert_not_called()
    moderate.assert_not_called()


def test_global_off_preserves_system_token_authentication(monkeypatch):
    from fastapi import Request
    from plugins_market.core import auth as auth_module

    monkeypatch.setattr(settings, "identity_extensions_enabled", False)
    monkeypatch.setattr(auth_module, "_resolved_system_admin_token", lambda: "system-token")
    forbidden = Mock(side_effect=AssertionError("System token consulted identity plugins"))
    monkeypatch.setattr(registry, "get_runtime_registry", forbidden)
    auth = asyncio.run(
        require_auth(
            Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)}),
            authorization=None,
            x_system_token="system-token",
            x_oauth_provider="keycloak",
        )
    )
    assert auth.is_admin is True
    assert auth.is_market_moderation_admin is True
    assert auth.acting_user_id == settings.system_admin_user
    forbidden.assert_not_called()


@pytest.mark.parametrize(
    ("frontend_prefix", "callback_prefix", "suffix"),
    [
        ("", "", ""),
        ("/hub", "", ""),
        ("/hub", "/hub", ""),
        ("", "", "/"),
        ("/hub", "/hub", "/"),
    ],
)
def test_browser_cookie_uses_external_callback_path(
    oidc_app, monkeypatch, frontend_prefix, callback_prefix, suffix
):
    app, state, _ = oidc_app
    provider = registry.get_registry().get("keycloak")
    callback_path = f"{callback_prefix}/api/v1/auth/oauth/keycloak/callback"
    callback_uri = f"https://hub.example.test{callback_path}{suffix}"
    provider.adapter.config.redirect_uri = callback_uri
    monkeypatch.setattr(
        settings, "oauth_frontend_origin", f"https://hub.example.test{frontend_prefix}"
    )
    with TestClient(
        app, base_url="https://hub.example.test", root_path=callback_prefix
    ) as client:
        response = client.get(
            f"{callback_prefix}/api/v1/auth/oauth/keycloak/start",
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert f"Path={callback_path}" in response.headers["set-cookie"]
        params = parse_qs(urlsplit(response.headers["location"]).query)
        state["nonce"] = params["nonce"][0]
        state["challenge"] = params["code_challenge"][0]
        callback = client.get(
            f"{callback_path}{suffix}",
            params={"state": params["state"][0], "code": "code"},
            follow_redirects=False,
        )
        if suffix:
            assert callback.status_code == 307
            callback = client.get(callback.headers["location"], follow_redirects=False)
        assert "oauth_session" in parse_qs(urlsplit(callback.headers["location"]).query)
        assert urlsplit(callback.headers["location"]).path == f"{frontend_prefix}/login"
        assert f"Path={callback_path}" in callback.headers["set-cookie"]
        assert provider.adapter.config.redirect_uri == callback_uri
        posted_redirects = []
        for upstream_request in state["requests"]:
            if upstream_request.url.path.endswith("/token"):
                posted_redirects.extend(
                    parse_qs(upstream_request.content.decode()).get("redirect_uri", [])
                )
        assert posted_redirects == [callback_uri]


def test_extension_uses_shared_operation_logs_and_no_duplicate_routes(
    oidc_app, monkeypatch
):
    app, state, _ = oidc_app
    logger = Mock()
    monkeypatch.setattr(oauth_routes, "logger", logger)
    with TestClient(app, base_url="https://hub.example.test") as client:
        response = finish(client, begin(client, state))
        pending = parse_qs(urlsplit(response.headers["location"]).query)[
            "oauth_session"
        ][0]
        assert (
            client.post(
                "/api/v1/auth/oauth/keycloak/session", json={"session": pending}
            ).status_code
            == 200
        )
        state["fault"] = "nonce"
        assert "oauth_error" in finish(client, begin(client, state)).headers["location"]
        assert client.get("/api/v1/auth/extensions/keycloak/start").status_code == 404
    operations = {
        call.kwargs.get("operation_type") for call in logger.info.call_args_list
    }
    assert operations == {"oauth_start", "oauth_callback", "oauth_session_exchange"}
    for call in logger.info.call_args_list:
        assert call.kwargs.get("operation_id")
        assert call.kwargs.get("provider") == "keycloak"
    assert any(
        call.kwargs.get("result") == "failure" for call in logger.warning.call_args_list
    )


@pytest.fixture
def identity_operation_logs(oidc_app, monkeypatch):
    from plugins_market.core.middleware import request_id
    from plugins_market.identity_extensions import operations

    app, state, store = oidc_app
    log = Mock()
    monkeypatch.setattr(settings, "interface_log_enabled", False)
    monkeypatch.setattr(oauth_routes, "logger", log)
    monkeypatch.setattr(operations, "logger", log)
    monkeypatch.setattr(request_id, "logger", log)
    register_exception_handlers(app, logger=log)
    app.add_middleware(request_id.RequestIDMiddleware)
    return app, state, store, log


def _completion_for_response(log, response):
    request_id = response.headers["x-request-id"]
    completions = []
    for call in log.method_calls:
        record = call.kwargs
        is_completion = record.get("stage") == "complete"
        matches_request = record.get("request_id") == request_id
        if is_completion and matches_request:
            completions.append(record)
    assert len(completions) == 1
    fields = completions[0]
    assert fields["operation_id"].startswith("op_")
    assert fields["resource_type"] == "oauth_provider"
    assert fields["resource_id"] == fields["provider"]
    return fields


def test_extension_success_logs_have_real_request_context_and_verified_actor(
    identity_operation_logs,
):
    app, state, _, log = identity_operation_logs
    with TestClient(app, base_url="https://hub.example.test") as client:
        start = client.get(
            "/api/v1/auth/oauth/keycloak/start", follow_redirects=False
        )
        fields = _completion_for_response(log, start)
        assert fields["operation_type"] == "oauth_start"
        assert fields["actor_type"] == "anonymous"
        assert fields["result"] == "success"
        assert "actor_id" not in fields
        params = parse_qs(urlsplit(start.headers["location"]).query)
        state["nonce"] = params["nonce"][0]
        state["challenge"] = params["code_challenge"][0]
        callback = finish(client, params["state"][0])
        pending = parse_qs(urlsplit(callback.headers["location"]).query)[
            "oauth_session"
        ][0]
        exchanged = client.post(
            "/api/v1/auth/oauth/keycloak/session", json={"session": pending}
        )
        me = client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": "Bearer access-token",
                "X-OAuth-Provider": "keycloak",
            },
        )
        for response, operation in (
            (callback, "oauth_callback"),
            (exchanged, "oauth_session_exchange"),
            (me, "oauth_me"),
        ):
            fields = _completion_for_response(log, response)
            assert fields["operation_type"] == operation
            assert fields["result"] == "success"
            assert fields["actor_id"] == "keycloak-user"
            assert fields["actor_type"] == "oauth_user"
        rendered = str(log.mock_calls)
        for credential in (
            params["state"][0],
            params["nonce"][0],
            pending,
            "access-token",
        ):
            assert credential not in rendered


@pytest.mark.parametrize("failure", ["global-off", "unknown", "broken", "invalid-id"])
def test_extension_resolution_failures_have_operation_logs(
    identity_operation_logs, monkeypatch, failure
):
    from pathlib import Path

    app, state, _, log = identity_operation_logs
    provider = "keycloak"
    if failure == "global-off":
        registry.get_runtime_registry()
        monkeypatch.setattr(settings, "identity_extensions_enabled", False)
    elif failure == "unknown":
        provider = "absent"
    elif failure == "broken":
        path = Path(settings.identity_extensions_config)
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config["providers"]["broken"] = {"enabled": True, "adapter": "absent"}
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        provider = "broken"
    else:
        provider = "invalid!private-marker"
    with TestClient(app, base_url="https://hub.example.test") as client:
        responses = [
            client.get(f"/api/v1/auth/oauth/{provider}/start", follow_redirects=False),
            client.get(
                f"/api/v1/auth/oauth/{provider}/callback?code=private-code&state=private-state"
            ),
            client.post(
                f"/api/v1/auth/oauth/{provider}/session",
                json={"session": "private-session"},
            ),
        ]
    for response, operation in zip(
        responses, ("oauth_start", "oauth_callback", "oauth_session_exchange")
    ):
        assert response.status_code == 404
        fields = _completion_for_response(log, response)
        assert fields["operation_type"] == operation
        assert fields["result"] == "invalid"
        assert fields["provider"] == (
            "<invalid>" if failure == "invalid-id" else provider
        )
        assert "actor_id" not in fields
    assert state["requests"] == []
    for credential in (
        "private-code",
        "private-state",
        "private-session",
        "private-marker",
    ):
        assert credential not in str(log.mock_calls)


@pytest.mark.parametrize(
    "failure", ["expired", "binding", "nonce", "cancel", "missing", "replay"]
)
def test_extension_callback_failure_logs_once_even_for_http_redirects(
    identity_operation_logs, failure
):
    app, state, store, log = identity_operation_logs
    with TestClient(app, base_url="https://hub.example.test") as client:
        login_state = begin(client, state)
        if failure == "expired":
            store.get_del(f"market_oauth_state:keycloak:{login_state}")
        elif failure == "binding":
            client.cookies.clear()
        elif failure == "nonce":
            state["fault"] = "nonce"
        elif failure == "replay":
            assert "oauth_session" in finish(client, login_state).headers["location"]
        if failure == "cancel":
            response = client.get(
                "/api/v1/auth/oauth/keycloak/callback",
                params={
                    "state": login_state,
                    "error": "access_denied",
                    "error_description": "private-upstream-body",
                },
                follow_redirects=False,
            )
        elif failure == "missing":
            response = client.get(
                "/api/v1/auth/oauth/keycloak/callback", follow_redirects=False
            )
        else:
            response = finish(client, login_state)
    assert response.status_code == 302
    assert "oauth_error" in parse_qs(urlsplit(response.headers["location"]).query)
    fields = _completion_for_response(log, response)
    assert fields["operation_type"] == "oauth_callback"
    assert fields["result"] == "failure"
    assert "actor_id" not in fields
    assert "private-upstream-body" not in str(log.mock_calls)


@pytest.mark.parametrize(
    "failure", ["expired", "replay", "cross-provider", "corrupt", "422"]
)
def test_extension_session_failures_log_once_including_pre_handler_validation(
    identity_operation_logs, failure
):
    app, state, store, log = identity_operation_logs
    with TestClient(app, base_url="https://hub.example.test") as client:
        callback = finish(client, begin(client, state))
        pending = parse_qs(urlsplit(callback.headers["location"]).query)[
            "oauth_session"
        ][0]
        provider = "keycloak"
        if failure == "expired":
            store.get_del(f"market_oauth_pending:keycloak:{pending}")
        elif failure == "replay":
            assert (
                client.post(
                    "/api/v1/auth/oauth/keycloak/session", json={"session": pending}
                ).status_code
                == 200
            )
        elif failure == "cross-provider":
            provider = "other"
        elif failure == "corrupt":
            store.set_ex(
                f"market_oauth_pending:keycloak:{pending}", "private-broken-json", 120
            )
        body = {"session": "short"} if failure == "422" else {"session": pending}
        response = client.post(f"/api/v1/auth/oauth/{provider}/session", json=body)
    assert response.status_code == (422 if failure == "422" else 400)
    fields = _completion_for_response(log, response)
    assert fields["operation_type"] == "oauth_session_exchange"
    assert fields["result"] == "invalid"
    assert "actor_id" not in fields
    assert "private-broken-json" not in str(log.mock_calls)
    assert pending not in str(log.mock_calls)


@pytest.mark.parametrize(
    "method", ["start_authorization", "complete_authorization", "fetch_profile"]
)
@pytest.mark.parametrize("error_kind", ["expected", "unexpected", "network"])
def test_plugin_exceptions_are_safe_and_have_one_operation_completion(
    identity_operation_logs, monkeypatch, method, error_kind
):
    from plugins_market.identity_extensions.contracts import IdentityError

    app, state, _, log = identity_operation_logs
    provider = registry.get_runtime_registry().get("keycloak")
    private = "private-token private-secret private-cookie private-verifier private-code private-nonce"
    errors = {
        "expected": IdentityError(private),
        "unexpected": RuntimeError(private),
        "network": httpx.ConnectError(
            private,
            request=httpx.Request("GET", "https://sso.test/?token=private-token"),
        ),
    }
    error = errors.get(error_kind)
    assert error is not None
    with TestClient(app, base_url="https://hub.example.test") as client:
        login_state = (
            begin(client, state) if method == "complete_authorization" else None
        )
        monkeypatch.setattr(
            provider.adapter, method, AsyncMock(side_effect=error)
        )
        if method == "start_authorization":
            response = client.get(
                "/api/v1/auth/oauth/keycloak/start", follow_redirects=False
            )
        elif method == "complete_authorization":
            response = finish(client, login_state)
        else:
            response = client.get(
                "/api/v1/auth/me",
                headers={
                    "Authorization": "Bearer private-token",
                    "X-OAuth-Provider": "keycloak",
                },
            )
    fields = _completion_for_response(log, response)
    if method == "complete_authorization":
        assert response.status_code == 302
        assert parse_qs(urlsplit(response.headers["location"]).query)[
            "oauth_status"
        ] == ["500" if error_kind == "unexpected" else "502"]
    else:
        expected_status = 401 if method == "fetch_profile" else 502
        assert response.status_code == (
            500 if error_kind == "unexpected" else expected_status
        )
    assert fields["result"] in {"failure", "invalid"}
    assert "actor_id" not in fields
    for credential in private.split():
        assert credential not in str(log.mock_calls)
    log.exception.assert_not_called()
    assert registry.get_runtime_registry().get("other") is not None


@pytest.mark.parametrize(
    "failure", ["missing-bearer", "invalid-token", "invalid-provider"]
)
def test_extension_me_validation_failures_have_anonymous_operation_logs(
    identity_operation_logs, failure
):
    app, state, _, log = identity_operation_logs
    headers = {"X-OAuth-Provider": "keycloak"}
    if failure != "missing-bearer":
        headers["Authorization"] = "Bearer access-token"
    if failure == "invalid-token":
        state["fault"] = "invalid-token"
    elif failure == "invalid-provider":
        headers["X-OAuth-Provider"] = "absent"
    with TestClient(app, base_url="https://hub.example.test") as client:
        response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == (400 if failure == "invalid-provider" else 401)
    fields = _completion_for_response(log, response)
    assert fields["operation_type"] == "oauth_me"
    assert fields["result"] == "invalid"
    assert fields["actor_type"] == "anonymous"
    assert "actor_id" not in fields


def test_extension_response_construction_failure_is_not_logged_as_success(
    identity_operation_logs, monkeypatch
):
    app, _, _, log = identity_operation_logs
    monkeypatch.setattr(
        oauth_routes,
        "set_browser_binding",
        Mock(side_effect=RuntimeError("private-cookie")),
    )
    with TestClient(app, base_url="https://hub.example.test") as client:
        response = client.get(
            "/api/v1/auth/oauth/keycloak/start", follow_redirects=False
        )
    assert response.status_code == 500
    fields = _completion_for_response(log, response)
    assert fields["result"] == "failure"
    assert "private-cookie" not in str(log.mock_calls)
    log.exception.assert_not_called()


@pytest.mark.parametrize("payload", ["shape", "json"])
def test_extension_validation_logs_do_not_include_rejected_request_body(
    identity_operation_logs, payload
):
    app, _, _, log = identity_operation_logs
    with TestClient(app, base_url="https://hub.example.test") as client:
        if payload == "shape":
            response = client.post(
                "/api/v1/auth/oauth/keycloak/session",
                json={"session": ["private-token"]},
            )
        else:
            response = client.post(
                "/api/v1/auth/oauth/keycloak/session",
                content='{"session":"private-token"',
                headers={"Content-Type": "application/json"},
            )
    assert response.status_code == 422
    fields = _completion_for_response(log, response)
    assert fields["operation_type"] == "oauth_session_exchange"
    assert fields["result"] == "invalid"
    assert "private-token" not in str(log.mock_calls)


@pytest.mark.parametrize("oidc_app", ["", "/"], indirect=True)
@pytest.mark.parametrize("ssl_verify", [None, True, False])
@pytest.mark.parametrize(
    "fault", [None, "mixed-jwks", "valid-azp", "multi-audience-valid-azp"]
)
def test_oidc_login_session_and_existing_business_identity(oidc_app, fault, ssl_verify):
    app, state, _ = oidc_app
    adapter = registry.get_registry().get("keycloak").adapter
    if ssl_verify is not None:
        adapter.config.ssl_verify = ssl_verify
    state["fault"] = fault
    with TestClient(app, base_url="https://hub.example.test") as client:
        login_state = begin(client, state)
        assert str(state["requests"][0].url) == (
            "https://sso.example.test/realms/company/.well-known/openid-configuration"
        )
        response = finish(client, login_state)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["oauth_provider"] == ["keycloak"]
        pending = query["oauth_session"][0]
        wrong = client.post(
            "/api/v1/auth/oauth/other/session", json={"session": pending}
        )
        assert wrong.status_code == 400
        exchanged = client.post(
            "/api/v1/auth/oauth/keycloak/session", json={"session": pending}
        )
        assert exchanged.status_code == 200
        assert exchanged.headers["cache-control"] == "no-store"
        assert exchanged.json()["data"]["user"]["id"] == "keycloak-user"
        assert "refresh_token" not in exchanged.text
        assert (
            client.post(
                "/api/v1/auth/oauth/keycloak/session", json={"session": pending}
            ).status_code
            == 400
        )
        assert "oauth_error" in finish(client, login_state).headers["location"]
        headers = {
            "Authorization": "Bearer access-token",
            "X-OAuth-Provider": "keycloak",
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

    assert {path.rsplit("/", 1)[-1] for path, _ in state["tls_requests"]} == {
        "openid-configuration",
        "token",
        "certs",
        "userinfo",
    }
    expected_verify = ssl_verify is not False
    assert all(verify is expected_verify for _, verify in state["tls_requests"])


@pytest.mark.parametrize("oidc_app", ["", "/"], indirect=True)
@pytest.mark.parametrize("issuer_field", ["metadata_issuer", "token_issuer"])
@pytest.mark.parametrize(
    "untrusted_issuer",
    [
        "https://other.example.test/realms/company",
        "https://sso.example.test/realms/other",
        "https://sso.example.test/realms/company/",
    ],
)
def test_oidc_issuer_normalization_preserves_exact_server_validation(
    oidc_app, issuer_field, untrusted_issuer
):
    app, state, _ = oidc_app
    state[issuer_field] = untrusted_issuer
    with TestClient(app, base_url="https://hub.example.test") as client:
        if issuer_field == "metadata_issuer":
            response = client.get(
                "/api/v1/auth/oauth/keycloak/start", follow_redirects=False
            )
            assert response.status_code == 502
            assert len(state["requests"]) == 1
        else:
            response = finish(client, begin(client, state))
            assert response.status_code == 302
            query = parse_qs(urlsplit(response.headers["location"]).query)
            assert "oauth_error" in query
            assert "oauth_session" not in query
            assert len(state["requests"]) == 3


@pytest.mark.parametrize("ssl_verify", [True, False])
@pytest.mark.parametrize(
    "fault",
    [
        "nonce",
        "unicode-nonce",
        "jwks",
        "duplicate-jwks",
        "issuer",
        "audience",
        "signature",
        "expired",
        "subject",
        "azp",
        "multi-audience-missing-azp",
        "multi-audience-wrong-azp",
        "hash",
        "reserved-id",
    ],
)
def test_invalid_oidc_result_cannot_create_session(oidc_app, fault, ssl_verify):
    app, state, _ = oidc_app
    registry.get_registry().get("keycloak").adapter.config.ssl_verify = ssl_verify
    state["fault"] = fault
    with TestClient(app, base_url="https://hub.example.test") as client:
        response = finish(client, begin(client, state))
        assert "oauth_error" in response.headers["location"]
        assert "oauth_session" not in response.headers["location"]


def test_browser_binding_loopback_and_expiry(oidc_app, monkeypatch):
    app, state, _ = oidc_app
    with TestClient(app, base_url="https://hub.example.test") as client:
        assert client.get("/api/v1/auth/oauth/missing/start").status_code == 404
        assert client.get("/api/v1/auth/oauth/github/start").status_code == 404
        assert (
            client.get(
                "/api/v1/auth/oauth/keycloak/start",
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
        now = time.monotonic()
        monkeypatch.setattr(
            oauth_session_store, "time", SimpleNamespace(monotonic=lambda: now)
        )
        expired = begin(client, state)
        now += 601  # State expires after 600 seconds, without a real-time wait.
        assert "oauth_error" in finish(client, expired).headers["location"]


@pytest.mark.parametrize(
    "change",
    [
        {"scopes": ["profile"]},
        {"algorithms": ["HS256"]},
        {"issuer": "http://untrusted.test"},
        {"issuer": "https:///missing-host"},
        {"issuer": "https://user@sso.test"},
        {"issuer": "https://user:password@sso.test"},
        {"issuer": "https://@sso.test"},
        {"issuer": "https://sso.test#fragment"},
        {"issuer": "https://sso.test?query=value"},
        {"issuer": "https://sso.test/realms/company/?query=value"},
        {"issuer": "https://sso.test/realms/company/#fragment"},
        {"issuer": "https://user:password@sso.test/realms/company/"},
        {"redirect_uri": "http://hub.test/cb"},
        {"redirect_uri": "https://hub.test/cb#fragment"},
        {"ssl_verify": "false"},
        {"ssl_verify": "true"},
        {"ssl_verify": 0},
        {"ssl_verify": 1},
        {"ssl_verify": None},
        {"ssl_verify": False, "issuer": "http://sso.test"},
        {"ssl_verify": False, "redirect_uri": "http://hub.test/cb"},
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


@pytest.mark.parametrize(
    ("options", "expected"),
    [({}, True), ({"ssl_verify": True}, True), ({"ssl_verify": False}, False)],
)
def test_keycloak_ssl_verify_defaults_true_and_accepts_only_booleans(options, expected):
    config = KeycloakConfig.model_validate(
        {
            "issuer": "https://sso.test",
            "client_id": "hub",
            "client_secret_env": "SECRET",
            "redirect_uri": "https://hub.test/cb",
            **options,
        }
    )
    assert config.ssl_verify is expected


@pytest.mark.parametrize(
    ("allow_insecure_http", "issuer", "expected_issuer"),
    [
        (False, "https://sso.test", "https://sso.test"),
        (True, "https://sso.test", "https://sso.test"),
        (True, "http://sso.test", "http://sso.test"),
        (False, "https://sso.test/", "https://sso.test"),
        (
            False,
            "https://sso.test/realms/company/",
            "https://sso.test/realms/company",
        ),
        (
            False,
            "https://sso.test/realms/company///",
            "https://sso.test/realms/company",
        ),
        (True, "http://sso.test/realms/company/", "http://sso.test/realms/company"),
    ],
)
def test_oidc_configuration_accepts_trusted_urls(
    allow_insecure_http, issuer, expected_issuer
):
    config = KeycloakConfig.model_validate(
        {
            "issuer": issuer,
            "client_id": "hub",
            "client_secret_env": "SECRET",
            "redirect_uri": "https://hub.test/cb",
            "allow_insecure_http": allow_insecure_http,
        }
    )
    assert config.issuer == expected_issuer


@pytest.mark.parametrize("ssl_verify", [True, False])
def test_extension_identity_reaches_existing_publish_and_moderation_services(
    oidc_app, monkeypatch, ssl_verify
):
    """Use actual publish routing and actual moderation service, with local I/O fixtures."""
    import io
    from pathlib import Path

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
    path = Path(settings.identity_extensions_config)
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["providers"]["broken"] = {"enabled": True, "adapter": "absent"}
    config["providers"]["keycloak"]["settings"]["ssl_verify"] = ssl_verify
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
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
                x_oauth_provider="keycloak",
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
    publish_auth = plugin.get_publish_auth("Bearer access-token", None, "keycloak")
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
