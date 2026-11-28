# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import dataclasses
import io
import zipfile
from types import SimpleNamespace

import pytest

from skill_runner.config import settings
from skill_runner.executor import local as local_mod
from skill_runner.executor.local import LocalExecutor


def _session_with_bundle(package_bytes: bytes, skill_md: str = "") -> SimpleNamespace:
    bundle = SimpleNamespace(asset_id="a1", package_bytes=package_bytes, skill_md=skill_md)
    return SimpleNamespace(session_id="s1", extra={"skill_bundle": bundle}, system_prompt="")


def _zip(files: dict[str, bytes], *, compress_type: int = zipfile.ZIP_STORED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress_type) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _provision(package_bytes: bytes, skill_dir: str) -> None:
    asyncio.run(LocalExecutor()._provision_skill(_session_with_bundle(package_bytes), skill_dir))


def test_normal_package_extracts(tmp_path) -> None:
    package = _zip({"SKILL.md": b"# hi", "refs/guide.md": b"guide"})
    skill_dir = tmp_path / "skill"
    _provision(package, str(skill_dir))
    assert (skill_dir / "SKILL.md").read_bytes() == b"# hi"
    assert (skill_dir / "refs" / "guide.md").read_bytes() == b"guide"


def test_extract_raises_on_entry_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        local_mod, "settings", dataclasses.replace(settings, package_zip_max_entries=2)
    )
    package = _zip({f"f{i}.txt": b"x" for i in range(4)})
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(package)) as zf:
        with pytest.raises(ValueError, match="too many entries"):
            LocalExecutor()._extract_package_entries(zf, "", str(skill_dir))


def test_extract_raises_on_decompressed_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        local_mod,
        "settings",
        dataclasses.replace(settings, package_zip_max_decompressed_bytes=1024),
    )
    package = _zip(
        {"big.bin": b"\x00" * (1024 * 1024)}, compress_type=zipfile.ZIP_DEFLATED
    )
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    with pytest.raises(ValueError, match="decompressed size exceeds limit"):
        with zipfile.ZipFile(io.BytesIO(package)) as zf:
            LocalExecutor()._extract_package_entries(zf, "", str(skill_dir))


def test_extract_cleans_up_partial_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        local_mod,
        "settings",
        dataclasses.replace(settings, package_zip_max_decompressed_bytes=100),
    )
    package = _zip({"a.bin": b"x" * 60, "b.bin": b"y" * 60})
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(package)) as zf:
        with pytest.raises(ValueError, match="decompressed size exceeds limit"):
            LocalExecutor()._extract_package_entries(zf, "", str(skill_dir))
    assert not (skill_dir / "a.bin").exists()
    assert not (skill_dir / "b.bin").exists()


def test_provision_falls_back_to_text_when_entry_limit_exceeded(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        local_mod, "settings", dataclasses.replace(settings, package_zip_max_entries=2)
    )
    package = _zip({f"f{i}.txt": b"x" for i in range(4)})
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    asyncio.run(
        LocalExecutor()._provision_skill(_session_with_bundle(package, skill_md="# hi"), skill_dir)
    )
    assert (skill_dir / "SKILL.md").read_bytes() == b"# hi"
    assert not (skill_dir / "f0.txt").exists()


def test_provision_falls_back_to_text_when_decompressed_limit_exceeded(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        local_mod,
        "settings",
        dataclasses.replace(settings, package_zip_max_decompressed_bytes=1024),
    )
    package = _zip(
        {"big.bin": b"\x00" * (1024 * 1024)}, compress_type=zipfile.ZIP_DEFLATED
    )
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    asyncio.run(
        LocalExecutor()._provision_skill(_session_with_bundle(package, skill_md="# hi"), skill_dir)
    )
    assert (skill_dir / "SKILL.md").read_bytes() == b"# hi"
    assert not (skill_dir / "big.bin").exists()


def test_traversal_entry_skipped_and_sibling_prefix_not_matched(tmp_path) -> None:
    package = _zip({"ok.txt": b"ok", "../escape.txt": b"evil"})
    skill_dir = tmp_path / "skill"
    _provision(package, str(skill_dir))
    assert (skill_dir / "ok.txt").read_bytes() == b"ok"
    assert not (tmp_path / "escape.txt").exists()


def test_symlink_entry_rejected(tmp_path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0xA1FF << 16
        zf.writestr(info, "../target")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        with pytest.raises(ValueError, match="symlink"):
            LocalExecutor()._extract_package_entries(zf, "", str(skill_dir))
