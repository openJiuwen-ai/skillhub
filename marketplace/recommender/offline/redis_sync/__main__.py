"""CLI for Redis sync writers.

Examples:
  python -m redis_sync
  python -m redis_sync --task topk_install
  python -m redis_sync --task user_sequences
  python -m redis_sync --task topk_install --top-k 100
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import replace

from recommender.shared.config import TopKInstallSettings, UserSeqSettings, load_config, load_redis_config
from recommender.shared.logging_utils import setup_logging

from .job import run_redis_sync, run_redis_task
from .tasks import REDIS_TASKS

logger = logging.getLogger(__name__)


def _should_override_topk(args: argparse.Namespace) -> bool:
    if args.top_k is not None or args.redis_key is not None:
        return True
    return args.ttl_seconds is not None and args.task in (None, "topk_install")


def _should_override_user_seq(args: argparse.Namespace) -> bool:
    if args.key_prefix is not None or args.max_len is not None:
        return True
    return args.ttl_seconds is not None and args.task == "user_sequences"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync recommendation data into Redis")
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help=f"Run one writer ({', '.join(sorted(REDIS_TASKS))}); default: all",
    )
    parser.add_argument("--list", action="store_true", help="List Redis writers")
    parser.add_argument("--top-k", type=int, default=None, help="Override topk_install.k")
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=None,
        help="Override TTL for the selected writer (topk_install / user_sequences)",
    )
    parser.add_argument("--redis-key", type=str, default=None, help="Override topk_install.key")
    parser.add_argument(
        "--key-prefix",
        type=str,
        default=None,
        help="Override user_sequences.key_prefix (e.g. skill_rec:user)",
    )
    parser.add_argument(
        "--max-len",
        type=int,
        default=None,
        help="Override user_sequences.max_len (per-user sequence length)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    if args.list:
        for name, task in REDIS_TASKS.items():
            logger.info("  - %s: %s", name, task.description)
        return 0

    app_cfg = load_config()
    redis_cfg = load_redis_config()

    if _should_override_topk(args):
        topk = redis_cfg.topk_install
        redis_cfg = replace(
            redis_cfg,
            topk_install=TopKInstallSettings(
                key=args.redis_key or topk.key,
                k=args.top_k if args.top_k is not None else topk.k,
                ttl_seconds=(
                    args.ttl_seconds if args.ttl_seconds is not None else topk.ttl_seconds
                ),
                interval_minutes=topk.interval_minutes,
            ),
        )

    if _should_override_user_seq(args):
        seq = redis_cfg.user_seq
        redis_cfg = replace(
            redis_cfg,
            user_seq=UserSeqSettings(
                key_prefix=args.key_prefix or seq.key_prefix,
                max_len=args.max_len if args.max_len is not None else seq.max_len,
                ttl_seconds=(
                    args.ttl_seconds if args.ttl_seconds is not None else seq.ttl_seconds
                ),
            ),
        )

    if args.task:
        result = run_redis_task(args.task, app_cfg, redis_cfg)
    else:
        result = run_redis_sync(app_cfg, redis_cfg)
    logger.info("redis_sync done: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
