"""
Agents propose questions.

Each agent hunts ONE SHAPE of question (see config/agents.yaml). They read the
same articles but look for different things, so they cannot produce the same
question. That is what makes the diversity real rather than cosmetic -- five
agents with five personalities but the same brief would produce five wordings
of one idea, and the deduplication step would then have to clean up a mess we
created ourselves.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass
class Proposal:
    proposal_id: str = ""
    date: str = ""
    system: str = ""
    agent: str = ""
    question: str = ""
    deadline: str = ""
    bucket: str = ""
    proposed_primary_tag: str = ""
    proposed_secondary_tags: str = ""
    proposed_tertiary_tags: str = ""
    resolution_criteria: str = ""
    resolution_source: str = ""
    reasoning_value: str = ""
    source: str = ""
    outcome: str = ""
    outcome_reason: str = ""
    flagged_exceptional: str = ""
    tag_justification: str = ""

    def as_row(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "date": self.date,
            "system": self.system,
            "agent": self.agent,
            "question": self.question,
            "deadline": self.deadline,
            "bucket": self.bucket,
            "proposed_primary_tag": self.proposed_primary_tag,
            "proposed_secondary_tags": self.proposed_secondary_tags,
            "proposed_tertiary_tags": self.proposed_tertiary_tags,
            "resolution_criteria": self.resolution_criteria,
            "resolution_source": self.resolution_source,
            "reasoning_value": self.reasoning_value,
            "source": self.source,
            "outcome": self.outcome,
            "outcome_reason": self.outcome_reason,
            "flagged_exceptional": self.flagged_exceptional,
        }


PROMPT = """You are a forecasting question generator working for a superforecasting system. Today's date is {today}.

YOUR DOMAIN: {system_name}
{system_brief}

YOUR HUNT -- this is the only shape of question you look for:
{hunt}

Other agents are hunting other shapes. Do not stray outside yours. If today's
articles contain nothing of your shape, return an empty array. Producing
nothing is a perfectly good outcome and far better than forcing a weak question.

RULES EVERY QUESTION MUST OBEY:

1. YES/NO ONLY. The answer must be strictly yes or no.

2. THE DATE GOES INSIDE THE QUESTION. Write "Will the RBI cut the repo rate on
   or before 15 September 2026?" -- not "Will the RBI cut rates?" with a
   separate date field. This means the deadline passing with nothing happening
   resolves the question NO, and the question resolves early if the thing
   happens early. Deadlines are never moved.

3. RESOLVABLE FROM NEWSPAPERS. The system reads {papers} and similar papers.
   State concretely which paper will carry the resolving story and roughly
   where. Newspapers reliably report LAUNCHES and unreliably report RESULTS --
   if you cannot name the story that will run when this resolves, do not
   propose it.

4. NOT ANSWERED BY THE SOURCE ARTICLE. If the article effectively tells you the
   answer, there is no forecast here, only copying. Reject those.

5. NOT INHERENTLY RANDOM. Some things cannot be forecast no matter how hard you
   think -- short-horizon market prices above all. Anything of the form "will
   this number be above X on date Y" deserves deep suspicion. Questions about
   decisions a known group of people will make are far more tractable, because
   you can reason about their incentives.

6. GOLDILOCKS. The question must be genuinely uncertain to a well-informed
   reader AND serious reasoning must move a forecaster meaningfully away from a
   naive guess. A coin flip fails this (no reasoning helps). An obvious outcome
   fails it (no uncertainty). You must state CONCRETELY what reasoning would
   help -- not a claim that it would, but what specifically a careful analyst
   would examine. If you cannot name the reasoning, you have not got any, and
   you should not propose the question.

7. HORIZON. Deadline must be between 14 and 730 days from today. Beyond two
   years, forecasting stops beating guesswork.

For each question return:
- "question": the full question text, with the date inside it
- "deadline": YYYY-MM-DD, the date in the question
- "resolution_criteria": exactly what counts as YES, written now, before anyone
  knows the answer. Be precise enough that two people reading it later could
  not disagree.
- "resolution_source": which paper and what kind of story will resolve this
- "reasoning_value": what specifically a careful analyst would examine that
  would move their forecast away from a naive guess
- "primary_tag": the ONE underlying driver such that, if it went the other way,
  your forecast would change a lot. Use lowercase_with_underscores.
- "secondary_tags": up to 3 other drivers that matter but would not alone flip
  the answer
- "tertiary_tags": up to 5 drivers that have some influence but little weight
- "tag_justification": one sentence on why the primary tag is primary
- "source": the headline of the article this came from

Propose AT MOST 3 questions. Quality matters enormously more than quantity.
Return ONLY a JSON array, or [] if nothing fits your shape.

TODAY'S ARTICLES:
---
{articles}
---"""


def _bucket_for(deadline: str, today: dt.date, settings: dict) -> str:
    try:
        d = dt.date.fromisoformat(deadline)
    except (ValueError, TypeError):
        return ""
    days = (d - today).days
    for name, rng in settings["buckets"].items():
        if rng["min_days"] <= days <= rng["max_days"]:
            return name
    return ""


def run_agent(router, system_key, system_cfg, shape_key, shape_cfg,
              articles, settings, papers_desc, today, log, seq) -> list[Proposal]:
    """Run one agent over today's articles."""
    context = "\n\n".join(a.as_context() for a in articles)
    # Keep well inside the token limit; articles are already condensed.
    context = context[:120000]

    prompt = PROMPT.format(
        today=today.isoformat(),
        system_name=system_cfg["name"],
        system_brief=system_cfg["brief"].strip(),
        hunt=shape_cfg["hunt"].strip(),
        papers=papers_desc,
        articles=context,
    )

    result, model = router.generate("propose", prompt, temperature=0.6,
                                    max_output_tokens=6000)
    if not result:
        log.warn(f"agent {system_key}/{shape_key} produced nothing (model failure)")
        return []
    if not isinstance(result, list):
        return []

    proposals = []
    for item in result:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question", "")).strip()
        deadline = str(item.get("deadline", "")).strip()
        if not q or not deadline:
            continue

        bucket = _bucket_for(deadline, today, settings)
        if not bucket:
            log.info(
                f"  rejected (horizon): {q[:70]}... deadline {deadline} is "
                "outside the 14-730 day window"
            )
            continue

        # A proposal with no named reasoning value has failed the Goldilocks
        # test by its own admission, so it never reaches the contest.
        reasoning_value = str(item.get("reasoning_value", "")).strip()
        if len(reasoning_value.split()) < 8:
            log.info(f"  rejected (no named reasoning): {q[:70]}...")
            continue

        seq[0] += 1
        proposals.append(Proposal(
            proposal_id=f"P-{today.isoformat()}-{seq[0]:03d}",
            date=today.isoformat(),
            system=system_key,
            agent=shape_key,
            question=q,
            deadline=deadline,
            bucket=bucket,
            proposed_primary_tag=str(item.get("primary_tag", "")).strip(),
            proposed_secondary_tags=_join(item.get("secondary_tags"),
                                          settings["tags"]["max_secondary"]),
            proposed_tertiary_tags=_join(item.get("tertiary_tags"),
                                         settings["tags"]["max_tertiary"]),
            resolution_criteria=str(item.get("resolution_criteria", "")).strip(),
            resolution_source=str(item.get("resolution_source", "")).strip(),
            reasoning_value=reasoning_value,
            source=str(item.get("source", "")).strip(),
            tag_justification=str(item.get("tag_justification", "")).strip(),
        ))

    log.info(f"  {system_key}/{shape_key}: {len(proposals)} proposals")
    return proposals


def _join(value, limit: int) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    else:
        parts = [str(p).strip() for p in value if str(p).strip()]
    parts = [p.lower().replace(" ", "_") for p in parts]
    return ", ".join(parts[:limit])


def relevant_articles(articles, system_key: str):
    """
    Articles the triage step tagged for this domain, plus untagged ones.

    Untagged articles are included deliberately: triage's domain guess is a
    hint, not a ruling, and an article it could not classify might still
    contain the best question of the day.
    """
    out = []
    for a in articles:
        domains = [d.strip() for d in (a.domains or "").split(",") if d.strip()]
        if not domains or system_key in domains:
            out.append(a)
    return out
