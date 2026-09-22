# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from fastapi import FastAPI

from plugins_market.core.config import settings
from plugins_market.clawhub_compat import router as clawhub_router
from plugins_market.routers import audit as audit_router_module
from plugins_market.routers import github_watch
from plugins_market.routers import notifications as notifications_router
from plugins_market.routers import oauth_provider
from plugins_market.routers import plugin as plugin_routers
from plugins_market.routers.groups import router as groups_router
from plugins_market.routers.interaction import interaction_router
from plugins_market.routers.site_public import router as site_public_router


def router_register(app: FastAPI) -> None:
    """注册所有路由。"""

    app.include_router(plugin_routers.router, prefix="/api/v1")
    app.include_router(interaction_router, prefix="/api/v1")
    app.include_router(groups_router, prefix="/api/v1")
    app.include_router(notifications_router.router, prefix="/api/v1")
    app.include_router(site_public_router, prefix="/api/v1")
    app.include_router(audit_router_module.router, prefix="/api/v1")
    app.include_router(oauth_provider.router, prefix="/api/v1/auth", tags=["auth"])
    if settings.recommender_enabled:
        from plugins_market.routers.recommender import router as recommender_router
    else:
        # 关闭时仍注册路径并按 install_count 出卡片；否则 Swarm 预热 POST /recommend 会 404。
        from plugins_market.routers.recommender_disabled import router as recommender_router

    app.include_router(recommender_router, prefix="/api/v1")
    app.include_router(github_watch.router, prefix="/api/v1")
    if settings.clawhub_compat_enabled:
        app.include_router(clawhub_router, prefix="/api/v1")

    if settings.playground_enabled:
        # skill-runner 是独立服务，marketplace 通过 HTTP 反向代理转发
        # /api/v1/playground/*，不在本进程内 import agent-core 执行栈。
        from plugins_market.core.playground_state import get_playground_state_store
        from plugins_market.routers.playground_proxy import router as playground_proxy_router
        from plugins_market.core.logging import get_logger
        # 启动即初始化状态存储：多实例开关开着但没配 Redis 属配置错误，
        # 在这里快速失败，而不是等第一个请求 500
        get_playground_state_store()
        app.include_router(playground_proxy_router, prefix="/api/v1")
        get_logger("marketplace").info(
            "Playground enabled, proxying /api/v1/playground/* -> %s", settings.skill_runner_url
        )

