# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path, override=True)

# Make the retrieval/ package importable.  Its __init__.py handles the
# internal sys.path setup required by bare imports (e.g. "from indexing.bm25").
#
# In local dev, main.py lives in the project root alongside retrieval/.
# In Docker, main.py is in /app/site-packages/ but retrieval/ source is at
# /app/retrieval/ (copied separately, wheel-installed copy removed).
_RETRIEVAL_PARENTS = [
    "/app",
    str(Path(__file__).resolve().parent),
]
for _parent in _RETRIEVAL_PARENTS:
    if not os.path.isdir(os.path.join(_parent, "retrieval", "indexing")):
        continue
    # Prefer retrieval parent first on sys.path, without insert(0, ...) (G.PSL.03).
    # Dedupe by normpath so the same directory under different spellings does not
    # appear twice and shadow unrelated modules.
    normalized = os.path.normpath(_parent)
    deduped = [p for p in sys.path if os.path.normpath(p) != normalized]
    sys.path[:] = [normalized] + deduped
    break

import uvicorn
from fastapi import FastAPI, Request

from plugins_market.core.audit_failed import audit_failed_mutation
from plugins_market.core.context import start_time_var, set_request_context
from plugins_market.core.config import settings
from plugins_market.core.database import engine
from plugins_market.core.errors import (
    as_json_response_body,
    http_error_payload,
)
from plugins_market.core.http_error_logging import register_exception_handlers, response_with_error_logging
from plugins_market.core.logging import setup_logging
from plugins_market.core.middleware.rate_limit import RateLimitMiddleware
from plugins_market.core.middleware.request_id import RequestIDMiddleware
from plugins_market.models.base import Base
import plugins_market.models.site_notifications  # noqa: F401  # register table for create_all
import plugins_market.models.git_sources  # noqa: F401  # register git_sources for create_all
from plugins_market.models import groups as group_models

_REGISTERED_MODEL_MODULES = (group_models,)
from plugins_market.routers.register import router_register
from plugins_market.core.s3_storage_client import close_storage_client_if_initialized
from plugins_market.validation.constants import MAX_FILE_SIZE

from plugins_market.core.logging import get_logger

logger = get_logger(__name__)


def _resolve_build_method(value: str):
    """Parse build_method text into BuildMethod flags."""
    from indexing.workflows.artifacts import BuildMethod  # type: ignore[import]

    norm_build_method = str(value or "").strip().lower().replace("-", "_")
    if norm_build_method in ("", "all"):
        return BuildMethod.ALL
    if norm_build_method == "embedding_bm25":
        return BuildMethod.EMBEDDING | BuildMethod.BM25

    method = BuildMethod(0)
    for part in norm_build_method.replace("|", "+").replace(",", "+").split("+"):
        token = part.strip()
        if not token:
            continue
        if token == "bm25":
            method |= BuildMethod.BM25
        elif token == "embedding":
            method |= BuildMethod.EMBEDDING
        elif token in ("tree", "llm"):
            method |= BuildMethod.TREE
        elif token == "all":
            return BuildMethod.ALL
    return method or BuildMethod.ALL


def _skill_tag_llm_config_status(skill_tag_build_config) -> tuple[bool, str]:
    """校验 skill-tag 分类用的 LLM 配置是否真实可用。

    skill-tag 为必选功能（直接驱动首页类别展示）。返回 (ok, reason)；
    ok=False 时 reason 说明缺哪项 / 哪项非法，供启动时明确报错。
    """
    if skill_tag_build_config is None:
        return False, "BuildConfig 未初始化（retrieval 模块不可导入）"
    client = getattr(skill_tag_build_config, "llm_openai_client", None)
    if client is None:
        return False, "未配置 LLM 端点/密钥（MARKET_RETRIEVAL_SKILL_TAG_LLM_API_BASE_URL / _API_KEY）"
    model = str(getattr(skill_tag_build_config, "llm_model", "") or "").strip()
    if not model:
        return False, "缺少模型名（MARKET_RETRIEVAL_SKILL_TAG_LLM_MODEL）"
    # 不据"key 为空 / 占位 dummy"判定未配置：无鉴权的本地大模型（vLLM/ollama 等）本就不需要 key。
    # 校验读的是 client 上已解密的明文 key（即便 .env 存的是密文，这里拿到的也是解密值）。
    # 真正必须拦下的是含非 ASCII 字符的值（中文乱码 / 把行内注释当成了值）：HTTP 请求行与
    # Authorization 头只能是 ASCII，这类值塞进去发请求必崩——正是本次事故的根因。
    api_key = str(getattr(client, "api_key", "") or "")
    base_url = str(getattr(client, "base_url", "") or "")
    for _label, _value in (("API 密钥", api_key), ("API 端点", base_url)):
        try:
            _value.encode("ascii")
        except UnicodeEncodeError:
            return False, f"{_label}含非 ASCII 字符（疑似配置/解密有误，例如把行内注释当成了值）"
    return True, ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    setup_logging(debug=settings.debug)
    # playground_usage 表仅在 Playground 开启时建（关闭时不建这张空表）。
    # 须在 create_all 之前注册模型，故在此条件导入。
    if settings.playground_enabled:
        import importlib
        importlib.import_module("plugins_market.models.playground_usage")  # register for create_all
    Base.metadata.create_all(bind=engine)

    # ── retrieval startup ──────────────────────────────────────────────────
    from plugins_market.core.database import SessionLocal
    from plugins_market.services.git_skill_sync import recover_interrupted_git_syncing_on_startup

    _git_orphan_db = SessionLocal()
    try:
        n_orphan = recover_interrupted_git_syncing_on_startup(_git_orphan_db)
        if n_orphan:
            logger.info("git sync: recovered %s stale syncing source(s) on startup", n_orphan)
    finally:
        _git_orphan_db.close()
    from plugins_market.core.s3_storage_client import get_storage_client
    from plugins_market.retrieval.daily_rebuild import (
        SkillTagRefreshOptions,
        _storage_uri_scheme,
        list_index_dirs,
        rebuild_all,
        refresh_skill_tags,
    )
    from plugins_market.retrieval.index_manager import get_index_manager
    from plugins_market.retrieval.reload_consumer import run_reload_consumer

    index_manager = get_index_manager()
    skill_prefix = settings.retrieval_skill_index_obs_prefix
    plugin_prefix = settings.retrieval_plugin_index_obs_prefix

    from common.security.security_utils import SecurityUtils
    from openai import OpenAI

    storage = get_storage_client()

    _llm_client = None
    _llm_model = settings.retrieval_finder_llm_model or settings.retrieval_default_llm_model
    if settings.retrieval_finder_llm_base_url or settings.retrieval_model_api_base_url:
        try:
            _llm_api_key = SecurityUtils.get_decrypt_secret("MARKET_RETRIEVAL_MODEL_API_KEY", default="") or "dummy"
            _llm_base_url = settings.retrieval_finder_llm_base_url or settings.retrieval_model_api_base_url or None
            _llm_client = OpenAI(base_url=_llm_base_url, api_key=_llm_api_key)
        except Exception as _exc:
            logger.warning("retrieval: failed to create LLM client: %s", _exc)

    _embedding_client = None
    _embedding_build_client = None
    _emb_api_key = ""
    if settings.retrieval_embedding_api_base_url:
        try:
            _emb_api_key = SecurityUtils.get_decrypt_secret("MARKET_RETRIEVAL_EMBEDDING_API_KEY", default="") or ""
            _embedding_client = OpenAI(
                base_url=settings.retrieval_embedding_api_base_url,
                api_key=_emb_api_key or "dummy",
            )
            from indexing.embedding import create_openai_embedding_client  # type: ignore[import]

            _embedding_build_client = create_openai_embedding_client(
                base_url=settings.retrieval_embedding_api_base_url,
                api_key=_emb_api_key,
                model=settings.retrieval_embedding_model,
            )
        except Exception as _exc:
            logger.warning("retrieval: failed to create embedding client: %s", _exc)

    index_manager.configure(
        llm_openai_client=_llm_client,
        llm_model=_llm_model,
        embedding_openai_client=_embedding_client,
        embedding_model=settings.retrieval_embedding_model,
    )

    try:
        from indexing.workflows.artifacts import BuildConfig  # type: ignore[import]

        _index_build_config = BuildConfig(
            method=_resolve_build_method(settings.retrieval_build_method),
            llm_openai_client=_llm_client,
            llm_model=_llm_model,
            embedding_openai_client=_embedding_build_client,
            embedding_model=settings.retrieval_embedding_model,
            embedding_batch_size=settings.retrieval_embedding_batch_size,
        )

        # Separate config for skill tag classification (build_skill_tags)
        # Ensure LLM client is available for proper skill categorization
        _skill_tag_llm_client = _llm_client
        _skill_tag_llm_model = _llm_model

        # Check if there's independent skill tag LLM configuration
        if settings.retrieval_skill_tag_llm_model:
            _skill_tag_llm_model = settings.retrieval_skill_tag_llm_model
            _skill_tag_llm_base_url = (
                settings.retrieval_skill_tag_llm_api_base_url or settings.retrieval_model_api_base_url
            )
            try:
                _skill_tag_api_key = SecurityUtils.get_decrypt_secret(
                    "MARKET_RETRIEVAL_SKILL_TAG_LLM_API_KEY",
                    default="",
                ) or (SecurityUtils.get_decrypt_secret("MARKET_RETRIEVAL_MODEL_API_KEY", default="") or "")
                if _skill_tag_api_key and _skill_tag_llm_base_url:
                    _skill_tag_llm_client = OpenAI(base_url=_skill_tag_llm_base_url, api_key=_skill_tag_api_key)
                    logger.info("retrieval: using independent skill tag LLM config (model=%s)", _skill_tag_llm_model)
            except Exception as _exc:
                logger.warning("retrieval: failed to create independent skill tag LLM client: %s", _exc)
        elif _skill_tag_llm_client is None and settings.retrieval_model_api_base_url:
            # Fallback: create LLM client using main config if it wasn't created earlier
            try:
                _llm_api_key = SecurityUtils.get_decrypt_secret("MARKET_RETRIEVAL_MODEL_API_KEY", default="") or ""
                if _llm_api_key:
                    _skill_tag_llm_client = OpenAI(base_url=settings.retrieval_model_api_base_url, api_key=_llm_api_key)
                    logger.info("retrieval: created LLM client for skill tag classification (fallback to main config)")
            except Exception as _exc:
                logger.warning("retrieval: failed to create LLM client for skill tagging: %s", _exc)

        _skill_tag_build_config = BuildConfig(
            method=_resolve_build_method(settings.retrieval_build_method),
            llm_openai_client=_skill_tag_llm_client,
            llm_model=_skill_tag_llm_model,
            embedding_openai_client=_embedding_build_client,
            embedding_model=settings.retrieval_embedding_model,
            embedding_batch_size=settings.retrieval_embedding_batch_size,
        )
    except ImportError:
        _index_build_config = None
        _skill_tag_build_config = None
        logger.warning("retrieval: BuildConfig not importable, builds will run without model config")

    # skill-tag 必选校验：配置缺失/无效时明确报错并禁用分类任务（不退出，其余功能照常）
    _skill_tag_ok, _skill_tag_reason = _skill_tag_llm_config_status(_skill_tag_build_config)
    if not _skill_tag_ok:
        logger.error(
            "skill-tag 分类功能未启用：%s。该功能为必选，直接影响首页类别展示；"
            "为避免每分钟空跑/报错，已跳过注册分类定时任务，其余功能正常运行。"
            "请正确配置 MARKET_RETRIEVAL_SKILL_TAG_LLM_MODEL / _API_BASE_URL / _API_KEY"
            "（须真实有效、可正常调用）后重启。",
            _skill_tag_reason,
        )

    redis_client = None
    if settings.redis_host:
        try:
            import redis as redis_lib
            redis_password = (
                SecurityUtils.get_decrypt_secret("MARKET_REDIS_PASSWORD", default="")
                or None
            )
            redis_client = redis_lib.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=redis_password,
                decode_responses=False,
                socket_connect_timeout=3,
                # 须大于 reload_consumer 的 xreadgroup block=5s，否则正常轮询被误判超时；
                # 坏连接限时报错后由消费者循环自行重试恢复
                socket_timeout=10,
                health_check_interval=30,
            )
            redis_client.ping()
            logger.info("retrieval: Redis connected %s:%s", settings.redis_host, settings.redis_port)
        except Exception as exc:
            logger.warning("retrieval: Redis unavailable (%s), reload consumer disabled", exc)
            redis_client = None

    uri_scheme = _storage_uri_scheme(storage)

    async def _warm_start_one_group(group: str, prefix: str) -> None:
        """后台加载单个分组的检索索引。阻塞的下载/加载放线程池，避免卡住事件循环；
        wait_for 给每个分组一个时间上限，超时只跳过该组、不影响服务与其它组。"""
        index_load_timeout = 600  # 每个分组最多等 10 分钟
        try:
            direct_path = getattr(settings, f"retrieval_{group}_index_path", "").strip()
            if direct_path:
                target = direct_path
            else:
                # list_index_dirs 会访问对象存储，同样放线程池，避免阻塞事件循环
                dirs = await asyncio.to_thread(list_index_dirs, storage, prefix)
                if not dirs:
                    logger.info("retrieval warm-start: no index found for group=%s prefix=%s, skipping", group, prefix)
                    return
                bucket = storage.config.bucket_name
                target = f"{uri_scheme}://{bucket}/{dirs[0]}"
            logger.info(
                "retrieval warm-start: loading group=%s from %s (timeout=%ds, background)",
                group, target, index_load_timeout,
            )
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(index_manager.load, group, target),
                    timeout=index_load_timeout,
                )
                logger.info("retrieval warm-start: group=%s loaded successfully", group)
            except asyncio.TimeoutError:
                logger.warning("retrieval warm-start: group=%s load timed out after %ds", group, index_load_timeout)
            except Exception as e:
                logger.warning("retrieval warm-start: group=%s load failed: %s", group, e)
        except Exception as exc:
            logger.warning("retrieval warm-start unexpected error group=%s: %s", group, exc, exc_info=True)

    async def _warm_start_indexes() -> None:
        # 两个分组串行加载（共用下载/解析资源，串行更稳）；整体在后台跑，不阻塞服务启动。
        for group, prefix in (("skill", skill_prefix), ("plugin", plugin_prefix)):
            await _warm_start_one_group(group, prefix)
        logger.info("retrieval warm-start: background warm-up finished")

    # 不阻塞 yield：索引就绪前检索接口自动降级（search.py 的 is_ready 守卫），服务立即可用。
    app.state.warmup_task = asyncio.create_task(_warm_start_indexes())

    if redis_client is not None:
        reload_task = asyncio.create_task(run_reload_consumer(index_manager, redis_client))
        app.state.reload_task = reload_task

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    def _run_index_rebuild(skip_lock: bool = False) -> None:
        started = time.monotonic()
        logger.info("retrieval index rebuild run begin [skip_lock=%s]", skip_lock)
        rebuild_all(
            SessionLocal,
            skill_prefix,
            plugin_prefix,
            storage,
            index_manager,
            redis_client,
            _index_build_config,
            _skill_tag_build_config,
            run_skill_tag=False,
            max_index_versions=settings.retrieval_index_max_versions,
            skip_lock=skip_lock,
        )
        elapsed = time.monotonic() - started
        logger.info("retrieval index rebuild run end [skip_lock=%s elapsed=%.1fs]", skip_lock, elapsed)

    def _run_skill_tag_refresh(skip_lock: bool = False) -> None:
        started = time.monotonic()
        logger.info("retrieval skill-tag refresh run begin [skip_lock=%s]", skip_lock)
        refresh_skill_tags(
            SkillTagRefreshOptions(
                db_factory=SessionLocal,
                skill_prefix=skill_prefix,
                storage=storage,
                redis_client=redis_client,
                build_config=_index_build_config,
                skill_tag_build_config=_skill_tag_build_config,
                skip_lock=skip_lock,
            )
        )
        elapsed = time.monotonic() - started
        logger.info("retrieval skill-tag refresh run end [skip_lock=%s elapsed=%.1fs]", skip_lock, elapsed)

    async def _index_rebuild_job() -> None:
        loop = asyncio.get_running_loop()
        # Set timeout to 2350s (39.2 min), slightly less than lock TTL (2400s = 40min) to prevent race condition
        try:
            await asyncio.wait_for(loop.run_in_executor(None, _run_index_rebuild), timeout=2350)
        except asyncio.TimeoutError:
            logger.error("retrieval index rebuild timed out after 39.2 minutes")

    async def _skill_tag_job() -> None:
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(loop.run_in_executor(None, _run_skill_tag_refresh), timeout=2350)
        except asyncio.TimeoutError:
            logger.error("retrieval skill-tag refresh timed out after 39.2 minutes")

    def _run_hot_score_recompute() -> None:
        started = time.monotonic()
        logger.info("hot_score recompute run begin")
        from plugins_market.services.hot_score import recompute_hot_scores

        result = recompute_hot_scores(SessionLocal)
        elapsed = time.monotonic() - started
        logger.info(
            "hot_score recompute run end [updated=%s elapsed=%.1fs]",
            result.get("updated"),
            elapsed,
        )

    async def _hot_score_job() -> None:
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(loop.run_in_executor(None, _run_hot_score_recompute), timeout=2350)
        except asyncio.TimeoutError:
            logger.error("hot_score recompute timed out after 39.2 minutes")
        except Exception as exc:
            logger.exception("hot_score recompute failed: %s", exc)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _index_rebuild_job,
        CronTrigger.from_crontab(settings.retrieval_rebuild_cron),
        id="index_rebuild",
        replace_existing=True,
        misfire_grace_time=60,
    )
    if _skill_tag_ok:
        scheduler.add_job(
            _skill_tag_job,
            CronTrigger.from_crontab(settings.retrieval_skill_tag_cron),
            id="skill_tag_refresh",
            replace_existing=True,
            misfire_grace_time=60,
        )
    scheduler.add_job(
        _hot_score_job,
        CronTrigger.from_crontab(settings.hot_score_recompute_cron),
        id="hot_score_recompute",
        replace_existing=True,
        misfire_grace_time=60,
    )
    scheduler.start()
    app.state.retrieval_scheduler = scheduler
    app.state.retrieval_redis = redis_client
    logger.info(
        "retrieval startup complete - index_cron=%s skill_tag_cron=%s hot_score_cron=%s",
        settings.retrieval_rebuild_cron,
        settings.retrieval_skill_tag_cron,
        settings.hot_score_recompute_cron,
    )

    if settings.retrieval_rebuild_on_startup:
        logger.info("retrieval: REBUILD_ON_STARTUP=true, scheduling immediate index rebuild")

        async def _startup_rebuild() -> None:
            logger.info("retrieval startup index rebuild begin")
            loop = asyncio.get_running_loop()
            started = time.monotonic()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: _run_index_rebuild(skip_lock=True)),
                    timeout=2350,
                )
            except asyncio.TimeoutError:
                logger.error("retrieval startup index rebuild timed out after 39.2 minutes")
            except Exception as exc:
                logger.exception("retrieval startup index rebuild failed: %s", exc)
            finally:
                elapsed = time.monotonic() - started
                logger.info("retrieval startup index rebuild end [elapsed=%.1fs]", elapsed)

        app.state.startup_rebuild_task = asyncio.create_task(_startup_rebuild())

    if settings.retrieval_skill_tag_on_startup and _skill_tag_ok:
        logger.info("retrieval: SKILL_TAG_ON_STARTUP=true, scheduling immediate skill-tag refresh")

        async def _startup_skill_tag_refresh() -> None:
            logger.info("retrieval startup skill-tag refresh begin")
            loop = asyncio.get_running_loop()
            started = time.monotonic()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: _run_skill_tag_refresh(skip_lock=True)),
                    timeout=2350,
                )
            except asyncio.TimeoutError:
                logger.error("retrieval startup skill-tag refresh timed out after 39.2 minutes")
            except Exception as exc:
                logger.exception("retrieval startup skill-tag refresh failed: %s", exc)
            finally:
                elapsed = time.monotonic() - started
                logger.info("retrieval startup skill-tag refresh end [elapsed=%.1fs]", elapsed)

        app.state.startup_skill_tag_task = asyncio.create_task(_startup_skill_tag_refresh())

    if settings.hot_score_recompute_on_startup:
        logger.info("hot_score: RECOMPUTE_ON_STARTUP=true, scheduling immediate recompute")

        async def _startup_hot_score_recompute() -> None:
            logger.info("hot_score startup recompute begin")
            loop = asyncio.get_running_loop()
            started = time.monotonic()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, _run_hot_score_recompute),
                    timeout=2350,
                )
            except asyncio.TimeoutError:
                logger.error("hot_score startup recompute timed out after 39.2 minutes")
            except Exception as exc:
                logger.exception("hot_score startup recompute failed: %s", exc)
            finally:
                elapsed = time.monotonic() - started
                logger.info("hot_score startup recompute end [elapsed=%.1fs]", elapsed)

        app.state.startup_hot_score_task = asyncio.create_task(_startup_hot_score_recompute())

    if settings.recommender_enabled:
        from plugins_market.recommender.bootstrap import apply_recommender_settings_to_env
        from plugins_market.recommender.jobs import (
            run_rec_milvus_full,
            run_rec_milvus_incremental,
            run_rec_package_sync,
            run_rec_redis_sync,
        )

        apply_recommender_settings_to_env()

        async def _rec_job(name: str, fn) -> None:
            loop = asyncio.get_running_loop()
            started = time.monotonic()
            logger.info("recommender job begin name=%s", name)
            try:
                await asyncio.wait_for(loop.run_in_executor(None, fn), timeout=2350)
            except asyncio.TimeoutError:
                logger.error("recommender job timed out name=%s", name)
            except Exception as exc:
                logger.exception("recommender job failed name=%s: %s", name, exc)
            finally:
                logger.info(
                    "recommender job end name=%s elapsed=%.1fs",
                    name,
                    time.monotonic() - started,
                )

        async def _rec_package_sync_job() -> None:
            await _rec_job("package_sync", run_rec_package_sync)

        async def _rec_milvus_incremental_job() -> None:
            await _rec_job("milvus_incremental", run_rec_milvus_incremental)

        async def _rec_milvus_full_job() -> None:
            await _rec_job("milvus_full", run_rec_milvus_full)

        async def _rec_redis_sync_job() -> None:
            await _rec_job("redis_sync", run_rec_redis_sync)

        scheduler.add_job(
            _rec_package_sync_job,
            CronTrigger.from_crontab(settings.rec_package_sync_cron),
            id="rec_package_sync",
            replace_existing=True,
            misfire_grace_time=60,
        )
        scheduler.add_job(
            _rec_milvus_incremental_job,
            CronTrigger.from_crontab(settings.rec_milvus_incremental_cron),
            id="rec_milvus_incremental",
            replace_existing=True,
            misfire_grace_time=60,
        )
        scheduler.add_job(
            _rec_milvus_full_job,
            CronTrigger.from_crontab(settings.rec_milvus_full_cron),
            id="rec_milvus_full",
            replace_existing=True,
            misfire_grace_time=60,
        )
        scheduler.add_job(
            _rec_redis_sync_job,
            CronTrigger.from_crontab(settings.rec_redis_sync_cron),
            id="rec_redis_sync",
            replace_existing=True,
            misfire_grace_time=60,
        )
        logger.info(
            "recommender enabled — package_cron=%s milvus_inc=%s milvus_full=%s redis=%s "
            "list_top_k=%s rebuild_on_startup=%s",
            settings.rec_package_sync_cron,
            settings.rec_milvus_incremental_cron,
            settings.rec_milvus_full_cron,
            settings.rec_redis_sync_cron,
            settings.rec_list_top_k,
            settings.rec_rebuild_on_startup,
        )

        if settings.rec_rebuild_on_startup:
            logger.info("recommender: REBUILD_ON_STARTUP=true, scheduling immediate offline jobs")

            async def _startup_rec_rebuild() -> None:
                # Redis first (fast cold-start fallback), then Milvus full (creates collection).
                await _rec_job("redis_sync(startup)", run_rec_redis_sync)
                await _rec_job("milvus_full(startup)", run_rec_milvus_full)

            app.state.startup_rec_rebuild_task = asyncio.create_task(_startup_rec_rebuild())

    # ── yield (app runs) ───────────────────────────────────────────────────
    yield

    # ── shutdown ───────────────────────────────────────────────────────────
    _scheduler = getattr(app.state, "retrieval_scheduler", None)
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception as exc:
            logger.warning("shutdown: scheduler stop failed: %s", exc)
    _reload_task = getattr(app.state, "reload_task", None)
    if _reload_task is not None:
        _reload_task.cancel()
    _warmup_task = getattr(app.state, "warmup_task", None)
    if _warmup_task is not None:
        _warmup_task.cancel()
    _redis = getattr(app.state, "retrieval_redis", None)
    if _redis is not None:
        try:
            _redis.close()
        except Exception as exc:
            logger.warning("shutdown: Redis close failed: %s", exc)
    try:
        close_storage_client_if_initialized()
    except Exception as e:
        logger.warning("shutdown cleanup: failed to close storage client: %s", e)
    try:
        engine.dispose()
    except Exception as e:
        logger.warning("shutdown cleanup: failed to dispose db engine: %s", e)
    # 关闭 GitHub 代理共享 httpx 客户端
    try:
        from plugins_market.core.github_proxy import close_shared_client
        await close_shared_client()
    except Exception as e:
        logger.warning("shutdown cleanup: failed to close github proxy client: %s", e)


def create_app() -> FastAPI:
    fastapi_app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    # 统一速率限制与请求上下文中间件。
    # 注意 Starlette add_middleware 为 insert(0)：后注册者更外层。
    # 故 RateLimitMiddleware 先注册、RequestIDMiddleware 后注册，
    # 使 RequestID 外层于 RateLimit —— 请求上下文（request_id/start_time）
    # 在限流判定前就绪，429 响应经 RequestID 的 send_wrapper 仍会写入
    # interface.log 并附带 x-request-id。已有限流端点（ClawHub 兼容层、
    # skill-import、git-source sync、Playground）由策略表豁免。
    fastapi_app.add_middleware(RateLimitMiddleware)
    fastapi_app.add_middleware(RequestIDMiddleware)

    register_exception_handlers(fastapi_app, logger=logger)

    @fastapi_app.middleware("http")
    async def reject_oversized_upload(request: Request, call_next):
        # 此 middleware 运行在 RequestIDMiddleware 之前，需要手动设置 start_time
        # 以便 audit_failed_mutation 能正确计算 duration_ms
        if not start_time_var.get():
            set_request_context()
        if request.method == "POST" and request.url.path.rstrip("/").endswith("/plugins"):
            cl_str = request.headers.get("content-length")
            if cl_str is not None:
                try:
                    cl = int(cl_str)
                except ValueError:
                    cl = None
                if cl is not None and cl > MAX_FILE_SIZE:
                    size_mb = cl / (1024 * 1024)
                    limit_mb = MAX_FILE_SIZE // (1024 * 1024)
                    detail_msg = (
                        f"上传文件大小 {size_mb:.1f} MB 超过限制 {limit_mb} MB"
                    )
                    payload = http_error_payload(
                        status_code=413,
                        message=detail_msg,
                        error="file_too_large",
                    )
                    audit_failed_mutation(request, 413, as_json_response_body(payload))
                    return response_with_error_logging(
                        logger,
                        request=request,
                        status_code=413,
                        payload=payload,
                    )
        return await call_next(request)

    @fastapi_app.get("/api/health")
    async def health():
        return {"status": "ok"}

    router_register(fastapi_app)

    return fastapi_app


app = create_app()


def main() -> None:
    host = os.getenv("STORE_HOST", settings.host)
    port = int(os.getenv("STORE_PORT", settings.port))
    workers = int(os.getenv("STORE_WORKERS", "1").strip() or "1")

    reload = bool(settings.debug)
    if reload:
        workers = 1

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=["plugins_market", "common"] if reload else None,
        workers=workers,
        log_level="info",
    )


if __name__ == "__main__":
    main()
