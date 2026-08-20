"""
approval.py — drives the two Teams round-trips over the ledger (plan §4e, §12 G5).

Order matters: **ticker resolution precedes title approval** (§4/Q2) — a chart
with any unresolved series is parked whole (Defect 2) and never reaches the title
gate until its tickers come back. Both round-trips are sync-wait but
NON-BLOCKING across runs: one pass posts the question (if not already asked) and
harvests any replies; a row with no reply yet simply stays `awaiting_*` for the
next run (Q1).

`confirm_ticker(code_at_db) -> bool` is injected so the resolver's Haver-metadata
access (the haver MCP at authoring time, or a direct metadata path at runtime) is
not hard-wired here; a reply that doesn't confirm leaves the series unresolved.

`title_proposals(row) -> list[{title, subtitle}]` is injected too; for the G4
charts it yields the g4_desired `_var1`/`_var2` pairs, in production the Opus
proposer (§4g).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import ledger as L
import resolve as R
import teams as TM


def day_thread(release_slug: str = "", when: str = "") -> str:
    day = (when or datetime.now(timezone.utc).strftime("%Y%m%d"))
    return f"renmac-charts-{day}" + (f"-{release_slug}" if release_slug else "")


def chart_thread(release_slug: str = "", chart_id: str = "", when: str = "") -> str:
    """Per-chart thread key `<day>-<slug>::<chart_id>`. The `::<chart_id>` suffix
    lets GraphTransport bind replies to THIS chart in the shared self-chat (and
    gives StubTransport a separate bucket per chart) — so a multi-park day can't
    misroute a reply (the same-sender ambiguity)."""
    return day_thread(release_slug, when) + f"::{chart_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bulk_approve_signal(transport: TM.Transport, rows: list[dict]) -> bool:
    """True if a chart-agnostic `approve all` arrived AFTER the earliest still-open
    ask among `rows` — the one message that ratifies every cleanly-approvable chart
    at this gate. Scanned tag-agnostically (it carries no `[chart_id]`); a stale
    approve-all from before the asks is ignored (cursor gate)."""
    cursors = [r.get("ask_cursor") for r in rows if r.get("ask_cursor")]
    if not cursors:
        return False
    try:
        msgs = transport.global_replies(min(cursors))
    except Exception:                         # transport can't see the whole surface
        return False
    return any(TM.is_approve_all(m) for m in msgs)


def _fully_bound(row: dict) -> bool:
    """GUARDRAIL for bulk approve: every series slot resolved (no parked/pending) —
    so `approve all` can never sweep up a chart with an unresolved series."""
    slots = row.get("series") or []
    return bool(slots) and all(s.get("status") == R.SLOT_RESOLVED for s in slots)


def run_ticker_roundtrip(led: L.Ledger, transport: TM.Transport,
                         confirm_ticker: Callable[[str], bool],
                         candidates: dict | None = None) -> None:
    """Post the unresolved-ticker question (once), harvest replies, validate via
    `confirm_ticker`, and un-park rows whose every series is resolved → RESOLVED.
    A `skip` reply drops the chart (SKIPPED)."""
    for row in led.by_status(L.AWAITING_TICKER):
        thread = row.get("thread_id") or chart_thread(
            row.get("release_slug", ""), row["chart_id"])
        unresolved = row.get("unresolved") or []
        if not row.get("ask_cursor"):  # post the question exactly ONCE
            cursor = transport.post(thread, TM.format_ticker_ask(
                row["chart_id"], unresolved, candidates))
            row.update(thread_id=thread, asked_at=_now(), ask_cursor=cursor)
            continue  # give the human a chance before polling this pass

        proposed, skip = TM.parse_ticker_reply(
            transport.replies(thread, row["ask_cursor"]), unresolved)
        if skip:
            led.set_status(row, L.SKIPPED, ask_cursor="", thread_id="")
            continue

        confirmed = dict(row.get("resolved_ticker") or {})
        pending = []
        for desc in unresolved:
            pick = next((t for t in proposed.get(desc, []) if confirm_ticker(t)),
                        None)
            if pick:
                confirmed[desc] = pick
            else:
                pending.append(desc)  # no valid code yet → keep asking
        row["resolved_ticker"] = confirmed
        row["unresolved"] = pending
        if not pending:
            led.set_status(row, L.RESOLVED, ask_cursor="", thread_id="")


def _ask_sig(pending: list[dict]) -> str:
    """Signature of the current question: which slots, and of which kind. If the
    set changes (e.g. a native-MA clarify is answered and the slot now needs a
    ticker), it's a genuinely NEW question and is re-posted — otherwise the cursor
    model posts exactly once (no duplicate posts on a no-change pass)."""
    return ";".join(f"{s['idx']}:{'c' if s.get('needs_clarification') else 't'}"
                    for s in pending)


def run_series_roundtrip(led: L.Ledger, transport: TM.Transport, *,
                         resolve_fn: Callable[[dict], dict],
                         confirm_ticker: Callable[[str], bool]) -> None:
    """G8b per-series-slot ticker round-trip (the vision→resolve path).

    For each AWAITING_TICKER chart that carries `series` slots: post one question
    listing every PENDING slot (once per question signature), harvest the reply,
    and apply it per slot —
      * a native-MA clarification → persist via the clarified store, then RE-RESOLVE
        that slot with `resolve_fn` (it may now bind, or fall to a ticker ask);
      * a `code@db` → confirm via `confirm_ticker` and bind the slot.
    The chart un-parks to RESOLVED only when EVERY slot is bound (Defect 2). A
    `skip` drops the whole chart (SKIPPED). Legacy single-slot rows (no `series`)
    are left to `run_ticker_roundtrip`."""
    for row in led.by_status(L.AWAITING_TICKER):
        slots = row.get("series")
        if not slots:
            continue  # legacy lane
        thread = row.get("thread_id") or chart_thread(
            row.get("release_slug", ""), row["chart_id"])
        pending = [s for s in slots if s.get("status") == R.SLOT_PENDING]
        sig = _ask_sig(pending)
        if not row.get("ask_cursor") or row.get("ask_sig") != sig:
            # Show the FULL set (auto-bound + parked) so confirm_all's "see every
            # bind" holds even on a partially-parked chart; sig still keys on pending.
            cursor = transport.post(thread, TM.format_series_ask(row["chart_id"], slots))
            row.update(thread_id=thread, asked_at=_now(), ask_cursor=cursor, ask_sig=sig)
            continue  # let the human answer before polling this question

        tickers, clarify, skip = TM.parse_series_reply(
            transport.replies(thread, row["ask_cursor"]), pending)
        if skip:
            for s in slots:  # `skip` drops the WHOLE chart — every slot, not just pending
                s.update(status=R.SLOT_SKIPPED, reason="human skip (drop chart)")
            L.sync_row_from_slots(row)
            led.set_status(row, L.SKIPPED, ask_cursor="", ask_sig="", thread_id="")
            continue

        for s in slots:
            if s.get("status") != R.SLOT_PENDING:
                continue
            idx = s["idx"]
            if s.get("needs_clarification") and idx in clarify:
                R.save_clarified(s.get("base_descriptor") or s.get("description"),
                                 clarify[idx])
                clarified_slot = dict(s)
                clarified_slot.update(native_ma_ambiguous=False,
                                      needs_clarification=False,
                                      native_ma_resolved=clarify[idx])
                new = resolve_fn(clarified_slot)  # re-resolve, now clarified
                s.clear()
                s.update(new)
            elif idx in tickers:
                pick = next((c for c in tickers[idx] if confirm_ticker(c)), None)
                if pick:
                    s.update(R.rebind_human_ticker(s, pick))

        L.sync_row_from_slots(row)
        if row["status"] == L.RESOLVED:
            row.update(ask_cursor="", ask_sig="", thread_id="")


def run_title_roundtrip(led: L.Ledger, transport: TM.Transport,
                        title_proposals: Callable[[dict], list[dict]], *,
                        from_status: str = L.RESOLVED,
                        gate_status: str = L.AWAITING_APPROVAL,
                        parser: Callable | None = None) -> None:
    """Move `from_status` rows into the title gate: post numbered options (once),
    harvest replies, and on a valid choice set chosen_title/subtitle → APPROVED.

    Defaults = selective_park (RESOLVED → AWAITING_APPROVAL, numbered/free-text).
    confirm_all (Part 3) calls this with from_status=RESOLUTION_APPROVED,
    gate_status=AWAITING_TITLE, parser=parse_title_reply — so titles are a SEPARATE
    round-trip fired only AFTER the resolution is approved."""
    parser = parser or TM.parse_title_choice
    for row in led.by_status(from_status):
        led.set_status(row, gate_status, title_options=title_proposals(row),
                       ask_cursor="", ask_sig="", thread_id="")

    rows_gate = led.by_status(gate_status)
    bulk = _bulk_approve_signal(transport, rows_gate)   # `approve all` → proposed title each
    bulk_ids: list[str] = []
    for row in rows_gate:
        thread = row.get("thread_id") or chart_thread(
            row.get("release_slug", ""), row["chart_id"])
        options = row.get("title_options") or []
        if not row.get("ask_cursor"):  # post the options exactly ONCE
            cursor = transport.post(thread, TM.format_title_ask(row["chart_id"], options))
            row.update(thread_id=thread, asked_at=_now(), ask_cursor=cursor)
            continue

        choice = parser(transport.replies(thread, row["ask_cursor"]), options)
        # bulk: a global `approve all` accepts the PROPOSED (first) option for any
        # title chart the human didn't individually answer. Titles are low-stakes and
        # editable later via --retitle, and the transform label is non-suppressible, so
        # there's no silent-wrong path — safe to bulk-approve.
        if not choice and bulk and options:
            choice = dict(options[0])
            bulk_ids.append(row["chart_id"])
        if choice:
            # `none` → title-less: chosen_title empty + no_title flag (render_row omits
            # the title, keeps the transform-label subtitle). Any real choice clears the
            # flag, so a later --retitle that picks a real title renders titled.
            led.set_status(row, L.APPROVED,
                           chosen_title=choice["title"],
                           subtitle=choice.get("subtitle", ""),
                           no_title="1" if choice.get("no_title") else "",
                           st_force="1" if choice.get("st_force") else "",
                           ask_cursor="", thread_id="")
    if bulk_ids:
        print(f"  [approve all] titles ratified with proposed option ({len(bulk_ids)}): "
              f"{', '.join(bulk_ids)}")


# ──────────────────── confirm_all resolution gate (Part 2 & 4) ───────────────
def _norm_axis(v: str) -> str:
    low = (v or "").strip().lower()
    if low in ("r", "right"):
        return "R"
    if low in ("l", "left"):
        return "L"
    return v.strip() or "shared"


def _apply_resolution_corrections(slots: list[dict], corrections: dict,
                                  confirm_ticker: Callable[[str], bool]) -> None:
    """Apply named-field corrections to a chart's slots (1-based positions)."""
    for pos, fields in corrections.items():
        if not (1 <= pos <= len(slots)):
            continue
        s = slots[pos - 1]
        if "code" in fields:
            code = fields["code"]
            if confirm_ticker(code):
                s.update(R.rebind_human_ticker(s, code, reason="human correction (confirmed)"))
                s.update(relevance=None, relevance_exact=False)
            else:
                s.update(status=R.SLOT_PENDING,
                         reason=f"human code {code} did not confirm")
        if "axis" in fields:
            s["axis"] = _norm_axis(fields["axis"])
        if "lag" in fields:
            s["lag"] = fields["lag"] or None
        if "legend" in fields:               # human-authored legend label wins
            s["proposed_legend"] = fields["legend"]
            s["legend_source"] = "human"
        for k in ("transform", "formula"):
            if k in fields:
                s["applied_transform"] = fields[k]
                if "(" in fields[k] or "+" in fields[k]:
                    s["formula"] = fields[k]


def _fill_legends(slots: list[dict], label_fn: Callable[[dict], str] | None) -> None:
    """Populate each slot's `proposed_legend` for the resolution summary:
      * a confirmed store label → shown SILENTLY (source=store, no Opus call);
      * else, if a `label_fn` is injected (live: Opus grounded on get_series) →
        proposed (source=opus);
      * else left blank (the ask shows `(pending — reply legendN=…)`).
    A human override already on the slot (source=human) is never overwritten."""
    store = R.load_legend()
    # A ticker that appears TWICE on one chart (two transforms of the same series) must not
    # take the legacy transform-blind store hit — that is how one label got served to both
    # lines (2026-07-30). Those slots go to Opus/ask so each transform gets its own label.
    base_counts: dict = {}
    for s in slots:
        base_counts[R.legend_key_base(s)] = base_counts.get(R.legend_key_base(s), 0) + 1
    for s in slots:
        if s.get("legend_source") == "human" and (s.get("proposed_legend") or "").strip():
            continue
        hit = R.lookup_legend(store, s,
                              allow_base=base_counts.get(R.legend_key_base(s), 0) <= 1)
        if hit and (hit.get("label") or "").strip():
            s["proposed_legend"] = hit["label"].strip()
            s["legend_source"] = "store"
            continue
        if label_fn is not None and not (s.get("proposed_legend") or "").strip():
            try:
                lab = (label_fn(s) or "").strip()
            except Exception as e:
                lab = ""
                print(f"    [legend] proposal failed for {R.legend_key(s)} "
                      f"({type(e).__name__}: {e}) — will ask pending")
            if lab:
                s["proposed_legend"] = lab
                s["legend_source"] = "opus"


def _persist_legends(row: dict) -> None:
    """On resolution approve, write each slot's confirmed legend label to the store
    (write-once, keyed canonical via legend_key) so it renders silently next time."""
    for s in row.get("series") or []:
        lab = (s.get("proposed_legend") or "").strip()
        if not lab:
            continue
        if s.get("legend_source") == "store":
            continue                          # already stored — no rewrite
        src = s.get("description") or s.get("base_descriptor") or ""
        R.save_legend(R.legend_key(s), lab, source_descriptor=src)


def _learn_from_row(row: dict) -> None:
    """Write an approved chart's description-binds to the learning store (Part 4):
    description→code (instant re-resolve) + mnemonic→code (mnemonic-honest trusted)."""
    for s in row.get("series") or []:
        code = s.get("resolved")
        if not code or "@" not in code or s.get("formula"):
            continue                      # only description-bound single series
        desc = s.get("description") or s.get("base_descriptor")
        if desc:
            R.save_learned(desc, code)
        R.save_trusted(code.split("@")[0], code)


def _log_approval_metric(metrics_path: str, row: dict, corrected: bool) -> None:
    """Append the approve-without-correction signal so the loop-closing rate is
    visible over time (Part 4 equilibrium metric)."""
    if not metrics_path:
        return
    import csv as _csv
    from pathlib import Path as _P
    p = _P(metrics_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with p.open("a", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        if new:
            w.writerow(["ts", "chart_id", "n_series", "approved_without_correction"])
        w.writerow([_now(), row.get("chart_id", ""),
                    len(row.get("series") or []), 0 if corrected else 1])


def run_resolution_roundtrip(led: L.Ledger, transport: TM.Transport, *,
                             confirm_ticker: Callable[[str], bool],
                             metrics_path: str = "", learn: bool = True,
                             label_fn: Callable[[dict], str] | None = None) -> None:
    """confirm_all gate: NOTHING advances to titles/render until the human ratifies
    the full resolved set (Part 2). Promote freshly-RESOLVED charts by posting the
    summary (once); on the reply, `approve` → RESOLUTION_APPROVED (+ learning store,
    Part 4), corrections → apply & re-post, `skip` → drop. Defect-2 holds: a chart
    never reaches the title round-trip without an explicit resolution approve."""
    # 1) promote: post the full resolved set for each newly-bound chart (once).
    for row in led.by_status(L.RESOLVED):
        if not (row.get("series")):
            continue                      # legacy single-slot lane not in confirm_all
        thread = row.get("thread_id") or chart_thread(
            row.get("release_slug", ""), row["chart_id"])
        _fill_legends(row["series"], label_fn)      # legend proposals into the summary
        cursor = transport.post(thread, TM.format_resolution_summary(
            row["chart_id"], row["series"], row.get("chart_spec")))
        led.set_status(row, L.AWAITING_RESOLUTION, thread_id=thread,
                       asked_at=_now(), ask_cursor=cursor, ask_sig="res:0")

    # 2) harvest ratifications / corrections / skips.
    rows_gate = led.by_status(L.AWAITING_RESOLUTION)
    bulk = _bulk_approve_signal(transport, rows_gate)     # one `approve all` sweeps the gate
    bulk_ids: list[str] = []
    for row in rows_gate:
        slots = row.get("series") or []
        thread = row.get("thread_id") or chart_thread(
            row.get("release_slug", ""), row["chart_id"])
        approve, corrections, skip = TM.parse_resolution_reply(
            transport.replies(thread, row["ask_cursor"]), len(slots))
        if skip:
            for s in slots:
                s.update(status=R.SLOT_SKIPPED, reason="human skip (drop chart)")
            L.sync_row_from_slots(row)
            led.set_status(row, L.SKIPPED, ask_cursor="", ask_sig="", thread_id="")
            continue
        if corrections:
            _apply_resolution_corrections(slots, corrections, confirm_ticker)
            L.sync_row_from_slots(row)            # recompute chart status from slots
            n = int((row.get("ask_sig") or "res:0").split(":")[-1]) + 1
            if row["status"] == L.RESOLVED:       # still fully bound → re-post for final approve
                _fill_legends(slots, label_fn)    # a code correction → refresh legend
                cursor = transport.post(thread, TM.format_resolution_summary(
                    row["chart_id"], slots, row.get("chart_spec")))
                led.set_status(row, L.AWAITING_RESOLUTION, thread_id=thread,
                               asked_at=_now(), ask_cursor=cursor, ask_sig=f"res:{n}")
            # else: a correction unbound a slot → it dropped to AWAITING_TICKER; the
            # series round-trip re-asks, then it re-enters this gate when bound again.
            continue
        # bulk: a global `approve all` ratifies this chart ONLY if it's cleanly bound
        # (guardrail) and the human didn't single-reply it (a per-chart reply wins).
        if not approve and bulk and _fully_bound(row):
            approve = True
            bulk_ids.append(row["chart_id"])
        if approve:
            corrected = (row.get("ask_sig") or "res:0") != "res:0"
            if learn:
                _learn_from_row(row)
            _persist_legends(row)            # confirmed legend labels → store (write-once)
            _log_approval_metric(metrics_path, row, corrected)
            led.set_status(row, L.RESOLUTION_APPROVED,
                           ask_cursor="", ask_sig="", thread_id="")
    if bulk_ids:
        print(f"  [approve all] resolution ratified ({len(bulk_ids)}): "
              f"{', '.join(bulk_ids)}")
