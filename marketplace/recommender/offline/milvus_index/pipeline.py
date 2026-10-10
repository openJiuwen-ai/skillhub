"""Incremental / full Milvus index pipeline driven by MySQL.

SkillHub-style flow: MySQL catalog -> on-demand MinIO/OBS zip ->
encode SKILL.md (name+description) -> upsert Milvus.

If the package is missing in object storage, the skill is skipped (not indexed).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from recommender.shared.config import AppConfig, load_config
from recommender.offline.package_sync.db import ActiveSkillVersion, fetch_active_latest_skills

from .embedding import embed_texts, make_embedding_model, upsert_batch
from .milvus_client import (
    AliasCutoverStuckError,
    CollectionConfig,
    connect_milvus,
    create_collection_with_schema,
    create_vector_index_if_needed,
    delete_by_asset_ids,
    drop_collection_if_exists,
    ensure_collection,
    load_collection_config,
    new_physical_collection_name,
    promote_collection_alias,
)
from .planner import plan_full, plan_incremental
from .skill_md import (
    SkillMdExtract,
    embedding_text_from_market_fields,
    extract_skill_from_zip,
)
from .state import DEFAULT_STATE_PATH, IndexedAsset, load_state, replace_state
from .zip_resolver import ensure_skill_zip, resolve_local_zip

logger = logging.getLogger(__name__)

MANIFEST_NAME = "last_milvus_index_manifest.json"


@dataclass
class PipelineStats:
    mode: str
    active_total: int
    upsert_candidates: int
    upserted: int
    deleted: int
    failed: int
    embedding_dim: int


@dataclass
class _IndexedItem:
    skill: ActiveSkillVersion
    extract: SkillMdExtract
    zip_path: str | None
    source: str  # "skill_md" | "market_fields"


def _resolve_collection_cfg(
    dim: int,
    *,
    collection_name: str | None,
    host: str | None,
    port: int | None,
    recreate: bool,
) -> CollectionConfig:
    cfg = load_collection_config(dim, recreate=recreate)
    if collection_name is None and host is None and port is None:
        return cfg
    return CollectionConfig(
        host=host or cfg.host,
        port=port if port is not None else cfg.port,
        collection=collection_name or cfg.collection,
        dim=dim,
        recreate=recreate,
    )


def _resolve_embedding_text(
    skill: ActiveSkillVersion,
    zip_path: Path,
) -> tuple[SkillMdExtract, str]:
    """Prefer SKILL.md; fall back to MySQL display_name/short_desc."""
    try:
        extract = extract_skill_from_zip(zip_path=str(zip_path), asset_id=skill.asset_id)
        return extract, "skill_md"
    except Exception as exc:
        logger.warning(
            "SKILL.md parse failed asset_id=%s path=%s; fallback to market fields: %s",
            skill.asset_id,
            zip_path,
            exc,
        )
        extract = embedding_text_from_market_fields(
            asset_id=skill.asset_id,
            display_name=skill.display_name,
            name=skill.name,
            short_desc=skill.short_desc,
        )
        return extract, "market_fields"


def _index_skills(
    collection,
    model,
    skills: list[ActiveSkillVersion],
    download_dir: Path,
    *,
    batch_size: int,
    download_force: bool,
    app_cfg: AppConfig,
) -> tuple[int, int, dict[str, IndexedAsset], list[_IndexedItem], list[str]]:
    pending_ids: list[str] = []
    pending_categories: list[str] = []
    pending_plugin_types: list[str] = []
    pending_texts: list[str] = []
    indexed: dict[str, IndexedAsset] = {}
    items: list[_IndexedItem] = []
    failed_ids: list[str] = []
    upserted = 0
    failed = 0
    now = datetime.now(timezone.utc).isoformat()

    def flush() -> None:
        nonlocal upserted, pending_ids, pending_categories, pending_plugin_types, pending_texts
        if not pending_ids:
            return
        vectors = embed_texts(model, pending_texts)
        upserted += upsert_batch(
            collection,
            pending_ids,
            vectors,
            category_ids=pending_categories,
            plugin_types=pending_plugin_types,
        )
        pending_ids = []
        pending_categories = []
        pending_plugin_types = []
        pending_texts = []

    for skill in skills:
        try:
            zip_path = resolve_local_zip(download_dir, skill)
            if zip_path is None or download_force:
                zip_path = ensure_skill_zip(app_cfg, skill, force=download_force)

            extract, source = _resolve_embedding_text(skill, zip_path)
            pending_ids.append(skill.asset_id)
            pending_categories.append(skill.normalized_category_id)
            pending_plugin_types.append(skill.normalized_plugin_type)
            pending_texts.append(extract.embedding_text)
            indexed[skill.asset_id] = IndexedAsset(
                version=skill.latest_version,
                artifact_sha256=skill.artifact_sha256,
                indexed_at=now,
                category_id=skill.normalized_category_id,
                plugin_type=skill.normalized_plugin_type,
            )
            items.append(
                _IndexedItem(
                    skill=skill,
                    extract=extract,
                    zip_path=str(zip_path) if zip_path else None,
                    source=source,
                )
            )
            if len(pending_ids) >= batch_size:
                flush()
        except Exception as exc:
            # No MinIO/OBS package (or unreadable): do not push metadata-only vectors.
            failed += 1
            failed_ids.append(skill.asset_id)
            logger.warning(
                "Skip index (package unavailable) asset_id=%s version=%s err=%s",
                skill.asset_id,
                skill.latest_version,
                exc,
            )

    flush()
    return upserted, failed, indexed, items, failed_ids


def _update_category_only(
    collection,
    skills: list[ActiveSkillVersion],
    *,
    batch_size: int,
) -> tuple[int, dict[str, IndexedAsset], list[str]]:
    """Reuse existing embeddings; rewrite category_id / plugin_type."""
    if not skills:
        return 0, {}, []

    asset_ids = [s.asset_id for s in skills]
    emb_map: dict[str, list[float]] = {}
    query_batch = 64
    for i in range(0, len(asset_ids), query_batch):
        batch = asset_ids[i:i + query_batch]
        quoted = ", ".join(f'"{aid}"' for aid in batch)
        rows = collection.query(
            expr=f"asset_id in [{quoted}]",
            output_fields=["asset_id", "embedding"],
        )
        for row in rows:
            aid = str(row["asset_id"])
            emb = row.get("embedding")
            if emb is None:
                continue
            emb_map[aid] = list(emb)

    upserted = 0
    indexed: dict[str, IndexedAsset] = {}
    failed_ids: list[str] = []
    now = datetime.now(timezone.utc).isoformat()

    pending_ids: list[str] = []
    pending_categories: list[str] = []
    pending_plugin_types: list[str] = []
    pending_vectors: list[list[float]] = []

    def flush() -> None:
        nonlocal upserted, pending_ids, pending_categories, pending_plugin_types, pending_vectors
        if not pending_ids:
            return
        vectors = np.asarray(pending_vectors, dtype=np.float32)
        upserted += upsert_batch(
            collection,
            pending_ids,
            vectors,
            category_ids=pending_categories,
            plugin_types=pending_plugin_types,
        )
        pending_ids = []
        pending_categories = []
        pending_plugin_types = []
        pending_vectors = []

    for skill in skills:
        emb = emb_map.get(skill.asset_id)
        if emb is None:
            failed_ids.append(skill.asset_id)
            logger.warning(
                "category-only update skipped; embedding missing asset_id=%s",
                skill.asset_id,
            )
            continue
        pending_ids.append(skill.asset_id)
        pending_categories.append(skill.normalized_category_id)
        pending_plugin_types.append(skill.normalized_plugin_type)
        pending_vectors.append(emb)
        indexed[skill.asset_id] = IndexedAsset(
            version=skill.latest_version,
            artifact_sha256=skill.artifact_sha256,
            indexed_at=now,
            category_id=skill.normalized_category_id,
            plugin_type=skill.normalized_plugin_type,
        )
        if len(pending_ids) >= batch_size:
            flush()

    flush()
    return upserted, indexed, failed_ids


def _write_index_manifest(
    download_dir: Path,
    *,
    mode: str,
    active: list[ActiveSkillVersion],
    items: list[_IndexedItem],
    stats: PipelineStats,
) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "active_total": len(active),
        "upserted": stats.upserted,
        "failed": stats.failed,
        "deleted": stats.deleted,
        "embedding_dim": stats.embedding_dim,
        "skills": [
            {
                "asset_id": s.asset_id,
                "name": s.name,
                "display_name": s.display_name,
                "plugin_type": s.plugin_type,
                "latest_version": s.latest_version,
                "item_path": s.item_path,
                "artifact_sha256": s.artifact_sha256,
                "category_id": s.normalized_category_id,
            }
            for s in active
        ],
        "indexed": [
            {
                "asset_id": item.skill.asset_id,
                "version": item.skill.latest_version,
                "category_id": item.skill.normalized_category_id,
                "item_path": item.skill.item_path,
                "zip_path": item.zip_path,
                "source": item.source,
                "name": item.extract.name,
                "description": item.extract.description,
                "embedding_text": item.extract.embedding_text,
            }
            for item in items
        ],
    }
    path = download_dir / MANIFEST_NAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote milvus index manifest: %s", path)
    return path


def run_incremental_index(
    *,
    app_cfg: AppConfig | None = None,
    state_path: Path = DEFAULT_STATE_PATH,
    collection_name: str | None = None,
    host: str | None = None,
    port: int | None = None,
    batch_size: int | None = None,
) -> PipelineStats:
    app_cfg = app_cfg or load_config()
    batch_size = batch_size or app_cfg.milvus.batch_size

    active = fetch_active_latest_skills(app_cfg)
    state = load_state(state_path)
    plan = plan_incremental(active, state)
    logger.info(
        "Incremental plan: active=%s upsert=%s category_only=%s delete=%s",
        len(plan.active),
        len(plan.to_upsert),
        len(plan.category_only),
        len(plan.to_delete),
    )

    model = make_embedding_model()
    dim = int(embed_texts(model, ["warmup"]).shape[1])
    cfg = _resolve_collection_cfg(
        dim,
        collection_name=collection_name,
        host=host,
        port=port,
        recreate=False,
    )
    connect_milvus(cfg)
    collection = ensure_collection(cfg)
    create_vector_index_if_needed(collection)

    deleted = delete_by_asset_ids(collection, plan.to_delete)
    if deleted:
        logger.info("Deleted %s offline/removed asset(s) from Milvus", deleted)

    upserted, failed, newly_indexed, items, failed_ids = _index_skills(
        collection,
        model,
        plan.to_upsert,
        app_cfg.download_dir,
        batch_size=batch_size,
        download_force=False,
        app_cfg=app_cfg,
    )
    cat_upserted, cat_indexed, cat_failed = _update_category_only(
        collection,
        plan.category_only,
        batch_size=batch_size,
    )
    upserted += cat_upserted
    newly_indexed.update(cat_indexed)
    # Category-only miss (no embedding): drop from state so next cycle full re-encodes.
    failed_ids = list(dict.fromkeys([*failed_ids, *cat_failed]))
    failed += len(cat_failed)

    if failed_ids:
        delete_by_asset_ids(collection, failed_ids)
        logger.info("Removed %s unpackaged/missing-embedding asset(s) from Milvus", len(failed_ids))

    active_by_id = {s.asset_id: s for s in plan.active}
    failed_set = set(failed_ids)
    merged: dict[str, IndexedAsset] = {}
    for asset_id in active_by_id:
        if asset_id in newly_indexed:
            merged[asset_id] = newly_indexed[asset_id]
        elif asset_id in failed_set:
            continue
        elif state.get(asset_id) is not None:
            merged[asset_id] = state.assets[asset_id]
    replace_state(merged, state_path)

    stats = PipelineStats(
        mode="incremental",
        active_total=len(plan.active),
        upsert_candidates=len(plan.to_upsert) + len(plan.category_only),
        upserted=upserted,
        deleted=deleted,
        failed=failed,
        embedding_dim=dim,
    )
    _write_index_manifest(
        app_cfg.download_dir,
        mode=stats.mode,
        active=plan.active,
        items=items,
        stats=stats,
    )
    return stats


def run_full_rebuild(
    *,
    app_cfg: AppConfig | None = None,
    state_path: Path = DEFAULT_STATE_PATH,
    collection_name: str | None = None,
    host: str | None = None,
    port: int | None = None,
    batch_size: int | None = None,
    download_force: bool = False,
) -> PipelineStats:
    app_cfg = app_cfg or load_config()
    batch_size = batch_size or app_cfg.milvus.batch_size

    active = fetch_active_latest_skills(app_cfg)
    plan = plan_full(active)
    logger.info("Full rebuild: active=%s", len(plan.active))

    model = make_embedding_model()
    dim = int(embed_texts(model, ["warmup"]).shape[1])
    cfg = _resolve_collection_cfg(
        dim,
        collection_name=collection_name,
        host=host,
        port=port,
        recreate=False,
    )
    connect_milvus(cfg)
    public_name = cfg.collection
    physical = new_physical_collection_name(public_name)
    logger.info(
        "Full rebuild: build physical=%s then swap alias=%s",
        physical,
        public_name,
    )
    collection = create_collection_with_schema(cfg, physical)

    try:
        upserted, failed, indexed, items, _failed_ids = _index_skills(
            collection,
            model,
            plan.to_upsert,
            app_cfg.download_dir,
            batch_size=batch_size,
            download_force=download_force,
            app_cfg=app_cfg,
        )
        create_vector_index_if_needed(collection)
        previous = promote_collection_alias(public_name, physical)
    except AliasCutoverStuckError:
        logger.exception(
            "full rebuild: public name %s missing after failed first-cutover; "
            "keeping physical %s for manual recover",
            public_name,
            physical,
        )
        raise
    except Exception:
        logger.exception(
            "full rebuild failed before alias swap; drop incomplete %s",
            physical,
        )
        try:
            drop_collection_if_exists(physical)
        except Exception:
            logger.exception("failed to drop incomplete collection %s", physical)
        raise

    replace_state(indexed, state_path)
    if previous and previous != physical:
        try:
            drop_collection_if_exists(previous)
        except Exception:
            logger.warning(
                "full rebuild: left orphan collection %s (alias already points to %s)",
                previous,
                physical,
                exc_info=True,
            )
    try:
        from recommender.online.search import clear_collection_cache

        clear_collection_cache()
    except Exception:
        logger.warning("full rebuild: clear_collection_cache failed", exc_info=True)

    stats = PipelineStats(
        mode="full",
        active_total=len(plan.active),
        upsert_candidates=len(plan.to_upsert),
        upserted=upserted,
        deleted=0,
        failed=failed,
        embedding_dim=dim,
    )
    _write_index_manifest(
        app_cfg.download_dir,
        mode=stats.mode,
        active=plan.active,
        items=items,
        stats=stats,
    )
    return stats
