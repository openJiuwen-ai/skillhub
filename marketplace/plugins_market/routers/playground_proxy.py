# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Playground 反向代理：/api/v1/playground/* → skill-runner。"""
from __future__ import annotations

import asyncio
import io
import json
import secrets
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from plugins_market.core.auth import AuthContext, require_auth
from plugins_market.core.config import settings
from plugins_market.core.database import get_db
from plugins_market.core.logging import get_logger
from plugins_market.core.playground_state import (
    CreateLockTimeout,
    PlaygroundSessionRecord,
    get_playground_state_store,
)
from plugins_market.repositories.playground_quota_repository import PlaygroundQuotaRepository

logger = get_logger(__name__)

# 会话跟踪状态走 state store：单实例=内存，多实例=Redis
_store = get_playground_state_store

# 进程级单例，避免每次验活新建连接池
_probe_client: httpx.AsyncClient | None = None


def _get_probe_client() -> httpx.AsyncClient:
    global _probe_client
    if _probe_client is None:
        _probe_client = httpx.AsyncClient(timeout=5.0)
    return _probe_client


def _runner_url(path: str = "", base: str = "") -> str:
    """拼 skill-runner URL，base 为空走 Service 地址。"""
    base = (base or settings.skill_runner_url).rstrip("/")
    if not path:
        return f"{base}/api/v1/playground"
    return f"{base}/api/v1/playground/{path.lstrip('/')}"


async def _session_runner_base(session_id: str) -> str:
    """会话所在实例基址；无记录返回空串。"""
    rec = await _store().get_session(session_id)
    return rec.runner_addr if rec else ""


def _runner_unavailable() -> HTTPException:
    """统一 503 报错。"""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": "skill_runner_unavailable",
                "message": "控制面暂不可达，请稍后重试"},
    )


async def _release_session(session_id: str) -> None:
    """释放并发计数与令牌记录。"""
    await _store().release_session(session_id)


async def _check_session_token(session_id: str, request: Request) -> bool:
    """能力令牌校验：EventSource/sendBeacon 带不了 header，用 ?pt= 查询参数。"""
    rec = await _store().get_session(session_id)
    if rec is None or not rec.token:
        return True
    supplied = request.query_params.get("pt", "")
    return secrets.compare_digest(supplied, rec.token)


async def _ensure_session_owner(session_id: str, auth: AuthContext) -> None:
    """归属校验：记录不存在时放行（避免重启锁死）。"""
    if auth.is_admin:
        return
    rec = await _store().get_session(session_id)
    if rec is not None and rec.user_id and rec.user_id != auth.acting_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "not_your_session", "message": "无权操作他人的 Playground 会话"},
        )


def _refund_quota(quota_repo: PlaygroundQuotaRepository | None, user_id: str) -> None:
    """退回一次已扣配额。"""
    if quota_repo is None:
        return
    try:
        quota_repo.decrement(user_id)
    except Exception:
        logger.warning("playground quota refund failed for %s", user_id)


# 调用方不得自带的执行内容字段：这些只能由 marketplace 注入（含审核状态校验），
# 否则请求体直接自带 skill_md/system_prompt 就能绕过 moderation 执行未过审内容。
_CLIENT_FORBIDDEN_FIELDS = (
    "skill_md", "workflow_md", "roles", "system_prompt", "package_bytes_b64", "team_mode",
)


def _strip_client_content(body: bytes) -> bytes:
    """剥掉非 admin 请求体里自带的执行内容字段，强制走 marketplace 注入链路。"""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body  # 非 JSON 交给 skill-runner 自行拒绝
    if not isinstance(payload, dict):
        return body
    dropped = [k for k in _CLIENT_FORBIDDEN_FIELDS if payload.pop(k, None) is not None]
    if not dropped:
        return body
    logger.warning("playground proxy: stripped client-supplied content fields %s", dropped)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _inject_user_id(body: bytes, user_id: str) -> bytes:
    """把 user_id 注入 JSON body 供 skill-runner token 计量。

    解析或序列化失败时返回原 body（保留旧行为：转发由 skill-runner 自行处理），
    但记一条 warning 便于排查 Content-Type 异常或上游格式问题。
    """
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("playground proxy: cannot decode body to inject user_id: %s", exc)
        return body
    if not isinstance(payload, dict):
        logger.warning("playground proxy: body is not a JSON object, skip user_id inject")
        return body
    payload["user_id"] = user_id
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


async def _session_still_alive(session_id: str) -> bool:
    """验活：True=仍在用，False=可释放。404/done/error=失活；实例级不可达=实例已死。"""
    base = await _session_runner_base(session_id)
    client = _get_probe_client()
    try:
        r = await client.get(_runner_url(f"sessions/{session_id}", base=base))
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        if base:
            # 实例级地址不可达=实例已死
            logger.warning(
                "skill-runner instance %s unreachable for session=%s; treat session as dead",
                base, session_id,
            )
            return False
        # Service 地址探测失败：不放行，防绕过并发限制
        logger.warning("skill-runner probe network error for session=%s; deny new", session_id)
        raise _runner_unavailable() from exc
    except httpx.HTTPError as exc:
        # 其它 httpx 异常：保守拒绝
        logger.warning("skill-runner probe http error: %s: %s", type(exc).__name__, exc)
        raise _runner_unavailable() from exc

    if r.status_code == 404:
        return False  # idle reaper 已回收，proxy 端清记录、放行新建
    if r.status_code >= 500:
        # 控制面 5xx：偏保守不放行；让用户重试或联系运维
        logger.warning("skill-runner probe 5xx for session=%s status=%s", session_id, r.status_code)
        raise _runner_unavailable()
    if r.status_code >= 400:
        # 4xx 非 404：当作仍活着
        logger.warning("skill-runner probe 4xx for session=%s status=%s; treat as alive",
                       session_id, r.status_code)
        return True
    try:
        status_val = r.json().get("status")
    except ValueError:
        # 2xx 非 JSON：当作仍活着
        return True
    return status_val not in ("done", "error")


router = APIRouter(prefix="/playground", tags=["playground"])


# ── Helpers ────────────────────────────────────────────────────────────────────

# team_mode 推导规则，与 TeamAgentSpec 的自动推导对齐：
#   有 roles/*.md + roles 里有 count 范围  → "hybrid"，保留预定义名单，同时允许 spawn_member
#   有 roles/*.md + 无 count 范围          → ""，skill-runner 侧返回 "predefined"，锁定名单
#   无 roles/*.md                           → ""，skill-runner 侧返回 None，框架自动推导 "default"
#   frontmatter 显式声明 team_mode          → 尊重声明，最高优先级
_VALID_TEAM_MODES = frozenset({"default", "predefined", "hybrid"})


def _extract_team_mode(skill_md: str) -> str:
    """从 frontmatter 推导 team_mode；失败返回空串交由 skill-runner 决策。"""
    if not skill_md:
        return ""
    try:
        from plugins_market.validation.types.skill import parse_skill_frontmatter

        fm, _ = parse_skill_frontmatter(skill_md.encode("utf-8"))
    except Exception as exc:
        logger.warning(
            "playground: skill frontmatter parse failed, fallback to default team_mode: %s: %s",
            type(exc).__name__, exc,
        )
        return ""
    # ① 显式声明
    val = fm.get("team_mode")
    if isinstance(val, str) and val.strip().lower() in _VALID_TEAM_MODES:
        return val.strip().lower()
    # ② count 范围推导：任意 role 的 count 为列表 → hybrid
    roles = fm.get("roles")
    if isinstance(roles, list):
        for role in roles:
            if isinstance(role, dict) and isinstance(role.get("count"), list):
                return "hybrid"
    return ""


def _parse_skill_zip(data: bytes) -> tuple[str, str, dict[str, str], str]:
    """Parse ZIP → (skill_md, workflow_md, roles, team_mode)。"""
    from plugins_market.validation.zip_utils import (
        DecompressCounter,
        safe_read_zip_member,
        validate_zip_safety,
    )

    skill_md = workflow_md = ""
    roles: dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            validate_zip_safety(zf)  # 元数据预检，异常包快速拒绝
            counter = DecompressCounter()  # 跨成员累计解压字节，超限即 abort
            for info in zf.infolist():
                if info.is_dir():
                    continue
                parts = info.filename.replace("\\", "/").split("/")
                basename_lower = parts[-1].lower()
                parent_lower = parts[-2].lower() if len(parts) >= 2 else ""
                # 只关心这三类文本，其余成员不读，省解压
                want = (
                    (basename_lower == "skill.md" and not skill_md)
                    or (basename_lower == "workflow.md" and not workflow_md)
                    or (parent_lower == "roles" and basename_lower.endswith(".md"))
                )
                if not want:
                    continue
                try:
                    content = safe_read_zip_member(zf, info.filename, counter).decode("utf-8", "replace")
                except Exception as exc:
                    logger.warning(
                        "playground proxy: skip unreadable zip member %s: %s: %s",
                        info.filename, type(exc).__name__, exc,
                    )
                    continue
                if basename_lower == "skill.md" and not skill_md:
                    skill_md = content
                elif basename_lower == "workflow.md" and not workflow_md:
                    workflow_md = content
                elif parent_lower == "roles" and basename_lower.endswith(".md"):
                    role_name = parts[-1].rsplit(".", 1)[0]
                    roles[role_name] = content
    except zipfile.BadZipFile as exc:
        logger.warning("playground proxy: not a valid zip, skip injection: %s", exc)
    except Exception as exc:
        logger.warning("playground proxy: zip safety check failed, skip injection: %s", exc)
        return "", "", {}, ""
    return skill_md, workflow_md, roles, _extract_team_mode(skill_md)


async def _inject_skill_content(body: bytes) -> bytes:
    """解析 skill ZIP 并注入请求体，skill-runner 收到纯文本无需凭证。"""
    try:
        payload = json.loads(body)
    except Exception:
        return body

    skill_id = payload.get("skill_id", "")
    version = payload.get("version", "latest")
    # 已有内容 / 无 skill_id / 调用方已自行提供 system_prompt 时跳过
    if not skill_id or payload.get("skill_md") or payload.get("system_prompt"):
        return body

    try:
        from plugins_market.core.database import SessionLocal
        from plugins_market.core.s3_storage_client import get_storage_client
        from plugins_market.repositories.market_assets_repository import (
            MarketAssetRepository,
            MarketAssetVersionRepository,
        )
        from plugins_market.services.skill_review_runtime import (
            build_package_name,
            download_archive,
            resolve_archive_key,
        )
    except ImportError as exc:
        logger.warning("playground proxy: import failed, skip skill content injection: %s", exc)
        return body

    storage = get_storage_client()
    if storage is None:
        logger.warning("playground proxy: storage client not ready, skip skill content injection")
        return body

    try:
        import tempfile
        from pathlib import Path

        with SessionLocal() as db:
            asset = MarketAssetRepository(db).get_by_asset_id(skill_id)
            if asset is None:
                logger.warning("playground proxy: asset not found: %s", skill_id)
                return body
            version_repo = MarketAssetVersionRepository(db)
            if not version or version == "latest":
                row = version_repo.get_latest_version(asset_id=skill_id)
            else:
                row = version_repo.get_version(asset_id=skill_id, version=version)
            if row is None:
                logger.warning("playground proxy: version not found: %s@%s", skill_id, version)
                return body
            version_moderation = getattr(row, "moderation_status", None)
            if version_moderation not in (None, "APPROVED"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "skill_not_approved",
                        "message": f"该 Skill 版本尚未通过审核（状态：{version_moderation}），无法在 Playground 中执行",
                    },
                )
            package_name = build_package_name(asset, row)
            archive_key = resolve_archive_key(storage, row.file_path, package_name)
            resolved_version = getattr(row, "version", version)

        # S3 下载放线程池
        def _download() -> bytes:
            with tempfile.TemporaryDirectory(prefix="playground_proxy_") as tmp:
                local = Path(tmp) / package_name
                download_archive(storage, archive_key, local)
                return local.read_bytes()

        zip_bytes = await asyncio.to_thread(_download)


        skill_md, workflow_md, roles, team_mode = _parse_skill_zip(zip_bytes)
        payload["skill_md"] = skill_md
        payload["workflow_md"] = workflow_md
        payload["roles"] = roles
        # 仅在 skill 显式声明时透传；"" 表示交给 skill-runner 自动推导，老 skill 零影响
        if team_mode:
            payload["team_mode"] = team_mode
        payload["version"] = resolved_version
        # ZIP 作为 base64 注入，超 MAX_FILE_SIZE 则只带文本
        from plugins_market.validation.constants import MAX_FILE_SIZE
        if len(zip_bytes) <= MAX_FILE_SIZE:
            import base64
            payload["package_bytes_b64"] = base64.b64encode(zip_bytes).decode("ascii")
        else:
            logger.warning(
                "playground proxy: package %s@%s too large for inline transfer (%d bytes), text fields only",
                skill_id, resolved_version, len(zip_bytes),
            )
        logger.info(
            "playground proxy: injected skill content %s@%s skill_md=%dB workflow_md=%dB roles=%d team_mode=%s zip=%dB",
            skill_id, resolved_version, len(skill_md), len(workflow_md), len(roles), team_mode or "-", len(zip_bytes),
        )
    except HTTPException:
        # 审核拦截等显式 HTTP 错误必须透传给调用方，不能被下面的兜底吞掉
        # 否则未过审 skill 会被静默放行、403 失效。
        raise
    except Exception as exc:
        logger.warning("playground proxy: skill content injection failed for %s: %s", skill_id, exc)

    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


# 逐跳（hop-by-hop）头部：转发时必须剔除，由各自的 HTTP 连接自行协商。
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)


def _filtered_headers(headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _next_midnight_utc() -> str:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.isoformat()


async def _forward(
    request: Request,
    target: str,
    body: bytes,
    extra_headers: dict | None = None,
    *,
    stream: bool = False,
) -> Response:
    """转发请求到 skill-runner。stream=True 返回 StreamingResponse。"""
    fwd_headers = _filtered_headers(request.headers)
    if extra_headers:
        fwd_headers.update(extra_headers)
    # 令牌不外泄给上游
    fwd_params = [(k, v) for k, v in request.query_params.multi_items() if k != "pt"]

    timeout = httpx.Timeout(
        connect=5.0,
        read=None if stream else 300.0,
        write=30.0,
        pool=10.0,
    )

    if stream:
        client = httpx.AsyncClient(timeout=timeout)
        upstream_req = client.build_request(
            request.method,
            target,
            params=fwd_params,
            headers=fwd_headers,
            content=body,
        )
        try:
            upstream = await client.send(upstream_req, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            logger.warning("playground stream proxy unreachable: %s (url=%s)", exc, target)
            return Response(status_code=502, content=b"skill-runner unreachable")

        async def _body_iter():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            _body_iter(),
            status_code=upstream.status_code,
            headers=_filtered_headers(upstream.headers),
            media_type=upstream.headers.get("content-type"),
        )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream = await client.request(
                request.method,
                target,
                params=fwd_params,
                headers=fwd_headers,
                content=body,
            )
    except httpx.HTTPError as exc:
        logger.warning("playground proxy unreachable: %s (url=%s)", exc, target)
        return Response(status_code=502, content=b"skill-runner unreachable")

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filtered_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


@asynccontextmanager
async def _create_lock_or_429(user_id: str):
    """创建锁：超时转 429。"""
    try:
        async with _store().create_lock(user_id):
            yield
    except CreateLockTimeout:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "create_busy", "message": "创建请求过于频繁，请稍后重试"},
            headers={"Retry-After": "5"},
        ) from None


async def _register_created_session(resp: Response, user_id: str) -> tuple[Response, str]:
    """登记归属/路由 + 下发令牌。返回 (响应, session_id)。"""
    if resp.status_code >= 300:
        return resp, ""
    try:
        payload = json.loads(resp.body) if resp.body else None
    except (json.JSONDecodeError, AttributeError) as exc:
        logger.warning("playground proxy: cannot parse session create response: %s", exc)
        payload = None
    if not isinstance(payload, dict) or not payload.get("session_id"):
        logger.error("playground proxy: 2xx create response without session_id")
        return resp, ""
    new_sid = str(payload["session_id"])
    # 剥掉 instance_addr 不外泄给浏览器
    runner_addr = str(payload.pop("instance_addr", "") or "")
    token = secrets.token_urlsafe(24)
    await _store().register_session(
        new_sid,
        PlaygroundSessionRecord(user_id=user_id, token=token, runner_addr=runner_addr),
        ttl_seconds=settings.playground_session_ttl_seconds,
    )
    payload["proxy_token"] = token

    headers = {k: v for k, v in resp.headers.items()
               if k.lower() not in ("content-length", "content-type")}
    return JSONResponse(payload, status_code=resp.status_code, headers=headers), new_sid


# ── Authenticated routes (registered before catch-all) ────────────────────────

@router.get("/quota")
async def playground_quota(
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """返回当前用户今日配额使用情况。"""
    limit = settings.playground_daily_limit
    reset_at = _next_midnight_utc()

    if auth.is_admin:
        return JSONResponse({
            "used": 0,
            "limit": limit,
            "is_unlimited": True,
            "reset_at": reset_at,
        })

    used = PlaygroundQuotaRepository(db).get_usage(auth.acting_user_id)
    return JSONResponse({
        "used": used,
        "limit": limit,
        "is_unlimited": limit == 0,
        "reset_at": reset_at,
    })


@router.post("/sessions")
async def create_session(
    request: Request,
    auth: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
) -> Response:
    """创建 session：鉴权 + 并发检查 + 配额。"""
    body = await request.body()

    # _inject_skill_content 在 lock 外执行，避免 ZIP 下载阻塞并发请求
    if not auth.is_admin:
        body = _strip_client_content(body)
    body = await _inject_skill_content(body)
    body = _inject_user_id(body, auth.acting_user_id)

    if auth.is_admin:
        # admin 无并发/配额限制
        resp = await _forward(request, _runner_url("sessions"), body)
        resp, _ = await _register_created_session(resp, auth.acting_user_id)
        return resp

    # 非 admin：锁保护并发检查和配额扣减
    async with _create_lock_or_429(auth.acting_user_id):
        # 并发检查：先剔除已死 session
        max_concurrent = settings.playground_max_concurrent_sessions
        active_list = await _store().list_user_sessions(auth.acting_user_id)
        # 并行验活
        check_tasks = [asyncio.create_task(_session_still_alive(sid)) for sid in active_list]
        results = await asyncio.gather(*check_tasks, return_exceptions=True)
        for sid, alive in zip(active_list, results):
            if isinstance(alive, Exception) or not alive:
                await _release_session(sid)
        active = set(await _store().list_user_sessions(auth.acting_user_id))
        if max_concurrent > 0 and len(active) >= max_concurrent:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "session_conflict",
                    "message": f"您已有 {len(active)} 个活跃的 Playground 会话（上限 {max_concurrent}），请先结束部分会话再新建",
                    "active_session_ids": sorted(active),
                },
            )

        # 每日配额
        limit = settings.playground_daily_limit
        reset_at = _next_midnight_utc()
        quota_repo = None
        extra_resp_headers: dict[str, str] = {}
        if limit > 0:
            quota_repo = PlaygroundQuotaRepository(db)
            allowed, used = quota_repo.try_increment(auth.acting_user_id, limit)
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "quota_exceeded",
                        "message": f"每日 Playground 试用次数已达上限 {limit} 次，明日 0 点重置",
                        "used": used,
                        "limit": limit,
                    },
                    headers={
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": reset_at,
                    },
                )
            extra_resp_headers = {
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": str(limit - used),
                "X-RateLimit-Reset": reset_at,
            }

        # 转发；失败则退回配额
        resp = await _forward(request, _runner_url("sessions"), body)
        if resp.status_code >= 300:
            _refund_quota(quota_repo, auth.acting_user_id)
            return resp

        # 登记归属/路由 + 下发令牌
        resp, new_sid = await _register_created_session(resp, auth.acting_user_id)
        if not new_sid:
            _refund_quota(quota_repo, auth.acting_user_id)
            return resp
        for k, v in extra_resp_headers.items():
            resp.headers[k] = v
        return resp


# ── Per-user message rate limit (registered before catch-all) ─────────────────

@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    request: Request,
    auth: AuthContext = Depends(require_auth),
) -> Response:
    """发送消息：归属校验 + 限流 + 转发。"""
    await _ensure_session_owner(session_id, auth)
    per_min = settings.playground_message_rate_per_minute
    if per_min > 0 and not auth.is_admin:
        if not await _store().allow_message(auth.acting_user_id, per_min, 60.0):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "rate_limited",
                    "message": f"发送过于频繁，每分钟最多 {per_min} 条，请稍后再试",
                },
                headers={"Retry-After": "60"},
            )
    body = await request.body()
    base = await _session_runner_base(session_id)
    return await _forward(request, _runner_url(f"sessions/{session_id}/messages", base=base), body)


# ── File upload (registered before catch-all) ─────────────────────────────────

# 可执行文件 magic bytes 黑名单：Playground 上传的是给 skill 分析的数据文件，
# 不该是可执行体。拒绝这些前缀做纵深防护（沙箱隔离是主防线）。
_EXECUTABLE_MAGIC = (
    b"\x7fELF",            # ELF：Linux 可执行 / .so
    b"MZ",                # DOS/PE：.exe / .dll
    b"\xcf\xfa\xed\xfe",  # Mach-O 64 LE
    b"\xce\xfa\xed\xfe",  # Mach-O 32 LE
    b"\xca\xfe\xba\xbe",  # Java class / Mach-O fat
    b"#!",                # shebang 脚本
    b"\x00asm",           # WebAssembly
)


def _sanitize_upload_filename(raw: str) -> str:
    """取 basename 并拒绝路径穿越。"""
    name = (raw or "").replace("\\", "/").split("/")[-1].strip()
    if not name or name in (".", "..") or any(ord(c) < 32 for c in name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_filename", "message": "非法的文件名"},
        )
    return name[:255]


@router.post("/sessions/{session_id}/files")
async def upload_file(
    session_id: str,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_auth),
) -> JSONResponse:
    """上传文件到会话沙箱。"""
    await _ensure_session_owner(session_id, auth)
    safe_name = _sanitize_upload_filename(file.filename or "")

    max_bytes = settings.playground_upload_max_file_bytes
    # 读到上限+1字节判超限
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "file_too_large",
                    "message": f"单文件大小超过上限（最大 {max_bytes // (1024 * 1024)} MB）"},
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "empty_file", "message": "空文件"},
        )
    if any(content.startswith(sig) for sig in _EXECUTABLE_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "executable_rejected", "message": "不允许上传可执行文件"},
        )

    import base64

    payload = json.dumps({
        "filename": safe_name,
        "content_b64": base64.b64encode(content).decode("ascii"),
    }).encode("utf-8")
    base = await _session_runner_base(session_id)
    target = _runner_url(f"sessions/{session_id}/files", base=base)
    timeout = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream = await client.post(
                target, content=payload, headers={"content-type": "application/json"},
            )
    except httpx.HTTPError as exc:
        logger.warning("playground upload proxy unreachable: %s (url=%s)", exc, target)
        raise _runner_unavailable() from exc
    if upstream.status_code >= 400:
        try:
            detail = upstream.json()
        except ValueError:
            detail = {"error": "upload_failed", "message": (upstream.text or "")[:200]}
        return JSONResponse(detail, status_code=upstream.status_code)
    return JSONResponse(upstream.json(), status_code=upstream.status_code)


# ── Session lifecycle: explicit routes to maintain concurrent-session counter ──

def _should_release_session(status_code: int, base: str) -> bool:
    """2xx/404 释放；502 仅多实例粘性地址释放。"""
    if status_code < 300 or status_code == 404:
        return True
    if base and status_code == 502:
        return True
    return False


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    request: Request,
    auth: AuthContext = Depends(require_auth),
) -> Response:
    """结束 session：校验归属 + 转发 + 释放计数。"""
    await _ensure_session_owner(session_id, auth)
    body = await request.body()
    base = await _session_runner_base(session_id)
    resp = await _forward(request, _runner_url(f"sessions/{session_id}", base=base), body)
    if _should_release_session(resp.status_code, base):
        await _release_session(session_id)
    return resp


@router.post("/sessions/{session_id}/beacon")
async def session_beacon(session_id: str, request: Request) -> Response:
    """sendBeacon 入口，同 DELETE。凭 ?pt= 令牌校验。"""
    if not await _check_session_token(session_id, request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "invalid_session_token", "message": "无效的会话令牌"},
        )
    body = await request.body()
    base = await _session_runner_base(session_id)
    resp = await _forward(request, _runner_url(f"sessions/{session_id}/beacon", base=base), body)
    if _should_release_session(resp.status_code, base):
        await _release_session(session_id)
    return resp


# ── Transparent catch-all proxy ────────────────────────────────────────────────

@router.api_route("/{path:path}", methods=["GET", "POST", "DELETE"])
async def playground_proxy(path: str, request: Request) -> Response:
    # 会话级路径凭 ?pt= 令牌放行
    parts = path.split("/")
    base = ""
    if parts[0] == "sessions" and len(parts) >= 2 and parts[1]:
        if not await _check_session_token(parts[1], request):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "invalid_session_token", "message": "无效的会话令牌"},
            )
        base = await _session_runner_base(parts[1])
    body = await request.body()
    # SSE 需要流式透传
    is_stream = request.method == "GET" and path.endswith("/stream")
    return await _forward(request, _runner_url(path, base=base), body, stream=is_stream)
