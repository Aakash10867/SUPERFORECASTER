#!/usr/bin/env python3
"""
Entry point.

    python run.py                 # normal run
    python run.py --dry-run       # do everything, write nothing
    python run.py --date 2026-08-10   # pretend today is a different date

The --date flag matters for backtesting: if you feed a paper from 10 August,
the agents must believe it is 10 August, or every deadline they calculate will
be wrong.
"""

import argparse
import datetime as dt
import sys

from src import pipeline


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="run the whole pipeline but write nothing to disk")
    ap.add_argument("--date", default="",
                    help="treat this as today's date (YYYY-MM-DD), for backtesting")
    args = ap.parse_args()

    today = None
    if args.date:
        try:
            today = dt.date.fromisoformat(args.date)
        except ValueError:
            print(f"Bad date: {args.date}. Use YYYY-MM-DD.")
            return 2

    try:
        pipeline.run(today=today, dry_run=args.dry_run)
    except SystemExit:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\nRun failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
