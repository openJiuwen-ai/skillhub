import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.auth import AuthContext
from plugins_market.models.base import Base
from plugins_market.models.groups import MarketGroupSkillGrantDB, MarketGroupJoinRequestDB
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.models.site_notifications import SiteNotificationDB
from plugins_market.schemas.group import (
    GroupCreateRequest,
    GroupJoinRequestCreate,
    GroupJoinRequestDecision,
    GroupMemberUpsertRequest,
    GroupSkillGrantRequest,
    GroupSkillGrantDecision,
    GroupUpdateRequest,
)
from plugins_market.services.groups import (
    create_group_service,
    create_join_request_service,
    decide_join_request_service,
    delete_group_service,
    discover_groups_service,
    get_group_service,
    grant_skill_to_group_service,
    decide_group_skill_grant_service,
    list_group_grants_service,
    list_group_members_service,
    list_join_requests_service,
    list_my_group_skills_service,
    list_my_groups_service,
    remove_group_member_service,
    revoke_skill_from_group_service,
    search_grantable_skills_service,
    update_group_service,
    upsert_group_member_service,
    user_has_group_skill_access,
    visible_group_granted_asset_ids,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    # 与生产 SessionLocal 一致使用 autoflush=False，使 refresh_counts 等「先 add 再查询」的
    # 路径在缺少显式 flush 时能被测试真实捕获（避免假阳性）。
    session_cls = sessionmaker(bind=engine, autoflush=False)
    return session_cls()


def _auth(user_id="owner", name="Owner"):
    return AuthContext(is_admin=False, acting_user_id=user_id, acting_user_name=name)


def _skill(asset_id="skill-1", publisher_id="owner"):
    return MarketAssetDB(
        asset_id=asset_id,
        asset_type="plugin",
        name=asset_id,
        display_name=asset_id,
        publisher_id=publisher_id,
        publisher_name=publisher_id,
        status="PUBLISHED",
        plugin_type="skill",
        latest_version="1.0.0",
        public_latest_version="1.0.0",
        moderation_status="APPROVED",
        view_count=0,
        install_count=0,
        like_count=0,
        star_count=0,
        review_count=0,
        average_rating=8.0,
        create_time=1,
        update_time=1,
    )


def test_group_create_adds_owner_member():
    db = _db()

    group = create_group_service(GroupCreateRequest(name="研发组"), _auth(), db)

    assert group.name == "研发组"
    assert group.viewer_role == "owner"
    assert group.member_count == 1


def test_join_request_approval_adds_member():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth(), db)
    req = create_join_request_service(group.group_id, GroupJoinRequestCreate(message="申请"), _auth("u2", "User2"), db)

    decided = decide_join_request_service(
        group.group_id,
        req.request_id,
        GroupJoinRequestDecision(status="approved"),
        _auth(),
        db,
    )

    members = list_group_members_service(group.group_id, _auth(), db, page=1, page_size=20)
    joined = next(item for item in members.items if item.user_id == "u2")

    assert decided.status == "approved"
    assert joined.role == "member"
    assert members.total == 2


def test_join_request_approval_is_idempotent_when_user_already_joined():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth(), db)
    first = create_join_request_service(
        group.group_id, GroupJoinRequestCreate(message="第一次"), _auth("u2", "User2"), db
    )
    decide_join_request_service(
        group.group_id, first.request_id, GroupJoinRequestDecision(status="approved"), _auth(), db
    )
    remove_group_member_service(group.group_id, "u2", _auth("u2", "User2"), db)
    second = create_join_request_service(
        group.group_id, GroupJoinRequestCreate(message="第二次"), _auth("u2", "User2"), db
    )

    decided = decide_join_request_service(
        group.group_id, second.request_id, GroupJoinRequestDecision(status="approved"), _auth(), db
    )

    assert decided.status == "approved"


def test_join_request_can_be_rejected_reapplied_and_rejected_again():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth(), db)
    first = create_join_request_service(
        group.group_id, GroupJoinRequestCreate(message="first"), _auth("u2", "User2"), db
    )
    decide_join_request_service(
        group.group_id, first.request_id, GroupJoinRequestDecision(status="rejected"), _auth(), db
    )
    second = create_join_request_service(
        group.group_id, GroupJoinRequestCreate(message="second"), _auth("u2", "User2"), db
    )

    decided = decide_join_request_service(
        group.group_id, second.request_id, GroupJoinRequestDecision(status="rejected"), _auth(), db
    )

    assert decided.status == "rejected"


def test_active_group_grant_cannot_be_rejected_via_decision_endpoint():
    db = _db()
    db.add(_skill(asset_id="external-skill", publisher_id="publisher"))
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth(), db)
    pending = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="external-skill"), _auth("publisher", "Publisher"), db
    )
    decide_group_skill_grant_service(
        group.group_id, pending.asset_id, GroupSkillGrantDecision(status="active"), _auth(), db
    )

    with pytest.raises(HTTPException) as exc_info:
        decide_group_skill_grant_service(
            group.group_id, pending.asset_id, GroupSkillGrantDecision(status="rejected"), _auth(), db
        )
    assert exc_info.value.status_code == 409

    assert user_has_group_skill_access(db, user_id="owner", asset_id="external-skill") is True


def test_member_can_leave_group_without_admin_permission():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth(), db)
    upsert_group_member_service(
        group.group_id, GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"), _auth(), db
    )

    remove_group_member_service(group.group_id, "u2", _auth("u2", "User2"), db)

    result = list_my_groups_service(_auth("u2", "User2"), db, page=1, page_size=20)
    assert result.total == 0


def test_owner_cannot_demote_self():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth("owner", "Owner"), db)

    with pytest.raises(HTTPException) as exc_info:
        upsert_group_member_service(
            group.group_id,
            GroupMemberUpsertRequest(user_id="owner", user_name="Owner", role="member"),
            _auth("owner", "Owner"),
            db,
        )
    assert exc_info.value.status_code == 400


def test_owner_cannot_leave_group_by_removing_self():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth(), db)

    try:
        remove_group_member_service(group.group_id, "owner", _auth(), db)
        assert False
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400


def test_list_my_groups_supports_keyword_search():
    db = _db()
    create_group_service(GroupCreateRequest(name="研发组", description="核心开发"), _auth(), db)
    create_group_service(GroupCreateRequest(name="运营组", description="内容运营"), _auth(), db)

    result = list_my_groups_service(_auth(), db, page=1, page_size=20, keyword="研发")

    assert result.total == 1
    assert result.items[0].name == "研发组"


def test_list_my_groups_orders_owner_before_member():
    db = _db()
    member_group = create_group_service(
        GroupCreateRequest(name="普通成员组"), _auth("member-owner", "Member Owner"), db
    )
    owner_group = create_group_service(GroupCreateRequest(name="所有者组"), _auth("u1", "User1"), db)
    upsert_group_member_service(
        member_group.group_id,
        GroupMemberUpsertRequest(user_id="u1", user_name="User1", role="member"),
        _auth("member-owner", "Member Owner"),
        db,
    )

    result = list_my_groups_service(_auth("u1", "User1"), db, page=1, page_size=20)

    assert [item.name for item in result.items] == ["所有者组", "普通成员组"]
    assert [item.viewer_role for item in result.items] == ["owner", "member"]


def test_publisher_grant_requires_group_admin_approval_before_access():
    db = _db()
    db.add(_skill(asset_id="external-skill", publisher_id="publisher"))
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth(), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"),
        _auth(),
        db,
    )

    pending = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="external-skill"), _auth("publisher", "Publisher"), db
    )

    assert pending.status == "pending"
    assert user_has_group_skill_access(db, user_id="u2", asset_id="external-skill") is False

    active = decide_group_skill_grant_service(
        group.group_id, "external-skill", GroupSkillGrantDecision(status="active"), _auth(), db
    )

    assert active.status == "active"
    assert user_has_group_skill_access(db, user_id="u2", asset_id="external-skill") is True


def test_non_member_publisher_can_request_grant_to_listed_group():
    db = _db()
    db.add(_skill(asset_id="external-skill", publisher_id="publisher"))
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )

    pending = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="external-skill"), _auth("publisher", "Publisher"), db
    )

    assert pending.status == "pending"


def test_active_group_grant_does_not_notify_group_members():
    db = _db()
    db.add(_skill(asset_id="alice-skill", publisher_id="alice"))
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth("alice", "Alice"), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="charlie", user_name="Charlie", role="member"),
        _auth("alice", "Alice"),
        db,
    )

    grant = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    assert grant.status == "active"
    rows = db.query(SiteNotificationDB).order_by(SiteNotificationDB.id.asc()).all()
    assert rows == []


def test_pending_group_grant_notifies_group_admins_except_actor():
    db = _db()
    db.add(_skill(asset_id="alice-skill", publisher_id="alice"))
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )

    pending = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    assert pending.status == "pending"
    rows = db.query(SiteNotificationDB).order_by(SiteNotificationDB.id.asc()).all()
    assert [(r.inbox_key, r.template) for r in rows] == [
        ("u:charlie", "group_skill_grant_pending")
    ]


def test_search_grantable_skills_marks_active_group_grants():
    db = _db()
    private_skill = _skill(asset_id="alice-skill", publisher_id="alice")
    private_skill.visibility = "private"
    private_other = _skill(asset_id="alice-other", publisher_id="alice")
    private_other.visibility = "private"
    db.add(private_skill)
    db.add(private_other)
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth("alice", "Alice"), db)
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    result = search_grantable_skills_service(
        _auth("alice", "Alice"), db, page=1, page_size=20, keyword=None, group_id=group.group_id
    )

    assert [item.asset_id for item in result.items] == ["alice-other", "alice-skill"]
    assert {item.asset_id: item.group_grant_status for item in result.items} == {
        "alice-other": None,
        "alice-skill": "active",
    }


def test_group_admin_can_withdraw_pending_group_grant_and_request_again():
    db = _db()
    private_skill = _skill(asset_id="alice-skill", publisher_id="alice")
    private_skill.visibility = "private"
    db.add(private_skill)
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )

    pending = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )
    revoke_skill_from_group_service(group.group_id, "alice-skill", _auth("charlie", "Charlie"), db)
    search = search_grantable_skills_service(
        _auth("alice", "Alice"), db, page=1, page_size=20, keyword=None, group_id=group.group_id
    )
    retry = grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    assert pending.status == "pending"
    assert {item.asset_id: item.group_grant_status for item in search.items} == {"alice-skill": None}
    assert retry.status == "pending"


def test_skill_publisher_can_revoke_own_group_grant():
    db = _db()
    private_skill = _skill(asset_id="alice-skill", publisher_id="alice")
    private_skill.visibility = "private"
    db.add(private_skill)
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    revoke_skill_from_group_service(group.group_id, "alice-skill", _auth("alice", "Alice"), db)
    search = search_grantable_skills_service(
        _auth("alice", "Alice"), db, page=1, page_size=20, keyword=None, group_id=group.group_id
    )

    assert {item.asset_id: item.group_grant_status for item in search.items} == {"alice-skill": None}


def test_search_grantable_skills_marks_pending_external_requests():
    db = _db()
    private_skill = _skill(asset_id="alice-skill", publisher_id="alice")
    private_skill.visibility = "private"
    private_other = _skill(asset_id="alice-other", publisher_id="alice")
    private_other.visibility = "private"
    db.add(private_skill)
    db.add(private_other)
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    result = search_grantable_skills_service(
        _auth("alice", "Alice"), db, page=1, page_size=20, keyword=None, group_id=group.group_id
    )

    assert [item.asset_id for item in result.items] == ["alice-other", "alice-skill"]
    assert {item.asset_id: item.group_grant_status for item in result.items} == {
        "alice-other": None,
        "alice-skill": "pending",
    }


def test_search_grantable_skills_keeps_rejected_group_grants():
    db = _db()
    private_skill = _skill(asset_id="alice-skill", publisher_id="alice")
    private_skill.visibility = "private"
    db.add(private_skill)
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )
    decide_group_skill_grant_service(
        group.group_id, "alice-skill", GroupSkillGrantDecision(status="rejected"), _auth("charlie", "Charlie"), db
    )

    result = search_grantable_skills_service(
        _auth("alice", "Alice"), db, page=1, page_size=20, keyword=None, group_id=group.group_id
    )

    assert [item.asset_id for item in result.items] == ["alice-skill"]


def test_approved_pending_group_grant_does_not_notify_group_members():
    db = _db()
    db.add(_skill(asset_id="publisher-skill", publisher_id="publisher"))
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth("alice", "Alice"), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="charlie", user_name="Charlie", role="member"),
        _auth("alice", "Alice"),
        db,
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="publisher-skill"), _auth("publisher", "Publisher"), db
    )

    active = decide_group_skill_grant_service(
        group.group_id, "publisher-skill", GroupSkillGrantDecision(status="active"), _auth("alice", "Alice"), db
    )

    assert active.status == "active"
    rows = db.query(SiteNotificationDB).order_by(SiteNotificationDB.id.asc()).all()
    assert [(r.inbox_key, r.template) for r in rows] == [
        ("u:alice", "group_skill_grant_pending"),
        ("u:publisher", "group_skill_grant_approved"),
    ]


def test_group_grants_hide_unavailable_assets():
    db = _db()
    asset = _skill(asset_id="alice-skill", publisher_id="alice")
    db.add(asset)
    db.add(MarketAssetVersionDB(version_id="v1", asset_id="alice-skill", version="1.0.0", create_time=1))
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth("alice", "Alice"), db)
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    asset.status = "OFFLINE"
    db.add(asset)
    db.commit()
    result = list_group_grants_service(
        group.group_id, _auth("alice", "Alice"), db, page=1, page_size=20, status_filter="active"
    )
    assert result.total == 0
    assert result.items == []

    asset.status = "PUBLISHED"
    db.query(MarketAssetVersionDB).filter(MarketAssetVersionDB.asset_id == "alice-skill").delete()
    db.add(asset)
    db.commit()
    result = list_group_grants_service(
        group.group_id, _auth("alice", "Alice"), db, page=1, page_size=20, status_filter="active"
    )
    assert result.total == 0
    assert result.items == []


def test_active_group_grants_filter_before_database_pagination():
    db = _db()
    unavailable = _skill(asset_id="unavailable", publisher_id="alice")
    unavailable.visibility = "private"
    available = _skill(asset_id="available", publisher_id="alice")
    available.visibility = "private"
    db.add_all([unavailable, available])
    db.add(MarketAssetVersionDB(version_id="available-v1", asset_id="available", version="1.0.0", create_time=1))
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth("alice", "Alice"), db)
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="available"), _auth("alice", "Alice"), db
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="unavailable"), _auth("alice", "Alice"), db
    )

    result = list_group_grants_service(
        group.group_id, _auth("alice", "Alice"), db, page=1, page_size=1, status_filter="active"
    )

    assert result.total == 1
    assert [item.asset_id for item in result.items] == ["available"]


def test_offline_asset_is_not_accessible_through_group_grant_acl():
    db = _db()
    asset = _skill(asset_id="alice-skill", publisher_id="alice")
    asset.visibility = "private"
    db.add(asset)
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth("alice", "Alice"), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"),
        _auth("alice", "Alice"),
        db,
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )

    assert user_has_group_skill_access(db, user_id="u2", asset_id="alice-skill") is True
    assert visible_group_granted_asset_ids(db, user_id="u2", asset_ids=["alice-skill"]) == {"alice-skill"}

    asset.status = "OFFLINE"
    db.add(asset)
    db.commit()

    assert user_has_group_skill_access(db, user_id="u2", asset_id="alice-skill") is False
    assert visible_group_granted_asset_ids(db, user_id="u2", asset_ids=["alice-skill"]) == set()


def test_group_delete_removes_grants_members_and_requests():
    db = _db()
    db.add(_skill())
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth(), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"),
        _auth(),
        db,
    )
    grant_skill_to_group_service(group.group_id, GroupSkillGrantRequest(asset_id="skill-1"), _auth(), db)

    delete_group_service(group.group_id, _auth(), db)

    assert db.query(MarketGroupSkillGrantDB).count() == 0
    assert user_has_group_skill_access(db, user_id="u2", asset_id="skill-1") is False


def test_group_delete_notifies_members_applicants_and_publishers():
    db = _db()
    db.add(_skill(asset_id="alice-skill", publisher_id="alice"))
    db.commit()
    owner = _auth("charlie", "Charlie")
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), owner, db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"),
        owner,
        db,
    )
    # alice 提交 skill 授权（pending），群主 charlie 审批通过（active）
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )
    decide_group_skill_grant_service(
        group.group_id, "alice-skill", GroupSkillGrantDecision(status="active"), owner, db
    )
    # bob 提交加入申请（pending）
    create_join_request_service(
        group.group_id, GroupJoinRequestCreate(message="申请加入"), _auth("bob", "Bob"), db
    )

    delete_group_service(group.group_id, owner, db)

    rows = db.query(SiteNotificationDB).all()
    deletion = {(r.inbox_key, r.template) for r in rows if r.template.startswith("group_deleted_")}
    # 三类受影响用户各收到对应失效通知
    assert ("u:u2", "group_deleted_member") in deletion
    assert ("u:bob", "group_deleted_applicant") in deletion
    assert ("u:alice", "group_deleted_publisher") in deletion
    # 执行删除的群主本人不收到「成员失效」通知
    assert ("u:charlie", "group_deleted_member") not in deletion
    # 群及关联数据已清除
    assert db.query(MarketGroupSkillGrantDB).count() == 0
    assert db.query(MarketGroupJoinRequestDB).count() == 0


def test_discover_shows_available_after_member_leaves():
    """用户退出组群后，发现列表应显示为可申请（join_request_status 为 None），而非已通过。"""
    db = _db()
    owner = _auth("owner", "Owner")
    member = _auth("u2", "User2")
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), owner, db)

    join_request = create_join_request_service(group.group_id, GroupJoinRequestCreate(message="申请加入"), member, db)
    decide_join_request_service(
        group.group_id, join_request.request_id, GroupJoinRequestDecision(status="approved"), owner, db
    )

    # 加入后退出
    remove_group_member_service(group.group_id, "u2", member, db)

    # 发现列表里该组群状态应为可申请（join_request_status 为 None）
    result = discover_groups_service(member, db, page=1, page_size=20, keyword=None)
    item = next(g for g in result.items if g.group_id == group.group_id)
    assert item.viewer_role is None
    assert item.join_request_status is None


def test_rejoin_shows_latest_join_request_after_repeated_join_leave():
    """反复加入退出后，群主的加入申请列表应显示最新一次申请记录，而非第一次。"""
    db = _db()
    owner = _auth("owner", "Owner")
    member = _auth("u2", "User2")
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), owner, db)

    # 第一次申请并加入
    req1 = create_join_request_service(group.group_id, GroupJoinRequestCreate(message="第一次"), member, db)
    decide_join_request_service(group.group_id, req1.request_id, GroupJoinRequestDecision(status="approved"), owner, db)

    # 退出
    remove_group_member_service(group.group_id, "u2", member, db)

    # 第二次申请并加入
    req2 = create_join_request_service(group.group_id, GroupJoinRequestCreate(message="第二次"), member, db)
    decide_join_request_service(group.group_id, req2.request_id, GroupJoinRequestDecision(status="approved"), owner, db)

    # 群主查看加入申请列表，应只有一条记录（最新的），message 为"第二次"
    result = list_join_requests_service(group.group_id, owner, db, page=1, page_size=20, status_filter=None)
    assert result.total == 1
    assert result.items[0].message == "第二次"
    assert result.items[0].request_id == req2.request_id


def test_admin_can_discover_and_operate_all_groups():
    """系统管理员在"发现"里能看到所有组群（含 private、未加入的），并能操作未加入的组群。"""
    db = _db()
    owner = _auth("owner", "Owner")
    admin = AuthContext(is_admin=True, acting_user_id="admin", acting_user_name="Admin")

    # 创建两个组群：一个 listed，一个 private
    listed_group = create_group_service(GroupCreateRequest(name="公开组", visibility="listed"), owner, db)
    private_group = create_group_service(GroupCreateRequest(name="私有组", visibility="private"), owner, db)

    # 系统管理员未加入任何组群，"发现"里能看到所有组群（含 private）
    discover_result = discover_groups_service(admin, db, page=1, page_size=20, keyword=None)
    discover_ids = {item.group_id for item in discover_result.items}
    assert listed_group.group_id in discover_ids
    assert private_group.group_id in discover_ids

    # "我的组群"只显示真实加入的，admin 未加入则为空
    my_result = list_my_groups_service(admin, db, page=1, page_size=20)
    assert my_result.total == 0

    # admin 能查看 private 组群详情（未加入也能看）
    detail = get_group_service(private_group.group_id, admin, db)
    assert detail.group_id == private_group.group_id
    # viewer_role 反映真实成员关系（未加入为 None），不是虚构的 owner
    assert detail.viewer_role is None

    # admin 能操作未加入的组群（如更新设置）
    updated = update_group_service(
        private_group.group_id, GroupUpdateRequest(name="改后的私有组"), admin, db
    )
    assert updated.name == "改后的私有组"

    # admin 可直接加入 private 组群（跳过申请审批），成为真实成员
    join_result = create_join_request_service(
        private_group.group_id, GroupJoinRequestCreate(), admin, db
    )
    assert join_result.status == "approved"
    members = list_group_members_service(private_group.group_id, admin, db, page=1, page_size=20)
    assert any(m.user_id == "admin" for m in members.items)
    # 加入后"我的组群"能看到
    my_after = list_my_groups_service(admin, db, page=1, page_size=20)
    assert any(g.group_id == private_group.group_id for g in my_after.items)


def test_group_creation_limit_enforced():
    """普通用户创建组群达到上限后被拒绝，特权用户不受限。"""
    db = _db()
    owner = _auth("owner", "Owner")

    # 临时把上限设为 2，创建 2 个后第 3 个应被拒
    import plugins_market.services.groups as groups_module
    original = groups_module.settings.max_groups_per_user
    groups_module.settings.max_groups_per_user = 2
    try:
        create_group_service(GroupCreateRequest(name="组1"), owner, db)
        create_group_service(GroupCreateRequest(name="组2"), owner, db)
        try:
            create_group_service(GroupCreateRequest(name="组3"), owner, db)
            assert False, "应该抛出 group_limit_exceeded"
        except HTTPException as exc:
            assert exc.status_code == 409

        # 特权用户不受限
        admin = AuthContext(is_admin=True, acting_user_id="admin", acting_user_name="Admin")
        g = create_group_service(GroupCreateRequest(name="管理员组"), admin, db)
        assert g.name == "管理员组"
    finally:
        groups_module.settings.max_groups_per_user = original


def test_group_member_limit_enforced():
    """普通用户添加成员达到上限后被拒绝。"""
    db = _db()
    owner = _auth("owner", "Owner")
    group = create_group_service(GroupCreateRequest(name="研发组"), owner, db)

    import plugins_market.services.groups as groups_module
    original = groups_module.settings.max_members_per_group
    groups_module.settings.max_members_per_group = 2
    try:
        # owner 已是成员（1），加 u2（2），加 u3 应被拒
        upsert_group_member_service(
            group.group_id, GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"), owner, db
        )
        try:
            upsert_group_member_service(
                group.group_id, GroupMemberUpsertRequest(user_id="u3", user_name="User3", role="member"), owner, db
            )
            assert False, "应该抛出 group_member_limit_exceeded"
        except HTTPException as exc:
            assert exc.status_code == 409
    finally:
        groups_module.settings.max_members_per_group = original


def test_privileged_user_grant_is_directly_active():
    """特权用户（系统管理员/审核管理员）授权 skill 给组群应直接 active，不需要审批。"""
    db = _db()
    # 已审核通过的私有 skill
    skill = _skill(asset_id="priv-skill", publisher_id="publisher")
    skill.visibility = "private"
    skill.moderation_status = "APPROVED"
    skill.publish_result = "publish_success"
    skill.public_latest_version = "1.0.0"
    db.add(skill)
    db.commit()

    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth("owner", "Owner"), db)
    admin = AuthContext(is_admin=True, acting_user_id="admin", acting_user_name="Admin")

    # admin 未加入组群，授权应直接 active
    grant = grant_skill_to_group_service(group.group_id, GroupSkillGrantRequest(asset_id="priv-skill"), admin, db)
    assert grant.status == "active"


def test_discover_by_group_id():
    """发现群组支持按 group_id 精确查找。"""
    db = _db()
    owner = _auth("owner", "Owner")
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), owner, db)
    other = create_group_service(GroupCreateRequest(name="测试组", visibility="listed"), _auth("u2", "User2"), db)

    # 用 group_id 作为关键字精确命中
    result = discover_groups_service(_auth("u3", "User3"), db, page=1, page_size=20, keyword=group.group_id)
    assert [g.group_id for g in result.items] == [group.group_id]
    assert other.group_id not in [g.group_id for g in result.items]

    # name 模糊匹配仍然有效
    result2 = discover_groups_service(_auth("u3", "User3"), db, page=1, page_size=20, keyword="研发")
    assert group.group_id in [g.group_id for g in result2.items]


def test_list_my_group_skills_includes_publisher_grants():
    """非成员发布者授权出去的 skill 应出现在"我授权的"列表，且来源标记为 owner。"""
    db = _db()
    skill = _skill(asset_id="alice-skill", publisher_id="alice")
    skill.visibility = "private"
    db.add(skill)
    db.add(MarketAssetVersionDB(version_id="v1", asset_id="alice-skill", version="1.0.0", create_time=1))
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )
    # alice 不是群组成员，授权自己的 skill 给群组（pending，需群主审批）
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )
    # pending 不在列表中（列表只含 active），先让群主审批通过
    decide_group_skill_grant_service(
        group.group_id, "alice-skill", GroupSkillGrantDecision(status="active"), _auth("charlie", "Charlie"), db
    )

    # alice 未加入群组，但作为发布者应能看到自己授权出去的 skill
    result = list_my_group_skills_service(_auth("alice", "Alice"), db, storage=None, page=1, page_size=20)
    assert result.total == 1
    item = result.items[0]
    assert item.skill.asset_id == "alice-skill"
    assert item.group_id == group.group_id
    assert item.viewer_access_source == "owner"


def test_list_my_group_skills_includes_member_grants():
    """群组成员能看到群组授权的 skill，来源标记为 group。"""
    db = _db()
    skill = _skill(asset_id="alice-skill", publisher_id="alice")
    skill.visibility = "private"
    db.add(skill)
    db.add(MarketAssetVersionDB(version_id="v1", asset_id="alice-skill", version="1.0.0", create_time=1))
    db.commit()
    owner = _auth("charlie", "Charlie")
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), owner, db)
    # alice 授权给群组（pending），群主审批通过
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )
    decide_group_skill_grant_service(
        group.group_id, "alice-skill", GroupSkillGrantDecision(status="active"), owner, db
    )
    # bob 加入群组成为成员
    upsert_group_member_service(
        group.group_id, GroupMemberUpsertRequest(user_id="bob", user_name="Bob", role="member"), owner, db
    )

    # bob 作为成员能看到该授权，来源标记为 group
    result = list_my_group_skills_service(_auth("bob", "Bob"), db, storage=None, page=1, page_size=20)
    assert result.total == 1
    item = result.items[0]
    assert item.skill.asset_id == "alice-skill"
    assert item.viewer_access_source == "group"


def test_revoke_by_non_member_publisher():
    """非成员发布者能撤回自己授权给群组的 skill。"""
    db = _db()
    skill = _skill(asset_id="alice-skill", publisher_id="alice")
    skill.visibility = "private"
    db.add(skill)
    db.add(MarketAssetVersionDB(version_id="v1", asset_id="alice-skill", version="1.0.0", create_time=1))
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="alice-skill"), _auth("alice", "Alice"), db
    )
    decide_group_skill_grant_service(
        group.group_id, "alice-skill", GroupSkillGrantDecision(status="active"), _auth("charlie", "Charlie"), db
    )

    # alice 非成员，但作为发布者可撤回
    revoke_skill_from_group_service(group.group_id, "alice-skill", _auth("alice", "Alice"), db)

    grants = list_group_grants_service(
        group.group_id, _auth("charlie", "Charlie"), db, page=1, page_size=20, status_filter="all"
    )
    assert all(g.status != "active" for g in grants.items if g.asset_id == "alice-skill")


def test_admin_publisher_grant_source_is_owner():
    """管理员作为发布者授权的 skill 来源应为 owner，以便前端展示撤销入口。"""
    db = _db()
    # admin 既是系统管理员又是该 skill 的发布者
    skill = _skill(asset_id="admin-skill", publisher_id="admin")
    skill.visibility = "private"
    db.add(skill)
    db.add(MarketAssetVersionDB(version_id="v1", asset_id="admin-skill", version="1.0.0", create_time=1))
    db.commit()
    group = create_group_service(
        GroupCreateRequest(name="研发组", visibility="listed"), _auth("charlie", "Charlie"), db
    )
    admin = AuthContext(is_admin=True, acting_user_id="admin", acting_user_name="Admin")
    # admin 授权自己的 skill 给组群（特权用户直接 active）
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="admin-skill"), admin, db
    )

    # 在"我授权的 skills"列表里，admin 看到的来源应为 owner
    result = list_my_group_skills_service(admin, db, storage=None, page=1, page_size=20)
    assert result.total == 1
    item = result.items[0]
    assert item.skill.asset_id == "admin-skill"
    assert item.viewer_access_source == "owner"

    # 在群组详情页授权列表里，admin 看到的来源也应为 owner
    grants = list_group_grants_service(
        group.group_id, admin, db, page=1, page_size=20, status_filter="active"
    )
    grant_item = next(g for g in grants.items if g.asset_id == "admin-skill")
    assert grant_item.viewer_access_source == "owner"


def test_list_my_groups_search_by_group_id():
    """我的组群支持按 group_id 精确查找（Bug 1）。"""
    db = _db()
    owner = _auth("owner", "Owner")
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), owner, db)
    other = create_group_service(GroupCreateRequest(name="测试组", visibility="listed"), owner, db)

    # 用 group_id 作为关键字精确命中
    result = list_my_groups_service(owner, db, page=1, page_size=20, keyword=group.group_id)
    assert [g.group_id for g in result.items] == [group.group_id]
    assert other.group_id not in [g.group_id for g in result.items]

    # name 模糊匹配仍然有效
    result2 = list_my_groups_service(owner, db, page=1, page_size=20, keyword="研发")
    assert group.group_id in [g.group_id for g in result2.items]


def test_admin_join_updates_member_count():
    """特权用户加入组群后 member_count 应正确同步（Bug 2）。"""
    db = _db()
    owner = _auth("owner", "Owner")
    admin = AuthContext(is_admin=True, acting_user_id="admin", acting_user_name="Admin")
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), owner, db)

    # 初始只有 owner，member_count=1
    detail = get_group_service(group.group_id, owner, db)
    assert detail.member_count == 1

    # admin 直接加入（跳过审批），member_count 应变为 2
    create_join_request_service(group.group_id, GroupJoinRequestCreate(), admin, db)
    detail = get_group_service(group.group_id, owner, db)
    assert detail.member_count == 2

    # 成员列表也应包含 admin
    members = list_group_members_service(group.group_id, owner, db, page=1, page_size=20)
    member_ids = {m.user_id for m in members.items}
    assert member_ids == {"owner", "admin"}


def test_skill_count_reflects_active_grant():
    """已授权的 skill 应计入 skill_count，即使资产级聚合滞后（Bug 3）。

    关键：模拟「新模型 + 资产级聚合滞后」时必须保留 publish_result 非空（维持 new_model），
    仅清空 public_latest_version 并把 moderation_status 置为 PENDING，使资产级两个分支
    （new_model_ok / legacy_ok）均不满足，从而真正走版本级 approved_version_exists 兜底。
    """
    db = _db()
    owner = _auth("owner", "Owner")
    # 构造一个新模型 skill：版本级已通过审核，public_latest_version 先设好以便通过授权校验
    skill = _skill(asset_id="skill-1", publisher_id="owner")
    skill.visibility = "private"
    skill.public_latest_version = "1.0.0"
    skill.publish_result = "publish_success"
    skill.moderation_status = "APPROVED"
    db.add(skill)
    db.add(
        MarketAssetVersionDB(
            version_id="v1",
            asset_id="skill-1",
            version="1.0.0",
            create_time=1,
            moderation_status="APPROVED",
            publish_result="publish_success",
        )
    )
    db.commit()

    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), owner, db)
    grant_skill_to_group_service(group.group_id, GroupSkillGrantRequest(asset_id="skill-1"), owner, db)

    # 授权后 skill_count 应为 1
    detail = get_group_service(group.group_id, owner, db)
    assert detail.skill_count == 1

    # 模拟资产级聚合滞后：保留 publish_result 维持新模型，仅清空 public_latest_version，
    # 并把资产级 moderation_status 置为 PENDING（资产级聚合滞后、版本行仍 APPROVED）。
    # 此时 new_model_ok=false、legacy_ok=false，仅靠版本级 approved_version_exists 兜底才会计入。
    skill.public_latest_version = None
    skill.moderation_status = "PENDING"
    db.add(skill)
    db.commit()

    # 手动触发 refresh_counts，验证版本级兜底仍能正确计数
    from plugins_market.repositories.groups_repository import MarketGroupRepository
    MarketGroupRepository(db).refresh_counts(group.group_id)
    db.commit()
    db.expire_all()

    detail = get_group_service(group.group_id, owner, db)
    assert detail.skill_count == 1

