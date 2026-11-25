"""Redis application namespace. Call once at the storage boundary, with logical names.

Only key/channel names are transformed: never values, hash fields, members,
stream IDs, consumer groups or Lua ARGV. Publishers and subscribers must use
redis_channel for channels. Pattern subscriptions must escape namespace glob
metacharacters before appending their logical pattern. Prefixes are naming
isolation, not Redis ACLs.
"""
from __future__ import annotations


def normalize_redis_key_prefix(value: str | None) -> str:
    prefix = (value or "").strip()
    return prefix + ":" if prefix and not prefix.endswith(":") else prefix


def redis_key(name: str, prefix: str = "") -> str:
    """Prepend the namespace once; the caller must supply an unprefixed name."""
    return normalize_redis_key_prefix(prefix) + name


def redis_channel(name: str, prefix: str = "") -> str:
    """Name a Pub/Sub channel in the same namespace as application keys."""
    return redis_key(name, prefix)
