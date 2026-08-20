"""
validate_0528.py — render the two STILL-UNTESTED-END-TO-END transform seams that the
2026-05-28 inbox uniquely carries: **summed / composite formula (A+B+C)** and
**difa%-annualized**. Both render paths have NEVER fired through the real renderer
(same category as the transform-drop bug — G3 was proven standalone at G4, but the
renderer↔G3 seam for these two shapes was never exercised end-to-end).

It drives the PRODUCTION render seam directly — `build_chart.render_row` (pull each
bound mnemonic via DLX → evaluate the formula through the signed-off G3 engine → lift
to common freq → clip to derived end → RenMac render). The rows here are FORMULA-only
charts from 2026-05-28 (embedded mnemonics, so no ticker guessing); the mnemonics'
databases were confirmed live (CBDB quarterly, USECON monthly).

Not a replacement for a live run_daily backdate — a focused, reliable render of the
two seams Aman needs to eyeball.

USAGE
  python scripts/validate_0528.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

import build_chart as BC        # noqa: E402  (the render seam under test)

OUT = ROOT / "outputs" / "g8_validation_0528"
OUT.mkdir(parents=True, exist_ok=True)


def _slot(formula, codes, resolved, label, axis="shared"):
    return {"status": "resolved", "formula": formula, "codes": codes,
            "resolved": resolved, "axis": axis, "base_descriptor": label, "lag": None}


# ── Chart A — SUMMED, the A+B+C (3-addend, nested) seam ───────────────────────
# CEO Confidence: share of CEOs expecting employment to expand >1% over next 12
# months = sum of the 1-2% + 2-3% + >3% diffusion buckets (BEEM1+(BEEM2+BEEM3)).
# This is the untested composite-formula render (the nested BinOp walked by G3).
CHART_A = {
    "chart_spec": {"sample_start": "2015", "axis_mode": "shared",
                   "recession_shading": False},
    "series": [_slot("BEEM1+(BEEM2+BEEM3)",
                     ["BEEM1@CBDB", "BEEM2@CBDB", "BEEM3@CBDB"],
                     "BEEM1+(BEEM2+BEEM3)",
                     "CEOs expecting employment expansion over next 12 months")],
    "chosen_title": "CEO confidence in hiring holds up",
    "subtitle": "Conference Board CEO survey, share expecting >1% employment "
                "growth, quarterly",
}

# ── Chart B — DIFA%-ANNUALIZED, two lines ─────────────────────────────────────
# 3-month/3-month annualized % change of core capital-goods orders (the DIFA% npy/n=4
# seam; the GFC trough near −49% is the visual check). difa%(movv(X,3),3) each.
CHART_B = {
    "chart_spec": {"sample_start": "2005", "axis_mode": "shared",
                   "recession_shading": True},
    "series": [_slot("difa%(movv(NMSCNX,3),3)", ["NMSCNX@USECON"], "NMSCNX@USECON",
                     "Nondefense capital goods orders ex aircraft"),
               _slot("difa%(movv(NMOCNX,3),3)", ["NMOCNX@USECON"], "NMOCNX@USECON",
                     "Nondefense capital goods orders")],
    "chosen_title": "Capital-goods orders momentum cools",
    "subtitle": "manufacturers' new orders",
}

# ── Chart C — SUMMED (2-addend) composites, two lines ─────────────────────────
# CEOs raising vs cutting capital-spending plans — each line a summed pair of the
# revision buckets. A second, independent summed-render check.
CHART_C = {
    "chart_spec": {"sample_start": "2020", "axis_mode": "shared",
                   "recession_shading": False},
    "series": [_slot("BECMU+BECMO", ["BECMU@CBDB", "BECMO@CBDB"], "BECMU+BECMO",
                     "CEOs raising capital-spending plans"),
               _slot("BECMM+BECMD", ["BECMM@CBDB", "BECMD@CBDB"], "BECMM+BECMD",
                     "CEOs cutting capital-spending plans")],
    "chosen_title": "CEOs still leaning toward more capex",
    "subtitle": "Conference Board CEO survey, share revising 12-month capex plans, "
                "quarterly",
}

JOBS = [("A_summed_ABC_employment", CHART_A, "SUMMED (A+B+C, nested)"),
        ("B_difa_annualized_capgoods", CHART_B, "DIFA%-ANNUALIZED"),
        ("C_summed_capex_plans", CHART_C, "SUMMED (2-addend x2)")]


def main() -> int:
    print("=" * 74)
    print("VALIDATE 2026-05-28 — untested render seams: SUMMED + DIFA%-ANNUALIZED")
    print("  driving the production seam build_chart.render_row (DLX pull → G3 → render)")
    print("=" * 74)
    rc = 0
    for name, row, seam in JOBS:
        path = str(OUT / f"{name}.png")
        try:
            info = BC.render_row(row, path)
            print(f"\n[{seam}]  {row['chosen_title']!r}")
            for p in info["plotted"]:
                print(f"    plotted: {p}")
            print(f"    derived end (latest plotted period): {info['end']}")
            print(f"    -> {path}")
        except Exception as exc:
            rc = 1
            print(f"\n[{seam}]  RENDER FAILED — {type(exc).__name__}: {exc}")
    print("\n" + "-" * 74)
    print("DONE — eyeball the PNGs in outputs/g8_validation_0528/")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
