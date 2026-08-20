Read .cursor/rules/, this project's prompt.md, and all referenced @scripts.

Produce a PLAN in plan.md with:

1. Data: every series, source (Haver/BBG/MB/FRED), ticker/mnemonic,
   frequency, span
   - For any Haver series whose mnemonic isn't already given in
     prompt.md or the confirmed-mnemonics list, resolve it via the
     haver-metadata MCP before writing it into the plan: search_series
     to find candidates by description, then get_series to confirm the
     exact code@database ticker, frequency, and date span. Cite the
     ticker from get_series — never guess a mnemonic or invent a
     database suffix. If the MCP isn't available in this workspace,
     mark the ticker TBD and ask.
2. Transforms: log/diff/SA, with method choice justified
3. Model: equations or pseudocode for the core spec
4. Outputs: figures, tables, what each shows
5. Validation: replication targets, sanity checks, expected ranges
6. Open questions: anything ambiguous in the spec

After writing plan.md, STOP. Wait for me to reply "approved" before any
implementation, data pull, or pip install.

═══════════════════════════════════════════════════════════════
PROJECT SPECIFICS
═══════════════════════════════════════════════════════════════

§1. PROJECT TYPE

Automation / operational tooling (release-day-adjacent pipeline).
Rationale: this builds a repeatable Python pipeline that ingests Neil's
"for the daily" emails, extracts raw Haver chart screenshots, and
reconstructs them as RenMac-styled PNGs with a human-in-the-loop
title/subtitle approval step. There is no published paper to hit
numerical targets against (not a replication) and no new econometric
model (not new analysis) — the deliverable is a production pipeline with
a state ledger and a Teams approval loop. Treat it with the operational-
tooling discipline: scraper-first robustness, idempotent re-runs, and a
mandatory pre-shipping shakedown on a low-stakes day.

§2. PROJECT NAME

Proposed: 2026_daily_chart_replicator
Alternates if you prefer: 2026_haver_chart_rebuilder,
2026_daily_chart_pipeline. Scaffold with `newproj` at
C:\Users\asingh\new_work\2026_daily_chart_replicator\.

§3. SOURCE MATERIALS (read in this order)

1. notes/functions.htm — Haver formula-bar function reference. This is
   the authoritative transform vocabulary. Build the transform-parser
   map directly from it (see §4). Confirmed tokens present:
   DIFF/DIFA/DIFV (+L log-diff, +C compound, +n period variants),
   YRYR / YRYRL, MOVV / MOVA / MOVT (+C), INDEX(X,YYYY=100), YTD/DYTD,
   ZS (z-score), NA, FX (currency), plus Seasonal Adjustment, cumulative
   sum, HP filter, abs, ln, SetNA. The example movv(yryr%(X),3) decodes
   as MOVV(YRYR(X),3) = 3-period moving average of YoY % change.
   SEPARATE FROM functions.htm: a bracket suffix on a SERIES LABEL such
   as "...Not Started [-5]" is a LAG operator, not a Haver function. It
   means that series is shifted by |n| periods (months for a monthly
   series, quarters for quarterly, etc.), applied AFTER the math
   transforms. It is per-series — in a multi-series chart each label can
   carry its own bracket or none. See §4d/§4f.
2. notes/ sample assets — sample1_commentary.txt + sample1_chart1.png,
   sample1_chart2.png …; sample2.docx (commentary + embedded charts) …
   These are the classification/extraction calibration set. The classifier
   in §4 must be tuned and demoed against these before any real email.
3. econ-templates/scripts/send_via_graph.py — reuse its MS Graph auth /
   token pattern for both Mail.Read and the Teams post. Do not re-roll auth.
4. Haver MCP (haver-mcp / haver-metadata) — confirm it is wired into THIS
   workspace's MCP config before relying on it for ticker resolution.
5. RenMac chart-style module in econ-templates (palette + source-box +
   title rules) — the renderer must consume this, not redefine colors.
6. MS Graph reference for the chosen Teams mechanism (chat vs channel
   message; whether an Adaptive Card or a plain numbered/free-text-reply
   prompt — see §7 risk on response capture).

Add to sandbox.json: graph.microsoft.com and login.microsoftonline.com
(if not already allowed), plus api.anthropic.com for the title LLM.

§4. KEY METHODOLOGY / PIPELINE

No econometric model. A multi-stage vision + ETL + reconstruction
pipeline, run by one command, idempotent across runs:

  (a) Ingest — MS Graph: list messages where sender is Neil and subject
      matches /for (the |today's )?daily/i (case-insensitive). Skip any
      message_id already marked done in the CSV ledger (§ below).
  (b) Asset extraction — pull every image from the email: inline HTML
      cid: images, base64 blobs, and file attachments; AND from any
      attached .docx, extract embedded media (the common case is a single
      docx holding both commentary text and charts).
  (c) Asset classification (THE CRUX) — for each image, decide: raw Haver
      chart screenshot (PROCESS) vs already-formatted RenMac chart /
      table / Bloomberg|Macrobond|AI-generated analysis (SKIP). See §7.
  (d) Chart reading — for each raw Haver chart, read via vision (recommend
      Claude/Opus vision over OCR for the structured fields): series
      description(s), the Haver transform expression(s) e.g.
      movv(yryr%(PCUSLFE@USECON),3), axis assignment (shared y-axis vs
      LHS/RHS dual), the visible sample range, AND any per-series bracket
      lag in the label, e.g. "Not Started [-5]" ⇒ lag that series by 5
      periods. Capture the lag as a signed integer per series (default 0).
  (e) Ticker resolution — for each series, resolve via Haver MCP
      (search_series → get_series). CRITICAL: pin SA vs NSA from the
      description; the wrong one renders a plausible-but-wrong chart.
      If the MCP cannot resolve a description to exactly one series — no
      confident match, ambiguous across databases, or SA/NSA undetermined
      — DO NOT guess and DO NOT proceed for that chart. Post a message to
      the Teams channel stating it could not identify the ticker(s),
      including the read series description and any candidate tickers, and
      WAIT for me to reply with the correct code@database before
      continuing. This is a hard pause, the same shape as the title gate.
  (f) Pull + transform — pull from Haver, then for each series apply, in
      order: (1) the parsed math transforms per functions.htm, (2) the
      bracket lag (shift by |n| periods). Because the lag consumes history,
      the pull lookback buffer = transform window + |lag|: a YoY series
      shown from 2005 with a [-5] lag needs data from 2004 (YoY's 12m)
      PLUS 5 more months, so the displayed start still has a value after
      shifting. Align mixed-frequency series by linear interpolation
      low→high. Linearly interpolate Oct-2025 shutdown gaps for affected
      series. Lag is per-series, so two series on one chart can have
      different shifts.
  (g) Title/subtitle proposal — Claude/Opus drafts a title + ≥1 alternate
      per chart from commentary context (≤11 words, sentence case, no
      colon, first word capitalized) and a subtitle when warranted
      (release group e.g. "Surveys"; transformation e.g. "Z-score",
      "3m % chg, saar"; sample range used for the transform). If a series
      carries a lag, surface it where the original chart does (e.g. in the
      legend label as "... [-5]") rather than dropping it silently.
  (h) Human approval via MS Teams — present per-chart title options +
      alternates + a custom-title path, and a custom-subtitle path.
      Generation waits on your selection. (Architecture: see §7 + Part C.)
  (i) Render — RenMac PNG, 8.5" wide × 6.1" tall, palette maroon #621909
      primary / navy #1B2A4A secondary, title/subtitle, source box:
      "Renaissance Macro Research, Haver Analytics". Legend names from
      series descriptions, shortened where long without losing key info;
      preserve any lag tag in the legend.
  (j) Output + ledger — write to
      <project>/MMDDYYYY/<release_slug>/<chart>.png and mark the email
      processed in the CSV.

§5. DATA DEPENDENCIES

The economic series are dynamic — resolved per-email at runtime via Haver
MCP, not fixed upfront. The fixed dependencies are the access surfaces:

Dependency                     | Source/Mech         | Access risk
-------------------------------|---------------------|------------
Neil's "for the daily" emails  | MS Graph Mail.Read  | MEDIUM (mailbox + scope)
Teams approval round-trip      | MS Graph (Teams)    | HIGH (send scope + response capture, §7)
Embedded chart images          | email body + docx   | MEDIUM (EMF/WMF risk, §7)
Ticker resolution              | Haver MCP           | LOW (you built it; confirm wired)
Series data pull               | Haver DLX (py pkg)  | LOW
Title/subtitle generation      | Claude/Opus API     | LOW (confirm model string, §6)
Chart-field reading            | vision/OCR          | MEDIUM–HIGH (silent-failure prone)

Illustrative runtime series (from your example): Core PCE
PCUSLFE@USECON (verify SA), monthly. Actual series vary per email and
must each pass get_series confirmation before rendering.

§6. TOOLBOX / LANGUAGE NOTES

Pure Python (shared-3.10). No MATLAB, no R. Likely needs beyond defaults:
  - python-docx and/or zipfile (extract word/media from .docx)
  - Pillow (image handling); a converter for EMF/WMF (LibreOffice headless
    or Inkscape) — Office frequently stores pasted screenshots as EMF/WMF,
    which Pillow cannot open directly. Confirm one is available.
  - matplotlib (renderer, fed by the econ-templates RenMac style module)
  - pandas, the Haver python package, anthropic SDK
  - MS Graph: reuse send_via_graph.py's stack (msal/requests) — no new auth
  - Adaptive Cards are just JSON; no package needed
X-13: NOT expected at runtime — pull the SA mnemonic from Haver directly
rather than re-running SA. Only if a chart shows a transform requiring SA
on an NSA pull do you need setup_x13() pre-flight; flag if that case arises.

§7. KNOWN RISKS

1. Asset classification is the whole ballgame. Distinguishing Neil's raw
   Haver screenshots from already-formatted RenMac charts, tables, and
   Bloomberg/Macrobond/AI screenshots must be robust both ways: a false
   positive re-renders a chart Neil already finished; a false negative
   drops a real one. Calibrate on notes/ samples. Likely strongest signal:
   finished RenMac charts carry the source box / RenMac styling and a
   formatted title; raw Haver charts do not. Confirm that heuristic against
   the samples and keep the decision visible in the Teams step.
2. Vision extraction fidelity / silent failures. Misreading a ticker,
   transform, or SA/NSA flag yields a wrong-but-plausible chart. Mitigate:
   cross-check every read ticker against Haver MCP get_series; if the
   description doesn't map to exactly one series, do NOT guess — message me
   via the Teams channel that the ticker couldn't be identified (with the
   read description + any candidates) and block on my reply with the
   correct code@database before continuing. Treat an unresolved ticker as a
   hard pause, identical in spirit to the title-approval gate; resume that
   chart only after I supply the ticker.
3. Sample-range / lag off-by-one. Two coupled traps. (a) The transform
   lookback buffer must extend transform-window + |lag| before the display
   start (§4f); validate the first plotted date equals the chart's start,
   not the pull's. (b) Bracket-lag SIGN and ORDER: confirm against the
   sample that [-5] shifts the series so its earlier values align to later
   dates (a leading-indicator alignment), that the shift is applied AFTER
   the math transform, and that the unit follows series frequency. Wrong
   sign or wrong order is a textbook plausible-but-wrong failure — assert
   it visibly (e.g. log the shift applied) and eyeball against the original.
4. Axis misread. Treating a dual LHS/RHS chart as shared-axis (or vice
   versa) distorts everything. Read and assert axis assignment explicitly.
5. Mixed-frequency + shutdown interpolation must be VISIBLE, never a silent
   fallback that masks missing data. Log every interpolated span.
6. EMF/WMF docx images (§6) — handle or the extractor silently misses
   pasted charts.
7. MS Teams response capture is a real architectural constraint, not a
   detail. There are now TWO kinds of round-trip — title/subtitle approval
   AND the ticker-resolution fallback (§4e/§7.2) — and the ticker fallback
   requires a free-text reply (you typing a code@database), not just an
   option pick. An Adaptive Card posted via Graph has no trivial way to
   capture Action.Submit clicks back without a bot framework or Power
   Automate flow, and handles free text even less cleanly. The
   low-infrastructure path that serves both round-trips: post the prompt
   (numbered options for titles, free text for tickers), have you reply in
   the thread, then poll the chat and parse your reply. Decide sync-wait vs
   two-phase async and the capture mechanism at plan review (Part C Q1).
8. Ledger granularity / partial failure. If an email yields 4 charts and 2
   render before a crash — or a chart is parked awaiting a ticker reply —
   per-email "done" loses state. Recommend a per-chart status in the CSV.
   Proposed columns: message_id, received_date, received_time, sender,
   subject, release_slug, chart_index, resolved_ticker, series_lag,
   proposed_title, chosen_title, subtitle, output_path, status (pending /
   awaiting_ticker / awaiting_approval / done / skipped), processed_at.

§8. STOP GATES (pause for my review at each)

G1. After plan.md draft — review data/classification approach, Teams
    architecture decision (covering BOTH title approval and ticker
    fallback), transform-parser map (incl. bracket-lag handling),
    ledger schema. No code, no pulls, no pip until I reply "approved".
G2. After the extraction + classification module — demo on notes/ samples:
    does it pick exactly the raw Haver charts and skip everything else
    (incl. EMF/WMF docx images)? Review before building reconstruction.
G3. Mid-build pseudocode signoff — the transform-parser (math transforms +
    per-series bracket lag, with sign/order asserted) + ticker-resolution
    (incl. the unresolved-ticker Teams fallback) + frequency/shutdown-
    interpolation logic, reviewed as pseudocode before heavy implementation.
G4. Intermediate validation gate (§7a equivalent) — one reconstructed PNG
    shown side-by-side against its original screenshot, including a chart
    that carries a bracket lag (use the housing [-5] sample), to confirm
    lag sign/order is right; I confirm fidelity before wiring the full loop.
G5. Teams loop demo — both round-trips end to end on one chart: the
    propose → post → response-capture → finalize title/subtitle loop, AND
    the unresolved-ticker prompt → my code@database reply → resume loop.
G6. Pre-commit — show staged files + commit message before any push; verify
    with git log --oneline -1.
G7. Pre-shipping shakedown (mandatory for operational tooling) — run the
    full pipeline against the notes/ sample set, then on one low-stakes real
    email (a between-releases day), before the first production morning.

§9. SUCCESS CRITERIA

Deliverable: a single-command Python pipeline that ingests new "for the
daily" emails from Neil, extracts and correctly classifies assets
(processing only raw Haver chart screenshots), reads each chart's series /
transforms / per-series bracket lag / axes / sample range, resolves tickers
via Haver MCP, pulls and transforms data with correct lookback (transform
window + lag) and interpolation, routes LLM-proposed titles/subtitles (with
alternates + custom override) through MS Teams for approval, and on approval
writes RenMac-styled PNGs (8.5"×6.1", palette, title rules, subtitle, source
box) to MMDDYYYY/<release_slug>/, while maintaining an idempotent CSV ledger.

Acceptance on the notes/ sample set: (a) every non-raw-Haver asset is
skipped; (b) each raw Haver chart reconstructs with correct series, SA/NSA,
transform, bracket lag (correct sign and order), axis assignment, and sample
range; (c) PNGs are visually faithful to the originals; (d) titles/subtitles
match the approved choices and lag tags are preserved in legends; (e) when a
ticker cannot be resolved, the pipeline messages me on Teams, parks that
chart (status awaiting_ticker), and resumes correctly once I reply with the
code@database; (f) a second run processes zero already-done emails.

═══════════════════════════════════════════════════════════════
INSTRUCTIONS FOR THE CURSOR AGENT
═══════════════════════════════════════════════════════════════

Per the kickoff prompt above:

1. Read all source materials in §3, starting with notes/functions.htm and
   the notes/ sample commentaries + charts.
2. Confirm the Haver MCP is available in this workspace; if so, do NOT pull
   data yet — only confirm you can reach search_series/get_series.
3. Build the transform-parser map from functions.htm and state, in plan.md,
   how each Haver token (DIFF/DIFA/YRYR/MOVV/INDEX/ZS/… + n and L/C
   variants) maps to a Python transform, AND how a per-series bracket lag
   like [-5] is parsed and applied after the math transforms.
4. Surface any contradictions between this brief and the sample assets
   before drafting (e.g., a sample chart whose styling makes the
   raw-vs-finished classification ambiguous, or a bracket whose sign
   convention you can't confirm from the image alone).
5. Draft plan.md following the canonical §0–§12 structure, including the
   ledger schema (§7.8), the unresolved-ticker Teams fallback (§4e), the
   bracket-lag handling (§4d/§4f), and the Teams-architecture decision as an
   explicit §6 open question.
6. STOP at G1 (plan signoff). Don't pull data, don't write code, don't pip
   install, don't run anything yet.

═══════════════════════════════════════════════════════════════
OPEN QUESTIONS BEFORE PLAN
═══════════════════════════════════════════════════════════════

1. Teams architecture — sync-wait (script posts and polls the thread for
   your reply, blocking) or two-phase async (run 1 proposes, you reply, run
   2 finalizes)? The mechanism must serve BOTH round-trips: title/subtitle
   selection (numbered options) AND the unresolved-ticker fallback (free-
   text code@database reply). That dual need favors a numbered/free-text
   reply-and-poll path over pure Adaptive Card buttons — confirm.
2. Pull-before or pull-after approval? Subtitles need the actual transform
   and sample range, which argues for pulling/parsing before the Teams gate
   and rendering only after. Note this sits after ticker resolution, so an
   unresolved-ticker pause precedes both. Confirm the ordering.
3. Email scope — filter on sender == ndutta@renmac.com AND subject match, or
   subject alone? Which mailbox — your inbox or a shared mailbox?
4. Classification signal — is "finished RenMac charts always have a source
   box / raw Haver screenshots never do" a reliable discriminator on your
   real emails? Are there examples of each in notes/ to calibrate against?
5. release_slug — who names the output subfolder (core_pce, umich_sentiment,
   richmond_fed_mfg…)? LLM-proposed and confirmed in the same Teams
   round-trip, or rule-based from the commentary header?
6. Bracket-lag sign convention — confirm [-n] means the series is shifted so
   its values appear n periods later (leading-indicator alignment), applied
   after the transform, in the series' own frequency. If any chart ever uses
   [+n] or a non-month unit, flag how you want it handled.
7. Opus model string — confirm the exact API model id to wire (you wrote
   "Opus 4.7"; verify against the current available model id) and that the
   firm Anthropic API key is already in env.