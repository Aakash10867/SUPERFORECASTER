# data/

Everything the system knows. Nothing here is hand-edited except by way of
`config/overrides.csv` and `config/resolutions.csv`.

| file | what it is | when to read it |
|---|---|---|
| `questions.csv` | the portfolio, one row per question | any time |
| `forecasts.csv` | append-only: every aggregate number ever produced | any time |
| `lens_outputs.csv` | append-only: what each lens said, numbers only | when a number looks wrong |
| `screens.csv` | append-only: the daily "does this bear on the question" decision, with a reason **either way** | when you want to know why nothing moved |
| `pending_resolutions.csv` | **questions the web check says HAPPENED but could not resolve on its own**, each with a ready-made row to paste into `config/resolutions.csv`. Rebuilt every run; never edit it | **after every run** -- it is the one file here that asks you for a decision |
| `web_checks.csv` | append-only: every web check, whatever the answer -- the reader's verdict, event date, the sites Google actually searched, and which code check decided the action | when a web YES looks wrong, or to see why something was not resolved |
| `coverage.csv` | one row per day for the last 30: which papers arrived, which **regular** papers did not, and any file no pattern could name. Rebuilt every run from `processed.csv` + `config/papers.csv` | when the log flags a gap, or to see what the system has actually been reading |
| `proposals.csv` | every proposed question and its fate, including rejections | when judging whether an agent is dead weight |
| `diagnostics.csv` | fast-clock signals: coherence breaks, trigger contradictions, curve divergence | weekly |
| `system_proposals.csv` | problems the system diagnosed about itself | monthly, at the change budget |
| `processed.csv` | paper fingerprints, so the same PDF is never read twice | rarely |
| `waiting_list.csv` | questions parked for later | rarely |
| `pending_tags.csv` | tags proposed but not yet in the lexicon | occasionally |
| `quota.json` | per-key, per-day API usage | when a run says it ran out |
| `runs/` | the full reasoning, one JSON per question per day | when you want to know **why** |
| `reference/` | the reference-class library | see its own README |
| `reports/` | scoring and diagnostics | see its own README |

## The CSV/JSON split

CSVs collapse newlines on purpose, so they stay readable in a terminal and in
Excel. Structured reasoning -- enumerated cases, both sides of an argument,
declared triggers -- does not survive that, so it lives in `runs/` instead.
`forecasts.csv` is the numbers-only scoring spine; `runs/` is the record of
thought.

## Web resolution (v16)

`web_checks.csv` is meaningful from the first run after v16: every open
question gets one Google-Search check a day. The `action` column is the
headline -- `resolved_yes`, `pending` (needs you), `not_yet`, or `failed`
(no search model answered; the question is retried next run). The `why`
column names the rule that decided it.

`pending_resolutions.csv` is usually empty. A row there means the reader
found the event but a mechanical check failed -- only one non-official
source, no date, or an event dated before the question was created (a
born-resolved question, which is a question defect worth noting). Paste the
row into `config/resolutions.csv` if you agree; it disappears on the next run.
If you disagree, write `Q00XX,reopen,,<why>` instead -- any entry for a
question in `resolutions.csv` stops the web check from touching it again.

## Paper coverage (v17)

A paper is **regular** if it arrived on at least half the days you uploaded
anything in the last 30 days (both numbers in `settings.yaml` → `coverage`).
Only missing regulars are flagged, so a one-off like New Scientist never
nags. The log flags each gap **once**, on the first run after it: a date
with no papers at all, or a date where regulars are missing.

`processed.csv` still has a `paper_guess` column. Rows written before v17 hold
the old page-content guess, which was often wrong; coverage ignores that
column and re-derives every name from the filename.

## Nothing here is a final score

Every score is recomputed from source on every run. If you flip an outcome in
`config/resolutions.csv` a month from now, the day-weighted Brier, the
calibration table, the baseline comparison and the `brier` column all change on
the next run. The `brier` column is a cached display value, not a record.
