"""Seed command.

Usage:
    python -m app.seed                    # use SEED_MODE from the environment
    python -m app.seed --mode reference   # override the mode
    python -m app.seed --reset            # wipe all application tables first
"""

import argparse
import logging

from app.core.config import get_settings
from app.seed.runner import SeedMode, run_seed


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.seed", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--mode",
        type=SeedMode,
        choices=list(SeedMode),
        default=None,
        help="what to seed (default: SEED_MODE setting)",
    )
    parser.add_argument(
        "--reset", action="store_true", help="truncate all application tables before seeding"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s [%(name)s] %(message)s")
    run_seed(args.mode or SeedMode(get_settings().seed_mode), reset=args.reset)


if __name__ == "__main__":
    main()
