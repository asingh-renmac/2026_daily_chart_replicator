"""FastMCP server — the chat lane for RenMac chart reconstruction (§13, §14).

Two tools that wrap this repo's existing chart pipeline so Claude can drive it from a
pasted Haver screenshot. No chart logic lives here; see `lane.py`.

DEFAULT TRANSPORT IS STDIO. Run with the shared venv (it has matplotlib, pandas, Haver
and fastmcp):

    C:/Users/asingh/envs/shared-3.10/Scripts/python.exe haver_chart/server.py

Set `HAVER_CHART_HTTP=1` to serve Streamable HTTP on loopback behind Entra OAuth instead
(§14.4), for a Windows host that has DLX. Keeping stdio the default is a hard invariant:
every teammate zip already in the field spawns this file with no environment beyond the
three path variables, and none of them may change behaviour.

The rules below are duplicated into every tool docstring on purpose: a tool
description is the ONLY guidance an MCP client loads automatically. A skill does
nothing until it is installed and a system prompt nothing until it is pasted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reads `config/.env` and resolves the three relocatable paths as a side effect of the
# import. Done explicitly and early — `lane` below would trigger it anyway, but
# `HTTP_ENABLED` and `_build_auth` depend on the file having been read, and an ordering
# dependency that subtle should not rest on the import graph staying as it is today.
from haver_chart import bootstrap                 # noqa: E402,F401

from fastmcp import FastMCP                       # noqa: E402
from fastmcp.exceptions import ToolError          # noqa: E402
from fastmcp.tools.tool import ToolResult         # noqa: E402
from fastmcp.utilities.types import Image         # noqa: E402
from mcp.types import TextContent                 # noqa: E402

from haver_chart import lane                      # noqa: E402

_TRUTHY = {"1", "true", "on", "yes"}

# Transport switch, read once at import (§14.4a). Unset = stdio = today's behaviour.
HTTP_ENABLED = os.environ.get("HAVER_CHART_HTTP", "").lower() in _TRUTHY


def _build_auth():
    """Entra OAuth for HTTP mode, or None for stdio (§14.7).

    Claude's custom connector brokers the connection through Anthropic's cloud and
    requires OAuth 2.1 with Dynamic Client Registration and PKCE. Entra does not
    implement DCR, so `AzureProvider` is an OAuth *proxy*: it presents DCR to Claude
    while using one fixed app registration upstream. That is why a bearer token is not
    an option here.

    Fails fast on a missing secret. A server that came up unauthenticated on a port a
    tunnel is about to publish is worse than one that refuses to start, and the DLX
    entitlement behind it belongs to one licensed user (§14.7).

    Own variable names rather than the metadata server's bare `AZURE_*`, `HAVER_PUBLIC_URL`
    and `HAVER_HTTP_PORT`, which would collide outright if two servers on one host ever
    shared a `config/.env`. Note this is about the VARIABLES, not the registration: an
    Entra app accepts many redirect URIs, so one app can front several servers, and
    `2026_haver_mcp` §17.4 has haver-data reuse this very registration.
    """
    if not HTTP_ENABLED:
        return None
    from fastmcp.server.auth.providers.azure import AzureProvider

    required = ("HAVER_CHART_AZURE_CLIENT_ID", "HAVER_CHART_AZURE_TENANT_ID",
                "HAVER_CHART_AZURE_CLIENT_SECRET", "HAVER_CHART_PUBLIC_URL",
                "HAVER_CHART_JWT_SIGNING_KEY")
    missing = [n for n in required if not os.environ.get(n)]
    if missing:
        raise RuntimeError(
            "HAVER_CHART_HTTP is set but these required env vars are missing: "
            + ", ".join(missing))
    return AzureProvider(
        client_id=os.environ["HAVER_CHART_AZURE_CLIENT_ID"],
        client_secret=os.environ["HAVER_CHART_AZURE_CLIENT_SECRET"],
        tenant_id=os.environ["HAVER_CHART_AZURE_TENANT_ID"],
        base_url=os.environ["HAVER_CHART_PUBLIC_URL"],
        required_scopes=["read"],
        # Fixed so FastMCP-issued tokens survive a restart and a teammate is not sent
        # back through the browser sign-in every time the service recycles.
        jwt_signing_key=os.environ["HAVER_CHART_JWT_SIGNING_KEY"],
    )


# mask_error_details is FALSE on stdio and TRUE over HTTP, and the reasoning that made it
# False still holds. The pipeline's raises ARE the product — an operator who sees
# "unmappable applied_transform 'Avg, % p.a.'" can restate the transform, whereas a masked
# "tool failed" turns a precise guardrail into a dead end. Masking does not touch that:
# every intentional guardrail reaches the client as ToolError(lane.explain(...)), and
# FastMCP passes ToolError messages through regardless. What masking suppresses is the
# UNINTENTIONAL exception — a stack trace, a C:\Users\... path, a psycopg error carrying a
# connection string. On stdio those went to one trusted local operator; on a public
# endpoint they are a leak (§14.4c).
mcp = FastMCP("haver-chart", mask_error_details=HTTP_ENABLED, auth=_build_auth())


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    """Unauthenticated liveness for the tunnel and uptime checks. No DB, no DLX.

    Reports render liveness, not just process liveness: a render stuck on the DLX login
    modal (§14.16) holds the lock forever while the process happily keeps answering HTTP,
    so a plain 200 is exactly how that failure would hide."""
    from starlette.responses import JSONResponse
    return JSONResponse(lane.health())


@mcp.custom_route("/chart/{chart_id}", methods=["GET"])
async def chart(request):
    """Serve a rendered PNG so a remote operator can get a FILE, not just an image in a
    transcript (§14.20).

    The inline image is enough to look at and useless to put in a newsletter: the file
    is on the server, and before this the only route to a copy was saving it out of the
    conversation by hand.

    UNAUTHENTICATED, DELIBERATELY, and the id is what makes that defensible. The whole
    point is a link that opens in a browser or drops into a document, and a browser
    following a link carries no bearer token — requiring one would leave the operator
    hand-crafting curl commands, i.e. not solving the problem. So the URL is the
    capability: `_save_path` names every file with a full uuid4, and 122 bits is not
    reachable by guessing at any rate the edge would tolerate.

    What that buys and what it costs, stated plainly, because it is a real trade:
    anyone holding the link can fetch that one chart until the retention sweep removes
    it. No id is derivable from another; nothing enumerates the directory; a wrong id is
    a flat 404 that reveals nothing. If a chart is ever too sensitive for that, the
    inline image still works and this route does not have to be used.
    """
    from starlette.responses import FileResponse, PlainTextResponse
    path = lane.chart_path(request.path_params.get("chart_id", ""))
    if path is None:
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=3600"})


def _park_text(out: dict) -> str:
    """Text block for a resolve result, carrying the next action when a slot parks.

    Built after §16 v1 shipped and did nothing. The model hit a park and asked the
    operator to type a ticker in prose, which was CORRECT behaviour — the docstring said
    to do exactly that, and a docstring written before the picker existed is what the
    model had. Adding a tool does not tell anyone the tool is there.

    Same reasoning as `_result_text` (G20a): a tool description is read once per
    connection and a skill does nothing until someone installs it, whereas this text
    arrives with EVERY result. It is the only guidance a client cannot skip.

    Degrades honestly. Over stdio there is no picker and no UI host, so the instruction
    reverts to asking in prose rather than naming a tool the model cannot see.
    """
    lines = [f"status: {out.get('status')}"]
    if out.get("resolved"):
        lines.append(f"resolved: {out['resolved']}")
    if out.get("reason"):
        lines.append(f"reason: {out['reason']}")
    for cand in out.get("candidates") or []:
        lines.append(f"candidate: {cand.get('code')} "
                     f"(exact={cand.get('exact_token_match')}, "
                     f"sim={cand.get('similarity')}) {cand.get('descriptor') or ''}")
    if out.get("status") != "parked":
        return "\n".join(lines)
    if HTTP_ENABLED:
        lines.append(
            "ACTION REQUIRED — this slot PARKED. Call `pick_series` now, with the SAME "
            "base_descriptor and the same applied_transform/formula you passed here. It "
            "shows the operator a table of candidates with source, start date, LIVE end "
            "date and observation count, and their click records the binding for you. Do "
            "NOT ask them to type a ticker in prose, and do NOT bind a candidate "
            "yourself. Wait for their choice — it comes back to you as a message. "
            "Pass `original_request` (their request VERBATIM, not your paraphrase) and "
            "`remaining_parks` (how many slots parked in total, including this one) so "
            "the panel can resume the work on a click without them retyping it.")
    else:
        lines.append(
            "ACTION REQUIRED — this slot PARKED. Show the operator the candidates above "
            "and ask which is right. Never bind the top hit on similarity alone.")
    return "\n".join(lines)


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
    """Bind ONE series description to a confirmed `code@database`.

    NEVER guess or hand-type a ticker — always come through this tool. It runs the same
    resolver the production daily lane runs (catalog search, exact-token-set relevance
    gate, SA + aggregation cross-checks, DLX confirmation), so the chat lane binds what
    the daily lane would bind. When the evidence is thin it PARKS rather than guessing;
    follow a park with `pick_series` and let the operator choose.

    USE IT FOR ANY DESCRIPTION, not only for chart replication. A plain data pull
    ("PPI for processed goods") needs resolving exactly as a chart does, and the data
    lane's `get_observations` refuses a ticker picked out of catalog search results —
    `search_series` ranks on text similarity, which cannot tell a directional sibling or
    an NSA twin from the series the words asked for. For a request with no chart behind
    it, pass the operator's words as `base_descriptor` and leave the rest empty.

    When there IS a source chart, pass what is PRINTED ON IT, not your interpretation:
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
      * `plot_kind` — read the SHAPE off the chart: "bar" for a bar or column chart,
        "stacked_bar" for a stacked contribution chart, "line" otherwise. It rides
        through in the returned `slot`, so set it here and `render_chart` draws it.
        Anything else raises rather than quietly drawing a line.

    A PARK IS A NORMAL RESULT, NOT AN ERROR. When `status` is "parked", follow the
    ACTION REQUIRED line in the text of this tool's result — where a `pick_series` tool
    exists, that means handing the choice to the operator as a panel instead of asking in
    prose. Never silently bind the top hit: `DFBACTS` vs `DFBACTDS` scored 0.909
    similarity on the WRONG directional sibling, which is why `exact_token_match` —
    not `similarity` — is the column that justifies a bind.

    Returns: status (resolved|parked), resolved (`code@db`), codes, via, similarity,
    exact_token_match, candidates, reason, freq_resolved, agg_resolved, and `slot` —
    a render-ready block to pass straight into `render_chart`'s `series` list.

    Never writes the daily learning stores. If `from_chat_memory` is true, this bind
    came from a park THIS operator resolved earlier (see `remember_binding`).
    """
    try:
        out = lane.resolve_one(
            base_descriptor=base_descriptor, applied_transform=applied_transform,
            formula=formula, sa_hint=sa_hint, freq_hint=freq_hint, axis=axis,
            lag=lag, plot_kind=plot_kind)
    except Exception as exc:
        raise ToolError(lane.explain(exc))
    return ToolResult(content=[TextContent(type="text", text=_park_text(out))],
                      structured_content=out)


@mcp.tool
def remember_binding(base_descriptor: str, code_at_db: str, note: str = "") -> dict:
    """Remember that `base_descriptor` means `code_at_db`, so the same park never
    re-asks in a later chat.

    CALL THIS ONLY AFTER THE OPERATOR HAS CHOSEN. It records a human decision; it is
    not a way to make your own pick stick. Two situations, both from the operator's
    explicit answer:

      * `resolve_series` parked and the operator said which candidate is right;
      * `resolve_series` bound the WRONG series automatically and the operator gave
        the correct ticker. The remembered answer then overrides the automatic one.

    Do NOT call it on a ticker you inferred, or on one the operator has not looked at.
    The code is DLX-confirmed before it is stored, which proves the series exists — it
    cannot prove the series is the one they wanted.

    The store is private to the signed-in operator and is layered over the daily
    learning stores at lookup time; it never writes into them.

    Returns: stored, operator, description, code, dlx_descriptor (what DLX says that
    ticker actually is — worth showing the operator as a receipt), store path, added.
    """
    try:
        return lane.remember_binding(base_descriptor=base_descriptor,
                                     code_at_db=code_at_db, note=note)
    except Exception as exc:
        raise ToolError(lane.explain(exc))


@mcp.tool
def forget_binding(base_descriptor: str) -> dict:
    """Remove a remembered binding for `base_descriptor` from this operator's chat
    memory, so the next resolve asks again.

    Use when a remembered bind turns out to be wrong. Only this operator's chat memory
    is editable — a bind inherited from the daily learning stores cannot be removed
    here, and `removed: false` with a note says so.

    Returns: removed (bool), operator, description, store path, note.
    """
    try:
        return lane.forget_binding(base_descriptor=base_descriptor)
    except Exception as exc:
        raise ToolError(lane.explain(exc))


def _result_text(summary: dict, link_key: str) -> str:
    """Mirror a render summary into the text block, link first.

    Image content alone is not enough. Claude's connector forwards `content` to the
    model and often drops `structured_content`, at which point the model honestly says
    it has no link — which is exactly what happened while the GET route was already
    serving 200s. Mirroring the WHOLE summary into text is what the MCP spec recommends
    anyway, so a client reading only one of the two fields loses nothing, and it also
    defuses the inverse client bug where a present `structured_content` causes `content`
    — images included — to be dropped.

    The instruction rides in-band deliberately. A tool description is read once per
    connection and a skill does nothing until someone installs it; this text arrives
    with EVERY result, so it is the only guidance a client cannot skip. It has to exist
    because the image is collapsed into a closed tool panel by default: a chart whose
    link the model never printed is a chart the operator cannot reach.

    Pure and separate from the tool so the G20a guarantee is testable without a live
    render — otherwise the only thing standing behind "every chart is reachable" is a
    manual look at one reply.
    """
    ordered = [link_key] + [k for k in summary if k != link_key]
    lines = [f"{k}: {summary[k]}" for k in ordered]
    lines.append(
        f"ACTION REQUIRED — print the {link_key} above in your reply to the operator, "
        f"one line per chart. The inline image is collapsed by default in this client, "
        f"so an unprinted {link_key} is a chart they cannot reach. After rendering "
        f"several charts, list every one, not only the last.")
    return "\n".join(lines)


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
    duplicate. An unmapped transform phrase RAISES, and the error lists the wordings
    that are recognized — restate it in one of those rather than working around it.

    PLOT TYPE. Look at the source chart before you call this: `plot_kind="bar"` for a
    bar or column chart, `"stacked_bar"` for a stacked contribution chart, `"line"`
    otherwise. Set it per series. Before 2026-07-30 the pipeline could not represent
    this at all and silently redrew every bar chart as lines, so it is worth a glance.
    An unrecognized value raises here rather than falling back to a line.

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

    Never touches the daily lane's renders or ledger. On stdio the result carries the
    PNG's `path` on this machine. Over HTTP it carries a `chart_id` and a `chart_url`
    instead, because the file is on the server and a path would be meaningless to the
    person reading it.

    ALWAYS PRINT THE `chart_url` (or `path`) IN YOUR REPLY, ONE LINE PER CHART. This
    client collapses the inline image into a closed tool panel, so a chart whose link
    you did not write out is one the operator can neither see nor keep. After rendering
    several charts, list every link — not only the last one.
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
        raise ToolError(lane.explain(exc))
    # ToolResult, not a plain return: the operator needs to SEE the chart (image
    # content) while Claude needs the path and the plotted labels (structured
    # content). Returning a bare list makes FastMCP infer an output schema and then
    # reject the mixed image/data payload against it.
    summary = {"plotted": out["plotted"], "end": out["end"]}
    # A server-local absolute Windows path is worse than useless to a remote caller: the
    # model will cheerfully tell a phone user their chart is at C:\Users\... . The image
    # itself is inline base64, so nothing is lost by withholding it (§14.4e).
    if HTTP_ENABLED:
        summary["chart_id"] = Path(out["path"]).stem
        # The downloadable link, assembled here rather than left for the model to guess:
        # a hallucinated URL for a chart that DOES exist is a uniquely annoying failure.
        summary["chart_url"] = (os.environ["HAVER_CHART_PUBLIC_URL"].rstrip("/")
                                + f"/chart/{summary['chart_id']}")
    else:
        summary["path"] = out["path"]
    note = TextContent(type="text",
                       text=_result_text(summary, "chart_url" if HTTP_ENABLED else "path"))
    return ToolResult(
        content=[Image(path=out["path"]).to_image_content(), note],
        structured_content=summary,
    )


# ---------------------------------------------------------------------------
# §16 — the parked-slot picker.
#
# HTTP only: a UI host is what makes this worth anything, and the stdio teammate zips
# would only gain a tool they cannot display.
#
# The four rules the G20b probes paid for, all of them load-bearing here, and all four
# failing SILENTLY — the host reports success, or blames a layer that is working
# (plan.md §16.3). The probes themselves are gone; the picker is now its own smoke test,
# since a rendered panel exercises every one of these.
#   1. `app=AppConfig(...)`, never `app=True` — a resource's `_meta.ui` must be an OBJECT
#   2. emit the deprecated flat `_meta["ui/resourceUri"]` beside the nested one
#   3. the view MUST run the `ui/initialize` handshake
#   4. the view MUST report its own height, or the frame stays at zero
if HTTP_ENABLED:
    from fastmcp import Context                       # noqa: E402
    from fastmcp.apps import AppConfig                # noqa: E402

    _PICKER_URI = "ui://haver-chart/pick-series.html"

    # Data is written with textContent and createElement, never innerHTML. The rows carry
    # DLX descriptors — text this server did not author — and a picker that pasted them as
    # markup would be an injection path into the operator's own panel.
    _PICKER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>pick a series</title>
<style>
 body{font:13px system-ui,sans-serif;margin:0;padding:14px;background:#0f172a;color:#e2e8f0}
 h1{font-size:15px;margin:0 0 2px}
 .sub{color:#94a3b8;margin-bottom:10px}
 table{border-collapse:collapse;width:100%}
 th{text-align:left;font-size:11px;text-transform:uppercase;color:#94a3b8;
    border-bottom:1px solid #334155;padding:5px 6px}
 td{padding:5px 6px;border-bottom:1px solid #1e293b;vertical-align:top}
 tr.pick{cursor:pointer}
 tr.pick:hover{background:#1e293b}
 .code{font-family:ui-monospace,Consolas,monospace;color:#7dd3fc;white-space:nowrap}
 .tag{font-size:10px;background:#166534;color:#dcfce7;padding:1px 5px;border-radius:8px}
 .man{margin-top:12px}
 input[type=text]{background:#1e293b;border:1px solid #334155;color:#e2e8f0;
                  padding:6px;border-radius:5px;width:230px;font-family:ui-monospace,monospace}
 button{margin-top:12px;background:#2563eb;color:#fff;border:0;padding:8px 16px;
        border-radius:6px;font-size:13px;cursor:pointer}
 button[disabled]{background:#334155;color:#94a3b8;cursor:default}
 button.ghost{background:transparent;color:#94a3b8;border:1px solid #334155;margin-left:8px}
 button.ghost:hover{color:#e2e8f0;border-color:#475569}
 .msg{margin-top:10px;padding:8px;border-radius:6px;background:#1e293b;white-space:pre-wrap}
 .err{background:#7f1d1d;color:#fee2e2}
 .ok{background:#14532d;color:#dcfce7}
</style></head>
<body>
 <h1 id="title">Pick a series</h1>
 <div class="sub" id="why">Waiting for candidates...</div>
 <table><thead><tr>
   <th></th><th>Ticker</th><th>Description</th><th>Source</th>
   <th>Start</th><th>Live end</th><th>Obs</th><th>Freq</th><th>Adj</th>
 </tr></thead><tbody id="rows"></tbody></table>
 <div class="man">Not listed &mdash; enter a ticker:
   <input type="text" id="manual" placeholder="CODE@DATABASE"></div>
 <button id="go" disabled>Save &amp; continue</button>
 <button id="nope" class="ghost">None of these &mdash; stop</button>
 <div id="msg"></div>
<script>
(function () {
  var nextId = 2, pending = {}, data = null, chosen = null, done = false;

  function send(m) { window.parent.postMessage(m, "*"); }
  function report() {
    send({jsonrpc: "2.0", method: "ui/notifications/size-changed",
          params: {width: document.documentElement.scrollWidth,
                   height: document.documentElement.scrollHeight}});
  }
  function request(method, params) {
    var id = nextId++;
    send({jsonrpc: "2.0", id: id, method: method, params: params});
    return new Promise(function (resolve, reject) { pending[id] = {ok: resolve, no: reject}; });
  }
  function note(text, cls) {
    var box = document.getElementById("msg");
    box.textContent = text;
    box.className = cls ? "msg " + cls : "msg";
    report();
  }
  function cell(row, text, cls) {
    var td = document.createElement("td");
    if (cls) { td.className = cls; }
    td.textContent = text == null ? "" : String(text);
    row.appendChild(td);
    return td;
  }

  function draw() {
    var list = (data && data.candidates) || [];
    document.getElementById("title").textContent = "Pick a series for: " + (data.description || "");
    // The adjustment note is shown, not silent. An operator who cannot see that the list
    // was reordered has no way to know an NSA copy exists below the cut.
    document.getElementById("why").textContent =
      (data.reason || "") + (data.sa_note ? " \\u2014 " + data.sa_note : "");
    var body = document.getElementById("rows");
    body.textContent = "";
    list.forEach(function (c, i) {
      var tr = document.createElement("tr");
      tr.className = "pick";
      var pick = document.createElement("td");
      var radio = document.createElement("input");
      radio.type = "radio"; radio.name = "cand"; radio.value = c.code;
      pick.appendChild(radio);
      tr.appendChild(pick);
      var codeCell = cell(tr, c.code, "code");
      if (c.exact) {
        var tag = document.createElement("span");
        tag.className = "tag"; tag.textContent = "exact";
        codeCell.appendChild(document.createTextNode(" "));
        codeCell.appendChild(tag);
      }
      cell(tr, c.descriptor);
      cell(tr, c.source);
      cell(tr, c.start);
      cell(tr, c.end);
      cell(tr, c.obs);
      cell(tr, c.frequency);
      cell(tr, c.sa ? c.sa.toUpperCase() : "?");
      function choose() {
        radio.checked = true;
        chosen = c.code;
        document.getElementById("manual").value = "";
        document.getElementById("go").disabled = false;
      }
      tr.addEventListener("click", choose);
      radio.addEventListener("change", choose);
      body.appendChild(tr);
      if (i === 0 && list.length === 1) { choose(); }
    });
    report();
  }

  document.getElementById("manual").addEventListener("input", function (e) {
    var v = e.target.value.trim();
    if (v) {
      chosen = v;
      var r = document.querySelector("input[name=cand]:checked");
      if (r) { r.checked = false; }
    } else {
      chosen = null;
    }
    document.getElementById("go").disabled = !chosen;
  });

  function tell(text) {
    // A REQUEST, awaited -- not fire-and-forget. This was sent with an id and no pending
    // handler, so the host's response, error included, went in the bin. When Save &
    // continue did nothing on 2026-09-08 there was no way to tell a host that had
    // declined ui/message from one that had never received it. Nothing about ui/message
    // support is discoverable up front either: HostCapabilities has no flag for it, so
    // sending it and reading the answer is the ONLY way to know.
    //
    // The content shape is retried as an array on failure. The spec says an object, but
    // shipped hosts differ, and this particular client already needed the deprecated
    // flat `ui/resourceUri` key to render at all -- so it has form.
    return request("ui/message",
                   {role: "user", content: {type: "text", text: text}})
      .catch(function () {
        return request("ui/message",
                       {role: "user", content: [{type: "text", text: text}]});
      })
      .then(function () { return true; })
      .catch(function (err) {
        // Say so IN the panel. The binding is already saved, so the operator is one
        // sentence away from finishing by hand -- but only if they are told.
        note(document.getElementById("msg").textContent +
             "\\n\\nThis chat client would not accept the follow-up (" + err + "), so " +
             "the request was not re-run. The binding IS saved: ask again in chat and it " +
             "will bind without showing this panel.", "err");
        return false;
      });
  }

  function ageMinutes() {
    // Server-stamped; a panel cannot be trusted to date itself. Missing means unknown,
    // and unknown is treated as fresh -- refusing to act would be worse than acting.
    if (!data || !data.issued) { return 0; }
    var t = Date.parse(data.issued);
    if (isNaN(t)) { return 0; }
    return (Date.now() - t) / 60000;
  }

  function handBack(code, key) {
    var what = "I picked " + code + " for \\u201c" + data.description +
               "\\u201d and it is saved.";
    var left = (data.remaining_parks || 1) - 1;

    // D15. Every panel re-running the request would render a three-park commentary three
    // times, twice with slots still unresolved. Only the last one resumes.
    if (left > 0) {
      tell(what + " There are still " + left + " parked slot(s) in my request. " +
           "Resolve those FIRST with pick_series and do not render anything yet.");
      note(document.getElementById("msg").textContent +
           "\\n" + left + " slot(s) still parked \\u2014 the chart runs once those are picked.", "ok");
      return;
    }

    // D16. The panel stays live in scroll-back forever, so a click on an hour-old one
    // would re-run an hour-old request as if it were current. Save it, but do not act.
    if (ageMinutes() > 30) {
      tell(what + " Do NOT re-run anything from this old panel \\u2014 the binding is " +
           "stored and I will ask again if I still want it.");
      note(document.getElementById("msg").textContent +
           "\\nThis panel is over 30 minutes old, so the request was not re-run. " +
           "Ask again in chat and it will bind without asking.", "ok");
      return;
    }

    if (data.original_request) {
      // Quoting the request verbatim is the point of the feature: "continue" alone left
      // the model to guess what it was continuing. The circuit breaker matters as much --
      // without it a save that lands under a key the next lookup misses becomes an
      // endless park / pick / re-run loop, with the panel reappearing every time.
      tell(what + " Now re-run my original request exactly as I gave it: \\u201c" +
           data.original_request + "\\u201d. If \\u201c" + data.description +
           "\\u201d parks AGAIN after this, stop and tell me the save did not take" +
           (key ? " (it was stored under key \\u201c" + key + "\\u201d)" : "") +
           " \\u2014 do not open the picker for it a second time.");
    } else {
      tell(what + " Continue building the chart with it.");
    }
  }

  document.getElementById("nope").addEventListener("click", function () {
    if (done) { return; }
    done = true;
    // Stores NOTHING. An abandoned pick must leave no trace, or a shrug today becomes a
    // remembered decision that every future chat inherits.
    document.getElementById("go").disabled = true;
    document.getElementById("nope").disabled = true;
    note("Nothing saved. Telling the chat to stop and ask you.", "ok");
    tell("None of the candidates are right for \\u201c" + data.description +
         "\\u201d. Do not bind anything, do not guess, and do not retry the request. " +
         "Ask me how to proceed.");
  });

  document.getElementById("go").addEventListener("click", function () {
    if (!chosen || done) { return; }
    document.getElementById("go").disabled = true;
    note("Confirming " + chosen + " against DLX...");
    // remember_binding DLX-confirms before it stores, so a typed ticker cannot poison
    // the store. Its refusal is the useful answer, which is why the error is shown here
    // rather than swallowed.
    request("tools/call", {name: "remember_binding",
                           arguments: {base_descriptor: data.description,
                                       code_at_db: chosen}})
      .then(function (res) {
        var failed = res && res.isError;
        if (failed) {
          var why = "";
          try { why = res.content.map(function (b) { return b.text || ""; }).join(" "); }
          catch (e) { why = "DLX would not confirm it."; }
          note(why || "DLX would not confirm that ticker.", "err");
          document.getElementById("go").disabled = false;
          return;
        }
        done = true;
        var key = "";
        try { key = (res.structuredContent || {}).key || ""; } catch (e) { key = ""; }
        note("Saved. " + chosen + " is now bound to \\u201c" + data.description +
             "\\u201d and will not be asked again." +
             (key ? "\\nStored under key: " + key : ""), "ok");
        handBack(chosen, key);
      })
      .catch(function (err) {
        note("Could not save: " + err, "err");
        document.getElementById("go").disabled = false;
      });
  });

  window.addEventListener("message", function (e) {
    var d = (e && e.data) || {};
    if (d.id != null && pending[d.id]) {
      var p = pending[d.id]; delete pending[d.id];
      if (d.error) { p.no(d.error.message || JSON.stringify(d.error)); } else { p.ok(d.result); }
      return;
    }
    if (d.id === 1) {                       // initialize result
      send({jsonrpc: "2.0", method: "ui/notifications/initialized", params: {}});
      report();
      return;
    }
    if (d.method === "ui/notifications/tool-result") {
      var p = d.params || {};
      data = p.structuredContent || null;
      if (data) { draw(); }
      else { note("No candidate data arrived with the tool result.", "err"); }
    }
  });

  send({jsonrpc: "2.0", id: 1, method: "ui/initialize",
        params: {protocolVersion: "2026-01-26",
                 appInfo: {name: "haver-chart picker", version: "1.0.0"},
                 appCapabilities: {availableDisplayModes: ["inline"]}}});
  report();
  window.addEventListener("resize", report);
})();
</script>
</body></html>
"""

    @mcp.resource(_PICKER_URI, app=AppConfig(prefers_border=True))
    def _pick_series_ui() -> str:
        """The picker view: candidate table, free-text fallback, Submit."""
        return _PICKER_HTML

    @mcp.tool(app=AppConfig(resource_uri=_PICKER_URI, visibility=["app", "model"]),
              meta={"ui/resourceUri": _PICKER_URI})
    def pick_series(ctx: Context, base_descriptor: str, applied_transform: str = "",
                    formula: str = "", sa_hint: str = "", freq_hint: str = "",
                    original_request: str = "",
                    remaining_parks: int = 1) -> ToolResult:
        """Show the operator a table of candidates for a PARKED series, so they can choose.

        Call this ONLY when `resolve_series` parked a slot. A resolved slot has nothing to
        pick, and a panel offering a choice that was already made is noise.

        The operator's click records the binding itself, through `remember_binding`, so the
        answer survives to later chats. You do not need to call `remember_binding`
        afterwards — wait for their selection to come back as a message, then carry on with
        `render_chart`.

        ALWAYS pass these two, or the panel cannot hand the thread back properly:

        * `original_request` — the operator's request VERBATIM, as they typed it. The
          panel quotes it back so the work resumes on a click instead of making them
          retype what they already asked for. Paraphrasing defeats it: a re-run of your
          summary is not a re-run of their request.
        * `remaining_parks` — how many slots are parked in total, INCLUDING this one. With
          three parks, three panels each resuming the request would render the chart three
          times, twice with slots still unresolved. Pass the real count and only the last
          panel resumes.

        Columns are the ones that settle a choice by eye: who publishes it, when it starts,
        the LIVE end date (a stale one is how a discontinued series announces itself),
        observation count and frequency.
        """
        out = lane.pick_series(base_descriptor, applied_transform, formula,
                               sa_hint, freq_hint,
                               original_request=original_request,
                               remaining_parks=remaining_parks)
        n = len(out.get("candidates") or [])
        if out.get("status") == "resolved":
            summary = (f"{base_descriptor!r} did not need a choice — it resolved to "
                       f"{out.get('resolved')}. No panel shown; carry on.")
        else:
            summary = (f"Showing {n} candidate(s) for {base_descriptor!r} in a picker "
                       f"panel. WAIT for the operator to choose — their selection comes "
                       f"back as a message and is recorded for you. Do not guess a "
                       f"ticker and do not call remember_binding yourself.")
        # Text mirrors the instruction for the same reason as `_result_text` (G20a): a
        # client that forwards only `content` would otherwise leave the model with a panel
        # it cannot see and no idea that waiting is the correct behaviour.
        return ToolResult(content=[TextContent(type="text", text=summary)],
                          structured_content=out)


if __name__ == "__main__":
    if HTTP_ENABLED:
        # Loopback ONLY. cloudflared is the public edge (§14.6); uvicorn must never be
        # the thing facing the internet. Port 8100 rather than 8000 so a host can
        # eventually run this and haver-data side by side without a clash.
        port = int(os.environ.get("HAVER_CHART_HTTP_PORT", "8100"))
        print(f"[haver-chart] Streamable HTTP on 127.0.0.1:{port} "
              f"(auth: Entra, public base {os.environ['HAVER_CHART_PUBLIC_URL']})",
              file=sys.stderr)
        mcp.run(transport="http", host="127.0.0.1", port=port)
    else:
        mcp.run()
