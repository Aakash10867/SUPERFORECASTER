#!/usr/bin/env python3
"""
Build data/open_questions.csv -- one row per open question, everything you
need to see at a glance, so you never have to cross-reference questions.csv
against forecasts.csv by hand.

    python make_dashboard.py

Runs automatically at the end of every pipeline run (see the workflow), but it
is safe to run on its own any time. It only READS the data files and writes one
derived file, so it can never corrupt anything: if the output looks wrong,
delete it and run this again.

WHY IT IS A SEPARATE SCRIPT
---------------------------
This is a VIEW, not a record. It is rebuilt from scratch every time, holds no
information of its own, and nothing else reads it. Keeping it outside the
pipeline means it cannot break a run, and you can regenerate it after editing
config/resolutions.csv without re-running anything.

Sorted by days remaining, so whatever is closest to its deadline is at the top.
"""

import csv
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
QUESTIONS = DATA / "questions.csv"
FORECASTS = DATA / "forecasts.csv"
OUT = DATA / "open_questions.csv"

FIELDS = [
    "id",
    "days_left",           # negative means the deadline has passed
    "deadline",
    "probability",         # the live number
    "change",              # movement since the previous forecast, "+3.0" / "-5.0"
    "previous",
    "trend",               # up | down | flat | new
    "question",
    "shape",               # window | point
    "spread",              # gap between the highest and lowest lens
    "disagreement",        # wide | moderate | tight -- see below
    "low_lens",
    "high_lens",
    "responding_lenses",
    "last_updated",
    "days_since_update",
    "cause",               # creation | refresh | trigger
    "forecast_count",      # how many times it has been forecast
    "domain",
    "primary_tag",
    "resolution_criteria",
]


def read(path):
    if not path.exists():
        return []
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_date(value):
    try:
        return dt.date.fromisoformat((value or "").strip())
    except (ValueError, AttributeError):
        return None


def describe_spread(spread):
    """
    How much the seven lenses disagreed.

    A median hides this completely: 12% from seven lenses that all said 10-14
    is a very different object from 12% from lenses that ranged 1 to 65. The
    second is a summary of an argument, not a consensus, and it is worth
    knowing which one you are looking at before you trust the number.
    """
    if spread is None:
        return ""
    if spread >= 40:
        return "wide"
    if spread >= 20:
        return "moderate"
    return "tight"


def build():
    questions = read(QUESTIONS)
    forecasts = read(FORECASTS)
    if not questions:
        print(f"No questions found at {QUESTIONS}")
        return 1

    # Aggregate rows only -- per-lens numbers live in lens_outputs.csv.
    by_question = {}
    for f in forecasts:
        if f.get("model") != "aggregate":
            continue
        by_question.setdefault(f.get("question_id", ""), []).append(f)
    for rows in by_question.values():
        rows.sort(key=lambda r: r.get("date", ""))

    today = dt.date.today()
    out_rows = []

    for q in questions:
        if q.get("status") != "open":
            continue

        history = by_question.get(q.get("id", ""), [])
        latest = history[-1] if history else {}
        prior = history[-2] if len(history) > 1 else {}

        prob = as_float(latest.get("probability"))
        prev = as_float(prior.get("probability"))

        if prob is None:
            change, trend = "", "not yet forecast"
        elif prev is None:
            change, trend = "", "new"
        else:
            delta = round(prob - prev, 1)
            change = f"{delta:+.1f}"
            trend = "up" if delta > 0.05 else "down" if delta < -0.05 else "flat"

        deadline = as_date(q.get("deadline"))
        days_left = (deadline - today).days if deadline else ""

        updated = as_date(latest.get("date"))
        days_since = (today - updated).days if updated else ""

        spread = as_float(latest.get("spread"))

        out_rows.append({
            "id": q.get("id", ""),
            "days_left": days_left,
            "deadline": q.get("deadline", ""),
            "probability": prob if prob is not None else "",
            "change": change,
            "previous": prev if prev is not None else "",
            "trend": trend,
            "question": q.get("question", ""),
            "shape": q.get("shape", ""),
            "spread": spread if spread is not None else "",
            "disagreement": describe_spread(spread),
            "low_lens": latest.get("low_lens", ""),
            "high_lens": latest.get("high_lens", ""),
            "responding_lenses": latest.get("responding_lenses", ""),
            "last_updated": latest.get("date", ""),
            "days_since_update": days_since,
            "cause": latest.get("cause", ""),
            "forecast_count": len(history),
            "domain": q.get("domain", ""),
            "primary_tag": q.get("primary_tag", ""),
            "resolution_criteria": q.get("resolution_criteria", ""),
        })

    # Closest deadline first: that is the one most likely to need attention.
    out_rows.sort(key=lambda r: (r["days_left"] if isinstance(r["days_left"], int)
                                 else 99999))

    DATA.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in out_rows:
            writer.writerow(row)

    _print_summary(out_rows)
    print(f"\nWritten to {OUT}")
    return 0


def _print_summary(rows):
    """A readable version in the run log, so the log alone often suffices."""
    if not rows:
        print("No open questions.")
        return

    print(f"\nOPEN QUESTIONS ({len(rows)})")
    print("-" * 78)
    for r in rows:
        prob = f"{r['probability']}%" if r["probability"] != "" else "not forecast"
        arrow = {"up": "^", "down": "v", "flat": "=", "new": "*"}.get(r["trend"], " ")
        change = f" {arrow} {r['change']}" if r["change"] else f" {arrow}"
        print(f"{r['id']}  {prob:>13}{change:<8}  {r['days_left']:>4}d left  "
              f"{r['disagreement']:<8}")
        print(f"       {r['question'][:70]}")
        if r["disagreement"] == "wide":
            print(f"       ^ lenses disagreed by {r['spread']} points "
                  f"({r['low_lens']} lowest, {r['high_lens']} highest) -- "
                  "the median summarises an argument, not a consensus")
    print("-" * 78)


if __name__ == "__main__":
    sys.exit(build())
