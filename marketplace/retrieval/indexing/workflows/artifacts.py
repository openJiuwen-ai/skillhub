# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import IntFlag
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional in tests
    OpenAI = Any  # type: ignore[misc,assignment]

from indexing.bm25 import (
    BM25Index,
    IndexedBM25Document,
    build_bm25_index,
    build_bm25_index_from_indexed_documents,
    load_bm25_index,
    save_bm25_index,
)
from indexing.catalog.records import CatalogRecord
from indexing.catalog.retrieval_text import (
    BM25Document,
    BM25FinderConfig,
    build_bm25_document_text,
    build_embedding_record_text,
    lexical_tokenize,
)
from indexing.embedding import (
    EmbeddingIndex,
    EmbeddingRecord,
    IndexedEmbeddingRecord,
    build_embedding_index,
    build_embedding_index_from_indexed_records,
    create_openai_embedding_client,
    load_embedding_index,
    save_embedding_index,
)


class BuildMethod(IntFlag):
    BM25 = 1
    EMBEDDING = 2
    TREE = 4
    ALL = BM25 | EMBEDDING | TREE


@dataclass(frozen=True)
class BuildConfig:
    method: BuildMethod = BuildMethod.ALL
    llm_openai_client: OpenAI | None = None
    llm_model: str = ""
    llm_seed: int | None = None
    embedding_openai_client: OpenAI | None = None
    embedding_model: str = ""
    embedding_batch_size: int = 16
    tree_branching_factor: int = 8
    tree_max_depth: int = 6
    tree_root_categories: list[str | dict[str, object]] | None = None
    tree_max_workers: int = 1
    tree_caching: bool = False
    tree_num_retries: int = 2
    tree_timeout_seconds: float = 180.0
    tree_context_window: int = 0
    tree_max_output_tokens: int = 0
    tree_postprocess_enabled: bool = True
    tree_postprocess_max_passes: int = 1
    tree_postprocess_min_skills: int = 6
    tree_equiv_grouping_enabled: bool = True
    tree_equiv_max_groups_per_parent: int = 6
    tree_equiv_allow_singleton_groups: bool = True
    tree_equiv_min_lexical_similarity: float = 0.12
    tree_deterministic_prompts: bool = True
    tree_discovery_seed: int = 42
    tree_prompt_fingerprint_version: str = "v1"
    tree_cache_observability: bool = True
    generate_tree_html: bool = True
    allow_fallback_tree: bool = True
    item_metadata_by_path: Dict[str, Dict[str, object]] | None = None


def _parse_build_method(value: str) -> BuildMethod:
    """Parse 'bm25', 'embedding', 'embedding_bm25', 'all' etc. into BuildMethod flags."""
    s = str(value or "").strip().lower().replace("-", "_")
    if s in ("all", ""):
        return BuildMethod.ALL
    if s == "embedding_bm25":
        return BuildMethod.EMBEDDING | BuildMethod.BM25
    result = BuildMethod(0)
    for part in re.split(r"[+|,\s]+", s):
        part = part.strip()
        if part == "bm25":
            result |= BuildMethod.BM25
        elif part == "embedding":
            result |= BuildMethod.EMBEDDING
        elif part in ("tree", "llm"):
            result |= BuildMethod.TREE
        elif part == "all":
            return BuildMethod.ALL
    return result if result else BuildMethod.ALL


@dataclass(frozen=True)
class IndexBuildRuntimeConfig:
    # build_method controls which indexes are built: "bm25", "embedding", "embedding_bm25", "all"
    # Default "embedding_bm25" skips tree (LLM) construction.
    build_method: str = "embedding_bm25"
    tree_llm_model: str = ""
    tree_llm_api_key: str = ""
    tree_llm_base_url: str = ""
    embedding_api_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_batch_size: int = 16


@dataclass(frozen=True)
class ResolvedBuildConfig:
    method: BuildMethod
    llm_openai_client: Any | None
    llm_model: str
    llm_seed: int | None
    tree_llm_api_key: str
    tree_llm_base_url: str
    embedding_openai_client: Any | None
    embedding_model: str
    embedding_api_base_url: str
    embedding_api_key: str
    embedding_batch_size: int
    tree_branching_factor: int
    tree_max_depth: int
    tree_root_categories: list[str | dict[str, object]] | None
    tree_max_workers: int
    tree_caching: bool
    tree_num_retries: int
    tree_timeout_seconds: float
    tree_context_window: int
    tree_max_output_tokens: int
    tree_postprocess_enabled: bool
    tree_postprocess_max_passes: int
    tree_postprocess_min_skills: int
    tree_equiv_grouping_enabled: bool
    tree_equiv_max_groups_per_parent: int
    tree_equiv_allow_singleton_groups: bool
    tree_equiv_min_lexical_similarity: float
    tree_deterministic_prompts: bool
    tree_discovery_seed: int
    tree_prompt_fingerprint_version: str
    tree_cache_observability: bool
    generate_tree_html: bool
    allow_fallback_tree: bool
    item_metadata_by_path: Dict[str, Dict[str, object]] | None


def resolve_build_config(
    *,
    config: BuildConfig | None = None,
    runtime_config: IndexBuildRuntimeConfig | None = None,
) -> ResolvedBuildConfig:
    if config is not None and runtime_config is not None:
        raise ValueError("Specify either config or runtime_config, not both")
    if config is not None:
        return ResolvedBuildConfig(
            method=BuildMethod(config.method),
            llm_openai_client=config.llm_openai_client,
            llm_model=str(config.llm_model or "").strip(),
            llm_seed=config.llm_seed,
            tree_llm_api_key="",
            tree_llm_base_url="",
            embedding_openai_client=config.embedding_openai_client,
            embedding_model=str(config.embedding_model or "").strip(),
            embedding_api_base_url="",
            embedding_api_key="",
            embedding_batch_size=max(1, int(config.embedding_batch_size or 16)),
            tree_branching_factor=max(1, int(config.tree_branching_factor or 8)),
            tree_max_depth=max(1, int(config.tree_max_depth or 6)),
            tree_root_categories=list(config.tree_root_categories) if config.tree_root_categories else None,
            tree_max_workers=max(1, int(config.tree_max_workers or 1)),
            tree_caching=bool(config.tree_caching),
            tree_num_retries=max(0, int(config.tree_num_retries or 0)),
            tree_timeout_seconds=float(config.tree_timeout_seconds),
            tree_context_window=max(0, int(config.tree_context_window or 0)),
            tree_max_output_tokens=max(0, int(config.tree_max_output_tokens or 0)),
            tree_postprocess_enabled=bool(config.tree_postprocess_enabled),
            tree_postprocess_max_passes=max(0, int(config.tree_postprocess_max_passes or 0)),
            tree_postprocess_min_skills=max(2, int(config.tree_postprocess_min_skills or 2)),
            tree_equiv_grouping_enabled=bool(config.tree_equiv_grouping_enabled),
            tree_equiv_max_groups_per_parent=max(2, int(config.tree_equiv_max_groups_per_parent or 2)),
            tree_equiv_allow_singleton_groups=bool(config.tree_equiv_allow_singleton_groups),
            tree_equiv_min_lexical_similarity=max(0.0, min(1.0, float(config.tree_equiv_min_lexical_similarity))),
            tree_deterministic_prompts=bool(config.tree_deterministic_prompts),
            tree_discovery_seed=int(config.tree_discovery_seed),
            tree_prompt_fingerprint_version=str(config.tree_prompt_fingerprint_version or "v1"),
            tree_cache_observability=bool(config.tree_cache_observability),
            generate_tree_html=bool(config.generate_tree_html),
            allow_fallback_tree=bool(config.allow_fallback_tree),
            item_metadata_by_path=dict(config.item_metadata_by_path or {}) or None,
        )
    runtime = runtime_config or IndexBuildRuntimeConfig()
    return ResolvedBuildConfig(
        method=_parse_build_method(runtime.build_method),
        llm_openai_client=None,
        llm_model=str(runtime.tree_llm_model or "").strip(),
        llm_seed=None,
        tree_llm_api_key=str(runtime.tree_llm_api_key or "").strip(),
        tree_llm_base_url=str(runtime.tree_llm_base_url or "").strip(),
        embedding_openai_client=None,
        embedding_model=str(runtime.embedding_model or "").strip(),
        embedding_api_base_url=str(runtime.embedding_api_base_url or "").strip(),
        embedding_api_key=str(runtime.embedding_api_key or "").strip(),
        embedding_batch_size=max(1, int(runtime.embedding_batch_size or 16)),
        tree_branching_factor=8,
        tree_max_depth=6,
        tree_root_categories=None,
        tree_max_workers=1,
        tree_caching=False,
        tree_num_retries=2,
        tree_timeout_seconds=180.0,
        tree_context_window=0,
        tree_max_output_tokens=0,
        tree_postprocess_enabled=True,
        tree_postprocess_max_passes=1,
        tree_postprocess_min_skills=6,
        tree_equiv_grouping_enabled=True,
        tree_equiv_max_groups_per_parent=6,
        tree_equiv_allow_singleton_groups=True,
        tree_equiv_min_lexical_similarity=0.12,
        tree_deterministic_prompts=True,
        tree_discovery_seed=42,
        tree_prompt_fingerprint_version="v1",
        tree_cache_observability=True,
        generate_tree_html=True,
        allow_fallback_tree=True,
        item_metadata_by_path=None,
    )


def build_catalog_records_from_nodes(
    *,
    nodes: Sequence[object],
    scanned_skills: Dict[str, dict],
    restrict_worker_ids: set[str] | None = None,
) -> List[CatalogRecord]:
    records: List[CatalogRecord] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        worker_id = str(raw_node.get("worker_id") or "").strip()
        if not worker_id:
            continue
        if restrict_worker_ids is not None and worker_id not in restrict_worker_ids:
            continue
        scanned = scanned_skills.get(worker_id) or {}
        cid = str(raw_node.get("cid") or "")
        name = str(scanned.get("name") or worker_id)
        description = str(scanned.get("description") or raw_node.get("description") or "").strip()
        content = str(scanned.get("content") or "").strip()
        plugin_display_name = str(scanned.get("plugin_display_name") or "").strip()
        market_display_name = str(scanned.get("market_display_name") or "").strip()
        market_short_desc = str(scanned.get("market_short_desc") or "").strip()
        market_detail_desc = str(scanned.get("market_detail_desc") or "").strip()
        skill_path = str(scanned.get("path") or "")
        raw_tags = scanned.get("tags")
        tags: Tuple[str, ...] = ()
        if isinstance(raw_tags, list):
            tags = tuple(str(t).strip() for t in raw_tags if isinstance(t, str) and t.strip())
        records.append(
            CatalogRecord(
                skill_id=worker_id,
                worker_id=worker_id,
                cid=cid,
                name=name,
                description=description,
                skill_path=skill_path,
                branch_path=tuple(cid.split(".")[:-1]),
                category=".".join(cid.split(".")[:-1]),
                retrieval_text=build_retrieval_text(
                    skill_id=worker_id,
                    name=name,
                    plugin_display_name=plugin_display_name,
                    market_display_name=market_display_name,
                    market_short_desc=market_short_desc,
                    market_detail_desc=market_detail_desc,
                    description=description,
                    content=content,
                    cid=cid,
                    tags=list(tags),
                ),
                metadata={
                    "content": content,
                    "plugin_display_name": plugin_display_name,
                    "market_display_name": market_display_name,
                    "market_short_desc": market_short_desc,
                    "market_detail_desc": market_detail_desc,
                },
                tags=tags,
            )
        )
    return sorted(records, key=lambda item: item.cid)


def write_catalog(records: Sequence[CatalogRecord], path: Path) -> None:
    lines = [
        json.dumps(
            {
                "skill_id": record.skill_id,
                "worker_id": record.worker_id,
                "cid": record.cid,
                "name": record.name,
                "description": record.description,
                "skill_path": record.skill_path,
                "branch_path": list(record.branch_path),
                "category": record.category,
                "retrieval_text": record.retrieval_text,
                "metadata": record.metadata,
                "tags": list(record.tags),
            },
            ensure_ascii=False,
        )
        for record in records
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_embedding_records(records: Sequence[CatalogRecord], path: Path) -> List[EmbeddingRecord]:
    embedding_records = [
        EmbeddingRecord(
            choice_id=record.worker_id,
            payload=record.cid,
            text=compact_text(
                build_embedding_record_text(
                    name=record.name,
                    description="",
                    retrieval_text=record.retrieval_text,
                    skill_id=record.worker_id,
                    cid=record.cid,
                ),
                limit=1800,
            ),
            description=record.description,
            metadata={"cid": record.cid, "skill_path": record.skill_path},
        )
        for record in records
    ]
    lines = [
        json.dumps(
            {
                "choice_id": item.choice_id,
                "payload": item.payload,
                "text": item.text,
                "description": item.description,
                "metadata": dict(item.metadata or {}),
            },
            ensure_ascii=False,
        )
        for item in embedding_records
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return embedding_records


def build_embedding_artifact(
    records: Sequence[EmbeddingRecord],
    output_path: Path,
    *,
    runtime_config: IndexBuildRuntimeConfig | None = None,
    config: BuildConfig | None = None,
) -> EmbeddingIndex:
    resolved = resolve_build_config(config=config, runtime_config=runtime_config)
    if not can_build_embedding_index(resolved):
        if output_path.exists():
            output_path.unlink()
        return EmbeddingIndex(model_name="", dimensions=0, records=())
    client = resolved.embedding_openai_client
    if client is None:
        client = create_openai_embedding_client(
            base_url=str(resolved.embedding_api_base_url or "").strip(),
            api_key=str(resolved.embedding_api_key or "").strip(),
            model=str(resolved.embedding_model or "").strip(),
        )
    index = build_embedding_index(
        records=records,
        embedding_client=client,
        model_name=str(resolved.embedding_model or "").strip(),
        batch_size=max(1, int(resolved.embedding_batch_size or 64)),
    )
    save_embedding_index(index, output_path)
    return index


def build_embedding_artifact_incremental(
    *,
    records: Sequence[EmbeddingRecord],
    output_path: Path,
    existing_index: EmbeddingIndex | None,
    runtime_config: IndexBuildRuntimeConfig | None = None,
    config: BuildConfig | None = None,
) -> EmbeddingIndex | None:
    resolved = resolve_build_config(config=config, runtime_config=runtime_config)
    if not can_build_embedding_index(resolved):
        if output_path.exists():
            output_path.unlink()
        return None
    existing_records = {str(item.payload): item for item in list(getattr(existing_index, "records", ()) or ())}
    client = resolved.embedding_openai_client
    if client is None:
        client = create_openai_embedding_client(
            base_url=str(resolved.embedding_api_base_url or "").strip(),
            api_key=str(resolved.embedding_api_key or "").strip(),
            model=str(resolved.embedding_model or "").strip(),
        )
    indexed_records: List[IndexedEmbeddingRecord] = []
    pending: List[EmbeddingRecord] = []
    for record in records:
        cached = existing_records.get(record.payload)
        if cached is not None and str(cached.text or "") == str(record.text or ""):
            indexed_records.append(cached)
        else:
            pending.append(record)
    if pending:
        fresh = build_embedding_index(
            records=pending,
            embedding_client=client,
            model_name=str(resolved.embedding_model or "").strip(),
            batch_size=max(1, int(resolved.embedding_batch_size or 64)),
        )
        indexed_records.extend(list(fresh.records))
    index = build_embedding_index_from_indexed_records(
        records=sorted(indexed_records, key=lambda item: str(item.payload)),
        model_name=str(resolved.embedding_model or "").strip(),
    )
    save_embedding_index(index, output_path)
    return index


def build_bm25_artifact(records: Sequence[CatalogRecord], output_path: Path) -> BM25Index:
    documents = [
        BM25Document(
            choice_id=record.worker_id,
            payload=record.cid,
            text=build_bm25_document_text(
                choice_id=record.worker_id,
                payload=record.cid,
                description=record.retrieval_text,
            ),
            description=record.description,
        )
        for record in records
    ]
    index = build_bm25_index(documents=documents, config=BM25FinderConfig(top_k=max(1, len(documents) or 1)))
    save_bm25_index(index, output_path)
    return index


def build_bm25_artifact_incremental(
    *,
    records: Sequence[CatalogRecord],
    output_path: Path,
    existing_index: BM25Index | None,
) -> BM25Index:
    existing_documents = {str(item.payload): item for item in list(getattr(existing_index, "documents", ()) or ())}
    indexed_documents: List[IndexedBM25Document] = []
    for record in records:
        text = build_bm25_document_text(
            choice_id=record.worker_id,
            payload=record.cid,
            description=record.retrieval_text,
        )
        cached = existing_documents.get(record.cid)
        if cached is not None and str(cached.text or "") == text:
            indexed_documents.append(cached)
            continue
        indexed_documents.append(
            IndexedBM25Document(
                choice_id=record.worker_id,
                payload=record.cid,
                text=text,
                description=record.description,
                tokens=tuple(lexical_tokenize(text)),
            )
        )
    index = build_bm25_index_from_indexed_documents(
        documents=sorted(indexed_documents, key=lambda item: str(item.payload)),
        config=BM25FinderConfig(top_k=max(1, len(indexed_documents) or 1)),
    )
    save_bm25_index(index, output_path)
    return index


def can_build_tree_with_llm(runtime_config: IndexBuildRuntimeConfig | ResolvedBuildConfig | None = None) -> bool:
    if isinstance(runtime_config, ResolvedBuildConfig):
        if not (runtime_config.method & BuildMethod.TREE):
            return False
        return bool(str(runtime_config.llm_model or "").strip()) and (
            runtime_config.llm_openai_client is not None or bool(str(runtime_config.tree_llm_api_key or "").strip())
        )
    runtime = runtime_config or IndexBuildRuntimeConfig()
    if not (BuildMethod.TREE & _parse_build_method(runtime.build_method)):
        return False
    return bool(str(runtime.tree_llm_model or "").strip()) and bool(str(runtime.tree_llm_api_key or "").strip())


def can_build_embedding_index(runtime_config: IndexBuildRuntimeConfig | ResolvedBuildConfig | None = None) -> bool:
    if isinstance(runtime_config, ResolvedBuildConfig):
        return bool(str(runtime_config.embedding_model or "").strip()) and (
            runtime_config.embedding_openai_client is not None or bool(
                str(runtime_config.embedding_api_key or "").strip())
        )
    runtime = runtime_config or IndexBuildRuntimeConfig()
    return bool(str(runtime.embedding_model or "").strip()) and bool(str(runtime.embedding_api_key or "").strip())


def build_fallback_tree_index(*, aggregate_dir: Path, output_path: Path) -> None:
    nodes = [
        {
            "cid": "Skills",
            "type": "branch",
            "description": "Fallback skill index built without LLM tree generation.",
        }
    ]
    for skill_dir in sorted(path for path in aggregate_dir.iterdir() if path.is_dir()):
        worker_id = skill_dir.name
        nodes.append(
            {
                "cid": f"Skills.{worker_id}",
                "type": "leaf",
                "description": worker_id,
                "worker_id": worker_id,
            }
        )
    from indexing.io.tree import write_tree_preset

    write_tree_preset({"nodes": nodes}, output_path)


def build_retrieval_text(
    *,
    skill_id: str,
    name: str,
    plugin_display_name: str = "",
    market_display_name: str = "",
    market_short_desc: str = "",
    market_detail_desc: str = "",
    description: str,
    content: str,
    cid: str,
    tags: list[str] | None = None,
) -> str:
    parts = [
        compact_text(name, limit=200),
        compact_text(plugin_display_name, limit=200),
        compact_text(market_display_name, limit=200),
        compact_text(market_short_desc, limit=600),
        compact_text("" if market_short_desc else description, limit=400),
        compact_text(skill_id, limit=120),
        compact_text(cid, limit=200),
        compact_text(" ".join(tags or []), limit=200),
    ]
    return "\n".join(part for part in parts if part)


def compact_text(text: str, *, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "..."


def safe_load_embedding_index(path: Path) -> EmbeddingIndex | None:
    if not path.exists():
        return None
    try:
        return load_embedding_index(path)
    except Exception:
        return None


def safe_load_bm25_index(path: Path) -> BM25Index | None:
    if not path.exists():
        return None
    try:
        return load_bm25_index(path)
    except Exception:
        return None


__all__ = [
    "BuildConfig",
    "BuildMethod",
    "IndexBuildRuntimeConfig",
    "ResolvedBuildConfig",
    "build_bm25_artifact",
    "build_bm25_artifact_incremental",
    "build_catalog_records_from_nodes",
    "build_embedding_artifact",
    "build_embedding_artifact_incremental",
    "build_fallback_tree_index",
    "build_retrieval_text",
    "can_build_embedding_index",
    "can_build_tree_with_llm",
    "compact_text",
    "safe_load_bm25_index",
    "safe_load_embedding_index",
    "resolve_build_config",
    "write_catalog",
    "write_embedding_records",
]
