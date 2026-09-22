# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Zip security utilities.

Two-layer defence:
  Layer 1 – Metadata pre-check  : fast rejection of obviously malicious zips
            (entry count, path sanity, declared sizes, compression ratio).
  Layer 2 – Streaming read      : actual decompressed bytes are counted while
            reading; abort as soon as the global accumulator exceeds the cap.
            This catches forged ZipInfo metadata.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from typing import Iterator

from plugins_market.core.errors import PublishError
from plugins_market.validation.constants import (
    ICON_MAX_BYTES,
    MAX_COMPRESSION_RATIO,
    MAX_DECOMPRESSED_TOTAL,
    MAX_ZIP_ENTRIES,
    PNG_MAGIC,
    ZIP_ENTRY_WINDOWS_DRIVE_PATTERN,
    ZIP_STREAM_READ_CHUNK_BYTES,
)

_ZIP_CORRUPT_ERRORS = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    EOFError,
    OSError,
    struct.error,
    zlib.error,
    RuntimeError,
)

# Windows drive-letter prefix, e.g. C: or c:
_WIN_DRIVE_RE = ZIP_ENTRY_WINDOWS_DRIVE_PATTERN

_CHUNK = ZIP_STREAM_READ_CHUNK_BYTES


# ---------------------------------------------------------------------------
# Global decompressed-bytes counter
# ---------------------------------------------------------------------------

class DecompressCounter:
    """Tracks cumulative decompressed bytes across all zip member reads."""

    def __init__(self, max_bytes: int = MAX_DECOMPRESSED_TOTAL) -> None:
        self.total = 0
        self.max_bytes = max_bytes

    def add(self, n: int) -> None:
        self.total += n
        if self.total > self.max_bytes:
            raise PublishError(
                code=400,
                error="zip_too_large",
                message=(
                    f"插件包解压后总量超过上限 "
                    f"（{self.max_bytes // (1024 * 1024)} MB），请减小插件包体积"
                ),
                error_code="SKILLHUB_PLUGIN_ZIP_TOO_LARGE",
                error_class="validation",
            )


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def raise_corrupt_zip(exc: BaseException) -> None:
    """Map client-controlled corrupt ZIP errors to a bounded 4xx."""
    raise PublishError(
        code=400,
        error="invalid_file_format",
        message="上传文件不是有效的 ZIP 格式，请检查文件是否损坏或格式是否正确",
        error_code="SKILLHUB_PLUGIN_FILE_FORMAT_INVALID",
        error_class="validation",
    ) from exc


def open_zip_bytes(content: bytes) -> zipfile.ZipFile:
    """Open a ZIP from bytes; truncated/malformed archives become PublishError 400."""
    try:
        return zipfile.ZipFile(io.BytesIO(content))
    except _ZIP_CORRUPT_ERRORS as exc:
        raise_corrupt_zip(exc)


def _zip_info_path_names(info: zipfile.ZipInfo) -> list[str]:
    """Return decoded names, including orig_filename before zipfile strips NUL."""
    names: list[str] = []
    for value in (info.filename, getattr(info, "orig_filename", None)):
        if isinstance(value, bytes):
            value = value.decode("latin-1")
        if isinstance(value, str) and value not in names:
            names.append(value)
    return names


def _validate_zip_entry_path(name: str) -> None:
    """Raise PublishError if the zip entry name is dangerous."""
    if not name or "\x00" in name:
        raise PublishError(
            code=400,
            error="invalid_plugin_structure",
            message=f"插件包含非法条目名：{name!r}",
        )
    normalized = name.replace("\\", "/")
    # Absolute path
    if normalized.startswith("/"):
        raise PublishError(
            code=400,
            error="invalid_plugin_structure",
            message=f"插件包含绝对路径条目：{name!r}",
        )
    # Windows drive letter (protect clients on Windows)
    if _WIN_DRIVE_RE.match(normalized):
        raise PublishError(
            code=400,
            error="invalid_plugin_structure",
            message=f"插件包含 Windows 盘符路径条目：{name!r}",
        )
    # Path traversal
    parts = normalized.split("/")
    if ".." in parts:
        raise PublishError(
            code=400,
            error="invalid_plugin_structure",
            message=f"插件包含路径穿越条目：{name!r}",
        )


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """Return True if the ZipInfo represents a symbolic link (Unix attr)."""
    # external_attr upper 16 bits are Unix file mode. Permission bits coexist
    # with the file type, so compare only the S_IFMT mask.
    mode = (info.external_attr >> 16) & 0xFFFF
    return (mode & 0xF000) == 0xA000


# ---------------------------------------------------------------------------
# Pre-check (layer 1)
# ---------------------------------------------------------------------------

def validate_zip_safety(zf: zipfile.ZipFile) -> None:
    """
    Metadata pre-check: fast rejection of obviously malicious archives.
    Does NOT open/decompress any member – relies only on ZipInfo headers.

    Checks:
    - Entry count ≤ MAX_ZIP_ENTRIES
    - Each entry: path safety, no symlinks
    - Declared total decompressed size ≤ MAX_DECOMPRESSED_TOTAL
    - Per-entry compression ratio ≤ MAX_COMPRESSION_RATIO (advisory)
    """
    infos = zf.infolist()

    if len(infos) > MAX_ZIP_ENTRIES:
        raise PublishError(
            code=400,
            error="zip_too_large",
            message=f"插件包条目数超过上限（最多 {MAX_ZIP_ENTRIES} 个条目）",
            error_code="SKILLHUB_PLUGIN_ZIP_TOO_LARGE",
            error_class="validation",
        )

    total_declared = 0
    for info in infos:
        for path_name in _zip_info_path_names(info):
            _validate_zip_entry_path(path_name)

        if _is_symlink(info):
            raise PublishError(
                code=400,
                error="invalid_plugin_structure",
                message=f"插件包含符号链接条目：{info.filename!r}",
            )

        total_declared += info.file_size

        # Compression ratio pre-check (advisory; real guard is streaming counter)
        if info.compress_size > 0 and info.file_size > 0:
            ratio = info.file_size / info.compress_size
            if ratio > MAX_COMPRESSION_RATIO:
                raise PublishError(
                    code=400,
                    error="zip_too_large",
                    message=(
                        f"插件包条目 {info.filename!r} 压缩比异常 "
                        f"（{ratio:.0f}:1，上限 {MAX_COMPRESSION_RATIO}:1），"
                        "疑似压缩炸弹"
                    ),
                    error_code="SKILLHUB_PLUGIN_ZIP_TOO_LARGE",
                    error_class="validation",
                )

    if total_declared > MAX_DECOMPRESSED_TOTAL:
        raise PublishError(
            code=400,
            error="zip_too_large",
            message=(
                f"插件包声明的解压总量超过上限 "
                f"（{MAX_DECOMPRESSED_TOTAL // (1024 * 1024)} MB）"
            ),
            error_code="SKILLHUB_PLUGIN_ZIP_TOO_LARGE",
            error_class="validation",
        )


# ---------------------------------------------------------------------------
# Streaming read (layer 2)
# ---------------------------------------------------------------------------

def safe_read_zip_member(
    zf: zipfile.ZipFile,
    path: str,
    counter: DecompressCounter,
) -> bytes:
    """
    Read a zip member in chunks, updating *counter* with actual decompressed
    bytes.  Raises PublishError (via counter.add) if the global cap is reached.

    Never calls ZipFile.read(name) directly to avoid reading the entire
    decompressed member into memory in one shot.
    """
    chunks: list[bytes] = []
    try:
        info = zf.getinfo(path)
        with zf.open(path) as fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                counter.add(len(chunk))
                chunks.append(chunk)
        data = b"".join(chunks)
        if int(info.file_size) != len(data):
            raise zipfile.BadZipFile(
                f"zip member size mismatch: declared {info.file_size}, read {len(data)}"
            )
        return data
    except PublishError:
        raise
    except _ZIP_CORRUPT_ERRORS as exc:
        raise_corrupt_zip(exc)


def iter_zip_members(
    zf: zipfile.ZipFile,
    counter: DecompressCounter,
) -> Iterator[tuple[str, bytes]]:
    """Yield (name, data) for every non-directory member, respecting the counter."""
    for info in zf.infolist():
        if info.filename.endswith("/"):
            continue  # directory entry
        data = safe_read_zip_member(zf, info.filename, counter)
        yield info.filename, data


def has_dist_wheels(names: set[str], prefix: str) -> bool:
    """Return True if at least one ``.whl`` file exists under ``<prefix>dist/``."""
    dist_prefix = f"{prefix}dist/" if prefix else "dist/"
    for n in names:
        norm = n.replace("\\", "/")
        if not norm.startswith(dist_prefix):
            continue
        rest = norm[len(dist_prefix):]
        if rest and "/" not in rest.rstrip("/") and rest.lower().endswith(".whl"):
            return True
    return False


def has_src_tree(names: set[str], prefix: str) -> bool:
    """Return True if at least one file exists under ``<prefix>src/`` (mcp-stdio / restful-api)."""
    src_prefix = prefix + "src/"
    return any(
        n.replace("\\", "/").startswith(src_prefix) and len(n) > len(src_prefix)
        for n in names
    )


# ---------------------------------------------------------------------------
# PNG icon validation（仅当调用方已确认存在 icon 成员时调用）
# ---------------------------------------------------------------------------

def validate_png_icon_bytes(data: bytes, *, path: str = "icon.png") -> None:
    """校验 PNG 魔数与大小上限；无图标时不要调用。"""
    if len(data) > ICON_MAX_BYTES:
        raise PublishError(
            code=400,
            error="invalid_plugin_structure",
            message=(
                f"{path} 大小超过上限（最大 {ICON_MAX_BYTES // (1024 * 1024)} MB）"
            ),
        )
    if not data.startswith(PNG_MAGIC):
        raise PublishError(
            code=400,
            error="invalid_plugin_structure",
            message=f"{path} 不是有效的 PNG 文件（文件头魔数不匹配）",
        )
