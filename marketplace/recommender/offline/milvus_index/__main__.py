"""CLI for Milvus index tasks."""

from __future__ import annotations

import argparse
import logging

from recommender.shared.config import load_config
from recommender.shared.logging_utils import setup_logging

from .pipeline import run_full_rebuild, run_incremental_index

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Milvus skill index")
    parser.add_argument(
        "--mode",
        choices=["incremental", "full"],
        default="incremental",
    )
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    cfg = load_config()

    if args.mode == "full":
        stats = run_full_rebuild(app_cfg=cfg, batch_size=args.batch_size)
    else:
        stats = run_incremental_index(app_cfg=cfg, batch_size=args.batch_size)
    logger.info("milvus_index done: %s", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
