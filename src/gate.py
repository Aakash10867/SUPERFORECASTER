"""
The portfolio gate.

This is the only place that sees the whole portfolio. It checks FIT, not
quality -- quality was already settled inside each system, where comparison is
meaningful. Here we only ask whether a question belongs alongside the ones we
already have.

Note the deliberate asymmetry: the system-level judges cannot see the
portfolio, so they will sometimes propose a question that is instantly rejected
here for tag concentration. That wasted effort is cheap, and it keeps each
judge's job simple.

Four checks, in order:
  1. Near-duplicate wording (text similarity -- free)
  2. Linked answers (one model call -- the "will X win / will Y win" problem)
  3. Tag concentration cap
  4. Portfolio space
"""

from __future__ import annotations

import datetime as dt
from collections import Counter


LINKED_PROMPT = """You are checking whether a new forecasting question is redundant against questions already in a portfolio.

NEW QUESTION: {new_q}

EXISTING OPEN QUESTIONS:
{existing}

Two questions are REDUNDANT if knowing the answer to one would largely tell you
the answer to the other. This is NOT about similar wording -- it is about
linked answers.

The clearest example: "Will candidate X win the election by 10 September?" and
"Will candidate Y win the same election by 10 September?" share almost no
words, but if you knew one answer you would know the other. That is redundant,
and only one should be in the portfolio.

Another example: "Will the RBI cut rates on or before 30 September?" and "Will
the RBI hold rates through September?" are the same question inverted.

Two questions are NOT redundant merely because they concern the same topic or
the same underlying driver. Several distinct questions can depend on the Iran
war without being redundant -- that is concentration, which is handled
elsewhere, not duplication.

Return JSON only:
{{"redundant_with": "<question id, or null>", "reasoning": "<one sentence>"}}"""


def _norm_tags(value: str) -> list[str]:
    return [t.strip().lower().replace(" ", "_")
            for t in (value or "").split(",") if t.strip()]


def check_near_duplicate(proposal, open_questions, sim, threshold):
    if not open_questions:
        return None
    texts = [q["question"] for q in open_questions]
    idx, score = sim.best_match(proposal.question, texts)
    if idx >= 0 and score >= threshold:
        return open_questions[idx]
    return None


def check_linked(router, proposal, open_questions, settings, log):
    if not open_questions:
        return None
    subset = open_questions[: settings["dedupe"]["linked_check_candidates"]]
    listing = "\n".join(f"{q['id']}: {q['question']}" for q in subset)
    prompt = LINKED_PROMPT.format(new_q=proposal.question, existing=listing)
    result, _ = router.generate("gate", prompt, temperature=0.1, max_output_tokens=600)
    if not result or not isinstance(result, dict):
        # A failed check must not silently admit a duplicate, but it must also
        # not silently reject a good question. We let it through and say so, so
        # the gap is visible in the log rather than hidden.
        log.warn("linked-answer check failed; admitting without that check")
        return None
    match = result.get("redundant_with")
    if not match or str(match).lower() in ("null", "none", ""):
        return None
    match = str(match).strip()
    for q in subset:
        if q["id"] == match:
            return q
    return None


def tag_counts(open_questions) -> Counter:
    return Counter(
        q.get("primary_tag", "").strip()
        for q in open_questions
        if q.get("primary_tag", "").strip()
    )


def concentration_report(open_questions) -> str:
    """
    Warning light, not a filter. Primary tags are capped, but secondary tags
    are not -- so a portfolio can pass every cap and still be far more exposed
    to one event than it looks. This makes that visible.
    """
    primary = tag_counts(open_questions)
    everywhere = Counter()
    for q in open_questions:
        tags = set()
        for field in ("primary_tag", "secondary_tags", "tertiary_tags"):
            tags.update(_norm_tags(q.get(field, "")))
        for t in tags:
            everywhere[t] += 1

    lines = []
    total = len(open_questions)
    if not total:
        return "  (portfolio empty)"
    for tag, n in everywhere.most_common(6):
        p = primary.get(tag, 0)
        flag = ""
        if n >= max(3, total // 2):
            flag = "  <-- HIGH EXPOSURE"
        lines.append(f"  {tag}: primary on {p}, appears anywhere on {n} of {total}{flag}")
    return "\n".join(lines)


def bucket_report(open_questions, settings) -> str:
    counts = Counter(q.get("bucket", "") for q in open_questions)
    target = settings["portfolio"]["target_shape"]
    parts = []
    for b in ("short", "medium", "long"):
        parts.append(f"{b} {counts.get(b, 0)} (rough aim {target.get(b, 0)})")
    return "  " + ", ".join(parts)


class PortfolioGate:
    def __init__(self, router, settings, sim, lexicon, log):
        self.router = router
        self.settings = settings
        self.sim = sim
        self.lexicon = lexicon
        self.log = log

    def admit(self, winners, open_questions, today: dt.date):
        """
        Returns (admitted, deferred). Mutates each proposal's outcome fields.
        `open_questions` is updated as we go, so two winners in the same run
        cannot both take the last slot or both breach a tag cap.
        """
        admitted = []
        deferred = []

        max_open = self.settings["portfolio"]["max_open_questions"]
        max_tag = self.settings["portfolio"]["max_per_primary_tag"]
        near_threshold = self.settings["dedupe"]["question_threshold"]

        for proposal in winners:
            # --- 1. near-duplicate wording -------------------------------
            dup = check_near_duplicate(proposal, open_questions, self.sim, near_threshold)
            if dup:
                proposal.outcome = "duplicate"
                proposal.outcome_reason = f"near-identical wording to {dup['id']}"
                self.log.info(f"  rejected (duplicate of {dup['id']}): {proposal.question[:70]}")
                continue

            # --- 2. linked answers ---------------------------------------
            linked = check_linked(self.router, proposal, open_questions,
                                  self.settings, self.log)
            if linked:
                proposal.outcome = "duplicate"
                proposal.outcome_reason = f"answer linked to {linked['id']}"
                self.log.info(f"  rejected (linked to {linked['id']}): {proposal.question[:70]}")
                continue

            # --- 3. resolve the tag against the lexicon -------------------
            primary = self.lexicon.resolve(
                proposal.proposed_primary_tag,
                proposal.question,
                proposal.tag_justification,
                proposal.system,
            )
            proposal.proposed_primary_tag = primary

            secondary = [
                self.lexicon.resolve(t, proposal.question, "", proposal.system)
                for t in _norm_tags(proposal.proposed_secondary_tags)
            ]
            proposal.proposed_secondary_tags = ", ".join(
                dict.fromkeys(t for t in secondary if t and t != primary)
            )

            # --- 4. tag concentration cap --------------------------------
            counts = tag_counts(open_questions)
            if primary and counts.get(primary, 0) >= max_tag:
                proposal.outcome = "tag_cap"
                proposal.outcome_reason = (
                    f"'{primary}' already primary on {counts[primary]} open questions "
                    f"(cap {max_tag})"
                )
                deferred.append(proposal)
                self.log.info(f"  deferred (tag cap on {primary}): {proposal.question[:70]}")
                continue

            # --- 5. space -------------------------------------------------
            if len(open_questions) >= max_open:
                proposal.outcome = "no_space"
                proposal.outcome_reason = f"portfolio full ({max_open} open questions)"
                deferred.append(proposal)
                self.log.info(f"  deferred (no space): {proposal.question[:70]}")
                continue

            admitted.append(proposal)
            open_questions.append(self._as_question_stub(proposal))

        return admitted, deferred

    @staticmethod
    def _as_question_stub(proposal) -> dict:
        """Minimal record so in-run cap checks see questions admitted moments ago."""
        return {
            "id": f"(pending {proposal.proposal_id})",
            "question": proposal.question,
            "primary_tag": proposal.proposed_primary_tag,
            "secondary_tags": proposal.proposed_secondary_tags,
            "tertiary_tags": proposal.proposed_tertiary_tags,
            "bucket": proposal.bucket,
            "status": "open",
        }
