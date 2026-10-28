"""One Milvus embedding job at a time across marketplace pods."""

from __future__ import annotations

import logging
import threading
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
_RENEW_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
end
return 0
"""


class MilvusLockError(RuntimeError):
    """Redis could not grant or check the Milvus index lock."""


@dataclass
class MilvusLockHandle:
    proceed: bool
    redis: Any | None = None


def _renew_until_stopped(client: Any, token: str, stop: threading.Event) -> None:
    interval = max(1.0, LOCK_TTL_SECONDS / 3)
    while not stop.wait(interval):
        try:
            renewed = client.eval(_RENEW_SCRIPT, 1, LOCK_KEY, token, str(LOCK_TTL_SECONDS))
        except Exception as exc:
            logger.warning("milvus index lock renew failed: %s", exc)
            continue
        if not renewed:
            logger.warning("milvus index lock lost; another run may overlap")
            return


@contextmanager
def milvus_index_lock(app_cfg: Any, *, skip: bool = False) -> Iterator[MilvusLockHandle]:
    """Yield proceed=True only for the pod that holds the lock.

    Another holder yields proceed=False. Redis errors raise MilvusLockError.
    The holder renews the TTL until the context exits.
    """
    if skip:
        yield MilvusLockHandle(proceed=True, redis=None)
        return

    token = str(uuid.uuid4())
    try:
        client = create_redis_client(app_cfg.redis)
        acquired = bool(client.set(LOCK_KEY, token, nx=True, ex=LOCK_TTL_SECONDS))
    except Exception as exc:
        logger.error("milvus index lock unavailable: %s", exc)
        raise MilvusLockError(str(exc)) from exc
    if not acquired:
        logger.info("milvus index: another instance holds the lock, skipping this run")
        yield MilvusLockHandle(proceed=False, redis=client)
        return

    stop = threading.Event()
    renewer = threading.Thread(
        target=_renew_until_stopped,
        args=(client, token, stop),
        name="milvus-index-lock-renew",
        daemon=True,
    )
    renewer.start()
    try:
        yield MilvusLockHandle(proceed=True, redis=client)
    finally:
        stop.set()
        renewer.join(timeout=1)
        try:
            client.eval(_RELEASE_SCRIPT, 1, LOCK_KEY, token)
        except Exception as exc:
            logger.warning("failed to release milvus index lock: %s", exc)
