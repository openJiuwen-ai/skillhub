# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Hub-side validation and publish-shape regressions for ``runtime.type: app``."""

from __future__ import annotations

import io
import os
import zipfile

os.environ.setdefault("STORE_DB_URL", "mysql+pymysql://test:test@127.0.0.1:3306/test")

import pytest
import yaml

from plugins_market.core.errors import PublishError
from plugins_market.validation import extract_plugin_metadata
from plugins_market.validation._pipeline import peek_plugin_runtime_type
from plugins_market.validation.constants import APP_SCRIPT_SCAN_BYTES, RUNTIME_APP
from plugins_market.validation.types.app_asset import validate_app_asset_layout
from plugins_market.validation.types.agent_asset import AgentAssetOuterRef
from plugins_market.validation.zip_utils import DecompressCounter


def _build_app_zip(
    *,
    name: str = "gomoku",
    version: str = "1.2.3-beta.1",
    html_version: str | None = "1.2.3-beta.1",
    inner_files: dict[str, str | bytes] | None = None,
    outer_files: dict[str, str | bytes] | None = None,
    app_meta: dict | None = None,
    omit_entry: bool = False,
    head_prefix: str = "",
    entry_html: str | None = None,
) -> bytes:
    plugin_yaml = yaml.safe_dump(
        {
            "name": name,
            "version": version,
            "display_name": "五子棋 · Gomoku",
            "description": "与 WorkSwarm AI 对弈。",
            "runtime": {"type": "app"},
            "metadata": {
                "author": "openjiuwen",
                "tags": ["game", "ai"],
                "app": app_meta if app_meta is not None else {},
            },
        },
        sort_keys=False,
    )
    entry = entry_html or (
        "<!doctype html><html><head>" + head_prefix
        + (
            f'<meta name="application-version" content="{html_version}">'
            if html_version is not None
            else ""
        )
        + '<meta name="application-name" content="五子棋"></head><body></body></html>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(f"{name}/plugin.yaml", plugin_yaml)
        for relative, content in (outer_files or {}).items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            zf.writestr(f"{name}/{relative}", content)
        prefix = f"{name}/{name}/"
        if not omit_entry:
            zf.writestr(prefix + "index.html", entry)
        zf.writestr(prefix + "assets/app.js", "const answer = 6 * 7;")
        for relative, content in (inner_files or {}).items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            zf.writestr(prefix + relative, content)
    return buffer.getvalue()


def _validate(content: bytes, name: str = "gomoku", version: str = "1.2.3-beta.1") -> dict:
    counter = DecompressCounter()
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return validate_app_asset_layout(
            zf,
            AgentAssetOuterRef(
                prefix=f"{name}/", name=name, version=version, runtime_type=RUNTIME_APP
            ),
            counter,
            metadata={
                "author": "openjiuwen",
                "tags": ["game"],
                "app": {"requires_project": True, "permissions": ["ai"]},
            },
        )


def test_valid_app_package_extracts_market_metadata() -> None:
    content = _build_app_zip(
        inner_files={"README.md": "# 五子棋\n", "scripts/app_server.py": "print('ok')\n"},
        app_meta={"requires_project": False, "permissions": ["ai"]},
    )
    meta = extract_plugin_metadata(content)

    assert meta["plugin_type"] == RUNTIME_APP
    assert meta["name"] == "gomoku"
    assert meta["version"] == "1.2.3-beta.1"
    assert meta["display_name"] == "五子棋 · Gomoku"
    assert meta["detail_desc"].startswith("# 五子棋")
    assert meta["requires_project"] is False
    assert meta["permissions"] == ["ai"]
    assert meta["has_server_script"] is True
    assert meta["source_size"] > 0


def test_app_requires_index_html() -> None:
    with pytest.raises(PublishError) as excinfo:
        _validate(_build_app_zip(omit_entry=True))
    assert excinfo.value.error == "invalid_plugin_structure"


@pytest.mark.parametrize(
    "path",
    [
        "data/seed.json",
        ".git/config",
        "scripts/START.PS1",
        "scripts",
        "Scripts/server.py",
        ".workswarm-app-release",
    ],
)
def test_app_rejects_installation_reserved_paths(path: str) -> None:
    with pytest.raises(PublishError) as excinfo:
        extract_plugin_metadata(_build_app_zip(inner_files={path: "placeholder"}))
    assert excinfo.value.error == "invalid_plugin_structure"


@pytest.mark.parametrize("name", ["a" * 65, "con", "lpt1"])
def test_app_rejects_names_that_swarm_cannot_install(name: str) -> None:
    with pytest.raises(PublishError) as excinfo:
        extract_plugin_metadata(_build_app_zip(name=name))
    assert excinfo.value.error == "invalid_plugin_structure"


def test_app_rejects_over_limit_individual_files(monkeypatch) -> None:
    from plugins_market.validation.types import app_asset

    monkeypatch.setattr(app_asset, "MAX_APP_SINGLE_FILE_SIZE", 1024)
    with pytest.raises(PublishError) as excinfo:
        extract_plugin_metadata(_build_app_zip(inner_files={"assets/data.bin": b"x" * 1025}))
    assert excinfo.value.error == "invalid_plugin_structure"


def test_audit_orm_column_matches_app_version_limit() -> None:
    from plugins_market.core.audit import AuditLog
    from plugins_market.validation.constants import APP_VERSION_MAX_LEN

    assert AuditLog.__table__.c.resource_version.type.length == APP_VERSION_MAX_LEN


def test_app_version_mismatch_is_rejected() -> None:
    with pytest.raises(PublishError) as excinfo:
        _validate(_build_app_zip(html_version="9.9.9"))
    assert excinfo.value.error == "app_version_mismatch"


def test_app_without_declared_html_version_is_accepted() -> None:
    layout = _validate(_build_app_zip(html_version=None))
    assert layout["asset_type"] == RUNTIME_APP


def test_app_frontend_bundle_is_not_blocked() -> None:
    content = _build_app_zip(
        inner_files={"assets/vendor.js": 'export const run = () => eval("1+1");\n'}
    )
    assert extract_plugin_metadata(content)["plugin_type"] == RUNTIME_APP


def test_app_server_script_dangerous_content_is_rejected() -> None:
    content = _build_app_zip(
        inner_files={"scripts/app_server.py": "import os\nos.system('id')\n"}
    )
    with pytest.raises(PublishError) as excinfo:
        extract_plugin_metadata(content)
    assert excinfo.value.error == "dangerous_content"


def test_app_rejects_unsupported_permission() -> None:
    content = _build_app_zip(app_meta={"permissions": ["fs"]})
    with pytest.raises(PublishError) as excinfo:
        extract_plugin_metadata(content)
    assert excinfo.value.error == "invalid_plugin_config"


def test_app_rejects_sibling_payload() -> None:
    buffer = io.BytesIO(_build_app_zip())
    out = io.BytesIO()
    with zipfile.ZipFile(buffer) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr("gomoku/other/index.html", "<html></html>")
    with pytest.raises(PublishError) as excinfo:
        extract_plugin_metadata(out.getvalue())
    assert excinfo.value.error == "invalid_plugin_structure"


@pytest.mark.parametrize(
    "version",
    ["1", "1.2", "1.2.3", "1.2.3.4", "1.2.3-beta.1", "2.0.0+rev.1"],
)
def test_app_version_rules_accept(version: str) -> None:
    content = _build_app_zip(version=version, html_version=version)
    assert extract_plugin_metadata(content)["version"] == version


@pytest.mark.parametrize("version", ["v1.2.3", "1.2.3.4.5", "1..2", "1.2.3-"])
def test_app_version_rules_reject(version: str) -> None:
    content = _build_app_zip(version=version, html_version=version)
    with pytest.raises(PublishError):
        extract_plugin_metadata(content)


def test_app_source_size_limit_is_enforced(monkeypatch) -> None:
    import plugins_market.validation.types.app_asset as app_asset

    monkeypatch.setattr(app_asset, "MAX_APP_SOURCE_SIZE", 10)
    with pytest.raises(PublishError) as excinfo:
        _validate(_build_app_zip())
    assert excinfo.value.error == "file_too_large"


def test_peek_runtime_type_reads_wrapper() -> None:
    assert peek_plugin_runtime_type(_build_app_zip()) == RUNTIME_APP
    bare = io.BytesIO()
    with zipfile.ZipFile(bare, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: demo\n---\n")
    assert peek_plugin_runtime_type(bare.getvalue()) == ""


def test_app_storage_root_is_apps() -> None:
    pytest.importorskip("structlog")
    pytest.importorskip("boto3")
    from plugins_market.services.plugin import _storage_root

    assert _storage_root(RUNTIME_APP, RUNTIME_APP) == "apps"
    assert _storage_root("plugin", "skill") == "skills"


def test_app_publish_overrides_must_match_package() -> None:
    pytest.importorskip("structlog")
    from plugins_market.imports.publish_wrap import PublishMetadataOverrides
    from plugins_market.services.plugin import (
        _validate_app_publish_overrides,
        _validate_version,
    )

    meta = {
        "name": "gomoku",
        "version": "1.2.3-beta.1",
        "display_name": "五子棋",
        "short_desc": "对弈",
        "tags": ["game", "ai"],
    }
    _validate_app_publish_overrides(
        meta,
        PublishMetadataOverrides(
            asset_name="gomoku",
            version="1.2.3-beta.1",
            display_name="五子棋",
            description="对弈",
            tags=["AI", "game"],
        ),
    )
    with pytest.raises(PublishError) as excinfo:
        _validate_app_publish_overrides(
            meta, PublishMetadataOverrides(display_name="改名")
        )
    assert excinfo.value.error == "invalid_plugin_config"

    _validate_version("1.2", "app")
    with pytest.raises(PublishError):
        _validate_version("v1.2", "app")


@pytest.mark.parametrize(
    "relative",
    ["assets/aux.txt", "assets/name.", "assets/name ", "assets/a:b.txt", "con/index.js"],
)
def test_app_package_rejects_windows_unportable_paths(relative: str) -> None:
    content = _build_app_zip(inner_files={relative: "x"})
    with pytest.raises(PublishError) as exc_info:
        _validate(content)
    assert exc_info.value.error == "invalid_plugin_structure"


def test_app_package_rejects_case_folded_collision() -> None:
    content = _build_app_zip(inner_files={"assets/A.txt": "a", "assets/a.txt": "b"})
    with pytest.raises(PublishError) as exc_info:
        _validate(content)
    assert exc_info.value.error == "invalid_plugin_structure"


def test_explicit_wrapper_directories_are_valid() -> None:
    content = io.BytesIO(_build_app_zip())
    with zipfile.ZipFile(content, "a") as archive:
        archive.writestr("gomoku/", "")
        archive.writestr("gomoku/gomoku/", "")
    assert extract_plugin_metadata(content.getvalue())["asset_type"] == "app"


def test_incomplete_html_head_cannot_hide_version_mismatch() -> None:
    content = _build_app_zip(head_prefix=" " * 65536, html_version="9")
    with pytest.raises(PublishError) as error:
        extract_plugin_metadata(content)
    assert error.value.error == "invalid_plugin_structure"


def test_html_without_head_cannot_hide_version_mismatch() -> None:
    entry = '<html><meta name="application-version" content="9.9.9"><body></body></html>'
    with pytest.raises(PublishError) as error:
        extract_plugin_metadata(_build_app_zip(entry_html=entry))
    assert error.value.error == "app_version_mismatch"


def test_script_over_scan_limit_is_rejected() -> None:
    oversized = "# pad\n" * (APP_SCRIPT_SCAN_BYTES // 6 + 1)
    content = _build_app_zip(inner_files={"scripts/app_server.py": oversized})
    with pytest.raises(PublishError) as error:
        extract_plugin_metadata(content)
    assert error.value.error == "invalid_plugin_structure"


@pytest.mark.parametrize("files", [
    {"assets/" + "sub/" * 30 + "image.svg": "image"},
    {"assets/Logo/icon.svg": "image", "assets/logo": "file"},
])
def test_app_rejects_uninstallable_resource_paths(files: dict[str, str]) -> None:
    with pytest.raises(PublishError) as error:
        extract_plugin_metadata(_build_app_zip(inner_files=files))
    assert error.value.error == "invalid_plugin_structure"


def test_app_is_a_wrapped_asset_with_a_valid_delete_response() -> None:
    """Deleting an app version must not fail response-model validation."""
    from plugins_market.core.moderation import (
        is_moderated_market_asset_type,
        is_wrapped_agent_asset_type,
        moderated_asset_type_label,
    )
    from plugins_market.schemas.plugin import AssetVersionDeleteData

    assert is_wrapped_agent_asset_type(RUNTIME_APP)
    assert is_moderated_market_asset_type(RUNTIME_APP)
    assert moderated_asset_type_label(RUNTIME_APP) == "应用"
    data = AssetVersionDeleteData(
        asset_id="asset-id",
        version="1.2.3-beta.1",
        plugin_type=RUNTIME_APP,
        asset_type=RUNTIME_APP,
    )
    assert data.asset_type == RUNTIME_APP
