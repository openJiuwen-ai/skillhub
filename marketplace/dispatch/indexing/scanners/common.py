from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        return {}, content

    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return {}, content

    frontmatter_str = content[3: end_match.start() + 3]
    body = content[end_match.end() + 3:]
    parsed = _safe_load_frontmatter(frontmatter_str.strip())
    return parsed, body


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
    return path.read_text(encoding="utf-8")


def extract_tags_from_metadata(metadata: Any) -> list[str]:
    """从 plugin.yaml 的 metadata.tags 提取并清洗标签列表。

    与发布校验侧（validation/plugin_yaml.py）同口径归一化：NFKC + casefold、
    剔除含逗号标签、去重变体。即便发布时已校验，索引文本仍须与 DB 入库形式一致，
    否则关键词检索路径与 DB LIKE 路径对同一标签会匹配到不同结果。
    与 retrieval/indexing/scanners/common.py 的同名实现保持同口径。
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


def _safe_load_frontmatter(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return _parse_simple_frontmatter(text)

    try:
        payload = yaml.safe_load(text) or {}
    except Exception:
        return _parse_simple_frontmatter(text)
    if not isinstance(payload, dict):
        return _parse_simple_frontmatter(text)
    return payload


def _parse_simple_frontmatter(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    lines = text.splitlines()
    index = 0
    last_key: str | None = None

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        if raw_line.startswith((" ", "\t")):
            index += 1
            continue
        if ":" not in raw_line:
            if last_key is not None and isinstance(payload.get(last_key), str):
                payload[last_key] = f"{payload[last_key]}\n{stripped}".strip()
            index += 1
            continue

        key, value = raw_line.split(":", 1)
        clean_key = key.strip()
        clean_value = value.strip()

        if clean_value in {"|", "|-", "|+", ">", ">-", ">+"}:
            block_lines: list[str] = []
            index += 1
            while index < len(lines):
                block_line = lines[index]
                if not block_line.strip():
                    block_lines.append("")
                    index += 1
                    continue
                if not block_line.startswith((" ", "\t")):
                    break
                block_lines.append(block_line.lstrip(" \t"))
                index += 1
            payload[clean_key] = "\n".join(block_lines).strip()
            last_key = clean_key
            continue

        payload[clean_key] = _parse_simple_scalar(clean_value)
        last_key = clean_key
        index += 1

    return payload


def _parse_simple_scalar(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text
