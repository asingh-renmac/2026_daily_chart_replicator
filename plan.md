# Plan — 2026_daily_chart_replicator

> Status: **G1 approved.** §4 re-grounded against the Haver reference pages
> (all `[verify]` flags removed). **G2 (extraction + classification) in
> progress.** Per the approval, the parser is **not** implemented until the
> re-grounded §4 is signed off at **G3**.
>
> **(Q3) mailbox** and **(Q7) `ANTHROPIC_API_KEY`** are both RESOLVED: mailbox =
> `asingh@renmac.com` (Mail.Read confirmed via the email app's `Mail.ReadWrite`);
> `ANTHROPIC_API_KEY` is set (vision classifier + title generation run live). The
> remaining major UNBUILT component is the **chart-field read (G8, §12)** — the
> Opus-vision read of tickers/transforms off a screenshot; the dated and Teams
> runs deliberately bypass it (hand-typed tickers) and validate none of it.

---

## §0. One-line summary

A single-command, idempotent Python pipeline that ingests Neil's "for the
daily" emails, extracts every embedded image, **classifies** which are raw
Haver chart screenshots (vs. already-finished RenMac / Macrobond / Bloomberg /
text assets), **reads** each raw Haver chart's series + transforms + per-series
bracket lag + axis assignment + sample range via vision, **resolves** tickers
through the Haver metadata MCP, **pulls + transforms** the data, routes
LLM-proposed titles/subtitles (and any unresolved-ticker questions) through an
**MS Teams** human-approval loop, and renders RenMac-styled PNGs to
`MMDDYYYY/<release_slug>/`, tracking per-chart state in a CSV ledger.

Operational tooling, not a replication: success = robust classification,
faithful reconstruction, idempotent re-runs (§8, §9).

---

## §1. What I read + what I found (calibration)

### 1.1 Source materials (all present)

| Source | Notes |
|---|---|
| `notes/functions.htm` | Haver function reference. **Re-grounded §4 against it** (findings in §4.0). |
| `notes/transformations.htm` | Two-variable arithmetic (`+ − × ÷`), with a frequency-convert prompt when mixing M+Q. |
| `notes/nest-functions.htm` | Nesting = **inner→outer** application order. Confirms §4 composition. |
| `notes/aggregation.htm` | High→low aggregation uses the series' type (avg / sum / EOP). |
| `notes/interpolation.htm` | "**utilizing linear interpolation**" → confirms §4.3 is Haver's actual method, not an assumption. |
| `notes/recession-shading.htm` | Default = **U.S. NBER** peaks/troughs; shade peak→trough. Drives §7.1. |
| `notes/expressionsetc.htm` | Parent reference page. |
| `notes/sample{1..9}_*` | Calibration set: **26 images** across 9 samples (§1.3). |
| `econ-templates/scripts/send_via_graph.py` | Reuse `AS_MSGRAPH_*` + `ClientSecretCredential` auth. |
| `econ-templates/charts/renmac_chart_style.py` | Renderer consumes this (colors, `renmac_style`, `add_recession_shading`, source box). |
| `econ-templates/data/haver_pull.py` | Pull pattern: bare ticker + `database=`, `QS`, parquet cache. |
| Haver MCP `user-haver-metadata` | **Wired & reachable** (§1.2). |

### 1.2 Haver MCP confirmed reachable (no data pulled)

- `get_series("PCUSLFE@USECON")` → **`CPI-U: All Items Less Food and Energy
  (SA, 1982-84=100)`**, M, 1957-01→2026-05, `sa=sa`.
- `search_series("PCE excluding food and energy chain price index", usecon, M)`
  → `jcbm@usecon`, `jcmxfem@usecon` (Market-Based core PCE), etc.

**Contradiction C1 (kept):** the brief's "Core PCE = `PCUSLFE@USECON`" is
**Core CPI**, not Core PCE — exactly the plausible-but-wrong-mnemonic trap. No
tickers are hard-coded; every runtime series is `get_series`-verified with
SA/NSA pinned before render.

### 1.3 Classification ground-truth (the calibration set for G2)

Discriminator (validated on all 26 images): **attribution text + title/palette
style**, NOT presence of a source box (raw Haver charts also have one).

| Signal | RAW HAVER → **PROCESS** | FINISHED / OTHER → **SKIP** |
|---|---|---|
| Source text | `…/**Haver Analytics**` | `**Renaissance Macro Research**, **Macrobond**/BLS…` |
| Title | **centered**, blue/teal; often the raw formula | **bold black, left-aligned** plain-English headline |
| Palette | Haver navy `#1f4e79` + light teal (occasional red dashed 3rd) | RenMac maroon `#621909` / Macrobond multi-color |
| Cues | `%`/`SCALAR`/`US$`/`INDEX` axis tags; ←/→ dual-axis arrows; `r =` box | bottom legend, plain axis labels |

**Ground-truth labels (26 images):**

| Asset | Verdict | Why |
|---|---|---|
| sample1_chart1/2/3 | PROCESS | Haver |
| sample2_chart1/2/3 | PROCESS | Haver (PCE chain PI; z-scores; wage tracker dual-axis) |
| sample3 img3 (JOLTS Quits `[-4]` vs ECI) | PROCESS | Haver + **bracket lag** (§4.4); **G4 lag case** |
| sample4 img1 (ISM Prices / Supplier Del.) | PROCESS | Haver |
| sample5 img1/2 (Philly Fed) | PROCESS | Haver |
| sample6_1/2/3 | PROCESS | Haver |
| sample7_1/2/3 | PROCESS | Haver |
| sample8.docx img1/2/3 (housing: units authorized vs starts) | PROCESS | Haver; img2 is a `movv(·,2)` dual-axis. **No `[-5]` lag present** (C3) |
| sample9_chart1/2 (TX service, Richmond) | PROCESS | Haver (`r=0.74`, `r=0.67`); same "Sluggish services" commentary as sample8 |
| **sample3 img1** (Job-openings bars) | **SKIP** | RenMac/Macrobond, maroon bars, "Renaissance Macro Research, Macrobond, BLS" |
| **sample3 img2** (5-line "overdone") | **SKIP** | Macrobond multi-color |
| **sample4 img2** (ISM PMI model) | **SKIP** | RenMac maroon-vs-gray, "RenMac PMI Model" |
| **sample4 img3** ("WHAT RESPONDENTS ARE SAYING") | **SKIP** | Text screenshot (navy text, not a chart) |
| **sample4 img4** (Inventory investment) | **SKIP** | Macrobond, "[m.a. 6 quarters, lead 4 obs]" |

(**26 images: 21 PROCESS, 5 SKIP.** Note the folder changed since the first
pass — `sample8` is now a *docx* of 3 housing charts; the TX/Richmond loose PNGs
are `sample9`. False-negatives are the costly error; the verdict is surfaced in
the Teams step.)

**G2 result — FINAL: 26/26 with vision tie-breaker (live `claude-opus-4-8`).**
`scripts/run_g2_demo.py` extracts + validates all 26 images (fail-loud). The
deterministic pre-filter alone scores **25/26**; the lone miss is `sample4 img3`
— a navy-**text** screenshot read as `raw_haver` (a false *positive*, never a
dropped chart), palette-indistinguishable from a navy line. Running
`run_g2_demo.py --vision` flips it to **`skip`** (vision: *"text screenshot of
survey quotes … no plotted line series or Haver attribution"*) for **26/26
final**. The pre-filter's only hard SKIPs are positive non-Haver signals
(maroon / green / orange / dense black text / no series), so it never turns a
real raw-Haver chart into a SKIP; the vision step only adjudicates the
pre-filter's `raw_haver`/`unsure` calls. Even absent vision, that text frame
would fail chart-reading/ticker resolution and route to Teams — never render
silently wrong.

**Contradiction C2 (per your steer):** every embedded image in `notes/` is PNG
— no EMF/WMF to calibrate against. We keep a **defensive format check** and
**fail loud** (route to Teams: "couldn't read image format `.emf` in <doc>");
we do **not** over-invest in an EMF/WMF converter. Never silently skip an image.

**Contradiction C3 (confirmed):** no housing `[-5]` chart exists; **G4 uses
JOLTS Quits `[-4]`** (sample3 img3).

---

## §2. Architecture / pipeline overview

```
run_daily()                       # single, idempotent entry point
 ├─ ingest         (a)  Graph: Neil's "for the daily" emails, skip done
 ├─ extract        (b)  images: inline cid:, base64, attachments, docx media; fail-loud on unreadable formats
 ├─ classify       (c)  raw-Haver? PROCESS ; else SKIP  (ledger: skipped)
 ├─ read_chart     (d)  vision → {series[], formula, axis map, sample range, per-series lag}
 ├─ resolve_ticker (e)  Haver MCP search→get ; not exactly 1 → Teams ASK, park (awaiting_ticker)
 ├─ pull+transform (f)  pull w/ lookback = transform_window+|lag| ; transforms ; lag ; interp
 ├─ propose_titles (g)  Opus drafts title + ≥1 alt + subtitle + release_slug
 ├─ approve        (h)  Teams: post options ; poll thread ; park (awaiting_approval)
 ├─ render         (i)  RenMac PNG 8.5"×6.1" + recession shading (no r-box) → MMDDYYYY/<release_slug>/<chart>.png
 └─ ledger         (j)  per-chart status row (idempotent)
```

Module layout (built incrementally; **`transforms.py` parser waits for G3**):

```
src/ pipeline.py ingest_graph.py extract_assets.py classify.py read_chart.py
     haver_resolve.py transforms.py haver_data.py titles.py render.py ledger.py config.py
data/ledger.csv   outputs/raw/   MMDDYYYY/<release_slug>/
```

---

## §3. Data

Series are **dynamic — resolved per email at runtime**. No hard-coded list.

### 3.1 Fixed access surfaces

| Dependency | Mechanism | Ticker/Field | Risk |
|---|---|---|---|
| Neil's emails | Graph **Mail.Read** (app-only, AS_MSGRAPH_*) — `src/ingest.py`, server-side `$filter` + paging | sender `ndutta@renmac.com` (server) AND subject `/\bdaily\b/i` (client refine — deliberately BROAD; sender already filtered to Neil, under-match = silent chart-drop, over-match = classifier no-op) | LOW — ingest BUILT + offline-proven; **Mail.Read CONFIRMED** (app has `Mail.ReadWrite`, live read=200); **Q3 mailbox = AS_DAILY_MAILBOX, default AS_SENDER_EMAIL** |
| Teams round-trip | Graph **delegated** (self-chat, `AS_TEAMS_CHAT_ID`); post + poll | `chats/{id}/messages`; scopes `Chat.ReadWrite`,`ChatMessage.Send`,`ChatMessage.Read`,`User.Read` (delegated; one-time **admin** grant — org disables user consent) | MED — `GraphTransport` built, G7 offline-proven; **BLOCKED on one-time admin consent** + chatId (§3.3, G7) |
| Embedded images | email + docx | cid/base64/attachment/`word/media/*` | MEDIUM (fail-loud, C2) |
| Ticker resolution | Haver MCP (authoring) + `resolve.confirm_ticker` (runtime, Haver DLX) | `search_series`→`get_series`; runtime existence check | LOW (wired + live) |
| Series pull | Haver DLX | `code` + `database=` | LOW |
| Title/subtitle/slug | Anthropic Opus `claude-opus-4-8` (`propose.propose_titles`) | key live (Q7) | LOW (wired + live) |
| Chart-field read (tickers/transforms/axes/lag/sample) | Opus vision (`chartspec.py`) + DLX/MCP resolver (`resolve.py`) | key live (Q7) | **ENGINE BUILT + LIVE-PROVEN — G8a read PARTIAL-SIGNED, G8b resolver offline-proven, G8c-v2 descriptor-relevance gate + multi-query search recall + confirm_all mode (g8b 29/29). LIVE texas RESOLVED 2/2 (DFBACTS/DBACTS@SURVEYS) through live search→get→DLX-confirm with seed bypassed.** confirm_all HELD at posted resolution summary (awaiting Aman's approve); NOT YET WIRED into `run_daily` seeding (gated on the texas E2E passing review) |

### 3.2 Runtime resolution discipline (per series)

1. If the title shows the **raw formula**, mnemonics are visible — still
   `get_series` to confirm descriptor/freq/SA/span.
2. If only the **descriptor** shows, `search_series` → inspect → `get_series`;
   pin **SA/NSA** + base year from the descriptor.
3. **Hard gate:** exactly one match with SA/NSA known, else **Teams ASK**
   (free-text `code@database`), park `awaiting_ticker`, resume on reply.
4. **Lookback** = `display_start − (transform_window + |lag|)` (§4.5).
5. Never auto-promote a newly confirmed mnemonic to the trusted list
   (`10-data-sources.mdc` human step).

### 3.3 Teams transport & delegated auth (decided)

App-only/client-credentials **cannot** post Teams messages at runtime (only
`Teamwork.Migrate.All`, import-only), so the loop runs **delegated as Aman**.

- **Surface:** a **self-chat (Aman ↔ Aman)** — a true "group chat with just me"
  isn't creatable in Teams. One fixed `chatId` in `AS_TEAMS_CHAT_ID`, grabbed via
  `python src/teams_auth.py list-chats` after consent (no Graph Explorer). A chat
  is a flat message list, so `GraphTransport` posts with a `[renmac-chart-bot]`
  tag and `replies()` returns later **non-bot** messages.
- **Same-sender disambiguation (echo-tag):** delegated, the bot posts AS Aman and
  Aman replies AS Aman — same author — so replies are bound to a chart by a
  **per-chart echo-tag**, not by author or by sole-pending (which would misroute
  on a multi-park day). Thread key = `…::<chart_id>`; each ask says "start your
  reply with `[chart_id]`"; `GraphTransport.replies` returns only chat messages
  carrying that `[chart_id]`. (This is the pre-production hardening, pulled into
  G7 because the second-park assertion makes G7 a genuine multi-park day.)
- **Auth:** `teams_auth.py`, MSAL **public-client device-code** (no client secret
  — a secret is the app-only model). One-time consent
  (`python src/teams_auth.py consent`) mints a refresh token; every run after
  refreshes silently (headless). Scopes `Chat.ReadWrite`, `ChatMessage.Send`,
  `ChatMessage.Read`, `User.Read` — user-consentable in general, but **RenMac
  disables tenant-wide user consent (org policy)**, so device-code `consent` needs a
  **one-time admin grant** (`.../adminconsent?client_id=...` or portal "Grant admin
  consent"); persistent tenant grant, not per-sign-in (§12 G7, blocked 2026-06-28).
  New dedicated app **renmac-chart-bot** (not the email app) with
  "Allow public client flows" = Yes; `AS_TEAMS_CLIENT_ID`/`AS_TEAMS_TENANT_ID` in
  `econ-templates/config/.env` (the same `.env` `teams_auth.py` loads for
  `AS_TEAMS_TOKEN_KEY` — one loader, no path split).
- **Secret discipline:** refresh token stored **Fernet-encrypted at rest**
  (`econ-templates/config/teams_token.bin`, 0600, gitignored), key in
  `AS_TEAMS_TOKEN_KEY` (`.env`). Treated as sensitive as the Graph creds — it
  acts as Aman and reads his chats.
- **Fail-loud, not stall:** a dead/expired/revoked token makes
  `get_access_token()` raise `TeamsAuthError`; the pipeline calls
  `teams_auth.alert_reauth_needed()` → out-of-band email via the existing
  Mail.Send creds (`AS_OPS_ALERT_EMAIL`/`AS_SENDER_EMAIL`) → then fails loud, so
  approvals never silently stop. **Re-mint = one command:**
  `python src/teams_auth.py consent`. Daily cadence keeps the token rolling
  (90-day inactivity won't bite); a RenMac password/MFA/CA-policy change
  eventually invalidates it → re-consent.
- **Boundary kept:** `approval.py` depends only on the `Transport` ABC;
  `StubTransport`↔`GraphTransport` are swappable without touching it.

### 3.4 Date-scoped ingestion (testing/backfill) — `scripts/run_daily.py`

Replays a specific past Eastern day instead of waiting for a fresh email; also the
first time Mail.Read + extraction + the raw-Haver classifier hit a REAL inbox.

- **CLI:** `python scripts/run_daily.py --date 2026-06-25` (+ `--vision`,
  `--reprocess`, `--approve`, `--selftest`). Mailbox = `--mailbox` / `AS_DAILY_MAILBOX`
  / `AS_SENDER_EMAIL`; sender default `ndutta@renmac.com`.
- **Timezone:** the day is Eastern wall-clock. `ingest.eastern_day_window` localizes
  both midnights in `ZoneInfo("America/New_York")` and converts to UTC — never a
  hard-coded offset. 2026-06-25 (EDT) → `ge 2026-06-25T04:00:00Z and lt
  2026-06-26T04:00:00Z`; a winter date resolves to 05:00Z automatically. DST-
  transition days span the correct 23/25h (two separately-localized midnights).
- **Server-side filter + paging:** Graph `$filter=from/emailAddress/address eq … and
  receivedDateTime ge … and receivedDateTime lt …`, `$top=50`, fully paged via
  `@odata.nextLink`. NOT pull-all-then-filter. Subject regex is refined CLIENT-side
  (Graph `$filter` can't regex/`contains` subject). **Two live gotchas fixed:**
  (1) NO `$orderby` — Graph 400s when ordering by `receivedDateTime` while filtering
  on a different property (`from`); we sort client-side. (2) attachments are fetched
  PER-MESSAGE (`/messages/{id}/attachments`), not `$expand`-ed — `$expand` on a
  filtered collection 400s and omits `contentBytes` for larger items (the daily docx).
  (3) attachments are fetched UNCONDITIONALLY — Graph sets `hasAttachments=False`
  when the ONLY attachments are INLINE (`cid:` body images), so gating the fetch on
  that flag silently drops inline charts. (Live 2026-06-25 07:32 email embedded
  `image001.png` inline with `hasAttachments=False`; it was missed until the gate
  was removed — happened to be a finished RenMac maroon chart→skip, but a raw-Haver
  inline chart would have vanished.) `cid:` images surface via `/attachments`
  (isInline=true, contentBytes present); the inline-HTML path only covers `data:` URIs.
- **Cross-folder dedup:** `/users/{mbx}/messages` spans ALL mail folders, so one
  delivered email recurs (Inbox + a rule's copy) with DIFFERENT Graph ids — the
  2026-06-25 inbox returned each email twice. Dedup on `internetMessageId` (the RFC
  Message-ID, stable across copies); it's also the ledger row key, so re-runs are
  idempotent. (Live 2026-06-25: 12 from sender → 6 unique → 3 matched subject.)
- **Resilient extraction:** inline data: images, file attachments, and .docx
  word/media/* are validated per-image; an unreadable one (EMF/WMF/corrupt) becomes
  an `IngestError` routed to Teams — never silently dropped, never aborts the batch.
- **Idempotency (DECIDED):** dated runs use a SEPARATE ledger namespace
  `data/ledger_backfill_<date>.csv`. Seeding is **insert-only** — a row whose
  `(message_id, chart_index)` already exists is left UNTOUCHED, so a re-run never
  clobbers a chart that has progressed to resolved/approved/etc. (an earlier
  upsert-overwrite bug *did* reset such rows to `awaiting_ticker`; fixed). `--reprocess`
  wipes the dated ledger for a clean repeatable redo, behind a **hard guard**: it
  refuses any path that isn't `data/ledger_backfill_*` — the production
  `outputs/ledger.csv` is physically unreachable from `run_daily`. Production is
  event-driven/idempotent and never force-reprocesses; all "redo a day" goes through
  this backfill lane — the two lanes cannot cross.
- **Downstream unchanged:** seeding emits one `awaiting_ticker` row per raw-Haver
  chart (linked to its saved screenshot via the new `asset_path` ledger field); the
  IDENTICAL G7 ticker+title state machine runs under `--approve`.
- **MAIL.READ CONFIRMED (pre-run):** the email app already holds `Mail.ReadWrite`
  (Application, admin-consented) — a superset of `Mail.Read`; live `$top=1` read of
  `asingh@renmac.com` = 200. No IT ticket. `ingest` still 403-checks at runtime.
- **KNOWN GAP — chart-field read is NOT built (gated G8, §12).** Auto-deriving each
  chart's tickers/transforms/axes/lag/sample-range from the screenshot is the CORE
  intelligence of the system and does not exist yet. Until G8, ingested charts park
  `awaiting_ticker` with a placeholder descriptor → the human hand-types `code@db` in
  Teams. `--approve` therefore posts one ticker ask per chart; default run is
  front-half only (ingest + report + seed).
- **What the 2026-06-25 run validates / does NOT:** it validates Mail.Read ingestion
  + docx/image extraction + the raw-Haver classifier + (under `--approve`) the Teams
  round-trip and ledger state machine. It validates **ZERO of the chart-reading
  intelligence (G8)** — no tickers/transforms are read from any screenshot. A green
  dated run does NOT imply the pipeline is near-complete; G8 remains the major
  unbuilt component.
- **Offline-proven:** `--selftest` asserts the window math (EDT 04:00Z / EST 05:00Z),
  the `$filter` string, extraction+classification on notes/ fixtures (inline + docx,
  raw_haver=2/skip=2), and the EMF→error path — all green, no network.

### 3.5 Bloomberg-chat source adapter — `src/folder_ingest.py`

Neil sometimes sends blurbs + raw-Haver charts over **Bloomberg chat**, not email.
Aman pastes each commentary's text + charts into a Word doc and drops it in a dated
folder; the pipeline ingests those as **another SOURCE into the SAME
extract→classify→(G8)→approve→render path** — NOT a new pipeline. The email ingester
yields `(commentary, images)` per message; the folder ingester yields the same per
`.docx`. Both return `ingest.EmailResult`; everything downstream is identical.

- **Layout:** `inbox_bloomberg/MMDDYYYY/<anything>.docx`. The DATE is the FOLDER
  (filenames are free-form/human — `jobless_claims.docx`, `ism.docx`); multiple
  commentaries that day = multiple docs in one folder. CLI keeps `--date YYYY-MM-DD`
  and maps it to the `MMDDYYYY` folder. Reuses the hardened docx media + commentary
  extraction (per-member validation, inline-attachment/EMF-WMF handling from §3.4).
- **CLI:** `--source {email|bloomberg|all}` (default `email`). `--source all` runs
  email + bloomberg for the date, both seeding the SAME `data/ledger_backfill_<date>.csv`.
- **Key (DECIDED) = content hash `bb:<sha1(file_bytes)>`** — NOT the filename
  (rename/collision-prone). Coexists with the email lane in one schema: the ledger key
  is `(message_id, chart_index)`, and `bb:…` is a disjoint string space from email's
  `<guid@domain>` internetMessageId, so the lanes share the CSV without crossover.
  Unchanged doc → same hash → insert-only seed skips it (idempotent). **Edited/corrected
  doc → new hash → re-processes**, and its prior-hash rows for that date that are still
  non-terminal are marked `skipped` (supersede reconcile, scoped by the `bb:` prefix so
  email rows are never touched) — no stale Teams asks linger.
- **Classification still runs:** every image in the doc goes through the raw-Haver
  classifier exactly like an email asset. Bloomberg-native charts, finished RenMac
  charts, and tables → SKIP; only raw-Haver charts seed. The doc's images are NOT
  blanket-trusted as charts-to-rebuild.
- **Offline-proven:** `--selftest` covers the bloomberg lane too — MMDDYYYY mapping,
  content-hash key, docx extract+classify (raw_haver=1/skip=2 on `sample3.docx`),
  source-aware report, and insert-only idempotency (re-run seeds 0, no dup rows); a
  separate check proves edited-doc supersede + the `--reprocess` production guard.
- **Same downstream:** seeded `awaiting_ticker` rows flow through the IDENTICAL ticker
  + title round-trips. Note the §G8 gap applies equally — a Bloomberg docx's charts
  also park for hand-typed `code@db` until chart-field read is built.

---

## §4. Transforms — re-grounded against the Haver reference pages

### 4.0 Findings report (what the files actually say)

Grounded in `functions.htm` (+ siblings). Where a formula is rendered as an
**image** (not machine-readable), I say so and pin it empirically at G4 rather
than guess.

- **DIFV** — `functions.htm` advanced table labels `DIFV%`/`DIFV`/`DIFVL` as
  **"Average % Change / Average Difference Change / Average Log Change."** So
  the three "modes" are **point-to-point (`DIFF`), average (`DIFV`), annualized
  (`DIFA`)**. **Now PINNED** from `advanced_fn_{1,2,3}.png`:
  `DIFV(X,n)=(x(t)−x(t−n))/n`, `DIFV%(X,n)=((x(t)/x(t−n))^(1/n)−1)·100`,
  `DIFVL(X,n)=ln(x(t)/x(t−n))/n·100` — i.e. the n-period change spread over `n`
  (arithmetic for `DIFV`, geometric per-period for `DIFV%`). Appears in none of
  the 26 samples but no longer needs empirical pinning.
- **MOVV vs MOVA vs MOVT** — authoritative:
  `MOVV(X,n)` = **Moving Average** → `rolling(n).mean()`;
  `MOVT(X,n)` = **Moving Total** → `rolling(n).sum()`;
  `MOVA(X,n)` = **Annualized Moving Total** → `rolling(n).sum() × (freq_base/n)`.
  Each has a centered `…C` variant. Confirmed by samples: `movv(·,3)` =
  "3-month MovingAverage" (sample1_chart2), `movv(·,9)` = "9-month
  MovingAverage" (sample3 JOLTS).
- **`+C` / `+L` suffix algebra (CORRECTION to the brief)** — the name scheme is
  `DIF` + **mode**`{F=point-to-point, V=average, A=annualized}` + **type**`{''=
  difference, %=percent, L=log}` + optional **`C`=Centered**. So **`+L` = the
  LOG variant** (e.g. `DIFAL`, `YRYRL`), and **`+C` = CENTERED** ("Same formula
  … using centered values"), applied to both DIF* and MOV* families. The
  brief's §3.1 "`+C` compound" is **wrong** — *compound annual rate* is the
  **annualized (`A`) mode** (`DIFA%`), not a `C` suffix.
- **INDEX base** — `INDEX(X,YYYY=100)` (year), `INDEX(X,YYYYQ=100)` (quarter),
  `INDEX(X,YYYYMM=100)` (month). A **full-year base (`YYYY`) → divide by the
  AVERAGE of that calendar year** ×100; `YYYYMM`/`YYYYQ` → that specific
  period's value. Formula image `a13-edited.png`; the documented date forms
  confirm average-of-year vs specific-period. Validate at G4 (sample1_chart3
  PPI 1982=100; PCE 2017=100).
- **ZS window (Decision 4; Bug-A fix)** — formula image: **`ZS(X): Z = (X−μ)/σ`**;
  doc text: "calculated based on the time span currently displayed on the
  graph." ⇒ **μ, σ over the DISPLAY WINDOW `[visual_start, visual_end]` only**
  (never full history). **Nested ZS is SUPPORTED.** `z = (x−μ)/σ` is **returned
  over the FULL buffered range it received** — *not* truncated to `±EDGE_TOL`
  (Bug A: truncating to EDGE_TOL silently starved an outer op whose lookback
  exceeds EDGE_TOL, e.g. `movv(zs(x),6)` needs 5 lead points > 4). μ/σ are still
  window-only; only the returned support is full-buffer. The outer op consumes
  `z`, then `finish()` slices to the window. So `movv(zs(x),6)` = window-μ/σ
  z-score over the full buffer → 6-period MA → slice. Confirmed by `SCALAR`-axis
  samples (sample2_2, sample7_1).
- **YTD / DYTD — OUT OF SCOPE.** Not carried as planned formulas at all. If a
  chart ever uses `YTD`/`DYTD`, the parser **flags that chart to Teams** ("chart
  uses YTD/DYTD — not supported; confirm how to handle") and **parks it** (same
  route as an unresolved ticker — never a render). The generic `NeedPin` fallback
  is what catches them. Not in any sample.

**Formula pins (from `notes/basic_fn_{1,2}.png` + `advanced_fn_{1..5}.png`, read
via vision):** the n-period and difference-mode forms below are now PINNED, which
**closes the computed map** (only out-of-scope `YTD`/`DYTD` route to Teams).
Exact extracted formulas:
  - `DIFF(X,n)=x(t)−x(t−n)`; `DIFF%(X,n)=((x(t)/x(t−n))−1)·100`;
    `DIFFL(X,n)=ln(x(t)/x(t−n))·100`
  - **`DIFV(X,n)=(x(t)−x(t−n))/n`** (average *difference*);
    **`DIFV%(X,n)=((x(t)/x(t−n))^(1/n)−1)·100`** (geometric avg %);
    **`DIFVL(X,n)=ln(x(t)/x(t−n))/n·100`**
  - `DIFA(X,n)=(x(t)−x(t−n))·(npy/n)`; `DIFA%(X,n)=((x(t)/x(t−n))^(npy/n)−1)·100`;
    `DIFAL(X,n)=ln(x(t)/x(t−n))·(npy/n)·100`
  - `YRYR(X)=x(t)−x(t−npy)`; `YRYR%(X)=((x(t)/x(t−npy))−1)·100`;
    `YRYRL(X)=ln(x(t)/x(t−npy))·100`  *(npy=freq_base)*
  - **`MOVV(X,n)=Σⱼ₌₀ⁿ⁻¹ X(t−j)/n`** (simple mean);
    **`MOVT(X,n)=Σⱼ₌₀ⁿ⁻¹ X(t−j)`** (sum);
    **`MOVA(X,n)=Σⱼ₌₀ⁿ⁻¹ X(t−j)·(npy/n)`** — *confirms the prior
    `rolling(n).sum()*(freq_base/n)` guess exactly.*
  - **Centered `…C` (Decision 6):** every C-variant is documented as "*same
    formula … using centered values*" → the window is **centered on t** (uses
    ~n/2 future points) instead of trailing. `MOVVC=rolling(n,center=True).mean()`
    etc.; for `DIF*C(X,n)` the lookback/lookahead straddles t. The images give no
    special even-n rule, so the pin is **pandas `center=True` semantics** (even-n:
    right-of-center label) — confirm visually if one ever appears (none in the 26).
  - `INDEX(X,YYYY=100)=x(t)/x(base)·BaseValue` (Decision 5; base = avg-of-year for
    `YYYY`, the period value for `YYYYQ`/`YYYYMM`); `ZS(X)=(X−μ)/σ`;
    `NA2Z`/`Z2NA` as named.

Additional grounding now folded into §4.2–4.3:
- **Nesting** = inner→outer (documented example: 3-mo MA *then* %chg) → confirms
  `difa%(movv(X,3),3)` evaluates MA first.
- **Interpolation** is **linear** (verbatim) → §4.3 matches Haver exactly.
- **Aggregation** high→low uses per-series **avg/sum/EOP**; Haver prompts to
  convert frequency when mixing M+Q (transformations.htm).
- **SA** = concurrent X-13ARIMA-SEATS, all defaults, multiplicative unless ≤0,
  needs M/Q ≥36 obs → matches "pull SA mnemonic; only `setup_x13()` if a chart
  shows SA on an NSA pull."
- **FX**(`CODE@DB`, geo/iso) divides by the exchange rate (EOP rate if series
  EOP, AVG if AVG); not valid on indexes.

### 4.1 Expression grammar

Recursive-descent evaluator (not regex). `func := NAME['%']`; operands are a
mnemonic (`CODE` or `CODE@DB`) or a nested call; supports `+ − × ÷` and
parentheses; `INDEX(X,YYYY=100)` keyword arg; per-series bracket lag `…[-n]` on
the **label** (§4.4).

```
expr := term (('+'|'-') term)* ;  term := factor (('*'|'/') factor)*
factor := number | series | func '(' arglist ')' | '(' expr ')'
```

### 4.2 Function → pandas map (`freq_base = {M:12,Q:4,W:52,A:1}`)

| Token | pandas |
|---|---|
| `DIFF(X,n)` | `X - X.shift(n)` |
| `DIFF%(X,n)` | `(X/X.shift(n) - 1)*100` |
| `DIFFL(X,n)` | `(ln X - ln X.shift(n))*100` |
| `DIFA(X,n)` | `(X - X.shift(n))*(freq_base/n)` |
| `DIFA%(X,n)` | `((X/X.shift(n))**(freq_base/n) - 1)*100`  *(== renmac `pct_change_annualized`; sample1_1 3m/3m SAAR)* |
| `DIFAL(X,n)` | `(ln X - ln X.shift(n))*(freq_base/n)*100` |
| `DIFV(X,n)` | **PINNED** `(X - X.shift(n))/n` |
| `DIFV%(X,n)` | **PINNED** `((X/X.shift(n))**(1/n) - 1)*100` |
| `DIFVL(X,n)` | **PINNED** `(ln X - ln X.shift(n))/n*100` |
| `YRYR(X)` / `YRYR%(X)` / `YRYRL(X)` | `X-X.shift(fb)` / `(X/X.shift(fb)-1)*100` / `(lnX-lnX.shift(fb))*100`, `fb=freq_base` |
| `MOVV(X,n)` | `X.rolling(n).mean()` |
| `MOVT(X,n)` | `X.rolling(n).sum()` |
| `MOVA(X,n)` | **PINNED** `X.rolling(n).sum()*(freq_base/n)`  *(matches advanced_fn_4 `Σ X(t−j)·(npy/n)`)* |
| `…C` centered (Decision 6) | **PINNED** window centered on `t`: `rolling(n, center=True)` for MOV*; for DIF*C the `t±n/2` straddle. Recent ~n/2 pts NaN — absorbed by EDGE_TOL (Dec. 2). |
| `INDEX(X,YYYY=100)` | `X / X[year==YYYY].mean() * 100` (month/quarter base → that period's value); native-units only (Dec. 5, §4.6 NeedRebuffer) |
| `ZS(X)` (Decision 4) | μ,σ over **display window** `[visual_start,visual_end]`; return `(x-μ)/σ` over the **full buffered range** (Bug-A: not truncated); **nested OK** |
| `YTD/DYTD` | **out of scope** — not computed; chart → **Teams flag + park** (§4.3a), never rendered |
| `LN/ABS/NA2Z/Z2NA/SetNA` | `np.log` / `.abs()` / fillna(0) / 0→NaN / mask |
| `FX(X,cur)` | divide by FXRATES rate (EOP/AVG per agg type) |
| HP filter | `sm.tsa.filters.hpfilter` (λ by freq); cyclical variant available |

**Order:** evaluate parse tree inner→outer, then apply bracket lag **last**.

### 4.3 Mixed-frequency + shutdown interpolation (visible, never silent)

Method = **linear** interpolation low→high (matches Haver's documented linear
method, interpolation.htm); same for Oct-2025 shutdown gaps. **Every**
interpolated span is logged (series, range, n) and asserted — never a silent
fill.

**PINNED (Decision 1, REVISED) — transform-then-interpolate.** Each series'
math runs at its **NATIVE** frequency; interpolation to the shared grid happens
**AFTER**. Per series:
1. **Pull native**, buffered in **native** periods (§4.5).
2. **eval the transform at native freq** — native `n`, native `freq_base`. So
   `movv(quarterly_X, 3)` is a **3-QUARTER** MA on the native quarterly series
   (`shift`/`rolling` in quarters); `yryr%` on quarterly = `shift(4)`. **No
   rescaling to a common grid** — `n` stays native.
3. **Apply the bracket lag at native freq** (Q6: lag unit = native).
4. **Interpolate the transformed+lagged result UP to `common_freq`** (linear),
   only when a lower-freq series must co-exist with a higher-freq one (shared
   x-grid, or a BinOp mixing frequencies). **Never extrapolate** past the
   series' first/last native obs.
5. Slice to window; tolerance-band guard on the **final (common-grid)** series,
   tolerance = `EDGE_TOL[common_freq]`.

`common_freq` = the **highest** frequency among the chart's series; it governs
**only** the final shared grid + plotting, **not** the transforms. **BinOp across
frequencies:** transform each operand natively, interpolate the lower-freq operand
up to the higher operand's freq, **then** apply the op.

**G4 is a visual CONFIRM** of this fixed order against the JOLTS Quits `[-4]`
(monthly) vs **ECI Wages** (quarterly, YoY) chart (sample3 img3) — not an open
switch. Every interpolated span is logged + asserted (§8.5).

### 4.3a Loud-pin policy (no silently-guessed formulas)

**The pinned map is now CLOSED.** The 7 formula screenshots (`basic_fn_*`,
`advanced_fn_*`) pinned `DIFV*`, `MOVA`, all `…C` centered variants, and nested
`ZS` (§4.0/§4.2/§4.6). Everything the parser will **COMPUTE** is a known formula:
`DIFF*`/`DIFA*`/`DIFV*`/`YRYR*`/`MOVV`/`MOVA`/`MOVT`/`…C`/`ZS` (outer or nested)/
native-`INDEX`/`LN`/`ABS`/`NA2Z`/`Z2NA`/`FX`/`HP`.

Anything **not** in that map routes to Teams instead of rendering:
- **`YTD` / `DYTD`** — **out of scope** (Decision: barely used). The chart is
  flagged to Teams ("chart uses YTD/DYTD — not supported; confirm how to handle")
  and **parked**, same route as an unresolved ticker.
- **Generic `NeedPin`** — the fallback that catches `YTD`/`DYTD` and **any other
  novel/unknown token**; flag + park, never a guessed formula.
- **`NeedRebuffer`** — retained for an applied `INDEX` whose base year precedes
  the pull (Decision 5, §4.6).

### 4.4 Per-series bracket lag `[-n]` (sign CONFIRMED — Q6)

Parsed from the **label** (trailing `[...]`, signed int, default 0; per-series).
Unit = the series' **native frequency**. Applied **after** all math transforms.
**`[-n]` ⇒ `x.shift(n)`** (values shifted forward so earlier values land on
later dates = leading-indicator overlay). Log the exact shift; preserve the
tag in the legend ("… [-4]"); eyeball at G4. `[+n]` or non-native unit ⇒ **stop
and ask**.

### 4.5 Lookback buffer

Sized **per series in NATIVE periods** (Decision 1 REVISED — transforms run
native) and padded by the edge tolerance (Decision 2):

```
pull_start = visual_start − (transform_window_native + |lag|_native + EDGE_TOL_native)
EDGE_TOL   = { M: 4 months, Q: 2 quarters, W: 13 weeks }   # native periods
```

`transform_window_native` = the max lookback of the parse tree **at the series'
native freq** (YoY at native = `freq_base_native`, e.g. quarterly YoY = 4;
`movv(·,k)` adds `k−1` native periods; nested calls sum; centered `…C` adds the
look-*ahead* half-window). The series is pulled to land **at or before
`visual_start`** (safe side). After transform+lag the result is interpolated up
to `common_freq` (§4.3); the first/last-valid **tolerance-band** checks then run
on that final common-grid series with `tol = EDGE_TOL[common_freq]` (§8.2/§8.10).

### 4.6 Parser pseudocode — FOR G3 SIGNOFF (not implemented until approved)

```
EDGE_TOL = { M: 4 months, Q: 2 quarters, W: 13 weeks }   # per-freq (Dec.2)
# QUARTER_ANCHOR (G4-pinned): a low-freq obs is placed at its PERIOD-END month
#   before lift — Q→M lands on Mar/Jun/Sep/Dec, A→M on Dec. A wrong anchor shifts
#   the interpolated low-freq line 1–2 months horizontally vs the high-freq series
#   (and moves where a native lag lands post-interp). Confirmed visually at G4.

# ---- tokenize + recursive-descent parse → AST -------------------------------
parse(formula) -> AST node, one of:
    Series(code, db)            # mnemonic, optional @db; carries native_freq
    Func(name, pct, n, centered, args[])
                                # name∈{DIFF,DIFV,DIFA,YRYR,MOVV,MOVA,MOVT,
                                #       INDEX,ZS,LN,ABS,NA2Z,Z2NA,SetNA,FX,HP}
                                #       pct=True if '%'; 'C' on DIF*/MOV* → centered
                                #   YTD/DYTD are NOT computed → raise NeedPin (park)
    BinOp(op, left, right)      # op∈{+,-,*,/}
    Num(value)
# grammar: expr := term (('+'|'-') term)* ; term := factor (('*'|'/') factor)*
#          factor := Num | Series | Func '(' args ')' | '(' expr ')'

common_freq = highest frequency among the chart's series   # final grid + plot ONLY

# ---- evaluate AST at each node's NATIVE freq (Decision 1 REVISED) ------------
# Returns (series_or_scalar, node_freq). freq_base is NATIVE, per node — the
# transform never touches the common grid; window threaded through for ZS (Dec.4).
eval(node, window) -> (value, freq):
    Series -> (native buffered series, native_freq)         # §4.5 native buffer
    Num    -> (scalar, None)
    Func(name, pct, n, centered):
        (sub, f) = eval(arg, window)                        # inner first
        fb = freq_base[f]                                   # NATIVE freq_base
        DIFF/DIFV/DIFA(+%/+L), YRYR(+%/+L), MOVV/MOVT/MOVA, LN, ABS,
        NA2Z, Z2NA, SetNA, FX, HP
            -> return (apply §4.2 formula on sub with native n, fb; centered=True
                       → rolling(center=True)/t±n/2), f      # all PINNED
        ZS  -> (Decision 4 + Bug-A fix; outer OR nested)
               μ,σ = sub.loc[window].mean(), sub.loc[window].std()   # WINDOW μ/σ
               return ((sub − μ)/σ, f)        # over FULL buffered range, NOT
                                              # truncated to EDGE_TOL (Bug A)
        INDEX(X, base=YYYY[/Q/MM]) -> (Decision 5)
               # native units only (PPI 1982=100, PCE 2017=100). Defensive:
               if applied AND base_year < pull_start: raise NeedRebuffer(X, base_year)
               return (X / X.loc[base].mean() * BaseValue, f)
    BinOp(op, L, R):
        (l, fl) = eval(L, window);  (r, fr) = eval(R, window)
        if fl is None or fr is None:                        # series <op> scalar
            return (l <op> r, fl if fr is None else fr)     # broadcast; NO lift
        if fl != fr:                                        # mixed-freq SERIES
            lift the LOWER-freq operand UP to max(fl,fr) by linear interp,
            anchored at QUARTER_ANCHOR (period-end month); no extrapolation past
            its native first/last obs; log span                          # §8.5
        return (l <op> r, max(fl, fr))

# ---- per-series finishing ----------------------------------------------------
finish(node, lag, window):
    (s, f) = eval(node, window)                             # native transform done
    s = s.shift(lag_abs)   where lag=[-n] ⇒ shift(n)   in NATIVE freq f  (§4.4)
    if f < common_freq:                                     # Decision 1 step 4
        s = interp_linear(s, up_to=common_freq, anchor=QUARTER_ANCHOR)  # AFTER xform+lag
        log_interp_span(s, range, n_filled); no_extrapolation               # §8.5
    s = s.loc[window.start : window.end]                    # slice on common grid
    # Bug-B fix: a combined expr starts at the LATEST-starting operand. Anchor on
    # the max inception across ALL series in the subtree, not one series.
    eff_inception = MAX(inception(sᵢ) for sᵢ in series_of(node))   # get_series
    anchor_first  = max(window.start, eff_inception)              # late start OK (D3)
    tol = EDGE_TOL[common_freq]
    assert anchor_first − tol  <=  s.first_valid_index()  <=  anchor_first + tol
    return s

# ---- chart-level -------------------------------------------------------------
# (No r-box: the correlation box is removed from the deliverable entirely.)
last_value_check:                                                           # §8.10
    assert | s.last_valid_index() − visual_end | <= tol     # tolerates edge NaN
    assert | s.at[s.last_valid_index()] − screenshot_last_flag | <= value_tol
    # centered …C wider than 2×EDGE_TOL → fails this → Teams-route (not silent).
```

**VERIFY — JOLTS Quits `[-4]` (M) vs ECI Wages (Q), transform-then-interpolate:**
- `common_freq = M` (JOLTS monthly is highest); it governs only the final grid.
- **ECI path (native Q → interp):** `visual_start = 2015-01`. Buffer ECI in
  **quarters**: `transform_window_native = 4` (quarterly YoY), so pull from
  `2015Q1 − (4 + 2)Q = 2013Q3`. Compute `yryr%` **on the native quarterly
  series** = `shift(4)` quarters → first valid YoY at `2014Q3`. **THEN
  interpolate** the quarterly-YoY result **up to monthly** (linear, no
  extrapolation), each quarterly obs placed at its **period-end month**
  (`QUARTER_ANCHOR`, G4-pinned). If ECI's last native print is `2025Q1` (placed
  at `2025-03`), interpolation fills monthly points only **up to** `2025-03` —
  the monthly ECI line **ends earlier on the right** than JOLTS (ragged edge,
  handled by the overlapping non-NaN window).
- **JOLTS path (native M = common, no interp):** 9-mo MA + `[-4]`. Buffer
  `2015-01 − (8 + 4 + 4)mo = 2013-09`. `movv(·,9)` → first valid `2014-05`;
  **shift(4) applied LAST** (native months); slice to `[2015-01 : visual_end]`.
  `first_valid_index = 2015-01`, within `EDGE_TOL=4mo` of `visual_start`. ✓
- **JOLTS right edge:** last actual Quits print `2025-04` → `shift(4)` pushes the
  lagged line's last valid point to **`2025-08`**. §8.10 compares at
  `s.last_valid_index() = 2025-08` (within tol of `visual_end`) — exactly where
  the screenshot's `[-4]` last-value flag sits. ✓

**Decision status folded in:** (1) ALIGN pinned = **transform-then-interpolate**,
per series at native freq, interp AFTER; (2) first/last-valid are `EDGE_TOL`
bands anchored on the x-axis read; (3) multi-start series plot over their own
range (no auto-shorten, no hard-fail); (4) ZS window-μ/σ, nested supported,
**returns full buffered range (Bug A)**; (5) INDEX native-only + `NeedRebuffer`;
(6) centered `…C` runs (`center=True`). **Bug B:** `eff_inception = MAX` over the
subtree's operands. **YTD/DYTD out of scope** → `NeedPin` → Teams-flag + park.
**Pinned map closed**; anything unknown routes to Teams.

Open at G3: sign off this block before any `transforms.py`.

---

## §5. Model / core spec (pseudocode)

```python
def run_daily():
    ledger = Ledger.load("data/ledger.csv")
    for msg in graph_find_daily_emails(sender=NEIL, subject_rx=DAILY_RX):
        if ledger.email_fully_done(msg.id): continue
        commentary, images = extract_assets(msg)        # fail-loud on unreadable formats
        for idx, img in enumerate(images):
            row = ledger.get_or_create(msg.id, idx)
            if row.status in ("done","skipped"): continue
            if classify(img) != "raw_haver":
                ledger.mark(row, status="skipped"); continue
            spec = read_chart(img)                       # ChartSpec
            try: spec = resolve_all_tickers(spec)
            except NeedTicker as e:
                teams_ask_ticker(row, e.candidates)
                ledger.mark(row, status="awaiting_ticker"); continue
            data = pull_and_transform(spec)              # §4
            if row.chosen_title is None:
                teams_post_options(row, propose_titles(spec, commentary))  # title+slug
                ledger.mark(row, status="awaiting_approval"); continue
            png = render(data, spec, row.chosen_title, row.subtitle)       # +recession (no r-box)
            ledger.mark(row, status="done", output_path=png)
    harvest_replies_and_resume(ledger)                   # no-reply rows stay parked (Q1)
```

`ChartSpec`: `series[]` (descriptor, formula?, code?, sa, freq, lag=0,
axis∈{L,R,shared}, legend_label), `axis_mode∈{shared,dual}`, `display_start/end`,
`units`. (No `show_r_box` — the r-box is removed.)

---

## §6. Open questions — resolved at G1 (status)

| Q | Resolution |
|---|---|
| Q0 functions.htm | **Resolved** — present; §4 re-grounded, `[verify]` removed (§4.0). |
| Q1 Teams | **Sync-wait, numbered + free-text, post-and-poll, per-day channel/thread.** No-reply → row stays `awaiting_*`, resumes next run (never block forever, never render unapproved). |
| Q2 ordering | **resolve → pull → parse/transform → propose → approve → render.** |
| Q3 email scope | **Confirmed:** sender `ndutta@renmac.com` AND subject regex; mailbox = **`asingh@renmac.com` (your inbox)**, not a shared mailbox → Graph `users/asingh@renmac.com`. |
| Q4 classification | attribution-text + title/palette primary; vision tie-breaker; verdict shown in Teams. |
| Q5 release_slug | LLM-proposed, confirmed in the **same** Teams round-trip as the title; subject-slug fallback. |
| Q6 lag sign | **`[-n]` = `x.shift(n)`**, after transform, native freq (§4.4). |
| Q7 model + key | **Resolved.** Model id `claude-opus-4-8`; `anthropic==0.111.0` installed; `ANTHROPIC_API_KEY` now in `econ-templates/config/.env` (the file `.bashrc` sources) and live. `api.anthropic.com` in `sandbox.json` (staged). **Vision 26/26 done.** |
| Q8 size | `figsize=(8.5, 6.1)` explicit (not the module default). |
| Q9 dual-axis | thin twin-axis wrapper in `render.py` consuming `renmac_chart_style` colors/style/source box. |

**All blockers cleared:** Q3 mailbox (`asingh@renmac.com` inbox), Q7 model
(`claude-opus-4-8`) + key both confirmed and live; **G2 vision re-run done at
26/26.** Next gate is **G3** — sign off the §4.6 parser pseudocode before any
`transforms.py`.

---

## §7. Outputs

### 7.1 Figures — RenMac PNG per processed raw-Haver chart

- **8.5" × 6.1"**, `dpi=200`, `bbox_inches="tight"`; colors from
  `renmac_chart_style.py` (maroon `#621909` / navy `#1B2A4A` + extended
  palette), consumed not redefined (`30-charts.mdc`).
- `renmac_style(ax, title, subtitle)`; title ≤11 words, sentence case, no colon.
- **LABELING DISCIPLINE (hard rule; `render._assert_clean` enforces it).** The
  legend label is the `get_series` DESCRIPTION shortened to its identifying
  essence, with the **bracket lag tag `[-n]` preserved**. The transform (YoY%,
  z-score, 3-mo annualized, MA) lives in the **SUBTITLE**; it appears in the
  legend ONLY when two series on the same chart carry **different** transforms and
  the distinction matters (then a clean human-readable suffix, e.g. `…, YoY`).
  The raw Haver function string (`movv(...)`, `difa%(...)`, `zs(...)`, `@db`)
  must **NEVER** reach a rendered legend or title — there is no formula-dumping
  fallback; a dirty label raises. Match `outputs/g4_desired/*_var{1,2}.png` for
  labeling/title/subtitle style (style reference, not pixel/curve/color replica;
  the `_var1`/`_var2` pairs are the alternate title+subtitle options surfaced in
  the G5 Teams round-trip).
- Source box: "Source: Renaissance Macro Research, Haver Analytics".
- **NBER recession shading** — gray vertical bands (recession-shading.htm =
  default U.S. NBER, peak→trough). Use `add_recession_shading()` from
  `renmac_chart_style.py` fed by Haver **`RECESSM2@USECON`** (monthly; `1` =
  recession month, `0` = not). Shade the **contiguous runs where `RECESSM2 == 1`**
  (peak→trough). Resolve the ticker via `get_series` like any other series.
  **Only shade when the original screenshot shows bands** (read off the chart);
  never hand-rolled dates.
- **No correlation `r =` box.** Removed from the deliverable entirely — never
  drawn, even when the original Haver screenshot shows one (just omit it). The
  `r =` glyph is still a *classification* cue (it marks a raw-Haver chart, §1.4),
  but it is not reproduced.
- **No partial charts (Defect 2 / park-the-whole-chart).** If ANY series on a
  chart is unresolved/pending, the WHOLE chart is parked to the Teams
  unresolved-ticker round-trip (`render.park_chart` → `awaiting_ticker`); never
  render a chart missing a series it is titled for. This is part of the G5 loop
  and is built together with it.
- **Last-value fidelity check (catches wrong-SA / wrong-base / wrong-transform)**
  — when the Haver screenshot shows a **last-value flag** (the boxed end-of-line
  number, e.g. sample7_2 `28`/`31`, sample7_3 `37`/`8`), the renderer compares
  the reconstructed series' last plotted value to it and **fails loud on a
  mismatch beyond tolerance** (routes to Teams), rather than waiting for the
  human to spot it. This is a cheap, high-signal guard against a
  plausible-but-wrong series/transform that the palette/ticker checks can't see.

### 7.2 File layout

```
MMDDYYYY/<release_slug>/<chart_slug>.png | .parquet | .csv
outputs/raw/<code@db>_<pull_date>.parquet
data/ledger.csv
```

### 7.3 Tables

None analytical; the ledger CSV (§10) is the artifact of record.

---

## §8. Validation

1. Idempotency: 2nd run processes zero done emails/charts.
2. **First-valid tolerance band (Dec. 2/3; Bug B):** `anchor_first − tol ≤
   s.first_valid_index() ≤ anchor_first + tol`, where `anchor_first =
   max(visual_start, eff_inception)`, **`eff_inception = MAX inception over ALL
   series in the subtree`** (a combined `A+B` starts at the later operand), and
   `tol = EDGE_TOL[common_freq]`. Catches a genuine under-pull; does not
   false-fail a centered/short/late-start/combined series.
3. Lag: logged shift == read `[-n]`; G4 side-by-side.
4. Axis: `axis_mode` read + asserted; dual renders twin axes.
5. Interpolation visible: every fill logged + asserted.
6. Ticker sanity: every series `get_series`-confirmed, SA/NSA matched; else parked.
7. Classification on `notes/`: picks exactly the §1.3 PROCESS set, skips the rest.
8. Smell tests: z-scores ~[-4,4]; index≈100 at base; first/last+rowcount printed.
9. Recession bands (Haver `RECESSM2@USECON==1` runs) land on the original's
   bands at G4. **G4 CONFIRMED** — runs verified against the series, not eyeballed:
   `2001-04→2001-11`, `2008-01→2009-06`, `2020-03→2020-04`. NB `RECESSM2` uses the
   FRED/USREC **month-after-peak** convention, so each run starts one month after
   the NBER peak month (Mar-2001 / Dec-2007 / Feb-2020) — correct, not drift.
   (No `r`-box check — the correlation box is removed from the spec entirely.)
10. **Last-value flag match (Dec. 2) — now CODE: `validate.check_last_value`.**
    `|s.last_valid_index() − visual_end| ≤ EDGE_TOL[freq]` (tolerates recent-edge
    NaN), then `|s.at[last_valid] − screenshot_flag| ≤ value_tol`, else fail loud.
    Compared at last-valid (e.g. the `[-4]`-extended right edge), not forced to
    display_end. **The end-value NUMBER is no longer DRAWN** (not in the desired
    style) — but the silent-error GUARD stays and runs in the validate step for
    any chart whose screenshot shows an end flag (wrong SA/base/transform catch).
11. **Transform-then-interpolate order PINNED (Dec. 1 REVISED)** — each series'
    transform runs at NATIVE freq, then the result is interpolated up to
    `common_freq` (no extrapolation). **G4 CONFIRMED** on JOLTS `[-4]`(M)/ECI(Q):
    reconstruction overlays the original across the full span incl. 2021–22 peak.
12. **Quarter→month interp ANCHOR = period-END — G4 CONFIRMED (not assumed).**
    On the JOLTS/ECI overlay the interpolated quarterly→monthly ECI line lands
    directly on the original (kinks/peaks/troughs aligned, no 1–2 month drift); a
    quarter-START anchor would have shifted it left by a quarter. Pinned.

---

## §9. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | Classification false ± | attribution+style validated on 26 samples; vision tie-break; Teams shows verdict |
| R2 | Vision misread | cross-check vs `get_series`; unresolved → Teams ASK + park |
| R3 | Range/lag off-by-one | lookback=transform+\|lag\|; first-date assert; lag shift logged + G4 |
| R4 | Axis misread | read + assert `axis_mode`; dual-axis renderer |
| R5 | Silent interp | log every span + assert |
| R6 | Unreadable image format | **fail loud → Teams** ("couldn't read `.emf` in <doc>"); no silent skip (C2) |
| R7 | Teams reply capture | post-and-poll numbered+free-text; `awaiting_*` states allow async |
| R8 | Partial failure | per-chart ledger row + status |
| C1 | `PCUSLFE`≠Core PCE | no fixed tickers; all `get_series`-verified |
| C3 | no housing `[-5]` | JOLTS `[-4]` for G4 |

---

## §10. Ledger schema (`data/ledger.csv`)

`message_id, received_date, received_time, sender, subject, release_slug,
chart_index, resolved_ticker, series_lag, proposed_title, chosen_title,
subtitle, output_path, status, processed_at` — one row per (email × chart).
`status ∈ {pending, awaiting_ticker, awaiting_approval, done, skipped}`; loop
acts only on non-terminal rows (resume-safe).

---

## §11. Environment, deps, sandbox

- Git Bash + `shared-3.10`. Env check this session: `FRED_KEY_OK=True`,
  `MSGRAPH_OK=True`, **`ANTHROPIC_OK=True`** (key in `econ-templates/config/.env`,
  sourced via `.bashrc`), `AS_SENDER=asingh@renmac.com`.
- New deps (G2+): `python-docx`/`zipfile` (docx media), `Pillow` (images +
  format sniff), `anthropic` SDK; reuse `msal`/`requests`/`msgraph`/
  `azure-identity` from `send_via_graph.py`. `matplotlib`/`pandas`/`Haver`
  present. **No EMF/WMF converter unless a real email needs it (C2).**
- `sandbox.json`: `graph.microsoft.com` + `login.microsoftonline.com` present;
  **`api.anthropic.com` added (staged)**.
- Graph scopes: **Mail.Read CONFIRMED** — the email app `renmac-mail-oath-postman-app`
  (`AS_MSGRAPH_*`) already has **`Mail.ReadWrite` + `Mail.Send` (Application,
  admin-consented)**; `Mail.ReadWrite` is a superset of `Mail.Read`, and a live
  `$top=1` read of `asingh@renmac.com` returned 200. **No IT ticket needed.** Teams
  is a SEPARATE **delegated** token (`renmac-chart-bot`), not application perms.

---

## §12. Stop gates

| Gate | What | Status |
|---|---|---|
| G1 | Plan | **approved** |
| G2 | Extraction + classification demo on `notes/` (exactly PROCESS set; fail-loud formats) | **DONE: extraction 26/26; pre-filter 25/26; vision tie-breaker 26/26** (`run_g2_demo.py --vision`, live `claude-opus-4-8`) |
| G3 | Pseudocode signoff: **§4.6 parser** — formulas pinned from 7 screenshots (map CLOSED; YTD/DYTD out of scope → Teams), Decisions 1–6 (Dec.1 = transform-then-interpolate) + Bugs A/B | **signed off → `transforms.py` built** |
| G4 | Two halves. **(a) Transform math — CONFIRMED, keep:** §8.11 transform-then-interpolate + §8.12 quarter-anchor=period-END pinned on JOLTS `[-4]` (`LJQTPA`/`LSWP@USECON`); DIFA% annualization npy/n=4 (`NMSCNX`/`NMOCNX`, −49% GFC trough); direct YRYR%; window-ZS μ/σ + nested yryr%; recession-band placement (`RECESSM2==1`). Applied-INDEX **untested** (Finding A — not in 26 samples). **(b) Deliverable vs render spec — RE-RENDERED, pending review:** r-box dropped everywhere; legends now clean `get_series` descriptions (transform→subtitle, lag tag kept), checked against `outputs/g4_desired/*_var{1,2}`; **sample1_3 PPI resolved BY DEFINITION = `pa413121@usecon`** (NAICS 413121 Pvt Capital Equipment for Mfg Industries, per the desired legend) — the shape-pick `sp3210` was the C1 circular trap, **discarded**; both lines now render (no partial). sample7_1 is now ZS+labeling only (no r-box). Park-the-whole-chart wired (`render.park_chart`) for the unresolved case. PPI `pa413121` added to the trusted "Confirmed mnemonics" list (`sp`-codes kept out; screenshot descriptor "PPI: Manufacturing Industries" will correctly park, not auto-reach pa413121). Profits = `ycp@usecon` (with IVA&CCAdj total = the pre-tax measure the desired ref names; `bncpbt` is nonfinancial/no-adj, rejected). Last-value guard restored as code (`validate.check_last_value`); recession runs verified. | **CLOSED** (a CONFIRMED, b validated) |
| G5 | Teams loop: **title round-trip** (offer the `g4_desired` `_var1`/`_var2` title+subtitle pairs as the proposed options) + **unresolved-ticker round-trip** (Defect-2 park-the-whole-chart, built here). **BUILT (offline-proven):** `ledger.py` (§7.8 schema + state machine: pending→awaiting_ticker→resolved→awaiting_approval→approved, idempotent CSV, no-reply stays parked), `teams.py` (Transport ABC + `StubTransport` for the dry-run + `GraphTransport` skeleton; numbered + free-text reply parsing — content-based, not timing-based, so bad-code-then-good-code resolves), `approval.py` (both round-trips; ticker precedes title). `scripts/g5_demo.py` proves: park→bad ticker rejected→`pa413121@usecon` confirmed→resolved; title option 2→approved; no-reply row stays parked. **LIVE off-stub:** `resolve.confirm_ticker` (Haver DLX existence check — accepts `pa413121@usecon`/`LSWP@USECON`, rejects bogus/malformed; headless, no MCP at runtime) and `propose.propose_titles` (Opus `claude-opus-4-8`, returns colon-free takeaway title + transform subtitle, no formula). `scripts/g5_wire_check.py` proves both. **REMAINING = ONLY the Graph transport.** **KEY FINDING (MS Learn `chatmessage-post`, upd. 2026-05-19):** app-only/client-credentials (the email pipeline's pattern) **cannot send Teams chat OR channel messages at runtime** — the sole app permission is `Teamwork.Migrate.All` (import-only); runtime messaging is **delegated-only** (`ChatMessage.Send`) or a Teams bot. So the email creds do NOT transfer. Chat surface ⇒ **delegated** `ChatMessage.Send` + `Chat.ReadWrite` (+`User.Read`), via a one-time interactive consent → cached refresh token (headless thereafter); this avoids the app-only protected-API burden (channel reads). **CORRECTION (2026-06-28):** the 4 scopes are user-consentable in general, but RenMac has **tenant-wide user consent disabled**, so the device-code `consent` still needs a **one-time admin grant** (org policy) — see G7 row. Delegated is still correct (app-only can't message at runtime); the grant is one-time/tenant-wide, not per-sign-in. Webhook rejected (post-only, can't read replies). **DECIDED + BUILT:** delegated **self-chat** (group-of-one isn't creatable). `GraphTransport.post/replies` implemented (httpx → `chats/{id}/messages`, `[renmac-chart-bot]` tag to skip own posts, **per-chart `[chart_id]` echo-tag** to bind replies on a multi-park day, opaque `createdDateTime` cursor); `teams_auth.py` (MSAL **public-client device-code**, Fernet-encrypted refresh token, `TeamsAuthError`→`alert_reauth_needed` via Mail.Send, `consent`/`check`/`keygen`/`list-chats` CLI); scopes finalized to 4 user-consentable (`Chat.ReadWrite`,`ChatMessage.Send`,`ChatMessage.Read`,`User.Read`); cursor model replaces wall-clock (ledger `ask_cursor`, post-once guard, chart-scoped thread keys). **REMAINING = live provisioning only:** run `consent` once on app **renmac-chart-bot**, set `AS_TEAMS_CHAT_ID` (via `list-chats`)/`AS_TEAMS_TOKEN_KEY`. | **in progress — all code LIVE/built + G7 offline-proven; live consent + chatId pending** |
| G6 | Pre-commit review | |
| G7 | **BUILT + OFFLINE-PROVEN** (`scripts/g7_run.py`): two-invocation, kill/restart between runs (separate processes; only the CSV ledger persists). `selftest` runs reset→run→reply→run→assert as fresh processes with the stub transport — **all six assertions PASS**: (1) RUN-1 ingests, posts one ticker ask each for sample1_chart3 + sample9_chart1 and one title round-trip for sample2_chart2, none for the pre-approved jolts chart; (2) idempotency — jolts renders in RUN-1, its PNG predates the RUN-2 renders (not re-rendered); (3) clean resume — sample1_chart3 ticker (`pa413121@usecon`) + sample2_chart2 title (opt 2) answered between runs complete in RUN-2; (4) partial-chart integrity — sample9_chart1 left unanswered stays `awaiting_ticker` with **zero PNG** (resolved sibling never rendered alone); (5) no duplicate post — exactly one post per asked chart, none re-posted in RUN-2 (cursor/post-once guard); (6) ledger is source of truth — resume reconstructs from CSV alone. Live mode (`G7_TRANSPORT=graph`) is the SAME code against `GraphTransport`; confirm_ticker (Haver) + propose_titles (Opus) already proven live (g5_wire_check). **FRONT HALF (real inbox):** `scripts/run_daily.py --date YYYY-MM-DD` adds date-scoped Mail.Read ingestion (`src/ingest.py` — Eastern-zoneinfo window, server-side `$filter`+paging, resilient extract/classify, EMF→Teams), seeding a SEPARATE `data/ledger_backfill_<date>.csv` (idempotent; `--reprocess` redo). Offline-proven via `--selftest` (§3.4). First live target 2026-06-25. **REMAINING = the live Graph round-trip + the live Mail.Read read**, after consent + `AS_TEAMS_CHAT_ID`. Live G7 order: `reset` → `run` → reply in Teams (tag `[chart_id]`, leave sample9 unanswered) → `run` → `assert`. **BLOCKED 2026-06-28 — one-time admin consent (RenMac org policy):** the device-code `consent` step returns "Need admin approval" even though all 4 delegated scopes show "Admin consent required: No" in the portal — RenMac has **tenant-wide user consent disabled** (org policy, not a scope/config bug; scopes remain minimal, no `.All`). Unblock = a one-time tenant grant by an Entra admin via `https://login.microsoftonline.com/<AS_TEAMS_TENANT_ID>/adminconsent?client_id=<AS_TEAMS_CLIENT_ID>` (or portal **API permissions → Grant admin consent for RenMac**). It's a persistent tenant `oauth2PermissionGrant`, **not per-sign-in**: after it lands, `consent → list-chats → check → --approve` proceed with no further admin involvement (re-grant only on new scopes or revocation). `list-chats`/`check`/live `--approve` held until then. | **offline-proven; live Graph BLOCKED on one-time admin consent (org policy); live Mail.Read pending** |
| **G8** | **Chart-field read (Opus vision) — THE CORE INTELLIGENCE.** Two stages, inverted flow (vision produces descriptions → resolver confirms → human only on genuine ambiguity). **CONFIRMED design (2026-06-29):** **Stage 1 vision read** (`src/chartspec.py`, Opus, headless) → `ChartSpec`: per series {description, raw Haver FORMULA if shown, ticker_read (mnemonic from formula), transform (if words-only), axis L/R/shared, bracket lag `[-n]`, SA/NSA + base hint, legend, confidence} + chart-level {n_series, axis_mode, sample_range, units, recession_shading}. The read IS the formula string — it feeds the G3 `transforms.py` parser directly. **Stage 2 resolution = Model A:** formula-embedded mnemonic → DLX `get_series` confirm + **cross-check freq/SA vs the read** (headless, auto-bind only on match); description-only series → park to Teams with real desc+candidates (per-series slot). `search_series`/`get_series` are the **authoring-time MCP** (I use them to build candidates + the field battery), NOT headless — resolver is dependency-injected so production stays DLX-only. **Read is a HYPOTHESIS; nothing binds until the `get_series` cross-check passes** (mismatch → fail-loud Teams, never silent-bind). **Ledger schema change:** N per-series slots (each independently pending/resolved/skipped) replace the single placeholder; Defect-2 holds (any pending slot → whole chart parked, renders nothing). **G8a BUILT + run (`scripts/g8_read_battery.py`):** vision read over 22 raw-Haver charts (14 loose + 7 docx + texas_mfg_outlook) → **22/22 read, 0 errors, 43 series (10 formula/DLX-confirm, 33 description-only)**; formula reads match G4-pinned mnemonics (NMSCNX/NMOCNX, JCSRM/JCSXEHM/JCGXFEM, YCP), JOLTS `[-4]` lag caught. **G8a v2 re-read after Aman's 4 corrections:** (1) `axis_mode` now DERIVED from L/R numeric tick ranges (n_series≤1 or equal ranges→shared; differ→dual) — 6 dual/16 shared, single-series impossibles fixed; (2) non-forecast x-axis END = `derive_at_pull` (impossible 2027–2031 ends gone), Stage-1 reads start + `has_forecast` only; (3) sum formulas captured whole (ticker_read demoted to advisory; G8b walks the G3 parser to confirm every addend); (4) `base_descriptor`/`applied_transform` split + `native_ma_ambiguous` flag (Wage-Growth-Tracker 3-mo-MA flagged → bot). Residual: right-axis tick read can copy the left (e.g. sample6_chart2 reads shared but is dual) → Aman's `corrections.json` axis_mode override settles it. Correction schema + template at `outputs/g8/corrections.template.json` (override-only: axis_mode/sample_start chart-level; ticker/axis/native_ma per series); battery scores read-vs-truth when `corrections.json` present (`--from-cache` scores the reviewed reads deterministically). **SCORED vs Aman's ground-truth key: 11/11 fields match (100%)** — axis_mode/axis/native-MA all correct on texas + sample6_chart2 (truth=shared, model right) + sample2_chart3 (native-MA both ways) + JOLTS; description-only tickers (DFBACTS/DBACTS@SURVEYS, FFEDTAR, WGTO, PCUSERH, LJQTPA/LSWP@USECON) are Stage-2 fills, vision correctly didn't fabricate. **G8b clarified-knowledge store = `knowledge_repo/clarified-knowledge/` (JSON keyed by descriptor)** for native-MA-vs-applied confirmations (write-once, never re-ask). **G8a SIGNED 2026-06-29.** **G8b BUILT + OFFLINE-PROVEN (`scripts/g8b_demo.py`, 15/15 checks):** Stage-2 resolver `resolve.py` (`build_slots`→N per-series slots; `resolve_slot`/`resolve_chart`; `chart_status_from_slots`) — **(1) formula path** walks the G3 parser (`transforms.formula_mnemonics`) and confirms EVERY mnemonic (sum BEEM1+BEEM2+BEEM3 → all 3 bound; any unconfirmed addend → parked, never silent-binds); **(2) description path** searches→confirms→**SA cross-checks** (`sa_matches`: read sa_hint vs `get_meta`; wrong-SA candidate rejected) → auto-binds only on a single confident match, else parks with candidates; **(3) native-MA** consults the clarified store (`load_clarified`/`save_clarified` at `knowledge_repo/clarified-knowledge/native_ma.json`; WGTO seeded) → auto-binds if known, else parks `needs_clarification`. `_qualify` attaches `@db` to bare formula mnemonics via `trusted_tickers.json` (human-confirmed: YPWM/YCP/PA413121/FFEDTAR/WGTO/PCUSERH/LJQTPA/LSWP@…, DFBACTS/DBACTS@SURVEYS) then search. **Ledger:** new JSON cols `chart_spec` + `series` (N slots, each pending/resolved/skipped) + `ask_sig`; `sync_row_from_slots` rolls slots up (RESOLVED only when EVERY slot bound — Defect 2). **Per-slot Teams round-trip** `approval.run_series_roundtrip` + `teams.format_series_ask`/`parse_series_reply` (`[chart_id]` + per-line `#idx` routing; bare codes fill pending ticker slots in order — the texas "two tickers line-by-line" case; native-MA clarify→persist→re-resolve→re-ask on changed signature; `skip` drops whole chart; post-once per signature). Resolver/transport fully dependency-injected (offline stubs; MCP=authoring, DLX=runtime). Legacy single-slot lane (g5/g7) untouched — both still green. **G8a REOPENED 2026-06-30 for the FREQUENCY field (Aman):** frequency is a DEFINING attribute (same tier as SA) — a monthly description resolving to a same-named QUARTERLY series passes the SA check and would silent-bind wrong. Added one-field per-series `freq_hint` to Stage 1 (`chartspec.py` prompt + `SeriesSpec`) and `resolve.freq_matches` folded into BOTH resolver paths via `_meta_reject` (read-freq vs `get_meta` freq; mismatch → park, exactly like SA; unknown either side → skip). **Park-safe by construction:** a wrong freq read can only cause an EXTRA park (human exception path), never a confident wrong-bind. Re-ran the live battery: **22/22 read, 0 errors, the 11 previously-grounded fields still 100% (no regression)**; `g8b_demo` freq-mismatch branch added (16/16). **Read-accuracy note (for the freq ground-truth pass):** 2 likely freq misses on formula/abbreviated series with no freq cue — `YCP` (corp profits, quarterly) read monthly; `FFEDTAR` (Fed Funds target, monthly in USECON) read daily — both would PARK (safe), not bind wrong. **G8a freq scored vs Aman's `corrections.json` (`--from-cache`): 20/23 (87%)** — all 11 hard fields still 100%; 3 freq misses all on no-cadence-cue series (Fed Funds EOP read daily; CEO Bus Conf read quarterly; YCP read monthly), every one PARK-safe. **G8a PARTIAL-SIGNED 2026-06-30:** 11 hard fields + non-ambiguous freq + texas clean; ambiguous-series freq explicitly ADVISORY. **FREQ REFRAME (Aman, the right reason — not fewer parks):** freq-of-record = `get_meta` (authoritative), vision `freq_hint` advisory. A park must fire on REAL ambiguity (no cadence cue→multiple candidates, EOP-vs-AVG, SA-undetermined), NOT on a spurious read-vs-meta freq mismatch the metadata resolves. Implemented in `resolve.py`: freq REMOVED from `_meta_reject` (never rejects); `meta_freq`/`freq_advisory_mismatch` record `freq_resolved` (authoritative) + a diagnostic flag on bind; genuine freq ambiguity (two same-named series at different freqs) is caught by CANDIDATE COUNT, not the advisory read. **AGGREGATION cross-check ADDED (Aman, Fed Funds EOP):** "EOP"/"AVG" in a descriptor is a RESOLUTION CONSTRAINT — confirmed `get_series` exposes **`agg_type`** (FFEDTARE@USECON=EOP vs FFEDTAR@USECON=AVG, both Monthly, same name → aggregation is the ONLY distinguisher). `agg_matches` is a HARD reject like SA (EOP/AVG cue must match `agg_type`; unconfirmable agg under a cue → park). Tight cue detection guards against "Moving Average"/"3-Mo Mov Avg" false positives (a transform, not an aggregation). Seed fixed: `trusted_tickers.json` Fed Funds → `FFEDTARE@USECON` (EOP, mnemonic-honest key). `g8b_demo` extended: E2 (freq advisory → single match BINDS on get_meta freq, flags mismatch), E3 (EOP cue binds FFEDTARE, rejects FFEDTAR AVG), E3b (no cue → both confirm → REAL ambiguity → park) — **18/18**. **G8c LIVE RESOLUTION run + STOPPED before render (`scripts/g8c_texas.py`, seed bypassed trusted={}):** three live surfaces exercised — `search_series` (MCP), `get_series` (MCP), `confirm` (Haver DLX, live). **KEY FINDING — search-recall miss, NOT a read or cross-check failure:** both `DFBACTS`/`DBACTS@SURVEYS` EXIST with descriptors that EXACTLY match the vision read (get_series: "Texas Mfg Outlook Survey: General Business Activity[, 6 Months Ahead] (SA, %Bal)", M, SA, AVG, Dallas Fed, 2004→2026, 264 obs), BUT `search_series` on the chart's description does NOT surface them — it returns global S&P PMI "Business Outlook" + NY/Philly Fed survey series (the colon-structured Haver descriptor + "Mfg" abbrev + generic "General Business Activity" out-rank the exact Texas match). **The resolver PARKED both series (safe): every live candidate is NSA, the SA cross-check rejected all → no confident match → park to Teams (no mis-bind).** The get_series cross-check on the human-supplied truth tickers confirms an EXACT match to the read (freq/SA/descriptor all OK). **TWO HARDENINGS surfaced (held for review, not yet built):** (1) **search recall** — verbatim Haver descriptors resolve poorly; need better query construction (DB-scoped/`surveys`, source/geography hints, or fuzzy-code) or the live path parks nearly everything; (2) **descriptor-relevance gate** — the description path currently binds on "single confident match" with NO check that the candidate's descriptor matches the read; here SA saved it, but a lone wrong-SA-passing candidate could mis-bind — bind should require descriptor similarity, not just confirm+SA+agg+count. **HARD STOP held — no render, no run_daily wiring.** **G8c-v2 — both hardenings BUILT + confirm_all mode added (2026-06-30):** **Part 1A descriptor-relevance gate** (`resolve.descriptor_similarity`/`descriptor_exact`, `_descr_tokens` with abbrev expansion + unit/stop drop): the description path NEVER binds on confirm+SA+agg+count alone — it requires the candidate's `get_series` descriptor to MATCH the read. Primary signal = EXACT normalized token-set equality (the only thing that separates a headline series from its near-identical directional siblings — "General Business Activity" vs "…: Worsened", one extra token Jaccard can't resolve); fallback = Jaccard ≥0.5 with a ≥0.15 margin over the runner-up. Below threshold → NON-bind even if it's the only candidate (every recall failure is a non-bind, never a wrong-bind). **Part 1B search recall** (`build_search_attempts`): DB-scope by keyword heuristic (surveys), issue 3 query VARIANTS per slot (verbatim, geography-lead + after-colon discriminator, discriminator alone) whose hits are UNIONED — recall up, precision from the gate. **Part 2 `resolution_mode` = confirm_all** (NEW, production default; `selective_park` PRESERVED + still green): nothing renders until the human ratifies — every chart posts ONE Teams summary with its FULL resolved set (`teams.format_resolution_summary`: per-series code@db/transform/axis/lag/freq + chart sample-range/axis_mode/recession), `approval.run_resolution_roundtrip` gates RESOLVED→AWAITING_RESOLUTION→(approve)→RESOLUTION_APPROVED; reply `[id] approve` / `[id] 1=CODE@DB 2:axis=R …` / `[id] skip` (`parse_resolution_reply`). **Part 3 titles = SEPARATE round-trip AFTER resolution approve** (`run_title_roundtrip(from_status=RESOLUTION_APPROVED, gate_status=AWAITING_TITLE, parser=parse_title_reply)`: approve/1/2/title=/subtitle=); renders ONLY after BOTH approves. **Part 4 learning store** (`learned_descriptors.json` desc→code + `save_trusted` mnemonic→code, written on approve; `load_learned` fast-path re-confirms+cross-checks before binding) + approve-without-correction metric logged. `g8b_demo` extended to a token-overlap CATALOG search (exercises recall+gate) + confirm_all branches → **29/29**; g5/g7 selective_park regression green. **Part 5 LIVE texas re-run (`scripts/g8c_texas.py`, confirm_all, seed bypassed trusted={} learned={}, live-captured search_series + live DLX confirm):** the multi-query union surfaced BOTH truth tickers (notably the VERBATIM query alone did NOT return them — the geography+discriminator variant did); **every one of the ~42 surviving candidates is SA, so the SA check disambiguated NOTHING — the relevance gate was the SOLE safety**, and EXACT-set match bound `DFBACTS@SURVEYS` (S0, over directional siblings at sim 0.909) and `DBACTS@SURVEYS` (S1) — **RESOLVED 2/2 LIVE, no seed, no park**; live DLX `confirm_ticker` exists=True for both, matching `corrections.json`. confirm_all POSTED the full resolution summary and **HELD at AWAITING_RESOLUTION** (Part-5 STOP step 2) — no approve, no title, no render. **LIVE PROOF the exact-token-set gate is the safety floor (2026-06-30, Aman-confirmed at approve):** for S0 the directional sibling `DFBACTDS@SURVEYS` ("…General Business Activity, 6 Months Ahead: **Worsened**") scored **Jaccard 0.909** — i.e. *similarity alone would have bound the WRONG directional decomposition* (0.909 ≫ any reasonable threshold). Only the EXACT normalized token-set match separated the headline `DFBACTS` (which equals the read token-set) from `DFBACTDS` (one extra token, "worsened"). With every candidate SA, neither SA/agg/freq nor a fuzzy-similarity score would have caught it — exact-set is the floor, not a nicety. **Aman APPROVED the texas resolution 2026-06-30** (`[texas_mfg_outlook] approve`) → flows to title round-trip → render-over-original (x-scale overlay, both lines, shared axis, recession months, derived end) for review. **Render done** (`scripts/g8c_render.py`): approve→RESOLUTION_APPROVED (learning store written: both Texas descriptions in `learned_descriptors.json`; approve-without-correction metric logged)→title round-trip posted 2 Opus options (held at AWAITING_TITLE for Aman's pick)→LIVE DLX pull of DFBACTS/DBACTS/RECESSM2→`outputs/g8c/texas_overlay.png` (recon tracks both original lines, shared −75..75, RECESSM2 spans on GFC trough + COVID spike) and `outputs/g8c/texas_reconstruction.png` (RenMac, derived end **2026-06** not the read's "25"). Title is provisional (option 1) pending Aman's `[texas_mfg_outlook] 1/2/title=…`.
| **G9** | **Chat lane (§13) — the same intelligence driven from Claude Desktop instead of email+Teams.** Two MCP tools (`resolve_series`, `render_chart`) wrapping `resolve.py` + `build_chart.render_row`; ingest/classify/ledger/Teams/propose replaced by the conversation. Closes the **render-preview gate** by construction (the operator sees the pixels before use), which is the open blocker to headless on the daily lane. Learning stores READ-ONLY from this lane in v1 (an unratified chat bind must not poison what `run_daily` trusts — the `mpcuhsro` class with a new entry point). No changes to the `run_daily` chain. **G9a SIGNED 2026-08-20. G9b BUILT + PROVEN the same day** (`haver_chart/`: `server.py` the two-tool FastMCP surface, `lane.py` the translation layer, `bootstrap.py` the boundary enforcement, plus `selftest.py`/`SETUP.md`/`SKILL.md`/`SYSTEM_PROMPT.md`). **Lane equivalence 6/6** (`scripts/g9b_lane_equivalence.py` over `ledger_backfill_2026-08-11`): 4 charts pixel-identical to the archived daily PNGs; `_1`/`_2` differed only because WTI and the 2Y yield have printed new observations since 2026-08-11 — against a same-vintage daily re-render both are pixel-identical, so the harness now renders that control automatically and reports vintage-vs-wrapper explicitly. **Philly Fed target rendered from resolution alone** (`scripts/g9b_philly.py`): `zs(yryr%(IP))`→`ip@ip` via the formula path, Philly Fed diffusion index→`bocgx@surveys` on an EXACT token-set match at sim 1.0 over two 0.8 siblings; `validate.check_last_value` PASS on both lines (IP 0.0397, Philly 2.4614 vs 2.4 read off the source). **Daily lane green in the same commit:** `selftest_workflow` 254/254, `selftest_transforms` 43/43. Read-only enforced by a tripwire, not a convention: `bootstrap.seal_stores` replaces every `resolve.save_*` with a raiser in this process, and `selftest.py` (19/19 at G9b, 28/28 after the §13.12 answers, 31/31 after G9e step 1) asserts both that the savers raise and that no daily-lane-only module is importable from the lane. **One additive change to a shared module**, per §13.5: `render_row` now also returns `drawn`/`window`/`common_freq` so `check_last_value` can run on the data BEHIND the pixels instead of a re-derivation (a window-ZS re-evaluated over a different window is a different number). Existing callers read named keys and are unaffected. **G9d + G9e CLOSED the same day (§13.14):** the store seal is permanent, not v1 — ratification lives in the daily approval round, and a shared store N teammates can write is the `mpcuhsro` class with N entry points. Because no consumer can write, a copy can only go STALE, never WRONG, which is what makes plain file distribution sound. The four stores publish to `P:\Public\RenMac_Chart_Knowledge` automatically at the end of every `run_daily` path that can write one (`--approve`/`--retitle`/`--relegend`) — last and fail-soft, so an unmapped drive warns instead of failing a finished day. `scripts/build_teammate_package.py` ships `dist/haver-chart-<sha>-<date>.zip` built from `git ls-tree HEAD` (so `notes/`/`data/`/`outputs/` cannot ride along) with the style and catalog modules vendored and no credential inside; `configure.py` generates the teammate's Claude config rather than having them hand-edit absolute paths. Proven from a clean unzip: 31/31 with a real RenMac-styled render. | **G9a SIGNED; G9b BUILT + PROVEN; G9d DECIDED (no writes, permanent); G9e DONE (zip proven from clean unzip) — all 2026-08-20. G9c (live chat run) is Aman's** |
| **G10** | **Remote lane (§14) — the SAME two tools served over Streamable HTTP from a Windows host that has DLX, behind Entra sign-in, registered in Claude as a custom connector.** What it buys is one thing: the chart lane stops requiring the operator's machine, so `claude.ai` and mobile work with no local Python and no local DLX. Transport changes; nothing else does. The pilot host is the **Azure AVD** session host — deliberately a test bed and not a destination (an AVD signs out on an idle policy and a pooled host discards the install, so "always on" is not a setting it has). It is there to answer the one question nothing else can answer cheaply: **does DLX serve a non-interactive caller, and does it survive a disconnected session?** The G9e teammate zip is already the deployment artefact — every external path is an env var and the style + catalog modules are vendored, so installing the lane on a server is an unzip. Four latent defects that stdio hid must be fixed first, and three of them bite with a **single** user because Claude issues parallel tool calls: the output filename collides (`lane._save_path` → `<stem>.png`, and the render-then-read-back gap can hand caller A caller B's chart), the figure is never closed (unbounded leak in a long-lived process), pyplot global state races, and `haver_search._READY` latches dark forever on one transient failure. The first three are fixed **inside the lane** (a `threading.Lock` around `render_row` plus `plt.close("all")`), so the daily lane's render path stays byte-for-byte untouched. Edge is a **Cloudflare named tunnel** onto `chart.hvr-mcp.work` — outbound-only, so no inbound port and no firewall change on a managed desktop. Auth is the metadata server's `AzureProvider` recipe with its own app registration; `mask_error_details` flips to TRUE in HTTP mode only (the guardrails already reach the client as `ToolError` and pass through masking — what masking stops is an accidental stack trace leaking a path or a connection string on a public endpoint). **The Entra assignment list is the licence boundary, not just access control:** `Haver.direct("on")` runs under the server account's entitlement, so the pilot assigns exactly one person — one user, one DLX, his own licence, reached through a different door — and G10h makes adding a second name a hard stop pending written confirmation from Haver. **G10c + G10d PASSED 2026-08-23** (§14.15, §14.17). Phase 1: the four fixes landed, three inside the lane so the daily render path is byte-for-byte unchanged. Phase 2: `HAVER_CHART_HTTP=1` serves Streamable HTTP on loopback behind Entra, stdio unchanged and proven by a real JSON-RPC handshake, `/mcp` 401 unauthenticated. **G10b findings (§14.16):** DLX serves a non-interactive caller with the desktop app CLOSED — it needs the credential, not the window — but an EXPIRED credential raises a GUI login modal, so the un-credentialed path blocks rather than fails, and a blocked pull inside the render lock would wedge every later render; hence the lock timeout and render-liveness `/health`. Green in the same commit: selftest 37/37, g10d 8/8, workflow 254/254, transforms 43/43, `run_daily --selftest` ALL PASS, lane equivalence 6/6. **Next: G10e (tunnel + Entra registration).** |

### G8c recall/park breakdown — the description-only set (2026-06-30, seed AND learned bypassed)

Per Aman's ask, ran the resolver LIVE across ALL 33 description-only (non-formula) series in the 26 samples (`scripts/g8c_recall_sweep.py`; live catalog driven in-process through the MCP's own `queries.build_search_query`+`db.run_query`, lexical FTS, 25-cap — `scripts/g8c_haver_query.py`). **Day-one auto-resolve = 16/33 (48%), with 0 silent mis-binds** (the gate is doing its job — every recall failure is a non-bind). 16 parked (ambiguous/recall), 1 parked-clarify (native-MA WGTO, correct by design).

Among the **8 keyed-truth** series: 3 resolved-CORRECT (texas DFBACTS rank-8 / DBACTS rank-4 within the 25-cap on the geography+discriminator variant — verbatim buried them at 38/26, confirming the search-cap fragility Aman flagged; BECE rank-1), **0 WRONG**, 1 relevance-miss, 3 recall-miss:
- **relevance-miss — LJQTPA (JOLTS Quits Rate)**: truth surfaced at **rank 1**, but the read's `[-4]` lag-tag polluted the token set and ≥8 near-identical JOLTS siblings cleared the Jaccard floor with no margin → ambiguity park (gate parks rather than guess). Reachable; a Stage-1 lag-tag strip before search would let it bind.
- **recall-miss — PCUSERH (PCE core services ex-housing chain price)**: **genuinely unreachable** — absent from every variant even at the 100-cap and under alternate phrasings. Canonical learned-store case.
- **recall-miss — FFEDTAR/FFEDTARE (Fed Funds Target Rate)**: pool=0 because the read `sa_hint=nsa` filter **zeroed all results** (the series isn't nsa-tagged). Unfiltered, the `Fed Funds Target Rate` discriminator surfaces FFEDTAR@rank-19 and FFEDTARE (the EOP truth)@rank-22 — **both inside 25**. FIXABLE: drop/soften the `sa_status` filter when it returns zero (and the verbatim `Federal Open Market Committee:` prefix buries it — discriminator-alone is what lands it).
- **recall-miss — LSWP (ECI wages & salaries, private)**: buried past 25 (best rank ~45 within 100). Cap-raise (~50) or learned store.

**Learned-store equilibrium proven** (`g8c_recall_sweep.py store`, on the genuinely-unreachable PCUSERH): RUN-1 cold → parks; human supplies `PCUSERH@USECON` once → `learned_descriptors.json`; RUN-2 → auto-resolves via the learned fast-path (`via=(learned)`) even though search still can't reach it. So the unreachable/buried tail converts to one-tap after a single supply.

**Production read for confirm_all**: day-one is "meaningful hand-supply early" (~half auto, half parked) but **safe by construction** (0 mis-binds; parks route to Teams). The 48% is a FLOOR — seed (`trusted_tickers`) AND learned store were both bypassed; with them warming (the ~43 recurring releases), the approve-without-correction rate climbs. Parks cluster in two shapes: (a) generic multi-sibling concepts (PCE chain price, ISM indices, housing starts variants, computer orders — many near-identical Census/BEA/Conf-Board siblings → ambiguity parks), and (b) verbatim-buried / differently-named series (FFEDTAR prefix, LSWP ECI, PCUSERH). Survey families (Texas, Philly, Richmond service) resolved cleanly on the discriminator variant. Artifacts: `outputs/g8c/sweep_report.json`, `sweep_hits/`, `sweep_hits_100.json`.

### G8c bounded recall fixes + re-measure (2026-06-30, Aman-directed)

Implemented in `resolve.py` — **the exact-token-set gate stays the bind precondition; the fixes only feed it / clean the query, never relax it**:
- **(i) SA-advisory retry-unfiltered** — a READ `sa_hint` must not HARD-FILTER search to zero (the read can be wrong; the catalog may tag SA differently). On a zero-result filtered attempt, retry unfiltered; the SA cross-check (`_meta_reject`) still guards the bind.
- **(ii) lag-tag strip** — `_strip_lag` removes the `[-n]` bracket (a display attribute) before BOTH the search query and the token-set gate.
- **(iii) bounded `SEARCH_CAP = 30`** (top-30, NOT 50), passed as `max_results`. `g8b_demo` still 29/29.

Re-measure across the 33 description-only series (seed + learned still bypassed — clean floor; `g8c_recall_sweep.py remeasure`, driven from `sweep_hits_100*.json` sliced to each cap):
- **Day-one auto-resolve: 48% (16/33, pre-fix) → 52% (17/33, post-fix).** The gain is **LJQTPA (JOLTS Quits)**: lag-strip removed the `[-4]` → unique exact bind to `LJQTPA@USECON` (was a relevance/ambiguity park; truth was already rank-1).
- **Cap 25→30 reject-load delta**: **+0 binds, +212 cross-check-passing candidates the gate now rejects, and 0 NEW exact-token-set siblings.** The wider net is *pure reject-load* — it gives the gate more to reject but introduces no new mis-bind surface; gate integrity preserved. `LSWP` (~rank 45) is still past 30 → learned store, exactly as predicted.
- **0 WRONG binds at either cap.**
- Residual keyed-truth parks (all learned-store / honest-ambiguity territory, no recall *bug*):
  - **PCUSERH** — genuine unreachable (absent even at cap-100); learned-store closes on one supply (proven).
  - **FFEDTAR/FFEDTARE** — the SA-retry **fixed the zero-pool recall**: `FFEDTARE@USECON` (the EOP truth Neil wants) now surfaces and exact-matches, and the EOP **agg cross-check correctly EXCLUDES** the avg `FFEDTAR@USECON`. It now parks only because TWO same-name EOP series tie on exact+EOP and differ only by frequency (`FFEDTARE@USECON` monthly vs `FFEDTAR@WEEKLY`) → a correct 1-of-2 freq disambiguation (human picks; `corrections.json`'s `FFEDTAR@USECON` avg is stale vs the EOP directive). Improved in KIND: zero-pool recall miss → honest disambiguation park.
  - **LSWP** — buried past cap-30 (~rank 45) → learned store.

**Net**: confirm_all day-one floor is **52% with 0 mis-binds**; every residual is unreachable/ambiguous and learned-store-closable, so the floor is a **decay curve**, not fixed. The cap-30 bump bought recall headroom with **zero new bind risk** (the metric Aman asked to see). No `run_daily` seeding wired pending Aman's read of these numbers.

### Texas FINALIZED (2026-06-30)
Title approved (option 1, Aman's subtitle): **"Texas factories see brighter days ahead despite soft present"** / subtitle stored paren-free **"current vs six-months-ahead, diffusion index, SA"** (renmac_style wraps the single outer paren → displays "(current vs six-months-ahead, diffusion index, SA)", inner parens FLATTENED per Aman). Ledger row → **APPROVED** (both resolution + title approves in hand), final RenMac render at `outputs/g8c/texas_reconstruction.png`. `scripts/g8c_finalize.py`.

### run_daily WIRED end-to-end (2026-06-30) — the chained G8 invocation
The full chain `ingest→classify→seed→G8 read→resolve→confirm_all→title→render` is now ONE `run_daily.py --approve` invocation (was: seed-to-`awaiting_ticker` + the old manual ticker round-trip). New seams:
- **`src/haver_search.py`** — runtime catalog `search`/`get_meta` reached IN-PROCESS through the MCP server's OWN modules (`queries.build_search_query`+`db.run_query`, lexical FTS over the read-only Neon mirror) — identical code path to the authoring-time MCP, **no agent in the loop**. `available()` False → description slots park (fail-safe). Smoke-tested live: search + `get_meta DFBACTS@SURVEYS` return real catalog rows.
- **`run_daily._read_resolve`** — G8 Stage-1 `chartspec.read_chart_spec` (Opus vision on each seeded asset PNG) → `build_slots` → `resolve_chart` (live catalog search/get_meta; `confirm=lambda True` for the auto path since every hit is a real catalog series) → **post-resolve DLX verify** of each bound code (`confirm_ticker`; a pull failure parks that slot, safe direction). Idempotent (skips rows that already carry `chart_spec`).
- **confirm_all default** (`--resolution-mode confirm_all`, `selective_park` preserved): parked slots get the focused per-series ask (`run_series_roundtrip`) → once every slot binds the chart is RESOLVED → `run_resolution_roundtrip` posts the full-set summary → `[id] approve` → `run_title_roundtrip(from=RESOLUTION_APPROVED→AWAITING_TITLE)` → APPROVED.
- **`src/build_chart.py`** — generic RenMac reconstruction from a resolved row (the LAST + least-tested seam): pulls every bound code (DLX `g4_lib.pull`), evaluates formulas via the G3 engine (`transforms.transform`, plain series unified as a 1-mnemonic formula), derives the x-end from the latest plotted period, shades RECESSM2 if read-flagged, hands clean labels to `render.py`. Best-effort: a render snag is reported on the row (`_render_error`), never aborts the run. **VALIDATED live** on the real Texas resolved row — pulled both Dallas Fed series + RECESSM2, derived end 2026-06-30, flattened subtitle, both lines present.
- **`run_daily._seam_report`** — per-chart shakedown report (read n_series/axis_mode → per-slot AUTO/PARK/SKIP with reason + similarity → render outcome → charts-by-status), the deliverable of the live run.

**LIVE SHAKEDOWN ran (2026-06-30) — chain held end-to-end on TWO real emails** (ingest→classify→G8 read→resolve→live Teams post all worked; `cecbf400bb_1` AUTO-RESOLVED ehn/yin@cbdb dual-axis with a clean confirm_all summary — happy path live for the first time). Two display defects in the parked-series ask, both FIXED (`teams.format_series_ask` + `approval.run_series_roundtrip` now pass the FULL slot set):
- **(2) candidate dump → top-3 ranked.** The ask dumped the ENTIRE raw candidate pool (27 for unemployment, 12 for auto-plans) — unusable. Now shows only the **TOP 3 by the relevance gate's descriptor similarity, WITH scores** (from `slot["candidate_detail"]`, already sorted exact-then-sim); full pool stays in the ledger, not Teams.
- **(3) auto-bound series hidden in a partially-parked ask → full-picture.** A chart with some auto-bound + some parked showed only the parked ones, hiding what the others resolved to until the next pass (broke confirm_all's "see every bind"). The ask now shows the FULL per-chart set — auto-bound series WITH their resolved ticker (`#0 … → CODE@DB (auto, exact/sim)`) alongside parked ones — matching the all-auto summary path. (post-once preserved: `ask_sig` still keys on the pending set, so the richer format lands on the next genuinely-new ask, not a re-post spam.)
- **(4) learned-store write CONFIRMED**: a human-supplied ticker (via the series round-trip) reaches confirm_all approve as a RESOLVED slot → `_learn_from_row` writes `description→code` to `learned_descriptors.json` (`_norm_key` matches the fast-path lookup; these parked descriptions carry no embedded lag tag, so keys align) → tomorrow's same unemployment/buying-plans/JOLTS descriptions auto-resolve. `g8b_demo` J/J2 prove the write + instant re-resolve; **29/29 still green** after the display change.

**FIRST RENDERS out → TRANSFORM-SEAM BUG caught + fixed (2026-06-30).** The confirm_all summaries carried the worded transforms (JOLTS "3-month moving average", buying-intentions "6-month MovingAverage") and were approved, but `build_chart` plotted the RAW pulled series for description-bound slots — it only ran `slot["formula"]` through G3, treating an `applied_transform` PHRASE as level. **Silent-wrong** (chart looks right, plots un-transformed data); it slipped because texas (the only prior render test) was LEVEL, so the renderer→G3 join was never exercised — G3 was validated standalone (G4), the renderer on texas (no transform), but the SEAM between them only fired now. **Fix:** `build_chart.phrase_to_haver` translates the read's `applied_transform` into a Haver/G3 formula on the bound mnemonic (`3-month moving average`→`movv(code,3)`, `6-month MovingAverage`→`movv(code,6)`, `% Change - Year to Year`→`yryr%(code)`, `N-month %Change-ann`→`difa%(code,N)`) and evaluates it before plotting; **fail-loud** on any present-but-unrecognized transform (never a silent fall-back to level). **Audit of the 4-chart run:** `cecbf400bb_0`/`_1` level/level (correct as-is); `cecbf400bb_2` (6mma×2) + `c45a62aebf_0` (3mma×2) had the bug → **re-rendered, noise collapsed into smooth MA lines** matching the screenshots. Translator unit-tested incl. fail-loud; `g8b_demo` 29/29 + selftest still green.
- **LAYOUT — legend/Source collision FIXED (all charts)**: the legend sat flush against the figure-level Source line. `render.py` lifts the legend anchor (single −0.18→−0.14, dual −0.20→−0.16), reserves a bottom band (`subplots_adjust(bottom=0.16)`) and pads the save (`pad_inches=0.25`) → clear gap. Re-rendered all 4.
- **Subtitle parens CONFIRMED intentional**: `renmac_style` wraps the subtitle in parens itself (`f"({subtitle})"`), so callers pass subtitles **paren-free** and the renderer adds exactly one set — consistent, by design.
- **TRANSFORM LABEL on the chart (the visual tripwire, 2026-06-30)**: a rendered chart now SAYS its transform, so a future dropped transform is self-evident (label "6-month moving average" over a visibly-raw line = eye-catching contradiction — exactly the signal whose absence hid today's bug). `build_chart.transform_label` derives a clean phrase from the evaluated formula's OUTER function (`movv→"N-{unit} moving average"`, `yryr%→"% change, year-over-year"`, `difa%→"N-{unit} annualized % change"`, `zs→"z-score"`; unit from common_freq; None for level/expression — never raw formula syntax, so render's clean-label guard holds). Placement follows g4 house-style: **SAME transform on every series → SUBTITLE** (`(…, 6-month moving average)`); **DIFFERENT per series → each in its LEGEND label**; **LEVEL → nothing**. Today's two MA charts re-rendered with the subtitle note (`cecbf400bb_2` "(…, 6-month moving average)", `c45a62aebf_0` "(Layoffs and Discharges rate, 3-month moving average)"); level charts stay clean. All four placement branches dry-tested; g8b 29/29.
- **JUNE 9 PRE-RUN INSPECTION (`scripts/inspect_date.py`, dry — ingest+vision-read into a temp dir, NO seed/approve, real run stays cold)**: June 9 is a RICH transform-variety day — 4 raw-Haver charts (all docx/bloomberg): (1) JOLTS openings + NFIB positions-hard-to-fill **z-score** (×2, phrase); (2) FNH `zs(yryr%(FNH))` formula + NFIB capex-plans **z-score** phrase; (3) 2-Yr Treasury + NFIB higher-interest **level, dual-axis, recession**; (4) NAR median price + homes-available **yryr%** (×2, recession). Tally: yryr% ×2, zs ×1 (formula) + z-score phrase ×3, level ×2 (dual). **COVERS the untested-e2e types yryr% AND zs** (NOT difa%-annualized or summed — those still need a later backdate). **CAUGHT PRE-RUN:** the `"Z-Score"` PHRASE was unmappable by `phrase_to_haver` → would fail-loud snag the render on 3 series; added `z-score/standardized → zs(code)` mapping (G3 ZS), labels as "z-score". g8b 29/29.
- **DESIGN NOTE (PARKED — top of post-shakedown hardening list): ratify the RENDER, not just the spec.** confirm_all approves the RESOLUTION (ticker/transform/axis), not the rendered output — which is why a render-seam bug (right spec, wrong application) passed the gate. End-state: a render PREVIEW posted into the approve loop so the human ratifies the actual chart ("right spec" = "right chart"). Deliberately NOT bolted on mid-shakedown (touches gate ordering + Teams image-posting; adding a major gate change mid-run = testing two things at once, can't attribute failures). Finish the shakedown on the current architecture, then spec render-preview as its own pass.
- **HARDENING (PARKED — 2026-07-01, from the May-12 store-poisoning): TRANSFORM-APPROPRIATENESS guard (double-transform detection).** The descriptor-relevance gate matches on "does the descriptor match," NOT "is this the right KIND of series for the applied transform." On `2026-05-12_72e42bb93c_3` the read `CPI-U: Lodging Away From Home % Change - Year to Year` bound `mpcuhsro@usecon` at sim 0.818 — but `mpcuhsro` is ALREADY the m/m %Chg of the index (`get_meta` descriptor literally "…(SA, M/M %Chg)", agg_type `NDF`), so applying yoy on top gave yoy-of-an-already-differenced-series → garbage vertical spikes. Correct series was `UHROM@USECON`, the LEVEL index (agg_type `AVG`), which yoy transforms cleanly. This is a real silent-wrong class: a HIGH-similarity bind that's the wrong kind of series for the transform, and it doubly hurt because the confirm_all approval WROTE the wrong descriptor→code into `learned_descriptors.json` (poisoned fast-path → future invisible auto-mis-binds). **Guard idea:** when a candidate's `get_meta` descriptor/`agg_type` signals it is ALREADY a rate/change (`%Chg`, `M/M`, `Y/Y`, `NDF`, `difa`/`yryr` in the name) AND the read wants an ADDITIONAL % transform (`yryr%`/`difa%`/`diff%`), do NOT auto-bind — PARK for confirmation (it's usually the wrong-kind bind). Cheap metadata check, catches the double-transform before it renders AND before it poisons the store. (Store cleanup is manual today: purge the bad `learned`/`trusted` entry + re-seed the correct code — see the 2026-07-01 fix.) **BUILT 2026-07-01** (`resolve.double_transform_reason` + `_cand_already_change`/`_read_wants_pct`): applied in BOTH the description-path bind AND the learned fast-path (defense-in-depth — a poisoned learned entry parks rather than serving the double-transform from cache); the negative control (yoy on a genuine LEVEL index) still binds, so it does not over-park the common CPI-yoy case. `g8b_demo` E4 (descriptor `%Chg`), E4b (agg_type `NDF`), E4c (level negative control) → **32/32**.
- **BUG CAUGHT pre-run:** `resolve_chart` RETURNS the resolved slots (resolve_slot yields fresh dicts); the first wiring ignored the return and stored the un-resolved `slots` → **every chart parked**. Fixed (`row["series"] = R.resolve_chart(build_slots(...), **kw)`); re-verified on a real asset — formula slot auto-binds `cbhm@usecon`, description slot parks with "24 descriptor-similar candidates". Offline `--selftest` still ALL PASS (front half un-regressed). | **G8a PARTIAL-SIGNED; G8c-v2: relevance gate + search recall + confirm_all/title/learning BUILT (g8b 29/29, g5/g7 green); LIVE texas RESOLVED 2/2 (gate-only safety, all-SA candidates), STOPPED at posted resolution summary — awaiting Aman's `approve`** |

### THREE WORKFLOW FEATURES from the May-12 run (2026-07-01) — `scripts/selftest_workflow.py` 28/28, g8b 29/29, run_daily selftest green
Built + offline-proven; feature 1 verified against the REAL May-12 inbox.
- **(1) THREAD-SCOPED quote-back dedup at INGEST (correctness-sensitive).** A reply-with-addition ("RE: for the daily" re-quoting the morning's charts + adding one) was re-ingesting the originals and round-tripping duplicates. `ingest.dedup_threads` groups messages by Graph **`conversationId`** (added to the `$select`; `EmailResult.conversation_id`) and, within a thread, marks any extracted image whose **content-hash** (`Asset.sha1`) already appeared in an EARLIER message as `duplicate=True` → dropped from `raw_haver` (so it never seeds/round-trips) but still shown `[DUP-SKIP]` in the ingest report. **Thread-scoped** (a genuinely-recurring chart in an UNRELATED thread is never clobbered) and runs BEFORE seeding. Same-message twins don't self-mark (record hashes only AFTER checking each message). **VERIFIED on the live May-12 inbox: exact content-hash CAUGHT it** — the 13:19 "RE:" re-quoted the 13:16 message's 3 charts with byte-identical sha1 (`bf63acad7d`/`1146512260`/`69fea060c7`, no re-encoding) → all 3 flagged, the genuinely-new `image008` (`144ff38f4e`) survived, and the unrelated 10:54 "for the daily" (different `conversationId`, one shared hash) was untouched. **Exact hash sufficed — no perceptual/descriptor fallback needed for this case.** (The already-seeded May-12 duplicate rows `72e42bb93c_1/_2/_3` predate the fix and remain `done` in that backfill ledger; harmless, removable on request.)
- **(2) `approve all` BULK token at BOTH gates.** A chart-agnostic `approve all` / `approve *` / `approve-all` (NO `[chart_id]`) ratifies every cleanly-approvable chart in one message. `teams.is_approve_all` (rejects a `[id]`-tagged reply and bare `approve`); a new tag-agnostic `Transport.global_replies` (Graph = whole-chat scan; Stub = merge all buckets). `approval._bulk_approve_signal` fires only for an approve-all **after** the earliest still-open ask (cursor gate → a stale pre-ask token doesn't count). **Resolution gate:** guardrail `_fully_bound` (every slot resolved) means it can NEVER sweep a parked/low-confidence chart; a per-chart reply (skip/correct/approve) always WINS. **Title gate:** accepts the PROPOSED (option 1) for each un-answered chart — safe because titles are low-stakes, editable via `--retitle`, and the transform label is non-suppressible (no silent-wrong path). Both gates ECHO the ratified set (`[approve all] … (N): id,id`).
- **(3) `--retitle <chart_id>`.** Re-opens a FINISHED chart's TITLE round-trip WITHOUT re-resolving (tickers/transforms/axes untouched). Pass 1: reset `done/approved → resolution_approved` clearing only title + render-forward state → the title round-trip re-fires and posts fresh options in Teams. Pass 2 (same command): the reply is harvested → APPROVED → **re-renders the PNG** (a retitle that doesn't regenerate the chart is useless). Guards: unknown id / not-fully-resolved / already-mid-retitle all handled. Fixes the terminal-`done` dead-end (a post-completion title edit sent to Teams was previously ignored). | **Three May-12 workflow features BUILT + proven (workflow selftest 28/28; dedup verified on live May-12; g8b 29/29; run_daily selftest green)** |
### PRE-PRODUCTION HARDENING CLOSED (2026-07-01) — items 1–3 + README
Three gating items closed before MANUAL production; `README.md` built at project root from this plan (kept in sync as items land).
- **(1) MAY-12 STORE CLEANUP — confirmed.** `learned_descriptors.json[cpi-u: lodging…yoy]` = `mpcuhsro@usecon` → **`UHROM@USECON`**; `trusted_tickers.json` `mpcuhsro` removed, `uhrom: UHROM@USECON` added; test pollution (`series a/b`, `aaa/bbb`) purged. Verified no `mpcuhsro@usecon` remains in either store.
- **(2) TRANSFORM-APPROPRIATENESS GUARD — BUILT.** `resolve.double_transform_reason` (+ `_cand_already_change` reading get_meta descriptor `%chg`/`m/m`/`y/y`/`difa`/`yryr` and agg_type `NDF`; `_read_wants_pct` reading the read's `applied_transform`/formula). Applied in BOTH the description-path bind AND the learned fast-path (defense-in-depth: a poisoned learned entry PARKS, never serves the double-transform from cache). Negative control (yoy on a genuine LEVEL index) still binds → does NOT over-park the common CPI-yoy case. `g8b_demo` E4/E4b/E4c → **32/32**; selftest_workflow 28/28; run_daily selftest green; lint clean.
- **(3) ONE MORE VALIDATION BACKDATE — 2026-05-28.** The one inbox date hitting BOTH still-untested render seams: **summed `A+B+C`** (`BEEM1+(BEEM2+BEEM3)`, CEO-confidence employment composite @CBDB) + **difa%-annualized** (`difa%(movv(NMSCNX,3),3)`/`difa%(movv(NMOCNX,3),3)`, 3m/3m SAAR cap-goods orders @USECON), plus bonus dual-axis-different-L/R + nested `zs(yryr%())`. Rendered through the PRODUCTION seam `build_chart.render_row` (DLX pull → G3 → render) to `outputs/g8_validation_0528/` — difa% shows the ≈ −49% GFC trough (matches the G4 pin), summed composites match their addends. (`scripts/validate_0528.py`; candidate found via a wide-window inbox scan + `inspect_date.py` across Apr–Jun dense days.)
- **STILL OPEN → the gate to HEADLESS: render-preview gate** (ratify the rendered chart, not just the spec — the gap both the transform-drop and store-poisoning bugs exposed). First production is **MANUAL** (run each morning, approve in Teams, eyeball every render) for ~a week; headless waits on the preview gate. | **Items 1–3 CLOSED; MANUAL production next; headless gated on render-preview** |

### NO-COMMENTARY (title-less) + EQUAL-MARGIN LAYOUT (2026-07-02, revised)
- **No-commentary is a title-round-trip CHOICE, not a run mode (revised).** First cut was a `--no-title`/`--replicate` flag that *bypassed* the title leg. Revised per Aman: the decision belongs in Teams, *after* he's seen the chart. So a bare/commentary-less chart flows the **normal path incl. the title round-trip**, and the round-trip gains a **`none`** option: `[id] none` (or `no-title`) → renders **title-less**. Title choices are now `1 / 2 / title=… / subtitle=… / none`. Wiring: `teams.parse_title_reply` returns `{"title":"","subtitle":"","no_title":True}` on a bare `none`/`no-title`; `approval.run_title_roundtrip` persists `no_title="1"` on APPROVE when that choice is made (and clears it on any real title, so a later pick renders titled); `render_row` renders `title=""` when `no_title` is set and there's no `chosen_title`. **The `--no-title`/`--replicate` flag and `_skip_titles` are RETIRED** — no new run mode. `--retitle` re-opens the same round-trip, so "add a title later" is automatic (retitle → pick a real title instead of `none`). **Transform label stays non-suppressible:** `none` = no title + no descriptive subtitle, but a transformed chart still shows e.g. `(3-month annualized % change)` (confirmed on a live difa% title-less render). **No-commentary proposals:** `_titles` never raises — on any failure it returns `[]`, and `format_title_ask` still posts a `none`/custom ask when proposals are empty (confirmed, no error). g8b 32/32, workflow **36/36** (added `none`/empty-proposals/custom-not-swallowed checks).
- **EQUAL, tight margins so the plot fills the width (revised).** First cut left-aligned with loose right whitespace; revised per Aman to **tight/equal both sides**. `render.py` now `subplots_adjust(left=LEFT_MARGIN=0.085, right=1-0.085=0.915 single, top=0.88, bottom=0.16)` (save WITHOUT `bbox="tight"`). **Dual-axis widens the right margin by `DUAL_RIGHT_PAD=0.05` → right=0.865** so the RIGHT y-axis tick labels clear. Eyeballed on live renders: single-axis (difa%, equal margins), dual-axis (levels L/R, right labels 55000..80000 un-clipped), and a title-less difa% (transform label kept). Check script: `scripts/render_check.py` → `outputs/g8_layout_check/`.

### LEGEND-LABEL STORE (Opus→confirm→store) + X-AXIS DATE FORMAT (2026-07-02)
- **Legend leak (the big fix).** Live 2026-07-02 charts `_1`/`_3` rendered legends as RAW MNEMONICS (`NRS - NRSI7`, `LITRTRDA`, `LETPRIVA`, `LEPRIVA`). Root cause: the vision read carried the **mnemonic as `base_descriptor`**, `_clean_label` returned it verbatim, and `render._assert_clean`'s dirty-regex (`[\w%]\(|@`) **does not catch a bare mnemonic** (no `@`, no `word(`) → silent degrade. Rebuilt as a **confirmed, learned artifact**, same discipline as tickers/titles (LLM proposes → human ratifies → persists → never re-asked):
  - **Opus grounding** (`propose.propose_series_label` / `propose_composite_label`, `claude-opus-4-8`): shortens the REAL `get_series` descriptor using standard abbrevs (AHE/CPI/…) WITHOUT dropping meaning-qualifiers (SA/NSA, index/level, units, base year); never identifies/invents. Composite (e.g. `NRS - NRSI7`) hands Opus BOTH operands' descriptors + the operation → one label; a subtraction isn't asserted as a clean "ex" unless the descriptions support it (Opus returns a `note` flagging phrasing risk; that's why it routes to human confirm).
  - **Folded INTO the resolution round-trip (no 3rd tap):** `format_resolution_summary` shows a `legend:` line per series (store-confirmed shown silently, else Opus-proposed); override with `[id] legendN=…` (parsed by `_LEGEND_OVERRIDE_RE`, coexists with code/field corrections); on `approve`, `_persist_legends` writes each confirmed label to the store. `--retitle` re-renders from the same store.
  - **Store `legend_labels.json`** (clarified-knowledge): **write-once, keyed canonically**. Two canonicalizers by key-kind (a single shared fn is impossible — the stores key on different things): `_norm_key` for TEXT stores (native_ma, learned); NEW `code_key(CODE@DB)` (upper/upper) for single series + `expr_key('NRS - NRSI7'→'expr:NRS-NRSI7')` for composites, both in `resolve.py`. A store hit renders **silently — no Opus, no re-confirm** (warms like the ticker store). save→load round-trip per store is regression-locked (the native-MA "why is it asking again" class).
  - **`_assert_clean` is now a real floor:** `build_chart._legend_label` uses (1) store label, else (2) a base_descriptor that is a genuine description (legacy), else (3) **RAISES/parks** — never falls back to the ticker (`_is_mnemonic_label` detects a label that is only the slot's mnemonics/operators). A guard that degrades to the raw ticker isn't a guard.
  - Confirmed labels for 0702: `LETPRIVA@USECON`→"AHE: Total Private (SA, \$/hr)", `LEPRIVA@LABOR`→"AHE Prod & Nonsuperv: Total Private (SA, \$/hr)", `LITRTRDA@USECON`→"Agg Weekly Hours Idx: Retail Trade (SA, 2007=100)", `expr:NRS-NRSI7`→"U.S. Retail Sales ex Gas Stations (SA, Mil.\$)". `_1` re-rendered canonical; `_3` previewed with the new title (retitle is Aman's step).
- **X-axis date format.** `render._style_time_axis`: month-level ticks render `%b-%y` ("Mar-25"); on spans > `_MONTH_SPAN_YEARS=5` the (Auto)locator's year ticks stay bare years ("2004"). Only the format string is set per span — the locator still decides resolution. Confirmed: `_1` (~1.5y) → Mmm-YY, `_3` (~26y) → years.
- Tests: workflow **51/51** (+14 legend checks: canon keys, save→load, override parse, summary line, propose→approve→persist→silent-reuse, and the mnemonic/composite RAISE floor). g8b 32/32, run_daily ALL PASS.
### st_force — EXPLICIT subtitle override (2026-07-02)
- **The subtitle-append duplication is closed by an explicit FLAG, not auto-detect.** Prior turn floated (B) auto-suppress-if-already-described; dropped — wording never matches (difa% "3m %chg" vs canonical "3-month annualized % change" → still dups). Replaced with `st_force` (subtitle-force): **DEFAULT unchanged** — a transform SHARED by all series is APPENDED to the subtitle (the non-suppressible tripwire stays ON, the SAFE default). **`st_force=true`** → subtitle used VERBATIM, nothing appended — an explicit human override (honored because asserted, not detected; Aman owns the subtitle then). Title round-trip syntax: `[id] title=… subtitle=… st_force=true`; `--retitle` accepts it identically (same round-trip). Wiring: `teams.parse_title_reply` parses+STRIPS `_ST_FORCE_RE` first (so it can't leak into the greedy `subtitle=(.*)`), returns `st_force` in the choice; `run_title_roundtrip` persists `st_force="1"` on approve (new ledger field); `build_chart.compose_subtitle(base, tlabels, st_force)` is the pure decision — st_force scopes to the SUBTITLE only (a MIXED-transform chart still routes to the legend, an orthogonal tripwire). Confirmed live: `_3` with `subtitle=y/y% chg st_force=true` renders `(y/y% chg)` — no dup; `_1` (no flag) still appends `z-score` (non-regression). Tests: workflow **59/59** (+8 st_force: parse/strip/no-leak, verbatim vs default-append, `_1` non-regression, mixed→legend, row-persist).

### phrase_to_haver — FULL Haver transform vocabulary pre-populated (2026-07-02)
- **`a7d2c0d8ae_2` snag was the fail-loud guard doing its job** — both slots read `% Change - Period to Period` (no formula), which `phrase_to_haver` didn't know, so it REFUSED to render rather than plot raw (the exact silent-wrong the seam-gate exists to stop). Nothing wrong shipped.
- **Root fix + broadening:** `phrase_to_haver` is rebuilt around one ordered flat mapper (`_flat_transform`) that closes the FINITE Haver DLX vocabulary instead of discovering it one chart at a time. `% Change - Period to Period` → `diff%(code,1)` (one NATIVE period — frequency-agnostic, so it's m/m on monthly `ipmfg`, not hard-coded "month"). Mapped families: **DIFF/DIFV/DIFA** (×`%`/`L`), **YRYR** (×`%`/`L`, G3 uses the series freq internally), **MOV[V|A|T]** (moving average/annual-rate/sum), **ZS**, **LN** — across the period qualifiers year-to-year / annual-rate / period-to-period / N-period. `Change` (no `%`) = arithmetic difference, `% Change` = percent, `Log` = log variant. **Composition:** `<transform> of <N>-period moving average` composes as `head(movv(code,N))` (e.g. `% Change - Year to Year of 3-month moving average` → `yryr%(movv(code,3))`) so a moving average is NEVER silently dropped by a flat rule matching only the head. **Floor stays fail-loud:** genuinely novel phrases RAISE; recognized-but-unsupported (YTD, index/rebase needing a base pin) RAISE with a specific message — never silent-level.
- **Label side (`transform_label`) expanded to match:** `diff%(x,1)` → "% change, period-over-period"; `diff%(x,N)` → "N-{unit} % change"; `diff(x,1)` → "change, period-over-period"; `yryr(x)` → "change, year-over-year"; `movt`→"N-{unit} moving sum", `mova`→"N-{unit} moving average, annual rate" (previously all MOV* mislabeled "moving average"); log/difference variants covered; `ln`→"natural log". `_2` re-rendered: subtitle `(% change, period-over-period)` (both series share it → subtitle, not legend), legends the real descriptions, x-axis `Mmm-YY`.
- **Was mapped (3, discovered one-at-a-time) → now pre-populated:** before = `movv` (MA), `yryr%` (yoy), `difa%` (annualized-N), `zs` (June-9). The gap that kept surfacing = `diff%`/`diff` (period-to-period + N-period), `yryr`/`yryrL` (level/log yoy), `difa`/`difaL` (level/log annualized), `difv%`/`difv` (per-period mean), `movt`/`mova`, `ln`, and composition. Tests: workflow **71/71** (+12: vocabulary table, compound-MA composition, novel-phrase floor).

### Human-supplied ticker drops the read's stale formula (2026-07-09)
- **`48c11cf71f_0` render SNAG: `ParseError: unexpected char at 11 in 'yryr%(CPI-U: Legal Services (NSA, Dec-86=100))'`.** Three legal-services series (CPI-U / HH-consumption PI / PPI) each read with `formula = yryr%(<DESCRIPTION>)` — Opus wrapped the transform around the **descriptor text** instead of leaving `formula=null` + `applied_transform="% Change - Year to Year"` (which it ALSO set correctly). Auto-resolve parked (formula unparseable); the human supplied `#0/#1/#2 code@db` in the Stage-1 ask; the bind path set `resolved`/`codes` but **left the bogus `formula`**, which then won at render → parse blew up on the `:` in the description. Fail-loud caught it (no wrong pixels), but it's a real seam bug.
- **Fix — `resolve.rebind_human_ticker(slot, code)`** centralizes the human-single-ticker bind for BOTH gates (Stage-1 supply in `approval` line ~168, Stage-2 `N=code@db` correction line ~250). A human binding ONE ticker reduces the series to a single code, so the read-`formula` (referencing the now-superseded description/mnemonic) must not ride into render. Transform preserved without the stale operand: **applied_transform present → drop the formula** (render rebuilds `yryr%(mnem)` via `build_chart.phrase_to_haver`, the one phrase→G3 source); **no applied_transform but a single-mnemonic parseable formula → rewrite that one token to the supplied mnemonic** (`yryr%(XYZ)`→`yryr%(NEW)`, transform kept); else drop. Repaired + re-rendered the live row (3× `yryr%`, dual-axis, readable legends, `(y/y% chg)` subtitle). Tests: workflow **76/76** (+5: description-formula dropped/transform kept, renders-clean, parseable-rewrite, token-swap, no-formula plain bind).

### Post-render edit flags: `--retitle` (title/subtitle/st_force) + `--relegend` (legends) (2026-07-14)
- **`--relegend <chart_id>`** added alongside `--retitle` so a finished chart's LEGEND can be edited without re-running the day or disturbing tickers/title. Mirrors `_retitle`: pass-1 resets `done/approved → resolved` (keeps `chosen_title`/`subtitle`/`st_force`), re-fires the resolution summary (with `legend:` lines); reply `[id] legendN=…` then `[id] approve`; the confirmed label persists to `legend_labels.json` (write-once, `code@db`/expr-keyed) and the chart is promoted STRAIGHT to APPROVED (no title round-trip re-fire) and re-renders. `learn=False` so a legend-only pass doesn't rewrite the learned-descriptor store. `--retitle` now documented as the title/subtitle/`st_force` path. Applied live on 2026-07-14 (`8cc2c82d64_1` legend → "Nonfarm Bus. Real Output/Hour (SA, 2017=100, y/y% chg)"; `f06a0e2c3d_0` legend → "CPI-U: Svcs less Energy, Rent & OER (SA, 6m %chg saar)" + `subtitle=Z-score st_force`). README gains a **Post-render edits** section (flag-by-intent table + worked examples). Tests: workflow **82/82** (+6: relegend unknown-id, pass-1 reset preserves title/tickers, legend override applied, ratify→APPROVED title intact, label persisted).

### Composite series trimmed by a cross-day stale parquet cache (2026-07-14)
- **Symptom:** `2026-07-14_8cc2c82d64_1` series-2 `zs((NFIB7 − NFIB6))` stopped in May and the newest June dip was missing, while the x-axis ran to June.
- **Root cause (two-part):** (1) `g4_lib.pull` caches to `outputs/raw/<code>_<db>_<start>.parquet` keyed by code only — **no vintage/TTL**. `nfib7` was re-pulled the same day (had June) but `nfib6` was a **July-1 cache** (ended May, before the June NFIB release). A difference is defined only on the operand **overlap**, so it truncated to May. (2) The render window END was `max(last-date over ALL raw operands)`, so the *longer* raw operand (`nfib7`'s June) stretched the axis PAST where the composite line actually stopped → the line dangled short of the edge, no error.
- **Fix (two guards):** (1) **Daily cache freshness** — `pull` re-pulls any parquet written on a *prior calendar day* (`_cache_fresh`; `HAVER_CACHE_TTL_DAYS` env override, default 0=today-only), so every operand in one run shares one vintage. (2) **Composite-aware window end** — `build_chart._window_end`: per plotted slot end = **min** of its operands' last dates; window end = **max** across slots (so the longest plotted line still reaches the edge, and a composite never dangles behind a phantom axis). A cross-operand date skew is logged **loud** on stderr (`[vintage-skew] …`). Re-rendered `8cc2c82d64_1` — both NFIB now end June, the dip shows. Tests: workflow **86/86** (+4: skew→composite-overlap end + loud warning; fresh→June, no warning).

### Legend wrapping + dual-axis LHS/RHS tagging + multi-override parse fix (2026-07-15)
- **Symptoms on the 2026-07-15 run:** `e3558873f1_0` legends spilled off both edges AND slot-0's label had swallowed slot-1's (`"MBA: 30Y FRM Contract Rate (%, y/y change) legend2=MBA Purchase Loan Apps Index (NSA, y/y %chg)"`). Dual-axis charts gave no LHS/RHS hint.
- **Bug 1 — greedy legend parser (`teams.py`):** `_LEGEND_OVERRIDE_RE = legend\s*(\d+)\s*=\s*(.+)` `.match`ed the whole line, so `legend1=` captured through `legend2=…`. Fix: `_LEGEND_MARK_RE` + `finditer` — each `legendN=` value runs up to the **next** marker (mirrors the `_CORR_MARK_RE` slicing), so multiple overrides per line parse independently. Repaired the poisoned `MBA30C@SURVEYS` key in `legend_labels.json` and the ledger slot in place.
- **Bug 2 — legend overspill (`render.py`):** legends were always side-by-side (`ncol=2/3`). Added `_legend_layout(labels, plot_w_in, cap)` — estimates entry width in points and picks the widest column count that fits; long labels collapse to `ncol=1` and **stack vertically**. Bottom margin now `0.135 + 0.05*nrows` so a tall legend clears the x-axis above and keeps a gap above the Source line; legend anchored `loc="upper center", bbox=(0.5,-0.11)`.
- **Feature — dual-axis side tags (`render._axis_side_label`):** on `axis_mode=="dual"`, each legend is tagged with `LHS`/`RHS` — **inside** a trailing qualifier bracket if present (`… (NSA, y/y %chg)` → `… (NSA, y/y %chg, RHS)`), else a fresh bracket (`ISM … Index` → `… (LHS)`). Idempotent (no double-tag on re-render); single-axis untagged.
- **House-style refinement (`build_chart._has_confirmed_legend`):** a **confirmed** (store-ratified) legend already carries its transform qualifier, so the mixed-transform auto-append is now **suppressed** for it — the append used to duplicate the qualifier (`… (NSA, y/y %chg), % change, year-over-year`) and push the axis tag outside the bracket. Re-rendered both charts: tags now sit inside the brackets exactly per spec. Tests: workflow **95/95** (+9: multi-override split, axis-tag in/append/idempotent, layout stack-vs-pack, confirmed-legend detection).

### Sparse Haver chart wrongly skipped by the colored-fraction floor (2026-07-20)
- **Symptom:** `Document7.docx:image2.png` (a genuine Haver PCE-less-F&E vs Unemployment chart) skipped with `no plotted series (colored_frac=0.0033)`; nothing seeded for the day.
- **Root cause (`classify.preclassify`):** the FIRST gate was `colored_frac < T_COLORED_MIN(0.004) → skip`. A sparse Haver chart (thin navy+teal lines, mostly white) measured `colored=blue=0.0033` — below the floor — so it was killed **before** the `blue_frac>0 → raw_haver` check. Downsampling isn't the cause (colored_frac ≈0.0033 at 320/480/640/800px); the chart is genuinely sparse. Broke the module's own stated invariant ("never SKIP a real raw-Haver chart on its own"). Note `image1` (FOMC Hawk-Dove, `orange_frac=0.0062`) is a finished multi-color chart — correctly skipped.
- **Fix:** reordered `preclassify` so the only legit hard skips — dense text + maroon/green/orange (positive non-Haver signals) — run FIRST (a finished chart usually also has a blue line; the non-Haver color must win), THEN a **blue-dominant rescue** (`blue_frac ≥ T_BLUE_MIN=0.0015` and `blue_frac ≥ 0.5·colored_frac`) → raw_haver, and only LAST the `colored_frac` "no series" floor. So a sparse blue chart is admitted while a colorless text/table and a stray sub-threshold blue speck still skip. Regression `run_g2_demo` holds **25/26** (the one miss is the pre-existing safe over-process caught by vision). Tests: workflow **99/99** (+4: sub-floor blue rescue, colorless skip, tiny-speck no-false-rescue, orange-wins-over-blue).

### Neon cold-start statement-timeout aborted the resolve (2026-07-20)
- **Symptom:** after the classifier fix seeded the PCE Haver chart, `run_daily` crashed in `_read_resolve` → `resolve_slot` → `_do_search` → `haver_search.search` → `db.run_query` with `psycopg.errors.QueryCanceled: canceling statement due to statement timeout`. (Unrelated to deleting the `inbox_bloomberg/07202026` test folder — that just yields 0 bloomberg docs.)
- **Root cause:** the read-only **Neon** mirror scales compute to zero when idle; the FIRST search of a run pays a cold compute + cold buffer cache and exceeds the MCP server's 15s `statement_timeout` (`2026_haver_mcp/server/db.py`). Warm searches measure ~2.8–5.2s live; the cold first call blew the budget. `_do_search` only caught `TypeError` (kwarg-compat), so the psycopg error propagated and killed the whole run.
- **Fix (`haver_search.py`):** added `_run_query` — a cold-start-resilient wrapper that retries transient DB errors (`QueryCanceled` statement-timeout, `OperationalError` connect/compute-waking blip) with exponential backoff (`_DB_RETRIES=4`, cap 8s), then raises LOUD if still failing (real outage). Non-transient errors are never retried. Wired into both `search()` and `get_meta()`. Left the 15s server timeout as-is (retry beats a blanket bump: the fast path stays fast, the cold path warms and recovers). Tests: workflow **101/101** (+2: transient timeout retried-then-succeeded; non-transient raised immediately).

### Right-edge pad had a CEILING but no FLOOR — invisible on every long chart (2026-08-11)
- **Symptom:** Aman: "all charts do not have ample right-side breathing space." Measured across the six 2026-08-11 charts the pad was **0.16%–1.27% of chart width** — single-digit pixels. The worst (`_2`, 37-year span) got 21 days because the WEEKLY series reached furthest right, so the highest-frequency cadence set the pad on the widest chart.
- **Root cause:** the pad was specified as "3 observations of the series' native period", which is scale-relative to the DATA's cadence but says nothing about the CHART's width. `_X_PAD_MAX_FRAC=6%` protected SHORT charts from an oversized margin; nothing protected LONG ones from a vanishing one. On a 40-year monthly chart 3 months is 0.6% of the panel.
- **Fix:** clamp the pad at BOTH ends — new `_X_PAD_MIN_FRAC=2%` floor alongside the 6% ceiling. The floor scales with `x_pad_periods` (so `0` still gives a flush edge and `1` still gives less than `3`, rather than snapping to the floor). Net effect: a **visually consistent ~2% gutter on every chart** regardless of span or cadence; the native-cadence rule still governs the middle band and still keeps a bar's first/last column whole.
- **Why the 2026-08-03 work missed it:** the chart validated then (`_2`, lagged) had a quarterly series setting the edge at 274 days ≈ 1.6% of width, which eyeballed as acceptable; the monthly charts that day sat at ~0.6% and were never measured — the check was "did the pad change only on the intended chart", not "is the gutter visible". **The regression tests made it worse**: they asserted the pad in DAYS ("monthly pads 80–95d"), which encoded the RULE rather than the GOAL and so locked the defect in. The new tests assert the pad as a FRACTION OF CHART WIDTH and that the gutter stays consistent across 5/15/27/42-year spans — the property that actually matters.
- Re-rendered all six 2026-08-11 charts (now 1.96%–1.97% each). Tests: workflow **254/254** (+10).

### Haver's aggregation/units line read as a TRANSFORM + daily frequency unsupported (2026-08-11)

**1. "Avg, % p.a." became a % change — silent-wrong, and it survived approval.**
- **Symptom:** `f03ffe76d8_2`'s 2-Year Treasury line looked wrong. The source plots the plain LEVEL (0–10% axis, ~4% in 2025); we plotted `difv%(FCM2,1)` — a 1-period percent change.
- **Root cause:** Haver prints an AGGREGATION + UNITS line under each series name ("Avg, % p.a.", "Sum, Mil.$", "EOP, Index") describing how the series *is*, not anything done to it. The read handed that line over as `applied_transform`, and `_flat_transform` saw the `%` plus the annual stem (`p.a.`) and matched a CHANGE rule. Confirmed against metadata: `FCM2@WEEKLY` has `agg_type: AVG`, `data_type: %` — the phrase is literally those two fields concatenated.
- **Why approval didn't catch it:** the resolution summary showed the transform as "Avg, % p.a.", which reads like a perfectly sensible description of a yield series. The defect was only visible in the plotted line.
- **Fix (`_is_agg_or_units` + `_AGG_UNIT_RE`, checked before any change rule):** a phrase whose every comma-separated part is an aggregation stem (avg/mean/sum/total/eop/bop/max/min/first/last) or a unit stem (`% p.a.`, %, ppt, index, Mil.$, $/unit, SA/NSA…) maps to a LEVEL. Safe because a genuine Haver transform always NAMES the operation ("% Change - Year to Year", "3-month moving average"); a bare aggregation stem never does. The no-over-match half is regression-locked hardest — a units guard that swallowed a real transform would turn a loud failure into a silent level, strictly worse than the bug it fixes.

**2. `_1` snagged on `KeyError: 'D'` — DAILY frequency is unsupported across the stack.**
- **Symptom:** the chart never rendered; the run logged `SNAGGED: KeyError: 'D'` and the row sat at APPROVED (the `_render_error` never reached the saved ledger, so the ledger alone looked clean — worth noting, the terminal log was the only record).
- **Root cause:** slot1 bound `petexa@daily` (Cushing WTI spot, business-daily, 10,551 obs). `FREQ_RANK` lists `'D'` but `FREQ_BASE`, `EDGE_TOL`, `_PANDAS_FREQ_END`, `g4_lib.pull`'s period map and `_PERIOD_DAYS` all stop at weekly. Daily has never run through this pipeline.
- **Resolution (Aman's call):** rebound to `petexa@weekly` — the same EIA series at weekly cadence (2,109 obs, identical descriptor/units) — rather than adding a frequency tier. Rendered clean and matches the source. **Daily support remains an open gap**: any future chart binding a `@daily` series will hit the same `KeyError`. If it recurs, the change is `FREQ_BASE['D']≈261` (business-daily), `EDGE_TOL['D']`, `_PANDAS_FREQ_END['D']='B'`, the `g4_lib.pull` period map, and `_PERIOD_DAYS['D']=1`.
- Tests: workflow **244/244** (+25), transforms 43/43.

### Signed lag/lead tags + HTML-entity decode (2026-08-03, follow-up)

**1. `[+n]` LEAD plotted as a LAG — the bracket SIGN was discarded.**
- **Symptom (as reported):** `636a683648_2` (`zs(fwill@surveys)[-4]` vs payroll y/y) looked like the lag wasn't applied. It *was* — but only because `[-4]`'s magnitude happens to be all the old parser kept.
- **Root cause (`build_chart._lag_int`):** the pattern `\[[-+]?(\d+)\]` matched the sign but captured **only the digits**, so `[+4]` and `[-4]` both returned `4`. A requested **LEAD** therefore shifted the same way as a lag, with no error to notice — the same silent-wrong class as the `-ann` bug. Compounding it, an unparseable-but-present tag returned `0`, i.e. plotted **unlagged** and perfectly plausible.
- **Fix (`_shift_periods`, replacing `_lag_int`):** returns a SIGNED pandas shift and documents the one confusing part — `shift(k>0)` moves values forward, which *is* a lag, so the shift is the NEGATION of the tag's sign (`[-4]` → `shift(+4)`, `[+4]` → `shift(-4)`). An unsigned `[n]` reads as a lag (that's the form Haver prints). A non-empty tag that cannot be parsed now **RAISES** instead of silently plotting unlagged.
- **Order confirmed (already correct, now regression-locked):** `transforms.finish` applies the shift AFTER the formula evaluates and BEFORE the lift to common frequency — so `zs(...)[-4]` z-scores over the series' own history and only then slides. The plotted values are the unshifted z-scores, moved; a lag never renormalizes μ/σ. The shift is in the series' NATIVE period, so `[-4]` on a quarterly series is 4 quarters (not 4 months) even on a monthly-common chart.

**1b. The lag DROPPED the newest n observations and the window clipped the rest (same chart, found on re-inspection).**
- **Symptom:** `_2`'s lagged line stopped at 2026Q3 — a year short. `fwill` runs to 2026Q3, so lagged 4 quarters it should reach **2027Q3**.
- **Root cause A (`transforms.finish`):** `s.shift(n)` shifts VALUES inside a FIXED index, so the last n observations fall off the end. On a lagged leading indicator those are the newest readings — precisely what the chart exists to show. 2025Q4–2026Q3 were silently discarded.
- **Root cause B (`build_chart._window_end`):** the window end was derived from raw operand end dates with no knowledge of the shift, so even once the values survived, `s.loc[:window[1]]` clipped the extension straight back off.
- **Fix:** `finish` now shifts the INDEX (`shift(1, freq=shift_offset(f, n))`), preserving every observation and carrying the series to where the lag puts it; `_window_end` adds each slot's shift to its end, counted in the slot's own native period via the new `_slot_freq` (highest operand frequency, matching how a BinOp lifts). A LEAD shortens the end by the same arithmetic. New `transforms.shift_offset` uses ANCHORED offsets (`QuarterEnd`/`MonthEnd`/`YearEnd`, exact 7-day multiples for weekly) rather than `DateOffset(months=…)`, which walks the day-of-month and drifts off the period end — Sep-30 + 3 months is Dec-**30**, and Feb-28 + 1 month is Mar-**28**, either of which would put a stamp one day shy of the grid anchor.

**1c. The right-edge pad silently collapsed on any MIXED-frequency chart.**
- **Symptom:** with the lag fixed, `_2`'s pad came out **93 days** where 3 quarters (~274) was intended.
- **Root cause (`_x_end_pad`):** the pad inferred cadence from the PLOTTED index spacing, but a quarterly series lifted onto a monthly common grid has ~30-day spacing. The docstring promises "the cadence of whichever series reaches furthest right"; once a lift happened it delivered the common grid's cadence instead. Latent on every mixed-frequency chart — invisible until a low-frequency series set the right edge.
- **Fix:** `PlotSeries` carries an optional NATIVE `freq`, stamped in `render_row`; `_x_end_pad` prefers it (`_PERIOD_DAYS`, 365.25/npy) and falls back to plotted spacing when absent. Swept both re-rendered dates: the only pad that moved is `_2`'s (93 → 274d) — every same-frequency chart is byte-identical, so the fix is confined to the case it was meant for.
- **Also fixed:** `_legend_label` hard-coded `[-{n}]`, so a lead would have been captioned `[-4]`; and it appended the tag unconditionally, giving `…(%. lagged by 4qtrs) [-4]` on this very chart. It now emits the tag in the tag's own notation and suppresses it when the ratified label already conveys the shift (`_states_lag`). A label SILENT on the shift still gets tagged — a lagged line must never plot without saying so.

**2. `&` rendered as `&amp;` — HTML entities leaked from Teams into chart text.**
- **Symptom:** `636a683648_1`'s title rendered *"Demand for C&amp;I credit keeps firming"*. The legend on the same chart was fine (Opus-proposed, never through the chat path), which localized it to human-supplied text.
- **Root cause (two layers):** Graph returns chat bodies as HTML, so a typed `C&I` arrives as `C&amp;I`. `_strip_html` removed tags and special-cased only `&nbsp;`, leaving every other entity literal — and it was applied by `GraphTransport.replies()` **only when** Graph reported `contentType == "html"`. A body typed `"text"` bypassed it entirely and the entity rode into `chosen_title` and onto the canvas.
- **Fix:** `_strip_html` now `html.unescape`s AFTER stripping tags (so real markup goes first and text the human escaped on purpose survives), and — the load-bearing half — `_untag` does the normalization, which is the choke point **all six** `parse_*` functions already funnel through. Decoding there is transport-independent, so no reply path (Graph html, Graph text, Stub) can smuggle an entity into chart content. Caught by the regression test, not by inspection: the transport-only fix passed every direct `_strip_html` assertion and still failed the end-to-end title round-trip.
- Swept all ledgers for entity leaks — one hit (this title), repaired. Re-rendered both charts. Tests: workflow **219/219** (+45), transforms **43/43** (+6).

### Right-edge pad — the newest observation was flush against the frame (2026-08-03)
- **Symptom:** every series ends at its latest print, so the last datapoint sat ON the axis — the exact value the reader came for, squeezed into the frame. On bar charts the final bar was half-clipped by it.
- **Fix (`build_chart._x_end_pad`):** the DISPLAY right edge is extended ~3 observations past the last plotted point. The pad is measured in the OWN period of the series that SETS the edge, so it scales with the cadence visible there (3 weeks weekly / 3 months monthly, and on a MIXED-frequency chart the cadence of whichever series reaches furthest right). Capped at `_X_PAD_MAX_FRAC=6%` of the visible span, because 3 whole quarters on a short 14-bar quarterly chart is ~20% of the panel — that trades a clipped point for a conspicuous empty margin. The cap still clears a bar's half-width, which is all the goal needs. Per-chart override `chart_spec.x_pad_periods` (`0` restores a flush edge). **Display-only** — the DATA window is untouched, so nothing is extrapolated and no phantom point is drawn (regression-locked: the series must be unmutated after padding).
- **Also fixed (same defect class, left edge):** a BAR is centered on its period stamp, so half the FIRST bar lies left of that stamp and the edge sliced it in two. `_x_start` now backs off a half-period when the opening series is drawn as bars; lines need no pad (a line starts AT its first point).
- Re-rendered 2026-08-03 (4 charts) and 2026-07-30 (5 charts). Tests: workflow **174/174** (+14).

### Four defects from the 2026-07-30 run (one SILENT-WRONG) (2026-07-30)

**1. `-ann` abbreviation dropped the ANNUALIZATION — silent-wrong math.**
- **Symptom:** `_3` (Compensation vs GDP, "2-qtr %Change-ann") and `01866d86e2_0` ("3-month %Change-ann") plotted plausible-looking but WRONG levels. Haver's own on-chart data labels read **6.81 / 3.55**; we rendered **3.35 / 1.76** — roughly half.
- **Root cause (`build_chart._flat_transform`):** the `annual` test matched only SPELLED-OUT keywords ("annual rate", "annualized", "saar"…). DLX prints the TRUNCATED stem — `2-qtr %Change-ann` — and `"annual" in t` is False for `"ann"`. The phrase therefore fell through to the generic `is_change` branch and produced `diff%(x,n)` (a plain n-period change) instead of `difa%(x,n)`. Worse than a miss: the fail-loud floor never fired because a *different valid rule* matched, so it rendered silently.
- **Fix:** match the abbreviated stem on a word boundary — `(?:^|[\s\-])ann(?:\.|ual(?:ized|ised)?)?(?:[\s\-]|$)` — alongside the existing keywords. Verified no bleed into the period-to-period / year-to-year / moving-average families.
- **Math confirmed** (`_dif`, `k = npy/n`): `difa%` = `((x_t / x_{t-n})**(npy/n) - 1) * 100`. Quarterly n=2 → exponent 4/2 = **2**; monthly n=3 → exponent 12/3 = **4** — exactly the formulas Aman specified. Re-rendered values now reproduce Haver's printed labels to the cent (6.81, 3.55, 3.17).

**2. Legend store key was TRANSFORM-BLIND → one ticker, two transforms, one label.**
- **Symptom:** `_2` and `01866d86e2_0` each render ONE ticker under TWO transforms (`fsdh@usecon` y/y + 1-qtr saar; `jcsxehm@usecon` y/y + 3m saar) and BOTH lines showed the SAME legend. The resolution ask had shown them CORRECTLY — the corruption happened after approval.
- **Root cause (`resolve.legend_key` → `code_key`):** the stored label TEXT carries a transform qualifier ("… (y/y %chg)" vs "… (1-qtr %chg saar)") but the KEY was the bare `CODE@DB`. So the store had finer-grained VALUES than KEYS: `_persist_legends` wrote slot0's label to `FSDH@USECON`, then slot1 OVERWROTE it (last write wins), and at render `_legend_label` looked both slots up on that one key. `proposed_legend` (correct, per-slot) was never consulted.
- **Fix — three layers:** (a) `legend_key` is now qualified by the transform, canonicalized on the derived G3 FORMULA (`FSDH@USECON|YRYR%(#)`) so two spellings of the same math share a label but different math cannot collide; a LEVEL slot keeps the bare legacy key so every warmed entry still hits. (b) `_legend_label` prefers the slot's OWN ratified `proposed_legend` — the most specific artifact, immune to any cross-chart store aliasing. (c) NEW FLOOR `_assert_distinct_legends`: two identical legends on one chart RAISE, because an unreadable chart must never ship. `lookup_legend` keeps a legacy blind-key fallback for back-compat but REFUSES it when that base key repeats on the chart (exactly the ambiguous case).

**3. Bar / stacked-bar was an UNREPRESENTED dimension (not a mis-detection).**
- **Symptom:** `_1` (a 4-component stacked contribution) and `_2` (bars + a line) were both redrawn as plain lines.
- **Root cause:** the pipeline had NO way to express it — the vision prompt never asked, `ChartSpec`/`SeriesSpec` had no field, and `render()` hard-coded `ax.plot`. Every chart was a line chart by construction, so there was nothing to "detect wrong".
- **Fix:** `plot_kind` ∈ {line, bar, stacked_bar} added to the read prompt + `SeriesSpec` (tolerant normalizer, defaults to line — the shape is cosmetic, so it never raises); `PlotSeries.kind` + bar/stacked drawing in `render.py`, reusing the econ-templates MIXED-SIGN rule (`_stacked_bottoms`: positives stack up off the running positive total, negatives hang down off the running negative total — naive single-`bottom` stacking puts a positive contribution in negative territory). Bar width derives from the series' own period spacing; stacks draw from `PALETTE_EXTENDED` (the 3-line palette would recycle maroon onto a 4th component); a zero rule is drawn whenever bars are present; the y-fit uses the stack ENVELOPE, not each component's own range.

**4. `ptfneh` ≠ "Other Equipment" — the stack DOUBLE-COUNTED (found while verifying `_1`).**
- **Symptom:** the rebuilt stack peaked at **4.29** where the source bar reads **2.42**.
- **Root cause:** slot0's read descriptor arrived TRUNCATED ("…Other Equipment: Contrib to Real GDP %Chg(SAAR,%P**...**") and resolved to `ptfneh@usecon` = "Pvt Nonres Fixed Investment: **Equipment**" — the TOTAL. So the chart stacked total equipment on top of three of its own sub-components. The correct series is `ptfneoh@usecon` ("**Other** Equipment"), and it was poisoning `learned_descriptors.json`.
- **Fix:** rebound slot0 → `ptfneoh@usecon`, repaired the learned-store entry, added the trusted mnemonic. **Decomposition identity now verified:** Other + Transportation + Info Processing + Industrial = **2.42** = total Equipment (`ptfneh`) — matching the source bar exactly. A contribution stack has a checkable identity; worth asserting whenever a "total" sibling exists.

**5. Display x-start opened on DEAD SPACE.** `_0`'s read said 1948 but `zs(yryr(YPSVR))` begins 1960Q1 — a blank decade. `_x_start` now moves the xlim in to the first period that actually plots (MIN across series so the longest line still shows fully) while the DATA window keeps the read start, since a z-score/YoY needs its run-up. Mirrors the `_end_anchor`/`_window_end` discipline already on the right edge.

**6. Per-chart x-axis tick interval + label format (2026-07-30, follow-up).** The automatic axis is a house default, not a law: `_0` (66-year span) wanted 5-year ticks rather than the AutoDateLocator's decades, and `_2` (quarterly, 3-year span) wanted quarter labels rather than month names. Added `chart_spec.x_tick_years` (force `YearLocator(N)`) and `chart_spec.x_label_fmt` (`auto`|`year`|`month`|`quarter`); an unknown format RAISES rather than silently defaulting. **Alignment trap caught in review:** the first `quarter` implementation ticked quarter STARTS (`MonthLocator(bymonth=1,4,7,10)`), which puts the Apr-1 gridline beside the Mar-31 bar and labels that bar "Q2-23" when it is **Q1-23** — a plausible-looking off-by-one-quarter mislabel. Fixed by pinning ticks to quarter ENDS via a `FixedLocator` over `date_range(freq="QE")`, matching the house QUARTER_ANCHOR obs stamp, thinned to ≤16 labels.

Tests: workflow **160/160** (+59: the `-ann` vocabulary incl. no-over-match, both saar formulas proved against the closed form, transform-aware keying + legacy back-compat + the duplicate floor, plot-kind normalization + mixed-sign stack identity, x-start clamp, axis tick/format overrides incl. the quarter-end alignment assertion). Transforms **37/37**.

### Month-level `sample_start` + per-series display-END anchor (2026-07-21)
- **Symptoms on the 2026-07-21 run:** `89b1a31f51_0/_1` rendered a decades-long sample (the vision read garbled the start to a bare month like `'AUG'` / `'JUL 25'`, which the old year-only parser floored to 2000-01-31); `89b1a31f51_2` (ADP monthly `LAXEPA` m/m chg + weekly `4*(LAXEPAW/1000)`) had the maroon **monthly** line overhang the gray **weekly** line at the right edge.
- **Fix 1 — month-level starts (`build_chart._start_ts`):** now parses `YYYY-MM` (`2025-07`), `Mon YYYY` (`Jul 2025`) and `Mon-YY` (`Jul-25` / `JUL 25`) anchored to the 1st of the month, before the year-only fallback. A bare month with **no** year (a stray axis-label misread like `'AUG'`) still falls through to the floor rather than guessing a year. `_0` set to `2025-07`, `_1` to `2025-08`; `_2`'s `'JUL 25'` read now resolves to Jul-2025 on its own (matches the source left edge).
- **Fix 2 — display-END anchor (`build_chart._end_anchor` + new `chart_spec.end_series`):** the DATA slice still keeps every series' natural last point (so no operand's final value is dropped), but the **xlim** right edge can be pinned to a named series' last observation. `LAXEPAW` (weekly) genuinely ends 2026-06-28 while `LAXEPA` (monthly) anchors its June point to the 06-30 month-end, so the plain window end (max across slots) let the monthly line stick ~2 days past the weekly line. Setting `_2`'s `end_series='laxepaw@weekly'` clamps the axis to the weekly end so both lines terminate together at the right, matching the source; `render` only sets xlim (it doesn't re-slice), so the monthly June point is retained and simply clipped at the boundary.

| ~~G7 (orig)~~ | **Live shakedown — designed to span TWO invocations** (the real test is the *second day*, where cross-run state bugs live; a one-shot "did it post" hides them). **Run-1:** ingest a low-stakes real email; `sample1_chart3` PPI is the genuine unresolved case (screenshot "PPI: Manufacturing Industries" → 4 metadata-identical `sp@PPI` candidates → no disambiguation → parks `awaiting_ticker` + posts to the group chat); a title round-trip posts the `_var1`/`_var2` pairs (proves the free-text + numbered duality live). Aman replies `pa413121@usecon` to one and leaves the other parked (the **half-answered day**). **Run-2:** must (a) **idempotency** — skip run-1's approved/rendered charts via the ledger, post nothing for them; (b) **clean resume** — pick up ONLY the still-parked ticker, harvest the reply, resolve, render; (c) **no duplicate posts** — a chart already `awaiting_*` with `ask_cursor` set is never re-posted. Asserts: ledger end-state correct across both runs; exactly one Teams post per question; no double-render. Gated on live consent done + `AS_TEAMS_CHAT_ID` set. No production run until this passes. | |

---

## §13. Chat lane — Claude Desktop as the operator surface (BUILT)

> **Status: G9a signed 2026-08-20; G9b built and proven the same day.** The design
> below is as approved — it is described in the present tense because it now exists.
> Nothing in this section changes the `run_daily` lane. §13.13 records what was built,
> the one deviation, and the evidence.

### 13.1 What this is, and the one thing it buys

Same intelligence, a different operator surface. Instead of Neil's email arriving and
the pipeline posting asks into Teams, **Aman pastes a Haver screenshot plus the
commentary into Claude Desktop** and gets a RenMac render back, iterating in the chat
until it is right. The first target is the 2026-08-20 Philly Fed chart: `zs(yryr%(IP))`
against the Philly Fed Mfg Business Outlook current-activity diffusion index, shared
scalar axis, recession bands, sources FRB + FRBPHI.

The reason this is worth building is not convenience. **It closes the render-preview
gate** — the one remaining blocker to headless in §12. `confirm_all` today ratifies the
*resolution* (ticker/transform/axis), not the *pixels*, and that gap is exactly what let
both silent-wrong bugs through (the transform-drop and the store-poisoning
double-transform). In a chat, the operator sees the rendered chart before it is used,
every single time. The gate we have been trying to engineer is a property of the surface.

### 13.2 What is reused UNCHANGED (the whole point)

No chart logic is reimplemented. Every accumulated correctness rule comes along:

| Module | What it gives the chat lane |
|---|---|
| `build_chart.render_row` | **The seam.** Pull → G3 evaluate → freq lift → window edges → styled render, in one call |
| `build_chart.phrase_to_haver` / `_flat_transform` | The finite Haver transform vocabulary incl. the abbreviated `-ann` stem, and the **fail-loud floor** on an unmapped phrase |
| `transforms.py` | The signed-off G3 parser/evaluator (transform-then-interpolate, quarter-anchor, window-ZS) |
| `render.py` | RenMac style, dual-axis LHS/RHS tagging, legend wrap, recession shading, x-pad floor/ceiling, bar + mixed-sign stacked contribution |
| `resolve.py` | Exact-token-set relevance gate, SA + aggregation cross-checks, `double_transform_reason`, multi-variant search recall |
| `haver_search.py` | The in-process catalog path (`queries.build_search_query` + `db.run_query`) the daily lane already uses |
| `validate.check_last_value` | The independent check against the value Haver prints on the source chart |

### 13.3 What Claude Desktop REPLACES (roughly half the pipeline)

| Replaced | By |
|---|---|
| `ingest.py` (Graph Mail.Read, `.docx` folders) | The operator pastes the image |
| `classify.py` (raw-Haver vs finished, palette pre-filter, vision tie-breaker) | The operator only pastes charts they want rebuilt |
| `chartspec.read_chart_spec` (Opus vision read) | Claude reads the screenshot natively in the conversation |
| `propose.py` (Opus title/legend drafting) | Claude drafts from the pasted commentary, in context |
| `ledger.py`, `teams.py`, `approval.py`, `--retitle`/`--relegend` | The conversation itself; "change the title" re-renders |

`ingest`, `classify`, `ledger`, `teams`, `approval` and `propose` are **not imported** by
the chat lane. They stay exactly as they are for the daily run.

### 13.4 Tool contracts — a new stdio MCP, `haver-chart`

Two tools, both read-only with respect to the stores (§13.6). Inputs are LLM-supplied
and therefore untrusted; every guard stays server-side.

**`resolve_series`** — wraps `resolve.slot_from_series` + `resolve.resolve_slot`, driven
by `haver_search` so the chat lane resolves **identically to the daily lane**. Claude
must not pass its own candidate list; it passes what it read off the chart:

```python
resolve_series(
    base_descriptor: str,          # the series name as printed on the chart
    applied_transform: str = "",   # the worded transform, if words-only
    formula: str = "",             # the raw Haver formula if the chart shows one
    sa_hint: str = "", freq_hint: str = "",
    axis: str = "shared", lag: str = "", plot_kind: str = "line",
) -> dict                          # {status: resolved|parked, resolved: 'code@db',
                                   #  via, similarity, candidates: top-3 with scores, reason}
```

A park is a **normal outcome**, not an error — it returns the top-3 candidates with
similarity so the operator can pick, exactly as the Teams ask does. Binding still
requires the exact normalized token-set match; the 2026-06-30 `DFBACTS` vs `DFBACTDS`
proof (Jaccard 0.909 on the WRONG directional sibling) is why similarity alone is never
sufficient.

**`render_chart`** — builds the `row` dict and calls `build_chart.render_row`, returning
the PNG as MCP image content (so it appears inline) plus the saved path. The row schema
is already fixed by `render_row`; the tool is a translation layer, nothing more:

| Level | Keys (verified against `render_row`) |
|---|---|
| row | `chart_spec`, `series`, `subtitle`, `st_force`, `no_title`, `chosen_title` |
| `chart_spec` | `sample_start`, `end_series`, `x_pad_periods`, `recession_shading`, `axis_mode` (`shared`\|`dual`), `left_axis{min,max}`, `right_axis{min,max}`, `x_tick_years`, `x_label_fmt` |
| slot | `status='resolved'`, then `formula`+`codes` **or** `resolved`; plus `applied_transform`, `axis`, `lag`, `plot_kind`, `proposed_legend` |

Renders land in a chat-lane directory (`outputs/chat/<date>/`), never in
`MMDDYYYY/<release_slug>/` or a backfill render dir — the two lanes must not share
output namespaces.

### 13.5 Boundary — where it lives, and what it must not touch

The MCP lives **in this repo**, beside the code it wraps. It is NOT added to
`2026_haver_mcp`: that repo's metadata server is read-only and vendor-free by invariant,
and its `haver_data` MCP is a thin observation wrapper. Copying chart logic there would
create a second source of chart truth that rots. In Claude Desktop the operator ends up
with three entries: `haver-metadata` (hosted connector), `haver-data` (observations),
`haver-chart` (this).

Hard boundary: the chat lane imports `build_chart`, `render`, `transforms`, `resolve`,
`haver_search`, `validate` — and **nothing else**. Any change needed in those modules to
support the chat lane must keep the daily lane green (`selftest_workflow`,
`selftest_transforms`) in the same commit.

### 13.6 Learning stores — READ-ONLY from the chat lane in v1

The chat lane calls `load_legend` / `load_learned` / `load_trusted` / `load_clarified`,
so it inherits every ratified label and bind and stays quiet on recurring series. It
**must not call any `save_*`**.

Rationale: a chat binding is ratified by a human looking at a chart, but it is not
ratified through `confirm_all`, and the daily run *depends* on those stores. An
unratified chat bind writing `learned_descriptors.json` is the May-12 `mpcuhsro`
poisoning class with a new entry point — and the 2026-07-30 `ptfneh` incident shows a
wrong bind can look entirely plausible on screen. One-way inheritance now; revisit at
G9d once the lane has a track record.

**G9d resolved 2026-08-20 — the seal is PERMANENT, not v1** (§13.14). Distribution made
the case stronger rather than weaker: a store several teammates' chat sessions can write
is this same poisoning class with N entry points. Read-only is also what makes teammate
distribution sound at all — a copy that cannot be written can only go stale, never
diverge.

### 13.7 Safety properties — what holds, what changes

Holds, unchanged, because it lives server-side: the unmapped-transform raise; the
raw-mnemonic legend raise; `_assert_distinct_legends`; the non-suppressible transform
label; the `double_transform_reason` park; the exact-token-set bind precondition; the
composite-aware window end and the vintage-skew warning; the daily cache-freshness
re-pull.

Changes: `confirm_all`'s "nothing renders until the resolved set is ratified" becomes
"nothing is *used* until the operator sees the render." That is stronger for render
correctness (§13.1) and weaker for store hygiene, which is precisely why §13.6 is
read-only. The `Defect-2` property (a partially-resolved chart renders nothing) still
holds by construction: `render_row` raises unless every slot is `resolved`.

### 13.8 The skill + system prompt

Mirror the pattern proven in `2026_haver_mcp/haver_data/`: a `SKILL.md` and a pasteable
system-prompt snippet, with the load-bearing rules ALSO in the tool docstrings, since a
tool description is the only guidance an MCP client loads automatically — a skill does
nothing until it is installed, and a system prompt nothing until it is pasted. Rules to
encode: never guess a ticker, always go through `resolve_series`; pass the **formula**
when the chart prints one (`zs(yryr%(IP))`) rather than paraphrasing it; a park means ask
the operator, never bind the top hit; state the transform in the subtitle or accept the
auto label; reproduce the source chart's printed last value before calling it done.

### 13.9 Known limits carried in

- **Daily frequency raises `KeyError: 'D'`** across the stack (five places, per the
  2026-08-11 entry). A chart with a daily line needs the weekly twin, or that fix first.
- **Applied-INDEX / rebase** still raises `NeedPin` — unchanged, and correct.
- **Packaging.** `haver_data` needed only `haver` + `fastmcp`; this needs matplotlib,
  pandas, the RenMac style module and this repo's `src/`. v1 was **single-machine
  (Aman's)**, pointing at the repo checkout. **Lifted by G9e** — see §13.14; the lane is
  now relocatable and ships as a zip.

### 13.10 Validation plan

1. **Lane equivalence.** Take a finished daily-lane row (Texas, or one of the 2026-08-11
   set), drive `render_chart` with the same spec, and diff the PNG against the daily
   render. Any visual difference is a wrapper bug, not a style choice.
2. **The Philly Fed chart** (§13.1) end to end in the chat: read → `resolve_series` on
   both series → `render_chart` → title from the commentary → correct it → re-render.
3. **Last-value check** on that render, per `validate.check_last_value`.
4. **Daily lane regression** — `selftest_workflow` + `selftest_transforms` green.

### 13.11 Gates

| Gate | What | Status |
|---|---|---|
| G9a | This section signed off | **SIGNED 2026-08-20** |
| G9b | `render_chart` + `resolve_series` built; lane-equivalence diff (13.10.1) clean | **PASSED 2026-08-20 — 6/6 equivalent, daily lane 254/254 + 43/43** |
| G9c | Live chat run on the Philly Fed chart; last-value check passed | **open — Aman's to run in Claude Desktop** |
| G9d | **STOP** — decide whether the chat lane may ever WRITE the learning stores | **DECIDED 2026-08-20 — NO. Read-only stands; §13.6 is permanent, not v1** |
| G9e | **STOP** — decide teammate distribution (packaging, per §13.9) | **DONE 2026-08-20 — all 5 steps; `dist/haver-chart-<sha>-<date>.zip` builds and was proven from a clean unzip** |

Hard stops: no `save_*` to the knowledge stores; no changes to the `run_daily` chain; no
new transform vocabulary except through `_flat_transform`'s fail-loud path.

### 13.12 Open questions — ANSWERED (Aman, 2026-08-20)

1. **Daily-lane state: NO — the chat lane stays stateless.** It does not read the daily
   renders or ledger, so "redo yesterday's chart 3 with a different title" is not
   available; paste the chart again. A lane that cannot reach the ledger cannot corrupt
   it, and that is worth more than the shortcut.
2. **Transform errors: carry the recognized wordings.** BUILT — `lane.TRANSFORM_PHRASES`
   + `lane.explain`, appended to the raise. The vocabulary floor is unchanged; what is
   added is the wording the operator should use instead, which the shared message cannot
   know. The shared raise ends in developer instructions ("extend
   `build_chart._flat_transform`") that mean nothing in a chat.
3. **`plot_kind`: the read reports it, and a bad value now raises.** BUILT —
   `lane._plot_kind`. It calls `build_chart._plot_kind_of` rather than re-implementing
   it, so "columns"/"stacked bars" normalize exactly as they do in the daily lane, but
   an unrecognized value ("area", "histogram") raises here instead of silently drawing a
   line. The daily lane keeps its lenient fallback, which is correct there: the shape is
   cosmetic and `--replot` fixes it. In the chat the render IS the answer, so a dropped
   bar chart must not pass unnoticed. Both tool docstrings, `SKILL.md` and
   `SYSTEM_PROMPT.md` now instruct the read to report the shape.

**Source line: DECIDED — keep the RenMac house line, no per-chart knob.** RenMac
publishes the reconstruction, so `Source: Renaissance Macro Research, Haver Analytics`
is the correct line; the Haver original's "Sources: FRB, FRBPHI/Haver" describes the
source chart. `RenderSpec.source` already exists with that default (`render.py:271`) and
`render_row` simply never passes one, so this stays a one-line change if a client ever
asks for the primary agency. The §13.4 row schema is unchanged.

### 13.13 What was built (G9b, 2026-08-20)

**Files.** `haver_chart/server.py` (the FastMCP stdio surface — two tools, the
load-bearing rules in their docstrings), `lane.py` (the translation layer; no chart
maths), `bootstrap.py` (sys.path, stdout discipline, store seal), `selftest.py` (31
pre-flight checks), `SETUP.md`, `SKILL.md`, `SYSTEM_PROMPT.md`,
`claude_desktop_config.example.json`. Harnesses: `scripts/g9b_lane_equivalence.py`,
`scripts/g9b_philly.py`.

**Post-signoff additions (§13.12 answers 2 and 3, both wrapper-local, no shared module
touched).** `lane.TRANSFORM_PHRASES` + `lane.explain` turn the vocabulary rejection into
an actionable one: the accepted wordings are appended and the shared message's developer
instruction is stripped, since neither the phrase list nor the audience is something the
shared raise can know. The list is hand-kept because the mapper is branch logic over
keyword stems rather than a table, so `selftest.py` asserts every advertised wording
still maps — that assertion, not discipline, is what stops the two drifting apart.
`lane._plot_kind` calls `build_chart._plot_kind_of` for normalization (so "columns" and
"stacked bars" resolve exactly as in the daily lane) and raises on a value that
normalizer would discard. The daily lane keeps its lenient line fallback, which is right
there — the shape is cosmetic and `--replot` corrects it — but in the chat the render IS
the answer, so a silently dropped bar chart has nothing to catch it. Verified live:
`plot_kind="columns"` reaches the renderer as a drawn `bar`. Selftest 19/19 → **28/28**
(and → **31/31** with the three path checks G9e step 1 added).

**Two things the design did not anticipate, both wrapper-local:**

1. **stdout is the JSON-RPC channel.** `g4_lib.pull`, `haver_search` and the Haver
   package print progress to STDOUT. On a stdio MCP that is protocol corruption, not
   noise — a stray `  pulled …` lands mid-frame and the client drops the connection
   with no useful error. `bootstrap.quiet_stdout` redirects it to stderr, where Claude
   Desktop captures it into `mcp-server-haver-chart.log`. No shared module changed.
2. **§13.6 is enforced, not promised.** `bootstrap.seal_stores` replaces every
   `resolve.save_*` with a raising tripwire in this process. Process-local, so the
   daily lane is untouched; `selftest.py` asserts both that the savers raise and that
   the four `load_*` readers still work.

**The one deviation from §13.4 — an additive change to a shared module (allowed by
§13.5).** `render_row` now also returns `drawn` (the `PlotSeries` actually rendered),
`window` and `common_freq`. Without it, `check_last_value` has to re-derive the series
by rebuilding the window, the common frequency and the applied formula by hand — and a
window z-score evaluated over a different window is a different number, so the check
would be validating a reconstruction of a reconstruction. Every existing caller
(`run_daily`, `render_check`, `validate_0528`) reads named keys and is unaffected; both
selftests are green in the same commit.

**Not built, deliberately.** `render_row` takes no `source` argument, so the chat lane
uses the RenMac house source line (`Source: Renaissance Macro Research, Haver
Analytics`) like every daily-lane chart. The Haver original's "Sources: FRB,
FRBPHI/Haver" is the source chart's own line, not the replication's. Adding a
per-chart source knob would extend the approved §13.4 row schema, so it was left as an
open question rather than taken unilaterally — **Aman decided 2026-08-20 to keep the
house line** (§13.12), so the schema stands as approved.

**Evidence.** Lane equivalence 6/6 over `ledger_backfill_2026-08-11` — 4 pixel-identical
to the archived PNGs; `_1`/`_2` differed only by data vintage (WTI and the 2Y yield have
moved since 2026-08-11) and are pixel-identical against a same-vintage daily re-render,
which the harness now produces automatically so a vintage difference can never be
mistaken for a wrapper bug. Philly Fed: `zs(yryr%(IP))`→`ip@ip` (formula path; the
higher-precision IP-database twin of `IP@USECON`, max abs difference 0.05 across 811
months), Philly Fed diffusion index→`bocgx@surveys` on an exact token-set match at sim
1.0 over two 0.8 siblings; `check_last_value` PASS on both lines. Daily lane
254/254 + 43/43. A real stdio handshake was verified end to end: `initialize`,
`tools/list`, a live `resolve_series`, a `render_chart` returning 198 KB of inline PNG
plus structured content, and an unmapped transform surfacing to the operator verbatim
as `ValueError: unmappable applied_transform 'flurgle'`.

### 13.14 Teammate distribution — G9d + G9e (2026-08-20)

**G9d — DECIDED: the chat lane never writes the stores.** §13.6 called read-only a "v1"
stance to revisit once the lane had a track record. Aman closed it the other way: the
seal is permanent. Ratification is what makes a bind trustworthy, and ratification lives
in the daily lane's approval round. Once the lane is on several machines the argument
gets stronger, not weaker — a shared store that any teammate's chat experiment can write
is precisely the `mpcuhsro` poisoning class with N entry points instead of one. The
`bootstrap.seal_stores` tripwire therefore stays, and the distribution design below is
built on top of that guarantee rather than working around it.

That guarantee is what makes the whole thing cheap. Because no consumer can write, a
teammate's copy can only ever be **stale**, never **wrong** — two copies can never
disagree about the same descriptor. A plain file copy is then a sound distribution
mechanism, and the only cost of staleness is a park where a bind was possible, which the
operator resolves by picking a candidate. Nothing here needs locking, merging or a
sync protocol.

**Step 1 — relocatable paths.** `src/render.py` reads `ECON_TEMPLATES_CHARTS` and
`src/haver_search.py` reads `HAVER_MCP_SERVER`, each defaulting to this machine's path,
exactly as `src/resolve.py` already did with `CLARIFIED_KNOWLEDGE_DIR`. Additive and
default-preserving, so the daily lane is untouched. `selftest.py` gained three checks
(28 → 31) that resolve all three before any live call and report each one's real
consequence — only the style module fails hard; the other two degrade — so a
misconfigured machine fails at pre-flight naming the variable to set, not at the first
render.

**Step 2 — credentials.** One shared read-only Neon role for all teammates: the blast
radius of a leak is a catalog metadata read, and one secret is one thing to rotate. DLX
is per-user licensing and cannot be distributed. `ANTHROPIC_API_KEY` is not needed at
all, since the chat lane replaced `propose.py` and there is no server-side Opus call.

**Step 3 — the stores travel by publish, not by pointer.** `scripts/publish_knowledge.py`
copies the four ratified stores to `P:\Public\RenMac_Chart_Knowledge`; teammates set
`CLARIFIED_KNOWLEDGE_DIR` to it. `P:\Public` is already reachable by everyone, so a git
remote would add a clone-and-pull step that buys nothing. The folder was **audited, not
assumed**, before publishing — `knowledge_repo` also holds personal post-ship commentary
notes. Every entry validates against the chart schema, timestamps span the replicator's
own lifetime (2026-06-30 to 2026-08-11), all databases referenced are Haver, and no
project outside this one references `CLARIFIED_KNOWLEDGE_DIR` anywhere in `new_work`.

Three design points, each guarding a specific failure:

1. **Why publish rather than point the daily lane straight at `P:`.**
   `resolve.save_legend` does an unguarded read-modify-write with no atomic rename, so a
   reader on the share can catch a truncated file mid-write — and `_read_json` swallows
   that as `{}`, silently costing every legend label for that call. Separately, a write
   failure inside `save_*` would raise in the middle of a Teams approval. The daily lane
   keeps writing locally; publishing copies finished files with temp-then-replace.
2. **Why an allowlist rather than a folder copy.** The script names the four files, so a
   new file appearing beside them — a personal note, a scratch dump — can never be
   published by accident. Every store is validated before the destination is touched, so
   a corrupt file aborts the whole publish instead of half-updating the share.
3. **Why `run_daily` publishes automatically, last, and fail-soft.** Every path that can
   write a store (`--approve`, `--retitle`, `--relegend`) calls `_publish_stores()` on
   completion, so a teammate is never more than one approval round behind — a manual step
   here would be forgotten and the staleness would be invisible. It runs **last** because
   the day's ingest, resolves, Teams round-trips and renders are already saved by then,
   and it is **fail-soft** because an unmapped drive on a laptop must not fail a
   successful day: it warns, names the manual re-run, and returns. `G7_NO_PUBLISH=1`
   skips it.

**Step 4 — the package.** `scripts/build_teammate_package.py` builds
`dist/haver-chart-<sha>-<date>.zip` (~284 KB): `README_FIRST.md`, `configure.py`, `repo/`,
and `vendor/`. Two choices worth recording. The file list comes from `git ls-tree HEAD`
rather than a folder walk, because the working tree holds `notes/` (client email
content), `data/` and `outputs/` — shipping only committed tracked files means nothing
untracked can ride along, and the exclude list then drops `plan.md`/`prompt.md`/`.cursor/`
on top of that. And the two external dependencies are **vendored** (three small
pure-python files, imported by path) rather than cloned, which turns a four-checkout
setup into an unzip; `--list` prints each vendored file's mtime so drift is visible at
build time. No credential ships in the zip.

**Step 5 — the teammate side is one command.** `configure.py` resolves absolute paths
from its own location, checks the interpreter's imports, writes the `.env`, and merges
the `haver-chart` entry into Claude Desktop's config — backing the file up and keeping
any MCP servers already there. Hand-editing an absolute path into JSON is the step most
likely to be got wrong, and the three env variables exist precisely so it can be
generated instead.

**Evidence.** The zip was extracted to a clean folder and run as a teammate would:
`selftest.py` **31/31** against the vendored style and catalog modules, the `P:` share,
and live DLX + Neon, producing a fully RenMac-styled Philly Fed z-score render — the
render is the proof that vendoring `renmac_chart_style.py` works rather than merely
importing. The extracted tree was scanned for credential-shaped strings; the only hits
are variable names and a placeholder. Daily lane green in the same commit:
`selftest_workflow` 254/254, `selftest_transforms` 43/43, `run_daily --selftest` ALL PASS.
The live publish moved 274 entries across the four stores, and a reader pointed at the
share returns all of them; the fail-soft path was tested against an unmapped `Z:`.

---

## §14. Remote lane — `haver-chart` over HTTPS from a Windows host (PROPOSAL)

### 14.1 What this is, and the one thing it buys

§13 put the chart pipeline behind Claude Desktop over **stdio**. Stdio means the client
*spawns the server as a local process*, which has one consequence that governs
everything below: the lane only exists on a machine that has Claude Desktop, a Python
environment, and a licensed DLX install. `claude.ai` in a browser cannot spawn a local
process. Neither can the phone. So today the answer to "rebuild this chart" is always
"open your laptop".

This section proposes serving the **same two tools** over **Streamable HTTP** from a
Windows machine that has DLX, behind TLS and Microsoft Entra sign-in, registered in
Claude as a **custom connector**. That is exactly the shape `haver-metadata` already
runs in (2026_haver_mcp §14), and the pattern is proven in production against
`hvr-mcp.work`.

The one thing it buys: **the chart lane stops requiring the operator's machine.** Paste
a Haver screenshot into Claude on a phone, get a RenMac render back. No local Python, no
local DLX, no Claude Desktop.

What it does *not* buy is a second lane. There is no new chart logic, no new tool, no new
vocabulary. The transport changes and nothing else does.

### 14.2 Why the AVD is the test bed and not the destination

Aman has DLX installed on an Azure Virtual Desktop Windows session host. That machine is
the cheapest possible way to answer the questions that actually carry risk, and it should
be used for exactly that and then retired from the design.

**What the AVD can prove, and nothing else can prove without spending money:**

- That `Haver.direct("on")` works from a **non-interactive** caller on a server-class
  Windows host — i.e. that DLX serves a background process, not just the desktop app in
  front of a logged-in human. This is the single largest unknown in the whole proposal.
- That a DLX session **survives disconnect** (closing the remote-desktop window while the
  session stays signed in), and how long it survives before 2FA or an idle policy kills
  it.
- That the transport swap, the Entra flow, and the Claude connector registration all work
  end to end, against a real render.
- That a chart rendered remotely is **pixel-identical** to the same chart rendered locally
  — i.e. that HTTP changed the delivery and not the product.

**Why it cannot be the destination.** An AVD session host is a desktop, not a service
host. Three properties disqualify it: sessions sign out on the organisation's idle policy
and take the server process with them; a pooled host with FSLogix discards anything
installed outside the profile container on the next reboot; and it has no stable inbound
identity, so "always on" is not a setting it has. If the pilot passes, production is a
dedicated always-on Windows VM (§14.11 G10i) and the AVD work carries over unchanged —
the artefacts are the same zip, the same env vars, the same tunnel config.

Stating this now matters because the failure mode is building the pilot, liking it, and
letting it become the production system by accident.

### 14.3 What is reused unchanged

Almost everything, and one item is a genuine windfall.

**The G9e teammate package is already the deployment artefact.** §13.14 step 4 solved
relocatability for a completely different reason — putting the lane on a colleague's
laptop — and a server is just another machine that is not Aman's. `dist/haver-chart-<sha>-<date>.zip`
already vendors `renmac_chart_style.py` and the three catalog modules, already drives
every external path through `ECON_TEMPLATES_CHARTS` / `HAVER_MCP_SERVER` /
`CLARIFIED_KNOWLEDGE_DIR`, and already ships a `selftest.py` that fails at pre-flight
naming the variable to set. Installing the lane on the AVD is *unzip the package*. No
four-repo checkout, no clone, no path surgery. This is the reason the pilot is days of
work rather than weeks.

Also unchanged and load-bearing:

- **`bootstrap.seal_stores`.** The G9d decision that the chat lane never writes the
  knowledge stores is what makes a shared server safe at all. A multi-caller server is
  the strongest possible argument for that seal, not a reason to revisit it.
- **The lane is stateless by decision** (§13.12 answer 1). No ledger, no session, no
  cursor. There is nothing per-user to keep, so there is nothing per-user to get wrong.
- **Matplotlib is already headless.** `matplotlib.use("Agg")` at `src/render.py:43`. No
  display, no GUI, no interactive session required. This part of the migration is free.
- **The image already travels inline.** `Image(path=...).to_image_content()` base64-encodes
  the PNG into the JSON-RPC payload, which is transport-agnostic. Typical render is
  100–205 KB, so ~135–275 KB on the wire. Nothing to change.
- **Postgres is already connect-per-call** against Neon's pooled endpoint with no
  module-level pool, so it is concurrency-safe by construction.
- **`stdio` stays the default.** Hard invariant, mirroring `HAVER_HTTP` on the metadata
  server: with the new variable unset, the process behaves exactly as it does today.
  Every teammate zip already in the field keeps working, untouched.

### 14.4 What actually changes — the transport swap

Three edits to `haver_chart/server.py`, all additive, all inert when the switch is off.

**(a) The transport flag and the run block.** Installed FastMCP is 3.4.7 and
`run(transport=..., **kwargs)` is already in its signature, so this needs no dependency
change:

```python
HTTP_ENABLED = os.environ.get("HAVER_CHART_HTTP", "").lower() in {"1", "true", "on", "yes"}
...
if __name__ == "__main__":
    if HTTP_ENABLED:
        # Loopback only. cloudflared is the edge; uvicorn is never publicly bound.
        mcp.run(transport="http", host="127.0.0.1",
                port=int(os.environ.get("HAVER_CHART_HTTP_PORT", "8100")))
    else:
        mcp.run()
```

Port **8100**, not 8000, so a box can eventually host both servers without a clash.

**(b) `_build_auth()`,** copied from `2026_haver_mcp/server/server.py:53-86` with the env
variables renamed. The rename is not cosmetic: each server needs its **own** Entra app
registration because the redirect URI differs, so `AZURE_CLIENT_ID` cannot be shared
between two servers on one host. Use `HAVER_CHART_AZURE_CLIENT_ID`,
`HAVER_CHART_AZURE_TENANT_ID`, `HAVER_CHART_AZURE_CLIENT_SECRET`.

Keep the **fail-fast** check verbatim. A missing secret must stop the process, never
start it unauthenticated on a port that a tunnel is about to publish.

**(c) `mask_error_details` becomes conditional — and this reverses today's comment.**
`server.py:31-36` argues, correctly, that the pipeline's raises *are* the product: an
operator who sees `unmappable applied_transform 'Avg, % p.a.'` can restate the transform.
That reasoning holds for the intentional guardrails, which all reach the client as
`ToolError(lane.explain(exc))` — and FastMCP passes `ToolError` messages through
**regardless of masking**. What masking suppresses is the *unintentional* exception: a
stack trace, a `C:\Users\asingh\...` path, a psycopg error carrying a connection string.
On stdio those went to one trusted local operator. On a public endpoint they are a leak.
So: `mask_error_details=HTTP_ENABLED`. Guardrails stay verbatim; accidents stop talking.

**(d) A `/health` route** with no auth and no DB touch, for the tunnel and for uptime
checks. Same three lines as the metadata server.

**(e) `structured_content["path"]` stops making sense.** It is a server-local absolute
Windows path. Remotely it is worse than useless: the model will cheerfully tell a phone
user their chart is at `C:\Users\asingh\...`. In HTTP mode return `chart_id` instead. The
image itself is inline, so nothing is lost. A served `/charts/{id}.png` URL is deliberately
deferred (§14.13 Q4) — it needs its own auth story and buys little while the PNG already
arrives in the message.

### 14.5 Making the lane safe for a long-lived, multi-caller process

Four defects that stdio hid. Every one of them is invisible when a process serves one
operator for five minutes and then dies, and every one of them bites a server that stays
up for weeks.

**The important framing: three of the four bite with a *single* user.** Claude routinely
issues parallel tool calls within one turn — "make me both charts" is two concurrent
`render_chart` invocations — and FastMCP's HTTP transport runs sync tool functions in a
thread pool. Concurrency is not a multi-tenancy problem here. It is a one-user problem.

| # | Defect | Where | Fix |
|---|---|---|---|
| 1 | Output filename collides | `lane._save_path` writes `outputs/chat/<date>/<stem>.png`; `stem` defaults to `chart` | Unique suffix per call |
| 2 | Figure never closed | `render.render()` returns `fig`; `build_chart.render_row:742` discards it; nothing calls `plt.close` | Close in the lane |
| 3 | pyplot global state races | `plt.subplots()` at `render.py:295`, `plt.FuncFormatter` at `:145` | Serialize renders |
| 4 | Catalog latches dark forever | `haver_search._READY` is a permanent tri-state latch | Re-check with a cooldown |

**On #1** — the hazard is sharper than "two files with one name". `lane.render` writes the
PNG and *then* `server.render_chart` reads it back through `Image(path=...)`. A second
call landing between those two steps hands caller A caller B's chart. `outputs/chat/2026-08-21/chart.png`
exists in the tree today, so the default stem is used in practice. Fix:
`<stem>-<uuid4hex[:8]>.png`, keeping the readable stem. This costs disk on iteration (a
retitle produces a new file rather than overwriting), which §14.13 Q3 turns into a
retention sweep.

**On #2 and #3** — both fixes belong in `haver_chart/lane.py`, **not** in `src/`. A
module-level `threading.Lock()` wrapping the `BC.render_row` call serializes every render
in the server process, and `plt.close("all")` inside that lock reclaims the figure. The
lane process never holds figures for any other purpose, so `close("all")` is safe there
in a way it would not be in shared code. Keeping both in the lane means the daily lane's
render path is **byte-for-byte untouched**, which is what preserves the §13.10.1 pixel
equivalence and keeps the blast radius at zero.

The lock also happens to close a fifth hole for free: `outputs/raw/*.parquet` is written
by `g4_lib.pull` with no lock and no temp-then-replace, and every cache write happens
inside a render. Serialized renders mean serialized cache writes. That covers the server
process against itself; it does **not** cover the server against a `run_daily` running on
the same box, which is why §14.13 Q2 exists.

**On #4** — `src/haver_search.py:27,68-82` latches `_READY = False` on the first failure
and never re-checks. On a five-minute stdio process that is fine. On a server it converts
a transient startup race — the `.env` not readable for two seconds, Neon cold — into
permanent silent degradation where every description-only series parks forever and
nothing ever says why. This one is in `src/` and therefore shared, so it ships with the
daily-lane regression suites green in the same commit.

### 14.6 The public edge — Cloudflare Tunnel onto `hvr-mcp.work`

The metadata server sits on a droplet with a public IP, so Caddy terminates TLS directly
and the DNS record is deliberately grey-cloud. **An AVD session host has no public IP and
no inbound path**, and asking corporate IT to open 443 to a desktop is a request that
should be refused. So the edge is inverted: `cloudflared` makes an **outbound** connection
and Cloudflare publishes the hostname.

This is a good fit for a pilot for reasons beyond necessity: no firewall change, no IT
ticket, no inbound attack surface at all, free, and it stops cleanly when the tunnel
process stops.

- **Hostname:** `chart.hvr-mcp.work`. The domain is already at Cloudflare Registrar, so
  this is one `cloudflared tunnel route dns` command, not a purchase.
- **Named tunnel, not a quick tunnel.** `trycloudflare.com` URLs are random and change on
  every restart; the Entra redirect URI is fixed, so a changing hostname breaks sign-in on
  every restart.
- **Ingress:** `chart.hvr-mcp.work` → `http://127.0.0.1:8100`.
- **Coexistence:** the new proxied CNAME sits alongside the existing grey-cloud root A
  record for the droplet. They do not interact.

**Two risks this edge introduces, both specific to it, both absent on the droplet.** First,
a tunnelled hostname is necessarily **orange-cloud proxied**, and Cloudflare's proxy
returns 524 if the origin takes longer than ~100 s to respond. A render is seconds, but a
cold Neon compute plus `haver_search`'s four-retry backoff can reach ~14 s before the pull
even starts, so the margin is real but not enormous. Second, Streamable HTTP leans on SSE,
and proxy buffering behaviour needs to be observed rather than assumed. Both are things
the pilot exists to measure — and both disappear on the production VM, where Caddy on a
public IP reproduces the droplet's arrangement exactly.

### 14.7 Identity — and why the Entra assignment list is the licence boundary

Auth is a straight copy of the metadata recipe (2026_haver_mcp plan §14.4): single-tenant
app registration, Web redirect URI `https://chart.hvr-mcp.work/auth/callback`, an exposed
API scope named `read`, `"requestedAccessTokenVersion": 2`, a client secret, and
**"assignment required" turned on**.

The reason OAuth rather than a bearer token is not negotiable, and it is worth restating
because it looks like over-engineering: Claude's custom connector brokers the connection
through Anthropic's cloud, and the connector requires OAuth 2.1 with Dynamic Client
Registration and PKCE. Entra does not implement DCR. FastMCP's `AzureProvider` is an OAuth
**proxy** that presents DCR to Claude while using one fixed app registration upstream.
That is why the pattern exists.

**The part that is specific to this section.** `Haver.direct("on")` is a process-global
switch into a per-user-licensed DLX install. Every request a shared server serves runs
under the **server account's** entitlement, not the caller's. That is a licensing question
before it is a technical one, and 2026_haver_mcp plan §15.12 already made it a formal STOP
gate: no central Windows data server without written confirmation that the Haver licence
permits one DLX to serve the team.

The pilot threads that needle honestly rather than dodging it. **Assign exactly one person
— Aman — in Entra.** One user, one DLX, his own entitlement, reached from his own phone
instead of his own laptop. That is not redistribution under any reading; it is the same
person using the same licence through a different door. The licence conversation becomes
necessary at precisely the moment a **second** name is added to the assignment list, and
G10h makes that a hard stop rather than a checkbox someone ticks on a Friday.

So the Entra assignment list is not merely access control here. It is the mechanism that
keeps the pilot inside the existing licence, and it must be treated with that weight.

### 14.8 Secrets and environment on a server

**The mechanism that supplies the environment today disappears.** All three path variables
reach the process through the `env` block that `configure.py:105-113` writes into
`claude_desktop_config.json`. A remote server has no MCP client spawning it, so that block
has no analogue.

Replace it with what the metadata server does: `load_dotenv(<absolute path>)` at the top of
`haver_chart/server.py`, reading a gitignored `config/.env` beside the install. This is
safe to add unconditionally — `load_dotenv` does not override variables already present in
the process, so a teammate's Claude-injected `env` block still wins on their laptop, and
nothing about the stdio path changes.

Full inventory for a server install:

| Variable | Purpose | Absent |
|---|---|---|
| `ECON_TEMPLATES_CHARTS` | RenMac style module (vendored in the zip) | **Process will not start** |
| `HAVER_MCP_SERVER` | Catalog modules; also fixes where `.env` is read from | Catalog dark, descriptions park |
| `CLARIFIED_KNOWLEDGE_DIR` | The four ratified stores | Recall collapses; renders raise on legend |
| `NEON_READONLY_DATABASE_URL` | Catalog login | Catalog dark |
| `HAVER_CHART_HTTP` | Transport switch | stdio (today's behaviour) |
| `HAVER_CHART_HTTP_PORT` | Loopback port | 8100 |
| `HAVER_CHART_PUBLIC_URL` | OAuth base; must match the Entra redirect base | Fail-fast |
| `HAVER_CHART_JWT_SIGNING_KEY` | Stable across restarts so sign-ins survive | Fail-fast |
| `HAVER_CHART_AZURE_*` | Entra client/tenant/secret | Fail-fast |

Two operational notes. `CLARIFIED_KNOWLEDGE_DIR` should point at a **local copy** on the
server, not at `P:`, because `load_legend()` is re-read several times per render and
`_read_json` swallows a share hiccup as `{}` — which turns a working chart into a "legend
label not confirmed" raise. The AVD may not have `P:` mapped for a service account anyway.
And the server needs no `ANTHROPIC_API_KEY`, no Graph credential, and no Teams secret; the
chat lane reaches none of that (§13.5).

### 14.9 Phases

Ordered so that each phase can fail without wasting the next one's work, and so the
riskiest unknown is answered first and cheapest.

**Phase 0 — AVD reconnaissance. No production code. Half a day of attention plus a night
of wall time. Answers whether the rest is worth starting.** Two harnesses, both read-only,
both installing nothing and needing no admin rights.

**(a) `scripts/g10b_avd_recon.ps1` — the cheap half, one shot.** Host identity and whether
an AVD agent is present; FSLogix (a pooled host discards installs outside the profile
container on reboot); the Terminal Services policy keys, of which `MaxDisconnectionTime`
is the one that matters because a *sign-out* kills the server where a *disconnect* does
not; Python; a live `LR@USECON` pull proving DLX answers a non-interactive caller;
outbound reachability for `cloudflared` on UDP 7844 and the TCP 443 HTTP/2 fallback, plus
proxy detection; local-admin rights (needed only to run `cloudflared` as a service); and
`P:`. Every non-PASS line prints the consequence, because a host fact is only useful if
you know which part of §14 it breaks.

**(b) `scripts/g10b_dlx_watch.py` — the decisive half, overnight.** Start it, disconnect
the remote-desktop window **without signing out**, and read the log the next morning. It
probes two shapes every ten minutes because the server is both at different moments: an
**in-process** pull through a handle that called `Haver.direct("on")` once (the server
between restarts) and a **subprocess** pull from a cold interpreter (the server *after* a
restart). Which one fails first is diagnostic. In-process failing alone means a long-lived
handle rots and the server needs periodic recycling. Subprocess failing alone is worse: it
works until the first restart and then cannot come back unattended, i.e. it fails at 3am
rather than in front of you. The last clean timestamp is the pilot's usable window and the
direct evidence for G10i. The log is flushed every cycle, so a session that dies still
leaves the finding on disk.

Both were smoke-tested on `RMACRO053` (Aman's own DLX machine, not an AVD) before being
carried over: recon 0 FAIL, and both probes PASS with a live 19-row pull.

**Phase 1 — make the lane server-safe. Code only, local machine, no network.** The four
fixes in §14.5, plus the `load_dotenv` of §14.8. Daily-lane regressions green in the same
commit. Nothing here is remote-specific; all of it is latent-bug repair that stands on its
own merits even if the pilot is abandoned.

**Phase 2 — transport swap, loopback only.** The §14.4 edits. Install the G9e zip on the
AVD, write the Neon URL into `repo/config/.env`, run `selftest.py` (must be 37/37), then
start with `HAVER_CHART_HTTP=1` and no auth variables — which must **fail to start**,
proving the fail-fast guard. Then set the auth variables and drive the server from the AVD
itself over `127.0.0.1:8100`. Nothing is public yet. The three path variables no longer
need setting by hand — §14.18 — and the whole sequence is written up for an operator in
`haver_chart/SERVER_SETUP.md`.

**Phase 3 — public edge and identity.** Register the Entra app; create the named tunnel and
the `chart.hvr-mcp.work` route; run `cloudflared` as a Windows service; confirm
`https://chart.hvr-mcp.work/health` returns `{"status":"ok"}` from a phone on cellular
data. Assign only Aman in Entra.

**Phase 4 — connect and prove parity.** Add the custom connector in Claude Desktop, complete
the Microsoft sign-in, and run the §13.1 Philly Fed target end to end. Then repeat on
claude.ai and on mobile. Then the parity diff of §14.10.

**Phase 5 — decide.** G10h (licence) and G10i (production host). Only after these does
`haver-data` get the same treatment, in its own repo, against the resolved §15.12 gate.

### 14.10 Validation plan

The gate that matters is **parity**, and it reuses machinery that already exists.

1. **Pixel parity, remote vs local.** Render the same spec through the remote connector and
   through the local stdio lane, and diff the PNGs with the `ImageChops` comparison from
   `scripts/g9b_lane_equivalence.py`. When they differ, use that harness's `daily_control`
   trick — re-render locally *right now* — so a data-vintage difference can never be
   mistaken for a transport bug. This is the same discipline that produced the 6/6 result
   at G9b, applied to a new seam.
2. **Concurrency.** Ask Claude for two charts in one turn and confirm two distinct files,
   two correct images, and no cross-delivery. Repeat ten times. Without the §14.5 fixes
   this should visibly fail, which is worth observing once before fixing it.
3. **Figure leak.** Sample the process's working set after 1, 25 and 50 renders. Flat is the
   pass condition.
4. **Catalog recovery.** Start the server with the Neon URL wrong, issue a resolve (expect a
   park), fix the URL, and confirm a later resolve binds. This fails today by construction.
5. **Guardrails survive masking.** An unmapped transform must still surface verbatim as
   `unmappable applied_transform 'flurgle'` with `mask_error_details=True`.
6. **Store seal holds over HTTP.** `selftest.py` section 4 already asserts every `save_*`
   raises `StoreWriteAttempted`; re-run it against the HTTP process.
7. **Daily lane untouched.** `selftest_workflow` 254/254, `selftest_transforms` 43/43,
   `run_daily --selftest` ALL PASS, `haver_chart/selftest.py` 37/37 — in the same commit as
   every phase that touches `src/`.
9. **The pre-flight runs where it is aimed.** `haver_chart/selftest.py` from an extracted
   zip, on a machine that is not the developer's, with no path variables set. This is
   listed as a validation step because assuming it rather than doing it is exactly how
   §14.18 stayed hidden.
8. **Session durability.** Leave the tunnel and server up, disconnect the AVD session, and
   render from the phone at +1 h, +4 h and the next morning. Record where it breaks; that
   number is the argument for the production VM.

### 14.11 Gates

| Gate | What | Status |
|---|---|---|
| G10a | This section signed off | **open — Aman** |
| G10b | **STOP** — AVD reconnaissance: DLX answers a non-interactive caller and survives a disconnected session; `cloudflared` permitted | **PASSED IN PART 2026-08-23 (§14.16).** DLX serves a non-interactive caller with the **desktop app closed** — the Python API needs the credential, not the window. An **expired** credential raises a GUI login modal, so the un-credentialed path blocks rather than fails. **Outstanding: the disconnected-session watch verdict.** |
| G10c | §14.5 fixes landed; daily lane green | **PASSED 2026-08-23 — selftest 37/37, workflow 254/254, transforms 43/43, `run_daily --selftest` ALL PASS, lane equivalence still 6/6 (§14.15)** |
| G10d | Transport swap; HTTP server serves tools on loopback; fail-fast proven with auth vars unset | **PASSED 2026-08-23 — `scripts/g10d_http_check.py` 8/8; stdio handshake unchanged, `/mcp` 401 unauthenticated (§14.17)** |
| G10e | Tunnel + Entra live; `/health` public; connector completes browser sign-in | **PASSED 2026-08-24 (§14.19).** `chart.hvr-mcp.work` live over a named Cloudflare tunnel (4 QUIC edge connections); `/health` verified from the AVD, from a second machine and from a phone on cellular; `/mcp` **401** unauthenticated; Entra single-tenant app with `read` scope, token v2, assignment required, one name assigned; Claude Desktop custom connector signed in and listing both tools |
| G10f | **STOP** — parity: remote render pixel-identical to a same-vintage local render | **harness built 2026-08-24 (§14.21); awaiting a real connector PNG** |
| G10g | Claude web and mobile render a chart with no local DLX | **open** |
| G10h | **STOP** — written confirmation the Haver licence permits it, before a **second** name is assigned in Entra | **PASSED 2026-08-24 — Aman holds written confirmation that the licence permits one central DLX to serve the team.** This is the same gate as 2026_haver_mcp §15.12(2), which is resolved by the same document; G10j unblocked |
| G10i | **STOP** — decide production host (dedicated always-on Windows VM) vs stopping at the pilot | **DECIDED 2026-08-24 — stay on the pilot.** The AVD remains the host; no dedicated VM is provisioned yet. Re-open when a second name is actually assigned or the lane is depended on for a deliverable, since §14.12's session-persistence risk is unfixed, not absent |
| G10j | `haver-data` gets the same treatment — in 2026_haver_mcp, against its §15.12 gate | **unblocked by G10h; specified in 2026_haver_mcp §17** — separate server on `data.hvr-mcp.work`, same tunnel and same Entra app |

Hard stops: `stdio` remains the default and every shipped teammate zip keeps working
unchanged; no change to the `run_daily` chain; `seal_stores` stays and no `save_*` is ever
reachable; no second Entra assignment before G10h; the AVD is never treated as production.

### 14.12 Risks carried in

- **DLX will not serve a background or disconnected session.** The proposal-ending risk,
  which is why it is G10b and why Phase 0 costs half a day. No mitigation — it is a
  finding, not a bug.
- **The DLX credential expires weekly (Aman, 2026-08-23).** Signing in to the DLX app
  once carries data pulls for about seven days, with a fresh prompt each Sunday. This is
  much better news than a per-session or few-hourly 2FA, and it makes an unattended
  server plausible — but it is not free, and it changes the operating model in three
  ways. (1) The service acquires a **standing weekly manual touch**: somebody signs in to
  the host every weekend or the connector is dead on Monday. (2) It acquires a **weekly
  failure window** that lands on the same day every week, which on a shared service is a
  recurring support burden rather than a one-off. (3) Most importantly, it is unknown
  what an **expired credential looks like to a headless caller** — a clean exception the
  server can surface and alert on, or a blocking prompt that wedges the process with no
  desktop to answer it. The second would turn a predictable weekly outage into a hung
  service. §14.13 Q6 carries this, and the watcher's subprocess timeout is deliberately
  written to make a login prompt legible when it happens.
- **The AVD host is pooled** and discards the install on reboot. Mitigation: the install is
  an unzip, so re-installing is minutes; but it makes the production VM urgent rather than
  optional.
- **Idle policy signs the session out** and takes the server with it. Expected. §14.10 test
  8 measures it rather than assuming it.
- **Cloudflare proxy timeout or SSE buffering.** ~100 s origin limit and unobserved
  buffering behaviour. Both vanish on a public-IP host with Caddy.
- **Licence.** Contained by the single Entra assignment, and gated at G10h. The containment
  only holds if the assignment list is actually treated as a gate.
- **Secret sprawl.** Six new secrets on a machine Aman does not administer. Keep them in a
  root-readable `config/.env`, never in the repo, and record the client-secret expiry — the
  metadata deployment already carries secret rotation as an open item, and a second app
  registration doubles it.
- **Corporate policy.** An outbound tunnel from a managed desktop publishing an internal
  service may well be against policy regardless of technical feasibility. Worth asking
  before building, not after.

### 14.13 Open questions — answered 2026-08-23 except where noted

1. **Pilot scope — ANSWERED: `haver-chart` only.** It exercises DLX, the catalog, auth,
   the transport *and* the image path in one server, so it proves strictly more than
   `haver-data` would, and `haver-data` becomes a small copy of the pattern afterwards
   (G10j). Doing both at once would split the work across two repos before either is
   proven.
2. **`run_daily` on the same box — recommendation stands: never co-locate.** If the daily
   lane ever runs on the server it shares `outputs/raw/*.parquet` with the HTTP process
   and the render lock no longer covers the cache. The server is a separate checkout with
   its own `outputs/`.
3. **Retention — ANSWERED: 14 days.** Unique filenames (§14.5 fix 1) mean `outputs/chat/`
   grows without bound on a long-lived host, so a sweep deletes date folders older than
   14 days. It runs in the server process, not as a scheduled task, so the policy travels
   with the install.
4. **Served image URL — deferred.** `/charts/{id}.png` would let a user open a full-size
   chart outside the chat, but it needs its own auth story and the inline image already
   works. Revisit only if the inline image proves too small to read on a phone.
5. **Hostname — `chart.hvr-mcp.work`,** one hostname per server rather than path-based
   routing, matching the one-app-registration-per-server rule of §14.4(b).
6. **What does the weekly DLX expiry look like to a headless caller? — OPEN, and it is
   the one Phase 0 question that cannot be answered in an hour.** Two sub-questions, in
   order of importance. **(a) Failure shape:** when the credential lapses, does a
   background `Haver.data` raise promptly, or block on a prompt nobody can answer? A
   raise is manageable — the server catches it, returns a clear "DLX sign-in required"
   to the operator, and an uptime check alerts. A block wedges the process and every
   subsequent request queues behind it. The cheapest way to find out is to let
   `g10b_dlx_watch.py` run **across a Sunday boundary**: its subprocess probe has a
   timeout precisely so a login prompt shows up as a timeout rather than a hang, and the
   in-process column will show whether an already-open handle outlives the expiry.
   **(b) Whether it can be avoided at all:** worth asking Haver whether a service account,
   a longer-lived token, or an unattended mode exists. That question rides along with the
   G10h licence conversation rather than being a separate approach.

   Note that this is a **different** question from G10b. The weekly expiry is about
   credential lifetime; G10b is about session attachment — whether DLX serves a caller
   while no desktop session is attached. A week-long credential says nothing about the
   second, and the hour-long disconnect test says nothing about the first.

### 14.14 What this section deliberately does not do

It does not touch the daily lane. It does not add a tool, a transform, or a row field. It
does not relax the store seal. It does not move the knowledge stores. It does not make the
AVD a production host, and it does not assume the Haver licence permits anything it does
not already permit today.

### 14.15 Phase 1 built — the lane is server-safe (G10c, 2026-08-23)

The four §14.5 defects are fixed and the `load_dotenv` of §14.8 is in. No transport change
yet; with `HAVER_CHART_HTTP` unset — which is still every install in the field — the lane
behaves exactly as it did before.

**Three of the four fixes landed in `haver_chart/lane.py`, deliberately, not in `src/`.**
A process-wide `threading.Lock` wraps the `render_row` call and `plt.close("all")` runs in
its `finally`; `_save_path` appends a `uuid4[:8]` token. Keeping all three in the lane
means the daily lane's render path is byte-for-byte unchanged, which is what preserves the
§13.10.1 pixel equivalence — and it does: 6/6 still identical after the change.

Two points worth recording because they are easy to get wrong later. The figure close is
in a `finally` rather than after the call, since a raised guardrail is exactly the render
most likely to be retried and leaking on the failure path would leak fastest. And the lock
covers more than pyplot: `g4_lib.pull` writes its parquet cache with no lock and no
temp-then-replace, so serializing renders also serializes every cache write this process
makes. That closes the cache-corruption hole *within* the process, which is why §14.13
answer 2 refuses to co-locate `run_daily` — a second process would reopen it.

**The fourth fix is in `src/` and could not be avoided.** `haver_search._ensure` latched
`_READY = False` permanently on the first failed probe. It now caches a failure for 60s
and re-probes after that. The latch was harmless when a process lived five minutes; on a
server it converted a two-second startup race into permanent silent degradation, parking
every description-only series for the life of the process with nothing saying why. Note
what the latch actually guards, which the original comment understated: only the module
import and the dotenv load. A missing `NEON_READONLY_DATABASE_URL` does not park — it
raises out of `db.run_query`, because `_is_transient_db` correctly declines to treat a
`RuntimeError` as a cold-start blip.

**Retention (§14.13 answer 3) ships with the change that creates the need.** Unique
filenames mean the folder only grows, so `_sweep_outputs` drops date folders older than
`CHAT_RETENTION_DAYS` (default 14), at most once per process per day, never today's, and
only for well-formed date names so the harnesses' `_control` namespace survives.

**Evidence.** `haver_chart/selftest.py` **37/37** — a new section 9 asserts the properties
directly: two `_save_path` calls with the same filename differ while keeping the readable
stem, `plt.get_fignums()` is empty after six live renders, the render lock exists, and the
sweep both deletes past the cutoff and spares a non-date folder. Daily lane green in the
same commit: `selftest_workflow` 254/254, `selftest_transforms` 43/43, `run_daily
--selftest` ALL PASS. `scripts/g9b_lane_equivalence.py` **6/6 match, 0 differ**.

### 14.16 DLX findings from the AVD (G10b, 2026-08-23)

Two results, one much better than expected and one that needs engineering rather than
hope.

**The Python API does not need the desktop app.** With DLX closed entirely, a pull still
succeeds. What the API needs is the *credential*, not the window — so the server does not
have to keep a GUI application running, and nothing about the lane depends on a visible
desktop. This is the finding that makes an unattended host plausible at all.

**An expired credential raises a GUI login modal** (`Haver Login2 DLXVG3 6.0.1.3d`, an
email-and-password window). This is the failure shape §14.13 Q6(a) was worried about, and
it is the unfavourable one: the un-credentialed path **blocks on a dialog** rather than
raising. On a host with no attached interactive desktop that dialog has nowhere to appear,
so the call does not fail — it waits.

Aman's mitigation is to sign in manually every Saturday night, ahead of the Sunday prompt.
That is sound and it is the right operating procedure, but it lowers the *probability* of
hitting the state without removing the *failure mode*, and one missed weekend is enough.
So the design has to assume the block will happen eventually.

**The consequence lands squarely on the §14.5 render lock, and it is worth being explicit
about.** Every `render_chart` holds that lock across the whole `render_row` call, and the
DLX pull happens inside it. A blocked pull therefore does not wedge one request — it holds
the lock forever and **every subsequent render in the process blocks behind it**, silently,
with no error reaching anyone. The lock is still correct (§14.5 gives the reasons), but it
converts one stuck call into a dead server, so it needs a guard:

1. **Acquire the lock with a timeout.** A caller that cannot get it within
   `CHART_RENDER_LOCK_TIMEOUT_S` raises a plain, specific error naming a stuck render and
   the restart, instead of joining an invisible queue. This does not unwedge the process —
   nothing in-process can, because the blocked call is inside a vendor library waiting on
   a window — but it makes the failure **legible and bounded**, which is the difference
   between a support ticket and a mystery.
2. **`/health` reports render liveness**, not just process liveness. A wedged server still
   answers a plain liveness probe, which is exactly how this failure would hide. Exposing
   the last-completed-render timestamp and whether the lock is currently held lets an
   uptime check spot the wedge and restart the service.

Neither is speculative hardening: the modal in the screenshot is the mechanism, and the
weekly expiry is the schedule on which it arrives.

### 14.17 Phase 2 built — the transport swap (G10d, 2026-08-23)

`HAVER_CHART_HTTP=1` now serves Streamable HTTP on `127.0.0.1:8100` behind Entra OAuth.
Unset — which is still every install in the field — nothing changed.

**What landed.** The `_build_auth()` recipe from the metadata server, with its fail-fast
check kept verbatim and its variables renamed to `HAVER_CHART_AZURE_*`: each server needs
its own app registration because the redirect URI differs, so bare `AZURE_*` would collide
the moment a host runs both. `mask_error_details` became `HTTP_ENABLED` — the guardrails
still reach the client verbatim because they arrive as `ToolError` and FastMCP passes
those through masking, while an accidental stack trace or connection string no longer
does. `structured_content` returns `chart_id` instead of `path` over HTTP. And `/health`
answers `lane.health()`.

**The lock guard of §14.16 shipped with it,** because the two belong together: the render
lock is what makes a stuck DLX call fatal, so the timeout that makes it legible has to
exist before anything long-lived runs. `_RENDER_LOCK.acquire(timeout=...)` raises a named
error pointing at the DLX sign-in and the restart, and `/health` reports `rendering` plus
`last_render_finished` so an uptime check can tell a busy server from a dead one. A plain
200 cannot: a wedged process answers it happily.

**The harness proves the thing most worth proving, which is not HTTP.** A dozen teammate
zips already spawn this file over stdio, so `scripts/g10d_http_check.py` leads with a real
JSON-RPC handshake — `initialize`, `tools/list`, both tools — run with every
`HAVER_CHART_*` variable stripped from the environment, so a stray shell variable cannot
make the check silently test the wrong transport.

Only `fastmcp[azure]` in `requirements.txt` changed, unconditionally rather than as an
optional extra. It is a few hundred KB, and one requirements file for both transports
means a host can never be one `pip install` short of the mode it was asked to run.

**Evidence.** `scripts/g10d_http_check.py` **8/8**: stdio advertises `resolve_series` and
`render_chart`; HTTP mode with no secrets exits 1 naming all five missing variables;
with credentials it binds and `/health` returns
`{"status":"ok","rendering":false,"last_render_finished":null,"retention_days":14}`; and
`/mcp` returns **401** to an unauthenticated request, so the auth is real and not merely
configured. Daily lane green in the same commit: selftest **37/37**, workflow
**254/254**, transforms **43/43**, `run_daily --selftest` ALL PASS, lane equivalence
**6/6**.

### 14.18 The pre-flight check had never been run outside this machine (2026-08-24)

Installing the zip on the AVD produced a `FAIL` at `selftest.py` section 2 and then a
crash importing `renmac_chart_style`. The three relocatable paths of G9e step 1 were only
ever supplied by the **`env` block Claude Desktop injects**, written by `configure.py`.
Anything else driving the lane — the selftest from a shell, a server started by hand —
fell back to defaults that are absolute paths inside this developer's checkout.

So **`TEAMMATE_SETUP.md` step 5 had never worked for a teammate**, and could not have.
The AVD did not cause it; it was the first machine to attempt the step.

**Why it survived G9e.** The tool itself works — Claude injects the variables — and the
selftest is precisely the step people skip when nothing appears broken. G9e's own
evidence was gathered here, where the defaults resolve. A check that only ever ran where
it could not fail is not evidence, and this is the second time that shape of mistake has
cost a day: §14.15's `_READY` latch was also invisible until something long-lived ran.

**The fix, in `bootstrap.py`.** Read `config/.env`, then for each of the three variables
adopt the copy vendored in the package when the variable is unset *or points at a
directory that no longer exists*. The zip already carries these files, so asking the
operator to re-state where they are was a configuration step that could only be got
wrong. Consequences worth naming:

- The **source checkout is untouched** — the probe requires a `vendor/` sibling, which
  only the zip layout has.
- **A moved package now keeps working** instead of crashing on `renmac_chart_style`,
  which was a standing row in the teammate troubleshooting table. The substitution
  prints to stderr; silently using different files than you were told to is its own bug.
- **The selftest reports `(package)` distinctly from `(env)`.** A silent fallback that
  happens to be right is how this defect survived, so the distinction is now visible.
- The `.env` read moved out of `server.py` into `bootstrap.py`, which is what lets the
  selftest see the environment the server will see. Without that it cannot honestly
  call itself a pre-flight check.

**One `.env` is enough** for a server, carrying only `NEON_READONLY_DATABASE_URL`: it is
loaded into the process before the catalog module looks for its own file, and
`load_dotenv` never overrides what is already set.

**Evidence.** The shipped zip extracted to a scratch folder with all three variables
cleared: **37/37**, three `(package)` lines in section 2. Regressions unchanged — g10d
8/8, workflow 254/254, transforms 43/43, `run_daily --selftest` ALL PASS.

**Written up for an operator** in `haver_chart/SERVER_SETUP.md`: the server install is a
third audience alongside the developer (`SETUP.md`) and the teammate laptop
(`TEAMMATE_SETUP.md`), differing in transport, in where settings come from, and in who
may call it. Folding it into either of those would have made both wrong.

### 14.19 Phase 3 done — the lane is public and signed in (G10e, 2026-08-24)

`https://chart.hvr-mcp.work` serves the lane from the AVD through a named Cloudflare
tunnel, behind Entra sign-in, registered in Claude Desktop as a custom connector. Both
tools list from a laptop with no DLX and no Python.

**Sequencing did the work here.** Each stage was proven with the next one still absent,
so no failure ever had two candidate causes: `g10d_http_check` on the AVD (HTTP works on
this host, no network); then the tunnel with **placeholder Entra GUIDs** (the network path
works, no identity); then the real registration (identity works, on a path already
proven). Publishing with placeholders is safe precisely because the endpoint is closed —
nobody authenticates against a client ID that does not exist — and it is what let the
public edge be verified from three vantage points before the portal was touched at all.
The one failure that did occur, `route dns` silently never having run, took two minutes
to find because `nslookup` from a third machine could separate "no DNS record" from
"tunnel down" from "server down".

**Verified.** `/health` from the AVD, from a second machine off the AVD, and from a phone
on cellular. `/mcp` **401** unauthenticated, with `WWW-Authenticate` pointing at the
protected-resource document, which names the authorization server, whose metadata
advertises `registration_endpoint` and `scopes_supported: ["read"]` — the discovery chain
Claude walks, complete and self-consistent, and `read` matching the scope actually defined
in Entra.

**CIMD, not DCR.** Claude detected `client_id_metadata_document_supported` and chose
Anthropic's hosted client metadata. Worth keeping deliberately: dynamic client
registration would have the server persist a client record per connection, and this server
gets restarted freely (twice during setup). Registrations lost across a restart present as
an auth bug. CIMD stores nothing, so there is nothing to lose.

**Entra shape.** Single tenant; redirect `https://chart.hvr-mcp.work/auth/callback`;
exposed scope `read` with consent set to *admins and users* (choosing admins-only would
have guaranteed an admin round-trip); `requestedAccessTokenVersion: 2`; client secret with
a diarised expiry; **assignment required, one name**. That last is the licence boundary of
§14.7, and G10h still gates a second.

**Carried forward.** The server currently runs from an elevated prompt — acceptable on a
test bed, not on the production host of G10i. Both processes are foreground and die with
the session, so §14.10's durability item is unchanged and still unmeasured.

---

### 14.20 A rendered chart is now a file you can fetch (2026-08-24)

The remote lane returned the chart as inline base64 and nothing else. That is enough to
**look** at and useless to **use**: the PNG is on the AVD, so putting one in a newsletter
meant saving it out of the transcript by hand, at whatever resolution the client chose to
show. `GET /chart/<chart_id>` closes that, and `render_chart` now returns `chart_url`
alongside `chart_id` over HTTP — assembled server-side from `HAVER_CHART_PUBLIC_URL`,
because a hallucinated URL for a chart that genuinely exists is a uniquely annoying thing
to debug.

**The route is unauthenticated, deliberately.** The point is a link that opens in a
browser or drops into a document, and a browser following a link carries no bearer token.
Gating it behind Entra would leave the operator hand-crafting `curl` calls with a token
pasted from somewhere — that is not solving the problem, it is relocating it. So the URL
*is* the capability, and the id is what has to carry the weight:

- `_save_path` now names every file with a **full uuid4** (122 bits) rather than the
  8-hex prefix it used before. Those were always two different jobs sharing one string:
  eight hex is ample against a collision between two concurrent renders and far too
  little against somebody guessing a URL. Only the second job is new, and it is the one
  with the larger number.
- `lane.chart_path` applies two independent guards, because this is the only route in
  the system that turns text off the public internet into a filesystem path. A character
  allowlist rejects every traversal spelling at once — no separators, no drive letters,
  no `..`, no NUL — and a containment check on the **resolved** path then catches what an
  allowlist structurally cannot, namely a symlink inside the tree pointing out of it.
  Either alone would probably do; "probably" is not the standard here.
- Every failure returns `None` and the route answers an identical flat 404, so it cannot
  be used to probe which ids once existed.

**The cost, stated rather than glossed:** anyone holding a link can fetch that one chart
until the retention sweep removes it (`CHAT_RETENTION_DAYS`, 14). No id is derivable from
another and nothing enumerates the directory, so the exposure is one chart per leaked
link, not the archive. If a chart is ever too sensitive for that, the inline image still
works and this route need not be used. Revisit if the lane ever renders something
genuinely market-moving before publication.

Proven in `selftest.py` §10 (41/41): nine traversal spellings all rejected, the id
asserted to be 32 hex, and resolution exercised against **the real PNG rendered in
section 6** rather than a fixture built to pass. A traversal guard with no test is not a
guard.

### 14.21 The parity harness, and why it needs a control (G10f, 2026-08-24)

`scripts/g9b_lane_equivalence.py` proved the **wrapper** — ledger row through
`render_chart`'s argument surface produces the daily lane's pixels — but it drives
`lane.render()` in-process. It never touches HTTP, the tunnel, Entra, or the server's
Python. `scripts/g10f_remote_parity.py` covers that gap: it takes a PNG that came back
**through the connector** and diffs it against a local render.

The load-bearing part is *when* the control is rendered. The remote chart was drawn from
data the server pulled at that moment; compare it against anything rendered earlier and a
revision to IP or a survey index shows up as differing pixels that read exactly like a
transport bug. Rendering the control **now** removes the only variable that matters — the
data vintage — so a difference that survives it is a real difference. Same reasoning as
g9b's `daily_control`, which is why g9b's `compare` and `daily_control` are imported
rather than copied; its `argv` parsing moved into `main()` so importing it no longer
consumes the caller's command line.

Two input modes, because the interesting charts do not all have ledger rows: `--spec`
takes the `render_chart` arguments as JSON (ask Claude to print them verbatim — a spec
retyped by hand tests your transcription, not the lane), and `--ledger DATE --chart-id ID`
reproduces a finished backfill row through `daily_control`.

On a difference it names the likely causes in order rather than leaving a pixel count.
A **stale `knowledge/` copy on the server** — one older `legend_labels.json`, one
different legend string — is far commoner than a wrapper defect, and nothing about
"4723 differing px" suggests looking there.

Verified in both directions before being trusted: an identical pair reports MATCH and
exits 0; a title-only change reports DIFFER, exits 1, and puts the bounding box over the
title. A parity harness that has only ever been shown to pass is not evidence.

**G10f is not closed by this.** The harness exists; the gate needs a PNG that has
actually crossed the tunnel.

---

### 14.22 Production host — leaving the AVD (PROPOSAL, 2026-08-25)

G10i stays **DECIDED — remain on the pilot** until this section is signed off. The
connectors work; the AVD does not. Three processes (chart, data, `cloudflared`) run in
foreground windows that die with the session, the host is pooled, and idle policy can
sign the session out. That is why a dedicated always-on Windows VM is the destination,
and why it is a separate decision from "the tools work."

**Do not start this until three things land.** The overnight watch (G10b's outstanding
half, §14.9(b)) measures whether DLX survives a disconnected session — a dedicated VM
that still needs someone logged in is just a more expensive AVD. G10f needs a real
connector PNG, because a host that draws different pixels is not a replacement. And G17e
(co-tenancy of the two processes against one DLX) should be measured on the AVD first,
where a failure is cheap.

#### Why a dedicated VM, not a bigger AVD

AVD is a *session host*. The product is a signed-in desktop; the MCP servers are
passengers. A production host is the opposite: nobody is looking at it, Windows Update
and a weekly DLX sign-in are the only reasons a human logs on, and the three processes
must survive a reboot as services. Putting more RAM on the AVD does not change any of
that.

DigitalOcean and most other VPS vendors are Linux-only. DLX is Windows-only. Azure (or
another Windows-capable cloud with Hybrid Benefit) is the viable path; the AVD
subscription already has the tenancy and the Entra tenant.

#### Minimum specification

The two servers serialize their own work (one render lock, one pull lock), so extra
cores buy almost nothing until a second assigned name is live and concurrent. Memory is
the constraint: matplotlib's Agg canvas is ~1700×1220×4 bytes per render, Haver holds
a COM session, and two Python processes plus `cloudflared` sit together.

| Resource | Minimum | Why that number |
|---|---|---|
| OS | Windows Server 2022 Datacenter | DLX is Windows-only; Server, not a desktop SKU, so it can run as a service without a logged-in session |
| CPU | 2 vCPU | Renders and pulls are serialized; 2 is enough for Python + tunnel + OS |
| RAM | 8 GB | 4 GB will page during a render; 8 GB leaves headroom for two processes |
| Disk | 128 GB SSD (OS + data) | Windows Server + Python + Haver + 14 days of uniquely-named PNGs. `CHAT_RETENTION_DAYS` caps growth |
| SKU | Azure `Standard_B2ms` (2 vCPU / 8 GB) | Burstable is correct: load is a few tool calls an hour, not a sustained render farm. Step up to `D2s_v5` only if B-series CPU credits exhaust |
| Network | No inbound ports. Outbound 443 + UDP 7844 | Same Cloudflare tunnel as the AVD; the VM needs no public IP if the tunnel is the edge. A public IP is optional and costs extra |
| Identity | A dedicated domain account that can sign in to DLX | Do not run as `LocalSystem` — DLX auth is per-user and that account has no profile |

**Cost, East US, always-on, indicative 2026 numbers, not a quote.** `B2ms` compute with
Azure Hybrid Benefit (Windows licence already owned) is about **$60–70/month**. Without
Hybrid Benefit the Windows surcharge roughly doubles it. Add ~$8 for a 128 GB managed
disk and ~$4 if a public IP is attached. Reserved 1-year + Hybrid Benefit is the
sensible buy once the host is depended on. Confirm eligibility for Hybrid Benefit
before provisioning — it is a licensing question, not a portal toggle you discover
later.

Do **not** oversize "for the future." The lock serializes work; a D4s does not render
two charts at once. Scale when a second name is assigned and G17e has a number, not
before.

#### What moves, and what stays

Moved onto the VM, as a service, not a foreground window:

- `haver-chart` on `127.0.0.1:8100`
- `haver-data` on `127.0.0.1:8101`
- one `cloudflared` process with both hostnames in `config.yml`
- a **local** copy of the four knowledge-store JSON files (not `P:`)
- `config/.env` for each lane (Entra values, Neon URL, signing keys)

Unchanged, on purpose:

- The Entra app, the two redirect URIs, the Cloudflare tunnel UUID and DNS records.
  Changing the origin from AVD to the VM is a `cloudflared` credentials copy plus a
  `tunnel run` on the new host — the public URLs stay `chart.hvr-mcp.work` and
  `data.hvr-mcp.work`, so no teammate re-adds a connector.
- The daily lane. It does not run here (§14.13 Q2). A separate checkout, its own
  `outputs/`.
- Local stdio on anyone's DLX laptop. This host replaces the AVD, not the teammate zip.

#### Build order

1. **Overnight watch on the AVD** (§14.9(b), steps below). A FAIL here changes the
   host story (always-on is not enough if DLX demands an interactive session).
2. **Provision the VM.** East US, `B2ms`, Windows Server 2022, Hybrid Benefit if
   eligible, no inbound NSG rules except whatever RDP you need to sign in to DLX on
   Saturdays. Attach it to the existing Entra tenant so the same account can RDP.
3. **Install the stack once, as the DLX user.** Python 3.12, `haver`, both venvs, both
   packages, `cloudflared`, a local `knowledge\` copy. Run `selftest.py` (41/41) and
   `haver_data/selftest.py` (six `LR@USECON` values) *before* any service wrapper —
   a service that fails at boot is harder to read than a window that prints why.
4. **Copy the Cloudflare credential and `config.yml`.** Same tunnel ID, same ingress.
   `cloudflared tunnel run` in the foreground first; `curl` both `/health` URLs from a
   phone. Only then install `cloudflared` as a Windows service
   (`cloudflared service install`).
5. **Wrap the two Python servers as Windows services.** NSSM or a scheduled task at
   startup, running as the DLX user, with the working directory set to each repo root
   so `config/.env` resolves. Confirm they survive RDP disconnect *and* a reboot.
   `/health` must still answer after both.
6. **Cut over.** Stop the AVD copies. The tunnel can only have one `cloudflared`
   running against it; two origins fighting is a flapping hostname, not a failover.
   Connectors do not change. Pull `LR@USECON` and render one chart from claude.ai to
   prove the new origin, not the old one.
7. **Decommission the AVD copies.** Leave the AVD itself until G10b/G10f/G17e are
   closed in writing — it is still the cheapest place to reproduce a failure.

#### Operating model that does not exist on the AVD

- **Weekly DLX sign-in** stays. Saturday night, RDP to the VM, open the DLX app, sign
  in, disconnect. The service account's credential is what both servers use.
- **Secret rotation.** One Entra client secret, two `.env` files, two service restarts.
  Diary the expiry.
- **Knowledge freshness.** The VM's `knowledge\` is a copy. Either a scheduled pull
  from `P:\Public\RenMac_Chart_Knowledge` or a reminder when labels drift. Pointing
  the service at `P:` directly is still wrong (`load_legend` treats a share hiccup as
  `{}`).
- **Patch Tuesday.** A reboot is now a real event: services must come back on their
  own. That is the test in step 5, not a hope.

#### Gates this section would add, if approved

| Gate | What it proves |
|---|---|
| G10k | Overnight watch (G10b remaining half) recorded a duration |
| G10l | VM `/health` answers after a reboot with nobody logged in |
| G10m | Cutover: same public URLs, connectors unchanged, one live pull + one live render from a phone |

## §15. Resolver repair — words→formula, mirror tie-break, chat memory

Two complaints from 2026-09-04, which turn out to have one root between them and one
that is separate:

1. **"It parks slots that are exact matches."** True, and the resolver was *right* about
   the specific slots — but for a reason it could not articulate, which made the park
   unanswerable. §15.2.
2. **"It will not apply a transformation I described in words; it demands a Haver
   formula."** Also true, and worse than it looked: the mapper did not refuse, it
   silently plotted *half* the transform. §15.1.

Neither is fixed by loosening a threshold. Both are fixed by getting evidence to the
place that makes the decision.

### §15.1 Phase 1 — natural language cannot reach a nested formula

The evaluator (`src/transforms.py`) is fully compositional and handles arbitrarily deep
nesting. The phrase translator (`build_chart.phrase_to_haver`) was flat, with exactly one
hard-coded composition. That asymmetry is the whole defect: the machinery to *evaluate*
`zs(yryr%(x))` has always existed, and only the path from words to that string was
missing.

The failure mode is not a refusal. `_flat_transform` matches the FIRST operation it
recognizes in the phrase and returns, so a compound phrase produced a real formula that
was quietly missing half its math:

| Phrase | Produced | Should have been |
|---|---|---|
| `"3-month moving average of the monthly change"` | `movv(X,3)` — a 3mma of the **level** | `movv(diff(X,1),3)` |
| `"% Change - Year to Year, Z-Score"` | `zs(X)` — a z-score of the **level** | `zs(yryr%(X))` |
| `"zs(difa%(movv(...,3),3))"` | `diff%(X,3)` — **unrelated math** | (raise: it is a formula) |

Each plots a plausible-looking line. That is what makes this class dangerous and why the
fix is a grammar plus a loud failure, not a bigger table.

#### §15.1a `transform_key` must be fixed FIRST — DONE 2026-09-04

`resolve.transform_key` derived the legend-store key from the *phrase*, wrapped in a bare
`except` falling back to `""`. Two consequences, the second of which fixes the ordering:

1. **Pre-existing under-qualification.** For a compound-phrase slot the key was weaker
   than the math — `ZS(#)` where the formula was `zs(yryr%(GDPH))`. A later plain
   `zs(gdph)` level chart would inherit that label: the 2026-07-30 collision class
   (one ticker, two transforms, one key) reintroduced through the phrase.
2. **A new raise would silently orphan those keys.** Because the `except` swallows it,
   `phrase_to_haver` raising does not crash — it empties the transform key, changes the
   legend key, and shifts legend text on charts that currently look right.

Fix: canonicalize the slot's **own `formula`** when it has one, falling back to the
phrase only when it does not. `render_row` ignores `applied_transform` once a formula is
present, so the formula is what was actually drawn and therefore the honest source.

Composite operands collapse to a single `#`: the tag names the transform CHAIN, and which
series it wraps already lives in `legend_key_base` as `expr:NFIB7-NFIB6`. Without that
collapse every warmed composite key would shift from `ZS(#)` to `ZS((#-#))`. The collapse
must also tell a function's argument parens from a grouping paren — conflating them emits
the malformed `ZS(YRYR%#)`.

**Migration — the estimate of "3 entries" was low; the measured answer is 8.**
`scripts/migrate_legend_keys.py` replays every slot in `data/ledger_*.csv` through both
the old and new key functions and re-files only where the store holds the old key.

| | count |
|---|---|
| distinct legend keys in the ledger | 130 |
| keys UNCHANGED by the fix | 111 |
| keys that move | 19 |
| ...of which the store holds a label → re-filed | **8** |
| ...of which the store holds nothing → nothing to carry | 11 |

Every one of the 8 was holding a label that already *described* the transform its key had
dropped — the defect stated in evidence rather than theory:

| Entry | Re-filed as | Label it was holding |
|---|---|---|
| `GDPH@USECON\|ZS(#)` | `ZS(YRYR%(#))` | Real GDP **(y/y %chg)** |
| `GDP@USECON\|ZS(#)` | `ZS(YRYR%(#))` | GDP **(y/y% chg)** |
| `LAPRIVA@LABOR\|ZS(#)` | `ZS(YRYR%(#))` | All Employees: Total Private **(y/y% chg)** |
| `FNEH@USECON\|YRYR%(#)` | `ZS(YRYR%(#))` | Real Nonres Fixed Invest: Equipment (y/y% chg) |
| `JCSXEHM@USECON\|YRYR%(#)` | `ZS(YRYR%(#))` | PCE Core Svcs ex Housing (y/y %chg) — **copy, see below** |
| `NFIB6@SURVEYS\|MOVV(#,3)` | `ZS(MOVV(#,3))` | NFIB: Net % Reporting Higher Sales (SA, 3mma) |
| `FWILL@SURVEYS` *(unqualified)* | `ZS(#)` | Banks Willingness to Lend (%. lagged by 4qtrs) |
| `YPSVR@USECON` *(unqualified)* | `ZS(YRYR(#))` | Personal Saving Rate **(Z-score of y/y difference)** |

The last two carried **no transform qualifier at all** while holding a transformed label.
A bare key is reachable through `lookup_legend`'s `allow_base` fallback, so a future
*level* chart on `YPSVR` would have rendered "Z-score of y/y difference" over a plain
saving-rate line.

**Re-filing is not always a move.** `JCSXEHM@USECON|YRYR%(#)` was the key for two
different slots: one carrying `zs(yryr%(JCSXEHM))`, which now asks for `ZS(YRYR%(#))`,
and one phrase-only y/y slot, which still asks for `YRYR%(#)`. Moving it stranded the
second on the bare base key, where it picked up the neighbouring `3m %chg saar` label —
caught by the replay, not by reading. The script COPIES when the old key is still live in
the new scheme and MOVES otherwise: 7 moves, 1 copy, store 79 → 80.

**Regression proof.** All 142 distinct ledger slots looked up against the pre-migration
store under old keys and the post-migration store under new keys: 89 had a label,
**0 lost, 0 swapped**. A second `--apply` is a no-op.

#### §15.1b The compositional parser — DONE 2026-09-04

**Not** a corpus, **not** a lookup table, and nothing learned at runtime. A table cannot
cover unbounded nesting; that is the failure mode a grammar does not have.

*Atoms are finite and already written.* `_flat_transform` implements the closed Haver G3
set — `MOV[V|T|A]`, `ZS`, `LN`, `YRYR`, `DIFA`, `DIFV`, `DIFF`, each with plain/percent/
log variants. The parser reuses those rules and restates none of them.

*Composition needs two rules, and they nest in opposite directions.* Ground truth from
the ledger, not invention:

| Marker | Direction | Evidence |
|---|---|---|
| `A of B` | B is inner → `A(B(x))` | `"Z-Score of Year-to-Year % Change"` shipped `zs(yryr%(X))` |
| `A , B` / `A ; B` | left-to-right, A is inner → `B(A(x))` | `"% Change - Year to Year, Z-Score"` shipped `zs(yryr%(X))` |

*The comma has two more senses, and missing either one breaks a phrase that works today:*

- **A window.** `", 3-period"` in `"difa% of 3-term moving average, 3-period"` is an
  argument of `difa%`, not another operation — so it attaches to the OUTERMOST segment
  of the part before it, giving `difa%(movv(X,3),3)`.
- **Punctuation inside ONE operation.** `"% change, year-over-year"` is a single y/y
  percent change. A part that does not map on its own is a CONTINUATION of the part
  before it, not a new operation.

Both joins are only accepted if they **change the mapping**. If the head maps identically
with and without the fragment, the fragment was ignored — which is the silent drop this
parser exists to end. That single rule catches `"z-score of 6-month moving average,
12-period"`, where `zs` takes no window and the `12` used to vanish.

*Fail-loud is what makes an incomplete grammar safe* (decision D6). Any fragment that is
not a recognized operation raises and **quotes itself**, so the operator knows which piece
needs a formula rather than re-guessing the whole phrase. Coverage gaps degrade to
today's behaviour, never to a wrong chart.

*Routing preserves history.* A phrase with no composition marker takes the flat route
untouched.

**What the ledger actually contained.** 27 distinct phrases, classified by replaying each
through the mapper and comparing with the formula that shipped beside it:

| Class | Count | Before → after |
|---|---|---|
| flat, already correct | 17 | unchanged, byte for byte |
| compound, silently truncated | 6 | now composed; all 6 match the shipped formula |
| a transcribed formula, not a description | 3 | now a precise raise (decision D11) |

**The one deliberate divergence** (decision D9). `"6-month %Change; z-score"` yields
`zs(diff%(X,6))` where the ledger shipped `zs(difa%(PCUSERH,6))`. The words never say
*annualized*, and the sibling phrase `"6-month %Change-ann"` — also in the ledger — does.
Reading annualization in from silence is exactly the guess that produced the 2026-07-30
GDP 3.35-vs-6.81 error. The parser stays faithful to the words; the `formula` field
remains the override, which is how that row got its `difa%` in the first place.

**"growth" is a percent change** (decision D10). Left out of the percent test it would
have emitted an absolute difference on a phrase every economist reads as a rate.

#### §15.1c The fixture — DONE 2026-09-04

`fixtures/transform_phrases.json`: `phrase → expected formula`, **55 cases**, asserted by
`selftest.py`. JSON rather than YAML so the test adds no dependency the AVD might lack.
Seeded by extracting the ledger phrases automatically, then extended by hand with nesting
the history does not contain — three deep in both directions, including the pair
`"z-score of the 3-month moving average of the year-to-year % change"` and
`"% change - year to year, 3-month moving average, z-score"`, which must produce the same
formula through opposite markers.

Each row carries a `source`: `ledger-flat` (pinned to the pre-parser output, the no-drift
half of G19a), `ledger-compound` (pinned to the shipped formula), `workflow` (mirrored
from `scripts/selftest_workflow.py`), or `hand`. Rows with `raises: true` are asserted to
raise **and to quote the fragment they choked on**.

**Read only by the test.** If the parser ever consults it at resolve time, this design has
failed.

#### §15.1d A window is stated in a UNIT; G3 counts it in PERIODS — DONE 2026-09-04

Surfaced by the operator while ratifying D10, and it is the same silent-wrong class as the
rest of §15.1. `difa%(x,2)` on a monthly series is a **two-month** annualized rate. A
phrase reading `"2-quarter annualized growth"` bound to a monthly series therefore plotted
something six times shorter than it said — far noisier, and plausible enough to pass
review. Chart 4 of the G19f commentary is worded exactly this way.

The series frequency now threads from `_freq_of` (already computed at the render site)
through `_applied_formula` into the mapper, and a window naming a different unit converts:
`2 x 12/4 = 6`. A window that will not divide evenly — two months of a quarterly series —
raises rather than rounding to something the words did not say. With no frequency the
number is taken literally, exactly as before.

**Blast radius: zero.** All 65 historical (phrase, ticker) render pairs produce the
identical formula with and without the conversion — every phrase in the history names the
unit its series is sampled at, which is why this never bit.

### §15.2 Phase 2 — two exact matches park with no tie-break — DONE 2026-09-04

`resolve_slot` bound on `len(exact) == 1`. The Jaccard fallback sat under `elif not
exact`, so **two** exact matches reached no branch at all and parked. Two exacts were
served strictly worse than zero.

Haver mirrors the same series across `usecon` / `bci` / `labor` / `empl` under a
byte-identical descriptor, and `descriptor_exact` compares normalized token sets with the
units parenthetical stripped — so the normalization that makes matching robust also
guarantees mirrors are indistinguishable. There is no database preference anywhere in
`src/`.

#### The catalog metadata is too thin to judge a tie — Haver's own is not

`haver_search.get_meta` returns **four** fields (descriptor, frequency, agg_type,
sa_status). On those four the 2026-09-04 slot A looks like a perfect mirror, which is why
an early reading concluded "auto-bind `usecon`". That was **wrong**. `Haver.metadata`
returns **eighteen** and settles it:

| field | `lrtmanua@usecon` | `a0m001@bci` |
|---|---|---|
| shortsource | **BLS** | **CB** (The Conference Board) |
| startdate | 2006-03-31 | **1945-01-31** |
| enddate | **2026-08-31** | 2026-07-31 |
| numobs | 246 | **979** |

**Two different series from two different statistical agencies.** The resolver was RIGHT
to park slot A. The defect was never that it parks — it is that the park message ("both
scored 1.0, similarity is not evidence") was *unanswerable*, when the evidence to answer
it was one 200 ms call away. §15.2 is a **park-quality** change first and a park-reduction
change second.

#### Which fields discriminate, and which must not

`LANAGRA@USECON` vs `LANAGRA@LABOR` match on source, start, end, obs count, descriptor and
aggregation, and differ **only** on `group` (E30 vs E40) — a catalog filing tag. Treating
`group` as discriminating would over-park a genuine mirror.

- **Discriminating (data-bearing):** `shortsource`, `longsource`, `startdate`, `numobs`,
  `frequency`, `aggtype`, `magnitude`, `datatype`, `diftype`.
- **NOT discriminating (catalog bookkeeping):** `group`, `decprecision`, `datetimemod`,
  the `geography` codes.
- **`enddate` alone is not a difference** — a mirror can be refreshed on a different
  schedule. It only ever chooses between two `usecon` candidates, where the fresher wins.

`diftype` was added to the data-bearing list: the plan enumerated both lists and it
appeared in neither, but it describes the series' difference basis.

**SA status needed a field of its own after all — corrected 2026-09-05 by G19f.** The
first reading of this section argued that SA needs no field, because it lives in the
descriptor's units parenthetical and units surface as `magnitude`. That was **measurably
wrong**, and G19f is what caught it. `lapriv@labor` (NSA) and `lapriva@labor` (SA) are
both BLS, both 1052 observations from 1939-01-31, both `AVG`, both magnitude 3, both
`Units`, both monthly. `Haver.metadata` has **no SA field at all**, and
`descriptor_exact` strips the very parenthetical that carries it. So an SA/NSA twin
arrived as a descriptor-exact tie that was identical on all nine data-bearing fields —
i.e. it read as a perfect mirror, and the tie-break would have bound one at random.

**D14 (2026-09-05): an SA/NSA pair is not a park, it is a default.** Once the twins are
correctly identified as two vintages of ONE series, there is nothing for the operator to
adjudicate — commentary charts are about momentum, and the house answer is seasonally
adjusted. So the resolver binds the SA copy, falls back to the raw one only when no SA
copy exists, and binds NSA when the read asks for it (an explicit `sa_hint`, or the words
"NSA" / "not seasonally adjusted" in the descriptor). The bind reason says how to ask for
raw, so the default is visible rather than silent.

This applies ONLY when the candidates agree on every identity field — source, frequency,
aggregation, magnitude, datatype, diftype. `numobs` and `startdate` are allowed to differ,
because a seasonally adjusted copy legitimately begins later than its raw sibling. A pair
differing on SA *and* on source is still two different series and still parks.

Three guards now cover this area, and the order matters:

1. **Two codes in the SAME database are never mirrors.** A database does not hold one
   series twice under two names, so the pair is two different series that happen to
   normalize to one descriptor. This is a structural invariant, so it does not depend on
   having found a field that separates them — which is exactly what let the SA case
   through the first time.
2. **`seasonal_adjustment`, derived** from the descriptor's last parenthetical at token
   level (so "Not Seasonally Adjusted" inside a series NAME cannot be mistaken for the
   units tag), is compared as data-bearing.
3. **When seasonal adjustment is the ONLY difference, D14 decides** rather than parking.
   This runs before guard 1, because an SA/NSA pair inside one database would otherwise
   hit it.

**What this prevented, measured.** Before the fix, "All Employees: Total Nonfarm" bound
`lanagr@usecon` — which is the **NSA** series — for chart 5, a payroll-momentum chart
that plainly wants SA. It now parks and asks. That is one fewer chart bound and one fewer
chart *wrong*, which is the trade this whole section is built on.

**Resolution, in order:** candidates sharing a database, **park** (they cannot be
mirrors) → fetch `Haver.metadata` for tied candidates only (cached per
process) → any data-bearing field differs, including derived seasonal adjustment,
**park** and show which (decision D2) → all
match, **bind the `usecon` copy** (its presence in the tie is the evidence the request is
US-scoped, so this can never reach for `usecon` on a non-US request) → true mirrors with
no `usecon`, **park**; do not invent an ordering between `labor` and `empl`.

#### Measured

| Candidates | Verdict | Evidence |
|---|---|---|
| `lrtmanua@usecon` vs `a0m001@bci` | **PARK**, correctly | differ on `shortsource`, `longsource`, `startdate`, `numobs` |
| `lanagra@usecon` vs `lanagra@labor` | **BIND `usecon`** | identical on all nine data-bearing fields |

`Haver.metadata` measured at **~170–500 ms**, better than the 560 ms budgeted, and cached
per process — a repeat tie resolves in 0 ms. Only slots that actually tie pay anything.

**A failure mode the plan had not accounted for:** `Haver.metadata` answers a failed query
with an **ErrorReport dict** rather than raising — the same shape behind the vague
`haver-data` error. A truthiness check sails past it, so the dict is rejected explicitly.
Unreachable metadata parks; it never degrades to a guess.

Each tied slot carries `slot["tie_metadata"]` — the full eighteen fields per candidate —
which is what the §16 picker will render as columns.

#### Side benefit: the stale end-date problem

`enddate` and `datetimemod` come from DLX itself, not the Neon catalog mirror, so this
same call answers the long-standing "the catalog's end date is stale" complaint — for the
park message today, and for the §16 picker later.

### §15.3 Phase 3 — the chat lane cannot remember (the ratchet) — DONE 2026-09-05

`seal_stores` makes the stores read-only from chat (§13.6), so every park resolved by eye
is forgotten at end of turn and tomorrow's commentary parks the same slots. Parks never
amortize. That, not the park rate itself, is what makes the lane feel worse over time.

**One collated store, referenced not copied.**

- **File:** `chat_learned.<operator>.json` in `CLARIFIED_KNOWLEDGE_DIR`, alongside the
  existing stores. **One file per operator** (decision D3), and within that operator
  **one** file for all chats and all days — the server is a long-running process with no
  concept of a chat session, so "a new chat" reads exactly the same file. It starts empty.
- **Operator identity comes from Entra** (decision D12: build this now, not later). Over
  HTTP the request is already authenticated, so the token subject names the file. On a
  local stdio install there is no identity and no second operator, so the suffix is a
  fixed `local`.
- **Read-through, not a copy.** Lookup consults the chat store, then falls through to the
  daily `learned` and `clarified` stores. Seeding by copy would freeze a snapshot and
  drift from the daily lane.
- **Writes are atomic** (temp file + replace) because two chats can be open at once.
- **Written on** explicit park resolution AND on correction of a wrong auto-bind
  (decision D5).
- **`forget_binding` server tool** to remove an entry (decision D4), so a wrong answer is
  revocable without hand-editing JSON on a share.

Promoting a chat entry into the daily store stays out of scope, gated behind G9d.

**Built as `haver_chart/chat_store.py`, and the location is the design.** Putting the
writer on the `resolve` module would have collided with `seal_stores`, which seals every
`save_*` there — so the chat lane owns its own module and its own file, and §13.6 is not
weakened to make room for it. `learned_descriptors.json` remains unwritable from chat;
`run_daily` has zero references to the chat store. The merge happens in one place,
`lane._resolve_kwargs`, as `learned={**R.load_learned(), **CHAT.load()}`.

**A remembered bind is a shortcut, not an exemption.** `resolve_slot` runs its
`double_transform_reason` guard over learned entries, so chat entries inherit the same
defense-in-depth as daily ones. `remember_binding` additionally DLX-confirms the code
before storing: without that, a hallucinated `LRTMANUA@USECN` would be written once and
then fail identically forever, with the failure disguised as a remembered decision. The
guard proves the series EXISTS — only the operator can say it is the right one, which is
what `forget_binding` is for.

**Provenance is surfaced.** `resolve_series` returns `from_chat_memory`, and the reason
line names the `forget_binding` call that would undo it. A remembered answer that looks
identical to a fresh catalog match gives the operator nothing to correct. `health` reports
the store path and entry count for the same reason.

**Identity trade (D12).** The filename uses a readable slug from `preferred_username`,
with the immutable `sub`/`oid` recorded inside the file. Changing your UPN therefore
starts a fresh file and re-asks some parks — recoverable — whereas a GUID filename would
make the store unauditable by the person who owns it. On stdio there is no token and no
second operator, so the slug is a fixed `local`.

Failure modes are all toward forgetting rather than toward a wrong chart: an unreadable
or half-written store reads as empty memory, and writes go through a temp file in the same
directory followed by `os.replace`, which is atomic on Windows and POSIX alike.

### §15.4 Build order

1. **§15.1a `transform_key`** — **DONE.** Had to precede any new raise, or the legend keys
   shift underneath it. 8 store entries re-filed, 0 labels lost.
2. **§15.1b parser + §15.1c fixture** — **DONE.** 55 cases; 17 flat phrases byte-identical,
   6 compound fixed against the ledger, 3 now raise.
3. **§15.1d window units** — **DONE.** Zero blast radius on history.
4. **§15.2 tie-break** — **DONE.** Mirrors bind `usecon`; different series park with the
   evidence attached.
5. **§15.3 chat store** — **DONE.** Makes the remaining parks amortize.
6. Run `fixtures/g19f_baseline_commentary.md` end to end and record park count and quality
   (G19f).

Ordering rationale: 1 unblocks 2; the migration in 1 must run BEFORE 2, because it
reconstructs the old key using the *current* phrase mapper. 4 shrinks the park set that 5
has to remember, and doing 5 first would build a memory of questions that should never
have been asked. The parked-slot widget (§16) stays after all of it.

### §15.5 Gates

| Gate | What it proves |
|---|---|
| G19a | **MET.** The 17 flat historical phrases map byte-identically before/after the parser; the 6 compound ones map to the formula the ledger shows shipped; the 3 formula-shaped ones raise instead of guessing. Pinned in `fixtures/transform_phrases.json` and asserted by `selftest.py`, so it stays proved rather than proved-once. *(Restated: as first written this gate required all 27 to be byte-identical, which is self-contradictory — six of them were the defect.)* |
| G19b | **MET.** A compound phrase the grammar cannot read RAISES and quotes the fragment; it never returns a partial transform |
| G19c | **MET.** `transform_key` derives from the slot formula; 142 ledger slots replayed with 0 labels lost and 0 swapped |
| G19d | **MET.** A true database mirror binds; genuinely different series still park, naming the differing fields |
| G19e | **MET.** Unreachable DLX metadata parks rather than guessing |
| G19f | **MET 2026-09-05.** Three runs of 18 slots: 2 bound -> 7 after D14 -> **13 bound, 5 parked** once six operator answers were remembered, with 4 of the 5 naming their evidence. **All 18 transform phrases mapped** at every run. **All 18 transform phrases mapped** — `movv(diff(X,1),3)` for the charts 5/8 compounds and `difa%(X,2)` for chart 4, none silently truncated. Its real value was catching the SA/NSA mirror defect above, which would have plotted NSA payrolls on chart 5. The 5 remaining unanswerable parks are all on the SIMILARITY path ("19 descriptor-similar candidates — human disambiguates"), which §15.2 never touched — see §15.7 |
| G19g | **MET.** `forget_binding` removes a chat-store entry and the next resolve re-asks |
| G19h | **MET.** A park resolved in one chat is not re-asked in the next, same operator; the chat entry overrides the daily entry it shadows. Proved end to end against live DLX: "Personal Saving Rate" parks, is remembered as `YPSVR@USECON`, and binds from memory in a fresh module state |

### §15.7 What G19f says is left (PROPOSAL)

Two things, neither of which §15 set out to fix, both now measured rather than guessed.

**The similarity path parks without evidence.** Five of the sixteen parks say only "19
descriptor-similar candidates — human disambiguates". The candidate list IS returned, so
the operator is not stuck, but the message names nothing about WHY the resolver could not
choose — it does not even say that no candidate matched exactly. §15.2 gave the
exact-tie path real evidence; the similarity path still has none. The same
`Haver.metadata` call would serve it.

**The bind rate is limited by the descriptors, and §15.3 is the remedy — measured.** A
commentary names series the way an economist says them; Haver spells them differently.
"Average Weekly Hours: Total Private" is `lrtpriva@usecon`, whose descriptor reads "Total
Private **Industries**"; "Aggregate Weekly Payrolls: Total Private" is `lypriva@labor`,
"Payrolls **of All Employees**: Total Private". Similarity 0.833 and 0.714 — right series,
under the gate.

**The gate was deliberately NOT loosened.** A threshold that accepts 0.833 is the one that
accepted `DFBACTS` for `DFBACTDS`, the wrong directional sibling, at 0.909. Answer once,
remember forever, is the correct remedy and it is what §15.3 is.

Measured over three runs of the same 18 slots:

| Run | Bound | Parked | What changed |
|---|---|---|---|
| 1 | 2 | 16 | §15.1 + §15.2 only |
| 2 | 7 | 11 | + D14, the SA default |
| 3 | 13 | 5 | + six operator answers, remembered (§15.3) |
| 4 | **18** | **0** | + the last five, found by searching Haver directly |

**The whole commentary now resolves with no parks at all.** The `Civilian Unemployment
Rate` row also exercised D5's second half: it auto-bound `a0m043@bci`, the Conference
Board copy, and the operator's `LR@USECON` correction overrides it.

The last five needed a ticker rather than a better threshold, and finding them took a
DIFFERENT query than the resolver's own. Two are worth recording because they are traps:

* **Fed funds target.** The `daily` copies cannot be plotted at all — `g4_lib.pull` maps
  M/Q/W/A and raises `KeyError` on `D`. The monthly copies live in `usecon`, and
  `ffedtar@usecon` was measured to BE the range midpoint (3.625 against 3.50–3.75), so no
  upper/lower arithmetic is needed. Operator chose `ffedtare@usecon`, the end-of-period
  copy.
* **Diffusion indexes** are a family keyed by SPAN — `zdla` (1-month), `zd3la`, `zd6la`,
  `zd12la`. The descriptor "One-Month Diffusion Index" names the span, not the frequency,
  and picking the wrong member gives a smoother line that still looks right.

**One bind worth a human look:** "Civilian Unemployment Rate" binds `a0m043@bci`, the
Conference Board's copy, rather than the BLS series most readers would assume. It is a
genuine exact match and correctly SA, so nothing is broken, but it is the kind of default
worth ratifying deliberately.

### §15.6 Process note — there are TWO test suites

`haver_chart/selftest.py` (50 checks, live DLX) and `scripts/selftest_workflow.py`
(254 checks, offline). Running only the first is how a comma-rule regression on
`"moving average, 6-month"` — a phrase asserted in the second — reached a green board
during this very section. Both must pass before any resolver change is claimed done. The
phrase cases from the workflow suite are now mirrored into
`fixtures/transform_phrases.json` so the two cannot drift silently.

## §16. Interactive widgets — parked-slot picker (PROPOSAL, blocked on G20b)

**Scope: the parked-slot picker only.** When a slot parks, render the candidates as a
selectable list instead of asking the operator to retype a ticker into the chat. Columns
come from `lane._candidates` plus `slot["tie_metadata"]` (§15.2): descriptor, database,
source, start date, **live** end date, observation count, frequency. A free-text field
covers the case where no candidate is right.

**Why it comes last.** Most of the parks it would display should not exist — §15.1 and
§15.2 remove the unwarranted ones and §15.3 stops the warranted ones from repeating. A
picker built first would have been a nicer way to answer a question that no longer gets
asked.

**Explicitly rejected: the chart-edit widget.** Re-rendering from a form duplicates the
whole chart spec in a second surface that must stay in sync with the renderer. Natural
language already edits a chart in one turn, and the render is the approval gate.

**Rejected: the series picker as a general pre-bind step.** Redundant with
`resolve_series` and its candidate list.

**Rejected: a preview tab inside the picker — DECIDED 2026-09-06.** A second tab plotting
the candidates together was considered and dropped. Mixed frequency and mixed start dates
are NOT the obstacle: each series draws at its native frequency on a shared axis spanning
the union of the ranges. The obstacle is **scale**. An index (2017=100) beside a level in
$bn leaves one line a flat worm, and the only fixes are twin axes — which works for
exactly two candidates — or rebasing, which erases the very difference the tab was opened
to show. The combined plot is therefore either unreadable or misleading.

Small multiples (a sparkline per row, each on its own axes) would dodge the scale problem,
and were **also dropped**. The strongest visual discriminator was SA vs NSA, and D14 now
decides that automatically, so the case most needing eyes is already settled by logic.
What remains — spotting a discontinued series — is already in the columns as live end date
and observation count. Against that, drawing N sparklines costs N DLX pulls at the moment
the operator is waiting, on the system whose failure mode is a hang.

**v1 is the table and a Submit button. Nothing else.**

**WORKING 2026-09-06, confirmed in Claude Desktop.** A separate `pick_series` tool rather than a
panel on `resolve_series`, and that follows from how the extension works: a UI attaches to
a TOOL, so whichever tool carries it renders a panel on EVERY call. On `resolve_series`
that is one panel per series — six charts, six panels, five with nothing to choose. A
separate tool means an auto-resolved series shows no panel at all, which is what was
asked for.

The risk that buys is the model forgetting to call it. G20a already answered that: an
`ACTION REQUIRED` line in the tool's own text arrives with every result and cannot be
skipped, which is what made chart links reliable.

Columns are DLX metadata, not resolver scores. Similarity and exact-token match are the
reasons the RESOLVER could not decide; showing them to an operator forwards the confusion
rather than settling it. Source, start, **live** end date, observation count and frequency
are what settle it by eye — and the live end date is the column that exposes a
discontinued series, which the catalog's stale copy hides.

Submit calls `remember_binding` from inside the view (`tools/call`, permitted by the host's
`serverTools` capability), then hands back to the conversation with `ui/message`. Recording
the bind from the view rather than asking the model to do it afterwards means the answer
cannot be lost to a model that forgets to follow up.

Locked by selftest §13 (fourteen checks). Every one of the four §16.3 rules fails SILENTLY,
so a test is the only thing stopping five rounds of measurement from being paid for twice.

**A working tool that nobody calls — the last defect, and the most instructive.** v1 shipped
correct and sat unused: the model parked, then asked for a ticker in prose. That was the
RIGHT behaviour for what it had been told, because `resolve_series`'s docstring predated the
picker and said to ask. Adding a tool does not tell anyone the tool is there, and a
description is read once per connection, so it could never reach a conversation already
under way. The instruction moved into the RESULT — `_park_text`, arriving with every call,
naming `pick_series` on a park and staying silent on a bind. Third time this project has
landed on the same conclusion (G20a chart links, the in-band render note, now this): on this
client, **guidance that is not in the tool result does not exist.**

### §16.1 What the library already provides — measured 2026-09-06

| Finding | Consequence |
|---|---|
| FastMCP 3.4.7 ships `fastmcp.apps` and `ui://` plumbing in the BASE package | a bare `ui://` resource registers with no new dependency |
| `AppConfig(resource_uri=..., visibility=[...])` on a tool is the wire format | tool→UI wiring is a decorator argument, not hand-rolled protocol |
| A built-in **`Choice`** provider exists — "LLM presents options, user clicks one, selection flows back as a message" | ~~that is the picker's exact shape~~ — **REJECTED 2026-09-06 after reading the source.** It renders a card with one button per option, and an option is a STRING. The picker's whole value is the columns beside each candidate (database, source, start, live end date, observation count, frequency); a row of buttons cannot carry them. It also returns the choice as a chat message rather than a tool call, so the model re-runs the resolve instead of the binding being recorded directly |
| `Choice` needs `prefab_ui`, behind the `fastmcp[apps]` extra, **not installed** in either venv | moot now that `Choice` is rejected. Hand-rolling adds NO dependency to the serving venv, and G20b has already produced a working reference view to copy |

The G20b probe is therefore built with **no new dependency**: `ui_probe` plus a static
`ui://haver-chart/g20b-probe.html`, registered under `HTTP_ENABLED` only so the stdio
teammate zips never see a fifth tool. Installing `fastmcp[apps]` to discover whether the
idea works would mutate the serving environment to answer a question a plain HTML string
answers. It is TEMPORARY and comes out once the gate reports.

| Gate | What it proves | State |
|---|---|---|
| G20a | Three charts in one turn produce three `chart_url`s in the visible reply (AVD) | **PASSED 2026-09-06.** Also locked by selftest §11 (6 checks) so it cannot regress silently |
| G20b | **STOP.** A hello-world `ui://` resource survives the tunnel and Entra. If MCP Apps do not work through this transport, §16 does not proceed | **PASSED 2026-09-06.** Panel painted in Claude Desktop through Cloudflare + Entra. Four defects had to be cleared first — see §16.3 |

### §16.3 What G20b cost, and the four things a view MUST do — measured 2026-09-06

The gate took five deploy-restart rounds, and not one of them was a wrong guess narrowing
down a list: each measured a different link and moved the failure one step later. Recorded
because the picker has to satisfy all four, and three of them fail SILENTLY — the client
reports success, or reports a fault that names the wrong layer.

| # | Defect | Symptom it produced | Fix |
|---|---|---|---|
| 1 | Client's cached tool manifest | new tools invisible after a full app relaunch | toggle the connector off/on. Relaunching is NOT enough (see `DEPLOY.md`) |
| 2 | Only the nested `_meta.ui.resourceUri` was emitted | extension negotiated, tool called, `resources/read` **never issued** | emit the deprecated flat `_meta["ui/resourceUri"]` alongside it. The spec keeps both until GA and the reference TS helper writes both |
| 3 | `app=True` on a resource emits `_meta.ui = true`, a BOOLEAN | "problem displaying content" **in a fresh chat with no tool call** — hosts prefetch UI resources on connect, so a malformed resource fails before anything is asked for | `app=AppConfig(...)`, which emits the UIResourceMeta OBJECT the spec types |
| 4 | The view was a static document | host reported "rendered an interactive widget"; operator saw nothing | a view is an MCP CLIENT: `ui/initialize` → `ui/notifications/initialized`, and `ui/notifications/size-changed` or the frame keeps zero height |

Defect 4 is the one to remember. **A view without script cannot render at all**, because
the handshake that earns it a height is written in script. `ui_probe_min` was built to test
whether a sandbox CSP blocks inline code and became the proof of the opposite: it stays
blank permanently, by construction.

What the passing handshake reported, which is what §16 gets to build on:

```
protocolVersion   2026-01-26
hostInfo          Claude 1.0.0
hostCapabilities  serverTools{listChanged}, serverResources, openLinks, downloadFile,
                  updateModelContext{text,image}, message{text}, sandbox, logging
hostContext       theme, locale, timeZone
notifications     tool-input-partial, tool-input, tool-result, host-context-changed
```

Two of those decide the picker's design. `tool-result` means the view RECEIVES the
candidate list, so the table needs no second fetch. `serverTools` means the view CAN CALL
`remember_binding` itself, so Submit records the bind directly instead of asking the model
to do it — which also means the bind cannot be lost to a model that forgets to follow up.

Diagnosis ran off `/g20b`, an unauthenticated route reporting whether the tool arrived and
whether the HTML was fetched. Worth keeping the habit: "the tool call never came" and "the
call came but the resource was never asked for" are indistinguishable from the chat window,
and each guess between them costs a restart at the console.

### §16.2 Defect found while running G20a — chat memory is exact-match

G20a incidentally exposed that the §15.3 ratchet misses on rewording. `_norm_key` only
lower-cases and collapses whitespace, so the lookup is otherwise an exact string match:

| descriptor | result |
|---|---|
| `civilian unemployment rate` | HIT |
| `Civilian Unemployment Rate` | HIT |
| `the civilian unemployment rate` | **MISS** |
| `civilian unemployment rate (SA)` | **MISS** |
| `US civilian unemployment rate` | **MISS** |

One leading article was enough to re-park a series the operator had already answered. This
matters more than it looks: the descriptor is GENERATED by the model from the commentary,
so its wording varies between runs, and the memory will miss far more often than the
stored-entry count suggests.

Fix is harder normalization — strip leading articles, trailing parentheticals and
punctuation. **Not** fuzzy matching: these are unreviewed personal answers, and a wrong
silent bind out of that store is precisely the failure this lane refuses to make.

### §16.4 The picker resumes the work — BUILT 2026-09-08

Saving a binding and then making the operator retype the request they had already made is
the kind of small indignity that stops a feature being used. The panel already handed
control back with "continue building the chart with it", but it did not know WHAT to
continue: `pick_series` received a descriptor, never the request. So "continue" was a hint
the model was free to interpret, and a paraphrase of the request is not the request.

`pick_series` now takes `original_request` (verbatim) and `remaining_parks`, and the panel
quotes the request back on submit. Three decisions shaped the rest, all of them about the
ways an automatic re-run misfires.

**D15 — with several parks, only the LAST panel resumes.** A three-park commentary opens
three panels. Each one resuming the request would render the chart three times, twice with
slots still unresolved, and the operator would be left deciding which of three charts was
the real one. Earlier panels instead say "N slot(s) still parked, resolve those first and
do not render yet". The count has to come from the model, since only it knows how many
slots the request produced.

**D16 — an old panel saves but does not re-run.** A rendered panel stays live HTML in
scroll-back indefinitely. Clicking submit on an hour-old one would re-run an hour-old
request as though it were current. Past 30 minutes the binding is still stored — that part
is always right — but the panel explicitly tells the model not to act, and tells the
operator to ask again. `issued` is stamped server-side in `lane.pick_series`; a panel is
not trusted to date itself.

**D17 — one action button plus an escape.** "Save & continue" and "None of these — stop".
The escape exists because there was previously no way to abandon a pick: the operator had
to close the panel and hope the model did not guess. It stores NOTHING, deliberately — a
shrug today must not become a remembered decision every future chat inherits.

Two failure modes got explicit guards rather than good intentions:

* **The loop.** If a save lands under a key the next lookup does not reach, the re-run
  parks the same descriptor, the panel reopens, and the operator is in a cycle with a
  widget in it. The resume instruction now carries its own circuit breaker: *if this
  descriptor parks AGAIN after the save, stop and say the save did not take — do not open
  the picker for it a second time.* `chat_store.remember` also returns the key it wrote,
  which the panel displays, because a save under an unreachable key is the one failure
  here that looks exactly like success.
* **DLX refusing a typed ticker** was already handled — `remember_binding` confirms before
  storing, and its refusal surfaces in the panel with the button re-enabled. No re-run
  fires on a failed save.

Locked by selftest §13 (fourteen new checks, 114/114 total), including that the
none-of-these path never reaches `remember_binding`.

### 16.5 Three defects the first live use of the picker exposed (2026-09-08)

The panel worked. Everything around it was wrong in ways only live use would show.

**The follow-up was posted into a void.** "Save & continue" saved the binding and did
nothing else. `tell()` sent `ui/message` with a request id but registered no pending
handler, so the host's response — *error included* — was discarded. The method name is
right (it is in the MCP Apps spec, and the sequence diagram shows the host answering it),
so the open question was whether this client honours it, and nothing in the protocol lets
us ask: `HostCapabilities` advertises `openLinks`, `serverTools`, `serverResources`,
`logging` and sandbox permissions, and has **no flag for `ui/message`**. Sending it and
reading the reply is the only available evidence.

So the panel now awaits it, and a refusal is stated in the panel — the binding is saved,
so the operator is one sentence away from finishing by hand, but only if they are told. It
also retries with `content` as an array after an object fails: the spec says object, hosts
differ, and this client already needed the deprecated flat `ui/resourceUri` key to render
at all, so it has form.

**A descriptor arrived HTML-escaped and was filed where nobody can look.** The pick came
in as `CPI-U: Commodities Less Food &amp; Energy Commodities` and stored under the key
`... food &amp; energy commodities`. Every later lookup spells that `&`, so the answer was
unreachable the instant it was given — the operator resolves the series and is asked again
forever. Precisely the D17 failure that "looks exactly like success", and it was the
key-display line added for D17 that made it visible.

Cleaned at the boundary, not in `_norm_key`. That function is frozen on purpose: every
text-keyed store on disk is filed under it, so relaxing it there would orphan legitimate
entries in order to fix corrupt ones. Cleaning the input instead fixes the key, the stored
copy, and the panel title, which was rendering the entity literally.

**D18 — the parked list orders by adjustment first, similarity second.** D14 ("seasonally
adjusted unless the read asks for raw") only ever fired on the auto-bind path. A parked
panel was pure similarity order, so it would offer five NSA copies of a series whose SA
copy sat below the cut, and the operator had to ask for SA by name — for momentum
commentary, the wrong default, since an NSA line answers a different question than the
words asked.

Two details carry it:

* **The pool is widened before re-ranking** (`3 x candidate_n`, floor 12). Re-ranking only
  the visible five cannot surface an SA copy that similarity ranked sixth, which was the
  entire defect. SA status lives nowhere but the descriptor's units parenthetical, so the
  pool must be fetched to be ordered.
* **Three tiers, not a filter:** target adjustment, then *untagged*, then the opposite. An
  untagged descriptor is unknown, not wrong, and a hard filter on SA would hide a series
  that simply carries no tag — hiding the only viable answer is worse than ranking it
  last. When no candidate matches, the panel says so rather than pretending, because that
  is also how a series with no SA copy legitimately looks.

Measured on the query that prompted this: `CPI-U: Commodities Less Food & Energy
Commodities` now holds 10 other-adjustment candidates below the cut, and the panel shows
an `Adj` column plus a note naming the reordering — an operator who cannot see that the
list was reordered has no way to know an NSA copy exists at all.

Locked by selftest §13 (eleven further checks, 125/125 total).

---

## 17. The data lane has no refuse-to-guess step — options (investigation, 2026-09-09)

**Not yet decided. No code written.** This section exists to be argued with.

### 17.1 The gap, stated precisely

Asked to "pull PPI for processed goods data from Haver", Claude searched the catalog via
`haver-metadata.search_series`, read the results, chose `PC1@USECON` on its own judgement,
and pulled it with `haver-data.get_observations`. The pick looks right. Nothing checked
that it was.

That is the whole of it: **resolution happens one step earlier than any of our guards, and
outside all of them.** §15 of this plan exists because a plausible wrong ticker is the
worst failure this work can produce, and the chart lane refuses to guess — it parks, and
the picker is what a park looks like. The data lane has no equivalent, because
`get_observations` takes a ticker that has *already* been chosen. There is nothing to park.

So the request "give the data lane a picker" is really "give the data lane a resolver".
The widget is the easy half.

Two smaller symptoms of the same root, both visible in the 2026-09-09 screenshot: nothing
applied D14/D18's SA preference, and the printed table labelled its index column SA and
its year-over-year column NSA off what appears to be a single SA series. The second is
unconfirmed — the table was cut off — but it is the kind of thing an unguarded path
produces and a guarded one does not.

### 17.2 Three facts that constrain every option

**The lanes are on different machines, and not by accident.** `haver-metadata` is a Linux
DigitalOcean droplet behind Entra OAuth, shared by teammates, and it *never imports
Haver* — it reads a Neon mirror of the catalog. `haver-data` and `haver-chart` are on the
Windows AVD because DLX only works next to a signed-in interactive session. Resolution
today happens on the droplet; verification can only happen on the AVD.

**Everything a picker needs is ALREADY co-located on the AVD.** The chart lane holds all
three ingredients in one process: catalog search (`haver_search`, against the same Neon
mirror), DLX confirmation, and the per-operator chat store. This is the fact that makes
the cheap options cheap and is easy to miss from the outside.

**The catalog knows about seasonal adjustment; DLX does not.** `sa_status` is a
first-class indexed column on the `series` table, and `search_series` *already accepts it
as a filter* — it is simply optional, and the model does not pass it. This is the exact
inverse of the chart lane, where `Haver.metadata` has no SA field and D18 had to parse the
descriptor's units parenthetical. On the droplet, SA-first ordering is close to free.

### 17.3 The options

**A — Delegate: route descriptions through the chart lane's resolver.** The model already
has `haver-chart.resolve_series` available; it is read-only, standalone, and every
argument but the descriptor has a default. It runs the full resolver — catalog search,
exact-token relevance gate, SA and aggregation cross-checks, DLX confirmation — parks when
the evidence is thin, and the picker fires on a park. Its answer is a confirmed
`code@database`, which is precisely `get_observations`' input.

*Cost:* close to zero. The instruction lives in `haver_data/SKILL.md`. Nothing is ported,
nothing is duplicated, and bindings land in the ONE store that already exists, so an
answer given in a data pull is honoured by the chart lane and vice versa.

*Weaknesses, and they are real:* it requires the `haver-chart` connector to be enabled
whenever the data lane is used, which couples two connectors that are currently
independent. `resolve_series`' docstring is written for chart replication ("pass what is
PRINTED ON THE CHART") and would mislead the model on an ad-hoc pull, so the wording needs
widening. And it does nothing for a teammate using the metadata connector on its own.

**B — SA-first and near-tie honesty in `search_series`.** Order results by `sa_status`
against an inferred preference and tell the model when the top candidates are
near-indistinguishable. Small, contained, pure SQL plus ranking on the droplet, and it
helps *everyone* including teammates who will never touch the AVD.

*Weakness:* it improves the odds without adding a refusal. The model may still pick. It
narrows the gap rather than closing it, and should not be mistaken for closing it.

**C — A real picker on the data lane.** Port the resolver so the AVD data lane can park.

*Cost, honestly:* `resolve.py` is ~1,300 lines and lives in a *different repository*
(`2026_daily_chart_replicator/src`) from the data lane (`2026_haver_mcp`), with separate
venvs and separate deploys. Sharing it means a packaging decision; copying it means two
copies of the most safety-critical file we have, drifting. It also duplicates the picker
widget and raises the chat-store question below.

*When it would be worth it:* if the data lane must work with the chart connector disabled.

**Rejected: a picker on the metadata droplet.** It is where resolution happens today, so
it looks like the obvious home, and it is the wrong one. The droplet has no DLX, so it
cannot confirm a ticker before storing it — the guard that makes `remember_binding` safe.
Worse, its bindings would live on the droplet while the chart lane's live on the AVD:
**two memories that cannot see each other and will disagree.** One store that is sometimes
absent beats two stores that quietly differ.

### 17.4 Recommendation, and what was built (2026-09-09)

**A first, then B.** A closes the gap with no new code and no second store; B raises the
floor for the shared connector and is independently useful. Hold C until A has been lived
with — the coupling it introduces is the thing most likely to annoy in practice, and that
is cheaper to discover than to design around.

**Built: A, with a gate rather than only an instruction.** `get_observations` gained a
required `ticker_source` — `exact`, `resolver` or `search` — carried as a schema enum so
the three options reach the model without relying on prose being read.
`haver_data/provenance.py` refuses `search`, and the refusal names the way forward
(`resolve_series`, then `pick_series` on a park) instead of merely saying no. `exact`
survives because "pull `LANAGRA@USECON`" is a legitimate request that needs no resolver.

`search` is deliberately IN the vocabulary so that it can be refused. Leaving it out would
push a model that had searched toward the nearest allowed label, converting a refusal we
can act on into a mislabelled pull we cannot see.

Every payload also carries `ticker_provenance`, in the result rather than the logs. The
gate is declarative and therefore has a door in it: a model that searched and reports
`exact` is not caught. Rather than claim a wall, the payload states in words that the
operator named the ticker — a claim the operator can read and contradict.

Also widened `resolve_series`' docstring, which said "pass what is PRINTED ON THE CHART"
and would have steered the model wrong on an ad-hoc pull, and rewrote
`haver_data/SKILL.md` step 1 — it had told the model to "call `search_series` first",
which is to say the bypass was the documented workflow.

Locked by `tests/test_haver_data.py` (seven new cases, 52 in the file, 186 in the repo)
and the chart lane's 125/125.

**Decided against: a chart/table widget in the data lane.** The chart Claude drew for the
2026-09-09 PPI pull plots a year-over-year transform *it computed*; the tool returned a
raw index at 1982=100. A widget can only draw its own tool's result, so ours would have
shown a different and less useful chart, and matching it would mean putting transforms in
the data lane — which is `render_chart`'s job, with house styling, ratified legends and a
non-suppressible transform label. Multi-series compounds it: `get_observations` is one
ticker per call and a widget binds to one result, so three series is three stacked
widgets, and a batch tool would then have to rule on mixed units, mixed frequencies,
ragged ranges and 10,000-point tables. Claude's rendering is free and handles the
transform case; the observed failures were wrong tickers and mislabelled adjustment, which
no widget fixes.

### 17.5 Open questions before any of this is built

* ~~Should a data-lane pull be *allowed* to proceed on an unresolved descriptor?~~
  **ANSWERED 2026-09-09 — no. It must refuse and show the picker, as the chart lane does.**
  The precedent turns out to be firmer than an instruction: `render_chart` does not merely
  ask for resolved slots, `build_chart.render_row` *raises* `render needs every slot
  resolved`. The chart lane's guarantee does not depend on the model's cooperation, so the
  data lane's should not either.

  One difference has to shape the design. The chart lane has ONE entry mode — a
  description off a chart, always needing resolution. The data lane has **two**: "pull
  `LANAGRA@USECON`" is exact and unambiguous and needs no resolver, while "pull PPI for
  processed goods" is a description and does. A blanket refusal would break the first, so
  the refusal attaches to the DESCRIPTION path, not to `get_observations` as such.

  The honest limit: full enforcement is not reachable from the AVD alone. A model can
  always turn words into a ticker itself via `search_series` on the droplet, and that
  connector is shared with teammates, so it cannot be constrained for one operator's
  benefit. The bypass can be made visible — by stamping each pull with whether its ticker
  came through a confirmed binding — but it cannot be made impossible.
* Should bindings learned in a data pull be remembered at all, or only for the session? A
  remembered answer is inherited by every future chart, which is a strong commitment to
  make from a throwaway query.
* Does the teammate-facing metadata connector need any of this, or is it acceptable that
  guards exist only on the operator's own host?

---

## 18. The 2026-09-09 outage: a reboot the supervision cannot survive

Three lanes went to 502 at 03:37 and stayed down until 07:18 — **3 hours 48 minutes**,
recovered only because a human signed in. Detection worked perfectly. Recovery does not
exist. Evidence from `scripts/avd_reboot_forensics.ps1`, run on the host afterwards.

### 18.1 It was not our nightly, and not a weekly schedule

Windows Update, and it rebooted the box **twice**:

| Time (ET) | Event |
|---|---|
| 03:00:02 | Update client starts .NET Security Update KB5126052 |
| 03:01:29 | Security Update KB5124008 |
| 03:30:01 | **`McpLaneNightly` runs** — our 03:30 restart, result 0x0 |
| 03:30:24 | supervisor: `macrobond-data RESTARTED (killed 2)` |
| 03:30:30 | supervisor: `cloudflared SERVICE RESTARTED` — nightly finishes |
| **03:30:34** | **Event 1074: `MoUsoCoreWorker.exe` restarts the computer on behalf of SYSTEM** |
| 03:32:56 | first boot |
| **03:35:28** | **Event 1074: `TrustedInstaller.exe` restarts it again** |
| 03:35:43 | final boot |
| 03:36:57 | KB5124008 and KB5126052 report success |
| 03:37 | health watch: three lanes `OK -> FAIL`, 502 |
| **03:30:30 → 07:18:03** | **`supervisor.log` is EMPTY. The five-minute task never ran.** |
| 07:17 | `madz` signs in over RDP |
| 07:18:03 | supervisor starts all three lanes |
| 07:29:22 | operator opens DLX by hand |
| 07:37 | health watch: three lanes `FAIL -> OK` |

The nightly finished **four seconds** before the reboot signal. Nothing about that is
robust; it is luck. Had the update landed thirty seconds earlier it would have killed the
supervisor mid-restart.

### 18.2 The actual defect: an interactive task cannot run with nobody logged in

`McpLaneEnsure` and `McpLaneNightly` are registered as `madz` with
**`LogonType = Interactive`**. Such a task is *skipped* when the user is not signed in. A
reboot leaves exactly that state, so the five-minute ensure-up — the mechanism whose whole
job is to notice a dead lane and start it — did not fire once in nearly four hours. The
empty `supervisor.log` is the proof, and the 07:18:03 entry one minute after the 07:17
logon shows it worked the instant it was allowed to.

This is not a bug in the supervisor. It follows from the deliberate choice to run lanes in
an interactive session because DLX refuses non-interactive accounts. What was never
designed is **restoring the session**. The architecture quietly assumed the session
persists, and a patch reboot is the ordinary event that breaks the assumption.

### 18.3 What did work, and is worth keeping

* **`cloudflared` as a service** came back by itself (`Auto`, `LocalSystem`). That is why
  the symptom was a clean 502 rather than a DNS or connection failure — the tunnel was up
  and the origin was dead, which is precisely the distinction the watchdog needs. The
  2026-09-08 migration paid for itself in under 24 hours.
* **`McpHealthWatch` runs as SYSTEM** (`LogonType = ServiceAccount`), so unlike the other
  two it *did* run throughout, and alerted in seven minutes.
* **Ensure self-heals on logon** — one minute, unprompted.

So of the three supervision tasks, the only one that survived the outage is the only one
not tied to the interactive session. That is the whole lesson in one line.

### 18.4 Two anomalies, not yet explained

**A successful pull at 07:22 predates the DLX window opening at 07:29.** `last_pull` for
`haver-data` is `11:22:08Z`, and `last_pull` is stamped on success only. There is no DLX
*service* on the host, so either the data path does not need the desktop window after all,
or a DLX process existed earlier and was replaced. If the window is unnecessary, an
unattended recovery is materially simpler. Worth one controlled test rather than an
assumption in either direction.

**The forensics script initially reported "no health watch state"** because it looked for
`health_state.json` when the files are `mcp_health_state.json` and `mcp_health.jsonl`. A
diagnostic that says "nothing here" about files sitting beside it is worse than no
diagnostic. Fixed, and recorded because the same class of error would be invisible in a
real incident.

### 18.5 Options

* **Restore the session automatically** (auto-logon, ideally via the LSA-secret route
  rather than a plaintext registry password), then let `Ensure` do what it already does
  correctly. This is the only option that closes the gap; it needs a decision about
  storing a credential, and AVD policy may forbid it.
* **Tell the operator precisely what is wrong.** The health watch cannot fix a reboot, but
  it can recognise one: recent `LastBootUpTime` plus no interactive session is a distinct
  state from "a lane crashed", and deserves a distinct alert — *"the AVD rebooted at
  03:35 and nobody is signed in; the lanes cannot start until someone connects"*. Cheap,
  safe, and turns a confusing 502 into an instruction. Worth doing whether or not
  auto-logon happens.
* **Make the nightly aware of a pending reboot**, so it does not do work that is about to
  be discarded — and, more importantly, is not interrupted halfway. *Resolved by moving
  the slot instead; see §18.9.*

### 18.6 Built: the alert now names the one thing waiting cannot fix

`Get-HostContext` reads boot time and interactive presence; `Get-OutageDiagnosis` turns
the pair into one sentence, and is pure so the wording is testable — text first exercised
during an incident is a liability at the moment it matters most. When lanes fail and no
session exists, the alert leads with **ACTION NEEDED** and says to connect over RDP.

Two details that are easy to get backwards:

* **With a session present the diagnosis says nothing.** `Ensure` will fix it inside five
  minutes, and commentary on a self-healing event is how alerts get ignored.
* **With no session the restart request is SKIPPED**, not written. `McpLaneEnsure` is its
  only reader and cannot run; the file would age out of its 30-minute window unread, and
  the cooldown it stamps would suppress a *real* request later.

Interactive presence comes from `explorer.exe` owners rather than `query user`, whose
output is localized and whose exit code is non-zero precisely when the answer is "nobody"
— a diagnostic that fails when it has something to say is not a diagnostic. `-ShowContext`
proves the reading, where `-SelfTest` proves the wording; on the live host it reports boot
`03:35:43`, `madz` present, diagnosis silent. 18/18.

**Auto-logon is viable here, and two of the three blockers I reported were my own bugs.**
Measured on the host: domain-joined to `renmac.local` (so the domain argument is the
NetBIOS name, not an Entra form); **no logon banner** — the first check called one "set"
because a one-space `LegalNoticeText` passed a truthiness test; **no MFA credential
provider** — of nine flagged, eight are Microsoft's own and the only third-party one,
ScreenConnect, is disabled. No FSLogix container to contend with. Two real items remain:
`AutoLogonCount = 97` is stale and gives auto-logon an expiry date, and `madz`'s Startup
folder is empty, so **DLX would not return even with the session restored** — which is
what makes the §18.4 anomaly worth settling before adding a startup shortcut for a window
that may not be needed.

The credential itself is deliberately not automated. Sysinternals `Autologon.exe` keeps it
as an LSA secret rather than the plaintext `DefaultPassword` registry value, and it should
be typed into that dialog at the console by the operator, never passed on a command line
where it lands in process listings and shell history.

### 18.7 Settled: DLX does not need its desktop window — and did not fix this morning

Measured, not inferred. A fresh pull as `madz` returned `LR@USECON`, 8 observations to
2026-08-31, **with no DLX process running on the host at all** — the window opened at
07:29 (pid 10604) had already exited. The Haver package reaches the data without a GUI or
any resident process.

Two consequences, one of them a correction:

* **No Startup shortcut is needed.** Restoring the session is the whole of unattended
  recovery, which makes auto-logon sufficient on its own.
* **Opening DLX is not what brought the lanes back.** Signing in at 07:17 is. `Ensure`
  started all three lanes at 07:18:03, and `haver-data` completed a pull at 07:22 —
  both *before* the window was opened at 07:29. The natural reading of the morning
  ("I opened DLX and it started working") credits the wrong action, and acting on it at
  the next outage would mean opening DLX and waiting instead of just signing in.

Getting here needed a detour worth recording. The probe was first run over SSH and it did
not fail, it **wedged for five minutes**: SSH lands as `mcpdeploy`, DLX refuses that
account by raising a GUI login modal, and in session 0 that modal has nowhere to appear,
so the process blocks rather than erroring. Any DLX test run over SSH measures the wrong
account and hangs while doing it. `run_as_madz.ps1` runs the probe through a scheduled
task with `LogonType = Interactive` — the same mechanism `McpLaneEnsure` uses — with a
timeout, a kill, and an unregister in `finally`, because a leftover task that fires a
probe on some future trigger is a booby trap.

Three of the diagnostics written today were themselves broken by the same StrictMode trap
in two forms: `.Count` on a pipeline that yields a single object, and `.Property` on an
object that lacks it. Both throw only in the case the code was written to detect. A
diagnostic is code that runs once, in an emergency, and these ones failed exactly then.

### 18.8 Proven: 3h48m and a human, down to 64 seconds and nobody

Auto-logon configured via the LSA secret, then a deliberate reboot at 08:48:35 with an
explicit instruction not to reconnect — an RDP sign-in would have reproduced the morning's
recovery and made a failure look like a pass.

| | 2026-09-09 03:35 (patch reboot) | 2026-09-09 08:49 (test) |
|---|---|---|
| Session | never returned | `madz`, **console session 1**, 08:49 |
| Lanes up | 07:18:03, after a human signed in | **08:49:56 – 08:50:07** |
| Recovery | **3h48m**, required RDP | **~64 seconds**, nobody touched it |

`query user` reports `console`, not `rdp-tcp` — the distinction that makes this a result
rather than an anecdote. Both lanes answered `HTTP 200` through the tunnel, and a pull
issued into that never-connected session returned `LR@USECON`, 8 observations to
2026-08-31, with no DLX process anywhere. Every store survived: `chat_learned.asingh.json`
still 5,570 bytes at its 07:32 write. The empty store in the `/health` payload is an
artifact of probing anonymously — stores are per-operator and the unauthenticated caller
is `local`.

**The monitoring loop watching the reboot was itself broken, and this is the lesson worth
keeping.** It reported "host unreachable (rebooting)" every 18 seconds for fifteen
minutes. The host was back at 08:49. The probe sent `.ToString(\"HH:mm:ss\")` through
bash into PowerShell, where `\"` is not an escape — PowerShell escapes with a backtick —
so the string terminated early and the command was a syntax error. The loop read empty
output as a dead host.

That is the fourth instance today of one failure mode: **a diagnostic that cannot tell
"my probe is broken" from "the thing I am measuring is broken", and defaults to the more
alarming reading.** It cost fifteen minutes of believing a successful test had failed. The
fix in every case is the same — make the probe prove it ran, rather than inferring from
silence. `curl` against the public endpoints, which needed no quoting at all, gave the
right answer in one call.

### 18.9 The nightly moves to 02:30, chosen against the patch schedule

The reboot is not weekly and not arbitrary. It is **monthly, the Wednesday after Patch
Tuesday**, and it is pinned to just after 03:00 by policy:

| Date | First reboot | Second reboot |
|---|---|---|
| Wed 2026-07-15 | 03:27:21 `MoUsoCoreWorker` | 03:31:12 `TrustedInstaller` |
| Wed 2026-08-12 | 03:29:26 | 03:33:31 |
| Wed 2026-09-09 | 03:30:34 | 03:35:28 |

`ActiveHoursStart = 18`, `ActiveHoursEnd = 3`. Windows is forbidden from auto-restarting
between 18:00 and 03:00 and fires the moment that protection expires, which is why all six
reboots cluster in the minutes after 03:27. Note there are always **two**, four minutes
apart — a slot that survives the first still has to survive the second.

The instinct to move the nightly earlier was right; **03:15 was the worst available slot**,
sitting in the unprotected gap between the policy lapsing and the reboot arriving. That
converts a restart that was merely discarded into one interrupted halfway. 02:30 sits
inside active hours, where a reboot is prevented by policy rather than by luck, with about
an hour of clearance from the earliest reboot on record.

Retimed in place rather than re-registered, because re-creating a task is how an
`Interactive` principal quietly becomes something else — verified afterwards as still
`madz / Interactive / Highest`, which is what lets it reach DLX at all. The installer
default and `SERVER_SETUP.md` were changed too, so a redeploy cannot silently restore
03:30.
