# data/

Everything the system knows. Nothing here is hand-edited except by way of
`config/overrides.csv` and `config/resolutions.csv`.

| file | what it is | when to read it |
|---|---|---|
| `questions.csv` | the portfolio, one row per question | any time |
| `forecasts.csv` | append-only: every aggregate number ever produced | any time |
| `lens_outputs.csv` | append-only: what each lens said, numbers only | when a number looks wrong |
| `screens.csv` | append-only: the daily "does this bear on the question" decision, with a reason **either way** | when you want to know why nothing moved |
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

## Nothing here is a final score

Every score is recomputed from source on every run. If you flip an outcome in
`config/resolutions.csv` a month from now, the day-weighted Brier, the
calibration table, the baseline comparison and the `brier` column all change on
the next run. The `brier` column is a cached display value, not a record.
