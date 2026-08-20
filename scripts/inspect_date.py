"""
inspect_date.py — DRY pre-run inspection of a date's raw-Haver charts.

Ingests a date (email + bloomberg), vision-reads each raw-Haver chart, and reports
the TRANSFORM TYPES present (level / MA / yryr% / difa%-annualized / zs / summed /
other) so a backdate can be chosen to deliberately cover the still-untested-end-to-end
transforms. Does NOT seed the production/backfill ledger and does NOT approve — it
reads into a throwaway temp dir, so the real `run_daily --approve` stays genuinely cold.

USAGE
  python scripts/inspect_date.py --date 2026-06-09 --source all
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

import ingest as I            # noqa: E402
import chartspec as CS        # noqa: E402
import transforms as T        # noqa: E402
import build_chart as BC      # noqa: E402

# transform types still UNTESTED end-to-end AFTER June 9 (which covered level, MA, yryr%,
# zs incl. nested zs(yryr%())). Still thin: annualized, summed, month/period %, index.
UNTESTED = {"difa% (annualized)", "summed", "diff%", "index"}


def classify_series(s: dict) -> tuple[str, str]:
    """(bucket, detail) for one chart_spec series — the transform type that the
    renderer would run through G3."""
    formula = s.get("formula")
    at = s.get("applied_transform")
    if formula:
        try:
            node = T.parse(formula)
        except Exception:
            return ("formula? (unparsed)", formula)
        if isinstance(node, T.BinOp):
            return ("summed", formula)
        if isinstance(node, T.Func):
            nm = node.name
            if nm.startswith("MOV"):
                return ("MA", formula)
            if nm == "YRYR" and node.pct:
                return ("yryr%", formula)
            if nm == "DIFA":
                return ("difa% (annualized)", formula)
            if nm in ("DIFV", "DIFF"):
                return ("diff%", formula)
            if nm == "ZS":
                return ("zs", formula)
            if nm == "INDEX":
                return ("index", formula)
            return (f"func:{nm}", formula)
        return ("level (bare formula)", formula)
    if at:
        try:
            f = BC.phrase_to_haver(at, "X")
        except Exception:
            return ("transform? (unmappable phrase)", at)
        if f is None:
            return ("level", at)
        node = T.parse(f)
        nm = node.name if isinstance(node, T.Func) else "?"
        if nm.startswith("MOV"):
            return ("MA", at)
        if nm == "YRYR":
            return ("yryr%", at)
        if nm == "DIFA":
            return ("difa% (annualized)", at)
        return (f"transform:{nm}", at)
    return ("level", "")


def _days(date_str: str, sources: list[str]) -> list[dict]:
    days = []
    if "email" in sources:
        mailbox = (os.environ.get("AS_DAILY_MAILBOX")
                   or os.environ.get("AS_SENDER_EMAIL"))
        if not mailbox:
            print("ERROR: no mailbox (AS_DAILY_MAILBOX / AS_SENDER_EMAIL)")
            return []
        try:
            days.append(I.ingest_day(mailbox, date_str, sender=I.DEFAULT_SENDER,
                                     use_vision=False))
        except I.MailAccessError as e:
            print(f"[FAIL] Mail.Read: {e}")
    if "bloomberg" in sources:
        import folder_ingest as FI
        days.append(FI.ingest_bloomberg_day(date_str, use_vision=False))
    return days


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True)
    ap.add_argument("--source", choices=["email", "bloomberg", "all"], default="all")
    args = ap.parse_args(argv)
    sources = ["email", "bloomberg"] if args.source == "all" else [args.source]

    days = _days(args.date, sources)
    charts = [(er, a) for day in days for er in day["results"] for a in er.raw_haver]
    print("=" * 78)
    print(f"DRY INSPECT — {args.date}  (no seed, no approve; temp read)")
    print(f"  raw-Haver charts found: {len(charts)}")
    print("=" * 78)
    if not charts:
        print("  NO raw-Haver charts on this date.")
        return 0

    bucket_tally: dict = {}
    shapes: set = set()        # the still-thin chart SHAPES present on this date
    for er, a in charts:
        try:
            spec = CS.read_chart_spec(a.data)
        except Exception as e:
            print(f"\n  «{er.subject}» {a.source}: VISION READ FAILED "
                  f"({type(e).__name__}: {e})")
            continue
        print(f"\n  «{er.subject}»  {a.source}   n_series={spec.n_series} "
              f"recession={spec.recession_shading} axis_mode={spec.axis_mode}")
        per_axis: dict = {}    # axis → set(transform buckets) for dual-different check
        for i, s in enumerate(spec.series):
            d = s.__dict__ if hasattr(s, "__dict__") else s
            bucket, detail = classify_series(d)
            bucket_tally[bucket] = bucket_tally.get(bucket, 0) + 1
            axis = (d.get("axis") or "shared")
            lag = d.get("lag")
            per_axis.setdefault(axis, set()).add(bucket)
            if bucket == "summed":
                shapes.add("summed/composite formula")
            if bucket == "difa% (annualized)":
                shapes.add("difa% annualized")
            if bucket == "index":
                shapes.add("index-rebase")
            if lag:
                shapes.add(f"bracket-lag {lag}")
            marks = []
            if bucket in UNTESTED:
                marks.append("*** UNTESTED-E2E")
            if lag:
                marks.append(f"LAG {lag}")
            desc = (d.get("base_descriptor") or d.get("description") or "")[:46]
            print(f"      #{i} ax={axis:6} [{bucket:20}] {desc}  «{detail}»"
                  f"  {'  '.join(marks)}")
        # dual-axis with DIFFERENT transforms L vs R (the untested placement shape)
        if spec.axis_mode == "dual":
            non_shared = {ax: b for ax, b in per_axis.items() if ax in ("L", "R")}
            allb = set().union(*non_shared.values()) if non_shared else set()
            if "L" in non_shared and "R" in non_shared and \
                    non_shared["L"] != non_shared["R"]:
                shapes.add("dual-axis, DIFFERENT L/R transforms")
            elif allb - {"level"}:
                shapes.add("dual-axis (transformed)")
            else:
                shapes.add("dual-axis (level)")

    print("\n" + "-" * 78)
    print("  TRANSFORM-TYPE TALLY:")
    for b, n in sorted(bucket_tally.items(), key=lambda kv: -kv[1]):
        mark = "  <-- transform still untested end-to-end" if b in UNTESTED else ""
        print(f"    {b:24} {n}{mark}")
    hit = UNTESTED & set(bucket_tally)
    print("\n  CHART SHAPES present: " + (", ".join(sorted(shapes)) or "—"))
    # the shapes still thin/untested after June 9
    STILL_THIN = {"summed/composite formula", "dual-axis, DIFFERENT L/R transforms",
                  "difa% annualized", "index-rebase"}
    new_shapes = {s for s in shapes
                  if s in STILL_THIN or s.startswith("bracket-lag")}
    print("-" * 78)
    if hit:
        print(f"  Adds untested TRANSFORMS: {', '.join(sorted(hit))}")
    if new_shapes:
        print(f"  Adds untested SHAPES: {', '.join(sorted(new_shapes))}")
    if not hit and not new_shapes:
        print("  REDUNDANT — only level/MA/yoy/zs + already-tested shapes; adds NO new "
              "untested surface. Pick a different backdate (summed / dual-different / "
              "difa% / index-rebase / bracket-lag).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
