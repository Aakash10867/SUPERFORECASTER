# config/

Everything you can change without touching code.

| file | hand-edited? | cleared after a run? |
|---|---|---|
| `settings.yaml` | yes | n/a |
| `models.yaml` | yes | n/a |
| `agents.yaml` | yes | n/a |
| `lenses.yaml` | yes -- **this is a method change** | n/a |
| `lexicon.csv` | rarely | no |
| `overrides.csv` | yes | **YES** -- consumed each run |
| `resolutions.csv` | yes | **NEVER** |
| `papers.csv` | yes, when a file is flagged as Unknown | no |

## The two override files do different jobs

`overrides.csv` admits a question the gate rejected. That is a **one-time act**,
so the file is consumed and cleared.

`resolutions.csv` states **what actually happened**. That is permanent. If it
were cleared, the next run would recompute scores from the system's own
outcomes and silently revert your correction. It is read fresh every run and
never emptied — so you can also revise your own earlier entry by editing it.

### Using resolutions.csv

| `outcome` | effect |
|---|---|
| `1` / `0` | set or flip the outcome |
| `void` | the question was ill-posed; excluded from all scoring |
| `reopen` | it was resolved in error; put it back to open |

`resolved_date` corrects **when** it resolved, which matters as much as the
outcome: the trail is scored up to resolution, so a question that really
resolved in October but lapsed in December was scored for 89 days against a
question that was already decided.

A human entry is **terminal for the absence watch and for the web check** —
the system stops looking and never second-guesses you. That is what makes
`reopen` safe after a wrong web YES: without it, the next day's check would
resolve the question again.

`data/pending_resolutions.csv` hands you ready-made rows for this file whenever
the web check finds an event it was not allowed to resolve on its own.

## papers.csv (v17)

Filename pattern → paper name. The city or edition is ignored, so Mint from
Mumbai and Mint from Bengaluru are one paper. Patterns are regular
expressions matched against the filename with every separator turned into a
space, so `\bbs\b` matches `BS ● Mumbai`, `BS_Delhi` and `bs-delhi`, but never
the "bs" inside "jobs". First match wins.

When a run flags a file as Unknown, add one line here. The whole history
re-labels on the next run. Currently unrecognised: `TT ● Delhi`, `THS- Delhi`,
`th21` -- add them once you know which papers they are.

## Questions: `resolves_on` (v17)

Every question says what resolves it: `announced`, `carried_out` or
`in_effect`. New questions set it at birth. Questions from before v17 are
classified once by a cheap call; if the criteria genuinely do not say, the
question is set to `ambiguous` and flagged. Fix it by editing the
`resolves_on` cell in `data/questions.csv` -- until you do, the web check
treats it as `carried_out`, the strict reading.

## settings.yaml: web_resolution (v16)

| key | meaning |
|---|---|
| `enabled` | master switch; off means papers-only resolution, exactly as before v16 |
| `max_checks_per_run` | cap on search calls in one run (the daily allowance is ~80 across both keys) |
| `min_independent_sources` | different sites needed for an automatic YES (default 2) |
| `official_suffixes` / `official_domains` | a single site matching these is enough on its own |

The search models are set in `models.yaml` under `grounding_models` and the
`web_resolve` chain. Only models listed in **both** are ever used for it.

## lenses.yaml is under the change budget

Changing an aperture, a forbidden list, or a threshold is a **method change**.
Bump `version`, and log what problem it was meant to fix. Batch them monthly:
spreading 40 resolutions a year across twelve different systems means none of
them can ever be evaluated. Report formatting and new diagnostics are not
method changes and can change freely.
