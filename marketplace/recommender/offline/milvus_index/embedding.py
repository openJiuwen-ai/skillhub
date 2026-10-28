"""Embedding helpers for skill texts (OpenAI-compatible API)."""

from __future__ import annotations

import logging
import time
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
        from indexing.embedding import OFFLINE_EMBED_MIN_INTERVAL_S, create_openai_embedding_client

        return create_openai_embedding_client(
            base_url=base_url,
            api_key=api_key,
            model=model,
            min_interval_s=OFFLINE_EMBED_MIN_INTERVAL_S,
        )
    except ImportError as exc:
        logger.debug(
            "indexing.embedding unavailable (%s); use local OpenAI embeddings client",
            exc,
        )

    from openai import OpenAI

    class _OpenAIEmbeddingClient:
        """Fallback when indexing.embedding cannot be imported. Keep backoff in step with it."""

        def __init__(self, *, client: Any, model: str, min_interval_s: float = 0.8) -> None:
            self._client = client
            self.model = str(model)
            self.min_interval_s = max(0.0, float(min_interval_s or 0.0))
            self._next_embed_at = 0.0

        def embed_texts(self, texts: Sequence[str], *, batch_size: int = 64) -> list[list[float]]:
            vectors: list[list[float]] = []
            chunk_size = max(1, int(batch_size))
            for start in range(0, len(texts), chunk_size):
                chunk = [str(text) for text in texts[start:start + chunk_size]]
                vectors.extend(self._embed_chunk_with_backoff(chunk))
            return vectors

        def _pace(self) -> None:
            if self.min_interval_s <= 0:
                return
            wait = self._next_embed_at - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next_embed_at = time.monotonic() + self.min_interval_s

        def _embed_chunk_with_backoff(self, chunk: Sequence[str]) -> list[list[float]]:
            if not chunk:
                return []
            second_attempts = 0
            minute_attempts = 0
            while True:
                self._pace()
                try:
                    response = self._client.embeddings.create(model=self.model, input=list(chunk))
                except Exception as exc:
                    message = str(exc).lower()
                    split = len(chunk) > 1 and (
                        "batch size" in message
                        or "should not be larger than" in message
                        or "maximum context length" in message
                        or "modelarts.81001" in message
                    )
                    if split:
                        midpoint = max(1, len(chunk) // 2)
                        return self._embed_chunk_with_backoff(chunk[:midpoint]) + self._embed_chunk_with_backoff(
                            chunk[midpoint:]
                        )
                    if len(chunk) == 1 and (
                        "maximum context length" in message or "modelarts.81001" in message
                    ) and len(chunk[0]) > 1:
                        return self._embed_chunk_with_backoff([chunk[0][: max(1, len(chunk[0]) // 2)]])
                    rate_limited = (
                        getattr(exc, "status_code", None) == 429
                        or "rate limit" in message
                        or "too many requests" in message
                    )
                    if not rate_limited or self.min_interval_s <= 0:
                        raise
                    if "per minute" in message:
                        minute_attempts += 1
                        if minute_attempts > 3:
                            raise
                        delay = 61.0
                        attempt = minute_attempts
                    else:
                        second_attempts += 1
                        if second_attempts > 5:
                            raise
                        delay = min(8.0, float(2 ** (second_attempts - 1)))
                        attempt = second_attempts
                    logger.warning("embedding rate limited, retry %s in %.0fs: %s", attempt, delay, exc)
                    time.sleep(delay)
                    continue
                return [[float(value) for value in item.embedding] for item in response.data]

    return _OpenAIEmbeddingClient(
        client=OpenAI(base_url=base_url, api_key=api_key, max_retries=0),
        model=model,
    )


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
