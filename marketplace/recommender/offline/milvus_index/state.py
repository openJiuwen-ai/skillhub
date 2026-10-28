"""Persist which assets are indexed in Milvus (version + artifact fingerprint)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recommender.shared.config import DATA_ROOT

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = DATA_ROOT / "milvus_index_state.json"
MILVUS_INDEX_STATE_KEY = "recommender:milvus:index_state"


@dataclass
class IndexedAsset:
    version: str
    artifact_sha256: str | None
    indexed_at: str
    category_id: str = ""
    plugin_type: str = ""


@dataclass
class IndexState:
    updated_at: str
    assets: dict[str, IndexedAsset]

    @classmethod
    def empty(cls) -> IndexState:
        return cls(updated_at="", assets={})

    def get(self, asset_id: str) -> IndexedAsset | None:
        return self.assets.get(asset_id)


def _state_from_payload(payload: dict) -> IndexState:
    assets: dict[str, IndexedAsset] = {}
    for asset_id, row in (payload.get("assets") or {}).items():
        assets[str(asset_id)] = IndexedAsset(
            version=str(row.get("version") or ""),
            artifact_sha256=row.get("artifact_sha256"),
            indexed_at=str(row.get("indexed_at") or ""),
            category_id=str(row.get("category_id") or "").strip(),
            plugin_type=str(row.get("plugin_type") or "").strip(),
        )
    return IndexState(updated_at=str(payload.get("updated_at") or ""), assets=assets)


def load_state(path: Path = DEFAULT_STATE_PATH) -> IndexState:
    if not path.exists():
        return IndexState.empty()
    return _state_from_payload(json.loads(path.read_text(encoding="utf-8")))


def _load_redis_state(redis_client: Any) -> IndexState | None:
    raw = redis_client.get(MILVUS_INDEX_STATE_KEY)
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return _state_from_payload(json.loads(raw))


def load_state_shared(path: Path = DEFAULT_STATE_PATH, redis_client: Any | None = None) -> IndexState:
    """Prefer the newer of the local file and the shared Redis copy."""
    local = load_state(path)
    if redis_client is None:
        return local
    try:
        remote = _load_redis_state(redis_client)
    except Exception as exc:
        logger.warning("load milvus index state from redis failed: %s", exc)
        return local
    if remote is None:
        return local
    if not local.assets:
        return remote
    if (remote.updated_at or "") >= (local.updated_at or ""):
        return remote
    return local


def merge_indexed_assets(
    active_ids: list[str],
    previous: IndexState,
    newly_indexed: dict[str, IndexedAsset],
    failed_ids: list[str],
) -> dict[str, IndexedAsset]:
    """Keep prior successes, add this run's commits, drop confirmed package failures."""
    failed = set(failed_ids)
    merged: dict[str, IndexedAsset] = {}
    for asset_id in active_ids:
        if asset_id in newly_indexed:
            merged[asset_id] = newly_indexed[asset_id]
        elif asset_id in failed:
            continue
        else:
            kept = previous.get(asset_id)
            if kept is not None:
                merged[asset_id] = kept
    return merged


def save_state(state: IndexState, path: Path = DEFAULT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": state.updated_at or datetime.now(timezone.utc).isoformat(),
        "assets": {
            asset_id: asdict(row) for asset_id, row in sorted(state.assets.items())
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def replace_state(active: dict[str, IndexedAsset], path: Path = DEFAULT_STATE_PATH) -> IndexState:
    state = IndexState(
        updated_at=datetime.now(timezone.utc).isoformat(),
        assets=dict(active),
    )
    save_state(state, path)
    return state


def save_state_shared(
    active: dict[str, IndexedAsset],
    path: Path = DEFAULT_STATE_PATH,
    redis_client: Any | None = None,
) -> IndexState:
    state = replace_state(active, path)
    if redis_client is None:
        return state
    try:
        redis_client.set(MILVUS_INDEX_STATE_KEY, path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("save milvus index state to redis failed: %s", exc)
    return state
