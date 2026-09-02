# Handoff — haver-chart

Read this file first if you are an agent (or a person) landing from another project.
It is the full picture of **what this is, how it is set up, and how to use it**.
Deeper files are linked at the end; do not start there.

**Repo:** `2026_daily_chart_replicator` (this package lives in `haver_chart/`).
**Sibling:** `2026_haver_mcp` — catalog search (`haver-metadata`) and number pulls
(`haver-data`). This lane **draws**. It does not replace those two.

---

## 1. What it is

A FastMCP server with two tools:

| Tool | Job |
|---|---|
| `resolve_series` | Bind one printed Haver series (or formula) to a confirmed `code@database`. Same resolver as the daily email pipeline. |
| `render_chart` | Draw the resolved series in RenMac house style. Returns an inline PNG. Over HTTP it also returns `chart_id` + `chart_url`. |

**What it is for**

- Rebuild a Haver screenshot in house style.
- Iterate in chat: title, legend, axis, transform, recessions, bars vs lines.
- Optional: suggest and **build** charts for a finished commentary (see §6).

**What it is not**

- Not the daily Teams/email pipeline (`scripts/run_daily.py`). That chain is untouched.
- Not a number pull. Use `haver-data` / `get_observations` to quote or arithmetic.
- Not a catalog. Use `haver-metadata` / `search_series` to *find* a ticker by words
  when you are not rebuilding a screenshot (resolve still searches; metadata is
  better for open discovery).
- It **never writes** the firm’s learning stores (`learned_descriptors.json`,
  `legend_labels.json`, …). Chat binds are not ratified for tomorrow’s daily.

**Two transports, one codebase**

| | Local stdio | Remote HTTP |
|---|---|---|
| Flag | `HAVER_CHART_HTTP` unset (default) | `HAVER_CHART_HTTP=1` |
| Who | A laptop with DLX + Python | Anyone who can complete Entra sign-in |
| Client | Claude Desktop developer plugin | Custom connector (Desktop, claude.ai, phone) |
| Result | `path` on that machine | `chart_id` + `chart_url` + inline image |
| Live URL | — | `https://chart.hvr-mcp.work/mcp` |

Stdio stays the default so teammate zips do not change.

---

## 2. How it is wired (plain)

Claude cannot see Haver. Haver/DLX lives on a Windows machine.

**Stdio:** Claude Desktop starts `haver_chart/server.py` as a child process. Tools
talk JSON-RPC on stdin/stdout. DLX is *that* laptop’s licence.

**HTTP (what most teammates will use):** The same `server.py` binds
`127.0.0.1:8100`. `cloudflared` publishes `chart.hvr-mcp.work`. Entra (OAuth via
FastMCP `AzureProvider`) checks the caller. The process pulls DLX and draws on
**the host** (today an Azure AVD; destination is a dedicated Windows VM).

Rendered files:

```
<repo>/outputs/chat/<YYYY-MM-DD>/<stem>-<uuid32>.png
```

`GET /chart/<chart_id>` serves that file. **Cloudflare does not store the PNG.**
It is a tunnel. Retention: 14 days (`CHAT_RETENTION_DAYS`). The GET route is
unauthenticated on purpose (a browser link carries no bearer token); the id is a
full uuid4.

Renders in one process are **serialized** (one lock around DLX + matplotlib).
Ten overlapping renders queue; they should not crash. A second process
(`haver-data`) can enter DLX at the same time — measured OK on the AVD for 10+10.

---

## 3. Tool contract (do not invent tickers)

### `resolve_series`

Pass what is **printed on the chart**, not a paraphrase.

| Arg | When |
|---|---|
| `base_descriptor` | Series name as printed |
| `formula` | If the chart prints `zs(yryr%(IP))`, pass that **verbatim** |
| `applied_transform` | Words-only charts (“Z-Score”, “% Change - Year to Year”). **Not** “Avg, % p.a.” — that is storage, not a transform |
| `sa_hint` | `sa` / `nsa` when the chart says so (hard cross-check) |
| `lag` | `[-n]` lag, `[+n]` lead |
| `plot_kind` | `line` / `bar` / `stacked_bar` only. “Column” → `bar` |
| `axis` | `shared` / `L` / `R` |

**Park is normal.** `status: "parked"` → show `candidates` (similarity +
`exact_token_match`) and **ask**. Never bind the top hit on similarity
(`DFBACTS` vs `DFBACTDS` scored 0.909 on the wrong sibling).

Return includes `slot` — pass that object into `render_chart`’s `series` list.

### `render_chart`

Needs every slot resolved. Each series needs `proposed_legend` (human text, not a
raw ticker). Then: `title`, `subtitle`, `st_force`, `no_title`, `sample_start`,
`axis_mode` (`shared` / `dual`), `recession_shading`, axis min/max, `x_label_fmt`
(`auto` / `year` / `month` / `quarter`), `x_tick_years`, `end_series`, `filename`.

There is **no edit API**. Every change is a full new render. Claude resends the
last spec with one field changed.

Guardrails raise on purpose: unmapped transform, unresolved slot, duplicate or
raw-mnemonic legend, daily frequency (`KeyError: 'D'`), `NeedPin` on INDEX.

---

## 4. Setup

### A. Use the live connector (usual)

Claude Desktop or claude.ai → Settings → Connectors → Add custom connector:

- Name: `haver-chart`
- URL: `https://chart.hvr-mcp.work/mcp`

Microsoft sign-in. Assignment is on the **enterprise application** (same app as
haver-data), not App registrations.

Health (no auth): `https://chart.hvr-mcp.work/health`  
File: `https://chart.hvr-mcp.work/chart/<chart_id>`

### B. Local stdio (developer / own DLX)

From this repo checkout: `haver_chart/SETUP.md`.
Teammate zip (no git clone): `haver_chart/TEAMMATE_SETUP.md` (ships as `README_FIRST.md`).

Pre-flight: `python haver_chart/selftest.py` — expect **41/41** (root README may
still say 37).

### C. New Windows host (AVD or dedicated VM)

`haver_chart/SERVER_SETUP.md`.

- Source clone: `config/.env` at **repo root** (`2026_daily_chart_replicator/config/.env`).
- Zip layout: `haver-chart/repo/config/.env`.
- Variables: `HAVER_CHART_HTTP=1`, port `8100`, `HAVER_CHART_PUBLIC_URL`,
  `HAVER_CHART_JWT_SIGNING_KEY`, `HAVER_CHART_AZURE_*`, plus
  `NEON_READONLY_DATABASE_URL` for catalog search.
- Knowledge JSON: local copy (not `P:`). A **source clone has no `vendor/`** —
  set `ECON_TEMPLATES_CHARTS` and `HAVER_MCP_SERVER` or copy those dirs.
- Secrets are gitignored. Clone ≠ runnable HTTP until `.env` + DLX + tunnel exist.

Do not open ports 8100/8101 on the NSG. Do not create a second Cloudflare tunnel
if one already serves `chart.hvr-mcp.work`.

### Live AVD paths (recorded 2026-08-30)

Same Azure Windows host, user `madz`. `config\.env` sits one level above each
package. `cloudflared` is a foreground process:
`cloudflared tunnel run haver-chart-avd`.

| Lane | Package directory on the AVD | Server script | Port | Health |
|---|---|---|---|---|
| haver-chart | `C:\Users\madz\Work\asingh\haver-chart\repo\haver_chart` | `server.py` | 8100 | `https://chart.hvr-mcp.work/health` |
| haver-data | `C:\Users\madz\Work\asingh\2026_haver_mcp\haver_data` | `server.py` | 8101 | `https://data.hvr-mcp.work/health` |
| macrobond-data | `C:\Users\madz\Work\asingh\2026_macrobond_mcp\macrobond_data` | `server.py` | 8102 | `https://data.mbond-mcp.work/health` |

---

## 5. Use cases and example prompts

**Rebuild a screenshot**

> [paste Haver image] Rebuild this in RenMac style. The formula is
> `zs(yryr%(IP))`; the other line is the Philly Fed manufacturing current-activity
> diffusion index, also z-scored. Shared axis, recession bands.

**Iterate** (full list: `SETUP.md` Example C)

> Title it “Factory output is lagging the survey”.
> Change the second legend to “Philly Fed current activity”.
> Put the Philly Fed line on the right axis.
> Make it year-over-year percent change instead of the level.
> Add recession bands. / Lag the survey by three months.
> Make the second series bars. Start it at 2010. Label the x-axis by year.

**Check the reconstruction**

> The source chart’s last reading for the survey is about 2.4. Does yours match?

**File for a newsletter**

> Give me the link to this chart.

**Commentary → charts** (not in the skill yet; works as a one-off)

> [paste commentary] Suggest 3–4 charts (type, series, transform, title, which
> paragraph). Then confirm tickers and build them. Do not invent a mnemonic.
> Parks: ask me. Give `chart_url` for each.

Habits: paste formulas as printed; full series names; answer parks; look at the
image before accepting.

---

## 6. Files that matter

| Path | Role |
|---|---|
| `haver_chart/server.py` | FastMCP tools + `/health` + `/chart/<id>` |
| `haver_chart/lane.py` | Translation to `resolve` / `build_chart.render_row`; locks; `chart_path` |
| `haver_chart/bootstrap.py` | `sys.path`, `.env`, vendored paths, store seal |
| `haver_chart/selftest.py` | Pre-flight |
| `src/resolve.py`, `scripts/build_chart.py` | Shared daily-lane code — do not fork for chat |
| `plan.md` §13 | Chat-lane design |
| `plan.md` §14 | Remote HTTP / AVD / gates |

Learning stores: read-only from this process (`seal_stores`).

---

## 7. Invariants (do not “simplify”)

- Never guess a ticker; always `resolve_series`.
- Parks wait for a human.
- Learning stores sealed.
- Daily `run_daily` chain unchanged; chat writes only `outputs/chat/`.
- Stdio remains default.
- `mask_error_details` is True over HTTP; intentional `ToolError`s still pass through.
- One Entra app can front chart + data (two redirect URIs). Assignment is per app.

---

## 8. Where to go next

| Need | File |
|---|---|
| Worked rebuild + every post-render sentence | `SETUP.md` |
| Laptop zip / `configure.py` | `TEAMMATE_SETUP.md` |
| HTTP host, tunnel, Entra, `.env` | `SERVER_SETUP.md` |
| Short tool habits for Claude | `SKILL.md`, `SYSTEM_PROMPT.md` |
| Design / gates / AVD findings | `plan.md` §13–§14 |
| Number pulls | `2026_haver_mcp/haver_data/HANDOFF.md` |
| Catalog only (Linux droplet) | `2026_haver_mcp/README.md` → `https://hvr-mcp.work/mcp` |
