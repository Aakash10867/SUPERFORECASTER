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
]

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

FORECAST_FIELDS = [
    "question_id",
    "date",
    "model",
    "probability",
    "reason",
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

_SCHEMAS = {
    config.QUESTIONS_CSV: QUESTION_FIELDS,
    config.PROPOSALS_CSV: PROPOSAL_FIELDS,
    config.FORECASTS_CSV: FORECAST_FIELDS,
    config.PROCESSED_CSV: PROCESSED_FIELDS,
    config.WAITING_CSV: WAITING_FIELDS,
    config.PENDING_TAGS_CSV: PENDING_TAG_FIELDS,
}


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
