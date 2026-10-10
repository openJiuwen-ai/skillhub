# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""skill_runner config. Override via environment variables."""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # dotenv 是可选依赖，缺失时仅用环境变量

if load_dotenv is not None:
    _ROOT_ENV = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"
    )
    if os.path.isfile(_ROOT_ENV):
        try:
            load_dotenv(_ROOT_ENV, override=False)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                ".env load failed, fallback to env vars: %s", exc
            )


def _env(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val else default


def _parse_port(raw: str) -> int:
    """Parse port from env var, handling K8s service-style values like tcp://1.2.3.4:8900."""
    raw = raw.strip()
    if raw.startswith("tcp://"):
        raw = raw.rsplit(":", 1)[-1]
    return int(raw)


@dataclass(frozen=True)
class Settings:
    # Executor backend: "k8s" 控制面，每 session 起 pod | "local" worker pod 内
    executor: str = os.environ.get("SKILL_RUNNER_EXECUTOR", "k8s")

    session_timeout_seconds: int = int(os.environ.get("SKILL_RUNNER_SESSION_TIMEOUT", "1800"))
    session_max_lifetime_seconds: int = int(os.environ.get("SKILL_RUNNER_SESSION_MAX_LIFETIME", "1800"))
    message_max_chars: int = int(os.environ.get("SKILL_RUNNER_MSG_MAX_CHARS", "4096"))
    message_max_turns: int = int(os.environ.get("SKILL_RUNNER_MSG_MAX_TURNS", "50"))
    # 并发 session 上限，超出则 create_session 返回 429
    max_concurrent_sessions: int = int(os.environ.get("SKILL_RUNNER_MAX_SESSIONS", "20"))
    exec_timeout_seconds: int = int(os.environ.get("SKILL_RUNNER_EXEC_TIMEOUT", "60"))
    allow_debug_shell: bool = os.environ.get("SKILL_RUNNER_ALLOW_DEBUG_SHELL", "false").lower() == "true"

    # per-session workspace root
    workspace_root: str = os.environ.get(
        "SKILL_RUNNER_WORKSPACE_ROOT",
        os.path.join(tempfile.gettempdir(), "skill-runner"),
    )

    # SSE event queue cap per session
    sse_buffer_max: int = int(os.environ.get("SKILL_RUNNER_SSE_BUFFER", "256"))
    # 上传单文件字节上限（纵深校验，主闸在 marketplace 入口层）
    upload_max_file_bytes: int = int(os.environ.get("SKILL_RUNNER_UPLOAD_MAX_FILE_BYTES", str(5 * 1024 * 1024)))
    # 每会话累计上传上限：文件数与总字节，防单会话占满 pod 磁盘
    upload_max_files_per_session: int = int(os.environ.get("SKILL_RUNNER_UPLOAD_MAX_FILES", "10"))
    upload_max_total_bytes: int = int(os.environ.get("SKILL_RUNNER_UPLOAD_MAX_TOTAL_BYTES", str(20 * 1024 * 1024)))
    # 向 SSE 队列投递事件的最长等待；超时=消费者已断连，中止本轮释放 pod
    sse_put_timeout_seconds: float = float(os.environ.get("SKILL_RUNNER_SSE_PUT_TIMEOUT", "30"))

    # ---- 多实例（高可用）----
    # false=单实例（默认）；true=多副本，create 响应回带实例地址做粘性路由
    multi_instance: bool = os.environ.get("SKILL_RUNNER_MULTI_INSTANCE", "false").lower() == "true"
    # 空则由 POD_IP + 端口推导
    advertise_addr: str = os.environ.get("SKILL_RUNNER_ADVERTISE_ADDR", "")
    listen_port: int = _parse_port(os.environ.get("SKILL_RUNNER_PORT", "8900"))

    # ---- Redis（仅多实例用：外置每用户每日 token 预算；单实例走进程内存，无需配置）----
    redis_host: str = os.environ.get("SKILL_RUNNER_REDIS_HOST", "")
    redis_port: int = int(os.environ.get("SKILL_RUNNER_REDIS_PORT", "6379"))
    redis_db: int = int(os.environ.get("SKILL_RUNNER_REDIS_DB", "0"))
    redis_password: str = os.environ.get("SKILL_RUNNER_REDIS_PASSWORD", "")

    # ---- k8s executor ----
    k8s_namespace: str = os.environ.get("SKILL_RUNNER_K8S_NAMESPACE", "skillhub-workers")
    k8s_pod_image: str = os.environ.get("SKILL_RUNNER_K8S_POD_IMAGE", "skill-agent-worker:0.1.0")
    k8s_image_pull_policy: str = os.environ.get("SKILL_RUNNER_K8S_IMAGE_PULL_POLICY", "IfNotPresent")
    k8s_runtime_class: str = os.environ.get("SKILL_RUNNER_K8S_RUNTIME_CLASS", "")
    pod_privileged: bool = os.environ.get("SKILL_RUNNER_POD_PRIVILEGED", "false").lower() == "true"
    pod_run_as_user: int = int(os.environ.get("SKILL_RUNNER_POD_RUN_AS_USER", "65534"))
    pod_seccomp_profile_type: str = os.environ.get("SKILL_RUNNER_POD_SECCOMP_PROFILE", "RuntimeDefault")
    pod_port: int = _parse_port(os.environ.get("SKILL_RUNNER_POD_PORT", "8080"))
    pod_ready_timeout_seconds: int = int(os.environ.get("SKILL_RUNNER_POD_READY_TIMEOUT", "120"))
    proxy_base_url: str = os.environ.get("SKILL_RUNNER_PROXY_BASE_URL", "http://skill-runner:8900")

    # Optional cloud K8s pod controls. Keep all defaults empty so local Docker Desktop
    # can run without cluster-specific objects; production can enable them via env only.
    k8s_service_account_name: str = os.environ.get("SKILL_RUNNER_K8S_SERVICE_ACCOUNT", "")
    k8s_image_pull_secrets: str = os.environ.get("SKILL_RUNNER_K8S_IMAGE_PULL_SECRETS", "")
    k8s_node_selector: str = os.environ.get("SKILL_RUNNER_K8S_NODE_SELECTOR", "")
    k8s_tolerations: str = os.environ.get("SKILL_RUNNER_K8S_TOLERATIONS", "")
    k8s_resources: str = os.environ.get("SKILL_RUNNER_K8S_RESOURCES", "")

    # ---- Worker Pod 预热池 ----
    # 0 = 即用即弃（每 session 起删一个 pod）；>0 = 预热复用池
    pool_size: int = int(os.environ.get("SKILL_RUNNER_POOL_SIZE", "0"))
    pool_max_idle: int = int(
        os.environ.get(
            "SKILL_RUNNER_POOL_MAX_IDLE",
            os.environ.get("SKILL_RUNNER_POOL_SIZE", "0"),
        )
    )
    # acquire 时若剩余寿命低于此值则弃用换新
    pool_min_lease_lifetime_seconds: int = int(
        os.environ.get("SKILL_RUNNER_POOL_MIN_LEASE_LIFETIME", "300")
    )
    # 超过 max_lifetime + 此宽限的 Running pod 判定为孤儿
    pod_orphan_grace_seconds: int = int(
        os.environ.get("SKILL_RUNNER_POD_ORPHAN_GRACE", "120")
    )


    # ---- LLM (worker pod 内 DeepAgent 使用；api_base 指控制面代理，key 不进 pod) ----
    # 配置优先级：SKILL_RUNNER_LLM_* > LLM_* > API_*
    llm_provider: str = _env(
        "SKILL_RUNNER_LLM_PROVIDER",
        os.environ.get("LLM_PROVIDER", "OpenAI"),
    )
    llm_api_base: str = _env(
        "SKILL_RUNNER_LLM_API_BASE",
        _env("LLM_API_BASE", os.environ.get("API_BASE", "")),
    )
    llm_api_key: str = _env(
        "SKILL_RUNNER_LLM_API_KEY",
        _env("LLM_API_KEY", os.environ.get("API_KEY", "")),
    )
    llm_model_name: str = _env(
        "SKILL_RUNNER_LLM_MODEL_NAME",
        _env("LLM_MODEL_NAME", os.environ.get("MODEL_NAME", "")),
    )
    llm_client_id: str = _env(
        "SKILL_RUNNER_LLM_CLIENT_ID",
        os.environ.get("LLM_CLIENT_ID", "skill-runner"),
    )
    llm_timeout_seconds: float = float(
        _env("SKILL_RUNNER_LLM_TIMEOUT", os.environ.get("LLM_TIMEOUT", "120"))
    )
    # 事件流静默期的 keepalive 间隔，须小于控制面 read 超时
    worker_keepalive_seconds: float = float(
        _env("SKILL_RUNNER_WORKER_KEEPALIVE", os.environ.get("WORKER_KEEPALIVE", "15"))
    )
    llm_max_iterations: int = int(
        _env("SKILL_RUNNER_LLM_MAX_ITERATIONS", os.environ.get("LLM_MAX_ITERATIONS", "1000"))
    )
    # 全局 LLM 并发闸，防 swarm 扇出打爆账户限流；<=0 不限
    llm_max_concurrency: int = int(
        os.environ.get("SKILL_RUNNER_LLM_MAX_CONCURRENCY", "3")
    )
    llm_temperature: float = float(
        _env("SKILL_RUNNER_LLM_TEMPERATURE", os.environ.get("LLM_TEMPERATURE", "0.3"))
    )
    llm_max_tokens: int = int(
        _env("SKILL_RUNNER_LLM_MAX_TOKENS", os.environ.get("LLM_MAX_TOKENS", "32768"))
    )
    swarm_max_roles: int = int(os.environ.get("SKILL_RUNNER_SWARM_MAX_ROLES", "3"))
    # 每用户每日 LLM token 上限（控制面 llm_proxy 层累计）；0 = 不限
    user_daily_token_limit: int = int(os.environ.get("SKILL_RUNNER_USER_DAILY_TOKEN_LIMIT", "500000"))
    # 默认 false：当前部署环境缺 CA 证书链
    llm_verify_ssl: bool = os.environ.get("SKILL_RUNNER_LLM_VERIFY_SSL", "false").lower() == "true"


settings = Settings()


def instance_base_url() -> str:
    """多实例回连基址；空则用 POD_IP 推导。"""
    if not settings.multi_instance:
        return ""
    if settings.advertise_addr:
        return settings.advertise_addr.rstrip("/")
    pod_ip = os.environ.get("POD_IP", "")
    if pod_ip:
        return f"http://{pod_ip}:{settings.listen_port}"
    return ""

