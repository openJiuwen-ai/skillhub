# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
import random
import string
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, DateTime, Index, Integer, JSON, String, Text, case, func, or_
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import Session

from plugins_market.core.audit_events import (
    EVENT_SKILL_MODERATION,  # 兼容旧 import 路径
    Result,
)
from plugins_market.core.context import (
    _BJ_TZ,
    CLI_UA_PREFIXES,
    SOURCE_CHANNEL_API,
    SOURCE_CHANNEL_BACKGROUND,
    SOURCE_CHANNEL_CLI,
    SOURCE_CHANNEL_WEB,
    WEB_UA_MARKERS,
    get_duration_ms,
    get_request_id,
    get_source_channel,
)
from plugins_market.core.database import SessionLocal
from plugins_market.core.logging import get_logger
from plugins_market.models.base import Base

logger = get_logger(__name__)

__all__ = [
    "AuditLog",
    "AuditLogParams",
    "AuditService",
    "EVENT_SKILL_MODERATION",
    "EXPORT_MAX_ROWS",
    "EXPORT_STREAM_CHUNK",
    "MAX_DATE_RANGE_DAYS",
    "SLOW_THRESHOLD_MS",
    "_BJ_TZ",
    "_bj_datetime_to_ms",
    "_build_audit_query",
    "_ms_to_bj_datetime",
    "audit_log",
    "count_audit_logs_for_admin",
    "fetch_asset_meta_map",
    "get_audit_log_by_event_id",
    "get_audit_log_stats",
    "iter_audit_logs_for_export",
    "list_audit_logs_for_admin",
    "list_skill_moderation_audit_logs_for_operator",
]

_AUDIT_USER_AGENT_MAX_LEN = 255
_AUDIT_DETAIL_MAX_LEN = 16_384
_AUDIT_EXTRA_JSON_MAX_BYTES = 32_768


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    request_id = Column(String(64), nullable=False, index=True)
    event_id = Column(String(64), unique=True, nullable=False, index=True)

    event_type = Column(String(50), nullable=False)
    action = Column(String(20), nullable=False)

    operator_id = Column(String(64), nullable=False, index=True)
    operator_name = Column(String(100), nullable=True)

    resource_type = Column(String(30), nullable=False)
    resource_id = Column(String(100), nullable=True, index=True)
    resource_version = Column(String(30), nullable=True)

    result = Column(String(20), nullable=False)
    duration_ms = Column(Integer, nullable=False)
    detail = Column(Text, nullable=True)

    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)

    extra = Column(JSON, nullable=True)
    created_at = Column(DateTime(3), nullable=False)

    __table_args__ = (
        Index("idx_event_type_action", event_type, action),
        Index("idx_created_at", created_at.desc()),
        Index("idx_event_type_created_at", event_type, created_at.desc()),
    )


@dataclass(frozen=True)
class AuditLogParams:
    """审计写入参数。

    注意：不再持有 db session — 审计写入始终用自己的 SessionLocal，
    避免污染调用方的事务（详见 AuditService.log）。
    """

    event_type: str
    action: str
    operator_id: str
    operator_name: Optional[str] = None
    resource_type: str = ""
    resource_id: Optional[str] = None
    resource_version: Optional[str] = None
    result: str = Result.SUCCESS
    detail: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


def _truncate_audit_text(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _sanitize_audit_extra(extra: Dict[str, Any] | None) -> Dict[str, Any]:
    if not extra:
        return {}
    try:
        encoded = json.dumps(extra, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return {"_truncated": True, "_reason": "extra_not_serializable"}
    if len(encoded.encode("utf-8")) <= _AUDIT_EXTRA_JSON_MAX_BYTES:
        return extra
    return {"_truncated": True, "_reason": "extra_too_large"}


class AuditService:

    @staticmethod
    def create_event_id() -> str:
        now_ms = int(time.time() * 1000)
        rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"audit_{now_ms}_{rand_chars}"

    @staticmethod
    def log(params: AuditLogParams) -> None:
        """Record an audit log entry.

        事务隔离：始终使用独立的 SessionLocal。这样：
        1. 审计 commit 不会附带提交调用方未提交的业务变更
        2. 审计 rollback 也不影响调用方事务
        3. 调用方在自己的事务里 raise 后审计仍能落库（用于失败补录场景）
        """
        own_db: Optional[Session] = None
        try:
            request_id = get_request_id() or "unknown"
            duration_ms = get_duration_ms()
            event_id = AuditService.create_event_id()

            # 仅后台任务显式写入；HTTP 场景读取时按 user_agent 反推
            extra_dict = dict(params.extra or {})
            if "source_channel" not in extra_dict:
                ch = get_source_channel()
                if ch == SOURCE_CHANNEL_BACKGROUND:
                    extra_dict["source_channel"] = ch

            audit_entry = AuditLog(
                request_id=request_id,
                event_id=event_id,
                event_type=params.event_type,
                action=params.action,
                operator_id=params.operator_id,
                operator_name=params.operator_name,
                resource_type=params.resource_type,
                resource_id=params.resource_id,
                resource_version=params.resource_version,
                result=params.result,
                duration_ms=duration_ms,
                detail=_truncate_audit_text(params.detail, _AUDIT_DETAIL_MAX_LEN),
                ip_address=params.ip_address,
                user_agent=_truncate_audit_text(params.user_agent, _AUDIT_USER_AGENT_MAX_LEN),
                extra=_sanitize_audit_extra(extra_dict),
                created_at=datetime.now(_BJ_TZ),
            )

            own_db = SessionLocal()
            own_db.add(audit_entry)
            own_db.commit()

            logger.info(
                f"Audit: {params.event_type}.{params.action}",
                event_id=event_id,
                event_type=params.event_type,
                action=params.action,
                operator_id=params.operator_id,
                operator_name=params.operator_name,
                resource_type=params.resource_type,
                resource_id=params.resource_id,
                result=params.result,
                detail=params.detail,
            )

        except Exception as e:
            logger.error(f"Failed to write audit log: {e}", exc_info=True)
            if own_db is not None:
                try:
                    own_db.rollback()
                except Exception as rollback_exc:
                    logger.warning(f"Failed to rollback audit log transaction: {rollback_exc}")
        finally:
            if own_db is not None:
                try:
                    own_db.close()
                except Exception as close_exc:
                    logger.warning(f"Failed to close audit log session: {close_exc}")


# Convenience function
def audit_log(
    event_type: str,
    action: str,
    operator_id: str,
    **kwargs,
) -> None:
    """AuditService.log 的便捷包装。审计始终用独立 SessionLocal，不与主事务共享。"""
    params = AuditLogParams(
        event_type=event_type,
        action=action,
        operator_id=operator_id,
        **kwargs,
    )
    AuditService.log(params)


def list_skill_moderation_audit_logs_for_operator(
    db: Session,
    *,
    operator_id: str,
    page: int,
    page_size: int,
) -> tuple[list[AuditLog], int]:
    """当前操作者作为审核员产生的 Skill 审核类审计记录（按时间倒序，分页）。

    仅返回 result=SUCCESS 的记录：成功路径的 audit_log 在 db.commit() 之后才写，
    代表"已生效的审核决定"；失败补录（如自审 self_moderation_forbidden）写的是
    result=FAILED + action=MODERATE 的兜底行（见 Action.MODERATE 注释），并非真实
    审核，排除出"我的审核历史"。失败记录仍保留在管理员全量审计日志（audit/logs）。
    """
    safe_page = max(1, page)
    safe_size = min(max(1, page_size), 100)
    q = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == EVENT_SKILL_MODERATION,
            AuditLog.operator_id == operator_id,
            AuditLog.result == Result.SUCCESS,
        )
        .order_by(AuditLog.created_at.desc())
    )
    total = q.count()
    rows = q.offset((safe_page - 1) * safe_size).limit(safe_size).all()
    return rows, total


# ──────────────────────────────────────────────────────────────────────────────
# 管理员审计日志查询（全量，多条件过滤）
# ──────────────────────────────────────────────────────────────────────────────

MAX_DATE_RANGE_DAYS = 90
EXPORT_MAX_ROWS = 50_000


def _ms_to_bj_datetime(ms: int) -> datetime:
    """毫秒时间戳 → 明确指定北京时区的 naive datetime（用于和 DB 列比较）。

    DB 中 created_at 是按北京时间写入的 naive DATETIME（见 AuditService.log
    使用 datetime.now(_BJ_TZ) 然后被 MySQL 当 naive 存入），因此查询时也必须
    在北京时区上下文中把毫秒戳转换为对应的本地时间，避免 8 小时偏差。
    """
    aware = datetime.fromtimestamp(ms / 1000, tz=_BJ_TZ)
    return aware.replace(tzinfo=None)


def _bj_datetime_to_ms(dt: datetime) -> int:
    """北京时间（naive）DATETIME → UTC 毫秒时间戳。"""
    aware = dt.replace(tzinfo=_BJ_TZ)
    return int(aware.timestamp() * 1000)


def _source_channel_sql_case():
    """SQL 等价物：镜像 detect_source_channel_from_ua（extra 优先，UA 兜底）。"""
    extra_ch = func.json_unquote(func.json_extract(AuditLog.extra, "$.source_channel"))
    ua_lower = func.lower(func.coalesce(AuditLog.user_agent, ""))
    ua_head = func.substring(ua_lower, 1, 32)

    cli_prefix_or_subst = or_(
        *[ua_lower.like(f"{p.lower()}%") for p in CLI_UA_PREFIXES],
        ua_head.like("%cli%"),
    )
    web_marker_match = or_(*[ua_lower.like(f"%{m}%") for m in WEB_UA_MARKERS])

    return case(
        (extra_ch.isnot(None), extra_ch),
        (cli_prefix_or_subst, SOURCE_CHANNEL_CLI),
        (web_marker_match, SOURCE_CHANNEL_WEB),
        else_=SOURCE_CHANNEL_API,
    )


def _build_audit_query(
    db: Session,
    *,
    keyword: Optional[str],
    date_from_ms: int,
    date_to_ms: int,
    resource_type: Optional[str],
    action: Optional[str],
    result: Optional[str],
    asset_plugin_type: Optional[str],
    source_channel: Optional[str] = None,
):
    """构造审计日志的核心查询（含全部过滤 + 排序），不分页、不 count。

    供 list_audit_logs_for_admin / iter_audit_logs_for_export / count helper 共用，
    避免重复维护 filter 链路。
    """
    dt_from = _ms_to_bj_datetime(date_from_ms)
    dt_to = _ms_to_bj_datetime(date_to_ms)

    q = db.query(AuditLog).filter(
        AuditLog.created_at >= dt_from,
        AuditLog.created_at <= dt_to,
    )

    if resource_type:
        q = q.filter(AuditLog.resource_type == resource_type)
    if action:
        q = q.filter(AuditLog.action == action)
    if result:
        q = q.filter(AuditLog.result == result)
    if asset_plugin_type:
        from plugins_market.models.market_assets import MarketAssetDB

        sub = (
            db.query(MarketAssetDB.asset_id)
            .filter(MarketAssetDB.plugin_type == asset_plugin_type)
            .subquery()
        )
        q = q.filter(AuditLog.resource_id.in_(sub))

    if source_channel:
        q = q.filter(_source_channel_sql_case() == source_channel)

    if keyword:
        kw = f"%{keyword.strip()}%"
        skill_name_expr = func.json_unquote(func.json_extract(AuditLog.extra, "$.skill_name"))
        skill_display_expr = func.json_unquote(
            func.json_extract(AuditLog.extra, "$.skill_display_name")
        )
        q = q.filter(
            or_(
                AuditLog.operator_name.like(kw),
                AuditLog.resource_id.like(kw),
                AuditLog.ip_address.like(kw),
                skill_name_expr.like(kw),
                skill_display_expr.like(kw),
            )
        )

    return q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())


def list_audit_logs_for_admin(
    db: Session,
    *,
    keyword: Optional[str] = None,
    date_from_ms: int,
    date_to_ms: int,
    resource_type: Optional[str] = None,
    action: Optional[str] = None,
    result: Optional[str] = None,
    asset_plugin_type: Optional[str] = None,
    source_channel: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    with_count: bool = True,
) -> tuple[list[AuditLog], Optional[int]]:
    """管理员全量查询。keyword 跨字段 LIKE 匹配；日期范围必填。

    keyword 命中字段：operator_name、extra.skill_name、extra.skill_display_name、
    resource_id、ip_address。
    resource_type / action / result 为精确匹配过滤，None 表示不过滤。
    asset_plugin_type 通过 market_assets.plugin_type 过滤（用 IN 子查询）：
    传 'skill' 只命中真正的 skill；传 'swarmskill' 只命中 swarmskill。

    with_count=False 时返回 (rows, None)，跳过 COUNT(*) 查询；用于导出场景。
    """
    safe_page = max(1, page)
    safe_size = min(max(1, page_size), 100)

    q = _build_audit_query(
        db,
        keyword=keyword,
        date_from_ms=date_from_ms,
        date_to_ms=date_to_ms,
        resource_type=resource_type,
        action=action,
        result=result,
        asset_plugin_type=asset_plugin_type,
        source_channel=source_channel,
    )

    total = q.count() if with_count else None
    rows = q.offset((safe_page - 1) * safe_size).limit(safe_size).all()
    return rows, total


def count_audit_logs_for_admin(
    db: Session,
    *,
    keyword: Optional[str] = None,
    date_from_ms: int,
    date_to_ms: int,
    resource_type: Optional[str] = None,
    action: Optional[str] = None,
    result: Optional[str] = None,
    asset_plugin_type: Optional[str] = None,
    source_channel: Optional[str] = None,
    limit: Optional[int] = None,
) -> int:
    """仅返回匹配数，不取行。

    limit 非空时短路：用 query.limit(limit+1).count() 等价物
    判断"是否超过阈值"，避免大表上昂贵的精确 COUNT(*)。
    """
    q = _build_audit_query(
        db,
        keyword=keyword,
        date_from_ms=date_from_ms,
        date_to_ms=date_to_ms,
        resource_type=resource_type,
        action=action,
        result=result,
        asset_plugin_type=asset_plugin_type,
        source_channel=source_channel,
    )
    if limit is not None:
        # 只取 id 列 + LIMIT，最多扫 limit+1 行就够判超限
        capped = (
            q.with_entities(AuditLog.id)
            .limit(limit + 1)
            .all()
        )
        return len(capped)
    return q.count()


def fetch_asset_meta_map(
    db: Session, asset_ids: list[str]
) -> Dict[str, Dict[str, Optional[str]]]:
    """批量取 market_assets 的 display_name + plugin_type，用于审计列表补名。

    返回 {asset_id: {"display_name": str, "plugin_type": str, "name": str}}。
    缺失的 asset_id 不出现在结果里（前端自行回落）。
    """
    if not asset_ids:
        return {}
    # 延迟导入避免循环依赖（models 不依赖 core/audit）
    from plugins_market.models.market_assets import MarketAssetDB

    rows = (
        db.query(
            MarketAssetDB.asset_id,
            MarketAssetDB.display_name,
            MarketAssetDB.plugin_type,
            MarketAssetDB.name,
        )
        .filter(MarketAssetDB.asset_id.in_(asset_ids))
        .all()
    )
    return {
        str(aid): {
            "display_name": (dn or None),
            "plugin_type": (pt or None),
            "name": (nm or None),
        }
        for aid, dn, pt, nm in rows
    }


def get_audit_log_by_event_id(db: Session, event_id: str) -> Optional[AuditLog]:
    """按 event_id 点查（UNIQUE 索引）。"""
    return db.query(AuditLog).filter(AuditLog.event_id == event_id).first()


# 慢操作阈值：超过此 ms 算"慢"，与卡片展示口径对齐
SLOW_THRESHOLD_MS = 3000


def get_audit_log_stats(
    db: Session,
    *,
    date_from_ms: int,
    date_to_ms: int,
    keyword: Optional[str] = None,
    resource_type: Optional[str] = None,
    action: Optional[str] = None,
    result: Optional[str] = None,
    asset_plugin_type: Optional[str] = None,
    source_channel: Optional[str] = None,
) -> Dict[str, Any]:
    """统计：选定时间范围内的总数、失败数、慢操作数。

    过滤口径必须与列表/导出完全一致：用户在列表筛选 action=DELETE 后，
    顶部卡片数字也只统计 DELETE 子集，否则数字对不上引发认知断裂。
    一次 SQL 三个 CASE 聚合，避免三次扫描。
    """
    # 聚合 query 不需要 ORDER BY，否则 MySQL 会按 created_at 排完再聚合，纯浪费
    q = _build_audit_query(
        db,
        keyword=keyword,
        date_from_ms=date_from_ms,
        date_to_ms=date_to_ms,
        resource_type=resource_type,
        action=action,
        result=result,
        asset_plugin_type=asset_plugin_type,
        source_channel=source_channel,
    ).order_by(None)

    row = q.with_entities(
        func.count(AuditLog.id).label("total"),
        func.sum(
            case((AuditLog.result != Result.SUCCESS, 1), else_=0)
        ).label("failed"),
        func.sum(
            case((AuditLog.duration_ms >= SLOW_THRESHOLD_MS, 1), else_=0)
        ).label("slow"),
    ).one()

    return {
        "total": int(row.total or 0),
        "failed": int(row.failed or 0),
        "slow": int(row.slow or 0),
        "slow_threshold_ms": SLOW_THRESHOLD_MS,
    }


EXPORT_STREAM_CHUNK = 500


def iter_audit_logs_for_export(
    db: Session,
    *,
    keyword: Optional[str],
    date_from_ms: int,
    date_to_ms: int,
    resource_type: Optional[str] = None,
    action: Optional[str] = None,
    result: Optional[str] = None,
    asset_plugin_type: Optional[str] = None,
    source_channel: Optional[str] = None,
    max_rows: int = EXPORT_MAX_ROWS,
):
    """真流式迭代器：用 yield_per 分块拉取，单批 EXPORT_STREAM_CHUNK 行。

    内存占用 ≈ EXPORT_STREAM_CHUNK 行 × 单行大小，与 max_rows 无关。
    达到 max_rows 后主动停，防御性截断（路由层已有 EXPORT_MAX_ROWS 预检）。
    """
    q = _build_audit_query(
        db,
        keyword=keyword,
        date_from_ms=date_from_ms,
        date_to_ms=date_to_ms,
        resource_type=resource_type,
        action=action,
        result=result,
        asset_plugin_type=asset_plugin_type,
        source_channel=source_channel,
    )
    emitted = 0
    # yield_per 让 SQLAlchemy 用 server-side cursor 分批 fetch，每批 EXPORT_STREAM_CHUNK 行
    for row in q.yield_per(EXPORT_STREAM_CHUNK):
        yield row
        emitted += 1
        if emitted >= max_rows:
            break
