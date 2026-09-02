# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from plugins_market.core.errors import PublishError
from plugins_market.core.logging import get_logger
from plugins_market.core.operation_log import operation_log_fields, safe_error_summary
from plugins_market.core.s3_storage_client import S3StorageClient
from plugins_market.imports.bundle_safe_extract import skill_import_extract_zip_to_dir
from plugins_market.imports.yaml_util import load_json_object_file, load_plugin_yaml
from plugins_market.validation.localized_manifest import localized_manifest_text
from plugins_market.validation.constants import (
    MAX_JSON_BYTES,
    MAX_ZIP_ENTRIES,
)
from plugins_market.imports.skill_entries import detect_import_entry_type, entry_to_publish_zip
from plugins_market.schemas.plugin import (
    AssetImportItemResult,
    AssetImportResponse,
    AssetImportSummary,
    PluginPublishResult,
    SkillImportItemResult,
    SkillImportResponse,
    SkillImportSummary,
)
from plugins_market.services.plugin import publish

logger = get_logger(__name__)


def _log_skill_import_entry(*, stage: str, result: str, entry: str, **fields: Any) -> None:
    log_method = logger.info if result in {"started", "success", "skipped", "invalid", "denied"} else logger.warning
    log_method(
        "skill import entry",
        **operation_log_fields(stage=stage, result=result, entry=entry, **fields),
    )


def _skill_import_merge_tags(raw: object, fallback: list[str]) -> list[str]:
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if t is not None and str(t).strip()]
    return list(fallback)


def _skill_import_parse_entries_map(manifest: dict[str, Any]) -> dict[str, Any]:
    """解析 ``manifest.json``：根上每个 **值为 object** 的键视为「顶层目录名 → 条目配置」。"""
    return {k: v for k, v in manifest.items() if isinstance(v, dict)}


def _asset_import_parse_entries_map(manifest: dict[str, Any]) -> dict[str, Any]:
    """Parse the multi-asset ``entries`` layout while retaining legacy maps."""
    entries = manifest.get("entries")
    if isinstance(entries, dict):
        return {k: v for k, v in entries.items() if isinstance(v, dict)}
    return _skill_import_parse_entries_map(manifest)


def _skill_import_entry_version_desc(entry_overrides: dict[str, Any]) -> str | None:
    """条目配置中的 ``version_desc``；缺省或非空串则 ``None``。"""
    raw = entry_overrides.get("version_desc")
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _resolve_import_fail_identity(entry: Path) -> tuple[str, str]:
    """规范化失败时从 plugin.yaml / manifest.json 尽力补全 name 与 version。"""
    fail_name, fail_version = "", ""
    plugin_yaml_path = entry / "plugin.yaml"
    if plugin_yaml_path.is_file():
        try:
            yaml_data = load_plugin_yaml(str(plugin_yaml_path))
            fail_name = str(yaml_data.get("name") or "").strip()
            fail_version = str(yaml_data.get("version") or "").strip()
        except Exception:  # noqa: BLE001 - 尽力而为，不影响原始错误
            pass
    if fail_name and fail_version:
        return fail_name, fail_version
    manifest_path = entry / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = load_json_object_file(manifest_path, label="manifest.json")
            if not fail_name:
                fail_name = str(manifest.get("id") or manifest.get("name") or "").strip()
            if not fail_version:
                fail_version = str(manifest.get("version") or "").strip()
        except Exception:  # noqa: BLE001 - 尽力而为，不影响原始错误
            pass
    return fail_name, fail_version


def _read_import_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        return load_json_object_file(path, label=label, max_bytes=MAX_JSON_BYTES)
    except ValueError as exc:
        too_large = " exceeds " in str(exc)
        raise PublishError(
            code=400,
            error="manifest_too_large" if too_large else "manifest_invalid",
            message=(
                f"{label} 超过 {MAX_JSON_BYTES} 字节上限"
                if too_large
                else f"{label} 不是合法 UTF-8 JSON：{exc}"
            ),
            error_code=(
                "SKILLHUB_IMPORT_MANIFEST_TOO_LARGE"
                if too_large
                else "SKILLHUB_IMPORT_MANIFEST_INVALID"
            ),
            error_class="validation",
        ) from exc


def _read_legacy_skill_manifest(path: Path) -> dict[str, Any]:
    raw_manifest = path.read_bytes()
    if len(raw_manifest) > MAX_JSON_BYTES:
        raise PublishError(
            code=400,
            error="manifest_too_large",
            message=f"manifest.json 超过 {MAX_JSON_BYTES} 字节上限",
            error_code="SKILLHUB_IMPORT_MANIFEST_TOO_LARGE",
            error_class="validation",
        )
    try:
        text = raw_manifest.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PublishError(
            code=400,
            error="manifest_invalid",
            message=f"manifest.json 不是合法 UTF-8：{exc}",
            error_code="SKILLHUB_IMPORT_MANIFEST_INVALID",
            error_class="validation",
        ) from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PublishError(
            code=400,
            error="manifest_invalid",
            message=f"manifest.json JSON 解析失败：{exc}",
            error_code="SKILLHUB_IMPORT_MANIFEST_INVALID",
            error_class="validation",
        ) from exc
    if not isinstance(parsed, dict):
        raise PublishError(
            code=400,
            error="manifest_invalid",
            message="manifest.json 根节点必须为 JSON object",
            error_code="SKILLHUB_IMPORT_MANIFEST_INVALID",
            error_class="validation",
        )
    return parsed


def _invalid_import_manifest(message: str) -> None:
    raise PublishError(
        code=400,
        error="manifest_invalid",
        message=message,
        error_code="SKILLHUB_IMPORT_MANIFEST_INVALID",
        error_class="validation",
    )


def _mcp_builtin_index_entries(tmp_root: Path) -> dict[str, dict[str, Any]]:
    index_path = tmp_root / "index.json"
    if not index_path.is_file():
        return {}
    index = _read_import_json_object(index_path, "index.json")
    mcps = index.get("mcps")
    if not isinstance(mcps, list):
        _invalid_import_manifest("index.json.mcps 必须为数组")
    result: dict[str, dict[str, Any]] = {}
    for position, item in enumerate(mcps):
        if not isinstance(item, dict):
            _invalid_import_manifest(f"index.json.mcps[{position}] 必须为对象")
        asset_id = str(item.get("id") or "").strip()
        source = str(item.get("source") or "").strip()
        if not asset_id or asset_id != source:
            _invalid_import_manifest(
                f"index.json.mcps[{position}] 要求非空 id == source"
            )
        description_raw = (
            item.get("description_zh")
            or item.get("description_en")
            or item.get("description")
        )
        description = (
            localized_manifest_text(description_raw)
            if isinstance(description_raw, dict)
            else str(description_raw or "").strip()
        )
        name_raw = item.get("name")
        if isinstance(name_raw, dict):
            # 多语言对象不能 str()，否则落库成 "{'zh': '...'}" 字符串
            display_name = localized_manifest_text(name_raw) or str(
                item.get("name_en") or asset_id
            ).strip()
        else:
            display_name = str(name_raw or item.get("name_en") or asset_id).strip()
        override: dict[str, Any] = {
            "name": asset_id,
            "display_name": display_name,
            "description": description,
        }
        item_version = item.get("version")
        if isinstance(item_version, str) and item_version.strip():
            override["version"] = item_version.strip()
        if source in result:
            _invalid_import_manifest(
                f"index.json.mcps[{position}] source 重复：{source}"
            )
        result[source] = override
    return result


# 简单包在 manifest 未写 ``version`` 时的 semver 兜底（无 HTTP/包级 defaults）
_SIMPLE_VERSION_FALLBACK = "0.0.1"
_SIMPLE_AUTHOR_FALLBACK = "system_admin"


def skill_import_from_staging_dir(
    tmp_root: Path,
    *,
    user_id: str,
    db: Session,
    storage: S3StorageClient,
    force: bool = False,
    fail_fast: bool = False,
    manifest_entry_extra: dict[str, dict[str, Any]] | None = None,
    entry_skip_predicate: Callable[[str, Path], tuple[bool, str | None]] | None = None,
    after_publish_success: Callable[[str, PluginPublishResult], None] | None = None,
    on_entry_progress: Callable[[str], None] | None = None,
    single_entry_name_hint: str | None = None,
    is_system_token: bool = False,
    publisher_name_override: str | None = None,
    allow_multi_asset: bool = False,
) -> SkillImportResponse | AssetImportResponse:
    """对已展开到 ``tmp_root`` 的集合包目录执行导入（与 ZIP 解压后布局一致）。

    调用方负责创建/清理 ``tmp_root``。``manifest.json`` 规则同 ``skill_import_from_bundle``。
    """
    item_result_model = AssetImportItemResult if allow_multi_asset else SkillImportItemResult
    results: list[SkillImportItemResult | AssetImportItemResult] = []

    single_root_entry = allow_multi_asset and detect_import_entry_type(tmp_root) is not None
    manifest: dict[str, Any] = {}
    mf = tmp_root / "manifest.json"
    if mf.is_file() and not single_root_entry:
        manifest = (
            _read_import_json_object(mf, "manifest.json")
            if allow_multi_asset
            else _read_legacy_skill_manifest(mf)
        )

    if single_root_entry:
        entry_dirs = [tmp_root]
    else:
        entry_dirs = sorted(
            [
                p
                for p in tmp_root.iterdir()
                if p.is_dir() and not p.name.startswith(".") and p.name != "__MACOSX"
            ],
            key=lambda p: p.name,
        )

    if not entry_dirs:
        raise PublishError(
            code=400,
            error="invalid_asset_bundle" if allow_multi_asset else "invalid_skill_bundle",
            message=(
                "无有效资产目录（支持 Skill/TeamSkill、agent-plugin、agent-template、agent-mcp）"
                if allow_multi_asset
                else "无有效 skill 顶层目录（简单包：根目录 SKILL.md；标准包：plugin.yaml + 子目录 SKILL.md，icon.png 可选）"
            ),
            error_code="SKILLHUB_IMPORT_INVALID_BUNDLE",
            error_class="validation",
        )

    if len(entry_dirs) > MAX_ZIP_ENTRIES:
        raise PublishError(
            code=400,
            error="too_many_skill_entries",
            message=(
                f"顶层 skill 目录数量 {len(entry_dirs)} 超过上限 {MAX_ZIP_ENTRIES} "
                f"（与 ZIP 条目数上限一致），请分批导入或拆分集合包"
            ),
            error_code="SKILLHUB_IMPORT_TOO_MANY_ENTRIES",
            error_class="validation",
        )

    if allow_multi_asset:
        entries_map = {
            **_mcp_builtin_index_entries(tmp_root),
            **_asset_import_parse_entries_map(manifest),
        }
    else:
        entries_map = _skill_import_parse_entries_map(manifest)

    for entry in entry_dirs:
        if single_root_entry and single_entry_name_hint:
            entry_name = Path(single_entry_name_hint).stem.strip() or "asset"
        else:
            entry_name = entry.name
        _log_skill_import_entry(stage="entry_prepare", result="started", entry=entry_name)
        if on_entry_progress is not None:
            on_entry_progress(entry_name)
        if entry_skip_predicate:
            skip, skip_reason = entry_skip_predicate(entry_name, entry)
            if skip:
                results.append(
                    item_result_model(
                        entry=entry_name,
                        status="skipped",
                        message=skip_reason or "skipped",
                    )
                )
                _log_skill_import_entry(
                    stage="entry_complete",
                    result="skipped",
                    entry=entry_name,
                    result_detail=skip_reason or "skipped",
                )
                continue

        raw_eo = entries_map.get(entry_name)
        eo = raw_eo if isinstance(raw_eo, dict) else {}
        extra = (manifest_entry_extra or {}).get(entry_name)
        if isinstance(extra, dict) and extra:
            eo = {**eo, **extra}
        author_a = str(eo.get("author") or _SIMPLE_AUTHOR_FALLBACK).strip() or _SIMPLE_AUTHOR_FALLBACK
        tags_a = _skill_import_merge_tags(eo.get("tags"), [])

        entry_force = force or bool(eo.get("force"))
        plugin_id = eo.get("plugin_id")
        plugin_id_str = str(plugin_id).strip() if plugin_id else None

        publish_zip: Path | None = None
        name: str = ""
        version: str = ""
        try:
            normalize_options: dict[str, Any] = {}
            if allow_multi_asset:
                normalize_options.update(
                    entry_name_hint=entry_name,
                    allow_multi_asset=True,
                )
            publish_zip, name, version = entry_to_publish_zip(
                entry,
                entry_key=entry_name,
                entry_overrides=eo,
                version_fallback=_SIMPLE_VERSION_FALLBACK,
                default_author=author_a,
                default_tags=tags_a,
                **normalize_options,
            )
        except ValueError as e:
            fail_name, fail_version = _resolve_import_fail_identity(entry)
            results.append(
                item_result_model(
                    entry=entry_name,
                    status="error",
                    name=fail_name or None,
                    version=fail_version or None,
                    error="import_normalize_failed",
                    message=str(e),
                )
            )
            _log_skill_import_entry(
                stage="entry_complete",
                result="invalid",
                entry=entry_name,
                error_code="SKILLHUB_IMPORT_NORMALIZE_FAILED",
                error_class="validation",
                error_message=str(e),
            )
            if fail_fast:
                break
            continue

        zip_bytes = publish_zip.read_bytes()
        inner_checksum = hashlib.sha256(zip_bytes).hexdigest()
        entry_version_desc = _skill_import_entry_version_desc(eo)
        try:
            publish_options: dict[str, Any] = {}
            if allow_multi_asset:
                publish_options.update(
                    is_system_token=is_system_token,
                    publisher_name_override=publisher_name_override,
                )
            pr = publish(
                user_id=user_id,
                content=zip_bytes,
                filename=f"{name}-{version}.zip",
                expected_checksum=inner_checksum,
                plugin_id=plugin_id_str,
                plugin_version=None,
                version_desc=entry_version_desc,
                force=entry_force,
                db=db,
                storage=storage,
                **publish_options,
            )
            if after_publish_success is not None:
                after_publish_success(entry_name, pr)
            # 幂等命中（同名同版本同内容）记 skipped 而非 ok；客户端应看 summary.skipped
            deduplicated = bool(getattr(pr, "deduplicated", False))
            result_fields: dict[str, Any] = {}
            if allow_multi_asset:
                result_fields.update(
                    asset_id=pr.asset_id or pr.plugin_id,
                    asset_type=pr.asset_type,
                    plugin_type=pr.plugin_type,
                )
            results.append(
                item_result_model(
                    entry=entry_name,
                    status="skipped" if deduplicated else "ok",
                    plugin_id=pr.plugin_id,
                    name=pr.name,
                    version=pr.version,
                    message=(
                        "已存在相同版本与内容，跳过" if deduplicated else None
                    ),
                    **result_fields,
                )
            )
            _log_skill_import_entry(
                stage="entry_complete",
                result="skipped" if deduplicated else "success",
                entry=entry_name,
                resource_type=(pr.asset_type or "plugin") if allow_multi_asset else "skill",
                resource_id=pr.plugin_id,
                resource_version=pr.version,
                plugin_name=pr.name,
            )
        except PublishError as e:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            detail = e.detail if isinstance(e.detail, dict) else {}
            err = str(detail.get("error") or "publish_failed")
            msg = safe_error_summary(
                error_code=str(detail.get("error_code") or err),
                error_class=detail.get("error_class") or "internal",
                fallback="skill import entry publish failed",
            )
            # version_conflict in non-force mode is a planned skip, not a failure
            is_skip = err == "version_conflict" and not entry_force
            results.append(
                item_result_model(
                    entry=entry_name,
                    status="skipped" if is_skip else "error",
                    name=name,
                    version=version,
                    error=None if is_skip else err,
                    message=msg,
                )
            )
            _log_skill_import_entry(
                stage="entry_complete",
                result=(
                    "skipped"
                    if is_skip
                    else "invalid"
                    if detail.get("error_class") in {
                        "validation",
                        "auth",
                        "not_found",
                        "conflict",
                    }
                    else "denied"
                    if detail.get("error_class") == "permission"
                    else "failure"
                ),
                entry=entry_name,
                resource_type="skill",
                resource_id=plugin_id_str,
                resource_version=version or None,
                error_code=str(detail.get("error_code") or err),
                error_class=detail.get("error_class") or "internal",
                error_message=msg,
            )
            if fail_fast:
                break
        except SQLAlchemyError as e:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            results.append(
                item_result_model(
                    entry=entry_name,
                    status="error",
                    name=name,
                    version=version,
                    error="publish_db_error",
                    message=str(e)[:500],
                )
            )
            _log_skill_import_entry(
                stage="entry_complete",
                result="failure",
                entry=entry_name,
                resource_type="skill",
                resource_id=plugin_id_str,
                resource_version=version or None,
                error_code="SKILLHUB_IMPORT_PUBLISH_DB_ERROR",
                error_class="internal",
                error_message=str(e)[:500],
            )
            if fail_fast:
                break
        finally:
            if publish_zip is not None:
                publish_zip.unlink(missing_ok=True)

    entry_total = len(entry_dirs)
    ok = sum(1 for r in results if r.status == "ok")
    failed = sum(1 for r in results if r.status == "error")
    skipped = sum(1 for r in results if r.status == "skipped")
    if allow_multi_asset:
        return AssetImportResponse(
            summary=AssetImportSummary(
                total=entry_total,
                ok=ok,
                failed=failed,
                skipped=skipped,
            ),
            results=results,
        )
    return SkillImportResponse(
        summary=SkillImportSummary(total=entry_total, ok=ok, failed=failed, skipped=skipped),
        results=results,
    )


def skill_import_from_bundle(
    *,
    bundle_path: Path,
    user_id: str,
    db: Session,
    storage: S3StorageClient,
    force: bool = False,
    fail_fast: bool = False,
    single_entry_name_hint: str | None = None,
    is_system_token: bool = False,
    publisher_name_override: str | None = None,
    allow_multi_asset: bool = False,
) -> SkillImportResponse | AssetImportResponse:
    """解压集合 ZIP，逐条规范化为市场包并 ``publish``。

    HTTP 入口已在落盘时校验 ``X-Checksum-SHA256`` 与 ``MAX_FILE_SIZE``；若从其它路径调用，
    调用方须自行保证 ``bundle_path`` 内容与完整性。

    默认保持原 skill-import 语义；``allow_multi_asset=True`` 时额外支持单资产根目录、
    ``manifest.json.entries`` 和 MCP ``index.json``。
    """
    tmp_root = Path(tempfile.mkdtemp(prefix="oj_skill_bundle_"))
    try:
        try:
            skill_import_extract_zip_to_dir(bundle_path, tmp_root)
        except PublishError:
            raise
        except ValueError as e:
            raise PublishError(
                code=400,
                error=(
                    "invalid_asset_bundle"
                    if allow_multi_asset
                    else "invalid_skill_bundle"
                ),
                message=str(e) or (
                    "资产集合包解压失败"
                    if allow_multi_asset
                    else "技能集合包解压失败"
                ),
            ) from e
        staging_options: dict[str, Any] = {}
        if allow_multi_asset:
            staging_options.update(
                single_entry_name_hint=single_entry_name_hint,
                is_system_token=is_system_token,
                publisher_name_override=publisher_name_override,
                allow_multi_asset=True,
            )
        return skill_import_from_staging_dir(
            tmp_root,
            user_id=user_id,
            db=db,
            storage=storage,
            force=force,
            fail_fast=fail_fast,
            **staging_options,
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
