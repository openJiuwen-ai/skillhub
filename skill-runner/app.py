# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""skill_runner FastAPI 应用。

本地运行：
    uvicorn skill_runner.app:app --reload --port 8900
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse

from .config import instance_base_url, settings
from .executor import SandboxExecutor, get_executor
from .models import (
    CreateSessionRequest,
    CreateSessionResponse,
    SendMessageRequest,
    SendMessageResponse,
    SessionStatus,
    UploadFileRequest,
    sse_event,
)
from .session_store import store

logger = logging.getLogger("skill_runner")

router = APIRouter(prefix="/playground", tags=["playground"])
_executor: SandboxExecutor | None = None

# ── 后台任务引用集：防止 GC 静默回收 fire-and-forget 任务 ──────────────────────
_bg_tasks: set[asyncio.Task] = set()
# 每 session 正在跑的 _drive_turn 任务；end_session 取消它，停止控制面继续读已被杀 pod 的流。
_drive_tasks: dict[str, asyncio.Task] = {}


def _fire_and_track(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return t


# 对工具输出/文本里的密钥类敏感串做正则替换。
_SECRET_KV_RE = re.compile(
    r"(?i)(api[_-]?key|secret|access[_-]?key|password|passwd|token|authorization)"
    r"([\"']?\s*[=:]\s*[\"']?)([A-Za-z0-9._\-]{6,})"
)
_SECRET_BEARER_RE = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{6,})")
_REDACT_SKIP_KEYS = frozenset({"type", "role", "member", "name", "code", "team_name"})


def _redact_text(s: str) -> str:
    s = _SECRET_BEARER_RE.sub(lambda m: f"{m.group(1)}***", s)
    s = _SECRET_KV_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", s)
    return s


def _redact_event(ev: dict) -> dict:
    return {
        k: (_redact_text(v) if isinstance(v, str) and k not in _REDACT_SKIP_KEYS else v)
        for k, v in ev.items()
    }


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(req: CreateSessionRequest) -> CreateSessionResponse:
    if await store.active_count() >= settings.max_concurrent_sessions:
        raise HTTPException(
            status_code=429,
            detail=f"max concurrent sessions reached ({settings.max_concurrent_sessions})",
        )

    from .skill_loader import SkillBundle

    system_prompt = req.system_prompt
    logger.info(
        "CREATE SESSION: skill_id=%s skill_md=%dB system_prompt=%dB",
        req.skill_id, len(req.skill_md), len(req.system_prompt),
    )
    skill_bundle: SkillBundle | None = None

    if req.skill_md:
        # 用传入的文本字段重建 SkillBundle
        # Decode base64 ZIP bytes if provided by proxy
        pkg_bytes = b""
        if req.package_bytes_b64:
            import base64
            try:
                pkg_bytes = base64.b64decode(req.package_bytes_b64)
            except Exception:
                logger.warning("failed to decode package_bytes_b64 for %s", req.skill_id)
        skill_bundle = SkillBundle(
            asset_id=req.skill_id,
            version=req.version,
            skill_md=req.skill_md,
            workflow_md=req.workflow_md,
            roles=req.roles,
            team_mode=req.team_mode,
            package_bytes=pkg_bytes,
        )
        if not system_prompt:
            system_prompt = skill_bundle.system_prompt_text()
        logger.info(
            "skill content received: %s@%s skill_md=%dB workflow_md=%dB roles=%d",
            req.skill_id, req.version,
            len(req.skill_md), len(req.workflow_md), len(req.roles),
        )
    elif not system_prompt and req.skill_id:
        logger.warning(
            "no skill content in request for %s@%s; session will run without skill context",
            req.skill_id, req.version,
        )

    session = await store.create(req.skill_id, req.version, req.skill_type)
    session.system_prompt = system_prompt or ""
    session.user_id = req.user_id
    if skill_bundle is not None:
        session.extra["skill_bundle"] = skill_bundle

    _fire_and_track(_init_session(session))

    return CreateSessionResponse(
        session_id=session.session_id,
        status=session.status,
        timeout_seconds=settings.session_timeout_seconds,
        # 多实例：回带本实例地址供 marketplace 做会话粘性路由；单实例为空
        instance_addr=instance_base_url(),
    )


async def _init_session(session) -> None:
    if _executor is None:
        raise RuntimeError("executor not initialized")
    try:
        await _executor.create(session)
        session.status = SessionStatus.READY
        await session.events.put(sse_event("ready"))
        _llm_warmup = getattr(_executor, "llm_warmup", None)
        if _llm_warmup is not None:
            _fire_and_track(_llm_warmup(session))
    except Exception as exc:
        logger.exception("session create failed: %s", session.session_id)
        session.status = SessionStatus.ERROR
        await session.events.put(sse_event("error", code="create_failed", message=str(exc)))
        await session.events.put(sse_event("done"))


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse)
async def send_message(session_id: str, req: SendMessageRequest) -> SendMessageResponse:
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session.status == SessionStatus.STARTING:
        raise HTTPException(status_code=503, detail="session still initializing")
    if session.status in (SessionStatus.DONE, SessionStatus.ERROR):
        raise HTTPException(status_code=410, detail="session already ended")
    if len(req.content) > settings.message_max_chars:
        raise HTTPException(status_code=400, detail="message too long")
    if session.turn_count >= settings.message_max_turns:
        raise HTTPException(status_code=429, detail="turn limit reached")
    running = _drive_tasks.get(session_id)
    if running is not None and not running.done():
        # 上一轮还在跑，并发写同一 events 队列会交错
        raise HTTPException(status_code=409, detail="previous turn still running")

    session.turn_count += 1
    session.status = SessionStatus.ACTIVE
    session.touch()

    _drive_tasks[session_id] = _fire_and_track(_drive_turn(session_id, req.content))
    return SendMessageResponse(message_id=f"msg-{uuid.uuid4().hex[:12]}")


def _emit_terminal(session, event: dict) -> None:
    """投递收尾事件：非阻塞，队列满时丢弃（阻塞 put 会让 finally 卡死）。"""
    try:
        session.events.put_nowait(event)
    except asyncio.QueueFull:
        logger.debug("turn %s: events queue full, drop terminal event %s",
                     session.session_id, event.get("type"))


async def _drive_turn(session_id: str, content: str) -> None:
    session = await store.get(session_id)
    if session is None:
        return
    try:
        async for event in _executor.run_turn(session, content):
            try:
                await asyncio.wait_for(
                    session.events.put(_redact_event(event)),
                    timeout=settings.sse_put_timeout_seconds,
                )
            except asyncio.TimeoutError:
# 消费者已断连，中止本轮释放 pod
                logger.warning(
                    "turn %s: SSE consumer stalled >%.0fs, aborting turn to free resources",
                    session_id, settings.sse_put_timeout_seconds,
                )
                break
            session.touch()
    except Exception as exc:
        logger.exception("turn failed: %s", session_id)
        _emit_terminal(session, sse_event("error", code="turn_failed", message=_redact_text(str(exc))))
    finally:
        _drive_tasks.pop(session_id, None)
        _emit_terminal(session, sse_event("done"))


@router.post("/sessions/{session_id}/files")
async def upload_file(session_id: str, req: UploadFileRequest) -> dict:
    """写入上传文件到会话沙箱（安全校验已在 marketplace 完成）。"""
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session.status in (SessionStatus.DONE, SessionStatus.ERROR):
        raise HTTPException(status_code=410, detail="session already ended")
    import base64

    try:
        content = base64.b64decode(req.content_b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 content") from exc
    if len(content) > settings.upload_max_file_bytes:
        raise HTTPException(status_code=413, detail="file too large")
    # 会话累计限制：防单会话占满 pod 磁盘
    up_count = session.extra.get("upload_count", 0)
    up_bytes = session.extra.get("upload_bytes", 0)
    if up_count >= settings.upload_max_files_per_session:
        raise HTTPException(status_code=429, detail="upload file count limit reached")
    if up_bytes + len(content) > settings.upload_max_total_bytes:
        raise HTTPException(status_code=413, detail="session upload size limit reached")
    try:
        result = await _executor.put_file(session, req.filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail="upload not supported by this executor") from exc
    session.extra["upload_count"] = up_count + 1
    session.extra["upload_bytes"] = up_bytes + len(content)
    session.touch()
    return result


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    """查询会话状态。"""
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "session_id": session.session_id,
        "status": session.status,
        "skill_type": session.skill_type,
        "turn_count": session.turn_count,
        "timeout_seconds": settings.session_timeout_seconds,
    }


@router.get("/sessions/{session_id}/stream")
async def stream(session_id: str) -> StreamingResponse:
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    async def gen():
        event_id = 0
        try:
            while True:
                try:
                    event = await asyncio.wait_for(session.events.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                except asyncio.CancelledError:
                    break
                event_id += 1
                payload = json.dumps(event, ensure_ascii=False)
                yield f"id: {event_id}\ndata: {payload}\n\n"
                # done = 一轮结束，连接不关闭以支持多轮；
                # session_ended = 会话结束，断开连接
                if event.get("type") in ("done", "session_ended"):
                    if event.get("type") == "session_ended":
                        break
        except asyncio.CancelledError:
            pass
        finally:
            logger.debug("SSE gen exiting for session=%s", session_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/sessions/{session_id}/beacon")
async def end_session_beacon(session_id: str) -> Response:
    """sendBeacon 入口，逻辑同 DELETE。"""
    return await end_session(session_id)


@router.delete("/sessions/{session_id}")
async def end_session(session_id: str) -> Response:
    session = await store.get(session_id)
    if session is not None:
        drive = _drive_tasks.pop(session_id, None)
        if drive is not None and not drive.done():
            drive.cancel()
            # 等待 finally 清理完成再 destroy，避免竞态
            try:
                await drive
            except asyncio.CancelledError:
                pass
        try:
            session.events.put_nowait(sse_event("session_ended"))
        except asyncio.QueueFull:
            logger.debug(
                "end_session: SSE queue full when emitting session_ended for %s",
                session_id,
            )
        except Exception:
            logger.warning(
                "end_session: failed to enqueue session_ended for %s",
                session_id,
                exc_info=True,
            )
        await _executor.destroy(session)
    else:
        logger.warning("end_session: session %s not found in store (pod may have leaked)", session_id)
    await store.end(session_id)
    from .llm_proxy import clear_session_tokens
    clear_session_tokens(session_id)
    return Response(status_code=204)


# ---- 应用装配 ----



async def _idle_session_reaper(interval_seconds: int = 60) -> None:
    """周期性回收空闲超时的会话。"""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            now = time.time()
            for session in await store.snapshot():
                if session.status in (SessionStatus.DONE, SessionStatus.ERROR):
                    continue
                if now - session.last_active_at < settings.session_timeout_seconds:
                    continue
                logger.info(
                    "idle reaper: ending stale session %s (idle %.0fs)",
                    session.session_id, now - session.last_active_at,
                )
                # 先置 DONE 避免与 end_session 重复 destroy
                session.status = SessionStatus.DONE
                if session.events is not None:
                    try:
                        session.events.put_nowait(sse_event("session_ended"))
                    except asyncio.QueueFull:
                        logger.debug(
                            "idle reaper: events queue full for %s, drop session_ended notice",
                            session.session_id,
                        )
                drive = _drive_tasks.pop(session.session_id, None)
                if drive is not None and not drive.done():
                    drive.cancel()
                    try:
                        await drive
                    except asyncio.CancelledError:
                        pass
                try:
                    await _executor.destroy(session)
                except Exception:
                    logger.warning("idle reaper: destroy failed for %s", session.session_id)
                await store.end(session.session_id)
                from .llm_proxy import clear_session_tokens
                clear_session_tokens(session.session_id)
        except Exception:
            logger.exception("idle reaper error (non-fatal)")


@asynccontextmanager
async def lifespan(api_app: FastAPI):
    global _executor
    from .log_redaction import configure_sensitive_log_redaction
    configure_sensitive_log_redaction()
    _executor = get_executor()
    logger.info("skill-runner started: in-process session state, single-replica control plane")

    warm = getattr(_executor, "warm", None)
    if warm is not None:
        async def _warm_bg() -> None:
            try:
                await warm()
                logger.info("executor pool warmed")
            except Exception:
                logger.warning("executor pool warm failed, continuing without pre-warmed pods")
        _fire_and_track(_warm_bg())

    reaper_task = None
    start_reaper = getattr(_executor, "start_pod_reaper", None)
    if start_reaper is not None:
        reaper_task = asyncio.create_task(start_reaper(interval_seconds=120))
        logger.info("pod reaper task started")

    idle_task = asyncio.create_task(_idle_session_reaper())
    logger.info("idle session reaper started")

    yield

    if reaper_task is not None:
        reaper_task.cancel()
    idle_task.cancel()
    aclose = getattr(_executor, "aclose", None)
    if aclose is not None:
        await aclose()


app = FastAPI(title="skill-runner", version="0.1.0", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")

# k8s executor 的 pod 经此代理调 LLM
from .llm_proxy import proxy_router

app.include_router(proxy_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "executor": settings.executor}
