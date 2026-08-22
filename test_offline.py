#!/usr/bin/env python3
"""
Offline end-to-end test.

Runs the entire pipeline against the real PDFs with a FAKE model, so every
stage -- extraction, filtering, triage, agents, contest, gate, CSV writing --
is exercised without spending API quota or needing a key.

This proves the plumbing works. It says nothing about whether the questions are
any good; only real models and your judgement can tell you that.

    python test_offline.py
"""

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SUPERFORECASTER_API", "fake-key-for-offline-test")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import config, models, pipeline, store  # noqa: E402


class _FakeQuota:
    """Stands in for the persistent quota file during offline tests."""
    def __init__(self):
        self.key_names = ["FAKE_KEY"]

    def summary(self):
        return "  FAKE_KEY: offline test, no real quota consumed"

    def used(self, key_name, model):
        return 0

    def remaining(self, key_name, model, rpd):
        return rpd

    def total_used(self):
        return 0

    def record(self, key_name, model, n=1):
        pass

    def flush(self):
        pass


class FakeRouter(models.ModelRouter):
    """Returns plausible canned responses instead of calling the API."""

    def __init__(self, settings, models_cfg, log):
        self.settings = settings
        self.log = log
        self.stats = models.CallStats()
        self.chains = models_cfg.get("chains", {})
        self.limits = models_cfg.get("limits", {})
        self._max_calls = 10_000
        self._counter = 0
        self.key_names = ["FAKE_KEY"]
        self.key_by_name = {"FAKE_KEY": "fake"}
        self.key = "fake"
        self.deep_models = set(models_cfg.get("deep_models", []) or [])
        self.forecast_tasks = set(models_cfg.get("forecast_tasks", []) or [])
        self.grounding_models = set(models_cfg.get("grounding_models", []) or [])
        self.last_grounding = {}
        self.quota = _FakeQuota()

    def _deep_remaining(self):
        return 999

    def generate(self, task, prompt, *, expect_json=True, temperature=0.4,
                 max_output_tokens=4096, grounded=False):
        self._counter += 1
        self.stats.total_calls += 1
        self.stats.calls_by_model[f"fake-{task}"] += 1
        self.last_grounding = (
            {"fired": True, "sources": ["fake source"], "queries": ["fake"]}
            if grounded else {}
        )

        # ---- stage two tasks ------------------------------------------------
        if task == "shape":
            # Alternate so both branches are exercised.
            return {"shape": "window" if self._counter % 2 else "point",
                    "reason": "test classification", "confident": True}, "fake"

        if task == "screen":
            # Alternate so that both the "no cause" and the "escalate" paths
            # get exercised, and so a resolution nomination happens at least
            # once (which then has to survive the two-confirmer bar).
            mod = self._counter % 4
            return {
                "resolution_nominee": mod == 3,
                "resolution_direction": "yes" if mod == 3 else "none",
                "resolution_event": "A test body announced the decision on 2026-08-19",
                "escalate": mod in (1, 3),
                "trigger_fired": "test trigger" if mod == 1 else "",
                "reason": "test screen decision",
                "diagnostic": "roughly as likely either way",
            }, "fake"

        if task == "confirm":
            # Deliberately says NOT resolved, so the strict bar is what the
            # test observes: a nomination alone must never write an outcome.
            return {
                "clauses": [{"clause": "the announcement occurred",
                             "met": False, "evidence": "not clearly reported"}],
                "resolved": False,
                "outcome": "0",
                "event_date": "",
                "event_description": "",
                "confidence_note": "test",
            }, "fake"

        if task == "lens_outside":
            frozen = {}
            if "FIXED HERE FOR GOOD" in prompt:
                frozen = {"substantive_probability": 40,
                          "substantive_reason": "base rate only"}
            return {**frozen, **{
                "abstraction": "Will a regulator of this type act within ~N months?",
                "population_needed": "regulator proposals reaching final rules",
                "frequency_question": "share of proposals finalised within 15 months",
                "membership_rule": "proposals published for public comment 2015-2026",
                "window": "2015-2026",
                "cases": [
                    {"name": "Case A", "date": "2019-03-01", "outcome": "hit"},
                    {"name": "Case B", "date": "2021-07-01", "outcome": "miss"},
                    {"name": "Case C", "date": "2023-01-01", "outcome": "hit"},
                ],
                "skeleton": "" if self._counter % 7 == 0 else "3 named cases",
                "multiplier": "" if self._counter % 7 == 0 else "n/a",
                "count": 9 if self._counter % 7 == 0 else 3,
                "hits": 7 if self._counter % 7 == 0 else 2,
                "rate": 0.78 if self._counter % 7 == 0 else 0.67,
                "coverage_note": "3 named; believed roughly 4 in total",
                "known_gaps": "test",
                "valid_until": "2027-08-01",
                "validity_reason": "structurally stable",
                "provenance_tier": "structured",
                "outside_probability": 40,
                "reasoning": "two of three comparable cases finalised in time",
                "abstain": False,
                "abstain_reason": "",
            }}, "fake"

        if task == "lens_inside":
            side = "YES" if "STRONGEST HONEST CASE that this resolves YES" in prompt else "NO"
            return {
                "case": f"test case for {side}",
                "key_evidence": ["a specific checkable fact"],
                "strength": "moderate",
                "strength_reason": "test",
                "deliberately_ignored": "actor preferences (another aperture)",
            }, "fake"

        if task == "lens_reconcile":
            if "ambiguous" not in prompt and self._counter % 5 == 0:
                # Reproduce the live-run bug: a fraction where a percentage was
                # asked for. The runner must catch it and re-ask.
                return {"probability": 0.35, "reasoning": "r",
                        "triggers": []}, "fake"
            return {
                "probability": 35,
                "what_changes_between_horizons": "the comment period closes",
                "p_one_third": 10,
                "p_two_thirds": 22,
                "p_full": 35,
                "stronger_case": "no",
                "stronger_case_reason": "test",
                "reasoning": "reconciled the frozen outside view with both cases",
                "deliberately_ignored": "forbidden ground left alone",
                "triggers": [
                    {"event": "a draft circular is published",
                     "by_date": "2026-11-01",
                     "direction": "up", "to_roughly": 60},
                    {"event": "an event that already happened",
                     "by_date": "2025-03-01",
                     "direction": "down", "to_roughly": 20},
                ],
                "moved_from": "", "move_reason": "",
                "crossed_bound_argument": "",
            }, "fake"

        if task == "lens_audit":
            if self._counter % 11 == 0:
                return {"violation": True, "passage": "p",
                        "forbidden_ground": "actor preferences",
                        "instruction": "remove it"}, "fake"
            return {"violation": False, "passage": "", "forbidden_ground": "",
                    "instruction": ""}, "fake"

        if task == "advocate":
            return {
                "load_bearing_assumption": "that the comment period closes on time",
                "why_it_could_be_wrong": "extensions are common",
                "should_move": True,
                "moved_to": 30,
                "move_reason": "extension risk is underweighted",
                "lenses_that_look_redundant": [],
                "concerns": "test",
            }, "fake"

        if task == "triage":
            # Pretend every third page yields one article.
            if self._counter % 3 != 0:
                return [], "fake"
            return [{
                "headline": f"Test story {self._counter}",
                "summary": "A government body is considering a policy change.",
                "key_facts": "Decision expected 15 September 2026. Current rate 6.5%.",
                "domains": "india_macro",
            }], "fake"

        if task == "propose":
            return [{
                "question": ("Will the Reserve Bank of India cut the repo rate on or "
                             f"before 15 September 2026? (variant {self._counter})"),
                "deadline": "2026-09-15",
                "resolution_criteria": ("YES if the RBI announces a reduction in the "
                                        "repo rate on or before 15 September 2026."),
                "resolution_source": "Mint front page on the day of the RBI decision",
                "reasoning_value": ("Examining the balance between rising fuel-driven "
                                    "inflation and slowing growth forecasts, plus the "
                                    "governor's recent public statements on liquidity."),
                "significance": ("Borrowing costs for millions of households, "
                                 "bank margins, and the rupee's trajectory all "
                                 "shift depending on the answer."),
                "primary_tag": "rbi_monetary_policy",
                "secondary_tags": ["india_inflation", "iran_conflict"],
                "tertiary_tags": ["indian_rupee"],
                "tag_justification": "The RBI's own reaction function dominates.",
                "source": f"Test story {self._counter}",
            }], "fake"

        if task == "contest":
            pid = _first_id(prompt)
            return {
                "winner": pid,
                "winner_reasoning": "Clears the bar: uncertain and reasoning helps.",
                "exceptional": False,
                "eliminated": [],
            }, "fake"

        if task == "gate":
            return {"redundant_with": None, "reasoning": "distinct"}, "fake"

        if task == "lexicon":
            return {"match": "rbi_monetary_policy", "reasoning": "same driver"}, "fake"

        return None, None

    def embed(self, texts):
        return None  # force the lexical-similarity fallback path to be tested


def _first_id(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith("proposal_id:"):
            return line.split(":", 1)[1].strip()
    return ""


def _make_test_papers(inbox: Path) -> int:
    """
    Write three synthetic newspaper PDFs.

    The test used to depend on real papers sitting in inbox/, which meant it
    only worked on a machine that happened to have them. Generating them here
    makes the test runnable anywhere, including in CI.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:
        return 0

    inbox.mkdir(parents=True, exist_ok=True)
    papers = {
        "mint_2026-08-19.pdf": "Mint",
        "wsj_2026-08-19.pdf": "The Wall Street Journal",
        "wapo_2026-08-19.pdf": "The Washington Post",
    }
    body = (
        "The central bank said on Tuesday that it would publish a consultation "
        "paper on lending norms, with comments due by the middle of September. "
        "Officials indicated a final decision could follow within several "
        "months, though they declined to commit to a timetable. Analysts said "
        "the proposal would reshape how households borrow against collateral, "
        "and noted that similar consultations have taken between nine and "
        "eighteen months to reach a final circular. Growth has slowed for two "
        "consecutive quarters while inflation has stayed above the midpoint of "
        "the target band, leaving policymakers with an uncomfortable trade-off. "
    )
    for name, masthead in papers.items():
        c = canvas.Canvas(str(inbox / name), pagesize=A4)
        for page in range(4):
            y = 800
            c.setFont("Helvetica-Bold", 16)
            c.drawString(60, y, masthead if page == 0 else f"{masthead} A{page+1}")
            y -= 30
            c.setFont("Helvetica", 9)
            for _ in range(5):
                for chunk in [body[i:i+95] for i in range(0, len(body), 95)]:
                    c.drawString(60, y, chunk)
                    y -= 12
                    if y < 60:
                        break
                if y < 60:
                    break
            c.showPage()
        c.save()
    return len(papers)


def _seed_questions(today: dt.date) -> None:
    """
    Put one WINDOW and one POINT question into the portfolio directly.

    Stage two must be testable without depending on stage one having produced
    anything, and the two shapes take genuinely different paths: the window
    question gets three horizon numbers, the point question must not.
    """
    rows = [
        {
            "id": "Q9001",
            "question": ("Will the central bank issue a final circular permitting "
                         "flexi loans on or before 31 December 2026?"),
            "domain": "india_macro", "bucket": "medium",
            "created": today.isoformat(), "deadline": "2026-12-31",
            "primary_tag": "rbi_monetary_policy",
            "resolution_criteria": ("YES if a final circular is published on or "
                                    "before 31 December 2026. NO otherwise."),
            "resolution_source": "Mint", "status": "open",
            "reasoning_value": "consultation timelines", "significance": "borrowing costs",
            "shape": "window", "admitted_by": "test",
        },
        {
            "id": "Q9002",
            "question": ("Will the Federal Reserve raise the upper bound of the "
                         "federal funds target range at its December 2026 meeting?"),
            "domain": "global_macro", "bucket": "medium",
            "created": today.isoformat(), "deadline": "2026-12-09",
            "primary_tag": "fed_policy",
            "resolution_criteria": ("YES if the upper bound is raised at the "
                                    "December 2026 FOMC meeting."),
            "resolution_source": "WSJ", "status": "open",
            "reasoning_value": "policy path", "significance": "global rates",
            "shape": "point", "admitted_by": "test",
        },
    ]
    for r in rows:
        store.append_row(config.QUESTIONS_CSV, r)


def main() -> int:
    root = Path(__file__).resolve().parent
    sandbox = Path(tempfile.mkdtemp(prefix="sf-test-"))
    print(f"Sandbox: {sandbox}\n")

    # Redirect all data paths into the sandbox so the real files are untouched.
    for name in ("DATA", "LOGS"):
        setattr(config, name, sandbox / name.lower())
    config.QUESTIONS_CSV = config.DATA / "questions.csv"
    config.PROPOSALS_CSV = config.DATA / "proposals.csv"
    config.FORECASTS_CSV = config.DATA / "forecasts.csv"
    config.PROCESSED_CSV = config.DATA / "processed.csv"
    config.WAITING_CSV = config.DATA / "waiting_list.csv"
    config.PENDING_TAGS_CSV = config.DATA / "pending_tags.csv"
    config.SCREENS_CSV = config.DATA / "screens.csv"
    config.LENS_CSV = config.DATA / "lens_outputs.csv"
    config.DIAGNOSTICS_CSV = config.DATA / "diagnostics.csv"
    config.SYSTEM_PROPOSALS_CSV = config.DATA / "system_proposals.csv"
    config.RUNS = config.DATA / "runs"
    config.REFERENCE = config.DATA / "reference"
    config.REPORTS = config.DATA / "reports"
    config.REFERENCE_INDEX_CSV = config.REFERENCE / "index.csv"
    config.QUOTA_JSON = config.DATA / "quota.json"
    config.LEXICON_CSV = sandbox / "lexicon.csv"
    config.OVERRIDES_CSV = sandbox / "overrides.csv"
    config.RESOLUTIONS_CSV = sandbox / "resolutions.csv"
    shutil.copy(root / "config" / "lexicon.csv", config.LEXICON_CSV)

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
    }

    pipeline.ModelRouter = FakeRouter

    config.INBOX = sandbox / "inbox"
    n_papers = _make_test_papers(config.INBOX)
    print(f"Generated {n_papers} synthetic newspaper PDFs\n")

    store.ensure_files()
    _seed_questions(dt.date(2026, 8, 19))

    print("=" * 70)
    print("RUN 1 -- three papers, two seeded questions")
    print("=" * 70)
    added = pipeline.run(today=dt.date(2026, 8, 19))

    print("\n" + "=" * 70)
    print("RUN 2 -- same papers again (must be skipped by fingerprint)")
    print("=" * 70)
    pipeline.run(today=dt.date(2026, 8, 20))

    # A question must be a WINDOW for the three-horizon machinery to fire, and
    # stage one does not yet emit `shape`. Set one by hand so run 3 exercises
    # the horizon path as well as the point path.
    qs = store.read_rows(config.QUESTIONS_CSV)
    if qs:
        store.update_question(qs[0]["id"], {"shape": "window"})

    print("\n" + "=" * 70)
    print("RUN 3 -- EMPTY INBOX (the old early return would have stopped here)")
    print("=" * 70)
    # Guard: never delete anything outside the sandbox. An earlier version of
    # this test emptied the real inbox/ because config.INBOX had not yet been
    # redirected at the point rmtree ran.
    assert str(config.INBOX).startswith(str(sandbox)), \
        f"refusing to clear {config.INBOX}: not inside the sandbox"
    shutil.rmtree(config.INBOX, ignore_errors=True)
    config.INBOX.mkdir(parents=True, exist_ok=True)
    pipeline.run(today=dt.date(2026, 8, 28))

    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)

    questions = store.read_rows(config.QUESTIONS_CSV)
    proposals = store.read_rows(config.PROPOSALS_CSV)
    processed = store.read_rows(config.PROCESSED_CSV)
    screens = store.read_rows(config.SCREENS_CSV)
    lens_rows = store.read_rows(config.LENS_CSV)
    forecasts = store.read_rows(config.FORECASTS_CSV)
    ref_index = store.read_rows(config.REFERENCE_INDEX_CSV)
    diagnostics = store.read_rows(config.DIAGNOSTICS_CSV)

    print(f"questions.csv    : {len(questions)} rows")
    print(f"proposals.csv    : {len(proposals)} rows")
    print(f"processed.csv    : {len(processed)} papers")
    print(f"screens.csv      : {len(screens)} rows")
    print(f"lens_outputs.csv : {len(lens_rows)} rows")
    print(f"forecasts.csv    : {len(forecasts)} rows")
    print(f"reference index  : {len(ref_index)} entries")
    print(f"diagnostics.csv  : {len(diagnostics)} rows")

    checks = []
    checks.append(("papers fingerprinted", len(processed) == n_papers))
    checks.append(("questions present", len(questions) >= 2))
    checks.append(("proposals recorded", len(proposals) > 0 or n_papers == 0))
    checks.append(("rejections recorded",
                   any(p["outcome"] != "won" for p in proposals)
                   or n_papers == 0))
    checks.append(("tag cap enforced",
                   sum(1 for q in questions
                       if q["primary_tag"] == "rbi_monetary_policy") <= 3))
    checks.append(("all questions have resolution criteria",
                   all(q["resolution_criteria"] for q in questions)))
    checks.append(("point question got NO horizon numbers",
                   all(not r["p_one_third"] for r in lens_rows
                       if r["question_id"] == "Q9002")))
    checks.append(("all questions have reasoning value",
                   all(q["reasoning_value"] for q in questions)))
    checks.append(("all questions have named consequences",
                   all(q["significance"] for q in questions)))
    checks.append(("second run skipped duplicates",
                   len(processed) == n_papers))
    checks.append(("log written", (config.LOGS / "2026-08-19.md").exists()))

    # ---- stage two -------------------------------------------------------
    checks.append(("every open question screened", len(screens) > 0))
    checks.append(("screens always carry a reason",
                   all(r["reason"] for r in screens)))
    checks.append(("lenses produced output", len(lens_rows) > 0))
    checks.append(("all seven lenses ran",
                   len({r["lens"] for r in lens_rows}) == 7))
    checks.append(("aggregate forecasts written",
                   any(f["model"] == "aggregate" for f in forecasts)))
    checks.append(("forecast carries lens counts",
                   all(f["responding_lenses"] for f in forecasts
                       if f["model"] == "aggregate")))
    checks.append(("extremized stored as shadow only",
                   all(f["median_extremized"] and
                       f["median_extremized"] != f["probability"]
                       for f in forecasts if f["model"] == "aggregate")))
    checks.append(("reference library populated", len(ref_index) > 0))
    checks.append(("reference entries carry a membership rule",
                   all(r["membership_rule"] for r in ref_index)))
    checks.append(("reference validity capped at 12 months",
                   all(r["valid_until"] <= "2027-08-19" for r in ref_index)))
    checks.append(("reference entries are lens-owned",
                   all(r["built_by"] for r in ref_index)))
    checks.append(("outside view recorded separately from final",
                   any(r["outside_probability"] for r in lens_rows)))
    checks.append(("triggers recorded",
                   any(r["triggers"] for r in lens_rows)))
    checks.append(("nomination alone did NOT resolve a question",
                   all(q["status"] == "open" or q["outcome_set_by"] == "system"
                       for q in questions)))
    checks.append(("empty inbox still ran forecasting",
                   (config.RUNS / "2026-08-28").exists()))
    checks.append(("run records are JSON per question",
                   any((config.RUNS / "2026-08-19").glob("*.json"))
                   if (config.RUNS / "2026-08-19").exists() else False))
    checks.append(("reports written",
                   (config.REPORTS / "latest.json").exists()))
    checks.append(("literalist froze its substantive term",
                   any(r["substantive_probability"] for r in lens_rows
                       if r["lens"] == "literalist")))
    checks.append(("only literalist has a frozen term",
                   all(not r["substantive_probability"] for r in lens_rows
                       if r["lens"] != "literalist")))
    checks.append(("advocate never moves the live number",
                   all(abs(float(f["probability"]) - float(f["median_raw"])) < 0.05
                       for f in forecasts
                       if f["model"] == "aggregate" and f["median_raw"])))
    checks.append(("advocate proposal stored as a shadow",
                   any(f["advocate_proposed"] for f in forecasts
                       if f["model"] == "aggregate")))
    checks.append(("inside drift recorded",
                   any(r["inside_drift"] for r in lens_rows)))
    checks.append(("no lens number is on a 0-1 scale",
                   all(float(r["probability"]) > 1 or float(r["probability"]) == 0
                       for r in lens_rows if r["probability"])))
    checks.append(("shape auto-classified, never blank",
                   all(q["shape"] in ("window", "point") for q in questions)))
    checks.append(("past-dated triggers dropped",
                   all("already happened" not in (r["triggers"] or "")
                       for r in lens_rows)))
    checks.append(("unsupported reference entries labelled honestly",
                   all(r["provenance_tier"] in
                       ("enumerated", "extrapolated", "unsupported", "reasoned")
                       for r in ref_index)))
    checks.append(("reference rate recomputed from cases",
                   all(abs(float(r["hits"]) / float(r["count"])
                           - float(r["rate"])) < 0.01
                       for r in ref_index if r["count"] and float(r["count"]))))
    checks.append(("window question got three horizons",
                   any(r["p_one_third"] and r["p_two_thirds"] and r["p_full"]
                       for r in lens_rows)))

    print()
    ok = True
    for label, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed

    if questions:
        print("\nSample question row:")
        for k, v in questions[0].items():
            if v:
                print(f"  {k:22s} {v[:95]}")

    if (config.REPORTS / "latest.json").exists():
        rep = json.loads((config.REPORTS / "latest.json").read_text())
        print("\nReport summary:")
        print(f"  questions scored : {rep['scoring']['questions_scored']}")
        print(f"  abstention rates : {rep['abstention_rates']}")

    print(f"\nSandbox kept for inspection: {sandbox}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
