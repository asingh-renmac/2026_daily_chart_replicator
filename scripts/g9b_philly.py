"""G9b step 2 — the first real chat-lane target (§13.1).

`zs(yryr%(IP))` against the Philly Fed Mfg Business Outlook current-activity diffusion
index (z-scored), shared scalar axis, recession bands, sources FRB + FRBPHI.

Both tickers come from `resolve_series`; none are hand-typed. The point of the exercise
is to watch what the resolver does with what is printed on the chart.

    python scripts/g9b_philly.py [--render]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Windows consoles default to cp1252, which cannot encode the arrows/dashes below.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from haver_chart import lane  # noqa: E402

READS = [
    dict(base_descriptor="Industrial Production",
         formula="zs(yryr%(IP))",              # printed on the chart, passed verbatim
         sa_hint="sa", freq_hint="monthly", axis="shared"),
    dict(base_descriptor="Philly Fed Mfg Business Outlook: Current Activity "
                         "Diffusion Index",
         applied_transform="Z-Score",
         sa_hint="sa", freq_hint="monthly", axis="shared"),
]


def show(i: int, r: dict) -> None:
    print(f"\n── series {i}: {r['_read']['base_descriptor']!r}")
    if r["_read"].get("formula"):
        print(f"   formula read : {r['_read']['formula']}")
    if r["_read"].get("applied_transform"):
        print(f"   transform    : {r['_read']['applied_transform']}")
    print(f"   STATUS       : {r['status'].upper()}")
    print(f"   resolved     : {r['resolved']}   codes={r['codes']}")
    print(f"   via          : {r['via']!r}")
    print(f"   similarity   : {r['similarity']}   exact_token_match={r['exact_token_match']}")
    print(f"   freq/agg     : {r['freq_resolved'] or '-'} / {r['agg_resolved'] or '-'}")
    if r["reason"]:
        print(f"   reason       : {r['reason']}")
    print("   candidates (top 3):")
    for c in r["candidates"] or []:
        print(f"     {c['similarity']:<8} exact={str(c['exact_token_match']):<5} "
              f"{c['code']:<18} {c['descriptor']}")
    if not r["candidates"]:
        print("     (none returned)")


def main() -> int:
    print("=" * 78)
    print("G9b step 2 — Philly Fed chart, resolved through resolve_series")
    print("=" * 78)
    results = []
    for i, read in enumerate(READS):
        r = lane.resolve_one(**read)
        r["_read"] = read
        show(i, r)
        results.append(r)

    if any(r["status"] != "resolved" for r in results):
        print("\nAt least one series PARKED — that is a normal outcome. Pick from the "
              "candidates above before rendering.")
        return 1

    if "--render" not in sys.argv:
        print("\nBoth resolved. Re-run with --render to draw it.")
        return 0

    slots = [dict(results[0]["slot"], proposed_legend="Industrial Production (y/y %, z-score)"),
             dict(results[1]["slot"],
                  proposed_legend="Philly Fed Mfg Current Activity Diffusion Index (SA, z-score)")]
    out = lane.render(
        slots, filename="philly_fed_ip",
        title="Manufacturing output and the Philly Fed survey",
        subtitle="Z-score", st_force=True,
        sample_start="1998", axis_mode="shared", recession_shading=True,
        left_min=-6.0, left_max=4.0)
    print("\n" + "=" * 78)
    print(f"RENDERED  {out['path']}")
    print(f"  plotted : {json.dumps(out['plotted'], indent=12)}")
    print(f"  end     : {out['end']}")

    # Values read off the SOURCE Haver chart's right edge. Tolerance is ~0.15 of a
    # gridline interval (gridlines are 2 z-units apart), which is about as precisely
    # as an endpoint can be read off a screenshot with no printed flag.
    print("\n  validate.check_last_value (source chart's right edge):")
    for r in lane.last_value_check(
            out, {"Industrial Production": 0.15, "Philly Fed": 2.4}, value_tol=0.3):
        print(f"    {r['label']}")
        print(f"      last {r['last_date']}  reconstructed={r['reconstructed']}  "
              f"source-chart={r['printed']}")
        print(f"      -> {r['check']}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
