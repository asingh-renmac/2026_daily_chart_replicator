"""
chartspec.py — G8 Stage 1: VISION READ of a raw-Haver chart screenshot.

Opus reads a single chart image and returns a structured `ChartSpec`: how many
series are plotted and, per series, the on-chart description, the raw Haver
FORMULA if shown (which `transforms.py` evaluates — the read IS the formula
string), a clean base_descriptor split from any applied_transform, axis side,
bracket lag, SA/NSA + base hints; plus chart-level axis tick ranges, start, and
forecast flag.

DISCIPLINE (G8 has the highest silent-wrong risk in the system):
  * The read is a HYPOTHESIS. Nothing here binds a ticker. Stage 2 (resolve.py)
    confirms every series via get_series and cross-checks freq/SA before any bind.
  * Fail loud, never fabricate: a malformed model response raises.

POST-REVIEW FIXES (2026-06-29, Aman's G8a corrections — read-level, not resolution):
  1. axis_mode is DERIVED FROM TICK VALUES, never guessed: the model reads the
     left/right axis numeric tick ranges; we compare them. n_series<=1 → shared
     (dual is impossible); right axis absent or ranges equal (Haver double-labels
     the same scale) → shared; ranges differ → dual.
  2. The x-axis END is NOT trusted for non-forecast series. The model reads a start
     and a `has_forecast` flag; `end_mode` is "derive_at_pull" unless the chart has
     a genuine forecast line (then keep its forward extent). The actual end is set
     at Stage-2 pull time to the latest plotted period.
  3. Summed/expression formulas: ticker_read only catches the first addend, so it is
     ADVISORY ONLY — Stage 2 resolves expressions by walking the G3 parser's Series
     nodes and confirming EVERY mnemonic (a G8b resolver rule, not a Stage-1 change).
  5. freq_hint (2026-06-30, G8a reopened): a one-field native FREQUENCY read per
     series. Frequency is a DEFINING attribute (same tier as SA) — a monthly
     description resolving to a same-named QUARTERLY series would pass the SA check
     and bind wrong. Stage 2 cross-checks read-freq vs get_series freq and parks on
     mismatch, exactly like SA (no "usually fails downstream" silent-wrong path).
  4. description→base split: `base_descriptor` is the series name with an APPLIED
     transform removed; `applied_transform` carries that phrase. When it is unclear
     whether a "moving average"/"MA"/"% change" is the NATIVE series name (e.g. the
     Atlanta Fed "Wage Growth Tracker: 3-Mo Mov Avg…") or an applied transform,
     `native_ma_ambiguous=true` and we DON'T strip — Stage 2 routes that to the bot.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

DEFAULT_MODEL = "claude-opus-4-8"  # Q7
_CURRENT_YEAR = datetime.now().year

_PROMPT = """You are reading ONE raw-Haver chart screenshot for a pipeline that \
will REBUILD it. Report exactly what the pixels show; never guess. Use null when a \
value is not visible.

AXES — read the NUMERIC TICK LABELS, do not infer from the lines:
- left_axis: the lowest and highest numeric tick label on the LEFT y-axis, plus its \
unit text. 
- right_axis: the same for the RIGHT y-axis. If there is NO separate right axis, set \
right_axis to null. NOTE: Haver often prints the SAME scale on BOTH sides (the left \
and right tick numbers are identical) — read both anyway so the ranges can be compared.

X-AXIS:
- sample_start: the earliest x-axis date/year shown.
- sample_end_read: the latest x-axis date/year shown (advisory — may be overridden).
- has_forecast: true ONLY if a plotted line genuinely runs INTO THE FUTURE as a \
projection/forecast (e.g. a dotted SEP dot-plot or projection extending past the last \
actual datapoint). If every line ends at the latest actual data, false.

For EACH plotted data series (EXCLUDE gray recession bands and any flat zero/reference \
line):
- description: the on-chart text naming it, verbatim.
- formula: the raw Haver formula if the label shows one (nested funcs, %/L/C variants, \
"@db" if shown, sums like "BEEM1+BEEM2+BEEM3"), else null.
- ticker_read: a single mnemonic directly readable from the formula/label (ADVISORY \
only; for a sum it's just the first term), else null.
- base_descriptor: the SERIES NAME with any APPLIED transform wording removed, but \
KEEP wording that is part of the official series name.
- applied_transform: the applied Haver transform phrase if one is clearly appended to \
a base series (e.g. "% Change - Year to Year", "3-month moving average", "6-month \
%Change-ann"), else null.
- native_ma_ambiguous: true if you CANNOT tell whether a "moving average" / "MA" / \
"% change" in the name is the NATIVE series name or an applied transform (e.g. \
"Wage Growth Tracker: 3-Mo Mov Avg of Median Wage Growth" — the 3-mo MA is the native \
name). When true, leave the full text in base_descriptor and applied_transform null.
- axis: "L", "R", or "shared" — which y-axis this series is plotted against.
- plot_kind: HOW this series is DRAWN — read the pixels, do not assume a line:
  * "line"  — a continuous stroke through the points.
  * "bar"   — discrete vertical bars rising/falling from the zero baseline, standing \
alone (not sitting on another series' bars).
  * "stacked_bar" — vertical bars in SEGMENTS, where several series' segments sit on top \
of one another to build a single composite bar per period (the classic contribution \
decomposition: each period's bar is split into colored blocks). If two or more series \
share stacked bars, mark EVERY one of them "stacked_bar".
  A chart may MIX kinds (e.g. bars for a quarterly rate + a line for its year-over-year). \
Judge each series separately; if you genuinely cannot tell, use "line".
- lag: a bracket lag like "[-4]" if shown, else null.
- freq_hint: the series' native reporting FREQUENCY — "monthly", "quarterly", \
"weekly", "annual", "daily", or "unknown". Infer from the well-known release (e.g. \
JOLTS/CPI/PCE/ISM = monthly; ECI/GDP = quarterly; jobless claims = weekly) and/or \
the spacing of the plotted points. Use "unknown" if you genuinely cannot tell — do \
NOT guess a frequency to look confident.
- sa_hint: "sa", "nsa", "saar", or "unknown".
- base_hint: an index base if shown (e.g. "2012=100"), else null.
- legend_label: the legend entry text, else null.
- confidence: 0.0-1.0, your confidence in THIS series' read.

Also chart-level: n_series (count of plotted data series, excluding bands/ref lines), \
units (overall units string if shown), recession_shading (true/false).

Return STRICT JSON only, no prose:
{"n_series":int,"left_axis":{"min":number|null,"max":number|null,"label":str|null},\
"right_axis":{"min":number|null,"max":number|null,"label":str|null}|null,\
"sample_start":str|null,"sample_end_read":str|null,"has_forecast":bool,\
"units":str|null,"recession_shading":bool,"series":[{"description":str,\
"formula":str|null,"ticker_read":str|null,"base_descriptor":str|null,\
"applied_transform":str|null,"native_ma_ambiguous":bool,"axis":"L|R|shared",\
"plot_kind":"line|bar|stacked_bar",\
"lag":str|null,"freq_hint":"monthly|quarterly|weekly|annual|daily|unknown",\
"sa_hint":"sa|nsa|saar|unknown","base_hint":str|null,\
"legend_label":str|null,"confidence":number}]}"""


@dataclass
class AxisTicks:
    min: Optional[float] = None
    max: Optional[float] = None
    label: Optional[str] = None

    @property
    def has_range(self) -> bool:
        return self.min is not None and self.max is not None


@dataclass
class SeriesSpec:
    description: str = ""
    formula: Optional[str] = None
    ticker_read: Optional[str] = None          # ADVISORY only (see #3)
    base_descriptor: Optional[str] = None
    applied_transform: Optional[str] = None
    native_ma_ambiguous: bool = False
    axis: str = "shared"
    plot_kind: str = "line"                    # 'line' | 'bar' | 'stacked_bar' (read)
    lag: Optional[str] = None
    freq_hint: str = "unknown"
    sa_hint: str = "unknown"
    base_hint: Optional[str] = None
    legend_label: Optional[str] = None
    confidence: float = 0.0

    @property
    def has_formula(self) -> bool:
        return bool(self.formula)

    @property
    def resolvable_headless(self) -> bool:
        """True when a formula is shown → Stage 2 confirms its mnemonic(s) via DLX
        with no search. Description-only series go to search/Teams."""
        return self.has_formula


@dataclass
class ChartSpec:
    n_series: int = 0
    left_axis: AxisTicks = field(default_factory=AxisTicks)
    right_axis: Optional[AxisTicks] = None
    axis_mode: str = "shared"          # DERIVED from tick ranges (not the model's word)
    sample_start: Optional[str] = None
    sample_end_read: Optional[str] = None
    has_forecast: bool = False
    end_mode: str = "derive_at_pull"   # DERIVED: "forecast" keeps read end; else derive
    units: Optional[str] = None
    recession_shading: bool = False
    series: list[SeriesSpec] = field(default_factory=list)
    model: str = DEFAULT_MODEL
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


# ───────────────────────────── derivations ─────────────────────────────────
def _axis_ranges_match(left: AxisTicks, right: Optional[AxisTicks]) -> bool:
    """Same scale on both sides? Haver double-labels identical ticks → shared."""
    if right is None or not right.has_range or not left.has_range:
        return True   # no comparable right axis → treat as one (shared)
    span = max(abs(left.max - left.min), abs(right.max - right.min), 1e-9)
    return (abs(left.min - right.min) <= 0.02 * span
            and abs(left.max - right.max) <= 0.02 * span)


def derive_axis_mode(n_series: int, left: AxisTicks,
                     right: Optional[AxisTicks]) -> str:
    if n_series <= 1:
        return "shared"               # dual is impossible with one line (#1)
    return "shared" if _axis_ranges_match(left, right) else "dual"


def _axis_ticks(d: Optional[dict]) -> Optional[AxisTicks]:
    if not d:
        return None
    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    return AxisTicks(min=num(d.get("min")), max=num(d.get("max")),
                     label=(d.get("label") or None))


_PLOT_KINDS = ("line", "bar", "stacked_bar")


def _plot_kind(v) -> str:
    """Normalize the read's `plot_kind` onto the supported set, defaulting to "line".
    Tolerant of spelling drift ("stacked bar"/"stacked-bars"/"column") because the read is
    a hypothesis a human ratifies; an unknown word must not break the whole read."""
    s = re.sub(r"[^a-z]+", "_", str(v or "").strip().lower()).strip("_")
    if not s:
        return "line"
    if "stack" in s:
        return "stacked_bar"
    if s.startswith("bar") or s.startswith("column") or s.startswith("histogram"):
        return "bar"
    return s if s in _PLOT_KINDS else "line"


def _coerce(detail: dict, model: str) -> ChartSpec:
    series = []
    for s in detail.get("series", []) or []:
        series.append(SeriesSpec(
            description=(s.get("description") or "").strip(),
            formula=(s.get("formula") or None),
            ticker_read=(s.get("ticker_read") or None),
            base_descriptor=(s.get("base_descriptor") or None),
            applied_transform=(s.get("applied_transform") or None),
            native_ma_ambiguous=bool(s.get("native_ma_ambiguous", False)),
            axis=(s.get("axis") or "shared"),
            plot_kind=_plot_kind(s.get("plot_kind")),
            lag=(s.get("lag") or None),
            freq_hint=(s.get("freq_hint") or "unknown"),
            sa_hint=(s.get("sa_hint") or "unknown"),
            base_hint=(s.get("base_hint") or None),
            legend_label=(s.get("legend_label") or None),
            confidence=float(s.get("confidence", 0.0) or 0.0),
        ))
    n = detail.get("n_series")
    if not isinstance(n, int):
        n = len(series)
    left = _axis_ticks(detail.get("left_axis")) or AxisTicks()
    right = _axis_ticks(detail.get("right_axis"))

    axis_mode = derive_axis_mode(n, left, right)
    # Reconcile per-series axis with the derived mode (#1): shared ⇒ all shared.
    if axis_mode == "shared":
        for s in series:
            s.axis = "shared"

    has_forecast = bool(detail.get("has_forecast", False))
    end_mode = "forecast" if has_forecast else "derive_at_pull"   # (#2)

    return ChartSpec(
        n_series=n, left_axis=left, right_axis=right, axis_mode=axis_mode,
        sample_start=(detail.get("sample_start") or None),
        sample_end_read=(detail.get("sample_end_read") or None),
        has_forecast=has_forecast, end_mode=end_mode,
        units=(detail.get("units") or None),
        recession_shading=bool(detail.get("recession_shading", False)),
        series=series, model=model, raw=detail,
    )


def read_chart_spec(image_bytes: bytes, model: str | None = None) -> ChartSpec:
    """Opus vision read → ChartSpec. Raises (fail-loud) if the key/pkg is missing
    or the model output can't be parsed — never returns a fabricated spec."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set — G8 vision read unavailable.")
    import anthropic

    mdl = model or DEFAULT_MODEL
    b64 = base64.standard_b64encode(image_bytes).decode()
    resp = anthropic.Anthropic().messages.create(
        model=mdl, max_tokens=2000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/png", "data": b64}},
            {"type": "text", "text": _PROMPT},
        ]}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in vision output: {text[:200]!r}")
    detail = json.loads(text[start:end + 1])
    if "series" not in detail:
        raise ValueError(f"vision output missing 'series': {detail!r}")
    return _coerce(detail, mdl)
