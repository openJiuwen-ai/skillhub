# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pytest hooks: stub optional runtime deps missing in lightweight test env."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


def _ensure_skill_review_stub() -> None:
    if "skill_review.model.openai_compatible" in sys.modules:
        return
    stub = MagicMock()
    sys.modules.setdefault("skill_review", stub)
    sys.modules.setdefault("skill_review.model", stub)
    sys.modules.setdefault("skill_review.model.openai_compatible", stub)


_ensure_skill_review_stub()
