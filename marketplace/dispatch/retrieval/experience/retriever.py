from __future__ import annotations

import logging
from .bank import ExperienceBank

LOGGER = logging.getLogger(__name__)


class ExperienceRetriever:
    """Fast-path retriever that queries the experience knowledge base.

    On hit: returns skill_ids from the matched experience item.
    On miss: returns None so the caller falls through to tree search.
    """

    def __init__(
        self,
        kb: ExperienceBank,
        *,
        threshold: float = 0.80,
        top_k: int = 1,
    ) -> None:
        self._kb = kb
        self._threshold = threshold
        self._top_k = top_k

    def search(self, query: str) -> list[str] | None:
        """Search the experience KB. Returns skill list on hit, None on miss."""
        results = self._kb.search_by_embedding(
            query, top_k=self._top_k, threshold=self._threshold
        )
        if not results:
            return None

        best_sim, best_item = results[0]
        LOGGER.info(
            "Experience hit: query='%s' pattern='%s' sim=%.3f skills=%s",
            query,
            best_item.query_pattern,
            best_sim,
            best_item.skill_ids,
        )
        return list(best_item.skill_ids)

    def search_details(self, query: str) -> dict | None:
        """Like search() but returns the full match detail for debugging."""
        results = self._kb.search_by_embedding(
            query, top_k=self._top_k, threshold=self._threshold
        )
        if not results:
            return None

        best_sim, best_item = results[0]
        return {
            "method": "experience",
            "skill_ids": list(best_item.skill_ids),
            "matched_pattern": best_item.query_pattern,
            "similarity": best_sim,
            "query_examples": best_item.query_examples,
            "success_count": best_item.success_count,
        }


__all__ = ["ExperienceRetriever"]
