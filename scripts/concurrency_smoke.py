"""Concurrency smoke test for the hosted haver-data and haver-chart connectors.

What happens when several colleagues use the connectors at the same moment? This drives
the REAL path a colleague's Claude takes — the public URLs, the Microsoft sign-in, the
tunnel, the MCP session manager and the shared DLX seat — with one MCP session per
simulated person. Every session connects first; then all of them fire at the same
instant, the worst case of everyone pressing Enter in the same second.

Both servers serialise their Haver work behind one lock (one pull at a time on data, one
render at a time on chart), so N simultaneous users are expected to QUEUE, not fail. The
number that matters is how long the last person in the queue waits, because Claude
abandons a tool call after about 60 seconds. Every call over that line is reported as
one Claude would have given up on.

    python scripts/concurrency_smoke.py                   # full ramp, up to 15 users
    python scripts/concurrency_smoke.py --only mixed15
    python scripts/concurrency_smoke.py --max-users 10

The first run opens a browser for the Microsoft sign-in, once per server. Tokens are
kept in ~/.haver_smoke_tokens so a re-run does not ask again; delete that folder to
sign out. Nothing is written to anyone's chat memory: resolve_series reads the stores
and render_chart files a PNG under the server's normal 14-day retention.
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

# name -> (chart users, data users, first SERIES index for chart users)
SCENARIOS = {
    "baseline": (1, 1, 0),
    "data5":    (0, 5, 0),
    "chart5":   (5, 0, 1),
    "data15":   (0, 15, 0),
    "chart15":  (15, 0, 6),
    "mixed15":  (8, 7, 21),
}


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


async def timed(client: Client, tool: str, args: dict, rec: dict, t0: float):
    t = time.perf_counter()
    call = {"tool": tool, "start_s": round(t - t0, 2)}
    rec["calls"].append(call)
    try:
        res = await client.call_tool(tool, args, raise_on_error=True)
        call.update(ok=True, s=round(time.perf_counter() - t, 2))
        return res
    except Exception as exc:
        call.update(ok=False, s=round(time.perf_counter() - t, 2),
                    err=f"{type(exc).__name__}: {exc}"[:300])
        raise


async def one_user(kind: str, i: int, series: tuple[str, str], token: str,
                   ready: list, go: asyncio.Event, t0box: list) -> dict:
    desc, ticker = series
    rec = {"kind": kind, "user": i, "series": ticker, "calls": [], "error": None}
    url = CHART_URL if kind == "chart" else DATA_URL
    tc = time.perf_counter()
    try:
        async with Client(url, auth=token, timeout=CLIENT_TIMEOUT_S) as c:
            rec["connect_s"] = round(time.perf_counter() - tc, 2)
            ready.append(i)
            await go.wait()
            t0 = t0box[0]
            if kind == "data":
                res = await timed(c, "get_observations",
                                  {"ticker": ticker, "ticker_source": "exact",
                                   "start_date": "2015-01-01"}, rec, t0)
                sc = res.structured_content or {}
                rec["detail"] = f"{sc.get('num_obs')} obs to {sc.get('end')}"
            else:
                res = await timed(c, "resolve_series", {"base_descriptor": desc}, rec, t0)
                sc = res.structured_content or {}
                slot = dict(sc.get("slot") or {})
                if sc.get("status") != "resolved":
                    # A park is not what this test measures; render the known ticker so
                    # the user still joins the render queue.
                    rec["detail"] = "parked; rendered the known ticker instead"
                    slot = {"resolved": ticker}
                slot.setdefault("proposed_legend", desc[:70])
                res = await timed(c, "render_chart",
                                  {"series": [slot], "title": f"Smoke test #{i}",
                                   "sample_start": "2015",
                                   "filename": f"smoke_{kind}_{i}"}, rec, t0)
                sc = res.structured_content or {}
                rec.setdefault("detail", f"chart ends {sc.get('end')}")
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
        if "connect_s" not in rec:
            rec["connect_s"] = round(time.perf_counter() - tc, 2)
            ready.append(i)
    return rec


async def run_scenario(name: str, tokens: dict) -> dict:
    n_chart, n_data, first = SCENARIOS[name]
    jobs = ([("chart", k, SERIES[(first + k) % len(SERIES)]) for k in range(n_chart)]
            + [("data", k, SERIES[k % len(SERIES)]) for k in range(n_data)])
    ready: list = []
    go = asyncio.Event()
    t0box = [0.0]
    tasks = [asyncio.create_task(one_user(kind, i, s, tokens[kind], ready, go, t0box))
             for kind, i, s in jobs]
    deadline = time.perf_counter() + 90
    while len(ready) < len(jobs) and time.perf_counter() < deadline:
        await asyncio.sleep(0.05)
    t0box[0] = time.perf_counter()
    go.set()
    recs = await asyncio.gather(*tasks)
    return {"scenario": name, "users": len(jobs), "chart_users": n_chart,
            "data_users": n_data, "wall_s": round(time.perf_counter() - t0box[0], 1),
            "records": recs}


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def report(res: dict) -> None:
    recs = res["records"]
    failed = [r for r in recs if r["error"]]
    conn = [r["connect_s"] for r in recs if "connect_s" in r]
    print(f"\n── {res['scenario']}: {res['users']} users "
          f"({res['chart_users']} chart, {res['data_users']} data) ──")
    print(f"   sessions opened   {len(recs) - sum(1 for r in recs if r['error'] and not r['calls'])}"
          f"/{len(recs)}   connect p50 {statistics.median(conn):.1f}s  max {max(conn):.1f}s")
    for tool in ("get_observations", "resolve_series", "render_chart"):
        calls = [c for r in recs for c in r["calls"] if c["tool"] == tool]
        if not calls:
            continue
        ok = [c["s"] for c in calls if c.get("ok")]
        over = sum(1 for c in calls if c.get("s", 0) > HOST_TIMEOUT_S)
        line = f"   {tool:17} ok {len(ok)}/{len(calls)}"
        if ok:
            line += (f"   fastest {min(ok):5.1f}s  median {statistics.median(ok):5.1f}s  "
                     f"p95 {pct(ok, 95):5.1f}s  slowest {max(ok):5.1f}s")
        if over:
            line += f"   ⚠ {over} over {HOST_TIMEOUT_S:.0f}s (Claude would give up)"
        print(line)
    print(f"   everyone done in  {res['wall_s']:.1f}s")
    for r in failed:
        print(f"   ✗ {r['kind']} user {r['user']} ({r['series']}): {r['error']}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(SCENARIOS), action="append")
    ap.add_argument("--max-users", type=int, default=15)
    ap.add_argument("--pause", type=float, default=5.0, help="seconds between scenarios")
    args = ap.parse_args()

    names = [n for n in (args.only or list(SCENARIOS))
             if sum(SCENARIOS[n][:2]) <= args.max_users]
    print("signing in (a browser opens the first time, once per server)...", flush=True)
    tokens = {"data": await sign_in(DATA_URL), "chart": await sign_in(CHART_URL)}
    print("signed in to both servers.")

    results = []
    for k, name in enumerate(names):
        if k:
            await asyncio.sleep(args.pause)
        res = await run_scenario(name, tokens)
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
