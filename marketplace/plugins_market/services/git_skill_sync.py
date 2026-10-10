# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""从 Git 仓库批量接入 Skill：复用 skill-import 归一化与 publish，字母序处理条目。"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
import time
from urllib.parse import urlparse

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from plugins_market.core.errors import PublishError
from plugins_market.core.auth import AuthContext
from plugins_market.core.publish_result import PUBLISH_RESULT_REVIEWING
from plugins_market.core.audit import audit_log
from plugins_market.core.audit_events import (
    Action,
    EventType,
    ResourceType,
    Result,
    resolve_batch_audit_result,
)
from plugins_market.core.context import (
    SOURCE_CHANNEL_BACKGROUND,
    set_request_context,
    set_source_channel,
)
from plugins_market.core.database import SessionLocal
from plugins_market.core.logging import get_logger
from plugins_market.core.operation_log import (
    get_operation_id,
    operation_context,
    operation_log_fields,
    safe_error_summary,
    sanitize_error_message,
)
from plugins_market.core.s3_storage_client import S3StorageClient, get_storage_client
from plugins_market.imports.skill_entries import (
    entry_to_publish_zip,
    is_simple_skill_entry,
    is_standard_skill_entry,
    resolve_skill_entry_declared_version,
)
from plugins_market.imports.skill_import_service import skill_import_from_staging_dir
from plugins_market.models.git_sources import GitSourceDB
from plugins_market.models.market_assets import MarketAssetDB
from plugins_market.repositories.git_source_repository import GitSourceRepository
from plugins_market.repositories.market_assets_repository import MarketAssetRepository
from plugins_market.schemas.plugin import (
    GitSyncRunResponse,
    SkillImportItemResult,
    SkillImportResponse,
    SkillImportSummary,
)
from plugins_market.services.skill_review import schedule_skill_publish_review
from plugins_market.utils.git_ref_rules import assert_git_ref_branch_or_tag
from plugins_market.utils.git_skills_subpath_rules import assert_git_skills_subpath
from plugins_market.utils.git_repo_canonical import (
    normalize_git_repo_global_key,
    parse_git_repo_owner,
)
from plugins_market.utils.git_source_dedup import compute_git_source_dedup_key
from plugins_market.utils.git_url_safety import (
    assert_public_git_clone_url,
    assert_resolved_git_host_allowed,
)
from plugins_market.validation.constants import MAX_ZIP_ENTRIES

logger = get_logger(__name__)


def _publish_error_log_fields(error: PublishError) -> dict[str, object]:
    detail = error.detail if isinstance(error.detail, dict) else {}
    error_code = detail.get("error_code") or detail.get("error")
    error_class = detail.get("error_class")
    return operation_log_fields(
        stage="complete",
        result="failure",
        error_code=error_code,
        error_class=error_class,
        error_message=sanitize_error_message(
            detail.get("message"),
            fallback=safe_error_summary(error_code=error_code, error_class=error_class, fallback="git sync failed"),
        ),
    )


def _exception_log_fields(
    *,
    error_code: str = "SKILLHUB_INTERNAL_UNEXPECTED",
    fallback: str = "git sync failed",
) -> dict[str, object]:
    return operation_log_fields(
        stage="complete",
        result="failure",
        error_code=error_code,
        error_class="internal",
        error_message=safe_error_summary(error_code=error_code, error_class="internal", fallback=fallback),
    )


def _safe_publish_error_text(error: PublishError) -> str:
    detail = error.detail if isinstance(error.detail, dict) else {}
    error_code = detail.get("error_code") or detail.get("error")
    error_class = detail.get("error_class")
    return sanitize_error_message(
        detail.get("message"),
        fallback=safe_error_summary(error_code=error_code, error_class=error_class, fallback="git sync failed"),
    ) or "git sync failed"


def _safe_internal_error_text(
    error_code: str = "SKILLHUB_INTERNAL_UNEXPECTED",
    fallback: str = "git sync failed",
) -> str:
    return safe_error_summary(error_code=error_code, error_class="internal", fallback=fallback)


def _safe_cleanup_error_text() -> str:
    return _safe_internal_error_text(error_code="git_source_cleanup_failed", fallback="git cleanup failed")


def _safe_background_error_text() -> str:
    return _safe_internal_error_text(error_code="git_sync_background_failed", fallback="git sync background failed")


def _safe_rollback_error_text() -> str:
    return _safe_internal_error_text(error_code="SKILLHUB_GIT_SYNC_ROLLBACK", fallback="git sync rollback failed")


def _safe_rollback_result_detail() -> str:
    return "rolled_back"


def _safe_cleanup_error_code() -> str:
    return "git_source_cleanup_failed"


def _safe_background_error_code() -> str:
    return "git_sync_background_failed"


def _start_background_git_sync_operation(
    *,
    source_id: str,
    user_id: str,
    fail_fast: bool,
    parent_operation_id: str | None = None,
    git_action: str = "sync",
    operator_name: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    set_request_context()
    set_source_channel(SOURCE_CHANNEL_BACKGROUND)
    with operation_context(operation_type="git_source_sync", parent_operation_id=parent_operation_id):
        logger.info(
            "git sync background",
            **operation_log_fields(
                stage="background_start",
                result="started",
                source_id=source_id,
                user_id=user_id,
                fail_fast=fail_fast,
            ),
        )
        run_git_source_sync_background(
            source_id=source_id,
            user_id=user_id,
            fail_fast=fail_fast,
            git_action=git_action,
            operator_name=operator_name,
            ip_address=ip_address,
            user_agent=user_agent,
        )



_SIMPLE_AUTHOR_FALLBACK = "skillhub_user"
# 后台同步心跳：刷新 update_time_ms，供僵死判定；长任务导入期间须周期性 touch
_GIT_SYNC_HEARTBEAT_INTERVAL_S = 5 * 60
# 超过该时长无心跳（update_time_ms 未刷新）且仍为 syncing → 僵死
_STALE_GIT_SYNC_MS = 30 * 60 * 1000
# 首次同步从未完成（last_indexed_at_ms 为空）且无心跳超过该时长 → 列表侧可修复（偏短，依赖心跳）
_LIST_ORPHAN_NO_HEARTBEAT_MS = 15 * 60 * 1000
_STALE_SYNC_RECOVER_MESSAGE = (
    "同步未完成（后台任务可能已中断，或环境缺少 git / 无法访问仓库）。请点「再次同步」重试。"
)
_ORPHAN_SYNC_RECOVER_MESSAGE = (
    "同步可能已中断（如服务重启或长时间无进度）。请点「再次同步」重试。"
)

_local_git_sync_active: set[str] = set()
_local_git_sync_lock = threading.Lock()


def commit_sha_to_version(full_sha: str) -> str:
    """无 SKILL.md 声明版本时，使用 commit 7 位短码作为市场版本号（与 DB version 列一致）。"""
    from ..validation.constants import commit_full_sha_to_version

    return commit_full_sha_to_version(full_sha)


def _resolve_git_entry_version(entry: Path, commit_sha: str) -> str:
    v = resolve_skill_entry_declared_version(entry)
    if v:
        return v
    return commit_sha_to_version(commit_sha)


def _assert_public_git_http_url(repo_url: str) -> str:
    return assert_public_git_clone_url(repo_url)


def is_reclaimable_git_source(db: Session, source: GitSourceDB) -> bool:
    """无关联 Skill 且未成功接入的 Git 源可由同一属主复用（释放 dedup 槽位供再次同步，不删除记录）。"""
    status = (source.last_index_status or "").strip().lower()
    if status == "syncing":
        return is_stale_git_source_sync(source) and GitSourceRepository(db).count_linked_assets(source.id) == 0
    if status in ("success", "partial_failure"):
        return False
    return GitSourceRepository(db).count_linked_assets(source.id) == 0


def mark_git_source_syncing(db: Session, source: GitSourceDB) -> None:
    """进入 syncing；保留 last_indexed_at_ms，避免再次同步被误判为孤儿。"""
    now_ms = int(time.time() * 1000)
    source.last_index_status = "syncing"
    source.last_index_error = None
    source.update_time_ms = now_ms
    db.add(source)
    db.commit()


def touch_git_source_sync_heartbeat(db: Session, source_id: str) -> None:
    """同步进行中刷新 update_time_ms，避免长耗时任务被僵死回收误判。"""
    sid = (source_id or "").strip()
    if not sid:
        return
    try:
        row = GitSourceRepository(db).get_by_id(sid)
        if row is None or (row.last_index_status or "").strip().lower() != "syncing":
            return
        row.update_time_ms = int(time.time() * 1000)
        db.add(row)
        db.commit()
    except SQLAlchemyError:
        db.rollback()


class _GitSyncHeartbeat:
    """按间隔 touch DB，供 run_git_source_sync 长任务使用。"""

    def __init__(self, db: Session, source_id: str) -> None:
        self._db = db
        self._source_id = source_id
        self._last_mono = 0.0
        self.touch()

    def touch(self) -> None:
        touch_git_source_sync_heartbeat(self._db, self._source_id)
        self._last_mono = time.monotonic()

    def maybe_touch(self) -> None:
        if time.monotonic() - self._last_mono >= _GIT_SYNC_HEARTBEAT_INTERVAL_S:
            self.touch()


def _finalize_git_source_sync_status(
    db: Session,
    source_id: str,
    *,
    status: str,
    error: str | None,
) -> None:
    """在可能污染 Session 的异常之后，用独立提交写回 Git 源同步状态。"""
    try:
        db.rollback()
    except SQLAlchemyError:
        pass
    row = GitSourceRepository(db).get_by_id(source_id)
    if row is None:
        return
    now_ms = int(time.time() * 1000)
    row.last_index_status = status
    row.last_index_error = (error or None)[:2000] if error else None
    row.last_indexed_at_ms = now_ms
    row.update_time_ms = now_ms
    db.add(row)
    db.commit()


def is_stale_git_source_sync(source: GitSourceDB) -> bool:
    """syncing 且超过 _STALE_GIT_SYNC_MS 未刷新 update_time_ms（无心跳）。"""
    if (source.last_index_status or "").strip().lower() != "syncing":
        return False
    updated = int(source.update_time_ms or 0)
    if updated <= 0:
        return True
    return int(time.time() * 1000) - updated > _STALE_GIT_SYNC_MS


def is_orphan_git_source_syncing(source: GitSourceDB) -> bool:
    """syncing 且从未完成过任一次同步（last_indexed_at_ms 仍为空）。"""
    if (source.last_index_status or "").strip().lower() != "syncing":
        return False
    return int(source.last_indexed_at_ms or 0) <= 0


def recover_interrupted_git_syncing_on_startup(db: Session) -> int:
    """进程启动时仅回收僵死 syncing，避免多副本误伤其他实例正在执行的后台任务。"""
    rows = db.query(GitSourceDB).filter(GitSourceDB.last_index_status == "syncing").all()
    n = 0
    for src in rows:
        if is_stale_git_source_sync(src):
            _set_git_source_sync_failed(db, src, _STALE_SYNC_RECOVER_MESSAGE)
            n += 1
        elif is_abandoned_orphan_git_sync(src):
            _set_git_source_sync_failed(db, src, _ORPHAN_SYNC_RECOVER_MESSAGE)
            n += 1
    return n


def recover_orphan_git_syncing_on_startup(db: Session) -> int:
    """兼容旧调用名。"""
    return recover_interrupted_git_syncing_on_startup(db)


def is_abandoned_orphan_git_sync(source: GitSourceDB) -> bool:
    """首次同步从未完成，且超过阈值无心跳（启动回收 / 列表修复 / 可打断同步）。"""
    if not is_orphan_git_source_syncing(source):
        return False
    updated = int(source.update_time_ms or 0)
    if updated <= 0:
        return True
    return int(time.time() * 1000) - updated > _LIST_ORPHAN_NO_HEARTBEAT_MS


def is_git_source_sync_blocking(source: GitSourceDB) -> bool:
    """仍为 syncing 且未僵死、非可打断孤儿 → 不应再开同步/删除。"""
    if (source.last_index_status or "").strip().lower() != "syncing":
        return False
    return not is_stale_git_source_sync(source) and not is_abandoned_orphan_git_sync(source)


def is_local_git_sync_running(source_id: str) -> bool:
    sid = (source_id or "").strip()
    if not sid:
        return False
    with _local_git_sync_lock:
        return sid in _local_git_sync_active


def register_local_git_sync(source_id: str) -> bool:
    """本进程登记同步任务；已登记则返回 False。后台 finally 释放；进程重启清空 set。"""
    sid = (source_id or "").strip()
    if not sid:
        return False
    with _local_git_sync_lock:
        if sid in _local_git_sync_active:
            return False
        _local_git_sync_active.add(sid)
        return True


def unregister_local_git_sync(source_id: str) -> None:
    sid = (source_id or "").strip()
    if not sid:
        return
    with _local_git_sync_lock:
        _local_git_sync_active.discard(sid)


def prepare_git_source_sync_start(db: Session, source: GitSourceDB) -> None:
    """HTTP 入队后台任务前：DB 互斥 + 本进程防双任务。"""
    if is_git_source_sync_blocking(source):
        raise PublishError(
            code=409,
            error="git_source_sync_in_progress",
            message="该 Git 源正在同步中，请稍后再试",
        )
    if is_local_git_sync_running(source.id):
        raise PublishError(
            code=409,
            error="git_source_sync_in_progress",
            message="该 Git 源正在同步中，请稍后再试",
        )
    if not register_local_git_sync(source.id):
        raise PublishError(
            code=409,
            error="git_source_sync_in_progress",
            message="该 Git 源正在同步中，请稍后再试",
        )


def recover_stale_git_sources_for_user(db: Session, user_id: str) -> int:
    """列表打开时修复「无心跳的孤儿 syncing」（不回收仍有心跳的长任务）。返回修复条数。"""
    uid = (user_id or "").strip()
    if not uid:
        return 0
    rows = (
        db.query(GitSourceDB)
        .filter(GitSourceDB.created_by_user_id == uid)
        .filter(GitSourceDB.last_index_status == "syncing")
        .all()
    )
    if not rows:
        return 0
    n = 0
    for src in rows:
        if not is_abandoned_orphan_git_sync(src):
            continue
        _set_git_source_sync_failed(db, src, _ORPHAN_SYNC_RECOVER_MESSAGE)
        n += 1
    return n


def _set_git_source_sync_failed(db: Session, source: GitSourceDB, message: str) -> None:
    now_ms = int(time.time() * 1000)
    source.last_index_status = "failed"
    source.last_index_error = (message or "同步失败")[:2000]
    source.last_indexed_at_ms = now_ms
    source.update_time_ms = now_ms
    db.add(source)
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()


def _resolve_skills_root(clone_root: Path, skills_subpath: str | None) -> Path:
    clone_resolved = clone_root.resolve()
    cleaned = assert_git_skills_subpath(skills_subpath)
    if cleaned is None:
        return clone_resolved
    rel = cleaned.replace("\\", "/").strip("/")
    root = (clone_root / rel).resolve()
    try:
        root.relative_to(clone_resolved)
    except ValueError as e:
        raise PublishError(
            code=400,
            error="invalid_skills_subpath",
            message="skills_subpath 必须位于克隆目录内",
        ) from e
    if not root.is_dir():
        raise PublishError(
            code=400,
            error="skills_subpath_not_found",
            message=f"仓库内未找到 skills_subpath 对应目录：{rel}",
        )
    return root


def _git_clone_at_ref(*, repo_url: str, ref: str, dest: Path, timeout: int = 300) -> str:
    host = (urlparse(repo_url).hostname or "").strip()
    if host:
        assert_resolved_git_host_allowed(host)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    proc_kw = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace", "env": env}
    try:
        dest.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=str(dest), check=True, timeout=60, **proc_kw)
        subprocess.run(["git", "remote", "add", "origin", repo_url], cwd=str(dest), check=True, timeout=60, **proc_kw)
        fetch = subprocess.run(
            ["git", "fetch", "--depth", "1", "origin", "--", ref],
            cwd=str(dest),
            timeout=timeout,
            **proc_kw,
        )
        if fetch.returncode != 0:
            msg = (fetch.stderr or fetch.stdout or "").strip()
            tail = msg[:800] if msg else ""
            message = f"git fetch 失败：{tail}" if tail else "git fetch 失败"
            raise PublishError(
                code=400,
                error="git_fetch_failed",
                message=message,
            )
        subprocess.run(["git", "checkout", "FETCH_HEAD"], cwd=str(dest), check=True, timeout=120, **proc_kw)
        rr = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(dest), check=True, timeout=60, **proc_kw)
        sha = (rr.stdout or "").strip()
        if len(sha) < 7:
            raise PublishError(code=500, error="git_invalid_state", message="无法解析 HEAD commit")
        return sha
    except FileNotFoundError as e:
        raise PublishError(
            code=503,
            error="git_not_available",
            message="服务器未安装 git 或不在 PATH 中",
        ) from e
    except PublishError:
        raise
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or e.stdout or str(e) or "").strip()
        raise PublishError(
            code=400,
            error="git_clone_failed",
            message=f"git 操作失败：{msg[:800]}",
        ) from e


def _external_skill_id(*, source_id: str, entry_folder_name: str) -> str:
    return f"git:{source_id}:{entry_folder_name}"


def _skill_entry_content_sha256(entry: Path) -> str:
    """对 skill 条目目录做内容摘要（路径+字节），与 zip mtime/非确定性无关。"""
    h = hashlib.sha256()
    root = entry.resolve()
    files = sorted(
        (p for p in root.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(root).as_posix(),
    )
    for fpath in files:
        rel = fpath.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(fpath.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()


def _git_entry_commit_unchanged(
    row: MarketAssetDB,
    *,
    git_source_id: str,
    head_sha: str,
) -> bool:
    prev_sha = (row.resolved_commit_sha or "").strip().lower()
    cur_sha = head_sha.strip().lower()
    if (row.storage_mode or "").strip().lower() != "git":
        return False
    if (row.git_source_id or "").strip() != (git_source_id or "").strip():
        return False
    return bool(prev_sha and cur_sha and prev_sha == cur_sha)


def _try_reclaim_publisher_skill_for_git(
    asset_repo: MarketAssetRepository,
    gs_repo: GitSourceRepository,
    *,
    user_id: str,
    skill_name: str,
    current_source_id: str,
) -> MarketAssetDB | None:
    """同发布者同名 Skill：仅回收 Git 孤儿，避免覆盖手动上传或其它有效源下的条目。

    可回收：storage_mode=git，且未绑定 / 绑当前源 / 所绑 Git 源已不存在。
    不可回收：手动上传（非 git）或仍绑定其它有效 Git 源——调用方应报同名冲突。
    """
    name = (skill_name or "").strip()
    if not name:
        return None
    current = (current_source_id or "").strip()
    rows = asset_repo.list_by_publisher_name_and_type(user_id, name)
    for row in rows:
        gs = (row.git_source_id or "").strip()
        mode = (row.storage_mode or "").strip().lower()
        if gs and gs == current:
            return row
        if mode != "git":
            continue
        if not gs or gs_repo.get_by_id(gs) is None:
            return row
    return None


def _raise_if_publisher_skill_name_blocks_git(
    asset_repo: MarketAssetRepository,
    *,
    user_id: str,
    skill_name: str,
    entry_folder_name: str,
) -> None:
    """存在不可回收的同名资产时，提示改名或删除，而不是 force 覆盖。"""
    name = (skill_name or "").strip()
    if not name:
        return
    rows = asset_repo.list_by_publisher_name_and_type(user_id, name)
    if not rows:
        return
    labels: list[str] = []
    for row in rows[:5]:
        nm = (row.name or "").strip() or name
        disp = (row.display_name or "").strip()
        aid = (row.asset_id or "").strip()
        bits = [f"名称={nm}"]
        if disp and disp != nm:
            bits.append(f"显示名={disp}")
        if aid:
            bits.append(f"id={aid}")
        labels.append("（" + "，".join(bits) + "）")
    listed = "、".join(labels)
    if len(rows) > 5:
        listed = f"{listed} 等共 {len(rows)} 个"
    folder = (entry_folder_name or "").strip() or name
    raise PublishError(
        code=409,
        error="plugin_name_exists",
        message=(
            f"重名冲突：仓库条目「{folder}」→ Skill「{name}」，"
            f"与您已发布的同名项冲突：{listed}。"
            "请修改仓库中的 Skill 名称，或先在「我的 Skill」中删除上述同名项后再同步。"
        ),
    )


def _list_skill_entry_dirs(skills_root: Path) -> list[Path]:
    entry_dirs = sorted(
        [
            p
            for p in skills_root.iterdir()
            if p.is_dir() and not p.name.startswith(".") and p.name != "__MACOSX"
        ],
        key=lambda p: p.name,
    )
    rows: list[Path] = []
    for p in entry_dirs:
        if is_standard_skill_entry(p) or is_simple_skill_entry(p):
            rows.append(p)
    return rows


def _skill_import_item_from_prep_error(entry_name: str, exc: BaseException) -> SkillImportItemResult:
    if isinstance(exc, PublishError):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return SkillImportItemResult(
            entry=entry_name,
            status="error",
            error=str(detail.get("error") or "publish_failed"),
            message=str(detail.get("message") or exc),
        )
    if isinstance(exc, ValueError):
        return SkillImportItemResult(
            entry=entry_name,
            status="error",
            error="import_normalize_failed",
            message=str(exc),
        )
    if isinstance(exc, SQLAlchemyError):
        return SkillImportItemResult(
            entry=entry_name,
            status="error",
            error="publish_db_error",
            message=str(exc)[:500],
        )
    logger.exception("git sync prep failed entry=%s", entry_name)
    return SkillImportItemResult(
        entry=entry_name,
        status="error",
        error="prep_entry_failed",
        message=str(exc)[:500],
    )


def _merge_skill_import_results(
    data: SkillImportResponse,
    extra: list[SkillImportItemResult],
) -> SkillImportResponse:
    if not extra:
        return data
    results = [*data.results, *extra]
    ok = sum(1 for r in results if r.status == "ok")
    failed = sum(1 for r in results if r.status == "error")
    skipped = sum(1 for r in results if r.status == "skipped")
    return SkillImportResponse(
        summary=SkillImportSummary(
            total=len(results),
            ok=ok,
            failed=failed,
            skipped=skipped,
        ),
        results=results,
    )


def _skill_import_response_from_results(results: list[SkillImportItemResult]) -> SkillImportResponse:
    ok = sum(1 for r in results if r.status == "ok")
    failed = sum(1 for r in results if r.status == "error")
    skipped = sum(1 for r in results if r.status == "skipped")
    return SkillImportResponse(
        summary=SkillImportSummary(
            total=len(results),
            ok=ok,
            failed=failed,
            skipped=skipped,
        ),
        results=results,
    )


def _format_git_sync_index_error(data: SkillImportResponse) -> str:
    summary = (
        f"导入汇总：成功 {data.summary.ok}，跳过 {data.summary.skipped}，失败 {data.summary.failed}"
    )
    failed_items = [r for r in data.results if r.status == "error"]
    if not failed_items:
        return summary
    details = "\n".join(
        f"{r.entry}: {r.message or r.error or '失败'}" for r in failed_items
    )
    text = f"{summary}\n{details}"
    return text[:2000]


def _skill_import_audit_items(data: SkillImportResponse) -> tuple[list[dict], list[dict]]:
    failed_entries = [
        {
            "entry": item.entry,
            "plugin_id": item.plugin_id,
            "name": item.name,
            "version": item.version,
            "error": item.error,
            "message": item.message,
        }
        for item in data.results
        if item.status == "error"
    ]
    skipped_entries = [
        {
            "entry": item.entry,
            "plugin_id": item.plugin_id,
            "name": item.name,
            "version": item.version,
            "message": item.message,
        }
        for item in data.results
        if item.status == "skipped"
    ]
    return failed_entries, skipped_entries


def _audit_git_sync_event(
    *,
    source: GitSourceDB,
    operator_id: str,
    operator_name: str | None,
    git_action: str,
    result: str,
    detail: str,
    extra: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    try:
        merged_extra = {
            "git_source_name": (getattr(source, "name", None) or "").strip() or None,
            "repo_url": (getattr(source, "repo_url", None) or "").strip() or None,
            "ref": (getattr(source, "ref", None) or "").strip() or None,
            "git_action": git_action,
            **(extra or {}),
        }
        audit_log(
            event_type=EventType.SKILL_MANAGE,
            action=Action.GIT_SYNC,
            operator_id=operator_id,
            operator_name=operator_name,
            resource_type=ResourceType.GIT_SOURCE,
            resource_id=source.id,
            result=result,
            detail=detail,
            ip_address=ip_address,
            user_agent=user_agent,
            extra=merged_extra,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit GIT_SYNC suppressed: %s", exc)


def _audit_git_sync_import_complete(
    *,
    source: GitSourceDB,
    data: SkillImportResponse,
    operator_id: str,
    operator_name: str | None,
    git_action: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    failed_entries, skipped_entries = _skill_import_audit_items(data)
    audit_result = resolve_batch_audit_result(
        ok_count=data.summary.ok,
        failed_count=data.summary.failed,
        skipped_count=data.summary.skipped,
    )
    if git_action == "create":
        action_label = "创建 Git 源并同步完成"
    else:
        action_label = "Git 源同步完成"
    _audit_git_sync_event(
        source=source,
        operator_id=operator_id,
        operator_name=operator_name,
        git_action=git_action,
        result=audit_result,
        detail=(
            f"{action_label}，成功 {data.summary.ok} 个，"
            f"失败 {data.summary.failed} 个，跳过 {data.summary.skipped} 个，"
            f"共 {data.summary.total} 个"
        ),
        ip_address=ip_address,
        user_agent=user_agent,
        extra={
            "total": data.summary.total,
            "ok_count": data.summary.ok,
            "failed_count": data.summary.failed,
            "skipped_count": data.summary.skipped,
            "failed_items": failed_entries[:50],
            "skipped_items": skipped_entries[:50],
            "skipped_items_truncated": len(skipped_entries) > 50,
        },
    )


def _bind_git_asset(
    db: Session,
    *,
    plugin_id: str,
    git_source_id: str,
    external_id: str,
    resolved_sha: str,
    payload_sha256: str,
    declared_skill_version: str | None = None,
    commit: bool = False,
) -> None:
    try:
        asset = db.query(MarketAssetDB).filter(MarketAssetDB.asset_id == plugin_id).first()
        if not asset:
            raise PublishError(
                code=500,
                error="git_bind_failed",
                message=f"发布成功但未找到资产记录：{plugin_id}",
            )
        asset.git_source_id = git_source_id
        asset.external_id = external_id
        asset.resolved_commit_sha = resolved_sha
        s256 = (payload_sha256 or "").strip().lower()[:64]
        if s256:
            asset.git_sync_payload_sha256 = s256
        asset.storage_mode = "git"
        ds = (declared_skill_version or "").strip()[:64]
        asset.declared_skill_version = ds or None
        db.add(asset)
        if commit:
            db.commit()
    except PublishError:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise PublishError(
            code=500,
            error="git_bind_failed",
            message=f"绑定 Git 元数据失败：{exc}",
        ) from exc


def run_git_source_sync(
    *,
    db: Session,
    storage: S3StorageClient,
    user_id: str,
    source: GitSourceDB,
    fail_fast: bool = False,
    reraise: bool = True,
    git_action: str = "sync",
    operator_name: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> GitSyncRunResponse | None:
    """克隆仓库、按字母序导入 skill；条目目录内容摘要未变则跳过发布（commit 未变时亦跳过）。"""
    logger.info(
        "git sync",
        **operation_log_fields(
            stage="start",
            result="started",
            source_id=source.id,
            user_id=user_id,
            fail_fast=fail_fast,
        ),
    )
    asset_repo = MarketAssetRepository(db)
    repo_url = _assert_public_git_http_url(source.repo_url)
    ref = assert_git_ref_branch_or_tag(source.ref or "main")
    logger.info(
        "git sync",
        **operation_log_fields(stage="prepare", result="started", source_id=source.id, repo_url=repo_url, ref=ref),
    )
    author = parse_git_repo_owner(repo_url).strip() or _SIMPLE_AUTHOR_FALLBACK
    # HTTP 路由在入队后台任务前已 mark；脚本入口 create_git_source_and_sync 自行 mark。
    heartbeat = _GitSyncHeartbeat(db, source.id)

    clone_dir = Path(tempfile.mkdtemp(prefix="oj_git_clone_"))
    staging_dir = Path(tempfile.mkdtemp(prefix="oj_git_stage_"))
    try:
        logger.info("git sync", **operation_log_fields(stage="clone_repo", result="started", source_id=source.id))
        head_sha = _git_clone_at_ref(repo_url=repo_url, ref=ref, dest=clone_dir, timeout=300)
        heartbeat.touch()
        logger.info(
            "git sync",
            **operation_log_fields(
                stage="clone_repo",
                result="success",
                source_id=source.id,
                resolved_commit_sha=head_sha,
            ),
        )
        logger.info("git sync", **operation_log_fields(stage="scan_entries", result="started", source_id=source.id))
        skills_root = _resolve_skills_root(clone_dir, source.skills_subpath)
        entry_dirs = _list_skill_entry_dirs(skills_root)
        logger.info(
            "git sync",
            **operation_log_fields(
                stage="scan_entries",
                result="success",
                source_id=source.id,
                entry_count=len(entry_dirs),
            ),
        )
        if not entry_dirs:
            raise PublishError(
                code=400,
                error="no_skills_in_repo",
                message="未在指定目录下找到有效 skill 条目",
            )
        if len(entry_dirs) > MAX_ZIP_ENTRIES:
            raise PublishError(
                code=400,
                error="too_many_skill_entries",
                message=f"skill 条目数 {len(entry_dirs)} 超过上限 {MAX_ZIP_ENTRIES}",
            )

        prep_payload_sha256: dict[str, str] = {}
        prep_content_sha256: dict[str, str] = {}
        manifest_entry_extra: dict[str, dict] = {}
        declared_semver_by_entry: dict[str, str | None] = {}
        prep_failures: list[SkillImportItemResult] = []
        ready_entries: list[Path] = []

        for entry in entry_dirs:
            heartbeat.maybe_touch()
            name = entry.name
            try:
                declared_semver_by_entry[name] = resolve_skill_entry_declared_version(entry)
                ext_id = _external_skill_id(source_id=source.id, entry_folder_name=name)
                existing = asset_repo.get_by_external_id(ext_id)
                if existing is not None and existing.publisher_id != user_id:
                    raise PublishError(
                        code=409,
                        error="git_external_id_conflict",
                        message=f"条目 {name} 已与另一发布者绑定，无法导入",
                    )

                ver = _resolve_git_entry_version(entry, head_sha)
                eo: dict = {
                    "version": ver,
                    "author": author,
                    "force": bool(existing),
                    "tags": [],
                    "version_desc": f"Git 同步 {source.name} @ {head_sha[:7]}",
                }
                if existing is not None:
                    eo["plugin_id"] = existing.asset_id

                # 内容摘要优先于 zip 字节：仓库其它路径变更导致 HEAD 前进时，未改动的 skill 应跳过
                prep_content_sha256[name] = _skill_entry_content_sha256(entry)

                publish_zip, skill_name, _v = entry_to_publish_zip(
                    entry,
                    entry_key=name,
                    entry_overrides=eo,
                    version_fallback="0.0.1",
                    default_author=author,
                    default_tags=[],
                )
                try:
                    raw = publish_zip.read_bytes()
                    prep_payload_sha256[name] = hashlib.sha256(raw).hexdigest()
                finally:
                    publish_zip.unlink(missing_ok=True)

                # Git 孤儿可回收 force；手动上传等同名不可覆盖，记入失败项提示用户改名或删除
                if existing is None:
                    reclaimed = _try_reclaim_publisher_skill_for_git(
                        asset_repo,
                        GitSourceRepository(db),
                        user_id=user_id,
                        skill_name=skill_name,
                        current_source_id=source.id,
                    )
                    if reclaimed is not None:
                        if reclaimed.publisher_id != user_id:
                            raise PublishError(
                                code=409,
                                error="git_external_id_conflict",
                                message=f"条目 {name} 已与另一发布者绑定，无法导入",
                            )
                        existing = reclaimed
                        eo["force"] = True
                        eo["plugin_id"] = reclaimed.asset_id
                    else:
                        _raise_if_publisher_skill_name_blocks_git(
                            asset_repo,
                            user_id=user_id,
                            skill_name=skill_name,
                            entry_folder_name=name,
                        )

                manifest_entry_extra[name] = eo
                ready_entries.append(entry)
            except Exception as exc:
                prep_failures.append(_skill_import_item_from_prep_error(name, exc))
                if fail_fast:
                    break

        for sub in staging_dir.iterdir():
            if sub.is_dir():
                shutil.rmtree(sub, ignore_errors=True)
        for entry in ready_entries:
            dest = staging_dir / entry.name
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(entry, dest, dirs_exist_ok=True)

        def _skip(name: str, _path: Path) -> tuple[bool, str | None]:
            ext_id = _external_skill_id(source_id=source.id, entry_folder_name=name)
            row = asset_repo.get_by_external_id(ext_id)
            if not row:
                return False, None
            prev_digest = (row.git_sync_payload_sha256 or "").strip().lower()
            cur_content = prep_content_sha256.get(name, "").strip().lower()
            cur_zip = prep_payload_sha256.get(name, "").strip().lower()
            # 1) 条目目录内容未变（即使仓库 HEAD 前进）
            if prev_digest and cur_content and prev_digest == cur_content:
                return True, "git_sync_content_unchanged"
            # 2) 兼容旧数据：曾存 zip 字节摘要
            if prev_digest and cur_zip and prev_digest == cur_zip:
                return True, "git_sync_payload_unchanged"
            # 3) 同一 commit：树未变（兜底；zip 非确定性时仍可靠）
            if _git_entry_commit_unchanged(
                row,
                git_source_id=source.id,
                head_sha=head_sha,
            ):
                return True, "git_sync_commit_unchanged"
            return False, None

        def _after_ok(entry_name: str, pr):
            ext_id = _external_skill_id(source_id=source.id, entry_folder_name=entry_name)
            # 优先持久化内容摘要，供后续同步在 HEAD 变化时仍可跳过
            digest = prep_content_sha256.get(entry_name, "") or prep_payload_sha256.get(entry_name, "")
            _bind_git_asset(
                db,
                plugin_id=pr.plugin_id,
                git_source_id=source.id,
                external_id=ext_id,
                resolved_sha=head_sha,
                payload_sha256=digest,
                declared_skill_version=declared_semver_by_entry.get(entry_name),
                commit=True,
            )
            if (pr.publish_result or "") == PUBLISH_RESULT_REVIEWING:
                schedule_skill_publish_review(
                    pr.plugin_id,
                    pr.version,
                    "git_sync",
                    parent_operation_id=get_operation_id(),
                )

        heartbeat.touch()
        logger.info(
            "git sync",
            **operation_log_fields(stage="import_entries", result="started", source_id=source.id, fail_fast=fail_fast),
        )
        if ready_entries:
            data = skill_import_from_staging_dir(
                staging_dir,
                user_id=user_id,
                db=db,
                storage=storage,
                force=False,
                fail_fast=fail_fast,
                manifest_entry_extra=manifest_entry_extra,
                entry_skip_predicate=lambda n, p: _skip(n, p),
                after_publish_success=_after_ok,
                on_entry_progress=lambda _name: heartbeat.maybe_touch(),
            )
            data = _merge_skill_import_results(data, prep_failures)
        else:
            data = _skill_import_response_from_results(prep_failures)

        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise PublishError(
                code=500,
                error="git_bind_failed",
                message=f"提交 Git 绑定失败：{exc}",
            ) from exc

        now_ms = int(time.time() * 1000)
        if data.summary.failed > 0:
            if data.summary.ok > 0 or data.summary.skipped > 0:
                source.last_index_status = "partial_failure"
            else:
                source.last_index_status = "failed"
            source.last_index_error = _format_git_sync_index_error(data)
        else:
            source.last_index_status = "success"
            source.last_index_error = None
        source.last_indexed_at_ms = now_ms
        source.update_time_ms = now_ms
        db.add(source)
        db.commit()

        operation_result = "partial_failure" if data.summary.failed > 0 else "success"
        logger.info(
            "git sync",
            **operation_log_fields(
                stage="complete",
                result=operation_result,
                result_detail=source.last_index_status,
                source_id=source.id,
                ok=data.summary.ok,
                skipped=data.summary.skipped,
                failed=data.summary.failed,
                resolved_commit_sha=head_sha,
            ),
        )

        _audit_git_sync_import_complete(
            source=source,
            data=data,
            operator_id=user_id,
            operator_name=operator_name,
            git_action=git_action,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return GitSyncRunResponse(
            source_id=source.id,
            resolved_commit_sha=head_sha,
            skill_import=data,
        )
    except PublishError as e:
        now_ms = int(time.time() * 1000)
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        logger.warning(
            "git sync",
            **_publish_error_log_fields(e),
            source_id=source.id,
        )
        _finalize_git_source_sync_status(
            db,
            source.id,
            status="failed",
            error=_safe_publish_error_text(e),
        )
        _audit_git_sync_event(
            source=source,
            operator_id=user_id,
            operator_name=operator_name,
            git_action=git_action,
            result=Result.FAILED,
            detail=f"Git 源同步失败：{_safe_publish_error_text(e)}",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if reraise:
            raise e
        return None
    except Exception as exc:
        logger.exception("git sync", **_exception_log_fields(), source_id=source.id)
        _finalize_git_source_sync_status(
            db,
            source.id,
            status="failed",
            error=_safe_internal_error_text(),
        )
        _audit_git_sync_event(
            source=source,
            operator_id=user_id,
            operator_name=operator_name,
            git_action=git_action,
            result=Result.FAILED,
            detail=f"Git 源同步失败：{_safe_internal_error_text()}",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if reraise:
            raise exc
        return None
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)
        shutil.rmtree(staging_dir, ignore_errors=True)


def run_git_source_sync_background(
    *,
    source_id: str,
    user_id: str,
    fail_fast: bool = False,
    git_action: str = "sync",
    operator_name: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """后台执行 Git 同步（独立 DB Session）。须在路由侧已 register_local_git_sync。"""
    db = SessionLocal()
    src: GitSourceDB | None = None
    try:
        storage = get_storage_client()
        gs_repo = GitSourceRepository(db)
        src = gs_repo.get_by_id(source_id)
        if src is None:
            logger.warning(
                "git sync background",
                **operation_log_fields(
                    stage="background_start",
                    result="failure",
                    error_code="source_not_found",
                    error_class="not_found",
                    source_id=source_id,
                ),
            )
            return
        if (src.created_by_user_id or "").strip() != (user_id or "").strip():
            logger.warning(
                "git sync background",
                **operation_log_fields(
                    stage="background_start",
                    result="failure",
                    error_code="user_mismatch",
                    error_class="permission",
                    source_id=source_id,
                    expected_user_id=user_id,
                ),
            )
            _set_git_source_sync_failed(db, src, "同步任务与 Git 源所有者不一致")
            _audit_git_sync_event(
                source=src,
                operator_id=user_id,
                operator_name=operator_name,
                git_action=git_action,
                result=Result.FAILED,
                detail="Git 源同步失败：同步任务与 Git 源所有者不一致",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return
        run_git_source_sync(
            db=db,
            storage=storage,
            user_id=user_id,
            source=src,
            fail_fast=fail_fast,
            reraise=False,
            git_action=git_action,
            operator_name=operator_name,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except Exception as exc:
        logger.exception(
            "git sync background",
            **_exception_log_fields(
                error_code=_safe_background_error_code(),
                fallback="git sync background failed",
            ),
            source_id=source_id,
        )
        if src is None:
            src = GitSourceRepository(db).get_by_id(source_id)
        if src is not None and (src.last_index_status or "").strip().lower() == "syncing":
            logger.warning(
                "git sync background",
                **operation_log_fields(
                    stage="background_finalize",
                    result="failure",
                    error_code=_safe_background_error_code(),
                    error_class="internal",
                    source_id=source_id,
                    error_message=_safe_background_error_text(),
                ),
            )
            _set_git_source_sync_failed(db, src, _safe_background_error_text())
            _audit_git_sync_event(
                source=src,
                operator_id=user_id,
                operator_name=operator_name,
                git_action=git_action,
                result=Result.FAILED,
                detail=f"Git 源同步失败：{_safe_background_error_text()}",
                ip_address=ip_address,
                user_agent=user_agent,
            )
    finally:
        unregister_local_git_sync(source_id)
        db.close()


def delete_git_source_for_user(
    *,
    db: Session,
    user_id: str,
    source_id: str,
    storage: S3StorageClient,
    auth: AuthContext,
) -> dict[str, object]:
    """删除 Git 源注册，并级联删除该源导入的全部 Skill（版本、审核、存储）。"""
    from fastapi import HTTPException

    from plugins_market.services.plugin import delete_plugin_version_service

    gs_repo = GitSourceRepository(db)
    src = gs_repo.get_by_id(source_id)
    if src is None or (src.created_by_user_id or "").strip() != (user_id or "").strip():
        raise PublishError(
            code=403,
            error="forbidden",
            message="无权删除该 Git 源或资源不存在",
        )
    if is_local_git_sync_running(source_id) or is_git_source_sync_blocking(src):
        raise PublishError(
            code=409,
            error="git_source_sync_in_progress",
            message="同步进行中，请稍后再删除",
        )

    asset_ids = gs_repo.list_linked_asset_ids(source_id)
    deleted_skill_count = 0
    failed_assets: list[dict[str, object]] = []
    for asset_id in asset_ids:
        try:
            delete_plugin_version_service(
                asset_id=asset_id,
                version="all",
                auth=auth,
                db=db,
                storage=storage,
            )
            deleted_skill_count += 1
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            failed_assets.append(
                {
                    "asset_id": asset_id,
                    "error": str(detail.get("error") or "git_source_cascade_delete_failed"),
                    "message": str(
                        detail.get("message")
                        or f"删除 Git 源关联 Skill 失败：{asset_id}"
                    ),
                    "status_code": int(exc.status_code or 500),
                }
            )
            logger.warning(
                "git source cascade delete skill failed: source_id=%s asset_id=%s status=%s error=%s",
                source_id,
                asset_id,
                exc.status_code,
                detail.get("error"),
            )
        except Exception:
            failed_assets.append(
                {
                    "asset_id": asset_id,
                    "error": "internal_error",
                    "message": f"删除 Git 源关联 Skill 失败：{asset_id}",
                    "status_code": 500,
                }
            )
            logger.exception(
                "git source cascade delete skill unexpected error: source_id=%s asset_id=%s",
                source_id,
                asset_id,
            )

    if failed_assets:
        failed_skill_count = len(failed_assets)
        raise PublishError(
            code=409,
            error="git_source_cascade_delete_partial",
            message=(
                f"级联删除未完成：成功 {deleted_skill_count} 个，失败 {failed_skill_count} 个；"
                "Git 源已保留，可重试"
            ),
            data={
                "deleted": False,
                "deleted_skill_count": deleted_skill_count,
                "failed_skill_count": failed_skill_count,
                "failed_assets": failed_assets,
            },
        )

    if not gs_repo.delete_by_id(source_id):
        raise PublishError(
            code=404,
            error="git_source_not_found",
            message="Git 源不存在或已删除",
        )
    db.commit()
    return {"deleted": True, "deleted_skill_count": deleted_skill_count}


def create_git_source(
    *,
    db: Session,
    user_id: str,
    name: str | None,
    repo_url: str,
    ref: str = "main",
    skills_subpath: str | None = None,
) -> GitSourceDB:
    """注册 Git 源（不执行同步）。全局唯一：规范化仓库 + 分支/tag + skills_subpath。"""
    gs_repo = GitSourceRepository(db)
    validated = _assert_public_git_http_url(repo_url)
    canonical = normalize_git_repo_global_key(validated)
    if not canonical:
        raise PublishError(code=400, error="invalid_repo_url", message="无效的仓库 URL")
    ref_clean = assert_git_ref_branch_or_tag(ref or "main")
    sub_clean = assert_git_skills_subpath(skills_subpath)
    dedup = compute_git_source_dedup_key(
        repo_url_canonical=canonical,
        ref=ref_clean,
        skills_subpath=sub_clean,
    )
    existing = gs_repo.find_global_by_dedup_key(dedup)
    if existing is not None:
        owner = (existing.created_by_user_id or "").strip()
        uid = (user_id or "").strip()
        if owner == uid and is_reclaimable_git_source(db, existing):
            now_ms = int(time.time() * 1000)
            existing.name = ((name or "").strip()[:128] or validated[:128])
            existing.repo_url = validated[:512]
            existing.repo_url_canonical = canonical[:640]
            existing.ref = ref_clean[:256]
            existing.skills_subpath = sub_clean
            existing.update_time_ms = now_ms
            db.add(existing)
            db.commit()
            db.refresh(existing)
            return existing
        raise PublishError(
            code=409,
            error="git_repo_already_registered",
            message="该 Git 接入配置已被全局使用（同一仓库、分支/tag 与技能根路径），无法重复注册。",
        )
    now_ms = int(time.time() * 1000)
    label = (name or "").strip()[:128] or validated[:128]
    src = GitSourceDB(
        id=uuid.uuid4().hex,
        name=label,
        repo_url=validated[:512],
        repo_url_canonical=canonical[:640],
        git_source_dedup_key=dedup,
        ref=ref_clean[:256],
        skills_subpath=sub_clean,
        visibility_scope="private",
        created_by_user_id=user_id,
        create_time_ms=now_ms,
        update_time_ms=now_ms,
        last_index_status=None,
        last_index_error=None,
        last_indexed_at_ms=None,
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    return src


def create_git_source_and_sync(
    *,
    db: Session,
    storage: S3StorageClient,
    user_id: str,
    name: str | None,
    repo_url: str,
    ref: str = "main",
    skills_subpath: str | None = None,
    fail_fast: bool = False,
) -> tuple[GitSourceDB, GitSyncRunResponse]:
    """创建 git_sources 并同步执行一次导入（测试/脚本用；HTTP API 请走后台同步）。"""
    src = create_git_source(
        db=db,
        user_id=user_id,
        name=name,
        repo_url=repo_url,
        ref=ref,
        skills_subpath=skills_subpath,
    )
    try:
        prepare_git_source_sync_start(db, src)
        mark_git_source_syncing(db, src)
        db.refresh(src)
        sync = run_git_source_sync(
            db=db,
            storage=storage,
            user_id=user_id,
            source=src,
            fail_fast=fail_fast,
            reraise=True,
            git_action="create",
        )
    finally:
        unregister_local_git_sync(src.id)
    if sync is None:
        raise PublishError(code=500, error="git_sync_failed", message="Git 同步未返回结果")
    return src, sync
