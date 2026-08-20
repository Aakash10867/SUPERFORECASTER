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

    def generate(self, task, prompt, *, expect_json=True, temperature=0.4,
                 max_output_tokens=4096):
        self._counter += 1
        self.stats.total_calls += 1
        self.stats.calls_by_model[f"fake-{task}"] += 1

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
    config.LEXICON_CSV = sandbox / "lexicon.csv"
    config.OVERRIDES_CSV = sandbox / "overrides.csv"
    shutil.copy(root / "config" / "lexicon.csv", config.LEXICON_CSV)

    store._SCHEMAS = {
        config.QUESTIONS_CSV: store.QUESTION_FIELDS,
        config.PROPOSALS_CSV: store.PROPOSAL_FIELDS,
        config.FORECASTS_CSV: store.FORECAST_FIELDS,
        config.PROCESSED_CSV: store.PROCESSED_FIELDS,
        config.WAITING_CSV: store.WAITING_FIELDS,
        config.PENDING_TAGS_CSV: store.PENDING_TAG_FIELDS,
    }

    pipeline.ModelRouter = FakeRouter

    print("=" * 70)
    print("RUN 1 -- three papers, fresh portfolio")
    print("=" * 70)
    added = pipeline.run(today=dt.date(2026, 8, 19))

    print("\n" + "=" * 70)
    print("RUN 2 -- same papers again (must be skipped by fingerprint)")
    print("=" * 70)
    pipeline.run(today=dt.date(2026, 8, 20))

    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)

    questions = store.read_rows(config.QUESTIONS_CSV)
    proposals = store.read_rows(config.PROPOSALS_CSV)
    processed = store.read_rows(config.PROCESSED_CSV)

    print(f"questions.csv : {len(questions)} rows")
    print(f"proposals.csv : {len(proposals)} rows")
    print(f"processed.csv : {len(processed)} papers")

    checks = []
    checks.append(("papers fingerprinted", len(processed) == 3))
    checks.append(("questions created", len(questions) > 0))
    checks.append(("proposals recorded", len(proposals) > 0))
    checks.append(("rejections recorded",
                   any(p["outcome"] != "won" for p in proposals)))
    checks.append(("tag cap enforced",
                   sum(1 for q in questions
                       if q["primary_tag"] == "rbi_monetary_policy") <= 3))
    checks.append(("all questions have resolution criteria",
                   all(q["resolution_criteria"] for q in questions)))
    checks.append(("all questions have reasoning value",
                   all(q["reasoning_value"] for q in questions)))
    checks.append(("second run skipped duplicates",
                   len(processed) == 3))
    checks.append(("log written", (config.LOGS / "2026-08-19.md").exists()))

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

    print(f"\nSandbox kept for inspection: {sandbox}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
