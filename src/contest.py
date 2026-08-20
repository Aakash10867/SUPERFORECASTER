"""
The within-system contest.

Competition happens INSIDE each domain, never across domains. Ranking a good
RBI question against a good election question compares things that are not
comparable, and whatever answer a model gives is close to arbitrary. Comparing
two RBI questions is a real judgement.

So each system produces its own best, or nothing, and the four winners go
forward independently. Up to four questions on a great news day; zero on a
dead one.

Order matters here:
  1. GATES, applied to each proposal individually. Fail one and you are out,
     regardless of how interesting the question is. Gates do not distort
     behaviour the way rankings do, because passing one gives you no advantage
     over anything else that passed.
  2. RANKING of survivors on Goldilocks alone. Deliberately NOT on resolution
     speed or on information edge -- any criterion the system optimises for
     gets gamed, and those two would push it steadily toward the easy and the
     near.
  3. AN ABSOLUTE BAR. The winner must be good in itself, not merely the best of
     a weak batch. Without this instruction stated explicitly, a judge always
     crowns someone.
"""

from __future__ import annotations


PROMPT = """You are judging forecasting questions proposed for one domain today. Today is {today}.

DOMAIN: {system_name}

STEP 1 -- GATES. Apply these to each question individually. A question failing
ANY gate is eliminated, no matter how interesting it is.

  GATE A -- RESOLVABLE. Will a specific story actually run, in one of these
  papers, when this resolves? {papers}. Newspapers reliably report launches and
  unreliably report results. Vague expectation of coverage is a failure. Also
  check the resolution criteria are precise enough that two people could not
  later disagree about the answer.

  GATE B -- NOT INHERENTLY RANDOM. Could careful thought actually beat a coin
  flip here? Short-horizon market prices, index levels and exchange rates
  almost never pass this. Decisions by identifiable people usually do, because
  you can reason about their incentives.

  GATE C -- NOT ALREADY ANSWERED. Does the source article effectively give away
  the answer? If so this is copying, not forecasting.

  GATE D -- DATE INSIDE THE QUESTION. The question text must contain its own
  deadline, so that the deadline passing with nothing happening resolves it NO.

STEP 2 -- RANK the survivors on ONE criterion only:

  Is this genuinely uncertain to a well-informed reader, AND would serious
  reasoning move a forecaster meaningfully away from a naive guess?

  Both halves matter. A coin flip is maximally uncertain and fails, because no
  reasoning helps. An obvious outcome fails too. What you want is the question
  where the gap between a lazy answer and a careful one is largest -- that gap
  is where forecasting skill lives.

  Judge the stated reasoning_value critically. If it is vague hand-waving
  rather than a concrete account of what an analyst would examine, rank it low.

STEP 3 -- THE ABSOLUTE BAR. Look at your top-ranked question and ask honestly:
is this genuinely a good question, or merely the best of a weak batch? If it is
merely the best of a weak batch, return no winner. Returning nothing is a
correct and common outcome. Do NOT crown a winner just because you were given
candidates.

Return JSON only:
{{
  "winner": "<proposal_id, or null if none clears the bar>",
  "winner_reasoning": "<why this one, and why it clears the absolute bar>",
  "exceptional": <true only if this concerns a genuine regime break -- a rare, systemically significant event, not merely a loud news day>,
  "eliminated": [
    {{"proposal_id": "...", "outcome": "failed_gate" or "lost_in_system", "reason": "<short, specific>"}}
  ]
}}

CANDIDATES:
{candidates}"""


def _format(proposals) -> str:
    blocks = []
    for p in proposals:
        blocks.append(
            f"proposal_id: {p.proposal_id}\n"
            f"agent (question shape hunted): {p.agent}\n"
            f"question: {p.question}\n"
            f"deadline: {p.deadline} (bucket: {p.bucket})\n"
            f"resolution_criteria: {p.resolution_criteria}\n"
            f"resolution_source: {p.resolution_source}\n"
            f"reasoning_value: {p.reasoning_value}\n"
            f"source_article: {p.source}"
        )
    return "\n\n---\n\n".join(blocks)


def run_contest(router, system_key, system_cfg, proposals, papers_desc, today, log):
    """
    Returns (winner_or_None, exceptional_flag). Every proposal has its outcome
    and outcome_reason set as a side effect, so proposals.csv records the fate
    of everything -- including what died and why.
    """
    if not proposals:
        return None, False

    prompt = PROMPT.format(
        today=today.isoformat(),
        system_name=system_cfg["name"],
        papers=papers_desc,
        candidates=_format(proposals),
    )

    result, model = router.generate("contest", prompt, temperature=0.2,
                                    max_output_tokens=3000)

    by_id = {p.proposal_id: p for p in proposals}

    if not result or not isinstance(result, dict):
        # If the judge fails we submit NOTHING from this system. Falling back
        # to "pick the first one" would put an unjudged question into the
        # portfolio, which is worse than an empty day.
        log.warn(f"contest judge failed for {system_key}; submitting nothing")
        for p in proposals:
            p.outcome = "lost_in_system"
            p.outcome_reason = "contest judge unavailable"
        return None, False

    for elim in result.get("eliminated") or []:
        if not isinstance(elim, dict):
            continue
        pid = str(elim.get("proposal_id", "")).strip()
        if pid in by_id:
            by_id[pid].outcome = str(elim.get("outcome", "lost_in_system")).strip()
            by_id[pid].outcome_reason = str(elim.get("reason", "")).strip()

    winner_id = result.get("winner")
    winner_id = str(winner_id).strip() if winner_id else ""

    if not winner_id or winner_id.lower() in ("null", "none") or winner_id not in by_id:
        reason = str(result.get("winner_reasoning", "")).strip() or "no candidate cleared the absolute bar"
        for p in proposals:
            if not p.outcome:
                p.outcome = "lost_in_system"
                p.outcome_reason = reason
        log.info(f"  {system_key}: no winner -- {reason[:120]}")
        return None, False

    winner = by_id[winner_id]
    winner.outcome = "won"
    # Preserve any note about other agents converging on the same question --
    # it is weak evidence the story matters, and useful when reading the log.
    prior = winner.outcome_reason
    reasoning = str(result.get("winner_reasoning", "")).strip()
    winner.outcome_reason = f"{reasoning} ({prior})" if prior else reasoning
    exceptional = bool(result.get("exceptional", False))
    winner.flagged_exceptional = "yes" if exceptional else ""

    for p in proposals:
        if not p.outcome:
            p.outcome = "lost_in_system"
            p.outcome_reason = "not selected"

    log.info(f"  {system_key} winner: {winner.question[:90]}")
    if exceptional:
        log.info(f"  {system_key} winner FLAGGED EXCEPTIONAL")
    return winner, exceptional
