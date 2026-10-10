# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Process-local sliding-window rate limiter for anonymous/public endpoints.

本模块同时承载通用 Marketplace API 的统一限流策略：档位（tier）、规则表（policy）与
滑动窗口检查（``RateLimitCheck``）。

设计约束：
- 分层：策略与算法属于 core 基础设施，不侵入 Router/Service 业务代码；
- 配置：全部限额经 ``MARKET_RATE_LIMIT_*`` 环境变量注入（0 = 关闭该档）；
- 可观测：拒绝请求由中间件输出标准 429 状态码 + ``X-RateLimit-*`` 头，
  并复用审计失败补录与接口日志；
- 复用：与既有 ClawHub 兼容层共用 ``SlidingWindowRateLimiter``，
  不引入 slowapi / fastapi-limiter 新依赖。

多副本说明：滑动窗口基于进程内存（与既有 skill-import / git-sync /
ClawHub 限流一致），跨实例不共享；如需跨实例共享需引入 Redis 存储。
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Optional


class SlidingWindowRateLimiter:
    """Per-key sliding window counter (thread-safe within one process)."""

    # 周期性全表清扫节流间隔（每 N 次 check 触发一次），清理已空或全过期的桶，
    # 避免高基数一次性 key（公网源 IP / 伪造 XFF）残留在 dict 中导致内存线性增长。
    _SWEEP_EVERY = 512

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = {}
        self._check_count = 0

    def allow(self, key: str, *, limit: int, window_sec: float = 60.0) -> bool:
        if limit <= 0:
            return True
        return self.check(key, limit=limit, window_sec=window_sec).allowed

    def check(self, key: str, *, limit: int, window_sec: float = 60.0) -> "RateLimitCheck":
        """记录一次访问并返回限流判定结果（含剩余额度与窗口重置信息）。

        ``limit <= 0`` 表示该档位关闭：不计数、直接放行。按 ``_SWEEP_EVERY`` 节流
        周期性清扫全表，删除全过期的桶，避免高基数一次性 key 残留导致内存增长。
        """
        if limit <= 0:
            return RateLimitCheck(allowed=True, limit=0, remaining=0, reset_at=0.0, retry_after=0)
        now = time.monotonic()
        cutoff = now - window_sec
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is not None:
                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()
            else:
                bucket = deque()
            if len(bucket) >= limit:
                # 拒绝：最老一条计数离开窗口的时刻即重置点（bucket 必非空）
                reset_at = (bucket[0] + window_sec) if bucket else (now + window_sec)
                retry_after = max(1, int(math.ceil(reset_at - now)))
                return RateLimitCheck(
                    allowed=False, limit=limit, remaining=0,
                    reset_at=reset_at, retry_after=retry_after,
                )
            bucket.append(now)
            # remaining 反映"本次请求已计数"后的剩余额度（GitHub 风格语义）
            remaining = max(0, limit - len(bucket))
            reset_at = (bucket[0] + window_sec) if bucket else (now + window_sec)
            retry_after = max(0, int(math.ceil(reset_at - now)))
            self._buckets[key] = bucket
            self._maybe_sweep(cutoff)
            return RateLimitCheck(
                allowed=True, limit=limit, remaining=remaining,
                reset_at=reset_at, retry_after=retry_after,
            )

    def reset(self) -> None:
        """清空所有计数（主要用于测试隔离）。"""
        with self._lock:
            self._buckets.clear()
            self._check_count = 0

    def bucket_count(self) -> int:
        """返回当前跟踪的独立限流 key 数量（运维/内存监控可观测）。"""
        with self._lock:
            return len(self._buckets)

    def sweep(self) -> int:
        """主动清扫全表，删除已空或全过期的桶，返回被回收数量。

        供运维主动触发或测试使用；正常路径由 ``check`` 按 ``_SWEEP_EVERY`` 节流自动触发。
        以默认 60s 窗口计算 cutoff（各档位窗口均为 60s，统一阈值安全）。
        """
        with self._lock:
            return self._sweep_locked(time.monotonic() - 60.0)

    def _maybe_sweep(self, cutoff: float) -> None:
        """周期性清扫全表。须在持锁状态下调用（由 ``check`` 在锁内触发）。"""
        self._check_count += 1
        if self._check_count % self._SWEEP_EVERY != 0:
            return
        self._sweep_locked(cutoff)

    def _sweep_locked(self, cutoff: float) -> int:
        """删除已空或全过期的桶；返回被回收数量。须在持锁状态下调用。"""
        stale = [k for k, b in self._buckets.items() if not b or b[-1] <= cutoff]
        for k in stale:
            del self._buckets[k]
        return len(stale)


@dataclass(frozen=True, slots=True)
class RateLimitCheck:
    """一次限流判定的结果。``reset_at`` 为 monotonic 时间戳。"""

    allowed: bool
    limit: int
    remaining: int
    reset_at: float
    retry_after: int


@dataclass(frozen=True, slots=True)
class RateLimitTier:
    """限流档位。``scope``: ``ip``（按客户端 IP）或 ``user``（按凭证用户）。

    ``limit <= 0`` 表示该档位关闭（放行且不计数）。
    """

    name: str
    limit: int
    window_sec: float = 60.0
    scope: str = "ip"


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    """规则：方法（``*`` 通配）+ 路径 fnmatch 模式 + 档位名。"""

    method: str
    path_pattern: str
    tier_name: str


@dataclass(slots=True)
class RateLimitPolicy:
    """有序规则表 + 档位表。``match()`` 首条命中即返回（先豁免、后分级）。"""

    tiers: dict[str, RateLimitTier]
    rules: list[RateLimitRule] = field(default_factory=list)
    enabled: bool = True

    def tier(self, name: str) -> Optional[RateLimitTier]:
        return self.tiers.get(name)

    def match(self, method: str, path: str) -> Optional[RateLimitTier]:
        """按 (方法, 路径) 返回命中的档位；未命中返回 None（不限流）。

        路径先做尾斜杠归一化（``/api/v1/plugins/`` 与 ``/api/v1/plugins`` 等价），
        与中间件的归一化保持一致。
        """
        if not self.enabled:
            return None
        norm_method = "GET" if method == "HEAD" else method
        norm_path = path.rstrip("/") or "/"
        for rule in self.rules:
            if rule.method != "*" and rule.method != norm_method:
                continue
            if fnmatch(norm_path, rule.path_pattern):
                return self.tiers.get(rule.tier_name)
        return None

    @staticmethod
    def keys_for(tier: RateLimitTier, *, client_ip: str, user_key: Optional[str]) -> list[str]:
        """按档位维度推导限流 key：user 档无凭证时回退 ip。

        key 携带 ``tier.name``，使每个档位拥有独立计数桶，避免同一 IP/用户
        在 public_read/auth/匿名回退等档位间共享桶导致跨档位误限流。
        """
        if tier.scope == "user":
            return [f"user:{user_key}:{tier.name}" if user_key else f"ip:{client_ip}:{tier.name}"]
        return [f"ip:{client_ip}:{tier.name}"]


# ── 客户端标识推导（与 core/middleware/request_id.py 的 _get_client_ip 保持同策略） ──

def client_ip_from_scope(scope: dict, *, trust_forwarded: bool = True) -> str:
    """从 ASGI scope 提取客户端 IP。

    ``trust_forwarded=True`` 时优先取 ``X-Forwarded-For`` 首项，其次 ``X-Real-IP``，最后 peer；
    适用于部署在受信反向代理（如 nginx）之后、由代理覆写这些头的场景。
    ``trust_forwarded=False`` 时直接用 peer（忽略可伪造的转发头），适用于后端直连暴露的场景。
    默认 True 兼容 SkillHub 常规 nginx 前置部署；直连暴露时应在配置中关闭。
    """
    if trust_forwarded:
        headers = dict(scope.get("headers") or [])
        forwarded = headers.get(b"x-forwarded-for")
        if forwarded:
            return forwarded.decode("latin-1").split(",")[0].strip() or "unknown"
        real_ip = headers.get(b"x-real-ip")
        if real_ip:
            return real_ip.decode("latin-1").strip() or "unknown"
    client = scope.get("client")
    if client:
        return client[0]
    return "unknown"


def user_key_from_scope(scope: dict) -> Optional[str]:
    """从凭证推导稳定的用户维度 key（不调用上游鉴权、不泄露令牌明文）。

    - ``Authorization: Bearer <token>`` → ``tok_<sha256(token)[:16]>``
    - ``X-System-Token`` → ``sys_<sha256(value)[:16]>``（按值哈希，避免任意伪造值与真实系统通道共享配额）
    - 无凭证 → None（调用方回退 IP 维度）
    """
    headers = dict(scope.get("headers") or [])
    auth = headers.get(b"authorization")
    if auth:
        raw = auth.decode("latin-1", errors="ignore").strip()
        if raw.lower().startswith("bearer "):
            token = raw[7:].strip()
            if token:
                digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
                return f"tok_{digest[:16]}"
        return None
    sys_token = headers.get(b"x-system-token")
    if sys_token:
        digest = hashlib.sha256(sys_token).hexdigest()
        return f"sys_{digest[:16]}"
    return None


# ── 默认规则表（首条命中生效；先豁免已有限流端点，再按敏感度分级） ──

_DEFAULT_RULES: list[RateLimitRule] = [
    # 健康检查 / 在线试用（自有配额与限流）/ ClawHub 兼容层（自有按 IP 限流）
    RateLimitRule("*", "/api/health", "exempt"),
    RateLimitRule("*", "/api/v1/playground*", "exempt"),
    RateLimitRule("*", "/api/v1/search*", "exempt"),
    # ClawHub 兼容层自有按 IP 限流（_enforce_clawhub_rate_limit）。
    # 仅豁免 /api/v1/skills 与 /api/v1/skills/* 子树，避免 /api/v1/skills* 吞掉
    # 未来同名前缀的企业 HTTP 接口（如 /api/v1/skills-admin）。
    RateLimitRule("*", "/api/v1/skills", "exempt"),
    RateLimitRule("*", "/api/v1/skills/*", "exempt"),
    RateLimitRule("*", "/api/v1/download*", "exempt"),
    RateLimitRule("*", "/api/v1/resolve*", "exempt"),
    # skill-import / git-source sync 已有各自进程内限流（routers/plugin.py），避免双重计数
    RateLimitRule("POST", "/api/v1/plugins/skill-import", "exempt"),
    RateLimitRule("POST", "/api/v1/plugins/git-sources*", "exempt"),
    RateLimitRule("DELETE", "/api/v1/plugins/git-sources*", "exempt"),
    # 登录/OAuth 端点（匿名、触发上游调用与 session 写入）
    RateLimitRule("*", "/api/v1/auth/*", "auth"),
    # 发布（上传 + 落库 + 异步审核，最重写路径）
    RateLimitRule("POST", "/api/v1/plugins", "publish"),
    # 批量/重型查询（多资产查询、向量检索）
    RateLimitRule("POST", "/api/v1/recommend*", "batch"),
    RateLimitRule("GET", "/api/v1/plugins/interactions/batch", "batch"),
    # 公开查询
    RateLimitRule("GET", "/api/v1/plugins*", "public_read"),
    RateLimitRule("GET", "/api/v1/artifacts*", "public_read"),
    RateLimitRule("GET", "/api/v1/site*", "public_read"),
    RateLimitRule("GET", "/api/v1/groups*", "public_read"),
    RateLimitRule("GET", "/api/v1/audit*", "public_read"),
    RateLimitRule("GET", "/api/v1/github*", "public_read"),
    RateLimitRule("GET", "/api/v1/notifications*", "public_read"),
    # 其余变更类端点兜底（点赞/收藏、群组管理、通知、标星、审核操作等）
    RateLimitRule("*", "/api/v1/plugins*", "default_write"),
    RateLimitRule("*", "/api/v1/groups*", "default_write"),
    RateLimitRule("*", "/api/v1/notifications*", "default_write"),
    RateLimitRule("*", "/api/v1/github*", "default_write"),
    RateLimitRule("*", "/api/v1/audit*", "default_write"),
    RateLimitRule("*", "/api/v1/recommend*", "default_write"),
    RateLimitRule("*", "/api/v1/*", "default_write"),
]


def build_rate_limit_policy(
    *,
    enabled: bool = True,
    public_read_per_minute: int = 300,
    publish_per_minute: int = 10,
    batch_per_minute: int = 5,
    auth_per_minute: int = 20,
    default_write_per_minute: int = 30,
    window_sec: float = 60.0,
) -> RateLimitPolicy:
    """从配置构建统一限流策略（0 = 关闭该档）。"""
    tiers = {
        "exempt": RateLimitTier(name="exempt", limit=0, window_sec=window_sec, scope="ip"),
        "public_read": RateLimitTier(
            name="public_read", limit=public_read_per_minute, window_sec=window_sec, scope="ip",
        ),
        "publish": RateLimitTier(
            name="publish", limit=publish_per_minute, window_sec=window_sec, scope="user",
        ),
        "batch": RateLimitTier(
            name="batch", limit=batch_per_minute, window_sec=window_sec, scope="user",
        ),
        "auth": RateLimitTier(
            name="auth", limit=auth_per_minute, window_sec=window_sec, scope="ip",
        ),
        "default_write": RateLimitTier(
            name="default_write", limit=default_write_per_minute, window_sec=window_sec, scope="user",
        ),
    }
    return RateLimitPolicy(tiers=tiers, rules=list(_DEFAULT_RULES), enabled=enabled)


_clawhub_compat_limiter = SlidingWindowRateLimiter()


def check_clawhub_compat_rate_limit(client_key: str, *, limit_per_minute: int) -> bool:
    """Return True if the request is allowed under the configured per-minute limit."""
    return _clawhub_compat_limiter.allow(
        f"clawhub:{client_key}",
        limit=limit_per_minute,
        window_sec=60.0,
    )
