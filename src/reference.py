"""
The reference-class library.

WHY THIS EXISTS
---------------
This is the one place the system is allowed to genuinely improve itself. Every
time a lens builds an enumerated skeleton -- five hiking cycles since 1994; how
many RBI consultative proposals became final circulars; how many announced
summits actually convened -- it is stored as a reusable object. Over time you
accumulate a private, human-auditable store of base rates that does not depend
on the model remembering them, which is the whole answer to having no search
grounding on most models.

It is the SAFE form of self-improvement:
  * no Goodhart surface -- an entry either contains a usable structure or it
    does not
  * it does not fragment outcome cohorts, because the system's METHOD is
    unchanged
  * corrections you make by hand persist forever

THE UNIT IS A POPULATION, NOT A TOPIC
-------------------------------------
"RBI rulemaking" is a topic and matches everything. An entry is ONE answerable
frequency question with an explicit membership rule:

    Of RBI proposals published for public comment between 2015 and 2026, what
    fraction became final circulars, and within what time?

Two questions can both be "about the RBI" and need different populations -- all
proposals, versus only those affecting NBFCs. Topic similarity would merge
them. Population matching will not.

MATCHING IS DELIBERATELY STRICT
-------------------------------
It is better to add a near-duplicate entry than to reuse a wrong number. So the
default is NO MATCH, and three gates must all pass.

NO CROSS-LENS SHARING
---------------------
An entry belongs to the lens that built it. Sharing would be more efficient,
but it is also a route by which the seven apertures start converging on the
same material, which is exactly what the lens design exists to prevent. The
duplication buys a diagnostic: if two lenses independently build near-identical
populations and get different rates, that divergence is a free signal.
"""

from __future__ import annotations

import datetime as dt
import json
import re

from . import config, store

# Only ever returns candidates; a model confirms or rejects them.
SIMILARITY_FLOOR = 0.78


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:n] or "entry"


def next_entry_id(lens: str) -> str:
    rows = store.read_rows(config.REFERENCE_INDEX_CSV)
    n = 0
    for r in rows:
        rid = r.get("id", "")
        m = re.match(r"^R(\d+)", rid)
        if m:
            n = max(n, int(m.group(1)))
    return f"R{n + 1:04d}-{lens}"


def entry_path(entry_id: str):
    return config.REFERENCE / f"{entry_id}.json"


def load_entry(entry_id: str) -> dict | None:
    p = entry_path(entry_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def active_entries(lens: str) -> list[dict]:
    """Index rows for this lens that are still usable."""
    return [
        r for r in store.read_rows(config.REFERENCE_INDEX_CSV)
        if r.get("built_by") == lens and r.get("state") == "active"
    ]


def save_entry(entry: dict, log=None) -> str:
    """Write the full JSON and add or update its index row."""
    entry_id = entry["id"]
    config.REFERENCE.mkdir(parents=True, exist_ok=True)
    with open(entry_path(entry_id), "w", encoding="utf-8") as fh:
        json.dump(entry, fh, indent=2, ensure_ascii=False)

    rows = store.read_rows(config.REFERENCE_INDEX_CSV)
    index_row = {
        "id": entry_id,
        "built_by": entry.get("built_by", ""),
        "built_on": entry.get("built_on", ""),
        "frequency_question": entry.get("frequency_question", ""),
        "membership_rule": entry.get("membership_rule", ""),
        "window": entry.get("window", ""),
        "count": entry.get("count", ""),
        "hits": entry.get("hits", ""),
        "rate": entry.get("rate", ""),
        "valid_until": entry.get("valid_until", ""),
        "state": entry.get("state", "active"),
        "supersedes": entry.get("supersedes", ""),
        "superseded_by": entry.get("superseded_by", ""),
        "verified": entry.get("verified", "unverified"),
        "provenance_tier": entry.get("provenance_tier", ""),
        "provenance_note": entry.get("provenance_note", ""),
        "used_by": "; ".join(entry.get("used_by", []) or []),
    }
    replaced = False
    for i, r in enumerate(rows):
        if r.get("id") == entry_id:
            rows[i] = index_row
            replaced = True
    if not replaced:
        rows.append(index_row)
    store.rewrite(config.REFERENCE_INDEX_CSV, rows)
    return entry_id


def mark_state(entry_id: str, state: str, log=None, **extra) -> None:
    """
    Change an entry's state. NOTHING IS EVER DELETED.

    active      matchable and usable
    superseded  a newer entry extends it; kept, chain visible via supersedes
    retired     never returned as a candidate again; kept, still readable

    That is the reconciliation between "entries never expire" (the RECORD is
    permanent) and "stop using stale numbers" (usability can end).
    """
    entry = load_entry(entry_id)
    if entry is None:
        return
    entry["state"] = state
    entry.update(extra)
    entry.setdefault("state_history", []).append(
        {"state": state, "on": dt.date.today().isoformat()}
    )
    save_entry(entry)
    if log and state == "retired":
        users = "; ".join(entry.get("used_by", []) or []) or "(none recorded)"
        log.flag(
            f"Reference entry {entry_id} RETIRED (extension failed). "
            f"Forecasts that leaned on it: {users}"
        )


def record_use(entry_id: str, qid: str) -> None:
    """
    Note which question used an entry.

    Reuse creates correlation across time: if an entry is wrong and forty
    forecasts drew on it, all forty are wrong together, and the calibration
    table will show a systematic miss with no obvious cause. This list is the
    blast radius, so the error is at least diagnosable.
    """
    entry = load_entry(entry_id)
    if entry is None:
        return
    used = entry.setdefault("used_by", [])
    if qid not in used:
        used.append(qid)
        save_entry(entry)


def is_stale(row: dict, today: dt.date) -> bool:
    vu = (row.get("valid_until") or "").strip()
    if not vu:
        return True
    try:
        return dt.date.fromisoformat(vu) < today
    except ValueError:
        return True


def cap_validity(claimed: str, built_on: dt.date, settings: dict) -> str:
    """
    Each entry declares its own validity horizon, but capped.

    A global constant would be wrong in both directions: FOMC hiking cycles
    since 1994 are structurally stable and stay valid for years, while "how
    often this administration finalises contested rules" rots fast. So the lens
    that builds the entry sets the horizon and justifies it -- but a lens left
    to itself will claim long horizons to avoid future work, so its claim can
    only ever be SHORTER than the ceiling, never longer.
    """
    months = int(
        settings.get("reference", {}).get("max_validity_months", 12)
    )
    ceiling = built_on + dt.timedelta(days=int(months * 30.44))
    try:
        claimed_date = dt.date.fromisoformat((claimed or "").strip())
    except ValueError:
        return ceiling.isoformat()
    return min(claimed_date, ceiling).isoformat()


def audit_provenance(payload: dict) -> tuple[str, str]:
    """
    Check that a claimed frequency is actually supported by what was enumerated.

    THE CASE THIS EXISTS FOR
    ------------------------
    On the first live run, R0001 listed 8 cases of which 7 were hits, then
    claimed count=16 and hits=11 via "8 jurisdictions over 2 cycles". The extra
    five hits were asserted, not enumerated -- and the entry was still tagged
    `structured`, which is the tier reserved for numbers with real structure
    underneath. The arithmetic was internally consistent (11/16 = 0.6875), so
    nothing caught it.

    Returns (tier, note):
      enumerated   count matches the case list; the number IS the cases
      extrapolated count exceeds the cases, but a skeleton and multiplier are
                   given -- legitimate, and honestly labelled
      unsupported  count exceeds the cases with no skeleton to justify it
    """
    cases = payload.get("cases") or []
    n_cases = len(cases)
    n_hits = sum(1 for c in cases
                 if str(c.get("outcome", "")).lower().startswith("hit"))
    count = _int(payload.get("count"), n_cases)
    hits = _int(payload.get("hits"), n_hits)

    if not cases:
        return "reasoned", "no cases enumerated"
    if count <= n_cases:
        if hits != n_hits:
            return ("enumerated",
                    f"claimed {hits} hits but {n_hits} of {n_cases} cases are "
                    "marked hit; rate recomputed from the cases")
        return "enumerated", ""
    skeleton = (payload.get("skeleton") or "").strip()
    multiplier = (payload.get("multiplier") or "").strip()
    if skeleton and multiplier:
        return ("extrapolated",
                f"{n_cases} cases named, {count} claimed via skeleton x multiplier")
    return ("unsupported",
            f"claims {count} cases but only {n_cases} are named, with no "
            "skeleton or multiplier to justify the rest")


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def build_entry(
    lens: str,
    payload: dict,
    today: dt.date,
    settings: dict,
    grounding: dict | None = None,
) -> dict:
    """Turn a lens's structured output into a stored entry."""
    entry_id = next_entry_id(lens)
    cases = payload.get("cases") or []
    verified = "unverified"
    if grounding and grounding.get("fired"):
        verified = "grounded"

    tier, note = audit_provenance(payload)
    count = _int(payload.get("count"), len(cases))
    hits = _int(payload.get("hits"), 0)
    if tier == "enumerated":
        # The cases ARE the evidence, so derive the numbers from them rather
        # than trusting the summary the model wrote alongside.
        count = len(cases)
        hits = sum(1 for c in cases
                   if str(c.get("outcome", "")).lower().startswith("hit"))
    rate = round(hits / count, 4) if count else ""

    return {
        "id": entry_id,
        "built_by": lens,
        "built_on": today.isoformat(),
        "frequency_question": payload.get("frequency_question", ""),
        "membership_rule": payload.get("membership_rule", ""),
        "window": payload.get("window", ""),
        "cases": cases,
        "count": count,
        "hits": hits,
        "rate": rate,
        "claimed_count": payload.get("count", ""),
        "claimed_hits": payload.get("hits", ""),
        "claimed_rate": payload.get("rate", ""),
        "provenance_tier": tier,
        "provenance_note": note,
        "coverage_note": payload.get("coverage_note", ""),
        "known_gaps": payload.get("known_gaps", ""),
        "skeleton": payload.get("skeleton", ""),
        "multiplier": payload.get("multiplier", ""),
        "second_decomposition": payload.get("second_decomposition", ""),
        "valid_until": cap_validity(
            payload.get("valid_until", ""), today, settings
        ),
        "validity_reason": payload.get("validity_reason", ""),
        "state": "active",
        "supersedes": "",
        "superseded_by": "",
        "verified": verified,
        "grounding": grounding or {},
        "used_by": [],
    }


def supersede(old_id: str, new_entry: dict, log=None) -> dict:
    """Extension succeeded: the new entry replaces the old for future use."""
    new_entry["supersedes"] = old_id
    save_entry(new_entry)
    old = load_entry(old_id)
    if old is not None:
        old["superseded_by"] = new_entry["id"]
        old["state"] = "superseded"
        save_entry(old)
    if log:
        log.info(
            f"    reference {old_id} superseded by {new_entry['id']} "
            "(window extended)"
        )
    return new_entry


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

CONFIRM_PROMPT = """You are checking whether a stored reference class can be \
reused for a new forecasting question. The default answer is NO. Reusing a \
wrong population is far worse than building a new one.

STORED ENTRY
Frequency question: {frequency_question}
Membership rule: {membership_rule}
Window: {window}

THE PRESENT CASE
Abstracted question: {abstraction}
Population needed: {needed}

Answer TWO questions SEPARATELY. Both must be a clear yes for a match.

1. CONTAINMENT: does the stored membership rule actually contain the present \
case? Not "is it about the same topic" -- does the case literally satisfy the \
rule as written?

2. SAME FREQUENCY: is the frequency being asked the same frequency? A rate of \
"proposals that became final rules" is not the same as "proposals that became \
final rules within twelve months".

If you are uncertain about either, answer no.

Return JSON only:
{{"contained": true/false, "contained_reason": "...", \
"same_frequency": true/false, "same_frequency_reason": "..."}}"""


def find_candidates(lens: str, needed: str, similarity, top: int = 3) -> list[dict]:
    """
    Gate 1: embedding similarity produces CANDIDATES ONLY, never a decision.

    A high floor, because loose retrieval reuses the wrong base rate, which is
    the expensive failure.
    """
    rows = active_entries(lens)
    if not rows or not needed:
        return []
    texts = [
        f"{r.get('frequency_question','')} {r.get('membership_rule','')}"
        for r in rows
    ]
    try:
        ranked = similarity.rank(needed, texts)
    except Exception:
        # Retrieval failing is not an error worth stopping for: the lens simply
        # builds a fresh entry, which is the safe direction.
        return []
    return [rows[i] for i, sc in ranked[:top] if sc >= SIMILARITY_FLOOR]
