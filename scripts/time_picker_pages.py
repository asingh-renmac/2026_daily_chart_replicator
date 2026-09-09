"""Does the tabbed picker actually beat the five panels it replaces?

The claim the design rests on: the panel appears after ONE page's work, not five, and a
second visit is cheap because candidate metadata now survives on disk. Both halves are
measurable, so neither should be taken on trust.

Run it TWICE in separate processes. The second run is the one that proves the disk cache,
because an in-process cache would flatter the first run's later descriptors and tell us
nothing about what an operator meets after a nightly restart.

    & C:\\Users\\madz\\envs\\haver-chart\\Scripts\\python.exe scripts\\time_picker_pages.py
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
    "Retail sales excluding auto, building material and gasoline stations",
]


def main() -> int:
    from haver_chart import lane

    cache = lane._meta_cache_path()
    print(f"metadata cache: {cache}")
    print(f"  exists={cache.exists()} "
          f"entries={len(lane._meta_cache_load())}")
    print()

    t0 = time.perf_counter()
    out = lane.pick_series_pages(DESCRIPTORS, original_request="timing probe")
    first = time.perf_counter() - t0
    pages = out.get("pages") or []

    print(f"pick_series_pages  {first:6.2f}s   <- how long before the panel can appear")
    print(f"  pages={len(pages)} enriched={[bool(p['enriched']) for p in pages]}")
    print(f"  page 1 candidates={len(pages[0]['candidates'])} "
          f"sa_note={pages[0]['sa_note']!r}")
    if first > 30:
        print("  WARNING: slower than the five separate panels this replaces.")
    print()

    # Tabs 2..5, one at a time, exactly as the panel requests them.
    total = first
    for p in pages[1:]:
        t0 = time.perf_counter()
        got = lane.enrich_page(p["description"])
        dt = time.perf_counter() - t0
        total += dt
        print(f"enrich_page  {dt:6.2f}s  {len(got['candidates'])} cands  "
              f"{p['description'][:44]}")

    print()
    print(f"all five, this design      : {total:6.2f}s "
          f"(but only {first:.2f}s before the operator can start)")
    print(f"all five, five old panels  : 101.85s (all of it before the LAST one appears)")

    print()
    print(f"cache now holds {len(lane._meta_cache_load())} entries. Run this again in a "
          f"fresh process:")
    print("the second run's page-1 time is what an operator meets after a restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
