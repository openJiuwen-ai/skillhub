# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from cli_core.logging_config import get_logger
from cli_core.market import (
    plugin_delete,
    plugin_info,
    plugin_install_download,
    resolve_plugin_info_version,
    plugin_search,
    skill_import,
)
from cli_core.plugin import (
    SKILL_IMPORT_BUNDLE_MAX_BYTES,
    PublishError,
    plugin_init,
    plugin_install,
    plugin_pack,
    plugin_pack_skill_bundle,
    plugin_publish,
    plugin_validate,
)
from cli_core.schemas import PluginListQuery, PublishPluginInput
from cli_core.utils import sanitize_terminal_text, sha256_file_hex

logger = get_logger(__name__)


def _cli_delete_target_id(args: object) -> str:
    """Delete target id from ``skill_id``, ``asset_id``, or ``plugin_id``."""
    sid = getattr(args, "skill_id", None)
    if sid is not None and str(sid).strip():
        return str(sid).strip()
    aid = getattr(args, "asset_id", None)
    if aid is not None and str(aid).strip():
        return str(aid).strip()
    pid = getattr(args, "plugin_id", None)
    return str(pid).strip() if pid is not None else ""


def _cli_effective_market_url(args: object) -> str | None:
    """Resolve market base URL from CLI arg/env only (no hardcoded defaults)."""
    u = getattr(args, "market_url", None) or os.getenv("OPENJIUWEN_MARKET_URL")
    return str(u).strip() if u is not None else None


def _cli_resolve_market_url(args: object, *, err_msg: str) -> str | None:
    market_url = _cli_effective_market_url(args)
    if not market_url:
        logger.error("%s", err_msg)
        return None
    return market_url


def _cli_resolve_publish_auth(args) -> tuple[str | None, str | None, int]:
    user_token = args.user_token or os.getenv("OPENJIUWEN_USER_TOKEN")
    system_token = args.system_token or os.getenv("OPENJIUWEN_SYSTEM_TOKEN")
    has_user = bool(user_token and str(user_token).strip())
    has_sys = bool(system_token and str(system_token).strip())
    if has_user and has_sys:
        logger.error("publish supports exactly one auth method: use either --token or --system-token")
        return None, None, 1

    user_token = str(user_token).strip() if user_token else ""
    system_token = str(system_token).strip() if system_token else ""
    if system_token:
        return None, system_token, 0

    if not user_token:
        logger.error(
            "publish requires user token: pass --token or set OPENJIUWEN_USER_TOKEN "
            "(or use --system-token / OPENJIUWEN_SYSTEM_TOKEN)"
        )
        return None, None, 1
    return user_token, None, 0


def _cli_resolve_delete_auth(args) -> tuple[str | None, str | None, int]:
    user_token = args.user_token or os.getenv("OPENJIUWEN_USER_TOKEN")
    system_token = args.system_token or os.getenv("OPENJIUWEN_SYSTEM_TOKEN")

    has_user = bool(user_token and str(user_token).strip())
    has_sys = bool(system_token and str(system_token).strip())
    if has_user and has_sys:
        logger.error("delete supports exactly one auth method: use either --token or --system-token")
        return None, None, 1

    if has_sys:
        return None, str(system_token).strip(), 0

    if not has_user:
        logger.error(
            "delete requires user token: pass --token or set OPENJIUWEN_USER_TOKEN "
            "(or use --system-token / OPENJIUWEN_SYSTEM_TOKEN)"
        )
        return None, None, 1
    return str(user_token).strip(), None, 0


def handle_init(args) -> int:
    try:
        plugin_root = plugin_init(
            args.name,
            Path(args.path),
            force=args.force,
            plugin_type=args.plugin_type,
        )
    except Exception as exc:
        logger.error("failed: %s", exc)
        return 1
    logger.info("Plugin initialized at: %s", plugin_root)
    return 0


def handle_validate(args) -> int:
    result = plugin_validate(Path(args.path).resolve())
    if result.warnings:
        for warning in result.warnings:
            logger.warning("%s", warning)
    if result.errors:
        for err in result.errors:
            logger.error("failed: %s", err)
        return 1
    logger.info("Plugin validation passed.")
    return 0


def handle_pack(args) -> int:
    try:
        plugin_root = Path(args.path).resolve()
        out_dir = (
            Path(args.output).resolve() if Path(args.output).is_absolute() else (plugin_root / args.output).resolve()
        )
        zip_path = plugin_pack(plugin_root, out_dir)
    except Exception as exc:
        logger.error("failed: %s", exc)
        return 1
    logger.info("Packed: %s", zip_path)
    return 0


def handle_publish(args) -> int:
    user_token, system_token, exit_code = _cli_resolve_publish_auth(args)
    if exit_code != 0:
        return exit_code

    market_url = _cli_resolve_market_url(
        args,
        err_msg="publish requires --market-url or OPENJIUWEN_MARKET_URL",
    )
    if not market_url:
        return 1
    if not args.file and not args.path:
        logger.error("path (plugin directory) is required when not using --file")
        return 1
    if not getattr(args, "plugin_version", None):
        logger.error("requires --plugin-version")
        return 1

    try:
        publish_input = PublishPluginInput(
            plugin_path=Path(args.path).resolve() if args.path else None,
            zip_path=Path(args.file).resolve() if args.file else None,
            plugin_id=args.plugin_id or None,
            plugin_version=str(args.plugin_version).strip() or None,
            version_desc=args.version_desc or None,
            force=args.force,
        )
        result = plugin_publish(
            market_url=market_url,
            user_token=user_token,
            system_token=system_token,
            publish_input=publish_input,
        )
    except PublishError as e:
        logger.error("failed: %s", e.detail)
        return 1
    except ValueError as e:
        logger.error("failed: %s", e)
        return 1

    logger.info(
        "Published successfully.\n"
        "  plugin_id: %s\n"
        "  name: %s\n"
        "  version: %s",
        result.plugin_id,
        result.name,
        result.version,
    )
    logger.info(
        "TIP:\n"
        "  - Save the plugin_id above.\n"
        "  - Pass --plugin-id for later publishes.\n"
        "  - If lost, run `agentichub search <keyword>` to look it up.",
    )
    return 0


def handle_info(args) -> int:
    market_url = _cli_effective_market_url(args)
    if not market_url:
        logger.error("requires --market-url or OPENJIUWEN_MARKET_URL")
        return 1
    try:
        version = resolve_plugin_info_version(
            market_url,
            args.asset_id,
            getattr(args, "version", None),
        )
        detail = plugin_info(market_url, args.asset_id, version)
    except Exception as exc:
        logger.error("failed: %s", exc)
        return 1

    logger.info("Details:")
    logger.info("  asset_id: %s", detail.asset_id or args.asset_id)
    for key in detail.__class__.model_fields:
        if key == "asset_id":
            continue
        val = getattr(detail, key)
        if val in (None, ""):
            continue
        if key == "tags":
            if isinstance(val, list) and val:
                safe_tags = ", ".join(sanitize_terminal_text(x) for x in val)
                logger.info("  tags: %s", safe_tags)
            continue
        logger.info("  %s: %s", sanitize_terminal_text(key), sanitize_terminal_text(val))

    return 0


def handle_search(args) -> int:
    market_url = _cli_effective_market_url(args)
    if not market_url:
        logger.error("requires --market-url or OPENJIUWEN_MARKET_URL")
        return 1
    try:
        if args.page is not None and args.page < 1:
            logger.error("--page must be >= 1")
            return 1
        if args.page_size is not None and (args.page_size < 1 or args.page_size > 200):
            logger.error("--page-size must be between 1 and 200")
            return 1
        query = PluginListQuery(
            search_keyword=args.query or "",
            plugin_type=args.plugin_type,
            publisher_name=args.author,
            asset_id=args.search_asset_id,
            asset_type=args.search_asset_type,
            publisher_id=args.search_publisher_id,
            page=args.page or 1,
            page_size=args.page_size or 20,
            order_by=args.order_by or "install_count",
            desc=args.desc,
        )
        result = plugin_search(market_url, query)
        if not result.items:
            logger.info("No results.")
            return 0
        logger.info("Search results:")
        logger.info("  page: %s", result.page)
        logger.info("  page_size: %s", result.page_size)
        logger.info("  total: %s", result.total)
        for item in result.items:
            aid = item.asset_id
            name = item.name
            ver = item.display_version_for_market_search()
            logger.info("  - asset_id=%s name=%s version=%s", aid, name, ver)
    except Exception as exc:
        logger.error("failed: %s", exc)
        return 1
    return 0


def handle_delete(args) -> int:
    market_url = _cli_resolve_market_url(
        args,
        err_msg="delete requires --market-url or OPENJIUWEN_MARKET_URL",
    )
    if not market_url:
        return 1
    try:
        user_token, system_token, exit_code = _cli_resolve_delete_auth(args)
        if exit_code != 0:
            return exit_code
        target_id = _cli_delete_target_id(args)
        if system_token:
            plugin_delete(
                market_url,
                target_id,
                logger,
                requested_version=args.version,
                system_token=system_token,
            )
        else:
            plugin_delete(
                market_url,
                target_id,
                logger,
                requested_version=args.version,
                user_token=user_token,
            )
    except Exception as exc:
        logger.error("failed: %s", exc)
        return 1
    return 0


def handle_install(args) -> int:
    market_url = _cli_resolve_market_url(
        args,
        err_msg="install requires --market-url or OPENJIUWEN_MARKET_URL",
    )
    if not market_url:
        return 1

    asset_id = str(args.asset_id).strip()
    plugin_version = (args.plugin_version or "").strip() or None

    fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="openjiuwen_dl_")
    os.close(fd)
    own_tmp = Path(tmp_name)
    zip_path = own_tmp

    extract_root = Path(args.output).resolve() if args.output else Path.cwd().resolve()
    try:
        dl_info = plugin_install_download(
            market_url,
            asset_id,
            zip_path,
            requested_version=plugin_version,
        )
        if dl_info.verified:
            logger.info("Download checksum verified: %s", dl_info.actual_checksum_sha256)
        else:
            logger.warning("TIP: Server did not provide a checksum; skipped verification.")
        installed = plugin_install(
            zip_path,
            extract_dir=extract_root,
            force=args.force,
        )
        logger.info("Install finished.\n  path: %s", installed.resolve())
    except Exception as exc:
        logger.error("failed: %s", exc)
        return 1
    finally:
        try:
            own_tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


def handle_skill_import(args) -> int:
    market_url = _cli_resolve_market_url(
        args,
        err_msg="skill-import requires --market-url or OPENJIUWEN_MARKET_URL",
    )
    if not market_url:
        return 1
    system_token = args.system_token or os.getenv("OPENJIUWEN_SYSTEM_TOKEN")
    if not system_token or not str(system_token).strip():
        logger.error("requires --system-token or OPENJIUWEN_SYSTEM_TOKEN")
        return 1

    path = Path(args.bundle_path).resolve()
    pack_tmp: Path | None = None
    try:
        if path.is_dir():
            pack_tmp = Path(tempfile.mkdtemp(prefix="oj_skill_bundle_pack_"))
            bundle = pack_tmp / "bundle.zip"
            try:
                plugin_pack_skill_bundle(path, bundle)
            except ValueError as e:
                logger.error("failed: %s", f"pack directory: {e}")
                return 1
        elif path.is_file():
            bundle = path
            raw = bundle.stat().st_size
            if raw > SKILL_IMPORT_BUNDLE_MAX_BYTES:
                logger.error(
                    "failed: %s",
                    f"bundle file too large: {raw} bytes "
                    f"(limit {SKILL_IMPORT_BUNDLE_MAX_BYTES})",
                )
                return 1
        else:
            logger.error("failed: %s", f"bundle zip or directory not found: {path}")
            return 1

        checksum = sha256_file_hex(bundle)
        try:
            result = skill_import(
                market_url,
                str(system_token).strip(),
                zip_path=bundle,
                checksum_sha256=checksum,
                force=bool(args.force),
                fail_fast=bool(args.fail_fast),
            )
        except PublishError as e:
            logger.error("failed: %s", e.detail)
            return 1

        s = result.summary
        logger.info("Import summary:")
        logger.info("  total: %s", s.total)
        logger.info("  ok: %s", s.ok)
        logger.info("  failed: %s", s.failed)
        id_label = "plugin_id"
        for item in result.results:
            if item.status == "ok":
                logger.info(
                    "  OK: entry=%s %s=%s name=%s version=%s",
                    item.entry,
                    id_label,
                    item.plugin_id,
                    item.name,
                    item.version,
                )
            else:
                logger.error("  FAIL: entry=%s error=%s message=%s", item.entry, item.error, item.message)

        return 1 if s.failed else 0
    finally:
        if pack_tmp is not None:
            shutil.rmtree(pack_tmp, ignore_errors=True)


COMMAND_HANDLERS = {
    "init": handle_init,
    "validate": handle_validate,
    "pack": handle_pack,
    "publish": handle_publish,
    "info": handle_info,
    "search": handle_search,
    "delete": handle_delete,
    "install": handle_install,
    "skill-import": handle_skill_import,
}
