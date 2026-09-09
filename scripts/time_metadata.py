"""What does one DLX metadata call cost, and can they run concurrently?

The first timing run said `12 x haver_metadata: 0.00s`, which is not a measurement -- it
re-fetched codes already cached by the runs above it. Measured properly here, in a fresh
process, on codes nothing has touched.

The number decides the whole picker redesign. `pick_series` makes 12 of these serially
while the operator watches an empty panel, so if each is ~1.7s the wait is ~20s and no
amount of UI wording fixes it; if concurrency works, the same 12 collapse to a few
seconds and a batched multi-series picker becomes possible at all.

Concurrency is the open question, not a given: DLX is a desktop application reached
through a COM-ish layer, and those are frequently apartment-threaded. This tries it
carefully and reports failures rather than assuming a speedup.

    & C:\\Users\\madz\\envs\\haver-chart\\Scripts\\python.exe scripts\\time_metadata.py
"""

from __future__ import annotations

import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

warnings.filterwarnings("ignore")

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Deliberately spread across databases and unrelated to the earlier probe's descriptors,
# so nothing is warm from a previous run.
CODES_SERIAL = ["LANAGRA@USECON", "LR@USECON", "PCU@USECON", "IP@USECON", "RSXFS@USECON",
                "CPIU@USECON"]
CODES_PARALLEL = ["GDPH@USECON", "PAYEMS@USECON", "UNRATE@USECON", "HOUST@USECON",
                  "PPIACO@USECON", "DGORDER@USECON"]


def main() -> int:
    from haver_chart import lane  # noqa: F401  (bootstraps sys.path for `resolve`)
    from haver_chart.bootstrap import quiet_stdout
    with quiet_stdout():
        import resolve as R

    def fetch(code):
        t = time.perf_counter()
        try:
            with quiet_stdout():
                R.haver_metadata(code)
            return code, time.perf_counter() - t, None
        except Exception as exc:
            return code, time.perf_counter() - t, f"{type(exc).__name__}: {exc}"

    print("=== serial, cold ===")
    t0 = time.perf_counter()
    for code in CODES_SERIAL:
        c, dt, err = fetch(code)
        print(f"  {c:18s} {dt:6.2f}s {err or ''}")
    serial = time.perf_counter() - t0
    print(f"  TOTAL {serial:.2f}s for {len(CODES_SERIAL)} "
          f"({serial / len(CODES_SERIAL):.2f}s each)")

    print()
    print("=== same count, 6 threads, cold codes ===")
    t0 = time.perf_counter()
    errs = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for c, dt, err in pool.map(fetch, CODES_PARALLEL):
            print(f"  {c:18s} {dt:6.2f}s {err or ''}")
            if err:
                errs.append(err)
    par = time.perf_counter() - t0
    print(f"  TOTAL {par:.2f}s for {len(CODES_PARALLEL)}")

    print()
    if errs:
        print(f"CONCURRENCY UNSAFE: {len(errs)} of {len(CODES_PARALLEL)} failed.")
        print("Serial fetching stays; the fix has to be a smaller pool or a cache.")
    elif par < serial * 0.6:
        print(f"CONCURRENCY WORKS: {serial:.1f}s -> {par:.1f}s "
              f"({serial / max(par, 0.01):.1f}x). A thread pool is the lever.")
    else:
        print(f"NO REAL SPEEDUP ({serial:.1f}s -> {par:.1f}s): something serializes")
        print("underneath, so parallelism is not the answer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
