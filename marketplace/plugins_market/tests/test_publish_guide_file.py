# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""上传指南 Markdown 从配置路径读取。"""

from __future__ import annotations

from unittest.mock import patch

from plugins_market.routers.site_public import plain_markdown_response
from plugins_market.services.privacy_statement_file import (
    markdown_path_for_lang,
    read_publish_guide_markdown,
)


def test_publish_guide_reads_configured_file(tmp_path) -> None:
    guide = tmp_path / "publish-guide.md"
    guide.write_bytes("# 上传资产\n".encode("utf-8"))
    with patch("plugins_market.services.privacy_statement_file.settings") as settings:
        settings.publish_guide_file = str(guide)
        assert read_publish_guide_markdown() == "# 上传资产\n"


def test_default_publish_guide_contains_web_and_api_steps() -> None:
    text = read_publish_guide_markdown()
    assert "在网页上发布" in text
    assert "POST /api/v1/plugins" in text


def test_publish_guide_blank_path_returns_empty() -> None:
    with patch("plugins_market.services.privacy_statement_file.settings") as settings:
        settings.publish_guide_file = "  "
        assert read_publish_guide_markdown() == ""


def test_english_guide_reads_sibling_en_file(tmp_path) -> None:
    zh = tmp_path / "publish-guide.md"
    en = tmp_path / "publish-guide.en.md"
    zh.write_bytes("# 上传资产\n".encode("utf-8"))
    en.write_bytes("# Publish an asset\n".encode("utf-8"))
    with patch("plugins_market.services.privacy_statement_file.settings") as settings:
        settings.publish_guide_file = str(zh)
        assert read_publish_guide_markdown("en") == "# Publish an asset\n"
        assert read_publish_guide_markdown("zh") == "# 上传资产\n"


def test_lang_is_not_appended_as_a_path_segment() -> None:
    from pathlib import Path

    unchanged = markdown_path_for_lang("/app/publish-guide.md", "../secret")
    english = Path(markdown_path_for_lang("/app/publish-guide.md", "en"))
    assert unchanged == "/app/publish-guide.md"
    assert english.name == "publish-guide.en.md"
    assert english.parent == Path("/app")


def test_empty_markdown_response_is_404() -> None:
    missing = plain_markdown_response("  \n")
    present = plain_markdown_response("# title\n")
    assert missing.status_code == 404
    assert present.status_code == 200
