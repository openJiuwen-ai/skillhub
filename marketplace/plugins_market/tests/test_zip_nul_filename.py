from __future__ import annotations

import io
import zipfile

import pytest

from plugins_market.core.errors import PublishError
from plugins_market.imports.bundle_safe_extract import skill_import_extract_zip_to_dir
from plugins_market.validation import extract_plugin_metadata
from plugins_market.validation.zip_utils import validate_zip_safety


def _inject_nul_member() -> bytes:
    marker = b"demo-skill/okXnotes.txt"
    with_nul = b"demo-skill/ok\x00notes.txt"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(
            "demo-skill/plugin.yaml",
            "name: demo-skill\nversion: 1.0.0\ndisplay_name: Demo\n"
            "description: demo\nruntime:\n  type: skill\nmetadata:\n"
            "  author: tester\n  tags: []\n",
        )
        zf.writestr(
            "demo-skill/demo-skill/SKILL.md",
            "---\nname: demo-skill\ndescription: Demo skill\n---\n# Demo\n",
        )
        zf.writestr("demo-skill/okXnotes.txt", b"x")
    raw = buf.getvalue()
    assert marker in raw
    return raw.replace(marker, with_nul)


def test_zip_safety_rejects_nul_in_orig_filename() -> None:
    raw = _inject_nul_member()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        with pytest.raises(PublishError, match="非法条目名"):
            validate_zip_safety(zf)


def test_publish_and_import_reject_nul_filename(tmp_path) -> None:
    raw = _inject_nul_member()
    with pytest.raises(PublishError, match="非法条目名"):
        extract_plugin_metadata(raw)
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(raw)
    with pytest.raises(ValueError, match="非法条目名|illegal zip entry path"):
        skill_import_extract_zip_to_dir(bundle, tmp_path / "out")
