# Agentic Superforecaster

Reads newspapers, generates forecasting questions, forecasts them through seven
independent lenses, resolves them, and scores itself — recording every decision
along the way.

**Stage one (question generation) is closed.** **Stage two (the forecasting
layer) is new** and is what most of this README now covers below the setup
section. See `FORECASTING_LAYER.md` for the design record: every decision, and
why it was made.

---

## Setup

1. **Create the repository** and add your API keys as secrets:
   Settings → Secrets and variables → Actions → New repository secret
   - Name: `SUPERFORECASTER_API` — your Gemini API key (required)
   - Name: `SUPERFORECASTER_API2` — a second key (optional)

   The second key only helps if it belongs to a **different Google Cloud
   project**. Free-tier limits are per project, so two keys in one project
   share a single pool and the second adds nothing.

2. **Check the model names.** The names for the newer Gemini models in
   `config/models.yaml` are a best guess. Run this once:

   ```
   python verify_models.py
   ```

   It prints the models your key can actually see and flags any in the config
   that do not exist. Correct them in `config/models.yaml`. A wrong name is not
   fatal — the system falls through to the next model in the chain — but you
   would be wasting the models you meant to use.

3. **Drop PDFs into `/inbox/`** and commit them.

4. **Run it:** Actions tab → *Superforecaster* → Run workflow.

   **On your first run after replacing the repository, set `stages` to
   `generation`.** That exercises the four stage-one fixes — persistent quota,
   crash-safe logging, the removed early return, and resolution — with none of
   the forecasting code in the path. If it looks clean, run `all` next time.

   When feeding older papers, set **as_of_date** to the paper's date. If you
   feed the 10 August paper without this, the agents will believe it is today
   and every deadline they calculate will be wrong.

Delete PDFs from `/inbox/` whenever you like. Files are tracked by a hash of
their contents, not their name, so a paper is never read twice even if you
re-upload it under a different name.

---

## What comes out

| File | What it holds |
|---|---|
| `data/questions.csv` | The live portfolio. One row per question. |
| `data/proposals.csv` | **Every proposal and its fate**, including everything rejected. |
| `data/forecasts.csv` | Empty for now — the forecasting layer will fill it. |
| `data/processed.csv` | Which papers have been read. |
| `data/waiting_list.csv` | Good questions with no room yet. |
| `data/pending_tags.csv` | New tags the system created, for your occasional glance. |
| `logs/YYYY-MM-DD.md` | The full reasoning, in prose. |

**`proposals.csv` is the one to read while testing.** `questions.csv` only
shows survivors, so it can never tell you that an agent has proposed forty
questions and won nothing. This file can — which is how you find agents worth
deleting.

---

## How to judge the test

The test is not "did the code run". It is you reading the questions and asking:

1. **Could I resolve this by reading my own papers on the deadline?**
   If not, the resolvability gate is too loose.

2. **Do I already know the answer?**
   If yes, it is not Goldilocks — it is a headline with a question mark.

3. **Is it a coin flip?**
   If no amount of thinking would help, the randomness gate is too loose.

4. **Is `significance` concrete?**
   It must name what changes in the world, not gesture at "sets a precedent" or
   "signals institutional tension". Abstractions here mean the gate was talked
   past rather than passed.

5. **Is `reasoning_value` concrete?**
   It should name what an analyst would examine, not claim that analysis would
   help. Vague answers here are the clearest early warning that question
   quality is drifting.

Expect **zero to four questions per day, often zero**. A system producing
fifteen on day one has lowered its standards to hit a number.

---

## Adding a question by hand

When the system flags something exceptional but the portfolio is full, the
question sits in `proposals.csv` and the flag appears at the top of the day's
log. To admit it, paste its id into `config/overrides.csv`:

```csv
proposal_id, note
P-2026-08-19-003, regime break: Hormuz closure
```

On the next run it is admitted directly, **bypassing every gate and cap**, and
marked `admitted_by: human`. The file is then cleared.

A mistyped id stops the run with an error rather than silently doing nothing.

Two things worth keeping in mind:

- **Keep it rare.** Its value comes entirely from being reserved for genuine
  regime breaks. Used routinely, the caps stop constraining anything.
- **It bypasses the tag cap.** The concentration report still prints, so you
  can see what you are doing.

The `admitted_by` column exists so that, months from now, you can ask whether
your overrides forecast better or worse than the system's own picks. If they do
worse, that is worth knowing. If they do better, the gates are too tight.

---

## How it works

```
PDFs in /inbox
      ↓
extract     pdftotext, multi-column aware
      ↓
filter      structural — drops ads, classifieds, notices, tables
      ↓        (conservative by design: when in doubt, let it through)
triage      cheap model — is this macro-relevant news?
      ↓
dedupe      the same wire story in three papers is ONE story
      ↓
4 systems   India macro · Global macro · US politics · Geopolitics
  ↓ ↓ ↓ ↓   each with agents hunting different QUESTION SHAPES
contest     within each system only — best, or nothing
      ↓
gate        duplicates · linked answers · tag caps · space
      ↓
questions.csv
```

### The four question shapes

Agents are defined by the **shape** of question they hunt, never by subject.
An agent told to "look for RBI stories" fires eight times a year. An agent told
to look for "a scheduled decision by a known body on a known date, where the
outcome is contested" fires most days — and no other agent can produce the same
question, because no other agent is hunting that shape.

| Shape | Hunts for |
|---|---|
| **Scheduled Decision** | Known body, known date, contested outcome |
| **Deadline Under Strain** | Something due, with visible doubt it holds |
| **Threshold Crossing** | Official statistical release crossing a line |
| **Announced Intention** | Someone said they would; will they? |

Threshold Crossing is deliberately restricted to **official statistical
releases only**. No market prices, no exchange rates, no index levels — those
look forecastable because there is endless commentary about them, and they are
the single biggest trap in the whole system.

### The gates and the ranking

Three gates, applied to every proposal individually. Fail any and it is out,
however interesting it is:

- **Resolvable** — a specific story will run, in a named paper, when this resolves
- **Not inherently random** — careful thought could actually beat a coin flip
- **It matters** — name at least two specific things, *outside the question's own
  subject*, that would be different depending on the answer

The third gate was added after the first live run produced a perfectly
forecastable question about whether a single building's construction would be
halted. It was uncertain, cleanly resolvable, and worthless — nothing outside
that building turned on the answer. A question can pass every other test and
still be pointless.

Then survivors are ranked on **one** thing:

> Is this genuinely uncertain, **and** would serious reasoning move a forecaster
> meaningfully away from a naive guess?

Deliberately **not** ranked on resolution speed or on information edge. Any
criterion the system optimises for gets gamed, and those two would push it
steadily toward the easy and the near.

### Tags and concentration

Every question carries one **primary** tag (the driver that would flip the
forecast), up to three **secondary**, and up to five **tertiary**.

Only the primary counts against the cap of **three open questions per tag**.
This exists because a portfolio can look diversified while nine of fifteen
questions ride on the same event — and then one piece of news moves them all at
once. The daily log prints exposure across *all* tag levels, so you can see
concentration the cap does not block.

Tags come from a controlled vocabulary in `config/lexicon.csv` (45 seeded).
When a new tag is proposed, the system finds the five nearest existing ones and
asks a model to **argue for a match**, not to judge whether they differ. A model
asked "are these different?" will usually agree they are. Biasing toward
merging matters because a wrongly merged tag is visible immediately, while a
wrongly split one — "Iran war" and "West Asia conflict" as separate tags —
hides for months and silently defeats the cap.

---

## Configuration

Everything adjustable lives in `/config/`, so you can change behaviour without
touching code.

- **`settings.yaml`** — caps, ceilings, thresholds, filter sensitivity
- **`agents.yaml`** — the systems and question shapes. Set `active: false` to
  retire an agent that never wins.
- **`models.yaml`** — fallback chains. Each task tries models in order until one
  works, so no task fails because a single model is rate-limited.
- **`lexicon.csv`** — the approved tag vocabulary

### Budget

A three-paper day costs roughly **90 model calls**: about 60 for triage, 25 for
agents, 5 for contests and gating.

Nearly all of it runs on the cheap high-volume models (500/day each). The
"deep" models — 20 requests per day each — are reserved for the contest, which
is the one genuine judgement in the pipeline. Your deep-model budget stays
almost entirely intact for the forecasting layer, where thinking actually pays.

---

## Testing offline

```
python test_offline.py
```

Runs the whole pipeline against real PDFs with a fake model — no key, no
quota. It proves the plumbing works. It tells you nothing about whether the
questions are good; only real models and your judgement can do that.

---

## Known limits

- **Model names for Gemini 3.x are guesses.** Run `verify_models.py` and correct
  `config/models.yaml`.
- **Scanned PDFs will not work.** Your three papers have proper text layers. A
  scanned paper would need OCR, which is not built.
- **Resolution is not automated.** Nothing yet reads later papers to close open
  questions — that belongs to the scoring layer.
- **The structural filter is tuned on three papers.** It is deliberately
  conservative, but check `logs/` for anything obviously wrong when you add a
  paper it has not seen.


---

# Stage two: the forecasting layer

## What happens on a run

```
0. Human overrides + config/resolutions.csv
1. Read papers, triage, deduplicate
2. RESOLUTION   merged screen, confirmation, lapse, absence watch
3. GENERATION   agents propose, contest, portfolio gate
4. FORECASTING  seven lenses, aggregate, devil's advocate
5. REPORTS      scoring recomputed from source, diagnostics
```

Resolution runs **before** generation because resolving frees a portfolio slot
the same run can fill. Forecasting runs **after** generation so a question
created today gets its first forecast today.

Every stage is isolated: a failure logs loudly, with a traceback, into the
markdown log, and the run continues.

## The seven lenses

N copies of one model asked the same question give N *correlated* answers, not
a crowd. Averaging correlated errors does not cancel them — it produces the
same bias with a tighter confidence interval, which is worse than useless
because it looks like agreement. Stage one proved this: four differently
prompted agents converged on the front page anyway.

So disagreement is manufactured by restricting what each forecaster can **see**.

| lens | aperture |
|---|---|
| `reference_class` | among cases of this kind, how often does it happen? |
| `analogy` | the 2–3 closest episodes, and how this one differs |
| `actor_incentive` | do the principals want it, and can they deliver? |
| `blocker` | what structural friction stands in the way? |
| `mechanism_calendar` | what steps are required, and does the clock allow them? |
| `telltale` | are the usual advance signals present — and does this ever happen cold? |
| `literalist` | could it happen in substance but miss the written criteria? |

All seven run on every question. There is **no selector**, because a selector
is a single model deciding relevance, which reintroduces exactly the correlated
blindness the lenses exist to escape. Instead each lens must state what
evidence it needs and may then **abstain**. Below three responding lenses the
question is flagged, not forecast — and the run continues.

Each lens definition lives in `config/lenses.yaml`, with its aperture, its
forbidden territory, and its abstention conditions.

## The five stages inside a lens

1. **Abstract + outside** — blind to news, blind to its own prior number
2. **Inside-YES** — strongest case for YES, through this aperture only
3. **Inside-NO** — strongest case for NO, cannot see the YES call
4. **Reconcile** — one number; the prior enters *here*
5. **Audit** — a different model sees only the reasoning and the forbidden list

**The firewall is one-way.** Once the outside number is produced it is frozen
for that run. The inside phase may disagree with it; it may never send work
back.

Two retries each, never more, and they do not compound. Retrying until a check
passes does not converge on correctness — it converges on *passing the check*.

## Scope sensitivity is built in, not measured

Window questions get **three probabilities** per lens: at one third, two thirds
and the full remaining horizon. A lens cannot produce three numbers without a
model of what changes between them. They must be non-decreasing.

Point-event questions — those that can only resolve at one scheduled moment —
**skip this entirely**. When a question is ambiguous, classify it `point`: the
failure modes are asymmetric.

Revision fires on **contradiction, never on flatness**. Reasoning that names a
time-dependent mechanism alongside flat numbers is a contradiction. Reasoning
that says nothing changes with time, alongside flat numbers, is correct.

## Updating

Newspapers select for **dramatic**, not for **diagnostic**, and the two are
close to uncorrelated. So each re-running lens asks in words: *would we be
seeing this story if the answer turned out NO?*

- **daily screen** (one cheap call per question) — records "no cause" **with a
  reason** either way, so "looked and found nothing" is distinguishable from
  "did not look"
- **full refresh** — on a fired trigger, a calendar hook, genuinely new
  material, or once a question is 7 days stale

Staleness is measured against the last refresh, not a calendar, because runs
happen when you upload papers. Between refreshes the number **persists flat**.

## Scoring

Nothing is ever stored as final. **Every score is recomputed from source on
every run**, because you can flip an outcome a month later.

- **day-weighted Brier**, always printed next to the *question count* — 100
  forecasts of 4 questions is 4 questions' worth of evidence
- **constant baseline ladder** — the system must beat the *best* rung, chosen
  with hindsight. A deliberately unfair bar.
- **ablation** — does the full pipeline beat its own outside-view-only number?
  If not, everything downstream of the base rate is decoration.

Expect the outcome figures to be uninformative for about two years. The **fast
clock** — abstention rates, redundancy, curve divergence, trigger
contradictions — is informative in weeks.

## Correcting an outcome

If a question lapsed NO and you know it actually happened, add a row to
`config/resolutions.csv`. Unlike `overrides.csv`, that file is **never
cleared**. Correct the `resolved_date` too, not just the outcome.

Questions that lapsed for want of news go on a 90-day **absence watch** — the
system keeps cheaply looking. Your correction stops the watch permanently.

## Budget

Roughly, per run: one screen call per open question, then five calls per lens
per refreshed question (35 per question). A dozen window questions on a weekly
rotation is around 60 calls a day plus screens — comfortable against ~2,000
flash-lite calls a day across two keys.

Deep models are capped at 20/day each. Generation's `contest` step holds a hard
reserve of 40 that forecasting may not touch, so a heavy forecasting day can
never silently degrade question quality.

## Known limits of stage two

- **Outcome accuracy will not be measurable for years.** That is arithmetic,
  not pessimism.
- **Resolution depends on your three papers.** Without grounding, a macro event
  the papers do not carry lapses as a false NO. The absence watch and
  `resolutions.csv` are the mitigations.
- **`shape` is not produced by stage one.** New questions default to `point`
  and are logged; set them to `window` by hand in `questions.csv`.
- **Extremizing is stored but never used.** It sits as a shadow number for the
  calibration table to settle later.
