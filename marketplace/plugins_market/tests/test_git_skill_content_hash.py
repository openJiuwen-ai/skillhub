# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Git 同步内容摘要与确定性 zip：同内容应稳定可跳过。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plugins_market.imports.skill_entries import build_skill_plugin_zip_to_path
from plugins_market.services.git_skill_sync import _skill_entry_content_sha256


class GitSkillContentHashTests(unittest.TestCase):
    def test_content_sha_stable_and_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            entry = root / "demo"
            entry.mkdir()
            (entry / "SKILL.md").write_text(
                "---\nname: demo\ndescription: d\n---\n\nbody\n",
                encoding="utf-8",
            )
            h1 = _skill_entry_content_sha256(entry)
            h2 = _skill_entry_content_sha256(entry)
            self.assertEqual(h1, h2)
            (entry / "SKILL.md").write_text(
                "---\nname: demo\ndescription: d\n---\n\nbody2\n",
                encoding="utf-8",
            )
            self.assertNotEqual(h1, _skill_entry_content_sha256(entry))

    def test_deterministic_zip_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            staging = root / "staging"
            staging.mkdir()
            (staging / "plugin.yaml").write_text("name: demo\nversion: 1.0.0\n", encoding="utf-8")
            skill = staging / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\nname: demo\ndescription: d\n---\n\nx\n",
                encoding="utf-8",
            )
            z1 = root / "a.zip"
            z2 = root / "b.zip"
            build_skill_plugin_zip_to_path(staging, "demo", "1.0.0", z1)
            build_skill_plugin_zip_to_path(staging, "demo", "1.0.0", z2)
            self.assertEqual(z1.read_bytes(), z2.read_bytes())


if __name__ == "__main__":
    unittest.main()
