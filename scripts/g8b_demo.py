"""
g8b_demo.py — prove the G8b Stage-2 resolver + per-series-slot Teams loop OFFLINE.

Stage 1 (vision read) is signed (G8a). Stage 2 turns a read into N per-series SLOTS
and resolves each WITHOUT a live MCP/DLX/Graph call — every dependency is injected:

  * confirm(code@db)  — stands in for DLX get-data existence (the runtime guard);
  * search(query)     — stands in for the haver-metadata MCP search_series;
  * get_meta(code)    — stands in for get_series metadata (SA cross-check);
  * a temp clarified-knowledge store (trusted_tickers.json + native_ma.json).

Branches proven (each a labelled assertion, no self-grade — it prints PASS/FAIL):

  AUTO (no human):
   A. formula sum confirms EVERY addend (BEEM1+BEEM2+BEEM3) → bound, codes×3.
   B. formula with a missing addend (…+BECMO) → PARKED (never silent-binds a sum).
   C. description single confident match → auto-bound (texas 2 series).
   D. native-MA resolved from the clarified store (WGTO) → no re-ask.
   E. SA cross-check rejects a wrong-SA candidate → no confident match → parked.

  HUMAN (per-slot Teams round-trip via StubTransport):
   F. ambiguous (2 metadata-identical PPI candidates) → ask → reply #idx code@db
      → confirmed → RESOLVED; the question is posted EXACTLY once (cursor model).
   G. native-MA NOT in the store → CLARIFY ask → reply "name" → persisted to the
      store → re-resolved → bound (and the question re-posts on the new signature).
   H. PARTIAL chart (one slot auto-binds, one pends) → the WHOLE chart stays parked
      (Defect 2: never RESOLVED, no render) until the human answers the second slot.
   I. `skip` drops the WHOLE chart → SKIPPED (both slots skipped).

Run:  python scripts/g8b_demo.py
"""

import shutil
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")   # Windows console is cp1252 by default
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import approval as A          # noqa: E402
import ledger as L            # noqa: E402
import resolve as R           # noqa: E402
import teams as TM            # noqa: E402

WORK = ROOT / "outputs" / "g8b"
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True, exist_ok=True)

# Point the clarified-knowledge store at a throwaway dir so the demo is hermetic
# and also exercises save_clarified/load (G's persisted answer).
R.CLARIFIED_DIR = WORK / "clarified-knowledge"
R.CLARIFIED_DIR.mkdir(parents=True, exist_ok=True)
(R.CLARIFIED_DIR / "trusted_tickers.json").write_text(
    '{"ypwm":"YPWM@USECON","ycp":"YCP@USECON"}', encoding="utf-8")
# WGTO native-MA pre-confirmed (branch D); the branch-G descriptor is NOT here.
R.save_clarified("Wage Growth Tracker: Overall: 3-Mo Mov Avg of Median Wage Growth",
                 True, note="seed for demo D", directory=R.CLARIFIED_DIR)

# ───────────────────────── injected Haver universe (stub) ───────────────────
# A small CATALOG (code → descriptor + metadata) plus a token-overlap `search` that
# mimics search_series (rank by query↔(descriptor∪code) overlap, hard db/sa filters).
# This genuinely exercises build_search_attempts (recall) + the descriptor-relevance
# gate, including the headline-vs-directional-sibling separation (DFBACTS vs DFBACTDS).
_CATALOG = {
    # texas — truth + look-alike distractors (the gate must pick the headline series)
    "DFBACTS@SURVEYS": {"descriptor": "Texas Mfg Outlook Survey: General Business Activity, 6 Months Ahead (SA, %Bal)", "sa_status": "sa", "frequency": "M", "agg_type": "AVG"},
    "DBACTS@SURVEYS": {"descriptor": "Texas Mfg Outlook Survey: General Business Activity (SA, %Bal)", "sa_status": "sa", "frequency": "M", "agg_type": "AVG"},
    "DFBACTDS@SURVEYS": {"descriptor": "Texas Mfg Outlook Survey: General Business Activity, 6 Months Ahead: Worsened (SA, %)", "sa_status": "sa", "frequency": "M", "agg_type": "AVG"},
    "DSACTS@SURVEYS": {"descriptor": "Texas Service Sector Outlook Survey: General Business Activity (SA, %Bal)", "sa_status": "sa", "frequency": "M", "agg_type": "AVG"},
    # native-MA targets
    "WGTO@USECON": {"descriptor": "Wage Growth Tracker: Overall: 3-Mo Mov Avg of Median Wage Growth (NSA, Y/Y %Chg)", "sa_status": "nsa", "frequency": "M", "agg_type": "AVG"},
    "MEDWG@USECON": {"descriptor": "Median Wage Growth Tracker 3-Mo Mov Avg (NSA)", "sa_status": "nsa", "frequency": "M", "agg_type": "AVG"},
    # SA / freq probes
    "XSA@USECON": {"descriptor": "SA Mismatch Test Series (NSA)", "sa_status": "nsa", "frequency": "M", "agg_type": "AVG"},
    "XQ@USECON": {"descriptor": "Freq Mismatch Test Series (SA)", "sa_status": "sa", "frequency": "Q", "agg_type": "AVG"},
    # PPI ambiguous pair (metadata-identical → both exact → park)
    "SP3210A@PPI": {"descriptor": "PPI: Manufacturing Industries (SA)", "sa_status": "sa", "frequency": "M", "agg_type": "AVG"},
    "SP3210B@PPI": {"descriptor": "PPI: Manufacturing Industries (SA)", "sa_status": "sa", "frequency": "M", "agg_type": "AVG"},
    "PA413121@USECON": {"descriptor": "Final Demand Private Capital Equipment for Manufacturing Industries (SA)", "sa_status": "sa", "frequency": "M", "agg_type": "AVG"},
    # Fed Funds EOP/AVG (same name + freq, differ only by agg_type)
    "FFEDTARE@USECON": {"descriptor": "Federal Open Market Committee: Fed Funds Target Rate (EOP, %)", "sa_status": "unknown", "frequency": "M", "agg_type": "EOP"},
    "FFEDTAR@USECON": {"descriptor": "Federal Open Market Committee: Fed Funds Target Rate (%)", "sa_status": "unknown", "frequency": "M", "agg_type": "AVG"},
    # double-transform guard (mpcuhsro-style): an ALREADY-%Chg series vs the raw LEVEL,
    # under near-identical descriptors. WIDGETMM signals change via descriptor ("M/M
    # %Chg"); GADGETX signals it via agg_type NDF only; THINGLV is a genuine LEVEL that
    # a yoy read must STILL bind (negative control — the guard must not over-park CPI).
    "WIDGETMM@USECON": {"descriptor": "Consumer Prices Widget (SA, M/M %Chg)", "sa_status": "sa", "frequency": "M", "agg_type": "NDF"},
    "GADGETX@USECON": {"descriptor": "Consumer Prices Gadget (SA)", "sa_status": "sa", "frequency": "M", "agg_type": "NDF"},
    "THINGLV@USECON": {"descriptor": "Consumer Prices Thing (SA, Index 2012=100)", "sa_status": "sa", "frequency": "M", "agg_type": "AVG"},
    # formula mnemonics (resolved by CODE, not descriptor)
    "BEEM1@SURVEYS": {"descriptor": "CEO Confidence Survey: Employment Component One", "sa_status": "unknown", "frequency": "Q", "agg_type": "AVG"},
    "BEEM2@SURVEYS": {"descriptor": "CEO Confidence Survey: Employment Component Two", "sa_status": "unknown", "frequency": "Q", "agg_type": "AVG"},
    "BEEM3@SURVEYS": {"descriptor": "CEO Confidence Survey: Employment Component Three", "sa_status": "unknown", "frequency": "Q", "agg_type": "AVG"},
    "BECMU@SURVEYS": {"descriptor": "CEO Confidence Survey: Capex Component U", "sa_status": "unknown", "frequency": "Q", "agg_type": "AVG"},
    # BECMO deliberately ABSENT (branch B — unconfirmed addend parks the sum)
}


def _db_of(code):
    return code.split("@")[-1].lower()


def _tok(s):
    import re
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def confirm(code):
    return (code or "").upper() in _CATALOG


def get_meta(code):
    return _CATALOG.get((code or "").upper())


def search(query, databases=None, sa_status=None):
    """Token-overlap stand-in for search_series: rank catalog by query↔(descriptor +
    code) overlap; hard-filter by database and sa_status when provided."""
    q = _tok(query)
    dbset = {d.lower() for d in databases} if databases else None
    scored = []
    for code, m in _CATALOG.items():
        if dbset and _db_of(code) not in dbset:
            continue
        if sa_status and (m.get("sa_status") or "unknown") != sa_status:
            continue
        cand = _tok(m["descriptor"]) | _tok(code.split("@")[0])
        overlap = len(q & cand)
        if overlap:
            scored.append((overlap, code, m["descriptor"]))
    scored.sort(reverse=True)
    return [{"code": c, "descriptor": d} for _, c, d in scored[:12]]


def resolve_fn(slot):
    """Fully-wired resolver closure (reloads the store each call so a just-saved
    clarification takes effect immediately)."""
    return R.resolve_slot(slot, confirm=confirm, get_meta=get_meta, search=search,
                          clarified=R.load_clarified(), trusted=R.load_trusted())


def resolve_fn(slot):
    """Fully-wired resolver closure (reloads the store each call so a just-saved
    clarification takes effect immediately)."""
    return R.resolve_slot(slot, confirm=confirm, get_meta=get_meta, search=search,
                          clarified=R.load_clarified(), trusted=R.load_trusted())


# ───────────────────────────── tiny test harness ───────────────────────────
_RESULTS = []


def check(name, ok, detail=""):
    ok = bool(ok)
    _RESULTS.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def series(**kw):
    base = dict(description="", base_descriptor=None, applied_transform=None,
                formula=None, axis="shared", lag=None, sa_hint="unknown",
                freq_hint="unknown", native_ma_ambiguous=False)
    base.update(kw)
    return base


def slots_for(*specs):
    return R.build_slots({"series": list(specs)})


# ═══════════════════════════════ AUTO branches ══════════════════════════════
print("\n=== G8b AUTO-RESOLUTION (no human) ===")

# A. formula sum — every addend confirmed.
a = resolve_fn(slots_for(series(formula="BEEM1+(BEEM2+BEEM3)",
                                description="(BEEM1 + (BEEM2 + BEEM3))"))[0])
check("A formula sum binds ALL addends",
      a["status"] == R.SLOT_RESOLVED and len(a["codes"]) == 3
      and a["resolved"] == "BEEM1+(BEEM2+BEEM3)", f"codes={a['codes']}")

# B. formula with a missing addend → parked.
b = resolve_fn(slots_for(series(formula="BECMU+BECMO",
                                description="(BECMU + BECMO)"))[0])
check("B formula w/ unconfirmed addend PARKS",
      b["status"] == R.SLOT_PENDING and "BECMO" in b["reason"], b["reason"])

# C. description single confident match (texas).
tx = R.resolve_chart(
    slots_for(series(description="Texas Mfg Outlook Survey: General Business Activity, 6 Months Ahead",
                     base_descriptor="Texas Mfg Outlook Survey: General Business Activity, 6 Months Ahead",
                     sa_hint="sa", axis="shared"),
              series(description="Texas Mfg Outlook Survey: General Business Activity",
                     base_descriptor="Texas Mfg Outlook Survey: General Business Activity",
                     sa_hint="sa", axis="shared")),
    confirm=confirm, get_meta=get_meta, search=search,
    clarified=R.load_clarified(), trusted=R.load_trusted())
check("C texas 2 desc series auto-bind",
      [s["status"] for s in tx] == [R.SLOT_RESOLVED, R.SLOT_RESOLVED]
      and tx[0]["resolved"] == "DFBACTS@SURVEYS" and tx[1]["resolved"] == "DBACTS@SURVEYS",
      f"{tx[0]['resolved']} / {tx[1]['resolved']}")

# D. native-MA resolved from the clarified store (no re-ask).
d = resolve_fn(slots_for(series(
    description="Wage Growth Tracker: Overall: 3-Mo Mov Avg of Median Wage Growth",
    base_descriptor="Wage Growth Tracker: Overall: 3-Mo Mov Avg of Median Wage Growth",
    native_ma_ambiguous=True, sa_hint="nsa"))[0])
check("D native-MA from store auto-binds (no ask)",
      d["status"] == R.SLOT_RESOLVED and not d.get("needs_clarification")
      and d["resolved"] == "WGTO@USECON", d["reason"])

# E. DESCRIPTOR-RELEVANCE GATE (the safety floor): a confirmable, SA-matching
# candidate that is NOT descriptor-relevant must NOT bind, even as the only option.
# "Manufacturing Robot Density Index" only shares the word "manufacturing" with the
# PPI / capital-equipment series → below threshold → park, never a wrong-bind.
e = resolve_fn(slots_for(series(description="Manufacturing Robot Density Index",
                                base_descriptor="Manufacturing Robot Density Index",
                                sa_hint="sa"))[0])
check("E gate floor: confirmable but descriptor-irrelevant → NON-bind (park)",
      e["status"] == R.SLOT_PENDING and not e["resolved"]
      and "relevant" in e["reason"], e["reason"])

# E1. The gate separates a headline series from its near-identical directional sibling:
# texas "General Business Activity" must bind DBACTS, never DFBACTS ("...6 Months
# Ahead") or DFBACTDS ("...: Worsened") — one extra token Jaccard can't resolve, but
# the EXACT token-set match does. (Already exercised in C; asserted explicitly here.)
e1 = resolve_fn(slots_for(series(
    description="Texas Mfg Outlook Survey: General Business Activity",
    base_descriptor="Texas Mfg Outlook Survey: General Business Activity",
    sa_hint="sa"))[0])
check("E1 gate: exact-set match picks headline over directional sibling",
      e1["status"] == R.SLOT_RESOLVED and e1["resolved"] == "DBACTS@SURVEYS"
      and e1.get("relevance_exact") is True, e1["reason"])

# E2. FREQ is ADVISORY: a single confident match whose get_meta freq disagrees with
# the (wrong) read still BINDS — freq-of-record = get_meta — and flags the mismatch.
# (Reframe: don't park on a spurious read-vs-meta freq mismatch the metadata resolves.)
e2 = resolve_fn(slots_for(series(description="Freq Mismatch Test Series",
                                 base_descriptor="Freq Mismatch Test Series",
                                 sa_hint="sa", freq_hint="monthly"))[0])
check("E2 freq advisory: single match BINDS on get_meta freq (no spurious park)",
      e2["status"] == R.SLOT_RESOLVED and e2["resolved"] == "XQ@USECON"
      and e2["freq_resolved"] == "Q" and e2["freq_advisory_mismatch"] is True,
      f"freq_resolved={e2['freq_resolved']} advisory_mismatch={e2['freq_advisory_mismatch']}")

# E3. AGGREGATION is a HARD constraint: "EOP" in the descriptor picks FFEDTARE (EOP)
# over the same-name same-freq FFEDTAR (AVG) — the silent-wrong-between-two-plausible
# case. Only the EOP variant survives the agg cross-check → single match → binds.
e3 = resolve_fn(slots_for(series(
    description="Federal Open Market Committee: Fed Funds Target Rate EOP, %",
    base_descriptor="Federal Open Market Committee: Fed Funds Target Rate",
    sa_hint="unknown", freq_hint="monthly"))[0])
check("E3 EOP cue binds FFEDTARE (EOP), rejects FFEDTAR (AVG)",
      e3["status"] == R.SLOT_RESOLVED and e3["resolved"] == "FFEDTARE@USECON"
      and e3["agg_resolved"] == "eop", f"resolved={e3['resolved']} agg={e3['agg_resolved']}")

# E3b. The SAME chart with NO aggregation cue → both EOP & AVG confirm → REAL
# ambiguity → parked with both candidates (park for the right reason).
e3b = resolve_fn(slots_for(series(
    description="Federal Open Market Committee: Fed Funds Target Rate",
    base_descriptor="Federal Open Market Committee: Fed Funds Target Rate",
    sa_hint="unknown", freq_hint="monthly"))[0])
check("E3b no agg cue → both variants confirm → REAL ambiguity → parked",
      e3b["status"] == R.SLOT_PENDING and len(e3b["candidates"]) == 2,
      f"candidates={e3b['candidates']}")

# E4. DOUBLE-TRANSFORM GUARD (transform-appropriateness): a descriptor-EXACT candidate
# that is ITSELF already a %Chg (per its get_meta descriptor), under a read that applies
# an ADDITIONAL yoy → would double-transform → PARK, not auto-bind (the mpcuhsro case).
e4 = resolve_fn(slots_for(series(
    description="Consumer Prices Widget % Change - Year to Year",
    base_descriptor="Consumer Prices Widget", applied_transform="% Change - Year to Year",
    sa_hint="sa"))[0])
check("E4 double-transform (descriptor %Chg) → PARK not bind",
      e4["status"] == R.SLOT_PENDING and not e4["resolved"]
      and "double-transform" in e4["reason"], e4["reason"])

# E4b. Same guard via agg_type ONLY: descriptor has no change words, but agg_type=NDF
# flags a pre-differenced series → a yoy read still parks.
e4b = resolve_fn(slots_for(series(
    description="Consumer Prices Gadget % Change - Year to Year",
    base_descriptor="Consumer Prices Gadget", applied_transform="% Change - Year to Year",
    sa_hint="sa"))[0])
check("E4b double-transform (agg_type NDF) → PARK not bind",
      e4b["status"] == R.SLOT_PENDING and not e4b["resolved"]
      and "double-transform" in e4b["reason"], e4b["reason"])

# E4c. NEGATIVE CONTROL: a yoy read on a genuine LEVEL index must STILL auto-bind — the
# guard must not over-park the common CPI-yoy case (June-9/May-12 price charts).
e4c = resolve_fn(slots_for(series(
    description="Consumer Prices Thing % Change - Year to Year",
    base_descriptor="Consumer Prices Thing", applied_transform="% Change - Year to Year",
    sa_hint="sa"))[0])
check("E4c guard does NOT over-park: yoy on a LEVEL index still binds",
      e4c["status"] == R.SLOT_RESOLVED and e4c["resolved"] == "THINGLV@USECON",
      f"status={e4c['status']} resolved={e4c.get('resolved')}")


# ═══════════════════════════════ HUMAN branches ═════════════════════════════
print("\n=== G8b HUMAN ROUND-TRIP (per-slot, StubTransport) ===")


def fresh_ledger(tag):
    return L.Ledger(str(WORK / f"ledger_{tag}.csv"))


def stub(tag):
    return TM.StubTransport(str(WORK / f"stub_{tag}.json"))


def seed(led, chart_id, slots):
    row = led.upsert(message_id=f"msg_{chart_id}", chart_index=0,
                     chart_id=chart_id, release_slug="g8b",
                     chart_spec={"series": []}, series=slots)
    L.sync_row_from_slots(row)
    led.save()
    return row


# F. ambiguous PPI → ask once → reply code@db → resolved.
led, tp = fresh_ledger("F"), stub("F")
ppi = resolve_fn(slots_for(series(description="PPI: Manufacturing Industries",
                                  base_descriptor="PPI: Manufacturing Industries",
                                  sa_hint="sa"))[0])
rowF = seed(led, "ppi_chart", [ppi])
check("F parked ambiguous (2 candidates)",
      rowF["status"] == L.AWAITING_TICKER and len(ppi["candidates"]) == 2,
      f"cands={ppi['candidates']}")
A.run_series_roundtrip(led, tp, resolve_fn=resolve_fn, confirm_ticker=confirm)   # posts ask
thr = A.chart_thread("g8b", "ppi_chart")
posts_after_1 = len(tp._thread(thr)["posts"])
A.run_series_roundtrip(led, tp, resolve_fn=resolve_fn, confirm_ticker=confirm)   # no reply yet
posts_after_2 = len(tp._thread(thr)["posts"])
check("F question posted EXACTLY once", posts_after_1 == 1 and posts_after_2 == 1,
      f"posts={posts_after_2}")
tp.queue_reply(thr, "[ppi_chart] #0 PA413121@USECON")
A.run_series_roundtrip(led, tp, resolve_fn=resolve_fn, confirm_ticker=confirm)
check("F resolves on human code@db",
      rowF["status"] == L.RESOLVED
      and rowF["series"][0]["resolved"] == "PA413121@USECON"
      and rowF["resolved_ticker"], f"status={rowF['status']}")

# G. native-MA NOT in store → clarify ask → "name" → persist → re-resolve → bound.
led, tp = fresh_ledger("G"), stub("G")
gslot = resolve_fn(slots_for(series(
    description="Median Wage Growth Tracker 3-Mo Mov Avg",
    base_descriptor="Median Wage Growth Tracker 3-Mo Mov Avg",
    native_ma_ambiguous=True, sa_hint="nsa"))[0])
rowG = seed(led, "wage_chart", [gslot])
check("G parked for native-MA clarification",
      rowG["status"] == L.AWAITING_TICKER and gslot["needs_clarification"],
      gslot["reason"])
A.run_series_roundtrip(led, tp, resolve_fn=resolve_fn, confirm_ticker=confirm)   # CLARIFY ask
thrG = A.chart_thread("g8b", "wage_chart")
tp.queue_reply(thrG, "[wage_chart] #0 name")
A.run_series_roundtrip(led, tp, resolve_fn=resolve_fn, confirm_ticker=confirm)
persisted = R.load_clarified().get(
    R._norm_key("Median Wage Growth Tracker 3-Mo Mov Avg"))
check("G clarification persisted to store",
      bool(persisted) and persisted.get("native_ma") is True, str(persisted))
check("G re-resolved to a bound ticker after clarify",
      rowG["status"] == L.RESOLVED
      and rowG["series"][0]["resolved"] == "MEDWG@USECON",
      f"status={rowG['status']} resolved={rowG['series'][0].get('resolved')}")

# H. PARTIAL → whole chart parked across the gap (Defect 2).
led, tp = fresh_ledger("H"), stub("H")
hslots = R.resolve_chart(
    slots_for(series(description="Texas Mfg Outlook Survey: General Business Activity",
                     base_descriptor="Texas Mfg Outlook Survey: General Business Activity",
                     sa_hint="sa"),
              series(description="Unknown Mystery Series",
                     base_descriptor="Unknown Mystery Series", sa_hint="sa")),
    confirm=confirm, get_meta=get_meta, search=search,
    clarified=R.load_clarified(), trusted=R.load_trusted())
rowH = seed(led, "partial_chart", hslots)
check("H one slot bound, one pending",
      hslots[0]["status"] == R.SLOT_RESOLVED and hslots[1]["status"] == R.SLOT_PENDING)
check("H whole chart PARKED (not RESOLVED, no render)",
      rowH["status"] == L.AWAITING_TICKER
      and rowH["unresolved"] == ["Unknown Mystery Series"]
      and not rowH.get("output_path"), f"status={rowH['status']}")
A.run_series_roundtrip(led, tp, resolve_fn=resolve_fn, confirm_ticker=confirm)   # ask
thrH = A.chart_thread("g8b", "partial_chart")
tp.queue_reply(thrH, "[partial_chart] #1 DBACTS@SURVEYS")
A.run_series_roundtrip(led, tp, resolve_fn=resolve_fn, confirm_ticker=confirm)
check("H un-parks to RESOLVED only when BOTH bound",
      rowH["status"] == L.RESOLVED
      and rowH["series"][1]["resolved"] == "DBACTS@SURVEYS",
      f"status={rowH['status']}")

# I. skip drops the whole chart.
led, tp = fresh_ledger("I"), stub("I")
islots = R.resolve_chart(
    slots_for(series(description="Texas Mfg Outlook Survey: General Business Activity",
                     base_descriptor="Texas Mfg Outlook Survey: General Business Activity",
                     sa_hint="sa"),
              series(description="Unknown Mystery Series",
                     base_descriptor="Unknown Mystery Series", sa_hint="sa")),
    confirm=confirm, get_meta=get_meta, search=search,
    clarified=R.load_clarified(), trusted=R.load_trusted())
rowI = seed(led, "skip_chart", islots)
A.run_series_roundtrip(led, tp, resolve_fn=resolve_fn, confirm_ticker=confirm)
thrI = A.chart_thread("g8b", "skip_chart")
tp.queue_reply(thrI, "[skip_chart] skip")
A.run_series_roundtrip(led, tp, resolve_fn=resolve_fn, confirm_ticker=confirm)
check("I skip drops the WHOLE chart",
      rowI["status"] == L.SKIPPED
      and all(s["status"] == R.SLOT_SKIPPED for s in rowI["series"]),
      f"status={rowI['status']}")


# ═══════════════════════ confirm_all mode (Part 2/3/4) ══════════════════════
print("\n=== G8b confirm_all MODE (resolution gate → title gate → render) ===")


def titles_for(row):
    return [{"title": "Texas Manufacturing Outlook", "subtitle": "Dallas Fed survey"},
            {"title": "Dallas Fed: Texas Activity", "subtitle": "SA, % balance"}]


def seed_cs(led, chart_id, slots, chart_spec):
    row = led.upsert(message_id=f"msg_{chart_id}", chart_index=0, chart_id=chart_id,
                     release_slug="g8b", chart_spec=chart_spec, series=slots)
    L.sync_row_from_slots(row)
    led.save()
    return row


_CS = {"axis_mode": "shared", "sample_start": "2004", "sample_end_read": None,
       "recession_shading": True, "series": []}


def confirm_all_pass(led, tp, metrics):
    """One orchestration pass in confirm_all order: tickers → resolution gate →
    titles (gated on RESOLUTION_APPROVED) → APPROVED."""
    A.run_series_roundtrip(led, tp, resolve_fn=resolve_fn, confirm_ticker=confirm)
    A.run_resolution_roundtrip(led, tp, confirm_ticker=confirm, metrics_path=metrics)
    A.run_title_roundtrip(led, tp, titles_for, from_status=L.RESOLUTION_APPROVED,
                          gate_status=L.AWAITING_TITLE, parser=TM.parse_title_reply)


# J. clean happy path — resolver binds both texas series → summary posted (once) →
# approve → titles posted → approve → APPROVED; learning store written; metric clean.
led, tp = fresh_ledger("J"), stub("J")
metJ = str(WORK / "metrics_J.csv")
jslots = R.resolve_chart(
    slots_for(series(description="Texas Mfg Outlook Survey: General Business Activity, 6 Months Ahead",
                     base_descriptor="Texas Mfg Outlook Survey: General Business Activity, 6 Months Ahead",
                     sa_hint="sa"),
              series(description="Texas Mfg Outlook Survey: General Business Activity",
                     base_descriptor="Texas Mfg Outlook Survey: General Business Activity",
                     sa_hint="sa")),
    confirm=confirm, get_meta=get_meta, search=search,
    clarified=R.load_clarified(), trusted=R.load_trusted())
rowJ = seed_cs(led, "tx_confirm", jslots, dict(_CS))
thrJ = A.chart_thread("g8b", "tx_confirm")
confirm_all_pass(led, tp, metJ)   # posts resolution summary
check("J nothing renders pre-approve: chart sits in AWAITING_RESOLUTION",
      rowJ["status"] == L.AWAITING_RESOLUTION
      and len(tp._thread(thrJ)["posts"]) == 1, f"status={rowJ['status']}")
confirm_all_pass(led, tp, metJ)   # no reply → no advance, no duplicate post
check("J no duplicate resolution post on a no-reply pass",
      len(tp._thread(thrJ)["posts"]) == 1)
tp.queue_reply(thrJ, "[tx_confirm] approve")
confirm_all_pass(led, tp, metJ)   # approve → RESOLUTION_APPROVED → titles posted
check("J approve → resolution ratified → title round-trip fires",
      rowJ["status"] == L.AWAITING_TITLE and rowJ["title_options"],
      f"status={rowJ['status']}")
tp.queue_reply(thrJ, "[tx_confirm] approve")
confirm_all_pass(led, tp, metJ)   # title approve → APPROVED (renders only now)
check("J title approve → APPROVED (renders only after BOTH approves)",
      rowJ["status"] == L.APPROVED and rowJ["chosen_title"] == "Texas Manufacturing Outlook",
      f"status={rowJ['status']} title={rowJ.get('chosen_title')}")
learnedJ = R.load_learned().get(
    R._norm_key("Texas Mfg Outlook Survey: General Business Activity"))
check("J learning store wrote the approved bind (Part 4)",
      bool(learnedJ) and learnedJ.get("code") == "DBACTS@SURVEYS", str(learnedJ))
import csv as _csv
with open(metJ, encoding="utf-8") as fh:
    mrow = list(_csv.DictReader(fh))[-1]
check("J approve-without-correction logged = 1 (clean)",
      mrow["approved_without_correction"] == "1", str(mrow))

# J2. equilibrium: the SAME description now auto-resolves from the learning store
# (no search needed) — the loop closing.
j2 = R.resolve_slot(series(description="Texas Mfg Outlook Survey: General Business Activity",
                           base_descriptor="Texas Mfg Outlook Survey: General Business Activity",
                           sa_hint="sa"),
                    confirm=confirm, get_meta=get_meta, search=search,
                    learned=R.load_learned())
check("J2 learned description re-resolves instantly (equilibrium)",
      j2["status"] == R.SLOT_RESOLVED and j2["resolved"] == "DBACTS@SURVEYS"
      and j2["bound_via_query"] == "(learned)", j2["reason"])

# K. correction path — human fixes one field, re-post, then approve → metric flags corrected.
led, tp = fresh_ledger("K"), stub("K")
metK = str(WORK / "metrics_K.csv")
kslots = R.resolve_chart(
    slots_for(series(description="Texas Mfg Outlook Survey: General Business Activity, 6 Months Ahead",
                     base_descriptor="Texas Mfg Outlook Survey: General Business Activity, 6 Months Ahead",
                     sa_hint="sa"),
              series(description="Texas Mfg Outlook Survey: General Business Activity",
                     base_descriptor="Texas Mfg Outlook Survey: General Business Activity",
                     sa_hint="sa")),
    confirm=confirm, get_meta=get_meta, search=search,
    clarified=R.load_clarified(), trusted=R.load_trusted())
rowK = seed_cs(led, "tx_correct", kslots, dict(_CS))
thrK = A.chart_thread("g8b", "tx_correct")
confirm_all_pass(led, tp, metK)                      # post summary
tp.queue_reply(thrK, "[tx_correct] 2:axis=R")        # correct series-2 axis
confirm_all_pass(led, tp, metK)                      # apply + re-post
check("K correction applied + summary re-posted (still awaiting approve)",
      rowK["status"] == L.AWAITING_RESOLUTION and rowK["series"][1]["axis"] == "R"
      and len(tp._thread(thrK)["posts"]) == 2, f"posts={len(tp._thread(thrK)['posts'])}")
tp.queue_reply(thrK, "[tx_correct] approve")
confirm_all_pass(led, tp, metK)
with open(metK, encoding="utf-8") as fh:
    mrowK = list(_csv.DictReader(fh))[-1]
check("K approve-after-correction logged = 0 (corrected)",
      rowK["status"] == L.AWAITING_TITLE and mrowK["approved_without_correction"] == "0",
      str(mrowK))

# L. skip in confirm_all drops the chart (never renders).
led, tp = fresh_ledger("L"), stub("L")
lslots = R.resolve_chart(
    slots_for(series(description="Texas Mfg Outlook Survey: General Business Activity",
                     base_descriptor="Texas Mfg Outlook Survey: General Business Activity",
                     sa_hint="sa")),
    confirm=confirm, get_meta=get_meta, search=search,
    clarified=R.load_clarified(), trusted=R.load_trusted())
rowL = seed_cs(led, "tx_skip", lslots, dict(_CS))
thrL = A.chart_thread("g8b", "tx_skip")
confirm_all_pass(led, tp, str(WORK / "metrics_L.csv"))
tp.queue_reply(thrL, "[tx_skip] skip")
confirm_all_pass(led, tp, str(WORK / "metrics_L.csv"))
check("L skip in confirm_all → chart dropped (never renders)",
      rowL["status"] == L.SKIPPED, f"status={rowL['status']}")


# ═══════════════════════════════════ summary ════════════════════════════════
passed, total = sum(_RESULTS), len(_RESULTS)
print(f"\n=== G8b offline proof: {passed}/{total} checks passed ===")
sys.exit(0 if passed == total else 1)
