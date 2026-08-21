"""
Forecasting: run the lenses, aggregate, record everything.

CADENCE
-------
Two levels, because a daily re-read is structurally an over-adjustment machine.
Newspapers select for DRAMATIC, not for DIAGNOSTIC, and the two are close to
uncorrelated.

  * daily screen (cheap): does anything since the last run bear on this
    question? Records "no cause" WITH a reason either way, so the trail
    distinguishes "looked and found nothing" from "did not look".
  * full refresh (seven lenses): when a declared trigger fires, when a calendar
    hook arrives, when genuinely new material appears, or when the question has
    not been refreshed for `staleness_days`.

Staleness is measured against the LAST REFRESH, not a calendar schedule,
because runs happen when papers are uploaded. A fixed "refresh on Sundays"
breaks the moment a day is skipped; staleness rotation is self-correcting.

WHY NO WEIGHTS, ANYWHERE
------------------------
Weighting assumes parallel voters. Our stages are sequential -- the inside view
already contains the outside view, the reconciliation already contains both --
so weighting would double-count the same evidence under different names. The
aggregate is a plain MEDIAN of the responding lenses, which is also robust to
one lens producing a wild number.

EXTREMIZING IS A SHADOW NUMBER
------------------------------
It helped ordinary crowds and barely helped superforecasters, because it
corrects for a crowd being collectively underconfident when each member holds
only a fragment. Our lenses share one model and one set of blind spots, so the
information diversity that justifies it is largely absent -- extremizing a
correlated crowd amplifies a shared bias with false confidence. So it is
computed and stored, and never used as the live number. The calibration table
settles it in a couple of years.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics

from . import config, lenses as lensmod, reference, store

ADVOCATE_PROMPT = """You are the devil's advocate on a forecast. Seven \
forecasters, each with a narrow aperture, have produced numbers for this \
question. Your job is to ATTACK the aggregate, not to produce a better one.

THE QUESTION: {question}
RESOLUTION CRITERIA: {criteria}
DEADLINE: {deadline} ({days_left} days from today)

AGGREGATE (median of responding lenses): {median}

WHAT EACH LENS SAID:
{lens_summary}

Find the SINGLE sub-question whose failure would break this estimate. Not a \
list of caveats -- the one load-bearing assumption that, if wrong, moves the \
number most.

Then say whether the aggregate should move, and if so to what. You are allowed \
to conclude that it should not move; a devil's advocate that always demands a \
change is worthless.

Return JSON only:
{{
  "load_bearing_assumption": "...",
  "why_it_could_be_wrong": "...",
  "should_move": true/false,
  "moved_to": 0,
  "move_reason": "...",
  "lenses_that_look_redundant": ["..."],
  "concerns": "..."
}}"""


def median_of(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return float(statistics.median(vals))


def extremize(p: float, k: float = 1.5) -> float:
    """
    Shadow computation only. Never the live number. See module docstring.
    """
    if p is None:
        return None
    p = max(0.001, min(0.999, p / 100.0))
    odds = (p / (1 - p)) ** k
    return round(100.0 * odds / (1 + odds), 1)


def is_window(question: dict) -> bool:
    return (question.get("shape") or "point").strip().lower() == "window"


def needs_refresh(question: dict, today: dt.date, staleness_days: int) -> bool:
    last = (question.get("last_refresh") or "").strip()
    if not last:
        return True
    try:
        last_date = dt.date.fromisoformat(last)
    except ValueError:
        return True
    return (today - last_date).days >= staleness_days


def run_question(
    question: dict,
    lens_defs: list[dict],
    runner: lensmod.LensRunner,
    router,
    log,
    settings,
    today: dt.date,
    news: str,
    cause: str,
    similarity=None,
    only_lenses: list[str] | None = None,
    prior_lens_numbers: dict | None = None,
):
    """
    Run the lens set for one question and return the aggregate record.

    `only_lenses` supports the partial re-run on a fired trigger: a court
    injunction is blocker business and has nothing to say to reference class,
    so we re-run the affected lens plus the aggregate and leave the rest at
    their stored values.
    """
    qid = question.get("id", "")
    fc = settings.get("forecasting", {})
    min_lenses = int(fc.get("min_responding_lenses", 3))
    config_version = runner.config_version

    record = {
        "question_id": qid,
        "question": question.get("question", ""),
        "date": today.isoformat(),
        "cause": cause,
        "config_version": config_version,
        "shape": question.get("shape", ""),
        "lenses": {},
        "abstained": [],
        "excluded": [],
    }

    prior_lens_numbers = prior_lens_numbers or {}
    numbers: list[float] = []

    for lens in lens_defs:
        lid = lens.get("id", "")
        if not lens.get("active", True):
            continue
        if only_lenses and lid not in only_lenses:
            # Carried forward unchanged from the last full refresh.
            carried = prior_lens_numbers.get(lid)
            if carried is not None:
                record["lenses"][lid] = {"status": "carried", "probability": carried}
                numbers.append(carried)
            continue

        try:
            entry = _run_one_lens(
                lens, question, runner, router, log, settings, today, news,
                similarity, prior_lens_numbers.get(lid),
            )
        except Exception as exc:                      # noqa: BLE001
            # One lens failing must never take the question down.
            log.error(f"lens {lid} on {qid}", exc)
            record["lenses"][lid] = {"status": "failed", "error": str(exc)}
            continue

        record["lenses"][lid] = entry
        if entry.get("status") == "responded":
            numbers.append(entry["probability"])
        elif entry.get("status") == "abstained":
            record["abstained"].append(lid)
        elif entry.get("status") == "excluded_audit":
            record["excluded"].append(lid)

    responding = len(numbers)
    record["responding_lenses"] = responding
    record["abstained_lenses"] = len(record["abstained"])

    if responding < min_lenses:
        # Flagged, not forecast -- and the run continues regardless.
        record["status"] = "insufficient_lenses"
        record["median"] = None
        log.flag(
            f"{qid}: only {responding} lens(es) responded (floor is "
            f"{min_lenses}). No forecast produced. Abstained: "
            f"{', '.join(record['abstained']) or 'none'}"
        )
        return record

    med = median_of(numbers)
    record["median_raw"] = round(med, 1)
    record["median_extremized"] = extremize(med)   # shadow only

    # -- devil's advocate on the aggregate ---------------------------------
    advocate = _run_advocate(question, record, router, today)
    record["advocate"] = advocate
    final = med
    if isinstance(advocate, dict) and advocate.get("should_move"):
        moved = lensmod._num(advocate.get("moved_to"))
        if moved is not None:
            final = lensmod.clamp_probability(moved, 0.0, 100.0)

    record["probability"] = round(final, 1)
    record["status"] = "forecast"
    return record


def _run_one_lens(lens, question, runner, router, log, settings, today, news,
                  similarity, prior_prob):
    """The five stages for a single lens."""
    lid = lens.get("id", "")
    entry: dict = {"lens": lid}

    # -- stage 1: abstract + outside (blind to news AND to the prior) -------
    prior_entry = None
    outside, used_entry_id = runner.outside(lens, question, today, prior_entry)
    if outside is None:
        entry["status"] = "abstained"
        entry["reason"] = "could not define a population after two decompositions"
        return entry

    entry["abstraction"] = outside.get("abstraction", "")
    entry["outside_probability"] = lensmod._num(
        outside.get("outside_probability")
    )
    entry["provenance_tier"] = outside.get("provenance_tier", "reasoned")
    entry["outside_reasoning"] = outside.get("reasoning", "")
    entry["cases"] = outside.get("cases", [])
    entry["grounding"] = outside.get("_grounding", {})

    # Store the enumerated skeleton in the library, if there is one.
    if entry["provenance_tier"] == "structured" and outside.get("cases"):
        try:
            new_entry = reference.build_entry(
                lid, outside, today, settings, outside.get("_grounding")
            )
            reference.save_entry(new_entry)
            reference.record_use(new_entry["id"], question.get("id", ""))
            entry["reference_entry"] = new_entry["id"]
        except Exception as exc:                      # noqa: BLE001
            log.error(f"reference store for {lid}", exc)

    # Reference-class lens has NO inside view and takes no stances. It is the
    # pure prior, which is what makes it the ablation baseline: there is no
    # YES-flavoured way to count how many of 24 nominees were confirmed.
    if (lens.get("inside_shape") or "none") == "none":
        entry["status"] = "responded"
        entry["probability"] = lensmod.clamp_probability(
            entry["outside_probability"] or 50.0, 0.0, 100.0
        )
        entry["p_full"] = entry["probability"]
        entry["triggers"] = [
            {"event": "time passing with no qualifying action",
             "direction": "down", "to_roughly": ""}
        ]
        return entry

    # -- stages 2 and 3: two one-sided cases, blind to each other -----------
    yes_case = runner.inside_case(lens, question, today, "yes", news)
    no_case = runner.inside_case(lens, question, today, "no", news)
    entry["yes_case"] = yes_case
    entry["no_case"] = no_case

    # -- stage 4: reconcile (prior enters HERE, not earlier) ----------------
    rec = runner.reconcile(
        lens, question, today, outside, yes_case, no_case,
        prior_prob=prior_prob,
    )
    if rec is None:
        entry["status"] = "failed"
        entry["reason"] = "reconcile stage returned nothing"
        return entry

    # -- stage 5: blind audit, up to two specific retries -------------------
    audit_note, audit_result = "", "clean"
    named_defects: list[str] = []
    for attempt in range(runner.max_contam_retries + 1):
        verdict = runner.audit(lens, rec.get("reasoning", ""))
        if not verdict.get("violation"):
            audit_result = "clean" if attempt == 0 else "retried_clean"
            break
        defect = (verdict.get("forbidden_ground") or "").strip().lower()
        audit_note = verdict.get("instruction", "") or verdict.get("passage", "")
        if attempt >= runner.max_contam_retries:
            audit_result = "excluded"
            break
        if defect and defect in named_defects:
            # The same leak twice is not a lens that needs another chance --
            # it is a lens whose forbidden list is drawn wrong.
            audit_result = "excluded"
            audit_note = f"same defect flagged twice ({defect})"
            break
        named_defects.append(defect)
        retry = runner.reconcile(
            lens, question, today, outside, yes_case, no_case,
            prior_prob=prior_prob,
        )
        if retry is None:
            audit_result = "excluded"
            break
        rec = retry

    entry["audit_result"] = audit_result
    entry["audit_note"] = audit_note
    if audit_result == "excluded":
        entry["status"] = "excluded_audit"
        return entry

    p = lensmod._num(rec.get("probability"))
    if p is None:
        entry["status"] = "failed"
        entry["reason"] = "no usable probability in reconcile output"
        return entry

    entry["status"] = "responded"
    entry["probability"] = lensmod.clamp_probability(p, 0.0, 100.0)
    entry["reasoning"] = rec.get("reasoning", "")
    entry["deliberately_ignored"] = rec.get("deliberately_ignored", "")
    entry["stronger_case"] = rec.get("stronger_case", "")
    entry["triggers"] = rec.get("triggers", [])
    entry["moved_from"] = prior_prob
    entry["move_reason"] = rec.get("move_reason", "")
    entry["what_changes_between_horizons"] = rec.get(
        "what_changes_between_horizons", ""
    )

    # Horizon numbers are only meaningful for WINDOW questions. A point-event
    # question can only resolve at one scheduled moment, so intermediate
    # probabilities correspond to nothing. Models will volunteer them anyway if
    # the JSON shape invites it, and storing them would feed meaningless values
    # to the coherence and curve-divergence checks. Drop them at the boundary
    # rather than trusting the prompt to suppress them.
    if is_window(question):
        for key in ("p_one_third", "p_two_thirds", "p_full"):
            entry[key] = lensmod._num(rec.get(key))
    else:
        for key in ("p_one_third", "p_two_thirds"):
            entry[key] = None
        entry["p_full"] = entry["probability"]
    entry["model"] = rec.get("_model", "")
    return entry


def _run_advocate(question, record, router, today):
    summary_lines = []
    for lid, e in record.get("lenses", {}).items():
        if e.get("status") == "responded":
            summary_lines.append(
                f"  {lid}: {e.get('probability')} -- "
                f"{(e.get('reasoning') or '')[:200]}"
            )
        else:
            summary_lines.append(f"  {lid}: {e.get('status')}")
    prompt = ADVOCATE_PROMPT.format(
        question=question.get("question", ""),
        criteria=question.get("resolution_criteria", ""),
        deadline=question.get("deadline", ""),
        days_left=lensmod._days_left(question.get("deadline", ""), today),
        median=record.get("median_raw"),
        lens_summary="\n".join(summary_lines) or "(none)",
    )
    result, _model = router.generate(
        "advocate", prompt, temperature=0.4, max_output_tokens=2048
    )
    return result if isinstance(result, dict) else {}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def persist(record: dict, question: dict, today: dt.date, log) -> None:
    """Write the JSON record, the lens rows, and the forecast row."""
    qid = record["question_id"]
    store.write_run_record(today.isoformat(), qid, record)

    for lid, e in record.get("lenses", {}).items():
        if e.get("status") == "carried":
            continue
        store.append_row(config.LENS_CSV, {
            "question_id": qid,
            "date": today.isoformat(),
            "lens": lid,
            "status": e.get("status", ""),
            "probability": e.get("probability", ""),
            "p_one_third": e.get("p_one_third", ""),
            "p_two_thirds": e.get("p_two_thirds", ""),
            "p_full": e.get("p_full", ""),
            "outside_probability": e.get("outside_probability", ""),
            "provenance_tier": e.get("provenance_tier", ""),
            "reference_entry": e.get("reference_entry", ""),
            "yes_case_strength": (e.get("yes_case") or {}).get("strength", ""),
            "no_case_strength": (e.get("no_case") or {}).get("strength", ""),
            "audit_result": e.get("audit_result", ""),
            "audit_note": e.get("audit_note", ""),
            "triggers": _trigger_summary(e.get("triggers")),
            "moved_from": e.get("moved_from", ""),
            "move_reason": e.get("move_reason", ""),
            "model": e.get("model", ""),
            "config_version": record.get("config_version", ""),
        })

    if record.get("status") != "forecast":
        return

    store.append_row(config.FORECASTS_CSV, {
        "question_id": qid,
        "date": today.isoformat(),
        "model": "aggregate",
        "probability": record.get("probability", ""),
        "reason": (record.get("advocate") or {}).get("move_reason", "")
                  or "median of responding lenses",
        "stage": "aggregate",
        "p_one_third": _agg_horizon(record, "p_one_third"),
        "p_two_thirds": _agg_horizon(record, "p_two_thirds"),
        "p_full": _agg_horizon(record, "p_full"),
        "median_raw": record.get("median_raw", ""),
        "median_extremized": record.get("median_extremized", ""),
        "responding_lenses": record.get("responding_lenses", ""),
        "abstained_lenses": record.get("abstained_lenses", ""),
        "config_version": record.get("config_version", ""),
        "cause": record.get("cause", ""),
        "grounded": "n/a",
    })

    store.update_question(qid, {
        "probability": record.get("probability", ""),
        "prob_source": "refresh" if record.get("cause") == "refresh"
                       else record.get("cause", ""),
        "last_refresh": today.isoformat(),
    })


def _agg_horizon(record: dict, key: str):
    vals = [
        e.get(key) for e in record.get("lenses", {}).values()
        if e.get("status") == "responded" and e.get(key) is not None
    ]
    med = median_of(vals)
    return round(med, 1) if med is not None else ""


def _trigger_summary(triggers) -> str:
    if not triggers:
        return ""
    bits = []
    for t in triggers[:2]:
        if isinstance(t, dict):
            bits.append(
                f"{t.get('event','')} -> {t.get('direction','')} "
                f"{t.get('to_roughly','')}"
            )
    return " | ".join(bits)


def persist_no_forecast(record: dict, today: dt.date) -> None:
    """Even a question we could not forecast leaves a full record."""
    store.write_run_record(today.isoformat(), record["question_id"], record)
