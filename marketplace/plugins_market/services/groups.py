# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from plugins_market.core.auth import AuthContext
from plugins_market.core.config import settings
from plugins_market.core.viewer_context import ViewerContext
from plugins_market.services.plugin import _list_item_from_asset
from plugins_market.core.errors import http_error_payload
from plugins_market.core.moderation import is_skill_like_plugin_type
from plugins_market.core.publish_result import is_skill_asset_publicly_visible
from plugins_market.models.groups import (
    MarketGroupDB,
    MarketGroupJoinRequestDB,
    MarketGroupMemberDB,
    MarketGroupSkillGrantDB,
)
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.repositories.market_assets_repository import list_icon_version_join_expr
from plugins_market.repositories import MarketAssetRepository, MarketAssetVersionRepository
from plugins_market.repositories.groups_repository import (
    GROUP_ROLE_MEMBER,
    GROUP_ROLE_OWNER,
    GROUP_VISIBILITY_LISTED,
    GROUP_VISIBILITY_PRIVATE,
    JOIN_STATUS_APPROVED,
    JOIN_STATUS_PENDING,
    GRANT_STATUS_ACTIVE,
    GRANT_STATUS_PENDING,
    GRANT_STATUS_REJECTED,
    GRANT_STATUS_REVOKED,
    MarketGroupJoinRequestRepository,
    MarketGroupMemberRepository,
    MarketGroupRepository,
    MarketGroupSkillGrantRepository,
    now_ms,
)
from plugins_market.services.site_notifications import (
    notify_group_deleted_applicants,
    notify_group_deleted_members,
    notify_group_deleted_publishers,
    notify_group_owners_skill_grant_pending,
    notify_publisher_skill_grant_approved,
    notify_publisher_skill_grant_rejected,
)
from plugins_market.schemas.group import (
    GroupCreateRequest,
    GroupGrantableSkillItem,
    GroupGrantableSkillListResponse,
    GroupItem,
    GroupJoinRequestCreate,
    GroupJoinRequestDecision,
    GroupJoinRequestItem,
    GroupJoinRequestListResponse,
    GroupListResponse,
    GroupMemberItem,
    GroupMemberListResponse,
    GroupMemberUpsertRequest,
    GroupSkillGrantDecision,
    GroupSkillGrantItem,
    GroupSkillGrantListResponse,
    GroupSkillGrantRequest,
    MyGroupSkillItem,
    MyGroupSkillListResponse,
    GroupUpdateRequest,
)


def _http_exception(status_code: int, message: str, *, error: str) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail=http_error_payload(status_code=status_code, message=message, error=error)
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _page(page: int) -> int:
    return max(1, int(page or 1))


def _page_size(page_size: int) -> int:
    return max(1, min(int(page_size or 20), 100))


def _member_role(db: Session, group_id: str, user_id: str) -> str | None:
    row = MarketGroupMemberRepository(db).get_member(group_id, user_id)
    return row.role if row else None


def _is_privileged(auth: AuthContext) -> bool:
    """系统管理员和审核管理员在组群管理上享有同等最高权限。"""
    return auth.is_admin or auth.is_market_moderation_admin


def _require_group(db: Session, group_id: str) -> MarketGroupDB:
    group = MarketGroupRepository(db).get_by_group_id(group_id)
    if not group or group.status != "active":
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Group not found", error="group_not_found")
    return group


@dataclass
class GroupAccessContext:
    """组群管理权限统一上下文：封装成员角色、特权身份、可见性、管理权、授权来源判定。

    与 Skill ACL（ViewerContext）分离：ViewerContext 管 Skill 资产读可见性，
    本类管组群写操作（建群/删群/审批/授权/撤销）的权限。组群内全部权限判定走本类，
    消除散落在各 service 函数的 if/else 与重复 source_map。
    """

    db: Session
    group: MarketGroupDB
    auth: AuthContext
    role: Optional[str]
    """真实成员角色（None=未加入），不虚构 owner 身份。"""
    is_privileged: bool
    """系统管理员/审核管理员在组群管理上享有同等最高权限。"""

    @classmethod
    def for_group(cls, db: Session, group_id: str, auth: AuthContext) -> "GroupAccessContext":
        """构造：查 group + 算 role + 算 is_privileged，一次查全。group 不存在则抛 404。"""
        group = _require_group(db, group_id)
        role = _member_role(db, group_id, auth.acting_user_id)
        return cls(db=db, group=group, auth=auth, role=role, is_privileged=_is_privileged(auth))

    @property
    def is_listed(self) -> bool:
        """组群是否为公开可见（listed）。"""
        return (getattr(self.group, "visibility", None) or GROUP_VISIBILITY_PRIVATE) == GROUP_VISIBILITY_LISTED

    # ---- 可见性 ----
    @property
    def can_view(self) -> bool:
        """能否看到该组群（特权/成员/公开组群）。"""
        if self.is_privileged:
            return True
        return bool(self.role) or self.is_listed

    def require_view(self, *, not_found: bool = False) -> None:
        """校验可见性。not_found=True 时不可见返回 404（用于搜索场景，避免泄露组群存在性）。"""
        if not self.can_view:
            if not_found:
                raise _http_exception(status.HTTP_404_NOT_FOUND, "Group not found", error="group_not_found")
            raise _http_exception(
                status.HTTP_403_FORBIDDEN, "Insufficient group permissions", error="permission_denied"
            )

    # ---- 成员/管理权 ----
    @property
    def can_manage(self) -> bool:
        """能否管理该组群（特权/群主）。用于 viewer_can_manage。"""
        return self.is_privileged or self.role == GROUP_ROLE_OWNER

    @property
    def effective_role_for_write(self) -> Optional[str]:
        """写操作时的有效角色：特权用户视作 owner（放行写操作），否则返回真实角色。"""
        return GROUP_ROLE_OWNER if self.is_privileged else self.role

    def require_member(self) -> str:
        role = self.effective_role_for_write
        if not role:
            raise _http_exception(
                status.HTTP_403_FORBIDDEN, "Insufficient group permissions", error="permission_denied"
            )
        return role

    def require_owner(self) -> None:
        if not self.can_manage:
            raise _http_exception(
                status.HTTP_403_FORBIDDEN, "Only group owner can perform this operation", error="permission_denied"
            )

    # ---- 授权相关 ----
    @property
    def grant_is_directly_active(self) -> bool:
        """群主或特权用户授权直接生效，无需其他群主审批。"""
        return self.role == GROUP_ROLE_OWNER or self.is_privileged

    def can_revoke_grant(self, asset: MarketAssetDB | None) -> bool:
        """能否撤销授权：特权/群主/该 skill 发布者。"""
        is_publisher = bool(asset and asset.publisher_id == self.auth.acting_user_id)
        return self.is_privileged or self.role == GROUP_ROLE_OWNER or is_publisher

    def can_grant_asset(self, asset: MarketAssetDB) -> bool:
        """能否授权该 skill：特权或该 skill 发布者。"""
        return self.is_privileged or asset.publisher_id == self.auth.acting_user_id

    def grant_access_source(
        self, asset: MarketAssetDB, *, granted_ids: set[str], is_active_or_visible: bool = True
    ) -> Optional[str]:
        """授权来源标签。

        组群场景特有：发布者优先于管理员，服务于前端撤销授权入口（撤销依据是发布者身份）。
        注意与 ViewerContext.skill_asset_access_source 顺序不同：ViewerContext 是 admin 优先
        （看资产视角），此处是 owner 优先（撤销授权视角）。
        """
        return self.grant_access_source_for_user(
            self.auth,
            asset,
            is_privileged=self.is_privileged,
            granted_ids=granted_ids,
            is_visible=is_active_or_visible,
        )

    @staticmethod
    def grant_access_source_for_user(
        auth: AuthContext,
        asset: MarketAssetDB,
        *,
        is_privileged: bool,
        granted_ids: set[str],
        is_visible: bool = True,
    ) -> Optional[str]:
        """跨群组场景（无 group_id）的来源判定：仅依赖 auth + granted_ids。

        供 list_my_group_skills_service 等无 group 上下文的列表复用，确保两处 source_map 逻辑一致。
        """
        if asset.publisher_id == auth.acting_user_id:
            return "owner"
        if is_privileged:
            return "admin"
        if asset.asset_id in granted_ids:
            return "group"
        return "public" if is_visible else None



def _group_item(
    row: MarketGroupDB,
    viewer_role: str | None = None,
    join_status: str | None = None,
    viewer_can_manage: bool = False,
) -> GroupItem:
    return GroupItem(
        group_id=row.group_id,
        name=row.name,
        description=row.description,
        owner_id=row.owner_id,
        owner_name=row.owner_name,
        visibility=getattr(row, "visibility", None) or "private",
        member_count=int(row.member_count or 0),
        skill_count=int(row.skill_count or 0),
        viewer_role=viewer_role,
        viewer_can_manage=viewer_can_manage,
        join_request_status=join_status,
        create_time=int(row.create_time or 0),
        update_time=int(row.update_time or 0),
    )


def _member_item(row: MarketGroupMemberDB) -> GroupMemberItem:
    return GroupMemberItem(
        user_id=row.user_id,
        user_name=row.user_name,
        role=row.role,
        create_time=int(row.create_time or 0),
        update_time=int(row.update_time or 0),
    )


def _join_request_item(row: MarketGroupJoinRequestDB) -> GroupJoinRequestItem:
    return GroupJoinRequestItem(
        request_id=row.request_id,
        group_id=row.group_id,
        user_id=row.user_id,
        user_name=row.user_name,
        message=row.message,
        status=row.status,
        create_time=int(row.create_time or 0),
        update_time=int(row.update_time or 0),
    )


def _grant_item(
    row: MarketGroupSkillGrantDB, asset: MarketAssetDB | None = None, viewer_access_source: str | None = None
) -> GroupSkillGrantItem:
    return GroupSkillGrantItem(
        group_id=row.group_id,
        asset_id=row.asset_id,
        skill_name=asset.name if asset else None,
        skill_display_name=asset.display_name if asset else None,
        icon_uri=None,
        latest_version=asset.latest_version if asset else None,
        public_latest_version=asset.public_latest_version if asset else None,
        status=row.status or GRANT_STATUS_ACTIVE,
        viewer_access_source=viewer_access_source,
        create_time=int(row.create_time or 0),
        update_time=int(row.update_time or row.create_time or 0),
    )


def _grantable_skill_item(row: MarketAssetDB, grant_status: str | None = None) -> GroupGrantableSkillItem:
    # 与 ACL 可见判定一致：新模型看 public_latest_version，旧模型回退 moderation_status
    grantable = is_skill_asset_publicly_visible(
        publish_result=getattr(row, "publish_result", None),
        moderation_status=getattr(row, "moderation_status", None),
        public_latest_version=getattr(row, "public_latest_version", None),
    )
    return GroupGrantableSkillItem(
        asset_id=row.asset_id,
        name=row.name,
        display_name=row.display_name,
        short_desc=row.short_desc,
        publisher_id=row.publisher_id,
        publisher_name=row.publisher_name,
        plugin_type=row.plugin_type,
        latest_version=row.latest_version,
        group_grant_status=grant_status,
        grantable=grantable,
        not_grantable_reason=None if grantable else "Skill has not passed moderation",
    )


def create_group_service(body: GroupCreateRequest, auth: AuthContext, db: Session) -> GroupItem:
    if not _is_privileged(auth) and settings.max_groups_per_user > 0:
        count = MarketGroupRepository(db).count_by_owner(auth.acting_user_id)
        if count >= settings.max_groups_per_user:
            raise _http_exception(
                status.HTTP_409_CONFLICT,
                f"您已创建 {count} 个组群，达到上限 {settings.max_groups_per_user}",
                error="group_limit_exceeded",
            )
    ts = now_ms()
    group = MarketGroupDB(
        group_id=_new_id("grp"),
        name=body.name,
        description=body.description,
        owner_id=auth.acting_user_id,
        owner_name=auth.acting_user_name,
        visibility=body.visibility,
        status="active",
        member_count=1,
        skill_count=0,
        create_time=ts,
        update_time=ts,
    )
    member = MarketGroupMemberDB(
        group_id=group.group_id,
        user_id=auth.acting_user_id,
        user_name=auth.acting_user_name,
        role=GROUP_ROLE_OWNER,
        create_time=ts,
        update_time=ts,
    )
    try:
        db.add(group)
        db.add(member)
        db.commit()
        db.refresh(group)
        return _group_item(group, GROUP_ROLE_OWNER, viewer_can_manage=True)
    except SQLAlchemyError:
        db.rollback()
        raise


def list_my_groups_service(
    auth: AuthContext,
    db: Session,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    role_filter: str | None = None,
    sort: str | None = None,
) -> GroupListResponse:
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    safe_role = role_filter if role_filter in (GROUP_ROLE_OWNER, GROUP_ROLE_MEMBER) else None
    safe_sort = sort if sort in ("updated", "members", "skills", "name") else None
    rows, total = MarketGroupRepository(db).list_for_user(
        auth.acting_user_id, keyword, page=safe_page, page_size=safe_size, role_filter=safe_role, sort=safe_sort
    )
    return GroupListResponse(
        page=safe_page, page_size=safe_size, total=total, items=[_group_item(row, role) for row, role in rows]
    )


def discover_groups_service(
    auth: AuthContext,
    db: Session,
    *,
    page: int,
    page_size: int,
    keyword: str | None,
    filter_by: str | None = None,
    sort: str | None = None,
) -> GroupListResponse:
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    safe_filter = filter_by if filter_by in ("joined", "pending", "available") else None
    safe_sort = sort if sort in ("updated", "members", "skills", "name") else None
    rows, total = MarketGroupRepository(db).discover(
        auth.acting_user_id, keyword, page=safe_page, page_size=safe_size, filter_by=safe_filter, sort=safe_sort,
        is_privileged=_is_privileged(auth),
    )
    return GroupListResponse(
        page=safe_page,
        page_size=safe_size,
        total=total,
        items=[_group_item(row, role, join_status) for row, role, join_status in rows],
    )


def get_group_service(group_id: str, auth: AuthContext, db: Session) -> GroupItem:
    access = GroupAccessContext.for_group(db, group_id, auth)
    access.require_view()
    latest = MarketGroupJoinRequestRepository(db).latest_for_user(group_id, auth.acting_user_id)
    return _group_item(access.group, access.role, latest.status if latest else None,
                       viewer_can_manage=access.can_manage)


def update_group_service(group_id: str, body: GroupUpdateRequest, auth: AuthContext, db: Session) -> GroupItem:
    access = GroupAccessContext.for_group(db, group_id, auth)
    access.require_owner()
    group = access.group
    if body.name is not None:
        group.name = body.name
    if body.description is not None:
        group.description = body.description
    if body.visibility is not None:
        group.visibility = body.visibility
    group.update_time = now_ms()
    try:
        db.add(group)
        db.commit()
        db.refresh(group)
        return _group_item(group, access.role, viewer_can_manage=access.can_manage)
    except SQLAlchemyError:
        db.rollback()
        raise


def delete_group_service(group_id: str, auth: AuthContext, db: Session) -> None:
    GroupAccessContext.for_group(db, group_id, auth).require_owner()
    member_repo = MarketGroupMemberRepository(db)
    join_repo = MarketGroupJoinRequestRepository(db)
    grant_repo = MarketGroupSkillGrantRepository(db)
    group_repo = MarketGroupRepository(db)
    # 删除前先抓取受影响用户：成员（排除执行删除者本人）、pending 申请人、仍生效或待审批授权的 Skill 发布者。
    # 删完即无数据可查，必须在删除事务之前读取。
    member_ids = member_repo.member_user_ids_for_group(group_id, exclude_user_id=auth.acting_user_id)
    applicant_ids = join_repo.pending_user_ids_for_group(group_id)
    publisher_ids = grant_repo.publisher_ids_for_group(
        group_id, [GRANT_STATUS_PENDING, GRANT_STATUS_ACTIVE]
    )
    try:
        grant_repo.delete_by_group(group_id)
        join_repo.delete_by_group(group_id)
        member_repo.delete_by_group(group_id)
        group_repo.delete_group(group_id)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    # 主事务提交后再发通知，避免通知写入影响删除事务；与授权审批流程一致，通知失败仅记日志。
    if member_ids:
        notify_group_deleted_members(db, user_ids=member_ids)
    if applicant_ids:
        notify_group_deleted_applicants(db, user_ids=applicant_ids)
    if publisher_ids:
        notify_group_deleted_publishers(db, user_ids=publisher_ids)


def list_group_members_service(
    group_id: str, auth: AuthContext, db: Session, *, page: int, page_size: int
) -> GroupMemberListResponse:
    access = GroupAccessContext.for_group(db, group_id, auth)
    access.require_member()
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    rows, total = MarketGroupMemberRepository(db).list_members(group_id, page=safe_page, page_size=safe_size)
    return GroupMemberListResponse(
        page=safe_page, page_size=safe_size, total=total, items=[_member_item(r) for r in rows]
    )


def upsert_group_member_service(
    group_id: str, body: GroupMemberUpsertRequest, auth: AuthContext, db: Session
) -> GroupMemberItem:
    access = GroupAccessContext.for_group(db, group_id, auth)
    access.require_owner()
    member_repo = MarketGroupMemberRepository(db)
    existing = member_repo.get_member(group_id, body.user_id)
    if existing and existing.role == GROUP_ROLE_OWNER and body.role != GROUP_ROLE_OWNER:
        raise _http_exception(status.HTTP_400_BAD_REQUEST, "Cannot demote group owner", error="cannot_demote_owner")
    if existing is None and not access.is_privileged and settings.max_members_per_group > 0:
        count = member_repo.count_members(group_id)
        if count >= settings.max_members_per_group:
            raise _http_exception(
                status.HTTP_409_CONFLICT,
                f"该组群已有 {count} 个成员，达到上限 {settings.max_members_per_group}",
                error="group_member_limit_exceeded",
            )
    try:
        row = member_repo.upsert_member(
            group_id=group_id, user_id=body.user_id, user_name=body.user_name, role=body.role
        )
        db.flush()
        MarketGroupRepository(db).refresh_counts(group_id)
        db.commit()
        db.refresh(row)
        return _member_item(row)
    except IntegrityError as exc:
        db.rollback()
        raise _http_exception(status.HTTP_409_CONFLICT, "Member already exists", error="member_conflict") from exc
    except SQLAlchemyError:
        db.rollback()
        raise


def remove_group_member_service(group_id: str, user_id: str, auth: AuthContext, db: Session) -> None:
    access = GroupAccessContext.for_group(db, group_id, auth)
    member_repo = MarketGroupMemberRepository(db)
    row = member_repo.get_member(group_id, user_id)
    if not row:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Member not found", error="member_not_found")
    # 移除他人需群主权限；自己退出不需要
    if user_id != auth.acting_user_id:
        access.require_owner()
    if row.role == GROUP_ROLE_OWNER:
        raise _http_exception(status.HTTP_400_BAD_REQUEST, "Cannot remove group owner", error="cannot_remove_owner")
    try:
        member_repo.remove_member(group_id, user_id)
        # 清理该用户在该组群的历史加入申请记录，避免退出后 discover 仍显示旧状态、
        # 以及再次加入时复用陈旧 approved 记录导致列表显示第一次申请
        MarketGroupJoinRequestRepository(db).delete_by_group_and_user(group_id, user_id)
        MarketGroupRepository(db).refresh_counts(group_id)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def create_join_request_service(
    group_id: str, body: GroupJoinRequestCreate, auth: AuthContext, db: Session
) -> GroupJoinRequestItem:
    access = GroupAccessContext.for_group(db, group_id, auth)
    group = access.group
    member_repo = MarketGroupMemberRepository(db)
    if member_repo.get_member(group_id, auth.acting_user_id):
        raise _http_exception(status.HTTP_409_CONFLICT, "User is already a group member", error="already_member")
    # 特权用户（系统管理员/审核管理员）可直接加入任意组群（含 private），跳过申请审批
    if access.is_privileged:
        ts = now_ms()
        member_repo.upsert_member(
            group_id=group_id, user_id=auth.acting_user_id, user_name=auth.acting_user_name, role=GROUP_ROLE_MEMBER
        )
        db.flush()
        MarketGroupRepository(db).refresh_counts(group_id)
        db.commit()
        return GroupJoinRequestItem(
            request_id="",
            group_id=group_id,
            user_id=auth.acting_user_id,
            user_name=auth.acting_user_name,
            message=None,
            status=JOIN_STATUS_APPROVED,
            create_time=ts,
            update_time=ts,
        )
    if not access.is_listed:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Group not found", error="group_not_found")
    join_repo = MarketGroupJoinRequestRepository(db)
    existing = join_repo.get_pending(group_id, auth.acting_user_id)
    if existing:
        return _join_request_item(existing)
    ts = now_ms()
    row = MarketGroupJoinRequestDB(
        request_id=_new_id("gjr"),
        group_id=group_id,
        user_id=auth.acting_user_id,
        user_name=auth.acting_user_name,
        message=body.message,
        status=JOIN_STATUS_PENDING,
        create_time=ts,
        update_time=ts,
    )
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
        return _join_request_item(row)
    except IntegrityError:
        db.rollback()
        existing = join_repo.get_pending(group_id, auth.acting_user_id)
        if existing:
            return _join_request_item(existing)
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


def list_join_requests_service(
    group_id: str, auth: AuthContext, db: Session, *, page: int, page_size: int, status_filter: str | None
) -> GroupJoinRequestListResponse:
    GroupAccessContext.for_group(db, group_id, auth).require_owner()
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    rows, total = MarketGroupJoinRequestRepository(db).list_for_group(
        group_id, page=safe_page, page_size=safe_size, status=status_filter
    )
    return GroupJoinRequestListResponse(
        page=safe_page, page_size=safe_size, total=total, items=[_join_request_item(r) for r in rows]
    )


def decide_join_request_service(
    group_id: str, request_id: str, body: GroupJoinRequestDecision, auth: AuthContext, db: Session
) -> GroupJoinRequestItem:
    access = GroupAccessContext.for_group(db, group_id, auth)
    access.require_owner()
    join_repo = MarketGroupJoinRequestRepository(db)
    req = join_repo.get_by_request_id(request_id)
    if not req or req.group_id != group_id:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Join request not found", error="join_request_not_found")
    if req.status != JOIN_STATUS_PENDING:
        return _join_request_item(req)
    member_repo = MarketGroupMemberRepository(db)
    if body.status == JOIN_STATUS_APPROVED and member_repo.get_member(group_id, req.user_id):
        req.status = JOIN_STATUS_REJECTED
        req.operator_id = auth.acting_user_id
        req.operator_name = auth.acting_user_name
        req.update_time = now_ms()
        try:
            db.add(req)
            db.commit()
            db.refresh(req)
            return _join_request_item(req)
        except SQLAlchemyError:
            db.rollback()
            raise
    ts = now_ms()
    try:
        if body.status == JOIN_STATUS_APPROVED:
            # 成员数上限校验（审批通过路径同样校验）
            if not access.is_privileged and settings.max_members_per_group > 0:
                count = member_repo.count_members(group_id)
                if count >= settings.max_members_per_group:
                    raise _http_exception(
                        status.HTTP_409_CONFLICT,
                        f"该组群已有 {count} 个成员，达到上限 {settings.max_members_per_group}",
                        error="group_member_limit_exceeded",
                    )
            approved = (
                db.query(MarketGroupJoinRequestDB)
                .filter(
                    MarketGroupJoinRequestDB.group_id == group_id,
                    MarketGroupJoinRequestDB.user_id == req.user_id,
                    MarketGroupJoinRequestDB.status == JOIN_STATUS_APPROVED,
                    MarketGroupJoinRequestDB.request_id != request_id,
                )
                .first()
            )
            if approved:
                member_repo.upsert_member(
                    group_id=group_id, user_id=req.user_id, user_name=req.user_name, role=GROUP_ROLE_MEMBER
                )
                db.delete(req)
                db.flush()
                MarketGroupRepository(db).refresh_counts(group_id)
                db.commit()
                return GroupJoinRequestItem(
                    request_id=request_id,
                    group_id=group_id,
                    user_id=approved.user_id,
                    user_name=req.user_name or approved.user_name,
                    message=req.message,
                    status=JOIN_STATUS_APPROVED,
                    operator_id=auth.acting_user_id,
                    operator_name=auth.acting_user_name,
                    create_time=int(req.create_time or 0),
                    update_time=ts,
                )
            member_repo.upsert_member(
                group_id=group_id, user_id=req.user_id, user_name=req.user_name, role=GROUP_ROLE_MEMBER
            )
        if body.status != JOIN_STATUS_PENDING:
            (
                db.query(MarketGroupJoinRequestDB)
                .filter(
                    MarketGroupJoinRequestDB.group_id == group_id,
                    MarketGroupJoinRequestDB.user_id == req.user_id,
                    MarketGroupJoinRequestDB.status == body.status,
                    MarketGroupJoinRequestDB.request_id != request_id,
                )
                .delete(synchronize_session=False)
            )
        req.status = body.status
        req.operator_id = auth.acting_user_id
        req.operator_name = auth.acting_user_name
        req.update_time = ts
        db.add(req)
        db.flush()
        MarketGroupRepository(db).refresh_counts(group_id)
        db.commit()
        db.refresh(req)
        return _join_request_item(req)
    except SQLAlchemyError:
        db.rollback()
        raise


def search_grantable_skills_service(
    auth: AuthContext, db: Session, *, page: int, page_size: int, keyword: str | None, group_id: str | None = None
) -> GroupGrantableSkillListResponse:
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    rows, total = MarketAssetRepository(db).search_grantable_skills_for_publisher(
        publisher_id=auth.acting_user_id,
        keyword=keyword,
        page=safe_page,
        page_size=safe_size,
    )
    grant_status_by_asset_id: dict[str, str] = {}
    if group_id and rows:
        access = GroupAccessContext.for_group(db, group_id, auth)
        access.require_view(not_found=True)
        grants = MarketGroupSkillGrantRepository(db).grants_for_assets(group_id, [row.asset_id for row in rows])
        grant_status_by_asset_id = {
            grant.asset_id: grant.status
            for grant in grants
            if grant.status in (GRANT_STATUS_ACTIVE, GRANT_STATUS_PENDING)
        }
    return GroupGrantableSkillListResponse(
        page=safe_page,
        page_size=safe_size,
        total=total,
        items=[_grantable_skill_item(r, grant_status_by_asset_id.get(r.asset_id)) for r in rows],
    )


def _require_grantable_asset(asset: MarketAssetDB | None, auth: AuthContext) -> MarketAssetDB:
    if not asset or (asset.status or "").upper() == "OFFLINE":
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Asset not found", error="asset_not_found")
    if not is_skill_like_plugin_type(asset.plugin_type):
        raise _http_exception(
            status.HTTP_400_BAD_REQUEST, "Only skill assets can be granted to groups", error="invalid_asset_type"
        )
    if not _is_privileged(auth) and asset.publisher_id != auth.acting_user_id:
        raise _http_exception(
            status.HTTP_403_FORBIDDEN, "Only publisher can grant this skill", error="permission_denied"
        )
    # 未通过审核的 skill 不可授权给组群：与 ACL 可见判定一致，
    # 新模型要求 public_latest_version 非空，旧模型回退 moderation_status == APPROVED
    if not is_skill_asset_publicly_visible(
        publish_result=getattr(asset, "publish_result", None),
        moderation_status=getattr(asset, "moderation_status", None),
        public_latest_version=getattr(asset, "public_latest_version", None),
    ):
        raise _http_exception(
            status.HTTP_409_CONFLICT,
            "该 Skill 未通过审核，无法授权给组群",
            error="skill_not_approved",
        )
    return asset


def grant_skill_to_group_service(
    group_id: str, body: GroupSkillGrantRequest, auth: AuthContext, db: Session
) -> GroupSkillGrantItem:
    access = GroupAccessContext.for_group(db, group_id, auth)
    access.require_view()
    asset = _require_grantable_asset(MarketAssetRepository(db).get_by_asset_id(body.asset_id), auth)
    grant_repo = MarketGroupSkillGrantRepository(db)
    existing = grant_repo.get_grant(group_id, asset.asset_id)
    if existing:
        if existing.status in (GRANT_STATUS_REJECTED, GRANT_STATUS_REVOKED):
            next_status = GRANT_STATUS_ACTIVE if access.grant_is_directly_active else GRANT_STATUS_PENDING
            grant_repo.set_status(
                existing,
                status=next_status,
                operator_id=auth.acting_user_id if next_status == GRANT_STATUS_ACTIVE else None,
                operator_name=auth.acting_user_name if next_status == GRANT_STATUS_ACTIVE else None,
            )
            MarketGroupRepository(db).refresh_counts(group_id)
            db.commit()
            db.refresh(existing)
            member_repo = MarketGroupMemberRepository(db)
            if next_status == GRANT_STATUS_PENDING:
                owner_ids = member_repo.owner_user_ids_for_group(group_id, exclude_user_id=auth.acting_user_id)
                notify_group_owners_skill_grant_pending(db, owner_user_ids=owner_ids)
        return _grant_item(existing)
    ts = now_ms()
    grant_status = GRANT_STATUS_ACTIVE if access.grant_is_directly_active else GRANT_STATUS_PENDING
    row = MarketGroupSkillGrantDB(
        group_id=group_id,
        asset_id=asset.asset_id,
        status=grant_status,
        operator_id=auth.acting_user_id if grant_status == GRANT_STATUS_ACTIVE else None,
        operator_name=auth.acting_user_name if grant_status == GRANT_STATUS_ACTIVE else None,
        create_time=ts,
        update_time=ts,
    )
    try:
        db.add(row)
        db.flush()
        MarketGroupRepository(db).refresh_counts(group_id)
        db.commit()
        db.refresh(row)
        member_repo = MarketGroupMemberRepository(db)
        if grant_status == GRANT_STATUS_PENDING:
            owner_ids = member_repo.owner_user_ids_for_group(group_id, exclude_user_id=auth.acting_user_id)
            notify_group_owners_skill_grant_pending(db, owner_user_ids=owner_ids)
        return _grant_item(row)
    except IntegrityError:
        db.rollback()
        existing = grant_repo.get_grant(group_id, asset.asset_id)
        if existing:
            return _grant_item(existing)
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


def decide_group_skill_grant_service(
    group_id: str, asset_id: str, body: GroupSkillGrantDecision, auth: AuthContext, db: Session
) -> GroupSkillGrantItem:
    GroupAccessContext.for_group(db, group_id, auth).require_owner()
    grant_repo = MarketGroupSkillGrantRepository(db)
    row = grant_repo.get_grant(group_id, asset_id)
    if not row or row.status == GRANT_STATUS_REVOKED:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Grant not found", error="grant_not_found")
    if row.status != GRANT_STATUS_PENDING:
        raise _http_exception(
            status.HTTP_409_CONFLICT, "Only pending grants can be reviewed", error="grant_not_pending"
        )
    next_status = GRANT_STATUS_ACTIVE if body.status == GRANT_STATUS_ACTIVE else GRANT_STATUS_REJECTED
    try:
        grant_repo.set_status(
            row, status=next_status, operator_id=auth.acting_user_id, operator_name=auth.acting_user_name
        )
        db.flush()
        MarketGroupRepository(db).refresh_counts(group_id)
        db.commit()
        db.refresh(row)
    except SQLAlchemyError:
        db.rollback()
        raise
    # 通知 Skill 发布者审批结果（commit 之后发送，避免通知写入影响主事务）
    asset = MarketAssetRepository(db).get_by_asset_id(asset_id)
    publisher_id = asset.publisher_id if asset else None
    if publisher_id:
        if next_status == GRANT_STATUS_ACTIVE:
            notify_publisher_skill_grant_approved(db, publisher_id=publisher_id)
        else:
            notify_publisher_skill_grant_rejected(db, publisher_id=publisher_id)
    return _grant_item(row)


def revoke_skill_from_group_service(group_id: str, asset_id: str, auth: AuthContext, db: Session) -> None:
    access = GroupAccessContext.for_group(db, group_id, auth)
    grant_repo = MarketGroupSkillGrantRepository(db)
    row = grant_repo.get_grant(group_id, asset_id)
    if not row or row.status == GRANT_STATUS_REVOKED:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "Grant not found", error="grant_not_found")
    asset = MarketAssetRepository(db).get_by_asset_id(asset_id)
    if not access.can_revoke_grant(asset):
        raise _http_exception(status.HTTP_403_FORBIDDEN, "Insufficient group permissions", error="permission_denied")
    try:
        grant_repo.set_status(
            row, status=GRANT_STATUS_REVOKED, operator_id=auth.acting_user_id, operator_name=auth.acting_user_name
        )
        db.flush()
        MarketGroupRepository(db).refresh_counts(group_id)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def list_group_grants_service(
    group_id: str, auth: AuthContext, db: Session, *, page: int, page_size: int, status_filter: str | None = None
) -> GroupSkillGrantListResponse:
    access = GroupAccessContext.for_group(db, group_id, auth)
    access.require_view()
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    safe_status = (
        status_filter
        if status_filter in (GRANT_STATUS_PENDING, GRANT_STATUS_ACTIVE, GRANT_STATUS_REJECTED, GRANT_STATUS_REVOKED)
        else None
    )
    # 非群主（含特权用户视作 owner）只能看 active 授权
    if safe_status != GRANT_STATUS_ACTIVE and not access.can_manage:
        safe_status = GRANT_STATUS_ACTIVE
    viewer = ViewerContext(
        user_id=auth.acting_user_id, user_login=auth.acting_user_name, is_system_admin=access.is_privileged
    )
    grant_repo = MarketGroupSkillGrantRepository(db)
    if safe_status == GRANT_STATUS_ACTIVE:
        rows, total = grant_repo.list_for_group_with_available_assets(
            group_id,
            viewer=viewer,
            page=safe_page,
            page_size=safe_size,
            status=safe_status,
        )
    else:
        rows, total = grant_repo.list_for_group(
            group_id, page=safe_page, page_size=safe_size, status=safe_status
        )
    asset_ids = [r.asset_id for r in rows]
    assets = db.query(MarketAssetDB).filter(MarketAssetDB.asset_id.in_(asset_ids)).all() if asset_ids else []
    asset_map = {a.asset_id: a for a in assets}
    granted_ids = grant_repo.asset_ids_granted_to_user(
        user_id=auth.acting_user_id, asset_ids=asset_ids
    )
    source_map: dict[str, str | None] = {}
    for asset in assets:
        is_visible = safe_status == GRANT_STATUS_ACTIVE or viewer.can_view_skill_asset(asset, db)
        source_map[asset.asset_id] = access.grant_access_source(
            asset, granted_ids=granted_ids, is_active_or_visible=is_visible
        )
    return GroupSkillGrantListResponse(
        page=safe_page,
        page_size=safe_size,
        total=total,
        items=[_grant_item(r, asset_map.get(r.asset_id), source_map.get(r.asset_id)) for r in rows],
    )


def user_has_group_skill_access(db: Session, *, user_id: str | None, asset_id: str) -> bool:
    return MarketGroupSkillGrantRepository(db).user_has_asset_grant(user_id=user_id or "", asset_id=asset_id)


def visible_group_granted_asset_ids(db: Session, *, user_id: str | None, asset_ids: list[str]) -> set[str]:
    return MarketGroupSkillGrantRepository(db).asset_ids_granted_to_user(user_id=user_id or "", asset_ids=asset_ids)


def list_my_group_skills_service(
    auth: AuthContext, db: Session, storage, *, page: int, page_size: int, keyword: str | None = None
) -> MyGroupSkillListResponse:
    safe_page = _page(page)
    safe_size = _page_size(page_size)
    viewer = ViewerContext(
        user_id=auth.acting_user_id, user_login=auth.acting_user_name, is_system_admin=_is_privileged(auth)
    )
    grant_rows, total = MarketGroupSkillGrantRepository(db).list_grants_for_user(
        user_id=auth.acting_user_id,
        page=safe_page,
        page_size=safe_size,
        keyword=keyword,
    )
    asset_ids = [row[0].asset_id for row in grant_rows]
    if not asset_ids:
        return MyGroupSkillListResponse(page=safe_page, page_size=safe_size, total=total, items=[])
    asset_rows = (
        db.query(MarketAssetDB)
        .filter(MarketAssetDB.asset_id.in_(asset_ids), MarketAssetDB.status != "OFFLINE")
        .outerjoin(
            MarketAssetVersionDB,
            and_(
                MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
                MarketAssetVersionDB.version == list_icon_version_join_expr(viewer, publisher_scoped=True),
            ),
        )
        .add_columns(MarketAssetVersionDB.file_path, MarketAssetVersionDB.has_icon)
        .all()
    )
    asset_map = {asset.asset_id: (asset, file_path, bool(has_icon)) for asset, file_path, has_icon in asset_rows}
    version_repo = MarketAssetVersionRepository(db)
    vrows = version_repo.list_all_by_asset_ids(list(asset_map.keys()))
    vmap: dict[str, list] = {}
    for row in vrows:
        vmap.setdefault(row.asset_id, []).append(row)
    # 计算每条授权对当前用户的可见来源：发布者(owner) / 群组成员(group) / 管理员(admin)
    # 注意：发布者身份优先于管理员身份——管理员自己发布的 skill 来源应为 owner，
    # 以便前端据此展示撤销授权入口（撤销依据是发布者身份，与管理员身份无关）。
    granted_ids = MarketGroupSkillGrantRepository(db).asset_ids_granted_to_user(
        user_id=auth.acting_user_id, asset_ids=asset_ids
    )
    is_privileged = _is_privileged(auth)
    source_map: dict[str, str | None] = {}
    for _aid, (asset, _fp, _icon) in asset_map.items():
        source_map[asset.asset_id] = GroupAccessContext.grant_access_source_for_user(
            auth, asset, is_privileged=is_privileged, granted_ids=granted_ids
        )
    items: list[MyGroupSkillItem] = []
    for grant, group_name in grant_rows:
        packed = asset_map.get(grant.asset_id)
        if not packed:
            continue
        asset, file_path, has_icon = packed
        skill = _list_item_from_asset(
            asset,
            file_path,
            has_icon,
            storage,
            vmap.get(asset.asset_id, []),
            viewer,
            market_public_scoped=False,
            db=db,
        )
        items.append(
            MyGroupSkillItem(
                group_id=grant.group_id,
                group_name=group_name or grant.group_id,
                skill=skill,
                viewer_access_source=source_map.get(grant.asset_id),
            )
        )
    return MyGroupSkillListResponse(page=safe_page, page_size=safe_size, total=total, items=items)
