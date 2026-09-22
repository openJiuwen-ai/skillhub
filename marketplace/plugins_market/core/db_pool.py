# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SQLAlchemy QueuePool 参数：按 uvicorn worker 数推导，并可用环境变量覆盖。"""

from __future__ import annotations

import os

# 默认按「单进程合计」封顶。K8s 多副本是每副本一份池，现网 RDS max_connections=4000，
# 单 worker 用 50+50=100；4 副本约 400，仍留余量。
# STORE_WORKERS>1 时在本进程内均分 _TOTAL_CONN_BUDGET。
_TOTAL_CONN_BUDGET = 200
_PER_PROCESS_CAP = 100
_DEFAULT_POOL_TIMEOUT = 10


def _parse_int(raw: str | None, default: int, *, minimum: int) -> int:
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    if value < minimum:
        return default
    return value


def sqlalchemy_pool_kwargs(
    *,
    workers: str | None = None,
    pool_size: str | None = None,
    max_overflow: str | None = None,
    pool_timeout: str | None = None,
) -> dict:
    """返回 create_engine 的连接池关键字参数。

    - STORE_DB_POOL_SIZE / STORE_DB_MAX_OVERFLOW 有值则用之。
    - 否则：workers = STORE_WORKERS（默认 1），
      per_process = clamp(budget/workers, 8..100)，
      pool_size = max(8, per_process//2)，overflow 补齐到 per_process。
    - STORE_DB_POOL_TIMEOUT 默认 10s（SQLAlchemy 默认 30s；0 表示不等待）。
    """
    worker_count = _parse_int(
        workers if workers is not None else os.getenv("STORE_WORKERS"),
        1,
        minimum=1,
    )
    timeout = _parse_int(
        pool_timeout if pool_timeout is not None else os.getenv("STORE_DB_POOL_TIMEOUT"),
        _DEFAULT_POOL_TIMEOUT,
        minimum=0,
    )

    per_process = max(8, min(_PER_PROCESS_CAP, _TOTAL_CONN_BUDGET // worker_count))
    default_size = max(8, per_process // 2)
    default_overflow = max(0, per_process - default_size)

    size_raw = os.getenv("STORE_DB_POOL_SIZE") if pool_size is None else pool_size
    overflow_raw = os.getenv("STORE_DB_MAX_OVERFLOW") if max_overflow is None else max_overflow

    size = _parse_int(size_raw, default_size, minimum=1)
    overflow = _parse_int(overflow_raw, default_overflow, minimum=0)

    return {
        "pool_size": size,
        "max_overflow": overflow,
        "pool_timeout": timeout,
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    }
