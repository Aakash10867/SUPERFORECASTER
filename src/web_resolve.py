"""
Web resolution: headlines screen, full text decides, code verifies every quote.

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

v19-v21 tuned the headline reader three times against Q0003 and it still
could not resolve: headlines carry no dates, "fell short of its cap" reads
like "did not happen", and one crypto headline cannot be judged. Counting
"two sources" was a stand-in for judging one source well. v22 stops tuning
the headline step and reads the ARTICLES.

HOW ONE CHECK RUNS (v22)
------------------------
  1. HEADLINES (Google News RSS, free, every question every day). A form
     (actor, act in past tense, object) builds fixed-shape queries; one cheap
     read ROUTES only: did a headline say it happened, is there a dated lead?
     Headlines never resolve anything.
  2. FULL TEXT (Tavily, 1 credit) when it matters:
       headline  a headline says it happened  -> read from question creation
       due       an announced ACT's date has passed
       occasion  a meeting/vote/decision date has passed (outcome open)
       sweep     no full-text read for 7 days (catches what headlines miss)
  3. The full-text reader must QUOTE, word for word, the sentence showing the
     act done, for every source that shows it.
  4. CODE (judge_deep) checks every quote really appears in that source and
     applies the EVIDENCE TIERS:
       A  quote verified in an official (government) page    -> resolves
       B  quote verified in a full article                   -> resolves with
                                                                one more publisher
       C  quote verified in a headline                       -> corroboration only
     A source dated before the event never counts ("will buy" stories); an
     undated full article counts only if official; quotes under 8 words (5
     for headlines) do not count; the same publisher counts once.

Searching is recall; deciding is precision. The search may use the act's own
words and look again; the decision is fixed rules over verified evidence.
A second model was considered and rejected (same family, same blind spots).

YES ONLY
--------
Never resolves NO. NO comes only from the deadline lapse in resolve.py.

OTHER GUARDS
------------
  created <= event date <= min(today, deadline); before created -> born
  resolved, goes to you. Dated to the EVENT (first occurrence), not the check.
  Your entry in config/resolutions.csv is final: such questions are never
  checked again.

BUDGET
------
Flash Lite: 2-3 calls per question per day (form, headline read, full-text
read when triggered), from 500/day. Tavily: one credit per full-text read;
with ~16 questions roughly 100-300 a month of the free 1,000; sweeps pause
below a reserve, everything stops (red alert) near zero.

FAILURE IS LOUD
---------------
No key, a bad key, no credits, a rate limit, an unreachable search or a dead
reading model each put a red "SOMETHING DID NOT RUN" box at the top of the
log and a red annotation on the Actions page. test_guards.py sabotages each
safety guard in turn and confirms the tests catch it.

BACKFILL GUARD
--------------
With --date in the past the web shows that date's future, so web resolution
runs only when the run date is the real today.
"""

from __future__ import annotations

import datetime as dt

from . import config, deep_search, news_search, resolve, store

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
  act_past  -- the act the RESOLUTION CRITERIA require to have happened, in \
the PAST TENSE, in the words a headline would use when reporting it DONE \
(e.g. "bought back", "confirms", "signed", "notified", "struck", \
"imposes"). The act itself -- not a plan, policy change or decision about \
it: for a question about buyback purchases reaching a size, the act is \
"bought", not "increased the program".
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
announced, unless the announcement is the act. If it has happened MORE THAN \
ONCE (repeated operations, several votes), give the date of the FIRST time.
- Cite EVERY article that shows it happened, by number -- all of them, not \
just the clearest one.
- Use nothing but these articles. If they do not show it clearly, answer \
"happened": false. Never conclude that the question resolves NO.
- LEAD: if the articles show a SPECIFIC date bearing on the resolving act, \
give the EARLIEST such date, a short description, and its KIND -- whether or \
not anything has happened since:
    "act"      -- the act ITSELF was announced to happen on that date \
("Treasury will buy $6bn on Thursday", "tariffs take effect 1 November")
    "occasion" -- a meeting, vote, hearing or decision date where the act \
MIGHT happen but the outcome is still open ("RBI policy meeting 5-7 October", \
"Senate vote scheduled for 14 October")
  Otherwise leave all three empty.

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
  "lead_date": "YYYY-MM-DD, the EARLIEST date the act was scheduled for, if any; else empty",
  "lead_what": "short description of that scheduled act or occasion; else empty",
  "lead_kind": "act | occasion; else empty",
  "reason": "one line, always filled in"
}}"""

OCCASION_HINT = """A DECISION OCCASION FELL ON {date}: {what}. Look for \
reports of its OUTCOME. If the outcome was the act the criteria require, it \
happened; if the outcome was something else (a hold, a delay, a rejection), \
it has NOT happened.
"""

LEAD_HINT = """THIS ACT WAS DUE ON {date}: {what}. Look specifically for \
reports that it took place -- results, outcomes, reactions to it having \
happened.
"""

VERIFY_HINT = """CHECK THIS CAREFULLY: the act was scheduled for {date} \
({what}). Look for reports that it took place on or after that date. If it \
has happened more than once, find the FIRST time and give that date. Cite \
every article that reports it done.
"""

DEEP_PROMPT = """Below are news articles found just now: FULL TEXTS (T1, T2, \
...) and HEADLINES (H1, H2, ...). Using ONLY these, decide ONE thing: has \
this forecasting question ALREADY RESOLVED YES?

TODAY: {today}
QUESTION: {question}
RESOLUTION CRITERIA: {criteria}
DEADLINE: {deadline}
WHAT RESOLVES IT: {resolves_on}
{focus}
THE RULE
- YES requires that the event the criteria describe has ACTUALLY HAPPENED, on \
or before today and on or before the deadline.
- An announcement that something WILL happen is NOT resolution. "Set to", \
"to buy", "expected to", "will take effect on <future date>" are NOT YET.
- A report of the act's RESULTS or OUTCOME proves it happened, even when the \
outcome disappointed ("purchases fell short of the cap" means purchases \
took place). Judge whether it HAPPENED, not whether it succeeded.
- Where the criteria say "officially", reliable reports of the official act \
count.
- "WHAT RESOLVES IT" says which act counts. Read every clause literally.
- If it happened more than once, event_date is the FIRST time.
- EVIDENCE: for each source that shows the act DONE, copy ONE sentence WORD \
FOR WORD -- the sentence stating it was done. From a full text (T#) copy a \
sentence of its text; from a headline (H#) copy the headline. Do not \
paraphrase, shorten, translate or fix typos: a quote that cannot be found \
verbatim in the source is thrown away. List every source that shows it done.
- If these sources do not clearly show it done, answer "happened": false. \
Never conclude that the question resolves NO.
- LEAD: if the sources show a SPECIFIC date bearing on the resolving act, give \
the EARLIEST such date, a short description, and its KIND: "act" (the act \
itself was announced for that date) or "occasion" (a meeting, vote or \
decision date where it MIGHT happen). Otherwise leave all three empty.

FULL TEXTS:
{texts}

HEADLINES:
{headlines}

Return JSON only:
{{
  "happened": true or false,
  "status": "happened | announced_not_yet_happened | in_progress | no_relevant_news",
  "event": "what specifically happened",
  "event_date": "YYYY-MM-DD, the FIRST date it happened; empty if it has not",
  "evidence": [{{"source": "T1", "quote": "exact sentence copied from it"}}],
  "lead_date": "YYYY-MM-DD or empty",
  "lead_what": "short description or empty",
  "lead_kind": "act | occasion; or empty",
  "reason": "one line, always filled in"
}}"""

# The only fixed vocabulary: one phrase per kind of resolving act. These words
# really are standard across acts of that kind.
TYPE_PHRASE = {
    "announced": "{actor} announces {object}",
    "in_effect": "{object} takes effect",
    # v20: none for carried_out. "{actor} {act_past}" without the object
    # ("US Treasury increased") matched every Treasury story of the week and
    # flooded the 3 Oct probe with bond-yield news.
    "carried_out": "",
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

QUOTE_MIN_TEXT = 8        # words: a real sentence, not "Treasury Department bought"
QUOTE_MIN_HEADLINE = 5


def judge_deep(question: dict, answer: dict | None, full: list[dict],
               heads: list[dict], today: dt.date,
               settings: dict) -> tuple[str, str, dict]:
    """
    The decision, from the FULL-TEXT reading. Pure code; no model. (v22)

    EVIDENCE TIERS -- replacing "count two sources":
      A  a quote verified VERBATIM in the text of an official (government)
         page                                         -> resolves on its own
      B  a quote verified verbatim in a full article  -> resolves with one more
                                                         publisher (A, B or C)
      C  a quote verified verbatim in a HEADLINE      -> corroboration only
    Headlines alone never resolve. Every quote must be found in the source
    text by code -- a quote the model invented, paraphrased or took from
    another article is thrown away. A dated source published BEFORE the event
    cannot report it done ("will buy" stories), so it does not count; an
    undated full article counts only if official.

    Returns (action, why, facts): action is resolved_yes | pending | not_yet
    | failed. facts["evidence"] lists every quote with its verdict, for the
    log and the probe.
    """
    wr = settings.get("web_resolution", {}) or {}
    facts = {"evidence": [], "sites": [], "official": False, "tier": ""}
    if not full and not heads:
        return "not_yet", "nothing found to read", facts
    if not isinstance(answer, dict):
        return "failed", "no parseable answer from the reader", facts
    if not _truthy(answer.get("happened")):
        return "not_yet", f"full-text reader: {answer.get('status') or 'not happened'}", facts

    event_date = _date(answer.get("event_date"))
    if event_date is None:
        return "pending", "reader says it happened but gave no usable date", facts
    facts["event_date"] = event_date.isoformat()
    deadline = _date(question.get("deadline"))
    created = _date(question.get("created"))
    if event_date > today:
        return "not_yet", f"event date {event_date} is in the future", facts
    if deadline and event_date > deadline:
        return ("not_yet", f"event date {event_date} is after the deadline "
                f"{deadline}; the lapse rule handles it", facts)
    if created and event_date < created:
        return ("pending", f"event date {event_date} is BEFORE the question was "
                f"created ({created}) -- looks born resolved", facts)

    good = []
    for item in answer.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("source", "")).strip().upper().lstrip("[").rstrip("]")
        quote = str(item.get("quote", "")).strip()
        verdict = {"source": ref, "quote": quote[:200], "ok": False, "tier": "", "why": ""}
        facts["evidence"].append(verdict)
        kind, num = ref[:1], ref[1:]
        pool = full if kind == "T" else heads if kind == "H" else None
        if pool is None or not num.isdigit() or not 1 <= int(num) <= len(pool):
            verdict["why"] = "no such source"
            continue
        art = pool[int(num) - 1]
        site = _site(art.get("site") or "") or (art.get("publisher") or "").lower()
        official = _is_official(site, settings)
        where = art.get("text", "") if kind == "T" else art.get("title", "")
        if not deep_search.quote_found(
                quote, where, QUOTE_MIN_TEXT if kind == "T" else QUOTE_MIN_HEADLINE):
            verdict["why"] = "quote not found verbatim in the source (or too short)"
            continue
        pub = _date(art.get("date"))
        if pub is None and not (kind == "T" and official):
            verdict["why"] = "source has no date, and is not official"
            continue
        if pub is not None and pub < event_date:
            verdict["why"] = (f"published {pub}, before the event -- can only "
                              "have reported it as planned")
            continue
        verdict.update(ok=True, site=site,
                       tier="A" if (kind == "T" and official) else "B" if kind == "T" else "C",
                       why="verified")
        good.append(verdict)

    sites = []
    for v in good:
        if v["site"] and v["site"] not in sites:
            sites.append(v["site"])
    tiers = {v["tier"] for v in good}
    facts.update(sites=sites, official="A" in tiers,
                 tier="A" if "A" in tiers else "B" if "B" in tiers else "C" if tiers else "")

    if "A" in tiers:
        return "resolved_yes", "official source, quote verified in its text", facts
    if "B" in tiers and len(sites) >= 2:
        return ("resolved_yes", f"full-text quote verified, corroborated by "
                f"{len(sites) - 1} more publisher(s)", facts)
    if "B" in tiers:
        return ("pending", "one full-text source verified, no second publisher "
                "corroborates it yet", facts)
    if tiers:
        return "pending", "only headlines back it -- headlines never resolve", facts
    return ("pending", "reader says it happened, but none of its quotes could "
            "be verified in the sources", facts)


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
                           "act_past": form.get("act_past", "")}) if template else "",
    ):
        text = _clean(text, 14)
        if text and text not in out:
            out.append(text)
    return out


def _kind(value) -> str:
    """v22: 'occasion' only when said so; anything else is treated as an act."""
    return "occasion" if str(value or "").strip().lower() == "occasion" else "act"


def _lead(answer: dict, q: dict):
    """
    (date, what, kind) of a dated lead: from today's reading if it found one,
    otherwise from the date stored on the question earlier.

    v22 -- KIND MATTERS. An "act" lead means the act itself was announced for
    that date; if the date passes with no report, the human is asked. An
    "occasion" lead (a meeting, vote, decision date) only means the act MIGHT
    happen then; the RBI can hold rates. Treating the 5 Oct MPC meeting as an
    act would have sent a false "was due, no report" alarm on 6 Oct.
    Stored as "YYYY-MM-DD: what" (act) or "YYYY-MM-DD [occasion]: what".
    """
    d = _date((answer or {}).get("lead_date"))
    what = _clean((answer or {}).get("lead_what"), 14)
    if d and what:
        return d, what, _kind((answer or {}).get("lead_kind"))
    stored = (q.get("awaiting") or "").strip()
    if ":" in stored:
        head, _, swhat = stored.partition(":")
        kind = "occasion" if "[occasion]" in head else "act"
        sd = _date(head.replace("[occasion]", "").strip())
        if sd and swhat.strip():
            return sd, swhat.strip(), kind
    return None


def awaiting_text(lead) -> str:
    tag = " [occasion]" if lead[2] == "occasion" else ""
    return f"{lead[0].isoformat()}{tag}: {lead[1]}"


def _article_block(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        snippet = f" -- {a['snippet']}" if a.get("snippet") else ""
        lines.append(f"[{i}] {a.get('date','')} | {a.get('publisher','')} | "
                     f"{a.get('title','')}{snippet}")
    return "\n".join(lines)


def _read(q, articles, today, router, hint=None):
    hint = hint or ""
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


def _text_block(full: list[dict], chars: int) -> str:
    out = []
    for i, a in enumerate(full, 1):
        body = (a.get("text") or a.get("snippet") or "")[:chars]
        out.append(f"[T{i}] {a.get('date') or 'undated'} | {a.get('publisher','')} | "
                   f"{a.get('title','')}\n{body}")
    return "\n\n".join(out) or "(none)"


def _head_block(heads: list[dict]) -> str:
    return "\n".join(f"[H{i}] {a.get('date','')} | {a.get('publisher','')} | "
                     f"{a.get('title','')}" for i, a in enumerate(heads, 1)) or "(none)"


def _last_deep(qid: str):
    last = None
    for row in store.read_rows(config.WEB_CHECKS_CSV):
        if row.get("question_id") == qid and row.get("deep"):
            last = _date(row.get("date")) or last
    return last


def check_one(q: dict, router, settings: dict, today: dt.date,
              deep=None, budget: dict | None = None) -> dict:
    """
    One full check, nothing written (v22).

      1. HEADLINES (Google News RSS, free): the cheap screen. Read once, only
         to ROUTE: did any headline say it happened, and is there a dated lead?
         Headlines never resolve anything.
      2. FULL TEXT (Tavily, 1 credit) when it matters:
           headline   -- a headline says it happened: search from creation
           due        -- an announced ACT's date has passed
           occasion   -- a decision occasion has passed (meeting, vote)
           sweep      -- no full-text check for `sweep_days` (default 7):
                         catches what headlines miss ("fell short" read as
                         "didn't happen")
      3. The full-text reader quotes evidence verbatim; judge_deep() verifies
         every quote in the source text and applies the tiers.

    Raises news_search.SearchUnavailable if the headline search cannot run.
    """
    wr = settings.get("web_resolution", {}) or {}
    ds = settings.get("deep_check", {}) or {}
    budget = budget if budget is not None else {"deep": True, "sweeps": True}
    limit = int(wr.get("max_articles", 40))
    form = _form_for(q, router)
    queries = build_queries(form, q)
    heads, problems = news_search.search(queries, limit=limit)
    r = {"form": form, "queries": queries, "articles": heads, "full": [],
         "problems": problems, "answer": {}, "headline_answer": {}, "model": "",
         "trigger": "", "window": None, "deep_query": "", "deep_error": None,
         "awaiting": None, "clear_awaiting": False}

    h_answer = {}
    if heads:
        got, model = _read(q, heads, today, router)
        if got is None:
            r.update(action="failed", why="headline reader gave no answer",
                     facts={}, model=model or "")
            return r
        h_answer = got if isinstance(got, dict) else {}
        r.update(headline_answer=h_answer, model=model or "")

    # -- route ---------------------------------------------------------------
    created = _date(q.get("created")) or today - dt.timedelta(days=30)
    deadline = _date(q.get("deadline")) or dt.date.max
    said = _truthy(h_answer.get("happened"))
    lead = _lead(h_answer, q)
    trigger, window, hint = "", None, ""
    if said:
        trigger = "headline"
        window = (created - dt.timedelta(days=1), today)
        hint = VERIFY_HINT.format(date=created.isoformat(),
                                  what=h_answer.get("event", "the act"))
    elif lead and lead[0] <= min(today, deadline):
        trigger = "due" if lead[2] == "act" else "occasion"
        window = (lead[0] - dt.timedelta(days=1),
                  min(lead[0] + dt.timedelta(days=4), today))
        hint = (LEAD_HINT if lead[2] == "act" else OCCASION_HINT).format(
            date=lead[0].isoformat(), what=lead[1])
        if lead[2] == "occasion" and today > lead[0] + dt.timedelta(days=4):
            r["clear_awaiting"] = True
    elif lead and lead[0] > today:
        r["awaiting"] = lead
    if not trigger:
        last = _last_deep(q.get("id", ""))
        every = int(ds.get("sweep_days", 7))
        if last is None or (today - last).days >= every:
            trigger = "sweep"
            window = (created - dt.timedelta(days=1), today)
    r.update(trigger=trigger, window=window)

    if not trigger:
        r.update(action="not_yet", facts={},
                 why=f"headlines: {h_answer.get('status') or 'nothing new'}; "
                     "full text not due today")
        return r

    # -- full text -------------------------------------------------------------
    allowed = budget.get("deep", True) and (trigger != "sweep" or budget.get("sweeps", True))
    if deep is None or not allowed:
        reason = ("full-text reading unavailable" if deep is None
                  else "credit reserve -- sweeps paused" if trigger == "sweep"
                  else "full-text budget for this run is spent")
        r["deep_error"] = reason if deep is None else None
        if said:
            r.update(action="pending", facts={},
                     why=f"headlines say it happened, but the full text was not "
                         f"read ({reason}) -- headlines never resolve")
        else:
            r.update(action="not_yet", facts={}, why=f"headlines: nothing; {reason}")
        return r

    query = (f"{form['actor']} {form.get('act_past','')} {form['object']}"
             if form else _fallback_query(q))
    query = _clean(query, 14)
    r["deep_query"] = query
    try:
        full = deep.search(query, window)
        if not full and trigger == "due":
            # An announced act's date may be off by a few days: try once
            # without dates. Not for an occasion -- a meeting with no decision
            # yet is normal, and the retry would just spend a second credit.
            full = deep.search(query, None)
    except deep_search.DeepUnavailable as exc:
        r["deep_error"] = exc
        if said:
            r.update(action="pending", facts={},
                     why=f"headlines say it happened, but full text could not be "
                         f"read ({exc.kind}) -- headlines never resolve")
        else:
            r.update(action="not_yet", facts={}, why=f"full text unavailable ({exc.kind})")
        return r
    if budget is not None:
        budget["spent"] = budget.get("spent", 0) + 1
    chars = int(ds.get("chars_per_article", 4000))
    r["full"] = full
    answer, model2 = router.generate(
        TASK,
        DEEP_PROMPT.format(
            today=today.isoformat(), question=q.get("question", ""),
            criteria=q.get("resolution_criteria", ""), deadline=q.get("deadline", ""),
            resolves_on=resolves_on_text(q), focus=hint,
            texts=_text_block(full, chars), headlines=_head_block(heads[:25]),
        ),
        temperature=0.1, max_output_tokens=3072,
    )
    action, why, facts = judge_deep(q, answer, full, heads[:25], today, settings)
    r.update(answer=answer if isinstance(answer, dict) else {}, model=model2 or r["model"],
             action=action, why=why, facts=facts)

    if action == "not_yet" and trigger == "due":
        r.update(action="pending",
                 why=f"was due on {lead[0]} ({lead[1]}); the full text shows no "
                     "report that it happened -- please check")
    elif action == "not_yet" and trigger == "headline":
        r.update(action="pending",
                 why="a headline says it happened, but the full text does not "
                     "confirm it -- please check")
    later = _lead(r["answer"], {})
    if later and later[0] > today and action != "resolved_yes":
        r["awaiting"] = later
    return r


def make_deep(settings: dict):
    """The Tavily client per settings, or None if full-text reading is off."""
    ds = settings.get("deep_check", {}) or {}
    if not ds.get("enabled", True):
        return None
    return deep_search.TavilyClient(max_results=int(ds.get("max_results", 8)))


def deep_budget(deep, settings: dict, log=None) -> dict:
    """
    Check Tavily credits ONCE per run and decide what may be spent:
      left <= min_credits  -> no full-text reading at all (alert)
      left <= reserve      -> triggered checks only; weekly sweeps paused
    Unknown usage (endpoint down) -> proceed; the 432 error is the backstop.
    """
    ds = settings.get("deep_check", {}) or {}
    budget = {"deep": deep is not None and bool(getattr(deep, "key", "")),
              "sweeps": True, "spent": 0, "left": None,
              "cap": int(ds.get("max_deep_per_run", 20))}
    if not budget["deep"]:
        return budget
    u = deep.usage()
    if u and u.get("left") is not None:
        budget["left"] = u["left"]
        if u["left"] <= int(ds.get("min_credits", 5)):
            budget["deep"] = False
            if log:
                _alert(log, f"FULL-TEXT CHECK OFF: only {u['left']} Tavily "
                            "credits left this month. Nothing can resolve until "
                            "they reset.")
        elif u["left"] <= int(ds.get("reserve_credits", 100)):
            budget["sweeps"] = False
            if log:
                log.flag(f"Tavily credits low ({u['left']} left): weekly sweeps "
                         "paused; checks triggered by news still run.")
        elif log:
            log.info(f"  Tavily credits left this month: {u['left']} of {u['limit']}")
    return budget


def run(router, settings: dict, today: dt.date, log, dry_run: bool = False,
        real_today: dt.date | None = None, deep="auto") -> dict:
    """
    Check every eligible question once. Returns a small summary dict.

    Never raises for a search or API problem: the failure becomes a red alert
    at the top of the run log, and the newspaper screen, lapses and
    forecasting carry on regardless.
    """
    summary = {"checked": 0, "resolved": [], "pending": [], "unchecked": 0,
               "deep_used": 0}
    wr = settings.get("web_resolution", {}) or {}
    log.sub("Web resolution (headlines screen, full text decides, YES only)")

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

    if deep == "auto":
        deep = make_deep(settings)
    if deep is not None and not getattr(deep, "key", ""):
        _alert(log, "FULL-TEXT CHECK OFF: the TAVILY_API_KEY secret is not set. "
                    "Headlines are still screened, but headlines never resolve, "
                    "so nothing can resolve from the web until the key is added.")
        deep = None
    budget = deep_budget(deep, settings, log)
    alerted = set()

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
        if budget["deep"] and budget["spent"] >= budget["cap"]:
            budget["deep"] = False
            log.info(f"  full-text cap of {budget['cap']} reached for this run")

        qid = q.get("id", "")
        try:
            r = check_one(q, router, settings, today, deep=deep, budget=budget)
        except news_search.SearchUnavailable as exc:
            summary["unchecked"] = len(queue) - i
            _alert(log,
                   f"WEB CHECK DID NOT RUN: the headline search could not be "
                   f"reached ({exc}). {summary['unchecked']} question(s) were "
                   "not checked; resolution is relying on the papers alone "
                   "this run.")
            if not dry_run:
                _record(today, q, "failed", f"headline search unreachable: {exc}",
                        {}, [], "", {})
            break

        if r["action"] == "failed":
            summary["unchecked"] = len(queue) - i
            _alert(log,
                   f"WEB CHECK STOPPED at {qid}: the reading model gave no "
                   f"answer ({router.stats.last_error or 'unknown error'}). "
                   f"{summary['unchecked']} question(s) were not checked.")
            if not dry_run:
                _record(today, q, "failed", r["why"], {}, r["queries"],
                        r["model"], r.get("facts") or {}, r.get("trigger", ""))
            break

        err = r.get("deep_error")
        if isinstance(err, deep_search.DeepUnavailable) and err.kind not in alerted:
            alerted.add(err.kind)
            _alert(log, f"FULL-TEXT CHECK FAILED ({err.kind}): {err.detail or err}. "
                        "Headlines never resolve, so questions that need full "
                        "text wait for the next run.")

        summary["checked"] += 1
        answer, facts, why = r["answer"], r["facts"], r["why"]
        queries = r["queries"] + ([f"[full text] {r['deep_query']}"] if r.get("deep_query") else [])
        was_pending = _last_action(qid) == ("pending", why)
        if r.get("deep_query"):
            summary["deep_used"] += 1
            w = r["window"]
            log.info(f"  {qid}: full text read ({r['trigger']}), "
                     f"{w[0]} to {w[1]}: {len(r['full'])} article(s), "
                     f"{sum(1 for a in r['full'] if a.get('full'))} with full text")
        if r["awaiting"] and not dry_run:
            date, what, kind = r["awaiting"]
            store.update_question(qid, {"awaiting": awaiting_text(r["awaiting"])})
            log.info(f"  {qid}: {kind} on {date} ({what}); will read for it "
                     "once that date passes")
        if r.get("clear_awaiting") and not dry_run:
            store.update_question(qid, {"awaiting": ""})
            log.info(f"  {qid}: the awaited occasion is past; stopped watching it")
        if r["problems"]:
            log.info(f"  {qid}: {len(r['problems'])} headline fetch(es) "
                     f"failed (others worked): {r['problems'][0]}")
        if not dry_run:
            _record(today, q, r["action"], why, answer, queries,
                    r["model"], facts, r.get("trigger", "") if r.get("deep_query") else "")

        if r["action"] == "resolved_yes":
            _apply_yes(q, facts["event_date"], answer, why, today, log, dry_run)
            summary["resolved"].append(qid)
        elif r["action"] == "pending" and was_pending:
            summary["pending"].append(qid)
            log.info(f"  {qid}: still waiting for you -- {why}")
        elif r["action"] == "pending":
            summary["pending"].append(qid)
            log.flag(
                f"{qid}: needs your call -- {why}.\n"
                f"    Question: {q.get('question','')}\n"
                f"    Event:    {answer.get('event','')} "
                f"({answer.get('event_date','no date')})\n"
                f"    Evidence: {_evidence_text(facts) or 'none'}\n"
                f"    If you agree, paste into config/resolutions.csv:\n"
                f"      {_paste_row(qid, answer)}"
            )
        else:
            log.info(f"  {qid}: {r['action']} -- {why}")

    _rebuild_pending(today, log, dry_run)
    log.info(
        f"  web checks this run: {summary['checked']}; full-text reads: "
        f"{summary['deep_used']}; resolved YES: {len(summary['resolved'])}; "
        f"waiting for you: {len(summary['pending'])}"
    )
    return summary


def _evidence_text(facts: dict) -> str:
    out = []
    for v in facts.get("evidence") or []:
        mark = f"{v.get('tier')} ok" if v.get("ok") else f"rejected: {v.get('why')}"
        out.append(f"{v.get('source')} [{mark}] \"{(v.get('quote') or '')[:90]}\"")
    return " | ".join(out)


def probe(router, settings: dict, qid: str, today: dt.date, out=print,
          deep="auto", budget=None) -> str:
    """
    Run ONE question's web check end to end and print everything: headlines,
    the route taken, every full-text article (with its length), every quote
    and whether code verified it, and the decision. Writes nothing.
    Run from GitHub (Actions -> Run workflow -> web_probe), where the network is.
    """
    q = store.question_by_id(qid)
    if q is None:
        out(f"No question {qid} in data/questions.csv")
        return "failed"
    if deep == "auto":
        deep = make_deep(settings)
    if deep is not None and not getattr(deep, "key", ""):
        deep = None
    out(f"PROBE {qid}: {q.get('question','')}")
    out(f"  created {q.get('created')}  deadline {q.get('deadline')}  "
        f"resolves_on {q.get('resolves_on') or '(blank -> carried_out)'}"
        f"  awaiting {q.get('awaiting') or '-'}")
    try:
        r = check_one(q, router, settings, today, deep=deep, budget=budget)
    except news_search.SearchUnavailable as exc:
        out(f"\n  HEADLINE SEARCH UNREACHABLE: {exc}")
        return "failed"
    out(f"\n  form: {r.get('form') or '(failed; question text used)'}")
    out(f"  headline queries: {r['queries']}")
    if r["problems"]:
        out(f"  headline fetch problems: {r['problems']}")
    out(f"  {len(r['articles'])} headlines; first 15:")
    for i, a in enumerate(r["articles"][:15], 1):
        out(f"   [H{i:<2d}] {a['date']}  {a['publisher'][:22]:22s}  {a['title'][:95]}")
    out(f"\n  headline reader: {r.get('headline_answer') or '(none)'}")
    if r.get("trigger"):
        out(f"\n  FULL TEXT: trigger={r['trigger']}  window={r['window']}  "
            f"query='{r.get('deep_query') or '(not run)'}'")
    else:
        out("\n  FULL TEXT: not triggered today")
    if r.get("deep_error"):
        out(f"  FULL TEXT UNAVAILABLE: {r['deep_error']}")
    for i, a in enumerate(r.get("full") or [], 1):
        kind = f"{len(a.get('text',''))} chars" if a.get("full") else "stub only"
        out(f"   [T{i}] {a.get('date') or 'undated':10s}  {a.get('site','')[:26]:26s}  "
            f"{kind:12s} {a.get('title','')[:80]}")
    if r.get("deep_query"):
        out(f"\n  full-text reader ({r['model'] or 'none'}): {r['answer']}")
    for v in (r.get("facts") or {}).get("evidence") or []:
        mark = f"OK tier {v.get('tier')}" if v.get("ok") else f"REJECTED ({v.get('why')})"
        out(f"   evidence {v.get('source')}: {mark}: \"{(v.get('quote') or '')[:110]}\"")
    if r.get("awaiting"):
        out(f"\n  AWAITING: {awaiting_text(r['awaiting'])} -- would be stored")
    out(f"\n  DECISION: {r['action']} -- {r['why']}")
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


def _record(today, q, action, why, answer, queries, model, facts, deep=""):
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
        "evidence": answer.get("evidence", "") if isinstance(answer.get("evidence"), str) else "",
        "sources": "; ".join(facts.get("sites", []) or []),
        "official_source": "yes" if facts.get("official") else "no",
        "queries": "; ".join(queries or []),
        "cited_articles": "",
        "deep": deep,
        "tier": facts.get("tier", ""),
        "quotes": _evidence_text(facts),
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
