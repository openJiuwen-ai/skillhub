# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ZIP 审查路径归一化后的重复成员必须拒绝，不能静默覆盖。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from skill_review.runtime.package_access.zip_access import (
    ZipPackageAccess,
    ZipPathCollisionError,
)


def _zipinfo_with_filename(name: str) -> ZipInfo:
    """Keep ``\\`` in the stored name; ZipInfo.__init__ rewrites os.sep to ``/``."""
    info = ZipInfo("placeholder")
    info.filename = name
    return info


def _write_collision_zip(path: Path, names: tuple[str, str], payloads: tuple[str, str]) -> None:
    archive = BytesIO()
    with ZipFile(archive, "w") as zf:
        for name, payload in zip(names, payloads, strict=True):
            zf.writestr(_zipinfo_with_filename(name), payload)
    path.write_bytes(archive.getvalue())


@pytest.mark.parametrize(
    "names",
    [
        ("scripts/run.py", "scripts\\run.py"),
        ("./scripts/run.py", "scripts/run.py"),
        ("/scripts/run.py", "scripts/run.py"),
        ("./scripts/run.py", "scripts\\run.py"),
    ],
)
def test_zip_package_access_rejects_normalized_path_collision(tmp_path: Path, names: tuple[str, str]) -> None:
    zip_path = tmp_path / "path-collision.zip"
    _write_collision_zip(zip_path, names, ("UNEXPECTED_CONTENT", "SAFE_CONTENT"))

    with pytest.raises(ZipPathCollisionError, match="重复路径"):
        ZipPackageAccess(zip_path)


def test_zip_package_access_lists_unique_normalized_member(tmp_path: Path) -> None:
    zip_path = tmp_path / "ok.zip"
    archive = BytesIO()
    with ZipFile(archive, "w") as zf:
        zf.writestr("scripts/run.py", "SAFE_CONTENT")
        zf.writestr("README.md", "hello")
    zip_path.write_bytes(archive.getvalue())

    access = ZipPackageAccess(zip_path)
    try:
        paths = [entry.path for entry in access.list_files()]
        assert paths == ["README.md", "scripts/run.py"]
        assert access.read_text_file("scripts/run.py", 4096).content == "SAFE_CONTENT"
    finally:
        access.close()
