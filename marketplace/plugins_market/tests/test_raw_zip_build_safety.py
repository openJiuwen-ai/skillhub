"""raw.zip 重打包：解压前必须经过 zip 安全校验（与 agent-asset 分支保持一致）。"""

from __future__ import annotations

import ast
import io
import zipfile
from pathlib import Path

import pytest

from plugins_market.validation.zip_utils import validate_zip_safety

_PLUGIN_SERVICE = Path(__file__).resolve().parents[1] / "services" / "plugin.py"


def _build_zip(entries: dict[str, str]) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


class TestRawZipBuildSafety:
    @staticmethod
    def test_traversal_member_is_rejected():
        zf = _build_zip({"../evil.txt": "pwn", "skill/SKILL.md": "# demo"})
        with pytest.raises(Exception):
            validate_zip_safety(zf)

    @staticmethod
    def test_clean_archive_passes():
        zf = _build_zip({"skill/SKILL.md": "# demo", "skill/run.py": "print(1)"})
        validate_zip_safety(zf)  # 不抛异常

    @staticmethod
    def test_both_raw_zip_builders_validate_before_extract():
        tree = ast.parse(_PLUGIN_SERVICE.read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in ("_build_raw_zip_from_original", "_build_agent_asset_raw_zip_from_original"):
            fn = functions[name]
            calls = {
                getattr(call.func, "id", getattr(call.func, "attr", ""))
                for call in ast.walk(fn)
                if isinstance(call, ast.Call)
            }
            assert "validate_zip_safety" in calls, f"{name} no longer validates the archive"
