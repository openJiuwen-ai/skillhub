# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""会话存储：进程内字典，控制面以单副本运行，会话状态随进程生命周期。"""
from __future__ import annotations

import asyncio
import os
import shutil

from .config import settings
from .models import Session, SessionStatus


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()

    async def create(self, skill_id: str, version: str, skill_type: str = "ordinary") -> Session:
        session = Session(
            skill_id=skill_id,
            version=version,
            workspace="",
            skill_type=skill_type,
        )
        session.workspace = os.path.join(settings.workspace_root, session.session_id)
        os.makedirs(session.workspace, exist_ok=True)
        session.events = asyncio.Queue(maxsize=settings.sse_buffer_max)
        async with self._lock:
            self._sessions[session.session_id] = session
        return session

    async def get(self, session_id: str) -> Session | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def active_count(self) -> int:
        async with self._lock:
            return len(self._sessions)

    async def snapshot(self) -> list[Session]:
        """所有会话的浅快照，供超时回收等周期任务遍历。"""
        async with self._lock:
            return list(self._sessions.values())

    async def end(self, session_id: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session.status = SessionStatus.DONE
        # local executor 的工作区清理。rmtree 对大目录可能耗时数百毫秒，
        # 放线程池执行，避免阻塞事件循环拖累其他会话。
        ws = session.workspace
        if ws and os.path.isdir(ws):
            await asyncio.to_thread(shutil.rmtree, ws, ignore_errors=True)
        return True


store = InMemorySessionStore()
