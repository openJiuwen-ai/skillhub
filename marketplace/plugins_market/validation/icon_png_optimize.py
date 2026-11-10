# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normalize and shrink marketplace PNG assets before object storage upload.

内置 ``icon.png`` 策略（与前端列表约 48px 头像、详情略大展示匹配）：
- 最长边不超过 ``ICON_PUBLISH_MAX_EDGE_PX``（默认 256），等比缩放，LANCZOS。
- 统一写出为 PNG：``optimize=True`` + ``compress_level=9``，保留透明通道（RGBA）。
- 若 Pillow 无法解码或写出异常，拒绝发布（不回退原图）。
- 若优化后体积大于原始（少见），保留较小的一方。
"""

from __future__ import annotations

import io
from typing import Final

from plugins_market.core.errors import PublishError
from plugins_market.validation.constants import ICON_MAX_BYTES, ICON_PUBLISH_MAX_EDGE_PX, PNG_MAGIC

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - 运行环境应已安装 pillow
    Image = None  # type: ignore[misc, assignment]
    ImageOps = None  # type: ignore[misc, assignment]

_RGBA_MODES: Final[frozenset[str]] = frozenset({"RGBA", "RGB"})


def _to_rgba_work_image(im: "Image.Image") -> "Image.Image":
    """得到带 alpha 的工作图，便于缩略与 PNG 写出。"""
    mode = im.mode
    if mode in _RGBA_MODES:
        return im
    if mode == "P":
        if "transparency" in im.info:
            return im.convert("RGBA")
        p = im.convert("RGBA")
        return p
    if mode in ("L", "LA"):
        return im.convert("RGBA")
    if mode == "1":
        return im.convert("RGBA")
    return im.convert("RGBA")


def assert_png_decodable(
    raw: bytes,
    *,
    path: str = "icon.png",
    max_pixels: int | None = None,
) -> None:
    """Fully decode PNG; truncated or otherwise invalid images raise PublishError."""
    if Image is None:
        raise PublishError(
            code=400,
            error="invalid_plugin_structure",
            message=f"{path} 无法校验：运行环境缺少 Pillow",
        )
    try:
        with Image.open(io.BytesIO(raw)) as verified:
            verified.verify()
        with Image.open(io.BytesIO(raw)) as loaded:
            width, height = loaded.size
            if width <= 0 or height <= 0:
                raise ValueError("invalid image size")
            if max_pixels is not None and width * height > max_pixels:
                raise PublishError(
                    code=400,
                    error="invalid_plugin_structure",
                    message=(
                        f"{path} 像素尺寸过大（最大 {max_pixels // 1_000_000} 百万像素）"
                    ),
                )
            loaded.load()
    except PublishError:
        raise
    except Exception as exc:  # noqa: BLE001 - 任意解码失败均视为非法图标
        raise PublishError(
            code=400,
            error="invalid_plugin_structure",
            message=f"{path} 不是有效的 PNG 文件（无法解码）",
        ) from exc


def optimize_png_image_bytes(
    raw: bytes,
    *,
    max_edge: int,
    max_bytes: int,
    path: str = "icon.png",
    min_edge: int = 32,
    max_pixels: int | None = None,
) -> bytes:
    """Decode, resize and re-encode a PNG, shrinking further until *max_bytes* fits."""
    if not raw or not raw.startswith(PNG_MAGIC):
        raise PublishError(
            code=400,
            error="invalid_plugin_structure",
            message=f"{path} 不是有效的 PNG 文件（文件头魔数不匹配）",
        )
    assert_png_decodable(raw, path=path, max_pixels=max_pixels)
    if Image is None or ImageOps is None:
        raise PublishError(
            code=400,
            error="invalid_plugin_structure",
            message=f"{path} 无法优化：运行环境缺少 Pillow",
        )

    max_edge = max(32, max_edge)
    min_edge = max(32, min(min_edge, max_edge))
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im = ImageOps.exif_transpose(im)
            im.load()
            work = _to_rgba_work_image(im)
            if max(work.size) > max_edge:
                work.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

            while True:
                buf = io.BytesIO()
                work.save(buf, format="PNG", optimize=True, compress_level=9)
                out = buf.getvalue()
                if len(out) <= max_bytes or max(work.size) <= min_edge:
                    return out
                next_edge = max(min_edge, int(max(work.size) * 0.8))
                if next_edge >= max(work.size):
                    return out
                work.thumbnail((next_edge, next_edge), Image.Resampling.LANCZOS)
    except PublishError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PublishError(
            code=400,
            error="invalid_plugin_structure",
            message=f"{path} 不是有效的 PNG 文件（无法解码）",
        ) from exc


def optimize_png_icon_bytes(raw: bytes, *, max_edge: int = ICON_PUBLISH_MAX_EDGE_PX) -> bytes:
    """
    将已通过魔数校验的 PNG 字节优化后返回；解码失败则拒绝发布。

    不改变 API 校验上限：调用方仍应先通过 ``validate_png_icon_bytes``。
    """
    out = optimize_png_image_bytes(
        raw,
        max_edge=max_edge,
        max_bytes=ICON_MAX_BYTES,
        path="icon.png",
    )

    if not out.startswith(PNG_MAGIC) or len(out) > ICON_MAX_BYTES:
        return raw
    if len(out) >= len(raw):
        return raw
    return out
