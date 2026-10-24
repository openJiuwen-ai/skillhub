# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""日志脱敏覆盖 exc_info，以及含特殊字符的凭证和连接串。"""

from __future__ import annotations

import io
import logging
import sys

from log_redaction import SensitiveLogFilter, redact_sensitive_text


def test_secret_kv_keeps_base64_and_punctuation() -> None:
    assert redact_sensitive_text("api_key=abc+def/ghi==jkl") == "api_key=***"
    assert redact_sensitive_text("password=P@ssw0rd!2026") == "password=***"
    assert redact_sensitive_text('{"api_key":"sk-abc+def/ghi=="}') == '{"api_key":"***"}'


def test_url_password_with_at_is_redacted() -> None:
    raw = "mysql+pymysql://root:Se@cr3t!@10.0.0.1:3306/db"
    assert redact_sensitive_text(raw) == "mysql+pymysql://root:***@10.0.0.1:3306/db"


def test_exc_info_and_stack_are_redacted() -> None:
    filt = SensitiveLogFilter()
    try:
        raise RuntimeError(
            "connect failed: mysql+pymysql://root:Se@cr3t!@10.0.0.1:3306/db "
            "api_key=sk-REALKEY123456"
        )
    except RuntimeError:
        record = logging.LogRecord(
            name="t",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="DB 连接异常",
            args=(),
            exc_info=sys.exc_info(),
        )
    record.stack_info = "stack password=P@ssw0rd!2026"
    assert filt.filter(record) is True
    assert record.exc_text is not None
    assert "Se@cr3t!" not in record.exc_text
    assert "sk-REALKEY123456" not in record.exc_text
    assert "P@ssw0rd!2026" not in record.stack_info
    assert "***" in record.exc_text


def test_percent_encoded_secret_is_fully_redacted() -> None:
    assert redact_sensitive_text("api_key=abc%2Fdef+/=") == "api_key=***"


def test_format_placeholder_is_interpolated_before_redaction(capsys) -> None:
    """api_key=%s 不能在插值前被擦掉，否则 Formatter 抛 TypeError，日志被丢掉。"""
    logger = logging.getLogger("skill_runner.redaction_format")
    logger.handlers.clear()
    logger.filters.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.addFilter(SensitiveLogFilter())

    logger.info("api_key=%s", "abc%2Fdef+/=")
    logger.info("api_key=%(key)s", {"key": "sk-REALKEY123456"})

    captured = capsys.readouterr()
    assert "TypeError" not in captured.err
    assert "not all arguments converted" not in captured.err
    lines = [line for line in buf.getvalue().splitlines() if line]
    assert lines == ["api_key=***", "api_key=***"]
    assert "abc%2Fdef" not in buf.getvalue()
    assert "sk-REALKEY123456" not in buf.getvalue()
