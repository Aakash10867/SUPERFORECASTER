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

A human entry is **terminal for the absence watch** — the system stops looking
and never second-guesses you.

## lenses.yaml is under the change budget

Changing an aperture, a forbidden list, or a threshold is a **method change**.
Bump `version`, and log what problem it was meant to fix. Batch them monthly:
spreading 40 resolutions a year across twelve different systems means none of
them can ever be evaluated. Report formatting and new diagnostics are not
method changes and can change freely.
