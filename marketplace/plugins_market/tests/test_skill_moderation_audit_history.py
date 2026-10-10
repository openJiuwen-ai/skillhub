"""回归：用户自审自己发布的 Skill 报错后，审核历史不得把失败补录误显示为 APPROVE。

背景（Bug #3）：moderate_skill_asset_service 在 publisher_id == acting_user_id 时抛
BusinessError(self_moderation_forbidden, 403)。该 403 经 business_error_handler ->
audit_failed_mutation 写一条 result=FAILED、action=MODERATE、resource_version=None 的
兜底审计行（见 audit_events.Action.MODERATE 注释）。修复前，审核历史查询不过滤
result，且展示层把非 REJECT 的 action 一律映射成 APPROVE，导致这条"从未发生的审核"
被显示成绿色"通过"，且因 resource_version=None 被渲染成 version="-"，点击查看详情
会跳转到不存在的版本而 404。修复：审核历史只返回 result=SUCCESS（已生效的真实决定）。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.audit import AuditLog
from plugins_market.core.audit_events import (
    EVENT_SKILL_MODERATION,
    Action,
    Result,
)
from plugins_market.core.auth import AuthContext
from plugins_market.core.errors import BusinessError
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB
from plugins_market.services.plugin import (
    list_my_skill_moderation_audits_service,
    moderate_skill_asset_service,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_cls = sessionmaker(bind=engine, autoflush=False)
    return session_cls()


def _mod_admin(user_id="admin1", name="admin1"):
    return AuthContext(
        is_admin=False,
        acting_user_id=user_id,
        acting_user_name=name,
        is_market_moderation_admin=True,
    )


def _skill(asset_id="skill-self", publisher_id="admin1", plugin_type="skill"):
    return MarketAssetDB(
        asset_id=asset_id,
        asset_type="plugin",
        name=asset_id,
        display_name=asset_id,
        publisher_id=publisher_id,
        publisher_name=publisher_id,
        status="PUBLISHED",
        plugin_type=plugin_type,
        latest_version="1.0.0",
        public_latest_version="1.0.0",
        moderation_status="PENDING",
        view_count=0,
        install_count=0,
        like_count=0,
        star_count=0,
        review_count=0,
        average_rating=8.0,
        create_time=1,
        update_time=1,
    )


def _audit_row(
    *,
    row_id: int,
    event_id: str,
    action: str,
    result: str,
    operator_id: str,
    resource_id: str,
    resource_version,
    detail: str,
    extra: dict,
    created_at: datetime,
) -> AuditLog:
    # 显式给 id：audit_logs.id 是 MySQL BIGINT(unsigned) autoincrement，
    # SQLite 不对 BIGINT 做 rowid 自增，必须手动赋值。
    return AuditLog(
        id=row_id,
        request_id="req-test",
        event_id=event_id,
        event_type=EVENT_SKILL_MODERATION,
        action=action,
        operator_id=operator_id,
        operator_name=operator_id,
        resource_type="skill",
        resource_id=resource_id,
        resource_version=resource_version,
        result=result,
        duration_ms=10,
        detail=detail,
        ip_address="127.0.0.1",
        user_agent="pytest",
        extra=extra,
        created_at=created_at,
    )


def test_failed_moderation_audit_excluded_from_review_history():
    db = _db()
    admin = _mod_admin()
    base = datetime(2026, 1, 1, 12, 0, 0)

    # 失败补录：模拟 self_moderation_forbidden 经 audit_failed_mutation 写入的兜底行
    failed_row = _audit_row(
        row_id=1,
        event_id="evt-failed-self-moderation",
        action=Action.MODERATE,
        result=Result.FAILED,
        operator_id=admin.acting_user_id,
        resource_id="skill-self",
        resource_version=None,
        detail="POST /api/v1/plugins/skill-self/moderation 失败：审核员不能审核自己发布的 Skill",
        extra={},
        created_at=base,
    )
    # 真实通过：成功路径在 db.commit() 之后写入
    success_row = _audit_row(
        row_id=2,
        event_id="evt-approve-other",
        action=Action.APPROVE,
        result=Result.SUCCESS,
        operator_id=admin.acting_user_id,
        resource_id="skill-other",
        resource_version="1.0.0",
        detail="审核通过 Skill「skill-other」(skill-other) v1.0.0",
        extra={
            "skill_name": "skill-other",
            "skill_display_name": "skill-other",
            "reject_reason": None,
        },
        created_at=base + timedelta(seconds=1),
    )
    db.add_all([failed_row, success_row])
    db.commit()

    resp = list_my_skill_moderation_audits_service(
        auth=admin, db=db, page=1, page_size=20
    )

    assert resp.total == 1
    assert len(resp.items) == 1
    item = resp.items[0]
    assert item.event_id == "evt-approve-other"
    assert item.asset_id == "skill-other"
    assert item.moderation_action == "APPROVE"
    assert item.version == "1.0.0"
    assert item.reject_reason is None


def test_moderate_own_skill_raises_self_moderation_forbidden():
    db = _db()
    admin = _mod_admin()  # acting_user_id == publisher_id == "admin1"
    db.add(_skill(asset_id="skill-self", publisher_id="admin1"))
    db.commit()

    with pytest.raises(BusinessError) as exc:
        moderate_skill_asset_service(
            asset_id="skill-self",
            action="approve",
            reason=None,
            version="1.0.0",
            auth=admin,
            db=db,
            storage=None,  # 自审在触及 storage 前即抛出
        )

    assert exc.value.error == "self_moderation_forbidden"
    assert exc.value.status_code == 403
    # 主事务未提交，故不会产生 result=SUCCESS 的审核行
    assert (
        db.query(AuditLog).filter(AuditLog.result == Result.SUCCESS).count() == 0
    )
