"""Shared configuration normalization for the namespace-aware Redis client.

Prefixes provide naming isolation, not Redis ACL authorization.
"""
from __future__ import annotations


def normalize_redis_key_prefix(value: str | None) -> str:
    prefix = (value or "").strip()
    return prefix + ":" if prefix and not prefix.endswith(":") else prefix
