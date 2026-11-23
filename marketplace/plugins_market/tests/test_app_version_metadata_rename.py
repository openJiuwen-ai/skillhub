# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""App version detail metadata must survive a package rename between versions.

Regression: ``_load_app_package_metadata`` built the object key from the
*current* asset name and bailed out on a HEAD miss, so browsing an older
version whose package was published under a previous name returned nothing.
The download path resolves through the version prefix (which carries no
name); the detail path must do the same.
"""

from __future__ import annotations

from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.services.plugin import _load_app_package_metadata


class _HeadStorage:
    """Minimal storage double backed by an explicit object map."""

    def __init__(self, objects: dict[str, dict]) -> None:
        self._objects = dict(objects)

    def head_object(self, key: str) -> dict:
        head = self._objects.get(key)
        return head if head is not None else {"success": False, "not_found": True}

    def list_keys(self, prefix: str) -> list[str]:
        return [key for key in self._objects if key.startswith(prefix)]


def _asset(name: str) -> MarketAssetDB:
    return MarketAssetDB(
        asset_id="app-gomoku",
        asset_type="plugin",
        name=name,
        display_name=name,
        publisher_id="owner",
        publisher_name="Owner",
        status="PUBLISHED",
        plugin_type="app",
        latest_version="2.0",
        visibility="public",
        view_count=0,
        install_count=0,
        like_count=0,
        star_count=0,
        review_count=0,
        average_rating=0.0,
        create_time=1,
        update_time=2,
    )


def _version(version: str, file_path: str) -> MarketAssetVersionDB:
    return MarketAssetVersionDB(
        version_id=f"v-{version}",
        asset_id="app-gomoku",
        version=version,
        file_path=file_path,
        create_time=1,
        moderation_status="APPROVED",
        publish_result="publish_success",
        status="ACTIVE",
    )


def _head(size: int, checksum: str) -> dict:
    return {
        "success": True,
        "size": size,
        "metadata": {
            "sha256": checksum,
            "size": str(size),
            "app-source-size": str(size * 2),
            "app-permissions": "camera,mic",
            "app-requires-project": "true",
            "app-has-server-script": "false",
        },
    }


def test_old_version_metadata_survives_package_rename() -> None:
    # v1 was published as "gomoku"; the asset was later renamed to "gomoku2"
    # for v2. The v1 object is therefore still keyed by the old name.
    old_key = "apps/owner/app-gomoku/1.0/gomoku_1.0.zip"
    storage = _HeadStorage({old_key: _head(size=1234, checksum="deadbeef")})
    version = _version("1.0", "apps/owner/app-gomoku/1.0/")

    metadata = _load_app_package_metadata(_asset("gomoku2"), version, storage)

    assert metadata is not None
    assert metadata["file_size"] == 1234
    assert metadata["source_size"] == 2468
    assert metadata["permissions"] == ["camera", "mic"]
    assert metadata["requires_project"] is True
    assert metadata["has_server_script"] is False


def test_metadata_uses_current_name_key_when_present() -> None:
    # No rename: the name-derived key exists and is used directly.
    key = "apps/owner/app-gomoku/1.0/gomoku_1.0.zip"
    storage = _HeadStorage({key: _head(size=10, checksum="aa")})
    version = _version("1.0", "apps/owner/app-gomoku/1.0/")

    metadata = _load_app_package_metadata(_asset("gomoku"), version, storage)

    assert metadata is not None
    assert metadata["file_size"] == 10


def test_metadata_absent_when_no_object_at_all() -> None:
    # Neither the name-derived key nor the version prefix yields an object.
    storage = _HeadStorage({})
    version = _version("1.0", "apps/owner/app-gomoku/1.0/")

    assert _load_app_package_metadata(_asset("gomoku"), version, storage) is None
