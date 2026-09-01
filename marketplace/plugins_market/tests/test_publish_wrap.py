from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
import yaml

from plugins_market.core.errors import PublishError
from plugins_market.imports.publish_wrap import PublishMetadataOverrides, prepare_publish_zip_content
from plugins_market.validation import extract_plugin_metadata


def _write_raw_agent_plugin(entry: Path, *, name: str = "wellness-plugin") -> None:
    (entry / "tools").mkdir(parents=True)
    manifest = {
        "version": "1.2.3",
        "packageType": "plugin",
        "id": name,
        "name": "Wellness fallback",
        "description": "Fallback description",
        "displayName": {"zh": "健康生活插件"},
        "displayDescription": {"zh": "健康工具集"},
        "tools": [{"file": "tools/wellness.py"}],
    }
    (entry / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (entry / "README.md").write_text("# Wellness", encoding="utf-8")
    (entry / "tools" / "wellness.py").write_text("def run():\n    return True\n", encoding="utf-8")


def _zip_dir(entry: Path, out: Path) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in entry.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(entry.parent).as_posix())


def _zip_text(content: bytes, suffix: str) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        member = next(name for name in zf.namelist() if name.endswith(suffix))
        return zf.read(member).decode("utf-8")


def test_prepare_publish_wraps_bare_agent_plugin_with_form_overrides(tmp_path: Path) -> None:
    entry = tmp_path / "wellness-life-steward"
    _write_raw_agent_plugin(entry, name="wellness-life-steward")
    raw_zip = tmp_path / "raw.zip"
    _zip_dir(entry, raw_zip)
    content = raw_zip.read_bytes()

    wrapped = prepare_publish_zip_content(
        content,
        filename="wellness-life-steward.zip",
        overrides=PublishMetadataOverrides(
            asset_name="renamed-plugin",
            version="2.0.0",
            display_name="重命名插件",
            description="表单描述",
            tags=["健康"],
        ),
        default_author="tester",
    )

    metadata = extract_plugin_metadata(wrapped)
    plugin_yaml = yaml.safe_load(_zip_text(wrapped, "/plugin.yaml"))
    manifest = json.loads(_zip_text(wrapped, "/manifest.json"))

    assert metadata["asset_type"] == "agent-plugin"
    assert plugin_yaml["name"] == "renamed-plugin"
    assert plugin_yaml["version"] == "2.0.0"
    assert plugin_yaml["display_name"] == "重命名插件"
    assert plugin_yaml["description"] == "表单描述"
    assert plugin_yaml["metadata"]["tags"] == ["健康"]
    assert manifest["id"] == "renamed-plugin"
    assert manifest["version"] == "2.0.0"


def test_prepare_publish_wraps_bare_agent_mcp_requires_version(tmp_path: Path) -> None:
    entry = tmp_path / "amap"
    entry.mkdir()
    (entry / "mcp.json").write_text(
        json.dumps({"mcpServers": {"amap-maps": {"url": "https://example.com/mcp"}}}),
        encoding="utf-8",
    )
    (entry / "README.md").write_text("# Amap", encoding="utf-8")
    raw_zip = tmp_path / "raw.zip"
    _zip_dir(entry, raw_zip)

    with pytest.raises(PublishError, match="version"):
        prepare_publish_zip_content(
            raw_zip.read_bytes(),
            filename="amap.zip",
            overrides=PublishMetadataOverrides(asset_name="amap"),
            default_author="tester",
        )


def _write_wrapped_skill(entry: Path, *, name: str = "demo-skill") -> None:
    skill_dir = entry / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo skill\n---\n# Demo\n",
        encoding="utf-8",
    )
    (entry / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "version": "1.0.0",
                "display_name": "Demo Skill",
                "description": "from package",
                "runtime": {"type": "skill"},
                "metadata": {"author": "tester", "tags": ["demo"]},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_prepare_publish_applies_version_override_on_wrapped_skill(tmp_path: Path) -> None:
    entry = tmp_path / "demo-skill-pkg"
    _write_wrapped_skill(entry)
    raw_zip = tmp_path / "skill.zip"
    _zip_dir(entry, raw_zip)

    wrapped = prepare_publish_zip_content(
        raw_zip.read_bytes(),
        filename="demo-skill.zip",
        overrides=PublishMetadataOverrides(version="2.0.0"),
        default_author="tester",
    )

    plugin_yaml = yaml.safe_load(_zip_text(wrapped, "/plugin.yaml"))
    metadata = extract_plugin_metadata(wrapped)

    assert plugin_yaml["version"] == "2.0.0"
    assert metadata["version"] == "2.0.0"
    assert metadata["plugin_type"] == "skill"


def test_prepare_publish_leaves_wrapped_package_when_overrides_match(tmp_path: Path) -> None:
    entry = tmp_path / "wellness-life-steward"
    _write_raw_agent_plugin(entry, name="wellness-life-steward")
    from plugins_market.imports.skill_entries import entry_to_publish_zip

    publish_zip, _name, _version = entry_to_publish_zip(
        entry,
        entry_key=entry.name,
        entry_overrides={},
        version_fallback="0.0.1",
        default_author="tester",
        default_tags=[],
        allow_multi_asset=True,
    )
    try:
        original = publish_zip.read_bytes()
        same = prepare_publish_zip_content(
            original,
            filename="wrapped.zip",
            overrides=PublishMetadataOverrides(
                asset_name="wellness-life-steward",
                version="1.2.3",
                display_name="健康生活插件",
                description="健康工具集",
            ),
            default_author="tester",
        )
        assert same == original
    finally:
        publish_zip.unlink(missing_ok=True)
