"""Concurrency smoke test for the hosted haver-data and haver-chart connectors.

What happens when several colleagues use the connectors at the same moment? This drives
the REAL path a colleague's Claude takes — the public URLs, the Microsoft sign-in, the
tunnel, the MCP session manager and the shared DLX seat — with one MCP session per
simulated person. Every session connects first; then all of them fire at the same
instant, the worst case of everyone pressing Enter in the same second.

Both servers serialise their Haver work behind one lock (one pull at a time on data, one
Haver call at a time on chart), so N simultaneous users are expected to QUEUE, not fail.
The number that matters is how long the last person in the queue waits, because Claude
abandons a tool call after about 60 seconds. Every call over that line is reported as
one Claude would have given up on.

    python scripts/concurrency_smoke.py                   # core suite, up to 15 users
    python scripts/concurrency_smoke.py --suite picker    # several pickers at once
    python scripts/concurrency_smoke.py --only mixed15
    python scripts/concurrency_smoke.py --max-users 10

The picker suite does what the panel does: `pick_series` opens a four-tab panel (tab one
enriched, capped at the server's 40s budget), then `enrich_page` for each other tab in
order, with one refill for any tab that came back partial ("dates still loading"). Each
user gets a different US state, so the tabs are cold unless a scenario reuses them on
purpose (`picker15_warm`). It never presses Save.

The first run opens a browser for the Microsoft sign-in, once per server. Tokens are
kept in ~/.haver_smoke_tokens so a re-run does not ask again; delete that folder to
sign out. Nothing is written to anyone's chat memory: resolve_series reads the stores,
render_chart files a PNG under the server's normal 14-day retention, and the picker
only fills the server's metadata cache.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from fastmcp import Client
from fastmcp.client.auth import OAuth
from key_value.aio._utils.sanitization import AlwaysHashStrategy
from key_value.aio.stores.filetree import FileTreeStore

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DATA_URL = "https://data.hvr-mcp.work/mcp"
CHART_URL = "https://chart.hvr-mcp.work/mcp"
HOST_TIMEOUT_S = 60.0
CLIENT_TIMEOUT_S = 330.0          # past the servers' 300s lock timeout, so we see it fire
TOKEN_DIR = Path.home() / ".haver_smoke_tokens"
OUT_DIR = ROOT / "outputs" / "concurrency_smoke"

# Well-known USECON series from the ratified store, so resolve_series binds rather than
# parks. Chart scenarios take disjoint slices: a render pulls fresh data either way, but
# distinct series rule out any chance that a later scenario coasts on an earlier one.
SERIES = [
    ("civilian unemployment rate: 16 yr + sa, %", "LR@USECON"),
    ("real gdp, chained 2017 dollars", "GDPH@USECON"),
    ("gdp chain-type price index", "JGDP@USECON"),
    ("personal income, nominal", "YPM@USECON"),
    ("real disposable personal income", "YPDHM@USECON"),
    ("retail sales & food services (sa, mil.$)", "NRST@USECON"),
    ("retail sales excluding motor vehicles and parts", "NRSTXM@USECON"),
    ("retail sales excluding motor vehicles and gasoline stations", "NRSTXM7@USECON"),
    ("retail sales control group (excludes autos, gasoline, building materials and "
     "food services)", "NRXMI47@USECON"),
    ("retail sales at food services and drinking places", "NRSV2@USECON"),
    ("retail sales at nonstore retailers (largely e-commerce)", "NRSJ4@USECON"),
    ("industrial production, total index", "IP@USECON"),
    ("industrial production, manufacturing (naics)", "IPMFN@USECON"),
    ("industrial production, mining", "IPB0@USECON"),
    ("industrial production, electric and gas utilities", "IPUTL@USECON"),
    ("capacity utilization: industry (sa, percent of capacity)", "CUT@USECON"),
    ("capacity utilization: manufacturing [naics] (sa, percent of capacity)", "CUMFN@USECON"),
    ("durable goods orders, total", "NMODG@USECON"),
    ("durable goods orders excluding transportation", "NMODGXT@USECON"),
    ("unfilled orders for durable goods (backlog)", "NMUDG@USECON"),
    ("manufacturers' inventories of durable goods", "NMIDG@USECON"),
    ("housing starts, total (all structure types)", "HST@USECON"),
    ("housing starts, single-family (1-unit)", "HST1@USECON"),
    ("housing starts, multifamily (5 or more units)", "HST5@USECON"),
    ("existing home sales, total (single-family plus condos and co-ops)", "HXTUS@USECON"),
    ("real final sales of domestic product (gdp excluding inventories)", "FSH@USECON"),
    ("2-year treasury note yield at constant maturity % p.a.", "FCM2@USECON"),
    ("adp nonfarm private payroll employment change - period to period", "LAXEPA@USECON"),
    ("industrial production: manufacturing [sic] % change - period to period sa, "
     "2017=100", "IPMFG@USECON"),
]

# One picker per simulated user: the same four questions about a different state, so
# every tab is a fresh catalogue search and a fresh set of candidate metadata.
STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
    "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "West Virginia", "Wisconsin", "Wyoming",
]
PICKER_TABS = [
    "Unemployment Rate: {s} (SA, %)",
    "All Employees: Total Nonfarm: {s} (SA, Thous)",
    "New Private Housing Units Authorized by Building Permits: {s}",
    "Average Hourly Earnings: Total Private: {s} (SA, $/Hour)",
]

# chart/data/picker = users of each kind; *_first = where that kind starts in its list.
SCENARIOS = {
    "baseline":      {"chart": 1, "data": 1, "chart_first": 0},
    "data5":         {"data": 5},
    "chart5":        {"chart": 5, "chart_first": 1},
    "data15":        {"data": 15},
    "chart15":       {"chart": 15, "chart_first": 6},
    "mixed15":       {"chart": 8, "data": 7, "chart_first": 21},
    "picker1":       {"picker": 1, "picker_first": 25},
    "picker5":       {"picker": 5, "picker_first": 0},
    "picker15":      {"picker": 15, "picker_first": 5},
    "picker15_warm": {"picker": 15, "picker_first": 5},
    "picker_mixed":  {"picker": 5, "chart": 5, "data": 5, "picker_first": 20,
                      "chart_first": 0},
}
SUITES = {
    "core": ["baseline", "data5", "chart5", "data15", "chart15", "mixed15"],
    "picker": ["picker1", "picker5", "picker15", "picker15_warm", "picker_mixed"],
}
SUITES["all"] = SUITES["core"] + SUITES["picker"]


async def sign_in(url: str) -> str:
    """One browser sign-in per server (cached after the first run); returns the token."""
    # The store files entries under the server URL, and "https:" is not a legal Windows
    # path component, so keys and collections are hashed.
    store = FileTreeStore(data_directory=TOKEN_DIR,
                          key_sanitization_strategy=AlwaysHashStrategy(),
                          collection_sanitization_strategy=AlwaysHashStrategy())
    oauth = OAuth(url, client_name="Haver concurrency smoke test", token_storage=store)
    async with Client(url, auth=oauth, timeout=120) as c:
        await c.list_tools()
    return oauth.context.current_tokens.access_token


async def timed(client: Client, tool: str, args: dict, rec: dict, t0: float, label=""):
    t = time.perf_counter()
    call = {"tool": tool, "label": label, "start_s": round(t - t0, 2)}
    rec["calls"].append(call)
    try:
        res = await client.call_tool(tool, args, raise_on_error=True)
        call.update(ok=True, s=round(time.perf_counter() - t, 2))
        return res
    except Exception as exc:
        call.update(ok=False, s=round(time.perf_counter() - t, 2),
                    err=f"{type(exc).__name__}: {exc}"[:300])
        raise


async def chart_user(c: Client, i: int, series: tuple[str, str], rec: dict, t0: float):
    desc, ticker = series
    res = await timed(c, "resolve_series", {"base_descriptor": desc}, rec, t0)
    sc = res.structured_content or {}
    slot = dict(sc.get("slot") or {})
    if sc.get("status") != "resolved":
        # A park is not what this test measures; render the known ticker so the user
        # still joins the render queue.
        rec["detail"] = "parked; rendered the known ticker instead"
        slot = {"resolved": ticker}
    slot.setdefault("proposed_legend", desc[:70])
    res = await timed(c, "render_chart",
                      {"series": [slot], "title": f"Smoke test #{i}", "sample_start": "2015",
                       "filename": f"smoke_chart_{i}"}, rec, t0)
    rec.setdefault("detail", f"chart ends {(res.structured_content or {}).get('end')}")


async def data_user(c: Client, series: tuple[str, str], rec: dict, t0: float):
    res = await timed(c, "get_observations",
                      {"ticker": series[1], "ticker_source": "exact",
                       "start_date": "2015-01-01"}, rec, t0)
    sc = res.structured_content or {}
    rec["detail"] = f"{sc.get('num_obs')} obs to {sc.get('end')}"


def _dated(candidates: list) -> int:
    return sum(1 for r in candidates or [] if r.get("end"))


async def picker_user(c: Client, state: str, rec: dict, t0: float):
    """Open a four-tab panel, then walk the tabs the way the panel does."""
    tabs = [t.format(s=state) for t in PICKER_TABS]
    res = await timed(c, "pick_series",
                      {"descriptors": tabs,
                       "original_request": f"Chart the {state} labour market and permits"},
                      rec, t0, label="open")
    pages = (res.structured_content or {}).get("pages") or []
    first = pages[0] if pages else {}
    rec["tabs"] = [{"tab": 1, "partial": bool(first.get("partial")),
                    "rows": len(first.get("candidates") or []),
                    "dated": _dated(first.get("candidates")),
                    "full_at_s": None if first.get("partial") else
                    round(time.perf_counter() - t0, 2)}]
    if first.get("partial"):
        # The panel's refill: same call again, which finds the rows the first one fetched.
        out = await timed(c, "enrich_page", {"base_descriptor": tabs[0]}, rec, t0,
                          label="refill 1")
        sc = out.structured_content or {}
        rec["tabs"][0].update(refilled=True, still_partial=bool(sc.get("partial")),
                              dated=_dated(sc.get("candidates")))
        if not sc.get("partial"):
            rec["tabs"][0]["full_at_s"] = round(time.perf_counter() - t0, 2)
    for k, desc in enumerate(tabs[1:], start=2):
        out = await timed(c, "enrich_page", {"base_descriptor": desc}, rec, t0,
                          label=f"tab {k}")
        sc = out.structured_content or {}
        tab = {"tab": k, "partial": bool(sc.get("partial")),
               "rows": len(sc.get("candidates") or []), "dated": _dated(sc.get("candidates")),
               "full_at_s": None if sc.get("partial") else round(time.perf_counter() - t0, 2)}
        if sc.get("partial"):
            out = await timed(c, "enrich_page", {"base_descriptor": desc}, rec, t0,
                              label=f"refill {k}")
            sc = out.structured_content or {}
            tab.update(refilled=True, still_partial=bool(sc.get("partial")),
                       dated=_dated(sc.get("candidates")))
            if not sc.get("partial"):
                tab["full_at_s"] = round(time.perf_counter() - t0, 2)
        rec["tabs"].append(tab)


async def one_user(kind: str, i: int, payload, token: str,
                   ready: list, go: asyncio.Event, t0box: list) -> dict:
    rec = {"kind": kind, "user": i, "calls": [], "error": None,
           "series": payload if kind == "picker" else payload[1]}
    url = DATA_URL if kind == "data" else CHART_URL
    tc = time.perf_counter()
    try:
        async with Client(url, auth=token, timeout=CLIENT_TIMEOUT_S) as c:
            rec["connect_s"] = round(time.perf_counter() - tc, 2)
            ready.append(i)
            await go.wait()
            t0 = t0box[0]
            if kind == "data":
                await data_user(c, payload, rec, t0)
            elif kind == "chart":
                await chart_user(c, i, payload, rec, t0)
            else:
                await picker_user(c, payload, rec, t0)
            rec["done_s"] = round(time.perf_counter() - t0, 2)
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
        if "connect_s" not in rec:
            rec["connect_s"] = round(time.perf_counter() - tc, 2)
            ready.append(i)
    return rec


async def run_scenario(name: str, tokens: dict, state_offset: int = 0) -> dict:
    sc = SCENARIOS[name]
    cf, pf = sc.get("chart_first", 0), sc.get("picker_first", 0) + state_offset
    jobs = ([("chart", k, SERIES[(cf + k) % len(SERIES)]) for k in range(sc.get("chart", 0))]
            + [("data", k, SERIES[k % len(SERIES)]) for k in range(sc.get("data", 0))]
            + [("picker", k, STATES[(pf + k) % len(STATES)])
               for k in range(sc.get("picker", 0))])
    ready: list = []
    go = asyncio.Event()
    t0box = [0.0]
    tasks = [asyncio.create_task(
        one_user(kind, i, p, tokens["data" if kind == "data" else "chart"], ready, go, t0box))
        for kind, i, p in jobs]
    deadline = time.perf_counter() + 90
    while len(ready) < len(jobs) and time.perf_counter() < deadline:
        await asyncio.sleep(0.05)
    t0box[0] = time.perf_counter()
    go.set()
    recs = await asyncio.gather(*tasks)
    return {"scenario": name, "users": len(jobs),
            "mix": {k: sc.get(k, 0) for k in ("chart", "data", "picker") if sc.get(k)},
            "wall_s": round(time.perf_counter() - t0box[0], 1), "records": recs}


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def _spread(xs: list[float]) -> str:
    if not xs:
        return ""
    return (f"fastest {min(xs):5.1f}s  median {statistics.median(xs):5.1f}s  "
            f"p95 {pct(xs, 95):5.1f}s  slowest {max(xs):5.1f}s")


def _call_line(name: str, calls: list) -> str:
    ok = [c["s"] for c in calls if c.get("ok")]
    over = sum(1 for c in calls if c.get("s", 0) > HOST_TIMEOUT_S)
    line = f"   {name:17} ok {len(ok)}/{len(calls)}   {_spread(ok)}"
    if over:
        line += f"   ⚠ {over} over {HOST_TIMEOUT_S:.0f}s (Claude would give up)"
    return line


def report(res: dict) -> None:
    recs = res["records"]
    conn = [r["connect_s"] for r in recs if "connect_s" in r]
    mix = ", ".join(f"{v} {k}" for k, v in res["mix"].items())
    print(f"\n── {res['scenario']}: {res['users']} users ({mix}) ──")
    opened = len(recs) - sum(1 for r in recs if r["error"] and not r["calls"])
    print(f"   sessions opened   {opened}/{len(recs)}   connect p50 "
          f"{statistics.median(conn):.1f}s  max {max(conn):.1f}s")
    for tool in ("get_observations", "resolve_series", "render_chart"):
        calls = [c for r in recs for c in r["calls"] if c["tool"] == tool]
        if calls:
            print(_call_line(tool, calls))

    pickers = [r for r in recs if r["kind"] == "picker"]
    if pickers:
        opens = [c for r in pickers for c in r["calls"] if c["label"] == "open"]
        tabs = [c for r in pickers for c in r["calls"] if c["label"].startswith("tab")]
        refills = [c for r in pickers for c in r["calls"] if c["label"].startswith("refill")]
        print(_call_line("picker opens", opens))
        if tabs:
            print(_call_line("tabs 2-4", tabs))
        if refills:
            print(_call_line("refills", refills))
        all_tabs = [t for r in pickers for t in r.get("tabs", [])]
        first_partial = sum(1 for r in pickers if r.get("tabs") and r["tabs"][0]["partial"])
        partial = sum(1 for t in all_tabs if t["partial"])
        stuck = sum(1 for t in all_tabs if t.get("still_partial"))
        rows = sum(t["rows"] for t in all_tabs)
        dated = sum(t["dated"] for t in all_tabs)
        print(f"   first tab partial {first_partial}/{len(pickers)} panels   "
              f"any tab partial {partial}/{len(all_tabs)}   "
              f"still partial after refill {stuck}")
        print(f"   rows with dates   {dated}/{rows} once the walk finished")
        full = [max(t["full_at_s"] for t in r["tabs"]) for r in pickers
                if len(r.get("tabs", [])) == len(PICKER_TABS)
                and all(t["full_at_s"] is not None for t in r["tabs"])]
        never = len(pickers) - len(full)
        print(f"   all 4 tabs full   {_spread(full) or 'nobody'}"
              + (f"   ({never} panel(s) never fully loaded)" if never else ""))

    print(f"   everyone done in  {res['wall_s']:.1f}s")
    for r in recs:
        if r["error"]:
            print(f"   ✗ {r['kind']} user {r['user']} ({r['series']}): {r['error']}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=list(SUITES), default="core")
    ap.add_argument("--only", choices=list(SCENARIOS), action="append")
    ap.add_argument("--max-users", type=int, default=15)
    ap.add_argument("--pause", type=float, default=5.0, help="seconds between scenarios")
    ap.add_argument("--state-offset", type=int, default=0,
                    help="shift every picker onto other states, for a cold re-run: the "
                         "server caches candidate metadata for 24h")
    args = ap.parse_args()

    names = [n for n in (args.only or SUITES[args.suite])
             if sum(v for k, v in SCENARIOS[n].items() if not k.endswith("_first"))
             <= args.max_users]
    print("signing in (a browser opens the first time, once per server)...", flush=True)
    tokens = {"data": await sign_in(DATA_URL), "chart": await sign_in(CHART_URL)}
    print("signed in to both servers.")

    results = []
    for k, name in enumerate(names):
        if k:
            await asyncio.sleep(args.pause)
        res = await run_scenario(name, tokens, args.state_offset)
        res["state_offset"] = args.state_offset
        report(res)
        results.append(res)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"smoke_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    n_err = sum(1 for res in results for r in res["records"] if r["error"])
    n_over = sum(1 for res in results for r in res["records"] for c in r["calls"]
                 if c.get("s", 0) > HOST_TIMEOUT_S)
    print(f"\nresults: {out.relative_to(ROOT)}")
    print(f"errors: {n_err}   calls over {HOST_TIMEOUT_S:.0f}s: {n_over}")
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
