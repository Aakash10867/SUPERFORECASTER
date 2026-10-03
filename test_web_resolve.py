#!/usr/bin/env python3
"""
Offline test for web resolution (v19: the model fills a form, code builds the
queries and searches Google News, a model reads, code decides, and one
follow-up search chases a scheduled act whose date has passed). No API key,
no network.

  Part 1  every code check in web_resolve.judge(), one case each
  Part 2  the RSS parser on a realistic Google News feed
  Part 3  query shape (form -> fixed queries) and the reader's new rules
  Part 4  REPLAY of the live 3 Oct probe: the 25 real headlines, the reader's
          real mistake, and the follow-up that must rescue it; plus the
          pending tripwire and stored "awaiting" dates
  Part 5  the full run loop, and LOUD failure at the top of the log
  Part 6  the probe writes nothing; lens grounding stays off

    python test_web_resolve.py
"""

import datetime as dt
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SUPERFORECASTER_API", "fake-key-for-offline-test")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import config, models, news_search, resolve, store, web_resolve  # noqa: E402
from src.lenses import LensRunner                                        # noqa: E402
from src.runlog import RunLog                                            # noqa: E402

TODAY = dt.date(2026, 10, 3)
SETTINGS = config.load_settings()
MODELS = config.load_models()
RESULTS: list[tuple[str, bool]] = []


def check(label, passed):
    RESULTS.append((label, bool(passed)))


def art(date, publisher, title, site=None):
    return {"date": date, "publisher": publisher, "title": title,
            "site": site or publisher.lower().replace(" ", "") + ".com",
            "snippet": "", "link": ""}


# ---------------------------------------------------------------------------
# Part 1: the code checks
# ---------------------------------------------------------------------------

Q = {"id": "Q1", "created": "2026-08-22", "deadline": "2026-12-31",
     "question": "q", "resolution_criteria": "c"}

ARTS = [
    art("2026-09-11", "Reuters", "Treasury buys $6 billion in long-dated buyback"),
    art("2026-09-10", "CNBC", "Treasury completes $6 billion buyback operation"),
    art("2026-09-09", "Bloomberg", "Treasury to buy back up to $6 billion on Thursday"),
    art("2026-09-10", "Treasury", "Buyback results", site="home.treasury.gov"),
    art("2026-09-12", "Reuters", "Reuters follow-up on buyback",
        site="www.reuters.com"),
]


def yes(date="2026-09-10", cite=(1, 2), **extra):
    return {"happened": True, "status": "happened", "event": "e",
            "event_date": date, "supporting_articles": list(cite),
            "evidence": "ev", "reason": "r", **extra}


def act(answer, articles=ARTS, q=Q):
    return web_resolve.judge(q, answer, articles, TODAY, SETTINGS)[0]


def part_one():
    check("two different publishers report it done -> YES",
          act(yes(cite=(1, 2))) == "resolved_yes")
    check("one official site -> YES", act(yes(cite=(4,))) == "resolved_yes")
    check("one unofficial publisher -> pending", act(yes(cite=(1,))) == "pending")
    check("same publisher twice is ONE source", act(yes(cite=(1, 5))) == "pending")
    check("cited article from BEFORE the event cannot show it done",
          act(yes(cite=(3,))) == "not_yet")
    check("pre-event article ignored, post-event ones still count",
          act(yes(cite=(3, 1, 2))) == "resolved_yes")
    check("cites nothing -> pending", act(yes(cite=())) == "pending")
    check("cites numbers that don't exist -> pending",
          act(yes(cite=(0, 99))) == "pending")
    check("citations as a string are understood",
          act(yes(cite=(), supporting_articles="1, 2")) == "resolved_yes")
    check("no articles found -> not yet, without asking a model",
          act(yes(), articles=[]) == "not_yet")
    check("reader says not happened -> not yet",
          act({"happened": False, "status": "announced_not_yet_happened"}) == "not_yet")
    check("happened but no date -> pending", act(yes(date="")) == "pending")
    check("future date -> not yet", act(yes(date="2026-11-04")) == "not_yet")
    check("after the deadline -> not a YES",
          act(yes(), q={**Q, "deadline": "2026-09-05"}) == "not_yet")
    check("before the question existed -> pending (born resolved)",
          act(yes(date="2026-08-19")) == "pending")
    check("no answer at all -> failed", act(None) == "failed")


# ---------------------------------------------------------------------------
# Part 2: the RSS parser
# ---------------------------------------------------------------------------

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>search</title>
<item>
  <title>Treasury buys $6 billion of older bonds in expanded buyback - Reuters</title>
  <link>https://news.google.com/rss/articles/abc</link>
  <pubDate>Thu, 10 Sep 2026 19:12:00 GMT</pubDate>
  <description>&lt;a href="x"&gt;Treasury buys $6 billion of older bonds in expanded buyback&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;Reuters&lt;/font&gt;</description>
  <source url="https://www.reuters.com">Reuters</source>
</item>
<item>
  <title>Treasury to buy back up to $6 billion - CNBC</title>
  <link>https://news.google.com/rss/articles/def</link>
  <pubDate>Wed, 09 Sep 2026 21:00:00 GMT</pubDate>
  <description>Treasury to buy back up to $6 billion</description>
  <source url="https://www.cnbc.com">CNBC</source>
</item>
<item>
  <title>No date here - Somebody</title>
  <source url="https://x.com">Somebody</source>
</item>
</channel></rss>"""


def part_two():
    items = news_search.parse(FEED)
    check("parser reads every dated item, drops the undated one", len(items) == 2)
    a = items[0]
    check("headline has the ' - Publisher' suffix removed",
          a["title"] == "Treasury buys $6 billion of older bonds in expanded buyback")
    check("publisher and site come from the feed's <source>",
          a["publisher"] == "Reuters" and a["site"] == "reuters.com")
    check("publication date parsed (UTC)", a["date"] == "2026-09-10")
    check("garbage XML gives no items, not a crash", news_search.parse("<oops") == [])

    calls = []

    def fetch(url):
        calls.append(url)
        return 200, FEED
    arts, problems = news_search.search(["treasury buyback"], fetch=fetch)
    check("each query runs against US and India editions",
          len(calls) == 2 and "gl=US" in calls[0] and "gl=IN" in calls[1])
    check("duplicate headlines across editions are merged", len(arts) == 2)
    check("feed (relevance) order kept, not re-sorted by date",
          [a["date"] for a in arts] == ["2026-09-10", "2026-09-09"])

    # v20: THE 3 OCT BUG. A flood of fresh results must not push an old,
    # relevant report out of the list. Feed A is relevance-ordered with the
    # Sep report first; feed B is 60 fresh October stories.
    old = art("2026-09-10", "Reuters", "Treasury buys $5.4 billion in buyback")
    flood = [art("2026-10-0%d" % (1 + i % 3), f"Pub{i}", f"Yields story {i}")
             for i in range(60)]
    got = news_search._interleave([[old] + flood[:5], flood], 40)
    check("an old relevant report survives a flood of new stories",
          got[0]["title"] == old["title"] and len(got) == 40)

    urls = []
    def spy(url):
        urls.append(url)
        return 200, FEED
    news_search.search(["treasury buyback"], fetch=spy,
                       window=(dt.date(2026, 9, 9), dt.date(2026, 9, 14)))
    check("date window becomes after:/before: in the query",
          "after%3A2026-09-09" in urls[0] and "before%3A2026-09-14" in urls[0])

    def half(url):
        return (200, FEED) if "gl=US" in url else (503, "")
    arts, problems = news_search.search(["x"], fetch=half)
    check("one edition failing is tolerated and reported",
          len(arts) == 2 and len(problems) == 1)

    def dead(url):
        raise OSError("blocked")
    try:
        news_search.search(["x"], fetch=dead)
        check("every fetch failing raises SearchUnavailable", False)
    except news_search.SearchUnavailable:
        check("every fetch failing raises SearchUnavailable", True)


# ---------------------------------------------------------------------------
# Fakes and sandbox
# ---------------------------------------------------------------------------

class FakeQuota:
    key_names = ["FAKE_KEY"]
    def remaining(self, *a): return 500
    def used(self, *a): return 0
    def record(self, *a, **k): pass
    def flush(self): pass
    def summary(self): return ""


class FakeReader(models.ModelRouter):
    """
    web_query  -> a form whose actor is the question id, so every query
                  carries it and the fake search knows which list to return.
    web_resolve -> the next scripted answer for that question, in order.
    A question with no answers left makes the reader fail, as a dead model
    or exhausted quota would.
    """
    def __init__(self, script):
        self.script = script
        self.used = {k: 0 for k in script}
        self.calls = []
        self.chains = MODELS["chains"]
        self.grounding_models = set()
        self.stats = models.CallStats()
        self.quota = FakeQuota()

    def generate(self, task, prompt, *, expect_json=True, temperature=0.4,
                 max_output_tokens=4096, grounded=False):
        assert not grounded, "v18+ never asks a model to search"
        qid = next((k for k in self.script if f"QUESTION: {k} " in prompt), None)
        self.calls.append((task, qid, prompt))
        if task == "web_query":
            return {"actor": qid, "act_past": "did", "object": "thing"}, "fake-lite"
        answers = (self.script.get(qid) or {}).get("answers", [])
        i = self.used.get(qid, 0)
        if qid is None or i >= len(answers) or answers[i] is None:
            self.stats.last_error = "gemini-3.5-flash-lite: rate-limited"
            return None, None
        self.used[qid] = i + 1
        return answers[i], "fake-lite"

    def reads(self, qid):
        return [c for c in self.calls if c[0] == "web_resolve" and c[1] == qid]


WINDOWS: list = []


def fake_search_from(script, log=None):
    def search(queries, limit=40, fetch=None, window=None):
        if log is not None:
            log.append(list(queries))
        WINDOWS.append(window)
        qid = next((k for k in script for q in queries if k in q.split()), None)
        if qid is None:
            return [], []
        followup = any(q.endswith(" results") for q in queries)
        return list(script[qid].get("follow" if followup else "first", [])), []
    return search


def _sandbox() -> Path:
    sandbox = Path(tempfile.mkdtemp(prefix="sf-web-"))
    config.DATA = sandbox / "data"
    config.LOGS = sandbox / "logs"
    config.RUNS = config.DATA / "runs"
    config.REFERENCE = config.DATA / "reference"
    config.REPORTS = config.DATA / "reports"
    names = {
        "QUESTIONS_CSV": "questions.csv", "PROPOSALS_CSV": "proposals.csv",
        "FORECASTS_CSV": "forecasts.csv", "PROCESSED_CSV": "processed.csv",
        "WAITING_CSV": "waiting_list.csv", "PENDING_TAGS_CSV": "pending_tags.csv",
        "SCREENS_CSV": "screens.csv", "LENS_CSV": "lens_outputs.csv",
        "DIAGNOSTICS_CSV": "diagnostics.csv",
        "SYSTEM_PROPOSALS_CSV": "system_proposals.csv",
        "WEB_CHECKS_CSV": "web_checks.csv",
        "PENDING_RESOLUTIONS_CSV": "pending_resolutions.csv",
        "COVERAGE_CSV": "coverage.csv",
    }
    for attr, fname in names.items():
        setattr(config, attr, config.DATA / fname)
    config.REFERENCE_INDEX_CSV = config.REFERENCE / "index.csv"
    config.QUOTA_JSON = config.DATA / "quota.json"
    config.OVERRIDES_CSV = sandbox / "overrides.csv"
    config.RESOLUTIONS_CSV = sandbox / "resolutions.csv"
    store._SCHEMAS = {getattr(config, a): getattr(store, f) for a, f in (
        ("QUESTIONS_CSV", "QUESTION_FIELDS"), ("PROPOSALS_CSV", "PROPOSAL_FIELDS"),
        ("FORECASTS_CSV", "FORECAST_FIELDS"), ("PROCESSED_CSV", "PROCESSED_FIELDS"),
        ("WAITING_CSV", "WAITING_FIELDS"), ("PENDING_TAGS_CSV", "PENDING_TAG_FIELDS"),
        ("LENS_CSV", "LENS_FIELDS"), ("SCREENS_CSV", "SCREEN_FIELDS"),
        ("DIAGNOSTICS_CSV", "DIAGNOSTIC_FIELDS"),
        ("SYSTEM_PROPOSALS_CSV", "SYSTEM_PROPOSAL_FIELDS"),
        ("REFERENCE_INDEX_CSV", "REFERENCE_INDEX_FIELDS"),
        ("WEB_CHECKS_CSV", "WEB_CHECK_FIELDS"),
        ("PENDING_RESOLUTIONS_CSV", "PENDING_RESOLUTION_FIELDS"),
        ("COVERAGE_CSV", "COVERAGE_FIELDS"))}
    store.ensure_files()
    return sandbox


def _question(qid, deadline, created="2026-08-22", **extra):
    row = {"id": qid, "question": f"{qid} test question", "domain": "global_macro",
           "bucket": "medium", "created": created, "deadline": deadline,
           "primary_tag": "t", "resolution_criteria": "criteria",
           "status": "open", "shape": "point", "admitted_by": "test",
           "resolves_on": "carried_out"}
    row.update(extra)
    store.append_row(config.QUESTIONS_CSV, row)


def NO(lead_date="", lead_what="", status="announced_not_yet_happened"):
    return {"happened": False, "status": status, "supporting_articles": [],
            "lead_date": lead_date, "lead_what": lead_what, "reason": "r"}


class Patched:
    """Swap the search for a fake inside a with-block."""
    def __init__(self, fn):
        self.fn = fn
    def __enter__(self):
        self.orig = web_resolve.news_search.search
        web_resolve.news_search.search = self.fn
    def __exit__(self, *a):
        web_resolve.news_search.search = self.orig


# ---------------------------------------------------------------------------
# Part 3: queries -- fixed shape, the act's own words
# ---------------------------------------------------------------------------

def part_three():
    form = {"actor": "US Treasury", "act_past": "bought back",
            "object": "long-dated bonds $4 billion"}
    qs = web_resolve.build_queries(form, {"resolves_on": "carried_out"})
    check("carried_out: two queries, topic and completion, both with the object",
          qs == ["US Treasury long-dated bonds $4 billion",
                 "US Treasury bought back long-dated bonds $4 billion"])
    check("no query is actor + verb alone (the 3 Oct junk query)",
          all(form["object"].split()[0] in q for q in qs))
    check("in_effect adds the standard 'takes effect' phrase",
          web_resolve.build_queries(form, {"resolves_on": "in_effect"})[-1]
          == "long-dated bonds $4 billion takes effect")
    check("announced adds 'announces'",
          "announces" in web_resolve.build_queries(form, {"resolves_on": "announced"})[-1])
    check("no form -> falls back to the question text",
          web_resolve.build_queries({}, {"question": "Will the RBI raise the repo rate?"})
          == ["RBI raise the repo rate"])
    check("follow-up queries chase the act's results",
          web_resolve.followup_queries(form, "Treasury $6 billion buyback operation")[0]
          == "Treasury $6 billion buyback operation results")
    prompt = web_resolve.WEB_PROMPT
    check("reader told: a RESULTS report proves the act happened",
          "RESULTS or OUTCOME proves it happened" in prompt)
    check("reader told: news reports of an official act count as official",
          "reliable news reports OF the official act count" in prompt)
    check("reader told: repeated act -> date of the FIRST time",
          "give the date of the FIRST time" in prompt)
    check("reader told: cite EVERY supporting article",
          "Cite EVERY article that shows it happened" in prompt)
    check("form told: the act itself, not a policy change about it",
          '"bought", not "increased the program"' in web_resolve.QUERY_PROMPT)


# ---------------------------------------------------------------------------
# Part 4: the Q0003 replay -- the real probe headlines of 3 Oct 2026
# ---------------------------------------------------------------------------

PROBE = [  # the 25 articles the live v18 probe found, as (date, publisher, title)
    ("2026-09-23", "Yahoo Finance UK", "US to Buy Back Up to $6 Billion in Longer-Dated Treasuries"),
    ("2026-09-22", "Chase Bank", "Why the Treasury's $6 billion bond buyback matters for investors"),
    ("2026-09-13", "Seeking Alpha", "Treasury buyback test exposes limits of support for long-term debt: SocGen"),
    ("2026-09-11", "The Business Times", "US Treasury yields surge as oil spike, buyback results fuel sell-off"),
    ("2026-09-10", "qz.com", "Bond yields hit multiyear highs before ECB rate decision"),
    ("2026-09-10", "Asia Economy", "U.S. Triples Long-Term Treasury Buybacks, but Yields Rise Instead"),
    ("2026-09-10", "WSJ", "U.S. 10-Year Treasury Yield Nears 5% as Oil Fuels Inflation Fears"),
    ("2026-09-10", "Moomoo", "The U.S. dollar gave back approximately 1% of its summer gains"),
    ("2026-09-10", "Futu", "Long-term municipal bond yields hit their highest level since 2011"),
    ("2026-09-10", "Futu", "The U.S. Treasury's long-term bond buyback program, exceeding $5 billion, fell short of its upper limit"),
    ("2026-09-09", "qz.com", "Treasury is doubling its long-term bond buybacks to boost market liquidity"),
    ("2026-09-09", "KuCoin", "U.S. stocks fall as Treasury repurchase falls short of expectations"),
    ("2026-09-09", "CNBC", "Treasury Department to buy back up to $6 billion in longer-term debt"),
    ("2026-09-09", "Yahoo Finance", "Bond yields jump despite $6 bn US government intervention"),
    ("2026-09-09", "UA.NEWS", "US Treasury Secretary Bessent to speak at Republican convention"),
    ("2026-09-09", "WSJ", "U.S. Treasury Plans $6 Billion Buyback, Yields Rise"),
    ("2026-09-09", "qz.com", "Bessent dared currency traders to bet against him"),
    ("2026-09-09", "CNBC", "Bessent bond plan details to be revealed"),
    ("2026-09-09", "Yahoo Finance", "US Treasury Triples Long-Dated Debt Buyback to $6 Billion"),
    ("2026-09-09", "The Hill", "Treasury to buy $6B in debt, but bond yields rise"),
    ("2026-09-09", "The New York Times", "Bond Market Rebuffs Treasury's $6 Billion Plan"),
    ("2026-09-09", "Politico", "Treasury poised to buy up to $6B in bonds"),
    ("2026-09-09", "WKOW", "Bond yields rise after Treasury announces size of buyback operation"),
    ("2026-09-09", "Bloomberg.com", "Bessent's Upsized Buybacks Get Hit by Bond Market Reality"),
    ("2026-09-09", "qz.com", "Bond yields hit a 3-year high even as Bessent triples the buyback plan"),
]
PROBE_ARTS = [art(d, p, t) for d, p, t in PROBE]
# What a results-seeking follow-up would add (illustrative, not from the probe):
FOLLOW = [art("2026-09-10", "Reuters", "Treasury buys $5.4 billion in long-end buyback, short of $6 billion cap"),
          art("2026-09-11", "Bloomberg.com", "Treasury buyback draws $5.4 billion as dealers hold back")]


def part_four():
    # (a) The exact v18 failure: the reader read "fell short" as "didn't
    # happen" and called it announced. v19's follow-up must rescue it.
    sandbox = _sandbox()
    _question("Q0003", "2026-12-31")
    lead = ("2026-09-10", "Q0003 Treasury $6 billion buyback operation")
    found = {}
    for n, (d, p, t) in enumerate(PROBE + [(a["date"], a["publisher"], a["title"]) for a in FOLLOW], 1):
        found[t] = n
    script = {"Q0003": {
        "first": PROBE_ARTS, "follow": FOLLOW,
        "answers": [NO(*lead),                               # the v18 mistake
                    None]}}                                  # filled below
    # Second reading, over the merged list: cites the Futu results headline
    # and the Reuters results story by their numbers in the merged list.
    merged = web_resolve.news_search.merge(PROBE_ARTS, FOLLOW, limit=40)
    num = {a["title"]: i for i, a in enumerate(merged, 1)}
    script["Q0003"]["answers"][1] = {
        "happened": True, "status": "happened", "event": "first $4bn+ operation",
        "event_date": "2026-09-10",
        "supporting_articles": [num[PROBE[9][2]], num[FOLLOW[0]["title"]]],
        "evidence": "bought $5.4bn", "reason": "results reported"}
    calls = []
    with Patched(fake_search_from(script, calls)):
        r = FakeReader(script)
        web_resolve.run(r, SETTINGS, TODAY, RunLog(TODAY), real_today=TODAY)
    q3 = store.question_by_id("Q0003")
    check("REPLAY: Q0003 resolves YES via the follow-up", q3["outcome"] == "1")
    check("REPLAY: dated 10 Sep 2026", q3["resolved_date"] == "2026-09-10")
    check("REPLAY: exactly one follow-up search, aimed at results",
          len(calls) == 2 and calls[1][0].endswith(" results"))
    check("REPLAY: second reading was told the act was due on 10 Sep",
          "THIS ACT WAS DUE ON 2026-09-10" in r.reads("Q0003")[1][2])
    row = store.read_rows(config.WEB_CHECKS_CSV)[0]
    check("REPLAY: both searches' queries recorded",
          row["queries"].count("Q0003") >= 4)
    shutil.rmtree(sandbox, ignore_errors=True)

    # (a2) REPLAY of the 3 Oct v19 probe. The reader cited only [5] and dated
    # the latest operation; ET [26] and Reuters [27] also reported operations
    # done. Its real answer must stay pending; a cite-everything answer over
    # the same 40 articles must resolve.
    v19 = [art("2026-10-0%d" % (3 - i % 3), f"Pub{i}", f"Yields story {i}")
           for i in range(40)]
    v19[4] = art("2026-10-02", "BeInCrypto",
                 "US Treasury Buys $6 Billion of Bonds as Bitcoin Battles 24-Year-High Yields",
                 site="beincrypto.com")
    v19[25] = art("2026-10-01", "The Economic Times",
                  "US Market: Treasury bond purchases fall below $6 billion buyback cap",
                  site="economictimes.indiatimes.com")
    v19[26] = art("2026-10-01", "Reuters",
                  "Treasury's smaller-than-expected buybacks fuel debate over aims",
                  site="reuters.com")
    q3 = {"id": "Q0003", "created": "2026-08-22", "deadline": "2026-12-31"}
    real = {"happened": True, "event_date": "2026-10-02", "supporting_articles": [5]}
    check("REPLAY v19: its real one-citation answer stays pending",
          web_resolve.judge(q3, real, v19, TODAY, SETTINGS)[0] == "pending")
    full = {"happened": True, "event_date": "2026-10-01",
            "supporting_articles": [5, 26, 27]}
    check("REPLAY v19: citing every report over the same articles resolves",
          web_resolve.judge(q3, full, v19, TODAY, SETTINGS)[0] == "resolved_yes")

    # (b) Follow-up finds nothing either -> it goes to you, not silence.
    sandbox = _sandbox()
    _question("Q0003", "2026-12-31")
    script = {"Q0003": {"first": PROBE_ARTS, "follow": [],
                        "answers": [NO(*lead), NO()]}}
    with Patched(fake_search_from(script)):
        web_resolve.run(FakeReader(script), SETTINGS, TODAY, RunLog(TODAY),
                        real_today=TODAY)
    pend = store.read_rows(config.PENDING_RESOLUTIONS_CSV)
    check("due date passed, no report found -> pending for you, not silent",
          [p["question_id"] for p in pend] == ["Q0003"]
          and "was due on 2026-09-10" in pend[0]["why"])
    # Next day: same reason -> listed, but not flagged again.
    log2 = RunLog(TODAY + dt.timedelta(days=1))
    with Patched(fake_search_from(script)):
        web_resolve.run(FakeReader({"Q0003": {"first": PROBE_ARTS, "follow": [],
                                              "answers": [NO(*lead), NO()]}}),
                        SETTINGS, TODAY + dt.timedelta(days=1), log2,
                        real_today=TODAY + dt.timedelta(days=1))
    check("same pending reason next day is not flagged again",
          log2.flag_count == 0
          and len(store.read_rows(config.PENDING_RESOLUTIONS_CSV)) == 1)
    shutil.rmtree(sandbox, ignore_errors=True)

    # (c) A FUTURE scheduled date is stored, and chased when it falls due --
    # even when that day's search no longer surfaces the announcement.
    sandbox = _sandbox()
    _question("QV", "2026-12-31")
    _question("QZ", "2026-10-10")          # nearer deadline, normally first
    script = {"QV": {"first": [art("2026-10-01", "AP", "Senate vote set for 14 Oct")],
                     "answers": [NO("2026-10-14", "QV Senate vote")]},
              "QZ": {"first": [], "answers": []}}
    with Patched(fake_search_from(script)):
        web_resolve.run(FakeReader(script), SETTINGS, TODAY, RunLog(TODAY),
                        real_today=TODAY)
    check("future scheduled date stored on the question",
          store.question_by_id("QV")["awaiting"] == "2026-10-14: QV Senate vote")
    day = dt.date(2026, 10, 15)
    script2 = {"QV": {"first": [art("2026-10-15", "AP", "Unrelated QV story")],
                      "follow": [art("2026-10-14", "AP", "Senate passes it"),
                                 art("2026-10-14", "Reuters", "Senate vote passes")],
                      "answers": [NO(), {"happened": True, "event_date": "2026-10-14",
                                         "supporting_articles": [2, 3], "event": "v",
                                         "reason": "r"}]},
               "QZ": {"first": [], "answers": []}}
    r = FakeReader(script2)
    with Patched(fake_search_from(script2)):
        web_resolve.run(r, SETTINGS, day, RunLog(day), real_today=day)
    first_read = next(c for c in r.calls if c[0] == "web_query")
    check("a question whose awaited date fell due is checked first",
          first_read[1] == "QV")
    qv = store.question_by_id("QV")
    check("stored date drives the follow-up when today's search shows no lead",
          qv["outcome"] == "1" and qv["resolved_date"] == "2026-10-14")
    check("awaiting cleared once resolved", qv["awaiting"] == "")
    shutil.rmtree(sandbox, ignore_errors=True)


# ---------------------------------------------------------------------------
# Part 4b (v21): the verify step -- "thin" and "earlier" second looks
# ---------------------------------------------------------------------------

V20 = [  # the 3 Oct v20 probe, abridged: announcements plus one done report
    art("2026-09-22", "Chase Bank", "Why the Treasury's $6 billion bond buyback matters"),
    art("2026-10-01", "KuCoin", "U.S. Treasury Expands Long-End Buyback Amid Rising Bond Yields"),
    art("2026-09-09", "CNBC", "Treasury Department to buy back up to $6 billion in longer-term debt"),
    art("2026-08-19", "Reuters", "Bessent doubles US long-bond buybacks"),
    art("2026-09-09", "WSJ", "U.S. Treasury Plans $6 Billion Buyback, Yields Rise"),
    art("2026-10-02", "BeInCrypto", "US Treasury Buys $6 Billion of Bonds", site="beincrypto.com"),
]
SEPT = [art("2026-09-10", "Reuters", "Treasury buys $5.4 billion in long-end buyback"),
        art("2026-09-11", "Bloomberg.com", "Treasury buyback draws $5.4 billion")]


def part_four_b():
    lead = ("2026-09-10", "Q0003 Treasury buyback operation")

    def run_one(script, qid="Q0003"):
        sandbox = _sandbox()
        _question(qid, "2026-12-31")
        calls = []
        with Patched(fake_search_from(script, calls)):
            web_resolve.run(FakeReader(script), SETTINGS, TODAY, RunLog(TODAY),
                            real_today=TODAY)
        q = store.question_by_id(qid)
        rows = store.read_rows(config.WEB_CHECKS_CSV)
        shutil.rmtree(sandbox, ignore_errors=True)
        return q, rows, calls

    # (thin) REPLAY of the v20 probe: one crypto site says "bought", dated
    # 2 Oct, and the announcements say it was scheduled for 10 Sep.
    merged = web_resolve.news_search.merge(V20, SEPT, limit=40)
    num = {a["title"]: i for i, a in enumerate(merged, 1)}
    first = {"happened": True, "event_date": "2026-10-02", "supporting_articles": [6],
             "lead_date": lead[0], "lead_what": lead[1], "event": "e", "reason": "r"}
    second = {"happened": True, "event_date": "2026-09-10", "event": "e", "reason": "r",
              "supporting_articles": [num[SEPT[0]["title"]], num[SEPT[1]["title"]]]}
    WINDOWS.clear()
    q, rows, calls = run_one({"Q0003": {"first": V20, "follow": SEPT,
                                        "answers": [first, second]}})
    check("REPLAY v20: thin evidence triggers ONE second look",
          len(calls) == 2)
    check("REPLAY v20: second look searches 9 Sep (day before schedule) to 3 Oct",
          WINDOWS[1] == (dt.date(2026, 9, 9), dt.date(2026, 10, 3)))
    check("REPLAY v20: resolves YES, corrected to the FIRST occurrence (10 Sep)",
          q["outcome"] == "1" and q["resolved_date"] == "2026-09-10")

    # (thin) second look finds nothing -> stays pending, never downgraded.
    q, rows, calls = run_one({"Q0003": {"first": V20, "follow": [],
                                        "answers": [first, NO()]}})
    check("thin + nothing more found -> still pending (not downgraded to not_yet)",
          q["status"] == "open" and rows[0]["action"] == "pending")

    # (earlier) resolved on 1 Oct by two outlets, but scheduled for 10 Sep:
    # the look back finds 10 Sep and the date is corrected.
    oct_ = [art("2026-10-01", "Reuters", "Treasury buys $5bn"),
            art("2026-10-01", "The Economic Times", "Treasury purchases below cap")]
    merged = web_resolve.news_search.merge(oct_, SEPT, limit=40)
    num = {a["title"]: i for i, a in enumerate(merged, 1)}
    a1 = {"happened": True, "event_date": "2026-10-01", "supporting_articles": [1, 2],
          "lead_date": lead[0], "lead_what": lead[1], "event": "e", "reason": "r"}
    a2 = {"happened": True, "event_date": "2026-09-10", "event": "e", "reason": "r",
          "supporting_articles": [num[SEPT[0]["title"]], num[SEPT[1]["title"]]]}
    q, rows, calls = run_one({"Q0003": {"first": oct_, "follow": SEPT,
                                        "answers": [a1, a2]}})
    check("earlier scheduled date -> look back corrects the date to 10 Sep",
          q["outcome"] == "1" and q["resolved_date"] == "2026-09-10")

    # (earlier) look back finds nothing -> keeps the 1 Oct YES, never loses it.
    q, rows, calls = run_one({"Q0003": {"first": oct_, "follow": [],
                                        "answers": [a1, NO()]}})
    check("look back fails -> the original YES (1 Oct) is kept",
          q["outcome"] == "1" and q["resolved_date"] == "2026-10-01")

    # A resolved YES with no earlier scheduled date makes no second look.
    plain = {"happened": True, "event_date": "2026-10-01", "supporting_articles": [1, 2],
             "event": "e", "reason": "r"}
    q, rows, calls = run_one({"Q0003": {"first": oct_, "answers": [plain]}})
    check("clean YES with no earlier lead -> no second look", len(calls) == 1)

    # Q0013-style: deadline passed, nothing happened, no lead -> nothing fires.
    q, rows, calls = run_one({"Q13": {"first": [art("2026-09-20", "Reuters",
                                                    "US weighs sanctions on Chinese banks")],
                                      "answers": [NO(status="no_relevant_news")]}},
                             qid="Q13")
    check("nothing happened, no lead -> stays open, no second look",
          q["status"] == "open" and len(calls) == 1)


# ---------------------------------------------------------------------------
# Part 5: the full run loop -- the v18 cases still hold
# ---------------------------------------------------------------------------

def part_five():
    sandbox = _sandbox()
    _question("QB", "2026-11-05")                    # one outlet -> pending
    _question("QC", "2027-08-26")                    # announced, no date -> open
    _question("QD", "2026-09-15", created="2026-08-20", status="resolved",
              outcome="0", resolved_date="2026-09-15",
              resolution_basis="lapsed_absence", outcome_set_by="system",
              watch_until="2026-12-14")              # lapsed NO, really happened
    _question("QE", "2026-10-20")                    # nothing found
    script = {
        "QB": {"first": [art("2026-09-28", "Reuters", "It happened")],
               "answers": [yes("2026-09-28", cite=(1,))]},
        "QC": {"first": [art("2026-10-01", "PIB", "Scheme announced", site="pib.gov.in")],
               "answers": [NO()]},
        "QD": {"first": [art("2026-09-12", "State Dept", "Done", site="state.gov")],
               "answers": [yes("2026-09-12", cite=(1,))]},
        "QE": {"first": [], "answers": []},
    }
    with Patched(fake_search_from(script)):
        r = FakeReader(script)
        web_resolve.run(r, SETTINGS, dt.date(2026, 9, 1), RunLog(TODAY), real_today=TODAY)
        check("backfill date -> no calls at all", r.calls == [])
        r = FakeReader(script)
        web_resolve.run(r, SETTINGS, TODAY, RunLog(TODAY), real_today=TODAY)
        check("single outlet -> pending",
              [p["question_id"] for p in store.read_rows(config.PENDING_RESOLUTIONS_CSV)] == ["QB"])
        check("announced with no date -> open, no follow-up",
              store.question_by_id("QC")["status"] == "open" and len(r.reads("QC")) == 1)
        check("lapsed NO flipped to YES by late web evidence",
              store.question_by_id("QD")["outcome"] == "1")
        check("no articles -> no reader call", r.reads("QE") == [])
        r2 = FakeReader(script)
        web_resolve.run(r2, SETTINGS, TODAY, RunLog(TODAY), real_today=TODAY)
        check("second run same day makes no calls", r2.calls == [])

    # Dead reader -> stop and alert at the top of the log.
    _question("QF", "2026-10-21")
    day = TODAY + dt.timedelta(days=1)
    log = RunLog(day)
    dead = {"QF": {"first": [art("2026-10-02", "AP", "x")], "answers": [None]}}
    with Patched(fake_search_from(dead)):
        web_resolve.run(FakeReader(dead), SETTINGS, day, log, real_today=day)
    check("dead reader -> red alert at the top of the log",
          log.alerts and "WEB CHECK STOPPED" in log.alerts[0]
          and "SOMETHING DID NOT RUN" in log.path.read_text())
    shutil.rmtree(sandbox, ignore_errors=True)

    # Unreachable search -> loud, recorded, retried.
    sandbox = _sandbox()
    _question("QX", "2026-12-30")
    log = RunLog(TODAY)

    def unreachable(queries, **k):
        raise news_search.SearchUnavailable("HTTP 403")
    with Patched(unreachable):
        web_resolve.run(FakeReader({"QX": {}}), SETTINGS, TODAY, log, real_today=TODAY)
    text = log.path.read_text()
    check("unreachable search -> 'WEB CHECK DID NOT RUN' at the top",
          log.alerts and "WEB CHECK DID NOT RUN" in log.alerts[0]
          and text.index("SOMETHING DID NOT RUN") < text.index("Web resolution"))
    shutil.rmtree(sandbox, ignore_errors=True)


# ---------------------------------------------------------------------------
# Part 6: the probe writes nothing; lens grounding stays off
# ---------------------------------------------------------------------------

def part_six():
    sandbox = _sandbox()
    _question("Q0003", "2026-12-31")
    lead = ("2026-09-10", "Q0003 Treasury $6 billion buyback operation")
    merged = web_resolve.news_search.merge(PROBE_ARTS, FOLLOW, limit=40)
    num = {a["title"]: i for i, a in enumerate(merged, 1)}
    script = {"Q0003": {"first": PROBE_ARTS, "follow": FOLLOW, "answers": [
        NO(*lead),
        {"happened": True, "event_date": "2026-09-10", "event": "e", "reason": "r",
         "supporting_articles": [num[FOLLOW[0]["title"]], num[PROBE[9][2]]]}]}}
    lines = []
    with Patched(fake_search_from(script)):
        action = web_resolve.probe(FakeReader(script), SETTINGS, "Q0003", TODAY,
                                   out=lines.append)
    printed = "\n".join(lines)
    check("probe shows the form, the follow-up and the decision",
          action == "resolved_yes" and "form:" in printed
          and "SECOND LOOK (scheduled date has passed" in printed
          and "DECISION: resolved_yes" in printed)
    check("probe changes nothing",
          store.question_by_id("Q0003")["status"] == "open"
          and store.read_rows(config.WEB_CHECKS_CSV) == [])
    shutil.rmtree(sandbox, ignore_errors=True)

    class Bare:
        chains = MODELS["chains"]
        grounding_models = set(MODELS["grounding_models"])

    class QuietLog:
        def info(self, *_a): pass
    settings = {**SETTINGS, "reference": {"verify_with_grounding": True}}
    check("lens grounding stays OFF",
          LensRunner(Bare(), QuietLog(), settings, "t").grounding_enabled is False)


def main() -> int:
    part_one()
    part_two()
    part_three()
    part_four()
    part_four_b()
    part_five()
    part_six()
    ok = True
    for label, passed in RESULTS:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    print(f"\n{sum(p for _, p in RESULTS)}/{len(RESULTS)} passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
