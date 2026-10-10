# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
import logging
import os
import re
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from typing import Optional

from plugins_market.core.context import now_bj_iso
from plugins_market.core.logging import _get_log_backup_count, _get_log_max_bytes

_interface_logger = logging.getLogger("interface")


def get_interface_logger() -> logging.Logger:
    return _interface_logger

_SECRET_KEY_PATTERNS = [
    re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api_key|apikey|access_key|secret_key)\b\s*[:=]\s*[^\s,}\]]+"),
    re.compile(r"(?i)\bauthorization\b\s*[:=]\s*[^\s,}\]]+"),
]

_PHONE_PATTERN = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
_ID_CARD_PATTERN = re.compile(r"(?<!\d)(\d{3})\d{11}(\d{4})(?!\d)")
_BANK_CARD_PATTERN = re.compile(r"(?<!\d)(\d{6})\d{5,9}(\d{4})(?!\d)")


def _mask_secret_value(match: re.Match) -> str:
    key = match.group(1)
    return f"{key}=****"


def mask_extra_msg(msg: str) -> str:
    if not msg:
        return msg
    result = msg
    for pat in _SECRET_KEY_PATTERNS:
        result = pat.sub(_mask_secret_value, result)
    result = _PHONE_PATTERN.sub(r"\1****\3", result)
    result = _ID_CARD_PATTERN.sub(r"\1***********\2", result)
    result = _BANK_CARD_PATTERN.sub(r"\1****\2", result)
    return result


def determine_success(status_code: int, has_business_error: bool = False) -> bool:
    if has_business_error:
        return False
    return 200 <= status_code < 300


@dataclass(frozen=True)
class InterfaceLogParams:
    request_id: str
    interface_name: str
    source_ip: str
    user_id: str
    cost_time: int
    success: bool
    return_code: str
    return_info: str
    body_size: int
    extra_msg: str = ""


def format_interface_log(params: InterfaceLogParams) -> str:
    resp_time = now_bj_iso()
    log_type = "SERVER"
    extra_masked = mask_extra_msg(params.extra_msg) if params.extra_msg else ""
    fields = [
        params.request_id,
        resp_time,
        log_type,
        params.interface_name,
        params.source_ip,
        params.user_id,
        str(params.cost_time),
        str(params.success).lower(),
        str(params.return_code),
        params.return_info,
        str(params.body_size),
        extra_masked,
    ]
    return "|".join(fields)


def log_interface(params: InterfaceLogParams) -> None:
    try:
        log_line = format_interface_log(params)
        _interface_logger.info(log_line)
    except Exception as e:
        _interface_logger.warning(f"Failed to write interface log: {e}")


def setup_interface_logger(log_file: str = "interface.log") -> None:
    _interface_logger.setLevel(logging.INFO)
    _interface_logger.handlers.clear()
    _interface_logger.propagate = False

    if str(os.getenv("INTERFACE_LOG_DISABLE_FILE", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return

    handler = RotatingFileHandler(
        log_file,
        maxBytes=_get_log_max_bytes(),
        backupCount=_get_log_backup_count(),
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    _interface_logger.addHandler(handler)
    _interface_logger.propagate = False
