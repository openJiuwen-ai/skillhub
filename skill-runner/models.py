# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""skill-runner data models."""
from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


class SessionStatus(str, enum.Enum):
    STARTING = "starting"
    READY = "ready"
    ACTIVE = "active"
    DONE = "done"
    ERROR = "error"


# ---- HTTP request/response ----

class CreateSessionRequest(BaseModel):
    skill_id: str
    version: str = "latest"
    # "ordinary" = single DeepAgent; "swarm" = multi-role TeamAgent
    skill_type: str = "ordinary"
    # marketplace proxy 在转发前解析 ZIP、注入文本内容，skill-runner 不做任何下载。
    # system_prompt 为空时 skill-runner 用 skill_md + workflow_md + roles 自行组装。
    system_prompt: str = ""
    skill_md: str = ""        # SKILL.md 文本
    workflow_md: str = ""     # workflow.md 文本（可选）
    roles: dict[str, str] = {}  # SwarmSkill 角色：{角色名: 角色文档文本}
    # SwarmSkill 团队模式，opt-in 动态 spawn：""=自动推导，有 roles 则 predefined，
    # 显式 "hybrid"/"default"/"predefined" 由 SKILL.md frontmatter 声明。
    team_mode: str = ""
    # ZIP package base64（marketplace proxy 编码），skill-runner 解码后交给 SkillBundle
    package_bytes_b64: str = ""
    # marketplace 注入的调用方用户 ID，用于 llm_proxy 侧的每日 token 计量
    user_id: str = ""


class CreateSessionResponse(BaseModel):
    session_id: str
    status: SessionStatus
    timeout_seconds: int
    # 多实例模式下本实例的回连基址（如 http://10.0.1.23:8900）；marketplace 代理
    # 据此做会话粘性路由，并在返回浏览器前剥除该字段。单实例恒为空。
    instance_addr: str = ""


class SendMessageRequest(BaseModel):
    content: str


class SendMessageResponse(BaseModel):
    message_id: str
    status: str = "queued"


class UploadFileRequest(BaseModel):
    filename: str
    content_b64: str


def sse_event(event_type: str, **payload):
    return {"type": event_type, **payload}


# ---- in-process session state ----

@dataclass
class Session:
    skill_id: str
    version: str
    workspace: str
    skill_type: str = "ordinary"  # "ordinary" | "swarm"
    session_id: str = field(default_factory=lambda: f"sess-{uuid.uuid4().hex[:12]}")
    status: SessionStatus = SessionStatus.STARTING
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    turn_count: int = 0
    # SSE 事件队列
    events: Any = None
    # System prompt - built from SKILL.md or supplied verbatim by caller
    system_prompt: str = ""
    # 调用方用户 ID
    user_id: str = ""
    # executor-private bag: store agent/sys_op/sandbox_id refs without coupling executors
    extra: dict = field(default_factory=dict)

    def touch(self) -> None:
        self.last_active_at = time.time()


