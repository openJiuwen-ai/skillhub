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


class IdentityRegistry:
    def __init__(self, config: dict[str, Any]):
        if not isinstance(config, dict) or set(config) - {"providers"}:
            raise ValueError("Invalid identity extension configuration")
        providers = config.get("providers", {})
        if not isinstance(providers, dict):
            raise TypeError("Identity providers must be a mapping")
        available = {
            item.name
            for item in pkgutil.iter_modules(extension_packages.__path__)
            if item.ispkg
        }
        self._providers: dict[str, Provider] = {}
        for provider_id, entry in providers.items():
            if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(
                provider_id
            ):
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
                raise ValueError(
                    f"Invalid identity provider configuration: {provider_id}"
                )
            enabled = entry.get("enabled", False)
            if not isinstance(enabled, bool):
                raise TypeError("Identity enabled must be a boolean")
            if "enabled_env" in entry:
                env_name = entry["enabled_env"]
                if not isinstance(env_name, str) or not _ENV_VAR_NAME.fullmatch(
                    env_name
                ):
                    raise ValueError(f"Invalid identity enabled_env: {provider_id}")
                enabled = os.getenv(env_name, "").strip().lower() == "true"
            if not enabled:
                continue
            adapter_name = entry.get("adapter")
            if not isinstance(adapter_name, str) or adapter_name not in available:
                raise ValueError(f"Unknown identity adapter for {provider_id}")
            name = entry.get("display_name", provider_id)
            options = entry.get("settings", {})
            if (
                not isinstance(name, str)
                or not name.strip()
                or len(name) > 128
                or not isinstance(options, dict)
            ):
                raise ValueError(
                    f"Invalid identity provider configuration: {provider_id}"
                )
            module = importlib.import_module(
                f"{extension_packages.__name__}.{adapter_name}"
            )
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
            self._providers[provider_id] = Provider(
                provider_id, name.strip(), instance, adapter_name
            )

    def get(self, provider_id: str) -> Provider | None:
        return self._providers.get(provider_id)

    def public_providers(self) -> list[dict[str, str]]:
        return [
            {"id": p.id, "display_name": p.display_name, "adapter": p.adapter_name}
            for p in self._providers.values()
        ]


@lru_cache(maxsize=1)
def get_registry() -> IdentityRegistry:
    config_path = settings.identity_extensions_config.strip()
    if not config_path:
        return IdentityRegistry({})
    path = Path(config_path).expanduser()
    with path.open(encoding="utf-8") as stream:
        config = yaml.load(stream, Loader=_UniqueLoader)
    return IdentityRegistry(config)


@lru_cache(maxsize=1)
def get_runtime_registry() -> IdentityRegistry:
    """Isolate optional plugin/configuration failures from built-in authentication.

    get_registry() remains the strict loader for configuration validation. Cache
    the runtime outcome until restart, including failures, to avoid repeatedly
    importing a broken plugin or reading a broken file on public requests.
    """
    try:
        return get_registry()
    except Exception as exc:  # noqa: BLE001 -- isolate arbitrary optional plugin factory failures
        # Plugin exceptions may include secrets; log only the exception class.
        logger.error(
            "Identity extensions could not initialize; extension login disabled; built-in OAuth unchanged",
            error_type=type(exc).__name__,
        )
        return IdentityRegistry({})
