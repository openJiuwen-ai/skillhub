# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Git 源去重键与 skills_subpath 归一化：同仓库不同目录应得到不同 dedup。"""

from __future__ import annotations

import unittest

from plugins_market.utils.git_source_dedup import compute_git_source_dedup_key
from plugins_market.utils.git_skills_subpath_rules import assert_git_skills_subpath


class GitSourceDedupSubpathTests(unittest.TestCase):
    def test_different_subpaths_different_keys(self) -> None:
        canonical = "github.com/org/skills"
        ref = "main"
        a = compute_git_source_dedup_key(
            repo_url_canonical=canonical,
            ref=ref,
            skills_subpath="team-a/skills",
        )
        b = compute_git_source_dedup_key(
            repo_url_canonical=canonical,
            ref=ref,
            skills_subpath="team-b/skills",
        )
        self.assertNotEqual(a, b)
        self.assertEqual(len(a), 64)
        self.assertEqual(len(b), 64)

    def test_subpath_normalization_collapses_slash_variants(self) -> None:
        self.assertEqual(assert_git_skills_subpath("skills/"), "skills")
        self.assertEqual(assert_git_skills_subpath("skills\\foo"), "skills/foo")
        self.assertEqual(assert_git_skills_subpath("skills/foo/"), "skills/foo")
        a = compute_git_source_dedup_key(
            repo_url_canonical="github.com/org/r",
            ref="main",
            skills_subpath=assert_git_skills_subpath("skills/"),
        )
        b = compute_git_source_dedup_key(
            repo_url_canonical="github.com/org/r",
            ref="main",
            skills_subpath=assert_git_skills_subpath("skills"),
        )
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
