# data/runs/

`YYYY-MM-DD/QXXXX.json` -- the full reasoning behind one question's number on
one day. This is the file to open when you want to know **why**.

Each record holds, per lens: its own abstraction of the question, the
enumerated cases behind its base rate, the frozen outside-view number, the
strongest case for YES and for NO (built blind to each other), the
reconciliation, what it deliberately ignored, its declared triggers, and the
audit verdict. Plus, at the aggregate level: the median, the extremized shadow
number, and the devil's advocate's findings.

## When it becomes meaningful

**Immediately.** This is the primary artifact of the fast clock. A forecast you
cannot interrogate is not worth having.

## Two runs on the same day

The earlier record is preserved under `_earlier_runs` rather than being
overwritten.

## The shadow number

`median_extremized` is computed and stored but **never used as the live
number**. Extremizing corrects for a crowd being collectively underconfident
because each member holds a fragment; our lenses share one model and one set of
blind spots, so extremizing would amplify a shared bias with false confidence.
It sits here so the calibration table can settle the question in a couple of
years.
