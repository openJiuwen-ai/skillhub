from __future__ import annotations

__all__ = [
    "FlatRetriever",
    "ProgressiveRetriever",
    "ProgressiveRetrieverConfig",
    "ProgressiveRetrieverResult",
    "RetrieverCandidate",
    "RetrieverChoice",
    "RetrieverItem",
    "RetrieverNode",
    "RetrieverTrace",
    "RetrieverTraceEvent",
]


def __getattr__(name: str):
    if name in {
        "RetrieverCandidate",
        "RetrieverChoice",
        "RetrieverItem",
        "RetrieverNode",
        "RetrieverTrace",
        "RetrieverTraceEvent",
    }:
        from models.retrieval import (
            RetrieverCandidate,
            RetrieverChoice,
            RetrieverItem,
            RetrieverNode,
            RetrieverTrace,
            RetrieverTraceEvent,
        )

        exports = {
            "RetrieverCandidate": RetrieverCandidate,
            "RetrieverChoice": RetrieverChoice,
            "RetrieverItem": RetrieverItem,
            "RetrieverNode": RetrieverNode,
            "RetrieverTrace": RetrieverTrace,
            "RetrieverTraceEvent": RetrieverTraceEvent,
        }
        return exports.get(name)
    if name == "FlatRetriever":
        from .flat import FlatRetriever

        return FlatRetriever
    if name == "ProgressiveRetriever":
        from .progressive import ProgressiveRetriever

        return ProgressiveRetriever
    if name in {"ProgressiveRetrieverConfig", "ProgressiveRetrieverResult"}:
        from .types import ProgressiveRetrieverConfig, ProgressiveRetrieverResult

        exports = {
            "ProgressiveRetrieverConfig": ProgressiveRetrieverConfig,
            "ProgressiveRetrieverResult": ProgressiveRetrieverResult,
        }
        return exports.get(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
