# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings


DEFAULT_AUTH_USER_API_URL = "https://gitcode.com/api/v5/user"
DEFAULT_GITHUB_USER_API_URL = "https://api.github.com/user"


def _default_privacy_statement_file() -> str:
    """
    默认隐私声明文件路径。
    - 源码运行：`marketplace/privacy-statement.md`（config 上两级目录）
    - Docker + wheel：`/app/privacy-statement.md`（site-packages 再向上一级）
    """
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "privacy-statement.md",
        here.parents[3] / "privacy-statement.md",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return str(candidates[0])


class Settings(BaseSettings):
    """Store 服务配置。"""

    app_name: str = "Store Service"
    app_version: str = "0.1.0"
    debug: bool = False

    # 默认仅监听本机回环（secure by default）；局域网/容器场景用 STORE_HOST 显式指定
    host: str = "127.0.0.1"
    port: int = 8100
    reload: bool = Field(default=False, validation_alias=AliasChoices("STORE_RELOAD", "RELOAD"))

    db_url: str = ""

    # Skill 审核管理员：env 中逗号分隔或 JSON 数组（如 ["a","b"]），须为 str 以便 pydantic-settings 不作 JSON 预解析
    review_admin_usernames: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_REVIEW_ADMIN_USERNAMES", "REVIEW_ADMIN_USERNAMES"),
    )
    # 备选：从文件每行一个用户名（UTF-8；# 行为注释）。仅当 review_admin_usernames 为空时使用
    review_admin_usernames_file: str = Field(
        default="",
        validation_alias=AliasChoices(
            "MARKET_REVIEW_ADMIN_USERNAMES_FILE",
            "REVIEW_ADMIN_USERNAMES_FILE",
        ),
    )

    # 鉴权：系统管理员 token，与请求头 X-System-Token 比对（环境变量 SYSTEM_ADMIN_TOKEN）
    system_admin_token: str = Field(default="", validation_alias="SYSTEM_ADMIN_TOKEN")
    # 系统管理员用户标识（管理员请求时作为 publisher_id / user_id 写入）；默认 system_admin
    system_admin_user: str = Field(default="system_admin", validation_alias="SYSTEM_ADMIN_USER")
    # 用户信息接口：GitCode，query 参数 access_token；默认地址见 DEFAULT_AUTH_USER_API_URL
    auth_user_api_url: str = Field(default=DEFAULT_AUTH_USER_API_URL, validation_alias="AUTH_USER_API_URL")

    # 市场搜索框下方标签筛选：运营配置优先展示的标签（逗号分隔），
    # 其余标签按使用次数自动推荐；为空则纯按热门度推荐
    market_featured_tags: str = Field(default="", validation_alias="MARKET_FEATURED_TAGS")

    # OAuth 回调后浏览器重定向到此前缀下的 /login?oauth_session=...（须与前端访问前缀一致；根路径默认无 /hub）
    oauth_frontend_origin: str = Field(
        default="http://localhost:9002",
        validation_alias=AliasChoices("MARKET_OAUTH_FRONTEND_ORIGIN", "OAUTH_FRONTEND_ORIGIN"),
    )

    # 可选：Redis / 华为云 DCS（多 worker / 多实例时必须配置，否则 OAuth pending 仅存内存）；REDIS_HOST 为空则仅用进程内存
    # CACHE_BACKEND=redis|dcs：dcs 时默认启用 SSL（可用 MARKET_REDIS_SSL 显式覆盖）
    redis_host: str = Field(default="", validation_alias=AliasChoices("MARKET_REDIS_HOST", "REDIS_HOST"))
    redis_port: int = Field(default=6379, validation_alias=AliasChoices("MARKET_REDIS_PORT", "REDIS_PORT"))
    redis_db: int = Field(default=0, validation_alias=AliasChoices("MARKET_REDIS_DB", "REDIS_DB"))
    redis_password: str = Field(default="", validation_alias="MARKET_REDIS_PASSWORD")
    cache_backend: str = Field(
        default="redis",
        validation_alias=AliasChoices("MARKET_CACHE_BACKEND", "CACHE_BACKEND", "REDIS_BACKEND"),
    )
    # 空字符串 = 未显式配置，由 cache_backend 推断；true/false 显式覆盖
    redis_ssl_env: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_REDIS_SSL", "REDIS_SSL"),
    )

    # GitCode OAuth2（应用回调 URL 须与 gitcode_oauth_redirect_uri 完全一致）
    gitcode_oauth_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MARKET_GITCODE_OAUTH_ENABLED", "GITCODE_OAUTH_ENABLED"),
    )
    gitcode_oauth_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_GITCODE_OAUTH_CLIENT_ID", "GITCODE_OAUTH_CLIENT_ID"),
    )
    gitcode_oauth_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_GITCODE_OAUTH_CLIENT_SECRET", "GITCODE_OAUTH_CLIENT_SECRET"),
    )
    gitcode_oauth_redirect_uri: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_GITCODE_OAUTH_REDIRECT_URI", "GITCODE_OAUTH_REDIRECT_URI"),
    )
    gitcode_oauth_scope: str = Field(
        default="user_info",
        validation_alias=AliasChoices("MARKET_GITCODE_OAUTH_SCOPE", "GITCODE_OAUTH_SCOPE"),
    )
    gitcode_oauth_authorize_url: str = Field(
        default="https://gitcode.com/oauth/authorize",
        validation_alias=AliasChoices("MARKET_GITCODE_OAUTH_AUTHORIZE_URL", "GITCODE_OAUTH_AUTHORIZE_URL"),
    )
    gitcode_oauth_token_url: str = Field(
        default="https://gitcode.com/oauth/token",
        validation_alias=AliasChoices("MARKET_GITCODE_OAUTH_TOKEN_URL", "GITCODE_OAUTH_TOKEN_URL"),
    )

    # GitHub OAuth2（回调 URL 须与 github_oauth_redirect_uri 完全一致）
    github_oauth_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MARKET_GITHUB_OAUTH_ENABLED", "GITHUB_OAUTH_ENABLED"),
    )
    github_oauth_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_GITHUB_OAUTH_CLIENT_ID", "GITHUB_OAUTH_CLIENT_ID"),
    )
    github_oauth_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_GITHUB_OAUTH_CLIENT_SECRET", "GITHUB_OAUTH_CLIENT_SECRET"),
    )
    github_oauth_redirect_uri: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_GITHUB_OAUTH_REDIRECT_URI", "GITHUB_OAUTH_REDIRECT_URI"),
    )
    # public_repo scope：标星公开仓库需要（PUT /user/starred/{owner}/{repo}）
    github_oauth_scope: str = Field(
        default="read:user user:email public_repo",
        validation_alias=AliasChoices("MARKET_GITHUB_OAUTH_SCOPE", "GITHUB_OAUTH_SCOPE"),
    )
    github_oauth_authorize_url: str = Field(
        default="https://github.com/login/oauth/authorize",
        validation_alias=AliasChoices("MARKET_GITHUB_OAUTH_AUTHORIZE_URL", "GITHUB_OAUTH_AUTHORIZE_URL"),
    )
    github_oauth_token_url: str = Field(
        default="https://github.com/login/oauth/access_token",
        validation_alias=AliasChoices("MARKET_GITHUB_OAUTH_TOKEN_URL", "GITHUB_OAUTH_TOKEN_URL"),
    )
    github_auth_user_api_url: str = Field(
        default=DEFAULT_GITHUB_USER_API_URL,
        validation_alias=AliasChoices("MARKET_GITHUB_AUTH_USER_API_URL", "GITHUB_AUTH_USER_API_URL"),
    )
    # 一键标星功能开关：false 时前端不显示按钮、后端 POST /github/watch 返回 404
    github_star_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MARKET_GITHUB_STAR_ENABLED", "GITHUB_STAR_ENABLED"),
    )

    # AgentOS OAuth2（Control Panel）
    agentos_oauth_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MARKET_AGENTOS_OAUTH_ENABLED", "AGENTOS_OAUTH_ENABLED"),
    )
    agentos_oauth_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_AGENTOS_OAUTH_CLIENT_ID", "AGENTOS_OAUTH_CLIENT_ID"),
    )
    agentos_oauth_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_AGENTOS_OAUTH_CLIENT_SECRET", "AGENTOS_OAUTH_CLIENT_SECRET"),
    )
    agentos_oauth_redirect_uri: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_AGENTOS_OAUTH_REDIRECT_URI", "AGENTOS_OAUTH_REDIRECT_URI"),
    )
    agentos_oauth_authorize_url: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_AGENTOS_OAUTH_AUTHORIZE_URL", "AGENTOS_OAUTH_AUTHORIZE_URL"),
    )
    agentos_oauth_token_url: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_AGENTOS_OAUTH_TOKEN_URL", "AGENTOS_OAUTH_TOKEN_URL"),
    )
    agentos_auth_user_api_url: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_AGENTOS_AUTH_USER_API_URL", "AGENTOS_AUTH_USER_API_URL"),
    )

    # 发布页「下载模板」zip：桶内对象 Key（私有桶）；为空则 GET /plugins/publish-template 返回 503
    # 仅读取环境变量 MARKET_PLUGIN_TEMPLATE_OBJECT_KEY（与类上 env_prefix 拼接字段名）
    plugin_template_object_key: str = Field(default="")

    # 发布页「下载 Skill/Swarm Skill 模板」zip：桶内对象 Key；为空则 kind=skill/swarmskill 时 GET /plugins/publish-template 返回 503
    # 仅读取 MARKET_SKILL_TEMPLATE_OBJECT_KEY
    skill_template_object_key: str = Field(default="")

    # 检索模块：skill / plugin 索引在 OBS 桶内的根路径前缀（不含前导斜杠）
    # 实际写入路径：{prefix}/{YYYYMMDDH}/，例如 skills-index/2026042117/
    retrieval_skill_index_obs_prefix: str = Field(
        default="skills-index",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_SKILL_INDEX_OBS_PREFIX", "RETRIEVAL_SKILL_INDEX_OBS_PREFIX"),
    )
    retrieval_plugin_index_obs_prefix: str = Field(
        default="plugins-index",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_PLUGIN_INDEX_OBS_PREFIX", "RETRIEVAL_PLUGIN_INDEX_OBS_PREFIX"),
    )

    # 检索模块：OBS 上保留的索引版本数（默认 168 = 每小时构建一次保留最近一周）
    retrieval_index_max_versions: int = Field(
        default=168,
        validation_alias=AliasChoices("MARKET_RETRIEVAL_INDEX_MAX_VERSIONS", "RETRIEVAL_INDEX_MAX_VERSIONS"),
    )

    # 检索模块：索引定时重建的 cron 表达式（标准 5 字段，默认每小时整点触发）
    retrieval_rebuild_cron: str = Field(
        default="0 * * * *",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_REBUILD_CRON", "RETRIEVAL_REBUILD_CRON"),
    )
    retrieval_skill_tag_cron: str = Field(
        default="* * * * *",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_SKILL_TAG_CRON", "RETRIEVAL_SKILL_TAG_CRON"),
    )

    # 检索模块：启动时是否立即触发一次全量索引构建（默认 false）
    retrieval_rebuild_on_startup: bool = Field(
        default=False,
        validation_alias=AliasChoices("MARKET_RETRIEVAL_REBUILD_ON_STARTUP", "RETRIEVAL_REBUILD_ON_STARTUP"),
    )
    retrieval_skill_tag_on_startup: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "MARKET_RETRIEVAL_SKILL_TAG_ON_STARTUP",
            "RETRIEVAL_SKILL_TAG_ON_STARTUP",
        ),
    )

    # 检索模块：直接指定 skill / plugin 索引的 OBS URI（obs://bucket/path 格式）
    # 设置后跳过 list_index_dirs 自动发现，优先用于测试或手动指定已有索引
    retrieval_skill_index_path: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_SKILL_INDEX_PATH", "RETRIEVAL_SKILL_INDEX_PATH"),
    )
    retrieval_plugin_index_path: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_PLUGIN_INDEX_PATH", "RETRIEVAL_PLUGIN_INDEX_PATH"),
    )

    # 检索模块：模型 / API 配置（非密钥字段，密钥在 main.py startup 中经 SecurityUtils 解密后注入）
    retrieval_model_api_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_MODEL_API_BASE_URL", "RETRIEVAL_MODEL_API_BASE_URL"),
    )
    retrieval_default_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_DEFAULT_LLM_MODEL", "RETRIEVAL_DEFAULT_LLM_MODEL"),
    )
    retrieval_finder_llm_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_FINDER_LLM_BASE_URL", "RETRIEVAL_FINDER_LLM_BASE_URL"),
    )
    retrieval_finder_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_FINDER_LLM_MODEL", "RETRIEVAL_FINDER_LLM_MODEL"),
    )
    # 独立的技能标签分类 LLM 配置（build_skill_tags 专用）
    retrieval_skill_tag_llm_model: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_SKILL_TAG_LLM_MODEL", "RETRIEVAL_SKILL_TAG_LLM_MODEL"),
    )
    retrieval_skill_tag_llm_api_base_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "MARKET_RETRIEVAL_SKILL_TAG_LLM_API_BASE_URL",
            "RETRIEVAL_SKILL_TAG_LLM_API_BASE_URL",
        ),
    )
    retrieval_embedding_api_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_EMBEDDING_API_BASE_URL", "RETRIEVAL_EMBEDDING_API_BASE_URL"),
    )
    retrieval_embedding_model: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_EMBEDDING_MODEL", "RETRIEVAL_EMBEDDING_MODEL"),
    )
    retrieval_embedding_batch_size: int = Field(
        default=16,
        validation_alias=AliasChoices("MARKET_RETRIEVAL_EMBEDDING_BATCH_SIZE", "RETRIEVAL_EMBEDDING_BATCH_SIZE"),
    )
    # 离线索引构建策略：bm25 | embedding | embedding_bm25 | all（all 包含 tree，需配 LLM）
    # 默认 embedding_bm25，不调用 LLM；填 all 时会用大模型构建 tree 索引（progressive 检索依赖）
    retrieval_build_method: str = Field(
        default="embedding_bm25",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_BUILD_METHOD", "RETRIEVAL_BUILD_METHOD"),
    )

    # 在线检索策略：auto | embedding | bm25 | progressive
    # auto: 有 LLM 时走 progressive+embedding+bm25，无 LLM 时走 embedding+bm25，无 embedding 时纯 bm25
    # embedding: embedding+bm25（无 LLM，性能好，默认推荐）
    # bm25: 纯关键词，最快
    # progressive: LLM 树状检索（精度最高但延迟高）
    retrieval_search_method: str = Field(
        default="embedding",
        validation_alias=AliasChoices("MARKET_RETRIEVAL_SEARCH_METHOD", "RETRIEVAL_SEARCH_METHOD"),
    )
    # 在线检索过滤阈值：卡掉低相关召回结果；可配置为 None 关闭
    retrieval_embedding_relative_min_score: float | None = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "MARKET_RETRIEVAL_EMBEDDING_RELATIVE_MIN_SCORE",
            "RETRIEVAL_EMBEDDING_RELATIVE_MIN_SCORE",
        ),
    )
    retrieval_bm25_min_query_term_matches: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices(
            "MARKET_RETRIEVAL_BM25_MIN_QUERY_TERM_MATCHES",
            "RETRIEVAL_BM25_MIN_QUERY_TERM_MATCHES",
        ),
    )

    # skill-import：单进程滑动窗口限流（每分钟请求数，0 关闭）；多 worker 时各进程独立计数
    skill_import_rate_limit_per_minute: int = Field(
        default=30,
        ge=0,
        validation_alias=AliasChoices(
            "MARKET_SKILL_IMPORT_RATE_LIMIT_PER_MINUTE",
            "SKILL_IMPORT_RATE_LIMIT_PER_MINUTE",
        ),
    )

    # Git 源创建/同步：单进程滑动窗口限流（每分钟请求数，0 关闭）；与 skill-import 独立计数
    git_source_sync_rate_limit_per_minute: int = Field(
        default=30,
        ge=0,
        validation_alias=AliasChoices(
            "MARKET_GIT_SOURCE_SYNC_RATE_LIMIT_PER_MINUTE",
            "GIT_SOURCE_SYNC_RATE_LIMIT_PER_MINUTE",
        ),
    )

    # ClawHub 兼容层：匿名接口进程内滑动窗口限流（每分钟 / IP；0 关闭）
    clawhub_compat_rate_limit_per_minute: int = Field(
        default=60,
        ge=0,
        validation_alias=AliasChoices(
            "MARKET_CLAWHUB_COMPAT_RATE_LIMIT_PER_MINUTE",
            "CLAWHUB_COMPAT_RATE_LIMIT_PER_MINUTE",
        ),
    )

    # Skill 发布自动审查总开关
    skill_review_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MARKET_SKILL_REVIEW_ENABLED", "SKILL_REVIEW_ENABLED"),
    )

    # 开关：开启后服务端拒绝发布非 skill-like 类型（tools / mcp-stdio / restful-api 等会执行代码的插件），
    # 仅允许 skill / swarmskill 上架。用于从发布入口直接关闭“可执行非 skill 插件”的上架，消除任意代码执行风险。
    # 默认开启（安全优先）；如需放开，置 MARKET_BLOCK_NONSKILL_PLUGIN_PUBLISH=false。
    block_nonskill_plugin_publish: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "MARKET_BLOCK_NONSKILL_PLUGIN_PUBLISH", "BLOCK_NONSKILL_PLUGIN_PUBLISH"
        ),
    )

    # Skill 能力默认模型配置：预留给非审查类 Skill 能力使用，系统审查不会隐式回退到这里
    skill_model_default_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_SKILL_MODEL_DEFAULT_BASE_URL", "SKILL_MODEL_DEFAULT_BASE_URL"),
    )
    skill_model_default_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_SKILL_MODEL_DEFAULT_API_KEY", "SKILL_MODEL_DEFAULT_API_KEY"),
    )
    skill_model_default_model: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_SKILL_MODEL_DEFAULT_MODEL", "SKILL_MODEL_DEFAULT_MODEL"),
    )
    skill_model_default_timeout_seconds: int = Field(
        default=300,
        ge=1,
        validation_alias=AliasChoices(
            "MARKET_SKILL_MODEL_DEFAULT_TIMEOUT_SECONDS",
            "SKILL_MODEL_DEFAULT_TIMEOUT_SECONDS",
        ),
    )

    # Skill 审查专用模型配置；开启系统审查时必须显式配置完整，缺省按 fail-close 处理
    skill_review_model_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_SKILL_REVIEW_MODEL_BASE_URL", "SKILL_REVIEW_MODEL_BASE_URL"),
    )
    skill_review_model_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_SKILL_REVIEW_MODEL_API_KEY", "SKILL_REVIEW_MODEL_API_KEY"),
    )
    skill_review_model_name: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_SKILL_REVIEW_MODEL_NAME", "SKILL_REVIEW_MODEL_NAME"),
    )
    skill_review_model_timeout_seconds: int = Field(
        default=300,
        ge=1,
        validation_alias=AliasChoices(
            "MARKET_SKILL_REVIEW_MODEL_TIMEOUT_SECONDS",
            "SKILL_REVIEW_MODEL_TIMEOUT_SECONDS",
        ),
    )

    # 接口日志开关（默认开启）
    interface_log_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("MARKET_INTERFACE_LOG_ENABLED", "INTERFACE_LOG_ENABLED"),
    )

    # Skill Playground：在线试用功能开关。关闭时后端路由不注册，前端按钮不显示，不影响现有功能。
    playground_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MARKET_PLAYGROUND_ENABLED", "PLAYGROUND_ENABLED"),
    )
    # skill-runner 服务地址（Playground 后端；仅 playground_enabled=true 时生效）
    skill_runner_url: str = Field(
        default="http://127.0.0.1:8900",
        validation_alias=AliasChoices("MARKET_SKILL_RUNNER_URL", "SKILL_RUNNER_URL"),
    )
    # Playground 每日配额：每用户每自然日最多创建的 session 数（0 = 不限制；管理员始终不受限）
    playground_daily_limit: int = Field(
        default=20,
        ge=0,
        validation_alias=AliasChoices("MARKET_PLAYGROUND_DAILY_LIMIT", "PLAYGROUND_DAILY_LIMIT"),
    )
    # Playground 每用户同时可活跃的 session 数上限（0 = 不限制；管理员始终不受限）
    playground_max_concurrent_sessions: int = Field(
        default=3,
        ge=0,
        validation_alias=AliasChoices(
            "MARKET_PLAYGROUND_MAX_CONCURRENT_SESSIONS", "PLAYGROUND_MAX_CONCURRENT_SESSIONS"
        ),
    )
    # Playground 每用户每分钟发消息上限（0 = 不限制；管理员始终不受限）
    playground_message_rate_per_minute: int = Field(
        default=10,
        ge=0,
        validation_alias=AliasChoices("MARKET_PLAYGROUND_MSG_RATE_PER_MIN", "PLAYGROUND_MSG_RATE_PER_MIN"),
    )
    # 每用户最多创建的组群数（0 = 不限制；特权用户始终不受限）
    max_groups_per_user: int = Field(
        default=10,
        ge=0,
        validation_alias=AliasChoices("MARKET_MAX_GROUPS_PER_USER", "MAX_GROUPS_PER_USER"),
    )
    # 每组群最多成员数（0 = 不限制；特权用户始终不受限）
    max_members_per_group: int = Field(
        default=500,
        ge=0,
        validation_alias=AliasChoices("MARKET_MAX_MEMBERS_PER_GROUP", "MAX_MEMBERS_PER_GROUP"),
    )
    # Playground 多实例开关：false（默认）= 单实例，会话跟踪状态存进程内存，行为与
    # 引入本开关前完全一致；true = 状态外置 Redis（复用 REDIS_HOST 等配置，必须已配置，
    # 否则启动即报错），marketplace 可多副本部署，且支持 skill-runner 多实例的粘性路由。
    playground_multi_instance: bool = Field(
        default=False,
        validation_alias=AliasChoices("MARKET_PLAYGROUND_MULTI_INSTANCE", "PLAYGROUND_MULTI_INSTANCE"),
    )
    # 多实例模式下会话注册记录的 Redis TTL 兜底（秒）；应 ≥ skill-runner 会话最大生命周期
    # 加余量。正常路径由 DELETE/beacon/探活清理，TTL 只兜底异常残留。
    playground_session_ttl_seconds: int = Field(
        default=7200,
        ge=60,
        validation_alias=AliasChoices("MARKET_PLAYGROUND_SESSION_TTL", "PLAYGROUND_SESSION_TTL"),
    )
    # Playground 上传单文件字节上限（入口主闸；会话累计/数量上限在 skill-runner 侧）
    playground_upload_max_file_bytes: int = Field(
        default=5 * 1024 * 1024,
        gt=0,
        validation_alias=AliasChoices("MARKET_PLAYGROUND_UPLOAD_MAX_FILE_BYTES", "PLAYGROUND_UPLOAD_MAX_FILE_BYTES"),
    )

    # ClawHub CLI 兼容：与 marketplace 同进程，路由挂在 /api/v1
    clawhub_compat_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("MARKET_CLAWHUB_COMPAT_ENABLED", "CLAWHUB_COMPAT_ENABLED"),
    )
    clawhub_plugin_type: str = Field(
        default="skill",
        validation_alias=AliasChoices("MARKET_CLAWHUB_PLUGIN_TYPE", "CLAWHUB_PLUGIN_TYPE"),
    )

    # Recommender（个性化推荐：Milvus + Redis 历史 / 下载量兜底）。默认关闭，本地部署按需开启。
    recommender_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MARKET_RECOMMENDER_ENABLED", "RECOMMENDER_ENABLED"),
    )
    # 首页「推荐精选」一次召回上限，再按 page/page_size 切片
    rec_list_top_k: int = Field(
        default=50,
        ge=1,
        le=2000,
        validation_alias=AliasChoices("MARKET_REC_LIST_TOP_K", "REC_LIST_TOP_K"),
    )
    milvus_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("MARKET_MILVUS_HOST", "MILVUS_HOST"),
    )
    milvus_port: int = Field(
        default=19530,
        validation_alias=AliasChoices("MARKET_MILVUS_PORT", "MILVUS_PORT"),
    )
    milvus_collection: str = Field(
        default="skill_index",
        validation_alias=AliasChoices("MARKET_MILVUS_COLLECTION", "MILVUS_COLLECTION"),
    )
    # Optional Milvus auth (when server authorizationEnabled=true; default root/Milvus)
    milvus_user: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_MILVUS_USER", "MILVUS_USER"),
    )
    milvus_password: str = Field(
        default="",
        validation_alias=AliasChoices("MARKET_MILVUS_PASSWORD", "MILVUS_PASSWORD"),
    )
    rec_package_sync_cron: str = Field(
        default="30 * * * *",
        validation_alias=AliasChoices("MARKET_REC_PACKAGE_SYNC_CRON", "REC_PACKAGE_SYNC_CRON"),
    )
    rec_milvus_incremental_cron: str = Field(
        default="0 * * * *",
        validation_alias=AliasChoices(
            "MARKET_REC_MILVUS_INCREMENTAL_CRON",
            "REC_MILVUS_INCREMENTAL_CRON",
        ),
    )
    rec_milvus_full_cron: str = Field(
        default="0 3 * * *",
        validation_alias=AliasChoices("MARKET_REC_MILVUS_FULL_CRON", "REC_MILVUS_FULL_CRON"),
    )
    rec_redis_sync_cron: str = Field(
        default="15 * * * *",
        validation_alias=AliasChoices("MARKET_REC_REDIS_SYNC_CRON", "REC_REDIS_SYNC_CRON"),
    )
    # 服务启动时立即跑一轮离线任务（redis_sync + milvus_full）；默认开启
    rec_rebuild_on_startup: bool = Field(
        default=True,
        validation_alias=AliasChoices("MARKET_REC_REBUILD_ON_STARTUP", "REC_REBUILD_ON_STARTUP"),
    )
    redis_topk_install_key: str = Field(
        default="skill_rec:topk:install",
        validation_alias=AliasChoices("MARKET_REDIS_TOPK_INSTALL_KEY", "REDIS_TOPK_INSTALL_KEY"),
    )
    redis_user_seq_key_prefix: str = Field(
        default="skill_rec:user",
        validation_alias=AliasChoices("MARKET_REDIS_USER_SEQ_KEY_PREFIX", "REDIS_USER_SEQ_KEY_PREFIX"),
    )

    # 隐私声明：本地 Markdown（UTF-8）。默认 marketplace/privacy-statement.md；可用环境变量覆盖路径；显式置空则不再读文件
    privacy_statement_file: str = Field(
        default_factory=_default_privacy_statement_file,
        validation_alias=AliasChoices(
            "MARKET_PRIVACY_STATEMENT_FILE",
            "PRIVACY_STATEMENT_FILE",
        ),
    )

    @field_validator("system_admin_user", mode="before")
    @classmethod
    def _normalize_system_admin_user(cls, v: object) -> str:
        s = "" if v is None else str(v).strip()
        return s or "system_admin"

    class Config:
        env_prefix = "MARKET_"
        env_file = "../../../.env"
        case_sensitive = False
        extra = "ignore"


def parse_review_admin_usernames_value(raw: str) -> list[str]:
    """解析 MARKET_REVIEW_ADMIN_USERNAMES：逗号分隔，或 JSON 数组（与 GitCode login 精确匹配）。"""
    s = (raw or "").strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


settings = Settings()
