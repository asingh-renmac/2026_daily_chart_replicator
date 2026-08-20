"""
g8c_haver_query.py — drive the Haver-metadata catalog DIRECTLY (same code path as
the MCP search_series: lexical FTS over Neon via queries.build_search_query +
db.run_query), so the 87-query recall sweep runs in-process with no agent-context
cost and full 25-row (and 100-row) recall.

Run with the SERVER's env (psycopg + NEON_READONLY url):
  uv run --directory C:/Users/asingh/new_work/2026_haver_mcp/server \
      python C:/Users/asingh/new_work/2026_daily_chart_replicator/scripts/g8c_haver_query.py

Identical to the running MCP (HAVER_SEMANTIC_SEARCH is off → lexical FTS).
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

SERVER = Path("C:/Users/asingh/new_work/2026_haver_mcp/server")
load_dotenv(SERVER.parent / "config" / ".env")
sys.path.insert(0, str(SERVER))

import db          # noqa: E402
import queries     # noqa: E402

OUT = Path("C:/Users/asingh/new_work/2026_daily_chart_replicator/outputs/g8c")
ATTEMPTS = json.loads((OUT / "sweep_queries.json").read_text(encoding="utf-8"))


def run(query, databases, sa_status, cap):
    sql, params = queries.build_search_query(
        query, databases=databases, sa_status=sa_status, max_results=cap)
    out = []
    for r in db.run_query(sql, params):
        s = queries.serialize_row(r)
        out.append({"code": s["ticker"], "descriptor": s.get("descriptor"),
                    "frequency": s.get("frequency"), "sa_status": s.get("sa_status"),
                    "agg_type": s.get("agg_type"),
                    "is_discontinued": s.get("is_discontinued")})
    return out


hits25, hits100, unfiltered100 = {}, {}, {}
for a in ATTEMPTS:
    q, dbs, sa = a["query"], a["databases"], a["sa_status"]
    key = json.dumps([q, dbs, sa])
    hits25[key] = run(q, dbs, sa, 25)
    hits100[key] = run(q, dbs, sa, 100)
    # unfiltered (sa=None) cap-100 — serves the resolver's SA-advisory retry; slice to
    # any cap downstream. Dedup by (query, databases).
    ukey = json.dumps([q, dbs, None])
    if ukey not in unfiltered100:
        unfiltered100[ukey] = run(q, dbs, None, 100)
    print(f"  n25={len(hits25[key]):2d} n100={len(hits100[key]):3d} "
          f"nUnf={len(unfiltered100[ukey]):3d}  {q!r} db={dbs} sa={sa}")

(OUT / "sweep_hits").mkdir(parents=True, exist_ok=True)
(OUT / "sweep_hits" / "all.json").write_text(
    json.dumps(hits25, indent=1, ensure_ascii=False), encoding="utf-8")
(OUT / "sweep_hits_100.json").write_text(
    json.dumps(hits100, indent=1, ensure_ascii=False), encoding="utf-8")
(OUT / "sweep_hits_100_unfiltered.json").write_text(
    json.dumps(unfiltered100, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"\nwrote {OUT/'sweep_hits'/'all.json'} ({len(hits25)} queries @25-cap)")
print(f"wrote {OUT/'sweep_hits_100.json'} ({len(hits100)} queries @100-cap)")
print(f"wrote {OUT/'sweep_hits_100_unfiltered.json'} ({len(unfiltered100)} unfiltered @100-cap)")
