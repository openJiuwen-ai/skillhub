from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile, ZipInfo

from skill_review.domain.types import PackageFileEntry
from skill_review.runtime.package_access.base import PackageAccess, PackageTextReadResult
from skill_review.runtime.package_access.file_catalog import (
    is_safe_archive_path,
    looks_text_like,
    normalize_archive_path,
)


class ZipPathCollisionError(ValueError):
    """ZIP members normalize to the same path; opening the archive is rejected."""


def _index_safe_entries(zip_file: ZipFile) -> dict[str, ZipInfo]:
    """Map normalized member paths to ZipInfo and reject aliases that would clobber."""
    entries: dict[str, ZipInfo] = {}
    for item in zip_file.infolist():
        if item.is_dir() or not is_safe_archive_path(item.filename):
            continue
        normalized = normalize_archive_path(item.filename)
        existing = entries.get(normalized)
        if existing is not None:
            raise ZipPathCollisionError(
                f"ZIP 包含重复路径：{normalized} "
                f"(members {existing.filename!r} and {item.filename!r})"
            )
        entries[normalized] = item
    return entries


class ZipPackageAccess(PackageAccess):
    archive_format = "zip"

    def __init__(self, local_path: Path):
        self._zip_file = ZipFile(local_path, "r")
        try:
            self._entries_by_path = _index_safe_entries(self._zip_file)
        except Exception:
            self._zip_file.close()
            raise

    def list_files(self) -> list[PackageFileEntry]:
        return [
            PackageFileEntry(path=path, size=int(info.file_size or 0))
            for path, info in sorted(self._entries_by_path.items())
        ]

    def read_text_file(self, path: str, max_bytes: int) -> PackageTextReadResult:
        normalized = normalize_archive_path(path)
        info = self._entries_by_path.get(normalized)
        if info is None:
            return PackageTextReadResult(path=normalized, status="not_found", content=None, error="file not found")
        try:
            raw_bytes = self._read_prefix(info, max_bytes)
            truncated = info.file_size > len(raw_bytes)
            content = decode_utf8_prefix(raw_bytes)
        except UnicodeDecodeError:
            return PackageTextReadResult(path=normalized, status="non_utf8", content=None)
        except Exception as exc:  # noqa: BLE001
            return PackageTextReadResult(path=normalized, status="read_error", content=None, error=str(exc))
        if not looks_text_like(content):
            return PackageTextReadResult(path=normalized, status="binary", content=None)
        return PackageTextReadResult(path=normalized, status="success", content=content, truncated=truncated)

    def close(self) -> None:
        self._zip_file.close()

    def _read_prefix(self, info, max_bytes: int) -> bytes:
        if max_bytes <= 0:
            return b""
        with self._zip_file.open(info, "r") as source:
            return source.read(max_bytes)


def open_zip_package(local_path: Path) -> ZipPackageAccess:
    return ZipPackageAccess(local_path)


def decode_utf8_prefix(raw_bytes: bytes) -> str:
    remaining = raw_bytes
    while remaining:
        try:
            return remaining.decode("utf-8")
        except UnicodeDecodeError as exc:
            if exc.reason != "unexpected end of data":
                raise
            remaining = remaining[:-1]
    return ""
