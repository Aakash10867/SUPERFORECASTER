"""
Web resolution: code searches Google News, one model reads, code decides.

HISTORY
-------
v16 added this so that resolution would not depend on the papers alone: Q0003
(the Treasury buyback question) sat open at 72% for weeks after Treasury had
bought a full $6bn in a single operation on 10 September, because the papers
that would have reported the completed purchase were never uploaded.

v16/v17 searched through Gemini's built-in Google Search. On these keys that
never worked: the only models with a free search allowance (2.5 Flash and
2.5 Flash-Lite) return HTTP 404 for NEW API keys, and every 3.x model has a
search allowance of zero. From 1 to 3 October the web check made no searches
at all -- Q0003 was never even looked up -- and the failure was a quiet warning
in the middle of the log. v18 replaces the search and makes failure loud.

v18's first live probe (3 Oct) proved the search worked -- 25 real articles,
several reporting the completed operation ("buyback results fuel sell-off",
"exceeding $5 billion, fell short of its upper limit") -- but the reader
called it "announced, not yet happened". Its queries were topic-only, it read
"fell short" as "did not happen", and it held out for an "official"
confirmation it could never see. v19 fixes all three.

HOW ONE CHECK RUNS (v19)
------------------------
  1. A cheap call fills a FORM: actor, the act in the past tense in headline
     words, object. Code assembles three queries of a FIXED SHAPE from it:
     topic, completion, and one standard phrase for the kind of act.
  2. news_search.py runs them against Google News (US and India editions) and
     returns real articles: headline, publisher, site, publication date.
  3. One model (3.x Flash Lite) reads that numbered list and answers: has the
     act ACTUALLY HAPPENED, on what date, and WHICH ARTICLES show it? A report
     of the act's results proves it happened, even a disappointing one; news
     reports of an official act count as official.
  4. FOLLOW THE CLUE: if it has not happened but was scheduled for a date that
     has passed (read today, or stored on the question earlier as
     `awaiting`), ONE more search chases that act's results and the reader
     looks again. Still nothing -> pending for you: "was due on X, no report
     it happened". A future date is stored as `awaiting`, and the question
     jumps the queue once that date passes.
  5. Plain code decides whether to act on the answer.

Searching is about recall: a mistake costs a missed find, and step 4 catches
it. Deciding is about precision: a false YES corrupts the record. So the
search is free to use the act's own words, and the decision stays fixed
rules.

A second model was considered and rejected (same family, same evidence, same
blind spots -- the "prompt variation is not a crowd" principle). The guard is
code instead.

YES ONLY
--------
Never resolves NO. NO comes only from the deadline lapse in resolve.py.

THE CODE CHECKS (all must pass for an automatic YES)
----------------------------------------------------
  1. The search returned articles.
  2. The reader says it happened, with a parseable event date.
  3. created <= event date <= min(today, deadline).
       after today     -> announced, not yet happened: stays open
       after deadline  -> not a YES; the lapse rule handles it
       before created  -> born resolved: a question defect, goes to you
  4. The reader must CITE articles by number, and only cited articles
     published ON OR AFTER the event date count. An article from before the
     event can only have reported it as planned, never as done -- this is the
     mechanical guard against reading "will buy" as "bought".
  5. Those counted articles come from at least two DIFFERENT publishers, or one
     official site (a government domain). Publishers and sites are taken from
     the feed, never from the model's text.

A reader that says "happened" but fails 3, 4 or 5 sends the question to
data/pending_resolutions.csv with a ready-made row for config/resolutions.csv.

Resolution is dated to the EVENT, not to the day of the check.

BUDGET
------
Two or three Flash Lite calls per question per day (form + read, plus a
second read only when a follow-up fires), from a 500/day allowance per model
per key. The news search itself has no quota. Each
question is still checked at most once a day; a per-run cap remains.

BACKFILL GUARD
--------------
With --date in the past the web shows that date's future, so web resolution
runs only when the run date is the real today.
"""

from __future__ import annotations

import datetime as dt

from . import config, news_search, resolve, store

TASK = "web_resolve"
QUERY_TASK = "web_query"

# v19: the model fills a FORM; code assembles the queries. The words belong
# to the act (a buyback is "bought", a nominee "confirmed", a scheme
# "notified"), so no fixed word list could find them -- but the SHAPE of the
# queries is fixed, so it is predictable and testable. Freedom where a
# mistake only costs a missed find (and the follow-up tripwire catches it);
# fixed rules where a mistake corrupts the record (the decision, in judge()).
QUERY_PROMPT = """Fill in this form for a news search that would find \
reporting on whether the act below has happened. Short plain words, as a \
person would type into Google News. No dates, no quotation marks.

QUESTION: {question}
RESOLUTION CRITERIA: {criteria}

  actor     -- who must act (e.g. "US Treasury", "RBI", "US Senate")
  act_past  -- the act in the PAST TENSE, in the words a headline would use \
when reporting it DONE (e.g. "bought back", "confirms", "signed", \
"notified", "struck", "imposes")
  object    -- what it is done to, with any key number (e.g. "long-dated \
bonds $4 billion", "Heidi Overton FDA", "Ganga water treaty")

Return JSON only: {{"actor": "...", "act_past": "...", "object": "..."}}"""

WEB_PROMPT = """Below are news articles found by a search just now. Using ONLY \
these articles, decide ONE thing: has this forecasting question ALREADY \
RESOLVED YES?

TODAY: {today}
QUESTION: {question}
RESOLUTION CRITERIA: {criteria}
DEADLINE: {deadline}
WHAT RESOLVES IT: {resolves_on}
{lead_hint}
THE RULE
- YES requires that the event the criteria describe has ACTUALLY HAPPENED, on \
or before today and on or before the deadline.
- An announcement that something WILL happen is NOT resolution, however \
official or certain it sounds. Plans, proposals, drafts, expectations, votes \
still pending, "set to", "to buy", "expected to", "will take effect on <future \
date>" are all NOT YET. They make YES more likely; they do not make it happen.
- A report of the act's RESULTS or OUTCOME proves it happened, even when the \
outcome disappointed. "Buyback results fuel sell-off", "purchase fell short of \
its upper limit", "vote passed narrowly", "strike disrupts banks" all report \
an act that took place. Judge whether it HAPPENED, not whether it succeeded.
- Where the criteria say "officially" (officially confirms, announces, \
implements), reliable news reports OF the official act count -- you will not \
see the official record itself.
- "WHAT RESOLVES IT" above says which act counts. Follow it exactly. Read the \
criteria literally, every other clause.
- If the act happened, give the date it HAPPENED -- not the date it was first \
announced, unless the announcement is the act.
- Cite the articles that show it HAPPENED, by their numbers.
- Use nothing but these articles. If they do not show it clearly, answer \
"happened": false. Never conclude that the question resolves NO.
- LEAD: if the articles show the resolving act was announced or scheduled for \
a SPECIFIC date but do not show it took place, give that date and a short \
description of the act. Otherwise leave both empty.

ARTICLES (number | published | publisher | headline -- snippet):
{articles}

Return JSON only, no prose before or after:
{{
  "happened": true or false,
  "status": "happened | announced_not_yet_happened | in_progress | no_relevant_news",
  "event": "what specifically happened (or what was announced)",
  "event_date": "YYYY-MM-DD, the date it actually happened; empty if it has not",
  "supporting_articles": [numbers of the articles that report it as done],
  "evidence": "one or two sentences from those articles that establish it",
  "lead_date": "YYYY-MM-DD the act was scheduled for, if announced but not shown done; else empty",
  "lead_what": "short description of that scheduled act; else empty",
  "reason": "one line, always filled in"
}}"""

LEAD_HINT = """THIS ACT WAS DUE ON {date}: {what}. Look specifically for \
reports that it took place -- results, outcomes, reactions to it having \
happened.
"""

# The only fixed vocabulary: one phrase per kind of resolving act. These words
# really are standard across acts of that kind.
TYPE_PHRASE = {
    "announced": "{actor} announces {object}",
    "in_effect": "{object} takes effect",
    "carried_out": "{actor} {act_past}",
}


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

def _numbers(value) -> list[int]:
    """The reader's cited article numbers, however it formatted them."""
    if isinstance(value, (int, float)):
        value = [value]
    if isinstance(value, str):
        value = [v for v in value.replace(";", ",").split(",")]
    out = []
    for v in value or []:
        try:
            n = int(str(v).strip().strip("[]#"))
        except ValueError:
            continue
        if n not in out:
            out.append(n)
    return out


def judge(question: dict, answer: dict | None, articles: list[dict],
          today: dt.date, settings: dict) -> tuple[str, str, dict]:
    """
    Turn the reader's answer into an action.

    `articles` is the numbered list the reader saw (1-based), straight from
    news_search -- the only place publishers, sites and dates come from.

    Returns (action, why, facts). `action` is one of:
      resolved_yes  every check passed
      pending       the reader says it happened, but a check failed -> you decide
      not_yet       nothing to act on
      failed        no usable answer at all

    Kept free of I/O so it can be tested exhaustively without a network.
    """
    wr = settings.get("web_resolution", {}) or {}
    min_sites = int(wr.get("min_independent_sources", 2))
    facts = {"sites": [], "official": False, "cited": []}

    # Check 1: the search found something to read.
    if not articles:
        return "not_yet", "the news search found no articles", facts
    if not isinstance(answer, dict):
        return "failed", "no parseable answer from the reader", facts

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

    # Check 4: cited articles, published on or after the event.
    cited = [articles[n - 1] for n in _numbers(answer.get("supporting_articles"))
             if 1 <= n <= len(articles)]
    facts["cited"] = cited
    if not cited:
        return ("pending",
                "reader says it happened but cited none of the articles found",
                facts)
    after = [a for a in cited if (_date(a.get("date")) or dt.date.min) >= event_date]
    if not after:
        return ("not_yet",
                f"every cited article was published before the event date "
                f"{event_date}, so it can only have reported the act as "
                "planned, not done", facts)

    # Check 5: independent publishers, or one official site.
    sites = []
    for a in after:
        key = _site(a.get("site") or "") or (a.get("publisher") or "").strip().lower()
        if key and key not in sites:
            sites.append(key)
    official = any(_is_official(_site(a.get("site") or ""), settings) for a in after)
    facts.update(sites=sites, official=official)
    if official:
        return "resolved_yes", "official source among the cited articles", facts
    if len(sites) >= min_sites:
        return ("resolved_yes",
                f"{len(sites)} different publishers report it done", facts)
    return ("pending",
            f"only {len(sites)} publisher(s) report it done and none official "
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

    def order(q):
        # v19: a question whose awaited act has fallen due goes first -- that
        # is the day its follow-up search is most likely to find the report.
        due = _lead({}, q)
        return (0 if due and due[0] <= today else 1,
                _date(q.get("deadline")) or far)

    open_qs = sorted((q for q in store.open_questions() if eligible(q)),
                     key=order)
    watched = sorted((q for q in store.watched_questions() if eligible(q)),
                     key=lambda q: _date(q.get("deadline")) or far)
    return open_qs + watched


def _ready(router) -> bool:
    chains = getattr(router, "chains", {}) or {}
    return bool(chains.get(TASK))


def _alert(log, text: str) -> None:
    """Top-of-log red alert where supported; a flag otherwise (tests, probes)."""
    if hasattr(log, "alert"):
        log.alert(text)
    else:
        log.flag(text)


def _clean(text, words: int = 10) -> str:
    text = " ".join(str(text or "").replace('"', " ").split())
    return " ".join(text.split()[:words])


def _fallback_query(q: dict) -> str:
    text = q.get("question", "")
    for junk in ("Will the ", "Will ", "?"):
        text = text.replace(junk, " ")
    return _clean(text, 14)


def _form_for(q: dict, router) -> dict:
    """The model fills actor / act_past / object. Empty dict if it fails."""
    result, _model = router.generate(
        QUERY_TASK,
        QUERY_PROMPT.format(question=q.get("question", ""),
                            criteria=q.get("resolution_criteria", "")),
        temperature=0.2, max_output_tokens=512,
    )
    if not isinstance(result, dict):
        return {}
    form = {k: _clean(result.get(k), 8) for k in ("actor", "act_past", "object")}
    return form if form["actor"] and form["object"] else {}


def build_queries(form: dict, q: dict) -> list[str]:
    """
    Fixed shape, the act's own words (v19):
      1. topic       actor + object
      2. completion  actor + act_past + object
      3. by type     one standard phrase for the kind of act (TYPE_PHRASE)
    Falls back to the question text if the form is missing.
    """
    if not form:
        return [_fallback_query(q)]
    kind = (q.get("resolves_on") or "").strip().lower()
    template = TYPE_PHRASE.get(kind, TYPE_PHRASE["carried_out"])
    out = []
    for text in (
        f"{form['actor']} {form['object']}",
        f"{form['actor']} {form.get('act_past','')} {form['object']}",
        template.format(**{"actor": form["actor"], "object": form["object"],
                           "act_past": form.get("act_past", "")}),
    ):
        text = _clean(text, 14)
        if text and text not in out:
            out.append(text)
    return out


def followup_queries(form: dict, lead_what: str) -> list[str]:
    """The one extra search when a scheduled date has passed (v19)."""
    what = _clean(lead_what, 10)
    out = [f"{what} results"] if what else []
    if form:
        out.append(_clean(f"{form['actor']} {form.get('act_past','')} "
                          f"{form['object']}", 14))
    return [x for x in out if x][:2]


def _lead(answer: dict, q: dict):
    """
    (date, what) of a scheduled resolving act: from today's reading if it
    found one, otherwise from the date stored on the question earlier.
    """
    d = _date((answer or {}).get("lead_date"))
    what = _clean((answer or {}).get("lead_what"), 14)
    if d and what:
        return d, what
    stored = (q.get("awaiting") or "").strip()
    if ":" in stored:
        sd, _, swhat = stored.partition(":")
        sd = _date(sd)
        if sd and swhat.strip():
            return sd, swhat.strip()
    return None


def _article_block(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        snippet = f" -- {a['snippet']}" if a.get("snippet") else ""
        lines.append(f"[{i}] {a.get('date','')} | {a.get('publisher','')} | "
                     f"{a.get('title','')}{snippet}")
    return "\n".join(lines)


def _read(q, articles, today, router, lead=None):
    hint = LEAD_HINT.format(date=lead[0].isoformat(), what=lead[1]) if lead else ""
    return router.generate(
        TASK,
        WEB_PROMPT.format(
            today=today.isoformat(),
            question=q.get("question", ""),
            criteria=q.get("resolution_criteria", ""),
            deadline=q.get("deadline", ""),
            resolves_on=resolves_on_text(q),
            lead_hint=hint,
            articles=_article_block(articles),
        ),
        temperature=0.1, max_output_tokens=2048,
    )


def check_one(q: dict, router, settings: dict, today: dt.date) -> dict:
    """
    One full check -- form, search, read, decide, and at most ONE follow-up
    -- with nothing written. Raises news_search.SearchUnavailable if the first
    search cannot run at all. Returns everything needed to record, apply or
    print the result.

    FOLLOW THE CLUE (v19). If the reading finds no completed act but the act
    was scheduled for a date that has now passed -- read today, or stored on
    the question from an earlier day -- run ONE more search aimed at that act,
    and read again. If that still shows nothing, the question goes to you:
    "was due on X, no report it happened". That is the Q0003 pattern, and it
    turns a silent miss into a prompt. A future date is returned as
    `awaiting`, to be stored on the question.
    """
    limit = int((settings.get("web_resolution", {}) or {}).get("max_articles", 40))
    form = _form_for(q, router)
    queries = build_queries(form, q)
    articles, problems = news_search.search(queries, limit=limit)
    r = {"form": form, "queries": queries, "articles": articles,
         "problems": problems, "answer": {}, "model": "",
         "followup": None, "awaiting": None}

    if articles:
        answer, model = _read(q, articles, today, router)
        action, why, facts = judge(q, answer, articles, today, settings)
        r.update(answer=answer if isinstance(answer, dict) else {},
                 model=model or "")
    else:
        action, why, facts = judge(q, None, [], today, settings)
    r.update(action=action, why=why, facts=facts)
    if action != "not_yet":
        return r

    lead = _lead(r["answer"], q)
    if lead is None:
        return r
    deadline = _date(q.get("deadline")) or dt.date.max
    if lead[0] > today:
        r["awaiting"] = lead
        return r
    if lead[0] > deadline:
        return r                           # too late to be a YES; lapse handles it

    # The one follow-up.
    fq = followup_queries(form, lead[1])
    try:
        more, p2 = news_search.search(fq, limit=limit)
    except news_search.SearchUnavailable as exc:
        r["problems"] = problems + [f"follow-up search: {exc}"]
        return r
    merged = news_search.merge(articles, more, limit=limit)
    answer2, model2 = _read(q, merged, today, router, lead=lead)
    action2, why2, facts2 = judge(q, answer2, merged, today, settings)
    r["followup"] = {"lead": lead, "queries": fq, "found": len(more)}
    r.update(articles=merged, problems=problems + p2,
             answer=answer2 if isinstance(answer2, dict) else {},
             model=model2 or r["model"])
    if action2 == "not_yet":
        action2 = "pending"
        why2 = (f"was due on {lead[0]} ({lead[1]}); a follow-up search found "
                "no report that it happened -- please check")
    r.update(action=action2, why=why2, facts=facts2)
    return r


def run(router, settings: dict, today: dt.date, log, dry_run: bool = False,
        real_today: dt.date | None = None) -> dict:
    """
    Check every eligible question once. Returns a small summary dict.

    Never raises for a search or API problem: the failure becomes a red alert
    at the top of the run log, and the newspaper screen, lapses and
    forecasting carry on regardless.
    """
    summary = {"checked": 0, "resolved": [], "pending": [], "unchecked": 0}
    wr = settings.get("web_resolution", {}) or {}
    log.sub("Web resolution (Google News search, YES only)")

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

    if not _ready(router):
        _alert(log, "WEB CHECK DID NOT RUN: no 'web_resolve' chain in "
                    "config/models.yaml. Resolution is relying on the papers "
                    "alone.")
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
        try:
            r = check_one(q, router, settings, today)
        except news_search.SearchUnavailable as exc:
            summary["unchecked"] = len(queue) - i
            _alert(log,
                   f"WEB CHECK DID NOT RUN: the news search could not be "
                   f"reached ({exc}). {summary['unchecked']} question(s) were "
                   "not checked; resolution is relying on the papers alone "
                   "this run.")
            if not dry_run:
                _record(today, q, "failed", f"news search unreachable: {exc}",
                        {}, [], "", {})
            break

        if r["action"] == "failed":
            # The reader model failed. If that is quota or a dead model it
            # will fail for every question, so stop and say so loudly.
            summary["unchecked"] = len(queue) - i
            _alert(log,
                   f"WEB CHECK STOPPED at {qid}: the reading model gave no "
                   f"answer ({router.stats.last_error or 'unknown error'}). "
                   f"{summary['unchecked']} question(s) were not checked.")
            if not dry_run:
                _record(today, q, "failed", r["why"], {}, r["queries"],
                        r["model"], r["facts"])
            break

        summary["checked"] += 1
        answer, facts, why = r["answer"], r["facts"], r["why"]
        queries = r["queries"] + ((r["followup"] or {}).get("queries") or [])
        was_pending = _last_action(qid) == ("pending", why)
        if r["followup"]:
            lead = r["followup"]["lead"]
            log.info(f"  {qid}: due on {lead[0]} ({lead[1]}) -- follow-up "
                     f"search found {r['followup']['found']} more article(s)")
        if r["awaiting"] and not dry_run:
            date, what = r["awaiting"]
            store.update_question(qid, {"awaiting": f"{date.isoformat()}: {what}"})
            log.info(f"  {qid}: scheduled for {date} ({what}); will look for "
                     "it specifically once that date passes")
        if r["problems"]:
            log.info(f"  {qid}: {len(r['problems'])} of the search fetches "
                     f"failed (others worked): {r['problems'][0]}")
        if not dry_run:
            _record(today, q, r["action"], why, answer, queries,
                    r["model"], facts)

        if r["action"] == "resolved_yes":
            _apply_yes(q, facts["event_date"], answer, why, today, log, dry_run)
            summary["resolved"].append(qid)
        elif r["action"] == "pending" and was_pending:
            # Same pending reason as the last check: still listed in
            # data/pending_resolutions.csv, but not flagged again every day.
            summary["pending"].append(qid)
            log.info(f"  {qid}: still waiting for you -- {why}")
        elif r["action"] == "pending":
            summary["pending"].append(qid)
            log.flag(
                f"{qid}: the web check says this HAPPENED, but it needs your "
                f"call -- {why}.\n"
                f"    Question: {q.get('question','')}\n"
                f"    Event:    {answer.get('event','')} "
                f"({answer.get('event_date','no date')})\n"
                f"    Cited:    "
                + ("; ".join(f"{a['date']} {a['publisher']}: {a['title'][:80]}"
                             for a in facts.get("cited", [])) or "none")
                + f"\n    If you agree, paste into config/resolutions.csv:\n"
                f"      {_paste_row(qid, answer)}"
            )
        else:
            log.info(f"  {qid}: {r['action']} -- {why} "
                     f"({len(r['articles'])} articles; "
                     f"queries: {' | '.join(r['queries'])})")

    _rebuild_pending(today, log, dry_run)
    log.info(
        f"  web checks this run: {summary['checked']}; "
        f"resolved YES: {len(summary['resolved'])}; "
        f"waiting for you: {len(summary['pending'])}"
    )
    return summary


def probe(router, settings: dict, qid: str, today: dt.date, out=print) -> str:
    """
    Run ONE question's web check end to end and print everything -- queries,
    every article found, the reader's answer, the code's decision. Writes
    nothing: no question changes, no web_checks row.

    Run from GitHub (Actions -> Run workflow -> web_probe = Q0003), because
    that is where the real network is. Returns the action.
    """
    q = store.question_by_id(qid)
    if q is None:
        out(f"No question {qid} in data/questions.csv")
        return "failed"
    out(f"PROBE {qid}: {q.get('question','')}")
    out(f"  created {q.get('created')}  deadline {q.get('deadline')}  "
        f"resolves_on {q.get('resolves_on') or '(blank -> carried_out)'}")
    try:
        r = check_one(q, router, settings, today)
    except news_search.SearchUnavailable as exc:
        out(f"\n  SEARCH UNREACHABLE: {exc}")
        return "failed"
    out(f"\n  form: {r.get('form') or '(failed; question text used)'}")
    out(f"  queries: {r['queries']}")
    if r["problems"]:
        out(f"  fetch problems: {r['problems']}")
    out(f"\n  {len(r['articles'])} articles found:")
    for i, a in enumerate(r["articles"], 1):
        out(f"   [{i:2d}] {a['date']}  {a['publisher'][:24]:24s}  {a['title'][:100]}")
    if r.get("followup"):
        f = r["followup"]
        out(f"\n  FOLLOW-UP: due on {f['lead'][0]} ({f['lead'][1]}); queries "
            f"{f['queries']} found {f['found']} more article(s); list above "
            "is the merged set the second reading saw")
    if r.get("awaiting"):
        out(f"\n  AWAITING: {r['awaiting'][0]} ({r['awaiting'][1]}) -- would be "
            "stored on the question")
    out(f"\n  reader ({r['model'] or 'none'}): {r['answer']}")
    out(f"\n  DECISION: {r['action']} -- {r['why']}")
    if r["facts"].get("sites"):
        out(f"  publishers counted: {r['facts']['sites']}")
    return r["action"]


def _last_action(qid: str):
    """(action, why) of this question's most recent recorded web check."""
    last = None
    for row in store.read_rows(config.WEB_CHECKS_CSV):
        if row.get("question_id") == qid and row.get("action") not in ("failed", "skipped"):
            last = (row.get("action"), row.get("why"))
    return last


def _apply_yes(q, event_date, answer, why, today, log, dry_run):
    qid = q.get("id", "")
    was_lapsed = q.get("status") == "resolved"
    if dry_run:
        log.info(f"  {qid}: WOULD resolve YES on {event_date} (dry run)")
        return
    if q.get("awaiting"):
        store.update_question(qid, {"awaiting": ""})
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


def _record(today, q, action, why, answer, queries, model, facts):
    answer = answer or {}
    cited = facts.get("cited", []) or []
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
        "sources": "; ".join(facts.get("sites", []) or []),
        "official_source": "yes" if facts.get("official") else "no",
        "queries": "; ".join(queries or []),
        "cited_articles": " || ".join(
            f"{a.get('date','')} {a.get('publisher','')}: {a.get('title','')}"
            for a in cited),
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
