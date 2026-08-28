# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
GitHub REST 代理转发：使用用户的 Bearer access_token 调 api.github.com。

面向「一键标星 openjiuwen 仓库」特性，封装：
- 标星仓库（PUT /user/starred/{owner}/{repo}，幂等）

注：早期版本曾封装 list_org_repos（列出组织公开仓库），后改为固定仓库清单
（见 routers/github_watch.py DEFAULT_STAR_REPO_NAMES），本模块不再承担「列出仓库」职责。

GitHub 内容生成限制按 token 计量（≤80 次/min，见调研文档 §2.2），非 IP 聚合，
因此转发层按 token hash 分桶做单用户速率闸（60/min < 80），辅以全局并发闸
（Semaphore 30）保护进程自身，并在收到 403 + Retry-After 时退避重试一次。
转发请求复用进程级共享 httpx.AsyncClient（连接池 + DNS 缓存），避免每次请求新建
TCP/TLS 连接导致并发被 DNS 解析串行化。
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import httpx

from plugins_market.core.errors import BusinessError
from plugins_market.core.logging import get_logger
from plugins_market.core.rate_limit import SlidingWindowRateLimiter

logger = get_logger(__name__)

GITHUB_API_BASE = "https://api.github.com"

# ── 全局节流闸 ──────────────────────────────────────────────
# GitHub 流控调研结论（见 GITHUB_WATCH_FEATURE_DESIGN.md §2.2）：
#   内容生成限制 ≤80 次/min，按 token 计量（非 IP/服务器聚合）。
# 因此真正的速率保护是下方按 token hash 的单用户闸（60/min < 80/min）。
# 本全局闸的职责仅为保护 SkillHub 进程自身不过载（连接池/协程），
# 不再用于压低出站写量。配额远高于单用户闸 × 少量并发用户，
# 实际并发上限由 _global_semaphore(30) 兜底。
# 注意：本闸为进程级（SlidingWindowRateLimiter 基于进程内存），
# 多实例部署时各实例独立计数。
_global_semaphore = asyncio.Semaphore(30)

_write_limiter = SlidingWindowRateLimiter()
_WRITE_LIMIT_KEY = "github_watch_global"
_WRITE_LIMIT_PER_MIN = 2000

# ── 单用户写流控 ──────────────────────────────────────────────
# 对应 GitHub 内容生成限制 80/min/token（写操作 PUT/POST/DELETE/PATCH）。
# 按 token hash 限制单用户每分钟写次数，留出至少 3 次完整标星全部仓库的
# 余量（组织约 18 个公开仓库 ×3 = 54），同时低于 GitHub 的 80/min 上限留安全余量。
_user_write_limiter = SlidingWindowRateLimiter()
_USER_WRITE_LIMIT_PER_MIN = 60


def _token_hash(token: str) -> str:
    """对 token 取 SHA-256 前 16 位作为流控键，避免明文 token 落入内存日志。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

# 复用现有 github_user.py 的请求头风格，补 X-GitHub-Api-Version
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=10.0)

# ── 进程级共享客户端 ──────────────────────────────────────
# 复用连接池 + DNS 解析结果，避免每次请求新建 TCP/TLS 连接。
# 在 Windows 上 httpx 默认走同步 DNS（阻塞事件循环），每次新建客户端都会
# 重新解析 api.github.com，导致并发请求被 DNS 串行化（12 个请求 ~35s）。
# 共享客户端后连接复用，并发请求真正并行（~3s）。
_shared_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """惰性创建进程级单例 AsyncClient（线程安全由 GIL 保证首次创建）。"""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _shared_client


async def close_shared_client() -> None:
    """关闭进程级共享客户端，在 app lifespan shutdown 时调用。"""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _from_response(res: httpx.Response, *, default_error: str = "github_upstream_error") -> BusinessError:
    """根据 GitHub 响应构造 BusinessError（error key 已在 errors.py 注册表注册）。"""
    sc = res.status_code
    try:
        body = res.json()
        msg = (body.get("message") if isinstance(body, dict) else None) or res.text[:200]
    except Exception:
        msg = res.text[:200]
    if sc == 401:
        # 复用中央注册的 auth_token_invalid（SKILLHUB_AUTH_TOKEN_INVALID）
        return BusinessError(code=401, status_code=401, error="auth_token_invalid",
                             message="GitHub token 无效或已过期")
    if sc == 403:
        return BusinessError(code=403, status_code=403, error="github_forbidden",
                             message=f"GitHub 权限不足（可能缺少 public_repo 授权）：{msg}")
    if sc == 404:
        return BusinessError(code=404, status_code=404, error="github_not_found",
                             message="仓库不存在或无权访问")
    return BusinessError(code=sc, status_code=sc, error=default_error, message=msg)


async def _gh_request(
    method: str,
    path: str,
    token: str,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    """带全局并发闸 + 写速率闸 + Retry-After 退避的 GitHub 请求。"""
    async with _global_semaphore:
        # 写操作（PUT/POST/DELETE）计入内容生成配额；GET 不计
        is_write = method in ("PUT", "POST", "DELETE")
        if is_write:
            # 单用户流控：对应 GitHub 内容生成限制 80/min/token，按 token hash 分桶
            if not _user_write_limiter.allow(
                f"github_user:{_token_hash(token)}",
                limit=_USER_WRITE_LIMIT_PER_MIN,
                window_sec=60.0,
            ):
                raise BusinessError(
                    code=429, status_code=429, error="github_proxy_rate_limited",
                    message="操作过于频繁，请稍后再试",
                )
            # 全局流控：保护 SkillHub 进程自身不过载（连接池/协程），不卡正常多用户场景
            if not _write_limiter.allow(
                _WRITE_LIMIT_KEY, limit=_WRITE_LIMIT_PER_MIN, window_sec=60.0
            ):
                raise BusinessError(
                    code=429, status_code=429, error="github_proxy_rate_limited",
                    message="服务繁忙，请稍后重试",
                )
        # star 等 PUT 端点要求 Content-Length: 0（不带 body）；带 body 的用 json=
        req_headers = _headers(token)
        if json_body is None and method in ("PUT", "DELETE"):
            req_headers["Content-Length"] = "0"
        client = _get_client()
        res = await client.request(
            method,
            f"{GITHUB_API_BASE}{path}",
            headers=req_headers,
            json=json_body,
        )
        # 二级流控信号：403 + Retry-After -> 退避重试一次
        if res.status_code == 403 and "Retry-After" in res.headers:
            try:
                wait = float(res.headers["Retry-After"])
            except (TypeError, ValueError):
                wait = 1.0
            logger.warning("github secondary rate limit, retrying after %.1fs", wait)
            await asyncio.sleep(min(max(wait, 0.5), 30.0))
            res = await client.request(
                method,
                f"{GITHUB_API_BASE}{path}",
                headers=req_headers,
                json=json_body,
            )
        return res


async def star_repo(token: str, owner: str, repo: str) -> None:
    """标星仓库（PUT /user/starred/{owner}/{repo}）。成功返回 204。"""
    res = await _gh_request(
        "PUT",
        f"/user/starred/{owner}/{repo}",
        token,
    )
    if res.status_code != 204:
        raise _from_response(res)
