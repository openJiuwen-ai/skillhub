# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""版本预览图：包内 assets/preview.png 的提取、校验与详情返回。"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.errors import PublishError
from plugins_market.core.viewer_context import ANONYMOUS_VIEWER
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.services.plugin import get_plugin_version_detail_service
from plugins_market.validation import extract_plugin_metadata
from plugins_market.validation.constants import (
    PREVIEW_PUBLISH_MAX_BYTES,
    PREVIEW_PUBLISH_MAX_EDGE_PX,
)
from plugins_market.validation.icon_png_optimize import assert_png_decodable
from plugins_market.validation.preview_png_optimize import optimize_png_preview_bytes


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 32), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _noise_png(size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.effect_noise(size, 100).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _solid_png(size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _system_plugin_zip(
    *,
    preview_path: str | None,
    avatar: str | None = None,
    avatar_png: bool = False,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(
            "demo-plugin/plugin.yaml",
            "name: demo-plugin\nversion: 1.0.0\ndisplay_name: Demo\n"
            "description: demo system plugin\nruntime:\n  type: system-plugin\nmetadata:\n"
            "  author: tester\n  tags: []\n",
        )
        display: dict[str, object] = {
            "name": {"zh": "Demo", "en": "Demo"},
            "description": {"zh": "Demo system plugin", "en": "Demo system plugin"},
        }
        if avatar:
            display["avatar"] = avatar
        zf.writestr(
            "demo-plugin/demo-plugin/manifest.json",
            json.dumps(
                {
                    "schemaVersion": 1,
                    "package_type": "extension",
                    "package": {
                        "id": "demo-plugin",
                        "version": "1.0.0",
                        "domain": "gateway",
                    },
                    "display": display,
                },
                ensure_ascii=False,
            ),
        )
        if avatar_png:
            zf.writestr("demo-plugin/demo-plugin/assets/avatar.png", _png())
        if preview_path:
            zf.writestr(f"demo-plugin/demo-plugin/{preview_path}", _png())
    return buf.getvalue()


class _Storage:
    @staticmethod
    def presigned_get_url(key, download_filename=None):
        return f"http://example.test/{key}"

    @staticmethod
    def read_bytes(key):
        return None


def test_preview_assets_path_extracted() -> None:
    meta = extract_plugin_metadata(_system_plugin_zip(preview_path="assets/preview.png"))
    assert meta["preview_bytes"].startswith(b"\x89PNG")


def test_preview_root_path_fallback() -> None:
    meta = extract_plugin_metadata(_system_plugin_zip(preview_path="preview.png"))
    assert meta["preview_bytes"].startswith(b"\x89PNG")


def test_preview_missing_is_empty() -> None:
    meta = extract_plugin_metadata(_system_plugin_zip(preview_path=None))
    assert meta["preview_bytes"] == b""


def test_icon_falls_back_to_assets_avatar_png() -> None:
    meta = extract_plugin_metadata(
        _system_plugin_zip(
            preview_path=None,
            avatar="assets/avatar.svg",
            avatar_png=True,
        )
    )
    assert meta["icon_bytes"].startswith(b"\x89PNG")


def test_invalid_preview_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(
            "demo-plugin/plugin.yaml",
            "name: demo-plugin\nversion: 1.0.0\ndisplay_name: Demo\n"
            "description: demo system plugin\nruntime:\n  type: system-plugin\nmetadata:\n"
            "  author: tester\n  tags: []\n",
        )
        zf.writestr(
            "demo-plugin/demo-plugin/manifest.json",
            json.dumps(
                {
                    "schemaVersion": 1,
                    "package_type": "extension",
                    "package": {
                        "id": "demo-plugin",
                        "version": "1.0.0",
                        "domain": "gateway",
                    },
                    "display": {
                        "name": {"zh": "Demo", "en": "Demo"},
                        "description": {
                            "zh": "Demo system plugin",
                            "en": "Demo system plugin",
                        },
                    },
                },
                ensure_ascii=False,
            ),
        )
        zf.writestr("demo-plugin/demo-plugin/assets/preview.png", b"not-a-png")
    with pytest.raises(PublishError, match="preview.png"):
        extract_plugin_metadata(buf.getvalue())


def test_preview_is_scaled_and_compressed() -> None:
    raw = _noise_png((2200, 1200))
    out = optimize_png_preview_bytes(raw)
    assert len(out) <= PREVIEW_PUBLISH_MAX_BYTES
    with Image.open(io.BytesIO(out)) as image:
        assert max(image.size) <= PREVIEW_PUBLISH_MAX_EDGE_PX


def test_preview_scales_even_when_source_is_small() -> None:
    out = optimize_png_preview_bytes(_solid_png((3000, 1000)))
    with Image.open(io.BytesIO(out)) as image:
        assert max(image.size) <= PREVIEW_PUBLISH_MAX_EDGE_PX


def test_preview_pixel_limit_rejected() -> None:
    with pytest.raises(PublishError, match="像素尺寸过大"):
        assert_png_decodable(_png(), max_pixels=100)


def test_version_detail_returns_preview_url() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    db.add(
        MarketAssetDB(
            asset_id="demo",
            asset_type="plugin",
            name="demo-skill",
            display_name="demo-skill",
            publisher_id="owner",
            publisher_name="Owner",
            status="PUBLISHED",
            plugin_type="skill",
            latest_version="1.0.0",
            public_latest_version="1.0.0",
            moderation_status="APPROVED",
            publish_result="publish_success",
            visibility="public",
            view_count=0,
            install_count=0,
            like_count=0,
            star_count=0,
            review_count=0,
            average_rating=8.0,
            create_time=1,
            update_time=2,
        )
    )
    db.add(
        MarketAssetVersionDB(
            version_id="v1",
            asset_id="demo",
            version="1.0.0",
            file_path="skills/owner/demo/1.0.0/",
            artifact_sha256="abc123",
            has_icon=True,
            has_preview=True,
            moderation_status="APPROVED",
            publish_result="publish_success",
            status="ACTIVE",
            create_time=1,
        )
    )
    db.commit()

    data = get_plugin_version_detail_service(
        asset_id="demo",
        version="1.0.0",
        db=db,
        storage=_Storage(),
        viewer=ANONYMOUS_VIEWER,
    )

    assert data.icon_uri == "http://example.test/skills/owner/demo/1.0.0/icon.png"
    assert data.preview_url == "http://example.test/skills/owner/demo/1.0.0/preview.png"
