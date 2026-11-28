from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KeycloakConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuer: str
    client_id: str = Field(min_length=1)
    client_secret_env: str = Field(min_length=1)
    redirect_uri: str
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    token_endpoint_auth_method: Literal["client_secret_basic", "client_secret_post"] = (
        "client_secret_basic"
    )
    login_claim: str = "preferred_username"
    name_claim: str = "name"
    avatar_claim: str = "picture"
    prompt: Literal["login"] | None = None
    algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    metadata_ttl_seconds: int = Field(default=300, ge=10, le=86400)
    timeout_seconds: float = Field(default=15, gt=0, le=60)
    ssl_verify: bool = Field(default=True, strict=True)
    allow_insecure_http: bool = False

    def validate_url(self, value: str) -> str:
        parts = urlsplit(value)
        schemes = {"https", "http"} if self.allow_insecure_http else {"https"}
        if parts.scheme not in schemes or not parts.hostname:
            raise ValueError("OIDC URLs must be trusted HTTPS URLs")
        if (
            parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            raise ValueError("OIDC URLs must be trusted HTTPS URLs")
        return value

    @model_validator(mode="after")
    def validate_config(self):
        self.validate_url(self.issuer)
        self.validate_url(self.redirect_uri)
        if urlsplit(self.issuer).query:
            raise ValueError("OIDC issuer must not contain a query")
        # Normalize only the configured Keycloak issuer; server issuers stay exact.
        self.issuer = self.issuer.rstrip("/")
        if "openid" not in self.scopes:
            raise ValueError("OIDC scopes must include openid")
        allowed = {
            "RS256",
            "RS384",
            "RS512",
            "PS256",
            "PS384",
            "PS512",
            "ES256",
            "ES384",
            "ES512",
        }
        if not self.algorithms or not set(self.algorithms).issubset(allowed):
            raise ValueError(
                "OIDC requires an explicit asymmetric signing algorithm allowlist"
            )
        return self
