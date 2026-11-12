# SPDX-License-Identifier: Apache-2.0

import codecs
import hashlib
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from cli_core.plugin import plugin_init, plugin_pack, plugin_validate
from cli_core.schemas import PluginPublishResult, PublishRequest
from openjiuwen_agentichub.main import main


@pytest.mark.parametrize("asset_type", ("skill", "swarmskill"))
@pytest.mark.parametrize("nested", (False, True))
@pytest.mark.parametrize("newline", ("\n", "\r\n"))
def test_bom_skill_validate_pack_publish(
    tmp_path: Path, monkeypatch, asset_type: str, nested: bool, newline: str
) -> None:
    workspace = tmp_path / "workspace"
    skill_root = plugin_init("encoding-demo", workspace, plugin_type=asset_type)
    root = workspace if nested else skill_root
    skill_md = skill_root / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8") + "\n## 使用说明\n保留中文正文。\n"
    original = codecs.BOM_UTF8 + text.replace("\n", newline).encode("utf-8")
    skill_md.write_bytes(original)

    result = plugin_validate(root)
    assert result.ok, result.errors
    assert result.runtime_type == asset_type
    assert main(["validate", str(root)]) == 0
    archive_path = plugin_pack(root, tmp_path / "packed")
    with zipfile.ZipFile(archive_path) as archive:
        member = next(name for name in archive.namelist() if name.endswith("/SKILL.md"))
        assert archive.read(member) == original

    def upload(_url, _token, _system_token, request: PublishRequest) -> PluginPublishResult:
        # 仅替代最后的网络上传，保留校验、打包和发布归一化流程。
        with zipfile.ZipFile(request.zip_path) as archive:
            member = next(name for name in archive.namelist() if name.endswith("/SKILL.md"))
            assert archive.read(member) == original
        assert request.checksum_sha256 == hashlib.sha256(request.zip_path.read_bytes()).hexdigest()
        return PluginPublishResult(plugin_id="demo-id", name="encoding-demo", version="1.0.0")

    monkeypatch.delenv("OPENJIUWEN_USER_TOKEN", raising=False)
    with patch("cli_core.plugin.plugin_upload", side_effect=upload) as mocked_upload:
        assert main([
            "publish", str(root), "--system-token", "test-token",
            "--market-url", "http://example.test", "--plugin-version", "1.0.0",
        ]) == 0
        mocked_upload.assert_called_once()
    assert skill_md.read_bytes() == original
    assert not (skill_root / "plugin.yaml").exists()


@pytest.mark.parametrize("nested", (False, True))
@pytest.mark.parametrize("invalid_bytes", (b"\xff", codecs.BOM_UTF16_LE + b"\x00"))
def test_invalid_skill_encoding_returns_validation_error(
    tmp_path: Path, monkeypatch, caplog, nested: bool, invalid_bytes: bytes
) -> None:
    workspace = tmp_path / "workspace"
    skill_root = plugin_init("encoding-demo", workspace)
    root = workspace if nested else skill_root
    skill_md = skill_root / "SKILL.md"
    skill_md.write_bytes(skill_md.read_bytes() + invalid_bytes)

    result = plugin_validate(root)
    assert not result.ok
    assert any("SKILL.md" in error and "UTF-8" in error for error in result.errors)
    assert not any("missing required entry" in error for error in result.errors)
    assert main(["validate", str(root)]) == 1
    assert main(["pack", str(root), "-o", str(tmp_path / "packed")]) == 1

    monkeypatch.delenv("OPENJIUWEN_USER_TOKEN", raising=False)
    with patch("cli_core.plugin.plugin_upload") as mocked_upload:
        assert main([
            "publish", str(root), "--system-token", "test-token",
            "--market-url", "http://example.test", "--plugin-version", "1.0.0",
        ]) == 1
        mocked_upload.assert_not_called()
    assert "Traceback" not in caplog.text
    assert not (tmp_path / "packed").exists()


@pytest.mark.parametrize(("text", "error_fragment"), (
    ("---\nname: [\n---\n", "invalid SKILL.md frontmatter YAML"),
    ("---\n- one\n---\n", "frontmatter must be a mapping"),
    ("---\nname: encoding-demo\n", "frontmatter not closed"),
))
def test_bom_does_not_bypass_frontmatter_validation(
    tmp_path: Path, text: str, error_fragment: str
) -> None:
    root = tmp_path / "encoding-demo"
    root.mkdir()
    (root / "SKILL.md").write_bytes(codecs.BOM_UTF8 + text.encode("utf-8"))
    result = plugin_validate(root)
    assert not result.ok
    assert any(error_fragment in error for error in result.errors)
