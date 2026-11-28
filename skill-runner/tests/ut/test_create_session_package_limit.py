# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import base64
import dataclasses

from fastapi.testclient import TestClient

from skill_runner import app as app_module
from skill_runner.config import settings


def test_package_over_limit_rejected_with_413(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module, "settings", dataclasses.replace(settings, package_max_bytes=16)
    )
    client = TestClient(app_module.app)
    big = base64.b64encode(b"x" * 32).decode("ascii")
    resp = client.post(
        "/api/v1/playground/sessions",
        json={"skill_id": "s1", "skill_md": "x", "package_bytes_b64": big},
    )
    assert resp.status_code == 413


def test_decoded_size_over_limit_rejected_with_413(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module, "settings", dataclasses.replace(settings, package_max_bytes=17)
    )
    client = TestClient(app_module.app)
    # 18 字节解码后超限，但 base64 编码长度 24 字符不超编码态闸门 (17//3+1)*4=24
    encoded = base64.b64encode(b"x" * 18).decode("ascii")
    assert len(encoded) == 24
    resp = client.post(
        "/api/v1/playground/sessions",
        json={"skill_id": "s1", "skill_md": "x", "package_bytes_b64": encoded},
    )
    assert resp.status_code == 413
