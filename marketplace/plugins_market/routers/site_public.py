# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from plugins_market.core.config import settings
from plugins_market.services.privacy_statement_file import (
    read_privacy_statement_markdown,
    read_publish_guide_markdown,
)

router = APIRouter(prefix="/site", tags=["site"])


def plain_markdown_response(text: str) -> PlainTextResponse:
    """有正文返回 200。缺失或空白返回 404，避免前端渲染成空白文章。"""
    body = text or ""
    if not body.strip():
        return PlainTextResponse(
            content="",
            status_code=404,
            media_type="text/markdown; charset=utf-8",
        )
    return PlainTextResponse(
        content=body,
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/config")
async def get_site_config():
    """前端运行时配置，用于功能开关，无需重新构建前端。"""
    return {
        "playground_enabled": settings.playground_enabled,
        "github_star_enabled": settings.github_star_enabled,
        "gitcode_oauth_enabled": settings.gitcode_oauth_enabled,
        "github_oauth_enabled": settings.github_oauth_enabled,
        "agentos_oauth_enabled": settings.agentos_oauth_enabled,
        "rec_list_top_k": settings.rec_list_top_k,
        "hot_list_top_k": settings.hot_list_top_k,
    }


@router.get("/privacy-statement")
async def get_privacy_statement():
    """直接返回 Markdown 正文（非 JSON）；浏览器新标签打开即可查看。"""
    text = await asyncio.to_thread(read_privacy_statement_markdown)
    return plain_markdown_response(text)


@router.get("/publish-guide")
async def get_publish_guide(lang: str = Query("zh")):
    """上传资产指南，Markdown 正文（非 JSON），无需鉴权。`lang=en` 读英文文件。"""
    text = await asyncio.to_thread(read_publish_guide_markdown, lang)
    return plain_markdown_response(text)
