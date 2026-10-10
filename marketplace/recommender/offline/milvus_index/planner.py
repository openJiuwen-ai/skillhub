"""Diff MySQL active skills vs local Milvus index state."""

from __future__ import annotations

from dataclasses import dataclass

from recommender.offline.package_sync.db import ActiveSkillVersion

from .state import IndexState, IndexedAsset


@dataclass(frozen=True)
class IndexPlan:
    active: list[ActiveSkillVersion]
    to_upsert: list[ActiveSkillVersion]
    to_delete: list[str]  # asset_ids removed from catalog / went offline
    category_only: list[ActiveSkillVersion]  # category / plugin_type changed; reuse embedding


def content_needs_reindex(skill: ActiveSkillVersion, indexed: IndexedAsset | None) -> bool:
    if indexed is None:
        return True
    if indexed.version != skill.latest_version:
        return True
    db_sha = (skill.artifact_sha256 or "").strip()
    if db_sha and indexed.artifact_sha256 != db_sha:
        return True
    return False


def category_needs_update(skill: ActiveSkillVersion, indexed: IndexedAsset | None) -> bool:
    if indexed is None:
        return False
    return indexed.category_id != skill.normalized_category_id


def plugin_type_needs_update(skill: ActiveSkillVersion, indexed: IndexedAsset | None) -> bool:
    if indexed is None:
        return False
    return (indexed.plugin_type or "").strip().lower() != skill.normalized_plugin_type


def scalars_need_update(skill: ActiveSkillVersion, indexed: IndexedAsset | None) -> bool:
    """True when category_id or plugin_type changed and content fingerprint did not."""
    return category_needs_update(skill, indexed) or plugin_type_needs_update(skill, indexed)


def needs_reindex(skill: ActiveSkillVersion, indexed: IndexedAsset | None) -> bool:
    """True when content or scalar metadata requires a Milvus write."""
    if content_needs_reindex(skill, indexed):
        return True
    return scalars_need_update(skill, indexed)


def plan_incremental(
    active_skills: list[ActiveSkillVersion],
    state: IndexState,
) -> IndexPlan:
    active_by_id = {s.asset_id: s for s in active_skills}
    active_ids = set(active_by_id)

    to_upsert: list[ActiveSkillVersion] = []
    category_only: list[ActiveSkillVersion] = []
    for skill in active_skills:
        indexed = state.get(skill.asset_id)
        if content_needs_reindex(skill, indexed):
            to_upsert.append(skill)
        elif scalars_need_update(skill, indexed):
            category_only.append(skill)

    to_delete = sorted(asset_id for asset_id in state.assets if asset_id not in active_ids)

    return IndexPlan(
        active=active_skills,
        to_upsert=to_upsert,
        to_delete=to_delete,
        category_only=category_only,
    )


def plan_full(active_skills: list[ActiveSkillVersion]) -> IndexPlan:
    return IndexPlan(
        active=active_skills,
        to_upsert=list(active_skills),
        to_delete=[],
        category_only=[],
    )
