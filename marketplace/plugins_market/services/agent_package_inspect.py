# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read-only inspection of agent-plugin / agent-template inner manifest for detail API."""

from __future__ import annotations

import json
import posixpath
import zipfile
from typing import Any

from plugins_market.validation.localized_manifest import (
    localized_manifest_text,
    localized_manifest_tags,
)
from plugins_market.validation.types.skill import parse_skill_frontmatter


def _normalize_zip_path(path: str) -> str:
    return path.replace("\\", "/").rstrip("/")


def _find_inner_manifest_member(names: list[str]) -> str | None:
    candidates: list[str] = []
    for raw in names:
        normalized = _normalize_zip_path(raw)
        if normalized.endswith("/manifest.json"):
            candidates.append(normalized)
        elif normalized == "manifest.json":
            candidates.append(normalized)
    if not candidates:
        return None
    # Prefer nested inner payload: outer/name/manifest.json
    candidates.sort(key=lambda item: (-item.count("/"), len(item)))
    return candidates[0]


def _payload_prefix(manifest_member: str) -> str:
    if manifest_member == "manifest.json":
        return ""
    return manifest_member[: -len("manifest.json")]


def _read_zip_text(zf: zipfile.ZipFile, member: str) -> str | None:
    try:
        return zf.read(member).decode("utf-8", errors="replace")
    except KeyError:
        return None


def _localized_item_text(value: Any) -> str:
    return localized_manifest_text(value)


def _capability_label(item: dict[str, Any], *, fallback: str) -> tuple[str, str]:
    name = _localized_item_text(item.get("display_name")) or fallback
    description = _localized_item_text(item.get("display_description"))
    return name, description


def _summarize_mcp_entry(item: dict[str, Any]) -> dict[str, str] | None:
    connector = item.get("connector")
    if isinstance(connector, str) and connector.strip():
        cid = connector.strip()
        return {"kind": "mcp", "id": cid, "name": cid, "description": "connector"}
    file_path = item.get("file")
    if isinstance(file_path, str) and file_path.strip():
        rel = file_path.strip().replace("\\", "/").removeprefix("./")
        base = posixpath.basename(rel) or rel
        return {"kind": "mcp", "id": rel, "name": base, "description": "file"}
    dir_path = item.get("dir")
    if isinstance(dir_path, str) and dir_path.strip():
        rel = dir_path.strip().replace("\\", "/").removeprefix("./").rstrip("/")
        base = posixpath.basename(rel) or rel
        return {"kind": "mcp", "id": rel, "name": base, "description": "dir"}
    return None


def _read_persona_markdown(
    zf: zipfile.ZipFile,
    names: list[str],
    payload_prefix: str,
    persona_dir: str,
) -> str | None:
    persona_dir_norm = persona_dir.strip().replace("\\", "/").removeprefix("./").rstrip("/")
    prefix = f"{payload_prefix}{persona_dir_norm}/"
    parts: list[tuple[str, str]] = []
    for raw in names:
        normalized = _normalize_zip_path(raw)
        if not normalized.startswith(prefix) or not normalized.lower().endswith(".md"):
            continue
        text = _read_zip_text(zf, raw)
        if text is None:
            continue
        rel = normalized[len(prefix) :]
        parts.append((rel, text.strip()))
    if not parts:
        return None
    parts.sort(key=lambda pair: pair[0].lower())
    return "\n\n---\n\n".join(body for _, body in parts if body)


def extract_agent_package_profile(zf: zipfile.ZipFile) -> dict[str, Any] | None:
    """Parse inner manifest.json (+ persona/skills) for agent detail display."""
    names = [info.filename for info in zf.infolist() if not info.is_dir()]
    manifest_member = _find_inner_manifest_member(names)
    if not manifest_member:
        return None
    raw_manifest = _read_zip_text(zf, manifest_member)
    if not raw_manifest:
        return None
    try:
        manifest = json.loads(raw_manifest)
    except json.JSONDecodeError:
        return None
    if not isinstance(manifest, dict):
        return None

    package_type = manifest.get("package_type")
    if package_type not in ("plugin", "agent_template"):
        return None

    prefix = _payload_prefix(manifest_member)
    capabilities: list[dict[str, str]] = []

    skills = manifest.get("skills")
    if isinstance(skills, list):
        for index, entry in enumerate(skills):
            if not isinstance(entry, dict):
                continue
            rel_dir = entry.get("dir")
            if not isinstance(rel_dir, str) or not rel_dir.strip():
                continue
            skill_rel = rel_dir.strip().replace("\\", "/").removeprefix("./").rstrip("/")
            skill_path = f"{prefix}{skill_rel}/SKILL.md"
            skill_member = next(
                (n for n in names if _normalize_zip_path(n) == skill_path),
                None,
            )
            skill_name = posixpath.basename(skill_rel) or f"skill-{index + 1}"
            skill_desc = ""
            if skill_member:
                skill_raw = _read_zip_text(zf, skill_member)
                if skill_raw:
                    frontmatter, _ = parse_skill_frontmatter(skill_raw.encode("utf-8"))
                    skill_name = str(frontmatter.get("name") or skill_name)
                    skill_desc = str(frontmatter.get("description") or "")
            capabilities.append(
                {
                    "kind": "skill",
                    "id": skill_name,
                    "name": skill_name,
                    "description": skill_desc,
                }
            )

    for field, kind in (("tools", "tool"), ("rails", "rail")):
        entries = manifest.get(field)
        if not isinstance(entries, list):
            continue
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                continue
            fallback = str(item.get("class") or item.get("file") or f"{kind}-{index + 1}")
            name, description = _capability_label(item, fallback=fallback)
            cap_id = str(item.get("class") or item.get("file") or name)
            capabilities.append(
                {"kind": kind, "id": cap_id, "name": name, "description": description}
            )

    mcps = manifest.get("mcps")
    if isinstance(mcps, list):
        for item in mcps:
            if not isinstance(item, dict):
                continue
            summary = _summarize_mcp_entry(item)
            if summary:
                capabilities.append(summary)

    persona_markdown: str | None = None
    persona = manifest.get("persona")
    if isinstance(persona, dict):
        persona_dir = persona.get("dir")
        if isinstance(persona_dir, str) and persona_dir.strip():
            persona_markdown = _read_persona_markdown(zf, names, prefix, persona_dir)

    quick_inputs: list[str] = []
    raw_quick = manifest.get("quick_inputs")
    if isinstance(raw_quick, list):
        for item in raw_quick:
            text = _localized_item_text(item)
            if text:
                quick_inputs.append(text)

    category = manifest.get("category")
    source = manifest.get("source")

    return {
        "package_type": package_type,
        "category": category if isinstance(category, str) and category.strip() else None,
        "source": source if isinstance(source, str) and source.strip() else None,
        "default_init_input": _localized_item_text(manifest.get("default_init_input")) or None,
        "quick_inputs": quick_inputs,
        "persona_markdown": persona_markdown,
        "capabilities": capabilities,
        "manifest_tags": localized_manifest_tags(manifest.get("tags")),
    }
