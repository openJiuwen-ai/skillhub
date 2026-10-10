"""Embedding helpers for skill texts (OpenAI-compatible API)."""

from __future__ import annotations

import logging
from typing import Any, Sequence

import numpy as np

from recommender.shared.config import _env

logger = logging.getLogger(__name__)

_client: Any | None = None
_batch_size: int = 16


def _resolve_api_key() -> str:
    try:
        from common.security.security_utils import SecurityUtils

        key = SecurityUtils.get_decrypt_secret("MARKET_REC_EMBEDDING_API_KEY", default="") or ""
        if key.strip():
            return key.strip()
    except Exception as exc:
        # Offline CLI may run without full marketplace security bootstrap; fall back to plain env.
        logger.warning(
            "decrypt MARKET_REC_EMBEDDING_API_KEY failed (%s); fallback to env plaintext",
            exc,
        )
    return _env(
        "MARKET_REC_EMBEDDING_API_KEY",
        "REC_EMBEDDING_API_KEY",
        default="",
    )


def _resolve_embedding_settings() -> tuple[str, str, str, int]:
    base_url = _env(
        "MARKET_REC_EMBEDDING_API_BASE_URL",
        "REC_EMBEDDING_API_BASE_URL",
        default="",
    )
    model = _env(
        "MARKET_REC_EMBEDDING_MODEL",
        "REC_EMBEDDING_MODEL",
        default="",
    )
    batch_size = int(
        _env(
            "MARKET_REC_EMBEDDING_BATCH_SIZE",
            "REC_EMBEDDING_BATCH_SIZE",
            default="16",
        )
        or "16"
    )
    api_key = _resolve_api_key()
    if not base_url or not model:
        raise RuntimeError(
            "Recommender embedding requires MARKET_REC_EMBEDDING_API_BASE_URL "
            "and MARKET_REC_EMBEDDING_MODEL (OpenAI-compatible embeddings API)."
        )
    return base_url, api_key or "dummy", model, max(1, batch_size)


def _create_openai_embedding_client(*, base_url: str, api_key: str, model: str) -> Any:
    try:
        from indexing.embedding import create_openai_embedding_client

        return create_openai_embedding_client(base_url=base_url, api_key=api_key, model=model)
    except ImportError as exc:
        logger.debug(
            "indexing.embedding unavailable (%s); use local OpenAI embeddings client",
            exc,
        )

    from openai import OpenAI

    class _OpenAIEmbeddingClient:
        """Minimal mirror of retrieval indexing.embedding.OpenAIEmbeddingClient."""

        def __init__(self, *, client: Any, model: str) -> None:
            self._client = client
            self.model = str(model)

        def embed_texts(self, texts: Sequence[str], *, batch_size: int = 64) -> list[list[float]]:
            vectors: list[list[float]] = []
            chunk_size = max(1, int(batch_size))
            for start in range(0, len(texts), chunk_size):
                chunk = [str(text) for text in texts[start:start + chunk_size]]
                vectors.extend(self._embed_chunk_with_backoff(chunk))
            return vectors

        def _embed_chunk_with_backoff(self, chunk: Sequence[str]) -> list[list[float]]:
            if not chunk:
                return []
            try:
                response = self._client.embeddings.create(model=self.model, input=list(chunk))
            except Exception as exc:
                message = str(exc).lower()
                if len(chunk) > 1 and ("batch size" in message or "should not be larger than" in message):
                    midpoint = max(1, len(chunk) // 2)
                    return self._embed_chunk_with_backoff(chunk[:midpoint]) + self._embed_chunk_with_backoff(
                        chunk[midpoint:]
                    )
                raise
            return [[float(value) for value in item.embedding] for item in response.data]

    return _OpenAIEmbeddingClient(client=OpenAI(base_url=base_url, api_key=api_key), model=model)


def make_embedding_model() -> Any:
    """Build (and cache) an OpenAI-compatible embedding client."""
    global _client, _batch_size
    if _client is not None:
        return _client

    base_url, api_key, model, batch_size = _resolve_embedding_settings()
    _batch_size = batch_size
    logger.info("Using recommender embedding API model=%s base_url=%s", model, base_url)
    _client = _create_openai_embedding_client(base_url=base_url, api_key=api_key, model=model)
    return _client


def embed_texts(model: Any, texts: list[str]) -> np.ndarray:
    """Embed texts via API; L2-normalize for Milvus IP metric."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)

    if hasattr(model, "embed_texts"):
        raw = model.embed_texts(texts, batch_size=_batch_size)
    else:
        raise TypeError(f"unsupported embedding model type: {type(model)!r}")

    arr = np.asarray(raw, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return arr / norms


def upsert_batch(
    collection,
    asset_ids: list[str],
    vectors: np.ndarray,
    *,
    category_ids: list[str] | None = None,
    plugin_types: list[str] | None = None,
) -> int:
    vectors_list = vectors.tolist()
    categories = category_ids if category_ids is not None else [""] * len(asset_ids)
    types = plugin_types if plugin_types is not None else [""] * len(asset_ids)
    if len(categories) != len(asset_ids):
        raise ValueError("category_ids length must match asset_ids")
    if len(types) != len(asset_ids):
        raise ValueError("plugin_types length must match asset_ids")
    payload = [asset_ids, categories, types, vectors_list]
    try:
        collection.upsert(payload)
        return len(asset_ids)
    except Exception as exc:
        logger.warning("upsert failed, fallback to insert: %s", exc)
        collection.insert(payload)
        return len(asset_ids)
