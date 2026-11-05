# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise the SkillPack gate through real publish/moderate/download services."""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("STORE_DB_URL", "mysql+pymysql://test:test@127.0.0.1:3306/test")

from plugins_market.core.auth import AuthContext
from plugins_market.core.errors import PublishError
from plugins_market.core.viewer_context import ANONYMOUS_VIEWER
from plugins_market.imports.publish_wrap import PublishMetadataOverrides, prepare_publish_zip_content
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.services import plugin


_PACK_NAME = "weekly-report-pack"
_PACK_TAG = "openjiuwen-skillpack-v1"


class _MemoryStorage:
    """Preserve uploaded bytes and metadata, including the original ZIP."""

    def __init__(self):
        self.objects = {}
        self.config = SimpleNamespace(bucket_name="skillpack-test")
        self.s3_client = self

    def upload_bytes(self, content, key, metadata=None):
        self.objects[key] = (bytes(content), dict(metadata or {}))
        return {"success": True, "key": key}

    def head_object(self, key):
        if key not in self.objects:
            return {"success": False, "not_found": True}
        content, metadata = self.objects[key]
        return {"success": True, "size": len(content), "metadata": dict(metadata)}

    def get_object(self, *, Bucket, Key):
        assert Bucket == self.config.bucket_name
        return {"Body": io.BytesIO(self.objects[Key][0])}

    def read_bytes(self, key):
        value = self.objects.get(key)
        return None if value is None else value[0]

    def list_keys(self, prefix):
        return [key for key in self.objects if key.startswith(prefix)]

    def delete_object(self, key):
        self.objects.pop(key, None)
        return {"success": True}

    def presigned_get_url(self, key, **kwargs):
        assert key in self.objects
        return "https://storage.invalid/" + key


@pytest.fixture
def hub(monkeypatch):
    # Only isolate external notifications/audit writes; all package and DB gates run.
    for function in (
        "audit_log",
        "notify_review_admins_new_skill_submission",
        "notify_publisher_skill_manual_review_approved",
        "notify_publisher_skill_manual_review_rejected",
    ):
        monkeypatch.setattr(plugin, function, lambda *args, **kwargs: None)
    monkeypatch.setattr(plugin.settings, "skill_review_enabled", False)
    monkeypatch.setattr(plugin.settings, "system_admin_user", "system-test")
    monkeypatch.setattr(plugin.settings, "max_skills_per_user", 0)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield SimpleNamespace(db=db, storage=_MemoryStorage())
    finally:
        db.close()
        engine.dispose()


def _zip(name, *, version="1.0.0", members=None, lock=None, body="# Skill\n", kind=None):
    front = {"name": name, "description": "Integration test skill"}
    if kind is not None:
        front["kind"] = kind
    if kind in {"team-skill", "swarm-skill"}:
        front["roles"] = [{"id": "reader"}, {"id": "writer"}]
    tags = []
    if members is not None:
        front.update(kind="skillpack", skills=[member["name"] for member in members])
        tags.append(_PACK_TAG)
    manifest = {
        "name": name, "version": version, "display_name": name,
        "description": "Integration test skill", "runtime": {"type": "skill"},
        "metadata": {"author": "creator", "tags": tags},
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{name}/plugin.yaml", yaml.safe_dump(manifest))
        archive.writestr(
            f"{name}/{name}/SKILL.md",
            "---\n" + yaml.safe_dump(front, sort_keys=False) + "---\n" + body,
        )
        if lock is not None:
            archive.writestr(f"{name}/{name}/skillpack.lock.json", json.dumps(lock))
    return stream.getvalue()


def _publish(hub, content, *, user="creator", plugin_version=None, **overrides):
    return plugin.publish(
        user_id=user, content=content, filename="package.zip",
        expected_checksum=hashlib.sha256(content).hexdigest(),
        plugin_id=None, plugin_version=plugin_version, version_desc=None, force=False,
        db=hub.db, storage=hub.storage, **overrides,
    )


def _moderate(hub, result, *, action="approve"):
    return plugin.moderate_skill_asset_service(
        asset_id=result.plugin_id, action=action,
        reason="Member requirements not met" if action == "reject" else None,
        version=result.version,
        auth=AuthContext(
            is_admin=False, acting_user_id="reviewer", acting_user_name="reviewer",
            is_market_moderation_admin=True,
        ),
        db=hub.db, storage=hub.storage,
    )


def _members(hub, *, first_kind=None):
    members = []
    for name in ("standup-structure", "action-report"):
        content = _zip(name, kind=first_kind if not members else None)
        result = _publish(hub, content)
        assert result.publish_result == "pending_moderation"
        _moderate(hub, result)
        members.append({
            "name": name, "asset_id": result.plugin_id, "version": result.version,
            "artifact_sha256": hashlib.sha256(content).hexdigest(),
        })
    return members


def _lock(members):
    return {
        "schema_version": "openjiuwen.skillpack.lock/v1",
        "pack": {"name": _PACK_NAME, "version": "1.0.0"},
        "members": members,
    }


def _version(hub, asset_id):
    return hub.db.query(MarketAssetVersionDB).filter_by(asset_id=asset_id, version="1.0.0").one()


def _artifact_key(hub, asset_id, version="1.0.0"):
    row = hub.db.query(MarketAssetVersionDB).filter_by(asset_id=asset_id, version=version).one()
    keys = [key for key in hub.storage.list_keys(row.file_path)
            if key.endswith(".zip") and not key.endswith(".raw.zip")]
    assert len(keys) == 1
    return keys[0]


def _stored_artifact(hub, result):
    key = _artifact_key(hub, result.plugin_id, result.version)
    return hub.storage.objects[key]


def test_pack_publish_approve_and_original_download_preserve_skill_compatibility(hub):
    members = _members(hub)
    lock = _lock(members)
    content = _zip(_PACK_NAME, members=members, lock=lock)
    result = _publish(hub, content)
    assert result.plugin_type == "skill"
    assert result.publish_result == "pending_moderation"
    asset = hub.db.get(MarketAssetDB, result.plugin_id)
    assert _PACK_TAG in asset.tags
    assert not ANONYMOUS_VIEWER.can_download_skill_asset(asset, hub.db)

    moderated = _moderate(hub, result)
    assert moderated.publish_result == "publish_success"
    download = plugin.get_download_info(
        asset_id=result.plugin_id, version="1.0.0", db=hub.db, storage=hub.storage,
        viewer=ANONYMOUS_VIEWER, is_cli_download=True, counted=False,
    )
    original_key = download.download_url.removeprefix("https://storage.invalid/")
    original = hub.storage.objects[original_key][0]
    assert original == content
    assert download.plugin_type == "skill"
    assert download.checksum_sha256 == hashlib.sha256(original).hexdigest()
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        root = f"{_PACK_NAME}/{_PACK_NAME}/"
        assert "kind: skillpack" in archive.read(root + "SKILL.md").decode()
        assert json.loads(archive.read(root + "skillpack.lock.json")) == lock
        assert set(archive.namelist()) == {
            f"{_PACK_NAME}/plugin.yaml", root + "SKILL.md", root + "skillpack.lock.json",
        }


@pytest.mark.parametrize("user", ["creator", "system-test"])
@pytest.mark.parametrize("failure", ["missing_lock", "bad_digest", "missing_asset", "wrong_order"])
def test_invalid_pack_rejected_before_upload_including_system_publisher(hub, user, failure):
    members = _members(hub)
    lock = _lock([dict(member) for member in members])
    if failure == "missing_lock":
        lock = None
    elif failure == "bad_digest":
        lock["members"][0]["artifact_sha256"] = "0" * 64
    elif failure == "missing_asset":
        lock["members"][0]["asset_id"] = "missing-member"
    else:
        lock["members"].reverse()
    content = _zip(_PACK_NAME, members=members, lock=lock)
    before = dict(hub.storage.objects)
    with pytest.raises(PublishError) as error:
        _publish(hub, content, user=user)
    assert error.value.error == "invalid_skillpack"
    assert hub.storage.objects == before
    assert hub.db.query(MarketAssetDB).filter_by(name=_PACK_NAME).count() == 0


def test_same_artifact_retry_revalidates_members_before_idempotent_return(hub):
    members = _members(hub)
    content = _zip(_PACK_NAME, members=members, lock=_lock(members))
    result = _publish(hub, content)
    assert _publish(hub, content).deduplicated is True
    member = hub.db.get(MarketAssetDB, members[0]["asset_id"])
    member.visibility = "private"
    hub.db.commit()
    before = dict(hub.storage.objects)
    with pytest.raises(PublishError, match="公开"):
        _publish(hub, content)
    assert hub.storage.objects == before
    assert hub.db.query(MarketAssetVersionDB).filter_by(asset_id=result.plugin_id).count() == 1


@pytest.mark.parametrize("kind", ["team-skill", "swarm-skill"])
def test_approved_swarm_skill_cannot_be_a_pack_member(hub, kind):
    members = _members(hub, first_kind=kind)
    assert hub.db.get(MarketAssetDB, members[0]["asset_id"]).plugin_type == "swarmskill"
    content = _zip(_PACK_NAME, members=members, lock=_lock(members))
    before = dict(hub.storage.objects)
    with pytest.raises(PublishError, match="普通 Skill"):
        _publish(hub, content)
    assert hub.storage.objects == before


def test_valid_pack_keeps_system_publisher_existing_immediate_publish_behavior(hub):
    members = _members(hub)
    content = _zip(_PACK_NAME, members=members, lock=_lock(members))
    result = _publish(hub, content, user="system-test")
    assert result.publish_result == "publish_success"
    assert result.plugin_type == "skill"
    asset = hub.db.get(MarketAssetDB, result.plugin_id)
    assert ANONYMOUS_VIEWER.can_download_skill_asset(asset, hub.db)


@pytest.mark.parametrize("change", ["private", "deleted", "rejected", "overwritten", "missing_object"])
def test_approval_rechecks_changed_members_but_rejection_remains_available(hub, change):
    members = _members(hub)
    content = _zip(_PACK_NAME, members=members, lock=_lock(members))
    result = _publish(hub, content)
    member = hub.db.get(MarketAssetDB, members[0]["asset_id"])
    row = _version(hub, member.asset_id)
    if change == "private":
        member.visibility = "private"
    elif change == "deleted":
        hub.db.delete(row)
    elif change == "rejected":
        row.moderation_status = "REJECTED"
        row.publish_result = "publish_failed"
    else:
        key = _artifact_key(hub, member.asset_id)
        if change == "missing_object":
            hub.storage.delete_object(key)
        else:
            hub.storage.upload_bytes(_zip(member.name, body="# Changed member\n"), key)
    hub.db.commit()

    with pytest.raises(PublishError):
        _moderate(hub, result)
    pack_row = _version(hub, result.plugin_id)
    assert pack_row.moderation_status == "PENDING"
    assert pack_row.publish_result == "pending_moderation"
    assert not ANONYMOUS_VIEWER.can_download_skill_asset(hub.db.get(MarketAssetDB, result.plugin_id), hub.db)
    rejected = _moderate(hub, result, action="reject")
    assert rejected.publish_result == "publish_failed"
    assert pack_row.moderation_status == "REJECTED"


def test_ordinary_skill_still_uses_existing_review_and_download_flow(hub):
    content = _zip("ordinary-report")
    result = _publish(hub, content)
    assert result.plugin_type == "skill"
    assert result.publish_result == "pending_moderation"
    _moderate(hub, result)
    download = plugin.get_download_info(
        asset_id=result.plugin_id, version="1.0.0", db=hub.db, storage=hub.storage,
        viewer=ANONYMOUS_VIEWER, is_cli_download=True, counted=False,
    )
    assert download.checksum_sha256 == hashlib.sha256(content).hexdigest()
    assert _PACK_TAG not in (hub.db.get(MarketAssetDB, result.plugin_id).tags or [])


def test_ordinary_skill_approval_already_requires_storage_for_listing_sync(hub, monkeypatch):
    result = _publish(hub, _zip("ordinary-report"))
    # Bypass only the newly added dependency gate to exercise the existing path.
    monkeypatch.setattr(plugin, "validate_stored_skillpack", lambda **kwargs: None)

    def unavailable(**kwargs):
        raise OSError("test storage outage")

    monkeypatch.setattr(hub.storage, "get_object", unavailable)
    with pytest.raises(PublishError) as error:
        _moderate(hub, result)
    assert error.value.error == "storage_error"
    hub.db.rollback()
    assert _version(hub, result.plugin_id).moderation_status == "PENDING"


def test_approval_reads_member_artifacts_before_acquiring_version_write_lock(hub, monkeypatch):
    members = _members(hub)
    result = _publish(hub, _zip(_PACK_NAME, members=members, lock=_lock(members)))
    events = []
    member_keys = {_artifact_key(hub, member["asset_id"]) for member in members}
    get_object = hub.storage.get_object
    lock_version = plugin.MarketAssetVersionRepository.lock_version_for_update

    def tracked_read(*, Bucket, Key):
        if Key in member_keys:
            assert "lock" not in events
        events.append("read" if "lock" not in events else "listing_read")
        return get_object(Bucket=Bucket, Key=Key)

    def tracked_lock(*args, **kwargs):
        events.append("lock")
        return lock_version(*args, **kwargs)

    monkeypatch.setattr(hub.storage, "get_object", tracked_read)
    monkeypatch.setattr(plugin.MarketAssetVersionRepository, "lock_version_for_update", tracked_lock)
    assert _moderate(hub, result).publish_result == "publish_success"
    assert events == ["read", "read", "read", "lock", "listing_read"]


@pytest.mark.parametrize("field,value", [
    ("artifact_sha256", "f" * 64), ("file_path", "changed/"),
    ("status", "INACTIVE"), ("moderation_status", "REJECTED"),
])
def test_approval_rejects_version_changed_during_artifact_validation(hub, monkeypatch, field, value):
    members = _members(hub)
    result = _publish(hub, _zip(_PACK_NAME, members=members, lock=_lock(members)))
    last_member_key = _artifact_key(hub, members[-1]["asset_id"])
    get_object = hub.storage.get_object

    def changed_during_read(*, Bucket, Key):
        response = get_object(Bucket=Bucket, Key=Key)
        if Key == last_member_key:
            setattr(_version(hub, result.plugin_id), field, value)
            hub.db.commit()
        return response

    monkeypatch.setattr(hub.storage, "get_object", changed_during_read)
    with pytest.raises(PublishError) as error:
        _moderate(hub, result)
    assert error.value.error == "moderation_version_changed"
    assert _version(hub, result.plugin_id).publish_result == "pending_moderation"


def test_pack_form_override_approves_and_downloads_with_stored_artifact_checksum(hub):
    members = _members(hub)
    lock = _lock(members)
    uploaded = _zip(_PACK_NAME, members=members, lock=lock)
    result = _publish(hub, uploaded, plugin_version="1.0.0", display_name="Weekly report")
    assert result.publish_result == "pending_moderation"
    _moderate(hub, result)

    downloaded = plugin.get_download_info(
        asset_id=result.plugin_id, version=result.version, db=hub.db, storage=hub.storage,
        viewer=ANONYMOUS_VIEWER, is_cli_download=True, counted=False,
    )
    key = downloaded.download_url.removeprefix("https://storage.invalid/")
    content, metadata = hub.storage.objects[key]
    checksum = hashlib.sha256(content).hexdigest()
    assert content != uploaded
    assert downloaded.checksum_sha256 == checksum
    assert _version(hub, result.plugin_id).artifact_sha256 == checksum
    assert metadata["sha256"] == checksum
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifests = [name for name in archive.namelist() if name.endswith("/plugin.yaml")]
        assert len(manifests) == 1
        manifest_path = manifests[0]
        outer = manifest_path.rsplit("/", 1)[0]
        manifest = yaml.safe_load(archive.read(manifest_path))
        assert manifest["display_name"] == "Weekly report"
        assert _PACK_TAG in manifest["metadata"]["tags"]
        assert "kind: skillpack" in archive.read(f"{outer}/{_PACK_NAME}/SKILL.md").decode()
        assert json.loads(archive.read(f"{outer}/{_PACK_NAME}/skillpack.lock.json")) == lock


def test_same_upload_and_form_override_remains_idempotent(hub):
    members = _members(hub)
    uploaded = _zip(_PACK_NAME, members=members, lock=_lock(members))
    first = _publish(hub, uploaded, plugin_version="1.0.0", display_name="Weekly report")
    before = dict(hub.storage.objects)
    second = _publish(hub, uploaded, plugin_version="1.0.0", display_name="Weekly report")
    assert second.deduplicated is True
    assert second.plugin_id == first.plugin_id
    assert hub.storage.objects == before
    assert hub.db.query(MarketAssetVersionDB).filter_by(asset_id=first.plugin_id).count() == 1
    stored, _ = _stored_artifact(hub, first)
    assert _version(hub, first.plugin_id).artifact_sha256 == hashlib.sha256(stored).hexdigest()


def test_same_upload_with_changed_form_metadata_conflicts_without_overwrite(hub):
    members = _members(hub)
    uploaded = _zip(_PACK_NAME, members=members, lock=_lock(members))
    first = _publish(hub, uploaded, plugin_version="1.0.0", display_name="Weekly report")
    before = dict(hub.storage.objects)
    with pytest.raises(PublishError) as error:
        _publish(hub, uploaded, plugin_version="1.0.0", display_name="Changed weekly report")
    assert error.value.error == "version_conflict"
    assert hub.storage.objects == before
    assert hub.db.get(MarketAssetDB, first.plugin_id).display_name == "Weekly report"


def test_form_rewrapped_ordinary_skill_uses_download_checksum_as_pack_member(hub):
    members = _members(hub)
    uploaded = _zip("custom-report")
    member_result = _publish(hub, uploaded, plugin_version="1.0.0", display_name="Custom report")
    stored, metadata = _stored_artifact(hub, member_result)
    checksum = hashlib.sha256(stored).hexdigest()
    assert stored != uploaded
    assert _version(hub, member_result.plugin_id).artifact_sha256 == checksum
    assert metadata["sha256"] == checksum
    _moderate(hub, member_result)
    downloaded = plugin.get_download_info(
        asset_id=member_result.plugin_id, version=member_result.version,
        db=hub.db, storage=hub.storage, viewer=ANONYMOUS_VIEWER,
        is_cli_download=True, counted=False,
    )
    assert downloaded.checksum_sha256 == checksum
    members[0] = {
        "name": "custom-report", "asset_id": member_result.plugin_id,
        "version": member_result.version, "artifact_sha256": downloaded.checksum_sha256,
    }
    pack = _publish(hub, _zip(_PACK_NAME, members=members, lock=_lock(members)))
    assert _moderate(hub, pack).publish_result == "publish_success"


@pytest.mark.parametrize("tags", [None, ["reports"]])
@pytest.mark.parametrize("is_pack", [False, True])
def test_form_rewrapping_is_deterministic_and_preserves_other_package_files(hub, tags, is_pack):
    members = _members(hub)
    uploaded_skill = _zip(_PACK_NAME, members=members, lock=_lock(members)) if is_pack else _zip(_PACK_NAME)
    stream = io.BytesIO(uploaded_skill)
    attachments = {
        f"{_PACK_NAME}/README.md": b"# Package publishing notes\n",
        f"{_PACK_NAME}/icon.svg": b'<svg xmlns="http://www.w3.org/2000/svg"/>',
    }
    if not is_pack:
        attachments[f"{_PACK_NAME}/{_PACK_NAME}/references/example.md"] = b"# Input example\n"
    with zipfile.ZipFile(stream, "a") as archive:
        for name, content in attachments.items():
            archive.writestr(name, content)
    uploaded = stream.getvalue()
    if tags is not None and is_pack:
        tags = [_PACK_TAG, *tags]
    options = {
        "filename": "package.zip", "default_author": "creator",
        "overrides": PublishMetadataOverrides(version="1.0.0", display_name="Weekly report", tags=tags),
    }
    prepared = prepare_publish_zip_content(uploaded, **options)
    assert prepare_publish_zip_content(uploaded, **options) == prepared
    with zipfile.ZipFile(io.BytesIO(prepared)) as archive:
        for name, content in attachments.items():
            assert archive.read(name) == content
        manifest = yaml.safe_load(archive.read(f"{_PACK_NAME}/plugin.yaml"))
        original_tags = [_PACK_TAG] if is_pack else []
        assert manifest["metadata"]["tags"] == (original_tags if tags is None else tags)
        with zipfile.ZipFile(io.BytesIO(uploaded)) as original:
            for name in (("SKILL.md", "skillpack.lock.json") if is_pack else ("SKILL.md",)):
                path = f"{_PACK_NAME}/{_PACK_NAME}/{name}"
                assert archive.read(path) == original.read(path)
    # Both fixtures must also be accepted by the actual publication gate.
    assert _publish(hub, prepared).publish_result == "pending_moderation"
