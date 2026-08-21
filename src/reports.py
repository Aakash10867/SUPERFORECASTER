"""
Diagnostics: the fast clock.

TWO CLOCKS, DIFFERING BY TWO ORDERS OF MAGNITUDE
------------------------------------------------
The outcome loop -- forecast, wait, resolve, learn -- produces maybe 40
resolutions a year. Regression to the mean is the test for luck, and at n=40
that test cannot be run. It will teach us nothing for years. That is
arithmetic, not pessimism.

The process loop produces roughly 5,000 data points a year: every lens output,
every screen, every curve-versus-refresh comparison, every abstention, every
trigger that fires or does not. That is where learning actually lives, and it
is also the honest answer to the poker problem -- the book says stop asking
"was I right?" and ask "was the judgement reasonable on what was known then?".
The process loop is that question asked at scale.

DIAGNOSIS IS NOT VALIDATION
---------------------------
These metrics can show a lens is BROKEN. They cannot show that a change FIXED
it -- that is a claim about accuracy, and only outcomes settle it. So nothing
here self-corrects. Everything is surfaced to you in system_proposals.csv, and
you decide. Every process metric is gameable by a system optimising it; flag
"this lens abstains too much" and the obvious fix is a lens that abstains less
by emitting noise numbers.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics

from . import config, store


def _note(today, qid, kind, detail, severity="note"):
    store.append_row(config.DIAGNOSTICS_CSV, {
        "date": today.isoformat(),
        "question_id": qid,
        "kind": kind,
        "detail": detail,
        "severity": severity,
    })


def check_record(record: dict, today: dt.date, log) -> None:
    """Per-question checks, run immediately after a forecast."""
    qid = record.get("question_id", "")

    # -- coherence: three horizons must be non-decreasing ------------------
    for lid, e in record.get("lenses", {}).items():
        if e.get("status") != "responded":
            continue
        trio = [e.get("p_one_third"), e.get("p_two_thirds"), e.get("p_full")]
        if all(v is not None for v in trio):
            if not (trio[0] <= trio[1] <= trio[2] + 1e-9):
                _note(today, qid, "coherence_break",
                      f"{lid}: horizons not non-decreasing {trio}", "flag")
                log.flag(
                    f"{qid}/{lid}: horizon probabilities are not "
                    f"non-decreasing ({trio}). More time cannot reduce the "
                    "chance of something happening by SOME deadline."
                )
            # Revision fires on CONTRADICTION, never on flatness. Flat numbers
            # with reasoning that says nothing changes with time are correct.
            elif trio[0] == trio[1] == trio[2]:
                changes = (e.get("what_changes_between_horizons") or "").strip()
                if changes and "nothing" not in changes.lower():
                    _note(today, qid, "broken_time_model",
                          f"{lid}: names a time-dependent mechanism "
                          f"({changes[:120]}) but returned flat numbers", "flag")
                    log.flag(
                        f"{qid}/{lid}: reasoning names something that changes "
                        f"with time, but the three horizon numbers are "
                        f"identical. Internal contradiction."
                    )

    # -- trigger contradiction --------------------------------------------
    for lid, e in record.get("lenses", {}).items():
        if e.get("status") != "responded":
            continue
        if record.get("cause", "").startswith("trigger:") and \
                e.get("moved_from") is not None:
            try:
                if abs(float(e["probability"]) - float(e["moved_from"])) < 0.5:
                    _note(today, qid, "trigger_contradiction",
                          f"{lid}: declared trigger fired but the number did "
                          "not move", "flag")
                    log.flag(
                        f"{qid}/{lid}: a declared trigger fired and this lens "
                        "declined to move. It is contradicting its own written "
                        "prediction."
                    )
            except (TypeError, ValueError):
                pass

    # -- thin lens set -----------------------------------------------------
    if record.get("responding_lenses", 0) and record["responding_lenses"] <= 3:
        _note(today, qid, "thin_lens_set",
              f"only {record['responding_lenses']} responded; abstained: "
              f"{', '.join(record.get('abstained', []))}")

    # -- audit exclusions --------------------------------------------------
    for lid in record.get("excluded", []):
        _note(today, qid, "audit_exclusion",
              f"{lid} excluded from the median after repeated contamination",
              "flag")


def curve_divergence(today: dt.date, log) -> list[dict]:
    """
    Last refresh declared where the number should sit today. Today's refresh
    says where it actually sits.

    The curve is a TEST INSTRUMENT, never a value source. Systematic divergence
    means the lens's time model is broken -- and that is fast-clock signal
    available in weeks, not years.
    """
    out = []
    if not config.RUNS.exists():
        return out
    dates = sorted(d.name for d in config.RUNS.iterdir() if d.is_dir())
    if len(dates) < 2:
        return out
    today_iso = today.isoformat()
    if today_iso not in dates:
        return out

    for path in (config.RUNS / today_iso).glob("*.json"):
        qid = path.stem
        try:
            now = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        prev = store.latest_run_record(qid, before=today_iso)
        if not prev:
            continue
        for lid, e in (now.get("lenses") or {}).items():
            if e.get("status") != "responded":
                continue
            p_now = e.get("probability")
            prior = (prev.get("lenses") or {}).get(lid, {})
            declared = prior.get("p_one_third")
            if p_now is None or declared is None:
                continue
            gap = abs(float(p_now) - float(declared))
            if gap >= 15:
                out.append({"question_id": qid, "lens": lid,
                            "declared": declared, "actual": p_now, "gap": gap})
                _note(today, qid, "curve_divergence",
                      f"{lid}: declared ~{declared} for this point in the "
                      f"horizon, now says {p_now}")
    return out


def redundancy(log) -> list[dict]:
    """
    Two lenses producing near-identical numbers across many questions are not
    perpendicular, and one of them is decorative. Pure correlation -- no
    outcomes needed.
    """
    rows = store.read_rows(config.LENS_CSV)
    by_q: dict[str, dict[str, float]] = {}
    for r in rows:
        if r.get("status") != "responded":
            continue
        try:
            by_q.setdefault(
                f"{r.get('question_id')}|{r.get('date')}", {}
            )[r.get("lens", "")] = float(r.get("probability"))
        except (TypeError, ValueError):
            continue
    lenses = sorted({l for v in by_q.values() for l in v})
    out = []
    for i, a in enumerate(lenses):
        for b in lenses[i + 1:]:
            diffs = [abs(v[a] - v[b]) for v in by_q.values() if a in v and b in v]
            if len(diffs) >= 10 and statistics.mean(diffs) < 5:
                out.append({"a": a, "b": b, "n": len(diffs),
                            "mean_gap": round(statistics.mean(diffs), 1)})
    return out


def abstention_rates() -> dict:
    rows = store.read_rows(config.LENS_CSV)
    tally: dict[str, dict] = {}
    for r in rows:
        t = tally.setdefault(r.get("lens", ""), {"total": 0, "abstained": 0})
        t["total"] += 1
        if r.get("status") == "abstained":
            t["abstained"] += 1
    return {
        k: {"n": v["total"],
            "abstain_pct": round(100 * v["abstained"] / v["total"], 1)}
        for k, v in tally.items() if v["total"]
    }


def propose(today, kind, subject, evidence, suggestion) -> None:
    """
    Write a diagnosed problem to the proposal file for human review.

    Same pattern as stage one's pending_tags.csv: the system proposes, you
    dispose. Nothing here changes behaviour on its own.
    """
    store.append_row(config.SYSTEM_PROPOSALS_CSV, {
        "date": today.isoformat(),
        "kind": kind,
        "subject": subject,
        "evidence": evidence,
        "suggestion": suggestion,
        "status": "open",
    })


def write_reports(today: dt.date, log, settings) -> None:
    """Assemble every report, write them to data/reports/, summarise in the log."""
    from . import score

    config.REPORTS.mkdir(parents=True, exist_ok=True)

    summary = score.score_all(log)
    stages = score.stage_comparison()
    calib = score.calibration_table()
    absten = abstention_rates()
    dupes = redundancy(log)

    payload = {
        "generated": today.isoformat(),
        "scoring": summary,
        "stage_comparison": stages,
        "calibration": calib,
        "abstention_rates": absten,
        "redundant_lens_pairs": dupes,
    }
    with open(config.REPORTS / "latest.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    with open(config.REPORTS / f"{today.isoformat()}.json", "w",
              encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    log.sub("Scoring")
    n = summary.get("questions_scored", 0)
    if not n:
        log.info(
            "  No questions have resolved yet, so there is nothing to score. "
            "Expect this for months -- the slow clock is genuinely slow."
        )
    else:
        log.info(
            f"  day-weighted Brier {summary['day_weighted_brier']} "
            f"across {summary['forecast_days']} forecast-days "
            f"-- but only {n} QUESTION(S) of evidence."
        )
        if summary.get("best_baseline_brier") is not None:
            verdict = "BEATS" if summary.get("beats_baseline") else "does NOT beat"
            log.info(
                f"  best constant baseline was {summary['best_baseline_rung']}% "
                f"at {summary['best_baseline_brier']}; the system {verdict} it."
            )
        if summary.get("human_set_outcomes"):
            log.info(
                f"  {summary['human_set_outcomes']} outcome(s) were set by hand."
            )
        if n < 100:
            log.info(
                "  NOTE: calibration is not informative below ~100 resolutions. "
                "Read it, do not act on it."
            )

    if stages.get("outside") is not None and stages.get("final") is not None:
        better = stages["final"] < stages["outside"]
        log.info(
            f"  ablation: outside-view-only {stages['outside']} vs full "
            f"pipeline {stages['final']} -- "
            f"{'the pipeline adds value' if better else 'THE PIPELINE IS NOT ADDING VALUE'}"
        )

    if absten:
        log.sub("Lens abstention rates")
        for lid, v in sorted(absten.items()):
            log.info(f"  {lid}: {v['abstain_pct']}% of {v['n']} runs")
            if v["n"] >= 20 and v["abstain_pct"] > 80:
                propose(today, "mis_scoped_lens", lid,
                        f"abstained on {v['abstain_pct']}% of {v['n']} runs",
                        "aperture may be aimed at nothing; consider widening "
                        "or retiring")
            if v["n"] >= 20 and v["abstain_pct"] == 0:
                propose(today, "mis_scoped_lens", lid,
                        f"never abstained across {v['n']} runs",
                        "a lens that never abstains may not be exercising "
                        "judgement")

    for d in dupes:
        log.flag(
            f"Lenses {d['a']} and {d['b']} produced numbers within "
            f"{d['mean_gap']} points of each other across {d['n']} forecasts. "
            "They may not be perpendicular."
        )
        propose(today, "redundant_lenses", f"{d['a']}+{d['b']}",
                f"mean gap {d['mean_gap']} over {d['n']} paired forecasts",
                "check whether one aperture is decorative")
