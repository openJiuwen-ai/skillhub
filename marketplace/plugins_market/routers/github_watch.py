# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
GitHub 仓库标星路由：代理转发到 api.github.com，供「一键标星 openjiuwen 仓库」使用。

注：路由路径 /github/watch 及类型名 Watch* 为历史命名（最初用 Watch/订阅 API，
后改为 Star/标星 API）。为兼容已发布的路径与前端调用，保留 watch 命名不改。

端点（挂载于 /api/v1/github）：
- POST /github/watch         批量标星选中的仓库（后台 fire-and-forget，立即返回 202）
- GET  /github/watch/status  查询当前用户是否已标星

鉴权：从 Authorization: Bearer 头取用户 GitHub token（与 auth_me 一致）。
节流：转发层全局并发闸 + 写速率闸 + Retry-After 退避（见 core/github_proxy.py）。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import date
from typing import Any

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException

from plugins_market.core.auth import get_oauth_user_id_and_login, normalize_oauth_provider_header
from plugins_market.core.cache import cache_get, cache_incr, cache_set_persistent
from plugins_market.core.config import settings
from plugins_market.core.errors import BusinessError, resolve_registered_error_metadata
from plugins_market.core.github_proxy import (
    star_repo,
)
from plugins_market.core.logging import get_logger
from plugins_market.core.operation_log import (
    bind_operation_actor,
    bind_operation_resource,
    complete_operation_result,
    is_invalid_or_denied_error,
    operation_context,
    operation_failure_result,
    operation_log_fields,
)
from plugins_market.schemas.common import ResponseModel

logger = get_logger(__name__)

router = APIRouter(prefix="/github", tags=["github"])

# 一键标星的目标组织：默认值，当 settings.github_star_org 为空时回退使用。
DEFAULT_WATCH_ORG = "openJiuwen-ai"

# 一键标星的目标仓库清单（openJiuwen-ai 组织下精选仓库）。
# 当 settings.github_star_repos 为空时回退使用此默认列表。
# 早期版本 repos 为空时会调 list_org_repos 拉取组织全部公开仓库（约 18 个），
# 现按业务要求固定为以下 10 个核心仓库，既聚焦核心项目又缩短标星耗时（≈13s）。
DEFAULT_STAR_REPO_NAMES = (
    "jiuwenswarm",
    "agent-studio",
    "agent-core",
    "jiuwensymbiosis",
    "deepsearch",
    "agent-memory",
    "agent-protocol",
    "agent-core-java",
    "agent-runtime-java",
    "skillhub",
)


def _get_watch_org() -> str:
    """读取标星目标组织，配置缺失时回退为默认值。"""
    org = settings.github_star_org.strip() if settings.github_star_org else ""
    return org or DEFAULT_WATCH_ORG


def _get_star_repo_names() -> tuple[str, ...]:
    """读取标星目标仓库列表，配置缺失时回退为硬编码默认值。

    环境变量格式：逗号分隔的仓库名（不含 owner 前缀），如：
      MARKET_GITHUB_STAR_REPOS=jiuwenswarm,agent-studio,agent-core
    空值或纯空白回退为 DEFAULT_STAR_REPO_NAMES。
    """
    raw = settings.github_star_repos.strip() if settings.github_star_repos else ""
    if not raw:
        return DEFAULT_STAR_REPO_NAMES
    names = tuple(n.strip() for n in raw.split(",") if n.strip())
    return names or DEFAULT_STAR_REPO_NAMES

# 标星状态 Redis key：按 provider:login 隔离，永久（无 TTL）。
# 写入时机：标星请求成功（至少一个仓库 success）后；读取时机：GET /watch/status。
# Redis 不可用时 cache_get 返回 None、cache_set 静默跳过，降级为「未标星」，用户可重新点（PUT 幂等，无害）。
STAR_USER_KEY_PREFIX = "github_star_user:"

# 后台 fire-and-forget 标星任务的强引用表（按 provider:login 隔离），两个作用：
# 1) 持强引用：asyncio.create_task 返回的 Task 若不被持有，可能被 GC 回收导致任务中途取消；
# 2) per-user 去重闸：同一用户任务运行中时重复提交直接返回 already_running，防止前端
#    202 返回后连点/跨标签页产生并发 batch（跨任务 PUT 并发，抵消串行改造并触发
#    GitHub 反自动化标星系统）。进程内去重（多 worker 部署各进程独立，前端锁兜底）。
_star_bg_tasks: dict[str, asyncio.Task[None]] = {}


def _pop_user_star_task(task: asyncio.Task[None], user_key: str) -> None:
    """done_callback：任务结束后从 per-user 表移除自身注册。

    仅当表里映射的仍是本任务时才移除：done_callback 经 call_soon 延迟执行，
    期间同用户可能已重建新任务覆盖表项，不能误删新任务的注册。
    """
    if _star_bg_tasks.get(user_key) is task:
        del _star_bg_tasks[user_key]


def _star_user_key(provider: str, login: str) -> str:
    return f"{STAR_USER_KEY_PREFIX}{provider}:{login}"


def _resolve_github_provider(x_oauth_provider: str | None) -> str:
    """解析 X-OAuth-Provider 头，缺失/非法时 fallback 为 "github"。

    本端点仅服务 GitHub 登录用户（前端 provider!=='github' 时隐藏按钮），
    故 token 归属一定是 github，fallback 必须用 "github" 以保证读写 key 一致。
    注意：normalize_oauth_provider_header(None) 返回 "gitcode"（app 默认），
    不符合本端点意图，故需显式判空；非法值（如 "gitlab"）抛 HTTPException(400)
    时也 fallback 为 "github"。
    """
    if x_oauth_provider and x_oauth_provider.strip():
        try:
            return normalize_oauth_provider_header(x_oauth_provider)
        except HTTPException:
            return "github"
    return "github"


# ── 操作日志辅助（与 groups.py 三段式模式一致）──────────────
def _log_started(event: str, **fields: Any) -> None:
    logger.info(event, **operation_log_fields(stage="start", result="started", **fields))


def _log_completed(event: str, *, result: str = "success", **fields: Any) -> None:
    logger.info(event, **complete_operation_result(result=result, **fields))


def _raise_with_failure_log(event: str, error: Exception, **fields: Any):
    """记录失败操作日志后重新抛出异常（与 groups.py _raise_with_operation_failure_log 一致）。"""
    if isinstance(error, BusinessError):
        payload = error.detail
        error_code, error_class = resolve_registered_error_metadata(str(payload.get("error") or ""))
        if error_code and payload.get("error_code") is None:
            payload["error_code"] = error_code
        if error_class and payload.get("error_class") is None:
            payload["error_class"] = error_class
        result = operation_failure_result(payload)
        log_method = logger.info if is_invalid_or_denied_error(payload) else logger.warning
        log_method(
            event,
            **complete_operation_result(
                result=result.result,
                error_code=result.error_code,
                error_class=result.error_class,
                error_message=result.error_message,
                result_detail=result.result_detail,
                **fields,
            ),
        )
    with contextlib.suppress(Exception):
        setattr(error, "_operation_completion_logged", True)
    raise error


def _extract_token(authorization: str | None) -> str:
    """从 Authorization 头提取 Bearer token（与 oauth_provider.auth_me 一致）。"""
    if not authorization or not authorization.strip().lower().startswith("bearer "):
        raise BusinessError(
            code=401,
            status_code=401,
            error="auth_header_missing",
            message="Missing or invalid Authorization",
        )
    # 从 strip 后的字符串提取，与上面的校验基准一致（避免 " Bearer xxx" 等情况偏移错位）
    return authorization.strip()[7:].strip()


class WatchItem(BaseModel):
    # pattern 限制为 GitHub 合法字符（字母/数字/._-），防止路径注入拼出非预期 API 路径
    owner: str = Field(..., min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    repo: str = Field(..., min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")


class WatchBatchBody(BaseModel):
    # repos 为空时表示「一键标星组织核心仓库」（配置项，见 _get_star_repo_names）
    repos: list[WatchItem] = Field(default_factory=list, max_length=100)


@router.post("/watch", response_model=ResponseModel[dict])
async def star_repos(
    body: WatchBatchBody,
    authorization: str | None = Header(None),
    x_oauth_provider: str | None = Header(None, alias="X-OAuth-Provider"),
):
    """批量标星选中的仓库（后台 fire-and-forget，立即返回 202）。

    鉴权：Authorization: Bearer <github token>。
    X-OAuth-Provider：标识 token 归属厂商（github/gitcode），用于标星成功后
    按用户隔离写入 Redis 状态；缺失/非法时 fallback 为 github（见 _resolve_github_provider，
    本端点仅服务 GitHub 用户，fallback 用 github 而非 app 默认 gitcode 以保证读写 key 一致）。

    串行标星默认 10 个仓库 ≈20s，若同步等待前端会转圈 20s 用户以为卡住。
    改为后台 asyncio.create_task 异步标星，立即返回 202 + 乐观写 Redis 已标星态。
    后台全失败时回滚 Redis 为 "0"（前端下次查状态返回 false）。
    """
    with operation_context(operation_type="star github repos"):
        bind_operation_actor(actor_type="oauth_user")
        watch_org = _get_watch_org()
        star_repo_names = _get_star_repo_names()
        bind_operation_resource(resource_type="github_watch", resource_id=watch_org)
        _log_started("star github repos", org=watch_org)

        if not settings.github_star_enabled:
            _raise_with_failure_log(
                "star github repos",
                BusinessError(code=404, status_code=404, error="feature_disabled",
                              message="标星功能已关闭"),
                org=watch_org,
            )
        token = _extract_token(authorization)
        # 点击计数：总计数（永不过期）+ 每日计数（当天过期），Redis 不可用时静默跳过
        cache_incr("github_star_clicks:total")
        cache_incr(f"github_star_clicks:daily:{date.today().isoformat()}", ttl=86400)
        # repos 为空时，使用配置的核心仓库清单（_get_star_repo_names），不再拉取组织全部仓库。
        # org 白名单：只允许标星目标组织下的仓库，
        # 防止用户传入任意 owner/repo 使 SkillHub 沦为通用标星代理。
        items_to_star: list[WatchItem] = body.repos
        for item in items_to_star:
            if item.owner.lower() != watch_org.lower():
                _raise_with_failure_log(
                    "star github repos",
                    BusinessError(code=403, status_code=403, error="github_forbidden",
                                  message=f"仅支持标星 {watch_org} 组织下的仓库"),
                    org=watch_org,
                )
        if not items_to_star:
            items_to_star = [
                WatchItem(owner=watch_org, repo=name) for name in star_repo_names
            ]

        # 串行标星 + 请求间隔，遵循 GitHub 官方最佳实践：
        # https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
        #   "For PUT/POST/DELETE requests, wait at least one second between each request."
        #   "Make requests serially instead of concurrently."
        # 早期版本用 asyncio.gather 并发打多个 PUT（<1s 内全部发出），触发了 GitHub
        # 二级流控与反自动化标星系统：star 先被写入（204）随后被后台批量撤销，表现为
        # 「标星后马上能看到，过一段时间就没了」。串行 + ≥1s 间隔可避免该问题。
        # PUT /user/starred 是幂等的，已标星的再标一次返回 204，无需先查已标星列表。
        async def _star_one(item: WatchItem) -> dict[str, Any]:
            entry: dict[str, Any] = {"owner": item.owner, "repo": item.repo}
            try:
                await star_repo(token, item.owner, item.repo)
                entry["status"] = "success"
            except BusinessError as e:
                entry["status"] = "failed"
                entry["error"] = e.message
                entry["code"] = e.status_code
                logger.warning(
                    "star_repo failed: %s/%s status=%s error=%s",
                    item.owner, item.repo, e.status_code, e.message,
                )
            except Exception as e:
                entry["status"] = "failed"
                entry["error"] = str(e)
                entry["code"] = 502
                logger.warning(
                    "star_repo unexpected error: %s/%s error=%s",
                    item.owner, item.repo, e,
                )
            return entry

        # GitHub 要求写请求间隔 ≥1s；取 1.25s 留余量。最后一个请求后不需等待。
        star_interval_sec = 1.25

        # 预解析 provider（标星成功后写 Redis 用），并提前校验 token 有效性。
        # 若 token 无效，立即返回 401，不启动后台任务。
        prov = _resolve_github_provider(x_oauth_provider)
        _, login = await get_oauth_user_id_and_login(token, prov)

        # 乐观写 Redis：标星请求已受理，前端立即显示已标星态。
        # 后台任务失败时由 get_watch_status 的实际标星结果决定（best-effort，PUT 幂等可重试）。
        cache_set_persistent(_star_user_key(prov, login), "1")

        # per-user 去重闸：同用户已有后台标星任务运行中时不再新建任务，直接返回
        # already_running。防止 202 返回后连点/跨标签页触发并发 batch（跨任务 PUT
        # 并发，抵消串行改造并触发 GitHub 反自动化标星系统）。仍返回 202：对前端
        # 而言请求同样被受理，乐观 "1" 已写入，语义一致无需区分。
        user_key = _star_user_key(prov, login)
        running = _star_bg_tasks.get(user_key)
        if running is not None and not running.done():
            _log_started("star github repos", org=watch_org, deduped=True)
            return ResponseModel(code=202, message="accepted", data={"status": "already_running"})

        # 后台 fire-and-forget 标星：串行仓库列表 ≈20s，若同步等待前端会转圈 20s
        # 用户以为卡住。改为后台任务立即返回 202，前端无需等待。
        async def _star_batch_bg() -> None:
            bg_success = 0
            bg_failed = 0
            bg_results: list[dict[str, Any]] = []
            bg_t0 = time.monotonic()
            try:
                for idx, item in enumerate(items_to_star):
                    try:
                        if idx > 0:
                            await asyncio.sleep(star_interval_sec)
                        r = await _star_one(item)
                        bg_results.append(r)
                        if r["status"] == "success":
                            bg_success += 1
                        else:
                            bg_failed += 1
                    except Exception as e:
                        logger.warning("github_watch background star item failed: %s", e)
                        bg_results.append({
                            "owner": item.owner, "repo": item.repo,
                            "status": "failed", "error": str(e), "code": 502,
                        })
                        bg_failed += 1
                bg_elapsed = time.monotonic() - bg_t0
                # 全失败时回滚 Redis 状态（前端下次查状态会返回 false）
                # 已知限制（暂不修）：回滚为无条件 SET "0"，存在跨标签页/跨设备竞态——
                # 同一用户并发发起两次标星时，若 A 全失败回滚 "0"、B 部分成功（依赖乐观
                # "1" 不再写），A 的 "0" 会覆盖 B 的成功态，导致显示未标星但实际已标星。
                # 触发苛刻（需 ~20s 窗口内同用户并发 + 一全败一部分成）、影响可逆（PUT
                # 幂等，用户重点即修复）。彻底消除需 req_id + Redis CAS（Lua 脚本），
                # 改动 Redis 值格式与读路径，收益与成本不匹配，暂记为已知限制。
                if bg_success == 0:
                    logger.warning("github_watch background star all failed, rolling back status: %s", bg_results)
                    cache_set_persistent(_star_user_key(prov, login), "0")
                _log_completed(
                    "star github repos",
                    result="success" if bg_success > 0 else "failure",
                    total=len(bg_results), success=bg_success, failed=bg_failed,
                    elapsed_ms=int(bg_elapsed * 1000),
                )
            except asyncio.CancelledError:
                # 进程关闭时事件循环取消本任务（CancelledError 继承 BaseException，
                # 上方 except Exception 捕获不到）。记录「已取消」操作日志后重新抛出，
                # 保持取消语义。不回滚 Redis：已成功标星的仓库乐观 "1" 仍正确，
                # 未完成的仓库用户可重点（PUT 幂等）；回滚反而会引入与全失败竞态相同的问题。
                bg_elapsed = time.monotonic() - bg_t0
                logger.warning(
                    "github_watch background star cancelled, partial results: %s",
                    bg_results,
                )
                _log_completed(
                    "star github repos",
                    result="cancelled",
                    total=len(bg_results), success=bg_success, failed=bg_failed,
                    elapsed_ms=int(bg_elapsed * 1000),
                )
                raise
            except Exception as e:
                # 兜底：循环后代码（_log_completed 等）若抛非 CancelledError 异常，
                # fire-and-forget 任务会静默死亡（仅产 "Task exception was never
                # retrieved"）。当前循环后原语都自保护（cache_set_persistent 吞异常、
                # logger 吞 handler 异常），实际不会走到；此分支为纵深防御，防止将来
                # 改动引入不自保护调用。不 re-raise：re-raise 只产噪音，已记日志 +
                # best-effort 回滚即可。
                logger.error("github_watch background star unexpected error: %s", e, exc_info=True)
                if bg_success == 0:
                    with contextlib.suppress(Exception):
                        cache_set_persistent(_star_user_key(prov, login), "0")
                with contextlib.suppress(Exception):
                    _log_completed(
                        "star github repos",
                        result="error",
                        total=len(bg_results), success=bg_success, failed=bg_failed,
                        elapsed_ms=int((time.monotonic() - bg_t0) * 1000),
                    )

        # 保存任务强引用（防 GC 回收导致中途取消），并登记 per-user 去重闸。
        _bg_task = asyncio.create_task(_star_batch_bg())
        _star_bg_tasks[user_key] = _bg_task
        _bg_task.add_done_callback(lambda t: _pop_user_star_task(t, user_key))

        return ResponseModel(code=202, message="accepted", data={"status": "started"})


@router.get("/watch/status", response_model=ResponseModel[dict])
async def get_watch_status(
    authorization: str | None = Header(None),
    x_oauth_provider: str | None = Header(None, alias="X-OAuth-Provider"),
):
    """查询当前用户是否已标星 openJiuwen-ai 组织仓库。

    返回 {starred: bool}。标星状态存 Redis（按 provider:login 隔离，永久 key），
    跨设备同步。Redis 不可用时返回 starred=false（降级，用户可重新点，PUT 幂等无害）。
    未登录 / token 无效返回 401。

    注：本端点是轻量查询，不进 operation_context，故功能关闭/token 失效时直接 raise
    而非走 star_repos 的 _raise_with_failure_log（那个会先记操作日志再抛）。
    """
    if not settings.github_star_enabled:
        raise BusinessError(code=404, status_code=404, error="feature_disabled",
                            message="标星功能已关闭")
    token = _extract_token(authorization)
    # 与 star_repos 一致：provider header 缺失/非法 fallback 为 "github"，
    # 保证读写 key 一致（见 _resolve_github_provider）。
    prov = _resolve_github_provider(x_oauth_provider)
    _, login = await get_oauth_user_id_and_login(token, prov)
    starred = cache_get(_star_user_key(prov, login)) == "1"
    return ResponseModel(code=200, message="ok", data={"starred": starred})
