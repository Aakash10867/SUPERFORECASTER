"""
Web resolution (v16): one reader with Google Search, judged by code.

WHY THIS EXISTS
---------------
Resolution used to see only the papers. Q0003 (the Treasury buyback question)
showed that is not enough: Treasury raised its long-end buyback operations to
"at least $4 billion" and bought a full $6 billion on 10 September, and the
question sat open at 72% for three weeks afterwards. The papers did carry it
once -- the 10 September screen escalated on "a $6 billion debt buyback
announcement" -- but the screen never nominated it, and on every other day the
story simply was not in the selection.

THE DESIGN, AND WHY IT IS THIS SMALL
------------------------------------
One grounded call per question per day asks one sharp question: HAS THE EVENT
ACTUALLY HAPPENED, AND ON WHAT DATE? Then plain code -- not a second model --
decides whether to act on the answer.

A second model reading the same excerpts was considered and rejected. It
shares the first reader's blind spots (same family, same evidence), so it
catches careless slips but not the systematic misreadings that matter -- the
same reason prompt-varied agents are not a crowd. The checks below are
mechanical and cannot be talked round.

YES ONLY
--------
This module never resolves NO. "This can no longer happen" is exactly the
judgement where a model overreaches, and NO already has a deterministic route:
the deadline lapse in resolve.py. A model that thinks a question is dead says
"happened: false" and the question waits for its deadline.

THE RULE THE READER IS GIVEN
----------------------------
An announcement that something WILL happen is evidence, not resolution. It
should push the forecast up; it must not close the question. The only
exception is when the criteria themselves name the announcement as the
resolving act ("Treasury officially announces sanctions ...") -- the criteria
are read literally either way.

THE CODE CHECKS (all must pass for an automatic YES)
----------------------------------------------------
  1. The search actually fired, with at least one source in Google's record.
     The record comes from the API's groundingMetadata, not from links the model
     writes in its answer -- those can be invented.
  2. The reader says it happened and gives a parseable event date.
  3. created <= event date <= min(today, deadline).
       - after today       -> announced, not yet happened: stays open.
       - after deadline    -> happened too late, so it is not a YES: the lapse
                              rule will resolve NO in the normal way.
       - before created    -> the question was born resolved. That is a
                              question defect, and a resolved date before the
                              creation date would also break the day-weighted
                              trail. Goes to you.
  4. At least two DIFFERENT sites in the search record, or one official site
     (a government domain). A single unofficial source goes to you.

If 2 holds but 3 or 4 fails, the question goes to data/pending_resolutions.csv
with a ready-made row to paste into config/resolutions.csv.

WHEN IT RESOLVES, IT USES THE EVENT DATE
----------------------------------------
resolved_date is the day the event happened, not the day the check noticed.
The day-weighted trail is scored to that date, so a late detection costs a few
days of portfolio slot, never scoring accuracy.

BUDGET
------
Only the Gemini 2 / 2.5 families can search on the free tier, and Gemini 2
Flash has a zero daily allowance. That leaves 2.5 Flash Lite and 2.5 Flash at
20 requests a day each, per key: 80 a day across both keys. Grounding's own
1,500-a-day allowance never binds, because each grounded call is still an
ordinary request that counts against the model's 20.

To stay inside that:
  - each question is checked at most ONCE A DAY, however many runs you do;
  - open questions are checked closest-deadline first, then lapsed questions
    still on the absence watch;
  - a per-run cap (settings: web_resolution.max_checks_per_run);
  - the first time every search model is out of quota, the loop stops and the
    rest wait for tomorrow. Nothing blocks.

BACKFILL GUARD
--------------
With --date set to a past day the web would show the future relative to that
date. The event-date check would catch most of that, but not all of it, so web
resolution simply does not run unless the run date is the real today.
"""

from __future__ import annotations

import datetime as dt

from . import config, resolve, store

TASK = "web_resolve"

WEB_PROMPT = """Search the web and decide ONE thing: has this forecasting \
question ALREADY RESOLVED YES?

TODAY: {today}
QUESTION: {question}
RESOLUTION CRITERIA: {criteria}
DEADLINE: {deadline}
WHAT RESOLVES IT: {resolves_on}

THE RULE
- YES requires that the event the criteria describe has ACTUALLY HAPPENED, on \
or before today and on or before the deadline.
- An announcement that something WILL happen is NOT resolution, however \
official or certain it sounds. Plans, proposals, drafts, expectations, votes \
still pending, "set to", "expected to", "will take effect on <future date>" are \
all NOT YET. They make YES more likely; they do not make it happen.
- "WHAT RESOLVES IT" above says which act counts. Follow it exactly. Read the \
criteria literally, every clause.
- If the act happened, give the date it HAPPENED -- not the date it was first \
announced, unless the announcement is the act.
- You are judging YES only. If it has not clearly happened, answer \
"happened": false. Never conclude that the question resolves NO.

Return JSON only, no prose before or after:
{{
  "happened": true or false,
  "status": "happened | announced_not_yet_happened | in_progress | no_relevant_news",
  "event": "what specifically happened (or what was announced)",
  "event_date": "YYYY-MM-DD, the date it actually happened; empty if it has not",
  "evidence": "one or two sentences from the reporting that establish it",
  "reason": "one line, always filled in"
}}"""


# v17: each question declares what resolves it. Blank or "ambiguous" gets the
# strict reading -- the act itself -- because a premature YES is the costly
# error and a late one is not.
RESOLVES_ON_TEXT = {
    "announced": ("the formal announcement or decision ITSELF resolves this "
                  "-- it does not also have to be carried out."),
    "carried_out": ("the act must actually have been CARRIED OUT. An "
                    "announcement that it will be is NOT enough."),
    "in_effect": ("it must actually have TAKEN EFFECT or been implemented. "
                  "Being issued, signed, or announced with a later effective "
                  "date is NOT enough; the event date is the day it took "
                  "effect."),
}


def resolves_on_text(question: dict) -> str:
    key = (question.get("resolves_on") or "").strip().lower()
    return RESOLVES_ON_TEXT.get(key, RESOLVES_ON_TEXT["carried_out"])


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _date(value):
    try:
        return dt.date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "yes", "1")


def _site(name: str) -> str:
    """Normalise a source name so 'www.Reuters.com' and 'reuters.com' are one site."""
    s = (name or "").strip().lower()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    s = s.split("/")[0]
    if s.startswith("www."):
        s = s[4:]
    return s


def _is_official(site: str, settings: dict) -> bool:
    wr = settings.get("web_resolution", {}) or {}
    for suffix in wr.get("official_suffixes", []) or []:
        suffix = suffix.strip().lower()
        if suffix and (site == suffix.lstrip(".") or site.endswith(suffix)):
            return True
    for domain in wr.get("official_domains", []) or []:
        domain = domain.strip().lower()
        if domain and (site == domain or site.endswith("." + domain)):
            return True
    return False


# ---------------------------------------------------------------------------
# The decision: pure code, no model
# ---------------------------------------------------------------------------

def judge(question: dict, answer: dict | None, grounding: dict,
          today: dt.date, settings: dict) -> tuple[str, str, dict]:
    """
    Turn the reader's answer into an action.

    Returns (action, why, facts). `action` is one of:
      resolved_yes  every check passed
      pending       the reader says it happened, but a check failed -> you decide
      not_yet       nothing to act on
      failed        no usable answer at all

    Kept free of I/O so it can be tested exhaustively without an API key.
    """
    wr = settings.get("web_resolution", {}) or {}
    min_sites = int(wr.get("min_independent_sources", 2))

    sites = []
    for s in (grounding or {}).get("sources", []) or []:
        site = _site(s)
        if site and site not in sites:
            sites.append(site)
    official = any(_is_official(s, settings) for s in sites)
    facts = {"sites": sites, "official": official}

    if not isinstance(answer, dict):
        return "failed", "no parseable answer from the search model", facts

    # Check 1: the search really happened. An ungrounded answer is the model
    # speaking from memory, which is precisely what we cannot use here.
    if not (grounding or {}).get("fired") or not sites:
        return ("not_yet",
                "the search did not fire (no sources in Google's record), so "
                "the answer is from memory and is ignored", facts)

    # Check 2: the reader says it happened, with a date.
    if not _truthy(answer.get("happened")):
        status = answer.get("status") or "not happened"
        return "not_yet", f"reader: {status}", facts

    event_date = _date(answer.get("event_date"))
    if event_date is None:
        return ("pending",
                "reader says it happened but gave no usable event date", facts)
    facts["event_date"] = event_date.isoformat()

    # Check 3: the date sits inside the question's life.
    deadline = _date(question.get("deadline"))
    created = _date(question.get("created"))
    if event_date > today:
        return ("not_yet",
                f"event date {event_date} is in the future -- announced, not "
                "yet happened", facts)
    if deadline and event_date > deadline:
        return ("not_yet",
                f"event date {event_date} is after the deadline {deadline}; "
                "that is not a YES, and the deadline lapse will handle it",
                facts)
    if created and event_date < created:
        return ("pending",
                f"event date {event_date} is BEFORE the question was created "
                f"({created}) -- the question looks born resolved, which is a "
                "question defect for you to judge", facts)

    # Check 4: independent corroboration, or one official source.
    if official:
        return "resolved_yes", "official source in the search record", facts
    if len(sites) >= min_sites:
        return ("resolved_yes",
                f"{len(sites)} different sites in the search record", facts)
    return ("pending",
            f"only {len(sites)} site(s) in the search record and none official "
            f"(needs {min_sites}, or one official)", facts)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def _checked_today(today_iso: str) -> set[str]:
    """Questions already given a real answer today -- once a day is enough."""
    done = set()
    for row in store.read_rows(config.WEB_CHECKS_CSV):
        if row.get("date") == today_iso and row.get("action") not in ("failed", "skipped"):
            done.add(row.get("question_id", ""))
    return done


def _candidates(today: dt.date) -> list[dict]:
    """
    Open questions closest-deadline first, then the absence watch.

    Any question you have written about in config/resolutions.csv is left
    alone for good. Without this, `reopen` on a wrong web YES would put the
    question back to open, and the next day's check would resolve it YES again
    -- every day, forever. Your word is final here, as it is for the watch.
    """
    far = dt.date(9999, 12, 31)
    human = {(r.get("question_id") or "").strip()
             for r in store.read_resolution_overrides()}

    def eligible(q):
        return (q.get("id") not in human
                and q.get("outcome_set_by") != "human")

    open_qs = sorted((q for q in store.open_questions() if eligible(q)),
                     key=lambda q: _date(q.get("deadline")) or far)
    watched = sorted((q for q in store.watched_questions() if eligible(q)),
                     key=lambda q: _date(q.get("deadline")) or far)
    return open_qs + watched


def _grounding_ready(router) -> bool:
    chain = (getattr(router, "chains", {}) or {}).get(TASK) or []
    return any(m in getattr(router, "grounding_models", set()) for m in chain)


def run(router, settings: dict, today: dt.date, log, dry_run: bool = False,
        real_today: dt.date | None = None) -> dict:
    """
    Check every eligible question once. Returns a small summary dict.

    Never raises for an API problem: every failure is logged and the newspaper
    screen, lapses and forecasting carry on regardless.
    """
    summary = {"checked": 0, "resolved": [], "pending": [], "unchecked": 0}
    wr = settings.get("web_resolution", {}) or {}
    log.sub("Web resolution (Google Search, YES only)")

    if not wr.get("enabled", True):
        log.info("  disabled in config/settings.yaml (web_resolution.enabled)")
        return summary

    real_today = real_today or dt.date.today()
    if today != real_today:
        log.info(
            f"  skipped: the run date {today} is not the real today "
            f"({real_today}). The web would show events from after the run "
            "date."
        )
        return summary

    if not _grounding_ready(router):
        log.warn(
            "Web resolution has no search-capable model: the 'web_resolve' "
            "chain in config/models.yaml lists no model that is also in "
            "grounding_models. Resolution falls back to the papers alone."
        )
        return summary

    cap = int(wr.get("max_checks_per_run", 30))
    done_today = _checked_today(today.isoformat())
    queue = [q for q in _candidates(today) if q.get("id") not in done_today]
    if done_today:
        log.info(f"  {len(done_today)} question(s) already checked today; "
                 "not re-checked")
    if not queue:
        log.info("  nothing to check")
        _rebuild_pending(today, log, dry_run)
        return summary

    for i, q in enumerate(queue):
        if summary["checked"] >= cap:
            summary["unchecked"] = len(queue) - i
            log.info(f"  per-run cap of {cap} reached; "
                     f"{summary['unchecked']} left for the next run")
            break

        qid = q.get("id", "")
        prompt = WEB_PROMPT.format(
            today=today.isoformat(),
            question=q.get("question", ""),
            criteria=q.get("resolution_criteria", ""),
            deadline=q.get("deadline", ""),
            resolves_on=resolves_on_text(q),
        )
        # Lots of room: 2.5 Flash spends part of its output budget thinking,
        # and a truncated answer costs a retry out of a 20-a-day allowance.
        answer, model = router.generate(
            TASK, prompt, temperature=0.1, max_output_tokens=4096,
            grounded=True,
        )
        grounding = dict(getattr(router, "last_grounding", {}) or {})

        if answer is None:
            # Every search model is out (quota) or erroring. Stop here rather
            # than burn through the queue logging the same failure.
            summary["unchecked"] = len(queue) - i
            log.warn(
                f"Web resolution stopped at {qid}: no search model answered "
                f"({router.stats.last_error or 'unknown error'}). "
                f"{summary['unchecked']} question(s) wait for the next run."
            )
            if not dry_run:
                _record(today, q, "failed", "no search model answered",
                        {}, grounding, model or "", {})
            break

        summary["checked"] += 1
        action, why, facts = judge(q, answer, grounding, today, settings)
        if not dry_run:
            _record(today, q, action, why, answer, grounding, model, facts)

        if action == "resolved_yes":
            _apply_yes(q, facts["event_date"], answer, why, today, log, dry_run)
            summary["resolved"].append(qid)
        elif action == "pending":
            summary["pending"].append(qid)
            log.flag(
                f"{qid}: the web check says this HAPPENED, but it needs your "
                f"call -- {why}.\n"
                f"    Question: {q.get('question','')}\n"
                f"    Event:    {answer.get('event','')} "
                f"({answer.get('event_date','no date')})\n"
                f"    Sources:  {', '.join(facts.get('sites', [])) or 'none'}\n"
                f"    If you agree, paste into config/resolutions.csv:\n"
                f"      {_paste_row(qid, answer)}"
            )
        else:
            log.info(f"  {qid}: {action} -- {answer.get('reason') or why}")

    _rebuild_pending(today, log, dry_run)
    log.info(
        f"  web checks this run: {summary['checked']}; "
        f"resolved YES: {len(summary['resolved'])}; "
        f"waiting for you: {len(summary['pending'])}"
    )
    return summary


def _apply_yes(q, event_date, answer, why, today, log, dry_run):
    qid = q.get("id", "")
    was_lapsed = q.get("status") == "resolved"
    if dry_run:
        log.info(f"  {qid}: WOULD resolve YES on {event_date} (dry run)")
        return
    resolve.resolve_now(
        q, "1", event_date, today, log, basis="web_confirmed",
        how=f"web check -- {why}",
    )
    log.info(f"    event: {answer.get('event','')}")
    log.info(f"    evidence: {answer.get('evidence','')}")
    if was_lapsed:
        log.flag(
            f"{qid}: LATE EVIDENCE FOUND ON THE WEB. This question had lapsed "
            f"as NO; it is now YES on {event_date}, and every score has been "
            "recomputed from source."
        )
    log.info(
        f"    Wrong? Add  {qid},reopen,,web check was wrong  (or {qid},0,...) "
        "to config/resolutions.csv and it is undone on the next run."
    )


def _paste_row(qid: str, answer: dict) -> str:
    event = (answer.get("event") or "").replace(",", ";").replace("\n", " ")
    return f"{qid},1,{answer.get('event_date','')},web: {event[:120]}"


def _record(today, q, action, why, answer, grounding, model, facts):
    answer = answer or {}
    store.append_row(config.WEB_CHECKS_CSV, {
        "date": today.isoformat(),
        "question_id": q.get("id", ""),
        "action": action,
        "why": why,
        "happened": answer.get("happened", ""),
        "status": answer.get("status", ""),
        "event": answer.get("event", ""),
        "event_date": answer.get("event_date", ""),
        "evidence": answer.get("evidence", ""),
        "sources": "; ".join(facts.get("sites", [])
                             or (grounding or {}).get("sources", []) or []),
        "official_source": "yes" if facts.get("official") else "no",
        "queries": "; ".join((grounding or {}).get("queries", []) or []),
        "model": model or "",
    })


def _rebuild_pending(today, log, dry_run) -> None:
    """
    Rewrite data/pending_resolutions.csv from the web-check log.

    A question is pending if its MOST RECENT web check said `pending`, it has
    not since been resolved by the system, and you have not written anything
    for it in config/resolutions.csv. Derived, never edited by hand.
    """
    if dry_run:
        return
    human = {(r.get("question_id") or "").strip()
             for r in store.read_resolution_overrides()}
    latest: dict[str, dict] = {}
    first_pending: dict[str, str] = {}
    for row in store.read_rows(config.WEB_CHECKS_CSV):
        qid = row.get("question_id", "")
        if row.get("action") in ("failed", "skipped"):
            continue
        latest[qid] = row
        if row.get("action") == "pending":
            first_pending.setdefault(qid, row.get("date", ""))
        else:
            first_pending.pop(qid, None)

    rows = []
    for qid, row in sorted(latest.items()):
        if row.get("action") != "pending" or qid in human:
            continue
        q = store.question_by_id(qid) or {}
        if q.get("outcome_set_by") == "system" and q.get("resolution_basis") == "web_confirmed":
            continue
        rows.append({
            "question_id": qid,
            "question": q.get("question", ""),
            "first_flagged": first_pending.get(qid, row.get("date", "")),
            "last_checked": row.get("date", ""),
            "why": row.get("why", ""),
            "event": row.get("event", ""),
            "event_date": row.get("event_date", ""),
            "evidence": row.get("evidence", ""),
            "sources": row.get("sources", ""),
            "paste_into_resolutions_csv": _paste_row(qid, row),
        })
    store.rewrite(config.PENDING_RESOLUTIONS_CSV, rows)
    if rows:
        log.info(f"  {len(rows)} question(s) in data/pending_resolutions.csv "
                 "waiting for your decision")
