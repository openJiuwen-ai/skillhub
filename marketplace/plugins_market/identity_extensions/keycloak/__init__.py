"""Keycloak identity adapter, using the OpenID Connect protocol."""


def create_adapter(*, provider_id: str, settings: dict):
    from .adapter import KeycloakAdapter
    from .config import KeycloakConfig

    return KeycloakAdapter(provider_id, KeycloakConfig.model_validate(settings))
