from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.auth import AuthContext
from plugins_market.models.base import Base
from plugins_market.core.viewer_context import ViewerContext
from plugins_market.models.groups import MarketGroupJoinRequestDB
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.repositories import MarketAssetRepository
from plugins_market.schemas.plugin import PluginListQuery
from plugins_market.schemas.group import (
    GroupCreateRequest,
    GroupJoinRequestCreate,
    GroupMemberUpsertRequest,
    GroupJoinRequestDecision,
    GroupSkillGrantRequest,
    GroupSkillGrantDecision,
)
from plugins_market.services.groups import (
    create_group_service,
    create_join_request_service,
    decide_join_request_service,
    discover_groups_service,
    get_group_service,
    grant_skill_to_group_service,
    decide_group_skill_grant_service,
    remove_group_member_service,
    search_grantable_skills_service,
    list_group_grants_service,
    list_my_group_skills_service,
    upsert_group_member_service,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_cls = sessionmaker(bind=engine)
    return session_cls()


def _auth(user_id="owner", name="Owner"):
    return AuthContext(is_admin=False, acting_user_id=user_id, acting_user_name=name)


def _asset(asset_id: str, publisher_id: str, plugin_type: str = "skill"):
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
        public_latest_version=None,
        moderation_status="PENDING",
        publish_result="pending_moderation",
        view_count=0,
        install_count=0,
        like_count=0,
        star_count=0,
        review_count=0,
        average_rating=8.0,
        create_time=1,
        update_time=1,
    )


def test_discover_only_lists_listed_groups_and_private_group_stays_hidden():
    db = _db()
    listed = create_group_service(GroupCreateRequest(name="公开组", visibility="listed"), _auth(), db)
    private = create_group_service(
        GroupCreateRequest(name="私有组", visibility="private"), _auth("owner2", "Owner2"), db
    )

    result = discover_groups_service(_auth("u2", "User2"), db, page=1, page_size=20, keyword=None)

    assert [item.group_id for item in result.items] == [listed.group_id]
    assert result.items[0].viewer_role is None

    try:
        get_group_service(private.group_id, _auth("u2", "User2"), db)
        assert False
    except HTTPException as exc:
        assert exc.status_code == 403


def test_listed_group_exposes_summary_and_join_status_to_non_member():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="公开组", visibility="listed"), _auth(), db)
    req = create_join_request_service(group.group_id, GroupJoinRequestCreate(message="join"), _auth("u2", "User2"), db)

    item = get_group_service(group.group_id, _auth("u2", "User2"), db)

    assert req.status == "pending"
    assert item.viewer_role is None
    assert item.join_request_status == "pending"


def test_listed_group_active_grants_visible_to_non_member_but_pending_hidden():
    db = _db()
    owner_skill = _asset("owner-skill", "owner", "skill")
    owner_skill.moderation_status = "APPROVED"
    owner_skill.publish_result = "success"
    owner_skill.public_latest_version = "1.0.0"
    publisher_skill = _asset("publisher-skill", "publisher", "skill")
    publisher_skill.moderation_status = "APPROVED"
    publisher_skill.publish_result = "success"
    publisher_skill.public_latest_version = "1.0.0"
    db.add(owner_skill)
    db.add(publisher_skill)
    db.add(
        MarketAssetVersionDB(
            version_id="v-owner", asset_id="owner-skill", version="1.0.0", moderation_status="APPROVED", create_time=1
        )
    )
    db.add(
        MarketAssetVersionDB(
            version_id="v-pub", asset_id="publisher-skill", version="1.0.0", moderation_status="APPROVED", create_time=1
        )
    )
    db.commit()
    group = create_group_service(GroupCreateRequest(name="公开组", visibility="listed"), _auth(), db)
    grant_skill_to_group_service(group.group_id, GroupSkillGrantRequest(asset_id="owner-skill"), _auth(), db)
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="publisher-skill"), _auth("publisher", "Publisher"), db
    )

    active = list_group_grants_service(
        group.group_id, _auth("alice", "Alice"), db, page=1, page_size=20, status_filter="active"
    )
    pending = list_group_grants_service(
        group.group_id, _auth("alice", "Alice"), db, page=1, page_size=20, status_filter="pending"
    )

    assert [item.asset_id for item in active.items] == ["owner-skill"]
    assert pending.items == active.items


def test_group_grant_list_visibility_distinguishes_member_publisher_and_outsider():
    db = _db()
    skill = _asset("publisher-skill", "publisher", "skill")
    skill.visibility = "private"
    skill.moderation_status = "APPROVED"
    skill.publish_result = "publish_success"
    skill.public_latest_version = "1.0.0"
    db.add(skill)
    db.add(
        MarketAssetVersionDB(
            version_id="v-pub",
            asset_id="publisher-skill",
            version="1.0.0",
            moderation_status="APPROVED",
            publish_result="publish_success",
            create_time=1,
        )
    )
    db.commit()
    group = create_group_service(GroupCreateRequest(name="公开组", visibility="listed"), _auth("owner", "Owner"), db)
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="publisher-skill"), _auth("publisher", "Publisher"), db
    )
    decide_group_skill_grant_service(
        group.group_id, "publisher-skill", GroupSkillGrantDecision(status="active"), _auth("owner", "Owner"), db
    )

    owner_view = list_group_grants_service(
        group.group_id, _auth("owner", "Owner"), db, page=1, page_size=20, status_filter="active"
    )
    publisher_view = list_group_grants_service(
        group.group_id, _auth("publisher", "Publisher"), db, page=1, page_size=20, status_filter="active"
    )
    outsider_view = list_group_grants_service(
        group.group_id, _auth("alice", "Alice"), db, page=1, page_size=20, status_filter="active"
    )

    assert [item.asset_id for item in owner_view.items] == ["publisher-skill"]
    assert [item.asset_id for item in publisher_view.items] == ["publisher-skill"]
    assert outsider_view.items == []


def test_discover_deduplicates_join_requests_with_same_timestamp_and_filters_server_side():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="公开组", visibility="listed"), _auth(), db)
    joined = create_group_service(
        GroupCreateRequest(name="已加入组", visibility="listed"), _auth("owner2", "Owner2"), db
    )
    rejected = create_join_request_service(
        group.group_id, GroupJoinRequestCreate(message="join"), _auth("u2", "User2"), db
    )
    decide_join_request_service(
        group.group_id, rejected.request_id, GroupJoinRequestDecision(status="rejected"), _auth(), db
    )
    pending = create_join_request_service(
        group.group_id, GroupJoinRequestCreate(message="join again"), _auth("u2", "User2"), db
    )
    joined_req = create_join_request_service(
        joined.group_id, GroupJoinRequestCreate(message="join"), _auth("u2", "User2"), db
    )
    decide_join_request_service(
        joined.group_id,
        joined_req.request_id,
        GroupJoinRequestDecision(status="approved"),
        _auth("owner2", "Owner2"),
        db,
    )
    db.query(MarketGroupJoinRequestDB).filter(
        MarketGroupJoinRequestDB.request_id == rejected.request_id
    ).update({"create_time": 1000, "update_time": 1000}, synchronize_session=False)
    db.query(MarketGroupJoinRequestDB).filter(
        MarketGroupJoinRequestDB.request_id == pending.request_id
    ).update({"create_time": 1000, "update_time": 1001}, synchronize_session=False)
    db.commit()

    all_groups = discover_groups_service(_auth("u2", "User2"), db, page=1, page_size=20, keyword=None)
    pending_groups = discover_groups_service(
        _auth("u2", "User2"), db, page=1, page_size=20, keyword=None, filter_by="pending"
    )
    joined_groups = discover_groups_service(
        _auth("u2", "User2"), db, page=1, page_size=20, keyword=None, filter_by="joined"
    )

    assert all_groups.total == 2
    assert len({item.group_id for item in all_groups.items}) == all_groups.total
    assert [item.group_id for item in pending_groups.items] == [group.group_id]
    assert [item.group_id for item in joined_groups.items] == [joined.group_id]


def test_private_group_rejects_direct_join_request():
    db = _db()
    group = create_group_service(GroupCreateRequest(name="私有组", visibility="private"), _auth(), db)

    try:
        create_join_request_service(group.group_id, GroupJoinRequestCreate(message="join"), _auth("u2", "User2"), db)
        assert False
    except HTTPException as exc:
        assert exc.status_code == 404


def test_search_grantable_skills_only_returns_current_publishers_skill_assets():
    db = _db()
    public_skill = _asset("mine-skill", "owner", "skill")
    public_skill.moderation_status = "APPROVED"
    public_skill.publish_result = "publish_success"
    public_skill.public_latest_version = "1.0.0"
    db.add(public_skill)
    private_skill = _asset("pending-skill", "owner", "skill")
    private_skill.visibility = "private"
    private_skill.moderation_status = "APPROVED"
    private_skill.publish_result = "publish_success"
    private_skill.public_latest_version = "1.0.0"
    db.add(private_skill)
    db.add(_asset("other-skill", "other", "skill"))
    db.add(_asset("mine-plugin", "owner", "plugin"))
    db.commit()

    result = search_grantable_skills_service(_auth(), db, page=1, page_size=20, keyword="skill")

    assert result.total == 1
    assert {item.asset_id for item in result.items} == {"pending-skill"}
    group = create_group_service(GroupCreateRequest(name="公开组", visibility="listed"), _auth(), db)
    grant = grant_skill_to_group_service(group.group_id, GroupSkillGrantRequest(asset_id="pending-skill"), _auth(), db)
    assert grant.asset_id == "pending-skill"


def test_grantable_skill_status_requires_private_group_access():
    db = _db()
    public_skill = _asset("mine-skill", "owner", "skill")
    public_skill.moderation_status = "APPROVED"
    public_skill.publish_result = "publish_success"
    public_skill.public_latest_version = "1.0.0"
    public_skill.visibility = "private"
    db.add(public_skill)
    db.commit()
    private_group = create_group_service(
        GroupCreateRequest(name="私有组", visibility="private"), _auth("other", "Other"), db
    )

    try:
        search_grantable_skills_service(
            _auth(), db, page=1, page_size=20, keyword="skill", group_id=private_group.group_id
        )
        assert False
    except HTTPException as exc:
        assert exc.status_code == 404


def test_multi_account_group_invite_approval_grant_and_access_control_flow():
    db = _db()
    owner = _auth("owner", "Owner")
    member = _auth("u2", "User2")
    outsider = _auth("u3", "User3")
    group_skill = _asset("owner-group-skill", "owner", "skill")
    group_skill.visibility = "private"
    group_skill.moderation_status = "APPROVED"
    group_skill.publish_result = "publish_success"
    group_skill.public_latest_version = "1.0.0"
    db.add(group_skill)
    db.add(
        MarketAssetVersionDB(
            version_id="v1",
            asset_id="owner-group-skill",
            version="1.0.0",
            create_time=1,
            file_path="artifacts/owner-group-skill.zip",
            moderation_status="APPROVED",
            publish_result="publish_success",
        )
    )
    db.commit()

    private_group = create_group_service(GroupCreateRequest(name="私有组", visibility="private"), owner, db)
    listed_group = create_group_service(GroupCreateRequest(name="公开组", visibility="listed"), owner, db)

    discovered_by_member = discover_groups_service(member, db, page=1, page_size=20, keyword=None)
    assert [item.group_id for item in discovered_by_member.items] == [listed_group.group_id]
    try:
        get_group_service(private_group.group_id, outsider, db)
        assert False
    except HTTPException as exc:
        assert exc.status_code == 403

    join_request = create_join_request_service(
        listed_group.group_id, GroupJoinRequestCreate(message="申请加入"), member, db
    )
    decide_join_request_service(
        listed_group.group_id, join_request.request_id, GroupJoinRequestDecision(status="approved"), owner, db
    )
    joined_group = get_group_service(listed_group.group_id, member, db)
    assert joined_group.viewer_role == "member"

    upsert_group_member_service(
        private_group.group_id, GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"), owner, db
    )

    grant = grant_skill_to_group_service(
        private_group.group_id, GroupSkillGrantRequest(asset_id="owner-group-skill"), owner, db
    )
    assert grant.asset_id == "owner-group-skill"
    asset = db.query(MarketAssetDB).filter(MarketAssetDB.asset_id == "owner-group-skill").one()
    member_viewer = ViewerContext(user_id="u2", user_login="User2", is_system_admin=False)
    outsider_viewer = ViewerContext(user_id="u3", user_login="User3", is_system_admin=False)
    assert member_viewer.can_view_skill_asset(asset, db) is True
    assert outsider_viewer.can_view_skill_asset(asset, db) is False
    # private Skill 不应出现在公开市场列表（首页/搜索），仅通过组群视角入口可见
    rows, total = MarketAssetRepository(db).list_plugins(
        PluginListQuery(page=1, page_size=20, search_keyword="owner-group-skill"),
        viewer=member_viewer,
    )
    assert total == 0
    _, outsider_total = MarketAssetRepository(db).list_plugins(
        PluginListQuery(page=1, page_size=20, search_keyword="owner-group-skill"),
        viewer=outsider_viewer,
    )
    assert outsider_total == 0

    class _Storage:
        @staticmethod
        def head_object(key):
            return {"success": True, "size": 1, "metadata": {"sha256": "abc"}}

        @staticmethod
        def presigned_get_url(key, download_filename=None):
            return f"http://example.test/{key}"

    group_skills = list_my_group_skills_service(member, db, _Storage(), page=1, page_size=20)
    assert group_skills.total == 1
    assert group_skills.items[0].skill.asset_id == "owner-group-skill"
    assert group_skills.items[0].skill.access_source == "group"
    version = db.query(MarketAssetVersionDB).filter(MarketAssetVersionDB.asset_id == "owner-group-skill").one()
    assert member_viewer.can_see_skill_version_row(asset, version, db) is True
    assert member_viewer.can_download_skill_version_row(asset, version, db) is True

    remove_group_member_service(private_group.group_id, "u2", owner, db)
    assert (
        ViewerContext(user_id="u2", user_login="User2", is_system_admin=False).can_view_skill_asset(asset, db) is False
    )
