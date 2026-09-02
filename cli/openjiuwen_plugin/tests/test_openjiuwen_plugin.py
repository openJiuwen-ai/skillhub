# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from cli_core.market import (
    PublishError,
    plugin_info,
    plugin_install_download,
    plugin_search,
    resolve_market_asset,
    resolve_skill_like_asset_for_cli,
)
from cli_core.plugin import (
    plugin_describe_local,
    plugin_init,
    plugin_install,
    plugin_pack,
    plugin_publish,
    plugin_validate,
    teamskill_validate_directory,
)
from cli_core.schemas import (
    PluginListItem,
    PluginListQuery,
    PluginListResponse,
    PluginVersionDetail,
    PublishPluginInput,
    PublishRequest,
    SkillImportItemResult,
    SkillImportResponse,
    SkillImportSummary,
)

from openjiuwen_plugin.main import main
from openjiuwen_plugin.parsers import build_plugin_parser


class PluginCommandsTest(unittest.TestCase):
    def test_delete_short_v_maps_to_version(self) -> None:
        parser = build_plugin_parser("openjiuwen-plugin")
        args = parser.parse_args(["delete", "demo-id", "-v", "1.0.0", "--token", "t", "--market-url", "http://x"])
        self.assertEqual(args.version, "1.0.0")

    def test_publish_plugin_input_rejects_v_prefix_plugin_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "leading v/V"):
                PublishPluginInput(plugin_path=root, plugin_version=" v1.2.3 ")

    def test_publish_plugin_input_rejects_prerelease_plugin_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "marketplace semver"):
                PublishPluginInput(plugin_path=root, plugin_version="1.0.0-rc1")

    def test_publish_plugin_input_accepts_git_commit_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            root.mkdir()
            inp = PublishPluginInput(plugin_path=root, plugin_version="a1b2c3d")
            self.assertEqual(inp.plugin_version, "a1b2c3d")

    def test_publish_plugin_input_rejects_full_git_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            root.mkdir()
            sha = "a1b2c3d4e5f678901234567890123456"
            with self.assertRaisesRegex(ValueError, "7 lowercase hex"):
                PublishPluginInput(plugin_path=root, plugin_version=sha)

    def test_publish_request_invalid_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "demo.zip"
            zip_path.write_bytes(b"PK\x03\x04")
            with self.assertRaisesRegex(ValueError, "checksum_sha256"):
                PublishRequest(
                    zip_path=zip_path,
                    checksum_sha256="bad-checksum",
                )

    def test_publish_request_zip_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "missing.zip"
            with self.assertRaisesRegex(ValueError, "zip file not found"):
                PublishRequest(
                    zip_path=zip_path,
                    checksum_sha256="a" * 64,
                )

    def test_init_and_validate_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("demo-plugin", Path(tmp))
            self.assertTrue((plugin_root / "schemas" / "tools.json").exists())
            self.assertTrue((plugin_root / "src" / "demo_plugin" / "plugin.py").exists())
            result = plugin_validate(plugin_root)
            self.assertTrue(result.ok, msg=f"errors: {result.errors}")

    def test_init_and_validate_mcp_stdio_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("demo-mcp", Path(tmp), plugin_type="mcp-stdio")
            self.assertTrue((plugin_root / "schemas" / "tools.json").exists())
            self.assertTrue((plugin_root / "src" / "demo_mcp" / "mcp_server.py").exists())
            self.assertTrue((plugin_root / "pyproject.toml").exists())
            result = plugin_validate(plugin_root)
            self.assertTrue(result.ok, msg=f"errors: {result.errors}")

    def test_validate_fails_with_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "broken"
            root.mkdir(parents=True)
            (root / "plugin.yaml").write_text("name: broken-plugin\nversion: 0.1.0\n", encoding="utf-8")
            result = plugin_validate(root)
            self.assertFalse(result.ok)
            self.assertGreater(len(result.errors), 0)

    def test_validate_detects_tool_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("mismatch-plugin", Path(tmp))
            plugin_py = plugin_root / "src" / "mismatch_plugin" / "plugin.py"
            plugin_py.write_text(
                """from openjiuwen.core.foundation.tool import tool

@tool(name="another-tool", description="x", input_params={})
def another_tool() -> dict:
    return {"ok": True}
""",
                encoding="utf-8",
            )
            result = plugin_validate(plugin_root)
            self.assertFalse(result.ok)
            self.assertTrue(any("another-tool" in e for e in result.errors))

    def test_validate_fails_with_invalid_compatibility_specifiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("compat-plugin", Path(tmp))
            plugin_yaml = plugin_root / "plugin.yaml"
            content = plugin_yaml.read_text(encoding="utf-8")
            content = content.replace(">=3.11, <3.14", "3.11")
            plugin_yaml.write_text(content, encoding="utf-8")

            result = plugin_validate(plugin_root)
            self.assertFalse(result.ok)
            self.assertTrue(any("compatibility.python" in e for e in result.errors))

    def test_validate_compatibility_extra_keys_not_validated(self) -> None:
        """仅校验 compatibility.python；其它键（如 openjiuwen）CLI 不校验格式。"""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("compat-extra", Path(tmp))
            plugin_yaml = plugin_root / "plugin.yaml"
            data = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))
            assert isinstance(data, dict) and isinstance(data.get("compatibility"), dict)
            data["compatibility"]["openjiuwen"] = "latest"
            plugin_yaml.write_text(
                yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            result = plugin_validate(plugin_root)
            self.assertTrue(result.ok, msg=f"errors: {result.errors}")

    def test_validate_rejects_prerelease_version(self) -> None:
        """与 marketplace 一致：version 仅允许 x.y.z 三位数字。"""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("ver-demo", Path(tmp), plugin_type="mcp-stdio")
            p = plugin_root / "plugin.yaml"
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            assert isinstance(data, dict)
            data["version"] = "1.0.0-rc1"
            p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
            result = plugin_validate(plugin_root)
            self.assertFalse(result.ok)
            self.assertTrue(any("x.y.z" in e for e in result.errors))

    def test_cli_init_via_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "demo-plugin", "--path", tmp])
            self.assertEqual(code, 0)

    def test_cli_init_with_mcp_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "demo-mcp", "--path", tmp, "--type", "mcp-stdio"])
            self.assertEqual(code, 0)

    def test_cli_init_with_swarmskill_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "demo-swarm", "--path", tmp, "--type", "swarmskill"])
            self.assertEqual(code, 0)
            root = Path(tmp) / "demo-swarm"
            self.assertTrue((root / "SKILL.md").is_file())

    def test_pack_success(self) -> None:
        """mcp-stdio 类型整目录打包，不依赖 wheel。"""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("pack-demo", Path(tmp), plugin_type="mcp-stdio")
            out_dir = Path(tmp) / "out"
            zip_path = plugin_pack(plugin_root, out_dir)
            self.assertTrue(zip_path.exists())
            self.assertEqual(zip_path.name, "pack-demo-0.0.1.zip")
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
            self.assertTrue(any("plugin.yaml" in n for n in names))
            self.assertTrue(any("pack-demo-0.0.1" in n for n in names))

    def test_pack_excludes_plugin_out_directory(self) -> None:
        """mcp/rest 整目录打包时不应包含插件根目录下的 out/（避免历史 zip 被打进新包）。"""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("skip-mcp", Path(tmp), plugin_type="mcp-stdio")
            (plugin_root / "out").mkdir(parents=True, exist_ok=True)
            (plugin_root / "out" / "stale.zip").write_bytes(b"dummy")
            zip_path = plugin_pack(plugin_root, Path(tmp) / "publish-out")
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
            prefix = "skip-mcp-0.0.1"
            self.assertFalse(any(n.replace("\\", "/").startswith(f"{prefix}/out/") for n in names))

    def test_init_swarmskill_validate_pack_install_no_pip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("demo-ts", Path(tmp), plugin_type="swarmskill")
            skill = plugin_root
            self.assertTrue((skill / "SKILL.md").is_file())
            self.assertFalse((plugin_root / "plugin.yaml").exists())
            result = plugin_validate(plugin_root)
            self.assertTrue(result.ok, msg=f"errors: {result.errors}")
            zip_path = plugin_pack(plugin_root, Path(tmp) / "out")
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
            prefix = "demo-ts"
            norm = [n.replace("\\", "/") for n in names]
            self.assertFalse(any("plugin.yaml" in n for n in norm))
            self.assertTrue(any(f"{prefix}/SKILL.md" in n for n in norm))

    def test_teamskill_validate_roles_requires_at_least_two_ids(self) -> None:
        one_role_fm = (
            "---\n"
            "name: x-skill\n"
            'description: "short desc"\n'
            "roles:\n"
            "  - id: only-one\n"
            "---\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "x-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(one_role_fm, encoding="utf-8")
            errors, _warnings = teamskill_validate_directory(skill_dir)
            self.assertTrue(errors)
            self.assertTrue(any("at least 2" in e for e in errors))

    def test_teamskill_validate_roles_rejects_duplicate_ids(self) -> None:
        dup_fm = (
            "---\n"
            "name: dup-skill\n"
            'description: "short desc"\n'
            "roles:\n"
            "  - id: same\n"
            "  - id: same\n"
            "---\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "dup-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(dup_fm, encoding="utf-8")
            errors, _warnings = teamskill_validate_directory(skill_dir)
            self.assertTrue(errors)
            self.assertTrue(any("repeat" in e.lower() for e in errors))

    def test_install_flat_skill_zip_creates_slug_subdirectory(self) -> None:
        """解压后根目录即 SKILL.md（无 <slug>/ 子目录）时，安装到目标下仍为 <slug>/SKILL.md。"""
        skill_md = (
            "---\n"
            "name: flat-skill\n"
            'description: "flat bundle"\n'
            'version: "0.0.1"\n'
            "---\n\n"
            "## Instructions\n\nx\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "flat-bundle-0.0.1"
            bundle.mkdir()
            (bundle / "SKILL.md").write_text(skill_md, encoding="utf-8")
            zip_path = Path(tmp) / "flat-skill.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("flat-bundle-0.0.1/SKILL.md", skill_md.encode("utf-8"))
            inst = Path(tmp) / "install_root"
            with patch("cli_core.plugin.subprocess.run") as m_run:
                out = plugin_install(zip_path, extract_dir=inst)
            m_run.assert_not_called()
            self.assertEqual(out, inst / "flat-skill")
            self.assertTrue((out / "SKILL.md").is_file())

    def test_install_skill_with_plugin_yaml_creates_slug_subdirectory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "with-manifest-skill.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "bundle/plugin.yaml",
                    yaml.safe_dump(
                        {
                            "name": "manifest-skill",
                            "version": "1.0.0",
                            "display_name": "Manifest Skill",
                            "description": "demo",
                            "runtime": {"type": "skill"},
                            "metadata": {"author": "tester", "tags": ["demo"]},
                        },
                        sort_keys=False,
                        allow_unicode=True,
                    ),
                )
                zf.writestr(
                    "bundle/manifest-skill/SKILL.md",
                    (
                        "---\n"
                        "name: manifest-skill\n"
                        'description: "skill from manifest"\n'
                        "---\n\n"
                        "## Instructions\n\nx\n"
                    ),
                )
                zf.writestr("bundle/manifest-skill/scripts/run.py", "print('ok')\n")

            inst = Path(tmp) / "install_root"
            inst.mkdir(parents=True, exist_ok=True)
            with patch("cli_core.plugin.subprocess.run") as m_run:
                out = plugin_install(zip_path, extract_dir=inst)
            m_run.assert_not_called()
            self.assertEqual(out, inst / "manifest-skill")
            self.assertTrue((inst / "manifest-skill" / "SKILL.md").is_file())
            self.assertTrue((inst / "manifest-skill" / "scripts" / "run.py").is_file())

    def test_install_skill_with_plugin_yaml_conflict_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "with-manifest-skill.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "bundle/plugin.yaml",
                    yaml.safe_dump(
                        {
                            "name": "manifest-skill",
                            "version": "1.0.0",
                            "display_name": "Manifest Skill",
                            "description": "demo",
                            "runtime": {"type": "skill"},
                            "metadata": {"author": "tester", "tags": ["demo"]},
                        },
                        sort_keys=False,
                        allow_unicode=True,
                    ),
                )
                zf.writestr(
                    "bundle/manifest-skill/SKILL.md",
                    (
                        "---\n"
                        "name: manifest-skill\n"
                        'description: "skill from manifest"\n'
                        "---\n\n"
                        "## Instructions\n\nx\n"
                    ),
                )

            inst = Path(tmp) / "install_root"
            inst.mkdir(parents=True, exist_ok=True)
            (inst / "manifest-skill").mkdir(parents=True, exist_ok=True)
            (inst / "manifest-skill" / "SKILL.md").write_text("old", encoding="utf-8")
            with patch("cli_core.plugin.subprocess.run") as m_run:
                with self.assertRaises(FileExistsError):
                    plugin_install(zip_path, extract_dir=inst, force=False)
                out = plugin_install(zip_path, extract_dir=inst, force=True)
            m_run.assert_not_called()
            self.assertEqual(out, inst / "manifest-skill")
            self.assertIn("manifest-skill", (inst / "manifest-skill" / "SKILL.md").read_text(encoding="utf-8"))

    def test_init_skill_validate_pack_install_no_pip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("demo-skill", Path(tmp), plugin_type="skill")
            self.assertTrue((plugin_root / "SKILL.md").is_file())
            self.assertTrue((plugin_root / "scripts").is_dir())
            self.assertFalse((plugin_root / "src").exists())
            self.assertFalse((plugin_root / "README.md").exists())
            self.assertFalse((plugin_root / "plugin.yaml").exists())
            result = plugin_validate(plugin_root)
            self.assertTrue(result.ok, msg=f"errors: {result.errors}")
            out_dir = Path(tmp) / "out"
            zip_path = plugin_pack(plugin_root, out_dir)
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
            prefix = "demo-skill"
            norm = [n.replace("\\", "/") for n in names]
            self.assertFalse(any("plugin.yaml" in n for n in norm))
            self.assertTrue(any(f"{prefix}/demo-skill/SKILL.md" in n for n in norm))
            self.assertFalse(any("README.md" in n for n in names))
            self.assertFalse(any(f"{prefix}/demo-skill/scripts/" in n.replace("\\", "/") for n in names))
            inst = Path(tmp) / "install_root"
            with patch("cli_core.plugin.subprocess.run") as m_run:
                skill_dir = plugin_install(zip_path, extract_dir=inst)
            m_run.assert_not_called()
            self.assertTrue((skill_dir / "SKILL.md").is_file())
            self.assertFalse((inst / "demo-skill-0.1.0").exists())

    def test_init_restful_api_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("demo-api", Path(tmp), plugin_type="restful-api")
            self.assertTrue((plugin_root / "schemas" / "tools.json").exists())
            self.assertTrue((plugin_root / "src" / "demo_api" / "rest_api.py").exists())
            self.assertTrue((plugin_root / "pyproject.toml").exists())
            result = plugin_validate(plugin_root)
            self.assertTrue(result.ok, msg=f"errors: {result.errors}")

    def test_validate_restful_api_requires_tools_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("demo-api", Path(tmp), plugin_type="restful-api")
            (plugin_root / "schemas" / "tools.json").unlink()
            result = plugin_validate(plugin_root)
            self.assertFalse(result.ok)
            self.assertTrue(any("schemas/tools.json" in e for e in result.errors))

    def test_validate_restful_api_rejects_invalid_tool_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("demo-api", Path(tmp), plugin_type="restful-api")
            tools_json = plugin_root / "schemas" / "tools.json"
            data = json.loads(tools_json.read_text(encoding="utf-8"))
            assert isinstance(data, dict)
            tools = data.get("tools")
            assert isinstance(tools, list) and tools
            assert isinstance(tools[0], dict)
            tools[0]["headers"] = [{"name": "Authorization", "value": 123}]
            tools_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            result = plugin_validate(plugin_root)
            self.assertFalse(result.ok)
            self.assertTrue(any("headers[0].value must be string" in e for e in result.errors))

    def test_install_mcp_stdio_skips_pip_copies_bundle_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("demo-mcp", Path(tmp), plugin_type="mcp-stdio")
            out_dir = Path(tmp) / "out"
            zip_path = plugin_pack(plugin_root, out_dir)
            inst = Path(tmp) / "install_root"

            with patch("cli_core.plugin.subprocess.run") as m_run:
                m_run.return_value = None
                installed_root = plugin_install(zip_path, extract_dir=inst)

            self.assertTrue((installed_root / "plugin.yaml").exists())
            m_run.assert_not_called()

    def test_install_restful_api_skips_pip_copies_bundle_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("demo-api", Path(tmp), plugin_type="restful-api")
            out_dir = Path(tmp) / "out"
            zip_path = plugin_pack(plugin_root, out_dir)
            inst = Path(tmp) / "install_root"

            with patch("cli_core.plugin.subprocess.run") as m_run:
                m_run.return_value = None
                installed_root = plugin_install(zip_path, extract_dir=inst)

            self.assertTrue((installed_root / "plugin.yaml").exists())
            m_run.assert_not_called()

    def test_install_tools_wheel_only_zip_calls_pip_install_whl(self) -> None:
        """tools 发布包仅含 dist/*.whl 时，install 应调用 pip 将 wheel 装入当前 Python 环境。"""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("demo-wheel", Path(tmp), plugin_type="tools")
            out_dir = Path(tmp) / "out"
            zip_path = plugin_pack(plugin_root, out_dir)
            inst = Path(tmp) / "install_root"

            with patch("cli_core.plugin.subprocess.run") as m_run:
                m_run.return_value = None
                installed_root = plugin_install(zip_path, extract_dir=inst)

            self.assertTrue((installed_root / "plugin.yaml").exists())
            self.assertEqual(m_run.call_count, 1)
            pip_cmd = m_run.call_args[0][0]
            self.assertIn("-m", pip_cmd)
            self.assertIn("pip", pip_cmd)
            i_install = pip_cmd.index("install")
            self.assertEqual(pip_cmd[i_install + 1], "--")
            self.assertTrue(any(str(x).endswith(".whl") for x in pip_cmd))

    def test_info_reads_readme_local(self) -> None:
        """本地插件目录读取（plugin_describe_local），非 CLI 市场接口。"""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("info-demo", Path(tmp))
            info = plugin_describe_local(plugin_root)
            self.assertEqual(info.get("name"), "info-demo")
            self.assertEqual(info.get("version"), "0.0.1")
            self.assertIsNotNone(info.get("readme"))
            self.assertIn("info-demo", info["readme"])

    def test_info_defaults_to_latest_version(self) -> None:
        with patch("cli_core.handlers.resolve_plugin_info_version") as m_resolve:
            m_resolve.return_value = "1.0.0"
            with patch("cli_core.handlers.plugin_info") as m_info:
                m_info.return_value = PluginVersionDetail.model_validate(
                    {"asset_id": "demo-id", "version": "1.0.0", "name": "demo-plugin"}
                )
                code = main(["info", "demo-id", "--market-url", "http://localhost:8000"])
                self.assertEqual(code, 0)
                m_resolve.assert_called_once()
                self.assertIsNone(m_resolve.call_args[0][2])
                m_info.assert_called_once_with("http://localhost:8000", "demo-id", "1.0.0")

    def test_info_from_market(self) -> None:
        """plugin info 通过版本详情 API 拉取摘要字段。"""
        with patch("cli_core.handlers.plugin_info") as m:
            m.return_value = PluginVersionDetail.model_validate(
                {
                    "asset_id": "demo-id",
                    "version": "1.0.0",
                    "asset_type": "plugin",
                    "plugin_type": "tools",
                    "name": "demo-plugin",
                    "display_name": "Demo Plugin",
                    "publisher_id": "u-1",
                    "publisher_name": "Alice",
                    "file_path": "plugins/u-1/demo-id/1.0.0/demo-plugin-1.0.0.zip",
                    "icon_uri": "http://example.com/icon.png",
                    "changelog": "initial release",
                }
            )
            code = main(["info", "demo-id", "--version", "1.0.0", "--market-url", "http://localhost:8000"])
            self.assertEqual(code, 0)
            m.assert_called_once()
            call_kw = m.call_args
            self.assertEqual(call_kw[0][1], "demo-id")
            self.assertEqual(call_kw[0][2], "1.0.0")

    def test_plugin_info_uses_versions_path(self) -> None:
        with patch("cli_core.market.requests.get") as m:
            m.return_value.status_code = 200
            m.return_value.headers = {"content-type": "application/json"}
            m.return_value.json.return_value = {
                "code": 200,
                "message": "ok",
                "data": {
                    "asset_id": "demo-id",
                    "version": "1.0.0",
                    "asset_type": "plugin",
                    "plugin_type": "tools",
                    "name": "demo-plugin",
                    "display_name": "Demo Plugin",
                    "publisher_id": "u-1",
                    "publisher_name": "Alice",
                },
            }
            detail = plugin_info("http://127.0.0.1:8100", "demo-id", "1.0.0")
            self.assertEqual(detail.asset_id, "demo-id")
            called_url = m.call_args[0][0]
            self.assertTrue(called_url.endswith("/api/v1/plugins/demo-id/versions/1.0.0"))

    def test_resolve_market_asset_matches_exact_asset_id(self) -> None:
        with patch("cli_core.market.plugin_search") as m_search:
            m_search.return_value = PluginListResponse(
                page=1,
                page_size=20,
                total=2,
                items=[
                    PluginListItem(asset_id="demo-id-extra", name="x", plugin_type="skill"),
                    PluginListItem(asset_id="demo-id", name="demo", plugin_type="teamskills"),
                ],
            )
            item = resolve_market_asset("http://market.local", "demo-id")
        self.assertEqual(item.asset_id, "demo-id")
        self.assertEqual(item.plugin_type, "swarmskill")

    def test_resolve_skill_like_asset_for_cli_rejects_non_skill_like_type(self) -> None:
        with patch("cli_core.market.plugin_search") as m_search:
            m_search.return_value = PluginListResponse(
                page=1,
                page_size=20,
                total=1,
                items=[PluginListItem(asset_id="demo-id", name="demo", plugin_type="tools")],
            )
            with self.assertRaisesRegex(ValueError, "not a skill-like asset"):
                resolve_skill_like_asset_for_cli("http://market.local", "demo-id")

    def test_plugin_info_missing_fields_not_error(self) -> None:
        with patch("cli_core.market.requests.get") as m:
            m.return_value.status_code = 200
            m.return_value.headers = {"content-type": "application/json"}
            m.return_value.json.return_value = {
                "code": 200,
                "message": "ok",
                "data": {
                    "asset_id": "demo-id",
                    "version": "1.0.0",
                    # display_name / publisher_name 等缺失
                },
            }
            detail = plugin_info("http://127.0.0.1:8100", "demo-id", "1.0.0")
            self.assertEqual(detail.asset_id, "demo-id")
            self.assertEqual(detail.version, "1.0.0")
            self.assertEqual(detail.display_name, "")

    def test_publish_with_system_token(self) -> None:
        """publish 使用 --system-token 时应走 X-System-Token 路径。"""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("publish-sys-demo", Path(tmp))
            out_zip = Path(tmp) / "out"
            zip_path = plugin_pack(plugin_root, out_zip)

            # patch ``plugin.plugin_upload``（即 market 的 multipart 上传），避免真实网络请求。
            with patch.dict(os.environ, {"OPENJIUWEN_USER_TOKEN": ""}, clear=False):
                with patch("cli_core.plugin.plugin_upload") as m:
                    # 提供 --system-token；清空用户 token 环境以免与 system 双鉴权冲突
                    code = main(
                        [
                            "publish",
                            "--file",
                            str(zip_path),
                            "--system-token",
                            "sys-token-123",
                            "--market-url",
                            "http://localhost:8000",
                            "--plugin-version",
                            "1.0.0",
                        ]
                    )
                    self.assertEqual(code, 0)

                    self.assertEqual(m.call_count, 1)
                    # plugin_upload(...) 来自 market 模块，经 plugin 命名空间绑定
                    call_args = m.call_args
                    self.assertEqual(call_args[0][0], "http://localhost:8000")
                    self.assertIsNone(call_args[0][1])
                    self.assertEqual(call_args[0][2], "sys-token-123")
                    self.assertIsInstance(call_args[0][3], PublishRequest)

    def test_publish_requires_plugin_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("publish-nover-demo", Path(tmp))
            out_zip = Path(tmp) / "out"
            zip_path = plugin_pack(plugin_root, out_zip)
            with patch("cli_core.plugin.plugin_upload") as m:
                code = main(
                    [
                        "publish",
                        "--file",
                        str(zip_path),
                        "--system-token",
                        "sys-token-123",
                        "--market-url",
                        "http://localhost:8000",
                    ]
                )
                self.assertEqual(code, 1)
                m.assert_not_called()

    def test_publish_skill_injects_plugin_yaml_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_root = plugin_init("publish-skill-demo", Path(tmp), plugin_type="skill")
            source_plugin_yaml = skill_root / "plugin.yaml"
            self.assertFalse(source_plugin_yaml.exists())

            captured: dict[str, object] = {}

            def _fake_upload(
                _market_url: str,
                _token: str | None,
                _system_token: str | None,
                req: PublishRequest,
                *,
                swarmskill: bool = False,
            ):
                with zipfile.ZipFile(req.zip_path, "r") as zf:
                    names = [n.replace("\\", "/") for n in zf.namelist()]
                    prefix = names[0].split("/", 1)[0]
                    plugin_yaml_bytes = zf.read(f"{prefix}/plugin.yaml")
                    captured["names"] = names
                    captured["plugin_yaml"] = yaml.safe_load(plugin_yaml_bytes.decode("utf-8"))
                return SimpleNamespace(
                    plugin_id="sid-1",
                    name="publish-skill-demo",
                    version="1.2.3",
                    status="published",
                )

            with patch("cli_core.plugin.plugin_upload", side_effect=_fake_upload):
                res = plugin_publish(
                    market_url="http://127.0.0.1:8100",
                    user_token=None,
                    system_token="sys-token-123",
                    publish_input=PublishPluginInput(plugin_path=skill_root, plugin_version="1.2.3"),
                )

            self.assertEqual(getattr(res, "plugin_id"), "sid-1")
            self.assertFalse(source_plugin_yaml.exists())
            names = captured.get("names")
            self.assertIsInstance(names, list)
            self.assertTrue(any(str(n).endswith("/plugin.yaml") for n in names or []))
            plugin_yaml = captured.get("plugin_yaml")
            self.assertIsInstance(plugin_yaml, dict)
            self.assertEqual(plugin_yaml.get("name"), "publish-skill-demo")
            self.assertEqual(plugin_yaml.get("version"), "1.2.3")
            self.assertEqual(plugin_yaml.get("runtime", {}).get("type"), "skill")
            self.assertEqual(plugin_yaml.get("metadata", {}).get("tags"), ["skill"])

    def test_publish_file_injects_plugin_yaml_for_skill_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_root = plugin_init("publish-file-skill", Path(tmp), plugin_type="skill")
            src_zip = plugin_pack(skill_root, Path(tmp) / "out")

            captured: dict[str, object] = {}

            def _fake_upload(
                _market_url: str,
                _token: str | None,
                _system_token: str | None,
                req: PublishRequest,
                *,
                swarmskill: bool = False,
            ):
                with zipfile.ZipFile(req.zip_path, "r") as zf:
                    names = [n.replace("\\", "/") for n in zf.namelist()]
                    prefix = names[0].split("/", 1)[0]
                    captured["names"] = names
                    captured["plugin_yaml"] = yaml.safe_load(zf.read(f"{prefix}/plugin.yaml").decode("utf-8"))
                return SimpleNamespace(
                    plugin_id="sid-file-1",
                    name="publish-file-skill",
                    version="2.0.0",
                    status="published",
                )

            with patch("cli_core.plugin.plugin_upload", side_effect=_fake_upload):
                res = plugin_publish(
                    market_url="http://127.0.0.1:8100",
                    user_token=None,
                    system_token="sys-token-123",
                    publish_input=PublishPluginInput(zip_path=src_zip, plugin_version="2.0.0"),
                )

            self.assertEqual(getattr(res, "plugin_id"), "sid-file-1")
            names = captured.get("names")
            self.assertIsInstance(names, list)
            self.assertTrue(any(str(n).endswith("/plugin.yaml") for n in names or []))
            plugin_yaml = captured.get("plugin_yaml")
            self.assertIsInstance(plugin_yaml, dict)
            self.assertEqual(plugin_yaml.get("name"), "publish-file-skill")
            self.assertEqual(plugin_yaml.get("version"), "2.0.0")
            self.assertEqual(plugin_yaml.get("runtime", {}).get("type"), "skill")

    def test_publish_swarmskill_injects_plugin_yaml_with_skill_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_root = plugin_init("publish-swarm-demo", Path(tmp), plugin_type="swarmskill")
            captured: dict[str, object] = {}

            def _fake_upload(
                _market_url: str,
                _token: str | None,
                _system_token: str | None,
                req: PublishRequest,
                *,
                swarmskill: bool = False,
            ):
                with zipfile.ZipFile(req.zip_path, "r") as zf:
                    names = [n.replace("\\", "/") for n in zf.namelist()]
                    prefix = names[0].split("/", 1)[0]
                    plugin_yaml_bytes = zf.read(f"{prefix}/plugin.yaml")
                    captured["names"] = names
                    captured["plugin_yaml"] = yaml.safe_load(plugin_yaml_bytes.decode("utf-8"))
                return SimpleNamespace(
                    plugin_id="swid-1",
                    name="publish-swarm-demo",
                    version="1.2.3",
                    status="published",
                )

            with patch("cli_core.plugin.plugin_upload", side_effect=_fake_upload):
                res = plugin_publish(
                    market_url="http://127.0.0.1:8100",
                    user_token=None,
                    system_token="sys-token-123",
                    publish_input=PublishPluginInput(plugin_path=skill_root, plugin_version="1.2.3"),
                )

            self.assertEqual(getattr(res, "plugin_id"), "swid-1")
            names = captured.get("names")
            self.assertIsInstance(names, list)
            self.assertTrue(any(str(n).endswith("/plugin.yaml") for n in names or []))
            plugin_yaml = captured.get("plugin_yaml")
            self.assertIsInstance(plugin_yaml, dict)
            self.assertEqual(plugin_yaml.get("name"), "publish-swarm-demo")
            self.assertEqual(plugin_yaml.get("version"), "1.2.3")
            self.assertEqual(plugin_yaml.get("runtime", {}).get("type"), "skill")
            self.assertEqual(plugin_yaml.get("metadata", {}).get("tags"), ["swarmskill"])

    def test_publish_file_injects_plugin_yaml_for_swarmskill_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_root = plugin_init("publish-file-swarm", Path(tmp), plugin_type="swarmskill")
            src_zip = plugin_pack(skill_root, Path(tmp) / "out")

            captured: dict[str, object] = {}

            def _fake_upload(
                _market_url: str,
                _token: str | None,
                _system_token: str | None,
                req: PublishRequest,
                *,
                swarmskill: bool = False,
            ):
                with zipfile.ZipFile(req.zip_path, "r") as zf:
                    names = [n.replace("\\", "/") for n in zf.namelist()]
                    prefix = names[0].split("/", 1)[0]
                    captured["names"] = names
                    captured["plugin_yaml"] = yaml.safe_load(zf.read(f"{prefix}/plugin.yaml").decode("utf-8"))
                return SimpleNamespace(
                    plugin_id="swid-file-1",
                    name="publish-file-swarm",
                    version="2.0.0",
                    status="published",
                )

            with patch("cli_core.plugin.plugin_upload", side_effect=_fake_upload):
                res = plugin_publish(
                    market_url="http://127.0.0.1:8100",
                    user_token=None,
                    system_token="sys-token-123",
                    publish_input=PublishPluginInput(zip_path=src_zip, plugin_version="2.0.0"),
                )

            self.assertEqual(getattr(res, "plugin_id"), "swid-file-1")
            names = captured.get("names")
            self.assertIsInstance(names, list)
            self.assertTrue(any(str(n).endswith("/plugin.yaml") for n in names or []))
            plugin_yaml = captured.get("plugin_yaml")
            self.assertIsInstance(plugin_yaml, dict)
            self.assertEqual(plugin_yaml.get("name"), "publish-file-swarm")
            self.assertEqual(plugin_yaml.get("version"), "2.0.0")
            self.assertEqual(plugin_yaml.get("runtime", {}).get("type"), "skill")

    def test_publish_expect_skill_like_rejects_tools_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("publish-tools-mismatch", Path(tmp), plugin_type="tools")
            src_zip = plugin_pack(plugin_root, Path(tmp) / "out")
            with self.assertRaisesRegex(PublishError, "expected skill/swarmskill package"):
                plugin_publish(
                    market_url="http://127.0.0.1:8100",
                    user_token=None,
                    system_token="sys-token-123",
                    publish_input=PublishPluginInput(
                        zip_path=src_zip,
                        plugin_version="1.0.0",
                        expect_skill_like=True,
                    ),
                )

    def test_delete_rejects_both_token_and_system_token(self) -> None:
        """delete 同时传 --token 与 --system-token 时应直接失败。"""
        with patch("cli_core.market.plugin_delete") as m:
            code = main(
                [
                    "delete",
                    "demo-id",
                    "--market-url",
                    "http://localhost:8000",
                    "--token",
                    "user-token-123",
                    "--system-token",
                    "sys-token-123",
                    "--version",
                    "all",
                ]
            )
            self.assertEqual(code, 1)
            m.assert_not_called()

    def test_pack_tools_wheel_zip(self) -> None:
        """tools 类型：先 build wheel，zip 含元数据 + dist/*.whl，不含 src/ 源码树。"""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("wheel-demo", Path(tmp))
            out_dir = Path(tmp) / "out"
            zip_path = plugin_pack(plugin_root, out_dir)
            self.assertTrue(zip_path.exists())
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
            self.assertTrue(any("plugin.yaml" in n for n in names))
            self.assertTrue(any("schemas/tools.json" in n for n in names))
            self.assertTrue(any("README.md" in n for n in names))
            self.assertTrue(any("dist/" in n and n.endswith(".whl") for n in names))
            norm = [n.replace("\\", "/") for n in names]
            self.assertFalse(any("/src/" in n for n in norm))

    def test_pack_fails_when_plugin_yaml_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "empty"
            root.mkdir(parents=True)
            with self.assertRaises(ValueError) as ctx:
                plugin_pack(root)
            self.assertIn("plugin.yaml", str(ctx.exception))

    def test_pack_fails_when_validation_fails(self) -> None:
        """pack 会先执行校验，校验不通过则不打 zip。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "minimal"
            root.mkdir(parents=True)
            (root / "plugin.yaml").write_text(
                "name: minimal-pack\nversion: 0.1.0\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                plugin_pack(root, Path(tmp) / "out")
            self.assertIn("validation failed", str(ctx.exception))

    def test_plugin_search_maps_plugin_list_query_params(self) -> None:
        """plugin_search 透传 PluginListQuery 相关 query 参数。"""
        with patch("cli_core.market.requests.get") as m:
            m.return_value.status_code = 200
            m.return_value.headers = {"content-type": "application/json"}
            m.return_value.json.return_value = {
                "code": 200,
                "message": "ok",
                "data": {"page": 3, "page_size": 15, "items": [], "total": 0},
            }
            plugin_search(
                "http://127.0.0.1:9",
                PluginListQuery(
                    search_keyword="keyword",
                    plugin_type="tools",
                    publisher_name="Alice",
                    asset_id="asset-1",
                    asset_type="plugin",
                    publisher_id="pub-1",
                    page=3,
                    page_size=15,
                    order_by="create_time",
                    desc=True,
                ),
            )
            m.assert_called_once()
            params = m.call_args[1]["params"]
            self.assertEqual(params["search_keyword"], "keyword")
            self.assertEqual(params["plugin_type"], "tools")
            self.assertEqual(params["publisher_name"], "Alice")
            self.assertEqual(params["asset_id"], "asset-1")
            self.assertEqual(params["asset_type"], "plugin")
            self.assertEqual(params["publisher_id"], "pub-1")
            self.assertEqual(params["page"], 3)
            self.assertEqual(params["page_size"], 15)
            self.assertEqual(params["order_by"], "create_time")
            self.assertTrue(params["desc"])

    def test_plugin_search_desc_true_by_default(self) -> None:
        with patch("cli_core.market.requests.get") as m:
            m.return_value.status_code = 200
            m.return_value.headers = {"content-type": "application/json"}
            m.return_value.json.return_value = {"code": 200, "message": "ok", "data": {"items": [], "total": 0}}
            plugin_search("http://127.0.0.1:9", PluginListQuery(order_by="install_count"))
            self.assertTrue(m.call_args[1]["params"]["desc"])

    def test_plugin_search_desc_true_when_flag_set(self) -> None:
        with patch("cli_core.market.requests.get") as m:
            m.return_value.status_code = 200
            m.return_value.headers = {"content-type": "application/json"}
            m.return_value.json.return_value = {"code": 200, "message": "ok", "data": {"items": [], "total": 0}}
            plugin_search("http://127.0.0.1:9", PluginListQuery(order_by="install_count", desc=True))
            self.assertTrue(m.call_args[1]["params"]["desc"])

    def test_plugin_search_desc_false_when_explicitly_set(self) -> None:
        with patch("cli_core.market.requests.get") as m:
            m.return_value.status_code = 200
            m.return_value.headers = {"content-type": "application/json"}
            m.return_value.json.return_value = {"code": 200, "message": "ok", "data": {"items": [], "total": 0}}
            plugin_search("http://127.0.0.1:9", PluginListQuery(order_by="install_count", desc=False))
            self.assertFalse(m.call_args[1]["params"]["desc"])

    def test_cli_search_logs_legacy_version_line(self) -> None:
        with patch("cli_core.handlers.plugin_search") as m_search:
            m_search.return_value = PluginListResponse(
                page=1,
                page_size=20,
                total=1,
                items=[
                    PluginListItem(
                        asset_id="aid-1",
                        name="demo-skill",
                        plugin_type="skill",
                        latest_version="2.0.0",
                        public_latest_version="1.0.0",
                        all_versions=["1.0.0", "1.0.1", "2.0.0"],
                    )
                ],
            )
            with patch("cli_core.handlers.logger.info") as m_info:
                code = main(["search", "demo", "--market-url", "http://x"])
            self.assertEqual(code, 0)
            self.assertIn(
                (("  - asset_id=%s name=%s version=%s", "aid-1", "demo-skill", "1.0.0"), {}),
                m_info.call_args_list,
            )

    def test_plugin_list_item_parses_all_versions(self) -> None:
        with patch("cli_core.market.requests.get") as m:
            m.return_value.status_code = 200
            m.return_value.headers = {"content-type": "application/json"}
            m.return_value.json.return_value = {
                "code": 200,
                "message": "ok",
                "data": {
                    "page": 1,
                    "page_size": 20,
                    "total": 1,
                    "items": [
                        {
                            "asset_id": "aid-1",
                            "name": "demo-skill",
                            "plugin_type": "skill",
                            "latest_version": "2.0.0",
                            "public_latest_version": "1.0.0",
                            "all_versions": ["1.0.0", "1.0.1", "2.0.0"],
                        }
                    ],
                },
            }
            result = plugin_search("http://127.0.0.1:9", PluginListQuery(search_keyword="demo"))
            self.assertEqual(len(result.items), 1)
            item = result.items[0]
            self.assertEqual(item.all_versions, ["1.0.0", "1.0.1", "2.0.0"])
            self.assertEqual(item.display_version_for_market_search(), "1.0.0")
            count, joined = item.search_versions()
            self.assertEqual(count, 3)
            self.assertEqual(joined, "1.0.0, 1.0.1, 2.0.0")

    def test_plugin_list_item_search_versions_fallback_when_no_all_versions(self) -> None:
        item = PluginListItem(
            asset_id="aid-2",
            name="tools-demo",
            plugin_type="tools",
            latest_version="3.1.0",
            all_versions=[],
        )
        self.assertEqual(item.display_version_for_market_search(), "3.1.0")
        count, joined = item.search_versions()
        self.assertEqual(count, 1)
        self.assertEqual(joined, "3.1.0")

    def test_plugin_list_item_git_commit_version_is_seven_hex(self) -> None:
        short = "1620d4d"
        item = PluginListItem(
            asset_id="aid-git",
            name="git-skill",
            plugin_type="skill",
            latest_version=short,
            public_latest_version=short,
            all_versions=[short],
        )
        self.assertEqual(item.display_version_for_market_search(), short)
        count, joined = item.search_versions()
        self.assertEqual(count, 1)
        self.assertEqual(joined, short)
        self.assertEqual(item.format_version_label(short), short)

    def test_plugin_list_item_git_version_display_as_commit_uses_resolved_sha(self) -> None:
        item = PluginListItem(
            asset_id="aid-git2",
            name="git-skill2",
            plugin_type="skill",
            latest_version="1620d4d",
            public_latest_version="1620d4d",
            all_versions=["1620d4d"],
            git_version_display_as_commit=True,
            resolved_commit_sha="1620d4d47cb1dd005868f4b9a3fc14f7",
        )
        self.assertEqual(item.format_version_label("1620d4d"), "1620d4d")

    def test_plugin_install_download_verifies_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plugin.zip"
            zip_bytes = (
                b"PK\x03\x04\x14\x00\x00\x00\x00\x00\x00\x00\x00\x00"
                b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            )
            digest = hashlib.sha256(zip_bytes).hexdigest()
            meta_resp = SimpleNamespace(
                ok=True,
                status_code=200,
                headers={"content-type": "application/json"},
                json=lambda: {"data": {"download_url": "http://download.local/plugin.zip", "checksum_sha256": digest}},
                text="",
            )
            file_resp = SimpleNamespace(
                ok=True,
                status_code=200,
                headers={"content-type": "application/zip"},
                iter_content=lambda chunk_size=0: [zip_bytes],
                text="",
            )
            with patch("cli_core.market.requests.get", side_effect=[meta_resp, file_resp]) as m_get:
                info = plugin_install_download("http://market.local", "asset-1", out)
            self.assertTrue(out.exists())
            self.assertTrue(info.verified)
            self.assertEqual(info.actual_checksum_sha256, digest)
            self.assertEqual(m_get.call_count, 2)
            metadata_url = m_get.call_args_list[0][0][0]
            self.assertIn("is_cli_download=true", metadata_url)

    def test_plugin_install_download_checksum_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plugin.zip"
            zip_bytes = (
                b"PK\x03\x04\x14\x00\x00\x00\x00\x00\x00\x00\x00\x00"
                b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            )
            meta_resp = SimpleNamespace(
                ok=True,
                status_code=200,
                headers={"content-type": "application/json"},
                json=lambda: {
                    "data": {
                        "download_url": "http://download.local/plugin.zip",
                        "checksum_sha256": "0" * 64,
                    }
                },
                text="",
            )
            file_resp = SimpleNamespace(
                ok=True,
                status_code=200,
                headers={"content-type": "application/zip"},
                iter_content=lambda chunk_size=0: [zip_bytes],
                text="",
            )
            with patch("cli_core.market.requests.get", side_effect=[meta_resp, file_resp]):
                with self.assertRaises(RuntimeError) as ctx:
                    plugin_install_download("http://market.local", "asset-1", out)
            self.assertIn("checksum mismatch", str(ctx.exception))
            self.assertFalse(out.exists())

    def test_validate_rejects_omitted_runtime_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("omit-rt", Path(tmp))
            py = plugin_root / "plugin.yaml"
            data = yaml.safe_load(py.read_text(encoding="utf-8"))
            assert isinstance(data, dict) and isinstance(data.get("runtime"), dict)
            del data["runtime"]["type"]
            py.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
            result = plugin_validate(plugin_root)
            self.assertFalse(result.ok)
            self.assertTrue(any("runtime.type" in e for e in result.errors))

    def test_validate_rejects_unknown_runtime_type(self) -> None:
        """显式填写未知 runtime.type 须报错；未默认成 tools。"""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("rt-unknown", Path(tmp))
            py = plugin_root / "plugin.yaml"
            data = yaml.safe_load(py.read_text(encoding="utf-8"))
            assert isinstance(data, dict) and isinstance(data.get("runtime"), dict)
            data["runtime"]["type"] = "custom-unknown"
            py.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
            result = plugin_validate(plugin_root)
            self.assertFalse(result.ok)
            self.assertTrue(any("runtime.type" in e for e in result.errors))

    def test_validate_flat_skill_without_frontmatter_name_skips_plugin_yaml_noise(self) -> None:
        """Flat bundle with SKILL.md but missing ``name`` must not complain about plugin.yaml/README."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text(
                "---\n"
                'description: "only desc"\n'
                "---\n\n"
                "# Skill body\n",
                encoding="utf-8",
            )
            result = plugin_validate(root)
            self.assertFalse(result.ok)
            self.assertFalse(any("plugin.yaml" in e for e in result.errors))
            self.assertFalse(any("README.md" in e for e in result.errors))
            self.assertTrue(any("SKILL.md frontmatter name" in e for e in result.errors))

    def test_validate_skill_fails_invalid_frontmatter_name_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("slug-skill", Path(tmp), plugin_type="skill")
            skill_md = plugin_root / "SKILL.md"
            text = skill_md.read_text(encoding="utf-8").replace("name: slug-skill", "name: Bad_Name")
            skill_md.write_text(text, encoding="utf-8")
            result = plugin_validate(plugin_root)
            self.assertFalse(result.ok)
            self.assertTrue(any("SKILL.md" in e or "name" in e for e in result.errors))

    def test_validate_skill_fails_when_frontmatter_name_differs_from_directory(self) -> None:
        """Nested layout only: slug-shaped directory must match ``name`` in SKILL.md."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = Path(tmp) / "proj"
            nested = plugin_root / "same-skill"
            nested.mkdir(parents=True)
            skill_md = nested / "SKILL.md"
            skill_md.write_text(
                "---\nname: same-skill\ndescription: ok\n---\n\n# x\n",
                encoding="utf-8",
            )
            text = skill_md.read_text(encoding="utf-8").replace("name: same-skill", "name: other-skill")
            skill_md.write_text(text, encoding="utf-8")
            result = plugin_validate(plugin_root)
            self.assertFalse(result.ok)
            self.assertTrue(any("skill directory name" in e or "directory name" in e for e in result.errors))

    def test_validate_skill_fails_empty_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("desc-skill", Path(tmp), plugin_type="skill")
            skill_md = plugin_root / "SKILL.md"
            text = skill_md.read_text(encoding="utf-8")
            text = text.replace(
                'description: "TODO: describe this skill for models and users"',
                'description: "   "',
            )
            skill_md.write_text(text, encoding="utf-8")
            result = plugin_validate(plugin_root)
            self.assertFalse(result.ok)
            self.assertTrue(any("description" in e.lower() for e in result.errors))

    def test_install_second_install_without_force_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("twice-mcp", Path(tmp), plugin_type="mcp-stdio")
            zip_path = plugin_pack(plugin_root, Path(tmp) / "out")
            inst = Path(tmp) / "install_root"
            with patch("cli_core.plugin.subprocess.run") as m_run:
                m_run.return_value = None
                plugin_install(zip_path, extract_dir=inst)
                m_run.reset_mock()
                with self.assertRaises(FileExistsError):
                    plugin_install(zip_path, extract_dir=inst, force=False)

    def test_install_second_install_with_force_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("force-mcp", Path(tmp), plugin_type="mcp-stdio")
            zip_path = plugin_pack(plugin_root, Path(tmp) / "out")
            inst = Path(tmp) / "install_root"
            with patch("cli_core.plugin.subprocess.run") as m_run:
                m_run.return_value = None
                p1 = plugin_install(zip_path, extract_dir=inst)
                p2 = plugin_install(zip_path, extract_dir=inst, force=True)
            self.assertEqual(p1, p2)
            self.assertTrue((p2 / "plugin.yaml").is_file())

    def test_cli_init_skill_via_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "cli-skill", "--path", tmp, "--type", "skill"])
            self.assertEqual(code, 0)
            root = Path(tmp) / "cli-skill"
            self.assertTrue((root / "SKILL.md").is_file())

    def test_init_skill_allows_leading_digit_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = plugin_init("1st-skill", Path(tmp), plugin_type="skill")
            self.assertTrue((plugin_root / "SKILL.md").is_file())

    def test_init_rejects_unknown_plugin_type_from_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                plugin_init("x", Path(tmp), plugin_type="not-a-supported-type")
            self.assertIn("plugin type", str(ctx.exception).lower())

    def test_runtime_type_accepts_skill_runtime_for_team_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "legacy-team"
            root.mkdir()
            (root / "plugin.yaml").write_text(
                yaml.safe_dump(
                    {
                        "name": "legacy-team",
                        "version": "1.0.0",
                        "display_name": "Legacy Team",
                        "description": "demo",
                        "runtime": {"type": "skill"},
                        "metadata": {"author": "tester", "tags": ["demo"]},
                    },
                    sort_keys=False,
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            (root / "SKILL.md").write_text(
                "---\n"
                "name: legacy-team\n"
                'description: "demo"\n'
                "kind: team-skill\n"
                "roles:\n"
                "  - id: id_01\n"
                "  - id: id_02\n"
                "---\n\n"
                "# Legacy Team\n",
                encoding="utf-8",
            )
            result = plugin_validate(root)
            self.assertTrue(result.ok, msg=f"errors: {result.errors}")
            self.assertEqual(result.runtime_type, "skill")

    def _skill_import_ok_response(self) -> SkillImportResponse:
        return SkillImportResponse(
            summary=SkillImportSummary(total=1, ok=1, failed=0),
            results=[
                SkillImportItemResult(
                    entry="skill-a",
                    status="ok",
                    plugin_id="pid-1",
                    name="skill-a",
                    version="0.0.1",
                )
            ],
        )

    def test_skill_import_cli_zip_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bundle.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("skill-a/SKILL.md", b"# x")
            with patch(
                "cli_core.handlers.skill_import",
                return_value=self._skill_import_ok_response(),
            ) as m:
                code = main(
                    [
                        "skill-import",
                        str(zip_path),
                        "--market-url",
                        "http://localhost:8000",
                        "--system-token",
                        "sys-tok",
                    ]
                )
            self.assertEqual(code, 0)
            m.assert_called_once()
            kw = m.call_args.kwargs
            self.assertEqual(kw["zip_path"].resolve(), zip_path.resolve())
            self.assertFalse(kw["force"])
            self.assertFalse(kw["fail_fast"])

    def test_skill_import_cli_passes_force_and_fail_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bundle.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("x.txt", b"y")
            with patch(
                "cli_core.handlers.skill_import",
                return_value=self._skill_import_ok_response(),
            ) as m:
                code = main(
                    [
                        "skill-import",
                        str(zip_path),
                        "--market-url",
                        "http://localhost:8000",
                        "--system-token",
                        "t",
                        "--force",
                        "--fail-fast",
                    ]
                )
            self.assertEqual(code, 0)
            kw = m.call_args.kwargs
            self.assertTrue(kw["force"])
            self.assertTrue(kw["fail_fast"])

    def test_skill_import_cli_directory_packs_then_uploads_zip(self) -> None:
        """目录会先打成临时 zip 再上传；请求里的 zip_path 应为该临时文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp) / "my-bundle"
            bundle_dir.mkdir()
            (bundle_dir / "a.txt").write_text("hi", encoding="utf-8")
            seen_zip: list[Path] = []

            def _upload(_market_url: str, _system_token: str, **kwargs):
                seen_zip.append(Path(kwargs["zip_path"]))
                return self._skill_import_ok_response()

            with patch("cli_core.handlers.skill_import", side_effect=_upload):
                code = main(
                    [
                        "skill-import",
                        str(bundle_dir),
                        "--market-url",
                        "http://localhost:8000",
                        "--system-token",
                        "t",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(len(seen_zip), 1)
            z = seen_zip[0]
            self.assertTrue(z.name.endswith(".zip"))
            self.assertNotEqual(z.resolve(), bundle_dir.resolve())
            # 打包目录在 finally 中删除后，临时 zip 不应仍存在
            self.assertFalse(z.is_file())

    def test_skill_import_requires_market_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "b.zip"
            zip_path.write_bytes(b"PK\x03\x04")
            with patch.dict(os.environ, {"OPENJIUWEN_MARKET_URL": ""}, clear=False):
                code = main(["skill-import", str(zip_path), "--system-token", "t"])
            self.assertEqual(code, 1)

    def test_skill_import_requires_system_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "b.zip"
            zip_path.write_bytes(b"PK\x03\x04")
            with patch.dict(os.environ, {"OPENJIUWEN_SYSTEM_TOKEN": ""}, clear=False):
                code = main(
                    ["skill-import", str(zip_path), "--market-url", "http://127.0.0.1:9"],
                )
            self.assertEqual(code, 1)

    def test_skill_import_bundle_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "absent.zip"
            code = main(
                [
                    "skill-import",
                    str(missing),
                    "--market-url",
                    "http://127.0.0.1:9",
                    "--system-token",
                    "t",
                ]
            )
        self.assertEqual(code, 1)

    def test_skill_import_publish_error_exits_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bundle.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("x", b"y")
            with patch(
                "cli_core.handlers.skill_import",
                side_effect=PublishError(403, "forbidden"),
            ):
                code = main(
                    [
                        "skill-import",
                        str(zip_path),
                        "--market-url",
                        "http://localhost:8000",
                        "--system-token",
                        "t",
                    ]
                )
        self.assertEqual(code, 1)

    def test_skill_import_exits_1_when_summary_has_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bundle.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("x", b"y")
            resp = SkillImportResponse(
                summary=SkillImportSummary(total=2, ok=1, failed=1),
                results=[
                    SkillImportItemResult(entry="a", status="ok"),
                    SkillImportItemResult(entry="b", status="error", error="x", message="m"),
                ],
            )
            with patch("cli_core.handlers.skill_import", return_value=resp):
                code = main(
                    [
                        "skill-import",
                        str(zip_path),
                        "--market-url",
                        "http://localhost:8000",
                        "--system-token",
                        "t",
                    ]
                )
        self.assertEqual(code, 1)

    def test_skill_import_rejects_oversize_zip_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "big.zip"
            zip_path.write_bytes(b"x" * 20)
            with patch("cli_core.handlers.SKILL_IMPORT_BUNDLE_MAX_BYTES", 10):
                code = main(
                    [
                        "skill-import",
                        str(zip_path),
                        "--market-url",
                        "http://localhost:8000",
                        "--system-token",
                        "t",
                    ]
                )
        self.assertEqual(code, 1)

    def test_skill_import_empty_directory_pack_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty-bundle"
            empty.mkdir()
            with patch("cli_core.handlers.skill_import") as m:
                code = main(
                    [
                        "skill-import",
                        str(empty),
                        "--market-url",
                        "http://localhost:8000",
                        "--system-token",
                        "t",
                    ]
                )
                m.assert_not_called()
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
