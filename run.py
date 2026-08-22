#!/usr/bin/env python3
"""
Entry point.

    python run.py                          # everything
    python run.py --dry-run                # do everything, write nothing
    python run.py --date 2026-08-10        # pretend today is a different date
    python run.py --stages generation      # run one stage only
    python run.py --stages resolution,forecasting

The --date flag matters for backtesting: if you feed a paper from 10 August,
the agents must believe it is 10 August, or every deadline they calculate will
be wrong. Note that API quota still comes out of the REAL day's allowance --
Google's counters do not care what date we tell ourselves it is.

The --stages flag exists so that after replacing the repository you can do one
ordinary `--stages generation` run first. That exercises the four stage-one
fixes -- persistent quota, crash-safe logging, the removed early return, and
resolution -- with none of the forecasting code in the path. If that looks
clean, run everything.

WHY THE LOG IS SAVED IN A `finally`
-----------------------------------
The old version called log.save() only at the end of a successful run, so any
crash left NO LOG FILE AT ALL -- the traceback went to stdout and the markdown
was never written. That is exactly the run you would want to send to someone
for diagnosis. Now the log is flushed after every write and finalised no matter
how the run ends.
"""

import argparse
import datetime as dt
import sys
import traceback

from src import pipeline
from src.runlog import RunLog


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="run the whole pipeline but write nothing to disk")
    ap.add_argument("--date", default="",
                    help="treat this as today's date (YYYY-MM-DD), for backtesting")
    ap.add_argument("--stages", default="all",
                    help="'all', or a comma-separated subset of: "
                         + ", ".join(pipeline.STAGE_NAMES))
    args = ap.parse_args()

    today = None
    if args.date:
        try:
            today = dt.date.fromisoformat(args.date)
        except ValueError:
            print(f"Bad date: {args.date}. Use YYYY-MM-DD.")
            return 2

    try:
        pipeline.run(today=today, dry_run=args.dry_run, stages=args.stages)
    except SystemExit:
        raise
    except Exception as exc:                              # noqa: BLE001
        # The pipeline logs and isolates its own stage failures, so reaching
        # here means something broke in setup -- a missing config file, a bad
        # API key, a malformed CSV. Get it into the markdown regardless, since
        # that is the file that gets uploaded as an artifact.
        traceback.print_exc()
        try:
            log = RunLog(today or dt.date.today())
            log.heading("Run failed before or outside the staged pipeline")
            log.error("run.py", exc)
            log.finalise()
        except Exception:                                 # noqa: BLE001
            pass
        print(f"\nRun failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
