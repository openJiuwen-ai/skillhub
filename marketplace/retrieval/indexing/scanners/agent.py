# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from shared.limits import MAX_JSON_ARTIFACT_BYTES, read_text_file

from .base import ScannedItem, console
from .plugin import PluginScanner


class AgentAssetScanner(PluginScanner):
    """Scan wrapped Agent assets while enriching them with market metadata."""

    item_type = "agent"

    def __init__(self, items_dir: Path | str, *, display_items_dir: Path | str | None = None) -> None:
        super().__init__(items_dir, display_items_dir=display_items_dir)
        self._metadata: dict[str, dict[str, object]] = {}
        self._load_metadata()

    def _load_metadata(self) -> None:
        metadata_path = self.items_dir / "skills.json"
        if not metadata_path.exists():
            return
        try:
            payload = json.loads(
                read_text_file(
                    metadata_path,
                    max_bytes=MAX_JSON_ARTIFACT_BYTES,
                    label="agent asset metadata",
                )
            )
        except Exception as exc:
            console.print(f"[yellow]Warning: Failed to load skills.json: {exc}[/yellow]")
            return
        if not isinstance(payload, dict):
            return
        for raw_item in payload.get("skills", []):
            if not isinstance(raw_item, dict):
                continue
            item_id = str(raw_item.get("id") or "").strip()
            if item_id:
                self._metadata[item_id] = raw_item

    def scan_item_dir(self, item_dir: Path) -> ScannedItem | None:
        item = super().scan_item_dir(item_dir)
        if item is None:
            return None
        meta = self._metadata.get(item.id, {})
        raw_tags = meta.get("tags")
        market_short_desc = str(meta.get("short_desc") or "").strip()
        market_detail_desc = str(meta.get("detail_desc") or "").strip()
        tags = (
            [str(tag).strip() for tag in raw_tags if str(tag).strip()]
            if isinstance(raw_tags, list)
            else item.tags
        )
        return replace(
            item,
            description="\n".join(
                part for part in (market_short_desc, market_detail_desc, item.description) if part
            ),
            plugin_display_name=item.name,
            market_display_name=str(meta.get("display_name") or "").strip(),
            market_short_desc=market_short_desc,
            market_detail_desc=market_detail_desc,
            additional_retrieval_text="\n".join(
                part for part in (market_detail_desc, item.description) if part
            ),
            tags=tags,
        )
