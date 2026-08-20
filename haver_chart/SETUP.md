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

**There is still no distributable zip for `haver-chart`.** Packaging is gate G9e in
`plan.md` §13.11. The zips in `2026_haver_mcp/dist/` are for the *data* MCP, which is
genuinely standalone — it needed only `haver` and `fastmcp`, so it zips cleanly. This is
a thin wrapper over a much larger machine, and the machine does not travel with it.

Three of the four G9e steps are now settled: the paths are relocatable (step 1, built),
the credential policy is decided (step 2), and the knowledge stores get their own
subscribe-able repo (step 3, decided — not yet created). What remains to build is the
package itself (step 4).

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

### Step 3 — how the knowledge stores travel — **DECIDED 2026-08-20: a remote, but not `knowledge_repo`'s**

The decision is that teammates **subscribe** rather than receive a one-off copy, so they
stay current as the daily lane learns. Two facts found while recording it change *which*
repo gets the remote:

1. **`clarified-knowledge/` is untracked.** `git status` in `knowledge_repo` reports it
   as `?? clarified-knowledge/` — the four store files have never been committed there.
   Giving `knowledge_repo` a remote would therefore ship none of them.
2. **`knowledge_repo` is mostly not these files.** It also holds `econometrics/` (~64 KB
   of personal concept notes), `economics/`, `_templates/`, `_anki/` and
   `code-cookbook/`, and it carries 19 uncommitted files. Publishing all of that to
   share four JSON files is a much wider disclosure than the decision intends.

**So: give the STORES their own small private repo**, and point `CLARIFIED_KNOWLEDGE_DIR`
at it. This needs no code — that variable already exists and `src/resolve.py` has always
read it. Steps:

```bash
# 1. new PRIVATE repo under the work org, e.g. asingh-renmac/renmac-chart-knowledge
# 2. seed it from the live stores
cd C:/Users/asingh/new_work/knowledge_repo/clarified-knowledge
git init && git add . && git commit -m "seed: ratified legend, learned, trusted, native-MA stores"
git remote add origin git@github-work:asingh-renmac/renmac-chart-knowledge.git
git push -u origin main
# 3. point the daily lane at the same checkout (or leave the default path in place)
#    setx CLARIFIED_KNOWLEDGE_DIR "C:/Users/asingh/new_work/renmac-chart-knowledge"
```

A teammate then clones that one repo, sets `CLARIFIED_KNOWLEDGE_DIR` to it, and runs
`git pull` whenever they want the daily lane's newer binds. **Not done — it publishes
firm ticker mappings, so the org and visibility are your call.**

The four files total about 47 KB, so the mechanics are trivial; the question is
governance.

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

### Step 4 — package — **OPEN, blocked only on step 3**

The zip is mechanical: `haver_chart/`, `src/`, `scripts/`, `requirements.txt`, a copy of
`renmac_chart_style.py` (or a pinned clone of `econ-templates`), the
`2026_haver_mcp/server/` query modules, an `.env.example`, and a teammate-facing SETUP
that sets the three variables. Model it on `2026_haver_mcp/dist/HaverData.zip`, which
already proves the shape.

### Step 5 — what a teammate then does

1. Install a Python 3.10+ venv and `pip install -r haver_chart/requirements.txt`.
2. Install and log in to DLX.
3. Set `ECON_TEMPLATES_CHARTS`, `HAVER_MCP_SERVER`, `CLARIFIED_KNOWLEDGE_DIR` and
   `NEON_READONLY_DATABASE_URL` for their own machine.
4. Run `haver_chart/selftest.py` and expect it green.
5. Add the `haver-chart` entry to their Claude Desktop config with **their** paths.
6. Quit Claude from the tray icon and reopen.

Steps 1 and 2 are done. Steps 3 and 4 are the remaining G9e work.

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
