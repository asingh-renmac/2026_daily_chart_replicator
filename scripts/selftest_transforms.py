"""Self-test for src/transforms.py — verifies the pinned formulas, native-freq
evaluation, BinOp (mixed-freq + scalar), ZS Bug-A, finish() Bug-B, and the
quarter->month interpolation anchor. No network; synthetic series only."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import transforms as T  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


def approx(a, b, tol=1e-9):
    return abs(float(a) - float(b)) <= tol


def m_series(vals, start="2010-01-31"):
    idx = pd.date_range(start, periods=len(vals), freq="ME")
    return pd.Series(np.asarray(vals, float), idx)


def q_series(vals, start="2010-03-31"):
    idx = pd.date_range(start, periods=len(vals), freq="QE")
    return pd.Series(np.asarray(vals, float), idx)


# --------------------------------------------------------------------------- #
print("parser")
# --------------------------------------------------------------------------- #
n = T.parse("difa%(movv(NMSCNX,3),3)")
check("nested parse is Func DIFA pct", isinstance(n, T.Func) and n.name == "DIFA" and n.pct)
check("inner is MOVV", isinstance(n.args[0], T.Func) and n.args[0].name == "MOVV")
n2 = T.parse("BEEM1+(BEEM2+BEEM3)")
check("binop parse", isinstance(n2, T.BinOp) and n2.op == "+")
check("centered flag", T.parse("movvc(X,3)").centered is True)
check("log type", T.parse("diffl(X)").log is True)
try:
    T.parse("ytd(X)"); check("YTD raises NeedPin", False)
except T.NeedPin:
    check("YTD raises NeedPin", True)
try:
    T.parse("frobnicate(X)"); check("unknown raises NeedPin", False)
except T.NeedPin:
    check("unknown raises NeedPin", True)

# --------------------------------------------------------------------------- #
print("DIF / YRYR / MOV formulas (monthly, fb=12)")
# --------------------------------------------------------------------------- #
x = m_series([100, 101, 103, 106, 110, 115, 121, 128, 136, 145, 155, 166, 178])
sm = {"X": T.SeriesData(x, "M", "X")}
ev = T.Evaluator(sm)

# DIFF(X,1) = x - x.shift(1)
r, _ = ev.ev(T.parse("diff(X,1)"))
check("DIFF", approx(r.iloc[1], 1.0) and approx(r.iloc[3], 3.0))
# DIFF%(X,1)
r, _ = ev.ev(T.parse("diff%(X,1)"))
check("DIFF%", approx(r.iloc[1], (101/100 - 1) * 100))
# DIFV(X,3) = (x - x.shift(3))/3
r, _ = ev.ev(T.parse("difv(X,3)"))
check("DIFV level", approx(r.iloc[3], (106 - 100) / 3))
# DIFV%(X,3) = ((x/x.shift(3))**(1/3)-1)*100  (geometric per-period)
r, _ = ev.ev(T.parse("difv%(X,3)"))
check("DIFV% geometric", approx(r.iloc[3], ((106 / 100) ** (1 / 3) - 1) * 100))
# DIFA%(X,3) = ((x/x.shift(3))**(12/3)-1)*100
r, _ = ev.ev(T.parse("difa%(X,3)"))
check("DIFA% annualized", approx(r.iloc[3], ((106 / 100) ** (12 / 3) - 1) * 100))
# DIFAL(X,1) = ln(x/x.shift(1))*12*100
r, _ = ev.ev(T.parse("difal(X,1)"))
check("DIFAL", approx(r.iloc[1], np.log(101 / 100) * 12 * 100))
# YRYR%(X) uses shift(12)
r, _ = ev.ev(T.parse("yryr%(X)"))
check("YRYR% shift12", approx(r.iloc[12], (178 / 100 - 1) * 100))
# MOVV(X,3) mean; MOVT sum; MOVA = sum*(12/3)
r, _ = ev.ev(T.parse("movv(X,3)"))
check("MOVV mean", approx(r.iloc[3], (101 + 103 + 106) / 3))
r, _ = ev.ev(T.parse("movt(X,3)"))
check("MOVT sum", approx(r.iloc[3], 101 + 103 + 106))
r, _ = ev.ev(T.parse("mova(X,3)"))
check("MOVA annualized total", approx(r.iloc[3], (101 + 103 + 106) * (12 / 3)))

# centered MOVV NaNs the recent edge
r, _ = ev.ev(T.parse("movvc(X,3)"))
check("MOVVC centered recent-edge NaN", np.isnan(r.iloc[-1]))
check("MOVVC centered value", approx(r.iloc[1], (100 + 101 + 103) / 3))

# --------------------------------------------------------------------------- #
print("ZS — window mu/sigma, full-range return (Bug A)")
# --------------------------------------------------------------------------- #
win = (pd.Timestamp("2010-05-31"), pd.Timestamp("2010-11-30"))
evz = T.Evaluator(sm, window=win)
z, _ = evz.ev(T.parse("zs(X)"))
w = x.loc[win[0]:win[1]]
check("ZS mu/sigma from window", approx(z.loc[win[0]], (x.loc[win[0]] - w.mean()) / w.std()))
check("ZS returns full range (Bug A: left of window not NaN)",
      not np.isnan(z.iloc[0]), f"z.iloc[0]={z.iloc[0]}")
check("ZS defined at series end too", not np.isnan(z.iloc[-1]))
# nested ZS supported: movv(zs(X),3)
zn, _ = evz.ev(T.parse("movv(zs(X),3)"))
check("nested ZS evaluates", isinstance(zn, pd.Series) and zn.notna().sum() > 0)

# --------------------------------------------------------------------------- #
print("BinOp — scalar broadcast (item 2) + mixed-freq lift")
# --------------------------------------------------------------------------- #
r, f = ev.ev(T.parse("X*100"))
check("series*scalar value", approx(r.iloc[0], 10000.0))
check("series*scalar keeps freq M", f == "M")
r, f = ev.ev(T.parse("X/1000"))
check("series/scalar", approx(r.iloc[0], 0.1) and f == "M")

# mixed freq: monthly X + quarterly Q  -> lift Q to M, result freq M
q = q_series([1, 2, 3, 4, 5], start="2010-03-31")
sm2 = {"X": T.SeriesData(x, "M", "X"), "Q": T.SeriesData(q, "Q", "Q")}
ev2 = T.Evaluator(sm2)
r, f = ev2.ev(T.parse("X+Q"))
check("mixed-freq result is monthly", f == "M")
check("mixed-freq interp logged", len(ev2.interp_log) == 1)
# interpolated quarterly value at a mid-quarter month is between its endpoints
qm = T._lift_series(q, "M")
check("Q->M anchor at quarter-end (Mar)", approx(qm.loc[pd.Timestamp("2010-03-31")], 1.0))
check("Q->M interpolated midpoint May between 1 and 2",
      1.0 < qm.loc[pd.Timestamp("2010-05-31")] < 2.0)
check("Q->M no extrapolation before first obs",
      pd.Timestamp("2010-01-31") not in qm.dropna().index)

# --------------------------------------------------------------------------- #
print("finish() — native lag then interp; Bug-B eff_inception")
# --------------------------------------------------------------------------- #
# Quarterly YoY then lift to monthly (transform-then-interpolate)
qy = q_series(np.arange(1, 30, dtype=float), start="2008-03-31")
smq = {"Q": T.SeriesData(qy, "Q", "Q")}
node = T.parse("yryr(Q)")
win2 = (pd.Timestamp("2010-03-31"), pd.Timestamp("2015-03-31"))
out = T.finish(node, 0, win2, smq, common_freq="M")
check("finish lifts quarterly YoY to monthly grid",
      out.index.freqstr is not None and out.dropna().shape[0] > 10)

# native lag: monthly [-4] shifts forward 4 months. Asserted by LABEL, not position:
# the shift moves the INDEX, so leading NaNs are no longer materialized and iloc[4] is
# now the 5th real observation rather than the first.
lagged = T.finish(T.parse("X"), 4, (x.index[0], x.index[-1] + T.shift_offset("M", 4)),
                  sm, common_freq="M")
check("lag shifts forward (value at t equals x[t-4])",
      approx(lagged.loc[x.index[4]], x.iloc[0]))
# The shift must move the INDEX, never drop observations off the end: a plain
# `Series.shift(n)` keeps the index and silently discards the newest n readings —
# on a lagged leading indicator exactly the values the chart exists to show.
check("lag CARRIES the last observation forward (none dropped)",
      approx(lagged.loc[x.index[-1] + T.shift_offset("M", 4)], x.iloc[-1]))
check("lag preserves the observation count", lagged.dropna().shape[0] == x.dropna().shape[0],
      f"{lagged.dropna().shape[0]} vs {x.dropna().shape[0]}")
# a LEAD is the same machinery with the opposite sign
led = T.finish(T.parse("X"), -4, (x.index[0] - T.shift_offset("M", 4), x.index[-1]),
               sm, common_freq="M")
check("lead shifts backward (value at t equals x[t+4])",
      approx(led.loc[x.index[0] - T.shift_offset("M", 4)], x.iloc[0]))
check("lead also drops nothing", led.dropna().shape[0] == x.dropna().shape[0])
# period-END anchors must survive the shift (a DateOffset would drift Sep-30 -> Dec-30)
check("quarterly shift keeps the quarter-END anchor",
      (pd.Timestamp("2026-09-30") + T.shift_offset("Q", 4)) == pd.Timestamp("2027-09-30"))
check("monthly shift keeps the month-END anchor",
      (pd.Timestamp("2026-02-28") + T.shift_offset("M", 1)) == pd.Timestamp("2026-03-31"))

# Bug B: combined expr inception = latest operand
a = T.SeriesData(m_series(np.arange(260.0), start="2000-01-31"), "M", "A")
b = T.SeriesData(m_series(np.arange(20.0), start="2018-01-31"), "M", "B")
smab = {"A": a, "B": b}
eff = T.effective_inception(T.parse("A+B"), smab)
check("eff_inception = later operand (2018)", eff == pd.Timestamp("2018-01-31"))

# first-valid guard: window starts 2018, combined series valid from 2018 → ok
win3 = (pd.Timestamp("2018-01-31"), pd.Timestamp("2019-08-31"))
s_ab = T.finish(T.parse("A+B"), 0, win3, smab, "M")
chk = T.check_first_valid(s_ab, T.parse("A+B"), win3, smab, "M")
check("Bug-B guard passes for late combined start", chk.ok, chk.detail)

# --------------------------------------------------------------------------- #
print("INDEX — native vs applied/NeedRebuffer")
# --------------------------------------------------------------------------- #
idx_series = m_series(np.linspace(80, 120, 60), start="2015-01-31")
smi = {"P": T.SeriesData(idx_series, "M", "P")}
evi = T.Evaluator(smi)
r, _ = evi.ev(T.parse("index(P,2017=100)"))
base_avg = idx_series[idx_series.index.year == 2017].mean()
check("INDEX rebases to year average*100", approx(r.iloc[0], idx_series.iloc[0] / base_avg * 100))
try:
    evi.ev(T.parse("index(P,1990=100)")); check("INDEX pre-buffer base -> NeedRebuffer", False)
except T.NeedRebuffer:
    check("INDEX pre-buffer base -> NeedRebuffer", True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
