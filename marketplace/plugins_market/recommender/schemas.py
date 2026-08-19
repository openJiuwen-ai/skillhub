"""Recommender API request / response schemas."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints


# Cap embedding cost / payload size for POST /recommend/by_queries
_QUERY_TEXT = Annotated[str, StringConstraints(min_length=1, max_length=2000, strip_whitespace=True)]


class RecommendRequest(BaseModel):
    user_id: str = Field(
        "",
        description=(
            "Target user id. Bearer callers: omit or must equal token user. "
            "X-System-Token (trusted service): may set any user id; empty => cold-start TopK."
        ),
    )
    request_id: str = Field("", description="Caller request id (echoed)")
    timestamp: int | float | None = Field(None, description="Client timestamp (logged only)")
    top_k: int = Field(10, ge=1, le=500)
    category_id: str = Field(
        "",
        description="Optional root category id (e.g. software-development); empty = all",
    )
    plugin_type: str = Field(
        "",
        description="Optional plugin type filter: skill / swarmskill (comma-separated). Empty = all.",
    )


class RecommendItemOut(BaseModel):
    asset_id: str
    score: float


class RecommendData(BaseModel):
    request_id: str
    user_id: str
    source: str
    category_id: str = ""
    plugin_type: str = ""
    items: list[RecommendItemOut]


class ByIdsRequest(BaseModel):
    asset_ids: list[str] = Field(..., min_length=1)
    top_k: int = Field(10, ge=1, le=500)
    category_id: str = Field("", description="Optional category filter for Milvus search")
    plugin_type: str = Field("", description="Optional plugin_type filter for Milvus search")


class ByQueriesRequest(BaseModel):
    queries: list[_QUERY_TEXT] = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Query texts to embed (1–32 items, each 1–2000 chars after strip)",
    )
    top_k: int = Field(10, ge=1, le=500)
    category_id: str = Field("", description="Optional category filter for Milvus search")
    plugin_type: str = Field("", description="Optional plugin_type filter for Milvus search")


class ScoredItem(BaseModel):
    asset_id: str
    score: float


class RerankMmrRequest(BaseModel):
    items: list[ScoredItem] = Field(..., min_length=1)
    top_k: int | None = Field(None, ge=1, le=500)


class RecommendItemsData(BaseModel):
    items: list[dict[str, Any]]
