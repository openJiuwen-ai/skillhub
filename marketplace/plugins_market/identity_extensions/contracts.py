"""Contract implemented by each identity extension package."""

from dataclasses import dataclass
from typing import Any, Protocol


class IdentityError(Exception):
    """Safe public error: never include tokens or upstream response bodies."""


@dataclass(frozen=True)
class AuthorizationStart:
    url: str
    transaction: dict[str, Any]


@dataclass(frozen=True)
class LoginResult:
    access_token: str
    user: dict[str, Any]
    token_type: str = "bearer"


class IdentityAdapter(Protocol):
    async def start_authorization(self, *, state: str) -> AuthorizationStart:
        ...

    async def complete_authorization(
        self, *, callback_params: dict[str, str], transaction: dict[str, Any]
    ) -> LoginResult:
        ...

    async def fetch_profile(self, access_token: str) -> dict[str, Any] | None:
        ...


def validate_profile(profile: dict[str, Any], *, system_user: str) -> dict[str, Any]:
    """Preserve existing ID semantics while respecting existing DB column sizes."""
    if not isinstance(profile, dict):
        raise IdentityError("Invalid identity profile")
    for field, limit in (("id", 64), ("login", 128), ("name", 128)):
        value = profile.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise IdentityError("Invalid identity profile")
    if profile["id"].strip() == system_user.strip():
        raise IdentityError("Identity conflicts with reserved system user")
    avatar = profile.get("avatar_url")
    if avatar is not None and not isinstance(avatar, str):
        raise IdentityError("Invalid identity avatar")
    return {key: profile.get(key) for key in ("id", "login", "name", "avatar_url")}
