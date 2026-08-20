"""
render_check.py — eyeball the two layout revisions:
  (1) tight, EQUAL left/right margins (plot fills the width) — single-axis; and the
      slightly-wider right margin on a DUAL-axis chart so the R-axis labels clear;
  (2) the title-less render (`none` title-round-trip choice): no title, no descriptive
      subtitle, but the transform label is still shown.

Drives the production seam build_chart.render_row (DLX pull → G3 → RenMac render) with
mnemonics whose databases are confirmed live (USECON monthly).

USAGE
  python scripts/render_check.py
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

OUT = ROOT / "outputs" / "g8_layout_check"
OUT.mkdir(parents=True, exist_ok=True)


def _slot(formula, codes, resolved, label, axis="shared"):
    return {"status": "resolved", "formula": formula, "codes": codes,
            "resolved": resolved, "axis": axis, "base_descriptor": label, "lag": None}


# ── single-axis: two capital-goods orders, difa%-annualized (shared axis) ─────
SINGLE = {
    "chart_spec": {"sample_start": "2005", "axis_mode": "shared",
                   "recession_shading": True},
    "series": [_slot("difa%(movv(NMSCNX,3),3)", ["NMSCNX@USECON"], "NMSCNX@USECON",
                     "Nondefense capital goods orders ex aircraft"),
               _slot("difa%(movv(NMOCNX,3),3)", ["NMOCNX@USECON"], "NMOCNX@USECON",
                     "Nondefense capital goods orders")],
    "chosen_title": "Single-axis — tight equal margins",
    "subtitle": "manufacturers' new orders",
}

# ── dual-axis: LEVELS on L and R (different magnitudes) → R-axis tick labels ──
DUAL = {
    "chart_spec": {"sample_start": "2010", "axis_mode": "dual",
                   "recession_shading": False},
    "series": [_slot("NMOCNX", ["NMOCNX@USECON"], "NMOCNX@USECON",
                     "Nondefense capital goods orders", axis="L"),
               _slot("NMSCNX", ["NMSCNX@USECON"], "NMSCNX@USECON",
                     "Ex-aircraft (right)", axis="R")],
    "chosen_title": "Dual-axis — R-axis labels must clear",
    "subtitle": "manufacturers' new orders, $ bil",
}

# ── title-less: `none` choice → no title, no descriptive subtitle, transform kept
TITLELESS = {
    "chart_spec": {"sample_start": "2005", "axis_mode": "shared",
                   "recession_shading": True},
    "series": [_slot("difa%(movv(NMSCNX,3),3)", ["NMSCNX@USECON"], "NMSCNX@USECON",
                     "Nondefense capital goods orders ex aircraft")],
    "chosen_title": "",            # `none` → title-less
    "subtitle": "",                # no descriptive subtitle …
    "no_title": "1",               # … but the transform label is still shown
}

JOBS = [("single_axis_equal_margins", SINGLE, "single-axis, equal margins"),
        ("dual_axis_right_labels", DUAL, "dual-axis, R-labels clear"),
        ("titleless_transform_kept", TITLELESS, "title-less (none), transform kept")]


def main() -> int:
    print("=" * 74)
    print("LAYOUT CHECK — equal margins (single/dual) + title-less render")
    print("=" * 74)
    rc = 0
    for name, row, what in JOBS:
        path = str(OUT / f"{name}.png")
        try:
            info = BC.render_row(row, path)
            print(f"\n[{what}]")
            for p in info["plotted"]:
                print(f"    plotted: {p}")
            print(f"    -> {path}")
        except Exception as exc:
            rc = 1
            print(f"\n[{what}]  RENDER FAILED — {type(exc).__name__}: {exc}")
    print("\n" + "-" * 74)
    print("DONE — eyeball the PNGs in outputs/g8_layout_check/")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
