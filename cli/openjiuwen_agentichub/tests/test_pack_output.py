# SPDX-License-Identifier: Apache-2.0

import hashlib
import zipfile
from pathlib import Path

import pytest

from cli_core.plugin import plugin_init, plugin_pack
from openjiuwen_agentichub.main import main


ASSET_TYPES = (
    "skill",
    "swarmskill",
    "agent-plugin",
    "agent-mcp",
    "agent-template",
    "agent-group",
)


def _archive_contents(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


@pytest.mark.parametrize("asset_type", ASSET_TYPES)
@pytest.mark.parametrize("output_mode", ("relative", "absolute", "root", "external", "ancestor"))
def test_pack_preserves_source_and_excludes_generated_output(
    tmp_path: Path, asset_type: str, output_mode: str
) -> None:
    root = plugin_init("pack-demo", tmp_path / "sources", plugin_type=asset_type)
    source = root if asset_type in {"skill", "swarmskill"} else root / root.name
    # ZIP 素材和名称相近的目录都属于需要保留的源文件。
    with zipfile.ZipFile(source / "reference.zip", "w") as archive:
        archive.writestr("reference.txt", "source asset")
    sibling = source / "build-artifacts-extra"
    sibling.mkdir()
    (sibling / "notes.txt").write_text("keep this source", encoding="utf-8")
    expected = _archive_contents(plugin_pack(root, tmp_path / "baseline"))

    if output_mode in {"relative", "absolute"}:
        output = root / "build-artifacts"
        output_arg = "build-artifacts" if output_mode == "relative" else str(output)
    elif output_mode == "root":
        output = root
        output_arg = "."
    elif output_mode == "ancestor":
        output = root.parent
        output_arg = str(output)
    else:
        output = tmp_path / "external-output"
        output_arg = str(output)
    output.mkdir(parents=True, exist_ok=True)
    if output_mode in {"relative", "absolute"}:
        (output / "old.zip").write_bytes(b"previous artifact")
        (output / "old.zip.sha256").write_text("previous checksum", encoding="utf-8")
        (output / "build.log").write_text("generated output", encoding="utf-8")

    zip_name = "pack-demo.zip" if asset_type in {"skill", "swarmskill"} else "pack-demo-0.0.1.zip"
    archive_path = output / zip_name
    checksum_path = archive_path.with_suffix(".zip.sha256")
    for _ in range(2):
        assert main(["pack", str(root), "-o", output_arg]) == 0
        assert _archive_contents(archive_path) == expected
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        assert checksum_path.read_text(encoding="utf-8") == f"{digest}  {zip_name}\n"


@pytest.mark.parametrize("asset_type", ("skill", "swarmskill"))
def test_pack_excludes_custom_output_inside_nested_skill(tmp_path: Path, asset_type: str) -> None:
    workspace = tmp_path / "workspace"
    root = plugin_init("nested-skill", workspace, plugin_type=asset_type)
    expected = _archive_contents(plugin_pack(workspace, tmp_path / "baseline"))
    output = root / "build-artifacts"
    output.mkdir()
    (output / "old.zip").write_bytes(b"previous artifact")

    for _ in range(2):
        archive = plugin_pack(workspace, output)
        assert _archive_contents(archive) == expected
