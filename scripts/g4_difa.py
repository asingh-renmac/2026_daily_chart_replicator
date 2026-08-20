"""G4 #2 — sample1_chart1: DIFA% annualization exponent (+ MOVV).

  navy/left : difa%(movv(NMSCNX@USECON,3),3)   Mfrs' Shipments NDCGxA (SA, Mil$)
  teal/right: difa%(movv(NMOCNX@USECON,3),3)   Mfrs' New Orders NDCGxA (SA, Mil$)
Both % on a shared -60..40 scale. No recession / no r-box on the original.
difa%(.,3) = ((x/x.shift(3))**(12/3) - 1)*100  → the npy/n = 12/3 = 4 exponent.
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
SHOT = ROOT / "notes" / "sample1_chart1.png"
WIN = (pd.Timestamp("1992-06-30"), pd.Timestamp("2026-04-30"))
COMMON = "M"


def main():
    ship = G.pull("NMSCNX", "USECON", "M")
    orders = G.pull("NMOCNX", "USECON", "M")
    smap = {ship.code: ship, orders.code: orders}

    n_ship = T.parse(f"difa%(movv({ship.code},3),3)")
    n_ord = T.parse(f"difa%(movv({orders.code},3),3)")
    s_ship = T.finish(n_ship, lag=0, window=WIN, series_map=smap, common_freq=COMMON)
    s_ord = T.finish(n_ord, lag=0, window=WIN, series_map=smap, common_freq=COMMON)

    print(f"  shipments difa%: last {s_ship.dropna().index[-1].date()} = {s_ship.dropna().iloc[-1]:.2f}")
    print(f"  orders    difa%: last {s_ord.dropna().index[-1].date()} = {s_ord.dropna().iloc[-1]:.2f}")

    # clean legends; both series share the SAME transform → transform goes in the
    # SUBTITLE only, not the legend.
    spec = RenderSpec(
        title="Core capital goods orders and shipments momentum remains strong",
        subtitle="Non-defense capital goods excluding aircraft, 3mma, 3m% chg saar",
        axis_mode="shared", left_label="%",
        x_range=WIN, y_left=(-60, 40),
    )
    render([PlotSeries("New orders", s_ord, "L", color="#6B6B6B"),
            PlotSeries("Value of shipments", s_ship, "L", color="#621909")],
           spec, save_path=str(OUT / "difa_reconstruction.png"))
    print(f"  wrote {OUT/'difa_reconstruction.png'}")

    calib = G.calibrate(
        str(SHOT), x_ticks=None,
        x_years=[1995, 2000, 2005, 2010, 2015, 2020, 2025],
        yL_ticks=None, yL_vals=[40, 20, 0, -20, -40, -60],
        yR_vals=[40, 20, 0, -20, -40, -60],
    )
    G.overlay(calib,
              [{"series": s_ship, "axis": "L", "color": "#E0218A",
                "label": "recon: shipments difa%"},
               {"series": s_ord, "axis": "R", "color": "#FF8C00",
                "label": "recon: orders difa%"}],
              str(OUT / "difa_overlay.png"),
              title="G4 #2 DIFA% — reconstruction (magenta/orange) over original",
              debug_grid=True)


if __name__ == "__main__":
    main()
