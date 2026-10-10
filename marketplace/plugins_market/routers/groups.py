# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Annotated, Any
import contextlib

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from plugins_market.core.auth import AuthContext, require_auth
from plugins_market.core.database import get_db
from plugins_market.core.errors import http_error_payload, resolve_registered_error_metadata
from plugins_market.core.logging import get_logger
from plugins_market.core.operation_log import (
    bind_operation_actor,
    bind_operation_resource,
    complete_operation_result,
    is_invalid_or_denied_error,
    operation_context,
    operation_failure_result,
    operation_log_fields,
)
from plugins_market.core.s3_storage_client import get_storage_client
from plugins_market.schemas.common import ResponseModel
from plugins_market.schemas.group import (
    GroupCreateRequest,
    GroupDiscoverQuery,
    GroupGrantableSkillListResponse,
    GroupGrantableSkillQuery,
    GroupItem,
    GroupJoinRequestCreate,
    GroupJoinRequestDecision,
    GroupJoinRequestItem,
    GroupJoinRequestListResponse,
    GroupListQuery,
    GroupListResponse,
    GroupMemberItem,
    GroupMemberListResponse,
    GroupMemberUpsertRequest,
    GroupSkillGrantDecision,
    GroupSkillGrantItem,
    GroupSkillGrantListResponse,
    GroupSkillGrantRequest,
    GroupSkillListQuery,
    GroupStatusListQuery,
    MyGroupSkillListResponse,
    GroupUpdateRequest,
)
from plugins_market.services.groups import (
    create_group_service,
    create_join_request_service,
    decide_group_skill_grant_service,
    decide_join_request_service,
    delete_group_service,
    discover_groups_service,
    get_group_service,
    grant_skill_to_group_service,
    list_group_grants_service,
    list_group_members_service,
    list_join_requests_service,
    list_my_groups_service,
    list_my_group_skills_service,
    remove_group_member_service,
    revoke_skill_from_group_service,
    search_grantable_skills_service,
    update_group_service,
    upsert_group_member_service,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/groups", tags=["groups"])


def _log_operation_started(event: str, **fields: Any) -> None:
    logger.info(event, **operation_log_fields(stage="start", result="started", **fields))


def _log_operation_completed(event: str, *, result: str = "success", **fields: Any) -> None:
    logger.info(event, **complete_operation_result(result=result, **fields))


def _raise_with_operation_failure_log(event: str, error: Exception, **fields: Any):
    if isinstance(error, HTTPException):
        payload = (
            error.detail
            if isinstance(error.detail, dict)
            else http_error_payload(status_code=error.status_code, message=str(error.detail or "Request failed"))
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
    with contextlib.suppress(Exception):
        setattr(error, "_operation_completion_logged", True)
    raise error


@router.post("", response_model=ResponseModel[GroupItem])
async def create_group(
    body: GroupCreateRequest, db: Session = Depends(get_db), auth: AuthContext = Depends(require_auth)
) -> ResponseModel[GroupItem]:
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=create_group_service(body, auth, db))


@router.get("/my", response_model=ResponseModel[GroupListResponse])
async def list_my_groups(
    query: Annotated[GroupListQuery, Query()],
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=list_my_groups_service(
            auth,
            db,
            page=query.page,
            page_size=query.page_size,
            keyword=query.keyword,
            role_filter=query.role,
            sort=query.sort,
        ),
    )


@router.get("/my/skills", response_model=ResponseModel[MyGroupSkillListResponse])
async def list_my_group_skills(
    query: Annotated[GroupSkillListQuery, Query()],
    db: Session = Depends(get_db),
    storage=Depends(get_storage_client),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[MyGroupSkillListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=list_my_group_skills_service(
            auth,
            db,
            storage,
            page=query.page,
            page_size=query.page_size,
            keyword=query.keyword,
        ),
    )


@router.get("/discover", response_model=ResponseModel[GroupListResponse])
async def discover_groups(
    query: Annotated[GroupDiscoverQuery, Query()],
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=discover_groups_service(
            auth,
            db,
            page=query.page,
            page_size=query.page_size,
            keyword=query.keyword,
            filter_by=query.filter_by,
            sort=query.sort,
        ),
    )


@router.get("/grantable-skills", response_model=ResponseModel[GroupGrantableSkillListResponse])
async def search_grantable_skills(
    query: Annotated[GroupGrantableSkillQuery, Query()],
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupGrantableSkillListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=search_grantable_skills_service(
            auth,
            db,
            page=query.page,
            page_size=query.page_size,
            keyword=query.keyword,
            group_id=query.group_id,
        ),
    )


@router.get("/{group_id}", response_model=ResponseModel[GroupItem])
async def get_group(
    group_id: str = Path(..., min_length=1), db: Session = Depends(get_db), auth: AuthContext = Depends(require_auth)
) -> ResponseModel[GroupItem]:
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=get_group_service(group_id, auth, db))


@router.patch("/{group_id}", response_model=ResponseModel[GroupItem])
async def update_group(
    body: GroupUpdateRequest,
    group_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupItem]:
    return ResponseModel(code=status.HTTP_200_OK, message="ok", data=update_group_service(group_id, body, auth, db))


@router.delete("/{group_id}", response_model=ResponseModel[dict])
async def delete_group(
    group_id: str = Path(..., min_length=1), db: Session = Depends(get_db), auth: AuthContext = Depends(require_auth)
) -> ResponseModel[dict]:
    with operation_context(operation_type="delete_group"):
        bind_operation_actor(actor_id=auth.acting_user_id, actor_name=auth.acting_user_name, actor_type="user")
        bind_operation_resource(resource_type="group", resource_id=group_id)
        _log_operation_started("delete group", group_id=group_id)
        try:
            delete_group_service(group_id, auth, db)
        except HTTPException as exc:
            _raise_with_operation_failure_log("delete group", exc, group_id=group_id)
        _log_operation_completed("delete group", group_id=group_id)
        return ResponseModel(code=status.HTTP_200_OK, message="ok", data={"group_id": group_id})


@router.get("/{group_id}/members", response_model=ResponseModel[GroupMemberListResponse])
async def list_group_members(
    group_id: str = Path(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupMemberListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=list_group_members_service(group_id, auth, db, page=page, page_size=page_size),
    )


@router.put("/{group_id}/members", response_model=ResponseModel[GroupMemberItem])
async def upsert_group_member(
    body: GroupMemberUpsertRequest,
    group_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupMemberItem]:
    return ResponseModel(
        code=status.HTTP_200_OK, message="ok", data=upsert_group_member_service(group_id, body, auth, db)
    )


@router.delete("/{group_id}/members/{user_id}", response_model=ResponseModel[dict])
async def remove_group_member(
    group_id: str = Path(..., min_length=1),
    user_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[dict]:
    with operation_context(operation_type="remove_group_member"):
        bind_operation_actor(actor_id=auth.acting_user_id, actor_name=auth.acting_user_name, actor_type="user")
        bind_operation_resource(resource_type="group", resource_id=group_id)
        _log_operation_started("remove group member", group_id=group_id, user_id=user_id)
        try:
            remove_group_member_service(group_id, user_id, auth, db)
        except HTTPException as exc:
            _raise_with_operation_failure_log("remove group member", exc, group_id=group_id, user_id=user_id)
        _log_operation_completed("remove group member", group_id=group_id, user_id=user_id)
        return ResponseModel(code=status.HTTP_200_OK, message="ok", data={"group_id": group_id, "user_id": user_id})


@router.post("/{group_id}/join-requests", response_model=ResponseModel[GroupJoinRequestItem])
async def create_join_request(
    body: GroupJoinRequestCreate,
    group_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupJoinRequestItem]:
    return ResponseModel(
        code=status.HTTP_200_OK, message="ok", data=create_join_request_service(group_id, body, auth, db)
    )


@router.get("/{group_id}/join-requests", response_model=ResponseModel[GroupJoinRequestListResponse])
async def list_join_requests(
    query: Annotated[GroupStatusListQuery, Query()],
    group_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupJoinRequestListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=list_join_requests_service(
            group_id,
            auth,
            db,
            page=query.page,
            page_size=query.page_size,
            status_filter=query.status,
        ),
    )


@router.post("/{group_id}/join-requests/{request_id}/decision", response_model=ResponseModel[GroupJoinRequestItem])
async def decide_join_request(
    body: GroupJoinRequestDecision,
    group_id: str = Path(..., min_length=1),
    request_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupJoinRequestItem]:
    with operation_context(operation_type="decide_join_request"):
        bind_operation_actor(actor_id=auth.acting_user_id, actor_name=auth.acting_user_name, actor_type="user")
        bind_operation_resource(resource_type="group", resource_id=group_id)
        _log_operation_started("decide join request", group_id=group_id, request_id=request_id, decision=body.status)
        try:
            data = decide_join_request_service(group_id, request_id, body, auth, db)
        except HTTPException as exc:
            _raise_with_operation_failure_log("decide join request", exc, group_id=group_id, request_id=request_id)
        _log_operation_completed("decide join request", group_id=group_id, request_id=request_id, decision=body.status)
        return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@router.get("/{group_id}/grants", response_model=ResponseModel[GroupSkillGrantListResponse])
async def list_group_grants(
    query: Annotated[GroupStatusListQuery, Query()],
    group_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupSkillGrantListResponse]:
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=list_group_grants_service(
            group_id,
            auth,
            db,
            page=query.page,
            page_size=query.page_size,
            status_filter=query.status,
        ),
    )


@router.post("/{group_id}/grants", response_model=ResponseModel[GroupSkillGrantItem])
async def grant_skill_to_group(
    body: GroupSkillGrantRequest,
    group_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupSkillGrantItem]:
    with operation_context(operation_type="grant_skill_to_group"):
        bind_operation_actor(actor_id=auth.acting_user_id, actor_name=auth.acting_user_name, actor_type="user")
        bind_operation_resource(resource_type="group", resource_id=group_id)
        _log_operation_started("grant skill to group", group_id=group_id, asset_id=body.asset_id)
        try:
            data = grant_skill_to_group_service(group_id, body, auth, db)
        except HTTPException as exc:
            _raise_with_operation_failure_log("grant skill to group", exc, group_id=group_id, asset_id=body.asset_id)
        _log_operation_completed(
            "grant skill to group", group_id=group_id, asset_id=body.asset_id, grant_status=data.status
        )
        return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@router.post("/{group_id}/grants/{asset_id}/decision", response_model=ResponseModel[GroupSkillGrantItem])
async def decide_group_skill_grant(
    body: GroupSkillGrantDecision,
    group_id: str = Path(..., min_length=1),
    asset_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[GroupSkillGrantItem]:
    with operation_context(operation_type="decide_group_skill_grant"):
        bind_operation_actor(actor_id=auth.acting_user_id, actor_name=auth.acting_user_name, actor_type="user")
        bind_operation_resource(resource_type="group", resource_id=group_id)
        _log_operation_started("decide group skill grant", group_id=group_id, asset_id=asset_id, decision=body.status)
        try:
            data = decide_group_skill_grant_service(group_id, asset_id, body, auth, db)
        except HTTPException as exc:
            _raise_with_operation_failure_log("decide group skill grant", exc, group_id=group_id, asset_id=asset_id)
        _log_operation_completed("decide group skill grant", group_id=group_id, asset_id=asset_id, decision=body.status)
        return ResponseModel(code=status.HTTP_200_OK, message="ok", data=data)


@router.delete("/{group_id}/grants/{asset_id}", response_model=ResponseModel[dict])
async def revoke_skill_from_group(
    group_id: str = Path(..., min_length=1),
    asset_id: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[dict]:
    with operation_context(operation_type="revoke_skill_from_group"):
        bind_operation_actor(actor_id=auth.acting_user_id, actor_name=auth.acting_user_name, actor_type="user")
        bind_operation_resource(resource_type="group", resource_id=group_id)
        _log_operation_started("revoke skill from group", group_id=group_id, asset_id=asset_id)
        try:
            revoke_skill_from_group_service(group_id, asset_id, auth, db)
        except HTTPException as exc:
            _raise_with_operation_failure_log("revoke skill from group", exc, group_id=group_id, asset_id=asset_id)
        _log_operation_completed("revoke skill from group", group_id=group_id, asset_id=asset_id)
        return ResponseModel(code=status.HTTP_200_OK, message="ok", data={"group_id": group_id, "asset_id": asset_id})
