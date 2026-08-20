"""G4 #4 — sample2_chart2: ZS window-mu/sigma (+ nested yryr%) + recession shading.

Core-PCE decomposition, each z-scored over the DISPLAY window:
  navy : zs(yryr%(JCSRM@USNA))     PCE Housing chain price index
  teal : zs(yryr%(JCSXEHM@USECON)) PCE Core Services ex Housing chain price index
  red  : zs(yryr%(JCGXFEM@USNA))   PCE Core Goods (ex food&energy) chain price index
SCALAR axis -4..6 both sides. Recession bands present (RECESSM2==1 runs).
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
SHOT = ROOT / "notes" / "sample2_chart2.png"
WIN = (pd.Timestamp("1998-01-31"), pd.Timestamp("2026-04-30"))
COMMON = "M"


def main():
    hous = G.pull("JCSRM", "USNA", "M")
    svc = G.pull("JCSXEHM", "USECON", "M")
    good = G.pull("JCGXFEM", "USNA", "M")
    rec = G.pull("RECESSM2", "USECON", "M")
    smap = {hous.code: hous, svc.code: svc, good.code: good}

    out = {}
    for label, sd in [("Housing", hous), ("Core Svcs ex Hsg", svc), ("Core Goods", good)]:
        node = T.parse(f"zs(yryr%({sd.code}))")
        out[label] = (node, T.finish(node, lag=0, window=WIN, series_map=smap,
                                     common_freq=COMMON))
        s = out[label][1].dropna()
        print(f"  {label:18s} zs: span {s.index[0].date()}..{s.index[-1].date()} "
              f"last={s.iloc[-1]:+.2f}  mean={s.mean():+.3f} std={s.std():.3f}")

    # clean legends; all three share the SAME transform → transform in SUBTITLE.
    spec = RenderSpec(
        title="Housing inflation normalizes while core goods and services stay elevated",
        subtitle="z-score of y/y %",
        axis_mode="shared", left_label="",
        recession=rec.values, x_range=WIN, y_left=(-4, 6),
    )
    render([PlotSeries("Housing", out["Housing"][1], color="#6B6B6B"),
            PlotSeries("Core services", out["Core Svcs ex Hsg"][1], color="#000000"),
            PlotSeries("Core goods", out["Core Goods"][1], color="#621909")],
           spec, save_path=str(OUT / "zs_reconstruction.png"))
    print(f"  wrote {OUT/'zs_reconstruction.png'}")

    calib = G.calibrate(
        str(SHOT), x_ticks=None,
        x_years=[2000, 2005, 2010, 2015, 2020, 2025],
        yL_ticks=None, yL_vals=[6, 4, 2, 0, -2, -4],
        yR_vals=[6, 4, 2, 0, -2, -4],
    )
    G.overlay(calib,
              [{"series": out["Housing"][1], "axis": "L", "color": "#E0218A",
                "label": "recon: zs Housing"},
               {"series": out["Core Svcs ex Hsg"][1], "axis": "L", "color": "#FF8C00",
                "label": "recon: zs Core Svcs"},
               {"series": out["Core Goods"][1], "axis": "L", "color": "#00B050",
                "label": "recon: zs Core Goods"}],
              str(OUT / "zs_overlay.png"),
              title="G4 #4 ZS + recession — reconstruction over original; red bands = RECESSM2==1",
              debug_grid=True, recession=rec.values)


if __name__ == "__main__":
    main()
