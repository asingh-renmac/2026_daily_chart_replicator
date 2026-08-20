"""Local stdio FastMCP server — the chat lane for RenMac chart reconstruction (§13).

Two tools that wrap this repo's existing chart pipeline so Claude Desktop can drive it
from a pasted Haver screenshot. No chart logic lives here; see `lane.py`.

Run with the shared venv (it has matplotlib, pandas, Haver and fastmcp):

    C:/Users/asingh/envs/shared-3.10/Scripts/python.exe haver_chart/server.py

The rules below are duplicated into every tool docstring on purpose: a tool
description is the ONLY guidance an MCP client loads automatically. A skill does
nothing until it is installed and a system prompt nothing until it is pasted.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastmcp import FastMCP                       # noqa: E402
from fastmcp.exceptions import ToolError          # noqa: E402
from fastmcp.tools.tool import ToolResult         # noqa: E402
from fastmcp.utilities.types import Image         # noqa: E402

from haver_chart import lane                      # noqa: E402

# mask_error_details stays FALSE. The pipeline's raises ARE the product here — an
# operator who sees "unmappable applied_transform 'Avg, % p.a.'" can restate the
# transform, whereas a masked "tool failed" turns a precise guardrail into a dead end.
mcp = FastMCP("haver-chart")


@mcp.tool
def resolve_series(
    base_descriptor: str,
    applied_transform: str = "",
    formula: str = "",
    sa_hint: str = "",
    freq_hint: str = "",
    axis: str = "shared",
    lag: str = "",
    plot_kind: str = "line",
) -> dict:
    """Bind ONE series printed on a Haver chart to a confirmed `code@database`.

    NEVER guess or hand-type a ticker — always come through this tool. It runs the same
    resolver the production daily lane runs (catalog search, exact-token-set relevance
    gate, SA + aggregation cross-checks, DLX confirmation), so the chat lane binds what
    the daily lane would bind.

    Pass what is PRINTED ON THE CHART, not your interpretation of it:
      * `base_descriptor` — the series name exactly as it appears (e.g.
        "Philly Fed Mfg Business Outlook: Current Activity Diffusion Index").
      * `formula` — if the chart prints a formula, pass it VERBATIM ("zs(yryr%(IP))").
        Do not paraphrase it into words; the parser understands Haver's own syntax and
        paraphrasing loses the nesting.
      * `applied_transform` — only for a words-only chart ("% Change - Year to Year",
        "3-month moving average", "Z-Score"). Haver's aggregation/units line
        ("Avg, % p.a.", "Sum, Mil.$", "EOP, Index") is NOT a transform: it describes
        how the series is stored. Passing it as one is how a level got plotted as a
        percent change on 2026-08-11.
      * `sa_hint` — "sa" or "nsa" when the chart says so; this is a HARD cross-check.
      * `lag` — a Haver bracket tag: "[-4]" lags 4 observations, "[+4]" leads 4.

    A PARK IS A NORMAL RESULT, NOT AN ERROR. If `status` is "parked", show the operator
    the `candidates` (top 3, with `similarity` and `exact_token_match`) and ASK which is
    right. Never silently bind the top hit: `DFBACTS` vs `DFBACTDS` scored 0.909
    similarity on the WRONG directional sibling, which is why `exact_token_match` —
    not `similarity` — is the column that justifies a bind.

    Returns: status (resolved|parked), resolved (`code@db`), codes, via, similarity,
    exact_token_match, candidates, reason, freq_resolved, agg_resolved, and `slot` —
    a render-ready block to pass straight into `render_chart`'s `series` list.

    Read-only: this tool never writes the learning stores.
    """
    try:
        return lane.resolve_one(
            base_descriptor=base_descriptor, applied_transform=applied_transform,
            formula=formula, sa_hint=sa_hint, freq_hint=freq_hint, axis=axis,
            lag=lag, plot_kind=plot_kind)
    except Exception as exc:
        raise ToolError(f"{type(exc).__name__}: {exc}")


@mcp.tool
def render_chart(
    series: list[dict],
    title: str = "",
    subtitle: str = "",
    st_force: bool = False,
    no_title: bool = False,
    sample_start: str = "",
    axis_mode: str = "shared",
    recession_shading: bool = False,
    left_min: float | None = None,
    left_max: float | None = None,
    right_min: float | None = None,
    right_max: float | None = None,
    x_tick_years: int | None = None,
    x_label_fmt: str = "",
    end_series: str = "",
    x_pad_periods: int | None = None,
    filename: str = "",
) -> ToolResult:
    """Render resolved series as a RenMac-styled chart. Returns the PNG inline + its path.

    `series` is a list of the `slot` blocks `resolve_series` returned — pass them
    through rather than retyping. Each needs `resolved` (`code@db`) OR
    `formula`+`codes`, plus optionally `applied_transform`, `axis` ("shared"|"L"|"R"),
    `lag`, `plot_kind` ("line"|"bar"|"stacked_bar") and `proposed_legend`.

    LEGENDS. Every series needs a human-readable label. If the series is not already in
    the ratified legend store, set `proposed_legend` to a brief label built from the
    series' real descriptor, keeping the qualifiers that change meaning (SA/NSA, units,
    base year, $/hour). If no confirmed label exists the render RAISES rather than
    printing the raw mnemonic — that fallback was a real defect, not a convenience.

    TRANSFORMS. The transform label is non-suppressible: it is appended to the subtitle
    so a transformed chart always says what was done to it. If your subtitle already
    states the transform, set `st_force=True` to use it verbatim and avoid the
    duplicate. An unmapped transform phrase RAISES — restate it in Haver's own wording
    rather than working around it.

    AXES. `axis_mode="dual"` puts series with `axis="R"` on the right and tags every
    legend LHS/RHS. Leave the min/max arguments empty unless you are matching a source
    chart's printed axis: the renderer expands the range rather than clipping data.

    SHOW THE OPERATOR THE IMAGE AND ASK BEFORE CALLING IT DONE. This is the point of
    the lane — the render is the approval gate. Then reproduce the last value the source
    chart prints; if it disagrees, something is bound or transformed wrong.

    Errors are deliberate guardrails. "render needs every slot resolved" means a slot
    parked — go back to `resolve_series`. Duplicate legends, raw-mnemonic legends and
    unmapped transforms all raise for the same reason: a wrong chart that looks right is
    worse than no chart.

    Writes to `outputs/chat/<date>/`. Never touches the daily lane's renders or ledger.
    """
    try:
        out = lane.render(
            series, filename=filename, title=title, subtitle=subtitle,
            st_force=st_force, no_title=no_title, sample_start=sample_start,
            axis_mode=axis_mode, recession_shading=recession_shading,
            left_min=left_min, left_max=left_max, right_min=right_min,
            right_max=right_max, x_tick_years=x_tick_years,
            x_label_fmt=x_label_fmt, end_series=end_series,
            x_pad_periods=x_pad_periods)
    except Exception as exc:
        raise ToolError(f"{type(exc).__name__}: {exc}")
    # ToolResult, not a plain return: the operator needs to SEE the chart (image
    # content) while Claude needs the path and the plotted labels (structured
    # content). Returning a bare list makes FastMCP infer an output schema and then
    # reject the mixed image/data payload against it.
    summary = {"path": out["path"], "plotted": out["plotted"], "end": out["end"]}
    return ToolResult(content=[Image(path=out["path"]).to_image_content()],
                      structured_content=summary)


if __name__ == "__main__":
    mcp.run()
