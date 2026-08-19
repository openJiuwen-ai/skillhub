"""Persist which assets are indexed in Milvus (version + artifact fingerprint)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from recommender.shared.config import DATA_ROOT

DEFAULT_STATE_PATH = DATA_ROOT / "milvus_index_state.json"


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


def load_state(path: Path = DEFAULT_STATE_PATH) -> IndexState:
    if not path.exists():
        return IndexState.empty()
    payload = json.loads(path.read_text(encoding="utf-8"))
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
