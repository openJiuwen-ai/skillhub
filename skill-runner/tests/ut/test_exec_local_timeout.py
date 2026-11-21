# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# 用例直接驱动 `_exec_local`：超时分支没有其他对外入口，走公开的 run_turn
# 需要先起 DeepAgent（依赖 LLM 配置），测不到纯进程回收逻辑。
# pylint: disable=protected-access

"""`!cmd` 调试入口超时后的子进程回收与进程组终止（GitCode #249 / GitHub #203）。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

# 与仓库根 conftest.py 同一做法：把 skill-runner/ 目录注册成 skill_runner 包，
# 这样 pytest 从任意 rootdir 启动本文件都能导入被测模块。
_SKILL_RUNNER_DIR = Path(__file__).resolve().parents[2]
if "skill_runner" not in sys.modules:
    _pkg = types.ModuleType("skill_runner")
    _pkg.__path__ = [str(_SKILL_RUNNER_DIR)]
    sys.modules["skill_runner"] = _pkg

from skill_runner.executor import local as local_mod  # noqa: E402
from skill_runner.executor.local import LocalExecutor, _terminate_process_tree  # noqa: E402
from skill_runner.models import Session  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="进程组语义（start_new_session / killpg）只在 POSIX 下成立",
)


def _settings(tmp_path: Path, timeout: int) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_root=str(tmp_path / "ws"),
        exec_timeout_seconds=timeout,
    )


def _session_with_workdir(tmp_path: Path) -> Session:
    """`_exec_local` 把命令 cwd 设成 <workspace_root>/<sid>/work，须先建好。"""
    session = Session(skill_id="s", version="v", workspace="")
    (tmp_path / "ws" / session.session_id / "work").mkdir(parents=True)
    return session


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _drain(agen) -> list[dict]:
    return [ev async for ev in agen]


def test_timeout_reaps_child_and_leaves_no_grandchild(tmp_path, monkeypatch):
    pidfile = tmp_path / "grandchild.pid"
    # 孙进程 sleep 30 刻意短于默认 60s 超时上限，避免测试自身泄漏进程
    command = f"sh -c 'sleep 30 & echo $! > {pidfile}; wait'"
    monkeypatch.setattr(local_mod, "settings", _settings(tmp_path, 1))

    events = asyncio.run(_drain(LocalExecutor()._exec_local(_session_with_workdir(tmp_path), command)))

    assert events[0]["type"] == "tool_call"
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "timeout"

    assert pidfile.exists(), "命令没跑起来，测试前提不成立"
    grandchild = int(pidfile.read_text().strip())
    # 修复前：kill 只命中 sh，sleep 继续在容器里跑
    for _ in range(20):
        if not _alive(grandchild):
            break
        time.sleep(0.1)
    alive = _alive(grandchild)
    if alive:  # 不把尸体留给后续用例
        os.kill(grandchild, 9)
    assert alive is False, "超时后孙进程仍在运行（未整组终止）"


def test_timeout_event_is_emitted_once_with_configured_seconds(tmp_path, monkeypatch):
    monkeypatch.setattr(local_mod, "settings", _settings(tmp_path, 1))

    events = asyncio.run(_drain(LocalExecutor()._exec_local(_session_with_workdir(tmp_path), "sleep 30")))

    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert "timed out after 1s" in errors[0]["message"]


def test_terminate_process_tree_leaves_a_reaped_process():
    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            "sleep", "30",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        await _terminate_process_tree(proc)
        return proc.returncode

    returncode = asyncio.run(scenario())
    # 修复前 kill() 后立刻读 returncode 恒为 None（进程未被回收）
    assert returncode is not None
    assert returncode < 0


def test_terminate_process_tree_tolerates_already_exited_process():
    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            "true",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        await proc.wait()
        await _terminate_process_tree(proc)  # 不抛 ProcessLookupError
        return proc.returncode

    assert asyncio.run(scenario()) == 0


def test_normal_exit_still_reports_exit_code_and_output(tmp_path, monkeypatch):
    monkeypatch.setattr(local_mod, "settings", _settings(tmp_path, 30))

    events = asyncio.run(_drain(LocalExecutor()._exec_local(_session_with_workdir(tmp_path), "echo hello")))

    result = [e for e in events if e["type"] == "tool_result"]
    assert len(result) == 1
    assert result[0]["exit_code"] == 0
    assert result[0]["output"].strip() == "hello"
