# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Safe extraction of admin skill bundle ZIPs.

Reuses ``validation.zip_utils`` (``validate_zip_safety``, ``DecompressCounter``) for
the same metadata pre-check and decompressed-byte limits as single-plugin uploads.
Paths are normalized with ``normalize_zip_entry_name`` and checked with
``Path.resolve`` / ``relative_to`` to prevent zip slip.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
import posixpath

from plugins_market.core.errors import PublishError
from plugins_market.validation.constants import (
    MAX_FILE_SIZE,
    MAX_ZIP_ENTRIES,
    ZIP_ENTRY_WINDOWS_DRIVE_PATTERN,
    ZIP_STREAM_READ_CHUNK_BYTES,
)
from plugins_market.validation.zip_utils import DecompressCounter, validate_zip_safety


def _reraise_publish_as_value(exc: PublishError) -> None:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    if detail.get("error") == "zip_too_large" and "条目数超过上限" in str(detail.get("message") or ""):
        raise PublishError(
            code=400,
            error="too_many_skill_entries",
            message=(
                f"顶层 skill 目录数量超过上限 {MAX_ZIP_ENTRIES} "
                "（与 ZIP 条目数上限一致），请分批导入或拆分集合包"
            ),
            error_code="SKILLHUB_IMPORT_TOO_MANY_ENTRIES",
            error_class="validation",
        ) from exc
    raise ValueError(str(detail.get("message") or exc)) from exc


def normalize_zip_entry_name(raw: str) -> str | None:
    """Normalize a single ZIP entry name for safe extraction.

    Returns ``None`` to skip empty path segments after normalization.
    Raises ``ValueError`` if the path is illegal (absolute, traversal, NUL, etc.).
    """
    if not isinstance(raw, str) or not raw:
        raise ValueError("illegal zip entry path")
    if "\x00" in raw:
        raise ValueError("illegal zip entry path")

    norm = raw.replace("\\", "/").strip("/")
    if not norm:
        return None

    if norm.startswith("/") or norm.startswith("\\"):
        raise ValueError("illegal zip entry path")
    if ZIP_ENTRY_WINDOWS_DRIVE_PATTERN.match(norm):
        raise ValueError("illegal zip entry path")

    parts = norm.split("/")
    if ".." in parts:
        raise ValueError("illegal zip entry path")

    norm2 = posixpath.normpath(norm)
    if norm2 in (".", "..") or norm2.startswith("../") or "/../" in f"/{norm2}/":
        raise ValueError("illegal zip entry path")

    return norm2


def skill_import_extract_zip_to_dir(bundle_zip: Path, dest: Path) -> None:
    """Extract ``bundle_zip`` under ``dest`` with the same safety rules as plugin zips.

    - Enforces raw size ≤ ``MAX_FILE_SIZE`` and PK signature before opening.
    - Runs ``validate_zip_safety`` (entry count, paths, symlink ban, declared sizes, etc.).
    - Streams each member with ``ZIP_STREAM_READ_CHUNK_BYTES`` and ``DecompressCounter``.
    - Verifies each file member's decompressed byte count matches ``ZipInfo.file_size``.
    """
    raw_sz = bundle_zip.stat().st_size
    if raw_sz > MAX_FILE_SIZE:
        raise ValueError("zip file exceeds maximum size")
    with open(bundle_zip, "rb") as f:
        sig = f.read(2)
    if len(sig) < 2 or sig != b"PK":
        raise ValueError("not a valid zip file")

    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(bundle_zip, "r") as zf:
        try:
            validate_zip_safety(zf)
        except PublishError as e:
            _reraise_publish_as_value(e)

        counter = DecompressCounter()

        for info in zf.infolist():
            orig = getattr(info, "orig_filename", None)
            if isinstance(orig, bytes):
                orig = orig.decode("latin-1")
            if isinstance(orig, str) and "\x00" in orig:
                raise ValueError("illegal zip entry path")
            name = normalize_zip_entry_name(info.filename)
            if name is None:
                continue

            target = (dest / name).resolve()
            try:
                target.relative_to(dest)
            except ValueError as e:
                raise ValueError("zip path escapes destination (zip slip)") from e

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            member_read = 0
            with zf.open(info, "r") as src, open(target, "wb") as out:
                while True:
                    chunk = src.read(ZIP_STREAM_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    member_read += len(chunk)
                    try:
                        counter.add(len(chunk))
                    except PublishError as e:
                        _reraise_publish_as_value(e)
                    out.write(chunk)

            declared = int(info.file_size)
            if member_read != declared:
                raise ValueError("zip member size mismatch after extraction")
