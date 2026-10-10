"""CLI entry: MySQL latest skills -> MinIO zips."""

from __future__ import annotations

import argparse
import logging

from recommender.shared.config import load_config
from recommender.shared.logging_utils import setup_logging

from .job import run_offline_sync

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline sync: MySQL latest skills -> MinIO zips")
    parser.add_argument("--force", action="store_true", help="Re-download even if local zip exists")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N skills")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    summary = run_offline_sync(load_config(), force=args.force, limit=args.limit)
    logger.info("package_sync done: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
