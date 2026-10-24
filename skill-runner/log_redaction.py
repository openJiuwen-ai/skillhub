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
# key=value / key: value。值取到空白或分隔符为止，覆盖 base64 / JWT 里的 + / =，
# 以及百分号编码。含 %，所以不能直接套到尚未插值的 logging 格式串上。
_SECRET_KV = re.compile(
    r"(?i)(api[_-]?key|secret|access[_-]?key|password|passwd|token)"
    r"([\"']?\s*[=:]\s*[\"']?)"
    r"([^\s\"',;&]+)"
)
# scheme://user:password@host。密码可含 @，取到该段最后一个 @ 之前。
_URL_CREDENTIAL = re.compile(
    r"(?i)(\b[a-z][a-z0-9+.\-]*://[^/\s:]+:)(\S*)(@[^/\s@]+)"
)


def redact_sensitive_text(text: str) -> str:
    if not text:
        return text
    out = _SENSITIVE_QUERY_PARAM.sub(r"\1***", text)
    out = _BEARER_TOKEN.sub(r"\1***", out)
    out = _POD_TOKEN.sub("pod-***", out)
    out = _URL_CREDENTIAL.sub(r"\1***\3", out)
    out = _SECRET_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}***", out)
    return out


class SensitiveLogFilter(logging.Filter):
    """挂到 logger / handler 上，逐条擦掉日志里的凭证。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # args 仅在 tuple 即 %-位置参数时逐个处理；dict 即 %(name)s 原样放行，
            # 避免把 dict 误转成 tuple 破坏格式化。
            if isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sensitive_text(a) if isinstance(a, str) else a
                    for a in record.args
                )
            # msg 在 Formatter 之前仍是格式串。_SECRET_KV 的值里包含 %，
            # 若把 api_key=%s 擦成 api_key=***，随后 msg % args 会 TypeError，
            # 整条日志被 handleError 丢掉并打到 stderr。先按 logging 规则插值，
            # 再对成品脱敏，并清掉 args，避免 Formatter 再格式化一次。
            if isinstance(record.msg, str) and record.args:
                try:
                    rendered = record.getMessage()
                except Exception as exc:
                    # 插值失败就保留原格式串，交给 Formatter 报原始错误；这里只记一笔，避免吞掉异常。
                    _log.debug("log message format failed, leave format string: %s", exc)
                else:
                    record.msg = rendered
                    record.args = None
            if isinstance(record.msg, str) and not record.args:
                record.msg = redact_sensitive_text(record.msg)
            # Formatter 会复用已生成的 exc_text；只改 exc_info 挡不住后续 handler。
            if record.exc_info and record.exc_info != (None, None, None):
                if not record.exc_text:
                    record.exc_text = logging.Formatter().formatException(record.exc_info)
                record.exc_text = redact_sensitive_text(record.exc_text)
            if isinstance(record.stack_info, str) and record.stack_info:
                record.stack_info = redact_sensitive_text(record.stack_info)
        except Exception as exc:
            _log.debug("log redaction failed: %s", exc)
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
