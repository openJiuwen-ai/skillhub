from __future__ import annotations

import io
import zipfile

import pytest

from plugins_market.core.errors import PublishError
from plugins_market.validation import extract_plugin_metadata
from plugins_market.validation.constants import MARKET_ASSET_DETAIL_DESC_MAX_BYTES


def _skill_zip(skill_md: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(
            "demo-skill/plugin.yaml",
            "name: demo-skill\nversion: 1.0.0\ndisplay_name: Demo\n"
            "description: demo skill\nruntime:\n  type: skill\nmetadata:\n"
            "  author: tester\n  tags: []\n",
        )
        zf.writestr("demo-skill/demo-skill/SKILL.md", skill_md)
    return buf.getvalue()


def test_legal_frontmatter_persists_body_not_full_skill_md() -> None:
    extra = "note: " + ("n" * 80_000) + "\n"
    skill_md = (
        "---\nname: demo-skill\ndescription: Demo skill\n"
        f"{extra}---\nhello body\n"
    )
    meta = extract_plugin_metadata(_skill_zip(skill_md))
    assert meta["detail_desc"].strip() == "hello body"
    assert len(meta["detail_desc"].encode("utf-8")) < MARKET_ASSET_DETAIL_DESC_MAX_BYTES


def test_oversized_detail_body_rejected_before_db() -> None:
    body = "x" * (MARKET_ASSET_DETAIL_DESC_MAX_BYTES + 1)
    skill_md = f"---\nname: demo-skill\ndescription: Demo skill\n---\n{body}"
    with pytest.raises(PublishError, match="详情描述超过数据库字段上限"):
        extract_plugin_metadata(_skill_zip(skill_md))
