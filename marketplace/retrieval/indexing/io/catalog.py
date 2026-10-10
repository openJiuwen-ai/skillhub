# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from indexing.catalog.records import CatalogRecord
from shared.limits import MAX_CATALOG_BYTES, read_text_file


def load_catalog_records(path: Path) -> List[CatalogRecord]:
    if not path.exists():
        return []
    records: List[CatalogRecord] = []
    for line in read_text_file(path, max_bytes=MAX_CATALOG_BYTES, label="catalog").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        records.append(
            CatalogRecord(
                skill_id=str(payload.get("skill_id") or ""),
                worker_id=str(payload.get("worker_id") or ""),
                cid=str(payload.get("cid") or ""),
                name=str(payload.get("name") or ""),
                description=str(payload.get("description") or ""),
                skill_path=str(payload.get("skill_path") or ""),
                branch_path=tuple(str(item) for item in payload.get("branch_path") or ()),
                category=str(payload.get("category") or ""),
                retrieval_text=str(payload.get("retrieval_text") or ""),
                metadata=dict(payload.get("metadata") or {}),
                tags=tuple(str(item).strip() for item in payload.get("tags") or () if str(item).strip()),
            )
        )
    return records


__all__ = ["load_catalog_records"]
