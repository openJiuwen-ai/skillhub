# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-local TTL cache for public POST /recommend cards.

Stores max(MARKET_REC_PLAZA_CACHE_TOP_K, MARKET_REC_LIST_TOP_K) cards.
Smaller requests slice the prefix; larger pages skip this cache.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from concurrent.futures import Future
from typing import Any, TypeVar

_TTL_S = 20.0
_MAX_KEYS = 64
_SF_TIMEOUT_S = 35.0
_lock = threading.Lock()
_store: dict[tuple[str, str], tuple[float, str, list[dict[str, Any]]]] = {}
_sf_lock = threading.Lock()
_sf: dict[Hashable, Future] = {}

T = TypeVar("T")
PlazaKey = tuple[str, str]


def plaza_cache_top_k() -> int:
    """Page size that still hits this cache.

    Home requests use MARKET_REC_LIST_TOP_K (often 50). A smaller plaza
    setting must not force those requests to skip the cache.
    """
    from plugins_market.core.config import settings

    return max(int(settings.rec_plaza_cache_top_k), int(settings.rec_list_top_k))


def cache_key(plugin_type: str, category_id: str) -> PlazaKey:
    return ((plugin_type or "").strip(), (category_id or "").strip())


def get(key: PlazaKey) -> tuple[str, list[dict[str, Any]]] | None:
    now = time.monotonic()
    with _lock:
        row = _store.get(key)
        if row is None or row[0] <= now:
            if row is not None:
                _store.pop(key, None)
            return None
        return row[1], row[2]


def take(key: PlazaKey, top_k: int) -> tuple[str, list[dict[str, Any]]] | None:
    """Return a prefix if this request fits in the plaza page."""
    need = int(top_k)
    if need > plaza_cache_top_k():
        return None
    hit = get(key)
    if hit is None:
        return None
    source, items = hit
    return source, items[:need]


def put(key: PlazaKey, source: str, items: list[dict[str, Any]]) -> bool:
    """Store a non-empty plaza page (capped). Empty pages are skipped."""
    if not items:
        return False
    page = items[: plaza_cache_top_k()]
    expires = time.monotonic() + _TTL_S
    with _lock:
        if key not in _store and len(_store) >= _MAX_KEYS:
            oldest = next(iter(_store), None)
            if oldest is not None:
                _store.pop(oldest, None)
        _store[key] = (expires, source, page)
    return True


def singleflight(key: Hashable, fn: Callable[[], T]) -> T:
    """Run fn once per key; concurrent waiters share the same result or exception."""
    with _sf_lock:
        existing = _sf.get(key)
        if existing is None:
            fut: Future = Future()
            _sf[key] = fut
            leader = True
        else:
            fut = existing
            leader = False
    if not leader:
        return fut.result(timeout=_SF_TIMEOUT_S)
    try:
        result = fn()
    except Exception as exc:
        fut.set_exception(exc)
        raise
    else:
        fut.set_result(result)
        return result
    finally:
        with _sf_lock:
            if _sf.get(key) is fut:
                _sf.pop(key, None)


def reset() -> None:
    with _lock:
        _store.clear()
    with _sf_lock:
        _sf.clear()
