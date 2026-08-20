---
name: haver-chart-rebuild
description: Rebuild a Haver chart in RenMac house style. Use when the user pastes a Haver chart screenshot and wants it redrawn, restyled, or reproduced with different series, transforms, titles, or axes. Requires the haver-chart MCP.
---

# Rebuilding a Haver chart

You are reproducing a chart the user pasted, using their firm's production chart
pipeline through two tools: `resolve_series` and `render_chart`. The pipeline carries
years of accumulated correctness rules. Your job is to read the screenshot accurately
and drive the tools — not to compute anything yourself.

## The one rule that matters most

**Never guess a ticker.** Every series goes through `resolve_series`, always, even when
you are confident you know the mnemonic. The resolver runs the same catalog search,
exact-token-set gate and DLX confirmation the production daily lane runs. A ticker you
supply from memory bypasses all of it.

## Sequence

1. **Read the screenshot.** For each plotted line, note the series name exactly as
   printed, any formula printed on the chart, the transform, SA/NSA, frequency, the
   axis it sits on, any lag tag, the sample start, the axis min/max, and whether there
   is recession shading. Note the **plot kind**: bars, stacked bars, or lines. Nothing
   downstream can infer this from the data, so a bar chart you do not report comes back
   as lines.

2. **Resolve each series** with `resolve_series`.
   - If the chart prints a formula (`zs(yryr%(IP))`), pass it **verbatim** in `formula`.
     Do not paraphrase it into words — the parser reads Haver's own syntax, and
     paraphrasing loses the nesting.
   - Only use `applied_transform` for a words-only chart ("% Change - Year to Year",
     "3-Month Moving Average", "Z-Score").
   - Haver's aggregation/units line — "Avg, % p.a.", "Sum, Mil.$", "EOP, Index" — is
     **not** a transform. It describes how the series is stored. Passing it as a
     transform plots a level as a percent change; that is a real bug that shipped.
   - Pass `sa_hint` when the chart says SA or NSA. It is a hard cross-check.
   - Lags use Haver bracket syntax: `[-4]` lags four observations, `[+4]` leads four.
   - Pass `plot_kind` — `"bar"`, `"stacked_bar"` or `"line"` — from what you saw in
     step 1. It travels in the returned `slot`, so setting it once is enough.

3. **Handle parks by asking.** `status: "parked"` is a normal outcome, not a failure.
   Show the user the top-3 `candidates` with their `similarity` and
   `exact_token_match`, and ask which is right. **Never bind the top hit yourself.**
   Similarity alone is not evidence: `DFBACTS` and `DFBACTDS` scored 0.909 against each
   other and one of them was the wrong directional sibling.

4. **Render** with `render_chart`, passing the `slot` blocks `resolve_series` returned.
   Give each series a `proposed_legend`: a short human-readable label built from the
   real descriptor, keeping qualifiers that change meaning (SA/NSA, units, base year).
   Draft the title from the user's commentary if they gave you any.

5. **Show the image and ask.** The render is the approval gate — the whole reason this
   lane exists is that the user sees the pixels before the chart is used. Then check the
   reconstruction against the last value the source chart shows. If they disagree,
   something is bound or transformed wrong; say so rather than explaining it away.

## When a tool raises

The errors are guardrails, not obstacles. Do not work around them.

| Error | What it means |
|---|---|
| `unmappable applied_transform '…'` | The vocabulary rejected your phrase. The error lists the wordings it does accept — restate it in one of those, or pass the printed formula instead. |
| `unrecognized plot_kind '…'` | Only `line`, `bar` and `stacked_bar` exist. It raises instead of falling back to a line, so that a dropped bar chart cannot pass unnoticed. |
| `render needs every slot resolved` | A slot parked. Return to step 3. |
| duplicate / raw-mnemonic legend | Supply a distinct human-readable `proposed_legend`. Never let a bare ticker be a label. |
| `KeyError: 'D'` | Daily frequency is not supported. Ask whether the weekly twin will do. |
| `NeedPin` | An INDEX/rebase transform whose base period cannot be inferred. Ask the user. |

## What this lane will not do

It never writes the firm's learning stores. A binding you make here is ratified by the
user's eye, not by their formal approval workflow, and the production run depends on
those files. Renders go to `outputs/chat/<date>/` only.
