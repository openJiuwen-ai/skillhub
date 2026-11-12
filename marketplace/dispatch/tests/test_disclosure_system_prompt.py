"""非 compact 码 + 扁平列表模式的系统提示构建回归。"""

from __future__ import annotations

import unittest

from retrieval.tree.render.disclosure import (
    LEGACY_FLAT_NON_COMPACT_SYSTEM_PROMPT,
    _build_system_prompt,
    _load_progressive_prompt_bank,
)

_CANDIDATE_BLOCK = "# 候选列表\nAvailable options:\n[{\"name\": \"WeatherQuery\"}, {\"name\": \"TicketBooking\"}]"


class ProgressivePromptPlaceholderTests(unittest.TestCase):
    def test_progressive_templates_keep_candidate_block_placeholder(self):
        """模板被改写成纯文本时容易漏掉候选块占位符，此时候选列表会静默消失。"""
        bank = _load_progressive_prompt_bank()
        for key in ("shared", "shared_top2"):
            self.assertIn("{candidate_block}", bank[key], f"{key} 缺少 candidate_block 占位符")


class LegacyPromptPlaceholderTests(unittest.TestCase):
    def test_legacy_template_has_no_placeholders(self):
        """legacy 模板是不含占位符的纯文本，因此 legacy 分支只能追加候选块而不能 format。"""
        self.assertNotIn("{candidate_block}", LEGACY_FLAT_NON_COMPACT_SYSTEM_PROMPT)
        self.assertNotIn("{top_k}", LEGACY_FLAT_NON_COMPACT_SYSTEM_PROMPT)


class SystemPromptFallbackTests(unittest.TestCase):
    def test_legacy_flat_non_compact_prompt_embeds_candidates(self):
        prompt = _build_system_prompt(
            compact_codes_enabled=False,
            flat_list_mode=True,
            top_k=10,
            candidate_block=_CANDIDATE_BLOCK,
        )
        self.assertIn("# 候选列表", prompt)
        self.assertIn("WeatherQuery", prompt)
        self.assertIn("TicketBooking", prompt)
        # 未格式化的占位符绝不能泄漏给模型
        self.assertNotIn("{candidate_block}", prompt)

    def test_legacy_flat_non_compact_prompt_without_candidates_stays_plain(self):
        prompt = _build_system_prompt(
            compact_codes_enabled=False,
            flat_list_mode=True,
            top_k=10,
        )
        self.assertEqual(prompt, LEGACY_FLAT_NON_COMPACT_SYSTEM_PROMPT)
        self.assertNotIn("# 候选列表", prompt)

    def test_progressive_flat_branch_embeds_candidates(self):
        """top_k != 2 走 shared 模板——正是此前丢掉占位符的那条。"""
        prompt = _build_system_prompt(
            compact_codes_enabled=True,
            flat_list_mode=True,
            top_k=10,
            candidate_block=_CANDIDATE_BLOCK,
        )
        self.assertIn("# 候选列表", prompt)
        self.assertIn("WeatherQuery", prompt)
        self.assertNotIn("{candidate_block}", prompt)

    def test_progressive_tree_branch_embeds_candidates(self):
        """top_k == 2 走 shared_top2 模板，占位符一直在，作为对照。"""
        prompt = _build_system_prompt(
            compact_codes_enabled=False,
            flat_list_mode=False,
            top_k=2,
            candidate_block=_CANDIDATE_BLOCK,
        )
        self.assertIn("# 候选列表", prompt)
        self.assertIn("WeatherQuery", prompt)
        self.assertNotIn("{candidate_block}", prompt)


if __name__ == "__main__":
    unittest.main()
