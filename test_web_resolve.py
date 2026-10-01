#!/usr/bin/env python3
"""
Offline test for web resolution (v16). No API key, no quota.

Part 1 tests every code check in web_resolve.judge() on its own -- that
function is the whole safety story, so each rule gets a case.

Part 2 runs web_resolve.run() end to end in a sandbox against a fake search
model, including the cases that matter operationally: once-a-day, the backfill
guard, running out of quota, a lapsed question flipping to YES, and your own
resolutions.csv clearing a pending item.

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

from src import config, models, store, web_resolve  # noqa: E402
from src.lenses import LensRunner                   # noqa: E402
from src.runlog import RunLog                       # noqa: E402

TODAY = dt.date(2026, 10, 2)
SETTINGS = config.load_settings()
MODELS = config.load_models()

RESULTS: list[tuple[str, bool]] = []


def check(label, passed):
    RESULTS.append((label, bool(passed)))


# ---------------------------------------------------------------------------
# Part 1: the code checks
# ---------------------------------------------------------------------------

Q = {"id": "Q1", "created": "2026-08-22", "deadline": "2026-12-31",
     "question": "q", "resolution_criteria": "c"}


def g(*sites, fired=True):
    return {"fired": fired, "sources": list(sites), "queries": ["x"]}


def yes(date="2026-09-10", **extra):
    return {"happened": True, "status": "happened", "event": "e",
            "event_date": date, "evidence": "ev", "reason": "r", **extra}


def act(answer, grounding, q=Q):
    return web_resolve.judge(q, answer, grounding, TODAY, SETTINGS)[0]


def part_one():
    check("two different sites -> YES",
          act(yes(), g("cnbc.com", "qz.com")) == "resolved_yes")
    check("one official site -> YES",
          act(yes(), g("home.treasury.gov")) == "resolved_yes")
    check("Indian government suffix counts as official",
          act(yes(), g("pib.gov.in")) == "resolved_yes")
    check("named official domain counts (rbi.org.in)",
          act(yes(), g("rbi.org.in")) == "resolved_yes")
    check("one unofficial site -> pending",
          act(yes(), g("reuters.com")) == "pending")
    check("same site twice is ONE source (www. and case ignored)",
          act(yes(), g("www.Reuters.com", "reuters.com")) == "pending")
    check("'gov' inside a name is not official (govtnews.com)",
          act(yes(), g("govtnews.com")) == "pending")
    check("search did not fire -> ignored, not resolved",
          act(yes(), g("cnbc.com", "qz.com", fired=False)) == "not_yet")
    check("fired but no sources -> ignored",
          act(yes(), g()) == "not_yet")
    check("reader says not happened -> not yet",
          act({"happened": False, "status": "announced_not_yet_happened"},
              g("cnbc.com", "qz.com")) == "not_yet")
    check("reader's 'true' as a string is understood",
          act(yes(happened="true"), g("cnbc.com", "qz.com")) == "resolved_yes")
    check("happened but no date -> pending",
          act(yes(date=""), g("cnbc.com", "qz.com")) == "pending")
    check("future date (announced, not happened) -> not yet",
          act(yes(date="2026-11-04"), g("cnbc.com", "qz.com")) == "not_yet")
    check("after the deadline -> not a YES",
          act(yes(date="2026-10-01"), g("cnbc.com", "qz.com"),
              q={**Q, "deadline": "2026-09-30"}) == "not_yet")
    check("before the question existed -> pending (born resolved)",
          act(yes(date="2026-08-19"), g("cnbc.com", "qz.com")) == "pending")
    check("event on the creation day itself is allowed",
          act(yes(date="2026-08-22"), g("cnbc.com", "qz.com")) == "resolved_yes")
    check("event on the deadline itself is allowed",
          act(yes(date="2026-09-30"), g("cnbc.com", "qz.com"),
              q={**Q, "deadline": "2026-09-30"}) == "resolved_yes")
    check("no answer at all -> failed",
          act(None, g("cnbc.com")) == "failed")


# ---------------------------------------------------------------------------
# Part 2: end-to-end against a fake search model
# ---------------------------------------------------------------------------

class FakeQuota:
    key_names = ["FAKE_KEY"]
    def remaining(self, *a): return 20
    def used(self, *a): return 0
    def record(self, *a, **k): pass
    def flush(self): pass
    def summary(self): return ""


class FakeSearch(models.ModelRouter):
    """
    Answers web_resolve calls from a script keyed by question id. A question
    missing from the script makes the fake behave as if every search model is
    out of quota.
    """

    def __init__(self, script, chains=None, grounding=None):
        self.script = script
        self.calls = []
        self.chains = chains if chains is not None else MODELS["chains"]
        self.grounding_models = set(
            grounding if grounding is not None else MODELS["grounding_models"])
        self.stats = models.CallStats()
        self.last_grounding = {}
        self.quota = FakeQuota()

    def generate(self, task, prompt, *, expect_json=True, temperature=0.4,
                 max_output_tokens=4096, grounded=False):
        assert task == "web_resolve" and grounded, "web calls must be grounded"
        qid = next((k for k in self.script if f"QUESTION: {k} " in prompt), None)
        self.calls.append(qid)
        if qid is None:
            self.stats.last_error = "gemini-2.5-flash: rate-limited"
            self.last_grounding = {}
            return None, None
        answer, grounding = self.script[qid]
        self.last_grounding = grounding
        return answer, "gemini-2.5-flash-lite"


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
    }
    for attr, fname in names.items():
        setattr(config, attr, config.DATA / fname)
    config.REFERENCE_INDEX_CSV = config.REFERENCE / "index.csv"
    config.QUOTA_JSON = config.DATA / "quota.json"
    config.OVERRIDES_CSV = sandbox / "overrides.csv"
    config.RESOLUTIONS_CSV = sandbox / "resolutions.csv"
    config.INBOX = sandbox / "inbox"
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
    }
    store.ensure_files()
    return sandbox


def _question(qid, deadline, created="2026-08-22", **extra):
    row = {"id": qid, "question": f"{qid} test question", "domain": "global_macro",
           "bucket": "medium", "created": created, "deadline": deadline,
           "primary_tag": "t", "resolution_criteria": "criteria",
           "status": "open", "shape": "point", "admitted_by": "test"}
    row.update(extra)
    store.append_row(config.QUESTIONS_CSV, row)


def part_two():
    sandbox = _sandbox()
    log = RunLog(TODAY)

    # Q0003-like: happened 10 Sept, two sites -> YES on the EVENT date.
    _question("QA", "2026-12-31")
    # One unofficial site -> pending, with a paste-ready row.
    _question("QB", "2026-11-05")
    # Announced for the future -> stays open.
    _question("QC", "2027-08-26")
    # Lapsed NO earlier, still watched; the web finds it happened in time.
    _question("QD", "2026-09-15", created="2026-08-20", status="resolved",
              outcome="0", resolved_date="2026-09-15",
              resolution_basis="lapsed_absence", outcome_set_by="system",
              watch_until="2026-12-14")

    script = {
        "QA": (yes("2026-09-10"), g("cnbc.com", "qz.com")),
        "QB": (yes("2026-09-28"), g("reuters.com")),
        "QC": ({"happened": False, "status": "announced_not_yet_happened",
                "event": "scheme announced", "event_date": "",
                "reason": "only announced"}, g("pib.gov.in")),
        "QD": (yes("2026-09-12"), g("state.gov")),
    }

    # Backfill guard first: a past --date must make no calls at all.
    r = FakeSearch(script)
    web_resolve.run(r, SETTINGS, dt.date(2026, 9, 1), log,
                    real_today=TODAY)
    check("backfill date -> no web calls", r.calls == [])

    r = FakeSearch(script)
    summary = web_resolve.run(r, SETTINGS, TODAY, log, real_today=TODAY)
    qa = store.question_by_id("QA")
    check("YES applied", qa["status"] == "resolved" and qa["outcome"] == "1")
    check("resolved on the EVENT date, not the check date",
          qa["resolved_date"] == "2026-09-10")
    check("basis recorded as web_confirmed",
          qa["resolution_basis"] == "web_confirmed")
    check("single source left open", store.question_by_id("QB")["status"] == "open")
    pending = store.read_rows(config.PENDING_RESOLUTIONS_CSV)
    check("single source listed in pending_resolutions.csv",
          [p["question_id"] for p in pending] == ["QB"])
    check("pending row carries a paste-ready resolutions.csv line",
          pending and pending[0]["paste_into_resolutions_csv"].startswith("QB,1,2026-09-28,"))
    check("announced-only question stays open",
          store.question_by_id("QC")["status"] == "open")
    qd = store.question_by_id("QD")
    check("lapsed NO flipped to YES by late web evidence",
          qd["outcome"] == "1" and qd["resolved_date"] == "2026-09-12"
          and qd["watch_until"] == "")
    check("closest deadline checked first",
          r.calls[:3] == ["QB", "QA", "QC"])
    check("every check logged in web_checks.csv",
          len(store.read_rows(config.WEB_CHECKS_CSV)) == 4)
    check("summary counts", summary["resolved"] == ["QA", "QD"]
          and summary["pending"] == ["QB"])

    # Second run the same day: nothing re-checked, pending list intact.
    r2 = FakeSearch(script)
    web_resolve.run(r2, SETTINGS, TODAY, log, real_today=TODAY)
    check("second run same day makes no calls", r2.calls == [])
    check("pending list survives a no-op run",
          len(store.read_rows(config.PENDING_RESOLUTIONS_CSV)) == 1)

    # You decide QB in resolutions.csv -> it drops off the pending list.
    # resolutions.csv is hand-edited and not in _SCHEMAS, so write it directly.
    with open(config.RESOLUTIONS_CSV, "a", encoding="utf-8") as fh:
        fh.write("QB,1,2026-09-28,checked by hand\n")
    web_resolve.run(FakeSearch(script), SETTINGS, TODAY, log, real_today=TODAY)
    check("your resolutions.csv entry clears the pending item",
          store.read_rows(config.PENDING_RESOLUTIONS_CSV) == [])

    # A wrong web YES that you reopen must NOT be re-resolved next day.
    with open(config.RESOLUTIONS_CSV, "a", encoding="utf-8") as fh:
        fh.write("QA,reopen,,web check was wrong\n")
    from src import resolve
    resolve.apply_human_resolutions(log, TODAY)
    check("reopen puts the question back to open",
          store.question_by_id("QA")["status"] == "open")
    r_re = FakeSearch(script)              # every remaining question answers
    later = TODAY + dt.timedelta(days=5)
    web_resolve.run(r_re, SETTINGS, later, log, real_today=later)
    check("...the run did reach the other open questions",
          "QC" in r_re.calls)
    check("a question you reopened is never web-checked again",
          "QA" not in r_re.calls
          and store.question_by_id("QA")["status"] == "open")

    # Out of quota: the loop stops at the first failure and says so.
    _question("QE", "2026-10-20")
    _question("QF", "2026-10-25")
    r3 = FakeSearch({})                     # every call "rate-limited"
    tomorrow = TODAY + dt.timedelta(days=1)
    summary = web_resolve.run(r3, SETTINGS, tomorrow, log, real_today=tomorrow)
    check("quota exhausted -> stops after one failed call", len(r3.calls) == 1)
    check("unchecked questions counted for the log", summary["unchecked"] >= 2)

    # No search model in the chain -> warns and makes no calls.
    r4 = FakeSearch(script, grounding=[])
    web_resolve.run(r4, SETTINGS, tomorrow, log, real_today=tomorrow)
    check("no search-capable model -> no calls", r4.calls == [])

    # Dry run: reads, writes nothing.
    _question("QG", "2026-10-30")
    before = len(store.read_rows(config.WEB_CHECKS_CSV))
    quiet = ({"happened": False, "status": "no_relevant_news", "reason": "n"},
             g("cnbc.com"))
    r5 = FakeSearch({"QE": quiet, "QF": quiet,
                     "QG": (yes("2026-09-30"), g("cnbc.com", "qz.com"))})
    day3 = TODAY + dt.timedelta(days=2)
    web_resolve.run(r5, SETTINGS, day3, log, dry_run=True, real_today=day3)
    check("dry run actually reached the resolvable question", "QG" in r5.calls)
    check("dry run writes nothing",
          store.question_by_id("QG")["status"] == "open"
          and len(store.read_rows(config.WEB_CHECKS_CSV)) == before)

    shutil.rmtree(sandbox, ignore_errors=True)


def part_three():
    """Registering 2.5 for the resolver must NOT switch lens grounding on."""
    class Bare:
        chains = MODELS["chains"]
        grounding_models = set(MODELS["grounding_models"])

    class QuietLog:
        def info(self, *_a): pass

    settings = {**SETTINGS, "reference": {"verify_with_grounding": True}}
    runner = LensRunner(Bare(), QuietLog(), settings, "test")
    check("lens grounding stays OFF with the real models.yaml",
          runner.grounding_enabled is False)

    class WithLens(Bare):
        chains = {**MODELS["chains"],
                  "lens_outside": ["gemini-2.5-flash-lite"]}
    runner = LensRunner(WithLens(), QuietLog(), settings, "test")
    check("...and would turn on only if lens_outside listed a search model",
          runner.grounding_enabled is True)


def main() -> int:
    part_one()
    part_two()
    part_three()
    ok = True
    for label, passed in RESULTS:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    print(f"\n{sum(p for _, p in RESULTS)}/{len(RESULTS)} passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
