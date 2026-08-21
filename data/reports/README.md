# data/reports/

`latest.json` plus a dated copy per run. Recomputed from source every time.

## READ THIS BEFORE ACTING ON ANYTHING HERE

There are two clocks and they run at very different speeds.

### The slow clock -- outcome accuracy

`scoring`, `calibration`, `stage_comparison`.

At 30-50 resolutions a year, **this will not be informative for roughly two
years.** Regression to the mean is the test for luck, and that test cannot be
run at n=40. Read these figures; do not act on them.

- **calibration**: not meaningful below ~100 resolutions
- **day_weighted_brier**: always read the `questions_scored` figure next to it.
  100 forecasts of 4 questions is 4 questions' worth of evidence.
- **baseline**: the system must beat the *best* constant rung, chosen with
  hindsight. That bar is deliberately unfair. Clearing an unfair bar means
  something.
- **stage_comparison**: the ablation. If the full pipeline does not beat its
  own outside-view-only number, everything downstream of the base rate is
  decoration.

### The fast clock -- process accuracy

`abstention_rates`, `redundant_lens_pairs`, plus `diagnostics.csv` and
`system_proposals.csv`.

**Informative within weeks**, because it needs no resolutions. Roughly 5,000
data points a year against the slow clock's 40.

- **abstention rates**: meaningful after ~20 runs per lens. A lens that never
  abstains may not be exercising judgement; one that always abstains is aimed
  at nothing.
- **redundant lens pairs**: needs ~10 paired forecasts. Two lenses agreeing
  closely are not perpendicular.
- **curve divergence**: informative within about six weeks.

## Nothing here changes behaviour on its own

These metrics can show a lens is **broken**. They cannot show that a change
**fixed** it -- only outcomes can, and outcomes are years away. So every
diagnosis is written to `system_proposals.csv` for you to accept or reject.
Every process metric is gameable by a system optimising it.
