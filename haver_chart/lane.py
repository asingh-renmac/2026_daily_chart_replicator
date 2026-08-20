"""Chat-lane translation layer (plan.md §13.4).

This module contains NO chart logic. Every correctness rule — the transform
vocabulary and its fail-loud floor, the exact-token-set bind gate, the SA and
aggregation cross-checks, legend keying, window edges, the x-pad clamp, RenMac
style — lives in `resolve.py`, `build_chart.py`, `transforms.py` and `render.py`
and is reached by calling those functions, unchanged. If something here starts
doing arithmetic on a series or reasoning about a transform phrase, it is a bug:
that code already exists and a second copy of it will rot out of sync with the
daily lane.

Split from `server.py` so the lane can be driven (and diffed against the daily
lane) without an MCP client in the loop.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

from haver_chart.bootstrap import REPO_ROOT, quiet_stdout, seal_stores

with quiet_stdout():                      # importing Haver prints to stdout
    import build_chart as BC              # noqa: E402
    import haver_search as HS             # noqa: E402
    import resolve as R                   # noqa: E402
    import validate as V                  # noqa: E402

seal_stores(R)                            # §13.6 — reads allowed, writes raise

OUT_ROOT = REPO_ROOT / "outputs" / "chat"

# One accepted phrasing per family `build_chart._flat_transform` recognizes. The mapper
# is branch logic over keyword stems rather than a table, so this list cannot be derived
# from it — `selftest.py` asserts every entry still maps, which is what stops the two
# from drifting apart. Used only to make the fail-loud error actionable: the raise names
# the phrase it rejected, and an operator who cannot see the accepted wording has to
# guess at it.
TRANSFORM_PHRASES = (
    "% Change - Year to Year",
    "% Change",
    "3-month %Change-ann",
    "Difference - Year to Year",
    "3-month moving average",
    "3-month moving sum",
    "Z-Score",
    "Log",
    "% Change - Year to Year of 3-month moving average",
)

PLOT_KINDS = ("line", "bar", "stacked_bar")
_LINE_WORDS = ("line", "lines", "curve", "curves")


def transform_help() -> str:
    """The accepted `applied_transform` wordings, for an operator to restate one."""
    return ("recognized wordings include " + "; ".join(f"{p!r}" for p in TRANSFORM_PHRASES)
            + ". Haver's aggregation/units line ('Avg, % p.a.', 'Sum, Mil.$') is NOT a "
              "transform — leave applied_transform empty for those. Year-to-date and "
              "index/rebase are recognized but unsupported: pass the chart's formula "
              "instead.")


# The shared raise ends by telling a DEVELOPER to extend the mapper, which is the right
# audience for the daily lane and the wrong one here. Swapped for the wordings the
# operator can actually act on. Deliberately not a change to the raise itself: the
# vocabulary floor is the guardrail. If that message is ever reworded this stops matching
# and the hint simply reappears — a cosmetic regression, never a silent one.
_DEV_HINT = re.compile(r"\s*[—–-]\s*extend build_chart[^)]*\)")


def explain(exc: BaseException) -> str:
    """Type-prefixed message, with the accepted wordings on an unmapped transform."""
    raw = str(exc)
    if "unmappable applied_transform" not in raw:
        return f"{type(exc).__name__}: {raw}"
    return f"{type(exc).__name__}: {_DEV_HINT.sub('', raw)} — {transform_help()}"


def _plot_kind(value: Any, where: str) -> str:
    """Normalize a plot kind the renderer's own way, but REJECT an unrecognized one.

    `build_chart._plot_kind_of` deliberately falls back to a line, and in the daily lane
    that is right: the shape is cosmetic and `--replot` corrects it later. In the chat
    lane the operator reads the render as the answer, so a value the renderer discards
    ("area", "histogram") would come back as a line with nothing anywhere saying the
    request was dropped. Same normalizer — calling it rather than re-implementing it,
    so "columns" and "stacked bars" keep resolving as they do in the daily lane — with
    a loud floor under it.
    """
    raw = str(value or "").strip()
    if not raw:
        return "line"
    kind = BC._plot_kind_of({"plot_kind": raw})
    if kind == "line" and raw.lower().replace("-", " ").replace("_", " ") not in _LINE_WORDS:
        raise ValueError(
            f"{where}: unrecognized plot_kind {raw!r} — use one of "
            f"{', '.join(PLOT_KINDS)}. Read the shape off the source chart: bars for a "
            f"bar/column chart, stacked_bar for a contribution chart, line otherwise.")
    return kind


# ─────────────────────────────── resolution ─────────────────────────────────
def _resolve_kwargs() -> dict:
    """The daily lane's resolver wiring, verbatim (`run_daily._resolve_kwargs`).

    Identical wiring is the whole point of §13.4: the chat lane must bind the same
    ticker the daily lane would, or the two lanes disagree about what a chart IS.
    `confirm=lambda: True` matches the daily AUTO path — every search hit is already
    a real catalog series, and the bound code is DLX-verified once, after resolve.
    """
    return dict(confirm=lambda c: True, get_meta=HS.get_meta, search=HS.search,
                clarified=R.load_clarified(), trusted=R.load_trusted(),
                learned=R.load_learned())


def _dlx_verify(slot: dict) -> dict:
    """Post-resolve DLX confirmation, mirroring `run_daily._read_resolve`.

    A catalog hit proves the series is in the metadata mirror, not that DLX will
    return observations for it. A code that does not confirm parks the slot rather
    than failing later inside the renderer."""
    if slot.get("status") != R.SLOT_RESOLVED:
        return slot
    for code in (slot.get("codes") or ([slot["resolved"]] if slot.get("resolved") else [])):
        if code and "@" in code and not R.confirm_ticker(code):
            slot.update(status=R.SLOT_PENDING, reason=f"DLX confirm failed for {code}")
            break
    return slot


def _candidates(slot: dict, n: int = 3) -> list[dict]:
    """Top-N candidates with their scores, for the operator to choose from on a park.

    Mirrors what the Teams ask shows. `exact` is the load-bearing column, not `sim`:
    the 2026-06-30 proof had `DFBACTS` vs `DFBACTDS` at Jaccard 0.909 on the WRONG
    directional sibling, so similarity alone never justifies a bind."""
    detail = slot.get("candidate_detail") or []
    ranked = sorted(detail, key=lambda c: (bool(c.get("exact")), c.get("sim") or 0.0),
                    reverse=True)
    return [{"code": c.get("code"), "descriptor": c.get("descriptor"),
             "similarity": round(float(c.get("sim") or 0.0), 4),
             "exact_token_match": bool(c.get("exact")),
             "via_query": c.get("via_query") or ""}
            for c in ranked[:n]]


def resolve_one(base_descriptor: str, applied_transform: str = "", formula: str = "",
                sa_hint: str = "", freq_hint: str = "", axis: str = "shared",
                lag: str = "", plot_kind: str = "line") -> dict:
    """Resolve ONE series through the daily lane's resolver. A park is a normal return."""
    kind = _plot_kind(plot_kind, "resolve_series")
    spec = {"description": base_descriptor, "base_descriptor": base_descriptor,
            "applied_transform": applied_transform, "formula": formula,
            "sa_hint": sa_hint or "unknown", "freq_hint": freq_hint or "unknown",
            "axis": axis or "shared", "lag": lag, "plot_kind": kind}
    with quiet_stdout():
        slot = R.slot_from_series(0, spec)
        slot = _dlx_verify(R.resolve_slot(slot, **_resolve_kwargs()))

    resolved = slot.get("status") == R.SLOT_RESOLVED
    return {
        "status": "resolved" if resolved else "parked",
        "resolved": slot.get("resolved"),
        "codes": slot.get("codes") or [],
        "via": slot.get("bound_via_query") or "",
        "similarity": slot.get("relevance"),
        "exact_token_match": bool(slot.get("relevance_exact")),
        "candidates": _candidates(slot),
        "reason": slot.get("reason") or "",
        "needs_clarification": bool(slot.get("needs_clarification")),
        "freq_resolved": slot.get("freq_resolved") or "",
        "agg_resolved": slot.get("agg_resolved") or "",
        # Render-ready slot: hand this straight to `render_chart`'s `series` list once
        # the operator is happy with the bind. Composing the two tools this way is what
        # keeps Claude from re-typing a ticker it did not resolve.
        "slot": {"status": "resolved" if resolved else "pending",
                 "resolved": slot.get("resolved"),
                 "codes": slot.get("codes") or [],
                 "formula": slot.get("formula"),
                 "applied_transform": slot.get("applied_transform"),
                 "base_descriptor": slot.get("base_descriptor"),
                 "axis": slot.get("axis") or "shared",
                 "lag": slot.get("lag") or None,
                 "plot_kind": kind},
    }


# ──────────────────────────────── rendering ─────────────────────────────────
def _axis_block(lo, hi) -> dict:
    return {"min": lo, "max": hi}


def build_row(series: list[dict], *, title: str = "", subtitle: str = "",
              st_force: bool = False, no_title: bool = False,
              sample_start: str = "", axis_mode: str = "shared",
              recession_shading: bool = False,
              left_min=None, left_max=None, right_min=None, right_max=None,
              x_tick_years=None, x_label_fmt: str = "", end_series: str = "",
              x_pad_periods=None) -> dict:
    """Assemble the `row` dict `build_chart.render_row` expects (§13.4 schema table).

    Pure translation — every key here is one `render_row` already reads. Slots are
    passed through with `status='resolved'` because `render_row` refuses to draw a
    partially-resolved chart (the Defect-2 property, which we inherit rather than
    re-check)."""
    slots = []
    for i, s in enumerate(series):
        slot = {k: v for k, v in s.items() if v not in (None, "")}
        slot["idx"] = i
        slot["status"] = "resolved"
        slot.setdefault("axis", "shared")
        slot["plot_kind"] = _plot_kind(slot.get("plot_kind"), f"series[{i}]")
        if not slot.get("formula") and not slot.get("resolved"):
            raise ValueError(
                f"series[{i}] has neither `resolved` nor `formula`+`codes`. Resolve it "
                f"with resolve_series first — do not hand-type a ticker.")
        if slot.get("formula") and not slot.get("codes"):
            raise ValueError(
                f"series[{i}] has a formula but no `codes`: every mnemonic in the "
                f"formula needs its confirmed code@db, which resolve_series returns.")
        slots.append(slot)

    chart_spec: dict[str, Any] = {
        "axis_mode": "dual" if axis_mode == "dual" else "shared",
        "recession_shading": bool(recession_shading),
        "left_axis": _axis_block(left_min, left_max),
        "right_axis": _axis_block(right_min, right_max),
    }
    if sample_start:
        chart_spec["sample_start"] = sample_start
    if end_series:
        chart_spec["end_series"] = end_series
    if x_pad_periods is not None:
        chart_spec["x_pad_periods"] = x_pad_periods
    if x_tick_years is not None:
        chart_spec["x_tick_years"] = x_tick_years
    if x_label_fmt:
        chart_spec["x_label_fmt"] = x_label_fmt

    return {"chart_spec": chart_spec, "series": slots, "subtitle": subtitle,
            "st_force": bool(st_force), "no_title": bool(no_title),
            "chosen_title": title}


def _save_path(filename: str = "", day: Optional[str] = None) -> Path:
    """`outputs/chat/<YYYY-MM-DD>/<name>.png` — the chat lane's own namespace.

    Never a `data/backfill_*/renders/` path and never `MMDDYYYY/<release_slug>/`:
    the two lanes must not share an output namespace, or a chat experiment can
    overwrite a shipped daily render."""
    day = day or date.today().isoformat()
    stem = Path(filename or "chart").stem or "chart"
    stem = "".join(ch for ch in stem if ch.isalnum() or ch in "-_") or "chart"
    out = OUT_ROOT / day
    out.mkdir(parents=True, exist_ok=True)
    return out / f"{stem}.png"


def render(series: list[dict], *, filename: str = "", **row_kw) -> dict:
    """Build the row and call `build_chart.render_row`. Returns its result + the path.

    Exceptions are NOT caught. An unmapped transform, a raw-mnemonic legend, duplicate
    legends and a partially-resolved chart all raise inside the shared code, and those
    raises are the guardrails (§13.7) — swallowing one here would reintroduce exactly
    the silent-wrong class the daily lane spent months eliminating."""
    row = build_row(series, **row_kw)
    path = _save_path(filename)
    with quiet_stdout():
        info = BC.render_row(row, str(path))
    return {"ok": True, "path": str(path), "plotted": list(info.get("plotted") or []),
            "end": info.get("end"), "row": row,
            "_drawn": info.get("drawn") or [], "_freq": info.get("common_freq")}


# ─────────────────────────────── validation ─────────────────────────────────
def last_value_check(rendered: dict, printed: dict, value_tol: float = 0.05) -> list:
    """Compare each drawn line's last value to the value the SOURCE chart prints.

    `printed` maps a legend label (or a unique fragment of one) to the number read off
    the source chart. Runs `validate.check_last_value` on the series that was actually
    drawn, not on a re-derivation of it: a window z-score evaluated over a different
    window is a different number, so re-deriving would validate the wrong thing.

    This is the independent check that the reconstruction is the SAME series, not
    merely a plausible-looking one — the 2026-07-30 `ptfneh` mis-bind produced a chart
    that looked entirely right."""
    import pandas as pd

    out = []
    for ps in rendered.get("_drawn") or []:
        want = next((v for k, v in printed.items() if k.lower() in ps.label.lower()),
                    None)
        s = ps.series.dropna()
        row = {"label": ps.label, "last_date": str(s.index.max().date()),
               "reconstructed": round(float(s.iloc[-1]), 4), "printed": want}
        if want is None:
            row["check"] = "skipped — no printed value supplied"
        else:
            # LastValueMismatch is RECORDED, not swallowed: the report must show every
            # line's verdict rather than aborting on the first bad one, and a recorded
            # FAIL is still a loud, blocking result.
            try:
                with quiet_stdout():
                    V.check_last_value(
                        ps.series, float(want), pd.Timestamp(rendered["end"]),
                        rendered.get("_freq") or "M", value_tol, label=ps.label)
                row["check"] = "PASS"
            except V.LastValueMismatch as exc:
                row["check"] = f"FAIL — {exc}"
        out.append(row)
    return out
