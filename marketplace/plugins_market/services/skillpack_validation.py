# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate thin SkillPacks without changing their ordinary Skill market type."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NoReturn

from sqlalchemy.orm import Session

from plugins_market.core.errors import PublishError
from plugins_market.core.viewer_context import ANONYMOUS_VIEWER
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.repositories import MarketAssetRepository, MarketAssetVersionRepository
from plugins_market.validation.base import require_string_field
from plugins_market.validation.constants import MAX_FILE_SIZE, MAX_YAML_BYTES
from plugins_market.validation.plugin_yaml import (
    safe_load_yaml,
    validate_plugin_yaml_bytes,
    validate_plugin_yaml_public,
)
from plugins_market.validation.types.skill import parse_skill_frontmatter, validate_skill_frontmatter
from plugins_market.validation.zip_utils import (
    DecompressCounter,
    open_zip_bytes,
    safe_read_zip_member,
    validate_zip_safety,
)

_LOCK_FILENAME = "skillpack.lock.json"
_LOCK_SCHEMA = "openjiuwen.skillpack.lock/v1"
_PACK_TAG = "openjiuwen-skillpack-v1"
# Keep a single request's DB/storage work bounded even for untrusted lock files.
_MAX_MEMBERS = 128
_SHA256 = re.compile(r"[0-9a-f]{64}")
ArtifactReader = Callable[[MarketAssetDB, MarketAssetVersionDB], bytes]


def _invalid(message: str, *, member: str | None = None) -> NoReturn:
    raise PublishError(
        code=400,
        error="invalid_skillpack",
        message=f"SkillPack 成员 {member!r}：{message}" if member else f"SkillPack：{message}",
        data={"member": member} if member else None,
        error_code="SKILLHUB_SKILLPACK_INVALID",
        error_class="validation",
    )


@dataclass(frozen=True)
class _SkillDocument:
    front: dict[str, Any]
    lock: bytes | None
    marked: bool
    name: str
    version: str


def _archive_document(content: bytes, name: str) -> _SkillDocument:
    """Read only the manifest-declared content root, never the first nested SKILL.md."""
    if len(content) > MAX_FILE_SIZE:
        _invalid("原始制品超过大小限制")
    with open_zip_bytes(content) as archive:
        validate_zip_safety(archive)
        paths: dict[str, str] = {}
        for info in archive.infolist():
            normalized = info.filename.replace("\\", "/").rstrip("/")
            if normalized in paths or "." in normalized.split("/"):
                _invalid("制品包含重复或不明确的文件路径")
            paths[normalized] = info.filename
        manifests = [path for path in paths if path.count("/") == 1 and path.endswith("/plugin.yaml")]
        if len(manifests) != 1:
            _invalid("制品必须包含唯一的外层 plugin.yaml")
        counter = DecompressCounter()
        manifest_text = validate_plugin_yaml_bytes(safe_read_zip_member(archive, paths[manifests[0]], counter))
        manifest = safe_load_yaml(manifest_text, context="plugin.yaml")
        public = validate_plugin_yaml_public(manifest)
        if public.name != name:
            _invalid("制品名称与成员或 Pack 身份不一致")
        if public.runtime_type != "skill":
            _invalid("制品不是普通 Skill 包装")
        root = f"{manifests[0].rsplit('/', 1)[0]}/{name}/"
        skill_path = root + "SKILL.md"
        if skill_path not in paths:
            _invalid("制品缺少根 SKILL.md")
        front, _ = parse_skill_frontmatter(safe_read_zip_member(archive, paths[skill_path], counter))
        validate_skill_frontmatter(front, dir_name=name, yaml_name=name)
        if _kind(front) == "skillpack":
            allowed = {skill_path, root + _LOCK_FILENAME}
            if any(path.startswith(root) and not archive.getinfo(raw).is_dir() and path not in allowed
                   for path, raw in paths.items()):
                _invalid("Pack 内容目录只能包含根 SKILL.md 和 skillpack.lock.json")
        lock_path = root + _LOCK_FILENAME
        lock = None
        if lock_path in paths:
            if archive.getinfo(paths[lock_path]).file_size > MAX_YAML_BYTES:
                _invalid("成员锁定文件超过大小限制")
            # ZIP header sizes are untrusted; stop reading before an oversized
            # actual lock can be accumulated, independently of the total ZIP cap.
            lock = safe_read_zip_member(archive, paths[lock_path], DecompressCounter(MAX_YAML_BYTES))
            counter.add(len(lock))
        marked = _PACK_TAG in public.tags
        version_value = manifest.get("version")
        version = require_string_field(version_value, "version") if version_value is not None else ""
        return _SkillDocument(front, lock, marked, name, version)


def _kind(front: dict[str, Any]) -> str:
    raw = front.get("kind")
    if raw is None:
        return ""
    if not isinstance(raw, str):
        _invalid("SKILL.md 的 kind 必须是字符串")
    return raw.strip().lower()


def _identity(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        _invalid(f"{field} 必须为非空身份字符串")
    if value != value.strip() or len(value) > 256:
        _invalid(f"{field} 必须为非空身份字符串")
    if value in {".", ".."} or any(char in value for char in "/\\\x00") or any(ord(char) < 32 for char in value):
        _invalid(f"{field} 含有非法字符")
    return value


def _version(value: Any) -> str:
    version = _identity(value, "version")
    if version.lower() == "latest" or any(char in version for char in "^~<>=|*, "):
        _invalid("必须锁定精确版本，不能使用 latest 或版本范围")
    return version


def _unique_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _invalid("成员锁定文件含有重复 JSON 字段")
        result[key] = value
    return result


def _locked_members(document: _SkillDocument, version: str) -> list[dict[str, Any]]:
    names = document.front.get("skills")
    if not isinstance(names, list) or not 2 <= len(names) <= _MAX_MEMBERS:
        _invalid(f"skills 必须包含 2 至 {_MAX_MEMBERS} 个普通 Skill 成员")
    names = [_identity(name, "skills") for name in names]
    if len(set(names)) != len(names) or document.name in names:
        _invalid("skills 含有重复成员或引用自身")
    if document.lock is None:
        _invalid("缺少 skillpack.lock.json")
    try:
        lock = json.loads(document.lock, object_pairs_hook=_unique_json_keys)
    except (ValueError, RecursionError) as exc:
        raise PublishError(
            code=400, error="invalid_skillpack", message="SkillPack：成员锁定文件不是有效 JSON",
            error_code="SKILLHUB_SKILLPACK_INVALID", error_class="validation",
        ) from exc
    if not isinstance(lock, dict) or lock.get("schema_version") != _LOCK_SCHEMA:
        _invalid("不支持的成员锁定文件格式")
    pack = lock.get("pack")
    if not isinstance(pack, dict) or pack.get("name") != document.name:
        _invalid("lock 中的 Pack 名称与 SKILL.md 不一致")
    if _version(pack.get("version")) != version or document.version != version:
        _invalid("lock 中的 Pack 版本与发布版本不一致")
    members = lock.get("members")
    if not isinstance(members, list) or len(members) != len(names):
        _invalid("lock 成员数量与 SKILL.md 的 skills 不一致")
    identities: set[str] = set()
    for name, member in zip(names, members):
        if not isinstance(member, dict) or member.get("name") != name:
            _invalid("lock 成员名称或顺序与 SKILL.md 不一致")
        asset_id = _identity(member.get("asset_id"), "asset_id")
        _version(member.get("version"))
        digest = member.get("artifact_sha256")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            _invalid("artifact_sha256 必须是原始 ZIP 的 64 位 SHA-256", member=name)
        if asset_id in identities:
            _invalid("成员 asset_id 重复", member=name)
        identities.add(asset_id)
    return members


def _member_rows(db: Session, member: dict[str, Any]) -> tuple[MarketAssetDB, MarketAssetVersionDB]:
    asset = MarketAssetRepository(db).get_by_asset_id(member["asset_id"])
    if asset is None or asset.name != member["name"]:
        _invalid("指定的成员资产不存在或名称不一致", member=member["name"])
    if asset.plugin_type != "skill":
        _invalid("仅允许单 Agent 普通 Skill，不允许 SwarmSkill 或嵌套 SkillPack", member=member["name"])
    if asset.status != "PUBLISHED" or (asset.visibility or "public") != "public":
        _invalid("成员必须已上架且公开可下载", member=member["name"])
    version = MarketAssetVersionRepository(db).get_version(asset_id=asset.asset_id, version=member["version"])
    if version is None or version.status != "ACTIVE":
        _invalid("指定成员版本不存在或已删除", member=member["name"])
    if version.moderation_status not in {None, "", "APPROVED"}:
        _invalid("指定成员版本尚未通过审核", member=member["name"])
    if not ANONYMOUS_VIEWER.can_download_skill_asset(asset, db):
        _invalid("成员尚未公开或未通过审核", member=member["name"])
    if not ANONYMOUS_VIEWER.can_download_skill_version_row(asset, version, db):
        _invalid("指定成员版本尚未通过审核或不可公开下载", member=member["name"])
    return asset, version


def _is_pack(document: _SkillDocument) -> bool:
    if _kind(document.front) != "skillpack":
        if document.marked or document.lock is not None:
            _invalid("存在技能包标记或 lock，但根 kind 不是 skillpack")
        return False
    return True


def _validate_members(members: list[dict[str, Any]], db: Session, read_artifact: ArtifactReader) -> None:
    total_size = 0
    for member in members:
        asset, row = _member_rows(db, member)
        original = read_artifact(asset, row)
        total_size += len(original)
        if total_size > MAX_FILE_SIZE:
            _invalid("成员原始制品总量超过校验上限", member=member["name"])
        actual_digest = hashlib.sha256(original).hexdigest()
        if actual_digest != member["artifact_sha256"]:
            _invalid("成员原始 ZIP 摘要与 lock 不一致", member=member["name"])
        if row.artifact_sha256 and row.artifact_sha256 != actual_digest:
            _invalid("成员原始 ZIP 摘要与 Hub 版本记录不一致", member=member["name"])
        try:
            member_document = _archive_document(original, member["name"])
            kind = _kind(member_document.front)
        except PublishError:
            _invalid("成员原始制品结构无效", member=member["name"])
        if kind not in {"", "skill"} or member_document.marked or member_document.lock is not None:
            _invalid("成员内容不是单 Agent 普通 Skill", member=member["name"])
        if member_document.version != member["version"]:
            _invalid("成员制品版本与 lock 不一致", member=member["name"])


def validate_skillpack_for_publish(
    *, content: bytes, name: str, version: str, plugin_type: str,
    db: Session, read_artifact: ArtifactReader,
) -> None:
    """Run after normal ZIP validation and before uploading or approving a Skill."""
    if plugin_type != "skill":
        return
    document = _archive_document(content, name)
    if _is_pack(document):
        _validate_members(_locked_members(document, version), db, read_artifact)


def validate_stored_skillpack(
    *, asset: MarketAssetDB, version_row: MarketAssetVersionDB,
    db: Session, read_artifact: ArtifactReader,
) -> None:
    """Revalidate the stored candidate immediately before manual approval."""
    if asset.plugin_type != "skill":
        return
    original = read_artifact(asset, version_row)
    document = _archive_document(original, asset.name)
    if not _is_pack(document):
        return
    if version_row.artifact_sha256 and hashlib.sha256(original).hexdigest() != version_row.artifact_sha256:
        _invalid("待审核原始制品与版本记录的摘要不一致")
    _validate_members(_locked_members(document, version_row.version), db, read_artifact)
