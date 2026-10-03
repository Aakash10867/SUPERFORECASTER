#!/usr/bin/env python3
"""
Offline tests for web resolution (v22: headlines screen, Tavily full text
decides, code verifies every quote). No API key, no network.

Written from a FAILURE LIST, not only from the happy path. v16-v21 tests fed
fakes that behaved the way the code's author imagined; every live failure was
where reality differed. So each part below names the ways a step can go
wrong -- a WRONG YES (corrupts the record) or a MISSED YES (the Q0003 kind) --
and tests each one.

  Part 1  headline search and query shape
  Part 2  the evidence judge: every wrong-YES and missed-YES case
  Part 3  the Tavily client against HTTP failures
  Part 4  end to end, including replays of the real Q0003/Q0013/Q0016 probes
  Part 5  the probe writes nothing; lens grounding stays off

    python test_web_resolve.py
"""

import datetime as dt
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SUPERFORECASTER_API", "fake-key-for-offline-test")
os.environ.pop("TAVILY_API_KEY", None)            # tests must never use a real key
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import config, deep_search, models, news_search, store, web_resolve  # noqa: E402
from src.lenses import LensRunner                                              # noqa: E402
from src.runlog import RunLog                                                  # noqa: E402

TODAY = dt.date(2026, 10, 3)
SETTINGS = config.load_settings()
MODELS = config.load_models()
RESULTS: list[tuple[str, bool]] = []


def check(label, passed):
    RESULTS.append((label, bool(passed)))


def head(date, publisher, title, site=None):
    """A headline as news_search returns it."""
    return {"date": date, "publisher": publisher, "title": title,
            "site": site or publisher.lower().replace(" ", "") + ".com",
            "snippet": "", "link": ""}


def full(date, site, title, text):
    """A full-text article as deep_search returns it."""
    return {"date": date, "site": site, "publisher": site, "title": title,
            "url": f"https://{site}/a", "text": text, "snippet": text[:200],
            "full": bool(text)}


# Realistic material, modelled on what the 3 Oct probes actually returned.
CNBC_TEXT = ("WASHINGTON -- The Treasury Department on Thursday bought $5.4 billion "
             "of older long-dated bonds in its first enlarged buyback operation, "
             "short of the $6 billion it had offered to repurchase. Dealers "
             "offered fewer bonds than expected. " * 3)
PLAN_TEXT = ("The Treasury said on Wednesday it will buy back up to $6 billion of "
             "longer-dated debt on Thursday, triple the normal size. " * 4)
OFFICIAL_TEXT = ("Treasury Buyback Results. Operation date September 10, 2026. "
                 "Total par amount accepted: $5,412 million in the 20 to 30 year "
                 "sector. " * 4)

Q = {"id": "Q1", "created": "2026-08-22", "deadline": "2026-12-31",
     "question": "q", "resolution_criteria": "c"}


# ---------------------------------------------------------------------------
# Part 1: headline search and query shape
# ---------------------------------------------------------------------------

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>search</title>
<item>
  <title>Treasury buys $6 billion of older bonds in expanded buyback - Reuters</title>
  <link>https://news.google.com/rss/articles/abc</link>
  <pubDate>Thu, 10 Sep 2026 19:12:00 GMT</pubDate>
  <description>Treasury buys</description>
  <source url="https://www.reuters.com">Reuters</source>
</item>
<item>
  <title>Treasury to buy back up to $6 billion - CNBC</title>
  <pubDate>Wed, 09 Sep 2026 21:00:00 GMT</pubDate>
  <source url="https://www.cnbc.com">CNBC</source>
</item>
<item><title>No date here - Somebody</title><source url="https://x.com">Somebody</source></item>
</channel></rss>"""


def part_one():
    items = news_search.parse(FEED)
    check("RSS: dated items parsed, undated dropped", len(items) == 2)
    check("RSS: publisher/site from the feed, headline suffix removed",
          items[0]["site"] == "reuters.com"
          and items[0]["title"] == "Treasury buys $6 billion of older bonds in expanded buyback")

    old = head("2026-09-10", "Reuters", "Treasury buys $5.4 billion")
    flood = [head("2026-10-02", f"P{i}", f"Yields story {i}") for i in range(60)]
    got = news_search._interleave([[old] + flood[:5], flood], 40)
    check("RSS: an old relevant report survives a flood of new stories",
          got[0]["title"] == old["title"])

    def dead(url):
        raise OSError("blocked")
    try:
        news_search.search(["x"], fetch=dead)
        check("RSS: every fetch failing raises SearchUnavailable", False)
    except news_search.SearchUnavailable:
        check("RSS: every fetch failing raises SearchUnavailable", True)

    form = {"actor": "US Treasury", "act_past": "bought back",
            "object": "long-dated bonds $4 billion"}
    qs = web_resolve.build_queries(form, {"resolves_on": "carried_out"})
    check("queries: topic + completion, every query carries the object",
          len(qs) == 2 and all("long-dated" in x for x in qs))


# ---------------------------------------------------------------------------
# Part 2: the evidence judge -- every way to a WRONG YES or a MISSED YES
# ---------------------------------------------------------------------------

FULL = [full("2026-09-10", "cnbc.com", "Treasury buys $5.4bn", CNBC_TEXT),
        full("2026-09-09", "wsj.com", "Treasury plans $6bn buyback", PLAN_TEXT),
        full("", "home.treasury.gov", "Buyback results", OFFICIAL_TEXT),
        full("", "cnbc.com", "Undated CNBC piece", CNBC_TEXT),
        full("2026-09-11", "treasury.gov.evil.com", "Fake official", OFFICIAL_TEXT),
        full("2026-09-12", "cnbc.com", "CNBC follow-up", CNBC_TEXT)]   # T6: same site, DATED
HEADS = [head("2026-10-02", "BeInCrypto", "US Treasury Buys $6 Billion of Bonds",
              site="beincrypto.com"),
         head("2026-09-09", "CNBC", "Treasury to buy back up to $6 billion",
              site="cnbc.com"),
         head("2026-09-11", "Reuters", "Treasury buyback draws $5.4 billion", site="reuters.com")]
Q_SENT = "The Treasury Department on Thursday bought $5.4 billion of older long-dated bonds"


def ans(*evidence, date="2026-09-10", happened=True):
    return {"happened": happened, "event_date": date, "event": "e", "reason": "r",
            "evidence": [{"source": s, "quote": qt} for s, qt in evidence]}


def verdict(answer, full_=FULL, heads=HEADS, q=Q):
    return web_resolve.judge_deep(q, answer, full_, heads, TODAY, SETTINGS)


def part_two():
    # The intended paths
    a, w, f = verdict(ans(("T1", Q_SENT), ("H3", "Treasury buyback draws $5.4 billion")))
    check("RESOLVE: full-text quote (B) + a second publisher's headline (C)",
          a == "resolved_yes" and f["tier"] == "B")
    a, w, f = verdict(ans(("T3", "Total par amount accepted: $5,412 million in the 20 to 30 year sector")))
    check("RESOLVE: one official page, quote verified (A), even undated",
          a == "resolved_yes" and f["tier"] == "A")

    # WRONG-YES risks
    a, w, f = verdict(ans(("T1", "The Treasury bought $9 billion of bonds on Thursday morning"),
                          ("H3", "Treasury buyback draws $5.4 billion")))
    check("WRONG-YES guard: invented quote (not in the text) is thrown away",
          a != "resolved_yes" and f["evidence"][0]["ok"] is False)
    a, w, f = verdict(ans(("T2", "it will buy back up to $6 billion of longer-dated debt on Thursday"),
                          ("H3", "Treasury buyback draws $5.4 billion")))
    check("WRONG-YES guard: a 'will buy' article dated BEFORE the event never counts",
          a != "resolved_yes" and "before the event" in f["evidence"][0]["why"])
    # Both CNBC articles are dated and verified, so ONLY de-duplication
    # stands between this and a YES. (An earlier draft used an undated
    # article, which was rejected for its missing date -- the test passed
    # without ever exercising de-duplication.)
    a, w, f = verdict(ans(("T1", Q_SENT), ("T6", Q_SENT)))
    check("WRONG-YES guard: the same publisher twice is ONE publisher",
          a == "pending" and f["sites"] == ["cnbc.com"]
          and sum(e["ok"] for e in f["evidence"]) == 2)
    a, w, f = verdict(ans(("T5", "Total par amount accepted: $5,412 million in the 20 to 30 year sector")))
    check("WRONG-YES guard: treasury.gov.evil.com is not official",
          a == "pending" and f["tier"] == "B")
    a, w, f = verdict(ans(("T1", "Treasury Department on Thursday bought"),
                          ("H3", "Treasury buyback draws $5.4 billion")))
    check("WRONG-YES guard: a too-short quote does not count",
          a != "resolved_yes" and f["evidence"][0]["ok"] is False)
    a, w, f = verdict(ans(("T4", Q_SENT), ("H3", "Treasury buyback draws $5.4 billion")))
    check("WRONG-YES guard: an undated non-official article does not count",
          a != "resolved_yes" and "no date" in f["evidence"][0]["why"])
    a, w, f = verdict(ans(("H1", "US Treasury Buys $6 Billion of Bonds"),
                          ("H3", "Treasury buyback draws $5.4 billion")))
    check("WRONG-YES guard: headlines alone NEVER resolve, however many",
          a == "pending" and f["tier"] == "C")
    a, w, f = verdict(ans(("T9", Q_SENT), ("Z1", Q_SENT), ("H0", "x")))
    check("WRONG-YES guard: citations to sources that don't exist are rejected",
          a == "pending" and all(not e["ok"] for e in f["evidence"]))
    check("WRONG-YES guard: future event date -> not yet",
          verdict(ans(("T1", Q_SENT), date="2026-11-04"))[0] == "not_yet")
    check("WRONG-YES guard: event after the deadline -> not a YES",
          verdict(ans(("T1", Q_SENT)), q={**Q, "deadline": "2026-09-05"})[0] == "not_yet")
    # With B + C this WOULD resolve; only the born-resolved check stops it.
    a, w, f = verdict(ans(("T1", Q_SENT), ("H3", "Treasury buyback draws $5.4 billion"),
                          date="2026-08-19"))
    check("WRONG-YES guard: event before the question existed -> you decide",
          a == "pending" and "BEFORE the question was created" in w)
    check("WRONG-YES guard: says happened but no date -> you decide",
          verdict(ans(("T1", Q_SENT), date=""))[0] == "pending")
    check("WRONG-YES guard: reader says not happened -> not yet",
          verdict(ans(happened=False))[0] == "not_yet")
    check("WRONG-YES guard: no parseable answer -> failed, not a guess",
          verdict(None)[0] == "failed")

    # MISSED-YES risks
    curly = Q_SENT.replace("$5.4", "$5.4").replace("Thursday", "Thursday") \
                  .replace("The Treasury", "The “Treasury”")
    a, w, f = verdict(ans(("T1", curly), ("H3", "Treasury buyback draws $5.4 billion")))
    check("MISSED-YES guard: curly quotes / punctuation differences still verify",
          a == "resolved_yes")
    a, w, f = verdict(ans(("T1", "  " + Q_SENT.upper() + ".  "),
                          ("H3", "Treasury buyback draws $5.4 billion")))
    check("MISSED-YES guard: case and spacing differences still verify", a == "resolved_yes")
    a, w, f = verdict(ans(("t1", Q_SENT), ("[H3]", "Treasury buyback draws $5.4 billion")))
    check("MISSED-YES guard: 't1' and '[H3]' source labels are understood",
          a == "resolved_yes")
    check("MISSED-YES guard: a B with no second publisher waits for you, not dropped",
          verdict(ans(("T1", Q_SENT)))[0] == "pending")


# ---------------------------------------------------------------------------
# Part 3: the Tavily client against HTTP failures
# ---------------------------------------------------------------------------

def tav_result(**kw):
    base = {"title": "Treasury buys $5.4bn", "url": "https://www.cnbc.com/x",
            "content": "snippet", "raw_content": CNBC_TEXT,
            "published_date": "Thu, 10 Sep 2026 19:00:00 GMT"}
    base.update(kw)
    return base


def part_three():
    sent = []

    def ok_post(url, body, headers):
        sent.append((url, body, headers))
        return 200, {"results": [tav_result()]}, ""
    c = deep_search.TavilyClient(key="tvly-test", post=ok_post)
    arts = c.search("q", (dt.date(2026, 9, 9), dt.date(2026, 9, 14)))
    b = sent[0][1]
    check("Tavily: request shape matches the docs (topic, raw text, dates, published date)",
          sent[0][0].endswith("/search") and b["topic"] == "news"
          and b["include_raw_content"] == "text" and b["include_published_date"] is True
          and b["start_date"] == "2026-09-09" and b["end_date"] == "2026-09-14"
          and sent[0][2]["Authorization"] == "Bearer tvly-test")
    check("Tavily: RFC-2822 date and site parsed", arts[0]["date"] == "2026-09-10"
          and arts[0]["site"] == "cnbc.com" and arts[0]["full"])
    check("Tavily: ISO dates parse too",
          deep_search.parse_date("2026-09-10T19:00:00Z") == dt.date(2026, 9, 10))
    check("Tavily: garbage date -> None, not a crash", deep_search.parse_date("soon") is None)

    stub = deep_search.to_article(tav_result(raw_content="Subscribe to read"))
    check("Tavily: a paywall stub is NOT full text", stub["full"] is False and stub["text"] == "")

    try:
        deep_search.TavilyClient(key="").search("q")
        check("Tavily: no key -> DeepUnavailable(no_key)", False)
    except deep_search.DeepUnavailable as e:
        check("Tavily: no key -> DeepUnavailable(no_key)", e.kind == "no_key")

    for status, kind in ((401, "bad_key"), (432, "out_of_credits"),
                         (433, "out_of_credits"), (429, "rate_limited"), (500, "error")):
        c = deep_search.TavilyClient(key="k", post=lambda u, b, h, s=status: (s, None, "x"))
        try:
            c.search("q")
            got = None
        except deep_search.DeepUnavailable as e:
            got = e.kind
        check(f"Tavily: HTTP {status} -> {kind}", got == kind)

    c = deep_search.TavilyClient(key="k", post=lambda u, b, h: (432, None, ""))
    try:
        c.search("q")
    except deep_search.DeepUnavailable:
        pass
    calls_before = c.calls
    try:
        c.search("q2")
    except deep_search.DeepUnavailable as e:
        check("Tavily: after out-of-credits, later searches stop without calling",
              c.calls == calls_before and e.kind == "out_of_credits")

    seen = []

    def picky(url, body, headers):
        seen.append(body)
        if "topic" in body:
            return 400, None, "unknown field: topic"
        return 200, {"results": [tav_result()]}, ""
    c = deep_search.TavilyClient(key="k", post=picky)
    arts = c.search("q", (dt.date(2026, 9, 1), dt.date(2026, 9, 5)))
    check("Tavily: a rejected parameter (400) retries ONCE with the minimum, and says so",
          len(seen) == 2 and "topic" not in seen[1] and arts
          and "_degraded" in arts[0])

    def net_down(url, body, headers):
        raise OSError("connection reset")
    try:
        deep_search.TavilyClient(key="k", post=net_down).search("q")
        check("Tavily: network error -> DeepUnavailable(error)", False)
    except deep_search.DeepUnavailable as e:
        check("Tavily: network error -> DeepUnavailable(error)", e.kind == "error")

    # v23: social sites -- asked to exclude, AND filtered if returned anyway.
    sent2 = []

    def social(url, body, headers):
        sent2.append(body)
        return 200, {"results": [tav_result(url="https://www.facebook.com/p/1"),
                                 tav_result(url="https://m.youtube.com/watch"),
                                 tav_result(url="https://x.com/a/status/1"),
                                 tav_result(url="https://www.tax.com/news"),
                                 tav_result()]}, ""
    arts = deep_search.TavilyClient(key="k", post=social).search("q")
    check("EXCLUDE: social sites named in the request",
          set(sent2[0]["exclude_domains"]) >= {"facebook.com", "instagram.com",
                                               "youtube.com", "x.com"})
    check("EXCLUDE: filtered in code even when Tavily returns them anyway",
          sorted(a["site"] for a in arts) == ["cnbc.com", "tax.com"])
    check("EXCLUDE: 'tax.com' is not mistaken for x.com",
          not deep_search.excluded("tax.com", deep_search.DEFAULT_EXCLUDE))

    u = deep_search.TavilyClient(key="k", get=lambda u, h: (200, {
        "key": {"usage": 120, "limit": 1000}, "account": {}}, "")).usage()
    check("Tavily usage: credits left computed", u == {"used": 120, "limit": 1000, "left": 880})
    check("Tavily usage: endpoint down -> unknown (None), not a crash",
          deep_search.TavilyClient(key="k", get=lambda u, h: (500, None, "")).usage() is None)


# ---------------------------------------------------------------------------
# Part 4: end to end
# ---------------------------------------------------------------------------

class FakeQuota:
    key_names = ["FAKE_KEY"]
    def remaining(self, *a): return 500
    def used(self, *a): return 0
    def record(self, *a, **k): pass
    def flush(self): pass
    def summary(self): return ""


class FakeReader(models.ModelRouter):
    """web_query -> form (actor = question id); headline / full-text reads ->
    script[qid]["h"] / ["d"], told apart by the prompt."""
    def __init__(self, script):
        self.script = script
        self.calls = []
        self.chains = MODELS["chains"]
        self.grounding_models = set()
        self.stats = models.CallStats()
        self.quota = FakeQuota()

    def generate(self, task, prompt, *, expect_json=True, temperature=0.4,
                 max_output_tokens=4096, grounded=False):
        qid = next((k for k in self.script if f"QUESTION: {k} " in prompt), None)
        kind = ("web_query" if task == "web_query"
                else "corrob" if "Your ONLY job" in prompt
                else "deep" if "FULL TEXTS:" in prompt else "headline")
        self.calls.append((kind, qid, prompt))
        if kind == "web_query":
            return {"actor": qid, "act_past": "did", "object": "thing"}, "fake"
        if kind == "corrob":
            ans_ = (self.script.get(qid) or {}).get("c", {"evidence": []})
            if ans_ is None:
                self.stats.last_error = "fake: corroboration failed"
                return None, None
            return ans_, "fake"
        ans_ = (self.script.get(qid) or {}).get("d" if kind == "deep" else "h")
        if ans_ is None:
            self.stats.last_error = "fake: no answer"
            return None, None
        return ans_, "fake"

    def n(self, kind, qid=None):
        return sum(1 for k, q, _ in self.calls if k == kind and (qid is None or q == qid))


class FakeTavily:
    def __init__(self, script, key="tvly-test", left=900, fail=None):
        self.script, self.key, self.left, self.fail = script, key, left, fail
        self.calls, self.windows, self.disabled = 0, [], ""

    def usage(self):
        return {"used": 1000 - self.left, "limit": 1000, "left": self.left}

    def search(self, query, window=None):
        if self.disabled:
            raise deep_search.DeepUnavailable(self.disabled)
        self.calls += 1
        self.windows.append(window)
        if self.fail:
            self.disabled = self.fail
            raise deep_search.DeepUnavailable(self.fail, "fake")
        qid = query.split()[0]
        return list((self.script.get(qid) or {}).get("full", []))


def rss_from(script):
    def search(queries, limit=40, fetch=None, window=None):
        qid = queries[0].split()[0]
        return list((script.get(qid) or {}).get("heads", [])), []
    return search


class Patched:
    def __init__(self, fn):
        self.fn = fn
    def __enter__(self):
        self.orig = web_resolve.news_search.search
        web_resolve.news_search.search = self.fn
    def __exit__(self, *a):
        web_resolve.news_search.search = self.orig


def _sandbox() -> Path:
    sandbox = Path(tempfile.mkdtemp(prefix="sf-web-"))
    config.DATA = sandbox / "data"
    config.LOGS = sandbox / "logs"
    names = {"QUESTIONS_CSV": "questions.csv", "WEB_CHECKS_CSV": "web_checks.csv",
             "PENDING_RESOLUTIONS_CSV": "pending_resolutions.csv",
             "PROPOSALS_CSV": "proposals.csv", "FORECASTS_CSV": "forecasts.csv",
             "PROCESSED_CSV": "processed.csv", "WAITING_CSV": "waiting_list.csv",
             "PENDING_TAGS_CSV": "pending_tags.csv", "SCREENS_CSV": "screens.csv",
             "LENS_CSV": "lens_outputs.csv", "DIAGNOSTICS_CSV": "diagnostics.csv",
             "SYSTEM_PROPOSALS_CSV": "system_proposals.csv", "COVERAGE_CSV": "coverage.csv"}
    for attr, fname in names.items():
        setattr(config, attr, config.DATA / fname)
    config.REFERENCE = config.DATA / "reference"
    config.REFERENCE_INDEX_CSV = config.REFERENCE / "index.csv"
    config.RUNS = config.DATA / "runs"
    config.REPORTS = config.DATA / "reports"
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


def _q(qid, deadline="2026-12-31", created="2026-08-22", **extra):
    row = {"id": qid, "question": f"{qid} test question", "domain": "global_macro",
           "bucket": "medium", "created": created, "deadline": deadline,
           "primary_tag": "t", "resolution_criteria": "criteria", "status": "open",
           "shape": "point", "admitted_by": "test", "resolves_on": "carried_out"}
    row.update(extra)
    store.append_row(config.QUESTIONS_CSV, row)


def NO(status="no_relevant_news", **lead):
    return {"happened": False, "status": status, "evidence": [], "reason": "r", **lead}


def run_day(script, day=TODAY, tav=None, dry=False, real_today=None):
    # real_today defaults to `day`; the backfill test passes a DIFFERENT one.
    # (An earlier draft hard-wired real_today=day, so the backfill guard could
    # never trigger and its test could never fail.)
    reader = FakeReader(script)
    tav = tav if tav is not None else FakeTavily(script)
    log = RunLog(day)
    with Patched(rss_from(script)):
        summary = web_resolve.run(reader, SETTINGS, day, log, dry_run=dry,
                                  real_today=real_today or day, deep=tav)
    return reader, tav, log, summary


def part_four():
    # -- REPLAY Q0003 (3 Oct): one crypto headline said "bought", dated 2 Oct.
    #    v22: that headline only ROUTES; full text finds the 10 Sep operation.
    sb = _sandbox()
    _q("Q0003")
    heads = [head("2026-09-09", "CNBC", "Q0003 Treasury to buy back up to $6 billion", site="cnbc.com"),
             head("2026-10-02", "BeInCrypto", "US Treasury Buys $6 Billion of Bonds", site="beincrypto.com")]
    fulls = [full("2026-09-10", "cnbc.com", "Treasury buys $5.4bn", CNBC_TEXT),
             full("2026-09-09", "wsj.com", "Treasury plans $6bn", PLAN_TEXT)]
    script = {"Q0003": {
        "heads": heads,
        "h": {"happened": True, "event_date": "2026-10-02", "event": "bought $6bn", "reason": "r"},
        "full": fulls,
        "d": ans(("T1", Q_SENT), ("H2", "US Treasury Buys $6 Billion of Bonds"))}}
    reader, tav, log, s = run_day(script)
    q = store.question_by_id("Q0003")
    check("REPLAY Q0003: resolves YES on its own", q["outcome"] == "1")
    check("REPLAY Q0003: dated 10 Sep (first operation), not 2 Oct",
          q["resolved_date"] == "2026-09-10")
    check("REPLAY Q0003: full text searched from creation (21 Aug) to today",
          tav.windows[0] == (dt.date(2026, 8, 21), TODAY))
    row = store.read_rows(config.WEB_CHECKS_CSV)[-1]
    check("REPLAY Q0003: the log records trigger, tier and the verified quotes",
          row["deep"] == "headline" and row["tier"] == "B" and "B ok" in row["quotes"])
    shutil.rmtree(sb, ignore_errors=True)

    # -- Same day, but the reader INVENTS its quote -> must not resolve.
    sb = _sandbox()
    _q("Q0003")
    script["Q0003"]["d"] = ans(("T1", "Treasury bought $6 billion on Thursday, its largest ever"),
                               ("H2", "US Treasury Buys $6 Billion of Bonds"))
    run_day(script)
    q = store.question_by_id("Q0003")
    check("WRONG-YES end to end: invented quote -> stays open, goes to you",
          q["status"] == "open"
          and [p["question_id"] for p in store.read_rows(config.PENDING_RESOLUTIONS_CSV)] == ["Q0003"])
    shutil.rmtree(sb, ignore_errors=True)

    # -- REPLAY Q0013: deadline passed, Russian/Turkish banks sanctioned, not
    #    Chinese. Headlines: no. Weekly sweep reads full text once: no.
    sb = _sandbox()
    _q("Q0013", deadline="2026-09-30", created="2026-08-26", resolves_on="announced")
    script = {"Q0013": {
        "heads": [head("2026-09-14", "CNBC", "Treasury hits Russia's VTB Bank over Iran ties")],
        "h": NO(),
        "full": [full("2026-08-26", "fortune.com", "China's banks got a pass",
                      "The U.S. declared an onslaught on Iran. China's banks got a pass. " * 10)],
        "d": NO()}}
    reader, tav, log, s = run_day(script)
    check("REPLAY Q0013: not resolved", store.question_by_id("Q0013")["status"] == "open")
    check("REPLAY Q0013: first run does its weekly full-text sweep", tav.calls == 1)
    reader2, tav2, log2, s2 = run_day(script, day=TODAY + dt.timedelta(days=1))
    check("COST: next day, no trigger and sweep not due -> no Tavily credit spent",
          tav2.calls == 0)
    reader3, tav3, _, _ = run_day(script, day=TODAY + dt.timedelta(days=7))
    check("COST: seven days later the sweep comes round again", tav3.calls == 1)
    shutil.rmtree(sb, ignore_errors=True)

    # -- REPLAY Q0016: the MPC meeting is an OCCASION.
    sb = _sandbox()
    _q("Q16", created="2026-09-21", resolves_on="announced")
    fc = [head("2026-10-03", "SBI", "Q16 RBI may raise repo rate in October")]
    script = {"Q16": {"heads": fc,
                      "h": NO("announced_not_yet_happened", lead_date="2026-10-05",
                              lead_what="Q16 RBI MPC meeting", lead_kind="occasion"),
                      "full": [], "d": NO()}}
    run_day(script)
    check("REPLAY Q0016: meeting stored as an occasion",
          store.question_by_id("Q16")["awaiting"] == "2026-10-05 [occasion]: Q16 RBI MPC meeting")
    script["Q16"]["h"] = NO()
    reader, tav, log, s = run_day(script, day=dt.date(2026, 10, 6))
    check("Q0016 on 6 Oct: occasion read in full, nothing yet -> not_yet, NO alarm",
          store.read_rows(config.WEB_CHECKS_CSV)[-1]["action"] == "not_yet"
          and log.flag_count == 0 and tav.windows == [(dt.date(2026, 10, 4), dt.date(2026, 10, 6))])
    check("COST: an occasion with nothing yet spends ONE credit, no undated retry",
          tav.calls == 1)
    script["Q16"]["full"] = [full("2026-10-07", "reuters.com", "RBI raises repo rate",
                                  "The Reserve Bank of India on Wednesday raised its key repo rate by 25 basis points to 5.50%. " * 5)]
    script["Q16"]["heads"] = fc + [head("2026-10-07", "Livemint", "RBI hikes repo rate to 5.5%", site="livemint.com")]
    script["Q16"]["d"] = ans(("T1", "The Reserve Bank of India on Wednesday raised its key repo rate by 25 basis points"),
                             ("H2", "RBI hikes repo rate to 5.5%"), date="2026-10-07")
    run_day(script, day=dt.date(2026, 10, 8))
    q = store.question_by_id("Q16")
    check("Q0016 on 8 Oct: hike found in full text -> YES dated 7 Oct",
          q["outcome"] == "1" and q["resolved_date"] == "2026-10-07")
    shutil.rmtree(sb, ignore_errors=True)

    # -- An announced ACT whose date passes unreported -> goes to you.
    sb = _sandbox()
    _q("QA", awaiting="2026-09-10: QA buyback operation")
    script = {"QA": {"heads": [head("2026-09-09", "CNBC", "QA to buy Thursday")],
                     "h": NO(), "full": [], "d": NO()}}
    run_day(script)
    check("an ACT due and unreported -> pending 'was due'",
          "was due on 2026-09-10" in store.read_rows(config.WEB_CHECKS_CSV)[-1]["why"])
    shutil.rmtree(sb, ignore_errors=True)

    # -- Failure modes of the full-text step itself.
    sb = _sandbox()
    _q("QK")
    script = {"QK": {"heads": [head("2026-10-02", "Blog", "QK it happened")],
                     "h": {"happened": True, "event_date": "2026-10-02", "reason": "r"},
                     "full": [], "d": None}}
    reader, tav, log, s = run_day(script, tav=FakeTavily(script, key=""))
    check("NO KEY: red alert at the top, headline 'happened' waits for you, nothing resolves",
          log.alerts and "TAVILY_API_KEY" in log.alerts[0]
          and store.question_by_id("QK")["status"] == "open"
          and store.read_rows(config.WEB_CHECKS_CSV)[-1]["action"] == "pending")
    shutil.rmtree(sb, ignore_errors=True)

    sb = _sandbox()
    _q("QK"), _q("QL", deadline="2026-12-30")
    script["QL"] = dict(script["QK"])
    script["QL"]["heads"] = [head("2026-10-02", "Blog", "QL it happened")]
    tav = FakeTavily(script, fail="out_of_credits")
    reader, tav, log, s = run_day(script, tav=tav)
    check("OUT OF CREDITS: alerted once, and Tavily not called again this run",
          sum("out_of_credits" in a for a in log.alerts) == 1 and tav.calls == 1)
    check("OUT OF CREDITS: both questions wait as pending, none resolved",
          all(store.question_by_id(x)["status"] == "open" for x in ("QK", "QL")))
    shutil.rmtree(sb, ignore_errors=True)

    sb = _sandbox()
    _q("QS")                                    # sweep-only question
    _q("QH", deadline="2026-12-30")             # headline-triggered question
    script = {"QS": {"heads": [], "h": NO(), "full": [], "d": NO()},
              "QH": {"heads": [head("2026-10-02", "AP", "QH it happened")],
                     "h": {"happened": True, "event_date": "2026-10-02", "reason": "r"},
                     "full": [], "d": NO()}}
    tav = FakeTavily(script, left=50)          # below the reserve of 100
    reader, tav, log, s = run_day(script, tav=tav)
    check("LOW CREDITS: sweeps paused, news-triggered reads still run",
          tav.calls == 1 and log.flag_count >= 1)
    tav = FakeTavily(script, left=3)           # below the minimum of 5
    _sandbox(); _q("QS"); _q("QH", deadline="2026-12-30")
    reader, tav, log, s = run_day(script, tav=tav)
    check("CREDITS NEARLY GONE: red alert, no Tavily calls at all",
          tav.calls == 0 and any("FULL-TEXT CHECK OFF" in a for a in log.alerts))
    shutil.rmtree(sb, ignore_errors=True)

    sb = _sandbox()
    _q("QB")
    script = {"QB": {"heads": [], "h": NO(), "full": [], "d": NO()}}
    reader, tav, log, s = run_day(script, day=dt.date(2026, 9, 1), real_today=TODAY)
    check("BACKFILL date: no reads, no credits", reader.calls == [] and tav.calls == 0)
    reader, tav, log, s = run_day(script, dry=True)
    check("DRY RUN: nothing written", store.read_rows(config.WEB_CHECKS_CSV) == [])
    run_day(script)
    reader, tav, log, s = run_day(script)
    check("ONCE A DAY: second run the same day makes no calls",
          reader.calls == [] and tav.calls == 0)
    shutil.rmtree(sb, ignore_errors=True)

    sb = _sandbox()
    _q("QR")
    with open(config.RESOLUTIONS_CSV, "a", encoding="utf-8") as fh:
        fh.write("QR,reopen,,wrong web YES\n")
    script = {"QR": {"heads": [head("2026-10-02", "AP", "QR done")],
                     "h": {"happened": True, "event_date": "2026-10-02", "reason": "r"},
                     "full": [], "d": NO()}}
    reader, tav, log, s = run_day(script)
    check("YOUR WORD IS FINAL: a question in resolutions.csv is never checked",
          reader.calls == [] and tav.calls == 0)
    shutil.rmtree(sb, ignore_errors=True)


# ---------------------------------------------------------------------------
# Part 4b (v23): corroboration -- and every way it could go wrong
# ---------------------------------------------------------------------------

CHASE_SENT = ("The Treasury Department completed a $6 billion buyback of Treasury "
              "securities maturing in 10 to 20 years on September 10.")
CHASE = full("2026-09-22", "chase.com", "Why the Treasury's $6 Billion Bond Buyback Matters",
             ("Investors have watched the Treasury closely this autumn. " + CHASE_SENT + " ") * 3)
PROBE_HEADS = [   # from the live v22 probe of 3 Oct
    head("2026-09-22", "Chase Bank", "Why the Treasury's $6 billion bond buyback matters for investors", site="chase.com"),
    head("2026-10-01", "KuCoin", "U.S. Treasury Expands Long-End Buyback Amid Rising Bond Yields", site="kucoin.com"),
    head("2026-09-09", "CNBC", "Treasury Department to buy back up to $6 billion in longer-term debt, triple the normal level", site="cnbc.com"),
    head("2026-08-19", "Reuters", "Treasury Secretary Bessent doubles US long-bond buybacks in the face of surging yields", site="reuters.com"),
    head("2026-09-09", "Financial Times", "Treasury yields jump as Scott Bessent's $6bn buyback plan disappoints investors", site="ft.com"),
    head("2026-09-09", "WSJ", "U.S. Treasury Plans $6 Billion Buyback, Yields Rise", site="wsj.com"),
    head("2026-10-02", "BeInCrypto", "US Treasury Buys $6 Billion of Bonds as Bitcoin Battles 24-Year-High Yields", site="beincrypto.com"),
]
PROBE_FULL = [full("2026-09-09", "cnbc.com", "Treasury to buy back up to $6 billion", PLAN_TEXT),
              CHASE]


def q3_script(corrob):
    return {"Q0003": {
        "heads": [dict(h, title=("Q0003 " + h["title"]) if i == 0 else h["title"])
                  for i, h in enumerate(PROBE_HEADS)],
        "h": {"happened": True, "event_date": "2026-10-02", "event": "bought $6bn", "reason": "r"},
        "full": PROBE_FULL,
        "d": ans(("T2", CHASE_SENT)),           # what the live reader actually gave
        "c": corrob}}


def part_four_b():
    def one(corrob, extra_q=False):
        sb = _sandbox()
        _q("Q0003")
        script = q3_script(corrob)
        if extra_q:
            # Later deadline, so QZ is checked AFTER Q0003 -- the point is to
            # show the run carries on past a failed corroboration.
            _q("QZ", deadline="2027-06-30")
            script["QZ"] = {"heads": [], "h": NO(), "full": [], "d": NO()}
        reader, tav, log, s = run_day(script)
        q = store.question_by_id("Q0003")
        rows = store.read_rows(config.WEB_CHECKS_CSV)
        out = (q, rows, reader, tav, log)
        shutil.rmtree(sb, ignore_errors=True)
        return out

    q, rows, reader, tav, log = one({"evidence": [
        {"source": "H7", "quote": "US Treasury Buys $6 Billion of Bonds as Bitcoin Battles 24-Year-High Yields"}]})
    check("REPLAY v22 probe: corroboration finds BeInCrypto -> Q0003 RESOLVES",
          q["outcome"] == "1")
    check("REPLAY v22 probe: dated 10 Sep (the verified Chase sentence)",
          q["resolved_date"] == "2026-09-10")
    check("REPLAY v22 probe: corroboration costs no Tavily credit",
          tav.calls == 1 and reader.n("corrob") == 1)

    q, rows, *_ = one({"evidence": [{"source": "H7",
                                     "quote": "US Treasury Buys $9 Billion of Bonds in record operation"}]})
    check("CORROB WRONG-YES: invented corroborating quote -> still pending",
          q["status"] == "open" and rows[-1]["action"] == "pending")

    q, rows, *_ = one({"evidence": [{"source": "H1",
                                     "quote": "Why the Treasury's $6 billion bond buyback matters for investors"}]})
    check("CORROB WRONG-YES: same publisher (Chase headline) is not a second publisher",
          q["status"] == "open")

    q, rows, *_ = one({"evidence": [{"source": "H3",
                                     "quote": "Treasury Department to buy back up to $6 billion in longer-term debt, triple the normal level"}]})
    check("CORROB WRONG-YES: a 'to buy' headline from BEFORE the event is rejected",
          q["status"] == "open" and "before the event" in rows[-1]["quotes"])

    q, rows, reader, tav, log = one(None, extra_q=True)
    check("CORROB FAILURE: call fails -> stays pending, run carries on to the next question",
          q["status"] == "open"
          and [(r["question_id"], r["action"]) for r in rows]
              == [("Q0003", "pending"), ("QZ", "not_yet")]
          and not log.alerts)

    # Not called when it cannot help (cost): already resolved, or not happened.
    sb = _sandbox()
    _q("Q0003")
    script = q3_script({"evidence": []})
    script["Q0003"]["d"] = ans(("T2", CHASE_SENT),
                               ("H7", "US Treasury Buys $6 Billion of Bonds as Bitcoin Battles 24-Year-High Yields"))
    reader, *_ = run_day(script)
    check("CORROB COST: not called when the first reading already resolves",
          reader.n("corrob") == 0 and store.question_by_id("Q0003")["outcome"] == "1")
    shutil.rmtree(sb, ignore_errors=True)
    sb = _sandbox()
    _q("Q0003")
    script = q3_script({"evidence": []})
    script["Q0003"]["d"] = NO()
    reader, *_ = run_day(script)
    check("CORROB COST: not called when nothing was verified", reader.n("corrob") == 0)
    shutil.rmtree(sb, ignore_errors=True)


# ---------------------------------------------------------------------------
# Part 5: probe safety, lens grounding
# ---------------------------------------------------------------------------

def part_five():
    sb = _sandbox()
    _q("Q0003")
    script = {"Q0003": {
        "heads": [head("2026-10-02", "BeInCrypto", "US Treasury Buys $6 Billion of Bonds", site="beincrypto.com")],
        "h": {"happened": True, "event_date": "2026-10-02", "reason": "r"},
        "full": [full("2026-09-10", "cnbc.com", "Treasury buys $5.4bn", CNBC_TEXT)],
        "d": ans(("T1", Q_SENT), ("H1", "US Treasury Buys $6 Billion of Bonds"))}}
    lines = []
    with Patched(rss_from(script)):
        action = web_resolve.probe(FakeReader(script), SETTINGS, "Q0003", TODAY,
                                   out=lines.append, deep=FakeTavily(script))
    text = "\n".join(lines)
    check("PROBE: shows trigger, each full article's length, and every quote's verdict",
          action == "resolved_yes" and "trigger=headline" in text
          and "chars" in text and "OK tier B" in text)
    check("PROBE: writes nothing", store.question_by_id("Q0003")["status"] == "open"
          and store.read_rows(config.WEB_CHECKS_CSV) == [])
    shutil.rmtree(sb, ignore_errors=True)

    class Bare:
        chains = MODELS["chains"]
        grounding_models = set(MODELS["grounding_models"])

    class QuietLog:
        def info(self, *_a): pass
    check("lens grounding stays OFF",
          LensRunner(Bare(), QuietLog(), {**SETTINGS, "reference": {
              "verify_with_grounding": True}}, "t").grounding_enabled is False)


def main() -> int:
    part_one()
    part_two()
    part_three()
    part_four()
    part_four_b()
    part_five()
    ok = True
    for label, passed in RESULTS:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    print(f"\n{sum(p for _, p in RESULTS)}/{len(RESULTS)} passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
