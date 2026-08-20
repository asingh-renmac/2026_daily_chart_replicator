"""G4 #3 — sample1_chart3: YRYR% (direct, non-nested).

  shipments : yryr%(NMSCNX@USECON)    Mfrs' Shipments NDCGxA
  PPI       : yryr%(pa413121@usecon)  PPI: Final Dem Pvt Capital Equipment for
                                      Manufacturing Industries (SA, 1982=100)

PPI resolved BY DEFINITION from the g4_desired reference, which names the navy
line "PPI, Final Demand, 413121, Private Capital Equipment for Manufacturing
Industries" = NAICS 413121 = pa413121@usecon (get_series-confirmed). The earlier
shape-pick (sp3210) was the circular C1 trap and is discarded. Applied-INDEX is
NOT exercised here (Finding A). Both series share YoY → transform in SUBTITLE.

Park guard (Defect 2): if a required ticker were still unresolved, the WHOLE
chart parks via render.park_chart — never a partial render.
"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import transforms as T          # noqa: E402
import g4_lib as G              # noqa: E402
from render import PlotSeries, RenderSpec, render, park_chart  # noqa: E402

OUT = ROOT / "outputs" / "g4"; OUT.mkdir(parents=True, exist_ok=True)
SHOT = ROOT / "notes" / "sample1_chart3.png"
WIN = (pd.Timestamp("1996-01-31"), pd.Timestamp("2026-04-30"))
COMMON = "M"

# (ticker, database, freq, legend description). None ticker => unresolved => park.
SERIES = [
    ("pa413121", "USECON", "M", "PPI: capital equipment, manufacturing"),
    ("NMSCNX", "USECON", "M", "Core capital goods shipments"),
]


def yoy(code, db, smap):
    node = T.parse(f"yryr%({code}@{db})")
    return T.finish(node, lag=0, window=WIN, series_map=smap, common_freq=COMMON)


def main():
    unresolved = [desc for (tk, *_rest, desc) in SERIES if tk is None]
    if unresolved:
        park_chart("sample1_chart3", unresolved,
                   "PPI series not uniquely resolvable; routed to Teams")
        return

    plots = []
    colors = ["#6B6B6B", "#621909"]
    for (code, db, freq, desc), col in zip(SERIES, colors):
        sd = G.pull(code, db, freq)
        s = yoy(code, db, {sd.code: sd})
        print(f"  {desc}: last {s.dropna().index[-1].date()} = {s.dropna().iloc[-1]:.2f}")
        plots.append(PlotSeries(desc, s, "L", color=col))

    spec = RenderSpec(
        title="Higher prices flatter nominal core capital goods shipments",
        subtitle="y/y % chg", axis_mode="shared", left_label="%",
        x_range=WIN, y_left=(-25, 20),
    )
    render(plots, spec, save_path=str(OUT / "yryr_reconstruction.png"))
    print(f"  wrote {OUT/'yryr_reconstruction.png'}")

    calib = G.calibrate(
        str(SHOT), x_ticks=None,
        x_years=[2000, 2005, 2010, 2015, 2020, 2025],
        yL_ticks=None, yL_vals=[22.5, 15, 7.5, 0, -7.5, -15, -22.5],
        yR_vals=[22.5, 15, 7.5, 0, -7.5, -15, -22.5],
    )
    G.overlay(calib,
              [{"series": plots[0].series, "axis": "L", "color": "#E0218A",
                "label": "recon: PPI cap equip"},
               {"series": plots[1].series, "axis": "L", "color": "#FF8C00",
                "label": "recon: shipments"}],
              str(OUT / "yryr_overlay.png"),
              title="G4 #3 YRYR% — recon (PPI=pa413121) over original", debug_grid=False)


if __name__ == "__main__":
    main()
