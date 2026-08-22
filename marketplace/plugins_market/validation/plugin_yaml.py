# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""plugin.yaml parsing and public-field validation."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
import yaml
import yaml.composer
import yaml.constructor
import yaml.nodes

from plugins_market.core.errors import PublishError
from plugins_market.validation.base import raise_invalid_config, require_string_field
from plugins_market.validation.constants import (
    DISPLAY_NAME_MAX_LEN,
    MARKET_VERSION_MAX_LEN,
    MAX_YAML_BYTES,
    NAME_PATTERN,
    PLUGIN_TAGS_MAX_COUNT,
    PLUGIN_TAG_MAX_LEN,
    PLUGIN_YAML_DESCRIPTION_MAX_LEN,
    RUNTIME_SKILL,
    SKILL_NAME_MAX_LEN,
    SKILL_NAME_PATTERN,
    SUPPORTED_RUNTIME_TYPES,
    YAML_MAX_ALIASES,
    YAML_MAX_DEPTH,
    YAML_MAX_SCALAR_LEN,
    is_valid_market_version,
)


# ---------------------------------------------------------------------------
# Bounded SafeLoader
# ---------------------------------------------------------------------------

class _BoundedSafeLoader(yaml.SafeLoader):
    """yaml.SafeLoader extended with resource-limit guards.

    Protects against:
    - Stack overflow from deeply nested structures
    - Alias/anchor explosion
    - Oversized scalar strings
    """

    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self._compose_depth = 0
        self._alias_count = 0

    def compose_mapping_node(self, anchor: str | None) -> yaml.nodes.MappingNode:
        self._compose_depth += 1
        if self._compose_depth > YAML_MAX_DEPTH:
            raise yaml.YAMLError(
                f"YAML 嵌套深度超过上限（最大 {YAML_MAX_DEPTH} 层）"
            )
        node = super().compose_mapping_node(anchor)
        self._compose_depth -= 1
        return node

    def compose_sequence_node(self, anchor: str | None) -> yaml.nodes.SequenceNode:
        self._compose_depth += 1
        if self._compose_depth > YAML_MAX_DEPTH:
            raise yaml.YAMLError(
                f"YAML 嵌套深度超过上限（最大 {YAML_MAX_DEPTH} 层）"
            )
        node = super().compose_sequence_node(anchor)
        self._compose_depth -= 1
        return node

    def compose_scalar_node(self, anchor: str | None) -> yaml.nodes.ScalarNode:
        node = super().compose_scalar_node(anchor)
        if isinstance(node.value, str) and len(node.value) > YAML_MAX_SCALAR_LEN:
            raise yaml.YAMLError(
                f"YAML 标量字符串长度超过上限（最大 {YAML_MAX_SCALAR_LEN // 1024} KB）"
            )
        return node

    def compose_alias_node(self, anchor: str) -> yaml.nodes.Node:
        self._alias_count += 1
        if self._alias_count > YAML_MAX_ALIASES:
            raise yaml.YAMLError(
                f"YAML 别名/锚点数量超过上限（最大 {YAML_MAX_ALIASES}）"
            )
        return super().compose_alias_node(anchor)


def safe_load_yaml(text: str, *, context: str = "YAML") -> Any:
    """Parse YAML text with resource-limit guards.

    Args:
        text: Raw YAML text (already byte-limited by caller).
        context: Human-readable label used in error messages.

    Returns:
        Parsed Python object.

    Raises:
        PublishError on parse failure or resource-limit violation.
    """
    try:
        loader = _BoundedSafeLoader(text)
        try:
            return loader.get_single_data()
        finally:
            loader.dispose()
    except yaml.YAMLError as exc:
        raise PublishError(
            code=400,
            error="invalid_plugin_config",
            message=f"{context} 解析失败：{exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PluginYamlPublicFields:
    """Validated public fields from plugin.yaml."""

    name: str
    display_name: str
    short_desc: str
    publisher_name: str
    tags: list[str]
    runtime_type: str


# ---------------------------------------------------------------------------
# Public validation entry-point
# ---------------------------------------------------------------------------

def validate_plugin_yaml_bytes(raw_bytes: bytes) -> str:
    """Enforce the 1 MB YAML byte limit; return decoded text."""
    if len(raw_bytes) > MAX_YAML_BYTES:
        raise PublishError(
            code=400,
            error="invalid_plugin_config",
            message=f"plugin.yaml 超过大小上限（最大 {MAX_YAML_BYTES // 1024} KB）",
        )
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PublishError(
            code=400,
            error="invalid_plugin_config",
            message=f"plugin.yaml 编码必须为 UTF-8：{exc}",
        ) from exc


def validate_plugin_yaml_public(data: dict[str, Any]) -> PluginYamlPublicFields:
    """Validate plugin.yaml public fields for all plugin types.

    The *runtime_type* returned is the normalised lower-case string.
    Type-specific slug validation (skill name) is done here.
    """
    # root must be mapping
    if not isinstance(data, dict):
        raise_invalid_config("plugin.yaml 根结构必须为 mapping（字典）")

    # name
    name = require_string_field(data.get("name"), "name")
    if not NAME_PATTERN.match(name):
        raise_invalid_config(
            "plugin.yaml 中 name 必须符合 ^[a-z][a-z0-9-]*$"
        )

    # version is optional at this stage – publish() handles missing version.
    version_val = data.get("version")
    if version_val is not None:
        version = require_string_field(version_val, "version")
        if len(version) > MARKET_VERSION_MAX_LEN:
            raise_invalid_config(
                f"plugin.yaml 中 version 长度不得超过 {MARKET_VERSION_MAX_LEN} 个字符"
            )
        if not is_valid_market_version(version):
            raise_invalid_config(
                "plugin.yaml 中 version 必须符合 x.y.z 语义化版本格式，或为 7 位小写十六进制 Git commit"
            )

    # display_name
    display_name = require_string_field(data.get("display_name"), "display_name")
    if len(display_name) > DISPLAY_NAME_MAX_LEN:
        raise_invalid_config(
            f"plugin.yaml 中 display_name 长度不得超过 {DISPLAY_NAME_MAX_LEN} 个字符"
        )

    # description
    desc_val = data.get("description")
    description = desc_val if isinstance(desc_val, str) else ""
    if len(description) > PLUGIN_YAML_DESCRIPTION_MAX_LEN:
        raise_invalid_config(
            f"plugin.yaml 中 description 长度不得超过 {PLUGIN_YAML_DESCRIPTION_MAX_LEN} 个字符"
        )

    # runtime.type
    runtime = data.get("runtime")
    if not isinstance(runtime, dict) or not runtime.get("type"):
        raise_invalid_config("plugin.yaml 配置文件格式错误或缺失：runtime.type 必填")
    runtime_type_raw = str(runtime["type"]).strip().lower()
    if runtime_type_raw not in SUPPORTED_RUNTIME_TYPES:
        raise_invalid_config(
            f"不支持的 runtime.type: {runtime['type']!r}"
            f"（支持 {', '.join(sorted(SUPPORTED_RUNTIME_TYPES))}）"
        )
    runtime_type = runtime_type_raw

    # metadata
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        raise_invalid_config("plugin.yaml 配置文件格式错误或缺失：metadata 必填")

    publisher_name = require_string_field(metadata.get("author"), "metadata.author")

    # tags – must be a list; elements must be non-empty strings
    raw_tags = metadata.get("tags")
    if raw_tags is None:
        raw_tags = []
    if not isinstance(raw_tags, list):
        raise_invalid_config(
            "plugin.yaml 中 metadata.tags 必须为字符串数组（允许空数组）"
        )
    if len(raw_tags) > PLUGIN_TAGS_MAX_COUNT:
        raise_invalid_config(
            f"plugin.yaml 中 metadata.tags 数量不得超过 {PLUGIN_TAGS_MAX_COUNT} 个"
        )
    tags: list[str] = []
    for i, item in enumerate(raw_tags):
        if not isinstance(item, str):
            raise_invalid_config(
                f"plugin.yaml 中 metadata.tags[{i}] 必须为字符串，"
                f"实际类型为 {type(item).__name__}"
            )
        stripped = item.strip()
        if not stripped:
            raise_invalid_config(
                f"plugin.yaml 中 metadata.tags[{i}] strip 后不得为空字符串"
            )
        # 归一化：NFKC 折叠全角->半角（防中文输入法全角模式产生的变体），
        # casefold 统一大小写。两者对纯中文恒等、无副作用；归一后去重，
        # 挡住大小写/全半角变体造成的重复垃圾标签。
        normalized = unicodedata.normalize("NFKC", stripped).casefold()
        if len(normalized) > PLUGIN_TAG_MAX_LEN:
            raise_invalid_config(
                f"plugin.yaml 中 metadata.tags[{i}] 长度不得超过 {PLUGIN_TAG_MAX_LEN} 个字符"
            )
        if "," in normalized:
            # tags 查询参数以逗号分隔多标签，标签本身含逗号会破坏该协议
            raise_invalid_config(
                f"plugin.yaml 中 metadata.tags[{i}] 不得包含逗号（多标签过滤参数以逗号分隔）"
            )
        if any(normalized == t for t in tags):
            continue  # 重复（含大小写/全半角变体）静默跳过，不占名额
        tags.append(normalized)

    # skill: extra slug + length validation
    if runtime_type == RUNTIME_SKILL:
        if len(name) > SKILL_NAME_MAX_LEN:
            raise_invalid_config(
                f"skill 插件 name 长度不得超过 {SKILL_NAME_MAX_LEN} 个字符"
            )
        if not SKILL_NAME_PATTERN.match(name):
            raise_invalid_config(
                "skill 插件 name 必须使用小写字母、数字，各段之间用单个连字符分隔，"
                "首尾不得为连字符，且不得有连续 '--'"
            )

    # non-skill types require compatibility.python (PEP 440)
    if runtime_type != RUNTIME_SKILL:
        _validate_compatibility_python(data)

    return PluginYamlPublicFields(
        name=name,
        display_name=display_name,
        short_desc=description,
        publisher_name=publisher_name,
        tags=tags,
        runtime_type=runtime_type,
    )


def _validate_compatibility_python(data: dict[str, Any]) -> None:
    """Validate that compatibility.python exists and is a valid PEP 440 specifier set."""
    compat = data.get("compatibility")
    if not isinstance(compat, dict):
        raise_invalid_config(
            "非 skill 类型插件必须包含 compatibility 字段"
        )
    python_spec = compat.get("python")
    if not isinstance(python_spec, str) or not python_spec.strip():
        raise_invalid_config(
            "非 skill 类型插件必须包含 compatibility.python 字段"
        )
    try:
        SpecifierSet(python_spec.strip())
    except InvalidSpecifier:
        raise_invalid_config(
            "plugin.yaml 中 compatibility.python 必须为合法的 PEP 440 版本约束"
        )
