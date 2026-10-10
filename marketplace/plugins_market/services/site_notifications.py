# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""站内审核相关通知：固定模板 + 写入时按收件箱淘汰至 10 条。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from plugins_market.core.review_admins import get_review_admin_usernames
from plugins_market.core.auth import AuthContext
from plugins_market.core.logging import get_logger
from plugins_market.repositories.site_notification_repository import (
    count_unread,
    insert_and_trim,
    inbox_key_for_login,
    inbox_key_for_user_id,
    list_for_inbox_keys,
    mark_all_read,
)
from plugins_market.schemas.site_notifications import (
    SiteNotificationItem,
    SiteNotificationListData,
)

logger = get_logger(__name__)

NOTIFICATION_TEMPLATE_ADMIN_PENDING = "admin_pending"
NOTIFICATION_TEMPLATE_USER_REVIEW_DONE = "user_review_done"
NOTIFICATION_TEMPLATE_USER_MANUAL_REVIEW_APPROVED = "user_manual_review_approved"
NOTIFICATION_TEMPLATE_USER_MANUAL_REVIEW_REJECTED = "user_manual_review_rejected"
NOTIFICATION_TEMPLATE_USER_SYSTEM_REVIEW_FAILED = "user_system_review_failed"
NOTIFICATION_TEMPLATE_GROUP_SKILL_GRANT_PENDING = "group_skill_grant_pending"
NOTIFICATION_TEMPLATE_GROUP_SKILL_GRANT_APPROVED = "group_skill_grant_approved"
NOTIFICATION_TEMPLATE_GROUP_SKILL_GRANT_REJECTED = "group_skill_grant_rejected"
NOTIFICATION_TEMPLATE_GROUP_DELETED_MEMBER = "group_deleted_member"
NOTIFICATION_TEMPLATE_GROUP_DELETED_APPLICANT = "group_deleted_applicant"
NOTIFICATION_TEMPLATE_GROUP_DELETED_PUBLISHER = "group_deleted_publisher"

MSG_ADMIN_PENDING = "您有新的待审核内容，请前往个人中心查看。"
MSG_USER_REVIEW_DONE = "您的 skill 已审核完成，请前往个人中心查看。"
MSG_USER_MANUAL_REVIEW_APPROVED = "您的 skill 已通过审核，请前往个人中心查看。"
MSG_USER_MANUAL_REVIEW_REJECTED = "您的 skill 未通过审核，请前往个人中心查看。"
MSG_USER_SYSTEM_REVIEW_FAILED = "您的 skill 未通过审查，请前往个人中心查看。"
MSG_GROUP_SKILL_GRANT_PENDING = "组群有新的 skill 授权待审批，请前往组群详情查看。"
MSG_GROUP_SKILL_GRANT_APPROVED = "您的 skill 授权申请已通过，组群成员现可访问。"
MSG_GROUP_SKILL_GRANT_REJECTED = "您的 skill 授权申请未通过。"
MSG_GROUP_DELETED_MEMBER = "您所在的组群已被删除，您将不再享有该组群内 Skill 的访问权。"
MSG_GROUP_DELETED_APPLICANT = "您申请加入的组群已被删除，加入申请已自动失效。"
MSG_GROUP_DELETED_PUBLISHER = "您的 Skill 授权所在的组群已被删除，相关授权已自动失效。"


def message_for_template(template: str) -> str:
    if template == NOTIFICATION_TEMPLATE_ADMIN_PENDING:
        return MSG_ADMIN_PENDING
    if template == NOTIFICATION_TEMPLATE_USER_REVIEW_DONE:
        return MSG_USER_REVIEW_DONE
    if template == NOTIFICATION_TEMPLATE_USER_MANUAL_REVIEW_APPROVED:
        return MSG_USER_MANUAL_REVIEW_APPROVED
    if template == NOTIFICATION_TEMPLATE_USER_MANUAL_REVIEW_REJECTED:
        return MSG_USER_MANUAL_REVIEW_REJECTED
    if template == NOTIFICATION_TEMPLATE_USER_SYSTEM_REVIEW_FAILED:
        return MSG_USER_SYSTEM_REVIEW_FAILED
    if template == NOTIFICATION_TEMPLATE_GROUP_SKILL_GRANT_PENDING:
        return MSG_GROUP_SKILL_GRANT_PENDING
    if template == NOTIFICATION_TEMPLATE_GROUP_SKILL_GRANT_APPROVED:
        return MSG_GROUP_SKILL_GRANT_APPROVED
    if template == NOTIFICATION_TEMPLATE_GROUP_SKILL_GRANT_REJECTED:
        return MSG_GROUP_SKILL_GRANT_REJECTED
    if template == NOTIFICATION_TEMPLATE_GROUP_DELETED_MEMBER:
        return MSG_GROUP_DELETED_MEMBER
    if template == NOTIFICATION_TEMPLATE_GROUP_DELETED_APPLICANT:
        return MSG_GROUP_DELETED_APPLICANT
    if template == NOTIFICATION_TEMPLATE_GROUP_DELETED_PUBLISHER:
        return MSG_GROUP_DELETED_PUBLISHER
    return ""


def _inbox_keys_for_context(auth: AuthContext) -> List[str]:
    keys: List[str] = []
    uid = (auth.acting_user_id or "").strip()
    if uid:
        keys.append(inbox_key_for_user_id(uid))
    name = (auth.acting_user_name or "").strip()
    if name:
        lg = inbox_key_for_login(name)
        if lg and lg not in keys:
            keys.append(lg)
    return keys


def list_notifications(
    db: Session,
    auth: AuthContext,
) -> SiteNotificationListData:
    keys = _inbox_keys_for_context(auth)
    rows = list_for_inbox_keys(db, keys, limit=10)
    unread = count_unread(db, keys)
    items = [
        SiteNotificationItem(
            id=int(r.id),
            template=str(r.template),
            message=message_for_template(str(r.template)),
            created_at_ms=int(r.created_at_ms or 0),
            read=bool(r.read_at_ms is not None),
        )
        for r in rows
    ]
    return SiteNotificationListData(items=items, unread_count=unread)


def mark_notifications_read_all(db: Session, auth: AuthContext) -> int:
    """将当前用户相关收件箱内未读标为已读，并 commit（否则仅有 GET 的会话关闭时会回滚）。"""
    at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    n = mark_all_read(db, _inbox_keys_for_context(auth), at_ms=at_ms)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        raise
    return n


def notify_review_admins_new_skill_submission(db: Session) -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    any_ins = False
    try:
        for login in get_review_admin_usernames():
            lg = (login or "").strip()
            if not lg:
                continue
            key = inbox_key_for_login(lg)
            insert_and_trim(
                db,
                inbox_key=key,
                template=NOTIFICATION_TEMPLATE_ADMIN_PENDING,
                created_at_ms=now_ms,
            )
            any_ins = True
        if any_ins:
            db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.exception("admin pending notification failed: %s", e)


def _notify_user_ids(db: Session, *, user_ids: list[str], template: str, failure_log: str) -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    any_ins = False
    try:
        with db.begin_nested():
            for user_id in user_ids:
                uid = (user_id or "").strip()
                if not uid:
                    continue
                insert_and_trim(
                    db,
                    inbox_key=inbox_key_for_user_id(uid),
                    template=template,
                    created_at_ms=now_ms,
                )
                any_ins = True
        if any_ins:
            db.commit()
    except SQLAlchemyError as e:
        logger.exception("%s: %s", failure_log, e)


def notify_group_owners_skill_grant_pending(db: Session, *, owner_user_ids: list[str]) -> None:
    _notify_user_ids(
        db,
        user_ids=owner_user_ids,
        template=NOTIFICATION_TEMPLATE_GROUP_SKILL_GRANT_PENDING,
        failure_log="group skill-grant-pending notification failed",
    )


def notify_publisher_skill_grant_approved(db: Session, *, publisher_id: str) -> None:
    _notify_user_ids(
        db,
        user_ids=[publisher_id],
        template=NOTIFICATION_TEMPLATE_GROUP_SKILL_GRANT_APPROVED,
        failure_log="group skill-grant-approved notification failed",
    )


def notify_publisher_skill_grant_rejected(db: Session, *, publisher_id: str) -> None:
    _notify_user_ids(
        db,
        user_ids=[publisher_id],
        template=NOTIFICATION_TEMPLATE_GROUP_SKILL_GRANT_REJECTED,
        failure_log="group skill-grant-rejected notification failed",
    )


def notify_group_deleted_members(db: Session, *, user_ids: list[str]) -> None:
    _notify_user_ids(
        db,
        user_ids=user_ids,
        template=NOTIFICATION_TEMPLATE_GROUP_DELETED_MEMBER,
        failure_log="group-deleted-member notification failed",
    )


def notify_group_deleted_applicants(db: Session, *, user_ids: list[str]) -> None:
    _notify_user_ids(
        db,
        user_ids=user_ids,
        template=NOTIFICATION_TEMPLATE_GROUP_DELETED_APPLICANT,
        failure_log="group-deleted-applicant notification failed",
    )


def notify_group_deleted_publishers(db: Session, *, user_ids: list[str]) -> None:
    _notify_user_ids(
        db,
        user_ids=user_ids,
        template=NOTIFICATION_TEMPLATE_GROUP_DELETED_PUBLISHER,
        failure_log="group-deleted-publisher notification failed",
    )


def notify_publisher_skill_manual_review_approved(db: Session, *, publisher_id: str) -> None:
    uid = (publisher_id or "").strip()
    if not uid:
        return
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    key = inbox_key_for_user_id(uid)
    try:
        insert_and_trim(
            db,
            inbox_key=key,
            template=NOTIFICATION_TEMPLATE_USER_MANUAL_REVIEW_APPROVED,
            created_at_ms=now_ms,
        )
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.exception("user manual-review-approved notification failed publisher=%s: %s", uid, e)


def notify_publisher_skill_manual_review_rejected(db: Session, *, publisher_id: str) -> None:
    uid = (publisher_id or "").strip()
    if not uid:
        return
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    key = inbox_key_for_user_id(uid)
    try:
        insert_and_trim(
            db,
            inbox_key=key,
            template=NOTIFICATION_TEMPLATE_USER_MANUAL_REVIEW_REJECTED,
            created_at_ms=now_ms,
        )
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.exception("user manual-review-rejected notification failed publisher=%s: %s", uid, e)


def notify_publisher_skill_system_review_failed(db: Session, *, publisher_id: str) -> None:
    uid = (publisher_id or "").strip()
    if not uid:
        return
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    key = inbox_key_for_user_id(uid)
    try:
        insert_and_trim(
            db,
            inbox_key=key,
            template=NOTIFICATION_TEMPLATE_USER_SYSTEM_REVIEW_FAILED,
            created_at_ms=now_ms,
        )
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.exception("user system-review-failed notification failed publisher=%s: %s", uid, e)
