"""Time and error-check DLX metadata reads for specific tickers, one at a time.

Exists because a descriptor-exact TIE is the only thing that makes the resolver call
DLX during resolve_series: with one exact match it binds, with none it scores and parks,
but with two it must ask DLX which is which (resolve.break_exact_tie). So a search
improvement that produces exact twins where there were none moves the tool onto a code
path that was previously never taken in that lane — and if DLX is unwell on this host,
that shows up as a tool error on a query that used to "work" only because it parked.

Each read is timed and exceptions are caught per ticker, so one bad code cannot hide the
behaviour of the others.

    python scripts/probe_dlx_meta.py jcdrgi@usna jcdrgim@usna
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "haver_chart"))


def main() -> int:
    codes = sys.argv[1:]
    if not codes:
        print("usage: probe_dlx_meta.py CODE@DB [CODE@DB ...]")
        return 2

    import bootstrap  # noqa: F401  -- sets the path vars at import time
    import resolve as R

    bad = 0
    for code in codes:
        t = time.perf_counter()
        try:
            meta = R.haver_metadata(code)
            dt = time.perf_counter() - t
            if meta is None:
                print(f"{code:16} {dt:6.2f}s  -> None (DLX did not return metadata)")
                bad += 1
                continue
            print(f"{code:16} {dt:6.2f}s  fields={len(meta)}")
            for k in ("descriptor", "shortsource", "frequency", "aggtype", "startdate"):
                if k in meta:
                    print(f"                   {k:12} = {str(meta[k])[:56]}")
        except Exception:
            print(f"{code:16} {time.perf_counter()-t:6.2f}s  RAISED:")
            traceback.print_exc()
            bad += 1

    # The tie-break itself, which is what resolve_series actually runs.
    if len(codes) >= 2:
        t = time.perf_counter()
        try:
            diff = R.mirror_differences(codes[0], codes[1])
            print(f"\nmirror_differences({codes[0]}, {codes[1]}) -> {diff} "
                  f"[{time.perf_counter()-t:.2f}s]")
            print("  None means DLX was unreachable for one of them, which parks the slot")
        except Exception:
            print(f"\nmirror_differences RAISED after {time.perf_counter()-t:.2f}s:")
            traceback.print_exc()
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
