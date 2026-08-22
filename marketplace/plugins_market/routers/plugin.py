# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import hashlib
import json
import tempfile
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from pathlib import Path as FsPath
from typing import Annotated, Any, List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from common.security.security_utils import SecurityUtils
from plugins_market.core.audit import audit_log
from plugins_market.core.audit_events import Action, EventType, ResourceType, Result, resolve_batch_audit_result
from plugins_market.core.moderation import is_skill_like_plugin_type
from plugins_market.core.auth import (
    AuthContext,
    get_oauth_user_id_and_login,
    normalize_oauth_provider_header,
    require_auth,
    resolve_viewer_context,
)
from plugins_market.core.context import (
    set_audit_hint,
    set_user_id,
    set_user_name,
    get_user_id as get_user_id_from_context,
    get_user_name,
)
from plugins_market.core.viewer_context import ViewerContext
from plugins_market.core.cache import cache_get, cache_set
from plugins_market.core.config import settings
from plugins_market.core.database import get_db
from plugins_market.core.errors import (
    http_error_payload,
    make_business_error,
    make_publish_error,
    resolve_registered_error_metadata,
)
from plugins_market.core.operation_log import (
    bind_operation_actor,
    bind_operation_resource,
    complete_operation_result,
    get_operation_id,
    is_invalid_or_denied_error,
    operation_context,
    operation_failure_result,
    operation_log_fields,
)
from plugins_market.core.s3_storage_client import get_storage_client
from plugins_market.repositories.git_source_repository import GitSourceRepository
from plugins_market.validation.constants import MAX_FILE_SIZE, ZIP_STREAM_READ_CHUNK_BYTES
from plugins_market.imports.skill_import_service import skill_import_from_bundle
from plugins_market.schemas.common import ResponseModel
from plugins_market.schemas.plugin import (
    GitSourceCreateRequest,
    GitSourceItem,
    GitSourceListResponse,
    GitSyncAcceptedResponse,
    PluginDownloadData,
    PluginListItem,
    PluginListQuery,
    PluginListResponse,
    PluginPublishForm,
    PluginPublishResult,
    PluginTemplatePresignData,
    PluginVersionDeleteData,
    PluginVersionDetail,
    SkillImportBundle,
    SkillImportResponse,
    SkillModerationRequest,
    SkillModerationResult,
    SkillModerationAuditListResponse,
    TagOption,
    VersionFilesData,
)
from plugins_market.repositories import MarketAssetRepository
from plugins_market.services import (
    PublishError,
    delete_plugin_version_service,
    get_plugin_version_detail_service,
    get_version_file_list_service,
    list_my_skill_moderation_audits_service,
    list_plugins_service,
    get_download_info,
    moderate_skill_asset_service,
    publish as plugin_publish,
)
from plugins_market.services.git_skill_sync import (
    _start_background_git_sync_operation,
    create_git_source,
    delete_git_source_for_user,
    mark_git_source_syncing,
    prepare_git_source_sync_start,
    recover_stale_git_sources_for_user,
    unregister_local_git_sync,
)
from plugins_market.services.skill_review import schedule_skill_publish_review
from plugins_market.core.logging import background_task_exception_boundary, get_logger
from plugins_market.core.publish_result import (
    PUBLISH_RESULT_PENDING_MODERATION,
    PUBLISH_RESULT_REVIEWING,
)

plugin_router = APIRouter(prefix="/plugins", tags=["plugins"])
artifact_router = APIRouter(prefix="/artifacts", tags=["plugins"])
logger = get_logger(__name__)


@background_task_exception_boundary(
    logger=logger,
    message="skill review background scheduling failed",
    reraise=False,
    context_extractor=lambda plugin_id, version, trigger: {
        "plugin_id": plugin_id,
        "version": version,
        "trigger": trigger,
    },
)
def _schedule_skill_publish_review_background(plugin_id: str, version: str, trigger: str) -> None:
    with operation_context(operation_type="skill_publish_review"):
        logger.info(
            "schedule skill publish review background task",
            **operation_log_fields(
                stage="start",
                result="started",
                plugin_id=plugin_id,
                version=version,
                trigger=trigger,
            ),
        )
        schedule_skill_publish_review(
            plugin_id,
            version,
            trigger,
            parent_operation_id=get_operation_id(),
        )
        logger.info(
            "schedule skill publish review background task",
            **complete_operation_result(
                result="accepted",
                plugin_id=plugin_id,
                version=version,
                trigger=trigger,
            ),
        )
        return None


@background_task_exception_boundary(
    logger=logger,
    message="git sync background task boundary failed",
    reraise=False,
    context_extractor=lambda *, source_id, user_id, fail_fast=False, parent_operation_id=None: {
        "source_id": source_id,
        "user_id": user_id,
        "fail_fast": fail_fast,
        "parent_operation_id": parent_operation_id,
    },
)
def _run_git_source_sync_background_safe(
    *,
    source_id: str,
    user_id: str,
    fail_fast: bool = False,
    parent_operation_id: str | None = None,
    git_action: str = "sync",
    operator_name: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    _start_background_git_sync_operation(
        source_id=source_id,
        user_id=user_id,
        fail_fast=fail_fast,
        parent_operation_id=parent_operation_id,
        git_action=git_action,
        operator_name=operator_name,
        ip_address=ip_address,
        user_agent=user_agent,
    )


_skill_import_req_times: deque[float] = deque()
_skill_import_rl_lock = asyncio.Lock()
_git_sync_req_times_by_user: dict[str, deque[float]] = {}
_git_sync_rl_lock = asyncio.Lock()
_git_sync_rl_op_count = 0
_GIT_SYNC_RL_PRUNE_ALL_EVERY = 128


async def _enforce_skill_import_rate_limit() -> None:
    limit = settings.skill_import_rate_limit_per_minute
    if limit <= 0:
        return
    async with _skill_import_rl_lock:
        now = time.monotonic()
        window = 60.0
        while _skill_import_req_times and _skill_import_req_times[0] < now - window:
            _skill_import_req_times.popleft()
        if len(_skill_import_req_times) >= limit:
            raise _auth_error(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "skill-import 请求过于频繁，请稍后再试",
                error="rate_limited",
            ) from None
        _skill_import_req_times.append(now)


def _prune_git_sync_rate_limit_buckets(*, now: float, window: float) -> None:
    """移除窗口外时间戳；删除空 deque，避免按用户 key 无限累积。"""
    for uid in list(_git_sync_req_times_by_user):
        bucket = _git_sync_req_times_by_user[uid]
        while bucket and bucket[0] < now - window:
            bucket.popleft()
        if not bucket:
            del _git_sync_req_times_by_user[uid]


async def _enforce_git_source_sync_rate_limit(user_id: str) -> None:
    limit = settings.git_source_sync_rate_limit_per_minute
    if limit <= 0:
        return
    uid = (user_id or "").strip() or "_anonymous"
    global _git_sync_rl_op_count
    async with _git_sync_rl_lock:
        now = time.monotonic()
        window = 60.0
        bucket = _git_sync_req_times_by_user.get(uid)
        if bucket is None:
            bucket = deque()
            _git_sync_req_times_by_user[uid] = bucket
        while bucket and bucket[0] < now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            raise _auth_error(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Git 源同步请求过于频繁，请稍后再试",
                error="rate_limited",
            ) from None
        bucket.append(now)
        _git_sync_rl_op_count += 1
        if _git_sync_rl_op_count % _GIT_SYNC_RL_PRUNE_ALL_EVERY == 0:
            _prune_git_sync_rate_limit_buckets(now=now, window=window)


def _auth_error(status_code: int, message: str, *, error: str | None = None) -> HTTPException:
    resolved_error = error
    if resolved_error is None:
        resolved_error = {
            (
                401,
                "Missing/invalid authorization: provide exactly one of "
                "Authorization: Bearer <token>, or X-System-Token",
            ): "auth_header_missing",
            (
                401,
                "Invalid X-System-Token",
            ): "system_token_invalid",
            (401, "Invalid or empty token"): "auth_token_invalid",
            (400, "Invalid X-OAuth-Provider"): "invalid_oauth_provider",
        }.get((status_code, message), "permission_denied")
    return HTTPException(
        status_code=status_code,
        detail=http_error_payload(
            status_code=status_code,
            message=message,
            error=resolved_error,
        ),
    )


def _parse_form_bool(value: Optional[str]) -> bool:
    if not value:
        return False
    return str(value).strip().lower() in ("true", "1", "on")


def _log_operation_started(event: str, *, stage: str = "start", **fields: Any) -> None:
    logger.info(event, **operation_log_fields(stage=stage, result="started", **fields))


def _log_operation_completed(event: str, *, result: str, **fields: Any) -> None:
    logger.info(event, **complete_operation_result(result=result, **fields))


def _log_operation_accepted(event: str, **fields: Any) -> None:
    _log_operation_completed(event, result="accepted", **fields)


def _log_operation_failure_from_error(event: str, error: Exception, **fields: Any) -> None:
    if not isinstance(error, (PublishError, HTTPException)):
        raise TypeError("unsupported error type for operation failure logging")
    if isinstance(error, PublishError):
        payload = error.detail if isinstance(error.detail, dict) else {}
    else:
        payload = (
            error.detail
            if isinstance(error.detail, dict)
            else http_error_payload(
                status_code=error.status_code,
                message=str(error.detail or "Request failed"),
            )
        )
        payload.setdefault("error", "http_error")
        error_code, error_class = resolve_registered_error_metadata(str(payload.get("error") or ""))
        if error_code and payload.get("error_code") is None:
            payload["error_code"] = error_code
        if error_class and payload.get("error_class") is None:
            payload["error_class"] = error_class
    result = operation_failure_result(payload)
    log_method = logger.info if is_invalid_or_denied_error(payload) else logger.warning
    log_method(
        event,
        **complete_operation_result(
            result=result.result,
            error_code=result.error_code,
            error_class=result.error_class,
            error_message=result.error_message,
            result_detail=result.result_detail,
            **fields,
        ),
    )


def _raise_with_operation_failure_log(event: str, error: Exception, **fields: Any):
    _log_operation_failure_from_error(event, error, **fields)
    try:
        setattr(error, "_operation_completion_logged", True)
    except Exception:
        pass
    raise error


def _normalize_asset_visibility(value: Optional[str]) -> str:
    v = (value or "public").strip().lower()
    if v not in ("public", "private"):
        raise make_business_error(
            status_code=400,
            message="visibility 仅支持 public 或 private",
            error="invalid_visibility",
            error_class="validation",
        )
    return v


def _parse_fail_fast_query(
    fail_fast: Optional[str] = Query(
        None,
        description="遇首条失败即停止：true/1/on 为开启；未传或其它任意值视为关闭（避免无法解析为布尔时整请求 422）",
    ),
) -> bool:
    """与 multipart 的 fail_fast 表单语义一致；非法查询值视为 false，不把整段 POST 判为 422。"""
    return _parse_form_bool(fail_fast)


def valid_checksum(
    checksum: Optional[str] = Header(None, alias="X-Checksum-SHA256"),
) -> str:
    if not checksum or not str(checksum).strip():
        raise make_business_error(
            status_code=400,
            message="请求头 X-Checksum-SHA256 必填，且为 64 位小写十六进制字符串",
            error="checksum_required",
            error_class="validation",
        )
    value = checksum.strip().lower()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise make_business_error(
            status_code=400,
            message="请求头 X-Checksum-SHA256 必填，且为 64 位小写十六进制字符串",
            error="checksum_required",
            error_class="validation",
        )
    return value


async def build_skill_import_bundle(
    file: UploadFile = File(..., description="技能集合包（ZIP，顶层为多个 skill 目录）"),
    checksum: str = Depends(valid_checksum),
    force: str = Form("false"),
    fail_fast: str = Form("false"),
) -> SkillImportBundle:
    return SkillImportBundle(
        file=file,
        checksum=checksum,
        force=_parse_form_bool(force),
        fail_fast=_parse_form_bool(fail_fast),
    )


class PublishFormRequired:
    """必填表单参数"""

    def __init__(
        self,
        file: UploadFile = File(..., description="插件包文件（.zip 格式）"),
        checksum: str = Depends(valid_checksum),
    ):
        self.file = file
        self.checksum = checksum


class PublishFormOptional:
    """可选表单参数"""

    def __init__(
        self,
        plugin_id: Optional[str] = Form(
            None,
            description="已存在插件发新版本时必填；首次发布请勿填写，由系统生成 plugin_id",
        ),
        plugin_version: Optional[str] = Form(None),
        version_desc: Optional[str] = Form(None),
        force: bool = Form(False),
        visibility: Optional[str] = Form("public"),
    ):
        self.plugin_id = plugin_id.strip() if plugin_id else None
        self.plugin_version = plugin_version.strip() if plugin_version else None
        self.version_desc = version_desc.strip() if version_desc else None
        self.force = force
        self.visibility = _normalize_asset_visibility(visibility)


def build_publish_form(
    required: PublishFormRequired = Depends(),
    optional: PublishFormOptional = Depends(),
) -> PluginPublishForm:
    return PluginPublishForm(
        file=required.file,
        checksum=required.checksum,
        plugin_id=optional.plugin_id,
        plugin_version=optional.plugin_version,
        version_desc=optional.version_desc,
        force=optional.force,
        visibility=optional.visibility,
    )


@dataclass(frozen=True)
class _ServiceDeps:
    db: Session
    storage: Any
    viewer: ViewerContext


def _get_service_deps(
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
    viewer: ViewerContext = Depends(resolve_viewer_context),
) -> "_ServiceDeps":
    return _ServiceDeps(db=db, storage=storage, viewer=viewer)


@dataclass(frozen=True)
class PublishPluginDependencies:
    db: Session
    storage: Any


def get_publish_plugin_dependencies(
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
) -> PublishPluginDependencies:
    return PublishPluginDependencies(db=db, storage=storage)


@dataclass(frozen=True)
class GitSourceSyncRouteDeps:
    request: Request
    background_tasks: BackgroundTasks
    auth: AuthContext
    db: Session
    fail_fast: bool


def get_git_source_sync_route_deps(
    request: Request,
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    fail_fast: bool = Depends(_parse_fail_fast_query),
) -> GitSourceSyncRouteDeps:
    return GitSourceSyncRouteDeps(
        request=request,
        background_tasks=background_tasks,
        auth=auth,
        db=db,
        fail_fast=fail_fast,
    )


def get_publish_auth(
    authorization: Optional[str] = Header(None, description="Authorization: Bearer <token>"),
    x_system_token: Optional[str] = Header(None, alias="X-System-Token"),
    x_oauth_provider: Optional[str] = Header(None, alias="X-OAuth-Provider"),
) -> Tuple[Optional[str], bool, Optional[str], str]:
    """
    返回 (token, is_system_token, acting_user_id, oauth_provider)
    - is_system_token=True：表示通过 X-System-Token（oauth_provider 占位为 gitcode，不使用）
    - is_system_token=False：token 需结合 oauth_provider 调用厂商用户接口鉴权
    """
    has_auth = bool(authorization and authorization.strip().lower().startswith("bearer "))
    has_bearer_token = has_auth
    has_system = bool(x_system_token and x_system_token.strip())

    auth_count = int(has_system) + int(has_bearer_token)
    if auth_count != 1:
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "Missing/invalid authorization: provide exactly one of Authorization: Bearer <token>, or X-System-Token",
        )

    if has_system:
        system_admin_token = SecurityUtils.get_decrypt_secret("SYSTEM_ADMIN_TOKEN", default="") or ""
        if system_admin_token and x_system_token.strip() == system_admin_token:
            acting = settings.system_admin_user
            return (None, True, acting, "gitcode")
        raise _auth_error(status.HTTP_401_UNAUTHORIZED, "Invalid X-System-Token")

    token = authorization[7:].strip()
    if not token:
        raise _auth_error(status.HTTP_401_UNAUTHORIZED, "Invalid or empty token")
    try:
        oauth_provider = normalize_oauth_provider_header(x_oauth_provider)
    except HTTPException as e:
        raise _auth_error(
            status.HTTP_400_BAD_REQUEST,
            str(e.detail) if isinstance(e.detail, str) else "Invalid X-OAuth-Provider",
            error="invalid_oauth_provider",
        ) from e
    return (token, False, None, oauth_provider)


@plugin_router.post("", response_model=ResponseModel[PluginPublishResult])
async def publish_plugin(
    request: Request,
    background_tasks: BackgroundTasks,
    form: PluginPublishForm = Depends(build_publish_form),
    dependencies: PublishPluginDependencies = Depends(get_publish_plugin_dependencies),
    auth: Tuple[Optional[str], bool, Optional[str], str] = Depends(get_publish_auth),
):
    db = dependencies.db
    storage = dependencies.storage
    token, is_system_token, acting_user_id, oauth_provider = auth
    publisher_name_override: str | None = None
    if not is_system_token:
        acting_user_id, publisher_name_override = await get_oauth_user_id_and_login(
            token or "",
            oauth_provider,
        )
    else:
        publisher_name_override = settings.system_admin_user
    set_user_id(acting_user_id or "")
    set_user_name(publisher_name_override)
    set_audit_hint(filename=form.file.filename, resource_type="skill")
    if form.plugin_id:
        set_audit_hint(resource_id=form.plugin_id)
    if form.plugin_version:
        set_audit_hint(resource_version=form.plugin_version)

    with operation_context(operation_type="plugin_publish"):
        bind_operation_actor(
            actor_id=acting_user_id or "",
            actor_name=publisher_name_override,
            actor_type="user",
        )
        _log_operation_started("plugin publish", filename=form.file.filename)
        try:
            content = await form.file.read()
            result = plugin_publish(
                user_id=acting_user_id or "",
                content=content,
                filename=form.file.filename,
                expected_checksum=form.checksum,
                plugin_id=form.plugin_id,
                plugin_version=form.plugin_version,
                version_desc=form.version_desc,
                force=form.force,
                visibility=form.visibility,
                db=db,
                storage=storage,
                publisher_name_override=publisher_name_override,
            )
        except (PublishError, HTTPException) as exc:
            _raise_with_operation_failure_log("plugin publish", exc, filename=form.file.filename)
        bind_operation_resource(
            resource_type=("skill" if is_skill_like_plugin_type(result.plugin_type) else "plugin"),
            resource_id=result.plugin_id,
            resource_version=result.version,
        )
        _log_operation_completed(
            "plugin publish",
            result="success",
            plugin_id=result.plugin_id,
            version=result.version,
            publish_result=result.publish_result,
        )

        if result.publish_result == PUBLISH_RESULT_REVIEWING:
            background_tasks.add_task(
                _schedule_skill_publish_review_background,
                result.plugin_id,
                result.version,
                "api_background",
            )

        is_skill_like = is_skill_like_plugin_type(result.plugin_type)
        event_type = "SKILL_MANAGE" if is_skill_like else "PLUGIN_MANAGE"
        resource_type = "skill" if is_skill_like else "plugin"
        audit_log(
            event_type=event_type,
            action="PUBLISH",
            operator_id=acting_user_id or "",
            operator_name=publisher_name_override,
            resource_type=resource_type,
            resource_id=result.plugin_id if hasattr(result, "plugin_id") else str(result),
            resource_version=result.version if hasattr(result, "version") else None,
            detail=f"发布{resource_type}成功: {getattr(result, 'plugin_id', '')} v{getattr(result, 'version', '')}",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            extra={
                "force": form.force,
                "visibility": form.visibility,
                "skill_name": getattr(result, "name", None) or None,
                "skill_display_name": getattr(result, "display_name", None) or None,
            },
        )

        return ResponseModel(
            code=status.HTTP_200_OK,
            message=(
                "Skill 已提交，正在自动审查"
                if result.publish_result == PUBLISH_RESULT_REVIEWING
                else (
                    "Skill 已提交，等待人工审核"
                    if result.publish_result == PUBLISH_RESULT_PENDING_MODERATION
                    else "Publish plugin successfully"
                )
            ),
            data=result,
        )


def _template_filename_from_key(key: str) -> str:
    base = (key or "").strip().rstrip("/").split("/")[-1]
    return base or "plugin-template.zip"


@plugin_router.get(
    "/publish-template",
    response_model=ResponseModel[PluginTemplatePresignData],
)
async def get_publish_template_presigned(
    auth: AuthContext = Depends(require_auth),
    storage=Depends(get_storage_client),
    kind: Optional[str] = Query(
        None,
        description='模板种类：不传或 "plugin" 为插件模板；传 "skill" 为 Skill 模板',
    ),
):
    """为发布页「下载模板」生成私有桶对象的预签名 GET URL（需 Bearer 或 X-System-Token）。"""
    _ = auth
    use_skill = is_skill_like_plugin_type(kind)
    if use_skill:
        key = (settings.skill_template_object_key or "").strip()
        unset_msg = "未配置 Skill 发布模板对象路径（MARKET_SKILL_TEMPLATE_OBJECT_KEY）"
    else:
        key = (settings.plugin_template_object_key or "").strip()
        unset_msg = "未配置发布模板对象路径（MARKET_PLUGIN_TEMPLATE_OBJECT_KEY）"
    if not key:
        raise make_business_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=unset_msg,
            error="template_not_configured",
            error_class="upstream",
        )
    try:
        url = storage.presigned_get_url(key)
        ttl = storage.config.presigned_expires_seconds
    except Exception as e:
        logger.exception("failed to generate publish template presigned url for key=%s", key)
        raise make_business_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="生成模板下载链接失败",
            error="presign_failed",
            error_class="internal",
        ) from e

    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=PluginTemplatePresignData(
            download_url=url,
            expires_in=int(ttl),
            filename=_template_filename_from_key(key),
        ),
    )


@plugin_router.post(
    "/skill-import",
    response_model=ResponseModel[SkillImportResponse],
)
async def skill_import(
    request: Request,
    bundle: SkillImportBundle = Depends(build_skill_import_bundle),
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
    auth: Tuple[Optional[str], bool, Optional[str], str] = Depends(get_publish_auth),
):
    """批量导入 skill：仅 X-System-Token；须 X-Checksum-SHA256。"""
    await _enforce_skill_import_rate_limit()

    _token, is_system_token, acting_user_id, _oauth_provider = auth
    if not is_system_token:
        raise _auth_error(
            status.HTTP_403_FORBIDDEN,
            "批量导入仅支持 X-System-Token（系统管理员）",
            error="forbidden",
        )
    set_user_id(acting_user_id or "")

    with operation_context(operation_type="skill_import"):
        bind_operation_actor(
            actor_id=acting_user_id or "",
            actor_name=settings.system_admin_user,
            actor_type="system",
        )
        _log_operation_started(
            "skill import",
            force=bundle.force,
            fail_fast=bundle.fail_fast,
            filename=bundle.file.filename,
        )

        tmp_path: FsPath | None = None
        upload_tmp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="oj_skill_bundle_",
                suffix=".zip",
                delete=False,
                mode="wb",
            ) as out:
                upload_tmp_name = out.name
                tmp_path = FsPath(out.name)
                bind_operation_resource(resource_type="skill_bundle", resource_id=tmp_path.name)
                hasher = hashlib.sha256()
                written = 0
                while True:
                    chunk = await bundle.file.read(ZIP_STREAM_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_FILE_SIZE:
                        raise make_publish_error(
                            status_code=400,
                            message="技能集合包原始大小超过 512MB 上限",
                            error="payload_too_large",
                            error_class="validation",
                        ) from None
                    hasher.update(chunk)
                    out.write(chunk)

            if hasher.hexdigest() != bundle.checksum:
                raise make_publish_error(
                    status_code=400,
                    message="技能集合包 X-Checksum-SHA256 与实际上传内容不一致",
                    error="checksum_mismatch",
                    error_class="validation",
                ) from None

            data = skill_import_from_bundle(
                bundle_path=tmp_path,
                user_id=acting_user_id or "",
                db=db,
                storage=storage,
                force=bundle.force,
                fail_fast=bundle.fail_fast,
            )
            result = "success"
            if data.summary.failed > 0 and data.summary.ok > 0:
                result = "partial_failure"
            elif data.summary.failed > 0 and data.summary.ok == 0:
                result = "failure"
            elif data.summary.skipped > 0 and data.summary.ok == 0 and data.summary.failed == 0:
                result = "skipped"
            _log_operation_completed(
                "skill import",
                result=result,
                total=data.summary.total,
                ok_count=data.summary.ok,
                failed_count=data.summary.failed,
                skipped_count=data.summary.skipped,
                force=bundle.force,
                fail_fast=bundle.fail_fast,
            )

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
            _audit_result = resolve_batch_audit_result(
                ok_count=data.summary.ok,
                failed_count=data.summary.failed,
                skipped_count=data.summary.skipped,
            )
            audit_log(
                event_type="SKILL_MANAGE",
                action="IMPORT",
                result=_audit_result,
                operator_id=acting_user_id or "",
                operator_name=settings.system_admin_user,
                resource_type="skill_bundle",
                detail=(
                    f"批量导入 Skill 完成，成功 {data.summary.ok} 个，"
                    f"失败 {data.summary.failed} 个，跳过 {data.summary.skipped} 个，"
                    f"共 {data.summary.total} 个"
                ),
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                extra={
                    "force": bundle.force,
                    "fail_fast": bundle.fail_fast,
                    "total": data.summary.total,
                    "ok_count": data.summary.ok,
                    "failed_count": data.summary.failed,
                    "skipped_count": data.summary.skipped,
                    "failed_items": failed_entries[:50],
                    "skipped_items": skipped_entries[:50],
                    "skipped_items_truncated": len(skipped_entries) > 50,
                },
            )

            return ResponseModel(
                code=status.HTTP_200_OK,
                message="Import skills finished",
                data=data,
            )
        except (PublishError, HTTPException) as exc:
            _raise_with_operation_failure_log(
                "skill import",
                exc,
                force=bundle.force,
                fail_fast=bundle.fail_fast,
                filename=bundle.file.filename,
            )
        finally:
            if upload_tmp_name:
                try:
                    FsPath(upload_tmp_name).unlink(missing_ok=True)
                except OSError:
                    pass


@plugin_router.get(
    "/git-sources",
    response_model=ResponseModel[GitSourceListResponse],
)
async def list_my_git_sources(
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """当前用户注册的 Git 仓库源列表。"""
    _ = set_user_id(auth.acting_user_id)
    recover_stale_git_sources_for_user(db, auth.acting_user_id)
    rows = GitSourceRepository(db).list_by_user(auth.acting_user_id)
    items = [GitSourceItem.model_validate(r) for r in rows]
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=GitSourceListResponse(items=items))


@plugin_router.post(
    "/git-sources",
    response_model=ResponseModel[GitSyncAcceptedResponse],
)
async def create_git_source_and_sync_route(
    body: GitSourceCreateRequest,
    deps: GitSourceSyncRouteDeps = Depends(get_git_source_sync_route_deps),
):
    auth = deps.auth
    await _enforce_git_source_sync_rate_limit(auth.acting_user_id)
    set_user_id(auth.acting_user_id)
    with operation_context(operation_type="git_source_sync"):
        bind_operation_actor(
            actor_id=auth.acting_user_id,
            actor_name=auth.acting_user_name,
            actor_type="user",
        )
        _log_operation_started("git source sync", source_name=body.name)
        src = create_git_source(
            db=deps.db,
            user_id=auth.acting_user_id,
            name=body.name,
            repo_url=body.repo_url,
            ref=body.ref,
            skills_subpath=body.skills_subpath,
        )
        bind_operation_resource(resource_type="git_source", resource_id=src.id)

        try:
            prepare_git_source_sync_start(deps.db, src)
            mark_git_source_syncing(deps.db, src)
            deps.background_tasks.add_task(
                _run_git_source_sync_background_safe,
                source_id=src.id,
                user_id=auth.acting_user_id,
                fail_fast=deps.fail_fast,
                parent_operation_id=get_operation_id(),
                git_action="create",
                operator_name=auth.acting_user_name,
                ip_address=deps.request.client.host if deps.request.client else None,
                user_agent=deps.request.headers.get("user-agent"),
            )
        except Exception:
            unregister_local_git_sync(src.id)
            raise

        _log_operation_accepted("git source sync", source_id=src.id)

        return ResponseModel(
            code=status.HTTP_200_OK,
            message="ok",
            data=GitSyncAcceptedResponse(source_id=src.id),
        )


@plugin_router.post(
    "/git-sources/{source_id}/sync",
    response_model=ResponseModel[GitSyncAcceptedResponse],
)
async def sync_git_source_route(
    source_id: str,
    deps: GitSourceSyncRouteDeps = Depends(get_git_source_sync_route_deps),
):
    auth = deps.auth
    await _enforce_git_source_sync_rate_limit(auth.acting_user_id)
    set_user_id(auth.acting_user_id)
    with operation_context(operation_type="git_source_sync"):
        bind_operation_actor(
            actor_id=auth.acting_user_id,
            actor_name=auth.acting_user_name,
            actor_type="user",
        )
        bind_operation_resource(resource_type="git_source", resource_id=source_id)
        _log_operation_started("git source sync", source_id=source_id)
        gs_repo = GitSourceRepository(deps.db)
        src = gs_repo.get_by_id(source_id)
        if src is None or src.created_by_user_id != auth.acting_user_id:
            raise _auth_error(
                status.HTTP_403_FORBIDDEN,
                "无权同步该 Git 源或资源不存在",
                error="forbidden",
            )
        try:
            prepare_git_source_sync_start(deps.db, src)
            mark_git_source_syncing(deps.db, src)
            deps.background_tasks.add_task(
                _run_git_source_sync_background_safe,
                source_id=src.id,
                user_id=auth.acting_user_id,
                fail_fast=deps.fail_fast,
                parent_operation_id=get_operation_id(),
                git_action="sync",
                operator_name=auth.acting_user_name,
                ip_address=deps.request.client.host if deps.request.client else None,
                user_agent=deps.request.headers.get("user-agent"),
            )
        except Exception:
            unregister_local_git_sync(src.id)
            raise

        _log_operation_accepted("git source sync", source_id=src.id)

        return ResponseModel(
            code=status.HTTP_200_OK,
            message="ok",
            data=GitSyncAcceptedResponse(source_id=src.id),
        )


@plugin_router.delete(
    "/git-sources/{source_id}",
    response_model=ResponseModel[dict],
)
async def delete_git_source_route(
    request: Request,
    source_id: str,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
):
    set_user_id(auth.acting_user_id)
    src = GitSourceRepository(db).get_by_id(source_id)
    source_snapshot = None
    if src is not None and (src.created_by_user_id or "").strip() == auth.acting_user_id:
        source_snapshot = {
            "name": (getattr(src, "name", None) or "").strip() or None,
            "repo_url": (getattr(src, "repo_url", None) or "").strip() or None,
            "ref": (getattr(src, "ref", None) or "").strip() or None,
            "skills_subpath": (getattr(src, "skills_subpath", None) or "").strip() or None,
        }
    with operation_context(operation_type="delete_git_source"):
        bind_operation_actor(
            actor_id=auth.acting_user_id,
            actor_name=auth.acting_user_name,
            actor_type="user",
        )
        bind_operation_resource(resource_type="git_source", resource_id=source_id)
        try:
            data = delete_git_source_for_user(
                db=db,
                user_id=auth.acting_user_id,
                source_id=source_id,
                storage=storage,
                auth=auth,
            )
        except (PublishError, HTTPException) as exc:
            _raise_with_operation_failure_log("delete git source", exc, source_id=source_id)
        _log_operation_completed(
            "delete git source",
            result="success",
            source_id=source_id,
            deleted_skill_count=data.get("deleted_skill_count"),
        )

    deleted_skill_count = int(data.get("deleted_skill_count") or 0)
    audit_log(
        event_type="SKILL_MANAGE",
        action="GIT_SOURCE_DELETE",
        operator_id=auth.acting_user_id,
        operator_name=auth.acting_user_name,
        resource_type="git_source",
        resource_id=source_id,
        detail=f"删除 Git 源注册（级联删除关联 Skill {deleted_skill_count} 个）",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        extra={**(source_snapshot or {}), "deleted_skill_count": deleted_skill_count},
    )
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@plugin_router.get(
    "",
    response_model=ResponseModel[PluginListResponse],
)
async def list_plugins(
    query: Annotated[PluginListQuery, Query()],
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
    viewer: ViewerContext = Depends(resolve_viewer_context),
):
    data = list_plugins_service(query=query, db=db, storage=storage, viewer=viewer)
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


# 标签热度变化慢；按 (type, keyword, limit) 缓存 30s，挡住搜索框每键/跨用户的重复子串扫描。
# 无 Redis 时 cache_get 返回 None、cache_set 静默跳过，端点照常回源计算。
_TAG_OPTIONS_CACHE_TTL = 30


@plugin_router.get(
    "/tags",
    response_model=ResponseModel[List[TagOption]],
)
async def list_plugin_tags(
    db: Session = Depends(get_db),
    plugin_type: Optional[str] = Query(None, description="限定插件类型（如 skill / swarmskill）"),
    limit: int = Query(20, ge=1, le=100, description="返回的标签数量上限"),
    keyword: Optional[str] = Query(None, description="标签子串搜索；提供时按子串匹配返回热门度前 N，跳过运营置顶"),
):
    """市场搜索框下方的标签筛选选项：按使用次数自动推荐热门标签。

    运营可通过 MARKET_FEATURED_TAGS 配置优先展示的标签（逗号分隔），
    配置中的标签按配置顺序排前，其余按使用次数降序补齐。
    数据库中无可见资产使用的标签（count=0）不展示，避免点击后空结果。

    keyword 非空时进入子串搜索模式：在全量标签上 ilike 匹配，按使用次数降序
    返回前 limit 个，跳过运营置顶--用于覆盖长尾标签（低 count 但名字匹配）。
    """
    repo = MarketAssetRepository(db)
    pt = (plugin_type or "").strip() or None
    # keyword 在直接调用（单测未走 FastAPI 解析）时默认是 Query 哨兵（truthy），须按 str 判定。
    kw = (keyword if isinstance(keyword, str) else "").strip()

    # 命中缓存直接返回；手动构造 dict 序列化，绕开 pydantic v1/v2 的 dump API 差异。
    cache_key = f"plugins:tags:v1:{pt or '-'}:{kw or '-'}:{limit}"
    hit = cache_get(cache_key)
    if hit is not None:
        try:
            return ResponseModel(
                code=status.HTTP_200_OK,
                message="ok",
                data=[TagOption(tag=x["tag"], count=x["count"]) for x in json.loads(hit)],
            )
        except Exception:
            pass  # 缓存值损坏（反序列化失败）-> 回退重新计算，不阻断请求

    if kw:
        rows = repo.list_tag_options(plugin_type=pt, keyword=kw, limit=limit)
        data = [TagOption(tag=t, count=c) for t, c in rows]
    else:
        count_map = dict(repo.list_tag_options(plugin_type=pt, limit=1000))
        featured: List[str] = []
        raw = getattr(settings, "market_featured_tags", "") or ""
        for t in raw.split(","):
            # 与发布校验侧同口径归一化：count_map 键来自 DB（已 NFKC + casefold），
            # 配置值不归一化会因大小写/全半角不一致而字典查找失败，置顶标签被静默跳过。
            t = unicodedata.normalize("NFKC", t.strip()).casefold()
            if t and t not in featured:
                featured.append(t)
        ordered: List[TagOption] = [
            TagOption(tag=tag_name, count=count_map[tag_name])
            for tag_name in featured
            if tag_name in count_map
        ]
        ordered_names = {opt.tag for opt in ordered}
        for tag, cnt in sorted(count_map.items(), key=lambda kv: (-kv[1], kv[0])):
            if len(ordered) >= limit:
                break
            if tag not in ordered_names:
                ordered.append(TagOption(tag=tag, count=cnt))
                ordered_names.add(tag)
        data = ordered[:limit]

    cache_set(cache_key, json.dumps([{"tag": opt.tag, "count": opt.count} for opt in data]), _TAG_OPTIONS_CACHE_TTL)
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@plugin_router.get(
    "/audit/skill-moderation",
    response_model=ResponseModel[SkillModerationAuditListResponse],
)
async def list_my_skill_moderation_audits(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
):
    """审核管理员：本人作为操作者产生的 Skill 审核审计记录，按时间倒序。"""
    data = list_my_skill_moderation_audits_service(
        auth=auth,
        db=db,
        page=page,
        page_size=page_size,
    )
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@artifact_router.get(
    "/{id}",
    response_model=ResponseModel[PluginDownloadData],
)
async def get_artifact_download(
    request: Request,
    artifact_id: str = Path(..., alias="id"),
    version: Optional[str] = Query(None, description="版本号（如 1.0.0），不指定则返回最新版本"),
    is_cli_download: bool = Query(False, description="是否 CLI 下载；CLI=true 下载原始 zip，其他下载 raw.zip"),
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
    viewer: ViewerContext = Depends(resolve_viewer_context),
):
    fetch_user_id: Optional[str] = get_user_id_from_context()

    with operation_context(operation_type="download_artifact"):
        bind_operation_actor(actor_id=fetch_user_id, actor_type="viewer")
        bind_operation_resource(resource_type="artifact", resource_id=artifact_id, resource_version=version)
        try:
            result = get_download_info(
                asset_id=artifact_id,
                version=version,
                db=db,
                storage=storage,
                fetch_user_id=fetch_user_id,
                viewer=viewer,
                is_cli_download=is_cli_download,
            )
        except (PublishError, HTTPException) as exc:
            _raise_with_operation_failure_log(
                "artifact download",
                exc,
                asset_id=artifact_id,
                version=version,
                is_cli_download=is_cli_download,
            )
        bind_operation_resource(resource_type="artifact", resource_id=artifact_id, resource_version=result.version)
        _log_operation_completed(
            "artifact download",
            result="success",
            asset_id=artifact_id,
            version=result.version,
            is_cli_download=is_cli_download,
        )

    # 审计：下载是系统对外提供数据出口，需长期保留下载记录。
    # resource_type 依资产实际 plugin_type 记录（skill / swarmskill / plugin），不要一律写 skill。
    # 团队技能下载会被统计成 skill，会污染 resource_type 维度下的“使用量”展示。
    # 失败下载（404/403 等）当前由 GET 路径外，不在 audit_failed 覆盖范围内，暂不补录失败；
    # 若未来需要追踪未授权访问尝试，可在上方 except 分支前补一条 FAILED 审计。
    download_resource_type = {
        ResourceType.SKILL: ResourceType.SKILL,
        ResourceType.SWARMSKILL: ResourceType.SWARMSKILL,
        ResourceType.PLUGIN: ResourceType.PLUGIN,
    }.get((result.plugin_type or "").strip().lower(), ResourceType.SKILL)
    try:
        audit_log(
            event_type=EventType.SKILL_USE,
            action=Action.DOWNLOAD,
            operator_id=fetch_user_id or "anonymous",
            operator_name=get_user_name(),
            resource_type=download_resource_type,
            resource_id=result.asset_id,
            resource_version=result.version,
            result=Result.SUCCESS,
            detail=f"下载 {result.name} v{result.version}",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            extra={
                "skill_name": result.name,
                "skill_display_name": result.display_name,
                "plugin_type": result.plugin_type,
                "file_size": int(result.file_size),
                "checksum_sha256": result.checksum_sha256,
                "is_cli_download": bool(is_cli_download),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "audit_log DOWNLOAD suppressed exception",
            error_message=str(exc),
            exc_info=True,
        )

    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=result,
    )


@plugin_router.get(
    "/{asset_id}/versions/{version}/files",
    response_model=ResponseModel[VersionFilesData],
)
async def list_version_files(
    asset_id: str,
    version: str,
    with_content: Optional[str] = Query(None, description="同时返回该文件的文本内容"),
    deps: _ServiceDeps = Depends(_get_service_deps),
):
    """返回版本 zip 包内文件列表；传 with_content=<path> 可在同一请求内附带指定文件内容。"""
    data = get_version_file_list_service(
        asset_id=asset_id,
        version=version,
        db=deps.db,
        storage=deps.storage,
        viewer=deps.viewer,
        with_content=with_content,
    )
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@plugin_router.get(
    "/{asset_id}/versions/{version}",
    response_model=ResponseModel[PluginVersionDetail],
)
async def get_plugin_version_detail(
    asset_id: str,
    version: str,
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
    viewer: ViewerContext = Depends(resolve_viewer_context),
):
    data = get_plugin_version_detail_service(
        asset_id=asset_id,
        version=version,
        db=db,
        storage=storage,
        viewer=viewer,
    )
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@plugin_router.post(
    "/{asset_id}/moderation",
    response_model=ResponseModel[SkillModerationResult],
)
async def moderate_skill(
    asset_id: str,
    body: SkillModerationRequest,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
):
    with operation_context(operation_type="skill_moderation"):
        bind_operation_actor(
            actor_id=auth.acting_user_id,
            actor_name=auth.acting_user_name,
            actor_type="user",
        )
        bind_operation_resource(resource_type="skill", resource_id=asset_id, resource_version=body.version)
        _log_operation_started("skill moderation", asset_id=asset_id, action=body.action)
        try:
            data = moderate_skill_asset_service(
                asset_id=asset_id,
                action=body.action,
                reason=body.reason,
                version=body.version,
                auth=auth,
                db=db,
                storage=storage,
            )
        except (PublishError, HTTPException) as exc:
            _raise_with_operation_failure_log(
                "skill moderation",
                exc,
                asset_id=asset_id,
                action=body.action,
                version=body.version,
            )
        bind_operation_resource(resource_type="skill", resource_id=asset_id, resource_version=data.version)
        _log_operation_completed(
            "skill moderation",
            result="success",
            asset_id=asset_id,
            action=body.action,
            version=data.version,
            publish_result=data.publish_result,
        )
        return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@plugin_router.delete(
    "/{asset_id}/versions/{version}",
    response_model=ResponseModel[PluginVersionDeleteData],
)
async def delete_plugin_version(
    asset_id: str,
    version: str,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    with operation_context(operation_type="delete_asset_version"):
        bind_operation_actor(
            actor_id=auth.acting_user_id,
            actor_name=auth.acting_user_name,
            actor_type="user",
        )
        bind_operation_resource(resource_type="asset", resource_id=asset_id, resource_version=version)
        try:
            data = delete_plugin_version_service(
                asset_id=asset_id,
                version=version,
                auth=auth,
                db=db,
                storage=storage,
            )
        except (PublishError, HTTPException) as exc:
            _raise_with_operation_failure_log("delete asset version", exc, asset_id=asset_id, version=version)

        is_skill_like = is_skill_like_plugin_type(data.plugin_type)
        resource_type = "skill" if is_skill_like else "plugin"
        bind_operation_resource(resource_type=resource_type, resource_id=asset_id, resource_version=version)
        _log_operation_completed(
            "delete asset version",
            result="success",
            asset_id=asset_id,
            version=version,
            deleted_all=version.lower() == "all",
        )

        event_type = "SKILL_MANAGE" if is_skill_like else "PLUGIN_MANAGE"
        audit_log(
            event_type=event_type,
            action="DELETE",
            operator_id=auth.acting_user_id,
            operator_name=auth.acting_user_name,
            resource_type=resource_type,
            resource_id=asset_id,
            resource_version=version,
            detail=f"删除{resource_type} {asset_id} 版本 {version}",
            ip_address=auth.ip_address,
            user_agent=auth.user_agent,
            extra={
                "deleted_all": version.lower() == "all",
                "skill_name": data.skill_name or None,
                "skill_display_name": data.skill_display_name or None,
            },
        )

        return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


router = APIRouter()
router.include_router(plugin_router)
router.include_router(artifact_router)
