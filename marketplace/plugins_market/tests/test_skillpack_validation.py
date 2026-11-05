# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Server-side SkillPack dependency checks use real ZIPs and versioned DB rows."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.errors import PublishError
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.services import skillpack_validation as validation


def _zip(name, *, kind=None, lock=None, names=None, tags=None, extra=None, version="1.0.0",
         manifest_name=None, runtime_type="skill"):
    manifest = {
        "name": manifest_name if manifest_name is not None else name,
        "version": version, "description": "test", "display_name": name,
        "runtime": {"type": runtime_type}, "metadata": {"author": "test", "tags": tags or []},
    }
    front = {"name": name, "description": "test"}
    if kind is not None:
        front["kind"] = kind
    if kind in ("swarm-skill", "team-skill"):
        front["roles"] = [{"id": "leader"}, {"id": "worker"}]
    if names is not None:
        front["skills"] = names
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(f"{name}/plugin.yaml", yaml.safe_dump(manifest))
        archive.writestr(f"{name}/{name}/SKILL.md", "---\n" + yaml.safe_dump(front) + "---\n# Test\n")
        if lock is not None:
            encoded_lock = lock if isinstance(lock, (str, bytes)) else json.dumps(lock)
            archive.writestr(f"{name}/{name}/skillpack.lock.json", encoded_lock)
        for path, content in (extra or {}).items():
            archive.writestr(path, content)
    return output.getvalue()


@pytest.fixture
def setup_pack():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    assets, versions, originals, members = [], [], {}, []
    for index, name in enumerate(["member-a", "member-b"]):
        content = _zip(name)
        digest = hashlib.sha256(content).hexdigest()
        asset = MarketAssetDB(
            asset_id=f"asset-{index}", asset_type="plugin", plugin_type="skill", name=name,
            display_name=name, publisher_id="other-author", publisher_name="Other", visibility="public",
            status="PUBLISHED", latest_version="1.0.0", public_latest_version="1.0.0",
            moderation_status="APPROVED", publish_result="publish_success",
        )
        version = MarketAssetVersionDB(
            asset_id=asset.asset_id, version_id=f"version-{index}", version="1.0.0", status="ACTIVE",
            moderation_status="APPROVED", publish_result="publish_success", artifact_sha256=digest,
        )
        assets.append(asset)
        versions.append(version)
        originals[asset.asset_id] = content
        members.append({"name": name, "asset_id": asset.asset_id, "version": "1.0.0", "artifact_sha256": digest})
        db.add_all([asset, version])
    db.commit()
    lock = {"schema_version": "openjiuwen.skillpack.lock/v1", "pack": {"name": "test-pack", "version": "1.0.0"},
            "members": members}
    result = SimpleNamespace(db=db, assets=assets, versions=versions, originals=originals, lock=lock, reads=[])

    def reader(asset, version):
        result.reads.append((asset.asset_id, version.version))
        return result.originals[asset.asset_id]

    result.reader = reader
    yield result
    db.close()
    engine.dispose()


def _pack(state, **kwargs):
    return _zip("test-pack", kind="skillpack", lock=state.lock, names=["member-a", "member-b"], **kwargs)


def _validate(state, content=None):
    validation.validate_skillpack_for_publish(
        content=content if content is not None else _pack(state), name="test-pack", version="1.0.0",
        plugin_type="skill", db=state.db, read_artifact=state.reader,
    )


def test_valid_pack_uses_exact_public_member_versions(setup_pack):
    _validate(setup_pack)
    assert setup_pack.reads == [("asset-0", "1.0.0"), ("asset-1", "1.0.0")]


def test_ordinary_skill_does_not_query_members(setup_pack):
    _validate(setup_pack, _zip("test-pack"))
    assert setup_pack.reads == []


@pytest.mark.parametrize("runtime_type", ["Skill", " skill "])
def test_ordinary_skill_preserves_existing_runtime_normalization(setup_pack, runtime_type):
    _validate(setup_pack, _zip("test-pack", runtime_type=runtime_type))
    assert setup_pack.reads == []


def test_ordinary_skill_preserves_existing_manifest_name_normalization(setup_pack):
    _validate(setup_pack, _zip("test-pack", manifest_name=" test-pack "))
    assert setup_pack.reads == []


def test_pack_uses_existing_manifest_version_and_name_normalization(setup_pack):
    _validate(setup_pack, _pack(setup_pack, version=" 1.0.0 ", manifest_name=" test-pack "))
    assert len(setup_pack.reads) == 2


def test_reserved_tag_uses_existing_case_and_whitespace_normalization(setup_pack):
    with pytest.raises(PublishError, match="kind"):
        _validate(setup_pack, _zip("test-pack", tags=[" OpenJiuwen-SkillPack-v1 "]))


@pytest.mark.parametrize("kind", [None, "skill", "team-skill"])
def test_lock_cannot_be_disguised_as_ordinary_skill(setup_pack, kind):
    with pytest.raises(PublishError, match="kind"):
        _validate(setup_pack, _zip("test-pack", kind=kind, lock=setup_pack.lock))


def test_reserved_tag_cannot_replace_root_kind(setup_pack):
    with pytest.raises(PublishError, match="kind"):
        _validate(setup_pack, _zip("test-pack", tags=["openjiuwen-skillpack-v1"]))


def test_pack_without_tag_is_still_validated(setup_pack):
    del setup_pack.lock["members"][0]["asset_id"]
    with pytest.raises(PublishError, match="asset_id"):
        _validate(setup_pack)


def test_lock_actual_bytes_are_bounded_even_when_declared_size_is_small(setup_pack, monkeypatch):
    monkeypatch.setattr(validation, "MAX_YAML_BYTES", 1024)
    original_open = zipfile.ZipFile.open

    def forged_lock_stream(archive, name, *args, **kwargs):
        if isinstance(name, str) and name.endswith("/skillpack.lock.json"):
            assert archive.getinfo(name).file_size < 1024
            return io.BytesIO(b" " * 1025)
        return original_open(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", forged_lock_stream)
    with pytest.raises(PublishError) as error:
        _validate(setup_pack)
    assert error.value.error == "zip_too_large"
    assert setup_pack.reads == []


@pytest.mark.parametrize("names", [None, [], ["member-a"], ["member-a", "member-a"],
                                  ["test-pack", "member-a"], ["../escape", "member-a"], [0, "member-a"]])
def test_invalid_declared_members(setup_pack, names):
    with pytest.raises(PublishError):
        _validate(setup_pack, _zip("test-pack", kind="skillpack", lock=setup_pack.lock, names=names))


@pytest.mark.parametrize("lock", [None, "not-json", "{", [], {"schema_version": "v2"},
                                 '{"schema_version":"x","schema_version":"y"}'])
def test_missing_or_malformed_lock(setup_pack, lock):
    with pytest.raises(PublishError):
        _validate(setup_pack, _zip("test-pack", kind="skillpack", lock=lock, names=["member-a", "member-b"]))


@pytest.mark.parametrize("field,value", [("name", "renamed"), ("version", "2.0.0"), ("version", "latest")])
def test_pack_lock_identity_must_match(setup_pack, field, value):
    setup_pack.lock["pack"][field] = value
    with pytest.raises(PublishError):
        _validate(setup_pack)


@pytest.mark.parametrize("field,value", [("name", "wrong"), ("asset_id", ""), ("version", "latest"),
                                      ("version", "^1.0.0"), ("artifact_sha256", "abc"), ("artifact_sha256", 123)])
def test_member_lock_fields_checked(setup_pack, field, value):
    setup_pack.lock["members"][0][field] = value
    with pytest.raises(PublishError):
        _validate(setup_pack)


def test_lock_order_must_match_declared_order(setup_pack):
    setup_pack.lock["members"].reverse()
    with pytest.raises(PublishError, match="顺序"):
        _validate(setup_pack)


def test_duplicate_asset_id_rejected(setup_pack):
    setup_pack.lock["members"][1]["asset_id"] = "asset-0"
    with pytest.raises(PublishError, match="重复"):
        _validate(setup_pack)


@pytest.mark.parametrize("field,value", [("visibility", "private"), ("status", "DELETED"),
                                      ("plugin_type", "swarmskill"), ("plugin_type", "skillpack"), ("name", "renamed")])
def test_member_asset_must_be_public_ordinary_skill(setup_pack, field, value):
    setattr(setup_pack.assets[0], field, value)
    setup_pack.db.commit()
    with pytest.raises(PublishError, match="member-a"):
        _validate(setup_pack)


@pytest.mark.parametrize("field,value", [("status", "DELETED"), ("moderation_status", "PENDING"),
                                      ("moderation_status", "REJECTED"), ("publish_result", "reviewing"),
                                      ("publish_result", "pending_moderation"), ("publish_result", "publish_failed")])
def test_member_version_must_be_approved_and_active(setup_pack, field, value):
    setattr(setup_pack.versions[0], field, value)
    setup_pack.db.commit()
    with pytest.raises(PublishError, match="member-a"):
        _validate(setup_pack)


def test_legacy_public_member_without_moderation_metadata_accepted(setup_pack):
    setup_pack.assets[0].public_latest_version = None
    setup_pack.assets[0].publish_result = None
    setup_pack.assets[0].moderation_status = None
    setup_pack.versions[0].publish_result = None
    setup_pack.versions[0].moderation_status = None
    setup_pack.versions[0].artifact_sha256 = None
    setup_pack.db.commit()
    _validate(setup_pack)


def test_approved_old_version_not_hidden_by_pending_new_version(setup_pack):
    setup_pack.assets[0].latest_version = "2.0.0"
    setup_pack.assets[0].publish_result = "pending_moderation"
    setup_pack.db.commit()
    _validate(setup_pack)


def test_missing_exact_version_is_not_replaced_with_latest(setup_pack):
    setup_pack.lock["members"][0]["version"] = "0.9.0"
    with pytest.raises(PublishError, match="版本不存在"):
        _validate(setup_pack)


@pytest.mark.parametrize("kind", ["skillpack", "swarm-skill", "team-skill", "unknown"])
def test_db_skill_type_does_not_override_member_content(setup_pack, kind):
    content = _zip("member-a", kind=kind)
    digest = hashlib.sha256(content).hexdigest()
    setup_pack.originals["asset-0"] = content
    setup_pack.lock["members"][0]["artifact_sha256"] = digest
    setup_pack.versions[0].artifact_sha256 = digest
    with pytest.raises(PublishError, match="不是单 Agent"):
        _validate(setup_pack)


def test_real_zip_hash_checked_even_when_db_matches_lock(setup_pack):
    setup_pack.originals["asset-0"] += b"modified"
    with pytest.raises(PublishError, match="摘要与 lock"):
        _validate(setup_pack)


def test_db_hash_mismatch_rejected(setup_pack):
    setup_pack.versions[0].artifact_sha256 = "a" * 64
    with pytest.raises(PublishError, match="Hub 版本记录"):
        _validate(setup_pack)


def test_member_embedded_manifest_version_matches_lock(setup_pack):
    content = _zip("member-a", version="2.0.0")
    digest = hashlib.sha256(content).hexdigest()
    setup_pack.originals["asset-0"] = content
    setup_pack.lock["members"][0]["artifact_sha256"] = digest
    setup_pack.versions[0].artifact_sha256 = digest
    with pytest.raises(PublishError, match="制品版本"):
        _validate(setup_pack)


def test_pack_does_not_embed_member_sources(setup_pack):
    with pytest.raises(PublishError, match="只能包含"):
        _validate(setup_pack, _pack(setup_pack, extra={"test-pack/test-pack/member-a/code.py": "print(1)"}))


def test_stored_pack_rechecks_member_review_state(setup_pack):
    content = _pack(setup_pack)
    asset = SimpleNamespace(asset_id="pack", name="test-pack", plugin_type="skill")
    row = SimpleNamespace(version="1.0.0", artifact_sha256=hashlib.sha256(content).hexdigest())
    setup_pack.originals["pack"] = content
    setup_pack.versions[0].moderation_status = "REJECTED"
    with pytest.raises(PublishError, match="member-a"):
        validation.validate_stored_skillpack(asset=asset, version_row=row, db=setup_pack.db,
                                            read_artifact=setup_pack.reader)


def test_stored_pack_hash_change_rejected(setup_pack):
    asset = SimpleNamespace(asset_id="pack", name="test-pack", plugin_type="skill")
    row = SimpleNamespace(version="1.0.0", artifact_sha256="a" * 64)
    setup_pack.originals["pack"] = _pack(setup_pack)
    with pytest.raises(PublishError, match="待审核原始制品"):
        validation.validate_stored_skillpack(asset=asset, version_row=row, db=setup_pack.db,
                                            read_artifact=setup_pack.reader)


def test_stored_ordinary_skill_keeps_legacy_checksum_behavior(setup_pack):
    asset = SimpleNamespace(asset_id="ordinary", name="ordinary", plugin_type="skill")
    row = SimpleNamespace(version="1.0.0", artifact_sha256="old-upload-checksum")
    setup_pack.originals["ordinary"] = _zip("ordinary")
    validation.validate_stored_skillpack(asset=asset, version_row=row, db=setup_pack.db,
                                        read_artifact=setup_pack.reader)
    assert setup_pack.reads == [("ordinary", "1.0.0")]


def test_other_asset_type_unchanged(setup_pack):
    validation.validate_skillpack_for_publish(content=b"other format", name="agent", version="1.0.0",
                                             plugin_type="agent-plugin", db=setup_pack.db,
                                             read_artifact=setup_pack.reader)
    assert setup_pack.reads == []


def test_pack_member_count_is_bounded(setup_pack):
    names = [f"member-{i}" for i in range(129)]
    with pytest.raises(PublishError):
        _validate(setup_pack, _zip("test-pack", kind="skillpack", names=names, lock=setup_pack.lock))


def test_duplicate_zip_entry_rejected(setup_pack):
    with pytest.warns(UserWarning, match="Duplicate name"):
        content = _pack(setup_pack, extra={"test-pack/test-pack/SKILL.md": "replacement"})
    with pytest.raises(PublishError, match="重复"):
        _validate(setup_pack, content)


def test_zip_traversal_rejected(setup_pack):
    with pytest.raises(PublishError):
        _validate(setup_pack, _pack(setup_pack, extra={"../escape": "bad"}))


@pytest.mark.parametrize("kind", [False, 123, ["skillpack"], {"type": "skillpack"}])
def test_non_string_root_kind_rejected(setup_pack, kind):
    with pytest.raises(PublishError, match="kind 必须是字符串"):
        _validate(setup_pack, _zip("test-pack", kind=kind))


def test_nested_skill_document_cannot_replace_manifest_root(setup_pack):
    decoy = "---\nname: nested\ndescription: test\nkind: skillpack\n---\n"
    content = _zip("test-pack", extra={"test-pack/test-pack/references/nested/SKILL.md": decoy})
    _validate(setup_pack, content)
    assert setup_pack.reads == []


def test_pack_lock_must_be_beside_root_document(setup_pack):
    content = _zip("test-pack", kind="skillpack", names=["member-a", "member-b"],
                   extra={"test-pack/skillpack.lock.json": json.dumps(setup_pack.lock)})
    with pytest.raises(PublishError, match="缺少 skillpack.lock.json"):
        _validate(setup_pack, content)
