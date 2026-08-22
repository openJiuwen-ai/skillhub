# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from shared.limits import MAX_TEXT_FILE_BYTES, read_text_file


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        return {}, content

    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return {}, content

    frontmatter_str = content[3: end_match.start() + 3]
    body = content[end_match.end() + 3:]
    parsed = _parse_frontmatter_yaml(frontmatter_str)
    if parsed is not None:
        return parsed, body
    return _parse_frontmatter_fallback(frontmatter_str), body


def _parse_frontmatter_yaml(frontmatter_str: str) -> dict[str, Any] | None:
    try:
        import yaml
    except Exception:
        return None
    try:
        parsed = yaml.safe_load(frontmatter_str) or {}
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return {}
    return {str(key).strip(): value for key, value in parsed.items() if str(key).strip()}


def _parse_frontmatter_fallback(frontmatter_str: str) -> dict[str, str]:
    frontmatter: dict[str, str] = {}
    lines = frontmatter_str.strip().split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        parsed_value = value.strip()
        if parsed_value in {"|", ">"}:
            block_lines: list[str] = []
            while index < len(lines):
                next_line = lines[index]
                if next_line and not next_line[:1].isspace() and ":" in next_line:
                    break
                block_lines.append(next_line.strip())
                index += 1
            parsed_value = "\n".join(line for line in block_lines if line).strip()
        if parsed_value.startswith('"') and parsed_value.endswith('"'):
            parsed_value = parsed_value[1:-1]
        elif parsed_value.startswith("'") and parsed_value.endswith("'"):
            parsed_value = parsed_value[1:-1]
        frontmatter[key] = parsed_value
    return frontmatter


def clean_first_paragraph(body: str, *, limit: int = 500) -> str:
    text = str(body or "").strip()
    if not text:
        return ""
    first_para = text.split("\n\n")[0]
    first_para = re.sub(r"^#+\s*", "", first_para)
    first_para = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", first_para)
    return first_para[:limit].strip()


def read_text_if_exists(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return read_text_file(path, max_bytes=MAX_TEXT_FILE_BYTES, label="scanner text file")


def extract_tags_from_metadata(metadata: Any) -> list[str]:
    """从 plugin.yaml 的 metadata.tags 提取并清洗标签列表。

    与发布校验侧（validation/plugin_yaml.py）同口径归一化：NFKC + casefold、
    剔除含逗号标签、去重变体。即便发布时已校验，索引文本仍须与 DB 入库形式一致，
    否则关键词检索路径与 DB LIKE 路径对同一标签会匹配到不同结果。
    """
    if not isinstance(metadata, dict):
        return []
    raw_tags = metadata.get("tags")
    if not isinstance(raw_tags, list):
        return []
    tags: list[str] = []
    for item in raw_tags:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        # 与发布校验侧同口径：NFKC 折全角->半角、casefold 统一大小写；
        # 归一化后查逗号（全角逗号被折成半角一并挡住）并去重变体，
        # 保证索引文本与 DB 入库形式一致。
        normalized = unicodedata.normalize("NFKC", stripped).casefold()
        if not normalized or "," in normalized:
            continue
        if any(normalized == t for t in tags):
            continue
        tags.append(normalized)
    return tags
