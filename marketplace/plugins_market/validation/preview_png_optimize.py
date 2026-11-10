# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normalize and shrink system-plugin ``preview.png`` before upload."""

from __future__ import annotations

from plugins_market.core.errors import PublishError
from plugins_market.validation.constants import (
    PNG_MAGIC,
    PREVIEW_MAX_PIXELS,
    PREVIEW_PUBLISH_MAX_BYTES,
    PREVIEW_PUBLISH_MAX_EDGE_PX,
)
from plugins_market.validation.icon_png_optimize import optimize_png_image_bytes


def optimize_png_preview_bytes(
    raw: bytes,
    *,
    max_edge: int = PREVIEW_PUBLISH_MAX_EDGE_PX,
    max_bytes: int = PREVIEW_PUBLISH_MAX_BYTES,
) -> bytes:
    """Scale to *max_edge* and shrink further until the PNG fits *max_bytes*."""
    out = optimize_png_image_bytes(
        raw,
        max_edge=max_edge,
        max_bytes=max_bytes,
        path="preview.png",
        min_edge=640,
        max_pixels=PREVIEW_MAX_PIXELS,
    )
    if not out.startswith(PNG_MAGIC) or len(out) > max_bytes:
        raise PublishError(
            code=400,
            error="invalid_plugin_structure",
            message=(
                f"preview.png 压缩后仍超过 {max_bytes // (1024 * 1024)} MB，"
                "请缩小预览图后重试"
            ),
        )
    return out
