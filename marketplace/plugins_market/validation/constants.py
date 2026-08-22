# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared constants for plugin validation."""

import re

# ---------------------------------------------------------------------------
# Name patterns
# ---------------------------------------------------------------------------

# Generic plugin name: ^[a-z][a-z0-9-]*$
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

# Skill name: starts with lowercase letter, segments separated by single hyphens,
# no leading/trailing hyphen, no consecutive '--'.
# Equivalent to CLI: NAME_PATTERN + SKILL_NAME_PATTERN + _validate_skill_slug
SKILL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
SKILL_NAME_MAX_LEN = 64

# Tool name inside schemas/tools.json (same as generic name)
TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
# Git commit 市场版本号：固定 7 位小写 hex（与 git log --oneline 一致）
GIT_COMMIT_VERSION_PATTERN = re.compile(r"^[0-9a-f]{7}$")
MARKET_VERSION_MAX_LEN = 32


def commit_full_sha_to_version(full_sha: str) -> str:
    """Git 同步专用：从完整 commit SHA 生成库表 7 位版本号（API/CLI 不接受更长 hex）。"""
    h = (full_sha or "").strip().lower()
    if len(h) < 7:
        h = h.ljust(7, "0")
    if len(h) > 40 or not re.fullmatch(r"[0-9a-f]+", h):
        raise ValueError("invalid commit SHA for version")
    return h[:7]


def normalize_market_version_for_storage(version: str) -> str:
    """semver x.y.z 原样；Git commit 须已是 7 位 hex，仅做小写归一。不接受 v 前缀。"""
    v = (version or "").strip()
    if not v:
        return v
    if VERSION_PATTERN.match(v):
        return v
    low = v.lower()
    if GIT_COMMIT_VERSION_PATTERN.match(low):
        return low
    return v


def is_valid_market_version(version: str) -> bool:
    """semver x.y.z 或 Git commit 7 位 hex。"""
    v = (version or "").strip()
    if not v:
        return False
    if VERSION_PATTERN.match(v):
        return True
    return bool(GIT_COMMIT_VERSION_PATTERN.match(v.lower()))

# ---------------------------------------------------------------------------
# Runtime types
# ---------------------------------------------------------------------------

RUNTIME_SKILL = "skill"
RUNTIME_TOOLS = "tools"
RUNTIME_MCP_STDIO = "mcp-stdio"
RUNTIME_RESTFUL_API = "restful-api"
SUPPORTED_RUNTIME_TYPES = {RUNTIME_SKILL, RUNTIME_TOOLS, RUNTIME_MCP_STDIO, RUNTIME_RESTFUL_API}

# ---------------------------------------------------------------------------
# File / zip size limits
# ---------------------------------------------------------------------------

MAX_FILE_SIZE = 512 * 1024 * 1024  # 512 MB – raw zip upload limit
MAX_ZIP_ENTRIES = 1000  # max number of entries in a zip
MAX_DECOMPRESSED_TOTAL = 512 * 1024 * 1024  # 512 MB – cumulative decompressed bytes
MAX_COMPRESSION_RATIO = 50  # pre-check only; real guard is byte counter

ZIP_STREAM_READ_CHUNK_BYTES = 64 * 1024  # streaming zip reads, uploads, bundle I/O (64 KiB)

# Zip entry path: Windows drive-letter prefix (validate_zip_safety + skill bundle extract)
ZIP_ENTRY_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")

# ---------------------------------------------------------------------------
# YAML parsing limits
# ---------------------------------------------------------------------------

MAX_YAML_BYTES = 1 * 1024 * 1024  # 1 MB per YAML document
YAML_MAX_DEPTH = 100
YAML_MAX_ALIASES = 1000
YAML_MAX_SCALAR_LEN = 1 * 1024 * 1024  # 1 MB per scalar string

# ---------------------------------------------------------------------------
# JSON parsing limits
# ---------------------------------------------------------------------------

MAX_JSON_BYTES = 10 * 1024 * 1024  # 10 MB：tools.json 校验与 skill-import 的 manifest.json 读取上限

# ---------------------------------------------------------------------------
# Field length limits
# ---------------------------------------------------------------------------

DISPLAY_NAME_MAX_LEN = 128
PLUGIN_YAML_DESCRIPTION_MAX_LEN = 4096
SKILL_DESC_MAX_LEN = 4096
PLUGIN_TAGS_MAX_COUNT = 32
PLUGIN_TAG_MAX_LEN = 64
# 查询参数侧：tags 过滤参数（逗号分隔）的长度上限。
# 发布侧单资产最多 32 个标签，查询侧留同量级余量；过长的参数直接 422 拒绝，
# 避免构造数百个 JSON_CONTAINS 条件拖垮查询计划。
QUERY_TAGS_MAX_LEN = 512
# parse_tag_filter 解析后的标签数量上限（截断，不报错）：正常前端最多选十几个 chip，
# 与 GET /plugins/tags 的 limit 同量级。
QUERY_TAGS_MAX_COUNT = 20
# 与 models.market_assets.MarketAssetDB.short_desc 列宽一致；较长文案走 detail_desc（Text）
MARKET_ASSET_SHORT_DESC_MAX_LEN = 4096

# ---------------------------------------------------------------------------
# Icon / PNG（仅当包内存在 icon.png 时校验；无则跳过校验且不写入占位对象）
# ---------------------------------------------------------------------------

PNG_MAGIC = b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a"  # 8-byte PNG signature
ICON_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
# 写入 OBS 前：icon 最长边像素上限（列表约 48px，256 已覆盖常见高 DPR）
ICON_PUBLISH_MAX_EDGE_PX = 256
