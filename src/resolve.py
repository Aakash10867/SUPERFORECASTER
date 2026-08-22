"""
Resolution, the merged daily screen, and the absence watch.

WHAT WAS MISSING
----------------
questions.csv has always had `status`, `resolved_date`, `outcome` and `brier`,
and settings has always had `resolution_grace_days`. Nothing ever wrote them.
Every scoring mechanism depends on outcomes, so without this module the
forecasting layer would score nothing, forever.

THE MERGED SCREEN
-----------------
The daily "does anything bear on this question?" check and the "did this
resolve?" check read the same material against the same question. Running them
separately doubles the cost and creates a class of contradiction where the
screen finds nothing relevant while resolution finds a resolution. So it is one
call per question per run, returning both answers.

Because papers are uploaded on weekdays only and runs are irregular, the screen
reasons over EVERYTHING SINCE THE LAST RUN, not "today". A Monday run covers
Friday through Monday. Framed as "today's news", weekend events fall into a gap
and Monday's reporting of them looks like stale restatement.

THE CONFIRMATION BAR
--------------------
A wrong resolution corrupts the record permanently and silently, so the screen
only NOMINATES. An actual write requires two independent confirmations that
agree, each naming the specific reported event and walking every clause of the
criteria. Both run BLIND TO THE CURRENT FORECAST -- a confirmer that sees
"system says 8%" will resist calling YES.

LAPSE NOTICE AT THE MOMENT, NEVER BEFORE
----------------------------------------
Warning days ahead would send you looking, and what you found would enter the
record BEFORE the question was settled -- making you an input to a still-live
forecast. Notice at the moment of lapse keeps you strictly downstream, and
config/resolutions.csv exists so corrections can be retrospective.
"""

from __future__ import annotations

import datetime as dt

from . import config, store

SCREEN_PROMPT = """You are screening one open forecasting question against \
recent reporting. Two jobs, answered separately.

THE QUESTION: {question}
RESOLUTION CRITERIA: {criteria}
DEADLINE: {deadline}

DECLARED TRIGGERS -- events the forecasters said would change their estimate:
{triggers}

REPORTING SINCE THE LAST RUN ({since} to {today}):
{news}

JOB 1 -- RESOLUTION. Has a DEFINITIVE REPORTED ACT occurred that meets the \
resolution criteria, in EITHER direction? Not "it looks likely" -- a specific \
reported event. If nothing definitive has been reported, say no. This is only \
a nomination; a separate check will confirm it.

JOB 2 -- ESCALATION. Does anything here bear on the question enough to justify \
re-running the full forecast? A restatement of a story already reported is not \
new. Drama is not the test -- ask whether we would be seeing this story if the \
answer turned out to be the opposite. Reporting that is equally likely either \
way carries no information however alarming the headline.

You must give a reason either way. "Nothing relevant" with a reason is a \
useful record; silence is not.

Return JSON only:
{{
  "resolution_nominee": true/false,
  "resolution_direction": "yes|no|none",
  "resolution_event": "the specific reported act, with date",
  "escalate": true/false,
  "trigger_fired": "which declared trigger, or empty",
  "reason": "one line, always filled in",
  "diagnostic": "would we be seeing this story if the answer were the \
opposite? brief"
}}"""

CONFIRM_PROMPT = """You are confirming whether a forecasting question has \
genuinely resolved. Be strict: a wrong resolution corrupts the record \
permanently.

You have NOT been told what anyone forecast, and you must not guess. Judge only \
the criteria against the reporting.

THE QUESTION: {question}
RESOLUTION CRITERIA (walk EVERY clause):
{criteria}

CLAIMED RESOLVING EVENT: {event}

REPORTING:
{news}

Walk each clause of the criteria separately and mark it met or not met. ANY \
clause not CLEARLY met means the question has NOT resolved.

You must name the specific reported event -- what happened, when, and which \
report carries it. Asserting that the criteria are met is not enough.

Return JSON only:
{{
  "clauses": [{{"clause": "...", "met": true/false, "evidence": "..."}}],
  "resolved": true/false,
  "outcome": "1|0",
  "event_date": "YYYY-MM-DD",
  "event_description": "...",
  "confidence_note": "..."
}}"""


def _date(value, fallback=None):
    try:
        return dt.date.fromisoformat((value or "").strip())
    except (ValueError, AttributeError):
        return fallback


def apply_human_resolutions(log, today: dt.date) -> int:
    """
    Apply config/resolutions.csv. Read fresh every run, NEVER cleared.

    A human decision is permanent and terminal: it stops the absence watch and
    the system never second-guesses it. You can revise your own entry later by
    editing the file, because it is re-read every run.
    """
    rows = store.read_resolution_overrides()
    if not rows:
        return 0
    applied = 0
    for row in rows:
        qid = (row.get("question_id") or "").strip()
        q = store.question_by_id(qid)
        if q is None:
            # Loud failure, same as stage one's override behaviour.
            raise SystemExit(
                f"config/resolutions.csv refers to unknown question '{qid}'. "
                "Fix the file and re-run."
            )
        outcome = (row.get("outcome") or "").strip().lower()
        changes = {"outcome_set_by": "human", "watch_until": ""}
        if outcome == "reopen":
            changes.update({
                "status": "open", "outcome": "", "resolved_date": "",
                "brier": "", "resolution_basis": "",
            })
        elif outcome == "void":
            changes.update({"status": "void", "outcome": "void"})
        elif outcome in ("0", "1"):
            changes.update({"status": "resolved", "outcome": outcome})
        new_date = (row.get("resolved_date") or "").strip()
        if new_date:
            changes["resolved_date"] = new_date
        store.update_question(qid, changes)
        applied += 1
        log.info(
            f"  human resolution applied to {qid}: outcome={outcome or 'unchanged'}"
            + (f", resolved_date={new_date}" if new_date else "")
            + f"  ({row.get('note','')})"
        )
    return applied


def screen_question(question, news, triggers_text, since, today, router):
    prompt = SCREEN_PROMPT.format(
        question=question.get("question", ""),
        criteria=question.get("resolution_criteria", ""),
        deadline=question.get("deadline", ""),
        triggers=triggers_text or "(none declared yet)",
        since=since,
        today=today.isoformat(),
        news=news or "(no new reporting since the last run)",
    )
    result, model = router.generate(
        "screen", prompt, temperature=0.2, max_output_tokens=1536
    )
    if isinstance(result, dict):
        result["_model"] = model
        return result
    return None


def confirm_resolution(question, event, news, router, log):
    """
    Two independent confirmations, both must agree.

    Returns (resolved: bool, outcome: str, event_date: str, detail: dict).
    """
    prompt = CONFIRM_PROMPT.format(
        question=question.get("question", ""),
        criteria=question.get("resolution_criteria", ""),
        event=event or "(unspecified)",
        news=news or "(no reporting supplied)",
    )
    verdicts = []
    for _ in range(2):
        result, model = router.generate(
            "confirm", prompt, temperature=0.1, max_output_tokens=2048
        )
        if isinstance(result, dict):
            result["_model"] = model
            verdicts.append(result)
    if len(verdicts) < 2:
        log.warn(
            "Resolution confirmation could not get two independent verdicts; "
            "treating as NOT resolved."
        )
        return False, "", "", {"verdicts": verdicts}

    a, b = verdicts[0], verdicts[1]
    if not (a.get("resolved") and b.get("resolved")):
        return False, "", "", {"verdicts": verdicts, "agreed": False}
    if str(a.get("outcome")) != str(b.get("outcome")):
        log.flag(
            f"{question.get('id')}: confirmers disagreed on direction "
            f"({a.get('outcome')} vs {b.get('outcome')}). Not resolved."
        )
        return False, "", "", {"verdicts": verdicts, "agreed": False}

    return True, str(a.get("outcome")), (
        a.get("event_date") or b.get("event_date") or ""
    ), {"verdicts": verdicts, "agreed": True}


def lapse_due(question, today, grace_days) -> bool:
    d = _date(question.get("deadline"))
    if d is None:
        return False
    return today > d + dt.timedelta(days=grace_days)


def lapse(question, today, settings, log) -> None:
    """
    Deadline plus grace passed with nothing reported: resolve NO.

    The question then goes on the absence watch, because a macro event that
    happened but was not carried by the three papers would otherwise sit in the
    record as a false NO -- and false NOs flatter a system that forecasts low.
    """
    watch_days = int(settings.get("forecasting", {}).get("watch_expiry_days", 90))
    qid = question.get("id", "")
    deadline = _date(question.get("deadline"), today)
    store.update_question(qid, {
        "status": "resolved",
        "outcome": "0",
        "resolved_date": deadline.isoformat(),
        "resolution_basis": "lapsed_absence",
        "outcome_set_by": "system",
        "watch_until": (today + dt.timedelta(days=watch_days)).isoformat(),
    })
    log.flag(
        f"{qid} has LAPSED as NO -- the deadline and {settings['portfolio']['resolution_grace_days']}-day "
        f"grace passed with no qualifying reported act.\n"
        f"    Question: {question.get('question','')}\n"
        f"    Criteria: {question.get('resolution_criteria','')}\n"
        f"    If you know this actually happened, add a row to "
        f"config/resolutions.csv and every score will recompute.\n"
        f"    The system will keep cheaply watching for it until "
        f"{(today + dt.timedelta(days=watch_days)).isoformat()}."
    )


def resolve_now(question, outcome, event_date, today, log, basis="confirmed_act"):
    qid = question.get("id", "")
    store.update_question(qid, {
        "status": "resolved",
        "outcome": str(outcome),
        "resolved_date": event_date or today.isoformat(),
        "resolution_basis": basis,
        "outcome_set_by": "system",
        "watch_until": "",
    })
    log.flag(
        f"{qid} RESOLVED as {'YES' if str(outcome) == '1' else 'NO'} on "
        f"{event_date or today.isoformat()} (definitive reported act, two "
        f"confirmations agreed)."
    )


def expire_watches(today, log) -> int:
    """Watches do not run forever, or the cost creeps as dead questions pile up."""
    n = 0
    for q in store.watched_questions():
        until = _date(q.get("watch_until"))
        if until and today > until:
            store.update_question(q["id"], {"watch_until": ""})
            log.info(
                f"  watch on {q['id']} expired ({q.get('watch_until')}); "
                "no late reporting found"
            )
            n += 1
    return n
