"""
The lens engine: five stages, one number.

THE IDEA IN ONE PARAGRAPH
-------------------------
N copies of one model asked the same question give N correlated answers, not a
crowd. Averaging correlated errors does not cancel them -- it produces the same
bias with a tighter confidence interval, which is worse than useless because it
LOOKS like agreement. Stage one proved this: four differently-prompted agents
converged on the front page anyway. So real disagreement has to be manufactured
by restricting what each forecaster can SEE. Seven lenses, seven apertures.

THE FIVE STAGES
---------------
  1. ABSTRACT + OUTSIDE   blind to news, blind to the prior forecast
  2. INSIDE-YES           strongest case for YES, through this aperture only
  3. INSIDE-NO            strongest case for NO, cannot see the YES call
  4. RECONCILE            one number; NOW sees the prior forecast
  5. AUDIT                sees only the reasoning and the forbidden list

THE FIREWALL IS ONE-WAY
-----------------------
Once the outside number is produced it is FROZEN for that run. The inside phase
may disagree with it; it may never send work back. If it could, the outside
number would become news-contaminated and the whole point of having a base rate
untouched by today's headline would collapse.

WHY THE LENS WRITES ITS OWN ABSTRACTION
---------------------------------------
People cannot take the outside view because they cannot stop seeing their own
case as special. Show a lens "will the RBI finalise flexi-loan rules by
December 2026" and its population quietly narrows to Indian NBFC lending. Show
it "will a financial regulator finalise a proposed rule within roughly fifteen
months of comment close" and it builds a real population. A single shared
abstraction would be a single point of failure feeding correlated blindness
into all seven, so each lens abstracts through its own aperture. Divergent
abstractions are themselves diagnostic.
"""

from __future__ import annotations

import datetime as dt

from . import reference

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

OUTSIDE_PROMPT = """You are one of seven forecasters working on the same \
question. Each of you sees the question through a different narrow aperture. \
Yours is:

APERTURE: {aperture}

FORBIDDEN: {forbidden}

You have NOT been shown any news, and you must not speculate about recent \
events. This stage is deliberately blind to reporting.

THE QUESTION: {question}
RESOLUTION CRITERIA: {criteria}
DEADLINE: {deadline} (that is {days_left} days from today, {today})

STEP 1 -- ABSTRACT
Write the generalised form of this question as YOUR aperture would frame it. \
You MUST preserve the time window and the structural shape. You MAY drop \
proper nouns and particulars. Losing the time window makes the rest worthless.

STEP 2 -- BUILD THE POPULATION
{outside_shape}

PROVENANCE RULE -- this is strict. You may not state a frequency that arrives \
as a whole number with no structure under it. Either:
  (a) enumerate the cases -- name them, with dates and outcomes; or
  (b) enumerate a SKELETON and multiply. A class too big to list almost always \
decomposes into something small enough to list. "Hikes per FOMC meeting since \
1994" is ~256 meetings and unenumerable, but the hiking CYCLES since 1994 are \
five items you can name, times meetings per year, times hikes per cycle.
If you can do neither, give a directional argument with NO number, and say so.

If you cannot define a population whose membership rule clearly contains this \
case, try a SECOND, different decomposition before giving up. "No base rate \
available" usually means you reached for a LEVEL when you needed a FREQUENCY.

{reuse_block}

{frozen_block}{scale_rule}

VALID_UNTIL means: how long this BASE RATE remains a fair description of the
world, and nothing else. It has NOTHING to do with the deadline of the question
you are helping with -- a base rate does not expire because a question closes.
Ask instead: how quickly does this frequency go stale? "Hiking cycles since
1994" barely ages. "How often this administration finalises contested rules"
ages with the administration. Set the date accordingly and justify it.

Return JSON only:
{{
  "abstraction": "the generalised question in your aperture's terms",
  "population_needed": "one line naming the population you need",
  "frequency_question": "the exact frequency you are estimating",
  "membership_rule": "what counts as a member, and what is excluded",
  "window": "the period covered",
  "cases": [{{"name": "...", "date": "...", "outcome": "hit|miss"}}],
  "skeleton": "if you used skeleton x multiplier, describe it",
  "multiplier": "the arithmetic, written out",
  "count": 0,
  "hits": 0,
  "rate": 0.0,
  "coverage_note": "e.g. 12 named; believed roughly 15 in total",
  "known_gaps": "...",
  "valid_until": "YYYY-MM-DD -- see note below",
  "validity_reason": "why that long",
  "provenance_tier": "structured|reasoned",
  "outside_probability": 0,{frozen_field}
  "reasoning": "how you got from the population to the number",
  "abstain": false,
  "abstain_reason": ""
}}"""

FROZEN_BLOCK = """TWO NUMBERS, AND ONE OF THEM IS FIXED HERE FOR GOOD.

Your final answer is: (chance the substantive event happens) x (chance it \
satisfies the written criteria, given that it happens).

The FIRST term is not your job, and you settle it HERE, once, from the base \
rate alone. Give it as a plain number with a one-line reason and move on -- do \
not argue it, do not weigh evidence for or against the event occurring. It is \
then frozen: the later stages of your own work may use it but may never revise \
it.

The SECOND term is your actual work, and it belongs to the later stage.

Report the first term as "substantive_probability".

"""


REUSE_BLOCK = """YOU HAVE BUILT THIS BEFORE. A stored reference class of yours \
has been confirmed as matching this case:

  {frequency_question}
  Membership rule: {membership_rule}
  Window: {window}
  Result: {hits} of {count} ({rate})
  Cases: {cases}

You MUST use it rather than deriving a different number for the same fact -- \
otherwise the same underlying fact gets different numbers on different days, \
which is inconsistency masquerading as reasoning.

{extend_note}

If you judge the stored entry to be WRONG, use it anyway and say so in \
"dissent" -- your disagreement will be surfaced for human review rather than \
silently acted on."""

EXTEND_NOTE = """Its window ends before today, so you must EXTEND it: add any \
cases that have occurred since, and return the full updated case list. If you \
cannot name the new cases, set "extension_failed": true and the entry will be \
retired."""

INSIDE_PROMPT = """You are one of seven forecasters on this question, each \
with a different narrow aperture. Yours is:

APERTURE: {aperture}

FORBIDDEN: {forbidden}

THE QUESTION: {question}
RESOLUTION CRITERIA: {criteria}
DEADLINE: {deadline} ({days_left} days from today, {today})

YOUR TASK: build the STRONGEST HONEST CASE that this resolves {side}.

This is deliberately one-sided. Another process is building the opposite case \
and cannot see yours. You are not being asked what you believe -- you are \
being asked to make the best argument your aperture supports. Do not hedge, \
and do not argue the other side.

You may use the reporting below. You may NOT reason about anything on your \
forbidden list, even if it seems obviously relevant -- that ground belongs to \
another aperture, and covering it yourself destroys the independence that \
makes seven lenses worth more than one.

{inside_shape}

RECENT REPORTING:
{news}

Return JSON only:
{{
  "case": "the strongest argument for {side}, through your aperture",
  "key_evidence": ["specific, checkable facts you are relying on"],
  "strength": "strong|moderate|weak",
  "strength_reason": "...",
  "deliberately_ignored": "what you could see but left alone because it is \
outside your aperture"
}}"""

RECONCILE_PROMPT = """You are one of seven forecasters on this question. Your \
aperture:

APERTURE: {aperture}
FORBIDDEN: {forbidden}

THE QUESTION: {question}
RESOLUTION CRITERIA: {criteria}
DEADLINE: {deadline} ({days_left} days from today, {today})

YOUR OUTSIDE VIEW (built before you saw any news -- it is FROZEN, you may \
disagree with it but it cannot be changed):
  probability: {outside_probability}
  reasoning: {outside_reasoning}
{frozen_recall}

THE CASE FOR YES:
{yes_case}

THE CASE FOR NO:
{no_case}

{prior_block}

Produce ONE number for the WHOLE question -- not a component. You are not \
saying "35% chance of an injunction"; you are saying "this question resolves \
YES with probability X, and here is what my aperture sees".

{horizon_block}

{scale_rule}

TRIGGERS. Name one or two SPECIFIC OBSERVABLE events that would change your \
number, with the direction and rough size of the move.

EVERY TRIGGER MUST BE ABLE TO FIRE. Today is {today}; the deadline is \
{deadline}. A trigger dated in the past can never happen, so any date you \
name must fall between today and the deadline. Give the date explicitly. Not "if the political \
situation deteriorates" -- that is unfalsifiable. Something like "if the RBI \
publishes a draft circular before 30 September, this rises to roughly 45%".
At least one trigger MUST move your number TOWARD the less likely outcome. If \
you are at 10%, you must name something that would push it UP.

Probabilities below {floor} or above {ceiling} need an explicit argument for \
crossing that bound. Rare-but-real events happen more often than intuition \
suggests, and our ability to rule things out is weaker than it feels.

Return JSON only:
{{
  "probability": 0,
  {horizon_fields}
  "stronger_case": "yes|no",
  "stronger_case_reason": "...",
  "reasoning": "how you reconciled the outside view with the two cases",
  "deliberately_ignored": "what you left alone because it is outside your \
aperture",
  "triggers": [{{"event": "...", "by_date": "YYYY-MM-DD", \
"direction": "up|down", "to_roughly": 0}}],
  {move_fields}
  "crossed_bound_argument": ""
}}"""

FROZEN_RECALL_BLOCK = """
YOUR SUBSTANTIVE PROBABILITY, FIXED EARLIER AND NOT REVISABLE: {substantive}
  ({substantive_reason})

Take that number as given. Do not argue it up or down, and do not introduce \
evidence about whether the event will occur -- six other forecasters are doing \
that. Your number is that figure multiplied by your judgement of whether a \
real event would satisfy the written criteria. Show both terms.
"""


PRIOR_BLOCK = """YOUR PREVIOUS NUMBER for this question was {prior} on \
{prior_date}. You must account for any change. If you now say something \
different, name the specific cause. A number that moves with no named cause is \
noise, and will be flagged."""

AUDIT_PROMPT = """You are auditing one forecaster's written reasoning for \
discipline violations.

You have NOT been shown the question or the probability, and you must not ask \
for them. Seeing the number would let you rationalise the reasoning that \
produced it.

THE FORECASTER'S JOB -- this is what they are SUPPOSED to reason about, and \
doing it is never a violation:
{aperture}

They are also required to reconcile their own earlier base-rate estimate with \
the arguments in front of them. Referring to that base rate is part of the \
job, not a violation.

GROUND EVERY FORECASTER SHARES -- using any of this is never a violation:
{shared_ground}

THE FORECASTER'S FORBIDDEN LIST -- ground that belongs to a different \
forecaster:
{forbidden}

THEIR WRITTEN REASONING:
{reasoning}

Did they reason about anything on the FORBIDDEN list? Merely MENTIONING that \
something is out of scope is fine and expected. Using it as a basis for the \
estimate is a violation.

Be conservative. If the passage is plausibly part of the job described above, \
it is NOT a violation. Only flag reasoning that clearly rests on forbidden \
ground. Asking a forecaster to stop doing their own job is worse than missing \
a marginal leak, because it removes an entire aperture from the aggregate.

If there is a violation, name the SPECIFIC passage and which forbidden ground \
it strays onto. A vague verdict is useless -- they get one chance to fix it, \
and they can only fix what you name.

Return JSON only:
{{"violation": true/false, "passage": "...", "forbidden_ground": "...", \
"instruction": "what specifically to remove and re-derive"}}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _num(value, default=None):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f


def parse_pct(value):
    """
    Return a probability on a 0-100 scale, or None if the value is AMBIGUOUS.

    THE BUG THIS EXISTS FOR
    -----------------------
    On the first live run the lenses mixed scales inside a single question --
    and inside a single lens across stages. actor_incentive returned 85.0 for
    its outside view and 0.45 for its final answer. The median of
    [0.25, 0.45, 0.85, 35, 38] is 0.85, so the aggregate was meaningless while
    looking perfectly well-formed.

    The prompts now state the scale explicitly, but a prompt instruction is not
    a guarantee, so this is the enforcement. Anything between 0 and 1 with a
    fractional part is treated as ambiguous and REJECTED rather than guessed
    at: 0.45 could be "0.45%" or "45%", and silently choosing is how the first
    run produced numbers nobody could trust.

    We lose the ability to express probabilities below 1%. That costs nothing,
    because the soft floor is 2% and crossing it requires an explicit argument.
    """
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f < 0 or f > 100:
        return None
    if 0 < f < 1:
        return None            # ambiguous: fraction or sub-1% percentage?
    return f


SCALE_RULE = (
    "SCALE: every probability you give is a whole number from 0 to 100, where "
    "0 means impossible, 50 means an even chance and 100 means certain. Write "
    "35, not 0.35. A value between 0 and 1 will be rejected as ambiguous and "
    "your answer discarded."
)


def clamp_probability(p, floor=0.0, ceiling=100.0):
    if p is None:
        return None
    return max(floor, min(ceiling, float(p)))


def horizon_dates(created: dt.date, deadline: dt.date, today: dt.date):
    """
    Thirds of the REMAINING horizon, recomputed every run.

    This is why a weekly refresh is a real call and not a replay: at creation,
    thirds of a twelve-month horizon are 4/8/12 months out; two months later,
    thirds of the remaining ten are ~3.3/6.7/10. Different cut points, different
    sub-questions, a genuinely new decomposition of time.
    """
    remaining = (deadline - today).days
    if remaining <= 0:
        return [deadline, deadline, deadline]
    return [
        today + dt.timedelta(days=int(remaining / 3)),
        today + dt.timedelta(days=int(2 * remaining / 3)),
        deadline,
    ]


class LensRunner:
    """Runs one lens through its five stages for one question."""

    def __init__(self, router, log, settings, config_version, similarity=None,
                 shared_ground=""):
        self.shared_ground = shared_ground or ""
        self.router = router
        self.log = log
        self.settings = settings
        self.config_version = config_version
        self.similarity = similarity
        fc = settings.get("forecasting", {})
        self.floor = float(fc.get("soft_floor", 2))
        self.ceiling = float(fc.get("soft_ceiling", 98))
        self.max_decomp_retries = int(fc.get("decomposition_retries", 2))
        self.max_contam_retries = int(fc.get("contamination_retries", 2))
        # PREFLIGHT. On the first live run both configured grounding models
        # returned 404 -- they do not exist for these keys. Because they were
        # the only grounding models, every lens_outside call wasted its first
        # attempt discovering there was nothing to ground with, then succeeded
        # ungrounded on the retry: seven alarming warnings per question for no
        # actual loss. Decide once, at startup, rather than per call.
        wanted = bool(settings.get("reference", {}).get("verify_with_grounding", True))
        reachable = bool(getattr(router, "grounding_models", set()))
        self.grounding_enabled = wanted and reachable
        if wanted and not reachable:
            log.info(
                "  grounding requested but no grounding-capable model is "
                "configured; reference entries will be stored as 'unverified'"
            )

    # -- stage 1 ------------------------------------------------------------

    def outside(self, lens, question, today, prior_entry=None):
        """
        Build the base rate. Blind to news, blind to the prior forecast.

        Returns (payload, entry_used_id) or (None, None) on abstention.
        """
        deadline = question.get("deadline", "")
        days_left = _days_left(deadline, today)

        reuse_block = ""
        extend = False
        if prior_entry:
            entry = reference.load_entry(prior_entry.get("id", ""))
            if entry:
                extend = reference.is_stale(prior_entry, today)
                reuse_block = REUSE_BLOCK.format(
                    frequency_question=entry.get("frequency_question", ""),
                    membership_rule=entry.get("membership_rule", ""),
                    window=entry.get("window", ""),
                    hits=entry.get("hits", ""),
                    count=entry.get("count", ""),
                    rate=entry.get("rate", ""),
                    cases="; ".join(
                        f"{c.get('name','')} ({c.get('date','')}: "
                        f"{c.get('outcome','')})"
                        for c in (entry.get("cases") or [])[:30]
                    ),
                    extend_note=EXTEND_NOTE if extend else "",
                )

        frozen = bool(lens.get("substantive_estimate_is_frozen"))
        prompt = OUTSIDE_PROMPT.format(
            frozen_block=FROZEN_BLOCK if frozen else "",
            frozen_field=('\n  "substantive_probability": 0,'
                          '\n  "substantive_reason": "one line, no argument",'
                          if frozen else ""),
            aperture=lens.get("aperture", ""),
            forbidden=lens.get("forbidden", ""),
            question=question.get("question", ""),
            criteria=question.get("resolution_criteria", ""),
            deadline=deadline,
            days_left=days_left,
            today=today.isoformat(),
            outside_shape=lens.get("outside_shape", ""),
            reuse_block=reuse_block,
            scale_rule=SCALE_RULE,
        )

        # Grounding is used ONLY here, and only on the abstracted population
        # query -- never on the live question text, and never in the inside
        # phase. Searching the original question would walk straight through
        # the firewall and pull in this week's coverage, contaminating the very
        # stage we isolated.
        want_grounding = self.grounding_enabled and not reuse_block

        payload = None
        for attempt in range(self.max_decomp_retries + 1):
            task = "lens_outside"
            result, model = self.router.generate(
                task, prompt, temperature=0.3, max_output_tokens=4096,
                grounded=want_grounding and attempt == 0,
            )
            if isinstance(result, dict):
                payload = result
                payload["_model"] = model
                payload["_grounding"] = dict(
                    getattr(self.router, "last_grounding", {}) or {}
                )
                if payload.get("abstain"):
                    if attempt < self.max_decomp_retries:
                        prompt += (
                            "\n\nYour previous attempt abstained. Try a "
                            "DIFFERENT decomposition -- if you reached for a "
                            "level, reach for a frequency instead."
                        )
                        continue
                    return None, None
                break
            if attempt >= self.max_decomp_retries:
                return None, None
        if payload is None:
            return None, None
        return payload, (prior_entry or {}).get("id", "")

    # -- stages 2 and 3 -----------------------------------------------------

    def inside_case(self, lens, question, today, side, news):
        prompt = INSIDE_PROMPT.format(
            aperture=lens.get("aperture", ""),
            forbidden=lens.get("forbidden", ""),
            question=question.get("question", ""),
            criteria=question.get("resolution_criteria", ""),
            deadline=question.get("deadline", ""),
            days_left=_days_left(question.get("deadline", ""), today),
            today=today.isoformat(),
            side=side.upper(),
            inside_shape=lens.get("inside_shape", "") or "",
            news=news or "(no relevant reporting available)",
        )
        result, model = self.router.generate(
            "lens_inside", prompt, temperature=0.5, max_output_tokens=3072
        )
        if isinstance(result, dict):
            result["_model"] = model
            return result
        return None

    # -- stage 4 ------------------------------------------------------------

    def reconcile(self, lens, question, today, outside, yes_case, no_case,
                  prior_prob=None, prior_date=None):
        is_window = (question.get("shape") or "point") == "window"
        if is_window:
            cuts = horizon_dates(
                _parse_date(question.get("created"), today),
                _parse_date(question.get("deadline"), today),
                today,
            )
            horizon_block = (
                "THREE HORIZONS. Give the probability that the question "
                f"resolves YES by EACH of these dates:\n"
                f"  one third: {cuts[0].isoformat()}\n"
                f"  two thirds: {cuts[1].isoformat()}\n"
                f"  deadline: {cuts[2].isoformat()}\n"
                "They must be non-decreasing -- more time cannot reduce the "
                "chance of something happening by SOME deadline.\n"
                "Before giving the numbers, state WHAT CHANGES between these "
                "dates. If genuinely nothing changes with time, say so and give "
                "three equal numbers; that is a legitimate answer and will not "
                "be penalised."
            )
            horizon_fields = (
                '"what_changes_between_horizons": "...",\n'
                '  "p_one_third": 0,\n  "p_two_thirds": 0,\n  "p_full": 0,'
            )
        else:
            horizon_block = (
                "This question can only resolve at a single scheduled event, "
                "so give ONE number for the deadline. Do not invent "
                "intermediate probabilities."
            )
            horizon_fields = ""

        prior_block = ""
        move_fields = '"moved_from": "", "move_reason": "",'
        if prior_prob is not None:
            prior_block = PRIOR_BLOCK.format(
                prior=prior_prob, prior_date=prior_date or "the last run"
            )

        frozen_recall = ""
        if lens.get("substantive_estimate_is_frozen"):
            sub = parse_pct((outside or {}).get("substantive_probability"))
            if sub is not None:
                frozen_recall = FROZEN_RECALL_BLOCK.format(
                    substantive=sub,
                    substantive_reason=(outside.get("substantive_reason") or "")[:200],
                )

        prompt = RECONCILE_PROMPT.format(
            frozen_recall=frozen_recall,
            aperture=lens.get("aperture", ""),
            forbidden=lens.get("forbidden", ""),
            question=question.get("question", ""),
            criteria=question.get("resolution_criteria", ""),
            deadline=question.get("deadline", ""),
            days_left=_days_left(question.get("deadline", ""), today),
            today=today.isoformat(),
            outside_probability=outside.get("outside_probability", "n/a"),
            outside_reasoning=(outside.get("reasoning", "") or "")[:1500],
            yes_case=_case_text(yes_case),
            no_case=_case_text(no_case),
            prior_block=prior_block,
            horizon_block=horizon_block,
            horizon_fields=horizon_fields,
            move_fields=move_fields,
            floor=self.floor,
            ceiling=self.ceiling,
            scale_rule=SCALE_RULE,
        )
        for attempt in range(2):
            result, model = self.router.generate(
                "lens_reconcile", prompt, temperature=0.3, max_output_tokens=3072
            )
            if not isinstance(result, dict):
                continue
            if parse_pct(result.get("probability")) is not None:
                result["_model"] = model
                return result
            # Ambiguous scale. Say so specifically and try once more; a vague
            # "try again" invites reformulation rather than correction.
            self.log.warn(
                f"  {lens.get('id')}: returned probability "
                f"{result.get('probability')!r}, which is ambiguous on a 0-100 "
                "scale. Asking once more."
            )
            prompt += (
                f"\n\nYour previous answer gave probability "
                f"{result.get('probability')!r}. That is between 0 and 1 and "
                "therefore ambiguous -- it could mean a fraction or a "
                "sub-1% percentage. Answer again using a WHOLE NUMBER from 0 "
                "to 100. If you meant an even chance, write 50."
            )
        return None

    # -- stage 5 ------------------------------------------------------------

    def audit(self, lens, reasoning_text):
        """
        Blind contamination check.

        The auditor sees only the reasoning and the forbidden list -- not the
        question, not the number. An auditor that sees 12% will rationalise
        whatever reasoning produced it.
        """
        prompt = AUDIT_PROMPT.format(
            aperture=lens.get("aperture", ""),
            forbidden=lens.get("forbidden", ""),
            shared_ground=self.shared_ground,
            reasoning=(reasoning_text or "")[:6000],
        )
        result, _model = self.router.generate(
            "lens_audit", prompt, temperature=0.1, max_output_tokens=1024
        )
        if isinstance(result, dict):
            return result
        # An auditor that fails to answer must not be treated as a pass, but it
        # must also not block the run. Treat as clean and note it.
        return {"violation": False, "audit_unavailable": True}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _parse_date(value, fallback: dt.date) -> dt.date:
    try:
        return dt.date.fromisoformat((value or "").strip())
    except (ValueError, AttributeError):
        return fallback


def _days_left(deadline: str, today: dt.date) -> int:
    d = _parse_date(deadline, today)
    return (d - today).days


def _case_text(case) -> str:
    if not isinstance(case, dict):
        return "(this side produced no usable case)"
    bits = [case.get("case", "")]
    ev = case.get("key_evidence") or []
    if ev:
        bits.append("Evidence: " + "; ".join(str(e) for e in ev[:8]))
    bits.append(f"Self-assessed strength: {case.get('strength','unknown')}")
    return "\n".join(b for b in bits if b)
