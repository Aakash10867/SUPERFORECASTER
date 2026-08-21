# Forecasting Layer — Design Record

Stage two of the Agentic AI Superforecaster. Stage one (question generation) is closed;
see HANDOFF.md. This document records decisions as each section closes. Philosophy →
structure → build → test. No code until the philosophy is covered.

Status: PHILOSOPHY COMPLETE (§0–§5). STRUCTURE COMPLETE (S1–S4). LIVE RUN 1 DONE;
six defects found and fixed in v9. All 25 notes accounted for.

### Repo findings (v7, reviewed at close of §4)

- **`forecasts.csv` already exists** as a declared schema (question_id, date, model,
  probability, reason), append-only, nothing writes to it yet. The file is reserved; only
  the schema needs widening.
- **CSV cannot hold what §1–§3 specify.** `store._flatten()` deliberately collapses
  newlines because long prose is meant to live in the daily log. Fermi sub-questions,
  enumerated skeletons, per-lens reasoning, triggers and three-horizon numbers are
  structured, nested and long. Natural shape: one JSON file per question per day, with
  `forecasts.csv` keeping only the numbers for scoring. Structure-stage decision.
- **Shape field (window vs point event) does not exist** in QUESTION_FIELDS and must be
  added, with human override via `config/overrides.csv`.

---

## Standing constraints

- Free-tier Gemini; additional API keys can be added if call volume binds
- GitHub Actions; no paid data feeds
- Portfolio caps at ~15 open questions; 4 live at time of writing
- Every forecast written down before the outcome is known — this is what makes the
  "you cannot learn from a single outcome" problem tractable at all
- Explanations stay plain; Aakash is a layman on the technical side

### Operating model (drives several design decisions)

Aakash drops newspapers into the inbox folder and runs the GitHub action. Nothing else.
No manual file moves, no intermediate steps. Consequences:

1. Question generation and forecasting run in one sequenced workflow — no handoff between
   them
2. **Nothing may block.** A question with fewer than three responding lenses cannot halt
   the run: flag, skip, log. Same for lens timeouts and unparseable JSON, both of which
   stage one already hit
3. The §0 calibration table is a **report to be read, never a gate**. Same for the
   `reasoning_value` coverage check

---

## §0 — What counts as success (CLOSED)

### Instrument

Brier score: average squared error of the probability. 20% on a non-event = 0.04.
Always saying 50% = 0.25. Lower is better.

**A Brier score means nothing on its own.** Every evaluation is a comparison.

### Decisions

**Per-stage recording.** Every intermediate probability is stored, not just the final
number — outside view, Fermi sub-estimates, inside view, pre-advocate, post-advocate,
final. Rationale: comparing stages *on the same question* is a paired comparison, so
question difficulty cancels out. Paired comparisons extract far more signal from ~40
resolutions/year than any comparison against an external standard.

**Constant baseline.** A fixed ladder (5/10/20/30/40/50% applied to everything) scored
alongside. The system must beat the *best* rung, chosen with hindsight — a deliberately
unfair bar, because clearing an unfair bar means something. Pure arithmetic, no API cost.
Rationale: per-stage recording shows which stage is best but cannot reveal that the whole
pipeline underperforms inertia.

**Rejected:** external anchors (market prices, human baseline). Only 1 of 4 questions has
a market price; sporadic human participation is noise.

**No weights, ever.** Not deferred — rejected structurally. Weighting assumes parallel
voters. Our stages are sequential: the inside view already contains the outside view, the
advocate already contains both. Weighting would double-count the same evidence under
different names. If the calibration table ever shows a stage destroys value, the fix is to
repair or delete that stage, not turn a dial down.

**Calibration table** computed and displayed per stage and horizon. Displayed only — no
automatic actuator. Human-in-the-loop adjustment via the `config/overrides.csv` pattern
from stage one.

**Day-weighted Brier** over the whole forecast trail, not final-forecast-only. Rationale:
being right early is worth more than being right the day before; final-only rewards
hedging then jumping. Dual scoring rejected as needless complexity. **The underlying
question count must be printed beside the score** — a day-weighted Brier over 100
forecasts of 4 questions is 4 questions' worth of evidence, and this is easy to forget.

**Two clocks, both built with equal care:**
- *Slow clock* — outcome accuracy. Expected to produce no usable signal for ~2 years at
  30–50 resolutions/year. Build it, log it, don't wire anything to it yet.
- *Fast clock* — process accuracy, gradeable today without waiting for resolution:
  - Scope sensitivity: move the deadline, does the probability move correctly?
  - Falsifiability: did the number move when the evidence it named actually arrived?
  - Stability under noise: did it stay put on days when nothing relevant happened?
  - Coherence: nested questions ordered sensibly, complements summing to one.

**Sharpening is not assumed to be the success signature.** Movement between stages is
recorded in both directions. A good devil's advocate *should* pull an overconfident
estimate back toward the middle; treating monotonic sharpening as success would penalise
the stage doing its job.

### Structural note (carried to build)

"Resolution" now means two things in this project — the Brier component (reward for moving
away from the base rate) and closing a question against later papers. These need different
names in code.

---

## §1 — What a forecaster is, and how disagreement is manufactured (CLOSED)

### Core finding

**There is no crowd from prompt variation.** Tetlock's wisdom of crowds works because his
forecasters were different people with different information and different blind spots.
N Gemini calls share one training corpus, one set of priors, one set of blind spots.
Averaging correlated errors doesn't cancel them — it produces the same bias with a tighter
confidence interval, which is worse than useless because it *looks* like agreement.

Stage one is direct evidence: four differently-prompted agents converged on the front page.
Independence had to be forced by shuffling inputs; it never arose from the prompts.

**Diversity must therefore come from restricted apertures — different information and
different jobs — not from personalities.**

### The seven lenses

Each enters the question at a different point and can only see through its own opening.

1. **Reference class** — how often does this class of thing happen at all? Statistical
   breadth.
2. **Analogy** — depth on the 2–3 closest past episodes and how *this* one differs.
   Distinct from reference class; they routinely disagree.
3. **Actor incentive** — the principals who must act: wants, capability, track record.
   Preferences only.
4. **Blocker** — structural friction: courts, committees, procedural requirements, third
   parties. Explicitly *not* preferences.
5. **Mechanism and calendar** — what concrete steps are required, and does the remaining
   time physically allow them?
6. **Telltale** — has this ever happened without advance signals, and are those signals
   present now?
7. **Literalist** — the chance the thing happens in substance but fails the written
   resolution criteria, or the reverse. Decides a meaningful fraction of real tournament
   questions.

Worked examples confirming the lenses do genuinely different work: Q0002 (Fed hike) and
Q0004 (Trump–Kim). Note that the lenses give Q0004 four independent angles despite it
having been classified as an unreferenceable one-off.

### Decisions

**All seven run on every question. No selector.** A selector is a single model deciding
relevance, which reintroduces exactly the correlated-blindness problem the lenses exist to
escape — if the selector is blind to something, all seven lenses are blind to it and we
never find out.

**Abstention, not selection.** Each lens must first state what evidence it needs, then may
abstain if it cannot find it. Not simply asked "are you relevant?" — a model will always
answer yes. Abstention is a judgement made *with* the evidence rather than a guess before
it.

**Floor of three responding lenses.** Below that, the question is flagged, not forecast.

**Abstention pattern is data.** Five of seven abstaining says something real about the
question.

**Mutual exclusion** enforced by describing each lens's *forbidden territory* abstractly
("you do not estimate historical frequency; another process handles that") rather than
narrating what the other lenses do. Same fence, less frame leakage. Stage one proved the
exclusion is necessary; the abstraction is the refinement.

**Fermi-izing is not a stage — it is how a lens thinks.** Each lens decomposes the question
into sub-questions of its own characteristic shape (reference class → "how many cases, how
many succeeded"; mechanism → "step 1 × step 2 × step 3"). Same technique, seven different
decompositions. This is note [13]: detective work, not exhaustive study.

**Definitions and precision questioning** apply to the sub-questions each lens generates,
and to the resolution text — not to the top-level question, which is already fixed by
stage one.

**Devil's advocate is not a lens** — lenses build, the advocate attacks. Two levels:
- Cheap self-check inside each lens: state the strongest reason your own number is wrong
- Full adversarial pass on the aggregate: hunt for the single sub-question whose failure
  would break the estimate

**YES/NO stances run same-day on identical evidence.** Never rotated across days — if
Monday is YES-framed and Tuesday NO-framed, we cannot tell whether news moved the number
or the framing flipped it, which wrecks the §4 update signal. Also faithful to the human
practice: re-entering from the opposite direction is an error check on the same evidence,
not a change of mind. Applied selectively where lenses diverge sharply, to control cost.

**Median, not mean.** Robust to one lens producing a wild number; costs nothing.

**Extremizing: stored as a shadow number, never live.** The book: extremizing helped
ordinary crowds, barely helped superforecasters. It corrects for a crowd being collectively
underconfident because each member holds only a fragment. Our lenses share one model and
one set of blind spots, so the information diversity that justifies extremizing is largely
absent — extremizing a correlated crowd amplifies a shared bias with false confidence.
Store raw median live, extremized alongside as a shadow that never touches the forecast.
Calibration table settles it in ~2 years. Same discipline as §0: measure now, actuate later
if ever.

**Freedom within boundaries.** We fix the aperture and the output format. We do *not* write
the sub-questions — each lens invents its own decomposition. Same bounded autonomy as
stage one: fixed walls, free movement inside.

### Storage contract

**Everything used to reach the number gets stored, not just the number.** The output we
want is the probability; the reasoning artifacts are kept for reference and for the fast
clock. Per lens, per run: evidence sought, sub-questions generated, sub-estimates,
resulting probability or abstention, self-check output. Plus aggregate-level: median,
extremized shadow, pre- and post-advocate numbers, advocate findings, abstention pattern,
responding-lens count.

Open sub-item folded into §3: whether each lens must state **what would change its mind and
by how much**, in advance. This is what makes §4 updating checkable rather than post-hoc.

---

## §2 — The procedure (CLOSED)

Most of the original §2 was resolved inside §1: Fermi-izing became the lens engine. The
remainder is settled below.

### Outside-then-inside runs inside each lens, not across the pipeline

Note [11] survives, but locally. Each lens does its own outside view first, then its own
inside view.

**Consequence — there is no single base rate. There are seven reference classes**, each
natural to its own aperture:
- Blocker: what fraction of proposed federal rules get enjoined before finalisation?
- Telltale: how often do summits of this kind occur with no advance signals?
- Literalist: how often does a thing happen in substance but miss its written criteria?
- Mechanism: how long does this class of rulemaking take from comment close to final
  publication?

Two numbers recorded per lens (outside, inside), so §0 can see which move helped.

### Firewall: the outside-view phase runs blind to the news

Outside phase sees **only** the question text and resolution criteria. No source article,
no `reasoning_value`, no newspaper. Inside phase then sees the specific evidence.

Three justifications:
1. *Anchoring.* Seeing the vivid specific first bends the "base rate" to fit it — the
   best-documented failure in this literature.
2. *§0 ablation for free.* An outside number recorded before any news exposure, on every
   question, every lens.
3. *Source division (the strongest, given no grounding).* Model knowledge is stale but
   durable; newspapers are fresh but narrow. They are good at opposite things, so each is
   placed where it can be trusted: model knowledge for structurally stable frequencies,
   newspapers as the only live data in the system.

**Heuristic given to every lens: prefer structurally stable frequencies over current
levels.** Hikes per FOMC meeting since 1994 barely degrades with a training cutoff; current
NBFC penetration degrades fast. This makes the knowledge cutoff far less binding than it
first appears.

### `reasoning_value` is withheld as input, used as a coverage check

Not discarded. It is a written record of what a competent analyst thought mattered. After
a run, compare it against what the seven lenses independently surfaced:
- Considerations recovered by the lenses → evidence the lens set is complete
- Anything in `reasoning_value` that no lens ever raises → a blind spot in the lens design

Turns a contaminating input into a free test of the architecture.

### Provenance rule — no bare recalled aggregate, at any N

**Context: the free Gemini tier does not include Google Search grounding.** So a cited
source is exactly as easy to invent as the number itself; source-citation labels provide no
protection and were rejected. Enumeration was also rejected as a general rule — fabrication
does not begin at a clean threshold, and reference classes in the hundreds cannot be listed
at all.

The load-bearing rule is that **a number must not arrive whole with no structure under it.**

Ladder:
1. **Structured** — the estimate rests on an enumerated list, or an enumerated skeleton ×
   multiplier. The structure is stored. Only tier permitted to state a frequency.
2. **Reasoned** — a directional argument, no number claimed.
3. **Abstain.**

Enumeration and Fermi construction are not separate tiers — they are the same move at
different scales. Worked example: "hikes per FOMC meeting since 1994" is ~256 meetings and
unenumerable, but factors into *hiking cycles since 1994* — 1994–95, 1999–2000, 2004–06,
2015–18, 2022–23 — five items, enumerable and checkable, times meetings per year, times
hikes per cycle. A reference class too big to list almost always decomposes into a skeleton
small enough to list.

**What this buys, honestly:** not verification. A ten-item list can still be partly
invented. It buys three things — fabricating a coherent structure is harder than asserting
a summary; the structure is auditable by a human later, with no grounding required; and
thin knowledge becomes visible instead of hiding inside a confident number.

### Double decomposition

Where a lens claims a number that **materially drives its estimate**, it factors the same
quantity twice by two different decompositions, in separate calls that cannot see each
other. Agreement is weak evidence the structure is real; sharp divergence means the number
is being manufactured, and the lens drops to tier 2. Not applied universally — only to
load-bearing numbers.

This is note [2] — statistical disconfirmation rather than good intentions — applied to the
lens's own arithmetic.

### Second-decomposition retry before abstaining

If a lens cannot find its reference class, it must attempt a second decomposition before
abstaining. Rationale: "no base rate available" usually means the lens reached for a
*level* when it needed a *frequency*. Q0001 does not need "what percentage of Indians use
NBFCs" (a level, and stale); it needs "how often do RBI consultative proposals become final
circulars, and how fast" (a frequency, about regulator behaviour, bounded and durable).

### Asset noted for §4

The accumulating corpus of dated newspaper PDFs is a private, timestamped record. Useless
for base rates, but potentially valuable for checking whether a signal actually appeared
before an event — which is the telltale lens's entire business.

### If grounding ever becomes available

It slots in as a verification step on tier 1 without disturbing anything else.

---

## §3 — What the number means (CLOSED)

### Governing principle

**These notes are specifications, not metrics.** Where a property matters, it is built into
the structure of the work, not instructed and then measured.

**Instruction is the weak form and does not work here.** Stage one proved it: four agents
told to look at different things converged on the front page anyway; restricting what each
could *see* is what worked. Calibration properties are especially vulnerable — tell a model
"be aware of scope sensitivity" and it produces text acknowledging scope sensitivity, then
returns the same number for both deadlines. Tell it "be granular" and it returns 63% with
no more information behind it than 60% had. The instruction is satisfied in form and
ignored in substance.

### 3.1 Granularity — not instructed at all

Tetlock's finding is real (1% reporters beat 5%/10% rounders; artificially rounding their
forecasts degraded accuracy) but **does not transfer**. A human saying 63% has overcome the
pull toward 60%, and that effort is itself evidence of a reason. A model has no such pull —
63% is exactly as cheap as 60%. The digit that was diagnostic in humans is free here.

**Resolution: granularity is not a property to request. It is what structured estimation
produces automatically.** A number's digits come from the arithmetic of its Fermi
decomposition (§2 tier 1) — five hiking cycles × meetings per year × hikes per cycle yields
a specific number whose precision is inherited from the structure underneath. Instructing
granularity on top would manufacture digits the structure does not support.

**Retained as background sampling only:** occasionally rerun a lens on identical inputs
across a handful of questions. Not part of the per-question flow. Purpose is narrow and
structure cannot supply it — distinguishing *this question is genuinely uncertain* from
*this lens is unstable*. These look identical in a single number and matter differently.

### 3.2 Scope sensitivity — built in structurally

Two question shapes, requiring different treatment. **This needs a shape field that stage
one does not currently produce**, with human override via `config/overrides.csv`.

**Window questions** (Q0001, Q0003, Q0004 — the thing may happen any time before the
deadline, so probability accumulates):

Each lens returns **three probabilities** — at one-third, two-thirds, and full horizon.
Only the last is the live forecast; the other two are free by-products of the same call.
Cut points scale with the existing `bucket` field (short/medium/long).

This forces time reasoning structurally rather than requesting it — a lens cannot produce
three numbers without a model of what changes between them. Side benefits: the three
numbers must be non-decreasing, giving a free coherence check; and a lens that says 15% by
October has made a claim checkable *in October*, long before resolution.

**Additional requirement:** the lens must state *what changes between horizons* before
giving numbers ("comment period closes in September, drafting typically takes four months,
so the rule cannot plausibly be final by October").

**Point-event questions** (Q0002 — can only resolve at a single scheduled event): **skipped
entirely.** No three-horizon output, no substitute mechanism. A nesting workaround was
considered and rejected as conjuring machinery not relevant to the question.

**Classification and its failure modes.** Near-mechanical: does the resolution criteria hang
on a single scheduled event, or on anything happening within a span? **When ambiguous,
default to point event** — misclassifying a window as a point merely loses a test, while
misclassifying a point as a window generates numbers corresponding to nothing and then
feeds them to a contradiction check.

### 3.3 Revision fires on contradiction, never on flatness

A revision loop that says "your numbers aren't different enough, revise" gets compliance —
the lens spreads the numbers to satisfy the critic whether or not spread is warranted. That
manufactures the *appearance* of scope sensitivity, which is worse than not having it,
because the failure becomes hidden instead of visible.

**Correct trigger: internal inconsistency.** If the stated reasoning names a time-dependent
mechanism but the numbers are flat, challenge. If the reasoning says nothing changes with
time, flat numbers are correct and accepted. Flat-but-right must not be punished.

### 3.4 Fat tails — reduced, not built

Fat tails are a property of distributions over *magnitudes*. Our questions are binary;
there is no tail on a yes/no question. No mechanism is built for this note, rather than
inventing one to honour it. The underlying insight transfers in exactly two places:

1. **Soft floor and ceiling at ~2% and ~98%.** Rare-but-real events happen more often than
   normal-distribution intuition suggests, and our ability to rule things out is weaker than
   it feels. A lens may cross the bound only by explicitly arguing past it. A speed bump,
   not a hard clamp.
2. **It is the telltale lens's justification.** That lens exists because our reasoning is
   worst precisely on rare-but-real events.

### 3.5 Trigger conditions — the strictest requirement in the design

Every responding lens, alongside its number, must produce one or two **trigger
conditions**: a specific observable event, plus the direction and rough size of the move it
would cause. Not "if the political situation deteriorates" — unfalsifiable. Rather: "if the
RBI publishes a draft circular before 30 September, this rises to roughly 45%."

Four things this buys:
1. **Makes updating checkable.** Without pre-declared triggers, §4 can only judge updates
   after the fact — exactly the bait-and-switch note [7] warns about.
2. **Forces a causal model.** You cannot name what would change your mind without a theory
   of what drives the outcome. A lens with only vibes produces vague triggers, and vagueness
   is visible.
3. **Concrete form of note [2]** — statistical disconfirmation rather than good intentions.
4. **Honest answer to the poker problem.** "Was the judgement reasonable on what was known
   then?" is hard to answer retrospectively because memory rewrites. A pre-committed trigger
   is a written record that cannot be rewritten.

**Gaming guard:** at least one trigger must move the number *toward* the less likely
outcome. A lens at 10% must name something that would push it up, not only things
confirming the low number.

## §4 — Updating (CLOSED)

### The core insight

Bayes: new belief = old belief × how **diagnostic** the news was. Diagnostic = how much
more likely this news is in a world where the thing happens than in one where it doesn't.

**The Hagel case is the whole lesson.** Base rate 96%. He botched the confirmation hearing
— dramatic, front-page, obviously bad. A commentator dropped to 50%. Ulfelder asked
instead: how often do *doomed* nominees botch hearings versus *safe* ones? Doomed nominees
almost always stumble, but safe ones stumble reasonably often too. Real news, weakly
diagnostic. 96% → 83%. Hagel was confirmed two weeks later.

**The general failure: newspapers select for dramatic, not diagnostic.** Vividness and
diagnosticity are close to uncorrelated, and newspapers are our only live input.

**Build the question, not the formula.** Tetlock: superforecasters know the theorem and
almost never compute it. Each re-running lens asks, in words:

> Would we be seeing this story if the answer turned out NO?

**Explicitly refused: computing a likelihood ratio and multiplying.** That would invent two
numbers (probability of this news under YES, under NO) with no data behind either, and
their ratio would carry false authority *because* it looks like arithmetic. Forbidden by
the §2 provenance rule. Ask in words, let the lens move its estimate, record both the
answer and the move.

**Diagnosticity is asked per lens, not at aggregate level.** A court filing is highly
diagnostic to blocker and irrelevant to reference class; one aggregate answer flattens
that. It attaches to news-driven updates only — on a scheduled refresh with no news, there
is nothing to assess the diagnosticity of.

### Two-level cadence

**Daily screen** (cheap, flash-lite or embeddings, every question every run): does today's
material bear on this question? Output is either escalation, or "no cause" **with a
one-line reason — recorded either way**. This is a considered no-move, not a blind skip:
the trail must distinguish *looked and found nothing* from *didn't look*. Same shape as
stage one's page filter — cheap filter first, expensive model on survivors.

**Escalation triggers:**
1. A **declared trigger** fires (from §3.5 — this is what triggers are for)
2. A **calendar hook** arrives (RBI comment period, Forest Service comment close, FOMC
   meeting, APEC — written down at question creation)
3. **Genuinely new material**, not a restatement of a story already logged

**Partial re-run on trigger.** A fired trigger belongs to a specific lens. A court
injunction is blocker business and has nothing to say to reference class. Re-run the
affected lens plus the aggregate; leave the rest at stored values. 2–3 calls, not 7.

### Weekly full refresh on window questions

**Staleness-driven, not calendar-driven.** On every run, refresh any window question whose
last full refresh is ≥7 days old. Rationale: runs happen when Aakash uploads papers, so a
fixed "Sundays" schedule breaks whenever a day is skipped. Staleness rotation is
self-correcting — regular running spreads load naturally, a three-day gap makes several
come due at once, and the daily ceiling absorbs it.

Monthly was considered and rejected as too slow for 3–12 month horizons.

**Why a real API call, not a cheaper substitute — the decisive argument.** The three-horizon
cut points *move*. At creation, thirds of a twelve-month horizon are 4/8/12 months out. Two
months later, thirds of the remaining ten are ~3.3/6.7/10 — different cut points, different
sub-questions, a genuinely new decomposition. The weekly call is not regeneration of the
same reasoning; it is reasoning about a different partition of time.

### Time itself is evidence — the under-adjustment trap

A pure "don't move without cause" design is **wrong for window questions**. If the Forest
Service hasn't published by June 2027 with an August deadline, the probability must be
lower than in January. Nothing happened — that is precisely the point. Nothing happening is
evidence against something happening. Holding the number flat while the window closes is
under-adjustment in its cleanest form (note [17]).

Point-event questions have no such decay — Q0002 doesn't decay, it waits for December. They
stay flat by default, correctly.

### The curve is a test instrument, never a value source

§3's three-horizon output is a declared decay curve: a lens saying 30% / 22% / 15% has
stated how its estimate should evolve if nothing happens.

**Rejected: letting the number follow that curve between refreshes.** Following a
once-generated number blindly is wrong, and the recomputing-thirds argument above removes
the need.

**Retained as a test:** last week the lens declared where the number should sit today;
today's refresh says where it actually sits. Systematic divergence means the lens's time
model is broken. Fast-clock signal in weeks, not years.

Between refreshes the live number **persists flat** — no interpolation, no invented values.
This still supplies §0's day-weighted Brier with a value every day.

### On refresh: anchor and account, never average

**Rejected: weighted average of new and prior forecasts.** Two reasons.
1. Same objection as §0's no-weights rule, applied in time. This week's forecast is not
   independent of last week's — same background, same history, same question. Averaging
   counts the same evidence twice under two dates.
2. Averaging damps movement *mechanically*, under-adjusting by construction whether the
   move was noise or real signal. The damping cannot be tuned without already knowing
   which it was.

**What is built instead:** on refresh the lens sees its own prior number and must account
for any change — "I said 22%, I now say 15%, because the comment period closed with no
draft circular." The prior anchors, so noise doesn't wander. Movement requires a stated
reason, so over-adjustment is visible. **This is the actual Bayesian update** — start from
the prior, move in proportion to the evidence. Mechanical averaging is a crude imitation
that throws away the reason.

Free check: a number that moved without a named cause is a flag.

**Firewall consistency (§2).** The outside-view phase runs blind to news *and* blind to its
own prior — it is re-deriving a reference class and must not be anchored to its last
answer. The prior enters at the **inside-view** stage, where anchoring is correct rather
than contaminating.

### Under-adjustment guard

The design leans hard against movement, so it needs one counterweight. When a **declared
trigger** fires, the lens has pre-committed to a direction and rough size. If it then
declines to move, flag it — the lens is contradicting its own written prediction.

### Budget

Free-tier limits are **per day, per key**, and reset daily. Rough load: ~12 window
questions × 7 lenses weekly ≈ 84 calls, plus ~15 cheap daily screens, plus occasional
trigger-driven updates at 2–3 calls each. Comfortably inside the flash-lite ceiling
(~500 rpd per model).

**Contention flag for the structure stage:** deep models are capped at 20 rpd *each*, ~100
total per day. Stage one's `contest` chain already reaches for them first — the most
important judgement in generation. Forecasting wants deep models for the aggregate advocate
pass. In one workflow, one can starve the other on a heavy day. Either forecasting yields
to generation, or the advocate runs on flash-lite.

`max_calls_per_run: 400` in settings.yaml is a per-run valve sitting under a per-day
ceiling and will need revisiting.

## §5 — Scoring and learning (CLOSED)

Most scoring machinery was settled in §0. This section is about the self-teaching loop.

### Two loops, differing by two orders of magnitude

**Outcome loop** — forecast, wait, resolve, learn. ~40 resolutions/year. Note [9] says
regression to the mean is the test for luck; at n=40 that test cannot be run. This loop
teaches nothing for years. Arithmetic, not pessimism.

**Process loop** — everything produced that doesn't require a question to resolve: ~84 lens
outputs/week, ~105 daily screens, 84 curve-vs-refresh comparisons, every abstention, every
double-decomposition result, every trigger fired or not. Roughly **5,000 data points/year**.

**The self-teaching loop lives on the process loop.** This is also the honest answer to the
poker problem [7]: the book says stop asking "was I right?" and ask "was the judgement
reasonable on what was known then?" The process loop *is* that question asked at scale —
not a consolation prize for lacking outcomes, but the thing Tetlock says to ask anyway.

### The governing boundary: diagnosis is not validation

Process metrics can show a lens is **broken**. They cannot show a change **fixed** it.

That a lens abstains 90% of the time is a genuine finding, no outcomes needed. Whether
narrowing its aperture *improves forecasts* is a claim about accuracy, and only outcomes
settle that.

**The loop diagnoses autonomously. It does not validate autonomously, and it does not
self-modify.** (Aakash: "I'm just being idealistic" — accepted as fair.)

### What is diagnosable immediately, with real statistical power

- **Redundant lenses** — blocker and actor-incentive producing near-identical numbers across
  30 questions means they aren't perpendicular and one is decorative. Pure correlation.
- **Mis-scoped lenses** — abstention rates. Never abstaining = not exercising judgement;
  always abstaining = aimed at nothing.
- **Broken time models** — curve-vs-refresh divergence, 84 comparisons/week.
- **Unfalsifiable triggers** — a lens whose triggers never fire is writing triggers designed
  not to fire. Note [2] failing, visible in weeks.
- **Self-contradiction** — trigger fired, lens declined to move.
- **Coverage gaps** — anything in `reasoning_value` no lens ever raises = missing aperture.
- **Fabrication rate** — double-decomposition divergence, per lens.

Calibration charts [5] and Brier resolution [6] are computed per lens as designed, but
belong to the slow loop and read as "not yet informative" for a long time.

### Two traps guarded against

**Goodhart.** Flag "this lens abstains too much" and the obvious fix is a lens that abstains
less — emitting noise numbers to satisfy the metric. Every process metric is gameable by a
system optimising it, and the only real check is outcomes, which take years. Process metrics
therefore drive **diagnosis surfaced to the human, never automatic correction**.

**Version drift.** Self-modifying twelve times a year spreads 40 resolutions across twelve
different systems. **The faster it teaches itself, the less anyone can verify it learned
anything.**

### Change budget: monthly, method changes only

Every forecast stamped with a config version. **Method changes** — lens definitions,
prompts, apertures, thresholds — batch monthly, each logged with the problem it was meant
to fix. **Non-method changes** — report formatting, storage layout, new diagnostics — are
free to change any time, since they don't alter what the system does.

Quarterly was proposed and rejected. Rationale for monthly: the budget's real job is
protecting *process* cohorts (~5,000/year, so ~400 per version — plenty), not outcome
cohorts, which are unprotectable at 40/year whatever we choose.

### The reference-class library — where real autonomy belongs

Every enumerated skeleton a lens builds (five hiking cycles since 1994; RBI consultative
proposals and how many became final circulars; announced summits and how many convened) is
**stored as a reusable object** with its date, structure, and source lens.

The library grows daily and gets used: next time a question touches RBI rulemaking, the
reference-class lens starts from its own audited prior work rather than re-deriving from a
stale training corpus. Hand corrections persist forever.

Why this is the safe form of self-improvement:
- **No Goodhart surface** — the library either contains a usable structure or it doesn't
- **Doesn't fragment outcome cohorts** — the system's *method* is unchanged
- **Directly attacks the §2 no-grounding problem** — accumulates a private, human-auditable
  base-rate store independent of model memory

**This is where the system gets the most freedom, and self-modification the least.**

**Entries never expire.** They are date-stamped and **superseded, not replaced** — a 2026
entry counting cycles since 1994 stays permanently valid as a record; what ages is its
coverage. A 2028 extension links back to the original, preserving the full chain so the
evolution of a base rate is visible over time. A lens reusing an entry must check whether
the window needs extending before relying on it.

### Proposal channel

Same pattern as stage one's `pending_tags.csv`: the system writes diagnosed problems with
evidence into a proposals file; the human approves or rejects with a line. Fits the
operating model, since the log is already being read after each run.

### Folder and documentation requirement (carried to structure stage)

Clear folders, as many as needed. **A README in every folder** — no ceiling on how many.
Each README must state not only *what lives here* but **when it becomes meaningful**:
- Calibration table: shows noise until roughly 100 resolutions; don't act on it before then
- Curve-divergence report: informative within about six weeks
- etc.

This makes the folder structure itself carry the two-clock discipline, so neither party has
to remember it a year from now.

---

---

# STRUCTURE STAGE

## S1 — Workflow (CLOSED)

### Repo bugs found in v7, to fix before forecasting goes live

**1. Rate limiting counts per run, not per day.** `CallStats` is created fresh in
`ModelRouter.__init__`, so `calls_by_model` starts at zero every run, yet it is checked
against `rpd` — a *daily* limit. Run the action twice in one day and the router believes it
has full quota both times while Google's counter keeps climbing. Survivable today because
generation is light and runs once; forecasting roughly triples load and runs manually
whenever papers arrive. **Fix:** persist per-model counts to a dated file in `data/`, load
at startup.

**2. A crashed run writes no log at all.** `log.save()` is called only from `_finish()` at
the end of a successful run. An exception anywhere earlier propagates to `run.py`, which
prints a traceback to stdout — the markdown file is never created. The workflow's artifact
upload is `if: always()` and dutifully uploads a `logs/` folder with nothing new in it.
This is precisely the case where the log needs sharing. **Fix:** try/finally so the log
always saves, plus a `log.error()` capturing exception and traceback into the markdown.

**3. Nothing resolves questions.** `questions.csv` has `status`, `resolved_date`,
`outcome`, `brier`; `store.py`'s docstring says the file is updated in place on resolution;
`settings.yaml` has `resolution_grace_days: 7`. No code does any of it. Every scoring
mechanism in §0 and §5 depends on outcomes that currently never arrive.

**4. The early return kills forecasting.** `pipeline.run` bails at `if not articles`.
Forecasting's weekly refresh is staleness-driven and needs no news; deadline lapses need no
news. Resolution and forecasting must sit outside that early return.

### Stage order

```
0. Human overrides            (existing)
1. Read papers, triage, dedupe (existing)
2. RESOLUTION CHECK            (new)
3. Generation                  (existing)
4. FORECASTING                 (new)
5. Reports and diagnostics     (new)
```

**Resolution before generation** — resolving frees a portfolio slot, so the same run can
fill it. Reversed, every resolution costs a day of portfolio capacity.

**Forecasting after generation** — questions created today get their first forecast today,
giving a complete trail from birth for §0's day-weighted Brier. Not contamination: §2's
firewall means the outside view runs blind to news anyway, and the inside view *should* see
the originating story.

**Failure isolation.** Each stage wrapped so a failure logs loudly and the run continues —
§3's "nothing may block" applied at stage level. Dry-run must extend to both new stages.

### Quota reservation

Deep models: ~100 calls/day across five. Generation currently spends 10–20 (contest per
active system, plus gate). Forecasting wants ~15 (one advocate pass per question). Both fit
today, but the reservation exists so **generation never degrades silently** — a bad
question is unfixable, a slightly worse forecast is refreshed within a week.

Hard reserve of ~40 deep calls for generation; forecasting draws only from the remainder
and falls back to flash-lite with a log note when its share is exhausted.

### Weekday-only papers

Aakash uploads on weekdays only — weekend editions lack depth. Consequences:

- **The screen must reason over everything since the last run, not "today".** A Monday run
  covers Friday–Monday. Framed as "today's news", weekend events fall in a gap and Monday's
  reporting of them looks like stale restatement. Gap-aware is correct regardless, since
  runs are irregular.
- `resolved_date` records when criteria were actually met, not the run date that noticed.
- Forecasts persist flat across weekends (§4).

### The merged screen

**The daily screen (§4) and the resolution check are the same call.** Both read the day's
material against a question; one asks "did this resolve it?", the other "does this bear on
it?" Running separately doubles cost and creates contradictions where the screen finds
nothing relevant while resolution finds a resolution. Merged: one flash-lite call per
question per run, returning resolution nomination plus escalation flag. Checking everything
daily costs ~15 calls, not 30.

### Resolution rules

**Early resolution requires a definitive reported act** matching the criteria — in either
direction. Early NO is real: Q0001 resolves NO if the RBI issues a final order *allowing*
flexi loans; Q0003 resolves NO if the rule is withdrawn or permanently enjoined.

**Everything else lapses at deadline + grace, as NO.**

**Ambiguity about criteria (not occurrence) defaults to NO** and is handled by the criteria
themselves — a handshake at a multilateral summit is a clear NO under Q0004's wording, not
an ambiguous case. Aakash's position, accepted: because questions clear stage one's macro
gate, "it happened but nobody reported it" is not a realistic failure. Residual real cases
are (a) criteria clauses early reporting can't establish — e.g. whether an injunction is
*permanent* — and (b) negative-defining acts that simply never occur, which correctly lapse.

**Confirmation bar for early resolution** (fires only on nominations, a handful of days a
year):
- **Two independent confirmation calls**, different models, both must agree. Disagreement =
  not resolved, flagged to the human.
- Each must **name the specific reported event** — what, when, which article — not merely
  assert criteria are met.
- Each must **walk every clause** of `resolution_criteria` and mark met/not met. Any clause
  not clearly met → not resolved.
- Both run **blind to the current forecast** (a confirmer seeing "system says 8%" will
  resist calling YES). Same firewall logic as §2.

### Lapse notice: at the moment, never before

**Rejected: warning some days before a lapse.** A pre-warning sends the human looking, and
what they find enters the record *before* the question is settled — making the human an
input to a still-live forecast. Notice at the moment of lapse keeps the human strictly
downstream. Cost is that corrections become retrospective, which is what `resolutions.csv`
is for.

### Pending nominations

When a question is nominated for resolution but awaiting human confirmation: **keep the
cheap daily screen running, stop the expensive lens refresh, tag those days "pending".**
The screen costs one call and can withdraw the nomination if reporting clarifies. The tag
matters because if the event is confirmed for Monday, the trail is scored to Monday and
pending days are dropped rather than counted as forecasts of a settled question.

### `config/resolutions.csv` — permanent, never cleared

Different semantics from `config/overrides.csv`, which is consumed and cleared after each
run (correct there: admitting a question is a one-time act). A resolution correction is a
**permanent statement about what happened**. If cleared, the next recompute would silently
revert it. Read fresh every run, never cleared.

Operations: flip an outcome; resolve a question the system hasn't; mark void; correct
`resolved_date`; reopen something resolved in error. Unknown question id exits loudly.

**`resolved_date` matters as much as the outcome.** The day-weighted trail is scored to
resolution. If the RBI acted 3 October, the papers missed it, and the question lapsed 31
December, then 89 days of forecasts were scored against an already-decided question.
Correcting the date is what makes the trail honest.

### Nothing scored is ever stored as final

Because an outcome can flip a month later, **all scores recompute from source on every
run** — outcomes plus forecast trail in, scores out. Nothing incremental. The `brier`
column becomes a cached display value rewritten each run, not a record. Cheap (arithmetic
over a few thousand rows) and makes corrections propagate automatically.

### `outcome_set_by` provenance

Field recording `system` | `human`, with the calibration report showing both figures.
Rationale: corrections are naturally sought where the system's NO felt wrong, and not
hunted for where a lucky NO happened to be right. That is bias in *which* corrections get
made, not in any single one, and it quietly flatters the scores. Provenance makes it
visible.

### The absence watch

**`resolution_basis` field:** `confirmed_act` (definitive reported event met criteria) or
`lapsed_absence` (deadline passed, nothing reported). **Only `lapsed_absence` goes on
watch.**

The watch reuses the merged screen — one flash-lite call per watched question per run, no
lens calls, since the question is no longer forecast.

**Three ways a watch ends:**
1. System finds a definitive act and flips it — same two-model, clause-by-clause bar as
   early resolution, no lower
2. Human flips it in `resolutions.csv` — stops the watch immediately and permanently
3. Expiry — **default 90 days, configurable.** Without expiry, two years in, eighty dead
   questions get screened every run and the cost creeps. Papers report late, rarely months
   late. Each expiry announced in the log.

**A late flip rewrites more than the outcome** — `resolved_date` moves too, and the trail
rescores. Propagates automatically via recompute-from-source.

**No gaming surface:** the watch can only find evidence the event *happened*; it cannot
manufacture a NO. Same confirmation bar, confirmers blind to the forecast.

**Human flip is terminal for the watch** — the system stops looking and never second-
guesses. Since `resolutions.csv` is never cleared and is re-read every run, the human can
revise their own entry later. Nothing else can.

### Log requirements

Verbose by default — skimmable, but unwritten information is unrecoverable. Must carry:
every stage entered and exited; quota state per model at each stage boundary; every
question's screen decision with its one-line reason; per-lens numbers and abstentions with
reasons; every boundary condition (fewer than three responding lenses, trigger
contradictions, curve divergence, resolution nominations); every exception with traceback.

---

## S2 — Storage (CLOSED)

```
data/
  questions.csv        + shape, calendar_hooks, resolution_basis,
                         outcome_set_by, watch_until, last_refresh,
                         probability, prob_source
  forecasts.csv        numbers-only scoring spine, widened
  lens_outputs.csv     per-lens numbers, one row per lens per run
  screens.csv          the daily decision WITH a reason either way
  diagnostics.csv      fast-clock signals
  system_proposals.csv the §5 self-diagnosis channel
  quota.json           per-key, per-day API counts
  runs/YYYY-MM-DD/QXXXX.json   the full reasoning
  reference/           the library, one JSON per entry + index.csv
  reports/             latest.json + a dated copy
config/
  lenses.yaml          the seven apertures — under the change budget
  resolutions.csv      permanent, NEVER cleared
```

**The CSV/JSON split.** `store._flatten()` collapses newlines deliberately, so
structured reasoning cannot live in a CSV cell. `forecasts.csv` keeps numbers;
`runs/` keeps thought.

**Migration.** `store._migrate()` adds the new columns to a stage-one
`questions.csv` in place, so no file has to be rebuilt by hand.

**READMEs in every folder**, each stating not only what lives there but **when
it becomes meaningful** — so the two-clock discipline is carried by the folder
structure rather than by memory.

## S3 — The lenses (CLOSED)

`config/lenses.yaml`, version 1. Seven lenses, five stages, one-way firewall,
two bounded retries per loop, blind audit. See the file itself — it is written
to be read.

**Two flagged asymmetries, recorded rather than hidden:**

1. **`reference_class` has no inside view and takes no stances.** There is no
   YES-flavoured way to count how many of 24 nominees were confirmed. This is
   what makes it the §0 ablation baseline. Its triggers are time-based, which
   is the decay curve.
2. **`literalist` cannot answer the whole question through its aperture**,
   because wording slippage is conditional on the event happening. It uses a
   two-term decomposition — a coarse, deliberately unelaborated estimate of the
   substantive event, times the conditional probability the criteria are
   satisfied — and is forbidden from arguing the first term.

**Blurriest boundary: telltale vs mechanism.** Split by reasoning direction —
mechanism reasons *forward from requirements*, telltale reasons *backward from
observation*. If the split does not hold, §5's redundancy diagnostic will catch
it within a couple of months.

**Most likely to leak: blocker.** It must hold out both preferences and the
calendar.

## S4 — Settings and build (CLOSED)

Added to `settings.yaml`: `staleness_days: 7`, `min_responding_lenses: 3`,
`soft_floor: 2`, `soft_ceiling: 98`, `decomposition_retries: 2`,
`contamination_retries: 2`, `watch_expiry_days: 90`,
`reference.max_validity_months: 12`, `reference.verify_with_grounding: true`,
`run.deep_reserve_for_generation: 40`, and `max_calls_per_run` raised 400 → 1200.

**Build decision: one zip, with a stage switch.** `run.py --stages` and a
workflow dropdown, so the first run after replacing the repo can be
`generation` only — exercising all four stage-one fixes with none of the
forecasting code in the path.

## BUILD NOTES (v8)

### Bugs fixed, each verified

1. **Per-run quota counter** → `src/quota.py`. Verified: 18 calls recorded, a
   fresh Quota in the same day still sees 18 (old code: 0). Keys independent.
   Corrupt file survives.
2. **Crashed run wrote no log** → flush-per-write plus `finally`. Verified with
   a simulated mid-run exception: file exists, traceback inside it, run
   continues.
3. **Early return on empty inbox** → removed; verified by an empty-inbox test run.
4. **Nothing resolved questions** → `src/resolve.py`.

### Two bugs found during the build

- **Point-event questions were storing horizon numbers.** The model volunteers
  them if the JSON shape invites it. Now discarded at the boundary, because
  meaningless numbers would have fed the coherence and curve-divergence checks.
- **Duplicate `chains:` key in models.yaml.** YAML keeps only the last
  duplicate, which would have deleted every stage-one chain and failed the
  first live run with a confusing error. Rewritten as one merged file, plus a
  validator.

### Test coverage

`test_offline.py` is now self-contained — it generates its own newspaper PDFs
and seeds one window and one point question, so it runs anywhere. 29 checks.
Plus 14 adversarial checks: total API failure, malformed model output,
out-of-range probabilities, past deadlines, corrupt dates, outlier robustness.
Plus an end-to-end scoring test proving a corrected outcome propagates and a
corrected date shortens the trail.

### Open for the live run

- Whether grounding actually fires (`verify_models.py` now tests it directly
  rather than assuming)
- Whether the two keys have genuinely separate quota pools
- Whether telltale and mechanism stay perpendicular
- Whether blocker's forbidden list holds

---

## LIVE RUN 1 — 21 August 2026 (v8) — findings

Ran end to end, committed, uploaded. Both keys drained evenly (97/96), quota
persisted, the deep reserve held (222 left), Gate E rejected the White House
ballroom question again, the reference library populated with 12 entries.

Six defects. Two were design errors, not accidents.

### 1. Probability scale — fatal (fixed)

Lenses mixed 0–1 and 0–100 **within one question, and within one lens across
stages**: `actor_incentive` returned 85.0 for its outside view and 0.45 for its
answer. Median of `[0.25, 0.45, 0.85, 35, 38]` = 0.85. Every aggregate was
meaningless while looking well-formed.

Cause: the prompts showed `"probability": 0` and never stated the scale.

Fix: `SCALE_RULE` stated in every prompt, plus `lenses.parse_pct()` — anything
in (0,1) is REJECTED as ambiguous rather than guessed at, with one specific
re-ask. Losing sub-1% precision costs nothing since the soft floor is 2%.

### 2. The auditor excluded lenses for doing their job — DESIGN ERROR (fixed)

5 of 14 lens runs excluded. The auditor told `literalist` to remove its
conditional-probability adjustment (its entire function), told `telltale` not to
analyse precursors (its entire function), and told `analogy` to stop referencing
the outside view (which reconcile is required to do).

Cause: the auditor was blind to the number — correct — **and blind to the
aperture** — wrong. It knew what the lens must not do and had no idea what it
was FOR, so anything substantive looked like a violation.

Fix: the auditor now sees the aperture alongside the forbidden list, is told
that aperture work and base-rate reconciliation are never violations, and is
instructed to be conservative.

### 3. Triggers dated in the past (fixed)

"before the end of 2025", "by 31 March 2026" — on a run dated 21 August 2026.
Unfireable triggers are the unfalsifiable-trigger failure mode from §5,
arriving on day one.

Fix: triggers carry an explicit `by_date`; the prompt states today and the
deadline; `_usable_triggers()` drops past-dated ones and flags the drop. A lens
left with no usable trigger is flagged as having declared that nothing would
change its mind.

### 4. A reference entry overstated its provenance (fixed)

`R0001` listed 8 cases (7 hits), then claimed `count: 16, hits: 11` via
"8 jurisdictions × 2 cycles" — and was still tagged `structured`. The
arithmetic was internally consistent, so nothing caught it. `R0004` was clean
by contrast.

Fix: `reference.audit_provenance()` classifies every entry as `enumerated`,
`extrapolated`, `unsupported` or `reasoned`. Where cases are complete, count,
hits and rate are RECOMPUTED from the cases rather than trusting the summary.
The claimed figures are kept alongside. `unsupported` is flagged loudly.
Verified against the real R0001: correctly reads `extrapolated`, and
`unsupported` once the skeleton is stripped.

### 5. `valid_until` set to the question's deadline (fixed)

All twelve entries. R0001's stated reason: "Matches the final resolution date
of the forecasting question." A base rate's shelf life has nothing to do with
when a question closes; the 12-month cap accepted it because it was shorter.

Fix: the prompt now defines the field explicitly and contrasts a durable
frequency with one that ages with an administration.

### 6. Both questions classified `point`; both were windows (fixed)

So the three-horizon mechanism — how scope sensitivity is BUILT IN rather than
measured — never ran at all.

Fix: `forecast.classify_shape()`, one cheap call per question, stored. Decision
(Aakash): the system classifies itself rather than the human setting it. An
unconfident `window` still falls back to `point`, preserving the asymmetry.

### Also fixed

- **Grounding preflight.** Both configured grounding models returned HTTP 404 —
  they do not exist for these keys, so the grounding question remains UNTESTED.
  Because they were the only grounding models, every `lens_outside` call wasted
  its first attempt. Now decided once at startup. Dead models removed from
  `models.yaml`: `gemini-2.5-flash`, `gemini-2.5-flash-lite`,
  `gemini-embedding-002`, `gemma-4-26b-it`.
- **Failed and excluded lenses were invisible in the log** — only "responded"
  and "abstained" were printed, so three vanished lenses left no trace. Every
  terminal state is now named.
- **Advocate truncation** — budget raised 2048 → 4096.
- **`reference_class` audit result** now recorded as `n/a` rather than blank; it
  has no inside phase to audit.

### Decision: fall back, do not drop (Aakash)

An audit exclusion no longer removes the lens from the median. It falls back to
its **frozen outside-view number**, which was built before any news was seen
and before the contaminated reasoning existed. Recorded as `fallback_outside`,
a distinct status, so the calibration table can separate fallbacks from full
answers. Rationale: losing an entire aperture is worse than mixing in a
news-free number — on run 1 it cost three of seven lenses on one question.

The same fallback applies when a probability is unusable after the re-ask.

---

## Closed in stage one — not reopened

Explicit deadlines in question wording [3]; question framing; gates A–E; tagging;
portfolio caps.
