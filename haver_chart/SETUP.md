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

Expected: `19/19 checks passed`. It checks the import boundary (§13.5), that the
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

Paste a Haver chart screenshot and say what you want. Claude should resolve each series
with `resolve_series`, show you the candidates on any park, render, and show you the
image before calling it done.

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
