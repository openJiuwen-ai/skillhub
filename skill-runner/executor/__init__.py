# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""可插拔沙箱执行器。

    SandboxExecutor
      ├── LocalExecutor - agent-core LOCAL 模式，直接在容器内运行
      └── K8sExecutor   - 每 session 起一个 worker pod
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from ..config import settings
from ..models import Session


class SandboxExecutor(ABC):
    @abstractmethod
    async def create(self, session: Session) -> None:
        """初始化执行环境。"""

    @abstractmethod
    async def run_turn(
        self, session: Session, message: str
    ) -> AsyncIterator[dict[str, Any]]:
        """跑一轮，异步生成事件 dict (text / tool_call / tool_result / error)。

        子类须重写本方法；基类的实现仅保证签名为 async generator function，
        若被直接调用会立即抛 NotImplementedError。
        """
        if False:  # pragma: no cover - 让基类成为 async generator function
            yield {}
        raise NotImplementedError

    @abstractmethod
    async def destroy(self, session: Session) -> None:
        """销毁执行环境。"""

    async def put_file(self, session: Session, filename: str, content: bytes) -> dict[str, Any]:
        """把用户上传的文件写入沙箱 work/uploads/，供 agent 用相对路径 ./uploads/<name> 读取。

        子类按需重写；返回 {"path": <work 内相对路径>, "size": <字节数>}。
        """
        raise NotImplementedError


def get_executor() -> SandboxExecutor:
    """根据配置返回执行器实例。"""
    name = settings.executor
    if name == "k8s":
        from .k8s import K8sExecutor
        return K8sExecutor()
    if name == "local":
        from .local import LocalExecutor
        return LocalExecutor()
    raise ValueError(f"未知 executor: {name!r}（合法值: k8s, local）")
