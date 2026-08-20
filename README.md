# Agentic Superforecaster — Question Generator

Reads newspapers, generates forecasting questions, and records every decision
it makes along the way.

This is **stage one of four**. It only produces questions. Forecasting, scoring
and updating come later, once you have judged whether the questions are any
good.

---

## Setup

1. **Create the repository** and add your API key as a secret:
   Settings → Secrets and variables → Actions → New repository secret
   - Name: `SUPERFORECASTER_API`
   - Value: your Gemini API key

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

4. **Run it:** Actions tab → *Generate Questions* → Run workflow.

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

4. **Is `reasoning_value` concrete?**
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

Two gates, applied to every proposal individually. Fail either and it is out,
however interesting it is:

- **Resolvable** — a specific story will run, in a named paper, when this resolves
- **Not inherently random** — careful thought could actually beat a coin flip

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
