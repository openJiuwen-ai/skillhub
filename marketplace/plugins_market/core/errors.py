# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from dataclasses import dataclass
from typing import Any, Optional, TypedDict


@dataclass(frozen=True, slots=True)
class ErrorMetadata:
    error_code: str
    error_class: str


_REGISTERED_ERROR_METADATA: dict[str, ErrorMetadata] = {
    "auth_header_missing": ErrorMetadata("SKILLHUB_AUTH_HEADER_MISSING", "auth"),
    "auth_headers_conflict": ErrorMetadata("SKILLHUB_AUTH_HEADERS_CONFLICT", "auth"),
    "auth_token_invalid": ErrorMetadata("SKILLHUB_AUTH_TOKEN_INVALID", "auth"),
    "system_token_invalid": ErrorMetadata("SKILLHUB_SYSTEM_TOKEN_INVALID", "auth"),
    "oauth_disabled": ErrorMetadata("SKILLHUB_OAUTH_DISABLED", "auth"),
    "oauth_not_configured": ErrorMetadata("SKILLHUB_OAUTH_NOT_CONFIGURED", "upstream"),
    "oauth_token_exchange_failed": ErrorMetadata("SKILLHUB_OAUTH_TOKEN_EXCHANGE_FAILED", "upstream"),
    "oauth_authorization_rejected": ErrorMetadata("SKILLHUB_OAUTH_AUTHORIZATION_REJECTED", "auth"),
    "oauth_missing_code_or_state": ErrorMetadata("SKILLHUB_OAUTH_MISSING_CODE_OR_STATE", "validation"),
    "oauth_state_invalid": ErrorMetadata("SKILLHUB_OAUTH_STATE_INVALID", "auth"),
    "oauth_access_token_missing": ErrorMetadata("SKILLHUB_OAUTH_ACCESS_TOKEN_MISSING", "upstream"),
    "oauth_profile_fetch_failed": ErrorMetadata("SKILLHUB_OAUTH_PROFILE_FETCH_FAILED", "upstream"),
    "oauth_user_id_missing": ErrorMetadata("SKILLHUB_OAUTH_USER_ID_MISSING", "upstream"),
    "oauth_request_failed": ErrorMetadata("SKILLHUB_OAUTH_REQUEST_FAILED", "upstream"),
    "oauth_session_expired": ErrorMetadata("SKILLHUB_OAUTH_SESSION_EXPIRED", "auth"),
    "oauth_session_invalid": ErrorMetadata("SKILLHUB_OAUTH_SESSION_INVALID", "validation"),
    "oauth_provider_mismatch": ErrorMetadata("SKILLHUB_OAUTH_PROVIDER_MISMATCH", "validation"),
    "invalid_oauth_provider": ErrorMetadata("SKILLHUB_OAUTH_PROVIDER_INVALID", "validation"),
    "oauth_redirect_error": ErrorMetadata("SKILLHUB_OAUTH_REDIRECT_ERROR", "auth"),
    "checksum_required": ErrorMetadata("SKILLHUB_VALIDATION_CHECKSUM_REQUIRED", "validation"),
    "checksum_mismatch": ErrorMetadata("SKILLHUB_IMPORT_CHECKSUM_MISMATCH", "validation"),
    "payload_too_large": ErrorMetadata("SKILLHUB_VALIDATION_PAYLOAD_TOO_LARGE", "validation"),
    "template_not_configured": ErrorMetadata("SKILLHUB_UPSTREAM_TEMPLATE_NOT_CONFIGURED", "upstream"),
    "presign_failed": ErrorMetadata("SKILLHUB_UPSTREAM_PRESIGN_FAILED", "internal"),
    "manifest_too_large": ErrorMetadata("SKILLHUB_IMPORT_MANIFEST_TOO_LARGE", "validation"),
    "manifest_invalid": ErrorMetadata("SKILLHUB_IMPORT_MANIFEST_INVALID", "validation"),
    "invalid_skill_bundle": ErrorMetadata("SKILLHUB_IMPORT_INVALID_BUNDLE", "validation"),
    "too_many_skill_entries": ErrorMetadata("SKILLHUB_IMPORT_TOO_MANY_ENTRIES", "validation"),
    "invalid_plugin_structure": ErrorMetadata("SKILLHUB_PLUGIN_STRUCTURE_INVALID", "validation"),
    "invalid_skill_md": ErrorMetadata("SKILLHUB_SKILL_MD_INVALID", "validation"),
    "zip_too_large": ErrorMetadata("SKILLHUB_PLUGIN_ZIP_TOO_LARGE", "validation"),
    "import_normalize_failed": ErrorMetadata("SKILLHUB_IMPORT_NORMALIZE_FAILED", "internal"),
    "publish_db_error": ErrorMetadata("SKILLHUB_IMPORT_PUBLISH_DB_ERROR", "internal"),
    "invalid_version": ErrorMetadata("SKILLHUB_PLUGIN_VERSION_INVALID", "validation"),
    "invalid_file_format": ErrorMetadata("SKILLHUB_PLUGIN_FILE_FORMAT_INVALID", "validation"),
    "file_too_large": ErrorMetadata("SKILLHUB_PLUGIN_FILE_TOO_LARGE", "validation"),
    "invalid_plugin_config": ErrorMetadata("SKILLHUB_PLUGIN_CONFIG_INVALID", "validation"),
    "dangerous_content": ErrorMetadata("SKILLHUB_DANGEROUS_CONTENT", "validation"),
    "plugin_not_found": ErrorMetadata("SKILLHUB_PLUGIN_NOT_FOUND", "not_found"),
    "permission_denied": ErrorMetadata("SKILLHUB_PERMISSION_DENIED", "permission"),
    "plugin_id_mismatch": ErrorMetadata("SKILLHUB_PLUGIN_ID_MISMATCH", "validation"),
    "manifest_validation_failed": ErrorMetadata("SKILLHUB_PLUGIN_MANIFEST_VALIDATION_FAILED", "validation"),
    "version_conflict": ErrorMetadata("SKILLHUB_PLUGIN_VERSION_CONFLICT", "conflict"),
    "version_exists": ErrorMetadata("SKILLHUB_PLUGIN_VERSION_EXISTS", "conflict"),
    "plugin_name_exists": ErrorMetadata("SKILLHUB_PLUGIN_NAME_EXISTS", "conflict"),
    "storage_error": ErrorMetadata("SKILLHUB_STORAGE_ERROR", "upstream"),
    "skill_limit_exceeded": ErrorMetadata("SKILLHUB_SKILL_LIMIT_EXCEEDED", "conflict"),
    "group_limit_exceeded": ErrorMetadata("SKILLHUB_GROUP_LIMIT_EXCEEDED", "conflict"),
    "group_member_limit_exceeded": ErrorMetadata("SKILLHUB_GROUP_MEMBER_LIMIT_EXCEEDED", "conflict"),
    "skill_name_immutable": ErrorMetadata("SKILLHUB_PLUGIN_SKILL_NAME_IMMUTABLE", "validation"),
    "plugin_type_immutable": ErrorMetadata("SKILLHUB_PLUGIN_TYPE_IMMUTABLE", "validation"),
    "skill_review_model_not_configured": ErrorMetadata("SKILLHUB_REVIEW_MODEL_NOT_CONFIGURED", "upstream"),
    "not_skill": ErrorMetadata("SKILLHUB_PLUGIN_NOT_SKILL", "validation"),
    "self_moderation_forbidden": ErrorMetadata("SKILLHUB_REVIEW_SELF_MODERATION_FORBIDDEN", "permission"),
    "invalid_moderation_state": ErrorMetadata("SKILLHUB_REVIEW_MODERATION_STATE_INVALID", "validation"),
    "moderation_version_locked": ErrorMetadata("SKILLHUB_REVIEW_VERSION_LOCKED", "conflict"),
    "already_rejected": ErrorMetadata("SKILLHUB_REVIEW_ALREADY_REJECTED", "conflict"),
    "reason_required": ErrorMetadata("SKILLHUB_REVIEW_REASON_REQUIRED", "validation"),
    "invalid_action": ErrorMetadata("SKILLHUB_REVIEW_ACTION_INVALID", "validation"),
    "version_not_found": ErrorMetadata("SKILLHUB_PLUGIN_VERSION_NOT_FOUND", "not_found"),
    "plugin_package_not_found": ErrorMetadata("SKILLHUB_PLUGIN_PACKAGE_NOT_FOUND", "not_found"),
    "version_deleted": ErrorMetadata("SKILLHUB_PLUGIN_VERSION_DELETED", "conflict"),
    "db_error": ErrorMetadata("SKILLHUB_DATABASE_ERROR", "internal"),
    "raw_zip_build_failed": ErrorMetadata("SKILLHUB_PLUGIN_RAW_ZIP_BUILD_FAILED", "internal"),
    "skill_not_found": ErrorMetadata("SKILLHUB_SKILL_NOT_FOUND", "not_found"),
    "skill_version_not_found": ErrorMetadata("SKILLHUB_SKILL_VERSION_NOT_FOUND", "not_found"),
    "version_required": ErrorMetadata("SKILLHUB_PLUGIN_VERSION_REQUIRED", "validation"),
    "artifact_upstream_unavailable": ErrorMetadata("SKILLHUB_ARTIFACT_UPSTREAM_UNAVAILABLE", "upstream"),
    "artifact_upstream_non_2xx": ErrorMetadata("SKILLHUB_ARTIFACT_UPSTREAM_NON_2XX", "upstream"),
    "artifact_lookup_failed": ErrorMetadata("SKILLHUB_ARTIFACT_LOOKUP_FAILED", "upstream"),
    "invalid_path": ErrorMetadata("SKILLHUB_BUNDLE_PATH_INVALID", "validation"),
    "file_too_large_for_inspect": ErrorMetadata("SKILLHUB_BUNDLE_FILE_TOO_LARGE_FOR_INSPECT", "validation"),
    "path_not_found_in_bundle": ErrorMetadata("SKILLHUB_BUNDLE_PATH_NOT_FOUND", "not_found"),
    "resolve_upstream_artifact_errors": ErrorMetadata("SKILLHUB_RESOLVE_UPSTREAM_ARTIFACT_ERRORS", "upstream"),
    "not_found": ErrorMetadata("SKILLHUB_RESOURCE_NOT_FOUND", "not_found"),
    "skill_not_approved": ErrorMetadata("SKILLHUB_SKILL_NOT_APPROVED", "permission"),
    "invalid_action_type": ErrorMetadata("SKILLHUB_INTERACTION_ACTION_TYPE_INVALID", "validation"),
    "self_interaction_forbidden": ErrorMetadata("SKILLHUB_INTERACTION_SELF_FORBIDDEN", "permission"),
    "too_many_ids": ErrorMetadata("SKILLHUB_INTERACTION_TOO_MANY_IDS", "validation"),
    "rate_limited": ErrorMetadata("SKILLHUB_RATE_LIMITED", "conflict"),
    "invalid_repo_url": ErrorMetadata("SKILLHUB_GIT_SYNC_REPO_URL_INVALID", "validation"),
    "invalid_git_ref": ErrorMetadata("SKILLHUB_GIT_SYNC_REF_INVALID", "validation"),
    "invalid_skills_subpath": ErrorMetadata("SKILLHUB_GIT_SYNC_SUBPATH_INVALID", "validation"),
    "skills_subpath_not_found": ErrorMetadata("SKILLHUB_GIT_SYNC_SUBPATH_NOT_FOUND", "validation"),
    "git_source_sync_in_progress": ErrorMetadata("SKILLHUB_GIT_SYNC_ALREADY_RUNNING", "conflict"),
    "git_fetch_failed": ErrorMetadata("SKILLHUB_GIT_SYNC_FETCH_FAILED", "upstream"),
    "git_not_available": ErrorMetadata("SKILLHUB_GIT_SYNC_GIT_UNAVAILABLE", "upstream"),
    "git_clone_failed": ErrorMetadata("SKILLHUB_GIT_SYNC_CLONE_FAILED", "upstream"),
    "git_invalid_state": ErrorMetadata("SKILLHUB_GIT_SYNC_INVALID_STATE", "internal"),
    "git_bind_failed": ErrorMetadata("SKILLHUB_GIT_SYNC_BIND_FAILED", "internal"),
    "no_skills_in_repo": ErrorMetadata("SKILLHUB_GIT_SYNC_NO_SKILLS_FOUND", "validation"),
    "git_external_id_conflict": ErrorMetadata("SKILLHUB_GIT_SYNC_EXTERNAL_ID_CONFLICT", "conflict"),
    "git_source_has_assets": ErrorMetadata("SKILLHUB_GIT_SOURCE_HAS_ASSETS", "conflict"),
    "git_source_cascade_delete_partial": ErrorMetadata(
        "SKILLHUB_GIT_SOURCE_CASCADE_DELETE_PARTIAL", "conflict"
    ),
    "git_repo_already_registered": ErrorMetadata("SKILLHUB_GIT_SOURCE_ALREADY_REGISTERED", "conflict"),
    "git_sync_failed": ErrorMetadata("SKILLHUB_GIT_SYNC_FAILED", "upstream"),
    "forbidden": ErrorMetadata("SKILLHUB_PERMISSION_FORBIDDEN", "permission"),
    "internal_error": ErrorMetadata("SKILLHUB_INTERNAL_UNEXPECTED", "internal"),
    "validation_error": ErrorMetadata("SKILLHUB_VALIDATION_FAILED", "validation"),
    # Playground 在线体验
    "session_conflict": ErrorMetadata("SKILLHUB_PLAYGROUND_SESSION_CONFLICT", "conflict"),
    "quota_exceeded": ErrorMetadata("SKILLHUB_PLAYGROUND_QUOTA_EXCEEDED", "conflict"),
    "not_your_session": ErrorMetadata("SKILLHUB_PLAYGROUND_NOT_YOUR_SESSION", "permission"),
    # Recommender
    "recommender_disabled": ErrorMetadata("SKILLHUB_RECOMMENDER_DISABLED", "upstream"),
    "recommend_failed": ErrorMetadata("SKILLHUB_RECOMMEND_FAILED", "internal"),
    "recommend_user_mismatch": ErrorMetadata("SKILLHUB_RECOMMEND_USER_MISMATCH", "permission"),
    # GitHub 一键标星
    "github_forbidden": ErrorMetadata("SKILLHUB_GITHUB_FORBIDDEN", "permission"),
    "github_not_found": ErrorMetadata("SKILLHUB_GITHUB_NOT_FOUND", "not_found"),
    "github_upstream_error": ErrorMetadata("SKILLHUB_GITHUB_UPSTREAM_ERROR", "upstream"),
    "github_proxy_rate_limited": ErrorMetadata("SKILLHUB_GITHUB_PROXY_RATE_LIMITED", "conflict"),
    "feature_disabled": ErrorMetadata("SKILLHUB_FEATURE_DISABLED", "not_found"),
}


class ErrorPayload(TypedDict, total=False):
    code: int
    data: Any
    error: str
    message: str
    http_status: int
    error_class: str
    error_code: str
    details: Any
    meta: dict[str, Any]


def resolve_registered_error_metadata(error: str) -> tuple[Optional[str], Optional[str]]:
    metadata = _REGISTERED_ERROR_METADATA.get(error)
    if metadata is None:
        return None, None
    return metadata.error_code, metadata.error_class


def build_error_payload(
    *,
    status_code: int,
    error: str,
    message: str,
    data: Optional[Any] = None,
    error_code: Optional[str] = None,
    error_class: Optional[str] = None,
    details: Optional[Any] = None,
    meta: Optional[dict[str, Any]] = None,
) -> ErrorPayload:
    registered_error_code, registered_error_class = resolve_registered_error_metadata(error)
    resolved_error_code = error_code if error_code is not None else registered_error_code
    resolved_error_class = error_class if error_class is not None else registered_error_class
    payload: ErrorPayload = {
        "code": status_code,
        "data": data,
        "error": error,
        "message": message,
        "http_status": status_code,
    }
    if resolved_error_class is not None:
        payload["error_class"] = resolved_error_class
    if resolved_error_code is not None:
        payload["error_code"] = resolved_error_code
    if details is not None:
        payload["details"] = details
    if meta is not None:
        payload["meta"] = meta
    return payload


class PublishError(Exception):
    """Legacy structured error type kept for publish/import/validation compatibility."""

    def __init__(
        self,
        *,
        code: int,
        error: str,
        message: str,
        data: Optional[Any] = None,
        error_code: Optional[str] = None,
        error_class: Optional[str] = None,
        details: Optional[Any] = None,
        meta: Optional[dict[str, Any]] = None,
        status_code: Optional[int] = None,
    ):
        resolved_status_code = status_code or code
        self.code = code
        self.error = error
        self.message = message
        self.data = data
        self.details = details
        self.meta = meta
        self.status_code = resolved_status_code
        self.detail = build_error_payload(
            status_code=resolved_status_code,
            error=error,
            message=message,
            data=data,
            error_code=error_code,
            error_class=error_class,
            details=details,
            meta=meta,
        )
        self.error_code = self.detail.get("error_code")
        self.error_class = self.detail.get("error_class")
        super().__init__(message)

    @property
    def payload(self) -> ErrorPayload:
        return self.detail


class BusinessError(PublishError):
    """Preferred generic structured business error for non-legacy codepaths."""

    pass


def http_error_payload(
    *,
    status_code: int,
    message: str,
    error: Optional[str] = None,
    data: Optional[Any] = None,
    error_code: Optional[str] = None,
    error_class: Optional[str] = None,
    details: Optional[Any] = None,
    meta: Optional[dict[str, Any]] = None,
) -> ErrorPayload:
    return build_error_payload(
        status_code=status_code,
        error=error or "http_error",
        message=message,
        data=data,
        error_code=error_code,
        error_class=error_class,
        details=details,
        meta=meta,
    )


def normalize_http_exception_detail(status_code: int, detail: Any) -> ErrorPayload:
    if isinstance(detail, dict):
        payload = dict(detail)
        payload.setdefault("code", status_code)
        payload.setdefault("http_status", status_code)
        payload.setdefault("data", None)
        payload.setdefault("error", "http_error")
        payload.setdefault("message", str(payload.get("message") or payload.get("detail") or "Request failed"))
        return ensure_standard_error_payload(payload)
    return http_error_payload(status_code=status_code, message=str(detail or "Request failed"))


def infer_error_class(status_code: int) -> str:
    if status_code in (400, 405, 422):
        return "validation"
    if status_code == 401:
        return "auth"
    if status_code == 403:
        return "permission"
    if status_code == 404:
        return "not_found"
    if status_code == 409:
        return "conflict"
    if status_code in (502, 503, 504):
        return "upstream"
    return "internal"


def ensure_error_class(payload: ErrorPayload) -> ErrorPayload:
    if "error_class" not in payload:
        _, registered_error_class = resolve_registered_error_metadata(
            str(payload.get("error") or "")
        )
        payload["error_class"] = registered_error_class or infer_error_class(
            int(payload.get("http_status") or payload.get("code") or 500)
        )
    return payload


def ensure_error_code(payload: ErrorPayload) -> ErrorPayload:
    if "error_code" not in payload:
        registered_error_code, _ = resolve_registered_error_metadata(str(payload.get("error") or ""))
        if registered_error_code is not None:
            payload["error_code"] = registered_error_code
    return payload


def ensure_standard_error_payload(payload: ErrorPayload) -> ErrorPayload:
    ensure_error_class(payload)
    ensure_error_code(payload)
    return payload


def internal_error_payload(message: str = "Internal server error") -> ErrorPayload:
    return build_error_payload(
        status_code=500,
        error="internal_error",
        message=message,
        error_code="SKILLHUB_INTERNAL_UNEXPECTED",
        error_class="internal",
        data=None,
    )


def validation_error_payload(*, message: str, details: Any) -> ErrorPayload:
    return build_error_payload(
        status_code=422,
        error="validation_error",
        message=message,
        error_class="validation",
        details=details,
        data=None,
    )


def upstream_error_payload(
    *,
    message: str,
    error: str,
    error_code: str,
    meta: Optional[dict[str, Any]] = None,
) -> ErrorPayload:
    return build_error_payload(
        status_code=502,
        error=error,
        message=message,
        error_code=error_code,
        error_class="upstream",
        data=None,
        meta=meta,
    )


def auth_error_payload(*, status_code: int, message: str, error: str, error_code: Optional[str] = None) -> ErrorPayload:
    return build_error_payload(
        status_code=status_code,
        error=error,
        message=message,
        error_code=error_code,
        error_class=infer_error_class(status_code),
        data=None,
    )


def make_publish_error(
    *,
    status_code: int,
    error: str,
    message: str,
    data: Optional[Any] = None,
    error_code: Optional[str] = None,
    error_class: Optional[str] = None,
    details: Optional[Any] = None,
    meta: Optional[dict[str, Any]] = None,
) -> PublishError:
    return PublishError(
        code=status_code,
        status_code=status_code,
        error=error,
        message=message,
        data=data,
        error_code=error_code,
        error_class=error_class,
        details=details,
        meta=meta,
    )


def make_business_error(
    *,
    status_code: int,
    error: str,
    message: str,
    data: Optional[Any] = None,
    error_code: Optional[str] = None,
    error_class: Optional[str] = None,
    details: Optional[Any] = None,
    meta: Optional[dict[str, Any]] = None,
) -> BusinessError:
    return BusinessError(
        code=status_code,
        status_code=status_code,
        error=error,
        message=message,
        data=data,
        error_code=error_code,
        error_class=error_class,
        details=details,
        meta=meta,
    )


def as_json_response_body(payload: ErrorPayload) -> dict[str, Any]:
    return ensure_standard_error_payload(payload)


__all__ = [
    "BusinessError",
    "ErrorPayload",
    "PublishError",
    "as_json_response_body",
    "auth_error_payload",
    "build_error_payload",
    "ensure_standard_error_payload",
    "http_error_payload",
    "infer_error_class",
    "internal_error_payload",
    "make_business_error",
    "make_publish_error",
    "normalize_http_exception_detail",
    "resolve_registered_error_metadata",
    "upstream_error_payload",
    "validation_error_payload",
]
