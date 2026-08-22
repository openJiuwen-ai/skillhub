# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plugin publish, validation, and conflict handling."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from urllib.parse import urlparse
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from plugins_market.core.auth import AuthContext
from plugins_market.core.audit import (
    EVENT_SKILL_MODERATION,
    audit_log,
    list_skill_moderation_audit_logs_for_operator,
)
from plugins_market.core.audit_events import Action, ResourceType, Result
from plugins_market.core.context import _BJ_TZ, set_audit_hint
from plugins_market.core.errors import BusinessError, PublishError, http_error_payload
from plugins_market.core.moderation import (
    MODERATION_APPROVED,
    MODERATION_PENDING,
    MODERATION_REJECTED,
    is_skill_like_plugin_type,
    moderation_coalesce_display,
    normalize_skill_like_plugin_type,
)
from plugins_market.core.publish_result import (
    PUBLISH_RESULT_FAILED,
    PUBLISH_RESULT_PENDING_MODERATION,
    PUBLISH_RESULT_REVIEWING,
    PUBLISH_RESULT_SUCCESS,
    coalesce_skill_publish_result,
    initial_skill_publish_state,
    is_skill_asset_publicly_visible,
    is_skill_version_publicly_visible,
    is_skill_in_manual_moderation_stage,
)
from plugins_market.core.viewer_context import ANONYMOUS_VIEWER, ViewerContext
from plugins_market.core.logging import get_logger
from plugins_market.core.operation_log import bind_operation_resource, operation_context
from plugins_market.core.s3_storage_client import S3StorageClient
from plugins_market.core.skill_model_client import resolve_skill_review_model_config
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB, MarketSkillReviewDB
from plugins_market.repositories import (
    MarketAssetRepository,
    MarketAssetVersionRepository,
    MarketGroupSkillGrantRepository,
    MarketSkillReviewRepository,
    PluginFetchRecordRepository,
)
from plugins_market.repositories.market_assets_repository import parse_tag_filter
from plugins_market.schemas.plugin import (
    PluginDownloadData,
    PluginListItem,
    PluginListQuery,
    PluginListResponse,
    PluginPublishResult,
    PluginVersionDeleteData,
    PluginVersionDetail,
    SkillModerationAuditListItem,
    SkillModerationAuditListResponse,
    SkillModerationResult,
)
from plugins_market.services.site_notifications import (
    notify_publisher_skill_manual_review_approved,
    notify_publisher_skill_manual_review_rejected,
    notify_review_admins_new_skill_submission,
)
from plugins_market.core.config import settings
from plugins_market.retrieval.index_manager import get_index_manager
from plugins_market.retrieval.search import retrieval_search  # noqa: F401  (被测试 monkeypatch)
from plugins_market.validation import extract_plugin_metadata
from plugins_market.validation.constants import (
    MARKET_ASSET_SHORT_DESC_MAX_LEN,
    MARKET_VERSION_MAX_LEN,
    MAX_FILE_SIZE,
    RUNTIME_SKILL,
    VERSION_PATTERN,
    is_valid_market_version,
)
from plugins_market.validation.icon_png_optimize import optimize_png_icon_bytes
from plugins_market.services.skill_review import (
    REVIEW_STATUS_SYSTEM_FAILED,
    build_review_summary,
    initialize_skill_review,
)

logger = get_logger(__name__)


def _http_exception(status_code: int, message: str, *, error: str | None = None, details: Any = None) -> HTTPException:
    resolved_error = error
    if resolved_error is None:
        resolved_error = {
            (404, "Asset not found"): "plugin_not_found",
            (404, "Version not found"): "version_not_found",
            (404, "Package not found"): "plugin_package_not_found",
            (404, "No versions found for asset"): "version_not_found",
            (403, "Insufficient permissions"): "permission_denied",
            (502, "Object storage delete failed"): "storage_error",
        }.get((status_code, message))
    return HTTPException(
        status_code=status_code,
        detail=http_error_payload(
            status_code=status_code,
            message=message,
            error=resolved_error,
            details=details,
        ),
    )


def _strip_yaml_front_matter(markdown_text: str | None) -> str | None:
    """Remove leading YAML front matter block from markdown text."""
    if markdown_text is None:
        return None
    text = markdown_text.lstrip("\ufeff")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return markdown_text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "".join(lines[idx + 1:]).lstrip("\r\n")
    return markdown_text


def _detail_desc_for_display(plugin_type: str | None, detail_desc: str | None) -> str | None:
    if is_skill_like_plugin_type(plugin_type):
        return _strip_yaml_front_matter(detail_desc)
    return detail_desc


def _access_source_for_viewer(asset: MarketAssetDB, viewer: ViewerContext, db: Session | None = None) -> str:
    if is_skill_like_plugin_type(asset.plugin_type):
        return viewer.skill_asset_access_source(asset, db) or "public"
    return "public"


def _list_item_with_viewer_flag(
    item: PluginListItem, viewer: ViewerContext, asset: MarketAssetDB | None = None, db: Session | None = None
) -> PluginListItem:
    updates = {"viewer_is_market_moderation_admin": viewer.is_market_moderation_admin}
    if asset is not None:
        updates["access_source"] = _access_source_for_viewer(asset, viewer, db)
    return item.model_copy(update=updates)


def _is_system_admin_publisher(user_id: str) -> bool:
    return (user_id or "").strip() == (settings.system_admin_user or "").strip()


def _moderation_for_publish(*, user_id: str, plugin_type: str | None) -> tuple[str | None, str | None]:
    """非 skill-like 始终已通过；skill / swarmskill 由普通用户发布为审核中，系统管理员发布为通过。"""
    if not is_skill_like_plugin_type(plugin_type):
        return MODERATION_APPROVED, None
    if (user_id or "").strip() == (settings.system_admin_user or "").strip():
        return MODERATION_APPROVED, None
    return MODERATION_PENDING, None


def _resolved_version_publish_result_value(version_row: MarketAssetVersionDB | None) -> str | None:
    if version_row is None:
        return None
    return coalesce_skill_publish_result(
        getattr(version_row, "publish_result", None),
        getattr(version_row, "moderation_status", None),
    )


def _skill_moderation_result_from_version(
    *,
    asset_id: str,
    version_row: MarketAssetVersionDB,
) -> SkillModerationResult:
    """审核接口响应：返回本次操作针对的版本级状态，而非资产聚合态。"""
    return SkillModerationResult(
        asset_id=asset_id,
        moderation_status=moderation_coalesce_display(getattr(version_row, "moderation_status", None)),
        moderation_reject_reason=getattr(version_row, "moderation_reject_reason", None),
        publish_result=_resolved_version_publish_result_value(version_row),
        version=(version_row.version or "").strip() or None,
    )


def _latest_version_row_from_asset_versions(
    versions: list[MarketAssetVersionDB],
    latest_version: str | None,
) -> MarketAssetVersionDB | None:
    if not versions:
        return None
    lv = (latest_version or "").strip()
    if lv:
        for row in versions:
            if (row.version or "").strip() == lv:
                return row
    return max(versions, key=lambda row: (row.create_time or 0, row.version or ""))


def _apply_skill_asset_aggregate_from_versions(db: Session, asset_id: str) -> None:
    """
    按版本行重算 Skill 的 market_assets 聚合：moderation_status、moderation_reject_reason、
    public_latest_version、publish_result。非 skill-like 则视为已通过，public_latest 跟随 latest。
    调用方在事务内执行；不 commit。
    """
    # SessionLocal uses autoflush=False. Flush first so aggregate queries can see
    # the just-added / just-updated asset and version rows in the current transaction.
    db.flush()
    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)
    asset = asset_repo.get_by_asset_id(asset_id)
    if not asset:
        return
    if not is_skill_like_plugin_type(asset.plugin_type):
        asset.moderation_status = MODERATION_APPROVED
        asset.moderation_reject_reason = None
        asset.public_latest_version = asset.latest_version
        asset.publish_result = getattr(asset, "publish_result", None) or PUBLISH_RESULT_SUCCESS
        db.add(asset)
        return

    versions = version_repo.list_versions_chronological(asset_id)
    any_approved = False
    any_pending = False
    public_row: MarketAssetVersionDB | None = None
    latest_rejected: MarketAssetVersionDB | None = None

    for v in versions:
        ms = moderation_coalesce_display(getattr(v, "moderation_status", None))
        pr = _resolved_version_publish_result_value(v)
        if ms == MODERATION_APPROVED:
            any_approved = True
        elif ms == MODERATION_PENDING:
            any_pending = True
        elif ms == MODERATION_REJECTED:
            if latest_rejected is None or (v.create_time or 0) > (latest_rejected.create_time or 0):
                latest_rejected = v
        if pr == PUBLISH_RESULT_SUCCESS:
            if public_row is None:
                public_row = v
            else:
                ct = v.create_time or 0
                pct = public_row.create_time or 0
                if ct > pct or (ct == pct and (v.version or "") > (public_row.version or "")):
                    public_row = v

    if any_approved:
        asset.moderation_status = MODERATION_APPROVED
        asset.moderation_reject_reason = None
    elif any_pending:
        asset.moderation_status = MODERATION_PENDING
        asset.moderation_reject_reason = None
    else:
        asset.moderation_status = MODERATION_REJECTED
        asset.moderation_reject_reason = (latest_rejected.moderation_reject_reason or None) if latest_rejected else None

    asset.public_latest_version = public_row.version if public_row else None
    latest_version_row = _latest_version_row_from_asset_versions(versions, asset.latest_version)
    asset.publish_result = _resolved_version_publish_result_value(latest_version_row)
    db.add(asset)


def _normalize_version(version: str) -> str:
    """Trim whitespace; Git commit hex 归一为 7 位入库串。"""
    from ..validation.constants import normalize_market_version_for_storage

    return normalize_market_version_for_storage(version)


def _validate_version(version: str) -> None:
    """Ensure version is semver x.y.z or Git commit hex (git sync without SKILL version)."""
    if len((version or "").strip()) > MARKET_VERSION_MAX_LEN or not is_valid_market_version(version):
        raise PublishError(
            code=400,
            error="invalid_version",
            message=(
                "版本号格式错误：须为 x.y.z（如 1.0.0），不接受 v 前缀；"
                "或 Git commit 7 位小写十六进制，且长度不得超过 32 个字符"
            ),
            error_code="SKILLHUB_PLUGIN_VERSION_INVALID",
            error_class="validation",
        )


def _storage_root(plugin_type: str | None) -> str:
    """Top-level OBS prefix: skills for skill-like types, plugins for everything else."""
    return "skills" if is_skill_like_plugin_type(plugin_type) else "plugins"


def _version_dir_prefix(publisher_id: str, asset_id: str, version: str, plugin_type: str | None = None) -> str:
    """Version directory key prefix: {root}/{publisher_id}/{asset_id}/{version}/"""
    root = _storage_root(plugin_type)
    return f"{root}/{publisher_id}/{asset_id}/{version}/"


def _build_storage_path(
    *,
    publisher_id: str,
    asset_id: str,
    version: str,
    asset_name: str,
    plugin_type: str | None = None,
) -> str:
    """Build object-key for zip: {root}/{publisher_id}/{asset_id}/{version}/{name}_{version}.zip"""
    prefix = _version_dir_prefix(publisher_id, asset_id, version, plugin_type)
    safe_name = asset_name.strip().replace(" ", "-")
    return f"{prefix}{safe_name}_{version}.zip"


def _compute_checksum(content: bytes) -> str:
    """SHA256 of content (for future client checksum comparison)."""
    return hashlib.sha256(content).hexdigest()


def _publish_idempotent_same_artifact(
    existing_version: MarketAssetVersionDB | None,
    computed_sha256: str,
) -> bool:
    """同一 asset + version 且库内已记录相同子包 SHA-256 时跳过写存储（幂等重试）。"""
    if existing_version is None:
        return False
    if (existing_version.status or "").upper() != "ACTIVE":
        return False
    stored = (existing_version.artifact_sha256 or "").strip()
    if not stored:
        return False
    return stored.lower() == computed_sha256.lower()


def _validate_asset_name_immutable_for_skill(
    existing_asset: MarketAssetDB | None,
    package_name: str,
    package_plugin_type: str | None,
) -> None:
    if existing_asset is None:
        return
    if not (
        is_skill_like_plugin_type(getattr(existing_asset, "plugin_type", None))
        or is_skill_like_plugin_type(package_plugin_type)
    ):
        return
    existing_name = (existing_asset.name or "").strip()
    incoming_name = (package_name or "").strip()
    if existing_name == incoming_name:
        return
    raise BusinessError(
        code=422,
        error="skill_name_immutable",
        message=(
            "同一 Skill 的 plugin.yaml name 不允许在不同版本间修改；"
            f"当前资产 name 为 '{existing_name}'，本次包内 name 为 '{incoming_name}'"
        ),
        error_code="SKILLHUB_PLUGIN_SKILL_NAME_IMMUTABLE",
        error_class="validation",
    )


def _get_system_failed_review(
    db: Session,
    version_row: MarketAssetVersionDB,
) -> MarketSkillReviewDB | None:
    review = (
        db.query(MarketSkillReviewDB)
        .filter(
            MarketSkillReviewDB.asset_id == version_row.asset_id,
            MarketSkillReviewDB.version_id == version_row.version_id,
        )
        .first()
    )
    if review is None:
        return None
    if (review.review_status or "").strip() != REVIEW_STATUS_SYSTEM_FAILED:
        return None
    return review


def _restart_same_artifact_system_review_if_needed(
    db: Session,
    asset: MarketAssetDB,
    version_row: MarketAssetVersionDB,
    needs_skill_review: bool,
) -> bool:
    if not needs_skill_review:
        return False
    review = _get_system_failed_review(db, version_row)
    if review is None:
        return False
    ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    version_row.publish_result = PUBLISH_RESULT_REVIEWING
    version_row.moderation_status = MODERATION_PENDING
    version_row.moderation_reject_reason = None
    initialize_skill_review(
        db=db,
        asset_id=asset.asset_id,
        version_id=version_row.version_id,
        created_at=ts_ms,
    )
    db.add(version_row)
    _apply_skill_asset_aggregate_from_versions(db, asset.asset_id)
    return True


def _ensure_skill_review_model_configured(needs_skill_review: bool) -> None:
    if not needs_skill_review:
        return
    if resolve_skill_review_model_config() is not None:
        return
    raise BusinessError(
        code=503,
        error="skill_review_model_not_configured",
        message=(
            "Skill 系统审查已开启，但审查模型未完整配置；"
            "请联系管理员配置 MARKET_SKILL_REVIEW_MODEL_BASE_URL、"
            "MARKET_SKILL_REVIEW_MODEL_API_KEY、MARKET_SKILL_REVIEW_MODEL_NAME 后重试"
        ),
        error_code="SKILLHUB_REVIEW_MODEL_NOT_CONFIGURED",
        error_class="upstream",
    )


def _make_publish_result(
    asset: MarketAssetDB,
    version_row: MarketAssetVersionDB,
    zip_key: str,
) -> PluginPublishResult:
    ts_ms = version_row.create_time or asset.create_time or 0
    published_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
    return PluginPublishResult(
        plugin_id=asset.asset_id,
        name=asset.name,
        display_name=asset.display_name,
        version=version_row.version,
        status=version_row.status or "ACTIVE",
        published_at=published_at,
        storage_url=zip_key,
        plugin_type=asset.plugin_type,
        publish_result=_resolved_version_publish_result_value(version_row),
        visibility=getattr(asset, "visibility", None) or "public",
    )


def _resolve_publish_failed_reason(
    *,
    version_row: MarketAssetVersionDB,
    review_row: MarketSkillReviewDB | None,
) -> str | None:
    if _resolved_version_publish_result_value(version_row) != PUBLISH_RESULT_FAILED:
        return None
    if (getattr(version_row, "moderation_status", None) or "").strip().upper() == MODERATION_REJECTED:
        reason = (getattr(version_row, "moderation_reject_reason", None) or "").strip()
        return reason or None
    if review_row is None:
        return None
    reason = (review_row.review_failed_reason or "").strip()
    if reason:
        return reason
    conclusion = (review_row.conclusion or "").strip()
    return conclusion or None


def _resolved_publish_result_value(asset: MarketAssetDB) -> str | None:
    return coalesce_skill_publish_result(
        getattr(asset, "publish_result", None),
        getattr(asset, "moderation_status", None),
    )


def _list_publish_result_for_viewer(
    asset: MarketAssetDB, viewer: ViewerContext, db: Session | None = None
) -> str | None:
    resolved = _resolved_publish_result_value(asset)
    if not is_skill_like_plugin_type(asset.plugin_type):
        return resolved
    if viewer.skill_asset_access_source(asset, db) in ("admin", "owner"):
        return resolved
    if (getattr(asset, "public_latest_version", None) or "").strip():
        return PUBLISH_RESULT_SUCCESS
    return resolved


def _semver_sort_key(version: str | None) -> tuple[int, int, int]:
    """Parse x.y.z for ordering; invalid/missing sorts last."""
    v = (version or "").strip()
    if not v or not VERSION_PATTERN.match(v):
        return (-1, -1, -1)
    a, b, c = v.split(".", 2)
    return (int(a), int(b), int(c))


def _render_cumulative_changelog_file(versions: list[MarketAssetVersionDB]) -> str:
    """
    Build UTF-8 text for changelog.log: all historical version rows.
    Order by semver descending (largest version first).
    Plain style: [version] then blank line then changelog (same as API version_desc).
    """
    if not versions:
        return "（暂无版本记录）\n"

    ordered = sorted(
        versions,
        key=lambda r: _semver_sort_key(r.version),
        reverse=True,
    )
    blocks: list[str] = []
    for row in ordered:
        ver = (row.version or "").strip() or "未知"
        body = (row.changelog or "").strip() or "（无变更说明）"
        blocks.append(f"[{ver}]\n\n{body}")

    return "\n\n".join(blocks) + "\n"


def _is_uk_publisher_name_error(exc: IntegrityError) -> bool:
    msg = str(getattr(exc, "orig", None) or exc)
    low = msg.lower()
    return "uk_publisher_name" in low or ("unique" in low and "publisher_id" in low and "name" in low)


def _is_uk_asset_version_error(exc: IntegrityError) -> bool:
    msg = str(getattr(exc, "orig", None) or exc)
    low = msg.lower()
    return "uk_asset_version" in low or ("unique" in low and "asset_id" in low and "version" in low)


def _validate_existing_asset_visibility(existing_asset: MarketAssetDB | None, requested_visibility: str) -> None:
    if existing_asset is None:
        return
    current_visibility = (getattr(existing_asset, "visibility", None) or "public").strip().lower()
    if requested_visibility != current_visibility:
        raise PublishError(
            code=409,
            error="visibility_immutable",
            message="发布新版本时不能修改 Skill 可见性，请沿用现有设置",
            data={"current_visibility": current_visibility},
        )


def publish(
    *,
    user_id: str,
    content: bytes,
    filename: str | None,
    expected_checksum: str,
    plugin_id: str | None,
    plugin_version: str | None,
    version_desc: str | None,
    force: bool,
    db: Session,
    storage: S3StorageClient,
    visibility: str = "public",
    publisher_name_override: str | None = None,
) -> PluginPublishResult:
    """Validate, resolve conflicts, upload to S3, write asset/version, return result. Raises PublishError on failure."""
    asset_visibility = (visibility or "public").strip().lower()
    if asset_visibility not in ("public", "private"):
        raise PublishError(code=400, error="invalid_visibility", message="visibility 仅支持 public 或 private")
    if not filename or not filename.lower().endswith(".zip"):
        raise PublishError(
            code=400,
            error="invalid_file_format",
            message="仅支持 .zip 格式的插件包文件",
        )

    if len(content) > MAX_FILE_SIZE:
        raise PublishError(
            code=413,
            error="file_too_large",
            message="文件大小超过限制（最大512MB）",
        )

    computed = _compute_checksum(content)
    if computed != expected_checksum.lower():
        raise PublishError(
            code=400,
            error="checksum_mismatch",
            message="文件校验和不匹配，文件可能在传输过程中损坏",
        )

    if len(content) < 2 or content[:2] != b"PK":
        raise PublishError(
            code=400,
            error="invalid_file_format",
            message="仅支持 .zip 格式的插件包文件",
        )

    meta = extract_plugin_metadata(content)
    content_size = len(content)
    name = (meta["name"] or "").strip()
    display_name = (meta.get("display_name") or "").strip()
    manifest_version = (meta["version"] or "").strip()

    # 解析出 SKILL.md 元信息后回填审计 hint：此后才失败的发布（plugin_not_found / 版本冲突 /
    # 越权 / 名称已存在等）也能记下真实 slug 与友好显示名，而非仅 plugin_id。
    # set_audit_hint 自动忽略 None/空串，故解析前就失败的场景不受影响（仍走文件名兜底）。
    set_audit_hint(skill_name=name, skill_display_name=display_name)

    if not name:
        raise PublishError(
            code=400,
            error="invalid_plugin_config",
            message="plugin.yaml 配置文件格式错误或缺失：缺少必需的 name 字段",
        )

    if plugin_version is None:
        if not manifest_version:
            raise PublishError(
                code=400,
                error="invalid_plugin_config",
                message="plugin.yaml 配置文件格式错误或缺失：缺少必需的 version 字段",
            )
        version = _normalize_version(manifest_version)
    else:
        version = _normalize_version(plugin_version)

    _validate_version(version)

    short_desc = meta.get("short_desc")
    if isinstance(short_desc, str) and len(short_desc) > MARKET_ASSET_SHORT_DESC_MAX_LEN:
        short_desc = short_desc[:MARKET_ASSET_SHORT_DESC_MAX_LEN]
    detail_desc = meta.get("detail_desc")
    tags = meta.get("tags") or []
    raw_publisher_name = meta.get("publisher_name") or ""
    plugin_type = meta.get("plugin_type")
    rt = (plugin_type or "").strip().lower() if isinstance(plugin_type, str) else ""
    publish_plugin_type = rt or None
    # 开关：开启后从发布入口直接拒绝非 skill-like 类型（tools / mcp-stdio / restful-api 等会执行代码的插件），
    # 不进入后续上传/建库/审核流程，彻底消除“上传即生效”的任意代码执行风险。仅放行 skill / swarmskill。
    if settings.block_nonskill_plugin_publish and not is_skill_like_plugin_type(rt):
        raise PublishError(
            code=403,
            error="plugin_type_publish_disabled",
            message="当前仅支持发布 Skill / TeamSkills 类型插件；tools / mcp-stdio / restful-api 类型发布已关闭",
        )
    # Bearer 发布时，市场展示发布者应优先使用当前登录用户身份，而不是包内 metadata.author/publisher_name。
    if publisher_name_override is not None:
        publisher_name = publisher_name_override.strip() or raw_publisher_name
    else:
        publisher_name = raw_publisher_name
    icon_bytes = meta.get("icon_bytes") or b""
    if icon_bytes:
        icon_bytes = optimize_png_icon_bytes(icon_bytes)

    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)

    pid = (plugin_id or "").strip()
    if pid:
        existing_asset = asset_repo.get_by_asset_id(pid)
        if not existing_asset:
            raise PublishError(
                code=404,
                error="plugin_not_found",
                message=f"插件 '{pid}' 不存在，无法添加新版本",
            )
        if existing_asset.publisher_id != user_id:
            raise PublishError(
                code=403,
                error="permission_denied",
                message="您无权限操作该插件",
            )
        by_name = asset_repo.list_by_publisher_name_and_type(user_id, name, "plugin")
        same_plugin_type_matches = asset_repo.list_by_publisher_name_type_and_plugin_type(
            user_id,
            name,
            "plugin",
            publish_plugin_type,
        )
        if len(same_plugin_type_matches) == 1 and same_plugin_type_matches[0].asset_id != pid:
            raise PublishError(
                code=422,
                error="plugin_id_mismatch",
                message=(
                    f"plugin_id 与插件包不匹配：您填写的 plugin_id='{pid}' 与插件名称 '{name}'、"
                    f"plugin_type '{publish_plugin_type or 'null'}' 对应的插件id不一致"
                ),
                data={"expected_plugin_id": same_plugin_type_matches[0].asset_id},
            )
        if len(same_plugin_type_matches) > 1 and pid not in {m.asset_id for m in same_plugin_type_matches}:
            raise PublishError(
                code=422,
                error="plugin_id_mismatch",
                message=(
                    f"plugin_id 与插件包不匹配：您填写的 plugin_id='{pid}' 与插件名称 '{name}'、"
                    f"plugin_type '{publish_plugin_type or 'null'}' 对应的插件id不一致，请从同类候选中选择正确的 plugin_id"
                ),
                data={"ambiguous_plugin_ids": [m.asset_id for m in same_plugin_type_matches]},
            )
        if not same_plugin_type_matches and by_name and existing_asset.name == name:
            existing_plugin_type = normalize_skill_like_plugin_type(existing_asset.plugin_type)
            existing_plugin_type = existing_plugin_type or existing_asset.plugin_type or "null"
            raise PublishError(
                code=422,
                error="plugin_type_immutable",
                message=(
                    f"该资产 plugin_type 已为 '{existing_plugin_type}'，"
                    f"本次包派生为 '{publish_plugin_type or 'null'}'，类型不可变"
                ),
            )
        asset_id = pid
    else:
        by_name = asset_repo.list_by_publisher_name_and_type(user_id, name, "plugin")
        matches = asset_repo.list_by_publisher_name_type_and_plugin_type(
            user_id,
            name,
            "plugin",
            publish_plugin_type,
        )
        if len(matches) > 1:
            raise PublishError(
                code=422,
                error="manifest_validation_failed",
                message=(
                    f"存在多个同名且同类型插件 '{name}'（plugin_type='{publish_plugin_type or 'null'}'），"
                    "请通过 plugin_id 指定要发布版本的插件"
                ),
                data={"ambiguous_plugin_ids": [m.asset_id for m in matches]},
            )
        if len(matches) == 1:
            existing_asset = matches[0]
            asset_id = existing_asset.asset_id
        elif by_name:
            existing_types = sorted(
                {
                    normalize_skill_like_plugin_type(getattr(item, "plugin_type", None))
                    or getattr(item, "plugin_type", None)
                    or "null"
                    for item in by_name
                }
            )
            raise PublishError(
                code=409,
                error="plugin_name_exists",
                message=(
                    f"您已发布过同名插件 '{name}'，但现有 plugin_type 为 {', '.join(existing_types)}；"
                    f"本次包派生为 '{publish_plugin_type or 'null'}'。请使用其他名称。"
                ),
                data={
                    "existing_plugin_ids": [m.asset_id for m in by_name],
                    "existing_plugin_types": existing_types,
                },
            )
        else:
            asset_id = uuid.uuid4().hex
            existing_asset = None
    _validate_existing_asset_visibility(existing_asset, asset_visibility)
    existing_version = version_repo.get_version(asset_id=asset_id, version=version)
    is_system_admin_publisher = _is_system_admin_publisher(user_id)
    is_skill_like_publish = is_skill_like_plugin_type(plugin_type)
    supports_system_skill_review = is_skill_like_publish
    needs_skill_review = bool(
        supports_system_skill_review and settings.skill_review_enabled and not is_system_admin_publisher
    )
    notify_review_admins_after_publish = bool(
        is_skill_like_publish and not needs_skill_review and not is_system_admin_publisher
    )
    initial_publish_result: str | None = None
    if is_skill_like_publish:
        initial_publish_result, _ = initial_skill_publish_state(
            skill_review_enabled=bool(supports_system_skill_review and settings.skill_review_enabled),
            is_system_admin_publisher=is_system_admin_publisher,
        )
    initial_version_publish_result = initial_publish_result if is_skill_like_publish else PUBLISH_RESULT_SUCCESS
    _validate_asset_name_immutable_for_skill(existing_asset, name, plugin_type)
    _ensure_skill_review_model_configured(needs_skill_review)

    version_dir = _version_dir_prefix(user_id, asset_id, version, plugin_type)
    zip_key = _build_storage_path(
        publisher_id=user_id,
        asset_id=asset_id,
        version=version,
        asset_name=name,
        plugin_type=plugin_type,
    )
    file_path = version_dir

    if existing_version and _publish_idempotent_same_artifact(existing_version, computed):
        asset_for_result = existing_asset if existing_asset is not None else asset_repo.get_by_asset_id(asset_id)
        if asset_for_result is None:
            raise PublishError(
                code=500,
                error="internal_error",
                message="发布幂等校验失败：缺少插件主记录",
            )
        if _restart_same_artifact_system_review_if_needed(
            db,
            asset_for_result,
            existing_version,
            needs_skill_review,
        ):
            db.commit()
            db.refresh(asset_for_result)
            db.refresh(existing_version)
            logger.info(
                "publish idempotent retry system review (same version + artifact_sha256): asset_id=%s version=%s",
                asset_id,
                version,
            )
            return _make_publish_result(asset_for_result, existing_version, zip_key)
        logger.info(
            "publish idempotent skip (same version + artifact_sha256): asset_id=%s version=%s",
            asset_id,
            version,
        )
        return _make_publish_result(asset_for_result, existing_version, zip_key)

    if existing_version and not force:
        raise PublishError(
            code=409,
            error="version_conflict",
            message=f"插件 '{name}' 版本 '{version}' 已存在，如需覆盖请勾选强制覆盖",
            data={
                "existing_plugin": {
                    "plugin_id": existing_asset.asset_id if existing_asset else asset_id,
                    "version": existing_version.version,
                }
            },
        )

    # 写入校验和/大小到对象 metadata，避免下载时读全量对象重复计算
    upload_result = storage.upload_bytes(
        content,
        zip_key,
        metadata={"sha256": computed, "size": str(content_size)},
    )
    if not upload_result.get("success"):
        raise PublishError(
            code=500,
            error="storage_error",
            message=upload_result.get("error", "插件包上传失败"),
        )

    if icon_bytes:
        icon_key = f"{version_dir}icon.png"
        r = storage.upload_bytes(icon_bytes, icon_key)
        if not r.get("success"):
            raise PublishError(
                code=500,
                error="storage_error",
                message=r.get("error", "插件图标上传失败"),
            )

    if detail_desc is not None:
        readme_key = f"{version_dir}readme.md"
        r = storage.upload_bytes(detail_desc.encode("utf-8"), readme_key)
        if not r.get("success"):
            raise PublishError(
                code=500,
                error="storage_error",
                message=r.get("error", "插件 README 上传失败"),
            )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Validate skill count for new skill assets (system admin is exempt).
    if not existing_asset and is_skill_like_plugin_type(rt) and not is_system_admin_publisher:
        skill_count = asset_repo.count_skills_by_publisher(user_id)
        if skill_count >= 50:
            raise PublishError(
                code=409,
                error="skill_limit_exceeded",
                message=f"您已发布 {skill_count} 个 Skill，达到发布上限（最多 50 个）",
            )

    try:
        if not existing_asset:
            # 新建插件：插入主表 + 版本表（审核态在版本上，主表由聚合重算）
            mod_st, mod_rs = _moderation_for_publish(user_id=user_id, plugin_type=plugin_type)
            asset_obj = MarketAssetDB(
                asset_id=asset_id,
                asset_type="plugin",
                name=name,
                display_name=display_name,
                short_desc=short_desc,
                detail_desc=detail_desc,
                publisher_id=user_id,
                publisher_name=publisher_name,
                tags=tags if tags else None,
                status="PUBLISHED",
                plugin_type=plugin_type,
                publish_result=initial_publish_result,
                visibility=asset_visibility,
                latest_version=version,
                create_time=now_ms,
                update_time=now_ms,
            )
            version_obj = MarketAssetVersionDB(
                version_id=uuid.uuid4().hex,
                asset_id=asset_id,
                version=version,
                changelog=version_desc,
                status="ACTIVE",
                create_time=now_ms,
                file_path=file_path,
                artifact_sha256=computed,
                has_icon=bool(icon_bytes),
                moderation_status=mod_st,
                moderation_reject_reason=mod_rs if mod_st == MODERATION_REJECTED else None,
                publish_result=initial_version_publish_result,
            )
            db.add(asset_obj)
            db.add(version_obj)
            if needs_skill_review:
                initialize_skill_review(
                    db=db,
                    asset_id=asset_id,
                    version_id=version_obj.version_id,
                    created_at=now_ms,
                )
            _apply_skill_asset_aggregate_from_versions(db, asset_id)
            db.commit()
            db.refresh(asset_obj)
            db.refresh(version_obj)
            asset = asset_obj
            version_row = version_obj
        else:
            # 已有插件：更新主表 + 新增或覆盖版本（不直接写主表审核态）
            mod_st, mod_rs = _moderation_for_publish(user_id=user_id, plugin_type=plugin_type)
            had_any_approved_version = (
                _compute_latest_approved_skill_version_row(
                    asset_id=asset_id,
                    version_repo=version_repo,
                )
                is not None
            )
            skip_listing_fields_for_pending_skill = (
                is_skill_like_plugin_type(plugin_type) and mod_st == MODERATION_PENDING and had_any_approved_version
            )

            existing_plugin_type = normalize_skill_like_plugin_type(existing_asset.plugin_type)
            incoming_plugin_type = normalize_skill_like_plugin_type(plugin_type)
            canonical_plugin_type = incoming_plugin_type or existing_plugin_type or None
            # plugin_type 一经确定即不可变：已发布资产新增版本时，skill 与 swarmskill 之间任意方向的
            # 变更都拒绝（包括 skill→swarmskill 的“升级”），避免同一资产跨类型漂移。
            if existing_plugin_type and incoming_plugin_type and existing_plugin_type != incoming_plugin_type:
                if existing_plugin_type == "swarmskill" and incoming_plugin_type == "skill":
                    msg = (
                        "该资产为团队技能（plugin_type=swarmskill），不能降级为普通技能；"
                        "请确认 SKILL.md 的 kind 字段是否被误删（应为 team-skill / swarm-skill）"
                    )
                else:
                    msg = (
                        f"该资产 plugin_type 已为 '{existing_plugin_type}'，"
                        f"本次包派生为 '{incoming_plugin_type}'，类型不可变"
                    )
                raise PublishError(code=422, error="plugin_type_immutable", message=msg)

            existing_asset.name = name
            existing_asset.latest_version = version
            existing_asset.update_time = now_ms
            existing_asset.publisher_name = publisher_name
            existing_asset.plugin_type = canonical_plugin_type
            existing_asset.publish_result = initial_publish_result
            if not skip_listing_fields_for_pending_skill:
                existing_asset.display_name = display_name
                existing_asset.short_desc = short_desc
                existing_asset.detail_desc = detail_desc
                existing_asset.tags = tags if tags else None

            if existing_version and force:
                existing_version.changelog = version_desc
                existing_version.status = "ACTIVE"
                existing_version.file_path = file_path
                existing_version.artifact_sha256 = computed
                existing_version.has_icon = bool(icon_bytes)
                existing_version.moderation_status = mod_st
                existing_version.moderation_reject_reason = mod_rs if mod_st == MODERATION_REJECTED else None
                existing_version.publish_result = initial_version_publish_result
                version_row = existing_version
            else:
                version_row = MarketAssetVersionDB(
                    version_id=uuid.uuid4().hex,
                    asset_id=asset_id,
                    version=version,
                    changelog=version_desc,
                    status="ACTIVE",
                    create_time=now_ms,
                    file_path=file_path,
                    artifact_sha256=computed,
                    has_icon=bool(icon_bytes),
                    moderation_status=mod_st,
                    moderation_reject_reason=mod_rs if mod_st == MODERATION_REJECTED else None,
                    publish_result=initial_version_publish_result,
                )
                db.add(version_row)
            if needs_skill_review:
                initialize_skill_review(
                    db=db,
                    asset_id=asset_id,
                    version_id=version_row.version_id,
                    created_at=now_ms,
                )
            db.add(existing_asset)
            _apply_skill_asset_aggregate_from_versions(db, asset_id)
            db.commit()
            db.refresh(existing_asset)
            db.refresh(version_row)
            asset = existing_asset

    except IntegrityError as e:
        db.rollback()
        if _is_uk_publisher_name_error(e):
            raise PublishError(
                code=409,
                error="plugin_name_exists",
                message=f"您已发布过同名插件 '{name}'，请使用其他名称或为现有插件添加新版本",
            ) from e
        if _is_uk_asset_version_error(e):
            raise PublishError(
                code=409,
                error="version_exists",
                message=f"插件版本 '{version}' 已存在，如需覆盖请勾选强制覆盖",
                data={"existing_version": version},
            ) from e
        raise

    if (
        notify_review_admins_after_publish
        and _resolved_version_publish_result_value(version_row) == PUBLISH_RESULT_PENDING_MODERATION
    ):
        try:
            notify_review_admins_new_skill_submission(db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify review admins failed after publish without system review: %s", exc)

    # Cumulative changelog for this release dir
    all_versions = version_repo.list_versions_chronological(asset_id)
    changelog_text = _render_cumulative_changelog_file(all_versions)
    cl_key = f"{version_dir}changelog.log"
    r = storage.upload_bytes(changelog_text.encode("utf-8"), cl_key)
    if not r.get("success"):
        raise PublishError(
            code=500,
            error="storage_error",
            message=r.get("error", "插件 changelog.log 上传失败"),
        )

    storage_url = zip_key
    published_at = datetime.fromtimestamp(
        (version_row.create_time or asset.create_time) / 1000, tz=timezone.utc
    ).isoformat()

    return PluginPublishResult(
        plugin_id=asset.asset_id,
        name=asset.name,
        display_name=asset.display_name,
        version=version_row.version,
        status=version_row.status or "ACTIVE",
        published_at=published_at,
        storage_url=storage_url,
        plugin_type=asset.plugin_type,
        publish_result=_resolved_version_publish_result_value(version_row),
    )


def _rows_pin_order_first(
    ordered: List[Tuple[MarketAssetDB, Optional[str], bool]],
) -> List[Tuple[MarketAssetDB, Optional[str], bool]]:
    """检索结果内：pin_order 非空的条目提前，按 pin_order 升序，同序保持原检索先后；其余保持检索顺序。"""
    pinned = [(row[0].pin_order, idx, row) for idx, row in enumerate(ordered) if row[0].pin_order is not None]
    pinned.sort(key=lambda x: (x[0], x[1]))
    pinned_ids = {x[2][0].asset_id for x in pinned}
    unpinned = [row for row in ordered if row[0].asset_id not in pinned_ids]
    return [x[2] for x in pinned] + unpinned


def _icon_presigned_url_from_file_path(
    storage: S3StorageClient,
    file_path: str | None,
    has_icon: bool = False,
) -> str | None:
    """图标固定为版本目录下 icon.png，与 file_path 拼出对象 Key 后预签名。"""
    if not has_icon:
        return None
    prefix = _version_prefix_from_file_path(storage, file_path)
    if not prefix:
        return None
    icon_key = f"{prefix}icon.png"
    try:
        return storage.presigned_get_url(icon_key)
    except Exception as e:
        logger.warning("预签名图标链接失败 key=%s: %s", icon_key, e)
        return None


def _asset_matches_list_moderation_filter(asset: MarketAssetDB, ms: str) -> bool:
    raw = getattr(asset, "moderation_status", None)
    if ms == MODERATION_PENDING:
        return (raw or "").strip().upper() == MODERATION_PENDING
    if ms == MODERATION_REJECTED:
        return (raw or "").strip().upper() == MODERATION_REJECTED
    if ms == MODERATION_APPROVED:
        return moderation_coalesce_display(raw) == MODERATION_APPROVED
    return True


def _asset_matches_list_moderation_filter_retrieval(
    asset: MarketAssetDB,
    ms: str,
    *,
    pending_version_asset_ids: set[str],
) -> bool:
    """检索路径的 PENDING 筛选：Skill 仅含“待人工审核”的版本。"""
    if ms != MODERATION_PENDING:
        return _asset_matches_list_moderation_filter(asset, ms)
    if not is_skill_like_plugin_type(asset.plugin_type):
        return _asset_matches_list_moderation_filter(asset, ms)
    return asset.asset_id in pending_version_asset_ids


def _filter_skill_version_strings_for_viewer(
    asset: MarketAssetDB,
    vrows: List[MarketAssetVersionDB],
    plugin_type: str | None,
    viewer: ViewerContext,
    db: Session | None = None,
) -> List[str]:
    if not is_skill_like_plugin_type(plugin_type):
        return [r.version for r in vrows]
    return [row.version for row in vrows if viewer.can_see_skill_version_row(asset, row, db)]


def _skill_version_moderation_map_for_list(
    asset: MarketAssetDB,
    vrows: List[MarketAssetVersionDB],
    viewer: ViewerContext,
    db: Session | None = None,
) -> dict[str, str] | None:
    """发布者或审核员在列表/详情拉取时可拿到各版本审核状态，供前端版本下拉展示。"""
    if not is_skill_like_plugin_type(asset.plugin_type):
        return None
    if viewer.skill_asset_access_source(asset, db) not in ("admin", "owner"):
        return None
    out: dict[str, str] = {}
    aid = (asset.asset_id or "").strip()
    for r in vrows:
        if (r.asset_id or "").strip() != aid:
            continue
        out[r.version] = moderation_coalesce_display(getattr(r, "moderation_status", None))
    return out or None


def _skill_version_publish_result_map_for_list(
    asset: MarketAssetDB,
    vrows: List[MarketAssetVersionDB],
    viewer: ViewerContext,
    db: Session | None = None,
) -> dict[str, str] | None:
    """发布者或审核员在列表/详情拉取时可拿到各版本发布阶段状态。"""
    if not is_skill_like_plugin_type(asset.plugin_type):
        return None
    if viewer.skill_asset_access_source(asset, db) not in ("admin", "owner"):
        return None
    out: dict[str, str] = {}
    aid = (asset.asset_id or "").strip()
    for r in vrows:
        if (r.asset_id or "").strip() != aid:
            continue
        out[r.version] = _resolved_version_publish_result_value(r)
    return out or None


def _skill_has_pending_version_for_viewer(
    vrows: List[MarketAssetVersionDB],
    plugin_type: str | None,
    viewer: ViewerContext,
    asset: MarketAssetDB,
    db: Session | None = None,
) -> bool:
    if not is_skill_like_plugin_type(plugin_type):
        return False
    if viewer.skill_asset_access_source(asset, db) not in ("admin", "owner"):
        return False
    return any(_resolved_version_publish_result_value(row) == PUBLISH_RESULT_PENDING_MODERATION for row in vrows)


def _git_version_display_as_commit(asset: MarketAssetDB, displayed_version: str | None) -> bool:
    """Git 且无 SKILL 声明版本时：仅当列表/详情展示的版本号与资产 latest_version 一致才用 commit 短码，避免对外 public 串误配。"""
    if (getattr(asset, "storage_mode", None) or "").strip().lower() != "git":
        return False
    if (getattr(asset, "declared_skill_version", None) or "").strip():
        return False
    if not (getattr(asset, "resolved_commit_sha", None) or "").strip():
        return False
    av = (getattr(asset, "latest_version", None) or "").strip()
    dv = (displayed_version or "").strip()
    if not av or not dv:
        return False
    return dv == av


def _list_item_skill_like_public_latest_for_viewer(
    asset: MarketAssetDB,
    item: PluginListItem,
    viewer: ViewerContext,
    db: Session | None = None,
) -> PluginListItem:
    """非发布者、非审核管理员：列表 latest_version 与对外可装版本一致，避免暴露待审新版本号。"""
    if not is_skill_like_plugin_type(asset.plugin_type):
        return item
    if viewer.skill_asset_access_source(asset, db) in ("admin", "owner"):
        return item
    plv = (getattr(asset, "public_latest_version", None) or "").strip()
    if plv:
        return item.model_copy(update={"latest_version": plv})
    return item


def _list_item_from_asset(
    asset: MarketAssetDB,
    latest_file_path: str | None,
    has_icon: bool,
    storage: S3StorageClient,
    vrows: List[MarketAssetVersionDB],
    viewer: ViewerContext,
    *,
    market_public_scoped: bool = False,
    db: Session | None = None,
) -> PluginListItem:
    """构建列表项。公开市场中组群授权项按当前用户展示，否则按匿名公开市场脱敏。"""
    access_source = _access_source_for_viewer(asset, viewer, db)
    item_viewer = (
        viewer
        if access_source in ("owner", "group", "admin")
        else (ANONYMOUS_VIEWER if market_public_scoped else viewer)
    )
    item = PluginListItem.model_validate(asset)
    item.detail_desc = _detail_desc_for_display(asset.plugin_type, item.detail_desc)
    item.icon_uri = _icon_presigned_url_from_file_path(storage, latest_file_path, has_icon)
    item.publish_result = _list_publish_result_for_viewer(asset, item_viewer, db)
    item.public_latest_version = getattr(asset, "public_latest_version", None)
    item.all_versions = _filter_skill_version_strings_for_viewer(asset, vrows, asset.plugin_type, item_viewer, db)
    item.has_pending_skill_version = _skill_has_pending_version_for_viewer(
        vrows, asset.plugin_type, item_viewer, asset, db
    )
    item.skill_version_moderation = _skill_version_moderation_map_for_list(asset, vrows, item_viewer, db)
    item.skill_version_publish_result = _skill_version_publish_result_map_for_list(asset, vrows, item_viewer, db)
    item = _list_item_skill_like_public_latest_for_viewer(asset, item, item_viewer, db)
    if market_public_scoped and is_skill_like_plugin_type(asset.plugin_type):
        plv = (getattr(asset, "public_latest_version", None) or "").strip()
        if plv:
            item = item.model_copy(
                update={
                    "moderation_status": MODERATION_APPROVED,
                    "publish_result": PUBLISH_RESULT_SUCCESS,
                    "latest_version": plv,
                }
            )
    item = item.model_copy(
        update={
            "git_version_display_as_commit": _git_version_display_as_commit(
                asset,
                (item.latest_version or "").strip() or None,
            ),
        },
    )
    return _list_item_with_viewer_flag(item, viewer, asset, db)


def list_plugins_service(
    query: PluginListQuery,
    db: Session,
    storage: S3StorageClient,
    *,
    viewer: ViewerContext,
    use_retrieval_search: bool = True,
) -> PluginListResponse:
    logger.info(
        "List plugins request: page=%s page_size=%s asset_id=%s "
        "publisher_id=%s category_id=%s plugin_type=%s moderation_status=%s order_by=%s desc=%s",
        query.page,
        query.page_size,
        query.asset_id,
        query.publisher_id,
        query.category_id,
        query.plugin_type,
        query.moderation_status,
        query.order_by,
        query.desc,
    )
    repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)
    market_public_scoped = repo.is_market_public_scoped_list(query, viewer)

    keyword = (query.search_keyword or "").strip()
    if not query.plugin_type and not query.plugin_type_exclude:
        query = query.model_copy(update={"plugin_type": "skill,swarmskill"})
    plugin_type = (query.plugin_type or "").strip()

    # Personalized recommend path: homepage「推荐精选」only (no keyword/category_id/tags).
    # 「全部」and category tabs use MySQL install_count. POST /api/v1/recommend is unchanged.
    category_id = (query.category_id or "").strip()
    use_recommend = (
        (query.order_by or "").strip() == "recommend"
        and not keyword
        and not category_id
        and not parse_tag_filter(query.tags)
    )
    if use_recommend and not settings.recommender_enabled:
        query = query.model_copy(update={"order_by": "install_count"})
        use_recommend = False

    if use_recommend:
        try:
            from plugins_market.recommender.bootstrap import apply_recommender_settings_to_env
            from plugins_market.recommender.service import run_recommend_for_user

            apply_recommender_settings_to_env()
            rec_items, rec_source = run_recommend_for_user(
                user_id=viewer.user_id or "",
                top_k=settings.rec_list_top_k,
                plugin_type=plugin_type,
            )
            item_ids = [it.asset_id for it in rec_items][: settings.rec_list_top_k]
            if item_ids:
                # Lightweight meta filter first (OFFLINE + plugin_type).
                meta_rows = (
                    db.query(
                        MarketAssetDB.asset_id,
                        MarketAssetDB.plugin_type,
                        MarketAssetDB.category_id,
                        MarketAssetDB.pin_order,
                    )
                    .filter(
                        MarketAssetDB.asset_id.in_(item_ids),
                        MarketAssetDB.status != "OFFLINE",
                    )
                    .all()
                )
                meta = {r.asset_id: r for r in meta_rows}
                pt_list = [p.strip().lower() for p in plugin_type.split(",") if p.strip()]
                ordered_ids: list[str] = []
                for iid in item_ids:
                    row = meta.get(iid)
                    if row is None:
                        continue
                    if pt_list and (row.plugin_type or "").strip().lower() not in pt_list:
                        continue
                    ordered_ids.append(iid)

                pinned = [aid for aid in ordered_ids if meta[aid].pin_order is not None]
                pinned.sort(key=lambda aid: int(meta[aid].pin_order or 0))
                unpinned = [aid for aid in ordered_ids if meta[aid].pin_order is None]
                ordered_ids = pinned + unpinned

                # Same ACL/moderation as the card hydrate. Counting only OFFLINE
                # left total ahead of items (e.g. 6 records, 4 cards).
                rows_with_path = repo.get_assets_with_file_paths(ordered_ids, viewer=viewer)
                rows_map = {asset.asset_id: (asset, fp, hi) for asset, fp, hi in rows_with_path}
                ordered_ids = [aid for aid in ordered_ids if aid in rows_map]
                logger.info(
                    "recommend path: source=%s user_id=%s ranked=%d visible=%d top_k=%s",
                    rec_source,
                    viewer.user_id or "",
                    len(item_ids),
                    len(ordered_ids),
                    settings.rec_list_top_k,
                )

                total = len(ordered_ids)
                start = (query.page - 1) * query.page_size
                page_asset_ids = ordered_ids[start:start + query.page_size]
                page_slice = [rows_map[aid] for aid in page_asset_ids]
                vrows = version_repo.list_all_by_asset_ids(page_asset_ids)
                vmap: Dict[str, List[MarketAssetVersionDB]] = defaultdict(list)
                for r in vrows:
                    vmap[r.asset_id].append(r)
                items = []
                for asset, latest_file_path, has_icon in page_slice:
                    items.append(
                        _list_item_from_asset(
                            asset,
                            latest_file_path,
                            has_icon,
                            storage,
                            vmap.get(asset.asset_id, []),
                            viewer,
                            market_public_scoped=market_public_scoped,
                            db=db,
                        )
                    )
                return PluginListResponse(
                    page=query.page,
                    page_size=query.page_size,
                    total=total,
                    items=items,
                )
            logger.info("recommend path empty; fallback to install_count")
        except Exception as exc:
            logger.warning("recommend path failed, fallback to install_count: %s", exc)
        query = query.model_copy(update={"order_by": "install_count"})

    # Never pass order_by=recommend into MySQL sorting.
    if (query.order_by or "").strip() == "recommend":
        query = query.model_copy(update={"order_by": "install_count"})

    # 标签是浏览态过滤器，不与关键词搜索组合：搜索时忽略 tags。
    # 前端已置灰标签行，此处保证直接调 API 的客户端同样是「搜索不看标签」。
    if keyword and parse_tag_filter(query.tags):
        query = query.model_copy(update={"tags": None})

    if keyword and plugin_type and use_retrieval_search:
        item_ids = retrieval_search(
            get_index_manager(),
            plugin_type,
            keyword,
            query.page,
            query.page_size,
            method=settings.retrieval_search_method,
        )
        # 守 retrieval_search 契约：None=检索不可用/出错 -> 回退 DB LIKE（下方 repo.list_plugins）；
        # []=检索确认无命中 -> 用空结果（下方 if not ordered 返回空页，不退化为子串 LIKE）。
        # tags 已拼进检索文本（build_retrieval_text），标签名当关键词 BM25 正常命中，故
        # "索引搜不到"基本只剩索引未重建的空窗期--召回缺口应在索引层补，而非搜索层 LIKE 兜底，
        # 否则所有真无匹配的关键词搜索都会翻出子串命中但语义无关的资产，拉低精度。
        if item_ids is not None:
            logger.info("retrieval path: plugin_type=%s keyword=%r hits=%d", plugin_type, keyword, len(item_ids))
            rows_with_path = repo.get_assets_with_file_paths(item_ids, viewer=viewer)
            rows_map = {asset.asset_id: (asset, fp, hi) for asset, fp, hi in rows_with_path}
            # preserve retrieval ranking; rows_map excludes OFFLINE (defensive filter)
            ordered = [rows_map[iid] for iid in item_ids if iid in rows_map]
            pt_list = [p.strip() for p in plugin_type.split(",") if p.strip()]
            if pt_list:
                ordered = [row for row in ordered if (row[0].plugin_type or "").strip().lower() in pt_list]
            ms_list = (query.moderation_status or "").strip().upper() if query.moderation_status else ""
            if ms_list in (MODERATION_PENDING, MODERATION_APPROVED, MODERATION_REJECTED):
                ids_for_pending = [row[0].asset_id for row in ordered]
                pending_extra: set[str] = set()
                if ms_list == MODERATION_PENDING and any(
                    is_skill_like_plugin_type(p.strip()) for p in plugin_type.split(",") if p.strip()
                ):
                    pending_extra = version_repo.asset_ids_with_pending_moderation_version(ids_for_pending)
                ordered = [
                    row
                    for row in ordered
                    if _asset_matches_list_moderation_filter_retrieval(
                        row[0],
                        ms_list,
                        pending_version_asset_ids=pending_extra,
                    )
                ]
            if query.category_id and query.category_id.strip():
                category_id = query.category_id.strip()
                ordered = [row for row in ordered if (row[0].category_id or "") == category_id]
            ordered = _rows_pin_order_first(ordered)

            if not ordered:
                # 检索确认无命中（[]），或命中全被 plugin_type/类目/审核合法过滤掉。
                # 过滤是用户筛选条件所致，属合法结果：返回空页，不退化为子串 LIKE 跨过滤召回，
                # 否则可能在他类目/他类型召回不相关结果（见评审意见）。
                logger.info(
                    "retrieval no hits after filter: plugin_type=%s keyword=%r hits=%d",
                    plugin_type,
                    keyword,
                    len(item_ids),
                )
                return PluginListResponse(
                    page=query.page,
                    page_size=query.page_size,
                    total=0,
                    items=[],
                )
            else:
                total = len(ordered)
                start = (query.page - 1) * query.page_size
                page_slice = ordered[start:start + query.page_size]
                page_asset_ids = [a.asset_id for a, _, _ in page_slice]
                vrows = version_repo.list_all_by_asset_ids(page_asset_ids)
                vmap: Dict[str, List[MarketAssetVersionDB]] = defaultdict(list)
                for r in vrows:
                    vmap[r.asset_id].append(r)
                items = []
                for asset, latest_file_path, has_icon in page_slice:
                    items.append(
                        _list_item_from_asset(
                            asset,
                            latest_file_path,
                            has_icon,
                            storage,
                            vmap.get(asset.asset_id, []),
                            viewer,
                            market_public_scoped=market_public_scoped,
                            db=db,
                        )
                    )
                return PluginListResponse(
                    page=query.page,
                    page_size=query.page_size,
                    total=total,
                    items=items,
                )
        logger.info("retrieval unavailable for plugin_type=%s, fallback to DB LIKE", plugin_type)

    rows, total = repo.list_plugins(query, viewer=viewer)
    logger.info("List plugins query done: total=%s rows=%s", total, len(rows))
    asset_ids = [a.asset_id for a, _, _ in rows]
    vrows = version_repo.list_all_by_asset_ids(asset_ids)
    vmap: Dict[str, List[MarketAssetVersionDB]] = defaultdict(list)
    for r in vrows:
        vmap[r.asset_id].append(r)
    items = []
    for asset, latest_file_path, has_icon in rows:
        items.append(
            _list_item_from_asset(
                asset,
                latest_file_path,
                has_icon,
                storage,
                vmap.get(asset.asset_id, []),
                viewer,
                market_public_scoped=market_public_scoped,
                db=db,
            )
        )
    return PluginListResponse(
        page=query.page,
        page_size=query.page_size,
        total=total,
        items=items,
    )


def _skill_visible_to_marketplace_viewer(
    asset: MarketAssetDB,
    viewer: ViewerContext,
    db: Session,
) -> bool:
    """公开市场（首页关联详情/互动）：仅已发布对外可见，不含发布者/审核员 bypass。"""
    if not is_skill_like_plugin_type(asset.plugin_type):
        return True
    if (getattr(asset, "visibility", None) or "public").strip().lower() == "private":
        return False
    return is_skill_asset_publicly_visible(
        publish_result=getattr(asset, "publish_result", None),
        moderation_status=getattr(asset, "moderation_status", None),
        public_latest_version=getattr(asset, "public_latest_version", None),
    )


def _skill_visible_to_download_viewer(
    asset: MarketAssetDB,
    viewer: ViewerContext,
    db: Session,
) -> bool:
    return viewer.can_download_skill_asset(asset, db)


def _skill_visible_for_version_detail(
    asset: MarketAssetDB,
    viewer: ViewerContext,
    db: Session | None = None,
) -> bool:
    """版本详情：审核员/发布者/组群授权成员可看；否则按公开市场规则。"""
    return viewer.can_view_skill_asset(asset, db)


def get_plugin_version_detail_service(
    asset_id: str,
    version: str,
    db: Session,
    storage: S3StorageClient,
    *,
    viewer: ViewerContext,
) -> PluginVersionDetail:
    version = _normalize_version(version)
    logger.info("Get plugin version detail request: asset_id=%s version=%s", asset_id, version)
    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)

    asset = asset_repo.get_by_asset_id(asset_id)
    if not asset:
        logger.warning("Get plugin version detail failed: asset not found, asset_id=%s", asset_id)
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Asset not found")
    if not _skill_visible_for_version_detail(asset, viewer, db):
        logger.warning("Get plugin version detail forbidden: moderation, asset_id=%s", asset_id)
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Asset not found")

    version_row = version_repo.get_version(asset_id=asset_id, version=version)
    if not version_row:
        logger.warning(
            "Get plugin version detail failed: version not found, asset_id=%s version=%s",
            asset_id,
            version,
        )
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Version not found")
    if not viewer.can_see_skill_version_row(asset, version_row, db):
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Version not found")

    is_skill_plugin = is_skill_like_plugin_type(asset.plugin_type)
    resolved_publish_result = _resolved_version_publish_result_value(version_row)
    skill_review_visible = bool(is_skill_plugin and settings.skill_review_enabled)
    review_row = None
    needs_review_row_for_reason = bool(
        is_skill_plugin
        and resolved_publish_result == PUBLISH_RESULT_FAILED
        and (getattr(version_row, "moderation_status", None) or "").strip().upper() != MODERATION_REJECTED
    )
    if is_skill_plugin and (skill_review_visible or needs_review_row_for_reason):
        review_row = (
            db.query(MarketSkillReviewDB)
            .filter(
                MarketSkillReviewDB.asset_id == asset_id,
                MarketSkillReviewDB.version_id == version_row.version_id,
            )
            .first()
        )
    review_summary = build_review_summary(review_row) if skill_review_visible else None
    view_count_value = int(asset.view_count or 0)
    try:
        updated_rows = asset_repo.increase_view_count_atomic(asset_id=asset.asset_id)
        if updated_rows == 1:
            db.commit()
            db.refresh(asset)
            view_count_value = int(asset.view_count or 0)
        elif updated_rows != 1:
            logger.warning(
                "increase_view_count unexpected row count=%s asset_id=%s",
                updated_rows,
                asset.asset_id,
            )
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning(
            "increase_view_count failed asset_id=%s: %s",
            asset.asset_id,
            exc,
            exc_info=True,
        )

    # 从 OBS 读取版本专属 readme.md 作为 detail_desc，失败时 fallback 到 DB 值
    # readme.md 版本发布后内容不变，Redis 缓存 7 天
    from plugins_market.core.cache import cache_get, cache_set  # noqa: PLC0415

    version_detail_desc: str | None = None
    v_prefix = _version_prefix_from_file_path(storage, version_row.file_path)
    if v_prefix:
        readme_sha16 = (version_row.artifact_sha256 or "")[:16]
        readme_cache_key = f"vreadme:{asset_id}:{version}:{readme_sha16}"
        cached_readme = cache_get(readme_cache_key)
        if cached_readme is None:
            readme_bytes = storage.read_bytes(f"{v_prefix}readme.md")
            if readme_bytes:
                cached_readme = readme_bytes.decode("utf-8", errors="replace")
                cache_set(readme_cache_key, cached_readme)
        if cached_readme:
            try:
                version_detail_desc = _detail_desc_for_display(asset.plugin_type, cached_readme) or None
            except Exception as e:
                logger.warning("版本 readme.md 解析失败 asset_id=%s version=%s: %s", asset_id, version, e)

    return PluginVersionDetail(
        asset_id=asset.asset_id,
        version=version_row.version,
        asset_type=asset.asset_type,
        plugin_type=asset.plugin_type,
        publish_result=resolved_publish_result,
        publish_failed_reason=_resolve_publish_failed_reason(version_row=version_row, review_row=review_row),
        moderation_status=getattr(asset, "moderation_status", None),
        moderation_reject_reason=getattr(asset, "moderation_reject_reason", None),
        version_moderation_status=getattr(version_row, "moderation_status", None),
        version_moderation_reject_reason=getattr(version_row, "moderation_reject_reason", None),
        viewer_is_market_moderation_admin=viewer.is_market_moderation_admin,
        access_source=_access_source_for_viewer(asset, viewer, db),
        name=asset.name,
        display_name=asset.display_name,
        short_desc=asset.short_desc,
        detail_desc=version_detail_desc or _detail_desc_for_display(asset.plugin_type, asset.detail_desc),
        publisher_id=asset.publisher_id,
        publisher_name=asset.publisher_name,
        tags=asset.tags,
        category_id=asset.category_id,
        category_name=asset.category_name,
        certification=asset.certification,
        changelog=version_row.changelog,
        file_path=version_row.file_path,
        icon_uri=_icon_presigned_url_from_file_path(storage, version_row.file_path, version_row.has_icon),
        review_status=review_row.review_status if skill_review_visible and review_row else None,
        review_failed_reason=review_row.review_failed_reason if skill_review_visible and review_row else None,
        review_summary=review_summary,
        review_sections=review_row.sections_json if skill_review_visible and review_row else None,
        review_mode=review_row.review_mode if skill_review_visible and review_row else None,
        review_engine=review_summary.get("review_engine") if review_summary else None,
        model_name=review_summary.get("model_name") if review_summary else None,
        trace_id=review_row.trace_id if skill_review_visible and review_row else None,
        install_count=int(asset.install_count or 0),
        view_count=view_count_value,
        update_time=int(version_row.create_time) if version_row.create_time is not None else None,
        storage_mode=getattr(asset, "storage_mode", None),
        resolved_commit_sha=getattr(asset, "resolved_commit_sha", None),
        declared_skill_version=getattr(asset, "declared_skill_version", None),
        git_version_display_as_commit=_git_version_display_as_commit(asset, version_row.version),
    )


def _key_from_object_uri(storage: Any, uri_or_key: str | None) -> str | None:
    if not uri_or_key:
        return None
    raw = uri_or_key.strip()
    if not raw:
        return None
    if "://" not in raw:
        return raw
    try:
        p = urlparse(raw)
        path = (p.path or "").lstrip("/")
        bucket = getattr(getattr(storage, "config", None), "bucket_name", None)
        if bucket and path.startswith(f"{bucket}/"):
            return path[len(bucket) + 1:]
        return path
    except Exception:
        return None


def _version_prefix_from_file_path(storage: Any, file_path: str | None) -> str | None:
    prefix = _key_from_object_uri(storage, file_path)
    if not prefix:
        return None
    prefix = prefix.strip()
    return prefix if prefix.endswith("/") else prefix + "/"


def delete_plugin_version_service(
    asset_id: str,
    version: str,
    auth: AuthContext,
    db: Session,
    storage: S3StorageClient,
) -> PluginVersionDeleteData:
    with operation_context(operation_type="delete_plugin_version"):
        bind_operation_resource(resource_type="asset", resource_id=asset_id, resource_version=version)
        logger.info("Delete plugin version request: asset_id=%s version=%s", asset_id, version)
        asset_repo = MarketAssetRepository(db)
        skill_review_repo = MarketSkillReviewRepository(db)
        version_repo = MarketAssetVersionRepository(db)
        grant_repo = MarketGroupSkillGrantRepository(db)

        asset = asset_repo.get_by_asset_id(asset_id)
        if not asset:
            logger.warning("Delete plugin version failed: asset not found, asset_id=%s", asset_id)
            raise _http_exception(status.HTTP_404_NOT_FOUND, "Asset not found")
        if not auth.is_admin and auth.acting_user_id and asset.publisher_id != auth.acting_user_id:
            logger.warning(
                "Delete plugin version forbidden: asset_id=%s acting_user_id=%s publisher_id=%s",
                asset_id,
                auth.acting_user_id,
                asset.publisher_id,
            )
            raise _http_exception(status.HTTP_403_FORBIDDEN, "Insufficient permissions")

        saved_plugin_type = asset.plugin_type
        saved_skill_name = asset.name
        saved_skill_display_name = asset.display_name
        prefixes: list[str] = []

        if version.strip().lower() == "all":
            logger.info("Delete all versions for asset_id=%s", asset_id)
            versions = version_repo.list_versions(asset_id)
            if not versions:
                logger.warning("Delete all versions failed: no versions found, asset_id=%s", asset_id)
                raise _http_exception(status.HTTP_404_NOT_FOUND, "No versions found for asset")
            for v in versions:
                p = _version_prefix_from_file_path(storage, v.file_path)
                if p:
                    prefixes.append(p)
                skill_review_repo.delete_by_version_id(v.version_id)
            version_repo.delete_all_versions(asset_id)
            grant_repo.delete_by_asset(asset_id)
            asset_repo.delete_asset(asset_id)
            logger.info("Delete all versions done: asset deleted, asset_id=%s", asset_id)
        else:
            version = _normalize_version(version)
            logger.info("Delete single version: asset_id=%s version=%s", asset_id, version)
            version_row = version_repo.get_version(asset_id=asset_id, version=version)
            if not version_row:
                logger.warning(
                    "Delete single version failed: version not found, asset_id=%s version=%s",
                    asset_id,
                    version,
                )
                raise _http_exception(status.HTTP_404_NOT_FOUND, "Version not found")
            p = _version_prefix_from_file_path(storage, version_row.file_path)
            if p:
                prefixes.append(p)
            skill_review_repo.delete_by_version_id(version_row.version_id)
            version_repo.delete_version(asset_id, version)
            if version_repo.count_versions(asset_id) == 0:
                grant_repo.delete_by_asset(asset_id)
                asset_repo.delete_asset(asset_id)
                logger.info("Delete single version done: no versions left, asset deleted, asset_id=%s", asset_id)
            else:
                remaining = version_repo.list_versions(asset_id)
                if remaining:
                    new_latest = remaining[0].version
                    fresh_asset = asset_repo.get_by_asset_id(asset_id)
                    if fresh_asset:
                        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                        fresh_asset.latest_version = new_latest
                        fresh_asset.update_time = now_ms
                        db.add(fresh_asset)
                        _apply_skill_asset_aggregate_from_versions(db, asset_id)
                        logger.info(
                            "Delete single version done: latest_version updated, asset_id=%s latest_version=%s",
                            asset_id,
                            new_latest,
                        )

        for p in prefixes:
            dr = storage.delete_prefix(p)
            if not dr.get("success"):
                logger.error(
                    "Delete storage prefix failed: asset_id=%s version=%s prefix=%s errors=%s",
                    asset_id,
                    version,
                    p,
                    dr.get("errors", []),
                )
                db.rollback()
                raise _http_exception(
                    status.HTTP_502_BAD_GATEWAY,
                    "Object storage delete failed",
                    details={
                        "prefix": p,
                        "errors": dr.get("errors", []),
                    },
                )
            logger.info("Delete storage prefix success: asset_id=%s prefix=%s", asset_id, p)

        db.commit()
        logger.info("Delete plugin version success: asset_id=%s version=%s", asset_id, version)
        return PluginVersionDeleteData(
            asset_id=asset_id,
            version=version,
            plugin_type=saved_plugin_type,
            skill_name=saved_skill_name,
            skill_display_name=saved_skill_display_name,
        )


def _build_artifact_key(
    publisher_id: str,
    asset_id: str,
    version: str,
    name: str,
    plugin_type: str | None = None,
) -> str:
    safe_name = name.strip().replace(" ", "-")
    root = _storage_root(plugin_type)
    return f"{root}/{publisher_id}/{asset_id}/{version}/{safe_name}_{version}.zip"


def _build_raw_artifact_key(
    publisher_id: str,
    asset_id: str,
    version: str,
    name: str,
    plugin_type: str | None = None,
) -> str:
    safe_name = name.strip().replace(" ", "-")
    root = _storage_root(plugin_type)
    return f"{root}/{publisher_id}/{asset_id}/{version}/{safe_name}_{version}.raw.zip"


def _resolve_artifact_key_from_version_row(
    storage: S3StorageClient, version_row: MarketAssetVersionDB, fallback_key: str
) -> str:
    head = storage.head_object(fallback_key)
    if head.get("success"):
        return fallback_key
    prefix = _version_prefix_from_file_path(storage, version_row.file_path)
    if not prefix:
        return fallback_key
    keys = storage.list_keys(prefix)
    zip_keys = [k for k in keys if k.lower().endswith(".zip") and not k.lower().endswith(".raw.zip")]
    return zip_keys[0] if zip_keys else fallback_key


def _extract_size_and_checksum_from_head(head: dict[str, Any]) -> tuple[int | None, str]:
    metadata = head.get("metadata") or {}
    checksum_sha256 = str(metadata.get("sha256") or "").strip().lower()
    size_meta = str(metadata.get("size") or "").strip()
    size: int | None = None
    if size_meta:
        try:
            size = int(size_meta)
        except ValueError:
            size = None
    if size is None:
        try:
            size = int(head.get("size")) if head.get("size") is not None else None
        except Exception:
            size = None
    return size, checksum_sha256


def _download_object_to_local_file(storage: S3StorageClient, key: str, target_file: str) -> None:
    body = None
    try:
        resp = storage.s3_client.get_object(Bucket=storage.config.bucket_name, Key=key)
        body = resp.get("Body")
        if body is None:
            raise BusinessError(
                code=500,
                error="storage_error",
                message=f"读取对象 body 为空: key={key}",
                error_code="SKILLHUB_STORAGE_ERROR",
                error_class="upstream",
            )
        with open(target_file, "wb") as wf:
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                wf.write(chunk)
    except PublishError:
        raise
    except Exception as e:
        raise BusinessError(
            code=500,
            error="storage_error",
            message=f"下载对象失败: {e}",
            error_code="SKILLHUB_STORAGE_ERROR",
            error_class="upstream",
        ) from e
    finally:
        if body is not None:
            try:
                body.close()
            except Exception:
                pass


def _compute_file_sha256_and_size(path: str) -> tuple[str, int]:
    hasher = hashlib.sha256()
    total = 0
    with open(path, "rb") as rf:
        while True:
            chunk = rf.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            total += len(chunk)
    return hasher.hexdigest(), total


def _resolve_package_root_by_first_skill_md(extract_dir: str) -> str:
    """在解压目录中找到第一个 SKILL.md，并将其所在目录作为 package_root。"""
    first_skill_md: str | None = None
    for cur_root, dirs, files in os.walk(extract_dir):
        dirs.sort()
        files.sort()
        for filename in files:
            if filename.lower() == "skill.md":
                first_skill_md = os.path.join(cur_root, filename)
                break
        if first_skill_md:
            break

    if not first_skill_md:
        raise BusinessError(
            code=500,
            error="raw_zip_build_failed",
            message="原始插件包结构不合法：缺少 SKILL.md",
            error_code="SKILLHUB_PLUGIN_RAW_ZIP_BUILD_FAILED",
            error_class="internal",
        )
    package_root = os.path.dirname(first_skill_md)
    if not os.path.basename(package_root).strip():
        raise BusinessError(
            code=500,
            error="raw_zip_build_failed",
            message="原始插件包结构不合法：无法从 SKILL.md 推导技能目录名",
            error_code="SKILLHUB_PLUGIN_RAW_ZIP_BUILD_FAILED",
            error_class="internal",
        )
    return package_root


def _build_raw_zip_from_original(
    *,
    source_zip: str,
    output_zip: str,
    skill_name: str,
    version: str,
) -> None:
    """从已发布 zip 生成 raw.zip：仅打包首个 SKILL.md 所在目录本身（不追加额外前缀）。"""
    _ = (skill_name, version)

    with tempfile.TemporaryDirectory(prefix="market_raw_zip_extract_") as extract_dir:
        with zipfile.ZipFile(source_zip, "r") as zf:
            zf.extractall(extract_dir)

        package_root = _resolve_package_root_by_first_skill_md(extract_dir)
        parent_dir = os.path.dirname(package_root)
        archive_base = os.path.splitext(output_zip)[0]
        built_zip = shutil.make_archive(
            base_name=archive_base,
            format="zip",
            root_dir=package_root,
            base_dir=".",
        )
        if os.path.normpath(built_zip) != os.path.normpath(output_zip):
            shutil.move(built_zip, output_zip)


def _ensure_non_cli_raw_artifact(
    *,
    storage: S3StorageClient,
    old_key: str,
    raw_key: str,
    skill_name: str,
    version: str,
) -> tuple[str, int, str]:
    raw_head = storage.head_object(raw_key)
    if raw_head.get("success"):
        raw_size, raw_checksum = _extract_size_and_checksum_from_head(raw_head)
        if raw_size is not None and raw_checksum:
            return raw_key, int(raw_size), raw_checksum

    try:
        with tempfile.TemporaryDirectory(prefix="market_raw_zip_build_") as tmp_dir:
            old_zip_file = os.path.join(tmp_dir, "origin.zip")
            raw_zip_file = os.path.join(tmp_dir, "origin.raw.zip")
            _download_object_to_local_file(storage, old_key, old_zip_file)
            _build_raw_zip_from_original(
                source_zip=old_zip_file,
                output_zip=raw_zip_file,
                skill_name=skill_name,
                version=version,
            )
            checksum, size = _compute_file_sha256_and_size(raw_zip_file)
            with open(raw_zip_file, "rb") as rf:
                storage.s3_client.put_object(
                    Bucket=storage.config.bucket_name,
                    Key=raw_key,
                    Body=rf,
                    Metadata={"sha256": checksum, "size": str(size)},
                )
            return raw_key, int(size), checksum
    except PublishError:
        raise
    except Exception as e:
        raise BusinessError(
            code=500,
            error="raw_zip_build_failed",
            message=f"生成或上传 raw.zip 失败: {e}",
            error_code="SKILLHUB_PLUGIN_RAW_ZIP_BUILD_FAILED",
            error_class="internal",
        ) from e


def _compute_latest_approved_skill_version_row(
    *,
    asset_id: str,
    version_repo: MarketAssetVersionRepository,
) -> MarketAssetVersionDB | None:
    """Semantically newest APPROVED skill version row (matches ``public_latest_version`` aggregate)."""
    versions = version_repo.list_versions_chronological(asset_id)
    public_row: MarketAssetVersionDB | None = None
    for v in versions:
        ms = moderation_coalesce_display(getattr(v, "moderation_status", None))
        if ms != MODERATION_APPROVED:
            continue
        if public_row is None:
            public_row = v
        else:
            ct = v.create_time or 0
            pct = public_row.create_time or 0
            if ct > pct or (ct == pct and (v.version or "") > (public_row.version or "")):
                public_row = v
    return public_row


def _refresh_skill_asset_listing_fields_from_public_artifact(
    *,
    db: Session,
    asset_id: str,
    storage: S3StorageClient,
) -> None:
    """
    将 market_assets 上用于列表/详情的展示字段与「当前对外已通过审」版本包内 metadata 对齐。

    在 Skill 新版本待审时发布流程会刻意不覆盖主表 display_name 等，待审核通过后在事务内调用本函数写回。
    """
    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)
    asset = asset_repo.get_by_asset_id(asset_id)
    if not asset or not is_skill_like_plugin_type(asset.plugin_type):
        return
    public_v = _compute_latest_approved_skill_version_row(asset_id=asset_id, version_repo=version_repo)
    if not public_v:
        return
    key = _build_artifact_key(
        publisher_id=asset.publisher_id,
        asset_id=asset.asset_id,
        version=public_v.version or "",
        name=asset.name,
        plugin_type=asset.plugin_type,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="market_skill_listing_sync_") as tmp_dir:
            zip_path = os.path.join(tmp_dir, "pkg.zip")
            _download_object_to_local_file(storage, key, zip_path)
            with open(zip_path, "rb") as rf:
                content = rf.read()
            meta = extract_plugin_metadata(content)
    except PublishError:
        raise
    except Exception as e:
        raise PublishError(
            code=500,
            error="storage_error",
            message=f"读取已通过审版本包以同步展示字段失败: {e}",
            error_code="SKILLHUB_STORAGE_ERROR",
            error_class="upstream",
        ) from e

    disp = (meta.get("display_name") or "").strip()
    short_desc = meta.get("short_desc")
    if isinstance(short_desc, str) and len(short_desc) > MARKET_ASSET_SHORT_DESC_MAX_LEN:
        short_desc = short_desc[:MARKET_ASSET_SHORT_DESC_MAX_LEN]
    detail_desc = meta.get("detail_desc")
    tags = meta.get("tags") or []

    asset.display_name = disp or asset.display_name
    asset.short_desc = short_desc
    asset.detail_desc = detail_desc
    asset.tags = tags if tags else None
    db.add(asset)


def _resolve_latest_version_for_download(
    *,
    asset_id: str,
    latest_version: str | None,
    version_repo: MarketAssetVersionRepository,
):
    if latest_version:
        row = version_repo.get_version(asset_id=asset_id, version=latest_version)
        if row:
            return row
    return version_repo.get_latest_version(asset_id=asset_id)


def moderate_skill_asset_service(
    *,
    asset_id: str,
    action: str,
    reason: str | None,
    version: str | None,
    auth: AuthContext,
    db: Session,
    storage: S3StorageClient,
) -> SkillModerationResult:
    if not auth.is_market_moderation_admin:
        raise _http_exception(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)
    asset = asset_repo.get_by_asset_id(asset_id)
    if not asset:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Asset not found")
    # Inject hint early so audit_failed has name even if subsequent validation fails
    set_audit_hint(
        skill_name=(getattr(asset, "name", None) or "").strip() or None,
        skill_display_name=(getattr(asset, "display_name", None) or "").strip() or None,
    )
    if not is_skill_like_plugin_type(asset.plugin_type):
        raise PublishError(
            code=400,
            error="not_skill",
            message="仅支持对 Skill / TeamSkills 类型资源进行审核",
            error_code="SKILLHUB_PLUGIN_NOT_SKILL",
            error_class="validation",
        )
    if (auth.acting_user_id or "").strip() == (asset.publisher_id or "").strip():
        raise BusinessError(
            code=403,
            error="self_moderation_forbidden",
            message="审核员不能审核自己发布的 Skill",
            error_code="SKILLHUB_REVIEW_SELF_MODERATION_FORBIDDEN",
            error_class="permission",
        )
    vstr = (version or "").strip() or None
    if not vstr:
        vstr = (asset.latest_version or "").strip()
    if not vstr:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Version not found")
    vrow = version_repo.lock_version_for_update(asset_id=asset_id, version=vstr)
    if not vrow:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Version not found")

    raw_moderation_status = (getattr(vrow, "moderation_status", None) or "").strip().upper()
    cur = moderation_coalesce_display(getattr(vrow, "moderation_status", None))
    current_publish_result = coalesce_skill_publish_result(
        getattr(vrow, "publish_result", None),
        getattr(vrow, "moderation_status", None),
    )
    act = (action or "").strip().lower()
    if act == "approve":
        if current_publish_result == PUBLISH_RESULT_REVIEWING:
            raise PublishError(
                code=400,
                error="invalid_moderation_state",
                message="Skill 仍处于系统审查中，暂不可执行人工审核",
                error_code="SKILLHUB_REVIEW_MODERATION_STATE_INVALID",
                error_class="validation",
            )
        if not is_skill_in_manual_moderation_stage(vrow.publish_result, vrow.moderation_status):
            if current_publish_result == PUBLISH_RESULT_SUCCESS:
                return _skill_moderation_result_from_version(asset_id=asset.asset_id, version_row=vrow)
            raise PublishError(
                code=400,
                error="invalid_moderation_state",
                message="当前 Skill 未进入人工审核阶段",
                error_code="SKILLHUB_REVIEW_MODERATION_STATE_INVALID",
                error_class="validation",
            )
        if cur == MODERATION_APPROVED and current_publish_result == PUBLISH_RESULT_SUCCESS:
            db.refresh(vrow)
            return _skill_moderation_result_from_version(asset_id=asset.asset_id, version_row=vrow)
        if raw_moderation_status not in (MODERATION_PENDING, MODERATION_REJECTED):
            raise PublishError(
                code=400,
                error="invalid_moderation_state",
                message="当前版本审核状态不允许执行通过操作",
                error_code="SKILLHUB_REVIEW_MODERATION_STATE_INVALID",
                error_class="validation",
            )
        vrow.moderation_status = MODERATION_APPROVED
        vrow.moderation_reject_reason = None
        vrow.publish_result = PUBLISH_RESULT_SUCCESS
    elif act == "reject":
        if current_publish_result == PUBLISH_RESULT_REVIEWING:
            raise PublishError(
                code=400,
                error="invalid_moderation_state",
                message="Skill 仍处于系统审查中，暂不可执行人工审核",
                error_code="SKILLHUB_REVIEW_MODERATION_STATE_INVALID",
                error_class="validation",
            )
        if not is_skill_in_manual_moderation_stage(vrow.publish_result, vrow.moderation_status):
            raise PublishError(
                code=400,
                error="invalid_moderation_state",
                message="当前 Skill 未进入人工审核阶段",
                error_code="SKILLHUB_REVIEW_MODERATION_STATE_INVALID",
                error_class="validation",
            )
        if cur == MODERATION_APPROVED or current_publish_result == PUBLISH_RESULT_SUCCESS:
            raise PublishError(
                code=409,
                error="moderation_version_locked",
                message="该版本已审核通过，不可驳回。",
                error_code="SKILLHUB_REVIEW_VERSION_LOCKED",
                error_class="conflict",
            )
        if raw_moderation_status == MODERATION_REJECTED:
            raise PublishError(
                code=409,
                error="already_rejected",
                message="该版本已被驳回，请勿重复驳回；可先「审核通过」或等待发布者更新版本。",
                error_code="SKILLHUB_REVIEW_ALREADY_REJECTED",
                error_class="conflict",
            )
        if raw_moderation_status != MODERATION_PENDING:
            raise PublishError(
                code=400,
                error="invalid_moderation_state",
                message="当前审核状态不允许执行驳回操作",
                error_code="SKILLHUB_REVIEW_MODERATION_STATE_INVALID",
                error_class="validation",
            )
        r = (reason or "").strip()
        if not r:
            raise BusinessError(
                code=422,
                error="reason_required",
                message="审核不通过时必须填写原因",
                error_code="SKILLHUB_REVIEW_REASON_REQUIRED",
                error_class="validation",
            )
        vrow.moderation_status = MODERATION_REJECTED
        vrow.moderation_reject_reason = r
        vrow.publish_result = PUBLISH_RESULT_FAILED
    else:
        raise BusinessError(
            code=400,
            error="invalid_action",
            message="action 必须为 approve 或 reject",
            error_code="SKILLHUB_REVIEW_ACTION_INVALID",
            error_class="validation",
        )
    db.add(vrow)
    _apply_skill_asset_aggregate_from_versions(db, asset_id)
    if act == "approve":
        _refresh_skill_asset_listing_fields_from_public_artifact(
            db=db,
            asset_id=asset_id,
            storage=storage,
        )
    publisher_id_for_notify = (asset.publisher_id or "").strip()
    db.commit()
    db.refresh(asset)
    db.refresh(vrow)
    dn = (getattr(asset, "display_name", None) or "").strip() or (getattr(asset, "name", None) or "").strip()
    sn = (getattr(asset, "name", None) or "").strip() or asset_id
    rr_audit: str | None = None
    if act == "reject":
        rr_audit = (getattr(vrow, "moderation_reject_reason", None) or "").strip() or None
    act_upper = Action.APPROVE if act == "approve" else Action.REJECT
    if act == "approve":
        detail_cn = f"审核通过 Skill「{dn}」({sn}) v{vstr}"
    else:
        detail_cn = f"驳回 Skill「{dn}」({sn}) v{vstr}，原因：{rr_audit or '—'}"
    audit_log(
        event_type=EVENT_SKILL_MODERATION,
        action=act_upper,
        operator_id=auth.acting_user_id,
        operator_name=auth.acting_user_name,
        resource_type=ResourceType.SKILL,
        resource_id=asset_id,
        resource_version=vstr,
        result=Result.SUCCESS,
        detail=detail_cn,
        ip_address=auth.ip_address,
        user_agent=auth.user_agent,
        extra={
            "skill_name": sn,
            "skill_display_name": (getattr(asset, "display_name", None) or "").strip() or None,
            "reject_reason": rr_audit,
        },
    )
    if act in ("approve", "reject") and publisher_id_for_notify:
        try:
            if act == "approve":
                notify_publisher_skill_manual_review_approved(db, publisher_id=publisher_id_for_notify)
            else:
                notify_publisher_skill_manual_review_rejected(db, publisher_id=publisher_id_for_notify)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify publisher review finished failed: %s", exc)
    return _skill_moderation_result_from_version(asset_id=asset.asset_id, version_row=vrow)


def _audit_created_at_ms(created_at) -> int:
    if not created_at:
        return 0
    ca = created_at
    if ca.tzinfo is None:
        ca = ca.replace(tzinfo=_BJ_TZ)
    return int(ca.timestamp() * 1000)


def _extra_field_str(value: Any) -> str:
    """Normalize audit `extra` JSON values to a stripped string (handles non-string legacy data)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _extra_field_optional_str(value: Any) -> Optional[str]:
    s = _extra_field_str(value)
    return s or None


def list_my_skill_moderation_audits_service(
    *,
    auth: AuthContext,
    db: Session,
    page: int,
    page_size: int,
) -> SkillModerationAuditListResponse:
    if not auth.is_market_moderation_admin:
        raise _http_exception(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    safe_page = max(1, page)
    safe_size = min(max(1, page_size), 100)
    rows, total = list_skill_moderation_audit_logs_for_operator(
        db,
        operator_id=auth.acting_user_id,
        page=safe_page,
        page_size=safe_size,
    )
    items: List[SkillModerationAuditListItem] = []
    for log in rows:
        extra_raw = log.extra
        extra: Dict[str, Any] = extra_raw if isinstance(extra_raw, dict) else {}
        sn = _extra_field_str(extra.get("skill_name")) or _extra_field_str(log.resource_id)
        sd = _extra_field_optional_str(extra.get("skill_display_name"))
        rr = _extra_field_optional_str(extra.get("reject_reason"))
        maj: Literal["APPROVE", "REJECT"] = "REJECT" if (log.action or "").strip().upper() == "REJECT" else "APPROVE"
        if maj == "APPROVE":
            rr = None
        ver = _extra_field_str(log.resource_version)
        items.append(
            SkillModerationAuditListItem(
                event_id=log.event_id,
                asset_id=_extra_field_str(log.resource_id),
                skill_name=sn or _extra_field_str(log.resource_id),
                skill_display_name=sd,
                version=ver or "—",
                moderation_action=maj,
                reject_reason=rr,
                created_at_ms=_audit_created_at_ms(log.created_at),
            )
        )
    return SkillModerationAuditListResponse(
        page=safe_page,
        page_size=safe_size,
        total=total,
        items=items,
    )


def get_download_info(
    *,
    asset_id: str,
    version: str | None = None,
    db: Session,
    storage: S3StorageClient,
    fetch_user_id: str | None = None,
    viewer: ViewerContext,
    is_cli_download: bool = False,
) -> PluginDownloadData:
    """根据 asset_id（可选 version）返回预签名下载信息。"""
    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)
    fetch_repo = PluginFetchRecordRepository(db)

    asset = asset_repo.get_by_asset_id(asset_id)
    if not asset:
        raise PublishError(
            code=404,
            error="plugin_not_found",
            message=f"插件 '{asset_id}' 不存在",
            error_code="SKILLHUB_PLUGIN_NOT_FOUND",
            error_class="not_found",
        )
    if not _skill_visible_to_download_viewer(asset, viewer, db):
        raise PublishError(
            code=404,
            error="plugin_not_found",
            message=f"插件 '{asset_id}' 不存在或暂不可下载",
            error_code="SKILLHUB_PLUGIN_NOT_FOUND",
            error_class="not_found",
        )

    version = (version or "").strip() or None
    if version is not None:
        if not is_valid_market_version(version):
            raise PublishError(
                code=400,
                error="invalid_version",
                data={"version": version},
                message=("version 参数格式错误：应为 x.y.z（如 1.0.0），不接受 v 前缀；" "或 commit 7 位小写 hex"),
                error_code="SKILLHUB_PLUGIN_VERSION_INVALID",
                error_class="validation",
            )
        version = _normalize_version(version)
        version_row = version_repo.get_version(asset_id=asset.asset_id, version=version)
        if not version_row:
            raise PublishError(
                code=404,
                error="version_not_found",
                data={"asset_id": asset.asset_id, "version": version},
                message=f"插件 '{asset.name}' 不存在版本 '{version}'",
                error_code="SKILLHUB_PLUGIN_VERSION_NOT_FOUND",
                error_class="not_found",
            )
        if not viewer.can_download_skill_version_row(asset, version_row, db):
            raise PublishError(
                code=404,
                error="plugin_not_found",
                message=f"插件 '{asset.asset_id}' 不存在或暂不可下载",
                error_code="SKILLHUB_PLUGIN_NOT_FOUND",
                error_class="not_found",
            )
    else:
        pt = (asset.plugin_type or "").strip().lower()
        if is_skill_like_plugin_type(pt) and not viewer.is_market_moderation_admin:
            acl_source = viewer.skill_asset_access_source(asset, db)
            version_row = None
            if acl_source == "owner":
                version_row = _resolve_latest_version_for_download(
                    asset_id=asset.asset_id,
                    latest_version=asset.latest_version,
                    version_repo=version_repo,
                )
            else:
                plv = (getattr(asset, "public_latest_version", None) or "").strip() or None
                if plv:
                    cand = version_repo.get_version(asset_id=asset.asset_id, version=plv)
                    if cand is not None and viewer.can_download_skill_version_row(asset, cand, db):
                        version_row = cand
                if version_row is None:
                    version_row = _compute_latest_approved_skill_version_row(
                        asset_id=asset.asset_id,
                        version_repo=version_repo,
                    )
            if not version_row or not viewer.can_download_skill_version_row(asset, version_row, db):
                raise PublishError(
                    code=404,
                    error="plugin_not_found",
                    message=f"插件 '{asset.asset_id}' 不存在或暂不可下载",
                )
        else:
            version_row = _resolve_latest_version_for_download(
                asset_id=asset.asset_id,
                latest_version=asset.latest_version,
                version_repo=version_repo,
            )
    if not version_row:
        raise PublishError(
            code=404,
            error="plugin_not_found",
            message=f"插件 '{asset.asset_id}' 暂无可下载版本",
            error_code="SKILLHUB_PLUGIN_NOT_FOUND",
            error_class="not_found",
        )

    normal_key = _resolve_artifact_key_from_version_row(
        storage,
        version_row,
        _build_artifact_key(
            publisher_id=asset.publisher_id,
            asset_id=asset.asset_id,
            version=version_row.version,
            name=asset.name,
            plugin_type=asset.plugin_type,
        ),
    )
    key = normal_key
    size: int | None = None
    checksum_sha256 = ""

    plugin_type_norm = (asset.plugin_type or "").strip().lower()
    if not is_cli_download and is_skill_like_plugin_type(plugin_type_norm):
        raw_key = _build_raw_artifact_key(
            publisher_id=asset.publisher_id,
            asset_id=asset.asset_id,
            version=version_row.version,
            name=asset.name,
            plugin_type=asset.plugin_type,
        )
        key, size, checksum_sha256 = _ensure_non_cli_raw_artifact(
            storage=storage,
            old_key=normal_key,
            raw_key=raw_key,
            skill_name=asset.name,
            version=version_row.version,
        )

    head = storage.head_object(key)
    if not head.get("success"):
        if head.get("not_found"):
            raise PublishError(
                code=404,
                error="version_deleted",
                message="插件版本已被删除",
                error_code="SKILLHUB_PLUGIN_VERSION_DELETED",
                error_class="not_found",
            )
        raise PublishError(
            code=500,
            error="storage_error",
            message=f"读取插件包元数据失败: {head.get('error', 'unknown')}",
            error_code="SKILLHUB_STORAGE_ERROR",
            error_class="upstream",
        )

    download_filename = f"{asset.name}_{version_row.version}.zip"
    download_url = storage.presigned_get_url(key, download_filename=download_filename)

    if size is None or not checksum_sha256:
        size, checksum_sha256 = _extract_size_and_checksum_from_head(head)

    if size is None or not checksum_sha256:
        raise PublishError(
            code=500,
            error="storage_error",
            message="插件包对象缺少必要的元数据（sha256/size），请重新发布该版本",
            error_code="SKILLHUB_STORAGE_ERROR",
            error_class="upstream",
        )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    try:
        updated_rows = asset_repo.increase_install_count_atomic(
            asset_id=asset.asset_id,
            now_ms=now_ms,
        )
        if updated_rows != 1:
            raise PublishError(
                code=500,
                error="db_error",
                message=f"更新下载统计失败：asset_id={asset.asset_id}",
                error_code="SKILLHUB_DATABASE_ERROR",
                error_class="internal",
            )

        fetch_repo.create_fetch_record(
            asset_id=asset.asset_id,
            version_id=version_row.version_id,
            fetch_user_id=fetch_user_id,
            create_time=now_ms,
        )
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        raise PublishError(
            code=500,
            error="db_error",
            message="更新下载统计失败",
            error_code="SKILLHUB_DATABASE_ERROR",
            error_class="internal",
        ) from e

    return PluginDownloadData(
        download_url=download_url,
        asset_id=asset.asset_id,
        name=asset.name,
        display_name=(getattr(asset, "display_name", None) or "").strip() or None,
        version=version_row.version,
        file_size=int(size),
        checksum_sha256=checksum_sha256,
    )


# ---------------------------------------------------------------------------
# Files tab：版本包内文件列表 / 文件内容
# ---------------------------------------------------------------------------

_FILES_CACHE_TTL = 86400 * 7  # 7 天；版本不可变，缓存可以很长
_FILE_CONTENT_MAX_BYTES = 50 * 1024 * 1024  # 详情文件包预览上限 50 MB，超限不读入接口响应
_HIDDEN_FILES = {"plugin.yaml", "icon.png"}


def _zip_strip_prefix(paths: list[str]) -> str:
    """
    返回 zip 内所有文件共享的公共目录前缀（含末尾 /）。
    循环剥除：每次只要所有路径的第一段相同且确实是目录（至少一条路径含 /），就继续剥。
    异常时返回空串，降级为原始路径。
    """
    try:
        current = list(paths)
        prefix = ""
        while True:
            if not current or not any("/" in p for p in current):
                break
            tops = {p.split("/")[0] for p in current}
            if len(tops) != 1:
                break
            top = tops.pop()
            seg = top + "/"
            if not all(p.startswith(seg) for p in current):
                break
            prefix += seg
            current = [p[len(seg):] for p in current]
            # 剥完后若有路径变为空串，说明该层本身就是文件，回退
            if any(p == "" for p in current):
                prefix = prefix[: -len(seg)]
                break
        return prefix
    except Exception:
        return ""


def _load_zip_from_obs(storage: S3StorageClient, version_row: MarketAssetVersionDB) -> zipfile.ZipFile | None:
    """从 OBS 下载 zip 包到内存并返回 ZipFile 对象；找不到或失败返回 None。
    使用原始上传包（排除 raw.zip），路径通过 _zip_strip_prefix 剥前缀。
    """
    prefix = _version_prefix_from_file_path(storage, version_row.file_path)
    if not prefix:
        return None
    keys = storage.list_keys(prefix)
    zip_key = next((k for k in keys if k.endswith(".zip") and not k.endswith(".raw.zip")), None)
    if not zip_key:
        logger.warning("_load_zip_from_obs: no zip found under prefix=%s", prefix)
        return None
    data = storage.read_bytes(zip_key)
    if not data:
        logger.warning("_load_zip_from_obs: read_bytes returned None for key=%s", zip_key)
        return None
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        logger.warning("_load_zip_from_obs: bad zip key=%s: %s", zip_key, e)
        return None


_TEXT_EXTENSIONS = {
    ".py",
    ".yaml",
    ".yml",
    ".json",
    ".jsonl",
    ".md",
    ".txt",
    ".sh",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".xml",
    ".html",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".csv",
    ".log",
    ".env",
    ".sql",
    ".java",
    ".go",
    ".rs",
    ".cpp",
    ".c",
    ".h",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".r",
    ".lua",
    ".pl",
    ".bat",
    ".ps1",
}


def _is_text_file(path: str) -> bool:
    dot = path.rfind(".")
    return dot < 0 or path[dot:].lower() in _TEXT_EXTENSIONS


def get_version_file_list_service(
    asset_id: str,
    version: str,
    db: Session,
    storage: S3StorageClient,
    *,
    viewer: ViewerContext,
    with_content: str | None = None,
) -> "VersionFilesData":
    """
    返回文件列表，可选附带指定文件的文本内容（with_content）。
    列表与内容各自独立缓存于 Redis（TTL 7 天）；缓存命中时仍走 DB 鉴权，只跳过 OBS 下载。
    with_content 匹配时不区分大小写（以 zip 内实际文件名为准）。
    """
    from plugins_market.core.cache import cache_get, cache_set
    from plugins_market.schemas.plugin import VersionFilesData, VersionFileEntry

    # 鉴权始终先行，确保缓存数据不会越权返回
    asset_repo = MarketAssetRepository(db)
    version_repo = MarketAssetVersionRepository(db)

    asset = asset_repo.get_by_asset_id(asset_id)
    if not asset:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Asset not found")
    if not _skill_visible_for_version_detail(asset, viewer, db):
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Asset not found")

    version_row = version_repo.get_version(asset_id=asset_id, version=version)
    if not version_row:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Version not found")
    if not viewer.can_see_skill_version_row(asset, version_row, db):
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Version not found")

    sha16 = (version_row.artifact_sha256 or "")[:16]
    list_cache_key = f"vfiles:{asset_id}:{version}:{sha16}"
    want_content = bool(with_content and _is_text_file(with_content))
    content_cache_key = f"vfile:{asset_id}:{version}:{sha16}:{with_content}" if want_content else None

    cached_list = cache_get(list_cache_key)
    files: list[dict] | None = None
    if cached_list:
        try:
            files = json.loads(cached_list)
        except Exception:
            pass

    content: str | None = None
    content_path: str | None = None
    if want_content and files is not None:
        cached_content = cache_get(content_cache_key)  # type: ignore[arg-type]
        if cached_content is not None:
            content = cached_content
            want_lower = (with_content or "").lower()
            content_path = next((f["path"] for f in files if f["path"].lower() == want_lower), None)

    # 两者都命中缓存，无需打开 zip
    if files is not None and (not want_content or content is not None):
        return VersionFilesData(
            files=[VersionFileEntry(**f) for f in files],
            content=content,
            content_path=content_path,
        )

    zf = _load_zip_from_obs(storage, version_row)
    if not zf:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Package not found")

    with zf:
        all_file_names = [
            info.filename
            for info in zf.infolist()
            if not info.is_dir() and info.filename.split("/")[-1] not in _HIDDEN_FILES
        ]
        prefix = _zip_strip_prefix(all_file_names)

        if files is None:
            files = sorted(
                (
                    {"path": info.filename[len(prefix):], "size": info.file_size}
                    for info in zf.infolist()
                    if not info.is_dir() and info.filename.split("/")[-1] not in _HIDDEN_FILES
                ),
                key=lambda f: (
                    (
                        0
                        if f["path"].split("/")[-1].lower() == "workflow.md"
                        else 1 if f["path"].split("/")[-1].lower() == "skill.md" else 2
                    ),
                    f["path"].lower(),
                ),
            )
            cache_set(list_cache_key, json.dumps(files), _FILES_CACHE_TTL)

        if want_content and content is None:
            want_lower = (with_content or "").lower()
            matched_stripped = next(
                (f["path"] for f in files if f["path"].lower() == want_lower),  # type: ignore[union-attr]
                None,
            )
            if matched_stripped:
                actual_path = prefix + matched_stripped
                try:
                    info = zf.getinfo(actual_path)
                    if info.file_size <= _FILE_CONTENT_MAX_BYTES:
                        content = zf.read(actual_path).decode("utf-8", errors="replace")
                        cache_set(content_cache_key, content, _FILES_CACHE_TTL)  # type: ignore[arg-type]
                        content_path = matched_stripped
                except Exception:
                    pass

    return VersionFilesData(
        files=[VersionFileEntry(**f) for f in (files or [])],
        content=content,
        content_path=content_path,
    )
