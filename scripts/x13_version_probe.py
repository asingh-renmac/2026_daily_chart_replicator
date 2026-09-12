"""Run the vendored X-13 wrapper and print a fingerprint of its output.

Point two interpreters at this and diff the output: identical fingerprints mean a
statsmodels upgrade is behaviour-preserving on the path this lane actually uses, which
is the only question a version pin is really asking. Synthetic input so it needs no DLX
and gives the same answer on any host.

    <venv-a>\\Scripts\\python.exe scripts\\x13_version_probe.py
    <venv-b>\\Scripts\\python.exe scripts\\x13_version_probe.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
import statsmodels

import x13_seasonal_adjust as X13

X13DIR = next((c for c in (r"C:\Users\asingh\tools\winx13\x13as",
                           r"C:\Users\madz\Work\asingh\tools\winx13\x13as")
               if Path(c).is_dir()), None)

# Deterministic: a trend, a fixed seasonal, and noise from a fixed seed. Nothing here
# may vary between runs or the fingerprints compare noise rather than behaviour.
idx = pd.date_range("2010-01-31", periods=180, freq="ME")
t = np.arange(len(idx))
seasonal = 4.0 * np.sin(2 * np.pi * t / 12) + 1.5 * np.cos(2 * np.pi * t / 6)
rng = np.random.default_rng(20260912)
series = pd.Series(100 + 0.25 * t + seasonal + rng.normal(0, 0.6, len(idx)),
                   index=idx, name="value")

print(f"statsmodels {statsmodels.__version__}  (python {sys.version.split()[0]})")
X13.setup_x13(x13_dir=X13DIR, quiet=True)

for label, kw in (("additive/no-TD", dict(log=False, trading=False, outlier=True)),
                  ("log/TD",         dict(log=True,  trading=True,  outlier=False))):
    try:
        out = X13.seasonal_adjust(series, freq="M", **kw)
        # Rounded so a last-bit float difference between builds is not read as a
        # behaviour change; 6dp is far finer than anything that reaches a chart.
        print(f"  {label:15} n={len(out)} "
              f"first={out.iloc[0]:.6f} last={out.iloc[-1]:.6f} "
              f"mean={out.mean():.6f} sd={out.std():.6f}")
    except Exception as exc:
        print(f"  {label:15} FAILED {type(exc).__name__}: {exc}")

# The HP filter is the OTHER statsmodels caller in this lane (transforms._ev_hp), and
# unlike X-13 it is arithmetic done inside statsmodels rather than shelled out to a
# binary -- so it is the one that a version change could actually move. Checking only the
# X-13 path would have proved the easy half.
import statsmodels.api as sm

cyc, trend = sm.tsa.filters.hpfilter(series, lamb=1600)
print(f"  {'hpfilter(1600)':15} cyc_first={cyc.iloc[0]:.6f} cyc_last={cyc.iloc[-1]:.6f} "
      f"cyc_sd={cyc.std():.6f} trend_last={trend.iloc[-1]:.6f}")
