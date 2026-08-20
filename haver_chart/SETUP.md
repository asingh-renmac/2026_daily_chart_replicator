# Wiring `haver-chart` into Claude Desktop

The chat lane (plan.md §13): paste a Haver screenshot into Claude Desktop and get a
RenMac render back, iterating in the conversation until it is right.

This is a **single-machine setup** (Aman's). Unlike `haver-data`, it is not packaged
for distribution — it runs from this repo checkout and needs matplotlib, pandas, the
RenMac style module and this repo's `src/`. Teammate distribution is G9e.

---

## Step 1 — Confirm the interpreter has everything

The shared venv already carries matplotlib, pandas, Haver **and** fastmcp. Verify
rather than assume:

```bash
C:/Users/asingh/envs/shared-3.10/Scripts/python.exe -c "import fastmcp, matplotlib, pandas, Haver; print('ok', fastmcp.__version__)"
```

Expected: `ok 3.4.7` (any 3.x is fine). If `Haver` fails, DLX is not installed for this
interpreter and nothing downstream will work.

## Step 2 — Run the pre-flight selftest

Do this **before** touching any Claude config, so that if something goes wrong later you
know it is the config and not the server:

```bash
cd C:/Users/asingh/new_work/2026_daily_chart_replicator
C:/Users/asingh/envs/shared-3.10/Scripts/python.exe haver_chart/selftest.py
```

Expected: `28/28 checks passed`. It checks the import boundary (§13.5), that the
learning stores are sealed read-only (§13.6), that a live resolve binds
`bocgx@surveys`, that a render lands in `outputs/chat/`, and that the fail-loud
guardrails still raise.

The first pull of the day may prompt for a **DLX login**. That is normal.

## Step 3 — Add the entry to the config file

Your Claude Desktop is the **MSIX build**, so Settings → Developer → Edit Config opens:

```
%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json
```

It already contains a `haver-data` entry. Add `haver-chart` **alongside** it — do not
replace the block, and remember the comma after the previous entry:

```json
  "mcpServers": {
    "haver-data": {
      "command": "C:/Users/asingh/envs/shared-3.10/Scripts/python.exe",
      "args": ["C:/Users/asingh/new_work/2026_haver_mcp/haver_data/server.py"]
    },
    "haver-chart": {
      "command": "C:/Users/asingh/envs/shared-3.10/Scripts/python.exe",
      "args": ["C:/Users/asingh/new_work/2026_daily_chart_replicator/haver_chart/server.py"]
    }
  },
```

The full file (with the rest of your existing settings left untouched) is in
`claude_desktop_config.example.json` next to this file.

Two things break this, both silently: **backslashes** — use forward slashes, a single
`\` invalidates the JSON and Claude ignores the whole file with no error — and a
**missing or extra comma**.

## Step 4 — Quit Claude Desktop **from the tray icon**, then reopen

**This is the step that most often goes wrong, and it fails silently.** Claude only
reads the config at startup. Closing the window does not stop the program: relaunching
while it is still running just hands off to the old copy, which is still using the old
config. Nothing appears to happen and there is no error. That exact mistake cost an
hour on the Haver MCP.

Closing the window is not enough. **Right-click the Claude icon in the system tray**
(bottom-right, possibly under the `^` arrow) **and choose Quit.** Then confirm:

```powershell
tasklist /FI "IMAGENAME eq claude.exe"
```

If any `claude.exe` is listed, force it:

```powershell
taskkill /F /IM claude.exe /T
```

Now start Claude Desktop from the Start menu. **Settings → Developer** should list
**haver-chart** with two tools.

## Step 5 — Teach it the habits (optional but recommended)

The tool descriptions already carry the load-bearing rules, so Claude knows the basics
on connect. To sharpen the routine, paste `SYSTEM_PROMPT.md` into a Claude **Project's**
Instructions (every chat in that project then follows it), or install `SKILL.md` as a
skill.

## Step 6 — Use it

Paste a Haver chart screenshot and say what you want. Claude resolves each series with
`resolve_series`, shows you the candidates on any park, renders, and shows you the image
before calling it done. Worked examples below.

---

# Using it — worked examples

## Example A — the basic flow

Paste the Haver screenshot into the chat and say what you want:

> **You:** *[pastes a Haver chart]* Rebuild this in RenMac style. The formula on the
> chart is `zs(yryr%(IP))` and the other line is the Philly Fed manufacturing survey's
> current-activity diffusion index, also z-scored. Shared axis, recession bands.

Claude reads the screenshot, then calls `resolve_series` once per line. You will see the
tool calls happen. The first one takes the printed formula verbatim:

```
resolve_series(base_descriptor="Industrial Production",
               formula="zs(yryr%(IP))", sa_hint="sa", freq_hint="monthly")

  -> status: "resolved",  resolved: "ip@ip",  reason: "formula confirmed: ip@ip"
```

The second has no formula on the chart, so the transform goes in as words:

```
resolve_series(base_descriptor="Philly Fed Mfg Business Outlook: Current Activity Diffusion Index",
               applied_transform="Z-Score", sa_hint="sa", freq_hint="monthly")

  -> status: "resolved",  resolved: "bocgx@surveys",  similarity: 1.0,  exact_token_match: true
     candidates:
       1.0    exact=True   bocgx@surveys    Philly Fed Mfg Business Outlook: Current Activity Diffusion Index (SA, %Bal)
       0.8    exact=False  bocivx@surveys   Philly Fed Mfg Business Outlook: Current Inventories Diffusion Index (SA, %Bal)
       0.8    exact=False  bocnex@surveys   Philly Fed Mfg Business Outlook: Current Employment Diffusion Index (SA, %Bal)
```

Note what happened there. Two sibling series scored 0.8 — close enough that picking on
similarity alone is a coin flip. The bind happened because `bocgx` was the only
**exact** token-set match. That is the gate doing its job, and it is why you should
never let Claude pick a ticker itself.

Then it renders, and the chart comes back **inline in the chat**:

```
render_chart(series=[...the two resolved slots...],
             title="Manufacturing output and the Philly Fed survey",
             subtitle="Z-score", st_force=true,
             sample_start="1998", axis_mode="shared", recession_shading=true,
             left_min=-6, left_max=4)

  -> outputs/chat/2026-08-20/philly_fed_ip.png
     plotted: ["Industrial Production (y/y %, z-score)",
               "Philly Fed Mfg Current Activity Diffusion Index (SA, z-score)"]
     end: 2026-08-31
```

**Look at the chart before you accept it.** That is the entire point of this lane — on
the email pipeline you approve a ticker list and find out about a mistake when the chart
ships. Here you approve pixels.

## Example B — a park, and how to answer it

Vague descriptors do not bind, by design:

```
resolve_series(base_descriptor="Business Activity Index", sa_hint="sa")

  -> status: "parked"
     reason: "4 descriptor-similar candidates — human disambiguates"
     candidates:
       0.6    exact=False  nmfbaia@surveys   ISM: Services: Business Activity Index (SA, 50+=Increasing)
       0.6    exact=False  nmfbaia@usecon    ISM Services: Business Activity Index (SA, 50+ = Economy Expanding)
       0.5    exact=False  h111ms@mktpmi     US PMI: Services Business Activity Index [Flash] (SA, 50+=Expansion)
```

A park is a normal result, not a failure. Claude should show you these and ask. You
answer in plain English:

> **You:** The ISM one, from the surveys database.

The most useful thing you can do to avoid parks is give the series name **as printed on
the chart**, including the source prefix and the qualifiers — "ISM: Services: Business
Activity Index" binds where "Business Activity Index" cannot.

## Example C — iterating

This is where the chat lane earns its keep. Every one of these is a re-render, in
context, with no ticker re-resolution:

> **You:** Title it "Factory output is lagging the survey" instead.

> **You:** Put the Philly Fed line on the right axis with its own scale.

*(Claude re-renders with `axis_mode="dual"` and `axis="R"` on that slot; the legends
pick up LHS/RHS tags automatically.)*

> **You:** Start it at 2010, and drop the recession bands.

> **You:** Lag the survey by three months so the lead-lag lines up.

*(That is a `lag` of `[-3]` on the slot. `[-n]` lags, `[+n]` leads — a lagged line
extends the right edge, which is intended.)*

> **You:** Make the second series bars instead of a line.

*(`plot_kind="bar"`; `"stacked_bar"` for contribution charts.)*

> **You:** No title at all, just the chart.

*(`no_title=true`. The subtitle stays, because it carries the transform label.)*

## Example D — checking the reconstruction is actually right

A chart that looks right can still be the wrong series. Before using one:

> **You:** The source chart's last reading for the survey line is about 2.4. Does yours
> match?

Claude reads the reconstruction's last value and compares. On the Philly Fed chart above
the reconstruction ends at **2.4614** against a source value of ~2.4, and IP at
**0.0397** — both within tolerance. If they disagree by more than rounding, something is
bound or transformed wrong; do not accept the chart.

## What to say to get good results

| Do | Instead of |
|---|---|
| Paste the formula the chart prints: `zs(yryr%(IP))` | "z-score of the year-over-year change in industrial production" |
| Give the full printed series name with its source prefix | "the ISM index" |
| Say "SA" or "NSA" when the chart says it | leaving it out and hoping |
| Answer a park by picking a candidate | telling Claude "just use the first one" |
| Say what looks wrong and ask for a re-render | accepting a chart you are unsure about |

## Things it will refuse to do, and why

These are guardrails, not bugs. If you hit one, the answer is to restate the request,
not to route around it.

| What you see | What it means |
|---|---|
| `unmappable applied_transform '…'` | The transform vocabulary rejected the phrase. Restate it in Haver's wording, or pass the printed formula. It refuses rather than silently plotting an untransformed level. |
| `render needs every slot resolved` | A slot is still parked. A partially-resolved chart renders nothing, deliberately. |
| a raw-mnemonic or duplicate legend error | Every line needs a distinct human-readable label. A bare ticker in a legend is never acceptable. |
| `KeyError: 'D'` | A daily-frequency series. Not supported yet — ask for the weekly twin. |
| `NeedPin` | An INDEX/rebase transform whose base period cannot be inferred. |

Two more properties worth knowing. The **transform label cannot be suppressed** — it is
appended to the subtitle so a transformed chart always says what was done to it. (If
your subtitle already states it, ask for `st_force` so it is used verbatim and not
duplicated.) And this lane **never writes the firm's learning stores**: it reads every
ratified label and binding the daily pipeline has accumulated, but a chat binding never
writes back, because it was ratified by eye rather than through the formal approval
workflow.

Renders land in `outputs/chat/<date>/` and never touch the daily pipeline's renders or
ledger.

---

# Sharing this with teammates

**There is a distributable zip.** Build it with:

```bash
python scripts/build_teammate_package.py --list   # manifest, writes nothing
python scripts/build_teammate_package.py          # dist/haver-chart-<sha>-<date>.zip
```

Send the zip; send the Neon URL separately, because the zip deliberately carries no
credential. The teammate unzips, runs `configure.py`, and reads `README_FIRST.md`
(which is `haver_chart/TEAMMATE_SETUP.md`).

All four G9e steps are done: the paths are relocatable (step 1), the credential policy
is decided (step 2), the stores publish to `P:\Public\RenMac_Chart_Knowledge` on every
approval round (step 3), and the package builds from any commit (step 4).

## What the lane actually reaches outside this repo

Four things, three of them at absolute paths hard-coded into `src/`:

All three paths are now **environment variables with this machine's path as the
default**, so nothing here changes for Aman and the daily lane is unaffected.

| What | Variable (default = this machine) | If it is missing |
|---|---|---|
| `renmac_chart_style` (palette, style, source box) | `ECON_TEMPLATES_CHARTS` → `…/econ-templates/charts`, used by `src/render.py` | **Hard `ImportError` at import.** The lane will not start. This is the only one that fails hard. |
| Catalog search + `NEON_READONLY_DATABASE_URL` | `HAVER_MCP_SERVER` → `…/2026_haver_mcp/server`, used by `src/haver_search.py`; the credential is read from that repo's `config/.env` | Caught. `available()` goes False and **every description-only series parks**. Formula-path series still bind. |
| Ratified stores (`legend_labels`, `learned_descriptors`, `trusted_tickers`, `native_ma`) | `CLARIFIED_KNOWLEDGE_DIR` → `…/knowledge_repo/clarified-knowledge`, used by `src/resolve.py` | Caught. Reads return `{}`; you lose recall and every series needs an explicit `proposed_legend`. |
| Haver DLX licence + login | the `Haver` package against a local DLX install | No data at all. Cannot be bundled — it is per-user licensing. |

`selftest.py` resolves all three before it makes any live call, so a misconfigured
machine fails at pre-flight naming the variable to set, instead of at the first render.

## Who can run it today

| Situation | Works? |
|---|---|
| Aman's machine | Yes — steps 1–6 above, no variables needed. |
| Another machine with the four repos cloned anywhere, the three variables set, the two `.env` files present, and DLX installed | Yes. The paths no longer have to match Aman's. |
| A machine without the Neon credential | Partly. Formula-path series and anything already in the learning stores resolve; description-only series park. |
| A machine without `econ-templates` | **No.** `src/render.py` raises `ModuleNotFoundError: renmac_chart_style`. |

## The G9e work, in detail

### Step 1 — make the paths configurable — **DONE 2026-08-20**

`src/render.py` reads `ECON_TEMPLATES_CHARTS` and `src/haver_search.py` reads
`HAVER_MCP_SERVER`, each defaulting to this machine's path, exactly as `src/resolve.py`
already did with `CLARIFIED_KNOWLEDGE_DIR`. Additive and default-preserving, so the
daily lane is untouched: `selftest_workflow` 254/254 and `selftest_transforms` 43/43 are
green in the same commit. `haver_chart/selftest.py` gained three path checks (28 → 31)
that resolve all three variables before any live call, reporting each one's real
consequence rather than a bare pass/fail.

### Step 2 — how the secrets travel — **DECIDED 2026-08-20**

- **`NEON_READONLY_DATABASE_URL`** — **one shared read-only role for all teammates.**
  The role has read-only rights on the catalog mirror, so the blast radius of a leak is
  a metadata read, and one secret is one thing to rotate. It lives in
  `2026_haver_mcp/config/.env` and must never be committed or pasted into a chat.
- **DLX** — each teammate uses their own licence and login. Nothing to distribute.
- **`ANTHROPIC_API_KEY`** — not needed. The chat lane replaced `propose.py`, so there is
  no Opus call server-side.

### Step 3 — how the knowledge stores travel — **DONE 2026-08-20: published to `P:\Public\RenMac_Chart_Knowledge`**

**Audit first — the stores are chart-replicator data only.** `knowledge_repo` also holds
the post-ship commentary notes (`econometrics/`, `economics/`, `_anki/`,
`code-cookbook/`), which are personal and must not be shared, so the folder was checked
rather than assumed:

- No project outside `2026_daily_chart_replicator` references `clarified-knowledge` or
  `CLARIFIED_KNOWLEDGE_DIR` anywhere in `new_work`.
- Every entry validates against the chart schema — 102 learned descriptors and 91
  trusted tickers all carry a well-formed `code@database`; the 79 legend keys are
  tickers or `expr:` composite keys; 2 native-MA entries.
- Timestamps run 2026-06-30 to 2026-08-11, the chart replicator's own lifetime.
- Databases referenced are all Haver: CBDB, CPIDATA, DAILY, EMPL, LABOR, MKTPMI,
  REALTOR, SURVEYS, USECON, USNA, WEEKLY.

**The share, not a git remote.** `P:\Public` is already reachable by everyone, so a repo
would add a clone-and-pull step that buys nothing. Teammates set
`CLARIFIED_KNOWLEDGE_DIR` to the published folder and read it directly.

```bash
python scripts/publish_knowledge.py            # publish
python scripts/publish_knowledge.py --dry-run  # show what would be published
```

**`run_daily` now publishes automatically.** Every path that can write a store — the
`--approve` round, `--retitle`, `--relegend` — calls `_publish_stores()` when it
finishes, so a teammate's copy is never more than one approval round behind. It runs
**last** and is **fail-soft**: the day's ingest, resolves, Teams round-trips and renders
are already saved by the time it fires, so an unmapped drive prints a warning naming the
manual re-run and nothing else. `G7_NO_PUBLISH=1` skips it.

**Why a publish step rather than pointing the daily lane straight at P:.** Two reasons,
both about not putting a network share on the approval path. `resolve.save_legend` does
an unguarded read-modify-write with no atomic rename, so a reader on the share can catch
a truncated file mid-write — and `_read_json` swallows that as `{}`, silently costing
every legend label for that call. And an unguarded write failure would raise in the
middle of a Teams approval. So the daily lane keeps writing locally, and publishing
copies finished files with temp-then-replace.

**Why an allowlist rather than copying the folder.** The script names the four files.
A new file appearing next to them — a commentary note, a scratch export — cannot be
published by accident. It also validates every store parses as a JSON object before it
touches the destination, because publishing a corrupt store would break every teammate
at once and the reader would swallow the error.

**Verified:** with `CLARIFIED_KNOWLEDGE_DIR` pointed at the share, all four stores load
(102 / 79 / 91 / 2) and "Texas Mfg Outlook Survey: General Business Activity" binds to
`DBACTS@SURVEYS` via `(learned)` with no catalog search — the fast path a teammate
actually gains. Writes remain sealed.

**Re-run `publish_knowledge.py` when you want teammates to see newer binds.** Nothing
does it automatically.

**How a read-only store still helps a teammate.** The stores are written by the *daily*
lane, when Aman approves a resolution in Teams. The chat lane only reads them, and
reading is where the whole benefit is:

| Store | What a read buys the teammate |
|---|---|
| `learned_descriptors.json` | A description approved in Teams binds instantly, `via: "(learned)"`, with no catalog search. This is the biggest effect — it converts a park into a bind. |
| `legend_labels.json` | A ratified label renders automatically. Without it the render RAISES unless the caller supplies `proposed_legend` every time. |
| `trusted_tickers.json` | A bare formula mnemonic gets its `@db` attached, so `zs(yryr%(IP))` resolves without the operator knowing the database. |
| `native_ma.json` | A series whose name already contains a moving average does not re-ask the "is this native?" question. |

So the flow is one-way: the daily lane learns, every chat user inherits. A teammate's
chat work adds nothing back, which is precisely §13.6 and decision G9d — a chat bind is
ratified by eye, not through `confirm_all`, and must not become something `run_daily`
trusts.

The useful consequence: because the chat lane cannot write, a teammate's copy can only
go **stale**, never **wrong**. Two copies can never disagree about the same descriptor.
That makes even a plain file copy safe; the only cost of staleness is a park where a
bind was possible, which the operator resolves by picking a candidate.

This is also why the subscribe model is worth the extra repo: a copy inherits a
snapshot, a pull stays subscribed, and neither can ever conflict.

### Step 4 — package — **DONE 2026-08-20**

`scripts/build_teammate_package.py` builds `dist/haver-chart-<sha>-<date>.zip`:

```
haver-chart/
  README_FIRST.md   <- haver_chart/TEAMMATE_SETUP.md
  configure.py      <- hoisted to the root; the teammate's one command
  repo/             <- tracked files at HEAD, minus plan.md/prompt.md/.cursor/
  vendor/charts/    <- renmac_chart_style.py
  vendor/haver_mcp/ <- db.py, queries.py, and a .env EXAMPLE
```

Two choices worth recording. **The file list comes from `git ls-tree HEAD`, not a folder
walk**: the working tree holds `notes/` (client email content), `data/` and `outputs/`,
so shipping only committed tracked files means nothing untracked can ride along. **The
two external modules are vendored** rather than cloned — all three files are small and
pure-python and imported by path, so copying them turns a four-checkout setup into an
unzip. `--list` prints each vendored file's mtime so drift is visible at build time.

`configure.py` is the teammate-side half of the same idea. It resolves absolute paths
from its own location, checks the interpreter's imports, writes the `.env`, and merges
the `haver-chart` entry into Claude Desktop's config — backing the file up and keeping
any MCP servers already there. Nobody hand-edits a path into JSON.

### Step 5 — what a teammate then does

1. Unzip somewhere permanent (the path goes into their Claude config).
2. `python -m venv …` and `pip install -r repo/haver_chart/requirements.txt`.
3. Install and log in to DLX.
4. `python configure.py --python <venv python> --neon-url "<sent separately>"`.
5. Run `repo/haver_chart/selftest.py` and expect it green.
6. Quit Claude from the tray icon and reopen.

All five are covered by `README_FIRST.md` in the zip.

---

## If something goes wrong

**`haver-chart` does not appear in Settings → Developer.** Almost always step 4. Check:

```powershell
Select-String -Path (Join-Path $env:LOCALAPPDATA "Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\logs\main.log") -Pattern "Not main instance" | Select-Object -Last 3
```

A recent `Not main instance, returning early from app ready` means Claude never
restarted. Redo step 4 with `taskkill`.

**The tool errors or disappears after starting.** The server log names the cause:

```powershell
Get-Content (Join-Path $env:LOCALAPPDATA "Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\logs\mcp-server-haver-chart.log") -Tail 40
```

Everything the pipeline prints goes to stderr and lands in that log — stdout is
reserved for the JSON-RPC protocol.

**"render needs every slot resolved".** A slot parked. Go back to `resolve_series` and
pick from its candidates.

**`KeyError: 'D'`.** A daily-frequency series. Known limit (§13.9, 2026-08-11 entry):
use the weekly twin (`petexa@weekly` rather than `petexa@daily`).

**`NeedPin`.** An applied INDEX/rebase transform. Known limit and correct behaviour —
the base period cannot be inferred.

**A wrong-looking chart.** Do not work around it. The render is the approval gate; say
what is wrong and re-resolve. Charts land in `outputs/chat/<date>/` and never touch the
daily lane's renders or ledger.
