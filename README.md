# 2026 Daily Chart Replicator

Rebuilds the raw **Haver** charts that Neil sends "for the daily" as clean,
**RenMac-styled** figures — automatically reading each screenshot, resolving the
underlying series, reapplying the transforms, and routing every decision through a
**Microsoft Teams** approval loop before anything renders.

> ## Status: SHAKEDOWN — not production-signed, not headless
>
> The pipeline runs end-to-end on real emails, but it is still being hardened on
> live inbox days. **First production is MANUAL, not scheduled/headless:** the
> operator runs `run_daily` each morning, approves in Teams, and **eyeballs every
> render**. It is deliberately *not* wired to a scheduler yet.
>
> ### Pre-production checklist
>
> | # | Item | State |
> |---|---|---|
> | 1 | **May-12 store cleanup** — purge the poisoned `mpcuhsro` mapping, re-seed `UHROM@USECON` | ✅ **closed** (2026-07-01) |
> | 2 | **Transform-appropriateness guard** — park a `%`/`yryr`/`difa` transform applied on top of an already-`%Chg` series | ✅ **closed** (2026-07-01; `resolve.double_transform_reason`, `g8b_demo` E4/E4b/E4c) |
> | 3 | **One more validation backdate** — a date hitting `difa%`-annualized **and** a summed `A+B+C` formula, rendered + eyeballed | ✅ **closed** (2026-07-01; **2026-05-28**, see [Example B](#example-b--a-backdate-validation-replay)) |
> | — | **Render-preview gate** (ratify the rendered *chart*, not just the spec) | ⛔ **open — the gate to HEADLESS** |
>
> Items 1–3 (the pre-production hardening) are closed. The remaining blocker to a
> *scheduled/headless* run is the **render-preview gate**: `confirm_all` today
> ratifies the *resolution* (ticker/transform/axis), not the *pixels*. That gap is
> exactly what let two silent-wrong bugs through — the **transform-drop** (right
> spec, plotted raw) and the **store-poisoning** double-transform. Until a rendered
> preview is posted into the approve loop, production stays **manual** so a human
> sees every chart.

---

## What it does (end to end)

Every morning Neil emails "for the daily" with screenshots of Haver charts he wants
turned into RenMac figures. Some of those images are *raw Haver* charts (to rebuild);
others are already-finished RenMac/Macrobond/Bloomberg charts or plain text (to skip).
The pipeline automates the whole chain:

```
ingest  →  classify  →  read  →  resolve  →  confirm  →  title  →  render
```

1. **Ingest** — pull the day's "for the daily" emails from the mailbox (Graph
   `Mail.Read`), and/or read Bloomberg-chat commentary from a dated docx folder.
   Extract every embedded image (inline `cid:`/base64, attachments, `.docx` media).
2. **Classify** — decide which images are **raw Haver** screenshots (→ PROCESS) vs
   already-finished / non-Haver assets (→ SKIP). Discriminator = attribution text +
   title/palette style, with an Opus-vision tie-breaker on the uncertain calls.
   A false *skip* is the costly error, so the classifier never turns a real raw-Haver
   chart into a skip; the verdict is surfaced in Teams.
3. **Read** (G8 Stage 1, Opus vision) — for each raw-Haver chart, read a **ChartSpec**:
   per series {descriptor, raw Haver formula if shown, transform, axis L/R/shared,
   bracket lag `[-n]`, SA/NSA + frequency hint, legend} plus chart-level {n_series,
   axis mode, sample range, units, recession shading}. The read is a **hypothesis** —
   nothing binds off it alone.
4. **Resolve** (G8 Stage 2) — turn each read into a confirmed Haver ticker:
   - **Formula series** (mnemonics visible): confirm every mnemonic against Haver DLX;
     any unconfirmed addend parks the whole series.
   - **Description-only series**: search the Haver catalog, then bind **only** on an
     exact descriptor token-set match that also passes the **SA**, **aggregation**
     (EOP/AVG), and **transform-appropriateness** cross-checks. Anything short of a
     single confident, relevant match **parks** for a human — never a guess.
5. **Confirm** (Teams, `confirm_all`) — the safety property: **nothing renders until a
   human ratifies the full resolved set.** Each chart posts one resolution summary
   (every series' code@db, transform, axis, lag, freq); you `approve`, correct a field,
   or `skip`.
6. **Title** — after resolution approve, a separate round-trip posts Opus-proposed
   title/subtitle options; you pick one or edit it.
7. **Render** — pull each bound series (DLX), evaluate its formula through the
   signed-off transform engine, lift to the common frequency, shade NBER recessions if
   the original showed them, and write a RenMac-styled PNG. A visible **transform label**
   on the chart is the tripwire that makes a dropped transform self-evident.

Per-chart state lives in a CSV **ledger** so runs are idempotent and resumable — an
unanswered chart simply stays parked and picks up on the next run.

### Two sources, two non-crossing lanes

| Source | Where it comes from | Convention |
|---|---|---|
| **Email** (default) | Neil's "for the daily" emails | sender `ndutta@renmac.com` + subject `/\bdaily\b/i` (deliberately broad — see [subject-gate note](#subject-gate-broad-on-purpose)); the *mailbox* is your own inbox |
| **Bloomberg** | Neil's Bloomberg-chat blurbs, pasted into Word | drop `.docx` files into `inbox_bloomberg/MMDDYYYY/` — **the date is the folder**, filenames are free-form |

Both yield the identical `(commentary, images)` unit and flow through the same
downstream path. They share one ledger schema but **cannot cross**: email rows key on
the RFC `internetMessageId` (`<guid@domain>`), Bloomberg rows on a content hash
(`bb:<sha1>`) — disjoint string spaces. Production is event-driven and idempotent;
every "redo a day" goes through the **backfill lane** (`--reprocess`), which is
physically forbidden from touching the production ledger.

---

## Worked examples

### Example A — a live run for TODAY (the multi-pass flow)

A real day is **multi-pass**: the pipeline posts what it needs, you reply in Teams, and
you **re-run the same command** to pick up your replies and advance. Nothing is
blocking — each run does as much as it can and parks the rest.

**Pass 1 — ingest, read, resolve, and post what needs you:**

```bash
python scripts/run_daily.py --date 2026-07-01 --source all --approve
```

What happens:
- Ingests today's emails **and** the `inbox_bloomberg/07012026/` docx folder.
- Classifies each image; seeds one ledger row per raw-Haver chart into
  `data/ledger_backfill_2026-07-01.csv`.
- Reads each chart (Opus vision) and resolves each series.
- Auto-bound charts post a **resolution summary** to Teams and wait; charts with a
  parked series post a **per-series ask**.
- Prints the **G8 seam report** — per chart: the read (n_series / axis mode / recession),
  each slot as `AUTO` (with the bound `code@db` and similarity) / `PARK` (with reason +
  top-3 candidates) / `SKIP`, and a roll-up of charts by status.

**You reply in Teams** (see the [Teams reply reference](#teams-reply-reference)) — e.g.
supply a ticker for a parked series, or `approve` a resolution summary.

**Pass 2 — re-run the exact same command:**

```bash
python scripts/run_daily.py --date 2026-07-01 --source all --approve
```

- Idempotent: charts already `done` are skipped; charts already parked with an open ask
  are **not re-posted** (a cursor/post-once guard).
- Your replies are harvested: a supplied ticker re-resolves that slot; an `approve`
  advances the chart to the **title round-trip**, which posts title options.

**Pass 3 — reply with a title choice, re-run again:** the chart flips to `done`, the
render seam pulls the data and writes the PNG to `data/backfill_2026-07-01/renders/`.
The seam report shows the render path (and any snag, which is reported, never fatal).

Repeat until the seam report shows every chart `done` or `skipped`. **Eyeball each
rendered PNG** — that manual check is the point of the shakedown.

### Example B — a backdate validation replay

A backdate replays a specific past Eastern day instead of waiting for a fresh email —
the way we validate render seams against known charts. **2026-05-28** was the item-3
validation date: it uniquely carries **both** untested render seams — a summed
`A+B+C` composite and `difa%`-annualized.

**Inspect a candidate date first** (dry: ingest + vision-read into a throwaway temp dir,
no seed, no approve — the real ledger stays cold):

```bash
python scripts/inspect_date.py --date 2026-05-28 --source all
```

It prints the transform-type tally and flags which shapes are still untested
end-to-end. For 2026-05-28 that surfaced `BEEM1+(BEEM2+BEEM3)` (CEO-confidence
employment composite, the 3-addend nested sum), `BECMU+BECMO` / `BECMM+BECMD`
(capex composites), and `difa%(movv(NMSCNX,3),3)` / `difa%(movv(NMOCNX,3),3)`
(3m/3m SAAR capital-goods orders).

**Run the backdate** (isolated `data/ledger_backfill_2026-05-28.csv`):

```bash
python scripts/run_daily.py --date 2026-05-28 --source all --approve
# clean, repeatable redo of that day:
python scripts/run_daily.py --date 2026-05-28 --source all --reprocess --approve
```

**Timezone:** the date is an **Eastern wall-clock day**. The window is built by
localizing both midnights in `America/New_York` and converting to UTC — never a
hard-coded offset (EDT → `04:00Z`, EST → `05:00Z`, DST-transition days span the correct
23/25h). `--reprocess` wipes only the dated backfill ledger (a hard guard refuses any
path that isn't `data/ledger_backfill_*`), so validation replays never disturb
production.

The item-3 render seams were rendered directly for eyeballing via the production render
seam (`build_chart.render_row`) — see `outputs/g8_validation_0528/` (difa% shows the
≈ −49% GFC trough; the summed composites match their addends).

### Example C — the field controls, in one Teams round (legend · subtitle · title-less · retitle)

A real 2026-07-02 run exercised the newest controls on one email of charts. All of these
happen in the **existing** two round-trips (resolution, then title) — no extra taps.

**At the resolution summary** you now see a `legend:` line per series. Confirm the proposed
labels (or override), all in the same reply that ratifies the tickers:

```
[2026-07-02_a7d2c0d8ae_1] approve
# ↑ accepts the Opus-proposed legend labels AND the tickers in one shot; labels persist to
#   legend_labels.json and render silently forever after.

[2026-07-02_a7d2c0d8ae_3] legend1=AHE: Total Private (SA, $/hr)  legend2=AHE: Prod & Nonsup, Total Private (SA, $/hr)
# ↑ override the proposed label on series 1 & 2 in the SAME reply, then `approve`.
```

**At the title round-trip** — a custom subtitle that *already* states the transform, so
suppress the auto-appended label with `st_force=true`; and a title-less chart:

```
[2026-07-02_a7d2c0d8ae_3] title=Wage growth is cooling subtitle=y/y% chg st_force=true
# ↑ renders "(y/y% chg)" verbatim — NOT "(y/y% chg, % change, year-over-year)".

[2026-07-02_a7d2c0d8ae_5] none
# ↑ title-less; the transform label (e.g. "(3-month annualized % change)") still shows.
```

**Fixing a title after the fact** (the chart already rendered with the wrong headline):

```bash
python scripts/run_daily.py --date 2026-07-02 --source all --approve --retitle 2026-07-02_a7d2c0d8ae_3
# reply:  [2026-07-02_a7d2c0d8ae_3] title=Wage growth is cooling subtitle=y/y% chg st_force=true
# then re-run the same command to harvest + re-render (tickers/legends untouched).
```

### Example D — the chat lane (Claude Desktop, no email and no Teams)

The same resolver and the same renderer, driven from a conversation instead of the
inbox. You paste a Haver screenshot into Claude Desktop and iterate until the chart is
right. Useful for a one-off rebuild that never came through Neil's email, and for
anything where you want to see the pixels before committing to it.

```
You:    [paste a Haver chart]  Rebuild this. The formula printed on it is
        zs(yryr%(IP)); the other line is the Philly Fed manufacturing survey's
        current-activity diffusion index, z-scored. Shared axis, recession bands.

Claude: resolve_series(base_descriptor="Industrial Production",
                       formula="zs(yryr%(IP))", sa_hint="sa")
          -> resolved: ip@ip

        resolve_series(base_descriptor="Philly Fed Mfg Business Outlook: Current
                                        Activity Diffusion Index",
                       applied_transform="Z-Score", sa_hint="sa")
          -> resolved: bocgx@surveys   (sim 1.0, exact token match;
                                        siblings bocivx/bocnex both scored 0.8)

        render_chart(...)  -> outputs/chat/2026-08-20/philly_fed_ip.png   [shown inline]

You:    Put the survey on the right axis and lag it three months.
        [re-renders in place]
```

It resolves through the **same** `resolve.py` gates as the daily lane (exact token-set
bind, SA and aggregation cross-checks, DLX confirmation) and renders through the **same**
`build_chart.render_row`, so the output is pixel-for-pixel what the daily lane would draw
on the same data — that equivalence is gated by `scripts/g9b_lane_equivalence.py`. It reads every learning
store the daily lane has accumulated but **never writes them**, and it writes only to
`outputs/chat/<date>/`, never to a backfill render dir or the ledger.

Setup, worked examples, and the guardrail reference: **`haver_chart/SETUP.md`**.
Design and gates: `plan.md` §13. Pre-flight check:

```bash
C:/Users/asingh/envs/shared-3.10/Scripts/python.exe haver_chart/selftest.py   # expects 28/28
```

---

## CLI flags

All flags are on `scripts/run_daily.py`.

| Flag | Meaning |
|---|---|
| `--date YYYY-MM-DD` | The Eastern calendar day to run. Default: **today (ET)**. |
| `--source email\|bloomberg\|all` | Which source(s) to ingest. `email` (default) = the inbox; `bloomberg` = the `inbox_bloomberg/MMDDYYYY/` docx folder; `all` = both, seeding the same dated ledger. |
| `--mailbox ADDR` | Mailbox to read. Falls back to `AS_DAILY_MAILBOX`, then `AS_SENDER_EMAIL`. |
| `--sender ADDR` | Sender to filter on. Default `ndutta@renmac.com`. |
| `--vision` | Add the Opus-vision **classification** tie-breaker (needs `ANTHROPIC_API_KEY`). Without it, the deterministic pre-filter classifies (it never turns a real raw-Haver chart into a skip). |
| `--approve` | Run the **full G8 chain** (read → resolve → `confirm_all` → title → render). **Off by default** — a bare run is the front-half **ingest + classify + seed** shakedown report. |
| `--resolution-mode confirm_all\|selective_park` | `confirm_all` (default): nothing renders until you ratify the full resolved set. `selective_park`: auto-bind, park on failure, then titles (the legacy lane). |
| `--reprocess` | Wipe the **dated backfill** ledger first for a clean repeatable redo. Hard-guarded to `data/ledger_backfill_*` — can never touch the production ledger. |
| `--retitle CHART_ID` | Re-open a **finished** chart's **title** round-trip — `title` / `subtitle` / `st_force` (tickers/transforms/axes/legends untouched) — and re-render. No ingest/resolve. Two passes: pass 1 re-posts title options, pass 2 (same command) harvests your reply and re-renders. See [Post-render edits](#post-render-edits-changing-a-finished-chart). |
| `--relegend CHART_ID` | Re-open a **finished** chart's **resolution summary** for **legend** edits — reply `[id] legendN=…` then `[id] approve` (title/tickers untouched) — and re-render. No ingest/resolve. See [Post-render edits](#post-render-edits-changing-a-finished-chart). |
| `--no-render` | Stop after the round-trips; skip the render seam. |
| `--selftest` | Offline proof on `notes/` fixtures (window/filter math, extract+classify, EMF→error path, Bloomberg lane). No network. |

---

## Features

- **Two sources + the Bloomberg docx convention** — email inbox and
  `inbox_bloomberg/MMDDYYYY/*.docx` (date = folder). Same downstream path.
- **Two non-crossing lanes** — production (event-driven, idempotent) vs backfill
  (`--reprocess`, dated ledgers). `--reprocess` is physically forbidden from touching
  the production ledger; the id key spaces (`<guid@domain>` vs `bb:<sha1>`) don't overlap.
- **Confirm-everything safety property** — in `confirm_all`, **nothing renders until a
  human ratifies the full resolved set.** Every recall/relevance failure is a *non-bind*
  (a park routed to Teams), never a silent wrong bind.
- **Descriptor-relevance gate** — a description binds only on an **exact normalized
  token-set match** (the only thing that separates a headline series from a near-identical
  directional sibling, e.g. "General Business Activity" vs "…: Worsened"), plus SA and
  aggregation (EOP/AVG) cross-checks.
- **Transform-appropriateness guard** — if a candidate's metadata says it's *already* a
  rate/change (`%Chg` / `M/M` / `Y/Y` / agg_type `NDF` / `difa` / `yryr`) **and** the read
  applies an additional `%`/`yryr`/`difa` transform, it **parks** rather than
  double-transforming (the May-12 `mpcuhsro` class).
- **Learning store + decay** — approved binds are written to a clarified-knowledge store
  (`learned_descriptors.json` desc→code, `trusted_tickers.json` mnemonic→code,
  `native_ma.json` for MA-vs-applied). A store hit is a re-confirmed fast-path on the next
  occurrence. Day-one auto-resolve is a **floor** (~52% cold, 0 mis-binds); as the ~43
  recurring releases warm the store, the buried/unreachable tail converts to one-tap — a
  **decay curve, not a fixed rate**.
- **Thread-scoped quote-back dedup** — a reply that re-quotes the morning's charts is
  de-duplicated at ingest by content hash *within a Graph `conversationId`*, so quoted
  images never re-seed. Shown as `[DUP-SKIP]` in the report; genuinely-recurring charts in
  unrelated threads are never clobbered.
- **`approve all` bulk token** — ratify every cleanly-approvable chart in one Teams
  message. Guardrailed: it can never sweep a parked/low-confidence chart, and a per-chart
  reply always wins.
- **No-commentary / title-less** (a title round-trip choice, not a run mode) — Neil sends
  a bare chart with no blurb. It flows the **normal path** (resolution still ratified);
  in the title round-trip you reply **`[id] none`** (or `no-title`) to render it
  **title-less**. The subtitle's **non-suppressible transform label is still shown** (e.g.
  "(3-month annualized % change)"); only the descriptive title/subtitle are dropped. When
  there's no commentary to draft from, the ask presents `none`/custom (proposals may be
  empty — never an error). Add a title later with `--retitle` (pick a real title instead
  of `none`).
- **`--retitle`** — fix a finished chart's title without re-resolving, and re-render.
  Re-opens the same title round-trip, so `none` → title-less and a later real title both
  work through one path.
- **Transform-label tripwire** — a rendered chart *says* its transform (subtitle if all
  series share it, per-legend if they differ), so a dropped transform is a self-evident
  contradiction (raw-looking line under a "6-month moving average" label).
- **Legend labels are confirmed, learned artifacts** — a legend never shows a raw
  mnemonic. Opus (`claude-opus-4-8`) drafts a brief label from the real `get_series`
  descriptor (abbreviates AHE/CPI/…, keeps SA/units/base-year; composites like `NRS-NRSI7`
  get one grounded label); you ratify/override in the resolution round-trip; it persists
  to `legend_labels.json` keyed on `code@db`+**transform** (or the composite expr) and renders
  **silently** thereafter. Two floors: if no confirmed label exists and the descriptor is just
  the mnemonic, the renderer **parks/raises** (never degrades to the ticker); and **two
  identical legends on one chart raise**, so a same-ticker/two-transform chart can't ship
  ambiguous. Keying on the transform is what keeps those two labels distinct.
- **Line · bar · stacked-contribution shapes** — the read reports a per-series `plot_kind`
  and the renderer draws it, including **mixed-sign** stacked bars (positives stack up off the
  running positive total, negatives hang down off the negative one — the
  `econ-templates/charts/stacked_contribution.py` rule). A chart may mix kinds. See
  [Chart shapes](#chart-shapes-line--bar--stacked-contribution).
- **Month-aware x-axis, with per-chart overrides** — by default month spans render `Mmm-YY`
  ("Mar-25") and multi-year/decade spans keep bare years ("2004"). Any chart can override the
  tick interval (`x_tick_years`) and the label format (`x_label_fmt`: `year` / `month` /
  `quarter` → "Q1-26"). See [X-axis ticks and labels](#x-axis-ticks-and-labels).
- **Window edges follow the DATA, not the read** — the display left edge moves in to the first
  period that actually plots (no dead leading years), the right edge ends where the *plotted*
  series have data (a composite ends at the **min** of its operands), and `end_series` can pin
  the right edge to a named series. The right edge is then padded ~3 observations so the newest
  print isn't flush against the frame (bars also get a half-period pad on the left, so the
  first and last bar read whole). Every edge adjustment is **display-only** — the data window
  stays wide so transforms keep their run-up, and nothing is ever extrapolated.
- **Full Haver transform vocabulary** — `build_chart.phrase_to_haver` maps the *finite*
  Haver DLX transform-descriptor set to G3 formulas up front (not one chart at a time):
  **DIFF/DIFV/DIFA** (×`%`/`L`), **YRYR** (×`%`/`L`), **MOV[V\|A\|T]**, **ZS**, **LN** —
  across the period qualifiers *period-to-period* / *year-to-year* / *annual-rate* /
  *N-period*. `% Change - Period to Period` → `diff%(code,1)` (one **native** period, so it's
  m/m on monthly data at any frequency — never a hard-coded "month"). Haver's **abbreviated**
  annualization stem is included (`2-qtr %Change-ann` → `difa%(code,2)`) — the spelling that
  silently produced a non-annualized line on 2026-07-30. Compound phrases like
  `% Change - Year to Year of 3-month moving average` compose as `yryr%(movv(code,3))` so the
  moving average is **never silently dropped**. See the [vocabulary table](#transform-vocabulary-phrase--g3-formula).
- **Fail-loud everywhere** — unreadable image formats (EMF/WMF) → Teams, never a silent
  drop; an **unmapped or genuinely-novel transform phrase raises** rather than plotting raw
  (recognized-but-unsupported ones — `Year-to-Date`, `Index/rebase` needing a base pin —
  raise with a specific message); a mismatched SA/agg/freq parks rather than binding; a
  raw-mnemonic legend parks rather than degrading to the ticker.

---

## Teams reply reference

Replies are bound to a chart by a leading **`[chart_id]`** tag (the chart_id is in each
ask). There are **three stages**, plus a bulk token.

> **Numbering — the one real gotcha.** Stage 1 (parked-series ask) uses **`#n`,
> zero-indexed** (it mirrors the vision read's slot indices: `#0`, `#1`, …). Stages 2 & 3
> (resolution summary, title) use **`1`/`2`, one-indexed, no `#`**. So `#0` is the first
> series in a Stage-1 ask, but `1` is the first series in a Stage-2 summary.

### Stage 1 — parked-series ask (supply tickers)

Posted when a chart has one or more parked series. Reply `[id]`, then **one line per
parked item**:

```
[chart_id]
#0 UHROM@USECON
#2 LJQTPA@USECON
```

- `#<n> code@db` — bind the ticker for parked slot `#n` (`#n` = the read's zero-indexed
  slot). A bare `code@db` with no `#n` fills the next pending ticker slot in order (the
  natural "two tickers, line by line" case).
- `#<n> name` / `#<n> transform` — answer a native-MA **clarify** (is the moving average
  the series *name*, or an *applied* transform?).
- `[id] skip` — drop the whole chart.

### Stage 2 — resolution summary (ratify the resolved set)

Posted once every series on a chart is bound. Positions are **1-based**:

```
[chart_id] approve
[chart_id] 1=UHROM@USECON              # fix ticker on series 1
[chart_id] 2:axis=R                    # move series 2 to the right axis
[chart_id] 1:transform=yryr%           # correct a transform
[chart_id] legend1=AHE: Total Private (SA, $/hr)   # override the proposed legend label
[chart_id] skip                        # drop the chart
```

The summary now shows a `legend:` line per series — a store-confirmed label (silent) or
an Opus-proposed one (grounded on the real `get_series` descriptor). `approve` ratifies
the shown labels and persists them (write-once); override any with `legendN=…`. No
separate round-trip. Precedence: `skip` > corrections (applied, then re-posted) > `approve`.

### Stage 3 — title round-trip (choose/edit the headline)

Posted after a resolution approve, with Opus-proposed options:

```
[chart_id] 1                           # accept option 1 (== approve)
[chart_id] 2                           # accept option 2
[chart_id] title=Hiring plans hold up subtitle=CEO survey, quarterly
[chart_id] title=Wage growth is cooling subtitle=y/y% chg st_force=true   # verbatim subtitle
[chart_id] none                        # (or `no-title`) → render TITLE-LESS
```

- Subtitles are **paren-free** — the renderer adds exactly one set of parens.
- **`st_force=true`** (subtitle-force) — use your `subtitle=` **verbatim**, appending no
  auto transform-label. Default (no flag) appends the shared transform to the subtitle
  (the tripwire stays on); `st_force=true` is an explicit override — you assert the
  subtitle carries (or intentionally omits) the transform. Works in `--retitle` too.
- Clear the subtitle with an empty `subtitle=` or a sentinel (`none` / `-` / `blank`)
  **inside a `subtitle=`**; a **bare** `[id] none` means the whole chart is title-less.
- **`none`** (bare) = no title **and** no descriptive subtitle — but the
  **transform label is non-suppressible** (the tripwire), so a transformed chart still
  shows e.g. "(3-month annualized % change)". A commentary-less chart with empty proposals
  still gets this ask (`none`/custom); it never errors.
- The **transform label is non-suppressible**, so there is no silent-wrong path from a
  title edit.

### Bulk — approve everything clean

```
approve all
```

- **No `[chart_id]` tag** — a tagged reply is chart-scoped. `approve all` / `approve *` /
  `approve-all` all work.
- Only sweeps **fully-clean** charts (every series bound at the resolution gate; the
  proposed option at the title gate). A per-chart reply **always wins**; parked/low-
  confidence charts are never swept. Both gates echo back the ratified set.

### After the fact — retitle a finished chart

```bash
python scripts/run_daily.py --date 2026-07-01 --retitle 2026-07-01_ab12cd34ef_0
# reply [id] 1/2/title=… in Teams, then re-run the same command to re-render
```

---

## Post-render edits (changing a finished chart)

Once a chart is `done`, you don't re-run the whole day to tweak it. Two focused,
Teams-driven re-open flags surgically re-open **one** round-trip on **one** chart, leave
everything else intact, and re-render. Both take a `chart_id` and neither re-ingests or
re-resolves. Pick by **what** you're changing:

| I want to change… | Flag | Reply in Teams |
|---|---|---|
| **Title / subtitle** (and `st_force`) | `--retitle CHART_ID` | `[id] title=… subtitle=… [st_force=true]` or `[id] none` |
| **Legend label** on a series | `--relegend CHART_ID` | `[id] legendN=…` then `[id] approve` |
| **A ticker / axis / transform** | (re-run the resolution gate) `--relegend` also re-opens the resolution summary, where `[id] N=code@db`, `[id] N:axis=R`, `[id] N:transform=…` are valid too | `[id] N=code@db` … then `[id] approve` |

Both are **multi-pass** (like the live run): the first invocation re-posts the ask, you
reply in Teams, and you **re-run the same command** to harvest and re-render.

### Change a subtitle (and force it verbatim)

`f06a0e2c3d_0` should show `Z-score` as the subtitle, with nothing auto-appended:

```bash
python scripts/run_daily.py --date 2026-07-14 --retitle 2026-07-14_f06a0e2c3d_0
# Teams reply:  [2026-07-14_f06a0e2c3d_0] subtitle=Z-score st_force=true
python scripts/run_daily.py --date 2026-07-14 --retitle 2026-07-14_f06a0e2c3d_0   # re-render
```

`st_force=true` uses your subtitle **verbatim** (no auto transform-label appended); drop it
and the shared transform is appended (the safe default). `keep the title` by omitting
`title=` — only the fields you send change.

### Change a legend label

`8cc2c82d64_1`, series 1 should read `Nonfarm Bus. Real Output/Hour (SA, 2017=100, y/y% chg)`:

```bash
python scripts/run_daily.py --date 2026-07-14 --relegend 2026-07-14_8cc2c82d64_1
# Teams reply:  [2026-07-14_8cc2c82d64_1] legend1=Nonfarm Bus. Real Output/Hour (SA, 2017=100, y/y% chg)
# then:         [2026-07-14_8cc2c82d64_1] approve
python scripts/run_daily.py --date 2026-07-14 --relegend 2026-07-14_8cc2c82d64_1   # re-render
```

The confirmed label **persists** to `legend_labels.json` keyed on the series' `code@db`
(or composite expression), so it renders silently from then on — and if the **same series
recurs** on a later day it reuses your label without asking. (Caveat: because a single
series keys on `code@db`, a transform baked into the label — e.g. `y/y% chg` — will ride
along if that exact ticker appears on another chart; keep transform wording in the subtitle
when it isn't intrinsic to the series.)

> **Doing both on one chart** (like `f06a0e2c3d_0`: a legend edit **and** a subtitle):
> run `--relegend` for the legend, then `--retitle` for the subtitle (or vice-versa) — each
> re-renders, and neither disturbs the other's fields.

---

## Situations we hit (and the fix)

Real incidents from the shakedown days, each an operational playbook: **symptom →
why → what you do**. Most are *not bugs* — they're the fail-loud guards working, and the
fix is a Teams reply or a flag, not a code change.

### A render SNAG on one chart (`SNAGGED: unmappable applied_transform …`)

- **Why:** the vision read carried a transform phrase the renderer didn't recognize, so it
  **refused to plot** rather than silently drawing the raw (un-transformed) series. That
  refusal is the whole point of the seam-gate — a chart that plots raw data under a real
  title *looks* right and is wrong.
- **What you do:** the run is **not aborted** — the snag is reported on the row and the
  chart stays approved for a retry. Most canonical Haver phrasings are already mapped (see
  the [vocabulary table](#transform-vocabulary-phrase--g3-formula)); if a genuinely new one
  surfaces, add it to `build_chart._flat_transform` and re-run. *This is how
  `% Change - Period to Period` (2026-07-02) got closed — it was the guard doing its job.*

### A legend shows a raw ticker (`LITRTRDA`, `NRS - NRSI7`)

- **Why (old bug, now fixed):** the descriptor lookup came back empty and the renderer used
  to **fall back to the mnemonic**. It no longer does — a raw-mnemonic legend now
  **parks/raises**.
- **What you do:** at the **resolution summary**, each series shows a `legend:` line —
  either a store-confirmed label (silent) or an Opus-proposed one drafted from the real
  `get_series` descriptor. `approve` ratifies them; override a wrong one in the same reply
  with `legendN=…` (e.g. `[id] legend1=AHE: Total Private (SA, $/hr)`). It persists to
  `legend_labels.json` and never asks again for that `code@db`. **After render**, edit a
  legend with `--relegend <chart_id>` (see [Post-render edits](#post-render-edits-changing-a-finished-chart)).

### The subtitle duplicates the transform (`(y/y% chg, % change, year-over-year)`)

- **Why:** by default the shared transform label is **appended** to your subtitle (the
  non-suppressible tripwire) — so a custom subtitle that *already* names the transform
  stacks.
- **What you do:** in the title round-trip, add **`st_force=true`** — your `subtitle=` is
  used **verbatim**, nothing appended. `[id] title=… subtitle=y/y% chg st_force=true`. Omit
  the flag and the auto-label append (the safe default) stays on.

### A chart didn't re-render after I edited its title in Teams

- **Why:** a finished chart isn't re-read on a normal run (idempotent — it's `done`).
- **What you do:** `--retitle <chart_id>` re-opens **only** that chart's title round-trip
  (tickers/legends/transforms untouched), then re-renders. Two passes: post options, then
  reply + re-run. Works with `st_force`/`none` too. For a **legend** edit use
  `--relegend <chart_id>` — see [Post-render edits](#post-render-edits-changing-a-finished-chart).

### The legend overspills off both edges / a label swallowed the next one (`… legend2=MBA …`)

- **Why (two bugs, both fixed):** (1) a single Teams reply with **two** overrides on one line
  (`legend1=… legend2=…`) was parsed **greedily** — `legend1` captured the rest of the line
  including `legend2=`'s text, welding both into slot-0's label (2026-07-15 MBA chart). (2) the
  renderer always laid legends side-by-side, so long labels ran off both edges.
- **What you do:** nothing, going forward:
  - The parser now splits **each** `legendN=` on a line at the next marker, so many overrides
    per reply are fine (`[id] legend1=… legend2=… approve`).
  - The renderer **wraps** long legends onto multiple rows (`render._legend_layout`): labels
    that would overflow collapse to one column and **stack vertically**, and the bottom band
    opens proportionally so the stack keeps a clear gap above the Source line.
  - If a poisoned label already persisted to `legend_labels.json`, purge that one key and
    re-approve (or `--relegend`); we repaired the MBA key in place.

### A dual-axis legend doesn't say which side a line reads against

- **Why:** on a dual-axis chart the two y-scales are different, so a legend without an axis
  hint is ambiguous.
- **What you do:** nothing — dual-axis legends are now **auto-tagged** with `LHS`/`RHS`
  (`render._axis_side_label`): the side goes **inside** a trailing qualifier bracket if the
  label has one (`MBA Purchase Loan Apps Index (NSA, y/y %chg)` → `… (NSA, y/y %chg, RHS)`),
  else a fresh bracket is added (`ISM Mfg: PMI Composite Index` → `… (LHS)`). It's idempotent
  (a re-render never double-tags), and single-axis charts stay untagged. A **confirmed**
  legend label is authoritative — the auto transform-label is **not** appended on top of it
  (that used to duplicate the qualifier and shove the axis tag outside the bracket).

### The run crashed mid-resolve with `QueryCanceled: canceling statement due to statement timeout`

- **Why:** the Haver metadata search hits a **Neon** read-only mirror that **scales its compute
  to zero when idle**. The first query of a run then pays a cold compute + cold buffer cache and
  can exceed the MCP server's 15s `statement_timeout` (warm searches measure ~2–5s). That one
  cold query used to abort the whole day's resolve. (It is **not** related to deleting the
  `inbox_bloomberg` day folder — that just makes the bloomberg source find nothing, which is
  fine.)
- **What you do:** nothing — `haver_search` now **retries** a transient DB timeout / connect
  blip (`QueryCanceled`, `OperationalError`) with backoff; the retry lands on a now-warm
  compute/cache and succeeds, so a cold start never sinks the run. A genuine, persistent DB
  failure still raises **loud** after the retry budget (it's an outage, not a warm-up blip);
  a non-transient error (e.g. a bad query) is never retried. If you hit it, just re-run — the
  compute is warm by then anyway.

### A real Haver chart was skipped as "no plotted series" (`colored_frac=0.0033`)

- **Why (fixed):** the palette pre-filter's first gate skipped any image whose colored-pixel
  fraction fell below `T_COLORED_MIN` (0.004) as "text/non-chart". A **sparse** Haver chart —
  thin navy+teal lines on lots of white — can measure `colored_frac ≈ 0.003` while being
  ~100% Haver blue, so it was wrongly dropped **before** the blue-palette check even ran
  (the 2026-07-20 PCE/Unemployment chart in `Document7.docx`). This violated the pre-filter's
  own invariant ("never SKIP a real raw-Haver chart on its own").
- **What you do:** nothing — `classify.preclassify` now checks the **positive non-Haver
  signals first** (dense text, maroon/green/orange series), then rescues a **blue-dominant**
  chart (`blue_frac ≥ T_BLUE_MIN` and blue is the majority of the color present) *before* the
  "no series" floor. A colorless text/table still skips; a stray sub-threshold blue speck is
  not falsely rescued; a finished multi-color chart (orange/green/maroon) still skips even if
  it also has a blue line.
- **Note on `Document7.docx`:** it holds **two** images — `image1` (the *FOMC Hawk-Dove Index*)
  is a **finished, multi-color** chart with a bold-black title and a custom source line, so it
  is **correctly skipped** (not a raw Haver screenshot); `image2` (the PCE/Unemployment Haver
  chart) is the one that now classifies as `raw_haver`.

### A composite line (a difference/sum) stops short of the right edge — its latest "dip" is trimmed

- **Why:** the two series in a difference like `zs(NFIB7 − NFIB6)` were pulled from parquet
  caches written on **different days** (`nfib7` re-pulled today with June, `nfib6` a stale
  July-1 cache ending May). A difference is defined only where **both** operands have data,
  so it truncated to the older operand's last month (May) — losing June. The x-axis, meanwhile,
  was set by the *longer* raw operand, so the line dangled short of the edge and its newest
  move (the dip) vanished with **no error** (2026-07-14 NFIB chart).
- **What you do:** nothing, going forward — two guards now prevent it:
  1. **Daily cache freshness** (`g4_lib.pull`): a parquet cache from a *prior calendar day* is
     re-pulled, so every operand in a run shares one vintage. Override with
     `HAVER_CACHE_TTL_DAYS` (e.g. a large value for a frozen offline replay).
  2. **Composite-aware window end** (`build_chart._window_end`): the x-axis ends where the
     *plotted* series actually have data (a composite ends at the **min** of its operands),
     and a cross-operand vintage skew is printed **loud** on stderr (`[vintage-skew] …`).
  To force it manually: delete the stale `outputs/raw/<code>_*.parquet` and re-render.

### Both lines on a chart show the SAME legend (one ticker, two transforms)

- **Why:** the legend store was keyed on the bare `CODE@DB` while the stored label *text*
  carried a transform qualifier — finer-grained **values** than **keys**. A chart plotting one
  ticker under two transforms (`fsdh@usecon` y/y **and** 1-qtr saar) wrote both labels to that
  one key, **last write won**, and then both lines looked up the loser's label. The resolution
  ask had shown them correctly, so it only surfaced in the render (2026-07-30).
- **What you do:** nothing, going forward — three layers now prevent it:
  1. the legend key is **transform-qualified** (`FSDH@USECON|YRYR%(#)`), canonicalized on the
     derived G3 formula, so two spellings of the same math share a label but different math
     can't collide. A **level** series keeps the old bare key, so warm stores still hit;
  2. `_legend_label` prefers the slot's **own** ratified label over the shared store;
  3. a **floor**: two identical legends on one chart now **raise** rather than render an
     unreadable chart.
  To relabel one after the fact: `--relegend <chart_id>` then `[id] legendN=…`.

### A transformed line looks plausible but the level is ~half (or ~2×) what Haver printed

- **Why:** Haver's DLX prints the **abbreviated** annualization stem — `2-qtr %Change-ann`,
  `3-month %Change-ann`. The old matcher only recognized spelled-out forms ("annual rate",
  "annualized", "saar"), so `"ann"` didn't match and the phrase fell through to plain
  `diff%(code,N)` — a **non-annualized** N-period change. Worse than a miss: the fail-loud
  floor never fired, because a *different valid rule* matched. On 2026-07-30 GDP's 2-qtr
  rendered **3.35** where the source chart's own data label read **6.81**.
- **What you do:** nothing — `-ann` / `-Ann` / `-Annualized` now map to `difa%`. The quickest
  independent check on any transformed chart: Haver screenshots often print the **last value**
  in a box on the right; reproduce that number before shipping.

### A Haver bar or stacked-contribution chart came out as lines

- **Why:** it wasn't a mis-detection — the shape was an **unrepresented dimension**. The read
  never reported it, the chart spec had no field for it, and the renderer hard-coded a line,
  so *every* chart was a line chart by construction (2026-07-30).
- **What you do:** the read now reports `plot_kind` per series (see
  [Chart shapes](#chart-shapes-line--bar--stacked-contribution)). To correct an
  already-rendered chart, set the slot's `plot_kind` and re-render:

```python
# from src/ — set the shape, then re-render (nothing else changes)
import ledger as L, build_chart as BC
led = L.Ledger('../data/ledger_backfill_2026-07-30.csv')
r = next(x for x in led.rows if x['chart_id'] == '2026-07-30_8c102dbbea_1')
for s in r['series']:
    s['plot_kind'] = 'stacked_bar'        # or 'bar' / 'line', per series
led.save()
BC.render_row(r, f"../data/backfill_2026-07-30/renders/{r['chart_id']}.png")
```

### A stacked contribution chart's bars are ~2× too tall

- **Why:** one slot was bound to the **total** rather than the named sub-component, so the
  stack double-counted. On 2026-07-30 the read descriptor arrived **truncated**
  (`…Other Equipment: Contrib to Real GDP %Chg(SAAR,%P...`) and resolved to `ptfneh@usecon`
  ("Pvt Nonres Fixed Investment: **Equipment**" — the total) instead of `ptfneoh@usecon`
  ("**Other** Equipment"). The peak read 4.29 where the source bar read 2.42.
- **What you do:** a contribution stack has a **checkable identity** — the components must sum
  to their "total" sibling. Verify it before shipping:

```python
# components should sum to the total series (here ptfneh = total Equipment)
import build_chart as BC, pandas as pd
parts = ['ptfneoh', 'ptfneth', 'ptfnenh', 'ptfneih']
df = pd.DataFrame({c: BC.G.pull(c, 'usecon', 'Q').values for c in parts})
df['stack'] = df.sum(axis=1)
df['total'] = BC.G.pull('ptfneh', 'usecon', 'Q').values
print(df.tail()[['stack', 'total']].round(2))     # the two columns must match
```

  If they don't, one slot is bound to the wrong level of the hierarchy — rebind it, and purge
  the bad `learned_descriptors.json` key so the mis-bind doesn't ride.

### The chart opens on a blank stretch (nothing plotted for the first N years)

- **Why:** the read's `sample_start` predated the data — either a misread, or the source axis
  genuinely starts earlier than the series. On 2026-07-30 `_0` read **1948** while
  `zs(yryr(YPSVR))` only begins **1960Q1**, leaving a dead decade.
- **What you do:** nothing — the display left edge now moves in to the first period that
  actually plots (MIN across series, so the longest line still shows fully). The **data**
  window still starts at the read, because a z-score / y-o-y needs its run-up history.

### A title/legend renders `&amp;` instead of `&` (or `&quot;`, `&lt;`)

- **Why:** Graph returns chat bodies as HTML, so a `C&I` you typed in Teams arrives as
  `C&amp;I`. The reply cleaner stripped tags but special-cased only `&nbsp;`, and it only ran
  when Graph reported `contentType == "html"` — a body typed `"text"` bypassed it entirely.
  On 2026-08-03 that put *"Demand for C&amp;I credit keeps firming"* on the canvas. (The
  legend on the same chart was fine, because Opus-proposed labels never go through the chat
  path — that asymmetry is the tell.)
- **What you do:** nothing going forward — entity decoding now happens in `_untag`, the choke
  point every reply parser funnels through, so it's transport-independent. For a title already
  baked into a ledger, repair it in place and re-render:

```python
# from src/
import ledger as L, teams as TM, build_chart as BC
led = L.Ledger('../data/ledger_backfill_2026-08-03.csv')
r = next(x for x in led.rows if x['chart_id'] == '2026-08-03_636a683648_1')
r['chosen_title'] = TM._strip_html(r['chosen_title'])      # '…C&amp;I…' → '…C&I…'
led.save()
BC.render_row(r, f"../data/backfill_2026-08-03/renders/{r['chart_id']}.png")
```

### A `[+n]` lead plotted like a lag (or a legend reads `… lagged by 4qtrs [-4]`)

- **Why:** the bracket parser matched the sign but captured only the digits, so `[+4]` and
  `[-4]` produced the *same* shift — a requested lead silently rendered as a lag. Separately,
  the legend tag was hard-coded to `[-n]` and appended unconditionally, so a label that already
  said "lagged by 4qtrs" got a second, redundant `[-4]`.
- **What you do:** nothing — the shift is signed now (`[-n]` lag, `[+n]` lead, unsigned reads as
  a lag), the tag is emitted in its own notation, and it's suppressed when the confirmed label
  already conveys the shift. A tag that's present but unparseable now **raises** instead of
  quietly plotting unlagged. See [Lag and lead tags](#lag-and-lead-tags--n--n) for the
  semantics and the transform-then-shift ordering.

### A level series got plotted as a % change (`Avg, % p.a.`, `Sum, Mil.$`)

- **Why:** Haver prints an **aggregation + units** line under each series name describing how
  the series *is* — `Avg, % p.a.` is just `agg_type: AVG` and `data_type: %` concatenated. The
  read handed that to `applied_transform`, and the `%` plus the annual `p.a.` stem matched a
  change rule, so 2026-08-11's 2-Year Treasury plotted as `difv%(FCM2,1)` instead of its level.
- **Why approval didn't catch it:** the resolution summary showed the transform as
  "Avg, % p.a.", which reads like a fine description of a yield series. Only the plotted line
  gave it away — worth a glance whenever a transform string looks like *units* rather than an
  *operation*.
- **What you do:** nothing — aggregation/units phrases now resolve to a level before any change
  rule can match. Phrases that NAME an operation ("% Change - Year to Year", "3-month moving
  average") are unaffected, and genuinely novel phrasing still fails loud.

### A chart snags with `KeyError: 'D'`

- **Why:** the slot bound a `@daily` series. Daily is not supported across the stack —
  `FREQ_RANK` lists `'D'`, but the pull's period map, `FREQ_BASE`, `EDGE_TOL`, the period-end
  alias table and the x-pad table all stop at weekly.
- **What you do:** if the series has a weekly twin, rebind to it — most Haver daily series do
  (`petexa@daily` → `petexa@weekly`, same EIA series and units). Otherwise daily needs adding as
  a real frequency tier; see the 2026-08-11 entry in `plan.md` for the five places to touch.
- **Heads-up:** a render snag leaves the row at `approved`, and the error text may not reach the
  saved ledger — check the run log, not just the ledger, when a chart is silently missing.

### A lagged line stops a year early (or its newest readings are missing)

- **Why:** two compounding bugs. `pandas.shift(n)` moves values *inside a fixed index*, so the
  last **n** observations fall off the end — on a lagged leading indicator those are the newest
  readings, exactly what the chart exists to show. And the render window was derived from raw
  operand end dates with no knowledge of the shift, so it clipped the extension back off anyway.
  On 2026-08-03 `_2`, `fwill` runs to 2026-Q3 but the line stopped there instead of reaching
  2027-Q3, losing 2025-Q4 through 2026-Q3.
- **What you do:** nothing — the shift now moves the **index** (preserving every observation) and
  `_window_end` adds each slot's shift, counted in that slot's native period. A lagged line
  legitimately extends past the other series; that's the chart working, not a bug.

### Every chart looks flush against the right frame

- **Why:** the pad had a ceiling but no **floor**. "3 observations" scales with the data's
  cadence, not the chart's width, so on a 40-year monthly chart 3 months is 0.6% of the panel —
  a couple of pixels. Measured across the 2026-08-11 run, the six charts padded 0.16%–1.27% of
  width. The padding was running; it was just invisible.
- **What you do:** nothing — the pad is now clamped to **2%–6% of chart width**, giving a
  consistent gutter on every chart regardless of span. Use `chart_spec.x_pad_periods` to scale
  it per chart (`0` = flush).

### The right-edge gap looks too small on a mixed-frequency chart

- **Why:** the pad is "~3 observations", but it was measured off the *plotted* index. A quarterly
  series interpolated up to a monthly common grid has ~30-day spacing, so 3 observations became 3
  **months** instead of 3 quarters. Latent on every mixed-frequency chart, and only visible once a
  low-frequency series set the right edge.
- **What you do:** nothing — `PlotSeries` now carries the native frequency and the pad uses it.
  Same-frequency charts are unaffected. To override on one chart, set `chart_spec.x_pad_periods`.

### "Why is it asking me about this series again?"

- **Why:** a key-normalization mismatch between the read and a store entry — the same
  series keyed two different ways, so the store never hit (this bit us on the native-MA
  store, and again as the `mpcuhsro` poisoning).
- **What you do:** the three series-keyed stores (`learned_descriptors`, `trusted_tickers`,
  `legend_labels`) share one normalization; a warm store re-confirms silently. If a bad bind
  ever gets ratified, purge that one key from the JSON store and re-approve — don't let a
  poisoned mapping ride (the **transform-appropriateness guard** now also parks a `%`/`yryr`/
  `difa` applied on top of an already-`%Chg` series, which is what caused that class).

### Neil titled the email just "Daily" and it seemed to be skipped

- **Why (fixed):** the old subject regex required "for … daily" and would **silently drop**
  a bare "Daily" — the worst kind of miss (fewer charts than he sent, no error).
- **What you do:** nothing — the gate is now broad (see below). If Neil ever uses a subject
  with no "daily" at all, that's the only remaining gap.

### Subject gate (broad on purpose)

`ingest.SUBJECT_RE` is **`/\bdaily\b/i`** — it matches "Daily", "Daily charts",
"for the daily", "RE: for the daily", etc. Deliberately broad because the sender is already
filtered to Neil and the error costs are asymmetric:

| | Cost |
|---|---|
| **Under-match** (too narrow) | a **silent chart-drop** — catastrophic, invisible |
| **Over-match** (too broad) | a cheap **no-op** — a non-chart "daily …" email is admitted, the classifier finds no raw-Haver chart, nothing seeds |

`classify` is the real gate, so breadth here is safe. (Regression-locked in
`selftest_workflow` incl. the over-match no-op.)

### Transform vocabulary (phrase → G3 formula)

What `build_chart.phrase_to_haver` recognizes up front. `code` is the bound mnemonic; `N` is
parsed from the phrase; frequency-agnostic (`,1` = one **native** period).

| Canonical Haver phrasing | G3 formula | On-chart label |
|---|---|---|
| Level / (blank) | *(none)* | — |
| Change - Period to Period | `diff(code,1)` | change, period-over-period |
| **% Change - Period to Period** | `diff%(code,1)` | % change, period-over-period |
| Change / % Change - Year to Year | `yryr(code)` / `yryr%(code)` | (%) change, year-over-year |
| % Change - Annual Rate | `difa%(code,1)` | annualized % change |
| N-month annualized % change | `difa%(code,N)` | N-{unit} annualized % change |
| **N-qtr / N-month `%Change-ann`** (Haver's abbreviated stem) | `difa%(code,N)` | N-{unit} annualized % change |
| N-period moving average | `movv(code,N)` | N-{unit} moving average |
| N-period moving sum/total | `movt(code,N)` | N-{unit} moving sum |
| N-period moving average, annual rate | `mova(code,N)` | N-{unit} moving average, annual rate |
| Log change (p2p / yoy) | `diffl(code,1)` / `yryrl(code)` | log change, … |
| z-score / standardized | `zs(code)` | z-score |
| natural log (level) | `ln(code)` | natural log |
| `<transform> of N-period moving average` | `<transform>(movv(code,N))` | outer transform's label |
| Year-to-Date · Index/rebase | **raises** (NeedPin — resolve as an explicit formula) | — |
| anything else | **raises** (fail-loud floor) | — |

**The annualized formulas, explicitly** (`difa%`, where `npy` = periods per year):

```text
difa%(x, n)  =  ( (x_t / x_{t-n}) ** (npy / n)  -  1 ) * 100

  quarterly, n=2  → exponent 4/2 = 2   →  ((x_t / x_{t-2})**2 - 1) * 100
  monthly,   n=3  → exponent 12/3 = 4  →  ((x_t / x_{t-3})**4 - 1) * 100
```

Compare with `diff%(x,n)` = `(x_t/x_{t-n} - 1)*100`, which is **not** annualized. On quarterly
GDP the two differ by ~2× (3.35 vs 6.81) — see the `-ann` situation below.

---

### Chart shapes: line · bar · stacked contribution

The read reports a per-series `plot_kind` (`line` | `bar` | `stacked_bar`) and the renderer
draws it. A chart may **mix** kinds (bars for a quarterly rate + a line for its y/y).

| `plot_kind` | Drawn as |
|---|---|
| `line` (default) | continuous stroke, house palette (maroon → slate → navy) |
| `bar` | bars off a zero rule; width derived from the series' own period spacing |
| `stacked_bar` | **mixed-sign** stacked contribution — positives stack up off the running positive total, negatives hang down off the running negative total (`PALETTE_EXTENDED`, so a 4+ component stack never recycles a color) |

Naive stacking on one cumulative `bottom` would put a positive contribution in negative
territory whenever an earlier component is negative; the mixed-sign rule (shared with
`econ-templates/charts/stacked_contribution.py`) is what makes a contribution chart correct.

To set it on an already-rendered chart, edit the slot's `plot_kind` in the ledger row and
re-render — see the situation below.

---

### X-axis ticks and labels

The default is automatic: a `Mmm-YY` format on spans up to 5 years, bare years beyond that,
with matplotlib choosing tick positions. Two `chart_spec` keys override it per chart.

| Key | Values | Effect |
|---|---|---|
| `x_tick_years` | any integer `N` | major ticks every **N years** (instead of the automatic decade/half-decade choice) |
| `x_label_fmt` | `auto` (default) | `Mmm-YY` on ≤5y spans, bare years beyond |
| | `year` | `2024` — bare years at any span |
| | `month` | `Mar-25` |
| | `quarter` | `Q1-26`, ticks pinned to **quarter ends** |
| `x_pad_periods` | integer, default **3** | observations of right-edge breathing room; `0` = flush edge |

An unrecognized `x_label_fmt` **raises** rather than silently falling back.

**Right-edge pad.** Because every series ends at its latest print, the newest observation used
to sit flush against the frame (and on bar charts the final bar was half-clipped). The display
right edge is now extended ~3 observations past the last point, measured in the **native period of
the series that sets the edge** — 3 weeks on a weekly chart, 3 months on a monthly one, 3 quarters
on a quarterly one, and on a mixed-frequency chart the cadence of whichever series reaches furthest
right. *Native* matters: a quarterly series interpolated onto a monthly common grid has monthly
index spacing, so measuring the pad off the plotted index quietly gave it 3 **months**.

That figure is then clamped to a share of the chart's width at **both** ends — a **2% floor** and a
**6% ceiling**. The ceiling stops a short quarterly chart from trading a clipped point for a big
empty margin. The floor exists because "3 observations" is relative to the *data's* cadence, not the
*chart's* width: on a 40-year monthly chart 3 months is 0.6% of the panel, so long charts still read
as flush. The floor is what actually delivers the gutter, and it keeps it visually consistent from
chart to chart. Setting `x_pad_periods` scales the floor with it, so `0` still gives a flush edge.

Bars also get a half-period pad on the **left**, since a bar is centered on its
stamp and the opening bar would otherwise be sliced in two. All of this is **display-only** —
the data window is untouched, so nothing is extrapolated.

```python
# from src/ — 5-year ticks on a 66-year chart; quarter labels on a quarterly one
import ledger as L, build_chart as BC
led = L.Ledger('../data/ledger_backfill_2026-07-30.csv')

r0 = next(x for x in led.rows if x['chart_id'] == '2026-07-30_8c102dbbea_0')
r0['chart_spec']['x_tick_years'] = 5              # 1960, 1965, 1970, …

r2 = next(x for x in led.rows if x['chart_id'] == '2026-07-30_8c102dbbea_2')
r2['chart_spec']['x_label_fmt'] = 'quarter'       # Q1-23, Q2-23, … Q2-26
led.save()

for r in (r0, r2):
    BC.render_row(r, f"../data/backfill_2026-07-30/renders/{r['chart_id']}.png")
```

**Why `quarter` ticks sit on quarter *ends*:** a low-frequency observation is stamped at the
END of its period house-wide (a Q1 value sits on Mar-31). Ticking quarter *starts* would place
the Apr-1 gridline beside the Mar-31 bar and label that bar "Q2" when it's Q1 — an
off-by-one-quarter mislabel that looks perfectly plausible. Regression-locked.

### Lag and lead tags (`[-n]` / `[+n]`)

A slot's `lag` field carries a Haver bracket tag, shifting the series in its **own native
period** — `[-4]` on a quarterly series is 4 quarters, even when the chart's common grid is
monthly.

| Tag | Meaning | At date *t* the line shows | Line moves |
|---|---|---|---|
| `[-4]` | **lag** 4 observations | the value from *t−4* | right (later) |
| `[+4]` | **lead** 4 observations | the value from *t+4* | left (earlier) |
| `[4]` | unsigned reads as a **lag** | the value from *t−4* | right |
| absent | no shift | — | — |

**Transform first, then shift.** The bracket shift is applied *after* the formula evaluates and
*before* the lift to the common frequency. So `zs(fwill@surveys)` with `[-4]` z-scores over the
series' own history and only then slides — the plotted values are the unshifted z-scores, just
moved. A lag never renormalizes μ/σ.

**The shift moves the index, so a lagged line runs PAST the raw data.** `fwill` ends 2026-Q3, so
lagged 4 quarters it plots through **2027-Q3** — and the render window extends to match. This is
deliberate: shifting values inside a fixed index would instead throw away the newest 4 readings,
which on a leading-indicator chart are the whole point. The other series still ends at its own
last print, so the two lines legitimately stop at different dates.

Set one by hand, or correct it in the resolution round-trip with `N:lag=[-4]`:

```python
# from src/ — z-score, then lag 4 quarters
r = next(x for x in led.rows if x['chart_id'] == '2026-08-03_636a683648_2')
r['series'][0]['lag'] = '[-4]'                 # '[+4]' would LEAD instead
led.save()
BC.render_row(r, f"../data/backfill_2026-08-03/renders/{r['chart_id']}.png")
```

The legend gets the tag appended (`… (%) [-4]`) unless the confirmed label already says so —
`"… (%. lagged by 4qtrs)"` is left alone rather than becoming `"… (%. lagged by 4qtrs) [-4]"`.
A label that is *silent* on the shift always gets tagged: a lagged line must never plot without
saying it's lagged. A present-but-unparseable tag **raises** rather than plotting unlagged.

---

## Setup & auth

### Environment

Windows + **Git Bash** with the shared venv at `C:\Users\asingh\envs\shared-3.10`
auto-activated (see `.cursor/rules/05-environment.mdc`). Verify at session start:

```bash
echo "shell=$0" && which python && \
  python -c "import os; print('FRED_KEY_OK=', bool(os.environ.get('FRED_API_KEY')))" && \
  python -c "import os; print('MSGRAPH_OK=', bool(os.environ.get('AS_MSGRAPH_CLIENT_SECRET')))" && \
  python -c "import os; print('ANTHROPIC_OK=', bool(os.environ.get('ANTHROPIC_API_KEY')))"
```

Config (keys/secrets) is sourced from `econ-templates/config/.env` via `.bashrc`.

### Mail.Read (email ingestion)

Uses the existing **email app** (`AS_MSGRAPH_*`), which already holds `Mail.ReadWrite`
(Application, admin-consented) — a superset of `Mail.Read`. No extra grant needed;
`ingest` still 403-checks at runtime.

### Teams (the approval loop)

Teams messaging at runtime is **delegated** (app-only credentials cannot post chat
messages), via a dedicated **`renmac-chart-bot`** app (public-client device-code):

```bash
python src/teams_auth.py keygen        # once: mint the Fernet key → AS_TEAMS_TOKEN_KEY
python src/teams_auth.py consent       # once: device-code sign-in → cached refresh token
python src/teams_auth.py list-chats    # grab the self-chat id → AS_TEAMS_CHAT_ID
python src/teams_auth.py check         # verify the token refreshes silently
```

- **One-time admin grant:** RenMac disables tenant-wide user consent, so the four
  delegated scopes (`Chat.ReadWrite`, `ChatMessage.Send`, `ChatMessage.Read`, `User.Read`)
  need a **one-time** tenant admin grant (Entra `adminconsent`, or portal → API
  permissions → Grant admin consent). It's persistent, not per-sign-in.
- The refresh token is stored **Fernet-encrypted at rest** (`config/teams_token.bin`,
  gitignored); the key lives in `AS_TEAMS_TOKEN_KEY`. Daily use keeps it rolling.
  A dead/revoked token fails **loud** (out-of-band email alert), never a silent stall;
  re-mint with `teams_auth.py consent`.

Relevant env vars: `AS_TEAMS_CLIENT_ID`, `AS_TEAMS_TENANT_ID`, `AS_TEAMS_CHAT_ID`,
`AS_TEAMS_TOKEN_KEY`; `AS_DAILY_MAILBOX` / `AS_SENDER_EMAIL`; `ANTHROPIC_API_KEY`.

### Where files land

| What | Path |
|---|---|
| Bloomberg inbox | `inbox_bloomberg/MMDDYYYY/*.docx` |
| Backfill ledger (per date) | `data/ledger_backfill_<date>.csv` |
| Backfill assets / renders | `data/backfill_<date>/assets/` · `data/backfill_<date>/renders/` |
| Production ledger | `outputs/ledger.csv` (unreachable from `--reprocess`) |
| Production renders | `MMDDYYYY/<release_slug>/<chart>.png` |
| Clarified-knowledge store | `knowledge_repo/clarified-knowledge/` (`learned_descriptors.json`, `trusted_tickers.json`, `native_ma.json`, `legend_labels.json`) |
| Secrets | `econ-templates/config/.env`; Teams token `config/teams_token.bin` |

---

## Quick reference

```bash
# ── run ──────────────────────────────────────────────────────────────────────
python scripts/run_daily.py --date YYYY-MM-DD --source all --approve   # full chain
python scripts/run_daily.py --date YYYY-MM-DD                          # ingest+seed only
python scripts/run_daily.py --date YYYY-MM-DD --reprocess --approve    # clean redo
python scripts/run_daily.py --date YYYY-MM-DD --retitle <chart_id>     # edit title/subtitle/st_force, re-render
python scripts/run_daily.py --date YYYY-MM-DD --relegend <chart_id>    # edit a legend label, re-render
# no-commentary / title-less: reply `[chart_id] none` in the title round-trip (no flag)
python scripts/inspect_date.py --date YYYY-MM-DD --source all          # dry pre-inspect
python scripts/run_daily.py --selftest                                 # offline proof

# ── auth (one-time) ──────────────────────────────────────────────────────────
python src/teams_auth.py keygen | consent | list-chats | check

# ── Teams replies ────────────────────────────────────────────────────────────
# Stage 1 (parked ask)     [id]  then per line:  #<n> code@db   (#n zero-indexed)
# Stage 2 (resolution)     [id] approve | [id] N=code@db | [id] N:axis=R
#                          [id] N:transform=… | [id] legendN=… | [id] skip   (N one-indexed)
# Stage 3 (title)          [id] 1 | [id] 2 | [id] title=… subtitle=… [st_force=true] | [id] none
# Bulk                     approve all        (no [id]; fully-clean only; per-chart wins)
```

**Numbering:** Stage 1 = `#0`/`#1` (with `#`, zero-indexed) · Stages 2/3 = `1`/`2`
(no `#`, one-indexed).
