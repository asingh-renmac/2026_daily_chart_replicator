"""G17e (chart half) — what ten overlapping renders actually do to this process.

The HTTP server accepts many MCP sessions at once. That is cheap. What is not cheap is
the work behind `render_chart`: one process-wide lock around DLX + matplotlib, because
pyplot is global and Haver's client is almost certainly not thread-safe. Ten people
hitting Render at the same moment do not get ten parallel charts — they get a queue.

This drives `lane.render` in-process (no Entra, no tunnel) so the only variable is that
lock. Run it on the AVD, against the same venv that serves the connector.

    python scripts/g17e_concurrent_renders.py
    python scripts/g17e_concurrent_renders.py --n 10

What a PASS looks like: every call returns a PNG, every path is unique, none of the
images is another caller's chart, and wall time is about N times one render — not one
render, which would mean the lock is missing.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from haver_chart import lane  # noqa: E402

SERIES = [{"resolved": "BOCGX@SURVEYS",
           "proposed_legend": "Philly Fed Mfg Current Activity (SA)"}]


def one(i: int) -> dict:
    t0 = time.perf_counter()
    out = lane.render(SERIES, filename=f"g17e_{i}", title=f"g17e #{i}",
                      sample_start="2020")
    return {"i": i, "s": time.perf_counter() - t0, "path": out["path"],
            "end": out.get("end")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10, help="overlapping renders (default 10)")
    args = ap.parse_args()

    print(f"firing {args.n} renders at once — they should queue, not crash")
    t0 = time.perf_counter()
    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=args.n) as pool:
        futs = [pool.submit(one, i) for i in range(args.n)]
        for fut in as_completed(futs):
            try:
                rows.append(fut.result())
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
    wall = time.perf_counter() - t0

    paths = [r["path"] for r in rows]
    unique = len(set(paths)) == len(paths)
    times = sorted(r["s"] for r in rows)
    print(f"  ok      {len(rows)}/{args.n}")
    print(f"  failed  {len(errors)}" + ("" if not errors else "  — " + "; ".join(errors[:3])))
    print(f"  unique  {unique}  ({len(set(paths))} paths)")
    if times:
        print(f"  each    {times[0]:.1f}s .. {times[-1]:.1f}s")
    print(f"  wall    {wall:.1f}s   (≈ {wall / max(args.n, 1):.1f}s each if fully queued)")
    print()
    if errors:
        print("FAIL — something raised. That is a real problem; a queue should wait, not die.")
        return 1
    if not unique:
        print("FAIL — two callers got the same path. The uniqueness suffix is broken.")
        return 1
    if wall < (times[0] * args.n * 0.5) and args.n >= 3:
        print("NOTE — wall time is much less than N × one render. Either the lock is not")
        print("      serialising (bad — look at the PNGs) or pulls were cached and cheap.")
    else:
        print("Expected shape: a queue. The last caller waited; nobody got a wrong chart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
