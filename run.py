#!/usr/bin/env python3
"""
Entry point.

    python run.py                          # everything
    python run.py --dry-run                # do everything, write nothing
    python run.py --date 2026-08-10        # pretend today is a different date
    python run.py --stages generation      # run one stage only
    python run.py --stages resolution,forecasting
    python run.py --web-probe Q0003,Q0013   # test web checks, write nothing

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
    ap.add_argument("--web-probe", default="",
                    help="run the web check for one or more question ids "
                         "(comma-separated), print every query, article and "
                         "decision, and write nothing")
    args = ap.parse_args()

    if args.web_probe:
        return _web_probe(args.web_probe)

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


class _Console:
    """Minimal log for the probe: prints, never writes a log file."""
    def info(self, t): print(t)
    def warn(self, t): print(f"WARNING: {t}")
    def flag(self, t): print(f"*** {t}")
    def sub(self, t): print(f"-- {t}")
    def heading(self, t): print(f"== {t}")


def _web_probe(ids: str) -> int:
    """
    v18. Proves the web check works BEFORE it is trusted: real search, real
    model, everything printed, nothing written. Run it from GitHub (Actions ->
    Run workflow -> web_probe), where the network is real.

    v21: several ids at once, comma-separated ("Q0003,Q0013,Q0016"), with a
    one-line summary at the end -- an acceptance test is a SET of questions,
    some of which must NOT resolve.
    """
    from src import config, models, web_resolve
    settings = config.load_settings()
    router = models.ModelRouter(settings, config.load_models(), _Console())
    qids = [x.strip().upper() for x in ids.replace(" ", ",").split(",") if x.strip()]

    # v22 PREFLIGHT: say plainly whether full-text reading can work at all,
    # before any question is judged.
    deep = web_resolve.make_deep(settings)
    print("PREFLIGHT")
    if deep is None:
        print("  full-text check: DISABLED in settings (deep_check.enabled)")
    elif not deep.key:
        print("  full-text check: NO KEY -- add the TAVILY_API_KEY secret. "
              "Without it nothing can resolve.")
    else:
        u = deep.usage()
        print(f"  full-text check: key present; credits left: "
              f"{u['left'] if u and u.get('left') is not None else 'unknown'}"
              f"{' of ' + str(u['limit']) if u and u.get('limit') else ''}")
    budget = {"deep": True, "sweeps": True, "spent": 0, "cap": 99}

    results = []
    for qid in qids:
        print("\n" + "=" * 78)
        results.append((qid, web_resolve.probe(router, settings, qid, dt.date.today(),
                                               deep=deep, budget=budget)))
    print("\n" + "=" * 78 + "\nSUMMARY (nothing was written)")
    for qid, action in results:
        print(f"  {qid}: {action}")
    return 0 if all(a != "failed" for _, a in results) else 1


if __name__ == "__main__":
    sys.exit(main())
