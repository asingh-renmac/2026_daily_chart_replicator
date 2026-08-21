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

One command. It checks your interpreter, writes the catalog credential, and adds
`haver-chart` to Claude Desktop's config without disturbing any MCP servers you
already have (it backs the file up first).

```bat
C:\Users\%USERNAME%\envs\haver-chart\Scripts\python.exe configure.py ^
    --python C:\Users\%USERNAME%\envs\haver-chart\Scripts\python.exe ^
    --neon-url "<the URL Aman sent you>"
```

Add `--show` first if you want to see exactly what it will write and change nothing. The
line it prints as `claude config:` is worth a glance — Claude Desktop stores that file in
one of two places depending on how it was installed, and `configure.py` picks whichever
one your machine actually uses.

If your `P:` drive is mapped somewhere else, add
`--knowledge "<your path>\RenMac_Chart_Knowledge"`.

## 4. Prove it works before you touch Claude

```bat
C:\Users\%USERNAME%\envs\haver-chart\Scripts\python.exe repo\haver_chart\selftest.py
```

This resolves a real series and renders a real PNG. Read the last line: if it says all
checks passed, the server is fine and any remaining problem is Claude's config or the
restart. Individual `WARN` lines are survivable and say what you lose.

## 5. Restart Claude Desktop properly

Quit it **from the system tray** — closing the window leaves it running and it will not
re-read the config. Reopen it and ask:

> what haver-chart tools do you have?

You should get `resolve_series` and `render_chart`.

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
| Claude doesn't list the tools | it didn't restart, or the config went to the wrong file | quit from the tray; re-run `configure.py --show` and confirm the `claude config:` line matches the file Settings → Developer → Edit Config opens |
| "catalog unavailable"; everything parks | no or bad Neon URL | re-run `configure.py --neon-url "..."` |
| Series bind but legends are generic | `P:` not reachable | map the drive, restart Claude |
| Import error on `renmac_chart_style` | package folder was moved after setup | re-run `configure.py` from the new location |
| A render fails with a transform error | you used an unsupported wording | the error lists the accepted wordings; use one |

The selftest is the fastest triage: run it first, and only debate Claude's config if it
passes.
