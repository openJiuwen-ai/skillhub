"""Generic Redis writers shared by all redis_sync tasks."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def write_json_snapshot(
    client,
    *,
    key: str,
    payload: dict[str, Any] | list[Any],
    ttl_seconds: int,
) -> str:
    """
    Atomically overwrite one STRING key with a JSON snapshot + TTL.

    New Redis jobs should reuse this for whole-key replace semantics.
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    client.set(key, body, ex=max(1, ttl_seconds))
    logger.info(
        "Redis SET %s (ttl=%ss, bytes=%s)",
        key,
        ttl_seconds,
        len(body.encode("utf-8")),
    )
    return body


def write_json_snapshots_pipeline(
    client,
    *,
    items: dict[str, dict[str, Any] | list[Any]],
    ttl_seconds: int,
) -> int:
    """Pipeline SET many JSON STRING keys with the same TTL. Returns written count."""
    if not items:
        return 0
    ttl = max(1, ttl_seconds)
    pipe = client.pipeline(transaction=False)
    for key, payload in items.items():
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        pipe.set(key, body, ex=ttl)
    pipe.execute()
    logger.info("Redis pipeline SET %s keys (ttl=%ss)", len(items), ttl)
    return len(items)
