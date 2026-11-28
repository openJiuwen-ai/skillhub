# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("STORE_DB_URL", "mysql+pymysql://test:test@127.0.0.1:3306/test")

from common.redis_client import RedisClient
from plugins_market.retrieval import reload_consumer as reload


def _run_consumer_once() -> MagicMock:
    client = MagicMock()
    client.xreadgroup.side_effect = asyncio.CancelledError()
    asyncio.run(reload.run_reload_consumer(object(), client))
    return client


def test_group_destroyed_on_graceful_shutdown() -> None:
    client = _run_consumer_once()
    group = client.xgroup_create.call_args.args[1]
    assert group.startswith(reload._CONSUMER_GROUP_PREFIX + "-")
    client.xgroup_destroy.assert_called_once_with(reload.RELOAD_STREAM, group)


def test_group_names_differ_across_restarts() -> None:
    first = _run_consumer_once().xgroup_create.call_args.args[1]
    second = _run_consumer_once().xgroup_create.call_args.args[1]
    assert first.startswith(reload._CONSUMER_GROUP_PREFIX + "-")
    assert second.startswith(reload._CONSUMER_GROUP_PREFIX + "-")
    assert first != second


def test_destroy_failure_does_not_break_shutdown() -> None:
    client = MagicMock()
    client.xreadgroup.side_effect = asyncio.CancelledError()
    client.xgroup_destroy.side_effect = RuntimeError("boom")
    asyncio.run(reload.run_reload_consumer(object(), client))


def test_facade_namespaces_destroy_stream() -> None:
    raw = MagicMock()
    RedisClient(raw, "app:").xgroup_destroy("index:reload", "g1")
    raw.xgroup_destroy.assert_called_once_with("app:index:reload", "g1")


@pytest.mark.parametrize("prefix", ["", "app:", "app::"])
def test_consumer_group_namespaced_via_facade(monkeypatch, prefix) -> None:
    raw = MagicMock()
    raw.xreadgroup.side_effect = asyncio.CancelledError()
    asyncio.run(reload.run_reload_consumer(object(), RedisClient(raw, prefix)))
    group = raw.xgroup_create.call_args.args[1]
    assert raw.xgroup_create.call_args.args[0] == prefix + reload.RELOAD_STREAM
    raw.xgroup_destroy.assert_called_once_with(prefix + reload.RELOAD_STREAM, group)
