"""
validate.py — silent-error guards run BEFORE a chart ships (plan §8).

These are deliberately separate from rendering: a chart can look perfect and
still be wrong (wrong SA/NSA, wrong base year, wrong transform). The checks here
compare the reconstruction against values READ OFF the original screenshot
(extraction/vision, §1.3) and FAIL LOUD on mismatch rather than silently
shipping.

§8.10 last-value fidelity is the headline guard. The end-value NUMBER is no
longer DRAWN on the chart (not in the RenMac/desired style), but the CHECK
stays: for any chart whose screenshot shows an end-value flag, read it and assert
the reconstruction's last value matches. Removing the annotation must NOT remove
the guard.
"""

from __future__ import annotations

import pandas as pd

# Edge tolerance by common frequency — how far last_valid_index may sit from the
# screenshot's visual end before we call it a real mismatch (centered MAs, recent
# NaN edges). Months.
EDGE_TOL = {"M": 2, "Q": 1, "W": 6, "D": 31, "A": 0}


class LastValueMismatch(AssertionError):
    """The reconstruction's last value disagrees with the screenshot flag."""


def check_last_value(recon: pd.Series, screenshot_flag: float,
                     visual_end: pd.Timestamp, freq: str,
                     value_tol: float, label: str = "") -> dict:
    """§8.10 — fail loud if the reconstruction's end value does not match the
    value flagged on the original screenshot.

    Two assertions:
      1. timing — `|last_valid_index - visual_end| <= EDGE_TOL[freq]` (tolerates a
         recent-edge NaN / the [-n]-extended right edge); compared at last-valid,
         not forced to display_end.
      2. value  — `|recon.at[last_valid] - screenshot_flag| <= value_tol`.

    Returns a small report dict on success; raises LastValueMismatch otherwise.
    `value_tol` is per-chart (e.g. half the axis label step, or the printed
    decimal precision). `screenshot_flag`/`visual_end` come from extraction.
    """
    s = recon.dropna()
    if s.empty:
        raise LastValueMismatch(f"{label}: reconstruction is empty")

    last_idx = s.index[-1]
    last_val = float(s.iloc[-1])
    tol_months = EDGE_TOL.get(freq.upper(), 2)
    months_off = abs((last_idx.to_period("M") - pd.Period(visual_end, "M")).n)

    if months_off > tol_months:
        raise LastValueMismatch(
            f"{label}: last-valid {last_idx.date()} is {months_off} mo from "
            f"screenshot end {pd.Timestamp(visual_end).date()} (> {tol_months})"
        )
    if abs(last_val - screenshot_flag) > value_tol:
        raise LastValueMismatch(
            f"{label}: last value {last_val:.3f} vs screenshot flag "
            f"{screenshot_flag:.3f} differs by {abs(last_val - screenshot_flag):.3f} "
            f"(> tol {value_tol})  — possible wrong SA/base/transform"
        )
    return {"label": label, "last_idx": last_idx, "last_val": last_val,
            "flag": screenshot_flag, "months_off": months_off, "ok": True}
