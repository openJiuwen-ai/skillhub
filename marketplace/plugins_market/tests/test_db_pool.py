# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from plugins_market.core.db_pool import sqlalchemy_pool_kwargs


def test_single_worker_default_is_50_plus_50_timeout_10():
    kwargs = sqlalchemy_pool_kwargs(
        workers="1",
        pool_size="",
        max_overflow="",
        pool_timeout="",
    )
    assert kwargs["pool_size"] == 50
    assert kwargs["max_overflow"] == 50
    assert kwargs["pool_timeout"] == 10
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 3600


def test_four_workers_share_budget():
    kwargs = sqlalchemy_pool_kwargs(
        workers="4",
        pool_size="",
        max_overflow="",
        pool_timeout="",
    )
    assert kwargs["pool_size"] + kwargs["max_overflow"] == 50


def test_explicit_overrides_and_timeout_zero_allowed():
    kwargs = sqlalchemy_pool_kwargs(
        workers="1",
        pool_size="12",
        max_overflow="8",
        pool_timeout="0",
    )
    assert kwargs["pool_size"] == 12
    assert kwargs["max_overflow"] == 8
    assert kwargs["pool_timeout"] == 0


def test_invalid_env_falls_back():
    kwargs = sqlalchemy_pool_kwargs(
        workers="nope",
        pool_size="-3",
        max_overflow="x",
        pool_timeout="-1",
    )
    assert kwargs["pool_size"] == 50
    assert kwargs["max_overflow"] == 50
    assert kwargs["pool_timeout"] == 10
