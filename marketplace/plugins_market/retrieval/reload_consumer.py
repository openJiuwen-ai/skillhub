# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Redis Stream consumer that hot-reloads the index when a rebuild completes.

Each process instance creates its own Consumer Group on the index:reload stream,
so every instance independently receives every reload broadcast.
"""

import asyncio
import socket

from plugins_market.core.logging import get_logger

logger = get_logger(__name__)

RELOAD_STREAM = "index:reload"
_CONSUMER_GROUP_PREFIX = "reload"


def _ensure_consumer_group(redis_client, group: str) -> None:
    """Create stream + consumer group if missing (Redis restart / empty DB)."""
    try:
        redis_client.xgroup_create(RELOAD_STREAM, group, id="$", mkstream=True)
        logger.info("reload_consumer: created group=%s stream=%s", group, RELOAD_STREAM)
    except Exception as exc:
        # BUSYGROUP = already exists; anything else is unexpected but non-fatal here
        msg = str(exc)
        if "BUSYGROUP" in msg:
            return
        # Some redis builds raise ResponseError with this text for existing groups
        if "already exists" in msg.lower():
            return
        logger.warning("reload_consumer: xgroup_create failed group=%s: %s", group, exc)


def _is_nogroup_error(exc: BaseException) -> bool:
    msg = str(exc)
    return "NOGROUP" in msg or "No such key" in msg


async def run_reload_consumer(index_manager, redis_client) -> None:
    """Read index:reload stream forever and hot-reload on each message.

    Designed to run as a long-lived asyncio Task. Exits cleanly on CancelledError.
    Redis xreadgroup blocks up to 5 s per call so the loop yields regularly.
    """
    if redis_client is None:
        logger.info("reload_consumer: Redis not configured, consumer disabled")
        return

    instance_id = f"{socket.gethostname()}-{id(index_manager)}"
    group = f"{_CONSUMER_GROUP_PREFIX}-{instance_id}"
    consumer = instance_id

    _ensure_consumer_group(redis_client, group)
    logger.info("reload_consumer started group=%s", group)

    loop = asyncio.get_running_loop()

    def _xreadgroup_blocking():
        return redis_client.xreadgroup(
            group,
            consumer,
            {RELOAD_STREAM: ">"},
            count=10,
            block=5000,
        )

    while True:
        try:
            msgs = await loop.run_in_executor(None, _xreadgroup_blocking)
            if not msgs:
                continue
            for _stream, entries in msgs:
                for msg_id, fields in entries:
                    await loop.run_in_executor(None, lambda f=fields: _apply_reload(index_manager, f))
                    try:
                        redis_client.xack(RELOAD_STREAM, group, msg_id)
                    except Exception as exc:
                        logger.warning("xack failed msg_id=%s: %s", msg_id, exc)
        except asyncio.CancelledError:
            logger.info("reload_consumer cancelled, stopping")
            break
        except Exception as exc:
            if _is_nogroup_error(exc):
                logger.warning(
                    "reload_consumer: stream/group missing, recreating group=%s (%s)",
                    group,
                    exc,
                )
                await loop.run_in_executor(None, lambda: _ensure_consumer_group(redis_client, group))
            else:
                logger.error("reload_consumer error: %s", exc)
            await asyncio.sleep(5)


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value or "")


def _apply_reload(index_manager, fields: dict) -> None:
    # keys may be bytes (decode_responses=False) or str — try both
    group = _decode(fields.get(b"group") or fields.get("group") or "")
    index_path = _decode(fields.get(b"index_path") or fields.get("index_path") or "")
    if not group or not index_path:
        logger.warning("reload_consumer: invalid message fields=%s", fields)
        return
    logger.info("reload_consumer: hot-reload group=%s path=%s", group, index_path)
    index_manager.load(group, index_path)
