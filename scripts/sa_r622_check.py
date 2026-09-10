"""Reproduce the `sa(diff%(R622))` chart's SA line and compare the two orderings.

R622 is a DLX LOCAL expression variable, not a Haver mnemonic — DLX names calculation
rows R1, R2, ... so it cannot be resolved from outside the workbook. The chart pairs a
BLS hospital PPI with BEA's hospitals PCE price index (note "Sources: BLS, BEA/Haver"),
and the sa() wrapper is on the PPI leg only: the BEA series is published SA and gets no
wrapper. p512101@ppi ("PPI: Hospital Inpatient Care", NSA) stands in for the PPI leg
here; jcsmhpm@usna is the BEA leg, shown unadjusted for contrast.

This is the ordering the house SA rule warns about — sa() applied to a growth rate of an
NSA series, rather than to the level. The pipeline honors what is written and says so in
`interp_log`; the numbers below are what that choice costs.

    python scripts/sa_r622_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np                       # noqa: E402
import pandas as pd                      # noqa: E402

import g4_lib as G                       # noqa: E402
import transforms as T                   # noqa: E402
import build_chart as B                  # noqa: E402

PPI, PCE, DB_PPI, DB_PCE, FREQ = "p512101", "jcsmhpm", "ppi", "usna", "M"
START = pd.Timestamp("2015-06-30")       # the source chart opens here

smap = {PPI: G.pull(PPI, DB_PPI, FREQ), PCE: G.pull(PCE, DB_PCE, FREQ)}
END = min(smap[PPI].values.index.max(), smap[PCE].values.index.max())
WIN = (START, END)
for k, v in smap.items():
    print(f"  {k}: {v.values.index.min().date()}..{v.values.index.max().date()} "
          f"n={len(v.values)}")
print()

variants = {
    "PPI  diff%(X)      NSA, raw MoM": f"diff%({PPI})",
    "PPI  sa(diff%(X))  as charted  ": f"sa(diff%({PPI}))",
    "PPI  diff%(sa(X))  reordered   ": f"diff%(sa({PPI}))",
    "PCE  diff%(X)      already SA  ": f"diff%({PCE})",
}

out = {}
for name, formula in variants.items():
    ev = T.Evaluator(smap, WIN)
    s = T.finish(T.parse(formula), 0, WIN, smap, FREQ, evaluator=ev)
    out[name] = s
    print(f"{name}  sd={s.std():.4f}  last={s.iloc[-1]:+.4f} @ {s.index[-1].date()}")
    print(f"{'':33s} label: {B.transform_label(formula, 'month')!r}")
    for line in ev.interp_log:
        print(f"{'':33s} {line}")

df = pd.DataFrame(out).dropna()
raw, lit, reo = df.iloc[:, 0], df.iloc[:, 1], df.iloc[:, 2]
print(f"\nover {df.index[0].date()}..{df.index[-1].date()} (n={len(df)}):")
print(f"  seasonality removed (sd):  raw {raw.std():.4f} -> "
      f"sa(diff%) {lit.std():.4f} ({100 * (1 - lit.std() / raw.std()):.1f}%), "
      f"diff%(sa) {reo.std():.4f} ({100 * (1 - reo.std() / raw.std()):.1f}%)")
print(f"  the two orderings:         r={lit.corr(reo):.4f}  "
      f"max|diff|={np.abs(lit - reo).max():.4f}pp  "
      f"mean|diff|={np.abs(lit - reo).mean():.4f}pp")
print(f"  PPI(SA) vs PCE:            r={lit.corr(df.iloc[:, 3]):.4f}")
