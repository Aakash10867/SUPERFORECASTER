"""
Scoring. Everything recomputed from source, every run.

WHY NOTHING IS STORED AS FINAL
------------------------------
An outcome can flip a month later -- you notice that a lapsed NO actually
happened and add a row to config/resolutions.csv. If the Brier score had been
computed once and written down, every derived figure would go stale: the
question's own score, the per-lens calibration tables, the day-weighted trail,
the baseline comparison. So all of it is arithmetic over the forecast trail and
the current outcomes, done fresh each run. The `brier` column in questions.csv
is a cached display value that gets rewritten, not a record.

DAY-WEIGHTED, WITH THE QUESTION COUNT BESIDE IT
-----------------------------------------------
Being right EARLY is worth more than being right the day before, so the whole
trail is scored, not just the final forecast. But 100 forecasts of 4 questions
is 4 questions' worth of evidence, not 100. The count is printed next to every
score because that is very easy to forget.

THE BASELINE IS THE POINT
-------------------------
A Brier score means nothing on its own. 0.10 sounds impressive until you learn
the dumb answer scored 0.09. Every question here resolves NO when nothing
happens -- governments default to inertia -- so the dumb competitor is a single
constant applied to everything. We score a whole ladder and require the system
to beat the BEST rung, which is chosen with hindsight and therefore unfair. If
the system clears an unfair bar, the result means something.
"""

from __future__ import annotations

import datetime as dt
import statistics

from . import config, store

LADDER = [5, 10, 20, 30, 40, 50]


def brier(prob_pct: float, outcome: int) -> float:
    p = max(0.0, min(1.0, float(prob_pct) / 100.0))
    return (p - float(outcome)) ** 2


def _date(v, fallback=None):
    try:
        return dt.date.fromisoformat((v or "").strip())
    except (ValueError, AttributeError):
        return fallback


def daily_trail(qid: str, forecasts: list[dict], start: dt.date,
                end: dt.date) -> list[tuple[dt.date, float]]:
    """
    One probability for every day the question was open.

    Between refreshes the number PERSISTS FLAT -- no interpolation, no invented
    values. The three-horizon numbers are a test instrument, not a value
    source: last week's declared curve says where the number should sit today,
    and today's refresh says where it actually sits. Divergence is signal about
    the lens's time model; it is not a substitute for asking again.
    """
    points = []
    for f in forecasts:
        if f.get("model") != "aggregate":
            continue
        d = _date(f.get("date"))
        p = f.get("probability")
        if d is None or p in (None, ""):
            continue
        try:
            points.append((d, float(p)))
        except ValueError:
            continue
    if not points:
        return []
    points.sort()

    out = []
    idx = 0
    current = points[0][1]
    day = max(start, points[0][0])
    while day <= end:
        while idx < len(points) and points[idx][0] <= day:
            current = points[idx][1]
            idx += 1
        out.append((day, current))
        day += dt.timedelta(days=1)
    return out


def score_all(log=None) -> dict:
    """
    Recompute every score from source. Returns a summary dict.
    """
    questions = store.read_rows(config.QUESTIONS_CSV)
    forecasts = store.read_rows(config.FORECASTS_CSV)
    by_q: dict[str, list[dict]] = {}
    for f in forecasts:
        by_q.setdefault(f.get("question_id", ""), []).append(f)

    resolved = []
    per_question = {}
    trail_scores: list[float] = []
    ladder_scores = {k: [] for k in LADDER}
    final_scores: list[float] = []

    for q in questions:
        if q.get("status") != "resolved":
            continue
        if str(q.get("outcome")).lower() == "void":
            continue
        try:
            outcome = int(q.get("outcome"))
        except (TypeError, ValueError):
            continue

        created = _date(q.get("created"))
        end = _date(q.get("resolved_date")) or _date(q.get("deadline"))
        if created is None or end is None:
            continue

        trail = daily_trail(q["id"], by_q.get(q["id"], []), created, end)
        if not trail:
            continue

        day_scores = [brier(p, outcome) for _, p in trail]
        q_trail = statistics.mean(day_scores)
        q_final = brier(trail[-1][1], outcome)

        trail_scores.extend(day_scores)
        final_scores.append(q_final)
        for rung in LADDER:
            ladder_scores[rung].extend(
                [brier(rung, outcome) for _ in trail]
            )

        per_question[q["id"]] = {
            "trail_brier": round(q_trail, 4),
            "final_brier": round(q_final, 4),
            "days": len(trail),
            "outcome": outcome,
            "outcome_set_by": q.get("outcome_set_by", ""),
            "basis": q.get("resolution_basis", ""),
        }
        resolved.append(q)

        store.update_question(q["id"], {"brier": round(q_trail, 4)})

    summary = {
        "questions_scored": len(resolved),
        "forecast_days": len(trail_scores),
        "day_weighted_brier": round(statistics.mean(trail_scores), 4)
                              if trail_scores else None,
        "final_forecast_brier": round(statistics.mean(final_scores), 4)
                                if final_scores else None,
        "per_question": per_question,
        "baseline": {
            str(k): round(statistics.mean(v), 4)
            for k, v in ladder_scores.items() if v
        },
        # Corrections are naturally sought where the system's NO felt wrong and
        # not hunted for where a lucky NO happened to be right. That is bias in
        # WHICH corrections get made, so both figures are shown separately.
        "human_set_outcomes": sum(
            1 for v in per_question.values() if v["outcome_set_by"] == "human"
        ),
    }
    if summary["baseline"]:
        best_rung, best = min(summary["baseline"].items(), key=lambda kv: kv[1])
        summary["best_baseline_rung"] = best_rung
        summary["best_baseline_brier"] = best
        if summary["day_weighted_brier"] is not None:
            summary["beats_baseline"] = (
                summary["day_weighted_brier"] < best
            )
    return summary


def stage_comparison() -> dict:
    """
    Per-stage accuracy: does the full pipeline beat its own outside view?

    This is the ablation, and it is a PAIRED comparison -- the question's
    inherent difficulty appears in both numbers and cancels out, which extracts
    far more signal from ~40 resolutions a year than any unpaired comparison
    would.

    If the full pipeline does not beat outside-view-only, everything downstream
    of the base rate is decoration and we have built an elaborate machine for
    adding noise.
    """
    questions = {q["id"]: q for q in store.read_rows(config.QUESTIONS_CSV)}
    lens_rows = store.read_rows(config.LENS_CSV)
    out: dict[str, list[float]] = {"outside": [], "final": []}
    per_lens: dict[str, list[float]] = {}

    for r in lens_rows:
        q = questions.get(r.get("question_id", ""))
        if not q or q.get("status") != "resolved":
            continue
        try:
            outcome = int(q.get("outcome"))
        except (TypeError, ValueError):
            continue
        for key, col in (("outside", "outside_probability"),
                         ("final", "probability")):
            val = r.get(col)
            if val in (None, ""):
                continue
            try:
                out[key].append(brier(float(val), outcome))
            except ValueError:
                continue
        val = r.get("probability")
        if val:
            try:
                per_lens.setdefault(r.get("lens", ""), []).append(
                    brier(float(val), outcome)
                )
            except ValueError:
                pass

    result = {
        k: round(statistics.mean(v), 4) for k, v in out.items() if v
    }
    result["per_lens"] = {
        k: {"brier": round(statistics.mean(v), 4), "n": len(v)}
        for k, v in per_lens.items() if v
    }
    return result


def calibration_table(bins=(0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)) -> dict:
    """
    Forecast on x, observed frequency on y. Above the diagonal means
    underconfident; below means overconfident.

    READ THE README: this shows noise until roughly 100 resolutions. At 30-50
    resolutions a year it will not be informative for about two years, and
    acting on it before then is worse than ignoring it.
    """
    questions = {q["id"]: q for q in store.read_rows(config.QUESTIONS_CSV)}
    rows = store.read_rows(config.FORECASTS_CSV)
    buckets: dict[str, list[int]] = {}
    for r in rows:
        if r.get("model") != "aggregate":
            continue
        q = questions.get(r.get("question_id", ""))
        if not q or q.get("status") != "resolved":
            continue
        try:
            outcome = int(q.get("outcome"))
            p = float(r.get("probability"))
        except (TypeError, ValueError):
            continue
        for lo, hi in zip(bins, bins[1:]):
            if lo <= p < hi or (hi == 100 and p == 100):
                buckets.setdefault(f"{lo}-{hi}", []).append(outcome)
                break
    return {
        k: {"n": len(v), "observed_rate": round(100 * sum(v) / len(v), 1)}
        for k, v in sorted(buckets.items()) if v
    }
