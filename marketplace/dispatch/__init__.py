"""Public SDK entrypoints for the repository.

This module re-exports canonical package APIs only. It must not depend on
demo/data/tests/training/scripts repository directories.
"""

from indexing import (
    CATALOG_FILENAME,
    CatalogRecord,
    INDEX_MANIFEST_FILENAME,
    TREE_HTML_FILENAME,
    TREE_INDEX_FILENAME,
)
from indexing.tree import DynamicTreeConfig, TreeBuilder, TreeNode, build_tree
from indexing.workflows.artifacts import BuildConfig, BuildMethod

# 向后兼容别名：IndexBuildRuntimeConfig 已合并到 BuildConfig
IndexBuildRuntimeConfig = BuildConfig
from indexing.workflows.index_builder import IndexBuilder
from agent import (
    AgenticRetrievalConfig,
    AgenticSkillRetrievalToolkit,
    LLMConfig,
    SkillIndexBuildConfig,
    SkillIndexRuntimeConfig,
    SkillRecord,
    scan_skill_records,
)

__all__ = [
    "BuildConfig",
    "BuildMethod",
    "CATALOG_FILENAME",
    "CatalogRecord",
    "DynamicTreeConfig",
    "INDEX_MANIFEST_FILENAME",
    "IndexBuilder",
    "IndexBuildRuntimeConfig",
    "AgenticRetrievalConfig",
    "AgenticSkillRetrievalToolkit",
    "LLMConfig",
    "SkillIndexBuildConfig",
    "SkillIndexRuntimeConfig",
    "SkillRecord",
    "TREE_HTML_FILENAME",
    "TREE_INDEX_FILENAME",
    "TreeBuilder",
    "TreeNode",
    "build_tree",
    "scan_skill_records",
]
