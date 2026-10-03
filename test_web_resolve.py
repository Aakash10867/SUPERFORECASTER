#!/usr/bin/env python3
"""
Offline test for web resolution (v18: code searches Google News, a model
reads, code decides). No API key, no network.

  Part 1  every code check in web_resolve.judge(), one case each
  Part 2  the RSS parser on a realistic Google News feed
  Part 3  end-to-end runs in a sandbox with a fake search and a fake reader,
          including the Q0003 scenario as it actually happened
  Part 4  failure is LOUD: an unreachable search raises a top-of-log alert
  Part 5  the probe prints everything and writes nothing
  Part 6  lens grounding stays off

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
    check("newest first", arts[0]["date"] >= arts[1]["date"])

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
# Part 3: end to end against a fake search and a fake reader
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
    web_query -> a query naming the question id (so the fake search knows
    which articles to return); web_resolve -> the scripted answer.
    A question missing from the script makes the reader fail, as a dead
    model or exhausted quota would.
    """
    def __init__(self, script):
        self.script = script
        self.calls = []
        self.chains = MODELS["chains"]
        self.grounding_models = set()
        self.stats = models.CallStats()
        self.quota = FakeQuota()

    def generate(self, task, prompt, *, expect_json=True, temperature=0.4,
                 max_output_tokens=4096, grounded=False):
        assert not grounded, "v18 never asks a model to search"
        qid = next((k for k in self.script if f"QUESTION: {k} " in prompt), None)
        self.calls.append((task, qid))
        if task == "web_query":
            return {"queries": [f"query-{qid}"]}, "fake-lite"
        if qid is None:
            self.stats.last_error = "gemini-3.5-flash-lite: rate-limited"
            return None, None
        return self.script[qid][1], "fake-lite"


def fake_search_from(script):
    def search(queries, limit=25, fetch=None):
        qid = queries[0].replace("query-", "")
        if qid in script:
            return script[qid][0], []
        return [], []
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
    store._SCHEMAS = {
        config.QUESTIONS_CSV: store.QUESTION_FIELDS,
        config.PROPOSALS_CSV: store.PROPOSAL_FIELDS,
        config.FORECASTS_CSV: store.FORECAST_FIELDS,
        config.PROCESSED_CSV: store.PROCESSED_FIELDS,
        config.WAITING_CSV: store.WAITING_FIELDS,
        config.PENDING_TAGS_CSV: store.PENDING_TAG_FIELDS,
        config.LENS_CSV: store.LENS_FIELDS,
        config.SCREENS_CSV: store.SCREEN_FIELDS,
        config.DIAGNOSTICS_CSV: store.DIAGNOSTIC_FIELDS,
        config.SYSTEM_PROPOSALS_CSV: store.SYSTEM_PROPOSAL_FIELDS,
        config.REFERENCE_INDEX_CSV: store.REFERENCE_INDEX_FIELDS,
        config.WEB_CHECKS_CSV: store.WEB_CHECK_FIELDS,
        config.PENDING_RESOLUTIONS_CSV: store.PENDING_RESOLUTION_FIELDS,
        config.COVERAGE_CSV: store.COVERAGE_FIELDS,
    }
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


def part_three():
    sandbox = _sandbox()
    log = RunLog(TODAY)

    # Q0003 as it actually happened: the 9 Sep "to buy" story, then the
    # 10-11 Sep "bought" stories from different outlets.
    _question("Q0003", "2026-12-31")
    _question("QB", "2026-11-05")          # one outlet only -> pending
    _question("QC", "2027-08-26")          # announced only -> stays open
    _question("QD", "2026-09-15", created="2026-08-20", status="resolved",
              outcome="0", resolved_date="2026-09-15",
              resolution_basis="lapsed_absence", outcome_set_by="system",
              watch_until="2026-12-14")     # lapsed NO, really happened
    _question("QE", "2026-10-20")          # nothing found at all

    script = {
        "Q0003": (ARTS[:3], yes("2026-09-10", cite=(1, 2))),
        "QB": ([art("2026-09-28", "Reuters", "It happened")],
               yes("2026-09-28", cite=(1,))),
        "QC": ([art("2026-10-01", "PIB", "Scheme announced", site="pib.gov.in")],
               {"happened": False, "status": "announced_not_yet_happened",
                "supporting_articles": [], "reason": "only announced"}),
        "QD": ([art("2026-09-12", "State Dept", "Done", site="state.gov")],
               yes("2026-09-12", cite=(1,))),
        "QE": ([], {}),
    }
    original = web_resolve.news_search.search
    web_resolve.news_search.search = fake_search_from(script)
    try:
        r = FakeReader(script)
        web_resolve.run(r, SETTINGS, dt.date(2026, 9, 1), log, real_today=TODAY)
        check("backfill date -> no calls at all", r.calls == [])

        r = FakeReader(script)
        summary = web_resolve.run(r, SETTINGS, TODAY, log, real_today=TODAY)
        q3 = store.question_by_id("Q0003")
        check("Q0003 RESOLVES YES from two outlets reporting the purchase",
              q3["status"] == "resolved" and q3["outcome"] == "1")
        check("Q0003 dated to the operation (10 Sep), not the check",
              q3["resolved_date"] == "2026-09-10")
        check("basis recorded as web_confirmed",
              q3["resolution_basis"] == "web_confirmed")
        check("single outlet -> left open, listed for you",
              store.question_by_id("QB")["status"] == "open"
              and [p["question_id"] for p in
                   store.read_rows(config.PENDING_RESOLUTIONS_CSV)] == ["QB"])
        check("announced-only stays open",
              store.question_by_id("QC")["status"] == "open")
        qd = store.question_by_id("QD")
        check("lapsed NO flipped to YES by late web evidence",
              qd["outcome"] == "1" and qd["resolved_date"] == "2026-09-12")
        check("no articles -> no reader call for that question",
              ("web_resolve", "QE") not in r.calls)
        rows = store.read_rows(config.WEB_CHECKS_CSV)
        check("every check logged, with queries and cited articles",
              len(rows) == 5 and all(x["queries"] for x in rows)
              and "CNBC" in next(x for x in rows if x["question_id"] == "Q0003")["cited_articles"])
        check("summary counts", sorted(summary["resolved"]) == ["Q0003", "QD"])

        r2 = FakeReader(script)
        web_resolve.run(r2, SETTINGS, TODAY, log, real_today=TODAY)
        check("second run same day makes no calls", r2.calls == [])

        # Reader dies (quota / dead model) -> stop, alert at top of the log.
        _question("QF", "2026-10-21")
        _question("QG", "2026-10-22")
        log2 = RunLog(TODAY + dt.timedelta(days=1))
        r3 = FakeReader({"QF": ([art("2026-10-02", "AP", "x")], None)})
        r3.script = {"QF": ([art("2026-10-02", "AP", "x")], None)}
        web_resolve.news_search.search = lambda q, **k: ([art("2026-10-02", "AP", "x")], [])
        web_resolve.run(r3, SETTINGS, TODAY + dt.timedelta(days=1), log2,
                        real_today=TODAY + dt.timedelta(days=1))
        check("dead reader -> stops after the first question",
              sum(1 for t, _ in r3.calls if t == "web_resolve") == 1)
        check("dead reader -> red alert at the top of the log",
              log2.alerts and "WEB CHECK STOPPED" in log2.alerts[0]
              and "SOMETHING DID NOT RUN" in log2.path.read_text())
    finally:
        web_resolve.news_search.search = original
    shutil.rmtree(sandbox, ignore_errors=True)


# ---------------------------------------------------------------------------
# Part 4: an unreachable search is loud
# ---------------------------------------------------------------------------

def part_four():
    sandbox = _sandbox()
    _question("Q0003", "2026-12-31")
    _question("QX", "2026-12-30")
    log = RunLog(TODAY)

    def unreachable(queries, **k):
        raise news_search.SearchUnavailable("HTTP 403")
    original = web_resolve.news_search.search
    web_resolve.news_search.search = unreachable
    try:
        r = FakeReader({"Q0003": ([], {}), "QX": ([], {})})
        web_resolve.run(r, SETTINGS, TODAY, log, real_today=TODAY)
    finally:
        web_resolve.news_search.search = original
    text = log.path.read_text()
    check("unreachable search -> alert says the web check DID NOT RUN",
          log.alerts and "WEB CHECK DID NOT RUN" in log.alerts[0])
    check("...and it sits at the very top of the log",
          text.index("SOMETHING DID NOT RUN") < text.index("Web resolution"))
    check("...and the failure is recorded, so it is retried next run",
          [x["action"] for x in store.read_rows(config.WEB_CHECKS_CSV)] == ["failed"])
    shutil.rmtree(sandbox, ignore_errors=True)


# ---------------------------------------------------------------------------
# Part 5: the probe writes nothing
# ---------------------------------------------------------------------------

def part_five():
    sandbox = _sandbox()
    _question("Q0003", "2026-12-31")
    script = {"Q0003": (ARTS[:3], yes("2026-09-10", cite=(1, 2)))}
    original = web_resolve.news_search.search
    web_resolve.news_search.search = fake_search_from(script)
    lines = []
    try:
        action = web_resolve.probe(FakeReader(script), SETTINGS, "Q0003", TODAY,
                                   out=lines.append)
    finally:
        web_resolve.news_search.search = original
    printed = "\n".join(lines)
    check("probe shows the decision", action == "resolved_yes"
          and "DECISION: resolved_yes" in printed)
    check("probe lists every article found", printed.count("Treasury") >= 3)
    check("probe changes nothing",
          store.question_by_id("Q0003")["status"] == "open"
          and store.read_rows(config.WEB_CHECKS_CSV) == [])
    shutil.rmtree(sandbox, ignore_errors=True)


# ---------------------------------------------------------------------------
# Part 6: lens grounding stays off
# ---------------------------------------------------------------------------

def part_six():
    class Bare:
        chains = MODELS["chains"]
        grounding_models = set(MODELS["grounding_models"])

    class QuietLog:
        def info(self, *_a): pass

    settings = {**SETTINGS, "reference": {"verify_with_grounding": True}}
    check("no grounding models configured", MODELS["grounding_models"] == [])
    check("lens grounding stays OFF",
          LensRunner(Bare(), QuietLog(), settings, "t").grounding_enabled is False)
    check("no 2.5 model left in any chain",
          not any("2.5" in m for c in MODELS["chains"].values() for m in c))


def main() -> int:
    part_one()
    part_two()
    part_three()
    part_four()
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
