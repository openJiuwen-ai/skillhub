from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import Request, UploadFile

import plugins_market.routers.plugin as plugin_router_module
from plugins_market.core.errors import PublishError
from plugins_market.imports.skill_entries import detect_import_entry_type, entry_to_publish_zip
from plugins_market.imports.skill_import_service import (
    skill_import_from_bundle,
    skill_import_from_staging_dir,
)
from plugins_market.routers.plugin import plugin_router
from plugins_market.schemas.plugin import (
    AssetImportResponse,
    AssetImportSummary,
    PluginPublishResult,
    SkillImportBundle,
)
from plugins_market.validation import extract_plugin_metadata


def _normalize(entry: Path, **overrides: object) -> tuple[Path, str, str]:
    return entry_to_publish_zip(
        entry,
        entry_key=entry.name,
        entry_overrides=dict(overrides),
        version_fallback="0.0.1",
        default_author="system_admin",
        default_tags=[],
        allow_multi_asset=True,
    )


def _zip_text(package: Path, suffix: str) -> str:
    with zipfile.ZipFile(package) as zf:
        member = next(name for name in zf.namelist() if name.endswith(suffix))
        return zf.read(member).decode("utf-8")


def test_raw_team_skill_preserves_kind_and_roles(tmp_path: Path) -> None:
    entry = tmp_path / "team-entry"
    entry.mkdir()
    (entry / "SKILL.md").write_text(
        """---
name: team-demo
description: Team demo
kind: swarm-skill
roles:
  - id: researcher
  - id: writer
---

# Team demo
""",
        encoding="utf-8",
    )

    package, name, version = _normalize(entry)
    try:
        normalized = _zip_text(package, "/team-demo/SKILL.md")
        assert name == "team-demo"
        assert version == "0.0.1"
        assert "kind: swarm-skill" in normalized
        assert "roles:" in normalized
        assert "id: researcher" in normalized
        assert extract_plugin_metadata(package.read_bytes())["plugin_type"] == "swarmskill"
    finally:
        package.unlink(missing_ok=True)


def test_raw_agent_plugin_is_wrapped_from_native_manifest(tmp_path: Path) -> None:
    entry = tmp_path / "wellness-life-steward"
    (entry / "tools").mkdir(parents=True)
    manifest = {
        "version": "1.2.3",
        "packageType": "plugin",
        "id": "wellness-life-steward",
        "name": "Wellness fallback",
        "description": "Fallback description",
        "displayName": {"en": "Wellness", "zh": "健康生活插件"},
        "displayDescription": {"en": "Health tools", "zh": "健康工具集"},
        "tags": [{"en": "Health", "zh": "健康"}],
        "tools": [{"file": "tools/wellness.py"}],
    }
    (entry / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (entry / "README.md").write_text("# Wellness", encoding="utf-8")
    (entry / "tools" / "wellness.py").write_text("def run():\n    return True\n", encoding="utf-8")

    package, name, version = _normalize(entry)
    try:
        plugin_yaml = yaml.safe_load(_zip_text(package, "/plugin.yaml"))
        metadata = extract_plugin_metadata(package.read_bytes())
        assert (name, version) == ("wellness-life-steward", "1.2.3")
        assert plugin_yaml["runtime"]["type"] == "agent-plugin"
        assert plugin_yaml["display_name"] == "健康生活插件"
        assert plugin_yaml["description"] == "健康工具集"
        assert plugin_yaml["metadata"]["tags"] == ["健康"]
        assert metadata["asset_type"] == "agent-plugin"
    finally:
        package.unlink(missing_ok=True)


def test_agent_manifest_takes_precedence_over_misplaced_root_skill(tmp_path: Path) -> None:
    entry = tmp_path / "agent-with-root-skill"
    _write_raw_agent_plugin(entry, name="agent-with-root-skill")
    (entry / "SKILL.md").write_text(
        "---\nname: misleading-skill\ndescription: Misplaced root skill\n---\n",
        encoding="utf-8",
    )

    assert detect_import_entry_type(entry) == "agent-plugin"


def test_raw_agent_plugin_rejects_manifest_version_override(tmp_path: Path) -> None:
    entry = tmp_path / "versioned-agent"
    _write_raw_agent_plugin(entry, name="versioned-agent")

    with pytest.raises(ValueError, match="version.*仅支持|version.*不支持"):
        _normalize(entry, version="2.0.0")


def test_raw_agent_template_is_wrapped_from_agent_card(tmp_path: Path) -> None:
    entry = tmp_path / "workplace-slim-coach"
    (entry / "persona").mkdir(parents=True)
    manifest = {
        "version": "2.0.0",
        "packageType": "agent_template",
        "agentCard": {
            "id": "workplace-slim-coach",
            "name": "Kaka",
            "description": "Office wellness coach",
        },
        "displayName": {"zh": "职场轻盈教练"},
        "displayDescription": {"zh": "帮助职场人改善健康习惯"},
        "persona": {"dir": "persona"},
    }
    (entry / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (entry / "README.md").write_text("# Coach", encoding="utf-8")
    (entry / "persona" / "coach.md").write_text("# Persona", encoding="utf-8")

    package, name, version = _normalize(entry)
    try:
        plugin_yaml = yaml.safe_load(_zip_text(package, "/plugin.yaml"))
        metadata = extract_plugin_metadata(package.read_bytes())
        assert (name, version) == ("workplace-slim-coach", "2.0.0")
        assert plugin_yaml["runtime"]["type"] == "agent-template"
        assert plugin_yaml["display_name"] == "职场轻盈教练"
        assert metadata["asset_type"] == "agent-template"
    finally:
        package.unlink(missing_ok=True)


def test_raw_agent_mcp_is_wrapped_from_entry_name_and_overrides(tmp_path: Path) -> None:
    entry = tmp_path / "amap"
    entry.mkdir()
    (entry / "mcp.json").write_text(
        json.dumps({"mcpServers": {"amap-maps": {"url": "https://example.com/mcp"}}}),
        encoding="utf-8",
    )
    (entry / "README.md").write_text("# Amap", encoding="utf-8")

    package, name, version = _normalize(
        entry,
        version="3.1.4",
        display_name="高德地图",
        description="地图查询能力",
        tags=["地图"],
    )
    try:
        plugin_yaml = yaml.safe_load(_zip_text(package, "/plugin.yaml"))
        metadata = extract_plugin_metadata(package.read_bytes())
        assert (name, version) == ("amap", "3.1.4")
        assert plugin_yaml["runtime"]["type"] == "agent-mcp"
        assert plugin_yaml["display_name"] == "高德地图"
        assert plugin_yaml["description"] == "地图查询能力"
        assert metadata["asset_type"] == "agent-mcp"
        assert metadata["integration_type"] == "remote-mcp"
    finally:
        package.unlink(missing_ok=True)


@pytest.mark.parametrize("runtime_type", ["tools", "mcp-stdio", "restful-api"])
def test_standard_legacy_plugin_is_rejected_by_admin_import(
    tmp_path: Path, runtime_type: str
) -> None:
    entry = tmp_path / runtime_type
    entry.mkdir()
    (entry / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "legacy-demo",
                "version": "1.0.0",
                "display_name": "Legacy demo",
                "description": "Legacy package",
                "runtime": {"type": runtime_type},
                "metadata": {"author": "system_admin", "tags": []},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="asset_type_not_supported"):
        _normalize(entry)


def _write_raw_agent_plugin(entry: Path, *, name: str = "wellness-plugin") -> None:
    (entry / "tools").mkdir(parents=True)
    (entry / "manifest.json").write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "packageType": "plugin",
                "id": name,
                "name": "Wellness",
                "description": "Wellness tools",
                "tools": [{"file": "tools/tool.py"}],
            }
        ),
        encoding="utf-8",
    )
    (entry / "README.md").write_text("# Wellness", encoding="utf-8")
    (entry / "tools" / "tool.py").write_text("def run():\n    return True\n", encoding="utf-8")


def _fake_publish_result(content: bytes) -> PluginPublishResult:
    meta = extract_plugin_metadata(content)
    name = str(meta["name"])
    return PluginPublishResult(
        plugin_id=f"id-{name}",
        asset_id=f"id-{name}",
        asset_type=str(meta.get("asset_type") or "plugin"),
        plugin_type=str(meta["plugin_type"]),
        name=name,
        display_name=str(meta["display_name"]),
        version=str(meta["version"]),
        status="ACTIVE",
        published_at="2026-08-21T00:00:00Z",
        storage_url=f"objects/{name}.zip",
    )


def test_single_asset_bundle_root_is_one_import_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "SKILL.md").write_text(
        """---
name: single-team
description: Single team
kind: team-skill
roles:
  - id: lead
  - id: reviewer
---
""",
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_publish(**kwargs: object) -> PluginPublishResult:
        calls.append(kwargs)
        return _fake_publish_result(kwargs["content"])  # type: ignore[arg-type]

    monkeypatch.setattr("plugins_market.imports.skill_import_service.publish", fake_publish)
    result = skill_import_from_staging_dir(
        tmp_path,
        user_id="system_admin",
        db=SimpleNamespace(rollback=lambda: None),
        storage=SimpleNamespace(),
        single_entry_name_hint="single-team",
        is_system_token=True,
        publisher_name_override="system_admin",
        allow_multi_asset=True,
    )

    assert result.summary.model_dump() == {"total": 1, "ok": 1, "failed": 0, "skipped": 0}
    assert result.results[0].entry == "single-team"
    assert result.results[0].asset_type == "plugin"
    assert result.results[0].plugin_type == "swarmskill"
    assert calls[0]["is_system_token"] is True
    assert calls[0]["publisher_name_override"] == "system_admin"


def test_mixed_asset_collection_returns_precise_types_and_uses_system_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_raw_agent_plugin(tmp_path / "wellness-plugin")
    mcp = tmp_path / "amap"
    mcp.mkdir()
    (mcp / "mcp.json").write_text(
        json.dumps({"mcpServers": {"amap": {"url": "https://example.com/mcp"}}}),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "entries": {
                    "amap": {
                        "version": "1.0.0",
                        "display_name": "Amap",
                        "description": "Map tools",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_publish(**kwargs: object) -> PluginPublishResult:
        calls.append(kwargs)
        return _fake_publish_result(kwargs["content"])  # type: ignore[arg-type]

    monkeypatch.setattr("plugins_market.imports.skill_import_service.publish", fake_publish)
    result = skill_import_from_staging_dir(
        tmp_path,
        user_id="system_admin",
        db=SimpleNamespace(rollback=lambda: None),
        storage=SimpleNamespace(),
        is_system_token=True,
        publisher_name_override="system_admin",
        allow_multi_asset=True,
    )

    assert result.summary.model_dump() == {"total": 2, "ok": 2, "failed": 0, "skipped": 0}
    assert {(item.asset_type, item.plugin_type) for item in result.results} == {
        ("agent-plugin", "agent-plugin"),
        ("agent-mcp", "agent-mcp"),
    }
    assert all(call["is_system_token"] is True for call in calls)


def test_mcp_builtin_index_supplies_per_asset_market_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp = tmp_path / "amap"
    mcp.mkdir()
    (mcp / "mcp.json").write_text(
        json.dumps({"mcpServers": {"amap": {"url": "https://example.com/mcp"}}}),
        encoding="utf-8",
    )
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "version": "v0.1",
                "mcps": [
                    {
                        "id": "amap",
                        "source": "amap",
                        "name": "高德地图",
                        "name_en": "Amap",
                        "description_zh": "地图查询能力",
                        "description_en": "Map tools",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    published_meta: list[dict[str, object]] = []

    def fake_publish(**kwargs: object) -> PluginPublishResult:
        meta = extract_plugin_metadata(kwargs["content"])  # type: ignore[arg-type]
        published_meta.append(meta)
        return _fake_publish_result(kwargs["content"])  # type: ignore[arg-type]

    monkeypatch.setattr("plugins_market.imports.skill_import_service.publish", fake_publish)
    result = skill_import_from_staging_dir(
        tmp_path,
        user_id="system_admin",
        db=SimpleNamespace(rollback=lambda: None),
        storage=SimpleNamespace(),
        is_system_token=True,
        publisher_name_override="system_admin",
        allow_multi_asset=True,
    )

    assert result.summary.ok == 1
    assert published_meta[0]["display_name"] == "高德地图"
    assert published_meta[0]["short_desc"] == "地图查询能力"
    assert published_meta[0]["version"] == "0.0.1"


def test_mcp_builtin_index_rejects_duplicate_source(tmp_path: Path) -> None:
    (tmp_path / "amap").mkdir()
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "mcps": [
                    {"id": "amap", "source": "amap", "name": "Amap"},
                    {"id": "amap", "source": "amap", "name": "Duplicate"},
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PublishError) as exc_info:
        skill_import_from_staging_dir(
            tmp_path,
            user_id="system_admin",
            db=SimpleNamespace(),
            storage=SimpleNamespace(),
            allow_multi_asset=True,
        )

    assert exc_info.value.detail["error"] == "manifest_invalid"
    assert "index.json.mcps[1]" in exc_info.value.detail["message"]
    assert "amap" in exc_info.value.detail["message"]


def test_reused_staging_service_does_not_elevate_non_admin_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = tmp_path / "ordinary-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: ordinary-skill\ndescription: Ordinary skill\n---\n",
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_publish(**kwargs: object) -> PluginPublishResult:
        calls.append(kwargs)
        return _fake_publish_result(kwargs["content"])  # type: ignore[arg-type]

    monkeypatch.setattr("plugins_market.imports.skill_import_service.publish", fake_publish)
    result = skill_import_from_staging_dir(
        tmp_path,
        user_id="git-user",
        db=SimpleNamespace(rollback=lambda: None),
        storage=SimpleNamespace(),
    )

    assert result.summary.ok == 1
    assert "is_system_token" not in calls[0]
    assert "publisher_name_override" not in calls[0]
    assert "asset_id" not in result.results[0].model_dump()
    assert "asset_type" not in result.results[0].model_dump()
    assert "plugin_type" not in result.results[0].model_dump()


def test_skill_import_default_rejects_agent_asset_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_raw_agent_plugin(tmp_path / "wellness-plugin")

    def unexpected_publish(**_kwargs: object) -> PluginPublishResult:
        raise AssertionError("legacy skill import must reject agent assets before publishing")

    monkeypatch.setattr("plugins_market.imports.skill_import_service.publish", unexpected_publish)

    result = skill_import_from_staging_dir(
        tmp_path,
        user_id="system_admin",
        db=SimpleNamespace(rollback=lambda: None),
        storage=SimpleNamespace(),
    )

    assert result.summary.model_dump() == {"total": 1, "ok": 0, "failed": 1, "skipped": 0}
    assert result.results[0].error == "import_normalize_failed"
    assert result.results[0].message == "skill_layout_unrecognized"


def test_skill_import_default_rejects_single_skill_at_bundle_root(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text(
        "---\nname: root-skill\ndescription: Root skill\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(PublishError) as exc_info:
        skill_import_from_staging_dir(
            tmp_path,
            user_id="system_admin",
            db=SimpleNamespace(rollback=lambda: None),
            storage=SimpleNamespace(),
        )

    detail = exc_info.value.detail
    assert detail["error"] == "invalid_skill_bundle"
    assert detail["error_code"] == "SKILLHUB_IMPORT_INVALID_BUNDLE"


def test_skill_import_default_keeps_legacy_simple_skill_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = tmp_path / "team-shaped-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        """---
name: team-shaped-skill
description: Legacy import
kind: swarm-skill
roles:
  - id: lead
---

# Body
""",
        encoding="utf-8",
    )
    published_skill_md: list[str] = []

    def fake_publish(**kwargs: object) -> PluginPublishResult:
        content = kwargs["content"]
        assert isinstance(content, bytes)
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            member = next(name for name in zf.namelist() if name.endswith("/SKILL.md"))
            published_skill_md.append(
                zf.read(member).decode("utf-8").replace("\r\n", "\n")
            )
        return PluginPublishResult(
            plugin_id="legacy-id",
            name="team-shaped-skill",
            display_name="team-shaped-skill",
            version="0.0.1",
            status="ACTIVE",
            published_at="2026-08-21T00:00:00Z",
            storage_url="objects/team-shaped-skill.zip",
            plugin_type="skill",
        )

    monkeypatch.setattr("plugins_market.imports.skill_import_service.publish", fake_publish)
    result = skill_import_from_staging_dir(
        tmp_path,
        user_id="system_admin",
        db=SimpleNamespace(rollback=lambda: None),
        storage=SimpleNamespace(),
    )

    assert result.summary.ok == 1
    assert published_skill_md == [
        '---\nname: team-shaped-skill\ndescription: "Legacy import"\n---\n\n# Body'
    ]


def test_asset_import_and_skill_import_have_distinct_endpoint_handlers() -> None:
    endpoints = {
        route.path: route.endpoint
        for route in plugin_router.routes
        if route.path in {"/plugins/asset-import", "/plugins/skill-import"}
    }
    assert set(endpoints) == {"/plugins/asset-import", "/plugins/skill-import"}
    assert endpoints["/plugins/asset-import"] is not endpoints["/plugins/skill-import"]


def test_asset_import_has_an_independent_rate_limit_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plugin_router_module.settings,
        "skill_import_rate_limit_per_minute",
        1,
    )
    skill_times = getattr(plugin_router_module, "_skill_import_req_times")
    asset_times = getattr(plugin_router_module, "_asset_import_req_times")
    enforce_skill_limit = getattr(
        plugin_router_module,
        "_enforce_skill_import_rate_limit",
    )
    enforce_asset_limit = getattr(
        plugin_router_module,
        "_enforce_asset_import_rate_limit",
    )
    skill_times.clear()
    asset_times.clear()
    try:
        asyncio.run(enforce_skill_limit())
        asyncio.run(enforce_asset_limit())
    finally:
        skill_times.clear()
        asset_times.clear()


def test_asset_import_uses_asset_specific_extract_error(tmp_path: Path) -> None:
    bundle_path = tmp_path / "broken.zip"
    bundle_path.write_bytes(b"not-a-zip")

    with pytest.raises(PublishError) as exc_info:
        skill_import_from_bundle(
            bundle_path=bundle_path,
            user_id="system_admin",
            db=SimpleNamespace(),
            storage=SimpleNamespace(),
            allow_multi_asset=True,
        )

    assert exc_info.value.detail["error"] == "invalid_asset_bundle"
    assert exc_info.value.detail["message"] == "not a valid zip file"


def test_asset_import_uses_asset_specific_success_and_audit_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"PK asset import route fixture"
    bundle = SkillImportBundle(
        file=UploadFile(filename="assets.zip", file=io.BytesIO(content)),
        checksum=hashlib.sha256(content).hexdigest(),
        force=False,
        fail_fast=False,
    )
    request = Request(
        {
            "type": "http",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )
    audit_details: list[str] = []

    async def no_rate_limit() -> None:
        return None

    def fake_import(**_kwargs: object) -> AssetImportResponse:
        return AssetImportResponse(
            summary=AssetImportSummary(total=1, ok=1, failed=0, skipped=0),
            results=[],
        )

    def fake_audit_log(**kwargs: object) -> None:
        audit_details.append(str(kwargs["detail"]))

    monkeypatch.setattr(
        plugin_router_module,
        "_enforce_asset_import_rate_limit",
        no_rate_limit,
    )
    monkeypatch.setattr(plugin_router_module, "skill_import_from_bundle", fake_import)
    monkeypatch.setattr(plugin_router_module, "audit_log", fake_audit_log)

    response = asyncio.run(
        plugin_router_module.asset_import(
            request=request,
            bundle=bundle,
            db=SimpleNamespace(),
            storage=SimpleNamespace(),
            auth=(None, True, "system_admin", "gitcode"),
        )
    )

    assert response.message == "Import assets finished"
    assert audit_details == ["批量导入资产完成，成功 1 个，失败 0 个，跳过 0 个，共 1 个"]


def test_deduplicated_publish_marks_import_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_raw_agent_plugin(tmp_path / "wellness-plugin")
    calls: list[dict[str, object]] = []

    def fake_publish(**kwargs: object) -> PluginPublishResult:
        calls.append(kwargs)
        return PluginPublishResult(
            plugin_id="id-wellness-plugin",
            asset_id="id-wellness-plugin",
            asset_type="agent-plugin",
            plugin_type="agent-plugin",
            name="wellness-plugin",
            display_name="Wellness",
            version="1.0.0",
            status="ACTIVE",
            published_at="2026-08-21T00:00:00Z",
            storage_url="objects/wellness-plugin.zip",
            deduplicated=True,
        )

    monkeypatch.setattr("plugins_market.imports.skill_import_service.publish", fake_publish)
    result = skill_import_from_staging_dir(
        tmp_path,
        user_id="system_admin",
        db=SimpleNamespace(rollback=lambda: None),
        storage=SimpleNamespace(),
        is_system_token=True,
        publisher_name_override="system_admin",
        allow_multi_asset=True,
    )

    assert result.summary.model_dump() == {"total": 1, "ok": 0, "failed": 0, "skipped": 1}
    assert result.results[0].status == "skipped"
    assert result.results[0].message == "已存在相同版本与内容，跳过"
    assert calls


def test_import_normalize_failure_includes_manifest_identity(tmp_path: Path) -> None:
    entry = tmp_path / "raw-agent"
    entry.mkdir()
    (entry / "manifest.json").write_text(
        json.dumps(
            {
                "version": "2.3.4",
                "packageType": "plugin",
                "id": "raw-agent-id",
                "name": "Raw Agent",
                "description": "Broken runtime",
                "runtime": {"type": "tools"},
                "tools": [{"file": "tools/tool.py"}],
            }
        ),
        encoding="utf-8",
    )
    (entry / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "yaml-should-win",
                "version": "9.9.9",
                "display_name": "Yaml",
                "description": "Yaml desc",
                "runtime": {"type": "tools"},
                "metadata": {"author": "system_admin", "tags": []},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = skill_import_from_staging_dir(
        tmp_path,
        user_id="system_admin",
        db=SimpleNamespace(rollback=lambda: None),
        storage=SimpleNamespace(),
        allow_multi_asset=True,
    )

    assert result.summary.failed == 1
    item = result.results[0]
    assert item.status == "error"
    assert item.name == "yaml-should-win"
    assert item.version == "9.9.9"
    assert item.error == "import_normalize_failed"


def test_import_normalize_failure_falls_back_to_manifest_without_plugin_yaml(
    tmp_path: Path,
) -> None:
    entry = tmp_path / "raw-agent"
    entry.mkdir()
    (entry / "manifest.json").write_text(
        json.dumps(
            {
                "version": "2.3.4",
                "packageType": "plugin",
                "id": "raw-agent-id",
                "name": "Raw Agent",
                "description": "Broken runtime",
                "tools": [{"file": "tools/tool.py"}],
            }
        ),
        encoding="utf-8",
    )
    (entry / "README.md").write_text("# Raw", encoding="utf-8")
    (entry / "tools").mkdir()
    (entry / "tools" / "tool.py").write_text("def run():\n    return True\n", encoding="utf-8")

    def unexpected_publish(**_kwargs: object) -> PluginPublishResult:
        raise AssertionError("normalize must fail before publish")

    import plugins_market.imports.skill_import_service as import_service

    original = import_service.entry_to_publish_zip

    def fail_normalize(*args: object, **kwargs: object) -> tuple[Path, str, str]:
        raise ValueError("asset_type_not_supported: tools")

    import_service.entry_to_publish_zip = fail_normalize  # type: ignore[assignment]
    try:
        result = skill_import_from_staging_dir(
            tmp_path,
            user_id="system_admin",
            db=SimpleNamespace(rollback=lambda: None),
            storage=SimpleNamespace(),
            allow_multi_asset=True,
        )
    finally:
        import_service.entry_to_publish_zip = original

    assert result.summary.failed == 1
    item = result.results[0]
    assert item.name == "raw-agent-id"
    assert item.version == "2.3.4"
