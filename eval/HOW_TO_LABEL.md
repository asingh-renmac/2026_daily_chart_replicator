# Labelling `alias_worksheet.csv`

Open it in Excel. You are marking **one column: E (`pick`)**, plus column F on one
kind of row. Everything else is there to read.

## Columns

| col | name | yours? |
|---|---|---|
| A | `group` | no — joins the rows of one alias |
| B | `alias` | no — the shorthand being labelled |
| C | `means` | no — what the source CSV says it means |
| D | `row_type` | **no — this is a printed label, never type in it** |
| E | `pick` | **YES — put `x` here** |
| F | `ticker` | pre-filled on candidates; **you type it only on the NONE row** |
| G–K | descriptor, frequency, adjustment, database, sim | no — read these to decide |
| L | `note` | optional — why, in your words |

`x`, `X`, `1` and `y` all count as a tick. Blank means "not this one".

## One alias = one block of rows

Each block is some candidate rows, then a `NONE` row, then a `PARK` row. Mark the
block **one** of these four ways:

**1. One candidate is right** — `x` in E on that row. Done. Ticking it captures the
ticker, frequency, adjustment and database automatically; you type nothing else.

**2. Several candidates are equally right** — `x` in E on each. This is not hedging
and not a mistake. The catalogue really does hold `jcdrgi` and `jcdrgim` under one
identical descriptor, and `jcsxeh` under both `usecon` and `usna`. An eval set that
accepts only one of a mirror pair marks a correct answer wrong, so say so here.

**3. None of them is right, and you know the series** — `x` in E on the `NONE` row,
and type the code in **F**. Either `lict4@usecon` or `usecon:lict4` is fine. These
rows are the most valuable in the sheet: each is a retrieval miss and a candidate
entry for the alias table.

**4. The alias is genuinely ambiguous and the resolver SHOULD refuse** — `x` in E on
the `PARK` row, nothing in F. "Claims" without initial-or-continuing is the shape of
this. Refusing to guess is a right answer, and it has to be sayable: if the set never
rewards a park, anything tuned on it learns to guess — which is how `establishment
survey` ended up bound to a reference-week marker.

## Don't know? Leave the whole block blank

Unticked blocks are skipped and counted, never guessed at. A blank is honest; a
default would turn "nobody looked" into ground truth. You can stop anywhere and
compile what you have.

## When you're done (or part-done)

```bash
python scripts/compile_alias_evalset.py --reviewer aman
# -> eval/alias_eval_set.json
```

It prints how many you labelled, how many were parks, and how many are still
unreviewed. Re-run it as often as you like.
