# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""_exec_local 超时分支：回收子进程并按进程组终止。

纯替身测试，不起真实子进程：用 FakeProcess 记录调用序列，断言修复的不变量
（wait 回收、killpg 按组终止、事件字段不变），在 POSIX/Windows runner 上都稳定。
"""
from __future__ import annotations

import asyncio
import importlib
import signal
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

# 与仓库根 conftest.py 相同的 skill_runner 别名（幂等），保证本文件可独立收集。
_ROOT = Path(__file__).resolve().parents[3]
_SR = _ROOT / "skill-runner"
if "skill_runner" not in sys.modules:
    _pkg = types.ModuleType("skill_runner")
    _pkg.__path__ = [str(_SR)]
    sys.modules["skill_runner"] = _pkg

local_mod = importlib.import_module("skill_runner.executor.local")

_PID = 4242
_TIMEOUT_SECONDS = 0.05


class FakeProcess:
    """记录调用序列的子进程替身；communicate 可模拟长命令，wait 可模拟回收失败。"""

    def __init__(self, communicate_result: tuple[bytes, None] | None = None, wait_error: Exception | None = None):
        self.pid = _PID
        self.returncode: int | None = None
        self.calls: list[str] = []
        self._communicate_result = communicate_result
        self._wait_error = wait_error

    async def communicate(self):
        self.calls.append("communicate")
        if self._communicate_result is None:
            await asyncio.sleep(30)  # 模拟长命令：等 wait_for 超时取消
            return b"", None
        self.returncode = 0
        return self._communicate_result

    async def wait(self):
        self.calls.append("wait")
        if self._wait_error is not None:
            raise self._wait_error
        self.returncode = -15  # 被 SIGTERM 终止后回收，returncode 落定
        return self.returncode

    def kill(self) -> None:
        self.calls.append("kill")


@pytest.fixture()
def fake_session() -> SimpleNamespace:
    return SimpleNamespace(session_id="sess-timeout-test")


def _patch_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        local_mod, "settings",
        SimpleNamespace(exec_timeout_seconds=_TIMEOUT_SECONDS, workspace_root=str(tmp_path)),
    )


def _patch_spawn(monkeypatch: pytest.MonkeyPatch, proc: FakeProcess, spawn_kwargs: dict) -> None:
    async def _fake_spawn(*args: object, **kwargs: object) -> FakeProcess:
        spawn_kwargs.update(kwargs)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_spawn)


def _patch_killpg(monkeypatch: pytest.MonkeyPatch, killpg_calls: list, error: Exception | None = None) -> None:
    def _fake_killpg(pgid: int, sig: int) -> None:
        killpg_calls.append((pgid, sig))
        if error is not None:
            raise error

    # Windows 无 os.getpgid/os.killpg，raising=False 允许补齐属性，monkeypatch 会在用例后还原。
    monkeypatch.setattr(local_mod.os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(local_mod.os, "killpg", _fake_killpg, raising=False)


def _drive(executor: local_mod.LocalExecutor, session: SimpleNamespace, command: str) -> list[dict]:
    async def _collect() -> list[dict]:
        return [ev async for ev in executor._exec_local(session, command)]

    return asyncio.run(_collect())


def _timeout_event() -> dict:
    return {
        "type": "error",
        "code": "timeout",
        "message": f"command timed out after {_TIMEOUT_SECONDS}s",
    }


def test_timeout_reaps_child_process(monkeypatch, tmp_path, fake_session) -> None:
    proc = FakeProcess()
    spawn_kwargs: dict = {}
    _patch_settings(monkeypatch, tmp_path)
    _patch_spawn(monkeypatch, proc, spawn_kwargs)

    events = _drive(local_mod.LocalExecutor(), fake_session, "sleep 30")

    assert "wait" in proc.calls, "超时分支必须 await proc.wait() 回收子进程"
    assert proc.returncode is not None, "回收后 returncode 必须落定（不再恒 None）"
    assert events == [
        {"type": "tool_call", "name": "shell", "input": "sleep 30"},
        _timeout_event(),
    ]


def test_timeout_kills_whole_process_group_on_posix(monkeypatch, tmp_path, fake_session) -> None:
    proc = FakeProcess()
    spawn_kwargs: dict = {}
    killpg_calls: list = []
    _patch_settings(monkeypatch, tmp_path)
    _patch_spawn(monkeypatch, proc, spawn_kwargs)
    _patch_killpg(monkeypatch, killpg_calls)
    monkeypatch.setattr(local_mod, "_IS_POSIX", True, raising=False)

    events = _drive(local_mod.LocalExecutor(), fake_session, "sleep 30")

    assert killpg_calls == [(_PID, signal.SIGTERM)], "POSIX 下必须以 SIGTERM 按进程组终止"
    assert "kill" not in proc.calls, "按组终止成功时不应退回单进程 kill()"
    assert spawn_kwargs.get("start_new_session") is True, "POSIX 下必须以独立会话启动子进程"
    assert "creationflags" not in spawn_kwargs
    assert "wait" in proc.calls
    assert events[-1] == _timeout_event()


def test_falls_back_to_kill_when_group_is_gone(monkeypatch, tmp_path, fake_session) -> None:
    proc = FakeProcess()
    killpg_calls: list = []
    _patch_settings(monkeypatch, tmp_path)
    _patch_spawn(monkeypatch, proc, {})
    _patch_killpg(monkeypatch, killpg_calls, error=ProcessLookupError())
    monkeypatch.setattr(local_mod, "_IS_POSIX", True, raising=False)

    events = _drive(local_mod.LocalExecutor(), fake_session, "sleep 30")

    assert "kill" in proc.calls, "进程组已消失时必须退回单进程 kill()"
    assert events[-1] == _timeout_event()


def test_windows_spawns_with_new_process_group(monkeypatch, tmp_path, fake_session) -> None:
    proc = FakeProcess(communicate_result=(b"hello", None))
    spawn_kwargs: dict = {}
    _patch_settings(monkeypatch, tmp_path)
    _patch_spawn(monkeypatch, proc, spawn_kwargs)
    monkeypatch.setattr(local_mod, "_IS_POSIX", False, raising=False)

    events = _drive(local_mod.LocalExecutor(), fake_session, "echo hello")

    assert spawn_kwargs.get("creationflags") == subprocess.CREATE_NEW_PROCESS_GROUP, \
        "应以 CREATE_NEW_PROCESS_GROUP 建组"
    assert "start_new_session" not in spawn_kwargs
    assert events[-1] == {"type": "tool_result", "exit_code": 0, "output": "hello"}


def test_timeout_event_is_unchanged_when_reap_fails(monkeypatch, tmp_path, fake_session) -> None:
    proc = FakeProcess(wait_error=RuntimeError("wait boom"))
    _patch_settings(monkeypatch, tmp_path)
    _patch_spawn(monkeypatch, proc, {})
    killpg_calls: list = []
    _patch_killpg(monkeypatch, killpg_calls, error=ProcessLookupError())
    monkeypatch.setattr(local_mod, "_IS_POSIX", True, raising=False)

    events = _drive(local_mod.LocalExecutor(), fake_session, "sleep 30")

    assert events == [
        {"type": "tool_call", "name": "shell", "input": "sleep 30"},
        _timeout_event(),
    ], "回收失败不得掩盖或改变 timeout 事件"


def test_success_path_does_not_terminate(monkeypatch, tmp_path, fake_session) -> None:
    proc = FakeProcess(communicate_result=(b"hello", None))
    _patch_settings(monkeypatch, tmp_path)
    _patch_spawn(monkeypatch, proc, {})

    events = _drive(local_mod.LocalExecutor(), fake_session, "echo hello")

    assert not {"kill", "wait"} & set(proc.calls), "正常完成路径不得终止/回收进程"
    assert events == [
        {"type": "tool_call", "name": "shell", "input": "echo hello"},
        {"type": "tool_result", "exit_code": 0, "output": "hello"},
    ]
