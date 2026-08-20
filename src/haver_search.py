"""
haver_search.py — runtime catalog search/get_meta for the G8 resolver.

The resolver's search surface (the haver-metadata MCP at authoring time) is reached
HERE in-process for a live `run_daily`, through the MCP server's OWN modules
(`queries.build_search_query` + `db.run_query`, lexical FTS over the read-only Neon
mirror) — identical code path, no agent in the loop. Metadata only; observations are
pulled separately via the Haver DLX path (g4_lib.pull / confirm_ticker).

If the server modules or the NEON_READONLY url aren't importable, `available()` is
False and the caller parks every description-only slot (fail-safe, never a guess).
"""
from __future__ import annotations

import os
import sys
import time as _time
from pathlib import Path
from typing import Optional

# The catalog query modules live in the haver-metadata repo. The default is this
# machine's checkout; `HAVER_MCP_SERVER` lets a different machine point at its own.
# A wrong path here does NOT stop the process — `_ensure` catches it and every
# description-only slot parks, which is the fail-safe already relied on (G9e step 1).
_SERVER = Path(os.environ.get(
    "HAVER_MCP_SERVER", "C:/Users/asingh/new_work/2026_haver_mcp/server"))
_READY = None  # tri-state: None=untried, True=ok, False=unavailable

# Neon scales the read-only mirror compute to ZERO when idle. The FIRST query of a run
# then pays a cold compute + cold buffer cache and can blow the MCP server's 15s
# statement_timeout (→ psycopg QueryCanceled) or hit a connect blip (→ OperationalError)
# — the 2026-07-20 crash where one cold search aborted the whole day's resolve. A retry
# lands on a now-warm compute/cache (warm searches measure ~2-5s) and succeeds. Budget is
# generous because the cache warms progressively across attempts; a persistent failure
# still raises after it (a real outage, not a warm-up blip).
_DB_RETRIES = 4


def _is_transient_db(exc: Exception) -> bool:
    try:
        import psycopg
    except Exception:
        return False
    # statement_timeout on a cold cache, or a connect/compute-waking blip.
    return isinstance(exc, (psycopg.errors.QueryCanceled, psycopg.OperationalError))


def _run_query(sql, params):
    """`db.run_query` with Neon cold-start resilience (retry transient timeouts/blips)."""
    import db
    last: Optional[Exception] = None
    for attempt in range(_DB_RETRIES + 1):
        try:
            return db.run_query(sql, params)
        except Exception as exc:
            if not _is_transient_db(exc):
                raise
            last = exc
            if attempt < _DB_RETRIES:
                wait = min(2.0 ** attempt, 8.0)
                print(f"[haver_search] transient DB error ({type(exc).__name__}) — retry "
                      f"{attempt + 1}/{_DB_RETRIES} in {wait:.0f}s (Neon cold-start warming)",
                      file=sys.stderr)
                _time.sleep(wait)
    raise last  # exhausted the budget — surface the real failure LOUD


def _ensure() -> bool:
    global _READY
    if _READY is not None:
        return _READY
    try:
        if str(_SERVER) not in sys.path:
            sys.path.insert(0, str(_SERVER))
        from dotenv import load_dotenv
        load_dotenv(_SERVER.parent / "config" / ".env")
        import db, queries  # noqa: F401
        _READY = True
    except Exception as exc:           # pragma: no cover - env-dependent
        print(f"[haver_search] catalog unavailable ({exc}); description slots will park")
        _READY = False
    return _READY


def available() -> bool:
    return _ensure()


def _row(serialized: dict) -> dict:
    return {"code": serialized.get("ticker"),
            "descriptor": serialized.get("descriptor"),
            "frequency": serialized.get("frequency"),
            "sa_status": serialized.get("sa_status"),
            "agg_type": serialized.get("agg_type")}


def search(query: str, databases=None, sa_status=None, max_results: int = 30) -> list:
    if not _ensure():
        return []
    import queries
    try:
        sql, params = queries.build_search_query(
            query, databases=databases, sa_status=sa_status, max_results=max_results)
    except ValueError:
        return []
    return [_row(queries.serialize_row(r)) for r in _run_query(sql, params)]


def get_meta(code: str) -> Optional[dict]:
    if not _ensure() or not code:
        return None
    import queries
    try:
        full = queries.parse_ticker(code)
    except ValueError:
        return None
    sql, params = queries.build_get_series(full)
    rows = _run_query(sql, params)
    if not rows:
        return None
    s = queries.serialize_row(rows[0])
    return {"descriptor": s.get("descriptor"), "sa_status": s.get("sa_status"),
            "frequency": s.get("frequency"), "agg_type": s.get("agg_type")}
