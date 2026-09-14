# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import logging
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

from indexing.models import (
    BM25_INDEX_FILENAME,
    CATALOG_FILENAME,
    EMBEDDING_INDEX_FILENAME,
    EMBEDDING_RECORDS_FILENAME,
    TREE_HTML_FILENAME,
    TREE_INDEX_FILENAME,
)
from indexing.workflows.tree_ops import (
    align_leaf_nodes_with_catalog,
    build_catalog_records_from_existing,
    enrich_branch_descriptions,
    merge_added_skills_into_tree,
    prune_deleted_skills_from_tree,
    tree_nodes_to_tree_dict,
)
from indexing.tree.visualizer import generate_html as generate_tree_html

from indexing.io import (
    load_catalog_records,
    load_manifest,
    load_tree_preset,
    normalize_item_paths,
    write_manifest,
    write_tree_preset,
)
from indexing.scanners import create_scanner, get_scanner_class, normalize_item_type
from indexing.tree import DynamicTreeConfig, TreeBuildConfig, TreeManagerConfig
from indexing.tree.builder import BuildTreeRequest, TreeBuilder, build_tree
from indexing.tree.schema import FIXED_ROOT_CATEGORIES, normalize_root_categories
from shared.limits import (
    MAX_ZIP_ARCHIVE_BYTES,
    MAX_ZIP_MEMBER_BYTES,
    MAX_ZIP_MEMBERS,
    MAX_ZIP_UNCOMPRESSED_BYTES,
    ensure_path_size,
)
from shared.storage import download_s3_object_to_path, is_s3_uri, materialize_s3_dir, upload_local_dir_to_s3

from .artifacts import (
    BuildConfig,
    BuildMethod,
    IndexBuildRuntimeConfig,
    ResolvedBuildConfig,
    build_bm25_artifact,
    build_bm25_artifact_incremental,
    build_catalog_records_from_nodes,
    build_embedding_artifact,
    build_embedding_artifact_incremental,
    build_fallback_tree_index,
    can_build_tree_with_llm,
    resolve_build_config,
    safe_load_bm25_index,
    safe_load_embedding_index,
    write_catalog,
    write_embedding_records,
)


_OBS_PUBLISHER_RE = re.compile(
    r'^(?:obs|s3)://[^/]+/(?:skills|plugins|agent-plugins|agent-templates|agent-mcps)/([^/]+)/'
)
_SKILLS_TAG_MAPPING_FILENAME = "skills_tag_mapping.jsonl"


def _unique_dir_name(source_path: str, original_name: str) -> str:
    """Return '{publisher_id}__{original_name}' for OBS/S3 paths, else original_name."""
    m = _OBS_PUBLISHER_RE.match(str(source_path))
    if m:
        return f"{m.group(1)}__{original_name}"
    return original_name


@dataclass(frozen=True)
class ResolvedItemPath:
    source_path: str
    source_type: str
    materialized_dir: Path


class IndexBuilder:
    @staticmethod
    def build(
        item_paths: list[str],
        output_dir: str | Path,
        *,
        item_type: str = "skill",
        config: BuildConfig | None = None,
        runtime_config: IndexBuildRuntimeConfig | None = None,
    ) -> str | Path:
        normalized_item_paths = normalize_item_paths(item_paths)
        resolved_config = resolve_build_config(config=config, runtime_config=runtime_config)
        normalized_item_type = normalize_item_type(item_type)
        output_value = str(output_dir).strip()
        if is_s3_uri(output_value):
            with tempfile.TemporaryDirectory(prefix="finder-index-s3-output-") as tmpdir:
                local_output_dir = Path(tmpdir) / "index"
                _IndexBuildWorkflow(
                    item_paths=normalized_item_paths,
                    output_dir=local_output_dir,
                    resolved_config=resolved_config,
                    item_type=normalized_item_type,
                ).build()
                upload_local_dir_to_s3(local_output_dir, output_value)
            return output_value.rstrip("/")
        return _IndexBuildWorkflow(
            item_paths=normalized_item_paths,
            output_dir=Path(output_dir),
            resolved_config=resolved_config,
            item_type=normalized_item_type,
        ).build()

    @staticmethod
    def build_skill_tags(
        item_paths: list[str],
        output_dir: str | Path,
        *,
        item_type: str = "skill",
        config: BuildConfig | None = None,
        runtime_config: IndexBuildRuntimeConfig | None = None,
        require_llm: bool = True,
    ) -> str | Path:
        normalized_item_paths = normalize_item_paths(item_paths)
        resolved_config = resolve_build_config(config=config, runtime_config=runtime_config)
        normalized_item_type = normalize_item_type(item_type)
        output_value = str(output_dir).strip()
        if is_s3_uri(output_value):
            with tempfile.TemporaryDirectory(prefix="finder-tag-s3-output-") as tmpdir:
                local_output_dir = Path(tmpdir) / "index"
                _build_root_tag_mapping(
                    item_paths=normalized_item_paths,
                    output_dir=local_output_dir,
                    resolved_config=resolved_config,
                    item_type=normalized_item_type,
                    require_llm=require_llm,
                )
                upload_local_dir_to_s3(local_output_dir, output_value)
            return output_value.rstrip("/")
        local_output_dir = Path(output_dir)
        _build_root_tag_mapping(
            item_paths=normalized_item_paths,
            output_dir=local_output_dir,
            resolved_config=resolved_config,
            item_type=normalized_item_type,
            require_llm=require_llm,
        )
        return local_output_dir

    @staticmethod
    def add(
        item_paths: list[str],
        base_index_dir: str | Path,
        output_dir: str | Path,
        *,
        item_type: str = "skill",
        config: BuildConfig | None = None,
        runtime_config: IndexBuildRuntimeConfig | None = None,
    ) -> str | Path:
        base_dir = _materialize_existing_index_dir(base_index_dir, cache_namespace="finder-index-add-cache")
        manifest = load_manifest(base_dir)
        existing = normalize_item_paths(manifest.get("item_paths") or ())
        added_paths = normalize_item_paths(item_paths)
        combined = normalize_item_paths([*existing, *added_paths])
        resolved_config = resolve_build_config(config=config, runtime_config=runtime_config)
        normalized_item_type = normalize_item_type(item_type)
        output_value = str(output_dir).strip()
        if is_s3_uri(output_value):
            with tempfile.TemporaryDirectory(prefix="finder-index-add-s3-output-") as tmpdir:
                local_output_dir = Path(tmpdir) / "index"
                _IncrementalIndexBuildWorkflow(
                    item_paths=combined,
                    output_dir=local_output_dir,
                    resolved_config=resolved_config,
                    base_index_dir=base_dir,
                    added_paths=added_paths,
                    removed_paths=[],
                    item_type=normalized_item_type,
                ).build()
                upload_local_dir_to_s3(local_output_dir, output_value)
            return output_value.rstrip("/")
        return _IncrementalIndexBuildWorkflow(
            item_paths=combined,
            output_dir=Path(output_dir),
            resolved_config=resolved_config,
            base_index_dir=base_dir,
            added_paths=added_paths,
            removed_paths=[],
            item_type=normalized_item_type,
        ).build()

    @staticmethod
    def delete(
        item_paths: list[str],
        base_index_dir: str | Path,
        output_dir: str | Path,
        *,
        item_type: str = "skill",
        config: BuildConfig | None = None,
        runtime_config: IndexBuildRuntimeConfig | None = None,
    ) -> str | Path:
        base_dir = _materialize_existing_index_dir(base_index_dir, cache_namespace="finder-index-delete-cache")
        manifest = load_manifest(base_dir)
        existing = normalize_item_paths(manifest.get("item_paths") or ())
        removed = set(normalize_item_paths(item_paths))
        remaining = [path for path in existing if path not in removed]
        resolved_config = resolve_build_config(config=config, runtime_config=runtime_config)
        normalized_item_type = normalize_item_type(item_type)
        output_value = str(output_dir).strip()
        if is_s3_uri(output_value):
            with tempfile.TemporaryDirectory(prefix="finder-index-delete-s3-output-") as tmpdir:
                local_output_dir = Path(tmpdir) / "index"
                _IncrementalIndexBuildWorkflow(
                    item_paths=normalize_item_paths(remaining),
                    output_dir=local_output_dir,
                    resolved_config=resolved_config,
                    base_index_dir=base_dir,
                    added_paths=[],
                    removed_paths=sorted(removed),
                    item_type=normalized_item_type,
                ).build()
                upload_local_dir_to_s3(local_output_dir, output_value)
            return output_value.rstrip("/")
        return _IncrementalIndexBuildWorkflow(
            item_paths=normalize_item_paths(remaining),
            output_dir=Path(output_dir),
            resolved_config=resolved_config,
            base_index_dir=base_dir,
            added_paths=[],
            removed_paths=sorted(removed),
            item_type=normalized_item_type,
        ).build()


def _materialize_existing_index_dir(base_index_dir: str | Path, *, cache_namespace: str) -> Path:
    raw = str(base_index_dir).strip()
    if is_s3_uri(raw):
        return materialize_s3_dir(raw, cache_namespace=cache_namespace)
    return Path(base_index_dir).resolve()


def _resolve_materialized_item_paths(
        item_paths: Sequence[str], *, work_dir: Path, item_type: str) -> list[ResolvedItemPath]:
    resolved: list[ResolvedItemPath] = []
    failed: list[str] = []
    extracted_root = work_dir / "materialized"
    extracted_root.mkdir(parents=True, exist_ok=True)
    scanner_cls = get_scanner_class(item_type)

    for index, raw_path in enumerate(item_paths):
        raw_text = str(raw_path).strip()
        if not raw_text:
            continue
        try:
            if is_s3_uri(raw_text):
                archive_path = _download_remote_zip(raw_text, extracted_root / f"item-{index}.zip")
                item_dir = _extract_item_zip(archive_path, extracted_root / f"item-{index}", scanner_cls=scanner_cls)
                resolved.append(ResolvedItemPath(source_path=raw_text, source_type="s3_zip", materialized_dir=item_dir))
                continue

            local_path = Path(raw_text).expanduser().resolve()
            if not local_path.exists():
                raise FileNotFoundError(f"Item path not found: {local_path}")
            if local_path.is_dir():
                item_dir = _validate_item_dir(local_path, scanner_cls=scanner_cls)
                resolved.append(
                    ResolvedItemPath(
                        source_path=str(local_path),
                        source_type="local_dir",
                        materialized_dir=item_dir,
                    )
                )
                continue
            if local_path.is_file() and local_path.suffix.lower() == ".zip":
                item_dir = _extract_item_zip(local_path, extracted_root / f"item-{index}", scanner_cls=scanner_cls)
                resolved.append(
                    ResolvedItemPath(
                        source_path=str(local_path),
                        source_type="local_zip",
                        materialized_dir=item_dir,
                    )
                )
                continue
            raise ValueError(f"Unsupported item path: {raw_text}. Only local dir/zip and s3://...zip are supported")
        except Exception as exc:
            logger.warning("item build failed, skipping: path=%s error=%s", raw_text, exc)
            failed.append(raw_text)

    names: set[str] = set()
    deduped: list[ResolvedItemPath] = []
    for item in resolved:
        # Keep duplicate check aligned with final materialization naming logic.
        # Different publishers can legitimately share the same skill dir name.
        effective_name = _unique_dir_name(item.source_path, item.materialized_dir.name)
        if effective_name in names:
            logger.warning("duplicate skill directory name, skipping: %s (source=%s)", effective_name, item.source_path)
            failed.append(item.source_path)
            continue
        names.add(effective_name)
        deduped.append(item)

    if failed:
        logger.warning("item resolution: %d/%d items failed or skipped: %s", len(failed), len(item_paths), failed)
    return deduped


def _download_remote_zip(uri: str, destination_path: Path) -> Path:
    if not str(uri).lower().endswith(".zip"):
        raise ValueError(f"Remote item path must point to a zip file: {uri}")
    return download_s3_object_to_path(str(uri), destination_path, max_bytes=MAX_ZIP_ARCHIVE_BYTES)


def _extract_item_zip(zip_path: Path, target_dir: Path, *, scanner_cls) -> Path:
    if zip_path.suffix.lower() != ".zip":
        raise ValueError(f"Zip path expected, got: {zip_path}")
    ensure_path_size(zip_path, max_bytes=MAX_ZIP_ARCHIVE_BYTES, label="zip archive")
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        _safe_extract_zip(archive, target_dir)

    direct_candidate = scanner_cls.detect_item_root(target_dir)
    if direct_candidate is not None:
        return direct_candidate

    child_dirs = [path for path in sorted(target_dir.iterdir()) if path.is_dir() and not path.name.startswith(".")]
    if len(child_dirs) == 1:
        nested_candidate = scanner_cls.detect_item_root(child_dirs[0])
        if nested_candidate is not None:
            return nested_candidate

    unique_parents = sorted(
        {
            path.resolve()
            for path in target_dir.rglob("*")
            if path.is_dir() and "__MACOSX" not in path.parts and scanner_cls.detect_item_root(path) is not None
        }
    )
    if len(unique_parents) == 1:
        return unique_parents[0]
    if len(unique_parents) > 1:
        # Prefer the shallowest root — nested SKILL.md files belong to sub-skills inside a
        # team-skill and should not be treated as competing roots.
        min_depth = min(len(p.relative_to(target_dir).parts) for p in unique_parents)
        shallowest = [p for p in unique_parents if len(p.relative_to(target_dir).parts) == min_depth]
        if len(shallowest) == 1:
            return shallowest[0]
        pretty = ", ".join(str(p.relative_to(target_dir)) for p in shallowest[:5])
        raise ValueError(
            "Zip archive contains multiple item roots at the same level; unable to choose one: "
            f"{pretty}"
        )
    raise ValueError(f"Zip archive does not contain a valid {scanner_cls.item_type} root: {zip_path}")


def _safe_extract_zip(archive: zipfile.ZipFile, target_dir: Path) -> None:
    target_root = target_dir.resolve()
    members = archive.infolist()
    if len(members) > MAX_ZIP_MEMBERS:
        raise ValueError(f"Zip archive contains too many members: {len(members)} > {MAX_ZIP_MEMBERS}")
    total_uncompressed = 0
    for member in members:
        member_name = str(member.filename or "").replace("\\", "/")
        member_path = Path(member_name)
        if not member_name or member_path.is_absolute() or any(part == ".." for part in member_path.parts):
            raise ValueError(f"Unsafe zip member path: {member.filename}")
        if member.file_size > MAX_ZIP_MEMBER_BYTES:
            raise ValueError(
                f"Zip member is too large: {member.filename} "
                f"({member.file_size} bytes > {MAX_ZIP_MEMBER_BYTES} bytes)"
            )
        total_uncompressed += int(member.file_size)
        if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ValueError(
                "Zip archive expands beyond the configured size budget: "
                f"{total_uncompressed} bytes > {MAX_ZIP_UNCOMPRESSED_BYTES} bytes"
            )
        destination = (target_root / member_path).resolve()
        if destination != target_root and target_root not in destination.parents:
            raise ValueError(f"Zip member escapes target directory: {member.filename}")
        file_mode = (member.external_attr >> 16) & 0o170000
        if file_mode == stat.S_IFLNK:
            raise ValueError(f"Zip member symlinks are not supported: {member.filename}")
        if member.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, destination.open("wb") as target:
            shutil.copyfileobj(source, target)


def _validate_item_dir(path: Path, *, scanner_cls) -> Path:
    candidate = scanner_cls.detect_item_root(path)
    if candidate is None:
        raise ValueError(f"Item directory does not contain a valid {scanner_cls.item_type} root: {path}")
    return candidate


def _normalize_manifest_item_path(value: str | Path) -> str:
    raw = str(value).strip()
    if is_s3_uri(raw):
        return raw
    return str(Path(raw).expanduser().resolve())


def _build_root_tag_mapping(
    *,
    item_paths: Sequence[str],
    output_dir: Path,
    resolved_config: ResolvedBuildConfig,
    item_type: str,
    require_llm: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if require_llm and not can_build_tree_with_llm(resolved_config):
        raise ValueError("build_tags requires LLM capability, but no LLM config is available")

    with tempfile.TemporaryDirectory(prefix="finder-root-tag-build-") as tmpdir:
        work_dir = Path(tmpdir)
        aggregate_dir = work_dir / "skills"
        aggregate_dir.mkdir(parents=True, exist_ok=True)
        resolved_item_paths = _resolve_materialized_item_paths(item_paths, work_dir=work_dir, item_type=item_type)
        _IndexBuildWorkflow.materialize_skill_dirs(aggregate_dir, resolved_item_paths)
        _IndexBuildWorkflow.write_item_metadata(
            aggregate_dir,
            resolved_item_paths,
            resolved_config.item_metadata_by_path,
        )

        scanned = create_scanner(item_type, aggregate_dir, display_items_dir=aggregate_dir).to_dict_list()
        source_by_skill = {
            _unique_dir_name(item.source_path, item.materialized_dir.name): item.source_path
            for item in resolved_item_paths
        }
        for worker_id, item in ((str(entry.get("id") or ""), entry) for entry in scanned):
            source_path = source_by_skill.get(worker_id)
            if source_path:
                item["path"] = source_path
            if "__" in worker_id and str(item.get("name", "")) == worker_id:
                item["name"] = worker_id.split("__", 1)[1]

        groups = normalize_root_categories(resolved_config.tree_root_categories)
        if can_build_tree_with_llm(resolved_config):
            manager_config = TreeManagerConfig(
                branching_factor=resolved_config.tree_branching_factor,
                max_depth=resolved_config.tree_max_depth,
                root_categories=groups,
                build=TreeBuildConfig(
                    max_workers=resolved_config.tree_max_workers,
                    caching=resolved_config.tree_caching,
                    num_retries=resolved_config.tree_num_retries,
                    timeout=resolved_config.tree_timeout_seconds,
                    context_window=resolved_config.tree_context_window,
                    max_output_tokens=resolved_config.tree_max_output_tokens,
                    postprocess_enabled=resolved_config.tree_postprocess_enabled,
                    postprocess_max_passes=resolved_config.tree_postprocess_max_passes,
                    postprocess_min_skills=resolved_config.tree_postprocess_min_skills,
                    equiv_grouping_enabled=resolved_config.tree_equiv_grouping_enabled,
                    equiv_max_groups_per_parent=resolved_config.tree_equiv_max_groups_per_parent,
                    equiv_allow_singleton_groups=resolved_config.tree_equiv_allow_singleton_groups,
                    equiv_min_lexical_similarity=resolved_config.tree_equiv_min_lexical_similarity,
                    deterministic_prompts=resolved_config.tree_deterministic_prompts,
                    discovery_seed=resolved_config.tree_discovery_seed,
                    prompt_fingerprint_version=resolved_config.tree_prompt_fingerprint_version,
                    cache_observability=resolved_config.tree_cache_observability,
                ),
            )
            assignments, resolved_groups = TreeBuilder.classify_root_tags_with_llm(
                skills=list(scanned),
                manager_config=manager_config,
                model=resolved_config.llm_model,
                api_key=resolved_config.tree_llm_api_key,
                base_url=resolved_config.tree_llm_base_url,
                client=resolved_config.llm_openai_client,
                llm_seed=resolved_config.llm_seed,
                max_workers=resolved_config.tree_max_workers,
                root_categories=groups,
                skills_dir=aggregate_dir,
                output_path=work_dir / "_root_tag_only.yaml",
                item_type=item_type,
                verbose=False,
            )
        else:
            resolved_groups = {
                cat_id: {
                    "name": str(meta.get("name") or cat_id),
                    "description": str(meta.get("description") or ""),
                }
                for cat_id, meta in (groups or FIXED_ROOT_CATEGORIES).items()
            }
            fallback_tag = next(iter(resolved_groups), "uncategorized")
            assignments = {
                str(item.get("id") or ""): fallback_tag
                for item in scanned
                if str(item.get("id") or "").strip()
            }

        rows: list[dict[str, str]] = []
        for item in sorted(scanned, key=lambda entry: str(entry.get("id") or "")):
            worker_id = str(item.get("id") or "").strip()
            if not worker_id:
                continue
            root_tag_id = str(assignments.get(worker_id) or "").strip()
            if not root_tag_id:
                continue
            group = resolved_groups.get(root_tag_id) or {}
            rows.append(
                {
                    "worker_id": worker_id,
                    "skill_name": str(item.get("name") or worker_id).strip(),
                    "skill_path": str(item.get("path") or item.get("skill_path") or "").strip(),
                    "root_tag_id": root_tag_id,
                    "root_tag_name": str(group.get("name") or root_tag_id).strip(),
                    "root_tag_description": str(group.get("description") or "").strip(),
                }
            )
        _write_jsonl_rows(output_dir / _SKILLS_TAG_MAPPING_FILENAME, rows)


def _write_jsonl_rows(path: Path, rows: Sequence[dict[str, str]]) -> None:
    lines = [json.dumps(dict(row), ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


class _IndexBuildWorkflow:
    def __init__(self, *, item_paths: Sequence[str], output_dir: Path,
                 resolved_config: ResolvedBuildConfig, item_type: str) -> None:
        self._item_paths = [str(path).strip() for path in item_paths if str(path).strip()]
        self._output_dir = output_dir.resolve()
        self._config = resolved_config
        self._item_type = normalize_item_type(item_type)

    def build(self) -> Path:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="finder-index-build-") as tmpdir:
            aggregate_dir = Path(tmpdir) / "skills"
            aggregate_dir.mkdir(parents=True, exist_ok=True)
            resolved_item_paths = _resolve_materialized_item_paths(
                self._item_paths, work_dir=Path(tmpdir), item_type=self._item_type)
            self.materialize_skill_dirs(aggregate_dir, resolved_item_paths)
            self.write_item_metadata(aggregate_dir, resolved_item_paths, self._config.item_metadata_by_path)

            tree_output_path = self._output_dir / TREE_INDEX_FILENAME
            if can_build_tree_with_llm(self._config):
                build_tree(
                    BuildTreeRequest(
                        skills_dir=aggregate_dir,
                        output_path=tree_output_path,
                        config=DynamicTreeConfig(
                            branching_factor=self._config.tree_branching_factor,
                            max_depth=self._config.tree_max_depth,
                            root_categories=normalize_root_categories(self._config.tree_root_categories),
                        ),
                        manager_config=TreeManagerConfig(
                            branching_factor=self._config.tree_branching_factor,
                            max_depth=self._config.tree_max_depth,
                            root_categories=normalize_root_categories(self._config.tree_root_categories),
                            build=TreeBuildConfig(
                                max_workers=self._config.tree_max_workers,
                                caching=self._config.tree_caching,
                                num_retries=self._config.tree_num_retries,
                                timeout=self._config.tree_timeout_seconds,
                                context_window=self._config.tree_context_window,
                                max_output_tokens=self._config.tree_max_output_tokens,
                                postprocess_enabled=self._config.tree_postprocess_enabled,
                                postprocess_max_passes=self._config.tree_postprocess_max_passes,
                                postprocess_min_skills=self._config.tree_postprocess_min_skills,
                                equiv_grouping_enabled=self._config.tree_equiv_grouping_enabled,
                                equiv_max_groups_per_parent=self._config.tree_equiv_max_groups_per_parent,
                                equiv_allow_singleton_groups=self._config.tree_equiv_allow_singleton_groups,
                                equiv_min_lexical_similarity=self._config.tree_equiv_min_lexical_similarity,
                                deterministic_prompts=self._config.tree_deterministic_prompts,
                                discovery_seed=self._config.tree_discovery_seed,
                                prompt_fingerprint_version=self._config.tree_prompt_fingerprint_version,
                                cache_observability=self._config.tree_cache_observability,
                            ),
                        ),
                        client=self._config.llm_openai_client,
                        model=self._config.llm_model,
                        api_key=self._config.tree_llm_api_key,
                        base_url=self._config.tree_llm_base_url,
                        llm_seed=self._config.llm_seed,
                        max_workers=self._config.tree_max_workers,
                        verbose=False,
                        show_tree=False,
                        generate_html=self._config.generate_tree_html,
                        display_skills_dir=self._infer_display_skills_dir(resolved_item_paths),
                        item_type=self._item_type,
                    )
                )
            else:
                if not self._config.allow_fallback_tree:
                    raise ValueError(
                        "Tree build requested but no LLM capability is configured and fallback is disabled")
                build_fallback_tree_index(aggregate_dir=aggregate_dir, output_path=tree_output_path)

            catalog_records = self._build_catalog_records(
                aggregate_dir,
                tree_output_path,
                resolved_item_paths=resolved_item_paths,
            )
            nodes = enrich_branch_descriptions(
                load_tree_preset(tree_output_path).get("nodes") or [],
                catalog_records=catalog_records)
            write_tree_preset({"nodes": nodes}, tree_output_path)
            if self._config.generate_tree_html:
                generate_tree_html(
                    tree_nodes_to_tree_dict(
                        nodes,
                        catalog_records),
                    self._output_dir /
                    TREE_HTML_FILENAME)
            else:
                self._unlink_if_exists(self._output_dir / TREE_HTML_FILENAME)
            write_catalog(catalog_records, self._output_dir / CATALOG_FILENAME)
            if self._config.method & BuildMethod.EMBEDDING:
                embedding_records = write_embedding_records(
                    catalog_records, self._output_dir / EMBEDDING_RECORDS_FILENAME)
                build_embedding_artifact(
                    embedding_records,
                    self._output_dir / EMBEDDING_INDEX_FILENAME,
                    **self._embedding_build_kwargs(),
                )
            else:
                self._unlink_if_exists(self._output_dir / EMBEDDING_RECORDS_FILENAME)
                self._unlink_if_exists(self._output_dir / EMBEDDING_INDEX_FILENAME)
            if self._config.method & BuildMethod.BM25:
                build_bm25_artifact(catalog_records, self._output_dir / BM25_INDEX_FILENAME)
            else:
                self._unlink_if_exists(self._output_dir / BM25_INDEX_FILENAME)
            write_manifest(self._output_dir, self._item_paths, catalog_records, mode="full", item_type=self._item_type)
        return self._output_dir

    def _to_public_build_config(self) -> BuildConfig:
        return BuildConfig(
            method=self._config.method,
            llm_openai_client=self._config.llm_openai_client,
            llm_model=self._config.llm_model,
            llm_seed=self._config.llm_seed,
            embedding_openai_client=self._config.embedding_openai_client,
            embedding_model=self._config.embedding_model,
            embedding_batch_size=self._config.embedding_batch_size,
            tree_branching_factor=self._config.tree_branching_factor,
            tree_max_depth=self._config.tree_max_depth,
            tree_root_categories=self._config.tree_root_categories,
            tree_max_workers=self._config.tree_max_workers,
            tree_caching=self._config.tree_caching,
            tree_num_retries=self._config.tree_num_retries,
            tree_timeout_seconds=self._config.tree_timeout_seconds,
            tree_context_window=self._config.tree_context_window,
            tree_max_output_tokens=self._config.tree_max_output_tokens,
            tree_postprocess_enabled=self._config.tree_postprocess_enabled,
            tree_postprocess_max_passes=self._config.tree_postprocess_max_passes,
            tree_postprocess_min_skills=self._config.tree_postprocess_min_skills,
            tree_equiv_grouping_enabled=self._config.tree_equiv_grouping_enabled,
            tree_equiv_max_groups_per_parent=self._config.tree_equiv_max_groups_per_parent,
            tree_equiv_allow_singleton_groups=self._config.tree_equiv_allow_singleton_groups,
            tree_equiv_min_lexical_similarity=self._config.tree_equiv_min_lexical_similarity,
            tree_deterministic_prompts=self._config.tree_deterministic_prompts,
            tree_discovery_seed=self._config.tree_discovery_seed,
            tree_prompt_fingerprint_version=self._config.tree_prompt_fingerprint_version,
            tree_cache_observability=self._config.tree_cache_observability,
            generate_tree_html=self._config.generate_tree_html,
            allow_fallback_tree=self._config.allow_fallback_tree,
            item_metadata_by_path=self._config.item_metadata_by_path,
        )

    def _to_embedding_runtime_config(self) -> IndexBuildRuntimeConfig:
        return IndexBuildRuntimeConfig(
            embedding_api_base_url=self._config.embedding_api_base_url,
            embedding_api_key=self._config.embedding_api_key,
            embedding_model=self._config.embedding_model,
            embedding_batch_size=self._config.embedding_batch_size,
        )

    def _embedding_build_kwargs(self) -> dict[str, BuildConfig | IndexBuildRuntimeConfig]:
        if self._config.embedding_openai_client is not None:
            return {"config": self._to_public_build_config()}
        return {"runtime_config": self._to_embedding_runtime_config()}

    @staticmethod
    def _unlink_if_exists(path: Path) -> None:
        if path.exists():
            path.unlink()

    @staticmethod
    def materialize_skill_dirs(aggregate_dir: Path, item_paths: Sequence[ResolvedItemPath]) -> None:
        for item in item_paths:
            skill_dir = item.materialized_dir
            # Skill zips use a nested layout: <prefix>/plugin.yaml + <prefix>/<name>/SKILL.md.
            # _extract_item_zip returns the inner dir (containing SKILL.md) as item_root,
            # which breaks scanner's path.parent search for plugin.yaml after materialization.
            # Copy plugin.yaml from the outer dir into item_root so the scanner can find it.
            # Only do this for zip-extracted items — local_dir items point at the user's own
            # source tree where writing would be an unwanted side effect, and local dirs use
            # the canonical layout where scanner's path.parent search already works.
            if item.source_type.endswith("_zip"):
                for fn in ("plugin.yaml", "plugin.yml", "plugin.json"):
                    src = skill_dir.parent / fn
                    if src.is_file() and not (skill_dir / fn).exists():
                        shutil.copy2(src, skill_dir / fn)
                        break
            dir_name = _unique_dir_name(item.source_path, skill_dir.name)
            destination = aggregate_dir / dir_name
            if destination.exists():
                logger.warning("duplicate skill directory name during materialization, skipping: %s", dir_name)
                continue
            try:
                destination.symlink_to(skill_dir, target_is_directory=True)
            except Exception:
                shutil.copytree(skill_dir, destination)

    @staticmethod
    def write_item_metadata(
        aggregate_dir: Path,
        item_paths: Sequence[ResolvedItemPath],
        metadata_by_path: dict[str, dict[str, object]] | None,
    ) -> None:
        if not metadata_by_path:
            return
        rows: list[dict[str, object]] = []
        for item in item_paths:
            metadata = metadata_by_path.get(item.source_path)
            if not metadata:
                continue
            rows.append(
                {
                    "id": _unique_dir_name(item.source_path, item.materialized_dir.name),
                    **metadata,
                }
            )
        if rows:
            (aggregate_dir / "skills.json").write_text(
                json.dumps({"skills": rows}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    @staticmethod
    def _infer_display_skills_dir(item_paths: Sequence[ResolvedItemPath]) -> Path | None:
        local_dirs = [
            item.materialized_dir.parent.resolve()
            for item in item_paths
            if item.source_type == "local_dir"
        ]
        if not local_dirs:
            return None
        parents = set(local_dirs)
        return next(iter(parents)) if len(parents) == 1 else None

    def _build_catalog_records(
        self,
        aggregate_dir: Path,
        tree_output_path: Path,
        *,
        resolved_item_paths: Sequence[ResolvedItemPath],
    ):
        source_by_skill = {
            _unique_dir_name(item.source_path, item.materialized_dir.name): item.source_path
            for item in resolved_item_paths
        }
        scanned = {
            str(item["id"]): item
            for item in create_scanner(self._item_type, aggregate_dir, display_items_dir=aggregate_dir).to_dict_list()
        }
        for worker_id, item in scanned.items():
            source_path = source_by_skill.get(worker_id)
            if source_path:
                item["path"] = source_path
            # Strip publisher prefix from name when scanner fell back to dir name
            if "__" in worker_id and str(item.get("name", "")) == worker_id:
                item["name"] = worker_id.split("__", 1)[1]
        return build_catalog_records_from_nodes(nodes=load_tree_preset(tree_output_path).get("nodes") or [], scanned_skills=scanned)


class _IncrementalIndexBuildWorkflow(_IndexBuildWorkflow):
    def __init__(
        self,
        *,
        item_paths: Sequence[str],
        output_dir: Path,
        resolved_config: ResolvedBuildConfig,
        base_index_dir: Path,
        added_paths: Sequence[str],
        removed_paths: Sequence[str],
        item_type: str,
    ) -> None:
        super().__init__(
            item_paths=item_paths,
            output_dir=output_dir,
            resolved_config=resolved_config,
            item_type=item_type,
        )
        self._base_index_dir = base_index_dir.resolve()
        self._added_paths = [str(path).strip() for path in added_paths if str(path).strip()]
        self._removed_paths = [str(path).strip() for path in removed_paths if str(path).strip()]

    def build(self) -> Path:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        if not (self._base_index_dir / TREE_INDEX_FILENAME).exists():
            return super().build()

        existing_catalog = load_catalog_records(self._base_index_dir / CATALOG_FILENAME)
        existing_nodes = list(load_tree_preset(self._base_index_dir / TREE_INDEX_FILENAME).get("nodes") or [])
        existing_embedding_index = safe_load_embedding_index(self._base_index_dir / EMBEDDING_INDEX_FILENAME)
        existing_bm25_index = safe_load_bm25_index(self._base_index_dir / BM25_INDEX_FILENAME)

        remaining_paths = set(self._item_paths)
        removed_path_set = set(self._removed_paths)
        kept_catalog = []
        for record in existing_catalog:
            normalized_path = _normalize_manifest_item_path(record.skill_path)
            if normalized_path in remaining_paths and normalized_path not in removed_path_set:
                kept_catalog.append(record)

        if self._added_paths:
            scanned_added = self._scan_added_skills()
            existing_nodes = merge_added_skills_into_tree(nodes=existing_nodes, added_skills=scanned_added)
            added_catalog = build_catalog_records_from_nodes(
                nodes=existing_nodes,
                scanned_skills=scanned_added,
                restrict_worker_ids=set(scanned_added),
            )
            merged = {record.worker_id: record for record in kept_catalog}
            for record in added_catalog:
                merged[record.worker_id] = record
            catalog_records = sorted(merged.values(), key=lambda item: item.cid)
        else:
            removed_worker_ids = {
                record.worker_id
                for record in existing_catalog
                if _normalize_manifest_item_path(record.skill_path) in removed_path_set
            }
            existing_nodes = prune_deleted_skills_from_tree(existing_nodes, removed_worker_ids=removed_worker_ids)
            catalog_records = sorted(kept_catalog, key=lambda item: item.cid)

        worker_to_record = {record.worker_id: record for record in catalog_records}
        nodes = align_leaf_nodes_with_catalog(existing_nodes, worker_to_record)
        catalog_records = build_catalog_records_from_existing(nodes=nodes, records_by_worker=worker_to_record)
        nodes = enrich_branch_descriptions(nodes, catalog_records=catalog_records)

        write_tree_preset({"nodes": nodes}, self._output_dir / TREE_INDEX_FILENAME)
        if self._config.generate_tree_html:
            generate_tree_html(tree_nodes_to_tree_dict(nodes, catalog_records), self._output_dir / TREE_HTML_FILENAME)
        else:
            self._unlink_if_exists(self._output_dir / TREE_HTML_FILENAME)
        write_catalog(catalog_records, self._output_dir / CATALOG_FILENAME)
        if self._config.method & BuildMethod.EMBEDDING:
            embedding_records = write_embedding_records(catalog_records, self._output_dir / EMBEDDING_RECORDS_FILENAME)
            build_embedding_artifact_incremental(
                records=embedding_records,
                output_path=self._output_dir / EMBEDDING_INDEX_FILENAME,
                existing_index=existing_embedding_index,
                **self._embedding_build_kwargs(),
            )
        else:
            self._unlink_if_exists(self._output_dir / EMBEDDING_RECORDS_FILENAME)
            self._unlink_if_exists(self._output_dir / EMBEDDING_INDEX_FILENAME)
        if self._config.method & BuildMethod.BM25:
            build_bm25_artifact_incremental(
                records=catalog_records,
                output_path=self._output_dir / BM25_INDEX_FILENAME,
                existing_index=existing_bm25_index,
            )
        else:
            self._unlink_if_exists(self._output_dir / BM25_INDEX_FILENAME)
        write_manifest(
            self._output_dir,
            self._item_paths,
            catalog_records,
            mode="incremental",
            item_type=self._item_type)
        return self._output_dir

    def _scan_added_skills(self) -> dict[str, dict]:
        scanned: dict[str, dict] = {}
        with tempfile.TemporaryDirectory(prefix="finder-index-added-items-") as tmpdir:
            resolved_item_paths = _resolve_materialized_item_paths(
                self._added_paths, work_dir=Path(tmpdir), item_type=self._item_type)
            for item in resolved_item_paths:
                skill_dir = item.materialized_dir
                scan_root = skill_dir.parent
                scanner = create_scanner(self._item_type, scan_root, display_items_dir=scan_root)
                for scanned_item in scanner.to_dict_list():
                    if str(scanned_item.get("id") or "") == skill_dir.name:
                        scanned[skill_dir.name] = scanned_item
                        if not is_s3_uri(item.source_path):
                            scanned[skill_dir.name]["path"] = _normalize_manifest_item_path(item.source_path)
                        else:
                            scanned[skill_dir.name]["path"] = item.source_path
                        break
        return scanned


__all__ = ["IndexBuilder", "_IndexBuildWorkflow", "_IncrementalIndexBuildWorkflow"]
