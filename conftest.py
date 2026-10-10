""""""
from __future__ import annotations

import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SR = _ROOT / "skill-runner"

if _SR.is_dir() and "skill_runner" not in sys.modules:
    _pkg = types.ModuleType("skill_runner")
    _pkg.__path__ = [str(_SR)]
    sys.modules["skill_runner"] = _pkg
