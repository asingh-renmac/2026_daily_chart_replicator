"""Reproduce the `sa(diff%(R622))` chart and compare the two orderings.

R622 is a DLX local alias for jcsmhpm@usna — "Household Consumption Expenditures:
Hospitals  Price Index (SA, 2017=100)". The base series is ALREADY SA at source, so the
outer sa() is stripping RESIDUAL seasonality out of the month-over-month change. That is
why the pipeline honors the written order instead of rewriting it to diff%(sa(X)): the
two are different operations here, not a tidy-up.

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

CODE, DB, FREQ = "jcsmhpm", "usna", "M"
START = pd.Timestamp("2015-06-30")       # the source chart opens here

smap = {CODE: G.pull(CODE, DB, FREQ)}
raw = smap[CODE].values
END = raw.index.max()
WIN = (START, END)
print(f"\n{CODE}@{DB}  {raw.index.min().date()}..{END.date()}  n={len(raw)}\n")

variants = {
    "diff%(X)        raw MoM": f"diff%({CODE})",
    "sa(diff%(X))    as charted": f"sa(diff%({CODE}))",
    "diff%(sa(X))    reordered": f"diff%(sa({CODE}))",
}

out = {}
for name, formula in variants.items():
    ev = T.Evaluator(smap, WIN)
    s = T.finish(T.parse(formula), 0, WIN, smap, FREQ, evaluator=ev)
    out[name] = s
    print(f"{name:28s} sd={s.std():.4f}  last={s.iloc[-1]:+.4f} @ {s.index[-1].date()}")
    print(f"{'':28s} label: {B.transform_label(formula, 'month')!r}")
    for line in ev.interp_log:
        print(f"{'':28s} {line}")

df = pd.DataFrame(out).dropna()
print(f"\ncorrelations over {df.index[0].date()}..{df.index[-1].date()} (n={len(df)}):")
print(df.corr().round(4).to_string())

a, b = df.iloc[:, 1], df.iloc[:, 2]
print(f"\nsa(diff%) vs diff%(sa):  r={a.corr(b):.4f}  "
      f"max|diff|={np.abs(a - b).max():.4f}pp  mean|diff|={np.abs(a - b).mean():.4f}pp")

# How much seasonality was actually removed from the already-SA source series?
resid = df.iloc[:, 0]
print(f"\nresidual seasonality removed: sd {resid.std():.4f} -> {a.std():.4f} "
      f"({100 * (1 - a.std() / resid.std()):.1f}% of variance in sd terms)")
