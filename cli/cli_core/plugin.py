# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import posixpath
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from cli_core.logging_config import get_logger
from cli_core.market import PublishError, plugin_upload
from cli_core.schemas import (
    MARKETPLACE_VERSION_PATTERN,
    PluginPublishResult,
    PublishPluginInput,
    PublishRequest,
)
from cli_core.schemas.plugin import is_valid_marketplace_version
from cli_core.utils import sha256_file_hex

logger = get_logger(__name__)

PACK_IGNORE_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".eggs",
        "dist",
        "out",
        "__MACOSX",
    }
)
PACK_IGNORE_SUFFIXES = (".pyc", ".pyo", ".egg-info")
SKILL_IMPORT_BUNDLE_MAX_BYTES = 512 * 1024 * 1024

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

SUPPORTED_PLUGIN_TYPES = {
    "skill",
    "swarmskill",
    "agent-plugin",
    "agent-mcp",
    "agent-template",
    "agent-group",
}
SKILL_LIKE_RUNTIME_TYPES = frozenset({"skill", "swarmskill"})
AGENT_ASSET_RUNTIME_TYPES = frozenset({"agent-plugin", "agent-mcp", "agent-template", "agent-group"})
LEGACY_RUNTIME_TYPES = frozenset({"tools", "mcp-stdio", "restful-api"})
_AGENT_MANIFEST_PACKAGE_TYPE = {
    "agent-plugin": "plugin",
    "agent-mcp": "mcp",
    "agent-template": "agent_template",
    "agent-group": "agent_group",
}
_AGENT_PLUGIN_FORBIDDEN_FIELDS = ("persona", "agent_card", "model", "subagents", "memories", "rubrics")
_AGENT_MCP_INTEGRATION_TYPES = frozenset({"stdio-mcp", "remote-mcp", "cli", "skill-only"})

SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_NAME_MAX_LEN = 64
SKILL_DESC_MAX_LEN = 4096
DEFAULT_TEAMSKILL_ROLES = ("id_01", "id_02")


def _collect_teamskill_role_errors(fm: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    roles = fm.get("roles", [])
    if not isinstance(roles, list) or not roles:
        errors.append("frontmatter `roles` must be a non-empty list")
        return errors

    role_ids: list[str] = []
    for i, role in enumerate(roles):
        if not isinstance(role, dict):
            errors.append(f"roles[{i}] must be an object")
            continue
        if "id" not in role:
            errors.append(f"roles[{i}] missing required field `id`")
            continue
        rid = role.get("id")
        if not isinstance(rid, str) or not rid.strip():
            errors.append(f"roles[{i}] `id` must be a non-empty string")
        else:
            role_ids.append(rid.strip())

    if len(role_ids) < 2:
        errors.append(
            "frontmatter `roles` must list at least 2 entries with valid `id` (team-skill multi-role contract)."
        )
    elif len(role_ids) != len(set(role_ids)):
        errors.append("frontmatter `roles` must not repeat the same `id`")

    return errors


def teamskill_validate_directory(root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    prefix = "Teamskill: "

    if not root.exists() or not root.is_dir():
        return ([f"{prefix}not a directory: {root}"], [])

    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        return (errors, warnings)

    fm, fm_err = _parse_skill_frontmatter(skill_md)
    if fm_err:
        return ([f"{prefix}{fm_err}"], warnings)

    if fm is None:
        errors.append("SKILL.md: missing YAML frontmatter (--- ... ---)")
    elif not isinstance(fm, dict):
        errors.append("SKILL.md: frontmatter must be a mapping")
    else:
        errors.extend(f"SKILL.md: {m}" for m in _collect_teamskill_role_errors(fm))

    return ([prefix + e for e in errors], warnings)


def _display_title(kebab: str) -> str:
    return " ".join(part.capitalize() for part in kebab.split("-") if part)


def _render_skill_md(
    name: str,
    *,
    description: str,
    kind: str | None = None,
    role_ids: list[str] | None = None,
    include_version: bool = False,
) -> str:
    display = _display_title(name)
    lines: list[str] = [
        "---",
        f"name: {name}",
        f'description: "{description}"',
    ]
    if include_version:
        lines.append('version: "0.0.1"')
    if kind:
        lines.append(f"kind: {kind}")
    if role_ids is not None:
        lines.append("roles:")
        for rid in role_ids:
            lines.append(f"  - id: {rid}")
    lines.extend(
        [
            "---",
            "",
            f"# {display}",
            "",
            "## Instructions",
            "",
            "TODO: add step-by-step guidance.",
            "",
        ]
    )
    return "\n".join(lines)


def scaffold_teamskill_skill_directory(
    skill_dir: Path,
    plugin_name: str,
    role_ids: list[str],
    *,
    kind: str,
) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        _render_skill_md(
            plugin_name,
            description="TODO: describe this team skill.",
            kind=kind,
            role_ids=role_ids,
        ),
        encoding="utf-8",
    )


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    warnings: list[str]
    runtime_type: str | None = None


def _validate_skill_slug(value: str, *, field: str = "name") -> str | None:
    if len(value) > SKILL_NAME_MAX_LEN:
        return f"{field} must be at most {SKILL_NAME_MAX_LEN} characters (Agent Skills rules)"
    if not SKILL_NAME_PATTERN.match(value):
        return (
            f"{field} must use lowercase letters, digits, and single hyphens between segments "
            "(no leading/trailing hyphen, no '--')"
        )
    return None


def _parse_skill_frontmatter(skill_md: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"cannot read SKILL.md: {e}"
    if not text.lstrip().startswith("---"):
        return None, "SKILL.md must start with YAML frontmatter (---)"
    rest = text.lstrip()[3:].lstrip("\n")
    end = rest.find("\n---")
    if end == -1:
        return None, "SKILL.md frontmatter not closed (missing closing ---)"
    raw_fm = rest[:end]
    try:
        fm = yaml.safe_load(raw_fm)
    except yaml.YAMLError as e:
        return None, f"invalid SKILL.md frontmatter YAML: {e}"
    if not isinstance(fm, dict):
        return None, "SKILL.md frontmatter must be a mapping"
    return fm, None


def _validate_skill_frontmatter_fields(
    fm: dict[str, Any], skill_dir_name: str, yaml_name: str
) -> list[str]:
    errors: list[str] = []
    name_val = fm.get("name")
    if not isinstance(name_val, str):
        errors.append("SKILL.md frontmatter name is required and must be a string")
    else:
        nm = name_val.strip()
        skill_nm_err = _validate_skill_slug(nm, field="SKILL.md frontmatter name")
        if skill_nm_err:
            errors.append(skill_nm_err)
        elif nm != skill_dir_name:
            errors.append("SKILL.md frontmatter name must equal the skill directory name")
        elif nm != yaml_name:
            errors.append(
                "SKILL.md frontmatter name must equal the declared skill name "
                "(plugin.yaml name when present)"
            )

    desc_val = fm.get("description")
    if not isinstance(desc_val, str):
        errors.append("SKILL.md frontmatter description is required and must be a string")
    else:
        stripped = desc_val.strip()
        if not stripped:
            errors.append("SKILL.md frontmatter description must be non-empty")
        elif len(stripped) > SKILL_DESC_MAX_LEN:
            errors.append(
                f"SKILL.md frontmatter description must be at most {SKILL_DESC_MAX_LEN} characters"
            )
    return errors


def _find_skill_subdirectory(root: Path) -> Path | None:
    """Exactly one non-hidden child directory that contains SKILL.md."""
    found: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / "SKILL.md").is_file():
            found.append(child)
    if len(found) != 1:
        return None
    return found[0]


def _find_skill_workspace(root: Path) -> tuple[Path, str] | None:
    """Skill tree: flat ``root/SKILL.md`` or one child dir with ``SKILL.md``; slug from frontmatter ``name``."""
    if (root / "SKILL.md").is_file():
        fm, fm_err = _parse_skill_frontmatter(root / "SKILL.md")
        if fm_err is not None or fm is None:
            return None
        name_val = fm.get("name")
        if not isinstance(name_val, str):
            return None
        slug = name_val.strip()
        slug_err = _validate_skill_slug(slug, field="SKILL.md frontmatter name")
        if slug_err:
            return None
        return root, slug

    nested = _find_skill_subdirectory(root)
    if nested is None or not (nested / "SKILL.md").is_file():
        return None
    fm, fm_err = _parse_skill_frontmatter(nested / "SKILL.md")
    if fm_err is not None or fm is None:
        return None
    name_val = fm.get("name")
    if not isinstance(name_val, str):
        return None
    slug = name_val.strip()
    slug_err = _validate_skill_slug(slug, field="SKILL.md frontmatter name")
    if slug_err:
        return None
    # Slug-shaped directory name must match ``name``; version-style dir names may differ.
    if (
        SKILL_NAME_PATTERN.match(nested.name)
        and _validate_skill_slug(nested.name, field="skill directory name") is None
        and slug != nested.name
    ):
        return None
    return nested, slug


def _diagnose_skill_like_workspace_when_unresolved(root: Path) -> list[str]:
    """Explain malformed flat/nested skill-like layouts before generic plugin checks.

    Avoid mis-reporting generic plugin requirements (``plugin.yaml``, ``README.md``) for a broken skill bundle.
    """
    candidates: list[tuple[Path, str | None]] = []
    flat = root / "SKILL.md"
    if flat.is_file():
        candidates.append((flat, None))
    nested = _find_skill_subdirectory(root)
    if nested is not None and (nested / "SKILL.md").is_file():
        candidates.append((nested / "SKILL.md", nested.name))
    if not candidates or _find_skill_workspace(root) is not None:
        return []

    skill_md, nested_dir_name = candidates[0]
    fm, fm_err = _parse_skill_frontmatter(skill_md)
    if fm_err:
        return [fm_err]
    if fm is None:
        return ["cannot parse SKILL.md frontmatter"]
    name_val = fm.get("name")
    if not isinstance(name_val, str):
        return ["SKILL.md frontmatter name is required and must be a string"]
    slug = name_val.strip()
    if not slug:
        return ["SKILL.md frontmatter name is required and must be non-empty"]
    slug_err = _validate_skill_slug(slug, field="SKILL.md frontmatter name")
    if slug_err:
        return [slug_err]
    if nested_dir_name and SKILL_NAME_PATTERN.match(nested_dir_name):
        nested_dir_err = _validate_skill_slug(nested_dir_name, field="skill directory name")
        if nested_dir_err is None and slug != nested_dir_name:
            return [
                f"SKILL.md frontmatter name ({slug!r}) must equal skill directory name ({nested_dir_name!r})"
            ]
    return []


def _diagnose_nested_skill_workspace(root: Path) -> list[str]:
    nested = _find_skill_subdirectory(root)
    if nested is None or not (nested / "SKILL.md").is_file():
        return []
    fm, fm_err = _parse_skill_frontmatter(nested / "SKILL.md")
    if fm_err:
        return [fm_err]
    if fm is None:
        return ["cannot parse SKILL.md frontmatter"]
    name_val = fm.get("name")
    if not isinstance(name_val, str):
        return ["SKILL.md frontmatter name is required and must be a string"]
    slug = name_val.strip()
    if not slug:
        return ["SKILL.md frontmatter name is required and must be non-empty"]
    slug_err = _validate_skill_slug(slug, field="SKILL.md frontmatter name")
    if slug_err:
        return [slug_err]
    if SKILL_NAME_PATTERN.match(nested.name):
        nested_dir_err = _validate_skill_slug(nested.name, field="skill directory name")
        if nested_dir_err is None and slug != nested.name:
            return [f"SKILL.md frontmatter name ({slug!r}) must equal skill directory name ({nested.name!r})"]
    return []


def _diagnose_skill_like_layout(root: Path) -> list[str]:
    return _diagnose_skill_like_workspace_when_unresolved(root) or _diagnose_nested_skill_workspace(root)


def _diagnose_skill_like_publish_root(root: Path) -> list[str]:
    if _find_skill_workspace(root) is not None:
        return []

    skill_like_diag = _diagnose_skill_like_layout(root)
    if skill_like_diag:
        return skill_like_diag

    skill_dirs = [
        child
        for child in root.iterdir()
        if child.is_dir() and not child.name.startswith(".") and (child / "SKILL.md").is_file()
    ]
    if len(skill_dirs) > 1:
        return [
            "skill/swarmskill publish expects exactly one skill directory under the publish root; "
            f"found {len(skill_dirs)}: {', '.join(sorted(child.name for child in skill_dirs))}"
        ]

    return [
        "skill/swarmskill publish expects root/SKILL.md or exactly one child directory containing SKILL.md"
    ]


def _infer_skill_like_runtime(root: Path) -> tuple[str | None, Path | None]:
    """Infer ``skill`` vs ``swarmskill`` from ``SKILL.md`` ``kind``; (None, None) if not skill-like."""
    ws = _find_skill_workspace(root)
    if ws is not None:
        skill_dir, _slug = ws
        fm, fm_err = _parse_skill_frontmatter(skill_dir / "SKILL.md")
        if fm_err is not None or fm is None:
            return None, None
        if str(fm.get("kind") or "").strip().lower() in {"team-skill", "swarm-skill"}:
            return "swarmskill", skill_dir
        return "skill", skill_dir
    nested = _find_skill_subdirectory(root)
    if nested is None or not (nested / "SKILL.md").is_file():
        return None, None
    fm, fm_err = _parse_skill_frontmatter(nested / "SKILL.md")
    if fm_err is not None or fm is None:
        return None, None
    if str(fm.get("kind") or "").strip().lower() in {"team-skill", "swarm-skill"}:
        return "swarmskill", nested
    return "skill", nested


def _read_skill_frontmatter_tags(skill_dir: Path, fallback_runtime: str | None = None) -> list[str]:
    """Read tags from SKILL.md frontmatter; return fallback when absent."""
    fm, fm_err = _parse_skill_frontmatter(skill_dir / "SKILL.md")
    if fm is None or fm_err is not None:
        return [fallback_runtime] if fallback_runtime else []
    tags_val = fm.get("tags")
    tags: list[str] = []
    if isinstance(tags_val, list):
        tags = [str(t).strip() for t in tags_val if isinstance(t, str) and str(t).strip()]
    elif isinstance(tags_val, str) and tags_val.strip():
        tags = [tags_val.strip()]
    if not tags and fallback_runtime:
        tags = [fallback_runtime]
    return tags


def _build_skill_like_plugin_yaml_for_publish(root: Path, plugin_version: str) -> dict[str, Any]:
    """Build a synthetic plugin.yaml for skill/swarmskill publish from SKILL.md + CLI version."""
    inferred_runtime, _ = _infer_skill_like_runtime(root)
    if inferred_runtime not in SKILL_LIKE_RUNTIME_TYPES:
        raise ValueError("cannot synthesize plugin.yaml: path is not skill/swarmskill layout")

    skill_ws = _find_skill_workspace(root)
    if skill_ws is None:
        raise ValueError(
            "skill layout requires root/SKILL.md (flat) or exactly one <slug>/SKILL.md under the plugin root"
        )
    skill_dir, slug = skill_ws
    fm, fm_err = _parse_skill_frontmatter(skill_dir / "SKILL.md")
    if fm_err is not None or fm is None:
        raise ValueError(fm_err or "cannot read SKILL.md frontmatter")

    desc_raw = fm.get("description")
    description = desc_raw.strip() if isinstance(desc_raw, str) and desc_raw.strip() else slug
    display_raw = fm.get("display_name")
    display_name = display_raw.strip() if isinstance(display_raw, str) and display_raw.strip() else slug

    author_raw = fm.get("author")
    author = author_raw.strip() if isinstance(author_raw, str) and author_raw.strip() else "unknown"

    tags = _read_skill_frontmatter_tags(skill_dir, fallback_runtime=inferred_runtime)

    return {
        "name": slug,
        "version": plugin_version,
        "display_name": display_name,
        "description": description,
        "runtime": {"type": "skill"},
        "metadata": {
            "author": author,
            "tags": tags,
        },
    }


def _init_plugin_skill(plugin_name: str, plugin_root: Path) -> Path:
    plugin_root.mkdir(parents=True, exist_ok=True)
    for sub in ("scripts", "references", "assets"):
        (plugin_root / sub).mkdir(parents=True, exist_ok=True)

    (plugin_root / "SKILL.md").write_text(
        _render_skill_md(
            plugin_name,
            description="TODO: describe this skill for models and users",
            include_version=False,
        ),
        encoding="utf-8",
    )

    return plugin_root


def _init_plugin_swarmskill(plugin_name: str, plugin_root: Path) -> Path:
    plugin_root.mkdir(parents=True, exist_ok=True)
    for sub in ("scripts", "references", "assets"):
        d = plugin_root / sub
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    scaffold_teamskill_skill_directory(
        plugin_root,
        plugin_name,
        list(DEFAULT_TEAMSKILL_ROLES),
        kind="swarm-skill",
    )

    return plugin_root


def _init_agent_asset(plugin_name: str, plugin_root: Path, plugin_type: str) -> Path:
    """Scaffold a wrapped Agent asset: outer plugin.yaml plus inner manifest.json."""
    plugin_root.mkdir(parents=True, exist_ok=True)
    inner = plugin_root / plugin_name
    inner.mkdir(parents=True, exist_ok=True)
    package_type = _AGENT_MANIFEST_PACKAGE_TYPE[plugin_type]
    manifest: dict[str, Any] = {
        "version": "0.0.1",
        "package_type": package_type,
        "description": "TODO: describe this asset",
    }
    if plugin_type == "agent-plugin":
        manifest["id"] = plugin_name
        manifest["name"] = plugin_name
        manifest["tools"] = [{"file": "tools/example.py", "class": "ExampleTool"}]
        tools_dir = inner / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        (tools_dir / "example.py").write_text(
            "class ExampleTool:\n    \"\"\"TODO: implement the tool.\"\"\"\n",
            encoding="utf-8",
        )
    elif plugin_type == "agent-mcp":
        manifest["id"] = plugin_name
        manifest["name"] = plugin_name
        manifest["integration"] = {"type": "remote-mcp", "file": "mcp.json"}
        (inner / "mcp.json").write_text(
            json.dumps(
                {"mcpServers": {"example": {"url": "https://example.com/mcp"}}},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    elif plugin_type == "agent-template":
        manifest["name"] = plugin_name
        manifest["persona"] = {"dir": "persona"}
        persona_dir = inner / "persona"
        persona_dir.mkdir(parents=True, exist_ok=True)
        (persona_dir / "persona.md").write_text("# Persona\n\nTODO: describe this expert.\n", encoding="utf-8")
    else:
        manifest["name"] = plugin_name
        manifest["instruction"] = "TODO: how members collaborate"
        manifest["agents"] = ["leader", "analyst"]
    (inner / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (inner / "README.md").write_text(f"# {plugin_name}\n\nTODO: describe this asset.\n", encoding="utf-8")
    (plugin_root / "README.md").write_text(f"# {plugin_name}\n\nTODO: describe this asset.\n", encoding="utf-8")
    (plugin_root / "plugin.yaml").write_text(
        yaml.safe_dump(
            _default_plugin_yaml(plugin_name, plugin_type),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return plugin_root


def _validate_mcp_json_shape(path: Path, integration_type: str, errors: list[str]) -> None:
    """Match marketplace agent-mcp: mcpServers object, first server matches integration.type."""
    data = _load_json(path, errors)
    if data is None:
        return
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        errors.append("mcp.json mcpServers must be a non-empty object")
        return
    first: dict[str, Any] | None = None
    for server_name, config in servers.items():
        if not isinstance(server_name, str) or not server_name.strip() or not isinstance(config, dict):
            errors.append("mcp.json mcpServers entries must use a non-empty name and an object config")
            return
        if first is None:
            first = config
    if first is None:
        errors.append("mcp.json mcpServers must be a non-empty object")
        return
    command = first.get("command")
    url = first.get("url")
    has_command = isinstance(command, str) and bool(command.strip())
    has_url = isinstance(url, str) and bool(url.strip())
    if has_command:
        inferred = "stdio-mcp"
    elif has_url:
        inferred = "remote-mcp"
    else:
        errors.append("mcp.json first server must include command or url")
        return
    if inferred != integration_type:
        errors.append(
            f"manifest.integration.type is {integration_type!r}, but mcp.json content matches {inferred!r}"
        )


def _validate_agent_asset_directory(
    root: Path,
    plugin_data: dict[str, Any] | None,
    runtime_type: str,
    errors: list[str],
) -> None:
    if not isinstance(plugin_data, dict):
        errors.append("agent asset requires plugin.yaml")
        return
    name = plugin_data.get("name")
    if not isinstance(name, str) or not name.strip():
        return
    version = plugin_data.get("version")
    manifest_rel = f"{name}/manifest.json"
    manifest_path = root / name / "manifest.json"
    if not manifest_path.is_file():
        errors.append(f"missing required entry: {manifest_rel}")
        return
    manifest = _load_json(manifest_path, errors)
    if manifest is None:
        return
    expected_package_type = _AGENT_MANIFEST_PACKAGE_TYPE[runtime_type]
    package_type = manifest.get("package_type")
    if package_type != expected_package_type:
        errors.append(
            f"manifest.package_type must be {expected_package_type!r}, got {package_type!r}"
        )
    if isinstance(version, str) and manifest.get("version") != version:
        errors.append(
            f"plugin.yaml version {version!r} must equal manifest.version {manifest.get('version')!r}"
        )
    if runtime_type in {"agent-plugin", "agent-mcp"}:
        if manifest.get("id") != name:
            errors.append(f"manifest.id {manifest.get('id')!r} must equal plugin.yaml name {name!r}")
    elif manifest.get("name") != name:
        errors.append(f"manifest.name {manifest.get('name')!r} must equal plugin.yaml name {name!r}")
    if runtime_type == "agent-plugin":
        for field in _AGENT_PLUGIN_FORBIDDEN_FIELDS:
            if field in manifest:
                errors.append(f"agent-plugin manifest must not declare {field}")
    if runtime_type == "agent-mcp":
        integration = manifest.get("integration")
        if not isinstance(integration, dict) or not str(integration.get("type") or "").strip():
            errors.append("manifest.integration.type is required")
        else:
            integration_type = str(integration.get("type") or "").strip()
            if integration_type not in _AGENT_MCP_INTEGRATION_TYPES:
                errors.append(
                    "manifest.integration.type must be stdio-mcp, remote-mcp, cli, or skill-only"
                )
            file_rel = integration.get("file")
            if integration_type != "skill-only":
                if not isinstance(file_rel, str) or not file_rel.strip():
                    errors.append("manifest.integration.file is required")
                else:
                    inner = (root / name).resolve()
                    target = (inner / file_rel).resolve()
                    try:
                        target.relative_to(inner)
                    except ValueError:
                        errors.append("manifest.integration.file must stay inside the asset directory")
                    else:
                        if not target.is_file():
                            errors.append(f"missing required entry: {name}/{file_rel.strip()}")
                        elif integration_type in {"stdio-mcp", "remote-mcp"}:
                            _validate_mcp_json_shape(target, integration_type, errors)
        description = manifest.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append("manifest.description is required")
    if runtime_type in {"agent-template", "agent-group"}:
        description = manifest.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append("manifest.description is required")
    if runtime_type == "agent-group":
        agents = manifest.get("agents")
        if agents is not None:
            if not isinstance(agents, list):
                errors.append("manifest.agents must be an array")
            else:
                for index, item in enumerate(agents):
                    bad_name = not isinstance(item, str) or not item.strip()
                    has_sep = isinstance(item, str) and ("/" in item or "\\" in item)
                    if bad_name or has_sep:
                        errors.append(f"manifest.agents[{index}] must be a name without path separators")


def plugin_init(plugin_name: str, base_path: Path, force: bool = False, plugin_type: str = "skill") -> Path:
    if plugin_type not in SUPPORTED_PLUGIN_TYPES:
        supported = ", ".join(sorted(SUPPORTED_PLUGIN_TYPES))
        raise ValueError(f"plugin type must be one of: {supported}")
    if plugin_type in SKILL_LIKE_RUNTIME_TYPES:
        err = _validate_skill_slug(plugin_name, field="skill name")
        if err:
            raise ValueError(err)
    elif not NAME_PATTERN.match(plugin_name):
        raise ValueError("plugin name must match ^[a-z][a-z0-9-]*$")

    plugin_root = (base_path / plugin_name).resolve()
    if plugin_root.exists() and any(plugin_root.iterdir()) and not force:
        raise FileExistsError(f"{plugin_root} already exists and is not empty. Use --force to continue.")

    if plugin_type == "skill":
        return _init_plugin_skill(plugin_name, plugin_root)
    if plugin_type == "swarmskill":
        return _init_plugin_swarmskill(plugin_name, plugin_root)
    if plugin_type in AGENT_ASSET_RUNTIME_TYPES:
        return _init_agent_asset(plugin_name, plugin_root, plugin_type)
    supported = ", ".join(sorted(SUPPORTED_PLUGIN_TYPES))
    raise ValueError(f"plugin type must be one of: {supported}")


def plugin_validate(plugin_path: Path) -> ValidationResult:
    """Validate a skill, swarmskill, or wrapped Agent asset directory."""
    errors: list[str] = []
    warnings: list[str] = []
    root = plugin_path.resolve()
    if not root.exists():
        return ValidationResult(False, [f"plugin path not found: {root}"], warnings, runtime_type=None)

    plugin_yaml_path = root / "plugin.yaml"
    plugin_data: dict[str, Any] | None = None
    if plugin_yaml_path.exists():
        plugin_data = _load_yaml(plugin_yaml_path, errors)

    runtime_type = _runtime_type(plugin_data)
    if runtime_type is None:
        inferred_rt, _skill_sub_hint = _infer_skill_like_runtime(root)
        if inferred_rt is not None:
            runtime_type = inferred_rt

    skill_like_diag = _diagnose_skill_like_layout(root)
    if skill_like_diag:
        errors.extend(skill_like_diag)

    required_entries: list[str] = []
    if runtime_type in SKILL_LIKE_RUNTIME_TYPES:
        skill_ws = _find_skill_workspace(root)
        if skill_ws is None and not skill_like_diag:
            errors.append(
                "skill/swarmskill: expected either root/SKILL.md (flat bundle) or exactly one "
                "child directory containing SKILL.md"
            )
    elif runtime_type in AGENT_ASSET_RUNTIME_TYPES:
        if not plugin_yaml_path.exists():
            errors.append("missing required entry: plugin.yaml")
        _validate_agent_asset_directory(root, plugin_data, runtime_type, errors)
    elif runtime_type in LEGACY_RUNTIME_TYPES:
        errors.append(
            f"runtime.type {runtime_type!r} is not an Agent asset type; "
            "use skill, swarmskill, agent-plugin, agent-mcp, agent-template, or agent-group"
        )
    elif not skill_like_diag:
        required_entries.extend(("plugin.yaml", "README.md"))

    for rel in required_entries:
        if not (root / rel).exists():
            errors.append(f"missing required entry: {rel}")

    if plugin_data is not None:
        _validate_plugin_yaml(plugin_data, errors)

    if runtime_type in SKILL_LIKE_RUNTIME_TYPES:
        skill_ws = _find_skill_workspace(root)
        if skill_ws is None:
            if not skill_like_diag:
                errors.append(
                    "skill/swarmskill: expected either root/SKILL.md (flat bundle) or exactly one "
                    "non-hidden child directory containing SKILL.md under the plugin root "
                    "(or a version-style folder whose name is not a skill slug, "
                    "with SKILL.md and matching name: in frontmatter)"
                )
        else:
            skill_dir, slug = skill_ws
            if plugin_data is not None:
                yaml_name = plugin_data.get("name")
                if isinstance(yaml_name, str) and slug != yaml_name:
                    errors.append(
                        f"skill slug {slug!r} (from layout / SKILL.md name) must equal plugin.yaml name {yaml_name!r}"
                    )
                if isinstance(yaml_name, str):
                    yaml_skill_err = _validate_skill_slug(yaml_name, field="plugin.yaml name")
                    if yaml_skill_err:
                        errors.append(yaml_skill_err)
            else:
                slug_err = _validate_skill_slug(slug, field="skill name")
                if slug_err:
                    errors.append(slug_err)

            yaml_for_fm = (
                plugin_data.get("name")
                if plugin_data is not None and isinstance(plugin_data.get("name"), str)
                else slug
            )
            fm, fm_err = _parse_skill_frontmatter(skill_dir / "SKILL.md")
            if fm_err:
                errors.append(fm_err)
            elif fm is not None and isinstance(yaml_for_fm, str):
                errors.extend(_validate_skill_frontmatter_fields(fm, slug, yaml_for_fm))

            if runtime_type == "swarmskill":
                ts_errors, ts_warnings = teamskill_validate_directory(skill_dir)
                errors.extend(ts_errors)
                warnings.extend(ts_warnings)

    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, runtime_type=runtime_type)


def plugin_pack(plugin_path: Path, output_dir: Path | None = None) -> Path:
    """Zip a plugin directory; runs ``validate`` first and raises if validation fails."""
    root = plugin_path.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"plugin path not found or not a directory: {root}")

    result = plugin_validate(root)
    for w in result.warnings:
        logger.warning("%s", w)
    if result.errors:
        raise ValueError("plugin validation failed: " + "; ".join(result.errors))

    plugin_yaml_path = root / "plugin.yaml"
    plugin_data: dict[str, Any] | None = None
    if plugin_yaml_path.is_file():
        plugin_data = yaml.safe_load(plugin_yaml_path.read_text(encoding="utf-8"))
        if not isinstance(plugin_data, dict):
            raise ValueError("plugin.yaml must be an object")

    runtime_type = _runtime_type(plugin_data) if plugin_data is not None else None
    if runtime_type is None:
        inferred_rt, _ = _infer_skill_like_runtime(root)
        if inferred_rt is not None:
            runtime_type = inferred_rt

    if runtime_type in SKILL_LIKE_RUNTIME_TYPES and plugin_data is None:
        skill_ws = _find_skill_workspace(root)
        if skill_ws is None:
            raise ValueError(
                "skill layout requires root/SKILL.md (flat) or exactly one <slug>/SKILL.md under the plugin root"
            )
        skill_dir, slug = skill_ws
        fm, fm_err = _parse_skill_frontmatter(skill_dir / "SKILL.md")
        if fm_err or fm is None:
            raise ValueError(fm_err or "cannot read SKILL.md frontmatter")
        name_val = fm.get("name")
        if not isinstance(name_val, str) or name_val.strip() != slug:
            raise ValueError("SKILL.md frontmatter name must match the resolved skill slug")
        name = slug
        version = None
    else:
        if plugin_data is None:
            raise ValueError("plugin.yaml not found")
        name = plugin_data.get("name")
        version = plugin_data.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise ValueError("plugin.yaml name and version required")
        if runtime_type is None:
            raise ValueError("plugin.yaml runtime.type is missing or not supported")

    out = (output_dir or (root / "out")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    zip_name = f"{name}-{version}.zip" if version else f"{name}.zip"
    zip_path = out / zip_name
    prefix = f"{name}-{version}" if version else name

    if runtime_type in SKILL_LIKE_RUNTIME_TYPES:
        _pack_plugin_skill(root, name, prefix, zip_path)
    else:
        _pack_plugin_directory(root, prefix, zip_path)

    digest = sha256_file_hex(zip_path)
    sha256_path = zip_path.with_suffix(zip_path.suffix + ".sha256")
    sha256_path.write_text(f"{digest}  {zip_name}\n", encoding="utf-8")
    return zip_path


def _pack_plugin_skill(root: Path, name: str, prefix: str, zip_path: Path) -> None:
    """Pack skill/swarmskill: skill tree under ``<name>/`` only (no plugin.yaml / icon.png in the zip)."""
    ws = _find_skill_workspace(root)
    if ws is not None and ws[1] == name:
        skill_dir = ws[0]
    elif (root / name / "SKILL.md").is_file():
        skill_dir = root / name
    else:
        raise ValueError(
            f"skill pack: expected {name}/SKILL.md or plugin root with flat SKILL.md (name: {name} in frontmatter)"
        )
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise ValueError(f"skill plugin requires SKILL.md under {skill_dir}")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        readme = root / "README.md"
        if readme.is_file():
            zf.write(readme, f"{prefix}/README.md".replace("\\", "/"))
        arc_prefix = f"{prefix}/{name}".replace("\\", "/")
        n_files = plugin_zip_write_directory_tree(zf, skill_dir, arcname_prefix=arc_prefix)
        if n_files == 0:
            raise ValueError(f"no files packed under skill directory: {skill_dir}")


def _pack_plugin_directory(root: Path, prefix: str, zip_path: Path) -> None:
    """Pack a wrapped Agent asset. The market outer layer is plugin.yaml, optional icon.png, and one inner directory."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        plugin_zip_write_directory_tree(
            zf,
            root,
            arcname_prefix=prefix,
            skip_relative=frozenset({"README.md"}),
        )


def plugin_zip_write_directory_tree(
    zf: zipfile.ZipFile,
    root: Path,
    *,
    arcname_prefix: str = "",
    skip_relative: frozenset[str] = frozenset(),
) -> int:
    """Append files under ``root`` to ``zf``; return number of files written."""
    base = root.resolve()
    prefix = arcname_prefix.strip().replace("\\", "/").strip("/")
    n = 0
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(base)
        except ValueError:
            continue
        rel = path.relative_to(base)
        parts = rel.parts
        if any(p in PACK_IGNORE_DIR_NAMES or p.endswith(".egg-info") for p in parts):
            continue
        if path.suffix in PACK_IGNORE_SUFFIXES:
            continue
        if path.name == ".DS_Store":
            continue
        rel_posix = rel.as_posix()
        if rel_posix in skip_relative:
            continue
        arcname = f"{prefix}/{rel_posix}" if prefix else rel_posix
        zf.write(path, arcname)
        n += 1
    return n


def _zip_write_all_files(zf: zipfile.ZipFile, root: Path, *, exclude: set[Path] | None = None) -> int:
    """Write all files under ``root`` preserving relative paths (no ignore filtering)."""
    base = root.resolve()
    excluded = {p.resolve() for p in (exclude or set())}
    n = 0
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.resolve() in excluded:
            continue
        rel = path.relative_to(base).as_posix()
        zf.write(path, rel)
        n += 1
    return n


def _prepare_publish_zip_for_upload(
    zip_path: Path,
    *,
    workspace: Path,
    plugin_version: str | None,
    expect_skill_like: bool = False,
) -> Path:
    """Normalize publish zip: inject skill-like plugin.yaml when missing."""
    stage = (workspace / "publish-zip-stage").resolve()
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        _safe_extractall(zf, stage)

    plugin_root = _find_plugin_root_in_extracted(stage)
    validation = plugin_validate(plugin_root)
    runtime_type = validation.runtime_type
    if expect_skill_like:
        skill_like_errors = [
            err
            for err in validation.errors
            if "missing required entry: plugin.yaml" not in err and "missing required entry: README.md" not in err
        ]
        if skill_like_errors:
            raise PublishError(400, "plugin validation failed: " + "; ".join(skill_like_errors))
        if runtime_type not in SKILL_LIKE_RUNTIME_TYPES:
            raise PublishError(
                400,
                f"expected skill/swarmskill package, got runtime.type={runtime_type or 'unknown'}",
            )
    else:
        for w in validation.warnings:
            logger.warning("%s", w)
        if validation.errors:
            raise PublishError(400, "plugin validation failed: " + "; ".join(validation.errors))

    plugin_yaml = plugin_root / "plugin.yaml"
    plugin_yaml_data: dict[str, Any] | None = None
    if plugin_yaml.is_file():
        loaded = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            plugin_yaml_data = loaded

    if runtime_type not in SKILL_LIKE_RUNTIME_TYPES:
        return zip_path

    # Skill-like bundles are normalized to canonical layout for publish:
    # <prefix>/plugin.yaml + <prefix>/<name>/SKILL.md...
    if plugin_yaml_data is None:
        if not plugin_version:
            raise PublishError(400, "skill/swarmskill publish requires --version")
        plugin_yaml_data = _build_skill_like_plugin_yaml_for_publish(plugin_root, plugin_version)

    inferred_runtime, skill_dir = _infer_skill_like_runtime(plugin_root)
    if inferred_runtime not in SKILL_LIKE_RUNTIME_TYPES or skill_dir is None:
        raise PublishError(400, "cannot infer skill/swarmskill type from SKILL.md")

    declared_runtime = _runtime_type(plugin_yaml_data)
    if declared_runtime is not None and declared_runtime not in SKILL_LIKE_RUNTIME_TYPES:
        raise PublishError(
            400,
            f"expected skill/swarmskill plugin.yaml runtime.type, got {declared_runtime or 'unknown'}",
        )
    if inferred_runtime == 'swarmskill':
        ts_errors, _ = teamskill_validate_directory(skill_dir)
        if ts_errors:
            raise PublishError(400, '; '.join(ts_errors))

    plugin_yaml_data["runtime"] = {"type": "skill"}

    if expect_skill_like:
        for w in validation.warnings:
            logger.warning("%s", w)
        kind_hint = "team-skill" if inferred_runtime == 'swarmskill' else "skill"
        logger.info("publish normalize: inferred type from SKILL.md = %s", kind_hint)

    prefix = str(plugin_yaml_data.get("name") or skill_dir.name).strip() or skill_dir.name

    repacked = workspace / "publish-ready.zip"
    with zipfile.ZipFile(repacked, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"{prefix}/plugin.yaml",
            yaml.safe_dump(plugin_yaml_data, sort_keys=False, allow_unicode=True),
        )
        readme = plugin_root / "README.md"
        if readme.is_file():
            zf.write(readme, f"{prefix}/README.md".replace("\\", "/"))
        files_written = plugin_zip_write_directory_tree(
            zf,
            skill_dir,
            arcname_prefix=f"{prefix}/{skill_dir.name}".replace("\\", "/"),
        )
    if files_written == 0:
        raise PublishError(400, f"zip file contains no skill files after normalization: {zip_path}")
    return repacked


def plugin_pack_skill_bundle(src_dir: Path, dest_zip: Path) -> None:
    """Build collection-bundle zip (zip root = ``src_dir``) for ``skill-import`` CLI upload."""
    src = src_dir.resolve()
    if not src.is_dir():
        raise ValueError(f"not a directory: {src}")
    dest_zip = dest_zip.resolve()
    dest_zip.parent.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            files_written = plugin_zip_write_directory_tree(zf, src, arcname_prefix="")
    except OSError as e:
        dest_zip.unlink(missing_ok=True)
        raise ValueError(f"failed to build bundle zip: {e}") from e

    if files_written == 0:
        dest_zip.unlink(missing_ok=True)
        raise ValueError(
            "no files packed (directory empty or only ignored paths such as .git / __pycache__)"
        )

    raw_size = dest_zip.stat().st_size
    if raw_size > SKILL_IMPORT_BUNDLE_MAX_BYTES:
        dest_zip.unlink(missing_ok=True)
        raise ValueError(
            f"packed bundle size {raw_size} bytes exceeds limit {SKILL_IMPORT_BUNDLE_MAX_BYTES} "
            "(same as server MAX_FILE_SIZE / 512MB)"
        )


def _safe_extractall(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract a zip while rejecting path traversal, absolute paths, and symlinks."""
    dest = dest.resolve()
    for member in zf.infolist():
        name = member.filename
        if not isinstance(name, str) or not name:
            raise ValueError("unsafe zip entry: empty filename")
        if "\x00" in name:
            raise ValueError(f"unsafe zip entry: contains NUL byte: {name!r}")

        # Normalize separators to POSIX style for consistent checks.
        norm = name.replace("\\", "/")

        # Reject absolute paths and Windows drive paths.
        if norm.startswith("/") or norm.startswith("\\") or re.match(r"^[A-Za-z]:", norm):
            raise ValueError(f"unsafe zip entry: absolute/drive path: {name!r}")

        # Reject path traversal before filesystem resolution.
        norm2 = posixpath.normpath(norm)
        if norm2 in (".", "..") or norm2.startswith("../") or "/../" in f"/{norm2}/":
            raise ValueError(f"unsafe zip entry: path traversal: {name!r}")

        # Reject symlinks in archive (defense-in-depth).
        is_symlink = ((member.external_attr >> 16) & stat.S_IFMT(stat.S_IFLNK)) == stat.S_IFLNK
        if is_symlink:
            raise ValueError(f"unsafe zip entry: symlink not allowed: {name!r}")

        out = (dest / norm2).resolve()
        try:
            out.relative_to(dest)
        except ValueError:
            raise ValueError(f"unsafe zip entry: {name!r}") from None
    zf.extractall(dest)


def _find_plugin_root_in_extracted(extract_root: Path) -> Path:
    """Resolve staging root: flat ``SKILL.md``, nested skill folder, or ``plugin.yaml`` parent."""
    if _find_skill_workspace(extract_root) is not None:
        return extract_root
    tops = [p for p in extract_root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    if len(tops) == 1 and _find_skill_workspace(tops[0]) is not None:
        return tops[0]

    found = list(extract_root.rglob("plugin.yaml"))
    if len(found) > 1:
        raise ValueError(
            f"expected at most one plugin.yaml in archive, found {len(found)} under {extract_root}"
        )
    if len(found) == 1:
        return found[0].parent

    if len(tops) == 1:
        raise ValueError(
            "single top-level directory is not a skill bundle "
            "(need root/SKILL.md flat or <slug>/SKILL.md) and archive has no plugin.yaml"
        )
    raise ValueError(
        "cannot resolve plugin root: need a skill bundle (flat SKILL.md, or one top dir with <slug>/SKILL.md) "
        "or a single plugin.yaml"
    )


def _resolve_runtime_for_install(plugin_root: Path) -> tuple[str, str, Path | None]:
    """Return ``(runtime_type, name, skill_src_dir)``; ``skill_src_dir`` is set only for skill/swarmskill."""
    plugin_yaml = plugin_root / "plugin.yaml"
    if plugin_yaml.is_file():
        data = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("plugin.yaml must be an object")
        runtime_type = _runtime_type(data)
        if runtime_type is None:
            raise ValueError("plugin.yaml runtime.type is missing or not supported")
        yaml_name = data.get("name")
        if not isinstance(yaml_name, str) or not yaml_name.strip():
            raise ValueError("plugin.yaml name must be a non-empty string")
        yaml_name = yaml_name.strip()
        if runtime_type in SKILL_LIKE_RUNTIME_TYPES:
            candidate = plugin_root / yaml_name
            if candidate.is_dir() and (candidate / "SKILL.md").is_file():
                return runtime_type, yaml_name, candidate
            ws = _find_skill_workspace(plugin_root)
            if ws is None:
                raise ValueError(
                    "skill/swarmskill install with plugin.yaml requires a sibling skill directory containing SKILL.md"
                )
            skill_dir, slug = ws
            if slug != yaml_name:
                raise ValueError(
                    f"skill slug {slug!r} (from SKILL.md) must equal plugin.yaml name {yaml_name!r}"
                )
            return runtime_type, yaml_name, skill_dir
        return runtime_type, yaml_name, None

    ws = _find_skill_workspace(plugin_root)
    if ws is not None:
        skill_dir, slug = ws
        fm, fm_err = _parse_skill_frontmatter(skill_dir / "SKILL.md")
        if fm_err or fm is None:
            raise ValueError(fm_err or "cannot read SKILL.md")
        name_val = fm.get("name")
        if not isinstance(name_val, str) or name_val.strip() != slug:
            raise ValueError("SKILL.md frontmatter name must match the resolved skill slug")
        rt = "swarmskill" if str(fm.get("kind") or "").strip().lower() in {"team-skill", "swarm-skill"} else "skill"
        return rt, slug, skill_dir

    raise ValueError(
        "not a skill bundle (flat SKILL.md or <slug>/SKILL.md) and plugin.yaml is missing"
    )


def _install_skill_from_staging(
    skill_src: Path,
    dest_slug: str,
    dest_parent: Path,
    *,
    force: bool,
) -> Path:
    if not skill_src.is_dir() or not (skill_src / "SKILL.md").is_file():
        raise ValueError(f"skill bundle must be a directory containing SKILL.md: {skill_src}")
    dest = dest_parent / dest_slug
    if dest.exists():
        if not force:
            raise FileExistsError(
                f"destination already exists: {dest} (use force=True or --force to overwrite)"
            )
        shutil.rmtree(dest)
    shutil.copytree(skill_src, dest)
    return dest


def _copy_bundle_to_output(
    plugin_root: Path,
    dest_parent: Path,
    *,
    dest_name: str,
    force: bool,
) -> Path:
    """Copy plugin root to ``dest_parent / dest_name`` (from plugin.yaml, not zip root folder name)."""
    safe_name = (dest_name or "").strip()
    is_empty_name = not safe_name
    is_reserved_name = safe_name in {".", ".."}
    has_path_separator = "/" in safe_name or "\\" in safe_name
    if is_empty_name or is_reserved_name or has_path_separator:
        raise ValueError(f"invalid install destination name: {dest_name!r}")
    bundle_dest = dest_parent / safe_name
    if bundle_dest.exists():
        if not force:
            raise FileExistsError(
                f"destination already exists: {bundle_dest} (use force=True or --force to overwrite)"
            )
        shutil.rmtree(bundle_dest)
    shutil.copytree(plugin_root, bundle_dest)
    return bundle_dest


def plugin_install(
    zip_path: Path,
    *,
    extract_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Extract zip, validate, then install by ``runtime.type``.

    Skill layout copies the slug directory. Wrapped Agent assets copy the bundle.
    """
    zpath = zip_path.resolve()
    if not zpath.is_file():
        raise ValueError(f"zip not found: {zpath}")

    dest_parent = (extract_dir if extract_dir is not None else Path.cwd()).resolve()
    dest_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="openjiuwen_install_") as tmp:
        extract_root = Path(tmp)
        with zipfile.ZipFile(zpath, "r") as zf:
            _safe_extractall(zf, extract_root)
        plugin_root = _find_plugin_root_in_extracted(extract_root)

        result = plugin_validate(plugin_root)
        for w in result.warnings:
            logger.warning("%s", w)
        if result.errors:
            raise ValueError("plugin validation failed: " + "; ".join(result.errors))

        runtime_type, yaml_name, skill_src = _resolve_runtime_for_install(plugin_root)

        if runtime_type in SKILL_LIKE_RUNTIME_TYPES:
            if skill_src is None:
                raise ValueError("internal error: skill install missing resolved skill source directory")
            return _install_skill_from_staging(skill_src, yaml_name, dest_parent, force=force)

        bundle_dest = _copy_bundle_to_output(
            plugin_root,
            dest_parent,
            dest_name=yaml_name,
            force=force,
        )

        if runtime_type in AGENT_ASSET_RUNTIME_TYPES:
            logger.info("installed %s at %s", runtime_type, bundle_dest)
        else:
            raise ValueError(f"unexpected runtime type after bundle copy: {runtime_type}")

        return bundle_dest


def plugin_publish(
    market_url: str,
    user_token: str | None,
    system_token: str | None,
    publish_input: PublishPluginInput,
) -> PluginPublishResult:
    """Publish: optionally ``plugin_pack`` then upload, or upload an existing zip."""
    with tempfile.TemporaryDirectory(prefix="openjiuwen_publish_") as tmp:
        out_dir = Path(tmp)
        if publish_input.zip_path is not None:
            z = publish_input.zip_path
            if not z.is_file():
                raise PublishError(400, f"zip file not found: {z}")
        else:
            root = publish_input.plugin_path
            if root is None:
                raise PublishError(400, "either plugin_path or zip_path must be provided")
            if not root.exists():
                raise PublishError(400, f"plugin path not found: {root}")
            if not root.is_dir():
                raise PublishError(400, f"plugin path must be a directory: {root}")
            if publish_input.expect_skill_like:
                skill_like_errors = _diagnose_skill_like_publish_root(root)
                if skill_like_errors:
                    raise PublishError(400, "plugin validation failed: " + "; ".join(skill_like_errors))
            z = plugin_pack(root, out_dir)

        z = _prepare_publish_zip_for_upload(
            z,
            workspace=out_dir,
            plugin_version=publish_input.plugin_version,
            expect_skill_like=publish_input.expect_skill_like,
        )

        checksum_sha256 = sha256_file_hex(z)
        req = PublishRequest(
            zip_path=z,
            checksum_sha256=checksum_sha256,
            plugin_id=publish_input.plugin_id,
            plugin_version=publish_input.plugin_version,
            version_desc=publish_input.version_desc,
            force=publish_input.force,
        )
        return plugin_upload(
            market_url,
            user_token,
            system_token,
            req,
        )


def _load_yaml(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"failed to parse YAML {path}: {exc}")
        return None
    if not isinstance(loaded, dict):
        errors.append(f"YAML root must be object: {path}")
        return None
    return loaded


def _load_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"failed to parse JSON {path}: {exc}")
        return None
    if not isinstance(loaded, dict):
        errors.append(f"JSON root must be object: {path}")
        return None
    return loaded


def _runtime_type(plugin_data: dict[str, Any] | None) -> str | None:
    """Parse and normalize ``runtime.type`` from ``plugin.yaml``."""
    if not isinstance(plugin_data, dict):
        return None
    runtime = plugin_data.get("runtime")
    if not isinstance(runtime, dict):
        return None
    raw = runtime.get("type")
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    rt = raw.strip().lower()
    if not rt:
        return None
    canonical_map = {
        "tools": "tools",
        "mcp-stdio": "mcp-stdio",
        "restful-api": "restful-api",
        "skill": "skill",
        "swarmskill": "swarmskill",
        "teamskills": "swarmskill",
        "agent-plugin": "agent-plugin",
        "agent-mcp": "agent-mcp",
        "agent-template": "agent-template",
        "agent-group": "agent-group",
    }
    return canonical_map.get(rt)


def _validate_plugin_yaml(
    plugin_data: dict[str, Any],
    errors: list[str],
) -> None:
    runtime_type = _runtime_type(plugin_data)
    name = plugin_data.get("name")
    if runtime_type in SKILL_LIKE_RUNTIME_TYPES:
        if not isinstance(name, str) or not name.strip():
            errors.append("plugin.yaml name is required")
        else:
            skill_slug_err = _validate_skill_slug(name, field="plugin.yaml name")
            if skill_slug_err:
                errors.append(skill_slug_err)
    elif not isinstance(name, str) or not NAME_PATTERN.match(name):
        errors.append("plugin.yaml name must match ^[a-z][a-z0-9-]*$")

    version = plugin_data.get("version")
    if not isinstance(version, str) or not is_valid_marketplace_version(version):
        errors.append(
            "plugin.yaml version must be x.y.z (e.g. 1.2.3) or a git commit (7 lowercase hex digits)"
        )

    display_name = plugin_data.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        errors.append("plugin.yaml display_name must be non-empty string")

    description = plugin_data.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append("plugin.yaml description must be non-empty string")

    runtime = plugin_data.get("runtime")
    if not isinstance(runtime, dict):
        errors.append("plugin.yaml runtime must be object")
    elif runtime_type is None:
        supported = ", ".join(sorted(SUPPORTED_PLUGIN_TYPES))
        errors.append(f"runtime.type must be one of: {supported}")

    metadata = plugin_data.get("metadata")
    if not isinstance(metadata, dict):
        errors.append("plugin.yaml metadata must be object")
    else:
        author = metadata.get("author")
        if not isinstance(author, str) or not author.strip():
            errors.append("metadata.author must be non-empty string")
        tags = metadata.get("tags")
        if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
            errors.append("metadata.tags must be array of non-empty strings")


def _default_plugin_yaml(plugin_name: str, plugin_type: str) -> dict[str, Any]:
    return {
        "name": plugin_name,
        "version": "0.0.1",
        "display_name": plugin_name.replace("-", " ").title(),
        "description": "TODO: describe your plugin",
        "runtime": {
            "type": plugin_type,
        },
        "metadata": {
            "author": "TODO: your name",
            "tags": ["demo"],
        },
    }


def plugin_describe_local(plugin_path: Path) -> dict[str, Any]:
    """Read local plugin README, CHANGELOG, and name/version from ``plugin.yaml`` or skill ``SKILL.md``."""
    root = plugin_path.resolve()
    result: dict[str, Any] = {"name": None, "version": None, "readme": None, "changelog": None}
    if not root.exists() or not root.is_dir():
        return result
    plugin_yaml = root / "plugin.yaml"
    if plugin_yaml.exists():
        try:
            data = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                result["name"] = data.get("name")
                result["version"] = data.get("version")
        except Exception as exc:
            logger.warning("failed to read/parse plugin.yaml: %s", exc)
    if result["name"] is None:
        ws = _find_skill_workspace(root)
        if ws is not None:
            skill_dir, slug = ws
            fm, fm_err = _parse_skill_frontmatter(skill_dir / "SKILL.md")
            if fm_err is None and isinstance(fm, dict):
                nm = fm.get("name")
                if isinstance(nm, str) and nm.strip() == slug:
                    result["name"] = slug
                    ver = fm.get("version")
                    if isinstance(ver, str) and MARKETPLACE_VERSION_PATTERN.match(ver.strip()):
                        result["version"] = ver.strip()
    readme_path = root / "README.md"
    if readme_path.is_file():
        try:
            result["readme"] = readme_path.read_text(encoding="utf-8")
        except Exception:
            result["readme"] = "(read failed)"
    changelog_path = root / "CHANGELOG.md"
    if changelog_path.is_file():
        try:
            result["changelog"] = changelog_path.read_text(encoding="utf-8")
        except Exception:
            result["changelog"] = "(read failed)"
    return result