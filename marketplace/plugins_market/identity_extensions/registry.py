"""Discover adapter packages and instantiate only configured, enabled channels."""

import importlib
import inspect
import os
import pkgutil
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import plugins_market.identity_extensions as extension_packages
import yaml
from plugins_market.core.config import settings
from plugins_market.core.logging import get_logger

from .contracts import IdentityAdapter

BUILTIN_PROVIDERS = frozenset({"gitcode", "github", "agentos"})
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_ENV_VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
logger = get_logger(__name__)


def provider_log_id(provider_id: object) -> str:
    if isinstance(provider_id, str) and _PROVIDER_ID.fullmatch(provider_id):
        return provider_id
    return "<invalid>"


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("Identity configuration requires unique string keys")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@dataclass(frozen=True)
class Provider:
    id: str
    display_name: str
    adapter: IdentityAdapter
    adapter_name: str


def _provider_enabled(entry: dict) -> bool:
    enabled = entry.get("enabled", False)
    if not isinstance(enabled, bool):
        raise TypeError("Identity enabled must be a boolean")
    if "enabled_env" not in entry:
        return enabled
    env_name = entry["enabled_env"]
    if not isinstance(env_name, str) or not _ENV_VAR_NAME.fullmatch(env_name):
        raise ValueError("Invalid identity enabled_env")
    return os.getenv(env_name, "").strip().lower() == "true"


def _build_provider(
    provider_id: str, entry: dict, available: set[str]
) -> Provider | None:
    if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
        raise ValueError("Invalid identity provider ID")
    if provider_id in BUILTIN_PROVIDERS:
        raise ValueError(f"Reserved identity provider: {provider_id}")
    if not isinstance(entry, dict) or set(entry) - {
        "enabled",
        "enabled_env",
        "adapter",
        "display_name",
        "settings",
    }:
        raise ValueError(f"Invalid identity provider configuration: {provider_id}")
    if not _provider_enabled(entry):
        return None
    adapter_name = entry.get("adapter")
    if not isinstance(adapter_name, str) or adapter_name not in available:
        raise ValueError(f"Unknown identity adapter for {provider_id}")
    name = entry.get("display_name", provider_id)
    options = entry.get("settings", {})
    invalid_name = not isinstance(name, str) or not name.strip() or len(name) > 128
    if invalid_name or not isinstance(options, dict):
        raise ValueError(f"Invalid identity provider configuration: {provider_id}")
    module = importlib.import_module(f"{extension_packages.__name__}.{adapter_name}")
    factory = getattr(module, "create_adapter", None)
    if not callable(factory):
        raise TypeError(f"Identity adapter has no factory: {adapter_name}")
    instance = factory(provider_id=provider_id, settings=options)
    for method in (
        "start_authorization",
        "complete_authorization",
        "fetch_profile",
    ):
        if not inspect.iscoroutinefunction(getattr(instance, method, None)):
            raise TypeError(f"Identity adapter missing async method: {method}")
    return Provider(provider_id, name.strip(), instance, adapter_name)


class IdentityRegistry:
    """Validate channels strictly by default, or retain healthy runtime channels."""

    def __init__(self, config: dict[str, Any], *, strict: bool = True):
        if not isinstance(config, dict) or set(config) - {"providers"}:
            raise ValueError("Invalid identity extension configuration")
        providers = config.get("providers", {})
        if not isinstance(providers, dict):
            raise TypeError("Identity providers must be a mapping")
        self._providers: dict[str, Provider] = {}
        self._failed_providers: list[str] = []
        if not providers:
            return
        available = {
            item.name
            for item in pkgutil.iter_modules(extension_packages.__path__)
            if item.ispkg
        }
        for index, (provider_id, entry) in enumerate(providers.items()):
            try:
                provider = _build_provider(provider_id, entry, available)
            except Exception as exc:
                if strict:
                    raise
                safe_id = provider_log_id(provider_id)
                self._failed_providers.append(safe_id)
                # Exceptions/configuration can contain secrets. Keep only safe metadata.
                logger.error(
                    "Identity provider could not initialize; channel disabled",
                    provider_id=safe_id,
                    entry_index=index,
                    stage="provider_init",
                    error_type=type(exc).__name__,
                )
                continue
            if provider is not None:
                self._providers[provider.id] = provider

    def assert_valid(self) -> None:
        """Let strict callers reject partial results without reinitializing plugins."""
        if self._failed_providers:
            raise ValueError(
                "Identity provider initialization failed: "
                + ", ".join(self._failed_providers)
            )

    def get(self, provider_id: str) -> Provider | None:
        return self._providers.get(provider_id)

    def public_providers(self) -> list[dict[str, str]]:
        return [
            {"id": p.id, "display_name": p.display_name, "adapter": p.adapter_name}
            for p in self._providers.values()
        ]


_EMPTY_REGISTRY = IdentityRegistry({})


def clear_registry_caches() -> None:
    """Reset plugin initialization caches for isolated validation and tests."""
    _load_runtime_registry.cache_clear()
    _load_registry.cache_clear()


def get_registry() -> IdentityRegistry:
    """Apply the global gate and strictly validate the cached initialization."""
    if not settings.identity_extensions_enabled:
        return _EMPTY_REGISTRY
    registry = _load_registry()
    registry.assert_valid()
    return registry


@lru_cache(maxsize=1)
def _load_registry() -> IdentityRegistry:
    """Cache one initialization shared by strict validation and runtime lookup."""
    config_path = settings.identity_extensions_config.strip()
    if not config_path:
        return _EMPTY_REGISTRY
    path = Path(config_path).expanduser()
    with path.open(encoding="utf-8") as stream:
        loader = _UniqueLoader(stream)
        try:
            config = loader.get_single_data()
        finally:
            loader.dispose()
    return IdentityRegistry(config, strict=False)


def get_runtime_registry() -> IdentityRegistry:
    """Enforce the global gate even when enabled providers have been cached."""
    if not settings.identity_extensions_enabled:
        return _EMPTY_REGISTRY
    return _load_runtime_registry()


@lru_cache(maxsize=1)
def _load_runtime_registry() -> IdentityRegistry:
    """Keep healthy channels; only file-level failures disable all extensions."""
    try:
        return _load_registry()
    except Exception as exc:  # noqa: BLE001 -- isolate optional configuration/discovery failures
        # Plugin exceptions may include secrets; log only the exception class.
        logger.error(
            "Identity extensions could not initialize; extension login disabled; built-in OAuth unchanged",
            stage="registry_init",
            error_type=type(exc).__name__,
        )
        return _EMPTY_REGISTRY
