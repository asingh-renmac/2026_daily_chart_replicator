"""Run the resolver over the desk's alias list and produce a sheet a human can mark.

WHAT THIS IS NOT: a score. The CSV has no Haver ticker column -- it pairs an alias with a
plain-English canonical name -- so there is no ground truth to measure against, and
deriving one from our own resolver would bake in the very errors the exercise should
expose (plan.md 10.8 puts a human in that loop deliberately).

A first pass DID try the obvious trick, using the canonical name's own resolution as the
reference answer, and it does not work. The canonical column is written in RELEASE
language -- "G.17 manufacturing production index", "MBOS current prices paid index" --
which is not how Haver writes descriptors. Measured on the first six: the alias
"manufacturing IP" bound ipmfg@ip correctly while its canonical parked. The shorthand is
frequently CLOSER to the catalogue than the formal name, so scoring the alias against the
canonical would have marked good answers wrong.

What is genuinely useful, and what this now produces, is a review sheet: every alias, what
the resolver did with it, and the catalogue descriptor of whatever it bound, so the
descriptor can be read straight across from the alias and ticked or crossed. Two payoffs:
the crosses become alias-table entries, and the ticks become the labelled eval set the
project does not yet have.

The dangerous bucket is not the parks -- a park asks for help and is safe. It is the
CONFIDENT WRONG BIND: "payroll employment" bound onia@oes, an OES wage series, rather than
CES nonfarm payrolls, and nothing in the output said so. Those are listed first.

`confirm` is stubbed True rather than calling DLX: DLX refuses concurrent callers and costs
~1.7s a code, and it proves a ticker EXISTS, which is not the question here.

    python scripts/eval_econ_aliases.py --n 150
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, "C:/Users/asingh/new_work/2026_haver_mcp/server")

import haver_search as HS  # noqa: E402
import resolve as R  # noqa: E402


def stratified(rows: list[dict], n: int, seed: int = 20260913) -> list[dict]:
    """Proportional sample by category, so a pilot is not all Employment."""
    by = defaultdict(list)
    for r in rows:
        by[r.get("category") or "?"].append(r)
    rng = random.Random(seed)
    out: list[dict] = []
    for _, group in sorted(by.items()):
        rng.shuffle(group)
        out.extend(group[:max(1, round(n * len(group) / len(rows)))])
    rng.shuffle(out)
    return out[:n]


def describe(code_at_db: str) -> str:
    """The catalogue's own words for a ticker -- the thing the reviewer actually reads."""
    try:
        meta = HS.get_meta(code_at_db) or {}
        return str(meta.get("descriptor") or "")
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--out", default="outputs/alias_pilot")
    ap.add_argument("--csv", default="fixtures/econ_aliases.csv")
    args = ap.parse_args()

    rows = list(csv.DictReader((ROOT / args.csv).open(encoding="utf-8-sig")))
    sample = stratified(rows, args.n)
    print(f"{len(rows)} aliases in the list; pilot sample = {len(sample)}", flush=True)

    results, buckets = [], Counter()
    t0 = time.perf_counter()
    for i, row in enumerate(sample, 1):
        alias = row["alias"]
        slot = {"description": alias, "base_descriptor": alias}
        t = time.perf_counter()
        try:
            out = R.resolve_slot(slot, confirm=lambda c: True, get_meta=HS.get_meta,
                                 search=HS.search, clarified={}, trusted={}, learned={})
        except Exception as exc:
            out = {"resolved": None, "reason": f"{type(exc).__name__}: {exc}"}
        code = out.get("resolved")
        buckets["bound" if code else "parked"] += 1
        results.append({
            "alias": alias,
            "expected_meaning": row.get("underlying_series"),
            "category": row.get("category"),
            "bound_ticker": code,
            "bound_descriptor": describe(code) if code else "",
            "reason": str(out.get("reason") or "")[:200],
            "verdict": "",                      # <- the human fills this in
            "secs": round(time.perf_counter() - t, 2),
        })
        if i % 10 == 0 or i == len(sample):
            el = time.perf_counter() - t0
            print(f"  {i:4}/{len(sample)}  {el/60:5.1f}min  eta {el/i*(len(sample)-i)/60:5.1f}min"
                  f"   {dict(buckets)}", flush=True)
            _write(ROOT / args.out, results, buckets)

    _write(ROOT / args.out, results, buckets)
    n = len(results)
    print("\n" + "=" * 72)
    print(f"  bound  {buckets['bound']:4} ({100.0*buckets['bound']/max(n,1):5.1f}%)"
          f"   parked {buckets['parked']:4} ({100.0*buckets['parked']/max(n,1):5.1f}%)")
    print("=" * 72)
    print("A bind is NOT a correct answer -- read bound_descriptor against the alias.")
    print(f"review sheet: {ROOT / args.out}.csv")
    return 0


def _write(stem: Path, results: list[dict], buckets: Counter) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix(".json").write_text(
        json.dumps({"n": len(results), "buckets": dict(buckets), "results": results},
                   indent=2), encoding="utf-8")
    # Binds first: a park is safe and asks for help, a confident wrong bind does not.
    ordered = sorted(results, key=lambda r: (r["bound_ticker"] is None, r["category"] or ""))
    with stem.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(ordered)


if __name__ == "__main__":
    raise SystemExit(main())
