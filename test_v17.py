#!/usr/bin/env python3
"""
Offline test for v17: paper names, coverage, resolves_on, and gate H wiring.
No API key, no quota.

    python test_v17.py
"""

import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SUPERFORECASTER_API", "fake-key-for-offline-test")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import agents, config, contest, papers, resolve, store, web_resolve  # noqa: E402

RESULTS: list[tuple[str, bool]] = []


def check(label, passed):
    RESULTS.append((label, bool(passed)))


class QuietLog:
    def __init__(self):
        self.flags, self.infos = [], []
    def sub(self, t): pass
    def info(self, t): self.infos.append(t)
    def warn(self, t): self.infos.append(t)
    def flag(self, t): self.flags.append(t)


# ---------------------------------------------------------------------------
# 1. Names come from filenames -- tested against real upload names
# ---------------------------------------------------------------------------

def test_names():
    cases = {
        "MUMBAI_Mint_03-09-2026.pdf": "Mint",
        "Mint ● Delhi ● 15‹09‹2026 Tr_●.pdf": "Mint",
        "Mint_LiveMint MUMBAI_21-09-2026.pdf": "Mint",
        "BS ● Mumbai ● 07‹09‹2026 Tr-Me.pdf": "Business Standard",
        "BS English-Delhi 30-09.pdf": "Business Standard",
        "ET-Delhi 21-09-2026.pdf": "The Economic Times",
        "The Economic Times_Mumbai_20260910.pdf": "The Economic Times",
        "Mumbai_FE_10-09-2026.pdf": "The Financial Express",
        "HT Bengaluru ¹³⁰⁹²⁰²⁶.pdf": "Hindustan Times",
        "Delhi_TOI_16-09-2026.pdf": "The Times of India",
        "th.th_bangalore.2026-09-21.pdf": "The Hindu",
        "THE HINDU AD-FREE HD 23~09~2026.pdf": "The Hindu",
        "IE UPSC EDITION 21-09-2026.pdf": "The Indian Express",
        "The Wall Street Journal Weekend - 5 September 2026.pdf": "The Wall Street Journal",
        "The Washington Post - September 10, 2026.pdf": "The Washington Post",
        "Boston Globe_!009.pdf": "The Boston Globe",
        "New Scientist International Edition - 19 September 2026.pdf": "New Scientist",
    }
    wrong = {f: papers.name_for(f) for f, want in cases.items()
             if papers.name_for(f) != want}
    check("every real filename style maps to the right paper"
          + (f" (wrong: {wrong})" if wrong else ""), not wrong)
    check("city is ignored: Mumbai and Bengaluru Mint are one paper",
          papers.name_for("MUMBAI_Mint_x.pdf") == papers.name_for("BENGALURU_Mint_x.pdf"))
    check("short codes never match inside words (Boston is not BS, jobs is not BS)",
          papers.name_for("jobs report.pdf") == papers.UNKNOWN
          and papers.name_for("Boston Globe.pdf") == "The Boston Globe")
    check("unrecognised file is Unknown, never guessed",
          papers.name_for("TT ● Delhi ● 17‹09‹2026.pdf") == papers.UNKNOWN)


# ---------------------------------------------------------------------------
# 2. Coverage: regulars, gaps, said once
# ---------------------------------------------------------------------------

def _sandbox():
    root = Path(tempfile.mkdtemp(prefix="sf-v17-"))
    config.DATA = root / "data"
    config.LOGS = root / "logs"
    config.PROCESSED_CSV = config.DATA / "processed.csv"
    config.COVERAGE_CSV = config.DATA / "coverage.csv"
    config.QUESTIONS_CSV = config.DATA / "questions.csv"
    config.DATA.mkdir(parents=True)
    config.LOGS.mkdir(parents=True)
    store._SCHEMAS = {
        config.PROCESSED_CSV: store.PROCESSED_FIELDS,
        config.COVERAGE_CSV: store.COVERAGE_FIELDS,
        config.QUESTIONS_CSV: store.QUESTION_FIELDS,
    }
    for path, fields in store._SCHEMAS.items():
        store.rewrite(path, [])
    return root


def _upload(day: dt.date, *names):
    for n in names:
        store.append_row(config.PROCESSED_CSV, {
            "fingerprint": f"{n}{day}", "filename": f"{n} {day:%d-%m-%Y}.pdf",
            "paper_guess": "", "issue_date_guess": day.isoformat(),
            "pages": 1, "processed_on": day.isoformat(), "articles_kept": 1,
        })


def test_coverage():
    _sandbox()
    settings = config.load_settings()
    today = dt.date(2026, 9, 20)
    # Mint and BS every upload day; New Scientist once. 11-12 Sep: nothing.
    for i in range(14):
        day = dt.date(2026, 9, 1) + dt.timedelta(days=i)
        if day.day in (11, 12):
            continue
        names = ["MUMBAI_Mint", "BS Mumbai"]
        if day.day == 5:
            names.append("New Scientist")
        if day.day == 13:
            names = ["MUMBAI_Mint"]          # BS missing on the 13th
        _upload(day, *names)
    # The previous run was on the 10th.
    (config.LOGS / "2026-09-10.md").write_text("x")

    log = QuietLog()
    out = papers.report(today, settings, log)
    check("regular = Mint and BS; a one-off paper is occasional",
          out["regular"] == {"Mint", "Business Standard"})
    flagged = "\n".join(log.flags)
    check("days with no papers are flagged", "2026-09-11" in flagged and "2026-09-12" in flagged)
    check("a missing regular is flagged by name",
          "2026-09-13: regular paper(s) missing -- Business Standard" in flagged)
    check("the occasional paper's absence is never flagged",
          "New Scientist" not in flagged)
    rows = {r["date"]: r for r in store.read_rows(config.COVERAGE_CSV)}
    check("coverage.csv records the gap", rows["2026-09-11"]["status"] == "none")

    # Next run, the next day: the same gaps must not be flagged again.
    (config.LOGS / "2026-09-20.md").write_text("x")
    log2 = QuietLog()
    papers.report(dt.date(2026, 9, 21), settings, log2)
    check("a gap is reported once, not every run",
          not any("2026-09-11" in f or "2026-09-13" in f for f in log2.flags))

    # Too little history: nothing is called regular, nothing flagged as missing.
    _sandbox()
    _upload(dt.date(2026, 9, 1), "MUMBAI_Mint")
    log3 = QuietLog()
    out = papers.report(dt.date(2026, 9, 2), settings, log3)
    check("with too little history nothing is called regular", out["regular"] == set())


# ---------------------------------------------------------------------------
# 3. resolves_on: agents must declare it; contest and web prompt see it
# ---------------------------------------------------------------------------

def test_resolves_on():
    check("contest shows resolves_on to the judge",
          "resolves_on: carried_out" in contest._format([agents.Proposal(
              proposal_id="P1", question="q", resolves_on="carried_out")]))
    check("gate H is in the contest prompt",
          "GATE H -- SOMETHING MUST STILL STAND IN THE WAY" in contest.PROMPT)
    check("agents are asked for resolves_on",
          "resolves_on" in agents.__dict__.get("PROMPT", "") or
          any("resolves_on" in v for v in agents.__dict__.values()
              if isinstance(v, str)))
    check("blank resolves_on gets the strict reading in the web check",
          web_resolve.resolves_on_text({}) ==
          web_resolve.RESOLVES_ON_TEXT["carried_out"])
    check("announced questions are told the announcement counts",
          "ITSELF resolves" in web_resolve.resolves_on_text({"resolves_on": "announced"}))
    prompt = web_resolve.WEB_PROMPT.format(today="t", question="q", criteria="c",
                                           deadline="d", resolves_on="X-MARK")
    check("web prompt carries the resolves_on line", "WHAT RESOLVES IT: X-MARK" in prompt)

    # The classifier: fills blanks once, flags ambiguous, leaves set ones alone.
    _sandbox()
    for qid, ro in (("Q1", ""), ("Q2", ""), ("Q3", "in_effect")):
        store.append_row(config.QUESTIONS_CSV, {
            "id": qid, "question": qid, "status": "open", "deadline": "2026-12-31",
            "resolution_criteria": "c", "resolves_on": ro})

    class Router:
        calls = []
        def generate(self, task, prompt, **k):
            self.calls.append(task)
            return ({"resolves_on": "ambiguous" if len(self.calls) == 1
                     else "carried_out", "reason": "r"}, "fake")
    r, log = Router(), QuietLog()
    resolve.classify_resolves_on(r, log)
    q = {x["id"]: x for x in store.read_rows(config.QUESTIONS_CSV)}
    check("classifier only touches questions without resolves_on", len(r.calls) == 2)
    check("ambiguous criteria are flagged to you",
          q["Q1"]["resolves_on"] == "ambiguous" and any("Q1" in f for f in log.flags))
    check("classified value stored", q["Q2"]["resolves_on"] == "carried_out")
    check("existing value left alone", q["Q3"]["resolves_on"] == "in_effect")
    r2 = Router()
    r2.calls = []
    resolve.classify_resolves_on(r2, QuietLog())
    check("second run makes no classification calls", r2.calls == [])


def main() -> int:
    test_names()
    test_coverage()
    test_resolves_on()
    ok = True
    for label, passed in RESULTS:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    print(f"\n{sum(p for _, p in RESULTS)}/{len(RESULTS)} passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
