"""resolve_cid_from_name_path 的降级路径回归：解析失败应返回 None，而不是抛 NameError。"""

from __future__ import annotations

import unittest

from models.cid import CIDTree


class ResolveCidFromNamePathTests(unittest.TestCase):
    @staticmethod
    def test_non_cid_name_path_returns_none():
        tree = CIDTree()
        # 中文名称不构成合法 CID，应降级走名称匹配；空树匹配不到 -> None
        assert tree.resolve_cid_from_name_path("用户服务") is None

    @staticmethod
    def test_illegal_cid_like_text_returns_none():
        tree = CIDTree()
        for text in ("1abc", "..x", "a..b", "-x"):
            assert tree.resolve_cid_from_name_path(text) is None

    @staticmethod
    def test_empty_and_root_paths():
        tree = CIDTree()
        assert tree.resolve_cid_from_name_path("") is None
        assert tree.resolve_cid_from_name_path("ROOT") == ""


if __name__ == "__main__":
    unittest.main()
