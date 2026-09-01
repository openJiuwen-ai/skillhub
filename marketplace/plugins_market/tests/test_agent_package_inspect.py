# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import io
import json
import zipfile

from plugins_market.services.agent_package_inspect import extract_agent_package_profile


def _build_zip(inner: dict[str, str]) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    zf = zipfile.ZipFile(buffer, "w")
    prefix = "coach/coach/"
    for relative, content in inner.items():
        zf.writestr(prefix + relative, content)
    zf.close()
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def test_extract_agent_template_profile() -> None:
    manifest = {
        "version": "1.0.0",
        "package_type": "agent_template",
        "name": "coach",
        "description": "Office coach",
        "category": "Life",
        "source": "local",
        "default_init_input": {"zh": "开始"},
        "quick_inputs": [{"zh": "开始"}, {"zh": "复盘"}],
        "persona": {"dir": "./persona"},
        "tools": [
            {
                "file": "tools/demo.py",
                "class": "DemoTool",
                "display_name": {"zh": "演示工具"},
                "display_description": {"zh": "演示用途"},
            }
        ],
        "mcps": [{"connector": "amap"}],
        "skills": [{"dir": "./skills/profile-manager"}],
        "tags": [{"zh": "健康"}],
    }
    zf = _build_zip(
        {
            "manifest.json": json.dumps(manifest),
            "persona/coach.md": "# Coach persona",
            "tools/demo.py": "class DemoTool: pass",
            "skills/profile-manager/SKILL.md": (
                "---\nname: profile-manager\ndescription: Profile skill\n---\n"
            ),
        }
    )
    with zf:
        profile = extract_agent_package_profile(zf)
    assert profile is not None
    assert profile["package_type"] == "agent_template"
    assert profile["category"] == "Life"
    assert profile["source"] == "local"
    assert profile["default_init_input"] == "开始"
    assert profile["quick_inputs"] == ["开始", "复盘"]
    assert "Coach persona" in (profile["persona_markdown"] or "")
    kinds = {item["kind"] for item in profile["capabilities"]}
    assert kinds == {"skill", "tool", "mcp"}
    assert profile["manifest_tags"] == ["健康"]
