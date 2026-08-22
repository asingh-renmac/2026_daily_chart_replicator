# haver-chart — setup on your machine

This package turns a Haver screenshot into a RenMac-styled chart from inside a Claude
Desktop conversation. You paste a chart, name the series in plain English, and the
tools bind each one to a real Haver ticker, pull it, and render it.

It is a **read-only** copy of the daily pipeline. You cannot change what the team has
learned; you inherit it. See "What you can and cannot do" below.

Budget about 20 minutes, most of it waiting on `pip`.

---

## 0. What you need first

| Requirement | How to check | If you don't have it |
|---|---|---|
| Python 3.10+ | `python --version` | Install 3.10 or newer, or use an existing venv |
| Haver DLX entitlement | `python -c "import Haver; Haver.direct('on')"` | Ask IT — this is the vendor data feed and there is no substitute |
| `P:` drive mapped | `dir P:\Public\RenMac_Chart_Knowledge` | Map it, or the lane runs without the team's learned bindings |
| Claude Desktop | it's installed and you can sign in | Install it |
| The read-only catalog URL | Aman sends it separately | You can set up without it; catalog search stays dark until you add it |

The catalog URL is **not** in this zip on purpose. Ask Aman for it and paste it into
step 3 below.

---

## 1. Unzip somewhere permanent

Anywhere is fine — `C:\Users\<you>\haver-chart` is a good default. Do **not** put it in
`Downloads`, because the path is written into your Claude config and moving the folder
later breaks it.

You should see:

```
haver-chart/
  README_FIRST.md        <- this file
  configure.py           <- run this in step 3
  repo/                  <- the chart pipeline
  vendor/                <- the chart style + catalog query modules
```

## 2. Install the Python packages

Use a venv so this can't disturb anything else you run:

```bat
python -m venv C:\Users\%USERNAME%\envs\haver-chart
C:\Users\%USERNAME%\envs\haver-chart\Scripts\python.exe -m pip install -r repo\haver_chart\requirements.txt
```

If you already have a venv with `matplotlib`, `pandas` and `Haver`, reuse it — you only
need to add `fastmcp` and `psycopg[binary]`.

`Haver` may not install from the public index. If pip cannot find it, ask IT for the
internal wheel; every other package is public.

## 3. Point everything at your machine

One command. It checks your interpreter, writes the catalog credential, and tries to
add `haver-chart` to Claude Desktop's config without disturbing any MCP servers you
already have (it backs the file up first).

```bat
C:\Users\%USERNAME%\envs\haver-chart\Scripts\python.exe configure.py ^
    --python C:\Users\%USERNAME%\envs\haver-chart\Scripts\python.exe ^
    --neon-url "<the URL Aman sent you>"
```

Add `--show` first if you want to see exactly what it will write and change nothing. The
block it prints as `MCP entry to install` is the JSON you will paste in the next step
if the script does not write the file for you.

The line it prints as `claude config:` is worth a glance — Claude Desktop stores that
file in one of two places depending on how it was installed, and `configure.py` picks
whichever one your machine actually uses.

If your `P:` drive is mapped somewhere else, add
`--knowledge "<your path>\RenMac_Chart_Knowledge"`.

A successful run prints `ok    wrote haver-chart` near the end. If it stops at
`1. Interpreter` with `FAIL`, or you never see that `wrote` line, **the config was
not changed.** Do the next step by hand. The selftest can still pass — it does not
talk to Claude.

## 4. Add the tool in Claude Desktop (do this if step 3 did not write the file)

This is the same step as `haver-data`. Claude only learns about a local tool from
`claude_desktop_config.json`. If that file has no `haver-chart` entry, the chat will
not see `resolve_series` or `render_chart`.

1. Run `configure.py --show` if you do not still have the JSON on screen. Copy the
   object inside `"haver-chart": { ... }` (the `command`, `args`, and `env` block).
2. Open **Claude Desktop**.
3. Go to **Settings → Developer → Edit Config**. This opens the right file — do not
   hunt for it yourself. The location differs between the Store build and the
   classic install.
4. **Do not delete anything already there.** You are adding one block.
   - If the file already has `"mcpServers"`, add a `"haver-chart"` entry next to
     the others. Put a comma after the previous entry.
   - If it does not have `"mcpServers"`, add the whole block below just after the
     opening `{` on the first line.

```json
  "mcpServers": {
    "haver-chart": {
      "command": "C:/Users/<you>/envs/haver-chart/Scripts/python.exe",
      "args": ["C:/Users/<you>/demo/haver-chart/repo/haver_chart/server.py"],
      "env": {
        "ECON_TEMPLATES_CHARTS": "C:/Users/<you>/demo/haver-chart/vendor/charts",
        "HAVER_MCP_SERVER": "C:/Users/<you>/demo/haver-chart/vendor/haver_mcp/server",
        "CLARIFIED_KNOWLEDGE_DIR": "P:\\Public\\RenMac_Chart_Knowledge"
      }
    }
  },
```

Replace every path with the ones `configure.py --show` printed. Those paths must
match **your** unzip folder and **your** venv. Do not copy the `<you>` example as-is.

5. **Save** the file and close the editor.

Two things break this, both silently:

- **Backslashes in `command` and `args`.** Use forward slashes (`C:/Users/...`),
  exactly as `configure.py` printed them. A single `\` can invalidate the JSON and
  Claude then ignores the whole file with no error. The `CLARIFIED_KNOWLEDGE_DIR`
  value may keep `P:\\Public\\...` — that is a JSON string, and is fine.
- **A missing or extra comma.** Every entry except the last needs a comma after it.

## 5. Prove the pipeline works (this does not add the tool to Claude)

```bat
C:\Users\%USERNAME%\envs\haver-chart\Scripts\python.exe repo\haver_chart\selftest.py
```

This resolves a real series and renders a real PNG. Read the last line: if it says all
checks passed, the **server** is fine. It does not write Claude's config. If the
selftest is green and Claude still has no tools, you missed step 4 or the restart.

Individual `WARN` lines are survivable and say what you lose.

## 6. Restart Claude Desktop properly

Quit it **from the system tray** — closing the window leaves it running and it will not
re-read the config. Reopen it and ask:

> what haver-chart tools do you have?

You should get `resolve_series` and `render_chart`. If Claude says it has no such
tools, open Settings → Developer and confirm `haver-chart` is listed. If it is not,
redo step 4.

---

## Your first chart

Paste a Haver chart image into Claude and say:

> Rebuild this in RenMac style. Read the legend and axis off the image, resolve each
> series, then render it.

The full worked examples — including how to handle a series that parks, how to iterate
on a chart you don't like, and how to check the last plotted value against the number
printed on the source image — are in `repo/haver_chart/SETUP.md`. Read that one next;
it is the actual user guide.

Two rules worth learning before you start, because they cause most confusion:

- **A parked series is the tool working, not failing.** If the description is ambiguous
  it refuses to guess a ticker and hands you candidates. Pick one, or give it a better
  description. Never invent a ticker to make it proceed.
- **Transform wording is a fixed vocabulary.** "Z-Score", "Year/Year % Change" and so on
  map to Haver operators. If you invent a phrase it will reject it and list what it
  accepts.

---

## What you can and cannot do

| | Daily lane (Aman) | Your chat lane |
|---|---|---|
| Rebuild a chart from an image | yes | yes |
| Full RenMac styling, recession bars, source line | yes | yes |
| Read the team's learned tickers and legend labels | yes | yes |
| **Write** to those learned stores | yes | **no — sealed** |
| Teams approval round-trip, email ingest | yes | no |

The stores are read-only for you by design. The daily lane's approval step is what
ratifies a binding, and one person's chat experiment must never quietly become
everyone's default. Your copy refreshes whenever the daily lane republishes to `P:`.

That also means a binding you had to correct by hand today will still need correcting
tomorrow. Tell Aman — one approval on his side fixes it for the whole team.

---

## When something breaks

| Symptom | Cause | Fix |
|---|---|---|
| Claude doesn't list the tools | the `haver-chart` block is missing from the config, or Claude did not restart | paste the JSON from `configure.py --show` via Settings → Developer → Edit Config (step 4); quit from the tray |
| "catalog unavailable"; everything parks | no or bad Neon URL | re-run `configure.py --neon-url "..."` |
| Series bind but legends are generic | `P:` not reachable | map the drive, restart Claude |
| Import error on `renmac_chart_style` | package folder was moved after setup | re-run `configure.py` from the new location |
| A render fails with a transform error | you used an unsupported wording | the error lists the accepted wordings; use one |

The selftest is the fastest triage: run it first, and only debate Claude's config if it
passes.
