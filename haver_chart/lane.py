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

import html as _html
import os
import re
import shutil
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from haver_chart.bootstrap import REPO_ROOT, quiet_stdout, seal_stores

with quiet_stdout():                      # importing Haver prints to stdout
    import build_chart as BC              # noqa: E402
    import haver_search as HS             # noqa: E402
    import resolve as R                   # noqa: E402
    import validate as V                  # noqa: E402

seal_stores(R)                            # §13.6 — reads allowed, writes raise

# Imported AFTER the seal, and deliberately not sealed: this is the chat lane's OWN
# store (§15.3), a different file that `run_daily` never reads. The seal still holds
# where it matters — `learned_descriptors.json` remains unwritable from here.
from haver_chart import chat_store as CHAT    # noqa: E402

OUT_ROOT = REPO_ROOT / "outputs" / "chat"

# Renders are serialized process-wide (§14.5). Two reasons, and the first one applies
# with a SINGLE user: pyplot is global state — `plt.subplots` and `plt.FuncFormatter` in
# `render.py` both mutate it — and Claude issues parallel tool calls within one turn,
# which FastMCP runs in a thread pool. The second is that `g4_lib.pull` writes its
# parquet cache with no lock and no temp-then-replace, so serializing renders also
# serializes every cache write this process makes. The lock lives HERE rather than in
# `src/` so the daily lane's render path stays byte-for-byte unchanged, which is what
# preserves the §13.10.1 pixel equivalence.
_RENDER_LOCK = threading.Lock()

# How long a caller waits for the lock before giving up. This is not tuning — it is the
# guard for a specific, observed failure (§14.16): when the weekly DLX credential lapses,
# a pull raises a GUI login modal, and on a host with no attached desktop that dialog has
# nowhere to appear, so the call BLOCKS instead of failing. That call holds this lock, and
# without a timeout every later render joins an invisible queue behind it and the server
# is dead with nothing reporting it. Timing out cannot unwedge the stuck call — it is
# inside a vendor library waiting on a window — but it turns a silent hang into a named
# error, which is the difference between a support ticket and a mystery.
RENDER_LOCK_TIMEOUT_S = float(os.environ.get("CHART_RENDER_LOCK_TIMEOUT_S", "300"))

# Set when a render completes, for the HTTP `/health` probe: a wedged process still
# answers a plain liveness check, so liveness has to mean "renders are moving".
_last_render_finished: Optional[str] = None

# DLX session state. The field names are deliberately IDENTICAL to the data lane's
# (`session_suspect`, `last_pull_finished`) rather than something more natural for a
# renderer, because the host watchdog already keys on exactly that pair. A second
# vocabulary for the same condition would mean a second rule to write and keep in step,
# and rules that exist in two places drift.
_dlx_suspect = False
_last_dlx_ok: Optional[str] = None


def _dlx_note(ok: bool) -> None:
    """Record the outcome of a DLX call so `/health` can speak about the session.

    Until this existed the chart lane published render liveness and NOTHING about DLX. So
    the failure the data lane took four days to notice on 2026-09-08 — a session that dies
    mid-life, survives its own re-probe, and is cleared only by restarting the process —
    would have been entirely invisible here, on the very same DLX installation. Render
    liveness does not cover it: `last_render_finished` stays fresh right up until the
    moment DLX stops answering, and says nothing afterwards.

    One failure means little on its own. `confirm_ticker` returns False both for a dead
    session and for a code Haver does not hold, and those are not reliably distinguishable
    — the same ambiguity §17.14 records for the data lane. That is exactly why the
    watchdog demands `session_suspect` AND a stale timestamp before calling it a fault,
    and why this function does not try to be cleverer than the evidence allows.
    """
    global _dlx_suspect, _last_dlx_ok
    if ok:
        _last_dlx_ok = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _dlx_suspect = False
    else:
        _dlx_suspect = True

# Unique filenames (see `_save_path`) mean this folder only grows, which is harmless on a
# laptop and unbounded on a server that stays up for months. 0 disables the sweep.
RETENTION_DAYS = int(os.environ.get("CHAT_RETENTION_DAYS", "14"))
_swept_on: Optional[str] = None

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
    "3-month moving average of the monthly change",
    "% Change - Year to Year, Z-Score",
)

PLOT_KINDS = ("line", "bar", "stacked_bar")
_LINE_WORDS = ("line", "lines", "curve", "curves")


def transform_help() -> str:
    """The accepted `applied_transform` wordings, for an operator to restate one."""
    return ("recognized wordings include " + "; ".join(f"{p!r}" for p in TRANSFORM_PHRASES)
            + ". These COMPOSE to any depth, in either direction: 'A of B' applies B "
              "first, 'A, B' applies A first — so 'z-score of the 3-month moving average "
              "of the year-to-year % change' and '% change - year to year, 3-month moving "
              "average, z-score' are the same chart. Haver's aggregation/units line "
              "('Avg, % p.a.', 'Sum, Mil.$') is NOT a transform — leave applied_transform "
              "empty for those. Year-to-date and index/rebase are recognized but "
              "unsupported: pass the chart's formula instead.")


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
    # §15.3: this operator's chat memory layers OVER the daily store, read-through
    # rather than copied. The chat entry wins on conflict, which is what makes
    # correcting a wrong auto-bind possible (D5). Scope is this process — the merge
    # happens here, in the kwargs, so nothing is written back and `run_daily` never
    # sees it.
    return dict(confirm=lambda c: True, get_meta=HS.get_meta, search=HS.search,
                clarified=R.load_clarified(), trusted=R.load_trusted(),
                learned={**R.load_learned(), **CHAT.load()})


def _dlx_verify(slot: dict) -> dict:
    """Post-resolve DLX confirmation, mirroring `run_daily._read_resolve`.

    A catalog hit proves the series is in the metadata mirror, not that DLX will
    return observations for it. A code that does not confirm parks the slot rather
    than failing later inside the renderer."""
    if slot.get("status") != R.SLOT_RESOLVED:
        return slot
    for code in (slot.get("codes") or ([slot["resolved"]] if slot.get("resolved") else [])):
        if code and "@" in code:
            # The one place every resolve touches DLX, which makes it the honest place to
            # observe the session from. Noted whether it passes or fails: a success is
            # the only proof the session is alive, and without recording it there is
            # nothing for a staleness check to measure against.
            ok = R.confirm_ticker(code)
            _dlx_note(ok)
            if not ok:
                slot.update(status=R.SLOT_PENDING, reason=f"DLX confirm failed for {code}")
                break
    return slot


def _clean_descriptor(text: str) -> str:
    """Undo HTML escaping in a descriptor the model handed us.

    Measured 2026-09-08: a pick arrived as `CPI-U: Commodities Less Food &amp; Energy
    Commodities` and was STORED under the key `... food &amp; energy commodities`. Every
    later lookup spells that `&`, so the binding was unreachable the moment it was
    written — the operator answers the question and is asked it again forever.

    Fixed at the boundary rather than in `_norm_key`, which is frozen on purpose: every
    text-keyed store on disk is filed under that exact function, so relaxing it there
    would orphan legitimate entries to fix corrupt ones. Cleaning the input instead means
    the descriptor is right everywhere downstream — key, stored copy, and panel title,
    which was also rendering the entity literally.
    """
    return _html.unescape(text or "").strip()


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
                lag: str = "", plot_kind: str = "line", candidate_n: int = 3) -> dict:
    """Resolve ONE series through the daily lane's resolver. A park is a normal return."""
    kind = _plot_kind(plot_kind, "resolve_series")
    base_descriptor = _clean_descriptor(base_descriptor)
    spec = {"description": base_descriptor, "base_descriptor": base_descriptor,
            "applied_transform": applied_transform, "formula": formula,
            "sa_hint": sa_hint or "unknown", "freq_hint": freq_hint or "unknown",
            "axis": axis or "shared", "lag": lag, "plot_kind": kind}
    with quiet_stdout():
        slot = R.slot_from_series(0, spec)
        slot = _dlx_verify(R.resolve_slot(slot, **_resolve_kwargs()))

    resolved = slot.get("status") == R.SLOT_RESOLVED
    # Provenance: a bind that came out of THIS operator's chat memory must say so.
    # Without it, a remembered answer is indistinguishable from a fresh catalog match,
    # and an operator who wants to correct one cannot tell there is anything to correct.
    # Uses the same lookup the resolver used, not a bare key test: since §16.2 a bind can
    # come from chat memory via the forgiving key, and a stricter check here would report
    # those as fresh catalog matches — leaving the operator no hint that a remembered
    # answer is what needs correcting.
    from_memory = bool(
        resolved and slot.get("bound_via_query") == "(learned)"
        and R.learned_lookup(CHAT.load(), base_descriptor) is not None)
    reason = slot.get("reason") or ""
    if from_memory:
        reason = (f"{reason} — from your chat memory; call forget_binding"
                  f"({base_descriptor!r}) if this bind is wrong").strip(" —")
    return {
        "status": "resolved" if resolved else "parked",
        "from_chat_memory": from_memory,
        "resolved": slot.get("resolved"),
        "codes": slot.get("codes") or [],
        "via": slot.get("bound_via_query") or "",
        "similarity": slot.get("relevance"),
        "exact_token_match": bool(slot.get("relevance_exact")),
        "candidates": _candidates(slot, candidate_n),
        "reason": reason,
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


def pick_series(base_descriptor: str, applied_transform: str = "", formula: str = "",
                sa_hint: str = "", freq_hint: str = "", candidate_n: int = 5,
                original_request: str = "", remaining_parks: int = 1) -> dict:
    """Candidates for ONE parked slot, enriched from DLX, for the picker panel (§16).

    Separate from `resolve_series` on purpose, and the separation IS the design. A UI
    attaches to a TOOL, so whichever tool carries the panel renders one on every call —
    put it on `resolve_series` and six charts produce six panels, five of which have
    nothing to choose. This tool exists only where a choice exists.

    Adds what `_candidates` cannot: `_candidates` reports similarity and exact-token
    match, which are reasons the RESOLVER could not decide, and re-showing them to an
    operator just forwards the confusion. What settles it by eye is the data — who
    publishes it, when it starts, whether it still updates, how many observations. Those
    come from `haver_metadata`, so `end` is the LIVE end date rather than the catalog's
    stale copy, which is the column that exposes a discontinued series.

    Costs one metadata call per candidate (~200 ms, cached per process). Bounded by
    `candidate_n` because this runs while the operator waits, on the system whose
    characteristic failure is a hang.

    `original_request` and `remaining_parks` exist so the panel can hand the thread back
    (D15). Saving a binding and then making the operator retype the request they already
    made is the kind of small indignity that stops a feature being used, so the panel
    quotes the request verbatim on submit. `remaining_parks` is what keeps that from
    firing three times on a three-park request and rendering three partial charts: only
    the LAST panel re-runs, the earlier ones say "keep going".

    Candidates are ordered by SEASONAL ADJUSTMENT first, similarity second (D18). D14
    already said "seasonally adjusted unless the read asks for raw", but it only ever
    fired on the auto-bind path — the parked list stayed in pure similarity order, so the
    panel would offer five NSA copies of a series whose SA copy existed two ranks lower
    and the operator had to ask for SA by name. Momentum commentary wants SA; an NSA line
    answers a different question than the words asked.

    Read-only. Nothing here writes to a store; `remember_binding` does that, after the
    operator has actually chosen.
    """
    base_descriptor = _clean_descriptor(base_descriptor)
    # Widened, then re-ranked and trimmed. Re-ranking the visible N cannot surface an SA
    # copy that similarity ranked below it, which was the whole defect. Metadata is the
    # only place SA status lives (Haver.metadata has no SA field — it is the descriptor's
    # units parenthetical), so the pool has to be fetched before it can be ordered.
    pool_n = max(candidate_n * 3, 12)
    out = resolve_one(base_descriptor, applied_transform, formula, sa_hint, freq_hint,
                      candidate_n=pool_n)
    rows = []
    for cand in out.get("candidates") or []:
        code = cand.get("code") or ""
        with quiet_stdout():
            meta = R.haver_metadata(code) or {}
        descriptor = str(meta.get("descriptor") or cand.get("descriptor") or "")
        tag = R._sa_of_descriptor(descriptor)
        rows.append({
            "code": code,
            # DLX's descriptor when we have it: it carries the units parenthetical that
            # says SA or NSA, which the catalog's copy can lack.
            "descriptor": descriptor,
            "database": code.split("@")[-1] if "@" in code else "",
            "source": str(meta.get("shortsource") or ""),
            "start": str(meta.get("startdate") or ""),
            "end": str(meta.get("enddate") or ""),
            "obs": str(meta.get("numobs") or ""),
            "frequency": str(meta.get("frequency") or ""),
            "sa": "sa" if tag == "saar" else tag,
            "exact": bool(cand.get("exact_token_match")),
            "similarity": cand.get("similarity"),
        })

    want = R.sa_requested({"sa_hint": sa_hint, "base_descriptor": base_descriptor})
    target = want or "sa"                                   # D14's default, applied here too
    # THREE tiers, not a filter. A hard filter on the target would hide a series whose
    # descriptor simply carries no SA tag at all, and an untagged descriptor is unknown,
    # not wrong — hiding the only viable answer is worse than showing it last.
    tiers = {target: 0, "": 1}
    rows.sort(key=lambda r: (tiers.get(r["sa"], 2), -(r["similarity"] or 0.0)))
    shown, hidden = rows[:candidate_n], rows[candidate_n:]
    note = ""
    if any(r["sa"] == target for r in shown):
        held = sum(1 for r in hidden if r["sa"] != target)
        note = (f"ordered {target.upper()} first"
                + (f"; {held} other-adjustment candidate(s) ranked below the cut" if held else ""))
    elif rows:
        note = (f"no candidate's descriptor says {target.upper()} — showing what exists, "
                f"which is how a series with no {target.upper()} copy looks")
    return {"description": base_descriptor,
            "sa_target": target,
            "sa_note": note,
            "status": out.get("status"),
            "resolved": out.get("resolved"),
            "reason": out.get("reason") or "",
            "applied_transform": applied_transform,
            "formula": formula,
            "original_request": (original_request or "").strip(),
            "remaining_parks": max(1, int(remaining_parks or 1)),
            # Stamped server-side, never trusted from the panel. A picker sitting in
            # scroll-back is still fully live HTML, so a click on a panel from an hour ago
            # would otherwise re-run an hour-old request as if it were fresh (D16).
            "issued": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidates": shown}


# ──────────────────────────────── rendering ─────────────────────────────────
def remember_binding(base_descriptor: str, code_at_db: str,
                     source: str = "park_resolution", note: str = "") -> dict:
    """Record an operator's answer so the same descriptor never re-asks (§15.3, D5).

    DLX-confirms the code BEFORE storing. That guard is the difference between a memory
    and a poisoning vector: without it, a model that hallucinated `LRTMANUA@USECN` would
    write the typo into the store and every later chart on that descriptor would fail
    the same way, with the failure now looking like a remembered decision. Confirming
    proves the series EXISTS; only the operator can say it is the RIGHT one, which is
    why `forget_binding` exists (D4).
    """
    code = (code_at_db or "").strip()
    base_descriptor = _clean_descriptor(base_descriptor)
    if "@" not in code:
        raise ValueError(f"expected a code@database, got {code_at_db!r}")
    if not (base_descriptor or "").strip():
        raise ValueError("base_descriptor is required — it is the lookup key")
    with quiet_stdout():
        if not R.confirm_ticker(code):
            raise ValueError(
                f"DLX will not confirm {code} — not storing it. Check the ticker "
                f"with resolve_series first; a remembered bind that does not resolve "
                f"turns one bad answer into a permanent one")
        meta = R.haver_metadata(code) or {}
    entry = CHAT.remember(base_descriptor, code, source=source, note=note)
    return {"stored": True, "operator": CHAT.operator_identity()[0],
            "description": base_descriptor, "code": code,
            "dlx_descriptor": str(meta.get("descriptor") or ""),
            "key": entry.get("key", ""),
            "store": str(CHAT.store_path()), "added": entry["added"]}


def forget_binding(base_descriptor: str) -> dict:
    """Remove one remembered binding (D4), so a wrong answer is revocable without
    hand-editing JSON on a network share."""
    base_descriptor = _clean_descriptor(base_descriptor)
    removed = CHAT.forget(base_descriptor)
    return {"removed": removed, "operator": CHAT.operator_identity()[0],
            "description": base_descriptor, "store": str(CHAT.store_path()),
            "note": ("" if removed else
                     "nothing stored under that descriptor — the bind you saw may have "
                     "come from the daily learned store, which chat cannot edit")}


def memory() -> dict:
    """What this operator's chat memory holds (audit without opening the file)."""
    return CHAT.describe()


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


def _sweep_outputs(today: str) -> None:
    """Drop chat render folders older than `RETENTION_DAYS` (§14.13 answer 3).

    At most once per process per day, and never today's folder. Only well-formed date
    folders are considered, so the harnesses' own namespaces (`_control`) survive.
    Deletion is safe under the render lock's callers because a folder this old cannot be
    the one a live render is writing into."""
    global _swept_on
    if RETENTION_DAYS <= 0 or _swept_on == today or not OUT_ROOT.exists():
        return
    _swept_on = today
    cutoff = date.fromisoformat(today) - timedelta(days=RETENTION_DAYS)
    for d in OUT_ROOT.iterdir():
        if not d.is_dir():
            continue
        try:
            when = date.fromisoformat(d.name)
        except ValueError:
            continue                      # not a date folder — not ours to delete
        if when < cutoff:
            shutil.rmtree(d, ignore_errors=True)


def _save_path(filename: str = "", day: Optional[str] = None) -> Path:
    """`outputs/chat/<YYYY-MM-DD>/<name>-<token>.png` — the chat lane's own namespace.

    Never a `data/backfill_*/renders/` path and never `MMDDYYYY/<release_slug>/`:
    the two lanes must not share an output namespace, or a chat experiment can
    overwrite a shipped daily render.

    The `-<token>` suffix is what makes the path safe under concurrent callers (§14.5).
    `render` writes the PNG and `server.render_chart` then reads it back to build the
    image content; with a fixed name — and the DEFAULT stem is fixed, `chart` — a second
    call landing between those two steps hands one caller the other's chart. That is
    reachable with one user, not only on a shared server, because Claude issues parallel
    tool calls within a single turn.

    The token is a FULL uuid4 (122 bits), not a short prefix, because over HTTP the file
    stem is also the `/chart/<id>` URL and that URL is an unauthenticated capability
    (§14.20). Eight hex characters is ample against collision and far too little against
    someone guessing a URL, and those are different jobs done by the same string."""
    day = day or date.today().isoformat()
    stem = Path(filename or "chart").stem or "chart"
    stem = "".join(ch for ch in stem if ch.isalnum() or ch in "-_") or "chart"
    out = OUT_ROOT / day
    out.mkdir(parents=True, exist_ok=True)
    _sweep_outputs(day)
    return out / f"{stem}-{uuid.uuid4().hex}.png"


# A chart id is the PNG's stem. Anything outside this set cannot name a file this lane
# wrote, so rejecting it costs nothing and removes every traversal spelling at once —
# no separators, no drive letters, no `..`, no NUL, no percent-decoded surprises.
_CHART_ID_OK = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")


def chart_path(chart_id: str) -> Optional[Path]:
    """Resolve a `/chart/<id>` request to a PNG under `OUT_ROOT`, or None.

    Two independent guards, because this is the one route that turns caller-supplied
    text into a filesystem path. The pattern above is the allowlist; the containment
    check afterwards is the backstop, comparing the FULLY RESOLVED path against the
    resolved root so that a symlink inside the tree cannot point out of it. Either alone
    would probably do. Both, because "probably" is not the standard for a path built
    from a public URL.

    Returns None rather than raising for every failure — a bad id, a missing file, a
    swept-away day — so the route answers 404 identically in all of them and cannot be
    used to probe which ids once existed.
    """
    if not _CHART_ID_OK.match(chart_id or ""):
        return None
    root = OUT_ROOT.resolve()
    for day_dir in sorted(OUT_ROOT.glob("*"), reverse=True):   # newest day first
        if not day_dir.is_dir():
            continue
        candidate = (day_dir / f"{chart_id}.png").resolve()
        if not candidate.is_file():
            continue
        if root not in candidate.parents:
            return None
        return candidate
    return None


def _close_figures() -> None:
    """Reclaim the figure `render_row` leaves behind.

    `render.render` returns `(fig, info)` and `render_row` keeps only `info`, but the
    figure was created through `plt.subplots`, so pyplot holds a reference in its global
    manager forever — roughly 1700x1220x4 bytes of Agg canvas per render plus the artist
    tree. Under stdio that never mattered (a handful of charts, then the process exits);
    a server leaks until it dies. `close("all")` rather than closing one figure because
    the lane process holds no figures for any other purpose, and it is called under the
    render lock so it can never reach a figure another thread is still drawing."""
    import matplotlib.pyplot as plt
    plt.close("all")


def render(series: list[dict], *, filename: str = "", **row_kw) -> dict:
    """Build the row and call `build_chart.render_row`. Returns its result + the path.

    Exceptions are NOT caught. An unmapped transform, a raw-mnemonic legend, duplicate
    legends and a partially-resolved chart all raise inside the shared code, and those
    raises are the guardrails (§13.7) — swallowing one here would reintroduce exactly
    the silent-wrong class the daily lane spent months eliminating."""
    global _last_render_finished
    row = build_row(series, **row_kw)
    path = _save_path(filename)
    if not _RENDER_LOCK.acquire(timeout=RENDER_LOCK_TIMEOUT_S):
        raise RuntimeError(
            f"another render has held the renderer for over {RENDER_LOCK_TIMEOUT_S:.0f}s "
            f"and has not returned. The usual cause is an expired Haver DLX sign-in: the "
            f"pull is waiting on a login window that cannot be shown on this host. Sign "
            f"in to DLX on the server and restart haver-chart.")
    try:
        try:
            with quiet_stdout():
                info = BC.render_row(row, str(path))
        finally:
            # In the `finally` so a raised guardrail does not also leak a half-built
            # figure — the failing render is exactly the one most likely to be retried.
            _close_figures()
    finally:
        _RENDER_LOCK.release()
    _last_render_finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {"ok": True, "path": str(path), "plotted": list(info.get("plotted") or []),
            "end": info.get("end"), "row": row,
            "_drawn": info.get("drawn") or [], "_freq": info.get("common_freq")}


def health() -> dict:
    """Liveness that a wedged process cannot fake (§14.16).

    A stuck render holds `_RENDER_LOCK` forever while the process keeps answering plain
    HTTP, so "the server is up" is exactly the wrong question. Reporting whether the lock
    is currently held, and when a render last COMPLETED, lets an uptime check tell a busy
    server from a dead one."""
    held = _RENDER_LOCK.locked()
    return {"status": "ok", "rendering": held,
            "last_render_finished": _last_render_finished,
            # Same two names the data lane uses, so one watchdog rule covers both hosts.
            # `last_pull_finished` is the last DLX call that SUCCEEDED, which is what a
            # staleness check needs; a render can finish from cache without touching DLX
            # at all, so `last_render_finished` cannot stand in for it.
            "session_suspect": _dlx_suspect,
            "last_pull_finished": _last_dlx_ok,
            "retention_days": RETENTION_DAYS,
            # Whose memory this process is reading, and how much of it. A remembered
            # bind that nobody can see is indistinguishable from a resolver that has
            # started guessing.
            "chat_memory": CHAT.describe()}


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
