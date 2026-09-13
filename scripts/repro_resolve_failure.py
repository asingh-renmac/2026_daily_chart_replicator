"""Reproduce a resolve_series failure with the traceback FastMCP hides.

The lane wraps tool bodies so a raised exception reaches the client as
"Error occurred during tool execution" with no detail, and nothing lands in the logs
(the HTTP request is still a 200 — the error travels inside the payload). That is fine
for teammates and useless for diagnosis, so this calls lane.resolve_one directly, on the
host, with the same arguments, and prints what actually went wrong.

Times each stage separately: a tool that fails and a tool that is merely slow look
identical from the client, and they have opposite fixes.

    python scripts/repro_resolve_failure.py "PCE: Info Processing Equip Price Index" --freq M --sa sa
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "haver_chart"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("descriptor")
    ap.add_argument("--freq", default="")
    ap.add_argument("--sa", default="")
    ap.add_argument("--transform", default="")
    args = ap.parse_args()

    import bootstrap  # noqa: F401  -- sets HAVER_MCP_SERVER etc. at import time

    print(f"descriptor : {args.descriptor!r}")
    print(f"freq_hint  : {args.freq!r}   sa_hint: {args.sa!r}\n")

    # Stage 1: catalog search alone. Isolates "the SQL is slow/broken" from "DLX is".
    t = time.perf_counter()
    try:
        import haver_search as HS
        ok = HS.available()
        hits = HS.search(args.descriptor, max_results=30) if ok else []
        print(f"[1] catalog search : {time.perf_counter()-t:6.2f}s  "
              f"available={ok}  hits={len(hits)}")
        for h in hits[:5]:
            print(f"      {str(h.get('code')):22} {str(h.get('descriptor'))[:58]}")
    except Exception:
        print(f"[1] catalog search RAISED after {time.perf_counter()-t:.2f}s")
        traceback.print_exc()
        return 1

    # Stage 2: one DLX metadata read. Measured separately because it is the expensive
    # call (~1.7s each) and the one that dies when the native client is unwell.
    if hits:
        t = time.perf_counter()
        try:
            import resolve as R
            meta = R.haver_metadata(hits[0]["code"])
            print(f"\n[2] DLX metadata   : {time.perf_counter()-t:6.2f}s  "
                  f"fields={len(meta or {})}")
        except Exception:
            print(f"\n[2] DLX metadata RAISED after {time.perf_counter()-t:.2f}s")
            traceback.print_exc()
            return 1

    # Stage 3: the whole tool body, exactly as the MCP tool calls it.
    t = time.perf_counter()
    try:
        import lane
        out = lane.resolve_one(base_descriptor=args.descriptor,
                               applied_transform=args.transform,
                               sa_hint=args.sa, freq_hint=args.freq)
        print(f"\n[3] lane.resolve_one: {time.perf_counter()-t:6.2f}s")
        print(f"      status    : {out.get('status')}")
        print(f"      resolved  : {out.get('resolved')}")
        print(f"      reason    : {str(out.get('reason'))[:100]}")
        print(f"      candidates: {len(out.get('candidates') or [])}")
    except Exception:
        print(f"\n[3] lane.resolve_one RAISED after {time.perf_counter()-t:.2f}s "
              f"-- THIS is what the client saw as 'Error occurred during tool execution'")
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
