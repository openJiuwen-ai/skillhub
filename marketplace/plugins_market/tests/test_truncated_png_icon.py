from __future__ import annotations

import io
import zipfile

import pytest

from plugins_market.core.errors import PublishError
from plugins_market.validation import extract_plugin_metadata
from plugins_market.validation.icon_png_optimize import optimize_png_icon_bytes
from plugins_market.validation.zip_utils import validate_png_icon_bytes

_TRUNCATED_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"


def test_truncated_png_icon_rejected() -> None:
    with pytest.raises(PublishError, match="无法解码"):
        validate_png_icon_bytes(_TRUNCATED_PNG, path="icon.png")
    with pytest.raises(PublishError, match="无法解码"):
        optimize_png_icon_bytes(_TRUNCATED_PNG)


def test_truncated_png_in_skill_zip_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(
            "demo-skill/plugin.yaml",
            "name: demo-skill\nversion: 1.0.0\ndisplay_name: Demo\n"
            "description: demo skill\nruntime:\n  type: skill\nmetadata:\n"
            "  author: tester\n  tags: []\n",
        )
        zf.writestr(
            "demo-skill/demo-skill/SKILL.md",
            "---\nname: demo-skill\ndescription: Demo skill\n---\n# Demo\n",
        )
        zf.writestr("demo-skill/icon.png", _TRUNCATED_PNG)
    with pytest.raises(PublishError, match="无法解码"):
        extract_plugin_metadata(buf.getvalue())
