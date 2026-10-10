"""Recommendation result types."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RecommendItem:
    asset_id: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
