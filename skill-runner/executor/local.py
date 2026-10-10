# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LOCAL executor：agent-core 直接在 worker pod 内跑（无 jiuwenbox 沙箱，pod 提供边界）。"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from collections.abc import AsyncIterator
from typing import Any

from ..config import settings
from ..models import Session, sse_event
from . import SandboxExecutor

logger = logging.getLogger("skill_runner.local")

_LLM_MODEL: Any = None
_LLM_LOCK = asyncio.Lock()


def _wipe_empty_proxy_env() -> None:
    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    ):
        if key in os.environ and not os.environ[key]:
            del os.environ[key]


def _import_openjiuwen() -> dict:
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from openjiuwen.agent_teams.schema.team import TeamMemberSpec, TeamRole
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec, TeamModelConfig
    from openjiuwen.core.foundation.llm.model import Model
    from openjiuwen.core.foundation.llm.schema.config import (
        ModelClientConfig,
        ModelRequestConfig,
    )
    from openjiuwen.core.runner.runner import Runner
    from openjiuwen.core.sys_operation import OperationMode, SysOperation, SysOperationCard
    from openjiuwen.core.sys_operation.config import LocalWorkConfig
    from openjiuwen.harness.factory import create_deep_agent
    from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail

    return {
        "Model": Model,
        "ModelClientConfig": ModelClientConfig,
        "ModelRequestConfig": ModelRequestConfig,
        "Runner": Runner,
        "OperationMode": OperationMode,
        "SysOperation": SysOperation,
        "SysOperationCard": SysOperationCard,
        "LocalWorkConfig": LocalWorkConfig,
        "create_deep_agent": create_deep_agent,
        "SysOperationRail": SysOperationRail,
        "TeamAgentSpec": TeamAgentSpec,
        "DeepAgentSpec": DeepAgentSpec,
        "TeamModelConfig": TeamModelConfig,
        "TeamMemberSpec": TeamMemberSpec,
        "TeamRole": TeamRole,
    }


async def _get_or_build_llm_model() -> Any:
    global _LLM_MODEL
    if _LLM_MODEL is not None:
        return _LLM_MODEL
    async with _LLM_LOCK:
        if _LLM_MODEL is not None:
            return _LLM_MODEL
        _wipe_empty_proxy_env()
        if not settings.llm_api_key:
            raise RuntimeError("LLM_API_KEY (or API_KEY) env not set; cannot build DeepAgent")
        oj = _import_openjiuwen()
        client_cfg = oj["ModelClientConfig"](
            client_id=settings.llm_client_id,
            client_provider=settings.llm_provider,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            timeout=settings.llm_timeout_seconds,
            verify_ssl=settings.llm_verify_ssl,
        )
        req_cfg = oj["ModelRequestConfig"](
            model=settings.llm_model_name,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
        _LLM_MODEL = oj["Model"](
            model_client_config=client_cfg,
            model_config=req_cfg,
        )
        logger.info(
            "LLM model built: provider=%s model=%s base=%s",
            settings.llm_provider, settings.llm_model_name, settings.llm_api_base,
        )
        return _LLM_MODEL


class LocalExecutor(SandboxExecutor):
    """LOCAL-mode executor：agent 直接在容器内运行，工具执行不经过 jiuwenbox。"""

    async def create(self, session: Session) -> None:
        work_dir, skill_dir = self._session_dirs(session)
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(skill_dir, exist_ok=True)
        session.workspace = work_dir

        await self._provision_skill(session, skill_dir)

        try:
            await self._build_agent(session, work_dir, skill_dir)
        except Exception as exc:
            logger.warning(
                "DeepAgent build failed for session=%s: %s",
                session.session_id, exc,
            )
            session.extra["llm_disabled_reason"] = str(exc)

    async def destroy(self, session: Session) -> None:
        oj_card_id = session.extra.pop("sysop_card_id", None)
        if oj_card_id is not None:
            try:
                oj = _import_openjiuwen()
                rm = oj["Runner"].resource_mgr
                for name in ("remove_sys_operation", "delete_sys_operation"):
                    fn = getattr(rm, name, None)
                    if fn:
                        try:
                            fn(oj_card_id)
                        except Exception:
                            logger.warning(
                                "destroy: %s(%s) failed for session=%s",
                                name, oj_card_id, session.session_id, exc_info=True,
                            )
                        break
            except Exception:
                logger.warning(
                    "destroy: openjiuwen cleanup failed for session=%s card=%s",
                    session.session_id, oj_card_id, exc_info=True,
                )

        session.extra.pop("agent", None)
        sys_op = session.extra.pop("sys_op", None)
        if sys_op is not None:
            for name in ("aclose", "close", "shutdown"):
                fn = getattr(sys_op, name, None)
                if fn:
                    try:
                        res = fn()
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        logger.warning(
                            "destroy: sys_op.%s failed for session=%s",
                            name, session.session_id, exc_info=True,
                        )
                    break

        work_dir, skill_dir = self._session_dirs(session)
        session_root = os.path.dirname(work_dir)
        if os.path.isdir(session_root):
            try:
                # rmtree 放线程池避免阻塞
                await asyncio.to_thread(shutil.rmtree, session_root, ignore_errors=True)
            except Exception:
                logger.warning(
                    "destroy: rmtree %s failed for session=%s",
                    session_root, session.session_id, exc_info=True,
                )

    async def run_turn(
        self, session: Session, message: str
    ) -> AsyncIterator[dict[str, Any]]:
        logger.info("run_turn enter: session=%s msg_len=%d", session.session_id, len(message))
        message = message.strip()

        if message.startswith("!"):
            if not settings.allow_debug_shell:
                yield sse_event(
                    "error", code="debug_shell_disabled",
                    message="debug shell (!cmd) is disabled",
                )
                return
            async for ev in self._exec_local(session, message[1:].strip()):
                yield ev
            return

        agent = session.extra.get("agent")
        if agent is None:
            reason = session.extra.get("llm_disabled_reason", "DeepAgent not initialised")
            yield sse_event("error", code="agent_unavailable", message=f"LLM disabled: {reason}")
            return

        oj = _import_openjiuwen()
        runner_cls = oj["Runner"]
        is_swarm = session.skill_type == "swarm"
        chunk_count = 0

        if is_swarm:
            stream_iter = runner_cls.run_agent_team_streaming(
                agent_team=agent,
                inputs={"query": message},
                session=session.session_id,
            )
        else:
            stream_iter = runner_cls.run_agent_streaming(
                agent=agent,
                inputs={"query": message},
                session=session.session_id,
            )

        try:
            async for chunk in stream_iter:
                chunk_count += 1
                events_emitted = self._translate_chunk(chunk)
                verbose_window = 30 if is_swarm else 10
                if chunk_count <= verbose_window or chunk_count % 50 == 0:
                    logger.info(
                        "chunk #%d type=%r -> %d events%s",
                        chunk_count, getattr(chunk, "type", None),
                        len(events_emitted),
                        " [swarm]" if is_swarm else "",
                    )
                for ev in events_emitted:
                    yield ev
        except Exception as exc:
            logger.exception("agent stream failed: session=%s", session.session_id)
            yield sse_event("error", code="agent_failed", message=str(exc))
        finally:
            logger.info(
                "run_turn done: session=%s chunks=%d turn=%d",
                session.session_id, chunk_count, session.turn_count,
            )

    async def _exec_local(
        self, session: Session, command: str
    ) -> AsyncIterator[dict[str, Any]]:
        """!cmd 调试：在 session 工作目录内直接跑一条命令。"""
        import shlex

        if not command:
            yield sse_event("error", code="empty_command", message="command empty")
            return
        if command.startswith("cmd "):
            command = command[4:].lstrip()

        work_dir, _ = self._session_dirs(session)
        yield sse_event("tool_call", name="shell", input=command)
        try:
            args = shlex.split(command)
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=work_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=settings.exec_timeout_seconds
                )
            except asyncio.TimeoutError:
                proc.kill()
                yield sse_event(
                    "error", code="timeout",
                    message=f"command timed out after {settings.exec_timeout_seconds}s",
                )
                return
            yield sse_event(
                "tool_result",
                exit_code=proc.returncode,
                output=(stdout or b"").decode("utf-8", "replace"),
            )
        except Exception as exc:
            yield sse_event("error", code="exec_failed", message=str(exc))

    async def _build_agent(self, session: Session, work_dir: str, skill_dir: str) -> None:
        if session.skill_type == "swarm":
            await self._build_team_agent(session, work_dir)
        else:
            await self._build_deep_agent(session, work_dir)

    async def _build_deep_agent(self, session: Session, work_dir: str) -> None:
        model = await _get_or_build_llm_model()
        oj = _import_openjiuwen()

        sysop_card = oj["SysOperationCard"](
            id=f"skill_runner_{session.session_id}",
            mode=oj["OperationMode"].LOCAL,
            work_config=self._make_work_config(oj),
        )
        sys_op = oj["SysOperation"](card=sysop_card)
        add_result = oj["Runner"].resource_mgr.add_sys_operation(sysop_card)
        if hasattr(add_result, "is_err") and add_result.is_err():
            raise RuntimeError(f"add_sys_operation failed: {add_result.msg()}")
        if hasattr(sys_op, "init") and asyncio.iscoroutinefunction(sys_op.init):
            await sys_op.init()
        elif hasattr(sys_op, "initialize") and asyncio.iscoroutinefunction(sys_op.initialize):
            await sys_op.initialize()

        agent = oj["create_deep_agent"](
            model=model,
            system_prompt=self._build_deep_system_prompt(session),
            sys_operation=sys_op,
            rails=[oj["SysOperationRail"](with_code_tool=True)],
            max_iterations=settings.llm_max_iterations,
            restrict_to_work_dir=False,
            workspace=work_dir,
        )

        session.extra["agent"] = agent
        session.extra["sys_op"] = sys_op
        session.extra["sysop_card_id"] = sysop_card.id
        logger.info("DeepAgent ready: session=%s work=%s", session.session_id, work_dir)

    async def _build_team_agent(self, session: Session, work_dir: str) -> None:
        oj = _import_openjiuwen()

        sysop_card = oj["SysOperationCard"](
            id=f"skill_runner_{session.session_id}",
            mode=oj["OperationMode"].LOCAL,
            work_config=self._make_work_config(oj),
        )
        sys_op = oj["SysOperation"](card=sysop_card)
        add_result = oj["Runner"].resource_mgr.add_sys_operation(sysop_card)
        if hasattr(add_result, "is_err") and add_result.is_err():
            raise RuntimeError(f"add_sys_operation failed: {add_result.msg()}")
        if hasattr(sys_op, "init") and asyncio.iscoroutinefunction(sys_op.init):
            await sys_op.init()
        elif hasattr(sys_op, "initialize") and asyncio.iscoroutinefunction(sys_op.initialize):
            await sys_op.initialize()

        model_cfg = self._make_team_model_config(oj)
        sysop_spec = self._make_sysop_spec(session, oj)

        from openjiuwen.agent_teams.schema.deep_agent_spec import WorkspaceSpec
        workspace_spec = WorkspaceSpec(root_path=work_dir, stable_base=False)

        leader_spec = oj["DeepAgentSpec"](
            model=model_cfg,
            system_prompt=self._build_team_system_prompt(session, role="leader"),
            sys_operation=sysop_spec,
            rails=[{"type": "core.sys_operation", "params": {"with_code_tool": True}}],
            max_iterations=settings.llm_max_iterations,
            workspace=workspace_spec,
        )
        teammate_spec = oj["DeepAgentSpec"](
            model=model_cfg,
            system_prompt=self._build_team_system_prompt(session, role="teammate"),
            sys_operation=sysop_spec,
            rails=[{"type": "core.sys_operation", "params": {"with_code_tool": True}}],
            max_iterations=settings.llm_max_iterations,
            workspace=workspace_spec,
        )
        predefined_members = self._build_predefined_team_members(session, oj)
        resolved_mode = self._resolve_team_mode(session, predefined_members)
        if resolved_mode == "default":
            predefined_members = []

        team_spec = oj["TeamAgentSpec"](
            agents={"leader": leader_spec, "teammate": teammate_spec},
            team_name=f"skill_team_{session.session_id[:8]}",
            spawn_mode="inprocess",
            team_mode=resolved_mode,
            predefined_members=predefined_members,
        )
        session.extra["agent"] = team_spec
        session.extra["sys_op"] = sys_op
        session.extra["sysop_card_id"] = sysop_card.id
        logger.info(
            "TeamAgent ready: session=%s work=%s team_mode=%s members=%d",
            session.session_id, work_dir, resolved_mode or "auto", len(predefined_members),
        )

    @staticmethod
    def _resolve_team_mode(session: Session, predefined_members: list) -> str | None:
        """优先用 SKILL.md frontmatter 声明的 team_mode；未声明时有预定义成员→predefined，否则 None。"""
        bundle = session.extra.get("skill_bundle")
        declared = (getattr(bundle, "team_mode", "") or "").strip().lower()
        if declared in ("default", "predefined", "hybrid"):
            return declared
        return "predefined" if predefined_members else None

    def _make_sysop_spec(self, session: Session, oj: dict) -> Any:
        from openjiuwen.agent_teams.schema.deep_agent_spec import SysOperationSpec

        return SysOperationSpec(
            id=f"skill_runner_{session.session_id}",
            mode=oj["OperationMode"].LOCAL,
            work_config=self._make_work_config(oj),
        )

    @staticmethod
    def _make_team_model_config(oj: dict) -> Any:
        client_cfg = oj["ModelClientConfig"](
            client_id=settings.llm_client_id,
            client_provider=settings.llm_provider,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            timeout=settings.llm_timeout_seconds,
            verify_ssl=settings.llm_verify_ssl,
        )
        req_cfg = oj["ModelRequestConfig"](
            model=settings.llm_model_name,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
        return oj["TeamModelConfig"](
            model_client_config=client_cfg,
            model_request_config=req_cfg,
        )

    def _build_predefined_team_members(self, session: Session, oj: dict) -> list[Any]:
        bundle = session.extra.get("skill_bundle")
        roles = getattr(bundle, "roles", None) or {}
        members: list[Any] = []
        used_names = {"team_leader", "leader", "human_agent", "user"}
        for raw_name, role_doc in roles.items():
            member_name = self._normalize_member_name(str(raw_name), used_names)
            used_names.add(member_name)
            display_name = str(raw_name).strip() or member_name
            role_text = (role_doc or "").strip()
            persona = role_text.splitlines()[0].strip("# ") if role_text else display_name
            prompt_hint = (
                f"You are the swarmskill role `{display_name}`. Follow this role document:\n\n"
                f"{role_text}"
            ) if role_text else f"You are the swarmskill role `{display_name}`."
            members.append(oj["TeamMemberSpec"](
                member_name=member_name,
                display_name=display_name,
                role_type=oj["TeamRole"].TEAMMATE,
                persona=persona,
                prompt_hint=prompt_hint,
            ))
        return members

    @staticmethod
    def _normalize_member_name(raw: str, used: set[str]) -> str:
        name = re.sub(r"[^0-9A-Za-z_]+", "_", raw.strip().lower()).strip("_")
        if not name:
            name = "teammate"
        if name[0].isdigit():
            name = f"role_{name}"
        base = name
        i = 2
        while name in used:
            name = f"{base}_{i}"
            i += 1
        return name

    def _build_deep_system_prompt(self, session: Session) -> str:
        base = (
            "You are an autonomous skill agent. You have shell, filesystem, and python "
            "code execution tools. Follow the SKILL document below precisely.\n"
            "Your current working directory is writeable; create your outputs here.\n"
            "Reference skill files live in the ./skill/ subdirectory (e.g. ./skill/SKILL.md) "
            "when present.\n"
            "When you finish, your final message MUST contain the COMPLETE deliverable "
            "inline — the full document/answer the user asked for, formatted in Markdown. "
            "Do not reply with only a short summary or a pointer to a file: if you also "
            "wrote the result to a file, still include its full content in your reply.\n"
        )
        sp = (session.system_prompt or "").strip()
        if sp:
            return base + "\n----- BEGIN SKILL DOCUMENT -----\n" + sp + \
                "\n----- END SKILL DOCUMENT -----\n"
        return base

    def _build_team_system_prompt(self, session: Session, *, role: str) -> str:
        bundle = session.extra.get("skill_bundle")

        if role == "leader":
            base = (
                "You are the LEADER of a skill agent team. Your job is to "
                "ORCHESTRATE, not to answer the user yourself. You MUST use your "
                "team tools to drive collaboration: build_team → create_task "
                "(split the request into subtasks for the roles listed below) → "
                "send_message to delegate, then integrate teammates' results. "
                "Never produce the final deliverable alone; always delegate first.\n"
                "Do NOT open by asking the user clarifying questions. This is a "
                "one-shot trial run: treat the user's first message as the complete "
                "brief, make reasonable assumptions for anything unspecified, and "
                "your VERY FIRST action MUST be a build_team tool call. Only after "
                "the team has produced results may you write prose to the user.\n"
                "Your FINAL message MUST contain the complete, integrated deliverable "
                "inline — merge the teammates' outputs into the full result the user "
                "asked for, formatted in Markdown. Do not end with only a summary or a "
                "list of file names.\n"
            )
            if bundle is None:
                sp = (session.system_prompt or "").strip()
                return base + (f"\n----- SKILL DOCUMENT -----\n{sp}\n" if sp else "")
            parts = [base]
            workflow = (getattr(bundle, "workflow_md", "") or "").strip()
            if workflow:
                parts.append("\n----- COLLABORATION WORKFLOW -----\n" + workflow + "\n")
            roster = self._roster_summary(bundle)
            if roster:
                parts.append("\n----- AVAILABLE TEAMMATE ROLES -----\n" + roster + "\n")
            overview = self._skill_overview(bundle)
            if overview:
                parts.append("\n----- SKILL OVERVIEW -----\n" + overview + "\n")
            return "".join(parts)

        return (
            "You are a TEAMMATE in a skill agent team. Follow the leader's task "
            "assignments precisely and report results clearly. Your specific role "
            "and instructions arrive with each task the leader sends you. You have "
            "shell, filesystem, and python tools; work in your working directory and "
            "read skill reference files under ./skill/ when needed.\n"
        )

    @staticmethod
    def _roster_summary(bundle) -> str:
        roles = getattr(bundle, "roles", None) or {}
        lines = []
        for name, doc in roles.items():
            doc_text = (doc or "").strip()
            persona = doc_text.splitlines()[0].strip("# ").strip() if doc_text else ""
            lines.append(f"- {name}: {persona}" if persona else f"- {name}")
        return "\n".join(lines)

    @staticmethod
    def _skill_overview(bundle, max_chars: int = 600) -> str:
        skill_md = (getattr(bundle, "skill_md", "") or "").strip()
        if not skill_md or len(skill_md) <= max_chars:
            return skill_md
        return skill_md[:max_chars].rstrip() + "\n…（完整 SKILL.md 见 ./skill/SKILL.md）"

    async def _provision_skill(self, session: Session, skill_dir: str) -> None:
        bundle = session.extra.get("skill_bundle")

        if bundle is not None and getattr(bundle, "package_bytes", b""):
            import io
            import zipfile

            try:
                zf = zipfile.ZipFile(io.BytesIO(bundle.package_bytes))
            except zipfile.BadZipFile:
                logger.warning("provision: bad zip for %s, falling back to text-only", bundle.asset_id)
                zf = None

            if zf is not None:
                with zf:
                    prefix = getattr(bundle, "archive_strip_prefix", "") or ""
                    # 用层级最浅的 SKILL.md 所在目录当 strip prefix
                    best_parent = None
                    best_depth = None
                    for info2 in zf.infolist():
                        if info2.is_dir():
                            continue
                        name = info2.filename
                        if prefix:
                            name = name[len(prefix):]
                        if name.lower().rsplit("/", 1)[-1] == "skill.md":
                            parent = name.rsplit("/", 1)[0] if "/" in name else ""
                            depth = parent.count("/") + 1 if parent else 0
                            if best_depth is None or depth < best_depth:
                                best_depth = depth
                                best_parent = parent
                    if best_parent:
                        prefix = prefix + best_parent + "/"
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        rel = info.filename[len(prefix):] if prefix else info.filename
                        if not rel or rel.startswith("/") or ".." in rel.split("/"):
                            continue
                        dest = os.path.join(skill_dir, rel)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        dest_path = os.path.normpath(dest)
                        if not dest_path.startswith(os.path.normpath(skill_dir)):
                            continue  # ZipSlip protection
                        with open(dest_path, "wb") as f:
                            f.write(zf.read(info))
            else:
                self._provision_text_fields(bundle, skill_dir)
        elif bundle is not None:
            self._provision_text_fields(bundle, skill_dir)
        else:
            sp = (session.system_prompt or "").strip()
            if sp:
                with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                    f.write(sp)

    @staticmethod
    def _provision_text_fields(bundle, skill_dir: str) -> None:
        if getattr(bundle, "skill_md", ""):
            with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(bundle.skill_md)
        if getattr(bundle, "workflow_md", ""):
            with open(os.path.join(skill_dir, "workflow.md"), "w", encoding="utf-8") as f:
                f.write(bundle.workflow_md)

    def _session_dirs(self, session: Session) -> tuple[str, str]:
        work = os.path.join(settings.workspace_root, session.session_id, "work")
        return work, os.path.join(work, "skill")

    @staticmethod
    def _make_work_config(oj: dict) -> Any:
        return oj["LocalWorkConfig"](
            shell_allowlist=None,
            restrict_to_sandbox=False,
            dangerous_patterns=[],
        )

    async def put_file(self, session: Session, filename: str, content: bytes) -> dict[str, Any]:
        """把上传文件写入 work/uploads/。"""
        work_dir, _ = self._session_dirs(session)
        uploads_dir = os.path.join(work_dir, "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        safe_name = os.path.basename(filename).replace("\\", "/").split("/")[-1]
        if not safe_name or safe_name in (".", ".."):
            raise ValueError("invalid filename")
        dest = os.path.normpath(os.path.join(uploads_dir, safe_name))
        uploads_norm = os.path.normpath(uploads_dir)
        if dest != uploads_norm and not dest.startswith(uploads_norm + os.sep):
            raise ValueError("path escapes uploads dir")
        with open(dest, "wb") as f:
            f.write(content)
        rel = os.path.relpath(dest, work_dir).replace(os.sep, "/")
        logger.info("uploaded file to sandbox: session=%s path=%s size=%d",
                    session.session_id, rel, len(content))
        return {"path": rel, "size": len(content)}

    @staticmethod
    def _translate_chunk(chunk: Any) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        ctype = getattr(chunk, "type", None)
        payload = getattr(chunk, "payload", None) or {}

        role = getattr(chunk, "role", None)
        role_value = getattr(role, "value", role) if role is not None else None
        role_str = str(role_value).strip().lower() if role_value else None
        member = getattr(chunk, "source_member", None)
        member_str = str(member) if member else None

        ctrl = ctype if ctype in ("team.runtime_ready", "team.completed") else (
            payload.get("event_type") if ctype == "message" and isinstance(payload, dict) else None
        )
        if ctrl == "team.runtime_ready":
            return [sse_event(
                "team_ready",
                team_name=payload.get("team_name") if isinstance(payload, dict) else None,
            )]
        if ctrl == "team.completed":
            return [sse_event(
                "team_complete",
                member_count=payload.get("member_count") if isinstance(payload, dict) else None,
                task_count=payload.get("task_count") if isinstance(payload, dict) else None,
            )]

        if ctype == "llm_output":
            text = payload.get("content", "")
            if text:
                events.append(sse_event("text", delta=text))
        elif ctype == "llm_reasoning":
            text = payload.get("content", "")
            if text:
                events.append(sse_event("reasoning", delta=text))
        elif ctype == "tool_call":
            events.append(sse_event(
                "tool_call",
                name=payload.get("tool_name") or payload.get("name"),
                input=payload.get("tool_input") or payload.get("input"),
            ))
        elif ctype == "tool_result":
            events.append(sse_event(
                "tool_result",
                output=payload.get("output") or payload.get("content"),
                error=payload.get("error"),
            ))
        elif ctype == "tracer_agent":
            p = payload if isinstance(payload, dict) else {}
            if not p and isinstance(payload, str):
                def _rgx(pat: str, s: str, grp: int = 1) -> str:
                    m = re.search(pat, s)
                    return m.group(grp).strip("'\\") if m else ""
                tstatus = _rgx(r"'status':\s*'(\w+)'", payload)
                tname = _rgx(r"'name':\s*'([^']+)'", payload)
            else:
                tstatus = p.get("status", "")
                tname = p.get("name", "")
            if tstatus == "start":
                tool_input = p.get("inputs", {}).get("inputs", {}) if p else {}
                events.append(sse_event(
                    "tool_call",
                    name=tname,
                    input=tool_input if isinstance(tool_input, dict) else str(tool_input),
                ))
            elif tstatus in ("end", "finish"):
                tool_output = p.get("outputs", {}).get("outputs", {}) if p else {}
                tool_error = p.get("error") if p else None
                events.append(sse_event(
                    "tool_result",
                    output=tool_output if isinstance(tool_output, (dict, str)) else str(tool_output),
                    error=str(tool_error) if tool_error else None,
                ))
        elif ctype == "llm_usage":
            meta = payload.get("usage_metadata") or {}
            events.append(sse_event(
                "usage",
                input_tokens=meta.get("input_tokens"),
                output_tokens=meta.get("output_tokens"),
                total_tokens=meta.get("total_tokens"),
            ))
        elif ctype == "answer":
            text = payload.get("output") or payload.get("content")
            rtype = payload.get("result_type")
            if rtype == "error":
                events.append(sse_event("error", code="agent_error", message=text))
            elif text:
                events.append(sse_event("answer", content=text))
        else:
            events.append(sse_event(
                "raw", chunk_type=str(ctype), payload=str(payload)[:500],
            ))

        if role_str or member_str:
            for ev in events:
                if role_str:
                    ev.setdefault("role", role_str)
                if member_str:
                    ev.setdefault("member", member_str)
        return events
