"""Backfill ESPN play-by-play (summary) for completed games, for RAPM.

Usage (from the repo root):
    python -m scripts.backfill_pbp --start 2026-10-01 --end 2026-11-02
    python -m scripts.backfill_pbp --start 2026-10-01 --end 2026-11-02 --fresh

Skips games that already have PBP rows (idempotent / resumable).
"""
from __future__ import annotations

import argparse

from app.ingest.service import backfill_pbp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="ISO date, inclusive")
    parser.add_argument("--end", required=True, help="ISO date, inclusive")
    parser.add_argument("--fresh", action="store_true", help="re-fetch even if PBP exists")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    args = parser.parse_args()

    stats = backfill_pbp(args.start, args.end, fresh=args.fresh, progress=not args.quiet)
    print(f"done: fetched={stats['fetched']} cached={stats['skipped']} errors={stats['errors']}")


if __name__ == "__main__":
    main()
