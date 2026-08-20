"""
run_daily.py — date-scoped ingestion of Neil's "for the daily" emails.

Replays a specific Eastern calendar day (testing/backfill) instead of waiting for
a fresh email, then runs the SAME downstream state machine as G7. This is the
first time Mail.Read + docx/image extraction + the raw-Haver classifier hit a REAL
inbox, so the default output is a FRONT-HALF SHAKEDOWN report (what ingested, what
classified raw-Haver vs skip, what the extractor choked on incl EMF/WMF).

IDEMPOTENCY (decision): dated runs use a SEPARATE ledger namespace —
`data/ledger_backfill_<date>.csv` — so they never collide with the production
ledger or skip already-processed real emails. Re-running resumes that dated ledger
(seeding is upsert-keyed, so no duplicate rows). `--reprocess` wipes the dated
ledger for a clean, repeatable debug redo.

SOURCES (--source): the email inbox (default), the Bloomberg-chat docx folder
(`inbox_bloomberg/MMDDYYYY/*.docx`, see folder_ingest.py), or `all` (both). Every
source yields the same (commentary, images) unit and seeds the SAME dated ledger;
downstream (classify → ledger state machine → Teams → render) is identical.

USAGE
  python scripts/run_daily.py --date 2026-06-25                      # email (default)
  python scripts/run_daily.py --date 2026-06-25 --source bloomberg   # docx folder
  python scripts/run_daily.py --date 2026-06-25 --source all         # email + bloomberg
  python scripts/run_daily.py --date 2026-06-25 --vision   # + Opus classify confirm
  python scripts/run_daily.py --date 2026-06-25 --reprocess
  python scripts/run_daily.py --date 2026-06-25 --approve  # also run Teams round-trips
  python scripts/run_daily.py --selftest                   # offline (notes/ fixtures)

Mailbox: --mailbox, else AS_DAILY_MAILBOX, else AS_SENDER_EMAIL.
Sender:  --sender, default ndutta@renmac.com.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Windows consoles default to cp1252; force UTF-8 so report glyphs (≤ → ⚠) and any
# unicode subject can't raise UnicodeEncodeError mid-report (send_via_graph gotcha).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

import ingest as I   # noqa: E402
import ledger as L   # noqa: E402

DATA = ROOT / "data"


def _dated_ledger(date_str: str) -> Path:
    return DATA / f"ledger_backfill_{date_str}.csv"


def _asset_dir(date_str: str) -> Path:
    return DATA / f"backfill_{date_str}" / "assets"


def _et(iso_z: str) -> str:
    try:
        dt = datetime.fromisoformat((iso_z or "").replace("Z", "+00:00"))
        return dt.astimezone(I.EASTERN).strftime("%Y-%m-%d %H:%M ET")
    except Exception:
        return iso_z


def _short(email_id: str) -> str:
    """Filesystem-safe, stable, unique tag for an email. A hash, because the
    internetMessageId is `<guid@domain>` — its tail is the shared domain (collides)
    and it carries `<>@` chars illegal in Windows filenames."""
    import hashlib
    return hashlib.sha1((email_id or "msg").encode("utf-8")).hexdigest()[:10]


# ─────────────────────────────── reporting ─────────────────────────────────
def _report(day: dict) -> dict:
    src = day.get("source", "email")
    print("=" * 78)
    if src == "bloomberg":
        print(f"INGESTION REPORT — {day['date']}  [SOURCE: bloomberg-chat docx]")
        print(f"  folder     : {day['folder']}"
              f"{'' if day.get('exists') else '   (does not exist)'}")
        print(f"  docs       : {day['n_docs']} .docx "
              f"({', '.join(day.get('doc_names') or []) or '—'})")
    else:
        print(f"INGESTION REPORT — {day['date']} (America/New_York wall-clock day)"
              f"  [SOURCE: email]")
        print(f"  UTC window : {day['window'][0]}  ≤ receivedDateTime <  {day['window'][1]}")
        print(f"  mailbox    : {day['mailbox']}")
        print(f"  $filter    : {day['filter']}   (server-side; paged)")
        print(f"  fetched    : {day['n_fetched']} from sender; "
              f"{day['n_subject_kept']} matched subject /\\bdaily\\b/i")
    print("=" * 78)

    label = "DOC  " if src == "bloomberg" else "EMAIL"
    tot = {"raw_haver": 0, "skip": 0, "unsure": 0, "dup": 0}
    n_err = 0
    for er in day["results"]:
        # a thread quote-back is neither raw_haver-to-seed nor a real skip — count apart
        rh = [a for a in er.assets if a.verdict == "raw_haver" and not a.duplicate]
        sk = [a for a in er.assets if a.verdict == "skip" and not a.duplicate]
        un = [a for a in er.assets if a.verdict == "unsure" and not a.duplicate]
        dup = [a for a in er.assets if a.duplicate]
        tot["raw_haver"] += len(rh)
        tot["skip"] += len(sk)
        tot["unsure"] += len(un)
        tot["dup"] += len(dup)
        n_err += len(er.errors)
        head = (f"«{er.subject}»  [{er.id}]" if src == "bloomberg"
                else f"{_et(er.received)}  ({er.received})  «{er.subject}»")
        print(f"\n  {label} {head}")
        dmsg = f" dup={len(dup)}" if dup else ""
        print(f"    assets: {len(er.assets)}  → raw_haver={len(rh)} "
              f"skip={len(sk)} unsure={len(un)}{dmsg}  | commentary={len(er.commentary)} chars")
        for a in er.assets:
            if a.duplicate:
                print(f"      [DUP-SKIP ] {a.source:28} {a.width}x{a.height}  {a.dup_reason}")
            else:
                print(f"      [{a.verdict:9}] {a.source:28} {a.width}x{a.height}  {a.reason}")
        for e in er.errors:
            print(f"      [EXTRACT-ERR] {e.where}: format '{e.detected}' — {e.detail}")
    print("\n" + "-" * 78)
    dmsg = f"  thread_dups_skipped={tot['dup']}" if tot["dup"] else ""
    print(f"  TOTALS: raw_haver={tot['raw_haver']}  skip={tot['skip']}  "
          f"unsure={tot['unsure']}{dmsg}  extract_errors={n_err}")
    if n_err:
        print("  ⚠ extract errors route to Teams (couldn't-read-format), not dropped.")
    print("-" * 78)
    return tot


# ─────────────────────────────── seeding ───────────────────────────────────
# Non-terminal states a re-run may legitimately leave in place / supersede.
_NON_TERMINAL = {L.PENDING, L.AWAITING_TICKER, L.RESOLVED, L.AWAITING_APPROVAL}


def _seed(days: list[dict], date_str: str) -> int:
    """One ledger row per raw_haver chart for ALL sources of the day, into the SAME
    dated ledger. Charts enter `awaiting_ticker` (no auto chart-field read yet — G8),
    so the IDENTICAL downstream state machine drives them from here.

    IDEMPOTENT (insert-only): a row whose (message_id, chart_index) already exists is
    left UNTOUCHED — a re-run never clobbers a row that has progressed to
    resolved/approved/etc. (the email lane keys on internetMessageId; bloomberg on
    `bb:<content-hash>`, so an unchanged doc maps to the same key and is skipped).

    BLOOMBERG SUPERSEDE: an edited/corrected doc gets a NEW content hash, so it seeds
    fresh rows; its prior-hash rows for this date that are still non-terminal are
    marked `skipped` so they don't linger as stale Teams asks."""
    adir = _asset_dir(date_str)
    adir.mkdir(parents=True, exist_ok=True)
    led = L.Ledger(str(_dated_ledger(date_str)))
    slug = f"backfill_{date_str}"
    seeded = skipped_existing = 0
    for day in days:
        for er in day["results"]:
            for idx, a in enumerate(er.raw_haver):
                if led.get(er.id, idx) is not None:   # already seeded → idempotent
                    skipped_existing += 1
                    continue
                cid = f"{date_str}_{_short(er.id)}_{idx}"
                apath = adir / f"{cid}.png"
                apath.write_bytes(a.data)
                led.upsert(
                    message_id=er.id, chart_index=str(idx), chart_id=cid,
                    received_date=date_str, received_time=er.received,
                    sender=er.sender, subject=er.subject, release_slug=slug,
                    status=L.AWAITING_TICKER,
                    unresolved=[f"identify series for {cid} (source: {a.source})"],
                    asset_path=str(apath))
                seeded += 1

    superseded = _reconcile_bloomberg(led, days, slug)
    led.save()
    msg = f"  seeded {seeded} raw_haver chart(s) → {_dated_ledger(date_str)}"
    if skipped_existing:
        msg += f"  ({skipped_existing} already present, left untouched)"
    if superseded:
        msg += f"  ({superseded} stale bloomberg row(s) superseded → skipped)"
    print(msg)
    return seeded


def _reconcile_bloomberg(led: "L.Ledger", days: list[dict], slug: str) -> int:
    """Mark non-terminal bloomberg rows for this date whose content hash is no longer
    present in the folder (doc edited or removed) as `skipped`. Scoped by the `bb:`
    id prefix, so email rows are never touched."""
    bb_days = [d for d in days if d.get("source") == "bloomberg"]
    if not bb_days:
        return 0
    current = {er.id for d in bb_days for er in d["results"]}  # all current doc hashes
    n = 0
    for row in led.rows:
        mid = row.get("message_id", "")
        if (mid.startswith("bb:") and row.get("release_slug") == slug
                and row.get("status") in _NON_TERMINAL and mid not in current):
            led.set_status(row, L.SKIPPED)
            n += 1
    return n


# ─────────────────────────────── downstream (G8) ───────────────────────────
def _transport(date_str: str):
    """GraphTransport (live Teams) unless G7_TRANSPORT=stub (offline bucket)."""
    import teams as TM
    if os.environ.get("G7_TRANSPORT", "graph").lower() == "stub":
        return TM.StubTransport(str(DATA / f"backfill_{date_str}" / "stub.json"))
    return TM.GraphTransport()


def _resolve_kwargs():
    """Shared resolver wiring: live catalog search/get_meta (in-process MCP path),
    the trusted/clarified/learned stores. `confirm=lambda True` for the AUTO path
    because every search hit is a real catalog series — the bound code is DLX-verified
    AFTER resolve (cheap, once per bind, not once per candidate)."""
    import resolve as R
    import haver_search as HS
    return dict(confirm=lambda c: True, get_meta=HS.get_meta, search=HS.search,
                clarified=R.load_clarified(), trusted=R.load_trusted(),
                learned=R.load_learned())


def _read_resolve(led: "L.Ledger") -> int:
    """G8 Stage 1 (vision read) + Stage 2 (resolve) over freshly-seeded rows.
    Idempotent: a row that already carries a `chart_spec` is skipped. Auto-bound
    codes are DLX-verified; a code that doesn't confirm parks that slot."""
    import chartspec as CS
    import resolve as R
    import haver_search as HS
    if not HS.available():
        print("  [g8] catalog search unavailable — description slots will park")
    kw = _resolve_kwargs()
    n = 0
    for row in led.by_status(L.AWAITING_TICKER):
        if row.get("chart_spec"):
            continue
        apath = row.get("asset_path")
        if not apath or not Path(apath).exists():
            print(f"  [g8] {row['chart_id']}: asset missing ({apath}) — left parked")
            continue
        try:
            spec = CS.read_chart_spec(Path(apath).read_bytes())
        except Exception as e:                          # fail-loud per chart, not run
            print(f"  [g8] {row['chart_id']}: vision read FAILED ({type(e).__name__}: {e})")
            continue
        row["chart_spec"] = spec.to_dict()
        slots = R.resolve_chart(R.build_slots(row["chart_spec"]), **kw)
        for s in slots:                                  # post-resolve DLX verify
            if s.get("status") != R.SLOT_RESOLVED:
                continue
            for code in (s.get("codes") or ([s["resolved"]] if s.get("resolved") else [])):
                if code and "@" in code and not R.confirm_ticker(code):
                    s.update(status=R.SLOT_PENDING,
                             reason=f"DLX confirm failed for {code}")
                    break
        row["series"] = slots
        L.sync_row_from_slots(row)
        n += 1
    return n


def _titles(row: dict) -> list:
    """Opus title proposals from the subject + series descriptions. A commentary-less
    chart has little to draft from — proposals may be weak or (on any failure) EMPTY;
    that's fine, the title round-trip still posts the `none`/custom options. Never
    raises: no commentary must not break the round-trip."""
    import propose
    labels = [s.get("base_descriptor") or s.get("description") or "series"
              for s in (row.get("series") or [])] or ["series"]
    try:
        return propose.propose_titles(subject=row.get("subject", "Chart"),
                                      series=labels, transform="(per chart)",
                                      span="daily", n=2) or []
    except Exception as e:
        print(f"  [titles] no proposal for {row.get('chart_id','?')} "
              f"({type(e).__name__}: {e}) — offering none/custom only")
        return []


def _legend_label_fn(slot: dict) -> str:
    """Live legend proposer: ground Opus on the bound series' get_series descriptor(s).
    Single series → propose_series_label(descriptor); composite (>1 code) →
    propose_composite_label(operation, operands). Raises if a descriptor is missing
    (the round-trip catches it and shows the label as pending rather than guessing)."""
    import haver_search as HS
    import propose
    codes = slot.get("codes") or []
    if len(codes) > 1:                       # composite operand expression
        operands = [{"code": c, "descriptor": (HS.get_meta(c) or {}).get("descriptor") or ""}
                    for c in codes]
        if any(not o["descriptor"] for o in operands):
            raise ValueError(f"missing get_series descriptor for composite {codes}")
        op = slot.get("base_descriptor") or slot.get("formula") or "combination"
        return propose.propose_composite_label(op, operands)["label"]
    code = codes[0] if codes else slot.get("resolved")
    desc = (HS.get_meta(code) or {}).get("descriptor") if code else None
    if not desc:
        raise ValueError(f"no get_series descriptor for {code!r}")
    return propose.propose_series_label(desc)


def _render_done(led: "L.Ledger", date_str: str) -> None:
    """Best-effort RenMac reconstruction of every APPROVED chart (the last seam).
    A render snag is REPORTED on the row (`_render_error`) and leaves the chart
    APPROVED for a retry/hand-render — it never aborts the run."""
    import build_chart as BC
    out = DATA / f"backfill_{date_str}" / "renders"
    for row in led.by_status(L.APPROVED):
        sp = str(out / f"{row['chart_id']}.png")
        try:
            info = BC.render_row(row, sp)
            led.set_status(row, L.DONE, output_path=info["save_path"])
            print(f"  [render] {row['chart_id']} → {sp}  (end {info['end']})")
        except Exception as e:
            row["_render_error"] = f"{type(e).__name__}: {e}"
            print(f"  [render] {row['chart_id']} SNAGGED: {row['_render_error']}")


def _downstream(date_str: str, *, mode: str = "confirm_all",
                do_render: bool = True) -> None:
    """The chained G8 invocation: read+resolve → (parked) series ask → confirm_all
    resolution gate → title round-trip → render. confirm_all is the production
    default; selective_park is preserved behind --resolution-mode.

    "No commentary" is NOT a run mode — it's a `none` choice in the title round-trip
    (`[id] none` → title-less), so a bare chart still flows the normal path and the
    title/none decision is made in Teams after seeing the chart."""
    import approval as A
    import resolve as R
    import teams as TM
    led = L.Ledger(str(_dated_ledger(date_str)))

    n_read = _read_resolve(led)
    led.save()
    print(f"  [g8] read+resolved {n_read} fresh chart(s)")

    transport = _transport(date_str)
    confirm = R.confirm_ticker
    kw = _resolve_kwargs()
    resolve_fn = lambda slot: R.resolve_slot(slot, **kw)   # noqa: E731 (re-resolve)

    if mode == "confirm_all":
        # parked slots get the focused per-series ask; once every slot binds the
        # chart is RESOLVED and enters the confirm_all gate below.
        A.run_series_roundtrip(led, transport, resolve_fn=resolve_fn,
                               confirm_ticker=confirm)
        A.run_resolution_roundtrip(
            led, transport, confirm_ticker=confirm,
            metrics_path=str(DATA / f"backfill_{date_str}" / "approve_metrics.csv"),
            label_fn=_legend_label_fn)
        A.run_title_roundtrip(led, transport, _titles,
                              from_status=L.RESOLUTION_APPROVED,
                              gate_status=L.AWAITING_TITLE,
                              parser=TM.parse_title_reply)
    else:  # selective_park (preserved): auto-bind, park on failure, then titles
        A.run_series_roundtrip(led, transport, resolve_fn=resolve_fn,
                               confirm_ticker=confirm)
        A.run_title_roundtrip(led, transport, _titles,
                              parser=TM.parse_title_reply)
    led.save()

    if do_render:
        _render_done(led, date_str)
        led.save()
    _seam_report(led)


def _publish_stores() -> None:
    """Copy the ratified stores to the shared drive so teammates inherit today's binds.

    Runs LAST and never fails the day. An approval round writes `learned_descriptors`
    / `legend_labels` / `trusted_tickers`, and those writes are what a teammate's chat
    lane reads; without this they would only ever see the state of the day the folder
    was last copied by hand.

    Deliberately fail-soft, and deliberately at the END. The share is a network path:
    it can be disconnected on a laptop, or slow. The day's real work — ingest, resolve,
    the Teams round-trips, the renders — is already finished and saved by the time this
    runs, so a share problem must warn and nothing more. Set `G7_NO_PUBLISH=1` to skip.
    """
    if os.environ.get("G7_NO_PUBLISH", "").lower() in ("1", "true", "yes"):
        print("  [publish] skipped (G7_NO_PUBLISH set)")
        return
    try:
        if str(ROOT / "scripts") not in sys.path:
            sys.path.insert(0, str(ROOT / "scripts"))
        import publish_knowledge as PK
        import resolve as R
        counts = PK.publish(R.CLARIFIED_DIR, PK.DEFAULT_DEST,
                            log=lambda m: None)      # quiet; summarize below
        total = ", ".join(f"{n.split('.')[0]} {c}" for n, c in counts.items())
        print(f"  [publish] stores -> {PK.DEFAULT_DEST}  ({total})")
    except Exception as exc:
        print(f"  [publish] SKIPPED — {type(exc).__name__}: {exc}\n"
              f"            teammates keep the previously published stores; re-run "
              f"`python scripts/publish_knowledge.py` when the share is reachable",
              file=sys.stderr)


def _retitle(date_str: str, chart_id: str, *, do_render: bool = True) -> int:
    """Re-open a resolved/rendered chart's TITLE round-trip WITHOUT re-resolving —
    tickers/transforms/axes are already approved and must not be disturbed.

    Pass 1: reset a finished (done/approved) chart to `resolution_approved`, clearing
    only the title + render-forward state, so the title round-trip re-fires and posts
    fresh options in Teams. Pass 2 (re-run the same command): the reply is harvested,
    the chart flips APPROVED and RE-RENDERS (a retitle that doesn't regenerate the PNG
    is useless). Scoped to the state machine — only charts in the title statuses move,
    so other finished charts are untouched."""
    import approval as A
    import resolve as R
    import teams as TM
    led = L.Ledger(str(_dated_ledger(date_str)))
    row = next((r for r in led.rows if r["chart_id"] == chart_id), None)
    if row is None:
        print(f"[retitle] no chart '{chart_id}' in {_dated_ledger(date_str)}",
              file=sys.stderr)
        return 1
    slots = row.get("series") or []
    if slots and any(s.get("status") != R.SLOT_RESOLVED for s in slots):
        print(f"[retitle] {chart_id} is not fully resolved "
              f"([{row['status']}]) — resolve its tickers first", file=sys.stderr)
        return 1
    if row["status"] in (L.DONE, L.APPROVED):
        # reset ONLY the title/render-forward state; keep resolved series intact
        led.set_status(row, L.RESOLUTION_APPROVED, chosen_title="", title_options=[],
                       ask_cursor="", ask_sig="", thread_id="")
        print(f"[retitle] {chart_id}: reset done → resolution_approved "
              f"(re-firing the title ask; tickers untouched)")
    elif row["status"] in (L.RESOLUTION_APPROVED, L.AWAITING_TITLE):
        print(f"[retitle] {chart_id}: already mid-retitle [{row['status']}] "
              f"— harvesting your reply")
    else:
        print(f"[retitle] {chart_id} is [{row['status']}], not a finished chart "
              f"— nothing to retitle", file=sys.stderr)
        return 1

    transport = _transport(date_str)
    A.run_title_roundtrip(led, transport, _titles,
                          from_status=L.RESOLUTION_APPROVED,
                          gate_status=L.AWAITING_TITLE,
                          parser=TM.parse_title_reply)
    led.save()
    if do_render:
        _render_done(led, date_str)
        led.save()
    _seam_report(led)
    return 0


def _relegend(date_str: str, chart_id: str, *, do_render: bool = True) -> int:
    """Re-open a finished chart's RESOLUTION round-trip for LEGEND edits ONLY —
    tickers/transforms/axes AND the chosen title/subtitle/st_force are all preserved.

    Pass 1: reset a finished (done/approved) chart to `resolved`, keeping its title so
    only the resolution summary (with the per-series `legend:` lines) re-fires in Teams.
    Pass 2 (re-run the same command): reply `[id] legendN=… approve`; the confirmed
    labels persist to `legend_labels.json` (write-once, code-keyed) and the chart is
    promoted straight back to APPROVED — WITHOUT re-firing the title round-trip — and
    re-renders. Scoped to the state machine, so other finished charts are untouched."""
    import approval as A
    import resolve as R
    import teams as TM
    led = L.Ledger(str(_dated_ledger(date_str)))
    row = next((r for r in led.rows if r["chart_id"] == chart_id), None)
    if row is None:
        print(f"[relegend] no chart '{chart_id}' in {_dated_ledger(date_str)}",
              file=sys.stderr)
        return 1
    slots = row.get("series") or []
    if slots and any(s.get("status") != R.SLOT_RESOLVED for s in slots):
        print(f"[relegend] {chart_id} is not fully resolved "
              f"([{row['status']}]) — resolve its tickers first", file=sys.stderr)
        return 1
    if row["status"] in (L.DONE, L.APPROVED):
        # reset ONLY to the resolution gate; keep resolved series AND the chosen title
        led.set_status(row, L.RESOLVED, ask_cursor="", ask_sig="", thread_id="")
        print(f"[relegend] {chart_id}: reset {row['status']} → resolved "
              f"(re-firing the resolution summary for legend edits; title untouched)")
    elif row["status"] in (L.RESOLVED, L.AWAITING_RESOLUTION):
        print(f"[relegend] {chart_id}: already mid-relegend [{row['status']}] "
              f"— harvesting your reply")
    else:
        print(f"[relegend] {chart_id} is [{row['status']}], not a finished chart "
              f"— nothing to relegend", file=sys.stderr)
        return 1

    transport = _transport(date_str)
    A.run_resolution_roundtrip(led, transport, confirm_ticker=R.confirm_ticker,
                               learn=False, label_fn=_legend_label_fn)
    # a legend edit must NOT force a fresh title round-trip: promote a re-approved
    # chart straight to APPROVED (its title/subtitle/st_force are already chosen).
    for r in led.by_status(L.RESOLUTION_APPROVED):
        if r["chart_id"] == chart_id:
            led.set_status(r, L.APPROVED)
    led.save()
    if do_render:
        _render_done(led, date_str)
        led.save()
    _seam_report(led)
    return 0


def _seam_report(led: "L.Ledger") -> None:
    """Per-chart shakedown report: read → resolve(auto/parked/corrected) → which
    round-trip each chart is waiting on → render outcome."""
    print("\n" + "=" * 78)
    print("G8 SEAM REPORT (read → resolve → confirm_all → title → render)")
    print("=" * 78)
    tally = {"auto": 0, "parked": 0, "skipped": 0}
    for row in led.rows:
        slots = row.get("series") or []
        spec = row.get("chart_spec") or {}
        print(f"\n  {row['chart_id']}  «{row.get('subject','')}»  [{row['status']}]")
        if not spec:
            print("    (no vision read — parked at awaiting_ticker)")
            continue
        print(f"    read: n_series={spec.get('n_series')} axis_mode={spec.get('axis_mode')}"
              f" recession={spec.get('recession_shading')} start={spec.get('sample_start')}")
        for s in slots:
            st = s.get("status")
            tag = ("AUTO " if st == "resolved" else
                   "PARK " if st == "pending" else "SKIP ")
            tally["auto" if st == "resolved" else
                  "parked" if st == "pending" else "skipped"] += 1
            what = s.get("resolved") or s.get("formula") or s.get("description") or "?"
            extra = ""
            if st == "resolved" and s.get("relevance") is not None:
                extra = f"  sim={s.get('relevance'):.3f}{'(exact)' if s.get('relevance_exact') else ''}"
            print(f"      [{tag}] {s.get('idx')}: {what}"
                  f"{'  ('+s.get('reason','')+')' if st!='resolved' and s.get('reason') else ''}{extra}")
        if row.get("output_path"):
            print(f"    render: {row['output_path']}")
        if row.get("_render_error"):
            print(f"    render SNAG: {row['_render_error']}")
    print("\n  " + "-" * 74)
    print(f"  slots: auto-resolved={tally['auto']}  parked={tally['parked']}"
          f"  skipped={tally['skipped']}")
    counts: dict = {}
    for row in led.rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print("  charts by status: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("=" * 78)


# ─────────────────────────────── selftest ──────────────────────────────────
def _selftest() -> int:
    import base64
    print("[selftest] window + filter math")
    assert I.eastern_day_window("2026-06-25") == (
        "2026-06-25T04:00:00Z", "2026-06-26T04:00:00Z"), "EDT window wrong"
    assert I.eastern_day_window("2026-01-15") == (
        "2026-01-15T05:00:00Z", "2026-01-16T05:00:00Z"), "EST window wrong"
    flt = I.build_filter("2026-06-25T04:00:00Z", "2026-06-26T04:00:00Z",
                         I.DEFAULT_SENDER)
    assert "from/emailAddress/address eq 'ndutta@renmac.com'" in flt
    assert "receivedDateTime ge 2026-06-25T04:00:00Z" in flt
    assert "receivedDateTime lt 2026-06-26T04:00:00Z" in flt
    print("  PASS EDT=04:00Z, EST=05:00Z, $filter sender+window")

    notes = ROOT / "notes"
    png = (notes / "sample1_chart1.png").read_bytes()
    html = f'<img src="data:image/png;base64,{base64.b64encode(png).decode()}">'
    docx = (notes / "sample3.docx").read_bytes()
    msg = {
        "id": "selftest-msg", "subject": "Charts for the daily (selftest)",
        "receivedDateTime": "2026-06-25T13:00:00Z",
        "from": {"emailAddress": {"address": I.DEFAULT_SENDER}},
        "hasAttachments": True,
        "body": {"contentType": "html", "content": html},
        "attachments": [
            {"name": "sample3.docx", "contentType": I.DOCX_CT,
             "contentBytes": base64.b64encode(docx).decode()},
            {"name": "chart.emf", "contentType": "image/emf",
             "contentBytes": base64.b64encode(b"NOT_AN_IMAGE_EMF").decode()},
        ],
    }
    er = I.ingest_message(msg, use_vision=False)
    day = {"date": "2026-06-25",
           "window": I.eastern_day_window("2026-06-25"),
           "filter": flt, "mailbox": "(offline notes/ fixtures)",
           "sender": I.DEFAULT_SENDER, "n_fetched": 1, "n_subject_kept": 1,
           "results": [er]}
    tot = _report(day)

    print("\n[selftest] assertions")
    checks = [
        (tot["raw_haver"] >= 2, "≥2 raw_haver (inline sample1_chart1 + docx image3)"),
        (tot["skip"] >= 1, "≥1 skip (docx image1/image2 finished charts)"),
        (any(e.detected == "emf" for e in er.errors),
         "chart.emf captured as extract error (EMF→Teams, not dropped)"),
        (len(er.commentary) >= 0, "docx commentary read"),
    ]
    ok = True
    for cond, msg_ in checks:
        print(("  PASS " if cond else "  FAIL ") + msg_)
        ok = ok and cond

    ok = _selftest_subject_gate() and ok
    ok = _selftest_bloomberg() and ok
    print(f"\n[selftest] {'ALL PASS' if ok else 'FAILURE'}")
    return 0 if ok else 1


def _selftest_subject_gate() -> bool:
    """The broad subject gate /\\bdaily\\b/i and its asymmetry, regression-locked:
      * COVERAGE — every form Neil uses matches ("Daily", "Daily charts",
        "for the daily", "RE: for the daily", …); a non-daily subject does NOT.
      * OVER-MATCH NO-OP (end-to-end) — a matching subject with NO raw-Haver chart
        (a "daily standup"-type note) is admitted by the regex, but the classifier
        seeds NOTHING: 0 rows, no error. Proves over-match is a cheap no-op, not just
        asserted at the regex — the whole point of going broad."""
    import shutil
    import tempfile
    print("\n[selftest] subject gate /\\bdaily\\b/i (broad; classify is the real gate)")
    ok = True
    for s in ("Daily", "Daily charts", "for the daily", "RE: for the daily",
              "FW: Daily 7/2", "Charts for today's daily"):
        c = bool(I.SUBJECT_RE.search(s))
        print(("  PASS " if c else "  FAIL ") + f"match: {s!r}")
        ok = ok and c
    for s in ("weekly recap", "monthly wrap", "P&L update", ""):
        c = not bool(I.SUBJECT_RE.search(s))
        print(("  PASS " if c else "  FAIL ") + f"non-match: {s!r}")
        ok = ok and c

    # a "daily standup"-type note: passes the broad gate, carries no chart → no-op.
    msg = {
        "id": "selftest-overmatch", "subject": "Daily standup notes",
        "receivedDateTime": "2026-06-25T13:00:00Z",
        "from": {"emailAddress": {"address": I.DEFAULT_SENDER}},
        "hasAttachments": False,
        "body": {"contentType": "text", "content": "no charts here, just notes"},
        "attachments": [],
    }
    assert I.SUBJECT_RE.search(msg["subject"]), "over-match fixture must pass the gate"
    er = I.ingest_message(msg, use_vision=False)
    day = {"date": "2026-06-25", "source": "email",
           "window": I.eastern_day_window("2026-06-25"), "filter": "(offline)",
           "mailbox": "(offline over-match fixture)", "sender": I.DEFAULT_SENDER,
           "n_fetched": 1, "n_subject_kept": 1, "results": [er]}
    tot = _report(day)

    global DATA  # _seed/_dated_ledger resolve the ledger under DATA
    saved_data = DATA
    tmp = Path(tempfile.mkdtemp(prefix="subjgate_selftest_"))
    DATA = tmp / "data"
    try:
        seeded = _seed([day], "2026-06-25")
        rows = len(L.Ledger(str(_dated_ledger("2026-06-25"))).rows)
    finally:
        DATA = saved_data
        shutil.rmtree(tmp, ignore_errors=True)

    for cond, m in (
        (tot["raw_haver"] == 0, "over-match: 0 raw_haver charts classified"),
        (not er.errors, "over-match: no extract errors"),
        (seeded == 0, "over-match: 0 rows seeded END-TO-END (clean no-op)"),
        (rows == 0, "over-match: dated ledger has 0 rows"),
    ):
        print(("  PASS " if cond else "  FAIL ") + m)
        ok = ok and cond
    return ok


def _selftest_bloomberg() -> bool:
    """Bloomberg lane on a temp folder: MMDDYYYY mapping, content-hash key, docx
    extract+classify, source-aware report, and insert-only idempotent seeding."""
    import shutil
    import tempfile
    import folder_ingest as FI

    print("\n[selftest] bloomberg folder lane")
    date_str = "2026-06-29"
    tmp = Path(tempfile.mkdtemp(prefix="bb_selftest_"))
    try:
        folder = FI.folder_for(date_str, root=tmp)
        folder.mkdir(parents=True)
        assert folder.name == "06292026", f"MMDDYYYY map wrong: {folder.name}"
        shutil.copy(ROOT / "notes" / "sample3.docx", folder / "jobless_claims.docx")

        day = FI.ingest_bloomberg_day(date_str, root=tmp, use_vision=False)
        rh = sum(len(er.raw_haver) for er in day["results"])
        keys = {er.id for er in day["results"]}
        _report(day)

        # Seed twice into a temp dated ledger; second pass must be a no-op (insert-only).
        global DATA  # _seed/_dated_ledger resolve the ledger under DATA
        saved_data = DATA
        DATA = tmp / "data"
        try:
            n1 = _seed([day], date_str)
            led = L.Ledger(str(_dated_ledger(date_str)))
            rows1 = len(led.rows)
            n2 = _seed([day], date_str)  # re-run, same content hash
            led2 = L.Ledger(str(_dated_ledger(date_str)))
            rows2 = len(led2.rows)
        finally:
            DATA = saved_data

        checks = [
            (day["source"] == "bloomberg", "day tagged source=bloomberg"),
            (day["n_docs"] == 1, "1 docx ingested from MMDDYYYY folder"),
            (rh >= 1, "≥1 raw_haver chart classified from the docx"),
            (all(k.startswith("bb:") for k in keys), "content-hash key bb:<sha1>"),
            (n1 >= 1 and n2 == 0, "insert-only: re-run seeds 0 (idempotent)"),
            (rows1 == rows2, "re-run adds no duplicate rows"),
        ]
        ok = True
        for cond, m in checks:
            print(("  PASS " if cond else "  FAIL ") + m)
            ok = ok and cond
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────── main ──────────────────────────────────
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="Eastern calendar day YYYY-MM-DD (default: today ET)")
    ap.add_argument("--mailbox", default=None)
    ap.add_argument("--sender", default=I.DEFAULT_SENDER)
    ap.add_argument("--source", choices=["email", "bloomberg", "all"],
                    default="email",
                    help="email inbox, bloomberg-chat docx folder, or both")
    ap.add_argument("--reprocess", action="store_true",
                    help="wipe the dated ledger first (clean repeatable redo)")
    ap.add_argument("--vision", action="store_true",
                    help="Opus vision classify confirm (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--approve", action="store_true",
                    help="run the full G8 chain: read+resolve → confirm_all → title "
                         "→ render (off by default; the front-half shakedown is the "
                         "ingest report)")
    ap.add_argument("--resolution-mode", choices=["confirm_all", "selective_park"],
                    default="confirm_all",
                    help="confirm_all (default): nothing renders until you ratify the "
                         "full resolved set; selective_park: auto-bind, park on failure")
    ap.add_argument("--no-render", action="store_true",
                    help="stop after the round-trips; skip the render seam")
    ap.add_argument("--retitle", metavar="CHART_ID",
                    help="re-open a finished chart's TITLE round-trip (title/subtitle/"
                         "st_force; tickers untouched) and re-render — no ingest/resolve")
    ap.add_argument("--relegend", metavar="CHART_ID",
                    help="re-open a finished chart's RESOLUTION summary for LEGEND edits "
                         "(reply `[id] legendN=… approve`; title/tickers untouched) and "
                         "re-render — no ingest/resolve")
    ap.add_argument("--selftest", action="store_true",
                    help="offline proof on notes/ fixtures (no Graph)")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    date_str = args.date or datetime.now(I.EASTERN).strftime("%Y-%m-%d")

    if args.retitle:                        # focused title-only re-open; no ingest/seed
        rc = _retitle(date_str, args.retitle, do_render=not args.no_render)
        _publish_stores()
        return rc

    if args.relegend:                       # focused legend-only re-open; no ingest/seed
        rc = _relegend(date_str, args.relegend, do_render=not args.no_render)
        _publish_stores()
        return rc

    sources = ["email", "bloomberg"] if args.source == "all" else [args.source]

    if args.reprocess:
        p = _dated_ledger(date_str)
        # HARD GUARD: --reprocess may ONLY wipe a dated backfill ledger, never the
        # production outputs/ledger.csv. The two lanes can't cross.
        if p.parent.resolve() != DATA.resolve() or not p.name.startswith("ledger_backfill_"):
            print(f"ERROR: refusing to --reprocess non-backfill ledger {p}",
                  file=sys.stderr)
            return 2
        if p.exists():
            p.unlink()
            print(f"[reprocess] wiped {p}")

    days: list[dict] = []
    if "email" in sources:
        mailbox = args.mailbox or os.environ.get("AS_DAILY_MAILBOX") \
            or os.environ.get("AS_SENDER_EMAIL")
        if not mailbox:
            print("ERROR: no mailbox (set --mailbox / AS_DAILY_MAILBOX / AS_SENDER_EMAIL)",
                  file=sys.stderr)
            return 2
        try:
            day = I.ingest_day(mailbox, date_str, sender=args.sender,
                               use_vision=args.vision)
        except I.MailAccessError as e:
            print(f"[FAIL] Mail.Read access: {e}", file=sys.stderr)
            return 1
        _report(day)
        days.append(day)

    if "bloomberg" in sources:
        import folder_ingest as FI
        day = FI.ingest_bloomberg_day(date_str, use_vision=args.vision)
        _report(day)
        days.append(day)

    _seed(days, date_str)
    if args.approve:
        _downstream(date_str, mode=args.resolution_mode,
                    do_render=not args.no_render)
        _publish_stores()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
