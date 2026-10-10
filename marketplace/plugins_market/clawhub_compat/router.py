# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ClawHub CLI-compatible routes on the same FastAPI app and port as marketplace (under /api/v1)."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import zipfile
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.orm import Session

from plugins_market.clawhub_compat import mappers
from plugins_market.clawhub_compat.fingerprint import hash_skill_zip, sanitize_zip_path
from plugins_market.core.cache import cache_delete, cache_set_nx
from plugins_market.core.config import settings
from plugins_market.core.rate_limit import check_clawhub_compat_rate_limit, client_ip_from_scope
from plugins_market.core.database import get_db
from plugins_market.core.errors import PublishError, http_error_payload
from plugins_market.core.logging import get_logger
from plugins_market.core.moderation import SKILL_LIKE_PLUGIN_TYPES, is_skill_like_plugin_type
from plugins_market.core.s3_storage_client import get_storage_client
from plugins_market.repositories import MarketAssetVersionRepository
from plugins_market.schemas.plugin import PluginListQuery
from plugins_market.core.publish_result import is_skill_version_publicly_visible
from plugins_market.core.viewer_context import ANONYMOUS_VIEWER
from plugins_market.services.plugin import (
    get_download_info,
    get_plugin_version_detail_service,
    list_plugins_service,
)
from plugins_market.validation.constants import MAX_FILE_SIZE, ZIP_STREAM_READ_CHUNK_BYTES
from plugins_market.validation.zip_utils import DecompressCounter, safe_read_zip_member, validate_zip_safety

logger = get_logger(__name__)

router = APIRouter()

CLAWHUB_RESOLVE_MAX_VERSIONS = 25
CLAWHUB_RESOLVE_FAILURE_RATIO_THRESHOLD = 0.5
CLAWHUB_DOWNLOAD_TIMEOUT_SECONDS = 600.0
CLAWHUB_INSPECT_FILE_MAX_BYTES = 10 * 1024 * 1024
# Native ClawHub CLI clamps --limit to a max (200); larger values do not 422 — they truncate.
CLAWHUB_LIMIT_CAP = 200


@dataclass(frozen=True)
class ClawhubRequestContext:
    db: Session
    storage: Any


def _get_clawhub_request_context(
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
) -> ClawhubRequestContext:
    return ClawhubRequestContext(db=db, storage=storage)


def _clamp_clawhub_limit(n: int) -> int:
    return min(max(n, 1), CLAWHUB_LIMIT_CAP)


def _publicly_visible_skill_versions(item: Any, rows: list[Any]) -> list[Any]:
    visible_rows: list[Any] = []
    for row in rows:
        if is_skill_version_publicly_visible(
            asset_publish_result=getattr(item, "publish_result", None),
            asset_public_latest_version=getattr(item, "public_latest_version", None),
            version=getattr(row, "version", None),
            version_publish_result=getattr(row, "publish_result", None),
            version_moderation_status=getattr(row, "moderation_status", None),
        ):
            visible_rows.append(row)
    return visible_rows


def _plugin_type_filter() -> Optional[str]:
    """
    ClawHub 兼容接口的 plugin_type 过滤值。

    配置值若解析为 skill-like（含旧别名 ``teamskills`` 与新值 ``swarmskill``），
    自动扩展为完整 skill-like 多值串，避免 swarmskill 派生/迁移后从 ClawHub
    视图中消失；其它取值（如 ``tools``）按原值透传；空值默认返回全部
    skill-like 类型，避免混入非 skill 资产。
    """
    pt = (settings.clawhub_plugin_type or "").strip()
    if not pt:
        return ",".join(sorted(SKILL_LIKE_PLUGIN_TYPES))
    if is_skill_like_plugin_type(pt) or pt.lower() == "teamskills":
        return ",".join(sorted(SKILL_LIKE_PLUGIN_TYPES))
    return pt


def _client_ip(request: Request) -> str:
    # 与限流中间件同源（client_ip_from_scope）：nginx 前置时 peer 是代理 IP，
    # 裸取会让所有用户共享同一个限流桶，集体触发 429。
    return client_ip_from_scope(request.scope, trust_forwarded=settings.rate_limit_trust_forwarded)


def _enforce_clawhub_rate_limit(request: Request) -> None:
    limit = int(settings.clawhub_compat_rate_limit_per_minute)
    if limit <= 0:
        return
    if not check_clawhub_compat_rate_limit(_client_ip(request), limit_per_minute=limit):
        raise HTTPException(status_code=429, detail="rate_limited")


def _safe_error_detail(default: str, detail: Any = None) -> str:
    """Return a short public-safe error message."""
    if isinstance(detail, str):
        msg = detail.strip()
        return msg if msg else default
    if isinstance(detail, dict):
        for key in ("message", "error", "detail"):
            candidate = detail.get(key)
            if isinstance(candidate, str):
                msg = candidate.strip()
                if msg:
                    return msg[:200]
    return default


def _http_exception(status_code: int, message: str, *, error: str) -> HTTPException:
    resolved_error = {
        "skill not found": "skill_not_found",
        "skill has no published version": "skill_version_not_found",
        "version not found": "version_not_found",
        "version required": "version_required",
        "artifact upstream unavailable": "artifact_upstream_unavailable",
        "artifact upstream returned non-2xx": "artifact_upstream_non_2xx",
        "invalid path": "invalid_path",
        "file too large for inspect": "file_too_large_for_inspect",
        "path not found in bundle": "path_not_found_in_bundle",
        "resolve failed due to upstream artifact errors": "resolve_upstream_artifact_errors",
    }.get(message, error)
    return HTTPException(
        status_code=status_code,
        detail=http_error_payload(
            status_code=status_code,
            message=message,
            error=resolved_error,
        ),
    )


def _sync_fetch_bytes(url: str) -> bytes:
    timeout = httpx.Timeout(
        CLAWHUB_DOWNLOAD_TIMEOUT_SECONDS,
        connect=min(30.0, CLAWHUB_DOWNLOAD_TIMEOUT_SECONDS),
    )
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", url) as r:
            r.raise_for_status()
            content_length = r.headers.get("content-length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError:
                    declared = -1
                if declared > MAX_FILE_SIZE:
                    raise OSError(f"artifact exceeds MAX_FILE_SIZE ({MAX_FILE_SIZE} bytes)")
            chunks: list[bytes] = []
            total = 0
            for chunk in r.iter_bytes(ZIP_STREAM_READ_CHUNK_BYTES):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_FILE_SIZE:
                    raise OSError(f"artifact exceeds MAX_FILE_SIZE ({MAX_FILE_SIZE} bytes)")
                chunks.append(chunk)
            return b"".join(chunks)


async def _open_upstream_stream(
    *,
    url: str,
    timeout: httpx.Timeout,
) -> tuple[httpx.AsyncClient, AbstractAsyncContextManager[httpx.Response], httpx.Response]:
    """
    Open upstream stream and fail fast on non-2xx before response headers are sent.
    Returns (client, stream_cm, response) that caller must close.
    """
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    stream_cm = client.stream("GET", url)
    try:
        resp = await stream_cm.__aenter__()
    except Exception as e:
        await client.aclose()
        raise _http_exception(
            status.HTTP_502_BAD_GATEWAY,
            "artifact upstream unavailable",
            error="artifact_upstream_unavailable",
        ) from e

    if resp.status_code < 200 or resp.status_code >= 300:
        await stream_cm.__aexit__(None, None, None)
        await client.aclose()
        raise _http_exception(
            status.HTTP_502_BAD_GATEWAY,
            "artifact upstream returned non-2xx",
            error="artifact_upstream_non_2xx",
        )

    return client, stream_cm, resp


def _find_list_item(
    slug: str,
    *,
    db: Session,
    storage: Any,
):
    pt = _plugin_type_filter()
    direct = list_plugins_service(
        PluginListQuery(page=1, page_size=1, asset_id=slug, plugin_type=pt),
        db,
        storage,
        viewer=ANONYMOUS_VIEWER,
    )
    if direct.items:
        return direct.items[0]
    fuzzy = list_plugins_service(
        PluginListQuery(page=1, page_size=CLAWHUB_LIMIT_CAP, search_keyword=slug, plugin_type=pt),
        db,
        storage,
        viewer=ANONYMOUS_VIEWER,
    )
    for it in fuzzy.items:
        if it.asset_id == slug:
            return it
    return None


@router.get("/search")
def clawhub_search(
    request: Request,
    q: str = Query(..., min_length=1),
    limit: Optional[int] = Query(None, ge=1),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    _enforce_clawhub_rate_limit(request)
    page_size = _clamp_clawhub_limit(limit or 25)
    pt = _plugin_type_filter()
    data = list_plugins_service(
        PluginListQuery(
            page=1,
            page_size=page_size,
            search_keyword=q.strip(),
            order_by="install_count",
            desc=True,
            plugin_type=pt,
        ),
        db,
        storage,
        viewer=ANONYMOUS_VIEWER,
        use_retrieval_search=False,
    )
    results = [mappers.search_result_row(it) for it in data.items]
    return {"results": results}


@router.get("/skills")
def clawhub_explore(
    limit: int = Query(25, ge=1),
    sort: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    order_by, desc = mappers.explore_sort_to_marketplace(sort)
    pt = _plugin_type_filter()
    page_size = _clamp_clawhub_limit(limit)
    data = list_plugins_service(
        PluginListQuery(
            page=1,
            page_size=page_size,
            order_by=order_by,
            desc=desc,
            plugin_type=pt,
        ),
        db,
        storage,
        viewer=ANONYMOUS_VIEWER,
    )
    items = [mappers.explore_item(it) for it in data.items]
    return {"items": items, "nextCursor": None}


@router.get("/skills/{slug}")
def clawhub_skill_meta(
    slug: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    item = _find_list_item(slug, db=db, storage=storage)
    if not item:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "skill not found", error="skill_not_found")
    eff_ver = (item.public_latest_version or item.latest_version or "").strip()
    if not eff_ver:
        raise _http_exception(
            status.HTTP_404_NOT_FOUND,
            "skill has no published version",
            error="skill_version_not_found",
        )
    detail = get_plugin_version_detail_service(
        item.asset_id,
        eff_ver,
        db,
        storage,
        viewer=ANONYMOUS_VIEWER,
    )
    return mappers.skill_detail_bundle(item, detail)


@router.get("/skills/{slug}/versions")
def clawhub_skill_versions(
    slug: str = Path(..., min_length=1),
    limit: int = Query(50, ge=1),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    item = _find_list_item(slug, db=db, storage=storage)
    if not item:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "skill not found", error="skill_not_found")
    vrepo = MarketAssetVersionRepository(db)
    cap = _clamp_clawhub_limit(limit)
    rows = vrepo.list_versions(slug, limit=cap)
    rows = _publicly_visible_skill_versions(item, rows)
    out = [
        mappers.version_list_item(
            r.version,
            r.changelog,
            int(r.create_time or 0),
        )
        for r in rows
    ]
    return {"items": out, "nextCursor": None}


@router.get("/skills/{slug}/versions/{version}")
async def clawhub_skill_version_detail(
    request: Request,
    slug: str = Path(..., min_length=1),
    version: str = Path(..., min_length=1),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    _enforce_clawhub_rate_limit(request)
    item = _find_list_item(slug, db=db, storage=storage)
    if not item:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "skill not found", error="skill_not_found")
    vrepo = MarketAssetVersionRepository(db)
    row = vrepo.get_version(asset_id=slug, version=version)
    if not row:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "version not found", error="version_not_found")
    detail = get_plugin_version_detail_service(slug, version, db, storage, viewer=ANONYMOUS_VIEWER)
    files: list[dict[str, Any]] = []
    try:
        dl = get_download_info(
            asset_id=slug,
            version=version,
            db=db,
            storage=storage,
            fetch_user_id=None,
            viewer=ANONYMOUS_VIEWER,
            counted=False,  # 仅列文件元数据（拉 zip 算哈希），不是真实下载，不计量
        )
        zip_bytes = await asyncio.to_thread(_sync_fetch_bytes, dl.download_url)
        file_rows, _fp = hash_skill_zip(zip_bytes)
        files = [{"path": str(fr["path"]), "sha256": str(fr["sha256"]), "size": int(fr["size"])} for fr in file_rows]
    except Exception as e:
        logger.warning("clawhub version files listing failed slug=%s version=%s: %s", slug, version, e)

    return mappers.skill_version_row(
        detail,
        files,
        created_ms=int(row.create_time or 0),
    )


@router.get("/skills/{slug}/file")
async def clawhub_skill_file(
    request: Request,
    slug: str = Path(..., min_length=1),
    path: str = Query(..., min_length=1, description="Path inside the zip bundle"),
    version: Optional[str] = Query(None),
    context: ClawhubRequestContext = Depends(_get_clawhub_request_context),
):
    _enforce_clawhub_rate_limit(request)
    item = _find_list_item(slug, db=context.db, storage=context.storage)
    if not item:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "skill not found", error="skill_not_found")
    ver = (version or item.public_latest_version or item.latest_version or "").strip()
    if not ver:
        raise _http_exception(status.HTTP_404_NOT_FOUND, "version required", error="version_required")
    try:
        dl = get_download_info(
            asset_id=slug,
            version=ver,
            db=context.db,
            storage=context.storage,
            fetch_user_id=None,
            viewer=ANONYMOUS_VIEWER,
            counted=False,  # 检查文件内容（IDE 查看源码用），不是真实下载，不计量
        )
        zip_bytes = await asyncio.to_thread(_sync_fetch_bytes, dl.download_url)
    except PublishError as e:
        raise _http_exception(
            e.status_code,
            _safe_error_detail("artifact lookup failed", e.detail),
            error="artifact_lookup_failed",
        ) from e

    want = sanitize_zip_path(path.replace("\\", "/"))
    if not want:
        raise _http_exception(status.HTTP_400_BAD_REQUEST, "invalid path", error="invalid_path")

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            validate_zip_safety(zf)
            counter = DecompressCounter()
            for info in zf.infolist():
                if info.filename.endswith("/"):
                    continue
                if sanitize_zip_path(info.filename) != want:
                    continue
                cap = int(CLAWHUB_INSPECT_FILE_MAX_BYTES)
                if info.file_size > cap:
                    raise HTTPException(status_code=413, detail="file too large for inspect")
                raw_data = safe_read_zip_member(zf, info.filename, counter)
                if len(raw_data) > cap:
                    raise _http_exception(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        "file too large for inspect",
                        error="file_too_large_for_inspect",
                    )
                text = raw_data.decode("utf-8", errors="replace")
                return PlainTextResponse(text, media_type="text/plain; charset=utf-8")
    except PublishError as e:
        raise _http_exception(
            status_code=e.status_code,
            message=_safe_error_detail("invalid artifact zip", e.detail),
            error="invalid_artifact_zip",
        ) from e
    raise _http_exception(status.HTTP_404_NOT_FOUND, "path not found in bundle", error="path_not_found_in_bundle")


@router.get("/download")
async def clawhub_download(
    request: Request,
    slug: str = Query(..., min_length=1),
    version: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    # /api/v1/download* 在全局限流中间件里是 exempt，本端点是最重的流式下载路径，
    # 需与同级 clawhub 端点一样走处理器级限流；限流拒绝发生在计量闸门之前，不消耗名额。
    _enforce_clawhub_rate_limit(request)
    # 与主站下载同口径的计量去重闸门：clawhub 兼容端点无登录态，一律匿名按 IP+UA 指纹，
    # 同资产同天只计一次。失败（404 等）时释放闸门，避免失败的首日下载消耗当日名额。
    _ip = client_ip_from_scope(request.scope, trust_forwarded=settings.rate_limit_trust_forwarded)
    _ua = request.headers.get("user-agent", "")
    source_fp = "ip:" + hashlib.sha256(f"{_ip}|{_ua}".encode()).hexdigest()[:16]
    utc_today = datetime.now(timezone.utc).strftime("%Y%m%d")
    gate_key = f"dlcnt:{slug}:{source_fp}:{utc_today}"
    counted_today = cache_set_nx(gate_key, 86400 * 8)
    try:
        info = get_download_info(
            asset_id=slug,
            version=version,
            db=db,
            storage=storage,
            fetch_user_id=None,
            viewer=ANONYMOUS_VIEWER,
            counted=counted_today,
        )
    except PublishError as e:
        if counted_today:
            cache_delete(gate_key)
        raise _http_exception(
            e.status_code,
            _safe_error_detail("artifact lookup failed", e.detail),
            error="artifact_lookup_failed",
        ) from e

    url = info.download_url
    timeout = httpx.Timeout(
        CLAWHUB_DOWNLOAD_TIMEOUT_SECONDS,
        connect=min(30.0, CLAWHUB_DOWNLOAD_TIMEOUT_SECONDS),
    )
    client, stream_cm, resp = await _open_upstream_stream(url=url, timeout=timeout)

    async def body():
        try:
            async for chunk in resp.aiter_bytes(64 * 1024):
                yield chunk
        finally:
            await stream_cm.__aexit__(None, None, None)
            await client.aclose()

    safe_name = (info.name or "skill").replace('"', "")
    filename = f"{safe_name}_{info.version}.zip"
    return StreamingResponse(
        body(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/resolve")
async def clawhub_resolve(
    request: Request,
    slug: str = Query(..., min_length=1),
    fingerprint: str = Query(..., alias="hash", min_length=1),
    db: Session = Depends(get_db),
    storage: Any = Depends(get_storage_client),
):
    _enforce_clawhub_rate_limit(request)
    item = _find_list_item(slug, db=db, storage=storage)
    if not item:
        return {"match": None, "latestVersion": None}

    vrepo = MarketAssetVersionRepository(db)
    max_n = int(CLAWHUB_RESOLVE_MAX_VERSIONS)
    rows = _publicly_visible_skill_versions(item, vrepo.list_versions(slug, limit=max_n))
    if not rows:
        return {"match": None, "latestVersion": None}

    latest = rows[0]
    latest_payload = {"version": latest.version}

    want = fingerprint.strip().lower()
    match_ver: Optional[str] = None
    checked_count = 0
    failed_count = 0

    for row in rows[:max_n]:
        try:
            dl = get_download_info(
                asset_id=slug,
                version=row.version,
                db=db,
                storage=storage,
                fetch_user_id=None,
                viewer=ANONYMOUS_VIEWER,
                counted=False,  # 逐版本拉 zip 算指纹做匹配，不是真实下载，不计量
            )
            zip_bytes = await asyncio.to_thread(_sync_fetch_bytes, dl.download_url)
            _files, fp = hash_skill_zip(zip_bytes)
            checked_count += 1
            if fp.lower() == want:
                match_ver = row.version
                break
        except Exception as e:
            failed_count += 1
            logger.warning("clawhub resolve skip version %s: %s", row.version, e)
            continue

    attempted = checked_count + failed_count
    if match_ver is None and failed_count > 0 and attempted > 0:
        failure_ratio = failed_count / attempted
        if failure_ratio >= CLAWHUB_RESOLVE_FAILURE_RATIO_THRESHOLD:
            raise _http_exception(
                status.HTTP_502_BAD_GATEWAY,
                "resolve failed due to upstream artifact errors",
                error="resolve_upstream_artifact_errors",
            )

    return {
        "match": {"version": match_ver} if match_ver else None,
        "latestVersion": latest_payload,
    }
