# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import random
import string
import time
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
start_time_var: ContextVar[Optional[datetime]] = ContextVar("start_time", default=None)
user_id_var: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
user_name_var: ContextVar[Optional[str]] = ContextVar("user_name", default=None)
source_channel_var: ContextVar[Optional[str]] = ContextVar("source_channel", default=None)
# 失败补录提示：framework 依赖层（Depends/middleware）注入业务标识，
# audit_failed_mutation 据此填充 resource_id / extra 等字段
audit_hint_var: ContextVar[Optional[Dict[str, Any]]] = ContextVar("audit_hint", default=None)

_BJ_TZ = timezone(timedelta(hours=8))


def generate_request_id() -> str:
    now_ms = int(time.time() * 1000)
    rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"req_{now_ms}_{rand_chars}"


def get_request_id() -> Optional[str]:
    return request_id_var.get()


def get_start_time() -> Optional[datetime]:
    return start_time_var.get()


def set_user_id(user_id: str) -> None:
    user_id_var.set(user_id)


def get_user_id() -> str:
    return user_id_var.get() or "anonymous"


def get_user_id_or_none() -> Optional[str]:
    """登录用户的真实 user_id，未登录返回 None。

    与 get_user_id() 的区别：后者把未登录归一成 "anonymous" 哨兵字符串，适合日志/审计展示；
    计量去重等需要区分「匿名」与「登录」的逻辑必须用本函数，否则所有匿名请求会共享同一个哨兵指纹。
    """
    return user_id_var.get()


def set_user_name(user_name: Optional[str]) -> None:
    user_name_var.set(user_name)


def get_user_name() -> Optional[str]:
    return user_name_var.get()


def set_audit_hint(**fields: Any) -> None:
    """合并式注入失败补录用的业务字段。None / 空字符串自动忽略。
    """
    cleaned: Dict[str, Any] = {}
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        cleaned[k] = v
    if not cleaned:
        return
    existing = audit_hint_var.get() or {}
    audit_hint_var.set({**existing, **cleaned})


def get_audit_hint() -> Optional[Dict[str, Any]]:
    return audit_hint_var.get()


# Source channel：调用入口标识，用于审计排查影响面
SOURCE_CHANNEL_WEB = "web"
SOURCE_CHANNEL_CLI = "cli"
SOURCE_CHANNEL_API = "api"
SOURCE_CHANNEL_BACKGROUND = "background"

# UA 模式匹配：浏览器关键词 / CLI 前缀
WEB_UA_MARKERS = ("mozilla", "chrome", "safari", "edge", "firefox")
CLI_UA_PREFIXES = ("openjiuwen-plugin", "jiuwen-teamskills")


def set_source_channel(channel: Optional[str]) -> None:
    source_channel_var.set(channel)


def get_source_channel() -> Optional[str]:
    return source_channel_var.get()


def detect_source_channel_from_ua(user_agent: Optional[str]) -> str:
    """从 User-Agent 推断调用入口。

    CLI 工具自带固定前缀；浏览器 UA 通常含 Mozilla；其它统一归为 api。
    """
    ua = (user_agent or "").strip().lower()
    if not ua:
        return SOURCE_CHANNEL_API
    if ua.startswith(CLI_UA_PREFIXES) or "cli" in ua[:32]:
        return SOURCE_CHANNEL_CLI
    if any(marker in ua for marker in WEB_UA_MARKERS):
        return SOURCE_CHANNEL_WEB
    return SOURCE_CHANNEL_API


def set_request_context(request_id: Optional[str] = None) -> str:
    if not request_id:
        request_id = generate_request_id()
    
    request_id_var.set(request_id)
    start_time_var.set(datetime.now(timezone.utc))
    return request_id


def clear_request_context() -> None:
    request_id_var.set(None)
    start_time_var.set(None)
    user_id_var.set(None)
    user_name_var.set(None)
    source_channel_var.set(None)
    audit_hint_var.set(None)


def get_duration_ms() -> int:
    start_time = get_start_time()
    if not start_time:
        return 0
    delta = datetime.now(timezone.utc) - start_time
    return int(delta.total_seconds() * 1000)


def now_bj_iso() -> str:
    now = datetime.now(_BJ_TZ)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}+08:00"
