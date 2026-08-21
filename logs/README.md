# logs/

One markdown file per day, written for a human, uploaded as a workflow artifact.

**The log is now written even when the run crashes.** It is flushed to disk
after every line and finalised in a `finally` block, and caught exceptions are
recorded with full tracebacks. Previously a crash produced no log at all, which
was precisely the run you would want to send to someone.

Things worth searching for:

- `FLAGGED FOR YOUR ATTENTION` -- something needing your judgement
- `ERROR in` -- a stage that failed; the run continued past it
- `WARNING` -- degraded but working
- `LAPSED as NO` -- a question ended for want of news. If you know it actually
  happened, add a row to `config/resolutions.csv`.
- `Quota used today` -- per key and per day, the limit that actually binds
