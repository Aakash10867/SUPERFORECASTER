"""
Tag assignment against a controlled vocabulary.

The failure this exists to prevent is silent and slow: the system creates
"Iran war", then "West Asia conflict", then "Middle East tensions" as three
separate tags for one thing. The concentration cap then never triggers, and the
portfolio looks diversified while being anything but. You would not notice for
months.

Two guardrails:

1. The model NEVER scans the whole lexicon. We find the nearest few existing
   tags first, then ask it to choose among those. Choosing from five is a much
   easier judgement than searching a hundred.

2. The prompt is MERGE-BIASED. It does not ask "are these different?" -- a
   model asked that will usually agree they are. It asks the model to ASSUME
   this is a duplicate and argue for which one. A new tag is created only if no
   match can be argued. A wrongly merged tag is visible immediately; a wrongly
   split one hides for months, so we lean toward merging.
"""

from __future__ import annotations

import datetime as dt

from . import store
from .dedupe import Similarity


MATCH_PROMPT = """You are maintaining a controlled vocabulary of tags describing the underlying drivers of forecasting questions.

A new question has been assigned this proposed tag:

PROPOSED TAG: {proposed}
QUESTION: {question}
WHY THIS TAG: {justification}

Here are the closest existing tags already in the vocabulary:

{candidates}

ASSUME the proposed tag is a duplicate of one of the existing tags above. Your
task is to identify WHICH ONE and explain why they describe the same underlying
driver.

Only if no match can honestly be argued -- if this genuinely describes a driver
that none of the existing tags covers -- should you say it is new.

Bias strongly toward matching. Two tags describing the same real-world force
under different words MUST be merged. A vocabulary that splits one driver into
several labels is worse than useless, because it hides concentration.

Return JSON only:
{{"match": "<existing tag name, or NEW>", "reasoning": "<one or two sentences>"}}"""


class Lexicon:
    def __init__(self, router, settings, sim: Similarity, log):
        self.router = router
        self.settings = settings
        self.sim = sim
        self.log = log
        self.rows = store.lexicon()
        self.tags = {r["tag"]: r for r in self.rows}

    def _candidate_block(self, proposed: str) -> tuple[str, list[str]]:
        n = self.settings["tags"]["candidates_shown"]
        names = list(self.tags.keys())
        if not names:
            return "", []
        descriptions = [
            f"{t}: {self.tags[t].get('description', '')}" for t in names
        ]
        ranked = self.sim.rank(proposed, descriptions)[:n]
        chosen = [names[i] for i, _ in ranked]
        block = "\n".join(f"- {descriptions[i]}" for i, _ in ranked)
        return block, chosen

    def resolve(self, proposed: str, question: str, justification: str,
                domain: str) -> str:
        """
        Map a proposed tag onto the controlled vocabulary. Returns the tag name
        to actually use -- either an existing one, or a newly created one.
        """
        proposed = (proposed or "").strip().lower().replace(" ", "_")
        if not proposed:
            return ""

        # Exact hit: nothing to decide.
        if proposed in self.tags:
            return proposed

        block, candidates = self._candidate_block(proposed)
        if not candidates:
            return self._create(proposed, question, domain, "lexicon was empty")

        prompt = MATCH_PROMPT.format(
            proposed=proposed,
            question=question,
            justification=justification or "(none given)",
            candidates=block,
        )
        result, _ = self.router.generate("lexicon", prompt, temperature=0.1,
                                         max_output_tokens=512)

        if not result or not isinstance(result, dict):
            # If the check fails we merge into the nearest neighbour rather
            # than creating a new tag. Failing toward merging keeps the
            # vocabulary tight; failing toward creation would let it sprawl
            # exactly when we have least information.
            self.log.warn(
                f"Tag check failed for '{proposed}'; merging into nearest "
                f"existing tag '{candidates[0]}'"
            )
            return candidates[0]

        match = str(result.get("match", "")).strip()
        if match and match.upper() != "NEW" and match in self.tags:
            if match != proposed:
                self.log.info(f"Tag '{proposed}' merged into existing '{match}'")
            return match

        return self._create(
            proposed, question, domain,
            str(result.get("reasoning", "")),
        )

    def _create(self, tag: str, question: str, domain: str, reasoning: str) -> str:
        today = dt.date.today().isoformat()
        description = f"Auto-created from question: {question[:150]}"
        store.add_lexicon_tag(tag, domain, description, today)
        self.tags[tag] = {
            "tag": tag, "domain_hint": domain,
            "description": description, "added": today, "added_by": "system",
        }

        # New tags go live immediately -- they never block a question. The
        # pending file is a RECORD, not a gate: if the system created eight new
        # tags in a month, something is wrong with the lexicon and you want to
        # see that. Thirty seconds of your attention, blocking nothing.
        store.append_row(
            store.config.PENDING_TAGS_CSV,
            {
                "tag": tag,
                "first_seen": today,
                "proposed_for_question": question,
                "nearest_existing": ", ".join(list(self.tags)[:3]),
                "model_justification": reasoning,
            },
        )
        self.log.info(f"NEW TAG created: '{tag}' (logged to pending_tags.csv for your review)")
        return tag
