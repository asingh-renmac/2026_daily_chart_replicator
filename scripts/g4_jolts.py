"""G4 #1 — JOLTS Quits Rate [-4] (M) vs ECI Wages & Salaries (Q), the end-to-end
pipeline proof: lag + mixed-freq interp + quarter-anchor.

Tickers (get_series-confirmed):
  navy/left  : LJQTPA@USECON  JOLTS: Quits Rate: Total (SA, %)      movv(.,9) [-4]
  teal/right : LSWP@USECON    ECI: Wages & Salaries: Private (SA)   yryr%
Original screenshot has NO recession bands / NO r-box → not drawn (§7.1).
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

OUT = ROOT / "outputs" / "g4"
OUT.mkdir(parents=True, exist_ok=True)
SHOT = ROOT / ".g4_inspect" / "sample3_img3.png"

WIN = (pd.Timestamp("2003-01-31"), pd.Timestamp("2026-04-30"))
COMMON = "M"


def main():
    jolts = G.pull("LJQTPA", "USECON", "M")
    eci = G.pull("LSWP", "USECON", "Q")

    smap = {jolts.code: jolts, eci.code: eci}

    # navy: 9-month MA of JOLTS quits, lagged [-4] (shift forward 4 months)
    navy_node = T.parse(f"movv({jolts.code},9)")
    navy = T.finish(navy_node, lag=4, window=WIN, series_map=smap, common_freq=COMMON)

    # teal: YoY% of ECI (native quarterly shift(4)) → interpolate up to monthly
    teal_node = T.parse(f"yryr%({eci.code})")
    teal = T.finish(teal_node, lag=0, window=WIN, series_map=smap, common_freq=COMMON)

    # edge diagnostics (§8.2 / §8.10)
    c_navy = T.check_first_valid(navy, navy_node, WIN, smap, COMMON)
    c_teal = T.check_first_valid(teal, teal_node, WIN, smap, COMMON)
    print(f"  navy first-valid: {navy.first_valid_index().date()} ({c_navy.detail}) ok={c_navy.ok}")
    print(f"  teal first-valid: {teal.first_valid_index().date()} ({c_teal.detail}) ok={c_teal.ok}")
    print(f"  navy last-valid : {navy.last_valid_index().date()}  val={navy.dropna().iloc[-1]:.3f}")
    print(f"  teal last-valid : {teal.last_valid_index().date()}  val={teal.dropna().iloc[-1]:.3f}")
    # record the ECI quarter-anchor: where do the interpolated monthly knots land?
    eci_native_last = eci.values.last_valid_index()
    print(f"  ECI native last obs (period-END anchor): {eci_native_last.date()}")

    # standalone RenMac reconstruction (clean description legends; the two series
    # carry DIFFERENT transforms, so each transform stays in its legend entry; the
    # [-4] lag tag is preserved)
    spec = RenderSpec(
        title="Weak quits point to cooler wage growth ahead",
        subtitle="quits rate lagged 4 months; both percent",
        axis_mode="dual", left_label="%", right_label="%",
        x_range=WIN,
    )
    render([PlotSeries("JOLTS quits rate, 9-mo MA [-4]", navy, "L", color="#621909"),
            PlotSeries("ECI wages & salaries, YoY", teal, "R", color="#6B6B6B")],
           spec, save_path=str(OUT / "jolts_reconstruction.png"))
    print(f"  wrote {OUT/'jolts_reconstruction.png'}")

    # overlay on the original screenshot at the same x-scale
    calib = G.calibrate(
        str(SHOT),
        x_ticks=None, x_years=[2005, 2010, 2015, 2020, 2025, 2030],
        yL_ticks=None, yL_vals=[3.2, 2.8, 2.4, 2.0, 1.6, 1.2],
        yR_vals=[6, 5, 4, 3, 2, 1],
    )
    G.overlay(calib,
              [{"series": navy, "axis": "L", "color": "#E0218A",
                "label": "recon: JOLTS 9mMA [-4]"},
               {"series": teal, "axis": "R", "color": "#FF8C00",
                "label": "recon: ECI YoY%"}],
              str(OUT / "jolts_overlay.png"),
              title="G4 #1 JOLTS [-4] vs ECI — reconstruction (magenta/orange) over original",
              debug_grid=True)


if __name__ == "__main__":
    main()
