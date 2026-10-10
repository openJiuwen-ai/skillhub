from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List

from ..service.retriever import Retriever
from ..service.models import SearchResult
from .collector import SkillKnowledgeBuilder
from .embed import EmbeddingClient
from .bank import ExperienceBank
from .retriever import ExperienceRetriever

LOGGER = logging.getLogger(__name__)


class ExperienceAwareRetriever:
    """Non-invasive wrapper around the existing Retriever.

    Usage is identical to Retriever:
        retriever = ExperienceAwareRetriever.from_index(
            index_dir="output_index",
            llm_openai_client=openai_client,
            embedding_client=EmbeddingClient(base_url="...", api_key="...", model="text-embedding-3-small"),
            kb_dir="experience_kb",
        )
        results = retriever.search("帮我画一个甘特图")
    """

    def __init__(
        self,
        wrapped: Retriever,
        experience_retriever: ExperienceRetriever,
        experience_collector: SkillKnowledgeBuilder,
    ) -> None:
        self._wrapped = wrapped
        self._exp_retriever = experience_retriever
        self._collector = experience_collector

    # ------------------------------------------------------------------
    # Drop-in replacements for Retriever public API
    # ------------------------------------------------------------------

    def search(self, query: str, *, search_config=None) -> List[str]:
        """Fast-path experience KB → fallback tree search."""
        # 1. Try experience KB
        exp_skills = self._exp_retriever.search(query)
        if exp_skills:
            LOGGER.info("ExperienceAwareRetriever: experience hit for query='%s'", query)
            return list(exp_skills)

        # 2. Fall through to tree search
        result = self._wrapped.search(query, search_config=search_config)

        return result

    def search_details(self, query, *, search_config=None) -> SearchResult:
        """Like search() but returns full details."""
        # 1. Try experience KB
        exp_detail = self._exp_retriever.search_details(query)
        if exp_detail:
            LOGGER.info("ExperienceAwareRetriever: experience hit for query='%s'", query)
            return _make_result_from_experience(query, exp_detail)

        # 2. Fall through to tree search
        result = self._wrapped.search_details(query, search_config=search_config)

        return result

    def close(self) -> None:
        """Flush pending experience records and close the wrapped retriever."""
        try:
            self._collector.flush()
        except Exception:
            LOGGER.debug("ExperienceAwareRetriever: flush failed on close", exc_info=True)
        self._wrapped.close()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_index(
        cls,
        index_dir: str | Path,
        *,
        embedding_client: EmbeddingClient,
        kb_dir: str | Path = "experience_kb",
        vector_algorithm: str = "IndexFlatIP",
        llm_openai_client: Any | None = None,
        llm_model: str = "",
        experience_threshold: float = 0.80,
        experience_top_k: int = 1,
        min_hits_for_pattern: int = 2,
        pending_flush_threshold: int = 20,
        **retriever_kwargs: Any,
    ) -> ExperienceAwareRetriever:
        """Build an ExperienceAwareRetriever from an index directory.

        Args:
            index_dir: Path to the retriever index directory
            embedding_client: EmbeddingClient for generating/searching embeddings
            kb_dir: Directory for the experience knowledge base (metadata + FAISS index + embeddings)
            vector_algorithm: FAISS index type, e.g. "IndexFlatIP" (inner product) or "IndexFlatL2" (L2)
            llm_openai_client: OpenAI-compatible client for the tree retriever
            llm_model: Model name for the tree retriever
            experience_threshold: Cosine similarity threshold for experience matching
            experience_top_k: Number of top experience results to consider
            min_hits_for_pattern: Minimum records to form a pattern before clustering
            pending_flush_threshold: Auto-flush pending records when this many accumulate
            **retriever_kwargs: Additional args forwarded to Retriever.from_index()
        """
        # Build the underlying tree retriever (unchanged)
        wrapped = Retriever.from_index(
            index_dir=index_dir,
            llm_openai_client=llm_openai_client,
            llm_model=llm_model,
            **retriever_kwargs,
        )

        # Build the experience KB
        kb = ExperienceBank(
            index_dir=kb_dir,
            embedding_client=embedding_client,
            vector_algorithm=vector_algorithm,
        )

        # Build the experience retriever (fast path)
        exp_retriever = ExperienceRetriever(
            kb=kb,
            threshold=experience_threshold,
            top_k=experience_top_k,
        )

        # Build the experience collector (slow path feedback)
        # Reuse the same LLM client for pattern extraction if available
        llm_for_patterns = None
        if llm_openai_client is not None and llm_model:
            from ..llm import coerce_generation_client
            llm_for_patterns = coerce_generation_client(llm_openai_client)

        builder = SkillKnowledgeBuilder(
            kb=kb,
            embedding_client=embedding_client,
            llm_client=llm_for_patterns,
            llm_model=llm_model,
            min_hits_for_pattern=min_hits_for_pattern,
            pending_flush_threshold=pending_flush_threshold,
        )

        return cls(
            wrapped=wrapped,
            experience_retriever=exp_retriever,
            experience_collector=builder,
        )


def _make_result_from_experience(query: str, detail: dict) -> SearchResult:
    """Build a SearchResult from an experience match."""
    skill_ids = detail["skill_ids"]
    return SearchResult(
        method="experience",
        payloads=list(skill_ids),
        candidate_records=[
            {
                "rank": i + 1,
                "raw_output": sid,
                "resolved_payload": sid,
                "valid": True,
                "selected": i == 0,
                "choice_id": sid,
                "description": f"Matched from experience: {detail['matched_pattern']}",
                "score": detail["similarity"],
                "source": "experience",
            }
            for i, sid in enumerate(skill_ids)
        ],
        summary_lines=[
            f"Experience match: pattern='{detail['matched_pattern']}' "
            f"(similarity={detail['similarity']:.3f})"
        ],
        selected_payload=skill_ids[0] if skill_ids else None,
        selected_rank=1 if skill_ids else -1,
        elapsed_ms=0.0,
        trace_events=[],
    )


__all__ = ["ExperienceAwareRetriever"]
