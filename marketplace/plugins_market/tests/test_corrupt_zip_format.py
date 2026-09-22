from __future__ import annotations

import io
import zipfile

import pytest

from plugins_market.core.errors import PublishError
from plugins_market.validation import extract_plugin_metadata


def _skill_zip() -> bytes:
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
    return buf.getvalue()


def test_truncated_zip_is_invalid_file_format() -> None:
    with pytest.raises(PublishError) as exc_info:
        extract_plugin_metadata(_skill_zip()[:-32])
    assert exc_info.value.code == 400
    assert exc_info.value.error in {"invalid_file_format", "invalid_plugin_config"}


def test_crc_corrupt_zip_is_invalid_file_format() -> None:
    raw = bytearray(_skill_zip())
    eocd = bytes(raw).rfind(b"PK\x05\x06")
    cd_off = int.from_bytes(raw[eocd + 16 : eocd + 20], "little")
    raw[cd_off + 16] ^= 0xFF
    with pytest.raises(PublishError) as exc_info:
        extract_plugin_metadata(bytes(raw))
    assert exc_info.value.code == 400


def test_declared_size_mismatch_is_invalid_file_format() -> None:
    raw = bytearray(_skill_zip())
    eocd = bytes(raw).rfind(b"PK\x05\x06")
    cd_off = int.from_bytes(raw[eocd + 16 : eocd + 20], "little")
    raw[cd_off + 24 : cd_off + 28] = (1).to_bytes(4, "little")
    raw[22:26] = (1).to_bytes(4, "little")
    with pytest.raises(PublishError) as exc_info:
        extract_plugin_metadata(bytes(raw))
    assert exc_info.value.code == 400
