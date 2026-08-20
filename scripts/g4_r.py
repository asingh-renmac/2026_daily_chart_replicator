"""G4 #5 — sample7_1: ZS + labeling check (NO r-box — removed by request).

  CEO confidence : zs(BECE@CBDB)          CEO Bus Conf: Current Conditions (Q)
  Corp profits   : zs(yryr%(YCP@USECON))  Corporate Profits w/ IVA & CCAdj (Q)
SCALAR axis -4..4, recession bands present (RECESSM2==1). The two series carry
DIFFERENT transforms (CEO = level z-score; profits = YoY z-score) → "YoY" stays
in the profits legend entry to disambiguate; the z-score lives in the subtitle.
"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import transforms as T          # noqa: E402
import g4_lib as G              # noqa: E402
from render import PlotSeries, RenderSpec, render  # noqa: E402

OUT = ROOT / "outputs" / "g4"; OUT.mkdir(parents=True, exist_ok=True)
SHOT = ROOT / "notes" / "sample7_chart1.png"
WIN = (pd.Timestamp("1976-06-30"), pd.Timestamp("2026-06-30"))
COMMON = "Q"


def main():
    ceo = G.pull("BECE", "CBDB", "Q")
    prof = G.pull("YCP", "USECON", "Q")
    smap = {ceo.code: ceo, prof.code: prof}

    n_ceo = T.parse(f"zs({ceo.code})")
    n_prof = T.parse(f"zs(yryr%({prof.code}))")
    s_ceo = T.finish(n_ceo, lag=0, window=WIN, series_map=smap, common_freq=COMMON)
    s_prof = T.finish(n_prof, lag=0, window=WIN, series_map=smap, common_freq=COMMON)
    print(f"  CEO conf zs last={s_ceo.dropna().iloc[-1]:+.2f}; "
          f"profits zs last={s_prof.dropna().iloc[-1]:+.2f}")

    spec = RenderSpec(
        title="CEO confidence broadly tracks corporate profit growth over time",
        subtitle="Z-score", axis_mode="shared", left_label="",
        recession=G.pull("RECESSM2", "USECON", "M").values,
        x_range=WIN, y_left=(-4, 4),
    )
    render([PlotSeries("CEO confidence", s_ceo, color="#6B6B6B"),
            PlotSeries("Corporate profits, YoY", s_prof, color="#621909")],
           spec, save_path=str(OUT / "sample7_reconstruction.png"))
    print(f"  wrote {OUT/'sample7_reconstruction.png'}")

    calib = G.calibrate(
        str(SHOT), x_ticks=None,
        x_years=[1980, 1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020, 2025],
        yL_ticks=None, yL_vals=[4, 2, 0, -2, -4], yR_vals=[4, 2, 0, -2, -4],
    )
    G.overlay(calib,
              [{"series": s_ceo, "axis": "L", "color": "#E0218A",
                "label": "recon: CEO confidence"},
               {"series": s_prof, "axis": "L", "color": "#FF8C00",
                "label": "recon: corp profits YoY"}],
              str(OUT / "sample7_overlay.png"),
              title="G4 #5 sample7 ZS — reconstruction over original (no r-box)",
              debug_grid=False)


if __name__ == "__main__":
    main()
