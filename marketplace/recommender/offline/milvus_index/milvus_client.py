"""Milvus connection / collection helpers."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from recommender.shared.config import _env, load_config

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("asset_id", "category_id", "plugin_type", "embedding")


class AliasCutoverStuckError(RuntimeError):
    """Public name is gone: first-cutover rename succeeded, create_alias and rollback both failed.

    The new physical collection must be kept so ops can ``create_alias`` or rename parked back.
    """


@dataclass(frozen=True)
class CollectionConfig:
    host: str
    port: int
    collection: str
    dim: int
    recreate: bool
    user: str = ""
    password: str = ""


def _resolve_milvus_password(plaintext_fallback: str = "") -> str:
    """Prefer SecurityUtils decrypt for MILVUS_PASSWORD / MARKET_MILVUS_PASSWORD."""
    try:
        from common.security.security_utils import SecurityUtils

        for key in ("MILVUS_PASSWORD", "MARKET_MILVUS_PASSWORD"):
            value = SecurityUtils.get_decrypt_secret(key, default="") or ""
            if value.strip():
                return value.strip()
    except Exception as exc:
        logger.warning(
            "decrypt MILVUS_PASSWORD failed (%s); fallback to env plaintext",
            exc,
        )
    return (plaintext_fallback or _env("MILVUS_PASSWORD", "MARKET_MILVUS_PASSWORD", default="")).strip()


def load_collection_config(dim: int, *, recreate: bool = False) -> CollectionConfig:
    settings = load_config().milvus
    return CollectionConfig(
        host=settings.host,
        port=settings.port,
        collection=settings.collection,
        dim=dim,
        recreate=recreate,
        user=(settings.user or "").strip(),
        password=_resolve_milvus_password(settings.password),
    )


def connect_milvus(cfg: CollectionConfig, *, timeout: float = 30.0) -> None:
    from pymilvus import connections

    kwargs: dict[str, Any] = {
        "alias": "default",
        "host": cfg.host,
        "port": cfg.port,
        "timeout": timeout,
    }
    user = (cfg.user or "").strip()
    password = (cfg.password or "").strip()
    if user:
        kwargs["user"] = user
        kwargs["password"] = password
    connections.connect(**kwargs)


def _collection_has_required_fields(collection: Any) -> bool:
    try:
        names = {f.name for f in collection.schema.fields}
    except Exception:
        return False
    return all(name in names for name in REQUIRED_FIELDS)


def _build_schema(dim: int):
    from pymilvus import CollectionSchema, DataType, FieldSchema

    return CollectionSchema(
        fields=[
            FieldSchema(
                name="asset_id",
                dtype=DataType.VARCHAR,
                is_primary=True,
                max_length=64,
            ),
            FieldSchema(
                name="category_id",
                dtype=DataType.VARCHAR,
                max_length=64,
            ),
            FieldSchema(
                name="plugin_type",
                dtype=DataType.VARCHAR,
                max_length=32,
            ),
            FieldSchema(
                name="embedding",
                dtype=DataType.FLOAT_VECTOR,
                dim=dim,
            ),
        ],
        description="Swarm skill embeddings",
    )


def create_collection_with_schema(cfg: CollectionConfig, name: str):
    """Create an empty physical collection. Never drops an existing name."""
    from pymilvus import Collection

    collection = Collection(name=name, schema=_build_schema(cfg.dim))
    for field_name in ("category_id", "plugin_type"):
        try:
            collection.create_index(
                field_name=field_name,
                index_params={"index_type": "INVERTED"},
            )
        except Exception:
            logger.warning("failed to create %s scalar index", field_name, exc_info=True)
    return collection


def ensure_collection(cfg: CollectionConfig):
    """Open public name (alias or collection), or create a physical collection with that name.

    Full rebuild no longer uses drop-here; see ``promote_collection_alias``.
    """
    from pymilvus import Collection, utility

    if cfg.recreate:
        logger.warning(
            "ensure_collection: recreate=True is ignored; full rebuild uses alias swap"
        )

    if utility.has_collection(cfg.collection):
        collection = Collection(cfg.collection)
        if _collection_has_required_fields(collection):
            return collection
        raise RuntimeError(
            f"Milvus collection {cfg.collection!r} is missing required fields "
            f"{REQUIRED_FIELDS}; run milvus full rebuild with recreate "
            "(MARKET_REC_REBUILD_ON_STARTUP or --mode full)."
        )

    return create_collection_with_schema(cfg, cfg.collection)


def new_physical_collection_name(public_name: str) -> str:
    """Unique physical name for a blue/green full rebuild."""
    base = (public_name or "skill_index").strip() or "skill_index"
    return f"{base}__{int(time.time())}_{os.getpid()}"


def resolve_physical_name(public_name: str) -> str | None:
    """Physical collection currently backing ``public_name`` (self or via alias)."""
    from pymilvus import utility

    name = (public_name or "").strip()
    if not name:
        return None
    physicals = list(utility.list_collections() or [])
    if name in physicals:
        return name
    for col in physicals:
        try:
            aliases = list(utility.list_aliases(col) or [])
        except Exception as exc:
            logger.warning("list_aliases failed for collection %s: %s", col, exc)
            continue
        if name in aliases:
            return col
    return None


def promote_collection_alias(public_name: str, new_physical: str) -> str | None:
    """Point ``public_name`` at ``new_physical`` without serving an empty collection.

    Returns the previous physical collection name (caller may drop it), or None.
    """
    from pymilvus import utility

    alias = (public_name or "").strip()
    if not alias:
        raise ValueError("public_name is required")
    if not (new_physical or "").strip():
        raise ValueError("new_physical is required")
    if not utility.has_collection(new_physical):
        raise RuntimeError(f"new physical collection missing: {new_physical!r}")

    previous = resolve_physical_name(alias)

    if previous == alias:
        # Existing deployments: live name is a physical collection, not an alias yet.
        parked = f"{alias}__old_{int(time.time())}_{os.getpid()}"
        logger.info(
            "full rebuild: rename live %s -> %s, then alias %s -> %s",
            alias,
            parked,
            alias,
            new_physical,
        )
        utility.rename_collection(alias, parked)
        previous = parked
        try:
            utility.create_alias(collection_name=new_physical, alias=alias)
        except Exception:
            logger.exception(
                "full rebuild: create_alias %s -> %s failed after rename; rolling back %s -> %s",
                alias,
                new_physical,
                parked,
                alias,
            )
            try:
                utility.rename_collection(parked, alias)
            except Exception as rollback_exc:
                logger.exception(
                    "full rebuild: rollback rename failed; public name %s is missing. "
                    "parked=%s new_physical=%s; restore with rename %s -> %s "
                    "or create_alias %s -> %s",
                    alias,
                    parked,
                    new_physical,
                    parked,
                    alias,
                    new_physical,
                    alias,
                )
                raise AliasCutoverStuckError(
                    f"public name {alias!r} missing after failed alias create and rollback; "
                    f"parked={parked!r} new_physical={new_physical!r}"
                ) from rollback_exc
            raise
        return previous

    if previous:
        logger.info(
            "full rebuild: alter alias %s (%s -> %s)",
            alias,
            previous,
            new_physical,
        )
        utility.alter_alias(collection_name=new_physical, alias=alias)
        return previous if previous != new_physical else None

    logger.info("full rebuild: create alias %s -> %s", alias, new_physical)
    utility.create_alias(collection_name=new_physical, alias=alias)
    return None


def drop_collection_if_exists(name: str) -> bool:
    from pymilvus import utility

    target = (name or "").strip()
    if not target or not utility.has_collection(target):
        return False
    utility.drop_collection(target)
    logger.info("dropped milvus collection %s", target)
    return True


def delete_by_asset_ids(collection: Any, asset_ids: list[str]) -> int:
    if not asset_ids:
        return 0
    quoted = ", ".join(f'"{asset_id}"' for asset_id in asset_ids)
    collection.delete(f"asset_id in [{quoted}]")
    return len(asset_ids)


def _has_embedding_vector_index(collection: Any) -> bool:
    """True only when the FLOAT_VECTOR field already has an index (not scalar indexes)."""
    try:
        indexes = list(collection.indexes or [])
    except Exception:
        return False
    for idx in indexes:
        field = getattr(idx, "field_name", None)
        if field is None:
            # Older / alternate pymilvus shapes.
            field = getattr(idx, "field", None)
        if str(field or "") == "embedding":
            return True
    return False


def create_vector_index_if_needed(collection: Any) -> None:
    # ensure_collection may already create a category_id scalar index. Do NOT treat
    # "any index exists" as enough for load(): Milvus requires a vector index on
    # embedding before Collection.load().
    try:
        if _has_embedding_vector_index(collection):
            collection.load()
            return
    except Exception:
        logger.warning(
            "failed to inspect existing milvus indexes; will create embedding index",
            exc_info=True,
        )

    collection.create_index(
        field_name="embedding",
        index_params={
            "index_type": "HNSW",
            "metric_type": "IP",
            "params": {"M": 8, "efConstruction": 64},
        },
    )
    collection.load()
