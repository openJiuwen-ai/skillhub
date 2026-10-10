from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from typing import Any

import yaml


@dataclass(frozen=True)
class SkillMdExtract:
    asset_id: str
    name: str
    description: str
    embedding_text: str  # name + "," + skill content
    skill_member_path: str


def find_skill_md_member(members: list[str]) -> str | None:
    # Most packages ship `SKILL.md` (uppercase) under `<skill_name>/<skill_name>/`.
    candidates: list[str] = []
    for n in members:
        base = n.split("/")[-1]
        if base.lower() == "skill.md":
            candidates.append(n)
    return sorted(set(candidates))[0] if candidates else None


def _extract_yaml_front_matter(text: str) -> tuple[str | None, str]:
    # Typical format:
    # ---
    # name: xxx
    # description: |
    #   ...
    # ---
    # <markdown body>
    if not text.lstrip().startswith("---"):
        return None, text

    # Find closing `---` line after opening.
    m = re.match(r"^\s*---\s*\n([\s\S]+?)\n---\s*\n", text)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def extract_from_skill_md_text(
    *,
    asset_id: str,
    skill_md_text: str,
    member_path: str,
) -> SkillMdExtract:
    front_matter, body = _extract_yaml_front_matter(skill_md_text)

    name: str | None = None
    description: str | None = None
    if front_matter:
        try:
            data: dict[str, Any] = yaml.safe_load(front_matter) or {}
            name = (data.get("name") or None) and str(data["name"]).strip() or None
            # In the current dataset we saw `description: | ...`.
            description = (
                (data.get("skill") or data.get("description") or None)
                and str(data.get("skill") or data.get("description") or "")
                or None
            )
        except Exception:
            # YAML parse fallback (best-effort).
            m_name = re.search(r"^\s*name\s*:\s*(.+?)\s*$", front_matter, flags=re.M | re.I)
            if m_name:
                name = m_name.group(1).strip()

            m_desc = re.search(
                r"^\s*(?:description|skill)\s*:\s*\|\s*\n([\s\S]+?)(?=^\s*\w[\w-]*\s*:|\Z)",
                front_matter,
                flags=re.M | re.I,
            )
            if m_desc:
                description = m_desc.group(1).strip()

    # If `description` doesn't exist, fall back to markdown body.
    if not description or description.strip() == "":
        description = body.strip()

    if not name or name.strip() == "":
        name = asset_id

    # User requested comma-separated: `name, skill_content`.
    embedding_text = f"{name},{description}".strip()

    return SkillMdExtract(
        asset_id=asset_id,
        name=name,
        description=description,
        embedding_text=embedding_text,
        skill_member_path=member_path,
    )


def extract_skill_from_zip(
    *,
    zip_path: str,
    asset_id: str,
) -> SkillMdExtract:
    with zipfile.ZipFile(zip_path, "r") as z:
        members = z.namelist()
        member = find_skill_md_member(members)
        if not member:
            raise FileNotFoundError(f"No SKILL.md in {zip_path}")
        text = z.read(member).decode("utf-8", errors="ignore")
        return extract_from_skill_md_text(
            asset_id=asset_id,
            skill_md_text=text,
            member_path=member,
        )


def embedding_text_from_market_fields(
    *,
    asset_id: str,
    display_name: str = "",
    name: str = "",
    short_desc: str = "",
) -> SkillMdExtract:
    """Fallback when SKILL.md is missing/unreadable: use MySQL market fields."""
    resolved_name = (display_name or name or asset_id).strip() or asset_id
    description = (short_desc or "").strip()
    return SkillMdExtract(
        asset_id=asset_id,
        name=resolved_name,
        description=description,
        embedding_text=f"{resolved_name},{description}".strip(),
        skill_member_path="",
    )

