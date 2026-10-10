# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Full index rebuild: queries DB for valid item_paths, calls IndexBuilder.build,
broadcasts index:reload to Redis, and GCs old index versions on OBS.

Index OBS structure (per group):
  {group_prefix}/{YYYYMMDDH}/manifest.json
  {group_prefix}/{YYYYMMDDH}/...

  skill example:  skills-index/2026042117/manifest.json
  plugin example: plugins-index/2026042117/manifest.json

group_prefix is configurable via:
  MARKET_RETRIEVAL_SKILL_INDEX_OBS_PREFIX  (default: skills-index)
  MARKET_RETRIEVAL_PLUGIN_INDEX_OBS_PREFIX (default: plugins-index)

Rollback: up to _MAX_INDEX_VERSIONS successful builds are kept on OBS.
If a build fails the in-memory index is unchanged; on restart warm-start
loads the latest existing dir (which is always the last successful build).
"""

import json
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError
from retrieval.indexing.workflows.artifacts import BuildMethod, IndexBuildRuntimeConfig
from plugins_market.core.logging import get_logger
from plugins_market.retrieval.groups import agent_retrieval_group, scanner_type_for_group

logger = get_logger(__name__)

SKILL_GROUP = "skill"
PLUGIN_GROUP = "plugin"
_SKILL_LIKE_PLUGIN_TYPES = ("skill", "swarmskill")
_PLUGIN_TYPES = ("tools", "mcp-stdio", "restful-api")
_MAX_INDEX_VERSIONS = 168  # fallback default; overridden at call site via settings
_RELOAD_STREAM = "index:reload"
_MATERIALIZED_ITEM_INDEX_RE = re.compile(r"item-(\d+)\.zip")
_CN_TZ = ZoneInfo("Asia/Shanghai")
_SKILL_TAG_MAPPING_FILENAME = "skills_tag_mapping.jsonl"
_SKILL_TAG_DELTA_FILENAME = "skills_tag_mapping.delta.jsonl"
_SKILL_TAG_SNAPSHOT_FILENAME = "skills_tag_mapping.snapshot.jsonl"
_SKILL_TAG_PREFIX = "skills-tag"
_PLUGIN_TAG_PREFIX = "plugins-tag"
_OBS_SKILL_ASSET_ID_RE = re.compile(r"^(?:obs|s3)://[^/]+/skills/[^/]+/([^/]+)/")
_MANIFEST_FILENAME = "manifest.json"


def _storage_uri_scheme(storage) -> str:
    """Return 's3' for MinIO, 'obs' for OBS (default). Does not affect existing OBS paths."""
    storage_type = getattr(getattr(storage, "config", None), "storage_type", "OBS")
    return "s3" if str(storage_type).upper() == "MINIO" else "obs"


@dataclass
class SkillTagRefreshOptions:
    """Parameters for refresh_skill_tags (G.FNM.03: named bundle instead of long arg list)."""

    db_factory: Any
    skill_prefix: str
    storage: Any
    redis_client: Any | None = None
    build_config: Any | None = None
    skill_tag_build_config: Any | None = None
    runtime_config: Any | None = None
    skip_lock: bool = False


@dataclass
class AgentTagRefreshOptions:
    """Parameters for the three independent Agent asset classification groups."""

    db_factory: Any
    agent_prefixes: Dict[str, str]
    storage: Any
    redis_client: Any | None = None
    build_config: Any | None = None
    skill_tag_build_config: Any | None = None
    runtime_config: Any | None = None
    skip_lock: bool = False


@dataclass(frozen=True)
class IndexItemRecord:
    item_path: str
    metadata: Dict[str, object]


def _index_dir_name() -> str:
    """Return datetime-to-second string for index dir, e.g. '20260422010203'."""
    return datetime.now(_CN_TZ).strftime("%Y%m%d%H%M%S")


def _fetch_valid_item_records(db, group: str, bucket_name: str, uri_scheme: str = "obs") -> List[IndexItemRecord]:
    """Return storage zip URIs and market metadata for non-OFFLINE, latest-version plugins in *group*."""
    from sqlalchemy import and_

    from plugins_market.models.market_assets import MarketAssetDB
    from plugins_market.core.viewer_context import ANONYMOUS_VIEWER
    from plugins_market.repositories.market_assets_repository import skill_moderation_list_clause

    agent_group = agent_retrieval_group(group)
    if group == SKILL_GROUP:
        type_filter = MarketAssetDB.plugin_type.in_(list(_SKILL_LIKE_PLUGIN_TYPES))
        moderation_filter = and_(type_filter, skill_moderation_list_clause(ANONYMOUS_VIEWER))
    elif agent_group is not None:
        type_filter = and_(
            MarketAssetDB.asset_type == agent_group.asset_type,
            MarketAssetDB.plugin_type == agent_group.asset_type,
        )
        moderation_filter = and_(type_filter, skill_moderation_list_clause(ANONYMOUS_VIEWER))
    else:
        type_filter = MarketAssetDB.plugin_type.in_(list(_PLUGIN_TYPES))
        moderation_filter = type_filter

    rows = (
        db.query(
            MarketAssetDB.asset_id,
            MarketAssetDB.asset_type,
            MarketAssetDB.publisher_id,
            MarketAssetDB.name,
            MarketAssetDB.latest_version,
            MarketAssetDB.public_latest_version,
            MarketAssetDB.plugin_type,
            MarketAssetDB.display_name,
            MarketAssetDB.short_desc,
            MarketAssetDB.detail_desc,
            MarketAssetDB.tags,
        )
        .filter(
            MarketAssetDB.status != "OFFLINE",
            moderation_filter,
        )
        .all()
    )

    records: List[IndexItemRecord] = []
    for row in rows:
        _row_is_skill_like = row.plugin_type in _SKILL_LIKE_PLUGIN_TYPES
        root = agent_group.storage_root if agent_group is not None else ("skills" if _row_is_skill_like else "plugins")
        safe_name = row.name.strip().replace(" ", "-")
        if agent_group is not None:
            eff = (row.public_latest_version or "").strip()
            if not eff:
                continue
        elif _row_is_skill_like:
            eff = (row.public_latest_version or row.latest_version or "").strip()
            if not eff:
                continue
        else:
            eff = (row.latest_version or "").strip()
            if not eff:
                continue
        key = f"{root}/{row.publisher_id}/{row.asset_id}" f"/{eff}/{safe_name}_{eff}.zip"
        item_path = f"{uri_scheme}://{bucket_name}/{key}"
        records.append(
            IndexItemRecord(
                item_path=item_path,
                metadata={
                    "asset_id": row.asset_id,
                    "asset_type": row.asset_type,
                    "name": row.name,
                    "display_name": row.display_name,
                    "short_desc": row.short_desc or "",
                    "detail_desc": row.detail_desc or "",
                    "tags": list(row.tags or []),
                    "plugin_type": row.plugin_type,
                    "version": eff,
                },
            )
        )
    return records


def _fetch_valid_item_paths(db, group: str, bucket_name: str, uri_scheme: str = "obs") -> List[str]:
    """Return storage zip URIs for non-OFFLINE, latest-version plugins in *group*."""
    return [record.item_path for record in _fetch_valid_item_records(db, group, bucket_name, uri_scheme=uri_scheme)]


def _build_config_with_item_metadata(build_config, metadata_by_path: Dict[str, Dict[str, object]]):
    if build_config is None:
        return None
    try:
        return replace(build_config, item_metadata_by_path=metadata_by_path)
    except TypeError:
        logger.warning("BuildConfig does not support item_metadata_by_path; market metadata will not be indexed")
        return build_config


def list_index_dirs(storage, group_prefix: str) -> List[str]:
    """List index dir prefixes under group_prefix on OBS, sorted newest-first.

    Returns paths like ["skills-index/2026042117", "skills-index/2026042116"].
    """
    prefix = group_prefix.rstrip("/") + "/"
    all_keys = storage.list_keys(prefix)
    dirs: set = set()
    for key in all_keys:
        rest = key[len(prefix):]
        slash = rest.find("/")
        if slash > 0:
            dirs.add(f"{group_prefix.rstrip('/')}/{rest[:slash]}")
    return sorted(dirs, reverse=True)


def _gc_old_indexes(storage, group_prefix: str, max_versions: int = _MAX_INDEX_VERSIONS) -> None:
    """Delete oldest index dirs on OBS beyond max_versions."""
    dirs = list_index_dirs(storage, group_prefix)
    for old_dir in dirs[max_versions:]:
        result = storage.delete_prefix(old_dir + "/")
        if result.get("success"):
            logger.info("GC old OBS index: %s", old_dir)
        else:
            logger.warning("GC failed %s: %s", old_dir, result.get("errors"))


def _extract_failed_item_index(exc: Exception) -> Optional[int]:
    """Parse temp materialized zip name like item-3.zip from build errors."""
    match = _MATERIALIZED_ITEM_INDEX_RE.search(str(exc))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _obs_uri_join(base_uri: str, leaf_name: str) -> str:
    return f"{base_uri.rstrip('/')}/{leaf_name.lstrip('/')}"


def _tag_prefix_for_group(group: str) -> str:
    agent_group = agent_retrieval_group(group)
    if agent_group is not None:
        return agent_group.tag_prefix
    return _SKILL_TAG_PREFIX if group == SKILL_GROUP else _PLUGIN_TAG_PREFIX


def _build_tag_output_uri(bucket_name: str, group: str, *, dir_name: str | None = None, uri_scheme: str = "obs") -> str:
    tag_prefix = _tag_prefix_for_group(group).rstrip("/")
    target_dir_name = (dir_name or "").strip() or _index_dir_name()
    return f"{uri_scheme}://{bucket_name}/{tag_prefix}/{target_dir_name}"


def _extract_asset_id_from_obs_skill_path(skill_path: str) -> Optional[str]:
    m = _OBS_SKILL_ASSET_ID_RE.match(str(skill_path or "").strip())
    if not m:
        return None
    return m.group(1)


def _read_obs_text(storage, obs_uri: str) -> str:
    key = storage.resolve_object_key(obs_uri)
    if not key:
        raise ValueError(f"failed to resolve object key from uri={obs_uri}")
    resp = storage.s3_client.get_object(Bucket=storage.config.bucket_name, Key=key)
    body = resp.get("Body")
    if body is None:
        raise RuntimeError(f"object body empty: {obs_uri}")
    with body:
        return body.read().decode("utf-8")


def _write_obs_text(storage, obs_uri: str, text: str) -> None:
    key = storage.resolve_object_key(obs_uri)
    if not key:
        raise ValueError(f"failed to resolve object key from uri={obs_uri}")
    storage.s3_client.put_object(
        Bucket=storage.config.bucket_name,
        Key=key,
        Body=(text or "").encode("utf-8"),
        ContentType="application/json",
    )


def _parse_jsonl_rows(text: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for line_no, line in enumerate((text or "").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("skip invalid jsonl line=%d: %s", line_no, exc)
            continue
        if not isinstance(row, dict):
            continue
        normalized = {str(k): str(v) for k, v in row.items()}
        rows.append(normalized)
    return rows


def _jsonl_text_from_rows(rows: List[Dict[str, str]]) -> str:
    lines = [json.dumps(dict(row), ensure_ascii=False) for row in rows]
    return "\n".join(lines) + ("\n" if lines else "")


def _tag_row_key(row: Dict[str, str]) -> str:
    worker_id = str(row.get("worker_id") or "").strip()
    if worker_id:
        return worker_id
    return str(row.get("skill_path") or "").strip()


def _load_previous_tag_snapshot_rows(
    *,
    storage,
    bucket_name: str,
    group: str,
    current_dir_name: str,
    uri_scheme: str = "obs",
) -> List[Dict[str, str]]:
    tag_prefix = _tag_prefix_for_group(group)
    for tag_dir in list_index_dirs(storage, tag_prefix):
        dir_name = str(tag_dir).rstrip("/").split("/")[-1]
        if not dir_name or dir_name == current_dir_name:
            continue
        snapshot_uri = f"{uri_scheme}://{bucket_name}/{tag_dir.rstrip('/')}/{_SKILL_TAG_SNAPSHOT_FILENAME}"
        try:
            text = _read_obs_text(storage, snapshot_uri)
            return _parse_jsonl_rows(text)
        except Exception:
            logger.warning("previous tag snapshot unreadable or missing (try next)")
            continue
    return []


def _load_manifest_item_paths(storage, manifest_uri: str) -> List[str]:
    try:
        text = _read_obs_text(storage, manifest_uri)
        obj = json.loads(text)
        paths = obj.get("item_paths") if isinstance(obj, dict) else None
        if not isinstance(paths, list):
            return []
        return [str(p).strip() for p in paths if str(p).strip()]
    except Exception as exc:
        logger.warning("load manifest item paths failed: uri=%s err=%s", manifest_uri, exc)
        return []


def _load_latest_index_manifest_item_paths(
    storage,
    group_prefix: str,
    bucket_name: str,
    uri_scheme: str = "obs",
) -> List[str]:
    dirs = list_index_dirs(storage, group_prefix)
    if not dirs:
        return []
    manifest_uri = f"{uri_scheme}://{bucket_name}/{dirs[0].rstrip('/')}/{_MANIFEST_FILENAME}"
    return _load_manifest_item_paths(storage, manifest_uri)


def _parse_skill_category_mapping_jsonl(text: str) -> Dict[str, Dict[str, str]]:
    mapping: Dict[str, Dict[str, str]] = {}
    for line_no, line in enumerate((text or "").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        row = None
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("skip invalid skill category mapping line=%d: %s", line_no, exc)
        if not isinstance(row, dict):
            continue
        asset_id = _extract_asset_id_from_obs_skill_path(str(row.get("skill_path") or ""))
        if not asset_id:
            continue
        category_id = str(row.get("root_tag_id") or "").strip()
        category_name = str(row.get("root_tag_name") or "").strip()
        if not category_id:
            continue
        mapping[asset_id] = {
            "category_id": category_id,
            "category_name": category_name,
        }
    return mapping


def _extract_asset_id_from_agent_path(item_path: str, group: str) -> Optional[str]:
    spec = agent_retrieval_group(group)
    if spec is None:
        return None
    pattern = rf"^(?:obs|s3)://[^/]+/{re.escape(spec.storage_root)}/[^/]+/([^/]+)/"
    match = re.match(pattern, str(item_path or "").strip())
    return match.group(1) if match else None


def _parse_agent_category_mapping_jsonl(text: str, group: str) -> Dict[str, Dict[str, str]]:
    mapping: Dict[str, Dict[str, str]] = {}
    for row in _parse_jsonl_rows(text):
        asset_id = _extract_asset_id_from_agent_path(row.get("skill_path", ""), group)
        category_id = str(row.get("root_tag_id") or "").strip()
        if asset_id and category_id:
            mapping[asset_id] = {
                "category_id": category_id,
                "category_name": str(row.get("root_tag_name") or "").strip(),
            }
    return mapping


def _fetch_uncategorized_skill_paths(db, item_paths: List[str]) -> set[str]:
    from plugins_market.models.market_assets import MarketAssetDB
    from sqlalchemy import or_

    asset_ids = {aid for aid in (_extract_asset_id_from_obs_skill_path(path) for path in item_paths) if aid}
    if not asset_ids:
        return set()
    rows = (
        db.query(MarketAssetDB.asset_id)
        .filter(
            MarketAssetDB.asset_id.in_(list(asset_ids)),
            MarketAssetDB.plugin_type.in_(list(_SKILL_LIKE_PLUGIN_TYPES)),
            MarketAssetDB.status != "OFFLINE",
            or_(MarketAssetDB.latest_version.isnot(None), MarketAssetDB.public_latest_version.isnot(None)),
            or_(MarketAssetDB.category_id.is_(None), MarketAssetDB.category_name.is_(None)),
        )
        .all()
    )
    uncategorized_ids = {str(row.asset_id) for row in rows}
    return {path for path in item_paths if (_extract_asset_id_from_obs_skill_path(path) in uncategorized_ids)}


def _fetch_uncategorized_agent_paths(db, group: str, item_paths: List[str]) -> set[str]:
    from sqlalchemy import or_

    from plugins_market.models.market_assets import MarketAssetDB

    asset_ids = {
        asset_id
        for asset_id in (_extract_asset_id_from_agent_path(path, group) for path in item_paths)
        if asset_id
    }
    if not asset_ids:
        return set()
    rows = (
        db.query(MarketAssetDB.asset_id)
        .filter(
            MarketAssetDB.asset_id.in_(list(asset_ids)),
            MarketAssetDB.asset_type == group,
            MarketAssetDB.plugin_type == group,
            MarketAssetDB.status != "OFFLINE",
            MarketAssetDB.public_latest_version.isnot(None),
            or_(MarketAssetDB.category_id.is_(None), MarketAssetDB.category_name.is_(None)),
        )
        .all()
    )
    uncategorized_ids = {str(row.asset_id) for row in rows}
    return {
        path
        for path in item_paths
        if _extract_asset_id_from_agent_path(path, group) in uncategorized_ids
    }


def _select_skill_paths_for_incremental_classification(
    *,
    current_item_paths: List[str],
    previous_item_paths: List[str],
    uncategorized_paths: set[str],
) -> List[str]:
    current_set = set(current_item_paths)
    if not previous_item_paths:
        # Cold start / no previous index manifest: classify all current items.
        return list(current_item_paths)

    previous_set = set(previous_item_paths)
    changed_or_new = current_set - previous_set
    targets = changed_or_new | uncategorized_paths
    if not targets:
        return []
    # Keep deterministic order from current inputs.
    return [path for path in current_item_paths if path in targets]


def _refresh_skill_categories_from_mapping(db, item_paths: List[str], mapping: Dict[str, Dict[str, str]]) -> None:
    from plugins_market.models.market_assets import MarketAssetDB

    candidate_asset_ids = {aid for aid in (_extract_asset_id_from_obs_skill_path(path) for path in item_paths) if aid}
    if not candidate_asset_ids:
        logger.warning("skill category refresh skipped: no candidate asset ids")
        return

    mapped_ids = set(mapping.keys())
    try:
        # Clear stale category fields for assets covered by this classification batch
        # but not present in mapping.
        stale_ids = candidate_asset_ids - mapped_ids
        if stale_ids:
            (
                db.query(MarketAssetDB)
                .filter(
                    MarketAssetDB.asset_id.in_(list(stale_ids)),
                    MarketAssetDB.plugin_type.in_(list(_SKILL_LIKE_PLUGIN_TYPES)),
                    MarketAssetDB.status != "OFFLINE",
                )
                .update(
                    {
                        MarketAssetDB.category_id: None,
                        MarketAssetDB.category_name: None,
                    },
                    synchronize_session=False,
                )
            )

        if mapped_ids:
            updates = [
                {
                    "asset_id": asset_id,
                    "category_id": data["category_id"],
                    "category_name": data["category_name"] or None,
                }
                for asset_id, data in mapping.items()
                if asset_id in candidate_asset_ids
            ]
            if updates:
                db.bulk_update_mappings(MarketAssetDB, updates)
        db.commit()
        logger.info(
            "skill category refresh done: candidates=%d mapped=%d stale_cleared=%d",
            len(candidate_asset_ids),
            len(mapped_ids & candidate_asset_ids),
            len(candidate_asset_ids - mapped_ids),
        )
    except Exception as exc:
        db.rollback()
        logger.warning("skill category refresh skipped due to DB error: %s", exc)


def _refresh_agent_categories_from_mapping(
    db,
    group: str,
    item_paths: List[str],
    mapping: Dict[str, Dict[str, str]],
) -> None:
    from plugins_market.models.market_assets import MarketAssetDB

    candidate_ids = {
        asset_id
        for asset_id in (_extract_asset_id_from_agent_path(path, group) for path in item_paths)
        if asset_id
    }
    if not candidate_ids:
        return
    eligible_rows = (
        db.query(MarketAssetDB.asset_id)
        .filter(
            MarketAssetDB.asset_id.in_(list(candidate_ids)),
            MarketAssetDB.asset_type == group,
            MarketAssetDB.plugin_type == group,
            MarketAssetDB.status != "OFFLINE",
        )
        .all()
    )
    eligible_ids = {str(row.asset_id) for row in eligible_rows}
    mapped_ids = eligible_ids & set(mapping)
    try:
        stale_ids = eligible_ids - mapped_ids
        if stale_ids:
            (
                db.query(MarketAssetDB)
                .filter(
                    MarketAssetDB.asset_id.in_(list(stale_ids)),
                    MarketAssetDB.asset_type == group,
                    MarketAssetDB.plugin_type == group,
                    MarketAssetDB.status != "OFFLINE",
                )
                .update(
                    {MarketAssetDB.category_id: None, MarketAssetDB.category_name: None},
                    synchronize_session=False,
                )
            )
        if mapped_ids:
            db.bulk_update_mappings(
                MarketAssetDB,
                [
                    {
                        "asset_id": asset_id,
                        "category_id": mapping[asset_id]["category_id"],
                        "category_name": mapping[asset_id]["category_name"] or None,
                    }
                    for asset_id in mapped_ids
                ],
            )
        db.commit()
        logger.info(
            "agent category refresh done: group=%s candidates=%d mapped=%d stale_cleared=%d",
            group,
            len(eligible_ids),
            len(mapped_ids),
            len(stale_ids),
        )
    except Exception as exc:
        db.rollback()
        logger.warning("agent category refresh skipped group=%s due to DB error: %s", group, exc)


def _build_skill_tag_runtime_config(tag_config, runtime_config):
    if tag_config is not None and getattr(tag_config, "llm_openai_client", None) is not None:
        return IndexBuildRuntimeConfig(
            build_method="tree",
            tree_llm_model=str(getattr(tag_config, "llm_model", "") or ""),
            tree_llm_api_key=str(getattr(tag_config.llm_openai_client, "api_key", "") or ""),
            tree_llm_base_url=str(getattr(tag_config.llm_openai_client, "base_url", "") or ""),
        )
    if runtime_config is not None:
        return IndexBuildRuntimeConfig(
            build_method="tree",
            tree_llm_model=str(getattr(runtime_config, "tree_llm_model", "") or ""),
            tree_llm_api_key=str(getattr(runtime_config, "tree_llm_api_key", "") or ""),
            tree_llm_base_url=str(getattr(runtime_config, "tree_llm_base_url", "") or ""),
        )
    return None


def _run_skill_tag_refresh(
    *,
    group: str,
    db,
    storage,
    group_prefix: str,
    output_tag_uri: str,
    current_item_paths: List[str],
    build_config,
    skill_tag_build_config,
    runtime_config,
    uri_scheme: str = "obs",
    item_metadata_by_path: Dict[str, Dict[str, object]] | None = None,
) -> None:
    try:
        from indexing.workflows.index_builder import IndexBuilder  # type: ignore[import]
    except ImportError:
        logger.warning("retrieval module not importable — skill tag refresh skipped")
        return

    bucket_name = storage.config.bucket_name
    previous_item_paths = _load_latest_index_manifest_item_paths(
        storage,
        group_prefix,
        bucket_name,
        uri_scheme=uri_scheme,
    )
    if group == SKILL_GROUP:
        uncategorized_paths = _fetch_uncategorized_skill_paths(db, current_item_paths)
    elif agent_retrieval_group(group) is not None:
        uncategorized_paths = _fetch_uncategorized_agent_paths(db, group, current_item_paths)
    else:
        uncategorized_paths = set()
    classify_paths = _select_skill_paths_for_incremental_classification(
        current_item_paths=current_item_paths,
        previous_item_paths=previous_item_paths,
        uncategorized_paths=uncategorized_paths,
    )
    if not classify_paths:
        logger.debug("skill category incremental: no changed/uncategorized items, skip classification")
        return

    logger.debug(
        "skill category incremental: classify=%d total=%d prev=%d uncategorized=%d",
        len(classify_paths),
        len(current_item_paths),
        len(previous_item_paths),
        len(uncategorized_paths),
    )
    t_tags = time.monotonic()
    logger.debug("skill category: starting build_skill_tags for %d items", len(classify_paths))
    tag_config = skill_tag_build_config or build_config
    if agent_retrieval_group(group) is not None and tag_config is not None:
        # Agent 分组：分类走 tree 构建，LLM 取自 tag_config 本身；
        # resolve_build_config 不允许 config 与 runtime_config 同传，
        # 因此必须显式带上 TREE 位（tag_config 的 method 是索引构建策略，默认不含 TREE），
        # 否则 can_build_tree_with_llm 校验失败、分类被静默跳过。
        agent_tag_config = _build_config_with_item_metadata(
            replace(tag_config, method=BuildMethod.TREE),
            item_metadata_by_path or {},
        )
    else:
        agent_tag_config = None
    tag_runtime_config = (
        None if agent_tag_config is not None else _build_skill_tag_runtime_config(tag_config, runtime_config)
    )
    try:
        IndexBuilder.build_skill_tags(
            classify_paths,
            output_tag_uri,
            item_type=scanner_type_for_group(group),
            config=agent_tag_config,
            runtime_config=tag_runtime_config,
            require_llm=True,
        )
    except Exception as exc:
        logger.info("skill category: build_skill_tags skipped: %s", exc)
        return
    elapsed_tags = time.monotonic() - t_tags
    logger.info("skill category: build_skill_tags completed in %.1fs", elapsed_tags)

    current_dir_name = output_tag_uri.rstrip("/").split("/")[-1]
    bucket_name = storage.config.bucket_name
    tag_mapping_uri = _obs_uri_join(output_tag_uri, _SKILL_TAG_MAPPING_FILENAME)
    delta_rows = _parse_jsonl_rows(_read_obs_text(storage, tag_mapping_uri))
    delta_text = _jsonl_text_from_rows(delta_rows)

    # Persist delta artifact for auditing.
    delta_uri = _obs_uri_join(output_tag_uri, _SKILL_TAG_DELTA_FILENAME)
    _write_obs_text(storage, delta_uri, delta_text)

    # Build versioned full snapshot by merging previous snapshot + current delta.
    prev_rows = _load_previous_tag_snapshot_rows(
        storage=storage,
        bucket_name=bucket_name,
        group=group,
        current_dir_name=current_dir_name,
        uri_scheme=uri_scheme,
    )
    merged_by_key: Dict[str, Dict[str, str]] = {}
    for row in prev_rows:
        key = _tag_row_key(row)
        if key:
            merged_by_key[key] = row
    for row in delta_rows:
        key = _tag_row_key(row)
        if key:
            merged_by_key[key] = row
    snapshot_rows = [merged_by_key[key] for key in sorted(merged_by_key.keys())]
    snapshot_text = _jsonl_text_from_rows(snapshot_rows)
    snapshot_uri = _obs_uri_join(output_tag_uri, _SKILL_TAG_SNAPSHOT_FILENAME)
    _write_obs_text(storage, snapshot_uri, snapshot_text)
    # Keep legacy filename as snapshot for backward compatibility.
    _write_obs_text(storage, tag_mapping_uri, snapshot_text)

    if group == SKILL_GROUP:
        skill_category_mapping = _parse_skill_category_mapping_jsonl(delta_text)
        if skill_category_mapping:
            _refresh_skill_categories_from_mapping(db, classify_paths, skill_category_mapping)
            logger.info("skill category: refreshed categories for %d items", len(skill_category_mapping))
        else:
            # 有待分类技能却拿不到任何映射：通常是 LLM 调用失败被 call_llm 内部吞掉，
            # 任务表面"成功"实则空跑。skill-tag 为必选功能，这里按失败大声报错，
            # 不再静默 warning，便于发现 skill-tag LLM 端点/密钥运行时失效等问题。
            logger.error(
                "skill-tag 分类失败：本次有 %d 个待分类技能但 LLM 未产出任何映射，"
                "首页类别展示将缺失；请检查 skill-tag LLM 端点/密钥是否有效可达。mapping=%s",
                len(classify_paths),
                tag_mapping_uri,
            )
    elif agent_retrieval_group(group) is not None:
        agent_category_mapping = _parse_agent_category_mapping_jsonl(delta_text, group)
        if agent_category_mapping:
            _refresh_agent_categories_from_mapping(db, group, classify_paths, agent_category_mapping)
            logger.info(
                "agent category: refreshed group=%s categories for %d items",
                group,
                len(agent_category_mapping),
            )
        else:
            logger.error(
                "agent 分类失败：group=%s 本次有 %d 个待分类资产但 LLM 未产出任何映射，"
                "mapping=%s",
                group,
                len(classify_paths),
                tag_mapping_uri,
            )


def rebuild_one_group(
    group: str,
    db,
    group_prefix: str,
    storage,
    index_manager=None,
    redis_client=None,
    build_config=None,
    skill_tag_build_config=None,
    runtime_config=None,
    max_index_versions: int = _MAX_INDEX_VERSIONS,
    run_skill_tag: bool = True,
    uri_scheme: str = "obs",
) -> Optional[str]:
    """Full rebuild for one index group. Returns new OBS index URI or None on failure.

    group_prefix: OBS key prefix for this group, e.g. "skills-index" or "plugins-index".
    Output path:  obs://{bucket}/{group_prefix}/{YYYYMMDDH}/
    build_config: BuildConfig for index building (embedding+bm25).
    skill_tag_build_config: Separate BuildConfig with LLM for skill tag classification.
    runtime_config: IndexBuildRuntimeConfig fallback (used only when matching BuildConfig is absent).
    """
    try:
        from indexing.workflows.index_builder import IndexBuilder  # type: ignore[import]
    except ImportError:
        logger.warning("retrieval module not importable — rebuild skipped for group=%s", group)
        return None

    bucket_name = storage.config.bucket_name
    t0 = time.monotonic()
    item_records = _fetch_valid_item_records(db, group, bucket_name, uri_scheme=uri_scheme)
    item_paths = [record.item_path for record in item_records]
    item_metadata_by_path = {record.item_path: record.metadata for record in item_records}
    logger.info("rebuild_one_group: group=%s items=%d", group, len(item_paths))

    if not item_paths:
        logger.warning("rebuild_one_group: no items for group=%s, skipping", group)
        return None

    output_dir = f"{uri_scheme}://{bucket_name}/{group_prefix.rstrip('/')}/{_index_dir_name()}"

    build_inputs = list(item_paths)
    new_path = None
    while build_inputs:
        try:
            effective_runtime_config = None if build_config is not None else runtime_config
            effective_build_config = _build_config_with_item_metadata(
                build_config,
                {path: item_metadata_by_path[path] for path in build_inputs if path in item_metadata_by_path},
            )
            new_path = IndexBuilder.build(
                build_inputs,
                output_dir,
                item_type=scanner_type_for_group(group),
                config=effective_build_config,
                runtime_config=effective_runtime_config,
            )
            break
        except Exception as exc:
            bad_index = _extract_failed_item_index(exc)
            if bad_index is None:
                logger.error("IndexBuilder.build failed group=%s: %s", group, exc, exc_info=True)
                return None
            if bad_index < 0 or bad_index >= len(build_inputs):
                logger.error(
                    "IndexBuilder.build failed group=%s with invalid bad_index=%s (inputs=%d): %s",
                    group,
                    bad_index,
                    len(build_inputs),
                    exc,
                    exc_info=True,
                )
                return None
            if len(build_inputs) == 1:
                logger.error(
                    "IndexBuilder.build failed group=%s and last candidate is invalid path=%s: %s",
                    group,
                    build_inputs[0],
                    exc,
                    exc_info=True,
                )
                return None
            bad_path = build_inputs.pop(bad_index)
            logger.error(
                "IndexBuilder.build failed group=%s due to bad package idx=%s path=%s; "
                "skip it and retry with remaining=%d",
                group,
                bad_index,
                bad_path,
                len(build_inputs),
            )

    if new_path is None:
        logger.error("IndexBuilder.build failed: all items exhausted for group=%s", group)
        return None

    new_path_str = str(new_path)
    elapsed = time.monotonic() - t0
    logger.info("rebuild done group=%s path=%s elapsed=%.1fs", group, new_path_str, elapsed)

    if run_skill_tag:
        try:
            tag_output_uri = _build_tag_output_uri(
                bucket_name,
                group,
                dir_name=new_path_str.rstrip("/").split("/")[-1],
                uri_scheme=uri_scheme,
            )
            _run_skill_tag_refresh(
                group=group,
                db=db,
                storage=storage,
                group_prefix=group_prefix,
                output_tag_uri=tag_output_uri,
                current_item_paths=build_inputs,
                build_config=build_config,
                skill_tag_build_config=skill_tag_build_config,
                runtime_config=runtime_config,
                uri_scheme=uri_scheme,
                item_metadata_by_path=item_metadata_by_path,
            )
        except Exception as exc:
            # Category build/refresh failure should not break index rebuild availability.
            logger.warning("skill category build/refresh skipped: %s", exc, exc_info=True)

    _gc_old_indexes(storage, group_prefix, max_versions=max_index_versions)

    if index_manager is not None:
        index_manager.load(group, new_path_str)

    if redis_client is not None:
        try:
            redis_client.xadd(_RELOAD_STREAM, {"group": group, "index_path": new_path_str})
        except Exception as exc:
            logger.warning("broadcast reload failed group=%s: %s", group, exc)

    return new_path_str


_REBUILD_LOCK_KEY = "retrieval:rebuild:lock"
_SKILL_TAG_LOCK_KEY = "retrieval:skill-tag:lock"
_AGENT_TAG_LOCK_KEY = "retrieval:agent-tag:lock"
_REBUILD_LOCK_TTL_SECONDS = 2400  # 40-minute upper bound for a full rebuild run


def rebuild_all(
    db_factory,
    skill_prefix: str,
    plugin_prefix: str,
    storage,
    index_manager=None,
    redis_client=None,
    build_config=None,
    skill_tag_build_config=None,
    runtime_config=None,
    max_index_versions: int = _MAX_INDEX_VERSIONS,
    skip_lock: bool = False,
    run_skill_tag: bool = True,
    agent_prefixes: Dict[str, str] | None = None,
) -> None:
    """Rebuild both index groups. Called from thread-pool by the scheduled job.

    When Redis is available a SET NX distributed lock ensures only one instance
    runs the rebuild at a time (multi-worker / multi-pod deployments). The lock
    expires automatically after _REBUILD_LOCK_TTL_SECONDS so a crashed holder
    does not block future runs.

    skip_lock=True: bypass the distributed lock entirely (used for startup rebuild
    so a stale lock from a crashed previous process does not block the new run).

    build_config: Config for index building (embedding+bm25).
    skill_tag_build_config: Separate config with LLM for skill tag classification.
    runtime_config: IndexBuildRuntimeConfig fallback when BuildConfig is not provided.
    run_skill_tag: whether to run skill tag classification in the same pass.
    """
    lock_acquired = False
    if redis_client is not None and not skip_lock:
        lock_acquired = bool(redis_client.set(_REBUILD_LOCK_KEY, "1", nx=True, ex=_REBUILD_LOCK_TTL_SECONDS))
        if not lock_acquired:
            logger.info("rebuild_all: another instance holds the rebuild lock, skipping this run")
            return

    try:
        uri_scheme = _storage_uri_scheme(storage)
        groups = [(SKILL_GROUP, skill_prefix), (PLUGIN_GROUP, plugin_prefix)]
        groups.extend((agent_prefixes or {}).items())
        for group, prefix in groups:
            db = db_factory()
            try:
                rebuild_one_group(
                    group,
                    db,
                    prefix,
                    storage,
                    index_manager,
                    redis_client,
                    build_config,
                    skill_tag_build_config,
                    runtime_config,
                    max_index_versions,
                    run_skill_tag=run_skill_tag,
                    uri_scheme=uri_scheme,
                )
            except Exception as exc:
                logger.error(
                    "rebuild_all: group=%s failed unexpectedly, continuing with next group: %s",
                    group,
                    exc,
                    exc_info=True,
                )
            finally:
                db.close()
    finally:
        if redis_client is not None and lock_acquired:
            try:
                redis_client.delete(_REBUILD_LOCK_KEY)
            except Exception as exc:
                logger.warning("rebuild_all: failed to release rebuild lock: %s", exc)


def refresh_skill_tags(options: SkillTagRefreshOptions) -> None:
    """Refresh skill tag/category mapping without rebuilding indexes."""
    db_factory = options.db_factory
    skill_prefix = options.skill_prefix
    storage = options.storage
    redis_client = options.redis_client
    build_config = options.build_config
    skill_tag_build_config = options.skill_tag_build_config
    runtime_config = options.runtime_config
    skip_lock = options.skip_lock

    lock_acquired = False
    if redis_client is not None and not skip_lock:
        lock_acquired = bool(redis_client.set(_SKILL_TAG_LOCK_KEY, "1", nx=True, ex=_REBUILD_LOCK_TTL_SECONDS))
        if not lock_acquired:
            logger.info("refresh_skill_tags: another instance holds the lock, skipping this run")
            return

    try:
        db = db_factory()
        try:
            bucket_name = storage.config.bucket_name
            uri_scheme = _storage_uri_scheme(storage)
            current_item_paths = _fetch_valid_item_paths(db, SKILL_GROUP, bucket_name, uri_scheme=uri_scheme)
            if not current_item_paths:
                logger.info("refresh_skill_tags: no skill items, skip")
                return

            dirs = list_index_dirs(storage, skill_prefix)
            if not dirs:
                logger.warning("refresh_skill_tags: no skill index found under prefix=%s, skip", skill_prefix)
                return

            latest_index_dir_name = dirs[0].rstrip("/").split("/")[-1]
            tag_output_uri = _build_tag_output_uri(
                bucket_name,
                SKILL_GROUP,
                dir_name=latest_index_dir_name,
                uri_scheme=uri_scheme,
            )
            _run_skill_tag_refresh(
                group=SKILL_GROUP,
                db=db,
                storage=storage,
                group_prefix=skill_prefix,
                output_tag_uri=tag_output_uri,
                current_item_paths=current_item_paths,
                build_config=build_config,
                skill_tag_build_config=skill_tag_build_config,
                runtime_config=runtime_config,
                uri_scheme=uri_scheme,
            )
        finally:
            db.close()
    finally:
        if redis_client is not None and lock_acquired:
            try:
                redis_client.delete(_SKILL_TAG_LOCK_KEY)
            except Exception as exc:
                logger.warning("refresh_skill_tags: failed to release lock: %s", exc)


def refresh_agent_tags(options: AgentTagRefreshOptions) -> None:
    """Refresh classification for each Agent asset type without touching Skill state."""
    lock_acquired = False
    if options.redis_client is not None and not options.skip_lock:
        lock_acquired = bool(
            options.redis_client.set(
                _AGENT_TAG_LOCK_KEY,
                "1",
                nx=True,
                ex=_REBUILD_LOCK_TTL_SECONDS,
            )
        )
        if not lock_acquired:
            logger.info("refresh_agent_tags: another instance holds the lock, skipping this run")
            return

    try:
        bucket_name = options.storage.config.bucket_name
        uri_scheme = _storage_uri_scheme(options.storage)
        for group, group_prefix in options.agent_prefixes.items():
            db = options.db_factory()
            try:
                records = _fetch_valid_item_records(db, group, bucket_name, uri_scheme=uri_scheme)
                if not records:
                    logger.info("refresh_agent_tags: no items for group=%s, skip", group)
                    continue
                dirs = list_index_dirs(options.storage, group_prefix)
                if not dirs:
                    logger.warning(
                        "refresh_agent_tags: no index found for group=%s prefix=%s, skip",
                        group,
                        group_prefix,
                    )
                    continue
                latest_dir_name = dirs[0].rstrip("/").split("/")[-1]
                item_paths = [record.item_path for record in records]
                _run_skill_tag_refresh(
                    group=group,
                    db=db,
                    storage=options.storage,
                    group_prefix=group_prefix,
                    output_tag_uri=_build_tag_output_uri(
                        bucket_name,
                        group,
                        dir_name=latest_dir_name,
                        uri_scheme=uri_scheme,
                    ),
                    current_item_paths=item_paths,
                    build_config=options.build_config,
                    skill_tag_build_config=options.skill_tag_build_config,
                    runtime_config=options.runtime_config,
                    uri_scheme=uri_scheme,
                    item_metadata_by_path={record.item_path: record.metadata for record in records},
                )
            except Exception as exc:
                logger.error("refresh_agent_tags: group=%s failed: %s", group, exc, exc_info=True)
            finally:
                db.close()
    finally:
        if options.redis_client is not None and lock_acquired:
            try:
                options.redis_client.delete(_AGENT_TAG_LOCK_KEY)
            except Exception as exc:
                logger.warning("refresh_agent_tags: failed to release lock: %s", exc)
