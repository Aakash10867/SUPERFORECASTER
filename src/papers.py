"""
Which paper is this, and which papers are missing? (v17)

WHY NAMES COME FROM THE FILENAME
--------------------------------
Until v17 the paper's name was guessed from its PAGE CONTENT, and the guess was
badly wrong: papers quote each other, carry each other's wire copy and run
each other's ads. Business Standard files were labelled "The Wall Street
Journal", Boston Globe files "The Washington Post", ET files "The Times of
India", and 45 files "Unknown". That fed Gate A (which papers will report the
resolution?) and the [paper] label on every article the screen and lenses read.

The filename is chosen by a human who knows what it is -- the same reason the
filename already wins on the issue DATE (see extract.identify_paper). So the
name now comes from config/papers.csv: a list of filename patterns, each
mapped to ONE paper name, with the city ignored. "MUMBAI_Mint", "Mint ● Delhi"
and "BENGALURU_Mint" are all Mint.

A file that matches nothing is called "Unknown" and FLAGGED -- never guessed.
Add a line to config/papers.csv and the next run re-labels the whole history.

WHY "REGULAR" AND NOT "EVERY PAPER EVER SEEN"
---------------------------------------------
A roster of every paper ever uploaded would report New Scientist or the
Chicago Tribune as missing every day forever. So a paper is REGULAR if it
arrived on at least half of the days you uploaded anything in the last 30
days; otherwise it is occasional and its absence is not news. Both numbers are
in settings.yaml (coverage).

WHAT GETS REPORTED
------------------
data/coverage.csv is rebuilt every run: one row per date in the window, with
the papers that arrived and the regular papers that did not. The run log flags
only the dates since the previous run -- a day with no papers at all, or a day
where regulars are missing -- so a gap is reported once, not every day for a
month.

Since v16 a missed day costs nothing on RESOLUTION (the web check runs every
day regardless). What it still costs is forecast updates: the screen never
saw that day's news. That is why the gap is reported, and why it is reported
in plain words.
"""

from __future__ import annotations

import csv
import datetime as dt
import re

from . import config, store

UNKNOWN = "Unknown"


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

_cache: dict = {"path": None, "mtime": None, "rules": []}


def _rules() -> list[tuple[re.Pattern, str]]:
    path = config.PAPERS_CSV
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    if _cache["path"] == path and _cache["mtime"] == mtime:
        return _cache["rules"]
    rules = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pattern = (row.get("pattern") or "").strip()
            paper = (row.get("paper") or "").strip()
            if not pattern or not paper:
                continue
            try:
                rules.append((re.compile(pattern, re.IGNORECASE), paper))
            except re.error as exc:
                raise SystemExit(
                    f"config/papers.csv: bad pattern {pattern!r} for {paper}: "
                    f"{exc}. Fix the line and re-run."
                )
    _cache.update(path=path, mtime=mtime, rules=rules)
    return rules


def _normalise(filename: str) -> str:
    """
    'BS ● Mumbai ● 07‹09‹2026 Tr-Me.pdf' -> 'bs mumbai 07 09 2026 tr me'

    Every separator becomes a space, so a pattern like \\bbs\\b matches whatever
    punctuation a download tool happened to use, and never matches inside a
    longer word ('jobs', 'Boston').
    """
    stem = re.sub(r"\.pdf$", "", filename or "", flags=re.IGNORECASE)
    return " ".join(re.sub(r"[^0-9a-z]+", " ", stem.lower()).split())


def name_for(filename: str) -> str:
    """The paper's name from config/papers.csv, or UNKNOWN. First match wins."""
    text = _normalise(filename)
    for pattern, paper in _rules():
        if pattern.search(text):
            return paper
    return UNKNOWN


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def _date(value):
    try:
        return dt.date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def _by_date() -> tuple[dict, dict]:
    """
    {date: set(paper names)} and {date: [unrecognised filenames]}, rebuilt from
    processed.csv using the CURRENT papers.csv -- so history re-labels itself.
    """
    papers_on: dict[dt.date, set] = {}
    unknown_on: dict[dt.date, list] = {}
    for row in store.read_rows(config.PROCESSED_CSV):
        d = _date(row.get("issue_date_guess")) or _date(row.get("processed_on"))
        if d is None:
            continue
        name = name_for(row.get("filename", ""))
        if name == UNKNOWN:
            unknown_on.setdefault(d, []).append(row.get("filename", ""))
        else:
            papers_on.setdefault(d, set()).add(name)
    return papers_on, unknown_on


def regulars(papers_on: dict, today: dt.date, settings: dict) -> tuple[set, int]:
    """
    Papers that arrived on at least `regular_share` of upload days in the
    window. Returns (regular set, number of upload days in the window).
    Below `min_upload_days` nothing is called regular yet -- too little history.
    """
    cov = settings.get("coverage", {}) or {}
    window = int(cov.get("window_days", 30))
    share = float(cov.get("regular_share", 0.5))
    min_days = int(cov.get("min_upload_days", 5))
    start = today - dt.timedelta(days=window - 1)
    days = [d for d in papers_on if start <= d <= today and papers_on[d]]
    if len(days) < min_days:
        return set(), len(days)
    counts: dict[str, int] = {}
    for d in days:
        for p in papers_on[d]:
            counts[p] = counts.get(p, 0) + 1
    return {p for p, n in counts.items() if n >= share * len(days)}, len(days)


def _previous_run(today: dt.date):
    """The date of the latest run log before today, if any."""
    best = None
    for path in config.LOGS.glob("????-??-??.md") if config.LOGS.exists() else []:
        d = _date(path.stem)
        if d and d < today and (best is None or d > best):
            best = d
    return best


def report(today: dt.date, settings: dict, log, dry_run: bool = False) -> dict:
    """
    Rebuild data/coverage.csv and flag the gaps since the previous run.
    Pure bookkeeping: no model calls, never blocks anything.
    """
    cov = settings.get("coverage", {}) or {}
    window = int(cov.get("window_days", 30))
    papers_on, unknown_on = _by_date()
    regular, n_days = regulars(papers_on, today, settings)

    log.sub("Paper coverage")
    if regular:
        log.info(f"  regular papers ({len(regular)}, from {n_days} upload days "
                 f"in the last {window}): {', '.join(sorted(regular))}")
    else:
        log.info(f"  only {n_days} upload day(s) in the last {window} -- too "
                 "few to say which papers are regular yet")

    rows = []
    start = today - dt.timedelta(days=window - 1)
    first_seen = min(papers_on) if papers_on else today
    d = max(start, first_seen)
    while d <= today:
        got = papers_on.get(d, set())
        missing = sorted(regular - got)
        status = "none" if not got else ("partial" if missing else "full")
        rows.append({
            "date": d.isoformat(),
            "status": status,
            "papers": "; ".join(sorted(got)),
            "regulars_missing": "; ".join(missing),
            "unknown_files": "; ".join(unknown_on.get(d, [])),
        })
        d += dt.timedelta(days=1)

    if not dry_run:
        store.rewrite(config.COVERAGE_CSV, rows)

    # Flag only what is new since the previous run, so each gap is said once.
    prev = _previous_run(today)
    since = prev + dt.timedelta(days=1) if prev else today - dt.timedelta(days=6)
    gaps = {"none": [], "partial": []}
    for row in rows:
        rd = _date(row["date"])
        if rd is None or rd < since:
            continue
        if row["status"] == "none":
            gaps["none"].append(row["date"])
        elif row["status"] == "partial":
            gaps["partial"].append((row["date"], row["regulars_missing"]))

    if gaps["none"]:
        log.flag(
            "NO PAPERS for: " + ", ".join(gaps["none"]) + ".\n"
            "    Resolution is unaffected (the web check runs every day), but "
            "nothing from those days reached the screen or the lenses."
        )
    for day, missing in gaps["partial"]:
        log.flag(f"{day}: regular paper(s) missing -- {missing}")
    if not gaps["none"] and not gaps["partial"]:
        log.info(f"  every regular paper present since {since}")

    recent_unknown = sorted({f for day, files in unknown_on.items()
                             if day >= since for f in files})
    if recent_unknown:
        log.flag(
            "File(s) whose paper could not be named from the filename:\n"
            + "\n".join(f"      {f}" for f in recent_unknown)
            + "\n    Add a pattern for each to config/papers.csv -- the whole "
              "history re-labels on the next run."
        )
    return {"regular": regular, "rows": rows, "gaps": gaps}
