# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

from plugins_market.services.plugin import (
    _build_raw_artifact_key,
    _ensure_non_cli_raw_artifact,
    _invalidate_force_overwrite_raw_artifact,
)


class _MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.config = SimpleNamespace(bucket_name="test-bucket")
        self.s3_client = self

    def head_object(self, key: str) -> dict:
        if key not in self.objects:
            return {"success": False, "not_found": True}
        body, metadata = self.objects[key]
        return {"success": True, "size": len(body), "metadata": dict(metadata)}

    def delete_object(self, key: str) -> dict:
        self.objects.pop(key, None)
        return {"success": True, "key": key}

    def get_object(self, **kwargs) -> dict:
        object_key = str(kwargs["Key"])
        body, _metadata = self.objects[object_key]
        return {"Body": io.BytesIO(body)}

    def put_object(self, **kwargs) -> None:
        object_key = str(kwargs["Key"])
        payload = kwargs["Body"]
        metadata = kwargs.get("Metadata") or {}
        data = payload.read() if hasattr(payload, "read") else payload
        self.objects[object_key] = (data, dict(metadata))


def _skill_zip(*, permissions_payload: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "demo-skill/plugin.yaml",
            "name: demo-skill\nversion: 1.0.0\ndisplay_name: Demo\n"
            "description: demo\nruntime:\n  type: skill\n",
        )
        zf.writestr(
            "demo-skill/demo-skill/SKILL.md",
            "---\nname: demo-skill\ndescription: Demo skill\n---\n# Demo\n",
        )
        zf.writestr(
            "demo-skill/demo-skill/skill_permissions.json",
            json.dumps(permissions_payload, ensure_ascii=False),
        )
    return buf.getvalue()


def _zip_member_text(content: bytes, filename: str) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        member = next(
            name
            for name in zf.namelist()
            if name.replace("\\", "/").rstrip("/").split("/")[-1] == filename
        )
        return zf.read(member).decode("utf-8")


def test_force_overwrite_rebuilds_raw_zip_skill_permissions() -> None:
    storage = _MemoryStorage()
    origin_key = "skills/u1/a1/1.0.0/demo-skill_1.0.0.zip"
    raw_key = _build_raw_artifact_key("u1", "a1", "1.0.0", "demo-skill", "skill")

    old_zip = _skill_zip(permissions_payload={"mode": "old"})
    new_zip = _skill_zip(permissions_payload={"mode": "new"})
    storage.put_object(
        Bucket="test-bucket",
        Key=origin_key,
        Body=old_zip,
        Metadata={"sha256": hashlib.sha256(old_zip).hexdigest(), "size": str(len(old_zip))},
    )

    first_key, _size, _checksum = _ensure_non_cli_raw_artifact(
        storage=storage,
        old_key=origin_key,
        raw_key=raw_key,
        asset_name="demo-skill",
        version="1.0.0",
        plugin_type="skill",
    )
    assert first_key == raw_key
    assert json.loads(_zip_member_text(storage.objects[raw_key][0], "skill_permissions.json")) == {
        "mode": "old"
    }

    storage.put_object(
        Bucket="test-bucket",
        Key=origin_key,
        Body=new_zip,
        Metadata={"sha256": hashlib.sha256(new_zip).hexdigest(), "size": str(len(new_zip))},
    )
    _invalidate_force_overwrite_raw_artifact(
        storage,
        publisher_id="u1",
        asset_id="a1",
        version="1.0.0",
        name="demo-skill",
        plugin_type="skill",
    )

    _ensure_non_cli_raw_artifact(
        storage=storage,
        old_key=origin_key,
        raw_key=raw_key,
        asset_name="demo-skill",
        version="1.0.0",
        plugin_type="skill",
    )
    assert json.loads(_zip_member_text(storage.objects[raw_key][0], "skill_permissions.json")) == {
        "mode": "new"
    }


def test_raw_artifact_rebuilds_when_source_sha_mismatches() -> None:
    storage = _MemoryStorage()
    origin_key = "skills/u1/a1/1.0.0/demo-skill_1.0.0.zip"
    raw_key = "skills/u1/a1/1.0.0/demo-skill_1.0.0.raw.zip"

    old_zip = _skill_zip(permissions_payload={"mode": "old"})
    new_zip = _skill_zip(permissions_payload={"mode": "new"})
    storage.put_object(
        Bucket="test-bucket",
        Key=origin_key,
        Body=old_zip,
        Metadata={"sha256": hashlib.sha256(old_zip).hexdigest(), "size": str(len(old_zip))},
    )
    _ensure_non_cli_raw_artifact(
        storage=storage,
        old_key=origin_key,
        raw_key=raw_key,
        asset_name="demo-skill",
        version="1.0.0",
        plugin_type="skill",
    )

    storage.put_object(
        Bucket="test-bucket",
        Key=origin_key,
        Body=new_zip,
        Metadata={"sha256": hashlib.sha256(new_zip).hexdigest(), "size": str(len(new_zip))},
    )
    _ensure_non_cli_raw_artifact(
        storage=storage,
        old_key=origin_key,
        raw_key=raw_key,
        asset_name="demo-skill",
        version="1.0.0",
        plugin_type="skill",
    )
    assert json.loads(_zip_member_text(storage.objects[raw_key][0], "skill_permissions.json")) == {
        "mode": "new"
    }


def test_legacy_raw_zip_without_source_sha_rebuilds_after_origin_change() -> None:
    storage = _MemoryStorage()
    origin_key = "skills/u1/a1/1.0.0/demo-skill_1.0.0.zip"
    raw_key = "skills/u1/a1/1.0.0/demo-skill_1.0.0.raw.zip"

    old_zip = _skill_zip(permissions_payload={"mode": "old"})
    new_zip = _skill_zip(permissions_payload={"mode": "new"})
    storage.put_object(
        Bucket="test-bucket",
        Key=origin_key,
        Body=new_zip,
        Metadata={"sha256": hashlib.sha256(new_zip).hexdigest(), "size": str(len(new_zip))},
    )
    storage.put_object(
        Bucket="test-bucket",
        Key=raw_key,
        Body=_skill_zip(permissions_payload={"mode": "old"}),
        Metadata={
            "sha256": hashlib.sha256(old_zip).hexdigest(),
            "size": str(len(old_zip)),
        },
    )

    _ensure_non_cli_raw_artifact(
        storage=storage,
        old_key=origin_key,
        raw_key=raw_key,
        asset_name="demo-skill",
        version="1.0.0",
        plugin_type="skill",
    )
    rebuilt = storage.objects[raw_key][0]
    assert json.loads(_zip_member_text(rebuilt, "skill_permissions.json")) == {"mode": "new"}
    assert storage.objects[raw_key][1].get("source_sha256") == hashlib.sha256(new_zip).hexdigest()

