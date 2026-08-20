"""
g4_lib.py — G4 validation helpers: live Haver pull, period-END SeriesData wrap
(the QUARTER_ANCHOR convention under test), and a gridline-calibrated overlay of
the reconstruction ON TOP of the original screenshot at the same x-scale.

The overlay is pixel-accurate: it detects the chart's light-gray gridlines and
maps known year/value ticks to pixels, so a 1-2 month horizontal drift on the
interpolated low-freq line (the quarter-anchor signal) is actually visible.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import transforms as T  # noqa: E402

RAW = ROOT / "outputs" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# Haver pull → period-END SeriesData
# --------------------------------------------------------------------------- #

_HAVER_READY = False


def _haver():
    global _HAVER_READY
    import Haver  # noqa: N813
    if not _HAVER_READY:
        Haver.direct("on")
        _HAVER_READY = True
    return Haver


# Parquet cache TTL. The cache is keyed by code_db_start ONLY — it carries NO
# vintage. Reusing a cache written on a PRIOR day silently pins that series to a
# stale vintage; when two operands of a composite (e.g. NFIB7 − NFIB6) are cached
# on DIFFERENT days, the difference is truncated to the OLDER operand's last month
# and the newest point (the "dip") vanishes with no error. That bit the 2026-07-14
# NFIB chart: nfib7 re-pulled same-day (had June), nfib6 was a July-1 cache (ended
# May) → the diff stopped in May. So a cache from an earlier calendar day is stale
# for a DAILY pipeline: re-pull it. HAVER_CACHE_TTL_DAYS overrides (0 = today-only,
# a big number = never expire, e.g. for offline replays).
_CACHE_TTL_DAYS = int(os.environ.get("HAVER_CACHE_TTL_DAYS", "0"))


def _cache_fresh(path: Path) -> bool:
    """A parquet cache is fresh iff written within HAVER_CACHE_TTL_DAYS calendar days."""
    if not path.exists():
        return False
    age = (date.today() - datetime.fromtimestamp(path.stat().st_mtime).date()).days
    return age <= _CACHE_TTL_DAYS


def pull(code: str, db: str, freq: str, start: str = "1959-01-01") -> T.SeriesData:
    """Pull one Haver series; cache to parquet; return a period-END SeriesData.

    Period-END indexing IS the QUARTER_ANCHOR convention (§4.6, G4-pinned): a
    quarterly obs lands on Mar/Jun/Sep/Dec month-end, a monthly obs on month-end.

    The parquet cache is refreshed once per calendar day (HAVER_CACHE_TTL_DAYS) so
    every operand pulled in one run shares one vintage — a stale cross-day cache is
    what silently truncated the NFIB difference on 2026-07-14.
    """
    cache = RAW / f"{code}_{db}_{start}.parquet"
    if _cache_fresh(cache):
        s = pd.read_parquet(cache).iloc[:, 0]
    else:
        Haver = _haver()
        df = Haver.data([code], db, startdate=start)
        if df is None or (isinstance(df, dict)):
            raise RuntimeError(f"Haver returned no data for {code}@{db}: {df!r}")
        s = df.iloc[:, 0]
        if isinstance(s.index, pd.PeriodIndex):
            s.index = s.index.to_timestamp()
        s.to_frame(name=code).to_parquet(cache)
    # normalize to PERIOD-END timestamps at the native freq
    per = s.index.to_period({"M": "M", "Q": "Q", "W": "W", "A": "A"}[freq])
    s.index = per.to_timestamp(how="end").normalize()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s = s[np.isfinite(s.values)]
    print(f"  pulled {code}@{db} [{freq}] {s.index.min().date()}..{s.index.max().date()} "
          f"n={len(s)}")
    return T.SeriesData(values=s.astype(float), freq=freq, code=f"{code}@{db}")


# --------------------------------------------------------------------------- #
# Screenshot gridline calibration
# --------------------------------------------------------------------------- #

@dataclass
class Calib:
    img: np.ndarray
    x_px: np.ndarray         # detected vertical-gridline pixel columns
    y_px: np.ndarray         # detected horizontal-gridline pixel rows
    x_to_px: object          # callable date/np-datetime -> pixel col
    yL_to_px: object         # callable value -> pixel row (left axis)
    yR_to_px: object         # callable value -> pixel row (right axis) or None


def _gray_mask(img: np.ndarray) -> np.ndarray:
    r, g, b = img[..., 0].astype(int), img[..., 1].astype(int), img[..., 2].astype(int)
    near_eq = (abs(r - g) < 14) & (abs(g - b) < 14) & (abs(r - b) < 14)
    mid = (img.mean(axis=2) > 150) & (img.mean(axis=2) < 225)
    return near_eq & mid


def _grid_mask(img: np.ndarray) -> np.ndarray:
    """Gray gridlines PLUS the near-black zero/axis line (achromatic, any darkness)."""
    r, g, b = img[..., 0].astype(int), img[..., 1].astype(int), img[..., 2].astype(int)
    near_eq = (abs(r - g) < 18) & (abs(g - b) < 18) & (abs(r - b) < 18)
    m = img.mean(axis=2)
    return near_eq & (m < 225)          # exclude white background only


def _peaks(profile: np.ndarray, min_frac: float, min_gap: int) -> np.ndarray:
    """Return indices of local peaks in a 1-D density profile above min_frac*max."""
    thr = profile.max() * min_frac
    cand = np.where(profile >= thr)[0]
    if len(cand) == 0:
        return cand
    groups, cur = [], [cand[0]]
    for p in cand[1:]:
        if p - cur[-1] <= min_gap:
            cur.append(p)
        else:
            groups.append(cur)
            cur = [p]
    groups.append(cur)
    return np.array([int(round(np.average(grp, weights=profile[grp]))) for grp in groups])


def _find_n(profile: np.ndarray, expected: int, min_gap: int) -> np.ndarray:
    """Adaptively pick the threshold whose peak count matches `expected` (else
    the closest), so a faint endpoint gridline isn't dropped."""
    best = _peaks(profile, 0.55, min_gap)
    for frac in np.linspace(0.70, 0.20, 26):
        pk = _peaks(profile, frac, min_gap)
        if len(pk) == expected:
            return pk
        if abs(len(pk) - expected) < abs(len(best) - expected):
            best = pk
    return best


def calibrate(png_path: str, x_ticks: list, x_years: list,
              yL_ticks: list, yL_vals: list,
              yR_vals: list | None = None) -> Calib:
    """Detect gridlines and build pixel<->data maps.

    x_years: labeled YEARS on the x-axis, used to ASSIGN vertical gridlines → dates.
    yL_vals/yR_vals: labeled axis values top→bottom (same horizontal gridlines).
    """
    img = np.asarray(Image.open(png_path).convert("RGB"))
    H, W, _ = img.shape
    y0, y1 = int(H * 0.12), int(H * 0.90)
    x0, x1 = int(W * 0.06), int(W * 0.94)
    col_prof = _gray_mask(img)[y0:y1, x0:x1].sum(axis=0).astype(float)
    row_prof = _grid_mask(img)[y0:y1, x0:x1].sum(axis=1).astype(float)
    xcols = _find_n(col_prof, len(x_years), 6) + x0
    yrows = _find_n(row_prof, len(yL_vals), 6) + y0
    return _build_maps(img, xcols, yrows, x_years, yL_vals, yR_vals)


def _affine(p0, v0, p1, v1):
    """Return f(value)->pixel given two (pixel, value) anchors."""
    m = (p1 - p0) / (v1 - v0)
    return lambda v: p0 + m * (np.asarray(v, float) - v0)


def _zero_row(img, y0, y1, x0, x1):
    """Pixel row of the solid black zero line (achromatic + dark, full width)."""
    r, g, b = img[..., 0].astype(int), img[..., 1].astype(int), img[..., 2].astype(int)
    dark = (abs(r - g) < 20) & (abs(g - b) < 20) & (abs(r - b) < 20) & (img.mean(axis=2) < 100)
    prof = dark[y0:y1, x0:x1].sum(axis=1)
    return y0 + int(np.argmax(prof)), int(prof.max())


def _build_maps(img, xcols, yrows, x_years, yL_vals, yR_vals):
    H, W, _ = img.shape
    y0, y1 = int(H * 0.12), int(H * 0.92)
    x0, x1 = int(W * 0.10), int(W * 0.90)
    xcols = np.sort(xcols)
    yrows = np.sort(yrows)
    print(f"  calib: detected {len(xcols)} vgrid, {len(yrows)} hgrid; "
          f"expect {len(x_years)} years, {len(yL_vals)} yL")
    # x: map first/last detected gridline to first/last expected year
    x_dates = [pd.Timestamp(f"{y}-06-30") for y in x_years]
    x_ord = np.array([d.toordinal() for d in x_dates], float)
    fx_val = _affine(xcols[0], x_ord[0], xcols[-1], x_ord[-1])
    x_to_px = lambda d: fx_val(np.array([pd.Timestamp(t).toordinal()
                                         for t in np.atleast_1d(d)], float))
    # y: prefer ZERO-LINE anchoring (robust to a missing faint extreme gridline).
    g_px = float(np.median(np.diff(yrows)))          # pixels per value step
    if 0 in yL_vals:
        zrow, zstrength = _zero_row(img, y0, y1, x0, x1)
        # snap zrow to the nearest detected gridline (the zero gridline)
        zrow = int(yrows[np.argmin(np.abs(yrows - zrow))])
        dvalL = abs(yL_vals[0] - yL_vals[1])
        fyL = lambda v: zrow - np.asarray(v, float) / dvalL * g_px
        if yR_vals is not None:
            dvalR = abs(yR_vals[0] - yR_vals[1])
            fyR = lambda v: zrow - np.asarray(v, float) / dvalR * g_px
        else:
            fyR = None
        print(f"  calib: zero-anchored at row {zrow}, {g_px:.1f}px/step")
    else:
        fyL = _affine(yrows[0], yL_vals[0], yrows[-1], yL_vals[-1])
        fyR = (_affine(yrows[0], yR_vals[0], yrows[-1], yR_vals[-1])
               if yR_vals is not None else None)
    return Calib(img=img, x_px=xcols, y_px=yrows,
                 x_to_px=x_to_px, yL_to_px=fyL, yR_to_px=fyR)


def _recession_runs(rec: pd.Series):
    """(start, end) timestamp pairs for contiguous rec==1 runs."""
    r = (rec.fillna(0) >= 0.5).astype(int)
    runs, in_r, start = [], False, None
    for d, v in r.items():
        if v and not in_r:
            start, in_r = d, True
        elif not v and in_r:
            runs.append((start, d)); in_r = False
    if in_r:
        runs.append((start, r.index[-1]))
    return runs


def overlay(calib: Calib, recon: list, save_path: str,
            title: str = "", debug_grid: bool = False,
            recession: pd.Series | None = None):
    """Lay reconstruction lines (in DATA coords) over the screenshot in PIXEL
    coords. `recon` = list of dicts: {series, axis('L'/'R'), color, label}.
    If `recession` given, draw RECESSM2==1 spans (pixel-mapped) to check the
    screenshot's gray bands land on the right months."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    H, W, _ = calib.img.shape
    fig, ax = plt.subplots(figsize=(W / 100, H / 100), dpi=100)
    ax.imshow(calib.img, origin="upper")
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)

    if debug_grid:
        for c in calib.x_px:
            ax.axvline(c, color="lime", lw=0.6, alpha=0.7)
        for r in calib.y_px:
            ax.axhline(r, color="magenta", lw=0.6, alpha=0.7)

    if recession is not None:
        for s, e in _recession_runs(recession):
            xs, xe = float(calib.x_to_px(s)), float(calib.x_to_px(e))
            ax.axvspan(xs, xe, color="red", alpha=0.18, zorder=2)

    for r in recon:
        s = r["series"].dropna()
        px = calib.x_to_px(s.index)
        ymap = calib.yL_to_px if r.get("axis", "L") == "L" else calib.yR_to_px
        py = ymap(s.values)
        ax.plot(px, py, color=r.get("color", "#E0218A"), lw=1.4,
                alpha=0.75, label=r.get("label", ""), zorder=5)

    ax.set_title(title, fontsize=9)
    ax.axis("off")
    if any(r.get("label") for r in recon):
        ax.legend(fontsize=7, loc="upper right", framealpha=0.6)
    fig.tight_layout()
    fig.savefig(save_path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {save_path}")
