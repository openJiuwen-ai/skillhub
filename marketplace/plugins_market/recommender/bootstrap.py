"""Bootstrap recommender env from marketplace Settings."""

from __future__ import annotations

import os

from plugins_market.core.config import settings
from plugins_market.core.redis_client import resolve_redis_ssl


def apply_recommender_settings_to_env() -> None:
    """
    Ensure recommender.shared.config.load_config() sees marketplace Settings.

    Only fills missing env keys so explicit process env still wins.
    """
    pairs = {
        "MILVUS_HOST": settings.milvus_host,
        "MILVUS_PORT": str(settings.milvus_port),
        "MILVUS_COLLECTION": settings.milvus_collection,
        "MILVUS_USER": settings.milvus_user,
        "REDIS_HOST": settings.redis_host or os.getenv("REDIS_HOST", "127.0.0.1"),
        "REDIS_PORT": str(settings.redis_port),
        "REDIS_DB": str(settings.redis_db),
        "REDIS_TOPK_INSTALL_KEY": settings.redis_topk_install_key,
        "REDIS_USER_SEQ_KEY_PREFIX": settings.redis_user_seq_key_prefix,
        "CACHE_BACKEND": settings.cache_backend or "redis",
    }
    if settings.milvus_password and not (
        os.getenv("MILVUS_PASSWORD") or os.getenv("MARKET_MILVUS_PASSWORD")
    ):
        pairs["MILVUS_PASSWORD"] = settings.milvus_password
    if settings.redis_password and not os.getenv("MARKET_REDIS_PASSWORD"):
        pairs["MARKET_REDIS_PASSWORD"] = settings.redis_password
    # Propagate resolved SSL so offline jobs / online seeds match marketplace clients.
    if not os.getenv("REDIS_SSL") and not os.getenv("MARKET_REDIS_SSL"):
        pairs["REDIS_SSL"] = "true" if resolve_redis_ssl() else "false"
    for key, value in pairs.items():
        if value is None or value == "":
            continue
        if not os.getenv(key):
            os.environ[key] = str(value)
