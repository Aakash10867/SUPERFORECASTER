# data/reference/ -- the reference-class library

One JSON file per entry, plus `index.csv` for cheap scanning.

**This is the one place the system genuinely improves itself.** Every time a
lens builds an enumerated skeleton -- five hiking cycles since 1994; how many
consultative proposals became final circulars -- it is stored and reused. Over
time this becomes a private, auditable store of base rates that does not depend
on the model remembering them, which matters because most models here have no
search grounding.

## When it becomes meaningful

**Immediately, and increasingly.** Unlike the calibration table, this needs no
resolutions to be useful. After a few weeks you should be able to open entries
and judge them yourself. That is the point: they are written to be checked.

## The unit is a population, not a topic

"RBI rulemaking" is a topic and would match everything. An entry is one
answerable frequency question with an explicit membership rule. Two questions
can both be "about the RBI" and need different populations.

## States -- nothing is ever deleted

| state | meaning |
|---|---|
| `active` | matchable and usable |
| `superseded` | a newer entry extends it; kept, chain visible via `supersedes` |
| `retired` | never offered again; kept, still readable |

Entries never expire in the sense that the **record** is permanent. What can
end is **usability**. An entry past its `valid_until` must be extended before
reuse; if the extension fails, it is retired and the lens derives fresh.

## Entries are owned by the lens that built them

There is no cross-lens sharing. It would be more efficient, but it is also a
route by which the seven apertures converge on the same material, which is
exactly what the lens design exists to prevent. The duplication buys a
diagnostic: two lenses independently building the same population and getting
different rates is a free signal.

## If you correct an entry by hand

Look at `used_by`. It lists every question that leaned on it -- the blast
radius. Reuse creates correlation across time, so a wrong entry makes many
forecasts wrong together.
