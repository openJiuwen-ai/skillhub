import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.auth import AuthContext
from plugins_market.core.viewer_context import ANONYMOUS_VIEWER, ViewerContext
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetInteractionDB, MarketAssetVersionDB
from plugins_market.repositories import MarketAssetRepository
from plugins_market.routers.interaction import get_interactions, get_interactions_batch, post_interact
from plugins_market.schemas.group import GroupCreateRequest, GroupMemberUpsertRequest, GroupSkillGrantRequest
from plugins_market.schemas.interaction import InteractRequest
from plugins_market.services.groups import (
    create_group_service,
    grant_skill_to_group_service,
    upsert_group_member_service,
    user_has_group_skill_access,
)
from plugins_market.core.errors import PublishError
from plugins_market.services.plugin import (
    _filter_skill_version_strings_for_viewer,
    _skill_visible_to_marketplace_viewer,
    _validate_existing_asset_visibility,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_cls = sessionmaker(bind=engine)
    return session_cls()


def _auth(user_id="owner", name="Owner"):
    return AuthContext(is_admin=False, acting_user_id=user_id, acting_user_name=name)


def _private_skill():
    return MarketAssetDB(
        asset_id="skill-1",
        asset_type="plugin",
        name="skill-1",
        display_name="skill-1",
        publisher_id="owner",
        publisher_name="Owner",
        status="PUBLISHED",
        plugin_type="skill",
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


def test_existing_skill_visibility_is_immutable_when_publishing_new_version():
    public_asset = _private_skill()
    public_asset.visibility = "public"
    private_asset = _private_skill()
    private_asset.visibility = "private"

    _validate_existing_asset_visibility(public_asset, "public")
    _validate_existing_asset_visibility(private_asset, "private")

    for asset, requested in ((public_asset, "private"), (private_asset, "public")):
        with pytest.raises(PublishError) as exc_info:
            _validate_existing_asset_visibility(asset, requested)
        assert exc_info.value.error == "visibility_immutable"
        assert exc_info.value.status_code == 409
        assert asset.visibility != requested


def test_private_visibility_hides_approved_skill_from_public_viewer():
    db = _db()
    asset = _private_skill()
    asset.visibility = "private"
    asset.public_latest_version = "1.0.0"
    asset.moderation_status = "APPROVED"
    asset.publish_result = "publish_success"
    db.add(asset)
    db.commit()

    assert ANONYMOUS_VIEWER.can_view_skill_asset(asset, db) is False
    assert (
        ViewerContext(user_id="owner", user_login="Owner", is_system_admin=False).can_view_skill_asset(asset, db)
        is True
    )


def test_private_approved_skill_hidden_from_marketplace_surface():
    db = _db()
    asset = _private_skill()
    asset.visibility = "private"
    asset.public_latest_version = "1.0.0"
    asset.moderation_status = "APPROVED"
    asset.publish_result = "publish_success"
    db.add(asset)
    db.commit()

    assert _skill_visible_to_marketplace_viewer(asset, ANONYMOUS_VIEWER, db) is False


def test_private_approved_skill_version_requires_private_acl():
    db = _db()
    asset = _private_skill()
    asset.visibility = "private"
    asset.public_latest_version = "1.0.0"
    asset.moderation_status = "APPROVED"
    asset.publish_result = "publish_success"
    version = MarketAssetVersionDB(
        version_id="v1",
        asset_id="skill-1",
        version="1.0.0",
        create_time=1,
        moderation_status="APPROVED",
        publish_result="publish_success",
    )
    db.add(asset)
    db.add(version)
    db.commit()

    viewer = ViewerContext(user_id="u2", user_login="User2", is_system_admin=False)

    assert viewer.can_view_skill_asset(asset, db) is False
    assert viewer.can_see_skill_version_row(asset, version, db) is False
    assert viewer.can_download_skill_version_row(asset, version, db) is False


def test_group_member_cannot_view_private_granted_pending_version():
    """未通过审核的私有 skill 不可授权给组群，且群成员也不可查看/下载（不可绕过审核）。"""
    db = _db()
    asset = _private_skill()
    version = MarketAssetVersionDB(
        version_id="v1",
        asset_id="skill-1",
        version="1.0.0",
        create_time=1,
        moderation_status="PENDING",
        publish_result="pending_moderation",
    )
    db.add(asset)
    db.add(version)
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth(), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"),
        _auth(),
        db,
    )
    # 未通过审核的 skill 不可授权给组群
    try:
        grant_skill_to_group_service(group.group_id, GroupSkillGrantRequest(asset_id="skill-1"), _auth(), db)
        assert False, "应该抛出 skill_not_approved 错误"
    except HTTPException as exc:
        assert exc.status_code == 409

    # 即使绕过授权，ACL 也不可见
    viewer = ViewerContext(user_id="u2", user_login="User2", is_system_admin=False)
    assert viewer.can_view_skill_asset(asset, db) is False
    assert viewer.can_see_skill_version_row(asset, version, db) is False
    assert viewer.can_download_skill_version_row(asset, version, db) is False
    assert _filter_skill_version_strings_for_viewer(asset, [version], asset.plugin_type, viewer, db) == []


def test_group_member_can_read_and_update_private_skill_interactions():
    db = _db()
    asset = _private_skill()
    asset.visibility = "private"
    asset.public_latest_version = "1.0.0"
    asset.moderation_status = "APPROVED"
    asset.publish_result = "publish_success"
    db.add(asset)
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
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth(), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"),
        _auth(),
        db,
    )
    grant_skill_to_group_service(group.group_id, GroupSkillGrantRequest(asset_id="skill-1"), _auth(), db)
    auth = _auth("u2", "User2")
    viewer = ViewerContext(user_id="u2", user_login="User2", is_system_admin=False)

    single = asyncio.run(get_interactions("skill-1", db=db, viewer=viewer))
    batch = asyncio.run(get_interactions_batch(asset_ids=["skill-1"], db=db, viewer=viewer))
    db.add(
        MarketAssetInteractionDB(
            id=1,
            asset_id="skill-1",
            user_id="u2",
            action_type="star",
            create_time=1,
            update_time=1,
        )
    )
    asset.star_count = 1
    db.commit()
    toggled = asyncio.run(
        post_interact("skill-1", InteractRequest(action_type="star"), db=db, auth=auth)
    )

    assert single.data.starred is False
    assert [item.asset_id for item in batch.data.items] == ["skill-1"]
    assert toggled.data.active is False
    assert MarketAssetRepository(db).filter_visible_asset_ids(["skill-1"], viewer) == {"skill-1"}


def test_private_skill_interactions_remain_hidden_without_group_grant():
    db = _db()
    asset = _private_skill()
    asset.visibility = "private"
    db.add(asset)
    db.commit()
    viewer = ViewerContext(user_id="u2", user_login="User2", is_system_admin=False)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_interactions("skill-1", db=db, viewer=viewer))

    assert exc_info.value.status_code == 404
    assert asyncio.run(get_interactions_batch(asset_ids=["skill-1"], db=db, viewer=viewer)).data.items == []


def test_private_skill_publisher_can_download_own_version():
    db = _db()
    asset = _private_skill()
    asset.visibility = "private"
    version = MarketAssetVersionDB(
        version_id="v1",
        asset_id="skill-1",
        version="1.0.0",
        create_time=1,
        moderation_status="PENDING",
        publish_result="pending_moderation",
    )
    db.add(asset)
    db.add(version)
    db.commit()

    viewer = ViewerContext(user_id="owner", user_login="Owner", is_system_admin=False)
    assert viewer.can_view_skill_asset(asset, db) is True
    assert viewer.can_see_skill_version_row(asset, version, db) is True
    assert viewer.can_download_skill_version_row(asset, version, db) is True


def test_rejected_skill_version_is_not_downloadable():
    db = _db()
    asset = _private_skill()
    asset.visibility = "public"
    asset.moderation_status = "REJECTED"
    asset.publish_result = "publish_failed"
    version = MarketAssetVersionDB(
        version_id="v1",
        asset_id="skill-1",
        version="1.0.0",
        create_time=1,
        moderation_status="REJECTED",
        publish_result="publish_failed",
    )
    db.add(asset)
    db.add(version)
    db.commit()

    owner = ViewerContext(user_id="owner", user_login="Owner", is_system_admin=False)
    admin = ViewerContext(user_id="admin", user_login="admin", is_system_admin=True)
    assert owner.can_see_skill_version_row(asset, version, db) is True
    assert owner.can_download_skill_version_row(asset, version, db) is False
    assert admin.can_download_skill_version_row(asset, version, db) is False
    assert ANONYMOUS_VIEWER.can_download_skill_version_row(asset, version, db) is False


def test_group_member_can_use_private_approved_skill_version():
    db = _db()
    asset = _private_skill()
    asset.visibility = "private"
    asset.public_latest_version = "1.0.0"
    asset.moderation_status = "APPROVED"
    asset.publish_result = "publish_success"
    version = MarketAssetVersionDB(
        version_id="v1",
        asset_id="skill-1",
        version="1.0.0",
        create_time=1,
        moderation_status="APPROVED",
        publish_result="publish_success",
    )
    db.add(asset)
    db.add(version)
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth(), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"),
        _auth(),
        db,
    )
    grant_skill_to_group_service(group.group_id, GroupSkillGrantRequest(asset_id="skill-1"), _auth(), db)

    viewer = ViewerContext(user_id="u2", user_login="User2", is_system_admin=False)
    assert viewer.can_view_skill_asset(asset, db) is True
    assert viewer.can_see_skill_version_row(asset, version, db) is True
    assert viewer.can_download_skill_version_row(asset, version, db) is True


def test_group_member_cannot_view_pending_grant_before_admin_approval():
    db = _db()
    asset = _private_skill()
    asset.publisher_id = "publisher"
    asset.public_latest_version = "1.0.0"
    asset.moderation_status = "APPROVED"
    asset.publish_result = "publish_success"
    db.add(asset)
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组", visibility="listed"), _auth(), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"),
        _auth(),
        db,
    )
    grant_skill_to_group_service(
        group.group_id, GroupSkillGrantRequest(asset_id="skill-1"), _auth("publisher", "Publisher"), db
    )

    viewer = ViewerContext(user_id="u2", user_login="User2", is_system_admin=False)

    assert viewer.can_view_skill_asset(asset, db) is True
    assert user_has_group_skill_access(db, user_id="u2", asset_id="skill-1") is False


def test_non_member_cannot_view_private_group_granted_skill():
    db = _db()
    asset = _private_skill()
    asset.visibility = "private"
    asset.public_latest_version = "1.0.0"
    asset.moderation_status = "APPROVED"
    asset.publish_result = "publish_success"
    db.add(asset)
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth(), db)
    grant_skill_to_group_service(group.group_id, GroupSkillGrantRequest(asset_id="skill-1"), _auth(), db)

    viewer = ViewerContext(user_id="u3", user_login="User3", is_system_admin=False)

    assert viewer.can_view_skill_asset(asset, db) is False


def test_group_member_can_download_private_approved_skill_version():
    """组授权成员可查看和下载已审核通过的私有 skill 版本。"""
    db = _db()
    asset = _private_skill()
    asset.visibility = "private"
    asset.public_latest_version = "1.0.0"
    asset.moderation_status = "APPROVED"
    asset.publish_result = "publish_success"
    version = MarketAssetVersionDB(
        version_id="v1",
        asset_id="skill-1",
        version="1.0.0",
        create_time=1,
        moderation_status="APPROVED",
        publish_result="publish_success",
    )
    db.add(asset)
    db.add(version)
    db.commit()
    group = create_group_service(GroupCreateRequest(name="研发组"), _auth(), db)
    upsert_group_member_service(
        group.group_id,
        GroupMemberUpsertRequest(user_id="u2", user_name="User2", role="member"),
        _auth(),
        db,
    )
    grant_skill_to_group_service(group.group_id, GroupSkillGrantRequest(asset_id="skill-1"), _auth(), db)

    viewer = ViewerContext(user_id="u2", user_login="User2", is_system_admin=False)
    assert viewer.can_view_skill_asset(asset, db) is True
    assert viewer.can_see_skill_version_row(asset, version, db) is True
    assert viewer.can_download_skill_version_row(asset, version, db) is True
    assert _filter_skill_version_strings_for_viewer(asset, [version], asset.plugin_type, viewer, db) == ["1.0.0"]

