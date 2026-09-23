# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import logging
import os
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from functools import wraps
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, TypeVar, cast

import structlog
from structlog.contextvars import merge_contextvars

from plugins_market.core.context import get_duration_ms, get_request_id
from plugins_market.core.operation_log import (
    get_operation_id,
    get_operation_type,
    get_parent_operation_id,
)

F = TypeVar("F", bound=Callable[..., Any])

_DEFAULT_LOG_MAX_BYTES = 50 * 1024 * 1024
_DEFAULT_LOG_BACKUP_COUNT = 5

_RESERVED_LOG_RECORD_KEYS = frozenset(logging.makeLogRecord({}).__dict__.keys())
_COMMON_FIELD_ORDER = (
    "request_id",
    "operation_id",
    "operation_type",
    "parent_operation_id",
    "duration_ms",
)
_RENDER_EXCLUDE_FIELDS = {
    "args",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


def _stringify_log_value(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _build_ordered_event_dict(event_dict: Mapping[str, Any]) -> dict[str, Any]:
    ordered: dict[str, Any] = {}
    for key in _COMMON_FIELD_ORDER:
        value = event_dict.get(key)
        if value in (None, ""):
            continue
        ordered[key] = value

    for key, value in event_dict.items():
        if key in ordered or value in (None, ""):
            continue
        ordered[key] = value
    return ordered


def _inject_context_fields(event_dict: dict[str, Any]) -> dict[str, Any]:
    request_id = get_request_id()
    if request_id and "request_id" not in event_dict:
        event_dict["request_id"] = request_id

    operation_id = get_operation_id()
    if operation_id and "operation_id" not in event_dict:
        event_dict["operation_id"] = operation_id

    operation_type = get_operation_type()
    if operation_type and "operation_type" not in event_dict:
        event_dict["operation_type"] = operation_type

    parent_operation_id = get_parent_operation_id()
    if parent_operation_id and "parent_operation_id" not in event_dict:
        event_dict["parent_operation_id"] = parent_operation_id

    duration_ms = get_duration_ms()
    if duration_ms > 0 and "duration_ms" not in event_dict:
        event_dict["duration_ms"] = duration_ms

    return _build_ordered_event_dict(event_dict)


def _inject_standard_logging_context(record: logging.LogRecord) -> logging.LogRecord:
    for key, value in _inject_context_fields({}).items():
        if key not in record.__dict__:
            setattr(record, key, value)
    return record


def _normalize_log_record(record: logging.LogRecord) -> dict[str, Any]:
    event_dict: dict[str, Any] = {
        "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
        "level": record.levelname.lower(),
        "logger": record.name,
    }

    if isinstance(record.msg, Mapping):
        payload = dict(record.msg)
        event = payload.pop("event", "")
        if event:
            event_dict["event"] = event
        event_dict.update(payload)
    else:
        event_dict["event"] = record.getMessage()

    for key, value in record.__dict__.items():
        if key in _RESERVED_LOG_RECORD_KEYS or key in _RENDER_EXCLUDE_FIELDS:
            continue
        if value in (None, "", (None, None, None)):
            continue
        event_dict[key] = value

    return _build_ordered_event_dict(event_dict)


def _render_plain_log(event_dict: Mapping[str, Any]) -> str:
    normalized = _build_ordered_event_dict(dict(event_dict))
    ts = _stringify_log_value(normalized.pop("timestamp", ""))
    level = _stringify_log_value(normalized.pop("level", "")).upper()
    logger_name = _stringify_log_value(normalized.pop("logger", ""))
    event = _stringify_log_value(normalized.pop("event", ""))

    parts = [part for part in (ts, level, logger_name) if part]
    rendered = ", ".join(parts)

    if event:
        rendered = f"{rendered}, event={event}" if rendered else f"event={event}"

    if normalized:
        rendered_fields = [f"{key}={_stringify_log_value(value)}" for key, value in normalized.items()]
        suffix = ", ".join(rendered_fields)
        rendered = f"{rendered}, {suffix}" if rendered else suffix

    return rendered


class _ContextEnrichmentFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        _inject_standard_logging_context(record)
        return True


class PlainLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = _render_plain_log(_normalize_log_record(record))
        if record.exc_info not in (None, (None, None, None)):
            rendered = f"{rendered}\n{self.formatException(record.exc_info)}"
        return rendered


_GLOBAL_LOG_FILTER = _ContextEnrichmentFilter()


def _ensure_filter(handler: logging.Handler) -> None:
    if _GLOBAL_LOG_FILTER not in handler.filters:
        handler.addFilter(_GLOBAL_LOG_FILTER)


def _get_log_max_bytes() -> int:
    try:
        value = int(os.getenv("LOG_MAX_BYTES", str(_DEFAULT_LOG_MAX_BYTES)))
    except (TypeError, ValueError):
        return _DEFAULT_LOG_MAX_BYTES
    return value if value > 0 else _DEFAULT_LOG_MAX_BYTES


def _get_log_backup_count() -> int:
    try:
        value = int(os.getenv("LOG_BACKUP_COUNT", str(_DEFAULT_LOG_BACKUP_COUNT)))
    except (TypeError, ValueError):
        return _DEFAULT_LOG_BACKUP_COUNT
    return value if value > 0 else _DEFAULT_LOG_BACKUP_COUNT


def _build_handler(log_path: str, level: int) -> logging.Handler:
    handler = RotatingFileHandler(
        log_path,
        maxBytes=_get_log_max_bytes(),
        backupCount=_get_log_backup_count(),
        encoding="utf-8",
    )
    handler.setFormatter(PlainLogFormatter())
    handler.setLevel(level)
    _ensure_filter(handler)
    return handler


def _build_stream_handler(level: int) -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(PlainLogFormatter())
    handler.setLevel(level)
    _ensure_filter(handler)
    return handler


def _sanitize_event_dict_for_log_record(event_dict: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in event_dict.items():
        if key in _RESERVED_LOG_RECORD_KEYS:
            sanitized[f"extra_{key}"] = value
            continue
        sanitized[key] = value
    return sanitized


def _inject_request_context(logger, method_name, event_dict):
    return _sanitize_event_dict_for_log_record(_inject_context_fields(event_dict))


def _ensure_event_field(logger, method_name, event_dict):
    if event_dict.get("event") in (None, ""):
        event_dict["event"] = method_name
    return event_dict


def setup_logging(debug: bool = False):
    structlog.configure(
        processors=[
            merge_contextvars,
            _inject_request_context,
            _ensure_event_field,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.render_to_log_kwargs,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    level = logging.DEBUG if debug else logging.INFO
    log_dir = os.getenv("INTERFACE_LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)

    logging.captureWarnings(True)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.filters.clear()
    root_logger.setLevel(level)
    root_logger.propagate = False
    root_logger.disabled = False

    root_stream_handler = _build_stream_handler(level)
    app_file_handler = _build_handler(os.path.join(log_dir, "app.log"), level)
    root_logger.addHandler(root_stream_handler)
    root_logger.addHandler(app_file_handler)
    root_logger.addFilter(_GLOBAL_LOG_FILTER)

    framework_stream_handler = _build_stream_handler(level)
    access_file_handler = _build_handler(os.path.join(log_dir, "access.log"), level)
    framework_file_handler = _build_handler(os.path.join(log_dir, "framework.log"), level)

    framework_loggers = ("uvicorn", "uvicorn.error", "uvicorn.asgi", "uvicorn.lifespan", "apscheduler")
    for logger_name in framework_loggers:
        logger_obj = logging.getLogger(logger_name)
        logger_obj.handlers.clear()
        logger_obj.filters.clear()
        logger_obj.addHandler(framework_stream_handler)
        logger_obj.addHandler(framework_file_handler)
        logger_obj.addFilter(_GLOBAL_LOG_FILTER)
        logger_obj.propagate = False
        logger_obj.setLevel(level)
        logger_obj.disabled = False

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.filters.clear()
    access_logger.addHandler(framework_stream_handler)
    access_logger.addHandler(access_file_handler)
    access_logger.addFilter(_GLOBAL_LOG_FILTER)
    access_logger.propagate = False
    access_logger.setLevel(level)
    access_logger.disabled = False

    for logger_name, logger_obj in logging.root.manager.loggerDict.items():
        if not isinstance(logger_obj, logging.Logger):
            continue
        if (
            logger_name.startswith("plugins_market")
            or logger_name.startswith("common")
        ):
            logger_obj.handlers.clear()
            logger_obj.filters.clear()
            logger_obj.addFilter(_GLOBAL_LOG_FILTER)
            logger_obj.propagate = True
            logger_obj.setLevel(level)
            logger_obj.disabled = False
        elif logger_name.startswith("sqlalchemy"):
            # 提到 INFO 会绕过 echo=False 刷出全量 SQL，锁 WARNING 以上
            logger_obj.handlers.clear()
            logger_obj.filters.clear()
            logger_obj.addFilter(_GLOBAL_LOG_FILTER)
            logger_obj.propagate = True
            logger_obj.setLevel(max(level, logging.WARNING))
            logger_obj.disabled = False

    from plugins_market.core.interface_log import setup_interface_logger

    setup_interface_logger(log_file=os.path.join(log_dir, "interface.log"))

    from plugins_market.core.log_redaction import configure_sensitive_log_redaction

    configure_sensitive_log_redaction()


def get_logger(name: str = None):
    """Get a structured logger."""
    return structlog.get_logger(name or __name__)


def non_request_exception_boundary(
    *,
    logger: Any,
    message: str,
    reraise: bool = True,
    context_extractor: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                extra = context_extractor(*args, **kwargs) if context_extractor else {}
                logger.exception(message, error_message=str(exc), **extra)
                if reraise:
                    raise
                return None

        return cast(F, wrapper)

    return decorator


def non_request_async_exception_boundary(
    *,
    logger: Any,
    message: str,
    reraise: bool = True,
    context_extractor: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any):
            try:
                return await func(*args, **kwargs)
            except Exception:
                extra = context_extractor(*args, **kwargs) if context_extractor else {}
                logger.exception(message, **extra)
                if reraise:
                    raise
                return None

        return cast(F, wrapper)

    return decorator


def background_task_exception_boundary(
    *,
    logger: Any,
    message: str,
    reraise: bool = True,
    context_extractor: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[F], F]:
    return non_request_exception_boundary(
        logger=logger,
        message=message,
        reraise=reraise,
        context_extractor=context_extractor,
    )


def startup_exception_boundary(
    *,
    logger: Any,
    message: str,
    reraise: bool = True,
    context_extractor: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[F], F]:
    return non_request_exception_boundary(
        logger=logger,
        message=message,
        reraise=reraise,
        context_extractor=context_extractor,
    )
