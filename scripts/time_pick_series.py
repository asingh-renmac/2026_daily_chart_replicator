"""How long does the picker make the operator wait, and where does it go?

The panel renders the instant the tool is CALLED, so every millisecond between the call
and the tool result is time the operator spends looking at "Waiting for candidates...".
Before redesigning around that wait it is worth knowing whether it is 300 ms or 5 seconds,
and which part of it is DLX.

Matters doubly for a batched multi-park picker: whatever one descriptor costs, five cost
five times, serially, in a single tool call.

    & C:\\Users\\madz\\envs\\haver-chart\\Scripts\\python.exe scripts\\time_pick_series.py
"""

from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DESCRIPTORS = [
    "PPI final demand less foods, energy, and trade services",
    "Redbook same store sales",
    "industrial production chemicals",
    "CPI services less medical care",
    "retail sales excluding autos, building materials and gasoline stations",
]


def main() -> int:
    # `resolve` is what lane.py calls R; it only becomes importable after
    # haver_chart.bootstrap has put the chart toolchain on sys.path, so import it AFTER
    # the lane and through the same quiet_stdout guard (importing Haver prints).
    from haver_chart import lane
    from haver_chart.bootstrap import quiet_stdout
    with quiet_stdout():
        import resolve as R

    # Cold vs warm matters: the docstring says metadata is cached per process, so the
    # SECOND call is the one a long-lived server actually serves. Both are reported --
    # quoting only the warm number would flatter a panel whose first use is the slow one.
    print(f"{'descriptor':52s} {'cold':>8s} {'warm':>8s} {'cands':>6s}")
    print("-" * 78)

    total_cold = 0.0
    for d in DESCRIPTORS:
        t0 = time.perf_counter()
        try:
            out = lane.pick_series(d, original_request="timing probe")
        except Exception as exc:
            print(f"{d[:52]:52s} FAILED: {type(exc).__name__}: {exc}")
            continue
        cold = time.perf_counter() - t0

        t1 = time.perf_counter()
        lane.pick_series(d, original_request="timing probe")
        warm = time.perf_counter() - t1

        total_cold += cold
        n = len(out.get("candidates") or [])
        print(f"{d[:52]:52s} {cold:7.2f}s {warm:7.2f}s {n:6d}")

    print("-" * 78)
    print(f"{'ALL FIVE, cold, serially':52s} {total_cold:7.2f}s")
    print()
    print("That total is what a single batched pick_series call would cost before the")
    print("panel could show anything at all.")

    # Isolate DLX so the fix can be aimed. If metadata dominates, parallelism or a
    # narrower pool is the lever; if resolve_one dominates, neither would help.
    print()
    t0 = time.perf_counter()
    out = lane.resolve_one(DESCRIPTORS[0], candidate_n=12)
    t_resolve = time.perf_counter() - t0
    codes = [c.get("code") for c in (out.get("candidates") or [])][:12]
    t0 = time.perf_counter()
    for code in codes:
        with quiet_stdout():
            R.haver_metadata(code)
    t_meta = time.perf_counter() - t0
    print(f"resolve_one(candidate_n=12) : {t_resolve:.2f}s")
    print(f"{len(codes)} x haver_metadata (cold) : {t_meta:.2f}s "
          f"({t_meta / max(len(codes), 1) * 1000:.0f} ms each)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
