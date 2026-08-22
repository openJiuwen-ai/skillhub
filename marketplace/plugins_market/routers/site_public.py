# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from plugins_market.core.config import settings
from plugins_market.services.privacy_statement_file import read_privacy_statement_markdown

router = APIRouter(prefix="/site", tags=["site"])


@router.get("/config")
async def get_site_config():
    """前端运行时配置，用于功能开关，无需重新构建前端。"""
    return {
        "playground_enabled": settings.playground_enabled,
        "github_star_enabled": settings.github_star_enabled,
        "agentos_oauth_enabled": settings.agentos_oauth_enabled,
        "rec_list_top_k": settings.rec_list_top_k,
    }


@router.get("/privacy-statement")
async def get_privacy_statement():
    """直接返回 Markdown 正文（非 JSON）；浏览器新标签打开即可查看。"""
    text = await asyncio.to_thread(read_privacy_statement_markdown)
    return PlainTextResponse(
        content=text or "",
        media_type="text/markdown; charset=utf-8",
    )
