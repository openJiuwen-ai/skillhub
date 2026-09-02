# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validation pipeline: orchestrates all checks for a plugin zip upload.

Called by services/plugin.py::publish().
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

from plugins_market.core.errors import PublishError
from plugins_market.validation.constants import (
    RUNTIME_AGENT_PLUGIN,
    RUNTIME_AGENT_MCP,
    RUNTIME_AGENT_TEMPLATE,
    RUNTIME_MCP_STDIO,
    RUNTIME_RESTFUL_API,
    RUNTIME_SKILL,
    RUNTIME_TOOLS,
)
from plugins_market.validation.plugin_yaml import (
    PluginYamlPublicFields,
    safe_load_yaml,
    validate_plugin_yaml_bytes,
    validate_plugin_yaml_public,
)
from plugins_market.validation.zip_utils import (
    DecompressCounter,
    safe_read_zip_member,
    validate_zip_safety,
)
from plugins_market.validation.types.skill import (
    parse_skill_frontmatter,
    validate_skill_frontmatter,
    validate_skill_layout,
)
from plugins_market.validation.types.tools import (
    validate_tools_json,
    validate_tools_layout,
)
from plugins_market.validation.types.mcp_stdio import validate_mcp_stdio_layout
from plugins_market.validation.types.restful_api import (
    extract_restful_api_contract,
    validate_restful_api_layout,
)
from plugins_market.validation.types.agent_asset import (
    AgentAssetOuterRef,
    validate_agent_asset_layout,
)
from plugins_market.validation.types.agent_mcp import validate_agent_mcp_layout


def _find_plugin_yaml_path(zf: zipfile.ZipFile) -> str | None:
    """Accept only the standard layout: <top>/plugin.yaml (exactly 2 path segments)."""
    matches: list[str] = []
    for name in zf.namelist():
        normalized = name.replace("\\", "/").strip("/")
        if not normalized:
            continue
        parts = normalized.split("/")
        if len(parts) == 2 and parts[-1] == "plugin.yaml":
            matches.append(name)
    if len(matches) > 1:
        raise PublishError(
            code=400,
            error="invalid_plugin_structure",
            message="插件包只能包含一个市场外层 plugin.yaml",
        )
    return matches[0] if matches else None


def _plugin_prefix(plugin_yaml_path: str) -> str:
    """Return directory prefix of plugin.yaml, e.g. 'myplugin/' or ''."""
    path = plugin_yaml_path.replace("\\", "/").strip("/")
    if "/" not in path:
        return ""
    return path.rsplit("/", 1)[0] + "/"


def _merge_agent_market_fields(
    public: PluginYamlPublicFields,
    layout: dict[str, Any],
) -> PluginYamlPublicFields:
    """Outer plugin.yaml (form/import overrides) wins; manifest layout fills gaps."""
    yaml_tags = list(public.tags or [])
    layout_tags = layout.get("tags") or []
    return PluginYamlPublicFields(
        name=public.name,
        display_name=public.display_name or layout.get("display_name") or public.name,
        short_desc=public.short_desc or layout.get("short_desc") or "",
        publisher_name=public.publisher_name,
        tags=yaml_tags if yaml_tags else layout_tags,
        runtime_type=public.runtime_type,
    )


def extract_plugin_metadata(content: bytes) -> dict[str, Any]:
    """Full validation pipeline for a plugin zip.

    Steps:
      1. Open zip (BadZipFile guard)
      2. zip safety pre-check (validate_zip_safety)
      3. Find and read plugin.yaml with streaming counter
      4. Parse plugin.yaml with bounded SafeLoader
      5. Validate public fields
      6. Type-specific layout + content validation
      7. Read icon.png and README with streaming counter

    Returns dict suitable for services/plugin.py::publish().
    """
    # ------------------------------------------------------------------
    # Open zip (magic bytes already verified before this call)
    # ------------------------------------------------------------------
    try:
        zf_obj = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise PublishError(
            code=400,
            error="invalid_plugin_config",
            message="上传文件不是有效的 ZIP 格式，请检查文件是否损坏或格式是否正确",
        ) from exc

    with zf_obj as zf:
        # ----------------------------------------------------------------
        # Layer 1: metadata pre-check
        # ----------------------------------------------------------------
        validate_zip_safety(zf)

        # Shared streaming counter for ALL reads inside this zip
        counter = DecompressCounter()

        # ----------------------------------------------------------------
        # Locate plugin.yaml
        # ----------------------------------------------------------------
        plugin_yaml_path = _find_plugin_yaml_path(zf)
        if not plugin_yaml_path:
            raise PublishError(
                code=400,
                error="invalid_plugin_config",
                message="plugin.yaml 配置文件格式错误或缺失",
            )

        # ----------------------------------------------------------------
        # Read plugin.yaml (byte limit enforced)
        # ----------------------------------------------------------------
        yaml_raw = safe_read_zip_member(zf, plugin_yaml_path, counter)
        yaml_text = validate_plugin_yaml_bytes(yaml_raw)

        # ----------------------------------------------------------------
        # Parse & validate plugin.yaml
        # ----------------------------------------------------------------
        yaml_data = safe_load_yaml(yaml_text, context="plugin.yaml")
        if not isinstance(yaml_data, dict):
            raise PublishError(
                code=400,
                error="invalid_plugin_config",
                message="plugin.yaml 根结构必须为 mapping（字典）",
            )

        public: PluginYamlPublicFields = validate_plugin_yaml_public(yaml_data)

        version_raw = yaml_data.get("version")
        version = str(version_raw).strip() if version_raw is not None else ""

        prefix = _plugin_prefix(plugin_yaml_path)
        rt = public.runtime_type

        # ----------------------------------------------------------------
        # Type-specific layout validation + icon/readme reads
        # ----------------------------------------------------------------
        detail_desc: str = ""
        icon_bytes: bytes = b""
        extra_meta: dict[str, Any] = {}
        derived_plugin_type: str = public.runtime_type

        if rt == RUNTIME_SKILL:
            layout = validate_skill_layout(zf, prefix, public.name, counter)

            # Read SKILL.md and validate frontmatter
            skill_md_raw = safe_read_zip_member(zf, layout["skill_md_path"], counter)
            fm, _ = parse_skill_frontmatter(skill_md_raw)
            validate_skill_frontmatter(
                fm, dir_name=public.name, yaml_name=public.name
            )

            kind_val = fm.get("kind")
            kind_norm = kind_val.strip().lower() if isinstance(kind_val, str) else ""
            if kind_norm in ("team-skill", "swarm-skill"):
                derived_plugin_type = "swarmskill"

            detail_desc = skill_md_raw.decode("utf-8")

            icon_bytes = layout["icon_bytes"]

        elif rt == RUNTIME_TOOLS:
            layout = validate_tools_layout(zf, prefix, counter)

            # Read and validate tools.json
            tools_json_raw = safe_read_zip_member(zf, layout["tools_json_path"], counter)
            validate_tools_json(tools_json_raw)

            readme_raw = safe_read_zip_member(zf, layout["readme_path"], counter)
            detail_desc = readme_raw.decode("utf-8", errors="replace")
            icon_bytes = layout["icon_bytes"]

        elif rt == RUNTIME_MCP_STDIO:
            layout = validate_mcp_stdio_layout(zf, prefix, counter)
            readme_raw = safe_read_zip_member(zf, layout["readme_path"], counter)
            detail_desc = readme_raw.decode("utf-8", errors="replace")
            icon_bytes = layout["icon_bytes"]

        elif rt == RUNTIME_RESTFUL_API:
            layout = validate_restful_api_layout(zf, prefix, counter)
            extra_meta = extract_restful_api_contract(yaml_data, zf, layout, counter)
            readme_raw = safe_read_zip_member(zf, layout["readme_path"], counter)
            detail_desc = readme_raw.decode("utf-8", errors="replace")
            icon_bytes = layout["icon_bytes"]

        elif rt in (RUNTIME_AGENT_PLUGIN, RUNTIME_AGENT_TEMPLATE):
            layout = validate_agent_asset_layout(
                zf,
                AgentAssetOuterRef(
                    prefix=prefix,
                    name=public.name,
                    version=version,
                    runtime_type=rt,
                ),
                counter,
            )
            extra_meta = {"asset_type": layout["asset_type"]}
            detail_desc = layout["detail_desc"]
            icon_bytes = layout["icon_bytes"]
            public = _merge_agent_market_fields(public, layout)

        elif rt == RUNTIME_AGENT_MCP:
            layout = validate_agent_mcp_layout(
                zf,
                prefix,
                public.name,
                counter,
                outer_version=version,
            )
            extra_meta = {
                "asset_type": layout["asset_type"],
                "integration_type": layout["integration_type"],
            }
            detail_desc = layout["detail_desc"]
            icon_bytes = layout["icon_bytes"]
            public = _merge_agent_market_fields(public, layout)

        else:
            # Should not reach here; validate_plugin_yaml_public already guards this
            raise PublishError(
                code=400,
                error="invalid_plugin_config",
                message=f"不支持的 runtime.type: {rt!r}",
            )

    result = {
        "name": public.name,
        "display_name": public.display_name,
        "version": version,
        "short_desc": public.short_desc,
        "detail_desc": detail_desc,
        "tags": public.tags,
        "publisher_name": public.publisher_name,
        "plugin_type": derived_plugin_type,
        "icon_bytes": icon_bytes,
    }
    result.update(extra_meta)
    return result
