"""
CSV read/write layer.

Design notes
------------
* questions.csv is the portfolio: one row per question, fixed columns. It is
  updated in place when a question resolves, because the full history of what
  we believed lives in forecasts.csv instead.
* forecasts.csv is append-only: one row every time any model makes a forecast.
  This is what stops questions.csv from growing sideways forever, and it is
  what lets you later ask "which model is best calibrated?".
* proposals.csv is append-only and records EVERY proposal and its fate,
  including the ones that died. questions.csv only shows survivors, so it can
  never tell you which agents are dead weight. This file can.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from . import config

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

QUESTION_FIELDS = [
    "id",
    "question",
    "domain",
    "bucket",
    "created",
    "deadline",
    "primary_tag",
    "secondary_tags",
    "tertiary_tags",
    "source",
    "reasoning_value",     # what hard thinking would actually move
    "significance",        # what changes in the world depending on the answer
    "resolution_criteria", # written BEFORE the answer is known -- guards against hindsight bias
    "resolution_source",   # which paper will carry the resolving story
    "status",              # open | resolved | void
    "resolved_date",       # may be earlier than deadline
    "outcome",             # 1 | 0 | void
    "brier",
    "admitted_by",         # system | human
    "admission_note",

    # ---- stage two additions ---------------------------------------------
    "shape",               # window | point -- see below
    "calendar_hooks",      # known scheduled catalysts, "; " separated
    "resolution_basis",    # confirmed_act | lapsed_absence
    "outcome_set_by",      # system | human
    "watch_until",         # absence-watch expiry; blank when not watching
    "last_refresh",        # date of last full seven-lens refresh
    "probability",         # current live number, 0-100 (cached for display)
    "prob_source",         # refresh | persisted | trigger
]

# WHY `shape` EXISTS
# ------------------
# Scope sensitivity -- the number must move when the time window moves -- is
# built in by asking each lens for three probabilities at one third, two
# thirds and the full horizon. That only makes sense for questions where the
# thing can happen at ANY time before the deadline ("window"). For a question
# that can only resolve at one scheduled event -- will the Fed hike at the
# December meeting -- intermediate probabilities are meaningless, so the three
# horizon numbers are skipped entirely.
#
# When a question is ambiguous, classify it as `point`. The failure modes are
# asymmetric: calling a window a point merely loses a test, while calling a
# point a window invents numbers that correspond to nothing and then feeds
# them to a contradiction check.

PROPOSAL_FIELDS = [
    "proposal_id",
    "date",
    "system",
    "agent",
    "question",
    "deadline",
    "bucket",
    "proposed_primary_tag",
    "proposed_secondary_tags",
    "proposed_tertiary_tags",
    "resolution_criteria",
    "resolution_source",
    "reasoning_value",
    "significance",
    "source",
    "outcome",             # won | lost_in_system | failed_gate | duplicate | tag_cap | no_space | waiting
    "outcome_reason",
    "flagged_exceptional",
]

# forecasts.csv is the NUMBERS-ONLY scoring spine. Everything else a lens
# produced -- its abstraction, its enumerated cases, both sides of the
# argument, its triggers -- lives in data/runs/<date>/<qid>.json, because
# _flatten() below deliberately collapses newlines and structured reasoning
# squeezed into a CSV cell is neither readable nor queryable.
FORECAST_FIELDS = [
    "question_id",
    "date",
    "model",              # aggregate | <lens id>
    "probability",        # the live number, 0-100
    "reason",             # one line; the full text is in the JSON
    "stage",              # outside | inside_yes | inside_no | reconcile | aggregate
    "p_one_third",        # window questions only
    "p_two_thirds",
    "p_full",
    "median_raw",         # aggregate rows: median before any adjustment
    "median_extremized",  # SHADOW ONLY -- never the live number (see §1)
    "advocate_proposed",  # SHADOW ONLY -- the advocate re-weights lenses (see forecast.py)
    "responding_lenses",
    "abstained_lenses",
    "config_version",     # which lens definitions produced this
    "cause",              # refresh | trigger:<id> | screen | creation
    "grounded",           # yes | no | n/a
]

LENS_FIELDS = [
    "question_id",
    "date",
    "lens",
    "status",             # responded | abstained | excluded_audit | failed
    "probability",
    "p_one_third",
    "p_two_thirds",
    "p_full",
    "outside_probability",  # frozen before any news was seen
    "provenance_tier",      # structured | reasoned
    "reference_entry",      # id of the library entry used, if any
    "yes_case_strength",
    "no_case_strength",
    "audit_result",         # clean | retried_clean | excluded
    "audit_note",
    "triggers",             # one line summary; full text in the JSON
    "inside_drift",         # probability minus outside_probability
    "moved_from",           # previous probability, blank on first forecast
    "move_reason",
    "model",
    "config_version",
]

SCREEN_FIELDS = [
    "question_id",
    "date",
    "outcome",            # no_cause | escalate | resolution_nominee | watch_hit
    "reason",             # one line, ALWAYS recorded -- "looked and found
                          # nothing" must be distinguishable from "did not look"
    "articles_considered",
    "trigger_fired",
    "model",
]

DIAGNOSTIC_FIELDS = [
    "date",
    "question_id",
    "kind",               # curve_divergence | trigger_contradiction |
                          # audit_exclusion | thin_lens_set | abstention_shift |
                          # coherence_break | unfired_triggers
    "detail",
    "severity",           # note | flag
]

SYSTEM_PROPOSAL_FIELDS = [
    "date",
    "kind",               # redundant_lenses | mis_scoped_lens | broken_time_model
                          # | unfalsifiable_triggers | coverage_gap | fabrication
    "subject",            # lens id, question id, or reference entry id
    "evidence",
    "suggestion",
    "status",             # open | accepted | rejected
]

PROCESSED_FIELDS = [
    "fingerprint",
    "filename",
    "paper_guess",
    "issue_date_guess",
    "pages",
    "processed_on",
    "articles_kept",
]

WAITING_FIELDS = [
    "proposal_id",
    "added",
    "expires",
    "flagged_exceptional",
    "question",
]

PENDING_TAG_FIELDS = [
    "tag",
    "first_seen",
    "proposed_for_question",
    "nearest_existing",
    "model_justification",
]

REFERENCE_INDEX_FIELDS = [
    "id",
    "built_by",           # lens id -- entries are NOT shared across lenses
    "built_on",
    "frequency_question",
    "membership_rule",
    "window",
    "count",
    "hits",
    "rate",
    "valid_until",
    "state",              # active | superseded | retired
    "supersedes",
    "superseded_by",
    "verified",           # grounded | unverified
    "provenance_tier",    # enumerated | extrapolated | unsupported | reasoned
    "provenance_note",
    "used_by",            # "; " separated question ids -- the blast radius
]

_SCHEMAS = {
    config.QUESTIONS_CSV: QUESTION_FIELDS,
    config.PROPOSALS_CSV: PROPOSAL_FIELDS,
    config.FORECASTS_CSV: FORECAST_FIELDS,
    config.PROCESSED_CSV: PROCESSED_FIELDS,
    config.WAITING_CSV: WAITING_FIELDS,
    config.PENDING_TAGS_CSV: PENDING_TAG_FIELDS,
    config.LENS_CSV: LENS_FIELDS,
    config.SCREENS_CSV: SCREEN_FIELDS,
    config.DIAGNOSTICS_CSV: DIAGNOSTIC_FIELDS,
    config.SYSTEM_PROPOSALS_CSV: SYSTEM_PROPOSAL_FIELDS,
    config.REFERENCE_INDEX_CSV: REFERENCE_INDEX_FIELDS,
}

RESOLUTION_OVERRIDE_FIELDS = [
    "question_id",
    "outcome",         # 1 | 0 | void | reopen
    "resolved_date",   # optional correction; blank keeps the existing date
    "note",
]


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def ensure_files() -> None:
    """Create any missing CSV files with their headers."""
    config.ensure_dirs()
    for path, fields in _SCHEMAS.items():
        if not path.exists():
            with open(path, "w", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=fields).writeheader()

    # overrides.csv lives in config/ because you edit it by hand
    if not config.OVERRIDES_CSV.exists():
        with open(config.OVERRIDES_CSV, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["proposal_id", "note"])
            w.writeheader()

    # resolutions.csv is also hand-edited, but unlike overrides.csv it is
    # NEVER cleared. Admitting a question is a one-time act; a statement about
    # what actually happened is permanent. If this file were emptied after a
    # run, the next recompute would silently revert your correction.
    if not config.RESOLUTIONS_CSV.exists():
        with open(config.RESOLUTIONS_CSV, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=RESOLUTION_OVERRIDE_FIELDS)
            w.writeheader()

    # Migrate older CSVs that predate stage two: add any missing columns
    # without losing data.
    _migrate(config.QUESTIONS_CSV, QUESTION_FIELDS)
    _migrate(config.FORECASTS_CSV, FORECAST_FIELDS)


def _migrate(path: Path, fields: list[str]) -> None:
    """
    Add columns that a file predating this version does not have.

    questions.csv from stage one has no `shape`, `probability` or watch
    columns. Rather than making you rebuild the file by hand, we read it,
    fill the new columns with blanks, and write it back.
    """
    if not path.exists():
        return
    with open(path, "r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        existing = reader.fieldnames or []
        if all(f in existing for f in fields):
            return
        rows = list(reader)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: _flatten(row.get(k, "")) for k in fields})


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def append_row(path: Path, row: dict) -> None:
    fields = _SCHEMAS[path]
    clean = {k: _flatten(row.get(k, "")) for k in fields}
    with open(path, "a", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=fields).writerow(clean)


def append_rows(path: Path, rows: Iterable[dict]) -> None:
    for row in rows:
        append_row(path, row)


def rewrite(path: Path, rows: list[dict]) -> None:
    """Overwrite a whole file. Used when a question's status changes."""
    fields = _SCHEMAS[path]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: _flatten(row.get(k, "")) for k in fields})


def _flatten(value) -> str:
    """
    CSV handles commas and quotes inside fields perfectly well (the writer
    quotes them), but embedded newlines make the file awkward to open in Excel
    and to eyeball in a terminal. Long prose belongs in the daily log file, so
    anything landing here should be short -- we just collapse line breaks.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(v) for v in value)
    return " ".join(str(value).split())


# ---------------------------------------------------------------------------
# Domain-specific accessors
# ---------------------------------------------------------------------------

def open_questions() -> list[dict]:
    return [q for q in read_rows(config.QUESTIONS_CSV) if q.get("status") == "open"]


def all_questions() -> list[dict]:
    return read_rows(config.QUESTIONS_CSV)


def next_question_id() -> str:
    rows = read_rows(config.QUESTIONS_CSV)
    n = 0
    for r in rows:
        qid = r.get("id", "")
        if qid.startswith("Q"):
            try:
                n = max(n, int(qid[1:]))
            except ValueError:
                pass
    return f"Q{n + 1:04d}"


def lexicon() -> list[dict]:
    return read_rows(config.LEXICON_CSV)


def add_lexicon_tag(tag: str, domain_hint: str, description: str, added: str) -> None:
    exists = config.LEXICON_CSV.exists()
    fields = ["tag", "domain_hint", "description", "added", "added_by"]
    with open(config.LEXICON_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if not exists:
            w.writeheader()
        w.writerow({
            "tag": tag,
            "domain_hint": domain_hint,
            "description": _flatten(description),
            "added": added,
            "added_by": "system",
        })


def read_overrides() -> list[dict]:
    if not config.OVERRIDES_CSV.exists():
        return []
    with open(config.OVERRIDES_CSV, "r", newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("proposal_id") or "").strip()]


def clear_overrides() -> None:
    with open(config.OVERRIDES_CSV, "w", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=["proposal_id", "note"]).writeheader()


# ---------------------------------------------------------------------------
# Stage two accessors
# ---------------------------------------------------------------------------

def read_resolution_overrides() -> list[dict]:
    """
    Hand-written corrections to outcomes. Read fresh every run, never cleared.

    Five operations are supported:
      outcome=1 / 0   flip or set an outcome
      outcome=void    the question was ill-posed; excluded from all scoring
      outcome=reopen  resolved in error; put it back to open
      resolved_date   correct WHEN it resolved, which matters as much as the
                      outcome: the day-weighted trail is scored up to
                      resolution, so a question that actually resolved in
                      October but lapsed in December was being scored against
                      an already-decided question for 89 days.
    """
    if not config.RESOLUTIONS_CSV.exists():
        return []
    with open(config.RESOLUTIONS_CSV, "r", newline="", encoding="utf-8") as fh:
        return [
            r for r in csv.DictReader(fh)
            if (r.get("question_id") or "").strip()
        ]


def question_by_id(qid: str) -> dict | None:
    for q in read_rows(config.QUESTIONS_CSV):
        if q.get("id") == qid:
            return q
    return None


def update_question(qid: str, changes: dict) -> bool:
    """Update one question in place. Returns False if the id is unknown."""
    rows = read_rows(config.QUESTIONS_CSV)
    found = False
    for r in rows:
        if r.get("id") == qid:
            r.update(changes)
            found = True
    if found:
        rewrite(config.QUESTIONS_CSV, rows)
    return found


def watched_questions() -> list[dict]:
    """
    Questions that lapsed for want of news and are still being watched.

    Only `lapsed_absence` questions are watched. A question closed by a
    definitive reported act is settled and needs no further looking.
    """
    out = []
    for q in read_rows(config.QUESTIONS_CSV):
        if q.get("status") != "resolved":
            continue
        if q.get("resolution_basis") != "lapsed_absence":
            continue
        if (q.get("outcome_set_by") or "") == "human":
            # A human decision is terminal for the watch: the system stops
            # looking and never second-guesses you.
            continue
        if not (q.get("watch_until") or "").strip():
            continue
        out.append(q)
    return out


def forecasts_for(qid: str) -> list[dict]:
    return [
        f for f in read_rows(config.FORECASTS_CSV)
        if f.get("question_id") == qid
    ]


# -- the per-question JSON record -------------------------------------------

def run_dir(date_iso: str) -> Path:
    d = config.RUNS / date_iso
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_run_record(date_iso: str, qid: str, record: dict) -> Path:
    """
    Store everything that produced today's number for one question.

    This is the file you read when you want to know WHY, and it is what makes
    the fast clock work: pre-declared triggers, both sides of every argument,
    the enumerated cases behind every base rate. CSVs cannot hold it.
    """
    path = run_dir(date_iso) / f"{qid}.json"
    if path.exists():
        # Two runs in one day: keep both rather than overwriting.
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            prior = None
        if prior is not None:
            history = prior.get("_earlier_runs", [])
            stripped = {k: v for k, v in prior.items() if k != "_earlier_runs"}
            history.append(stripped)
            record = dict(record)
            record["_earlier_runs"] = history
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    return path


def read_run_record(date_iso: str, qid: str) -> dict | None:
    path = config.RUNS / date_iso / f"{qid}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def latest_run_record(qid: str, before: str | None = None) -> dict | None:
    """Most recent stored reasoning for a question, optionally before a date."""
    if not config.RUNS.exists():
        return None
    dates = sorted(
        (d.name for d in config.RUNS.iterdir() if d.is_dir()), reverse=True
    )
    for d in dates:
        if before and d >= before:
            continue
        rec = read_run_record(d, qid)
        if rec:
            return rec
    return None
