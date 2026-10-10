from __future__ import annotations

import io
import zipfile

import pytest

from plugins_market.core.errors import PublishError
from plugins_market.validation import extract_plugin_metadata
from plugins_market.validation.constants import YAML_MAX_ALIASES
from plugins_market.validation.plugin_yaml import safe_load_yaml


def _alias_doc(alias_count: int) -> str:
    lines = ["anchor: &a 1", "refs:"]
    lines.extend(["  - *a"] * alias_count)
    return "\n".join(lines) + "\n"


def test_yaml_alias_limit_1000_accepted_1001_rejected() -> None:
    safe_load_yaml(_alias_doc(YAML_MAX_ALIASES), context="plugin.yaml")
    with pytest.raises(PublishError, match="别名"):
        safe_load_yaml(_alias_doc(YAML_MAX_ALIASES + 1), context="plugin.yaml")


def test_skill_frontmatter_alias_limit() -> None:
    extra = "anchor: &a 1\nrefs:\n" + ("  - *a\n" * (YAML_MAX_ALIASES + 1))
    skill_md = (
        "---\nname: demo-skill\ndescription: Demo skill\n"
        f"{extra}---\n# Demo\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(
            "demo-skill/plugin.yaml",
            "name: demo-skill\nversion: 1.0.0\ndisplay_name: Demo\n"
            "description: demo skill\nruntime:\n  type: skill\nmetadata:\n"
            "  author: tester\n  tags: []\n",
        )
        zf.writestr("demo-skill/demo-skill/SKILL.md", skill_md)
    with pytest.raises(PublishError, match="别名"):
        extract_plugin_metadata(buf.getvalue())
