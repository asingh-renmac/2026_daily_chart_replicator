"""Does the background warmer actually make series 3 fast?

The claim, and the reason the whole mechanism exists: after `pick_series_pages` returns,
the remaining series are enriched in the background, so an operator who spends a minute on
series one finds series two AND three already done.

Measured the way the operator meets it -- the panel calls `enrich_page` when they walk
onto a page, and that call is either a cache hit or a 20-second wait. This simulates the
reading time with a plain sleep, because the reading time is exactly what the warmer is
spending.

Run in a FRESH process with the caches cleared, or it measures nothing:

    Remove-Item knowledge\\metadata_cache.json -Force
    & C:\\Users\\madz\\envs\\haver-chart\\Scripts\\python.exe scripts\\time_warm_prefetch.py
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
    "PPI price index for Final Demand Less Foods, Energy, and Trade Services",
    "Redbook same store sales",
    "IP chemicals",
    "cpi price index for services less medical care",
]

READING_SECONDS = 90        # a modest stand-in for "I spent five minutes on series one"


def main() -> int:
    from haver_chart import lane

    print(f"cache entries at start: {len(lane._meta_cache_load())}")
    print()

    t0 = time.perf_counter()
    out = lane.pick_series_pages(DESCRIPTORS, original_request="warm probe")
    first = time.perf_counter() - t0
    pages = out["pages"]
    print(f"pick_series_pages          {first:6.2f}s  (page 1 ready, warmer started)")
    print(f"  queued for warming: {len(DESCRIPTORS) - 1}")
    print()

    print(f"...simulating {READING_SECONDS}s reading series 1...")
    for _ in range(READING_SECONDS):
        time.sleep(1)
    print()

    # Now walk the remaining pages exactly as the panel does.
    worst = 0.0
    for p in pages[1:]:
        t0 = time.perf_counter()
        got = lane.enrich_page(p["description"])
        dt = time.perf_counter() - t0
        worst = max(worst, dt)
        verdict = "WARM" if dt < 3 else ("cold" if dt > 10 else "partial")
        print(f"  arrive at {p['description'][:44]:46s} {dt:6.2f}s  {verdict}  "
              f"({len(got['candidates'])} cands)")

    print()
    if worst < 3:
        print(f"PASS: every later series was already warm (worst {worst:.2f}s).")
    else:
        print(f"FAIL: worst arrival was {worst:.2f}s -- the warmer did not keep up, or "
              f"did not run.")
        print("Check stderr above for '[picker] warmed ...' lines.")
    return 0 if worst < 3 else 1


if __name__ == "__main__":
    sys.exit(main())
