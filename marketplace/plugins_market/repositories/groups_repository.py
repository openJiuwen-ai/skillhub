# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import time
from typing import Optional, TYPE_CHECKING

from sqlalchemy import and_, case, exists, func, or_
from sqlalchemy.orm import Session

from plugins_market.models.groups import (
    MarketGroupDB,
    MarketGroupJoinRequestDB,
    MarketGroupMemberDB,
    MarketGroupSkillGrantDB,
)
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.core.moderation import MODERATION_APPROVED
from plugins_market.core.publish_result import PUBLISH_RESULT_SUCCESS
from plugins_market.repositories.base_repository import MarketBaseRepository

if TYPE_CHECKING:
    from plugins_market.core.viewer_context import ViewerContext

GROUP_ROLE_OWNER = "owner"
GROUP_ROLE_MEMBER = "member"
GROUP_VISIBILITY_PRIVATE = "private"
GROUP_VISIBILITY_LISTED = "listed"
JOIN_STATUS_PENDING = "pending"
JOIN_STATUS_APPROVED = "approved"
JOIN_STATUS_REJECTED = "rejected"
GRANT_STATUS_PENDING = "pending"
GRANT_STATUS_ACTIVE = "active"
GRANT_STATUS_REJECTED = "rejected"
GRANT_STATUS_REVOKED = "revoked"


def now_ms() -> int:
    return int(time.time() * 1000)


def _skill_grant_publicly_visible_clause():
    """组群授权计入 skill_count 的审核可见条件，与 is_skill_asset_publicly_visible 对齐。

    新模型（publish_result / public_latest_version 非空）：要求 public_latest_version 非空，
    或存在任一已通过审核的版本行（资产级聚合可能滞后于版本级审批结果）。
    旧模型：moderation_status 为 APPROVED 或空（空值兼容旧数据视为已通过）。
    """
    new_model = or_(
        func.coalesce(MarketAssetDB.publish_result, "").op("!=")(""),
        func.coalesce(MarketAssetDB.public_latest_version, "").op("!=")(""),
    )
    new_model_ok = func.trim(func.coalesce(MarketAssetDB.public_latest_version, "")).op("!=")("")
    # 版本级兜底：资产级 public_latest_version 聚合滞后时，存在已通过审核的版本即计入
    approved_version_exists = (
        exists()
        .where(
            MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
            or_(
                MarketAssetVersionDB.moderation_status == MODERATION_APPROVED,
                MarketAssetVersionDB.publish_result == PUBLISH_RESULT_SUCCESS,
            ),
        )
        .correlate(MarketAssetDB)
    )
    legacy_ok = or_(
        MarketAssetDB.moderation_status == MODERATION_APPROVED,
        func.coalesce(MarketAssetDB.moderation_status, "").op("=")(""),
    )
    return or_(
        and_(new_model, or_(new_model_ok, approved_version_exists)),
        and_(~new_model, legacy_ok),
    )


class MarketGroupRepository(MarketBaseRepository[MarketGroupDB]):
    def __init__(self, db: Session):
        super().__init__(db, MarketGroupDB)

    def get_by_group_id(self, group_id: str) -> Optional[MarketGroupDB]:
        return self.filter_by(group_id=group_id).first()

    def count_by_owner(self, owner_id: str) -> int:
        return self.query().filter(MarketGroupDB.owner_id == owner_id, MarketGroupDB.status == "active").count()

    def list_for_user(
        self,
        user_id: str,
        keyword: str | None = None,
        *,
        page: int,
        page_size: int,
        role_filter: str | None = None,
        sort: str | None = None,
    ) -> tuple[list[tuple[MarketGroupDB, str]], int]:
        q = (
            self.db.query(MarketGroupDB, MarketGroupMemberDB.role)
            .join(MarketGroupMemberDB, MarketGroupMemberDB.group_id == MarketGroupDB.group_id)
            .filter(
                MarketGroupDB.status == "active",
                MarketGroupMemberDB.user_id == user_id,
            )
        )
        if keyword and keyword.strip():
            kw = keyword.strip()
            like = f"%{kw}%"
            # 关键字匹配 name/description/owner_name 模糊，或 group_id 精确命中（与 discover 保持一致）
            q = q.filter(
                or_(
                    MarketGroupDB.name.ilike(like),
                    MarketGroupDB.description.ilike(like),
                    MarketGroupDB.owner_name.ilike(like),
                    MarketGroupDB.group_id == kw,
                )
            )
        if role_filter in (GROUP_ROLE_OWNER, GROUP_ROLE_MEMBER):
            q = q.filter(MarketGroupMemberDB.role == role_filter)
        role_order = case((MarketGroupMemberDB.role == GROUP_ROLE_OWNER, 0), else_=1)
        if sort == "members":
            q = q.order_by(MarketGroupDB.member_count.desc(), MarketGroupDB.name.asc(), MarketGroupDB.group_id.asc())
        elif sort == "skills":
            q = q.order_by(MarketGroupDB.skill_count.desc(), MarketGroupDB.name.asc(), MarketGroupDB.group_id.asc())
        elif sort == "name":
            q = q.order_by(MarketGroupDB.name.asc(), MarketGroupDB.group_id.asc())
        else:
            q = q.order_by(role_order.asc(), MarketGroupDB.update_time.desc(), MarketGroupDB.group_id.asc())
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        return rows, total

    def discover(
        self,
        user_id: str,
        keyword: str | None,
        *,
        page: int,
        page_size: int,
        filter_by: str | None = None,
        sort: str | None = None,
        is_privileged: bool = False,
    ) -> tuple[list[tuple[MarketGroupDB, str | None, str | None]], int]:
        latest_join = (
            self.db.query(
                MarketGroupJoinRequestDB.group_id.label("group_id"),
                MarketGroupJoinRequestDB.status.label("status"),
                func.row_number()
                .over(
                    partition_by=MarketGroupJoinRequestDB.group_id,
                    order_by=(
                        MarketGroupJoinRequestDB.create_time.desc(),
                        MarketGroupJoinRequestDB.update_time.desc(),
                        MarketGroupJoinRequestDB.request_id.desc(),
                    ),
                )
                .label("rn"),
            )
            .filter(MarketGroupJoinRequestDB.user_id == user_id)
            .subquery()
        )
        q = (
            self.db.query(MarketGroupDB, MarketGroupMemberDB.role, latest_join.c.status)
            .outerjoin(
                MarketGroupMemberDB,
                and_(MarketGroupMemberDB.group_id == MarketGroupDB.group_id, MarketGroupMemberDB.user_id == user_id),
            )
            .outerjoin(latest_join, and_(latest_join.c.group_id == MarketGroupDB.group_id, latest_join.c.rn == 1))
            .filter(MarketGroupDB.status == "active")
        )
        # 系统管理员可发现所有组群（含 private）；普通用户仅发现 listed
        if not is_privileged:
            q = q.filter(MarketGroupDB.visibility == GROUP_VISIBILITY_LISTED)
        if keyword and keyword.strip():
            kw = keyword.strip()
            like = f"%{kw}%"
            # 关键字匹配 name/description 模糊，或 group_id 精确命中
            q = q.filter(
                or_(
                    MarketGroupDB.name.ilike(like),
                    MarketGroupDB.description.ilike(like),
                    MarketGroupDB.group_id == kw,
                )
            )
        if filter_by == "joined":
            q = q.filter(MarketGroupMemberDB.role.isnot(None))
        elif filter_by == "pending":
            q = q.filter(latest_join.c.status == JOIN_STATUS_PENDING)
        elif filter_by == "available":
            q = q.filter(
                MarketGroupMemberDB.role.is_(None),
                or_(latest_join.c.status.is_(None), latest_join.c.status != JOIN_STATUS_PENDING),
            )
        if sort == "members":
            q = q.order_by(MarketGroupDB.member_count.desc(), MarketGroupDB.name.asc(), MarketGroupDB.group_id.asc())
        elif sort == "skills":
            q = q.order_by(MarketGroupDB.skill_count.desc(), MarketGroupDB.name.asc(), MarketGroupDB.group_id.asc())
        elif sort == "name":
            q = q.order_by(MarketGroupDB.name.asc(), MarketGroupDB.group_id.asc())
        else:
            q = q.order_by(MarketGroupDB.update_time.desc(), MarketGroupDB.group_id.asc())
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        return rows, total

    def list_owned(self, owner_id: str, *, page: int, page_size: int) -> tuple[list[MarketGroupDB], int]:
        q = (
            self.query()
            .filter(MarketGroupDB.status == "active", MarketGroupDB.owner_id == owner_id)
            .order_by(MarketGroupDB.update_time.desc(), MarketGroupDB.group_id.asc())
        )
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        return rows, total

    def delete_group(self, group_id: str) -> int:
        return self.query().filter(MarketGroupDB.group_id == group_id).delete(synchronize_session=False)

    def refresh_counts(self, group_id: str) -> None:
        member_count = (
            self.db.query(func.count(MarketGroupMemberDB.id)).filter(MarketGroupMemberDB.group_id == group_id).scalar()
            or 0
        )
        version_exists = (
            self.db.query(MarketAssetVersionDB.version_id)
            .filter(MarketAssetVersionDB.asset_id == MarketGroupSkillGrantDB.asset_id)
            .exists()
        )
        skill_count = (
            self.db.query(func.count(func.distinct(MarketGroupSkillGrantDB.id)))
            .join(MarketAssetDB, MarketAssetDB.asset_id == MarketGroupSkillGrantDB.asset_id)
            .filter(
                MarketGroupSkillGrantDB.group_id == group_id,
                MarketGroupSkillGrantDB.status == GRANT_STATUS_ACTIVE,
                MarketAssetDB.status == "PUBLISHED",
                version_exists,
                # 仅计入已通过审核的 skill，使 skill_count 与 ACL 可见列表一致
                _skill_grant_publicly_visible_clause(),
            )
            .scalar()
            or 0
        )
        self.query().filter(MarketGroupDB.group_id == group_id).update(
            {
                MarketGroupDB.member_count: int(member_count),
                MarketGroupDB.skill_count: int(skill_count),
                MarketGroupDB.update_time: now_ms(),
            },
            synchronize_session=False,
        )


class MarketGroupMemberRepository(MarketBaseRepository[MarketGroupMemberDB]):
    def __init__(self, db: Session):
        super().__init__(db, MarketGroupMemberDB)

    def get_member(self, group_id: str, user_id: str) -> Optional[MarketGroupMemberDB]:
        return self.filter_by(group_id=group_id, user_id=user_id).first()

    def count_members(self, group_id: str) -> int:
        return self.query().filter(MarketGroupMemberDB.group_id == group_id).count()

    def list_members(self, group_id: str, *, page: int, page_size: int) -> tuple[list[MarketGroupMemberDB], int]:
        q = (
            self.query()
            .filter(MarketGroupMemberDB.group_id == group_id)
            .order_by(MarketGroupMemberDB.create_time.asc(), MarketGroupMemberDB.id.asc())
        )
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        return rows, total

    def upsert_member(self, *, group_id: str, user_id: str, user_name: str | None, role: str) -> MarketGroupMemberDB:
        ts = now_ms()
        row = self.get_member(group_id, user_id)
        if row:
            row.user_name = user_name
            row.role = role
            row.update_time = ts
            self.db.add(row)
            return row
        row = MarketGroupMemberDB(
            group_id=group_id,
            user_id=user_id,
            user_name=user_name,
            role=role,
            create_time=ts,
            update_time=ts,
        )
        self.db.add(row)
        return row

    def remove_member(self, group_id: str, user_id: str) -> int:
        return (
            self.query()
            .filter(MarketGroupMemberDB.group_id == group_id, MarketGroupMemberDB.user_id == user_id)
            .delete(synchronize_session=False)
        )

    def delete_by_group(self, group_id: str) -> int:
        return self.query().filter(MarketGroupMemberDB.group_id == group_id).delete(synchronize_session=False)

    def member_group_ids_for_user(self, user_id: str) -> list[str]:
        rows = self.db.query(MarketGroupMemberDB.group_id).filter(MarketGroupMemberDB.user_id == user_id).all()
        return [str(r[0]) for r in rows if r[0]]

    def member_user_ids_for_group(self, group_id: str, *, exclude_user_id: str | None = None) -> list[str]:
        q = self.db.query(MarketGroupMemberDB.user_id).filter(MarketGroupMemberDB.group_id == group_id)
        if exclude_user_id:
            q = q.filter(MarketGroupMemberDB.user_id != exclude_user_id)
        rows = q.order_by(MarketGroupMemberDB.id.asc()).all()
        return [str(r[0]) for r in rows if r[0]]

    def owner_user_ids_for_group(self, group_id: str, *, exclude_user_id: str | None = None) -> list[str]:
        q = self.db.query(MarketGroupMemberDB.user_id).filter(
            MarketGroupMemberDB.group_id == group_id,
            MarketGroupMemberDB.role == GROUP_ROLE_OWNER,
        )
        if exclude_user_id:
            q = q.filter(MarketGroupMemberDB.user_id != exclude_user_id)
        rows = q.order_by(MarketGroupMemberDB.id.asc()).all()
        return [str(r[0]) for r in rows if r[0]]


class MarketGroupJoinRequestRepository(MarketBaseRepository[MarketGroupJoinRequestDB]):
    def __init__(self, db: Session):
        super().__init__(db, MarketGroupJoinRequestDB)

    def get_by_request_id(self, request_id: str) -> Optional[MarketGroupJoinRequestDB]:
        return self.filter_by(request_id=request_id).first()

    def get_pending(self, group_id: str, user_id: str) -> Optional[MarketGroupJoinRequestDB]:
        return self.filter_by(group_id=group_id, user_id=user_id, status=JOIN_STATUS_PENDING).first()

    def latest_for_user(self, group_id: str, user_id: str) -> Optional[MarketGroupJoinRequestDB]:
        return (
            self.query()
            .filter(MarketGroupJoinRequestDB.group_id == group_id, MarketGroupJoinRequestDB.user_id == user_id)
            .order_by(MarketGroupJoinRequestDB.create_time.desc())
            .first()
        )

    def list_for_group(
        self, group_id: str, *, page: int, page_size: int, status: str | None = None
    ) -> tuple[list[MarketGroupJoinRequestDB], int]:
        q = self.query().filter(MarketGroupJoinRequestDB.group_id == group_id)
        if status:
            q = q.filter(MarketGroupJoinRequestDB.status == status)
        q = q.order_by(MarketGroupJoinRequestDB.create_time.desc())
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        return rows, total

    def delete_by_group(self, group_id: str) -> int:
        return self.query().filter(MarketGroupJoinRequestDB.group_id == group_id).delete(synchronize_session=False)

    def pending_user_ids_for_group(self, group_id: str) -> list[str]:
        """组群下所有处于 pending 的申请人 user_id（去重），用于删除组群前抓取待通知对象。"""
        rows = (
            self.db.query(MarketGroupJoinRequestDB.user_id)
            .filter(
                MarketGroupJoinRequestDB.group_id == group_id,
                MarketGroupJoinRequestDB.status == JOIN_STATUS_PENDING,
            )
            .distinct()
            .all()
        )
        return [str(r[0]) for r in rows if r[0]]

    def delete_by_group_and_user(self, group_id: str, user_id: str) -> int:
        return (
            self.query()
            .filter(
                MarketGroupJoinRequestDB.group_id == group_id,
                MarketGroupJoinRequestDB.user_id == user_id,
            )
            .delete(synchronize_session=False)
        )


class MarketGroupSkillGrantRepository(MarketBaseRepository[MarketGroupSkillGrantDB]):
    def __init__(self, db: Session):
        super().__init__(db, MarketGroupSkillGrantDB)

    def get_grant(self, group_id: str, asset_id: str) -> Optional[MarketGroupSkillGrantDB]:
        return self.filter_by(group_id=group_id, asset_id=asset_id).first()

    def grants_for_assets(self, group_id: str, asset_ids: list[str]) -> list[MarketGroupSkillGrantDB]:
        if not asset_ids:
            return []
        return (
            self.query()
            .filter(MarketGroupSkillGrantDB.group_id == group_id, MarketGroupSkillGrantDB.asset_id.in_(asset_ids))
            .all()
        )

    def list_for_group(
        self, group_id: str, *, page: int, page_size: int, status: str | None = None
    ) -> tuple[list[MarketGroupSkillGrantDB], int]:
        q = self.query().filter(MarketGroupSkillGrantDB.group_id == group_id)
        if status:
            q = q.filter(MarketGroupSkillGrantDB.status == status)
        q = q.order_by(MarketGroupSkillGrantDB.create_time.desc(), MarketGroupSkillGrantDB.id.desc())
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        return rows, total

    def list_for_group_with_available_assets(
        self,
        group_id: str,
        *,
        viewer: "ViewerContext",
        page: int,
        page_size: int,
        status: str | None = None,
    ) -> tuple[list[MarketGroupSkillGrantDB], int]:
        from plugins_market.repositories.market_assets_repository import skill_moderation_list_clause

        version_exists = (
            self.db.query(MarketAssetVersionDB.version_id)
            .filter(MarketAssetVersionDB.asset_id == MarketGroupSkillGrantDB.asset_id)
            .exists()
        )
        q = (
            self.query()
            .join(MarketAssetDB, MarketAssetDB.asset_id == MarketGroupSkillGrantDB.asset_id)
            .filter(MarketGroupSkillGrantDB.group_id == group_id, MarketAssetDB.status == "PUBLISHED", version_exists)
        )
        if status:
            q = q.filter(MarketGroupSkillGrantDB.status == status)
        mod_clause = skill_moderation_list_clause(
            viewer, publisher_scoped=True, moderation_queue_scoped=True
        )
        if mod_clause is not None:
            q = q.filter(mod_clause)
        q = q.order_by(MarketGroupSkillGrantDB.create_time.desc(), MarketGroupSkillGrantDB.id.desc())
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        return rows, total

    def asset_ids_for_group_by_statuses(self, group_id: str, statuses: list[str]) -> list[str]:
        if not statuses:
            return []
        rows = (
            self.db.query(MarketGroupSkillGrantDB.asset_id)
            .filter(MarketGroupSkillGrantDB.group_id == group_id, MarketGroupSkillGrantDB.status.in_(statuses))
            .all()
        )
        return [str(row[0]) for row in rows if row[0]]

    def publisher_ids_for_group(self, group_id: str, statuses: list[str]) -> list[str]:
        """组群下指定状态授权所涉 Skill 的发布者 publisher_id（去重），用于删除组群前抓取待通知对象。"""
        if not statuses:
            return []
        rows = (
            self.db.query(MarketAssetDB.publisher_id)
            .join(MarketGroupSkillGrantDB, MarketGroupSkillGrantDB.asset_id == MarketAssetDB.asset_id)
            .filter(
                MarketGroupSkillGrantDB.group_id == group_id,
                MarketGroupSkillGrantDB.status.in_(statuses),
            )
            .distinct()
            .all()
        )
        return [str(r[0]) for r in rows if r[0]]

    def delete_grant(self, group_id: str, asset_id: str) -> int:
        return (
            self.query()
            .filter(MarketGroupSkillGrantDB.group_id == group_id, MarketGroupSkillGrantDB.asset_id == asset_id)
            .delete(synchronize_session=False)
        )

    def set_status(
        self, row: MarketGroupSkillGrantDB, *, status: str, operator_id: str | None, operator_name: str | None
    ) -> MarketGroupSkillGrantDB:
        row.status = status
        row.operator_id = operator_id
        row.operator_name = operator_name
        row.update_time = now_ms()
        self.db.add(row)
        return row

    def delete_by_group(self, group_id: str) -> int:
        return self.query().filter(MarketGroupSkillGrantDB.group_id == group_id).delete(synchronize_session=False)

    def delete_by_asset(self, asset_id: str) -> int:
        return self.query().filter(MarketGroupSkillGrantDB.asset_id == asset_id).delete(synchronize_session=False)

    def user_has_asset_grant(self, *, user_id: str, asset_id: str) -> bool:
        if not user_id:
            return False
        row = (
            self.db.query(MarketGroupSkillGrantDB.id)
            .join(MarketGroupMemberDB, MarketGroupMemberDB.group_id == MarketGroupSkillGrantDB.group_id)
            .join(MarketGroupDB, MarketGroupDB.group_id == MarketGroupSkillGrantDB.group_id)
            .join(MarketAssetDB, MarketAssetDB.asset_id == MarketGroupSkillGrantDB.asset_id)
            .filter(
                MarketGroupSkillGrantDB.asset_id == asset_id,
                MarketGroupSkillGrantDB.status == GRANT_STATUS_ACTIVE,
                MarketGroupMemberDB.user_id == user_id,
                MarketGroupDB.status == "active",
                MarketAssetDB.status != "OFFLINE",
            )
            .limit(1)
            .first()
        )
        return row is not None

    def asset_ids_granted_to_user(self, *, user_id: str, asset_ids: list[str]) -> set[str]:
        if not user_id or not asset_ids:
            return set()
        rows = (
            self.db.query(MarketGroupSkillGrantDB.asset_id)
            .join(MarketGroupMemberDB, MarketGroupMemberDB.group_id == MarketGroupSkillGrantDB.group_id)
            .join(MarketGroupDB, MarketGroupDB.group_id == MarketGroupSkillGrantDB.group_id)
            .join(MarketAssetDB, MarketAssetDB.asset_id == MarketGroupSkillGrantDB.asset_id)
            .filter(
                MarketGroupSkillGrantDB.asset_id.in_(asset_ids),
                MarketGroupSkillGrantDB.status == GRANT_STATUS_ACTIVE,
                MarketGroupMemberDB.user_id == user_id,
                MarketGroupDB.status == "active",
                MarketAssetDB.status != "OFFLINE",
            )
            .distinct()
            .all()
        )
        return {str(r[0]) for r in rows if r[0]}

    def list_grants_for_user(self, *, user_id: str, page: int, page_size: int, keyword: str | None = None):
        if not user_id:
            return [], 0
        version_exists = (
            self.db.query(MarketAssetVersionDB.version_id)
            .filter(MarketAssetVersionDB.asset_id == MarketGroupSkillGrantDB.asset_id)
            .exists()
        )
        # 当前用户可见的授权：作为群组成员可用，或作为发布者授权出去的。
        # 成员关系用 LEFT JOIN，避免把发布者非成员的授权排除在外。
        q = (
            self.db.query(MarketGroupSkillGrantDB, MarketGroupDB.name.label("group_name"))
            .outerjoin(
                MarketGroupMemberDB,
                and_(
                    MarketGroupMemberDB.group_id == MarketGroupSkillGrantDB.group_id,
                    MarketGroupMemberDB.user_id == user_id,
                ),
            )
            .join(MarketGroupDB, MarketGroupDB.group_id == MarketGroupSkillGrantDB.group_id)
            .join(MarketAssetDB, MarketAssetDB.asset_id == MarketGroupSkillGrantDB.asset_id)
            .filter(
                MarketGroupSkillGrantDB.status == GRANT_STATUS_ACTIVE,
                or_(
                    MarketGroupMemberDB.user_id == user_id,
                    MarketAssetDB.publisher_id == user_id,
                ),
                MarketGroupDB.status == "active",
                MarketAssetDB.status == "PUBLISHED",
                version_exists,
            )
        )
        if keyword and keyword.strip():
            like = f"%{keyword.strip()}%"
            q = q.filter(
                or_(
                    MarketAssetDB.name.ilike(like),
                    MarketAssetDB.display_name.ilike(like),
                    MarketAssetDB.asset_id.ilike(like),
                    MarketGroupDB.name.ilike(like),
                )
            )
        q = q.order_by(MarketGroupSkillGrantDB.create_time.desc(), MarketGroupSkillGrantDB.id.desc())
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        return rows, total
