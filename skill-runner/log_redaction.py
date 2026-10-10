# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""skill-runner 日志脱敏。

控制面持真 LLM key、worker 持 per-pod token，日志里大量出现 Bearer/api_key/
pod-token/URL query secret。本模块挂一个 logging.Filter 把这些串替换成 ***，避免凭证经日志外泄。
"""
from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

# URL query 里的敏感参数：?token=xxx&api_key=yyy
_SENSITIVE_QUERY_PARAM = re.compile(
    r"([?&](?:access_token|token|password|client_secret|api_key|secret|refresh_token)=)"
    r"([^&\s\"']+)",
    re.IGNORECASE,
)
# Bearer <token>
_BEARER_TOKEN = re.compile(r"(Bearer\s+)([^\s\"']+)", re.IGNORECASE)
# llm_proxy 签发的 pod token，格式 pod- + token_urlsafe
_POD_TOKEN = re.compile(r"\bpod-[A-Za-z0-9._\-]{8,}")
# key=value / key: value 形式
_SECRET_KV = re.compile(
    r"(?i)(api[_-]?key|secret|access[_-]?key|password|passwd|token)"
    r"([\"']?\s*[=:]\s*[\"']?)([A-Za-z0-9._\-]{6,})"
)


def redact_sensitive_text(text: str) -> str:
    if not text:
        return text
    out = _SENSITIVE_QUERY_PARAM.sub(r"\1***", text)
    out = _BEARER_TOKEN.sub(r"\1***", out)
    out = _POD_TOKEN.sub("pod-***", out)
    out = _SECRET_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}***", out)
    return out


class SensitiveLogFilter(logging.Filter):
    """挂到 logger / handler 上，逐条擦掉日志里的凭证。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_sensitive_text(record.msg)
            # args 仅在 tuple 即 %-位置参数时逐个处理；dict 即 %(name)s 原样放行，
            # 避免把 dict 误转成 tuple 破坏格式化。
            if isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sensitive_text(a) if isinstance(a, str) else a
                    for a in record.args
                )
        except Exception as exc:
            _log.debug("log redaction failed: %s", exc, exc_info=True)
        return True


_CONFIGURED = False


def configure_sensitive_log_redaction() -> None:
    """幂等挂载脱敏 filter。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    filt = SensitiveLogFilter()
    for name in ("skill_runner", "httpx", "httpcore", "uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addFilter(filt)
    for handler in logging.getLogger().handlers:
        handler.addFilter(filt)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
