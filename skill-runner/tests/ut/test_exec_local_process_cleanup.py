# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""覆盖 !cmd 超时后的进程组终止与回收：父进程不留僵尸，孙进程不逃逸。"""

from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_SR_ROOT = Path(__file__).resolve().parents[2]
if "skill_runner" not in sys.modules:
    _pkg = types.ModuleType("skill_runner")
    _pkg.__path__ = [str(_SR_ROOT)]
    sys.modules["skill_runner"] = _pkg

import skill_runner.executor.local as local_mod
from skill_runner.executor.local import LocalExecutor
from skill_runner.models import Session


def _make_session() -> Session:
    return Session(skill_id="demo", version="latest", workspace="")


def _run(executor: LocalExecutor, session: Session, command: str) -> list[dict]:
    async def collect() -> list[dict]:
        return [ev async for ev in executor._exec_local(session, command)]

    return asyncio.run(collect())


def _pid_state(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2]
    except FileNotFoundError:
        return None


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = _pid_state(pid)
        if state is None or state == "Z":
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
def test_timeout_reaps_process_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_mod,
        "settings",
        SimpleNamespace(exec_timeout_seconds=1, workspace_root=str(tmp_path)),
    )
    executor = LocalExecutor()
    session = _make_session()
    (tmp_path / session.session_id / "work").mkdir(parents=True)

    events = _run(
        executor,
        session,
        "sh -c 'echo $$ > parent.pid; sleep 30 & echo $! > child.pid; wait'",
    )

    assert events[-1] == {
        "type": "error",
        "code": "timeout",
        "message": "command timed out after 1s",
    }
    work = tmp_path / session.session_id / "work"
    parent_pid = int((work / "parent.pid").read_text())
    child_pid = int((work / "child.pid").read_text())
    assert _wait_dead(parent_pid), "shell process was not reaped"
    assert _wait_dead(child_pid), "grandchild escaped the timeout kill"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell command")
def test_successful_command_returns_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        local_mod,
        "settings",
        SimpleNamespace(exec_timeout_seconds=10, workspace_root=str(tmp_path)),
    )
    executor = LocalExecutor()
    session = _make_session()
    (tmp_path / session.session_id / "work").mkdir(parents=True)

    events = _run(executor, session, "sh -c 'printf hello'")

    assert events[-1]["type"] == "tool_result"
    assert events[-1]["exit_code"] == 0
    assert events[-1]["output"] == "hello"