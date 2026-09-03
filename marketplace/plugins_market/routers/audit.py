# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""审计日志管理后台路由。

挂载路径：/api/v1/audit
权限：require_auth + is_market_moderation_admin
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from plugins_market.core.audit import (
    AuditLog,
    EXPORT_MAX_ROWS,
    MAX_DATE_RANGE_DAYS,
    _BJ_TZ,
    _bj_datetime_to_ms,
    audit_log,
    count_audit_logs_for_admin,
    fetch_asset_meta_map,
    get_audit_log_by_event_id,
    get_audit_log_stats,
    iter_audit_logs_for_export,
    list_audit_logs_for_admin,
)
from plugins_market.core.audit_events import (
    ASSET_LINKED_RESOURCE_TYPES,
    Action,
    EventType,
    ResourceType,
    Result,
)
from plugins_market.core.auth import AuthContext, require_auth
from plugins_market.core.context import detect_source_channel_from_ua
from plugins_market.core.database import SessionLocal, get_db
from plugins_market.schemas.audit import (
    AuditLogDetail,
    AuditLogListData,
    AuditLogListItem,
    AuditStatsData,
)
from plugins_market.core.logging import get_logger
from plugins_market.core.operation_log import bind_operation_actor, bind_operation_resource, operation_context
from plugins_market.schemas.common import ResponseModel


logger = get_logger(__name__)

router = APIRouter(prefix="/audit", tags=["audit"], include_in_schema=False)


# market asset 对应的精确 resource_type 会指向 market_assets，可用于补名。
# 集中定义在 core.audit_events，这里只做本模块别名引用。
_ASSET_LINKED_RESOURCE_TYPES = ASSET_LINKED_RESOURCE_TYPES

# CSV detail 列中的换行/制表符替换，避免 Excel 中行结构错乱
_CSV_CLEAN_RE = re.compile(r"[\r\n\t]+")

# Excel 公式注入防御：以这些字符开头的单元格会被 Excel 当公式执行
_CSV_FORMULA_LEADING = ("=", "+", "-", "@", "\t", "\r")

# 导出时每批补 asset name 的批量大小，避免 IN 列表过长
_EXPORT_ASSET_LOOKUP_BATCH = 200


@dataclass(frozen=True)
class AuditLogFilterDeps:
    """审计日志查询的过滤条件聚合（列表 + 导出共用）。"""

    keyword: Optional[str]
    date_from_ms: int
    date_to_ms: int
    resource_type: Optional[str]
    action: Optional[str]
    result: Optional[str]
    asset_plugin_type: Optional[str]
    source_channel: Optional[str]


def get_audit_log_filter_deps(  # pylint: disable=huawei-too-many-arguments
    keyword: Optional[str] = Query(None, max_length=128),
    date_from_ms: int = Query(..., ge=0),
    date_to_ms: int = Query(..., ge=0),
    resource_type: Optional[str] = Query(None, max_length=30),
    action: Optional[str] = Query(None, max_length=20),
    result: Optional[str] = Query(None, max_length=20),
    asset_plugin_type: Optional[str] = Query(None, max_length=32),
    source_channel: Optional[str] = Query(None, max_length=20),
) -> AuditLogFilterDeps:
    """把 8 个过滤类查询参数封装成单个依赖，缩小路由签名。"""
    return AuditLogFilterDeps(
        keyword=keyword,
        date_from_ms=date_from_ms,
        date_to_ms=date_to_ms,
        resource_type=resource_type,
        action=action,
        result=result,
        asset_plugin_type=asset_plugin_type,
        source_channel=source_channel,
    )


def _require_audit_admin(auth: AuthContext) -> None:
    if not auth.is_market_moderation_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="audit log admin permission required",
        )


def _validate_date_range(date_from_ms: int, date_to_ms: int) -> None:
    if date_to_ms < date_from_ms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="date_to_ms must be >= date_from_ms",
        )
    span_ms = date_to_ms - date_from_ms
    if span_ms > MAX_DATE_RANGE_DAYS * 86400 * 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"date range must not exceed {MAX_DATE_RANGE_DAYS} days",
        )


def _collect_asset_ids(rows: list[AuditLog]) -> list[str]:
    """挑出市场资产 resource_id，用于批量补充资产名称。"""
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        if not row.resource_id or row.resource_type not in _ASSET_LINKED_RESOURCE_TYPES:
            continue
        if row.resource_id in seen:
            continue
        seen.add(row.resource_id)
        out.append(row.resource_id)
    return out


def _resolve_source_channel(extra: Any, user_agent: Optional[str]) -> Optional[str]:
    """extra 优先，否则按 user_agent 反推（与 SQL _source_channel_sql_case 等价）。"""
    if isinstance(extra, dict):
        v = extra.get("source_channel")
        if isinstance(v, str) and v:
            return v
    return detect_source_channel_from_ua(user_agent)


def _to_list_item(
    row: AuditLog,
    asset_meta_map: Dict[str, Dict[str, Optional[str]]],
) -> AuditLogListItem:
    meta = asset_meta_map.get(row.resource_id or "") if row.resource_id else None
    _extra = row.extra if isinstance(row.extra, dict) else {}
    item = AuditLogListItem(
        event_id=row.event_id,
        event_type=row.event_type,
        action=row.action,
        operator_id=row.operator_id,
        operator_name=row.operator_name,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        resource_version=row.resource_version,
        result=row.result,
        ip_address=row.ip_address,
        extra=_extra or None,
        created_at_ms=_bj_datetime_to_ms(row.created_at),
        duration_ms=int(row.duration_ms or 0),
        source_channel=_resolve_source_channel(row.extra, row.user_agent),
        asset_display_name=(meta or {}).get("display_name") if meta else None,
        asset_plugin_type=(meta or {}).get("plugin_type") if meta else None,
        asset_name=(meta or {}).get("name") if meta else None,
    )
    # Fallback: asset deleted -> JOIN yields no meta; use extra snapshot
    if item.asset_display_name is None:
        item.asset_display_name = _extra.get("skill_display_name")
    if item.asset_name is None:
        item.asset_name = _extra.get("skill_name")
    return item


def _to_detail(
    row: AuditLog,
    asset_meta_map: Dict[str, Dict[str, Optional[str]]],
) -> AuditLogDetail:
    meta = asset_meta_map.get(row.resource_id or "") if row.resource_id else None
    _extra = row.extra if isinstance(row.extra, dict) else {}
    item = AuditLogDetail(
        event_id=row.event_id,
        event_type=row.event_type,
        action=row.action,
        operator_id=row.operator_id,
        operator_name=row.operator_name,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        resource_version=row.resource_version,
        result=row.result,
        ip_address=row.ip_address,
        extra=_extra or None,
        created_at_ms=_bj_datetime_to_ms(row.created_at),
        duration_ms=int(row.duration_ms or 0),
        source_channel=_resolve_source_channel(row.extra, row.user_agent),
        asset_display_name=(meta or {}).get("display_name") if meta else None,
        asset_plugin_type=(meta or {}).get("plugin_type") if meta else None,
        asset_name=(meta or {}).get("name") if meta else None,
        detail=row.detail,
        user_agent=row.user_agent,
        request_id=row.request_id,
    )
    # Fallback: asset deleted -> JOIN yields no meta; use extra snapshot
    if item.asset_display_name is None:
        item.asset_display_name = _extra.get("skill_display_name")
    if item.asset_name is None:
        item.asset_name = _extra.get("skill_name")
    return item


@router.get(
    "/logs",
    response_model=ResponseModel[AuditLogListData],
)
async def list_audit_logs(
    filters: AuditLogFilterDeps = Depends(get_audit_log_filter_deps),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[AuditLogListData]:
    _require_audit_admin(auth)
    _validate_date_range(filters.date_from_ms, filters.date_to_ms)

    rows, total = list_audit_logs_for_admin(
        db,
        keyword=filters.keyword,
        date_from_ms=filters.date_from_ms,
        date_to_ms=filters.date_to_ms,
        resource_type=filters.resource_type,
        action=filters.action,
        result=filters.result,
        asset_plugin_type=filters.asset_plugin_type,
        source_channel=filters.source_channel,
        page=page,
        page_size=page_size,
    )
    asset_meta_map = fetch_asset_meta_map(db, _collect_asset_ids(rows))
    items = [_to_list_item(r, asset_meta_map) for r in rows]
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=AuditLogListData(items=items, total=total, page=page, page_size=page_size),
    )


@router.get(
    "/stats",
    response_model=ResponseModel[AuditStatsData],
)
async def get_audit_stats(
    filters: AuditLogFilterDeps = Depends(get_audit_log_filter_deps),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[AuditStatsData]:
    _require_audit_admin(auth)
    _validate_date_range(filters.date_from_ms, filters.date_to_ms)
    stats = get_audit_log_stats(
        db,
        date_from_ms=filters.date_from_ms,
        date_to_ms=filters.date_to_ms,
        keyword=filters.keyword,
        resource_type=filters.resource_type,
        action=filters.action,
        result=filters.result,
        asset_plugin_type=filters.asset_plugin_type,
        source_channel=filters.source_channel,
    )
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=AuditStatsData(**stats),
    )


@router.get(
    "/logs/export",
    response_class=StreamingResponse,
)
async def export_audit_logs(
    filters: AuditLogFilterDeps = Depends(get_audit_log_filter_deps),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> StreamingResponse:
    with operation_context(operation_type="audit_export"):
        bind_operation_actor(
            actor_id=auth.acting_user_id,
            actor_name=auth.acting_user_name,
            actor_type="audit_admin",
        )
        bind_operation_resource(resource_type="audit_log", resource_id="export")
        _require_audit_admin(auth)
        _validate_date_range(filters.date_from_ms, filters.date_to_ms)

        # 轻量预检：limit=EXPORT_MAX_ROWS 时最多扫描 5万+1 行就能判超限，
        # 避免在大表上做精确 COUNT(*)
        matched = count_audit_logs_for_admin(
            db,
            keyword=filters.keyword,
            date_from_ms=filters.date_from_ms,
            date_to_ms=filters.date_to_ms,
            resource_type=filters.resource_type,
            action=filters.action,
            result=filters.result,
            asset_plugin_type=filters.asset_plugin_type,
            source_channel=filters.source_channel,
            limit=EXPORT_MAX_ROWS,
        )
        if matched > EXPORT_MAX_ROWS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"导出条数超过上限 {EXPORT_MAX_ROWS}，"
                    f"请缩小日期范围或过滤条件"
                ),
            )

        # 审计：管理员把审计数据整体往外搬，敏感操作必须留痕
        try:
            audit_log(
                event_type=EventType.AUDIT,
                action=Action.EXPORT,
                operator_id=auth.acting_user_id,
                operator_name=auth.acting_user_name,
                resource_type=ResourceType.AUDIT_LOG,
                result=Result.SUCCESS,
                detail=f"导出审计日志 {matched} 条",
                ip_address=auth.ip_address,
                user_agent=auth.user_agent,
                extra={
                    "matched_rows": int(matched),
                    "date_from_ms": int(filters.date_from_ms),
                    "date_to_ms": int(filters.date_to_ms),
                    "keyword": filters.keyword,
                    "filter_resource_type": filters.resource_type,
                    "filter_action": filters.action,
                    "filter_result": filters.result,
                    "filter_asset_plugin_type": filters.asset_plugin_type,
                    "filter_source_channel": filters.source_channel,
                },
            )
        except Exception as exc:  # noqa: BLE001
            # 审计写入失败不阻断导出流程
            logger.warning("audit AUDIT.EXPORT suppressed: %s", exc)

    # 中文表头，按用户阅读习惯排列；时间放最左，方便排序检索
    header = [
        "操作时间",
        "用户名称",
        "用户ID",
        "Skill名称",
        "Skill类型",
        "资源类型",
        "版本",
        "事件类型",
        "操作",
        "状态",
        "IP地址",
        "来源",
        "操作摘要",
        "耗时(ms)",
        "事件ID",
        "请求ID",
        "User-Agent",
        "资源ID",
    ]

    # 中文映射，与前端 badges.tsx 保持一致
    event_type_cn = {
        EventType.SKILL_MANAGE: "Skill 管理",
        EventType.SKILL_MODERATION: "Skill 审核",
        EventType.PLUGIN_MANAGE: "插件管理",
        EventType.SKILL_REVIEW: "审查",
        EventType.AUDIT: "审计自身",
    }
    action_cn = {
        Action.APPROVE: "审核通过",
        Action.REJECT: "驳回",
        Action.PUBLISH: "发布",
        Action.DELETE: "删除",
        Action.GIT_SYNC: "Git 同步",
        Action.GIT_SOURCE_DELETE: "删除 Git 源",
        Action.IMPORT: "批量导入",
        Action.AUTO_REVIEW_PASS: "审查通过",
        Action.AUTO_REVIEW_FAIL: "审查未通过",
        Action.AUTO_REVIEW_SYS_FAIL: "审查异常",
        Action.PENDING_MOD_SET: "转入待审",
        Action.EXPORT: "导出",
    }
    result_cn = {
        Result.SUCCESS: "成功",
        Result.FAILED: "失败",
        Result.PARTIAL_FAILED: "部分失败",
    }

    def _clean(value: Optional[str]) -> str:
        if value is None:
            return ""
        cleaned = _CSV_CLEAN_RE.sub(" ", str(value)).strip()
        # 防御 Excel 公式注入：前缀单引号让 Excel 当字符串
        if cleaned and cleaned[0] in _CSV_FORMULA_LEADING:
            cleaned = "'" + cleaned
        return cleaned

    def _format_bj_datetime(dt: datetime) -> str:
        if dt is None:
            return ""
        aware = dt.replace(tzinfo=_BJ_TZ)
        return aware.strftime("%Y-%m-%d %H:%M:%S")

    def _row_to_csv(row: AuditLog, asset_meta_map: Dict[str, Dict[str, Optional[str]]]) -> list:
        extra = row.extra if isinstance(row.extra, dict) else {}
        skill_name = str(extra.get("skill_name") or "")
        skill_display = str(extra.get("skill_display_name") or "")
        meta = asset_meta_map.get(row.resource_id or "") if row.resource_id else None
        if meta:
            if not skill_display:
                skill_display = meta.get("display_name") or ""
            if not skill_name:
                skill_name = meta.get("name") or ""
        skill_label = skill_display or skill_name or (row.resource_id or "")
        asset_plugin_type = (meta or {}).get("plugin_type") if meta else None
        version_str = row.resource_version or ""
        if version_str.lower() == "all":
            version_str = "全部版本"
        return [
            _format_bj_datetime(row.created_at),
            _clean(row.operator_name),
            _clean(row.operator_id),
            _clean(skill_label),
            _clean(asset_plugin_type or ""),
            _clean(row.resource_type),
            version_str,
            event_type_cn.get(row.event_type, row.event_type),
            action_cn.get(row.action, row.action),
            result_cn.get(row.result, row.result),
            _clean(row.ip_address),
            _clean(_resolve_source_channel(row.extra, row.user_agent) or ""),
            _clean(row.detail),
            int(row.duration_ms or 0),
            row.event_id,
            _clean(row.request_id),
            _clean(row.user_agent),
            _clean(row.resource_id),
        ]

    def _generate():
        # UTF-8 BOM 让 Excel 直接打开不乱码
        yield "﻿"
        buf = io.StringIO()
        writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        stream_db = SessionLocal()
        meta_db = SessionLocal()
        try:
            # 分批补 asset name：每页查一次 market_assets 避免 IN 列表过长
            batch_rows: list[AuditLog] = []

            def _flush(batch: list[AuditLog]):
                asset_ids = _collect_asset_ids(batch)
                meta_map = fetch_asset_meta_map(meta_db, asset_ids)
                for row in batch:
                    writer.writerow(_row_to_csv(row, meta_map))
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate(0)

            for row in iter_audit_logs_for_export(
                stream_db,
                keyword=filters.keyword,
                date_from_ms=filters.date_from_ms,
                date_to_ms=filters.date_to_ms,
                resource_type=filters.resource_type,
                action=filters.action,
                result=filters.result,
                asset_plugin_type=filters.asset_plugin_type,
                source_channel=filters.source_channel,
            ):
                batch_rows.append(row)
                if len(batch_rows) >= _EXPORT_ASSET_LOOKUP_BATCH:
                    yield from _flush(batch_rows)
                    batch_rows = []
            if batch_rows:
                yield from _flush(batch_rows)
        finally:
            stream_db.close()
            meta_db.close()

    # 文件名按当前日期，比毫秒戳更直观
    today = datetime.now(_BJ_TZ).strftime("%Y%m%d_%H%M%S")
    filename = f"审计日志_{today}.csv"
    ascii_fallback = f"audit_logs_{today}.csv"
    encoded = quote(filename)
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"
        ),
    }
    return StreamingResponse(
        _generate(),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )


@router.get(
    "/logs/{event_id}",
    response_model=ResponseModel[AuditLogDetail],
)
async def get_audit_log_detail(
    event_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[AuditLogDetail]:
    _require_audit_admin(auth)
    row = get_audit_log_by_event_id(db, event_id)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"audit log {event_id!r} not found",
        )
    asset_meta_map = fetch_asset_meta_map(db, _collect_asset_ids([row]))
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=_to_detail(row, asset_meta_map),
    )
