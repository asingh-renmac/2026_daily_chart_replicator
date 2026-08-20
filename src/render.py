"""
render.py — RenMac-styled reconstruction of a (raw Haver) chart (plan.md §7.1).

Consumes the canonical RenMac styling helpers (econ-templates/charts/
renmac_chart_style.py) — colors, renmac_style, the source box, and
add_recession_shading — rather than redefining them (Q9). Adds:

  * a thin dual-axis (twinx) wrapper that still uses the RenMac palette/style,
  * NBER recession shading driven by Haver RECESSM2@USECON (==1 runs; §7.1),
  * figsize=(8.5, 6.1) landscape, passed explicitly (Q8 — not the module default).

LABELING DISCIPLINE (hard rule — plan §7.1):
  The legend label is the get_series DESCRIPTION shortened to its identifying
  essence, with the bracket lag tag [-n] preserved. The transform (YoY%, z-score,
  3-mo annualized, MA) lives in the SUBTITLE; it appears in the legend ONLY when
  two series on the same chart carry DIFFERENT transforms and the distinction
  matters. The raw Haver function string (e.g. movv(...), difa%(...), zs(...))
  must NEVER reach a rendered legend or title — `_assert_clean()` raises if one
  does, so there is no silent formula-dumping fallback.

  There is NO correlation "r =" box — removed by request; never drawn even if the
  original Haver screenshot shows one.

If ANY series on a chart is unresolved/pending, the caller must PARK the whole
chart via park_chart() (the Teams unresolved-ticker round-trip, §4e/G5) — never
render a chart missing a series it is titled for (Defect 2).
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402
import pandas as pd  # noqa: E402

_ECON_CHARTS = Path("C:/Users/asingh/new_work/econ-templates/charts")
if str(_ECON_CHARTS) not in sys.path:
    sys.path.insert(0, str(_ECON_CHARTS))

from renmac_chart_style import (  # noqa: E402
    C_PRIMARY, C_NAVY, C_SECONDARY, SRC_TEXT, PALETTE_EXTENDED,
    renmac_style, add_recession_shading,
)

FIGSIZE_LANDSCAPE = (8.5, 6.1)          # Q8 — explicit landscape
LINE_COLORS = [C_PRIMARY, C_SECONDARY, C_NAVY]   # maroon, slate, navy
# A contribution stack routinely runs to 4-6 components, past the 3-line palette (which
# would RECYCLE maroon onto the 4th component and make two stack segments indistinguishable).
# PALETTE_EXTENDED is the house sequence; a stack draws from it in slot order.
STACK_COLORS = list(PALETTE_EXTENDED)
# Tight, EQUAL margins so the plot fills the width with minimal whitespace all around.
# LEFT clears the y-tick labels (e.g. "-60"); RIGHT mirrors it on single-axis charts.
# Dual-axis charts need a wider right margin so the RIGHT y-axis tick labels clear —
# DUAL_RIGHT_PAD is added to the right margin only when axis_mode == "dual".
LEFT_MARGIN = 0.085
DUAL_RIGHT_PAD = 0.05          # extra right margin for the R-axis labels on dual charts

# A label is "dirty" if it contains a Haver function call (word/percent before
# "(") or a ticker "@". Normal parentheticals ("Wages & Salaries (SA)") are fine
# because they have whitespace before "(".
_DIRTY_LABEL = re.compile(r"[\w%]\(|@")


# House x-axis date format: month-level ticks read "Mar-25" (%b-%y); on a long
# (multi-year/decade) span the locator picks YEAR ticks and those stay bare years
# ("2004"). We only fix the FORMAT string per resolution — the AutoDateLocator still
# decides tick positions/resolution. 5y threshold: a ~1-4y chart gets Mmm-YY; a
# decade+ chart isn't crammed with month labels.
_MONTH_SPAN_YEARS = 5.0

X_LABEL_FMTS = ("auto", "year", "month", "quarter")


_MAX_QUARTER_TICKS = 16


def _quarter_ends(lo: float, hi: float) -> list:
    """Quarter-END timestamps spanning the xlim [lo, hi] (matplotlib date units), thinned so
    at most `_MAX_QUARTER_TICKS` labels are drawn (they collide beyond that)."""
    a = mdates.num2date(lo).replace(tzinfo=None)
    b = mdates.num2date(hi).replace(tzinfo=None)
    try:
        qs = pd.date_range(a, b, freq="QE")
    except ValueError:                          # pandas < 2.2 spelling
        qs = pd.date_range(a, b, freq="Q")
    if not len(qs):
        return []
    step = max(1, math.ceil(len(qs) / _MAX_QUARTER_TICKS))
    return list(qs[::step])


def _quarter_tick(x, _pos=None) -> str:
    """A quarter tick label: 'Q1-26'. Matplotlib ships no quarter formatter, and a quarterly
    series labelled with month names ('May-23') invites reading a quarter-end stamp as a
    month observation."""
    d = mdates.num2date(x)
    return f"Q{(d.month - 1) // 3 + 1}-{d.year % 100:02d}"


def _style_time_axis(ax, tick_years: Optional[int] = None,
                     label_fmt: Optional[str] = None) -> None:
    """Place and format the x ticks.

    DEFAULT (auto) is unchanged: an AutoDateLocator picks the resolution, and we only fix the
    FORMAT per resolution — `Mmm-YY` on a ≤5y span, bare years beyond it. Two per-chart
    overrides (from `chart_spec`) exist because the automatic choice is a house default, not a
    law, and re-reading a long history or a quarterly series often wants something specific:
      * `tick_years=N` — force major ticks every N years (e.g. 5 on a 66-year chart the
        AutoDateLocator would otherwise tick each decade);
      * `label_fmt` — 'year' (2024) | 'month' (Mar-25) | 'quarter' (Q1-26) | 'auto'.
    'quarter' also places the ticks on quarter ends, thinning to every other quarter once
    there are enough of them to collide."""
    lo, hi = ax.get_xlim()                     # matplotlib float days (date units)
    span_years = (hi - lo) / 365.25
    fmt = (label_fmt or "auto").strip().lower()
    if fmt not in X_LABEL_FMTS:
        raise ValueError(f"x_label_fmt must be one of {X_LABEL_FMTS}, got {label_fmt!r}")

    if fmt == "quarter":
        # Ticks are pinned to quarter ENDS, because that is where a low-frequency observation
        # is stamped house-wide (QUARTER_ANCHOR — a Q1 value sits on Mar-31). Ticking quarter
        # STARTS instead puts the Apr-1 gridline beside the Mar-31 bar and labels that bar
        # "Q2" when it is Q1 — an off-by-one-quarter mislabel that reads as plausible.
        ax.xaxis.set_major_locator(mticker.FixedLocator(
            mdates.date2num(_quarter_ends(lo, hi))))
        ax.xaxis.set_major_formatter(plt.FuncFormatter(_quarter_tick))
        return

    if tick_years:
        ax.xaxis.set_major_locator(mdates.YearLocator(int(tick_years)))
    else:
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())

    if fmt == "year":
        strf = "%Y"
    elif fmt == "month":
        strf = "%b-%y"
    else:                                       # auto — the house default
        strf = "%b-%y" if 0 < span_years <= _MONTH_SPAN_YEARS else "%Y"
    ax.xaxis.set_major_formatter(mdates.DateFormatter(strf))


def _fit_ylim(read: tuple, extents: list) -> tuple:
    """A read-off axis range FRAMES the view but must never CLIP the reconstructed data
    (our series often extend past the original chart's last period — e.g. the 2026-07-09
    legal-services CPI line ran above the read's 12.5 top). Honor the read range exactly
    when the data fits inside it; otherwise expand ONLY the exceeded side and add a small
    pad so the line isn't flush against the frame."""
    rlo, rhi = read
    if not extents:
        return read
    lo = min(a for a, _ in extents)
    hi = max(b for _, b in extents)
    new_lo, new_hi = min(rlo, lo), max(rhi, hi)
    if new_lo == rlo and new_hi == rhi:
        return read                            # data fits — keep the read framing/ticks
    pad = 0.04 * (new_hi - new_lo or 1.0)
    return (new_lo - (pad if new_lo < rlo else 0.0),
            new_hi + (pad if new_hi > rhi else 0.0))


def _assert_clean(text: str, where: str) -> None:
    if text and _DIRTY_LABEL.search(text):
        raise ValueError(
            f"{where} contains raw formula/ticker syntax: {text!r}. "
            "Legends/titles must use the shortened get_series description "
            "(transform belongs in the subtitle)."
        )


# a trailing "(…)" group on a legend label (the qualifier bracket we tuck the axis side
# into) — matched at end-of-string only, no nested parens.
_TRAILING_PAREN_RE = re.compile(r"\(([^()]*)\)\s*$")


def _axis_side_label(label: str, axis: str) -> str:
    """On a DUAL-axis chart, tag each legend with the side it reads against so the
    reader knows which y-scale a line uses:
      * a label that ALREADY ends in a qualifier bracket gets the side appended INSIDE it —
        'MBA Purchase Loan Apps Index (NSA, y/y %chg)' → '… (NSA, y/y %chg, RHS)';
      * a label with no trailing bracket gets a fresh one —
        'ISM Mfg: PMI Composite Index' → 'ISM Mfg: PMI Composite Index (LHS)'.
    Idempotent: a label that already carries an LHS/RHS tag is returned unchanged (so a
    re-render / re-relegend never double-tags)."""
    side = "RHS" if axis == "R" else "LHS"
    if re.search(r"\b(?:LHS|RHS)\b", label):
        return label
    m = _TRAILING_PAREN_RE.search(label)
    if m:
        inner = m.group(1).rstrip()
        return f"{label[:m.start()]}({inner}, {side})"
    return f"{label} ({side})"


def _legend_layout(labels: list[str], plot_w_in: float, cap: int) -> tuple[int, int]:
    """Choose (ncol, nrows) so no legend row overflows the plot width. Entry width is
    estimated in points (a handle+gap ~30pt + ~4.7pt per char at fontsize 8); we take the
    largest ncol (≤ cap, ≤ n) whose widest column-row still fits, then LONG labels fall to
    one column and stack VERTICALLY instead of spilling off both edges (the 2026-07-15
    MBA chart). Returns (ncol, nrows)."""
    n = len(labels)
    if n <= 1:
        return max(n, 1), n
    plot_w_pt = plot_w_in * 72.0
    max_entry_pt = max(30.0 + len(lbl) * 4.7 for lbl in labels)
    fit = max(1, int(plot_w_pt // max_entry_pt))
    ncol = min(n, cap, fit)
    return ncol, math.ceil(n / ncol)


def _bar_width_days(index: pd.Index) -> float:
    """Bar width in DAYS from the series' own spacing (matplotlib date units), so a
    quarterly bar is wide and a monthly bar narrow without the caller guessing. ~78% of
    the median period keeps a visible gutter between bars."""
    if len(index) < 2:
        return 20.0
    med = float(pd.Series(index).diff().dt.days.median() or 30.0)
    return max(1.0, med * 0.78)


def _stacked_bottoms(prior: list, current) -> list:
    """Per-period bar bottoms for a MIXED-SIGN stack (the econ-templates
    `stacked_contribution.stacked_bottoms` rule): a positive bar sits on the running sum of
    prior POSITIVE bars, a negative bar hangs beneath the running sum of prior NEGATIVE
    bars. Naive single-`bottom` stacking puts a positive contribution in negative territory
    whenever an earlier component is negative — a silently-wrong contribution chart."""
    out = []
    for i, v in enumerate(current):
        if v >= 0 or pd.isna(v):
            out.append(sum(s[i] for s in prior if s[i] >= 0 and not pd.isna(s[i])))
        else:
            out.append(sum(s[i] for s in prior if s[i] < 0))
    return out


@dataclass
class PlotSeries:
    label: str                          # clean description (+ optional [-n] tag)
    series: pd.Series
    axis: str = "L"                     # 'L' | 'R'
    color: Optional[str] = None
    kind: str = "line"                  # 'line' | 'bar' | 'stacked_bar'
    # NATIVE frequency ('A'|'Q'|'M'|'W'), independent of the grid the values were
    # interpolated onto. `series.index` spacing reflects the CHART's common grid, so a
    # quarterly line on a monthly chart looks monthly — which silently shrank anything
    # measured in "observations" (see build_chart._x_end_pad). Optional: callers that
    # don't set it fall back to the plotted spacing.
    freq: Optional[str] = None


@dataclass
class RenderSpec:
    title: str
    subtitle: Optional[str] = None
    axis_mode: str = "shared"           # 'shared' | 'dual'
    left_label: str = ""
    right_label: str = ""
    recession: Optional[pd.Series] = None       # RECESSM2 (0/1), shade ==1 runs
    source: str = SRC_TEXT
    x_range: Optional[tuple] = None
    figsize: tuple = FIGSIZE_LANDSCAPE
    y_left: Optional[tuple] = None
    y_right: Optional[tuple] = None
    x_tick_years: Optional[int] = None   # force major ticks every N years
    x_label_fmt: Optional[str] = None    # 'auto'|'year'|'month'|'quarter'


def render(series: list[PlotSeries], spec: RenderSpec,
           save_path: Optional[str] = None):
    """Render `series` to a RenMac-styled figure; return (fig, info)."""
    _assert_clean(spec.title, "title")
    _assert_clean(spec.subtitle or "", "subtitle")
    for ps in series:
        _assert_clean(ps.label, "legend label")

    fig, axL = plt.subplots(figsize=spec.figsize)
    axR = axL.twinx() if spec.axis_mode == "dual" else axL

    if spec.recession is not None:
        add_recession_shading(axL, spec.recession)

    info: dict = {"plotted": {}}
    l_ext: list = []                           # (min,max) of every LEFT-axis series
    r_ext: list = []                           # (min,max) of every RIGHT-axis series
    dual = spec.axis_mode == "dual"

    # STACKED bars share one date grid and one running bottom, so they are drawn as a GROUP
    # (union of their indexes, reindexed) before the per-series pass. Everything else (lines,
    # standalone bars) draws independently in slot order.
    stack_idx = [i for i, ps in enumerate(series) if ps.kind == "stacked_bar"]
    stack_grid = None
    stack_prior: list = []
    if stack_idx:
        grid = series[stack_idx[0]].series.index
        for i in stack_idx[1:]:
            grid = grid.union(series[i].series.index)
        stack_grid = grid.sort_values()

    for i, ps in enumerate(series):
        on_right = dual and ps.axis == "R"
        ax = axR if on_right else axL
        if ps.color:
            color = ps.color
        elif ps.kind == "stacked_bar":       # stacks need >3 distinct colors
            color = STACK_COLORS[stack_idx.index(i) % len(STACK_COLORS)]
        else:
            color = LINE_COLORS[i % len(LINE_COLORS)]
        # DUAL charts: tag each legend with its axis side (LHS/RHS) so the reader knows
        # which y-scale the line reads against; single-axis charts stay untagged.
        disp = _axis_side_label(ps.label, ps.axis) if dual else ps.label

        if ps.kind == "stacked_bar":
            v = ps.series.reindex(stack_grid).astype(float)
            vals = [0.0 if pd.isna(x) else float(x) for x in v.values]
            bottoms = _stacked_bottoms(stack_prior, vals)
            ax.bar(stack_grid, vals, width=_bar_width_days(stack_grid), bottom=bottoms,
                   color=color, edgecolor="none", label=disp, zorder=2)
            stack_prior.append(vals)
            s = ps.series.dropna()
            # A stacked component's visual extent is its bar TOP/BOTTOM, not its own value —
            # the axis must fit the whole stack envelope or the tallest bars get clipped.
            tops = [b + x for b, x in zip(bottoms, vals)]
            ext = (min(min(tops), min(bottoms), 0.0), max(max(tops), max(bottoms), 0.0))
        elif ps.kind == "bar":
            s = ps.series.dropna()
            ax.bar(s.index, s.values, width=_bar_width_days(s.index),
                   color=color, edgecolor="none", label=disp, zorder=2)
            ext = (float(min(s.min(), 0.0)), float(max(s.max(), 0.0))) if not s.empty else None
        else:
            s = ps.series.dropna()
            ax.plot(s.index, s.values, color=color, lw=1.6, label=disp, zorder=3)
            ext = (float(s.min()), float(s.max())) if not s.empty else None

        info["plotted"][disp] = (s.index.min(), s.index.max(), len(s))
        if ext is not None:
            (r_ext if on_right else l_ext).append(ext)

    # A bar chart must be read against a ZERO baseline (bar length encodes the value), so
    # draw the zero rule whenever any bar is present and zero is inside the view.
    if any(ps.kind in ("bar", "stacked_bar") for ps in series):
        axL.axhline(0, color="black", lw=0.6, zorder=3)

    if spec.x_range is not None:
        axL.set_xlim(spec.x_range[0], spec.x_range[1])
    # Mmm-YY on month spans, years on long ones — unless the chart overrides tick/format.
    _style_time_axis(axL, tick_years=spec.x_tick_years, label_fmt=spec.x_label_fmt)
    if spec.y_left is not None:
        axL.set_ylim(*_fit_ylim(spec.y_left, l_ext))
    if spec.axis_mode == "dual" and spec.y_right is not None:
        axR.set_ylim(*_fit_ylim(spec.y_right, r_ext))

    renmac_style(axL, title=spec.title, subtitle=spec.subtitle, source=spec.source)
    if not spec.title:
        # "replicate, no commentary" mode: no TITLE, but keep the SUBTITLE (it carries
        # the non-suppressible transform label). Reclaim the title band's pad so the
        # subtitle sits at the top instead of below an empty title gap.
        axL.set_title("", pad=2)
    if spec.left_label:
        axL.set_ylabel(spec.left_label, fontsize=9)
    if dual:
        axR.spines["top"].set_visible(False)
        axR.spines["left"].set_visible(False)
        if spec.right_label:
            axR.set_ylabel(spec.right_label, fontsize=9)
        h1, l1 = axL.get_legend_handles_labels()
        h2, l2 = axR.get_legend_handles_labels()
        handles, labels = h1 + h2, l1 + l2
    else:
        handles, labels = axL.get_legend_handles_labels()

    # LEFT-ALIGN the plot (house layout): a tight left margin — just enough to clear
    # the y-tick labels (e.g. "-60"); the RIGHT margin mirrors it so the plot fills the
    # width with minimal, symmetric whitespace. Explicit margins, NOT tight_layout()+
    # bbox_inches="tight" (which re-crops and can re-center the plot). Dual-axis charts
    # widen the right margin by DUAL_RIGHT_PAD so the RIGHT y-axis tick labels clear.
    right = 1.0 - LEFT_MARGIN - (DUAL_RIGHT_PAD if dual else 0.0)
    plot_w_in = spec.figsize[0] * (right - LEFT_MARGIN)

    # Legend: WRAP long labels onto multiple rows instead of letting them spill off both
    # edges. _legend_layout picks the widest column count that still fits the plot width;
    # long labels collapse to ncol=1 and stack vertically. The bottom band then OPENS
    # proportionally to the row count so a tall legend clears BOTH the x-axis labels above
    # and keeps a clear gap above the figure-level Source line below.
    cap = 2 if dual else 3
    ncol, nrows = _legend_layout(labels, plot_w_in, cap)
    axL.legend(handles, labels, fontsize=8, frameon=False, loc="upper center",
               bbox_to_anchor=(0.5, -0.11), ncol=ncol,
               columnspacing=1.6, handlelength=1.7, labelspacing=0.7)
    bottom = 0.135 + 0.05 * nrows       # 1 row → 0.185; each extra row adds headroom
    fig.subplots_adjust(left=LEFT_MARGIN, right=right, top=0.88, bottom=bottom)
    if save_path:
        fig.savefig(save_path, dpi=200)   # no bbox="tight" → preserve the right-side space
    return fig, info


def park_chart(chart_id: str, unresolved: list[str], reason: str,
               ledger_path: str = "outputs/ledger.jsonl") -> dict:
    """Defect-2 park path: when ANY series is unresolved, DO NOT render a partial
    chart — record an awaiting_ticker row for the Teams round-trip (G5) and return
    the parked record. The full chart resumes only once the ticker(s) come back.
    """
    rec = {
        "chart_id": chart_id,
        "status": "awaiting_ticker",
        "unresolved": unresolved,
        "reason": reason,
        "parked_at": datetime.now(timezone.utc).isoformat(),
    }
    p = Path(ledger_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(f"  PARKED {chart_id}: awaiting {unresolved} ({reason})")
    return rec
