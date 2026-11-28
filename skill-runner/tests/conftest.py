# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Register skill_runner as a package for tests rooted inside skill-runner/."""

from __future__ import annotations

import sys
import types
from pathlib import Path

_SR = Path(__file__).resolve().parents[1]

if _SR.is_dir() and "skill_runner" not in sys.modules:
    _pkg = types.ModuleType("skill_runner")
    _pkg.__path__ = [str(_SR)]
    sys.modules["skill_runner"] = _pkg
