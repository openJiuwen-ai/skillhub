# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""从环境变量指定的本地路径读取站点 Markdown（隐私声明、上传指南）。"""

from pathlib import Path

from plugins_market.core.config import settings
from plugins_market.core.logging import get_logger

logger = get_logger(__name__)

# 防止误配超大文件拖垮进程
_MAX_BYTES = 512 * 1024


def read_configured_markdown(raw_path: str, log_name: str) -> str:
    raw = (raw_path or "").strip()
    if not raw:
        return ""
    if "\x00" in raw:
        logger.warning("%s: path contains NUL, ignored", log_name)
        return ""
    try:
        path = Path(raw).expanduser()
        path = path.resolve()
        if not path.is_file():
            logger.warning("%s: not a file or missing: %s", log_name, path)
            return ""
        data = path.read_bytes()
        if len(data) > _MAX_BYTES:
            logger.warning(
                "%s: file too large (%s bytes), max %s",
                log_name,
                len(data),
                _MAX_BYTES,
            )
            return ""
        return data.decode("utf-8", errors="replace")
    except OSError as e:
        logger.warning("%s: read failed: %s", log_name, e)
        return ""


def read_privacy_statement_markdown() -> str:
    return read_configured_markdown(
        settings.privacy_statement_file,
        "privacy_statement_file",
    )


def markdown_path_for_lang(raw_path: str, lang: str) -> str:
    """英文取同目录 `name.en.md`。其他语言沿用配置路径，不把 lang 拼进路径。"""
    raw = (raw_path or "").strip()
    if not raw or not (lang or "").lower().startswith("en"):
        return raw
    path = Path(raw)
    if path.suffix.lower() == ".md":
        return str(path.with_name(f"{path.stem}.en{path.suffix}"))
    return f"{raw}.en.md"


def read_publish_guide_markdown(lang: str = "zh") -> str:
    return read_configured_markdown(
        markdown_path_for_lang(settings.publish_guide_file, lang),
        "publish_guide_file",
    )
