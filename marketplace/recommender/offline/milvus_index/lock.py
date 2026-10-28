"""One Milvus embedding job at a time across marketplace pods."""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from recommender.offline.redis_sync.client import create_redis_client

logger = logging.getLogger(__name__)

LOCK_KEY = "recommender:milvus:index:lock"
LOCK_TTL_SECONDS = 2400
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


@dataclass
class MilvusLockHandle:
    proceed: bool
    redis: Any | None = None


@contextmanager
def milvus_index_lock(app_cfg: Any, *, skip: bool = False) -> Iterator[MilvusLockHandle]:
    """Yield proceed=True only for the pod that holds the lock.

    Redis errors skip the run. A stale holder expires after LOCK_TTL_SECONDS.
    """
    if skip:
        yield MilvusLockHandle(proceed=True, redis=None)
        return

    client = None
    token = str(uuid.uuid4())
    acquired = False
    try:
        client = create_redis_client(app_cfg.redis)
        acquired = bool(client.set(LOCK_KEY, token, nx=True, ex=LOCK_TTL_SECONDS))
    except Exception as exc:
        logger.error("milvus index lock unavailable, skip this run: %s", exc)
        yield MilvusLockHandle(proceed=False, redis=None)
        return
    if not acquired:
        logger.info("milvus index: another instance holds the lock, skipping this run")
        yield MilvusLockHandle(proceed=False, redis=client)
        return
    try:
        yield MilvusLockHandle(proceed=True, redis=client)
    finally:
        try:
            client.eval(_RELEASE_SCRIPT, 1, LOCK_KEY, token)
        except Exception as exc:
            logger.warning("failed to release milvus index lock: %s", exc)
