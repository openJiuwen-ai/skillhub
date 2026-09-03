from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from plugins_market.core.audit import audit_log
from plugins_market.core.audit_events import Action, EventType, ResourceType, Result
from plugins_market.core.context import (
    SOURCE_CHANNEL_BACKGROUND,
    set_request_context,
    set_source_channel,
    set_user_id,
)
from plugins_market.core.database import SessionLocal
from plugins_market.core.logging import get_logger
from plugins_market.core.operation_log import (
    complete_operation_result,
    get_operation_id,
    operation_context,
    operation_log_fields,
    safe_error_summary,
)
from plugins_market.core.publish_result import (
    PUBLISH_RESULT_FAILED,
    PUBLISH_RESULT_PENDING_MODERATION,
)
from plugins_market.core.s3_storage_client import get_storage_client
from plugins_market.models.market_assets import (
    MarketAssetDB,
    MarketAssetVersionDB,
    MarketSkillReviewDB,
)
from plugins_market.services.site_notifications import (
    notify_publisher_skill_system_review_failed,
    notify_review_admins_new_skill_submission,
)
from plugins_market.services.skill_review_runtime import (
    SkillReviewSemanticRuntimeError,
    run_skill_review,
)

logger = get_logger(__name__)

REVIEW_STATUS_QUEUED = "queued"
REVIEW_STATUS_RUNNING = "running"
REVIEW_STATUS_PASSED = "passed"
REVIEW_STATUS_FAILED = "failed"
REVIEW_STATUS_SYSTEM_FAILED = "system_failed"

SEMANTIC_REVIEW_STATUS_COMPLETED = "completed"
SEMANTIC_REVIEW_STATUS_FALLBACK = "fallback"
SEMANTIC_REVIEW_STATUS_DISABLED = "disabled"
SEMANTIC_REVIEW_STATUS_FAILED = "failed"

REVIEW_WRITEBACK_RETRY_BACKOFF_SECONDS = (0.1, 0.3, 0.7)
REVIEW_WRITEBACK_RETRY_JITTER_SECONDS = 0.05
RETRYABLE_REVIEW_WRITEBACK_MYSQL_ERROR_CODES = {1020, 1205, 1213}

USER_FACING_REVIEW_FAILED_TIMEOUT = "审查超时，请稍后重试发布。"
USER_FACING_REVIEW_FAILED_SERVICE_UNAVAILABLE = "审查服务暂时不可用，请稍后重试发布。"
USER_FACING_REVIEW_FAILED_INVALID_RESPONSE = "审查结果异常，请稍后重试发布。"
USER_FACING_REVIEW_FAILED_UNKNOWN = "审查执行异常，请稍后重试发布。"
USER_FACING_SEMANTIC_FALLBACK = "AI 语义复核暂时不可用，本次已按规则审查完成。"
USER_FACING_SEMANTIC_DISABLED = "AI 语义复核未启用，本次已按规则审查完成。"
USER_FACING_SEMANTIC_NO_RISK = "AI 语义复核已完成，当前未发现额外风险。"
USER_FACING_SEMANTIC_HAS_FINDINGS = "AI 语义复核已补充发现可疑信号，请结合下方证据继续判断。"


@dataclass(frozen=True)
class SemanticSummaryPresence:
    semantic_status: str | None
    semantic_engine: Any
    semantic_model_name: Any
    semantic_conclusion: str
    semantic_finding_count: int


_active_review_tasks: set[str] = set()
_active_review_tasks_lock = threading.Lock()


def now_ms() -> int:
    return int(time.time() * 1000)


def _review_task_key(asset_id: str, version: str) -> str:
    return f"{asset_id}:{version}"


def _is_review_task_active(asset_id: str, version: str) -> bool:
    with _active_review_tasks_lock:
        return _review_task_key(asset_id, version) in _active_review_tasks


def _try_claim_review_task(asset_id: str, version: str) -> bool:
    task_key = _review_task_key(asset_id, version)
    with _active_review_tasks_lock:
        if task_key in _active_review_tasks:
            return False
        _active_review_tasks.add(task_key)
        return True


def _release_review_task(asset_id: str, version: str) -> None:
    with _active_review_tasks_lock:
        _active_review_tasks.discard(_review_task_key(asset_id, version))


def initialize_skill_review(
    *,
    db: Session,
    asset_id: str,
    version_id: str,
    created_at: int | None = None,
) -> MarketSkillReviewDB:
    """Create or reset the single review row for a Skill asset version."""
    ts_ms = created_at or now_ms()
    review = (
        db.query(MarketSkillReviewDB)
        .filter(
            MarketSkillReviewDB.asset_id == asset_id,
            MarketSkillReviewDB.version_id == version_id,
        )
        .first()
    )
    if review is None:
        review = MarketSkillReviewDB(
            asset_id=asset_id,
            version_id=version_id,
            review_status=REVIEW_STATUS_QUEUED,
            created_at=ts_ms,
            updated_at=ts_ms,
        )
        db.add(review)
        return review

    review.review_status = REVIEW_STATUS_QUEUED
    review.review_failed_reason = None
    review.review_mode = None
    review.review_engine = None
    review.model_name = None
    review.policy_version = None
    review.score = None
    review.overall = None
    review.risk = None
    review.conclusion = None
    review.metrics_json = None
    review.sections_json = None
    review.raw_output_json = None
    review.trace_id = None
    review.started_at = None
    review.finished_at = None
    review.updated_at = ts_ms
    return review


def _map_system_failure_reason_from_text(reason: str) -> str:
    normalized = str(reason or "").strip().lower()
    if not normalized:
        return USER_FACING_REVIEW_FAILED_UNKNOWN
    if "timed out" in normalized:
        return USER_FACING_REVIEW_FAILED_TIMEOUT
    if "failed with status" in normalized:
        return USER_FACING_REVIEW_FAILED_SERVICE_UNAVAILABLE
    invalid_response_markers = (
        "non-json",
        "missing message content",
        "not valid json",
        "not a valid semantic finding object",
    )
    if any(marker in normalized for marker in invalid_response_markers):
        return USER_FACING_REVIEW_FAILED_INVALID_RESPONSE
    return USER_FACING_REVIEW_FAILED_UNKNOWN


def _to_user_facing_review_failure_reason(exc: Exception) -> str:
    if isinstance(exc, SkillReviewSemanticRuntimeError):
        return _map_system_failure_reason_from_text(str(exc))
    return USER_FACING_REVIEW_FAILED_UNKNOWN


def _build_semantic_review_summary(review: MarketSkillReviewDB, raw_output: dict[str, Any]) -> dict[str, Any] | None:
    semantic_payload = raw_output.get("semantic_review") if isinstance(raw_output, dict) else None
    semantic_payload = semantic_payload if isinstance(semantic_payload, dict) else {}
    observability = raw_output.get("observability") if isinstance(raw_output, dict) else None
    observability = observability if isinstance(observability, dict) else {}
    semantic_response = semantic_payload.get("response") if isinstance(semantic_payload.get("response"), dict) else {}
    semantic_findings = semantic_payload.get("findings") if isinstance(semantic_payload.get("findings"), list) else []
    semantic_engine = semantic_payload.get("engine")
    semantic_model_name = semantic_payload.get("model_name")
    semantic_conclusion = (
        semantic_response.get("conclusion") if isinstance(semantic_response.get("conclusion"), str) else ""
    ).strip()
    semantic_error = (semantic_payload.get("error") if isinstance(semantic_payload.get("error"), str) else "").strip()
    review_mode = str(review.review_mode or observability.get("review_mode") or "").strip()

    semantic_status: str | None = None
    display_message = ""

    if review_mode == "hybrid" or semantic_conclusion or len(semantic_findings) > 0:
        semantic_status = SEMANTIC_REVIEW_STATUS_COMPLETED
        display_message = semantic_conclusion or (
            USER_FACING_SEMANTIC_HAS_FINDINGS if len(semantic_findings) > 0 else USER_FACING_SEMANTIC_NO_RISK
        )
    elif review_mode == "rules_fallback":
        if semantic_error:
            semantic_status = SEMANTIC_REVIEW_STATUS_FALLBACK
            display_message = USER_FACING_SEMANTIC_FALLBACK
        else:
            semantic_status = SEMANTIC_REVIEW_STATUS_DISABLED
            display_message = USER_FACING_SEMANTIC_DISABLED
    elif review.review_status == REVIEW_STATUS_SYSTEM_FAILED:
        semantic_status = SEMANTIC_REVIEW_STATUS_FAILED
        display_message = _map_system_failure_reason_from_text(semantic_error or review.review_failed_reason or "")
    elif semantic_error:
        semantic_status = SEMANTIC_REVIEW_STATUS_FALLBACK
        display_message = USER_FACING_SEMANTIC_FALLBACK

    if is_empty_semantic_review_summary(
        SemanticSummaryPresence(
            semantic_status=semantic_status,
            semantic_engine=semantic_engine,
            semantic_model_name=semantic_model_name,
            semantic_conclusion=semantic_conclusion,
            semantic_finding_count=len(semantic_findings),
        )
    ):
        return None

    return {
        "status": semantic_status or SEMANTIC_REVIEW_STATUS_COMPLETED,
        "engine": semantic_engine,
        "model_name": semantic_model_name,
        "conclusion": semantic_conclusion,
        "display_message": display_message or semantic_conclusion,
        "finding_count": len(semantic_findings),
        "findings": semantic_findings,
    }


def is_empty_semantic_review_summary(presence: SemanticSummaryPresence) -> bool:
    return (
        presence.semantic_status is None
        and not isinstance(presence.semantic_engine, str)
        and not isinstance(presence.semantic_model_name, str)
        and not presence.semantic_conclusion
        and presence.semantic_finding_count == 0
    )


def build_review_summary(review: MarketSkillReviewDB | None) -> dict[str, Any] | None:
    if review is None:
        return None
    sections = review.sections_json if isinstance(review.sections_json, list) else []
    metrics = review.metrics_json if isinstance(review.metrics_json, dict) else {}
    failed_count = _summary_metric_count(metrics, "failed_check_count", _fallback_count_sections(sections, "failed"))
    attention_count = _summary_metric_count(
        metrics,
        "attention_check_count",
        _fallback_count_sections(sections, "attention"),
    )
    dimension_count = _summary_metric_count(metrics, "section_count", len(sections))
    raw_output = review.raw_output_json or {}
    semantic_review = _build_semantic_review_summary(review, raw_output)
    return {
        "status": review.review_status,
        "failed_reason": review.review_failed_reason,
        "score": review.score,
        "overall": review.overall,
        "risk": review.risk,
        "conclusion": review.conclusion,
        "finished_at": review.finished_at,
        "failed_count": failed_count,
        "attention_count": attention_count,
        "dimension_count": dimension_count,
        "review_mode": review.review_mode,
        "review_engine": semantic_review.get("engine") if semantic_review else None,
        "model_name": semantic_review.get("model_name") if semantic_review else None,
        "trace_id": review.trace_id,
        "semantic_review": semantic_review,
    }


def _summary_metric_count(metrics: dict[str, Any], key: str, fallback: int) -> int:
    value = metrics.get(key)
    if isinstance(value, int):
        return max(0, value)
    return fallback


def _fallback_count_sections(sections: list[Any], status_value: str) -> int:
    count = 0
    for section in sections:
        if not isinstance(section, dict):
            continue
        status = str(section.get("display_status") or section.get("gate_status") or section.get("status") or "").strip()
        if status == status_value:
            count += 1
    return count


def _get_review(db: Session, asset_id: str, version_id: str) -> MarketSkillReviewDB | None:
    return (
        db.query(MarketSkillReviewDB)
        .filter(
            MarketSkillReviewDB.asset_id == asset_id,
            MarketSkillReviewDB.version_id == version_id,
        )
        .first()
    )


def _snapshot_asset_for_review(asset: MarketAssetDB) -> SimpleNamespace:
    return SimpleNamespace(
        asset_id=asset.asset_id,
        name=asset.name,
        plugin_type=asset.plugin_type,
        detail_desc=asset.detail_desc,
        short_desc=asset.short_desc,
        tags=list(asset.tags) if isinstance(asset.tags, list) else [],
        publisher_id=asset.publisher_id,
    )


def _snapshot_version_for_review(version_row: MarketAssetVersionDB) -> SimpleNamespace:
    return SimpleNamespace(
        version_id=version_row.version_id,
        version=version_row.version,
        file_path=version_row.file_path,
        artifact_sha256=version_row.artifact_sha256,
    )


def _normalize_artifact_sha256(value: Any) -> str:
    return str(value or "").strip().lower()


def _artifact_sha256_changed(reviewed_sha256: Any, current_sha256: Any) -> bool:
    return _normalize_artifact_sha256(reviewed_sha256) != _normalize_artifact_sha256(current_sha256)


def _database_error_code(exc: Exception) -> int | None:
    for candidate in (exc, getattr(exc, "orig", None)):
        args = getattr(candidate, "args", None)
        if args and isinstance(args[0], int):
            return args[0]
    return None


def _is_retryable_review_writeback_error(exc: Exception) -> bool:
    return _database_error_code(exc) in RETRYABLE_REVIEW_WRITEBACK_MYSQL_ERROR_CODES


def _review_writeback_retry_delay_seconds(retry_index: int) -> float:
    base_delay = REVIEW_WRITEBACK_RETRY_BACKOFF_SECONDS[retry_index]
    jitter = random.uniform(-REVIEW_WRITEBACK_RETRY_JITTER_SECONDS, REVIEW_WRITEBACK_RETRY_JITTER_SECONDS)
    return max(0.0, base_delay + jitter)


def _set_review_running(db: Session, review: MarketSkillReviewDB, started_at: int) -> None:
    review.review_status = REVIEW_STATUS_RUNNING
    review.review_failed_reason = None
    review.started_at = started_at
    review.updated_at = started_at
    db.add(review)
    db.commit()


def _audit_review_event(
    *,
    db: Session,
    asset: MarketAssetDB | None,
    version: str | None,
    action: str,
    result: str,
    detail: str,
    extra: dict | None = None,
) -> None:
    """SKILL_REVIEW 类审计写入。静默失败不影响主流程。

    operator_id 用 publisher_id（这次审查是因谁发布触发的）；
    后台线程必须确保已设置 source_channel='background'。
    """
    try:
        publisher_id = (getattr(asset, "publisher_id", "") or "").strip() or "system"
        publisher_name = (getattr(asset, "publisher_name", "") or "").strip() or None
        skill_name = (getattr(asset, "name", "") or "").strip() or None
        skill_display = (getattr(asset, "display_name", "") or "").strip() or None
        asset_id = (getattr(asset, "asset_id", "") or "").strip() or None
        merged_extra = {
            "skill_name": skill_name,
            "skill_display_name": skill_display,
            **(extra or {}),
        }
        audit_log(
            event_type=EventType.SKILL_REVIEW,
            action=action,
            operator_id=publisher_id,
            operator_name=publisher_name,
            resource_type=ResourceType.SKILL,
            resource_id=asset_id,
            resource_version=version,
            result=result,
            detail=detail,
            extra=merged_extra,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit SKILL_REVIEW suppressed: %s", exc)


def _recalculate_skill_asset_aggregate(db: Session, asset: MarketAssetDB | None) -> None:
    if asset is None:
        return
    db.flush()
    # Import lazily to avoid a module cycle: plugin.py imports initialize_skill_review from here.
    from plugins_market.services.plugin import _apply_skill_asset_aggregate_from_versions

    _apply_skill_asset_aggregate_from_versions(db, asset.asset_id)


def _set_review_completed(
    *,
    db: Session,
    asset: MarketAssetDB,
    version_row: MarketAssetVersionDB,
    review: MarketSkillReviewDB,
    execution: Any,
    finished_at: int,
) -> None:
    review_result = execution.review_result or {}
    passed = review_result.get("overall") == "approved"
    review.review_status = REVIEW_STATUS_PASSED if passed else REVIEW_STATUS_FAILED
    review.review_failed_reason = None if passed else str(review_result.get("conclusion") or "Skill 审查未通过")[:2000]
    review.review_mode = execution.review_mode
    review.review_engine = execution.review_engine
    review.model_name = execution.model_name
    review.policy_version = execution.policy_version
    review.score = review_result.get("score") if isinstance(review_result.get("score"), int) else None
    review.overall = review_result.get("overall")
    review.risk = review_result.get("risk")
    review.conclusion = review_result.get("conclusion")
    review.metrics_json = review_result.get("metrics")
    review.sections_json = review_result.get("sections")
    review.raw_output_json = execution.raw_output
    review.trace_id = execution.trace_id
    review.finished_at = finished_at
    review.updated_at = finished_at
    if passed:
        version_row.publish_result = PUBLISH_RESULT_PENDING_MODERATION
    else:
        version_row.publish_result = PUBLISH_RESULT_FAILED
    publisher_id = (asset.publisher_id or "").strip()
    db.add(review)
    db.add(version_row)
    _recalculate_skill_asset_aggregate(db, asset)
    db.commit()

    # 审计原则：仅记录"状态变更/替代人审"类事件。
    # - 通过：审查本身未改变 publish_result，紧接着的 PENDING_MOD_SET 才是状态变更，
    #   单独写 AUTO_REVIEW_PASS 与之语义重复，故不再记录（细节走运行日志）。
    # - 未通过：系统代替人做了拒绝决策，必须记录。
    review_extra = {
        "score": review.score,
        "overall": review.overall,
        "risk": review.risk,
        "review_mode": review.review_mode,
        "review_engine": review.review_engine,
        "model_name": review.model_name,
        "policy_version": review.policy_version,
    }
    if passed:
        _audit_review_event(
            db=db,
            asset=asset,
            version=version_row.version,
            action=Action.PENDING_MOD_SET,
            result=Result.SUCCESS,
            detail=f"审查通过后转入待审核 v{version_row.version}",
            extra={
                "publish_result": PUBLISH_RESULT_PENDING_MODERATION,
                **review_extra,
            },
        )
    else:
        _audit_review_event(
            db=db,
            asset=asset,
            version=version_row.version,
            action=Action.AUTO_REVIEW_FAIL,
            result=Result.FAILED,
            detail=f"系统自动审查未通过 v{version_row.version}：{review.review_failed_reason or '—'}",
            extra=review_extra,
        )

    if passed:
        try:
            notify_review_admins_new_skill_submission(db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify review admins failed after system review pass: %s", exc)
    elif publisher_id:
        try:
            notify_publisher_skill_system_review_failed(db, publisher_id=publisher_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify publisher system review failed: %s", exc)


def _set_review_system_failed(
    *,
    db: Session,
    asset: MarketAssetDB | None,
    version_row: MarketAssetVersionDB | None,
    review: MarketSkillReviewDB | None,
    reason: str,
    finished_at: int,
    internal_reason: str | None = None,
    raw_output: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> None:
    stored_internal_reason = (internal_reason or reason or "")[:4000]
    raw_observability = raw_output.get("observability") if isinstance(raw_output, dict) else None
    raw_observability = raw_observability if isinstance(raw_observability, dict) else {}
    raw_semantic = raw_output.get("semantic_review") if isinstance(raw_output, dict) else None
    raw_semantic = raw_semantic if isinstance(raw_semantic, dict) else {}
    if review is not None:
        review.review_status = REVIEW_STATUS_SYSTEM_FAILED
        review.review_failed_reason = reason[:2000]
        review.review_mode = str(raw_observability.get("review_mode") or "system_failed")
        review.review_engine = raw_observability.get("review_engine") or raw_semantic.get("engine")
        review.model_name = raw_semantic.get("model_name")
        review.policy_version = raw_observability.get("policy_version") or review.policy_version
        review.raw_output_json = raw_output or {"error": stored_internal_reason}
        review.trace_id = trace_id
        review.finished_at = finished_at
        review.updated_at = finished_at
        db.add(review)
    if version_row is not None:
        version_row.publish_result = PUBLISH_RESULT_FAILED
        db.add(version_row)
    if asset is not None:
        publisher_id = (asset.publisher_id or "").strip()
        _recalculate_skill_asset_aggregate(db, asset)
    else:
        publisher_id = ""
    db.commit()

    # 审计：审查异常（区别于审查未通过，是审查流程本身挂了）
    _audit_review_event(
        db=db,
        asset=asset,
        version=getattr(version_row, "version", None),
        action=Action.AUTO_REVIEW_SYS_FAIL,
        result=Result.FAILED,
        detail=f"审查执行异常：{(reason or '未知错误')[:200]}",
        extra={
            "user_facing_reason": reason,
            "internal_reason": stored_internal_reason,
            "trace_id": trace_id,
        },
    )

    if publisher_id:
        try:
            notify_publisher_skill_system_review_failed(db, publisher_id=publisher_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify publisher system review system-failed: %s", exc)


def run_skill_publish_review(asset_id: str, version: str) -> bool:
    """Run the data2ontology-compatible Skill review pipeline for a published Skill version.

    Returns True when the reviewed artifact was superseded and a fresh review should be scheduled.
    """
    db = SessionLocal()
    try:
        asset = db.query(MarketAssetDB).filter(MarketAssetDB.asset_id == asset_id).first()
        version_row = (
            db.query(MarketAssetVersionDB)
            .filter(
                MarketAssetVersionDB.asset_id == asset_id,
                MarketAssetVersionDB.version == version,
            )
            .first()
        )
        if not asset or not version_row:
            logger.warning(
            "skill review",
            **operation_log_fields(
                stage="prepare",
                result="failure",
                error_code="review_target_not_found",
                error_class="not_found",
                error_message="asset/version not found",
                asset_id=asset_id,
                version=version,
            ),
        )
            return False

        # 给后续 audit_log 设 user_id 为发布者，便于审计页按操作者过滤
        publisher_id_for_audit = (asset.publisher_id or "").strip()
        if publisher_id_for_audit:
            set_user_id(publisher_id_for_audit)

        asset_for_review = _snapshot_asset_for_review(asset)
        version_for_review = _snapshot_version_for_review(version_row)

        logger.info(
            "skill review",
            **operation_log_fields(stage="start", result="started", asset_id=asset_id, version=version),
        )
        ts_ms = now_ms()
        review = _get_review(db, asset.asset_id, version_row.version_id)
        logger.info(
            "skill review",
            **operation_log_fields(stage="prepare", result="started", asset_id=asset_id, version=version),
        )
        if review is None:
            review = initialize_skill_review(
                db=db,
                asset_id=asset.asset_id,
                version_id=version_row.version_id,
                created_at=ts_ms,
            )
            db.commit()
        _set_review_running(db, review, ts_ms)

        storage = get_storage_client()
        logger.info(
            "skill review",
            **operation_log_fields(stage="run_review", result="started", asset_id=asset_id, version=version),
        )
        execution = run_skill_review(asset=asset_for_review, version_row=version_for_review, storage=storage)
        logger.info(
            "skill review",
            **operation_log_fields(
                stage="run_review",
                result="success",
                asset_id=asset_id,
                version=version,
                review_mode=getattr(execution, "review_mode", None),
                review_engine=getattr(execution, "review_engine", None),
            ),
        )
        finished_at = now_ms()
        completion_review_status = None
        max_writeback_attempts = 1 + len(REVIEW_WRITEBACK_RETRY_BACKOFF_SECONDS)
        for attempt_index in range(max_writeback_attempts):
            if attempt_index > 0:
                time.sleep(_review_writeback_retry_delay_seconds(attempt_index - 1))
            # Drop stale transaction state before each writeback attempt, then reload current rows.
            db.rollback()
            asset = db.query(MarketAssetDB).filter(MarketAssetDB.asset_id == asset_id).first()
            version_row = (
                db.query(MarketAssetVersionDB)
                .filter(
                    MarketAssetVersionDB.asset_id == asset_id,
                    MarketAssetVersionDB.version == version,
                )
                .first()
            )
            review = _get_review(db, asset_id, version_row.version_id) if version_row else None
            if not asset or not version_row or not review:
                logger.warning(
                    "skill review",
                    **operation_log_fields(
                        stage="writeback",
                        result="failure",
                        error_code="review_writeback_target_not_found",
                        error_class="not_found",
                        error_message="asset/version/review not found",
                        asset_id=asset_id,
                        version=version,
                    ),
                )
                return False
            # Review validity is tied to the package bytes. Display metadata changes do not trigger full safety review.
            if _artifact_sha256_changed(version_for_review.artifact_sha256, version_row.artifact_sha256):
                logger.warning(
                    "skill review completion discarded because artifact changed: asset_id=%s version=%s "
                    "reviewed_sha256=%s current_sha256=%s",
                    asset_id,
                    version,
                    _normalize_artifact_sha256(version_for_review.artifact_sha256) or "<empty>",
                    _normalize_artifact_sha256(version_row.artifact_sha256) or "<empty>",
                )
                return True
            try:
                _set_review_completed(
                    db=db,
                    asset=asset,
                    version_row=version_row,
                    review=review,
                    execution=execution,
                    finished_at=finished_at,
                )
                completion_review_status = review.review_status
                break
            except Exception as writeback_exc:
                if (
                    attempt_index >= max_writeback_attempts - 1
                    or not _is_retryable_review_writeback_error(writeback_exc)
                ):
                    raise
                db.rollback()
                logger.warning(
                    "skill review",
                    **operation_log_fields(
                        stage="writeback",
                        result="retrying",
                        error_code="review_writeback_conflict",
                        error_class="upstream",
                        asset_id=asset_id,
                        version=version,
                        attempt_no=attempt_index + 1,
                        max_attempts=max_writeback_attempts,
                        mysql_error_code=_database_error_code(writeback_exc),
                    ),
                )
                logger.info(
                    "skill review",
                    **operation_log_fields(stage="writeback", result="started", asset_id=asset_id, version=version),
                )
        logger.info(
            "skill review",
            **operation_log_fields(
                stage="complete",
                result="success",
                asset_id=asset_id,
                version=version,
                review_status=completion_review_status,
            ),
        )
        return False
    except Exception as exc:
        db.rollback()
        finished_at = now_ms()
        internal_reason = str(exc) or exc.__class__.__name__
        user_reason = _to_user_facing_review_failure_reason(exc)
        try:
            asset = db.query(MarketAssetDB).filter(MarketAssetDB.asset_id == asset_id).first()
            version_row = (
                db.query(MarketAssetVersionDB)
                .filter(
                    MarketAssetVersionDB.asset_id == asset_id,
                    MarketAssetVersionDB.version == version,
                )
                .first()
            )
            review = _get_review(db, asset_id, version_row.version_id) if version_row else None
            raw_output = None
            trace_id = getattr(exc, "trace_id", None)
            if isinstance(exc, SkillReviewSemanticRuntimeError):
                raw_output = {
                    "observability": {
                        "trace_id": trace_id,
                        "policy_version": getattr(exc, "policy_version", None),
                        "review_mode": "system_failed",
                        "review_engine": getattr(exc, "engine", None),
                    },
                    "semantic_review": {
                        "engine": getattr(exc, "engine", None),
                        "model_name": getattr(exc, "model_name", None),
                        "request_body": exc.request_body,
                        "response_body": exc.response_body,
                        "response_content": exc.response_content,
                        "error": internal_reason[:4000],
                    },
                    "error": internal_reason[:4000],
                }
            _set_review_system_failed(
                db=db,
                asset=asset,
                version_row=version_row,
                review=review,
                reason=user_reason,
                finished_at=finished_at,
                internal_reason=internal_reason,
                raw_output=raw_output,
                trace_id=trace_id,
            )
        except Exception as rollback_exc:
            db.rollback()
            logger.exception(
                "skill review",
                **operation_log_fields(
                    stage="rollback_failure",
                    result="failure",
                    error_code="review_system_failed",
                    error_class="internal",
                    error_message=safe_error_summary(
                        error_code="review_system_failed",
                        error_class="internal",
                        fallback=rollback_exc.__class__.__name__,
                    ),
                    asset_id=asset_id,
                    version=version,
                ),
            )
        logger.exception("skill review failed")
        logger.warning(
            "skill review",
            **complete_operation_result(
                result="failure",
                error_code="review_system_failed",
                error_class="internal",
                error_message=safe_error_summary(
                    error_code="review_system_failed",
                    error_class="internal",
                    fallback="skill review failed",
                ),
                asset_id=asset_id,
                version=version,
            ),
        )
        return False
    finally:
        db.close()


def _run_skill_review_task(
    asset_id: str,
    version: str,
    trigger: str,
    parent_operation_id: str | None = None,
    retry_of: str | None = None,
) -> None:
    # 后台线程不继承父请求的 ContextVar；为审计提供最少上下文：
    #   - request_id：本次审查独立的 id，便于按 review 串联事件
    #   - source_channel：'background'
    # user_id 在 run_skill_publish_review 内部加载 asset 后再设为发布者 id
    set_request_context()
    set_source_channel(SOURCE_CHANNEL_BACKGROUND)

    should_schedule_fresh_review = False
    current_operation_id: str | None = None
    try:
        with operation_context(
            operation_type="skill_publish_review",
            parent_operation_id=parent_operation_id,
            retry_of=retry_of,
        ):
            current_operation_id = get_operation_id()
            logger.info(
                "skill review",
                **operation_log_fields(
                    stage="background_start",
                    result="started",
                    asset_id=asset_id,
                    version=version,
                    trigger=trigger,
                ),
            )
            should_schedule_fresh_review = run_skill_publish_review(asset_id, version)
    finally:
        _release_review_task(asset_id, version)
    if should_schedule_fresh_review:
        schedule_skill_publish_review(
            asset_id,
            version,
            "artifact_changed",
            parent_operation_id=parent_operation_id,
            retry_of=current_operation_id,
        )


def schedule_skill_publish_review(
    asset_id: str,
    version: str,
    trigger: str = "api",
    parent_operation_id: str | None = None,
    retry_of: str | None = None,
) -> bool:
    if not _try_claim_review_task(asset_id, version):
        logger.info(
            "skill review scheduling skipped because task is already active: asset_id=%s version=%s trigger=%s",
            asset_id,
            version,
            trigger,
        )
        return False
    thread = threading.Thread(
        target=_run_skill_review_task,
        args=(asset_id, version, trigger, parent_operation_id, retry_of),
        daemon=True,
        name=f"skill-review-{asset_id[:8]}-{version}",
    )
    thread.start()
    logger.info(
        "skill review",
        **operation_log_fields(
            stage="schedule",
            result="accepted",
            asset_id=asset_id,
            version=version,
            trigger=trigger,
            retry_of=retry_of,
        ),
    )
    return True
