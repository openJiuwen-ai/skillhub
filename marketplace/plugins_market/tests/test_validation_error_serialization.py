# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""校验错误响应的可序列化性（issue #173 根因回归）。

根因：请求未带 ``Content-Type: application/json``（被当作 form-urlencoded）时，
FastAPI 把 body 解析失败抛 ``RequestValidationError``，其 ``input`` 字段为原始
body 的 ``bytes``。``validation_error_handler`` 将其放进 422 响应 payload，
``json.dumps`` 对 ``bytes`` 抛 ``TypeError``，被全局兜底处理器转成 500。
修复：在 ``validation_error_payload`` 中用 ``_json_safe`` 清洗 details，使 422
响应始终可序列化。
"""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from plugins_market.core.errors import validation_error_payload
from plugins_market.core.http_error_logging import register_exception_handlers
from plugins_market.core.logging import get_logger


class _Body(BaseModel):
    name: str | None = None


def _app_with_handlers() -> FastAPI:
    app = FastAPI()

    @app.patch("/groups/{group_id}")
    def update_group(group_id: str, body: _Body):
        return {"ok": True}

    register_exception_handlers(app, logger=get_logger("test_validation_error"))
    return app


def test_validation_error_payload_serializes_bytes_input():
    """validation details 里的 bytes input 必须可被 JSON 序列化。"""
    details = [
        {
            "type": "model_attributes_type",
            "loc": ("body",),
            "msg": "Input should be a valid dictionary or object to extract fields from",
            "input": b'{"name":"x"}',
        }
    ]
    payload = validation_error_payload(message="请求参数校验失败", details=details)

    serialized = json.dumps(payload)  # 修复前此处抛 TypeError
    again = json.loads(serialized)
    assert again["error"] == "validation_error"
    assert again["http_status"] == 422
    # bytes 已被解码为字符串，不再是不可序列化的 bytes
    assert again["details"][0]["input"] == '{"name":"x"}'


def test_form_urlencoded_body_returns_422_not_500():
    """缺/错 Content-Type（form-urlencoded）时 body 校验失败应返回 422，而非 500。"""
    app = _app_with_handlers()
    client = TestClient(app, raise_server_exceptions=False)

    r = client.patch(
        "/groups/grp1",
        content='{"name":"x"}',
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["detail"]["error"] == "validation_error"
    assert body["detail"]["http_status"] == 422

    # 正常 application/json 不回归
    r2 = client.patch(
        "/groups/grp1",
        content='{"name":"x"}',
        headers={"Content-Type": "application/json"},
    )
    assert r2.status_code == 200
