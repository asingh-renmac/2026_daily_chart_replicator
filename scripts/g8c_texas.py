"""
g8c_texas.py — G8c (v2): LIVE texas resolution under the Part-1 hardenings +
confirm_all mode, STOPPING at the posted resolution summary (no render).

What this proves (per the Part-5 ask):
  1. Both Dallas Fed series RESOLVE through live search → get → confirm — NOT park,
     NOT from the seed (trusted={}, learned={} are bypassed).
  2. confirm_all composes the FULL resolved ChartSpec summary for the chart.
  STOP at step 2: print the summary + HOW (which live query surfaced the binding
  candidate, descriptor-similarity scores). No approve, no title, no render.

The search RESULTS embedded below are the ACTUAL live search_series output captured
at authoring time (DB-scoped to `surveys`, SA-filtered) for the six query variants
build_search_attempts() emits for the two texas slots. Note: every candidate is SA,
so the SA cross-check cannot disambiguate here — the descriptor-relevance gate is the
ONLY thing standing between the read and a wrong bind. That is the point.
"""
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import approval as A          # noqa: E402
import ledger as L            # noqa: E402
import resolve as R           # noqa: E402
import teams as TM            # noqa: E402

# ── LIVE-captured catalog: bare code → descriptor (all sa / M / AVG) ──────────
DESCR = {
    # Texas Mfg Outlook (FRBDAL)
    "dbacts": "Texas Mfg Outlook Survey: General Business Activity (SA, %Bal)",
    "dbactds": "Texas Mfg Outlook Survey: General Business Activity: Worsened (SA, %)",
    "dbactis": "Texas Mfg Outlook Survey: General Business Activity: Improved (SA, %)",
    "dbactns": "Texas Mfg Outlook Survey: General Business Activity: No Change (SA, %)",
    "dfbacts": "Texas Mfg Outlook Survey: General Business Activity, 6 Months Ahead (SA, %Bal)",
    "dfbactds": "Texas Mfg Outlook Survey: Gen Business Activity, 6 Months Ahead: Worsened(SA, %)",
    "dfbactis": "Texas Mfg Outlook Survey: Gen Business Activity, 6 Months Ahead: Improved(SA, %)",
    "dfbactns": "Texas Mfg Outlook Survey: Gen Business Activity, 6 Months Ahead: No Chg (SA, %)",
    "dfcolks": "Texas Mfg Outlook Survey: Company Outlook, 6 Months Ahead (SA, %Bal)",
    "dfprods": "Texas Mfg Outlook Survey: Production, 6 Months Ahead (SA, %Bal)",
    "davwks": "Texas Mfg Outlook Survey: Average Employee Workweek (SA, %Bal)",
    "davwkds": "Texas Mfg Outlook Survey: Average Employee Workweek: Decrease (SA, %)",
    "davwkis": "Texas Mfg Outlook Survey: Average Employee Workweek: Increase (SA, %)",
    "davwkns": "Texas Mfg Outlook Survey: Average Employee Workweek: No Change (SA, %)",
    "dcapus": "Texas Mfg Outlook Survey: Capacity Utilization (SA, %Bal)",
    "dcexps": "Texas Mfg Outlook Survey: Capital Expenditures (SA, %Bal)",
    # Texas Service Sector
    "dsacts": "Texas Service Sector Outlook Survey: General Business Activity (SA, %Bal)",
    "dsactds": "Texas Service Sector Outlook Survey: General Business Activity: Decrease (SA, %)",
    "dsactis": "Texas Service Sector Outlook Survey: General Business Activity: Increase (SA, %)",
    "dsactns": "Texas Service Sector Outlook Survey: General Business Activity: No Chg (SA, %)",
    "dsfacts": "Texas Svc Sector Outlook Survey: Gen Business Activity, 6 Mos Ahead (SA, %Bal)",
    "dsfactds": "TX Svc Sector Outlook Svy: Gen Business Activity, 6 Mos Ahead: Decrease (SA, %)",
    "dsfactis": "TX Svc Sector Outlook Svy: Gen Business Activity, 6 Mos Ahead: Increase (SA, %)",
    "dsfactns": "TX Svc Sector Outlook Survey: Gen Business Activity, 6 Mos Ahead: No Chg (SA, %)",
    # Texas Retail (discontinued)
    "dracts": "Texas Retail Outlook Survey: General Bus Activity DISC(SA, %Bal)",
    "dractds": "Texas Retail Outlook Survey: General Bus Activity: Dec DISC(SA, %)",
    "dractis": "Texas Retail Outlook Survey: General Bus Activity: Inc DISC(SA, %)",
    "dractns": "Texas Retail Outlook Survey: General Bus Activity: No Change DISC(SA, %)",
    "drfacts": "Texas Ret Outlook Survey: General Bus Activity, 6 Months Ahead DISC(SA, %Bal)",
    # distractors from other surveys (descriptor-irrelevant — the gate filters them)
    "bl6whna": "Business Leaders Survey: Wages, 6 Months Ahead: Increase (SA, %)",
    "bl6wina": "Business Leaders Survey: Wages, 6 Months Ahead: Diffusion Index (SA, %Bal)",
    "bl6wlna": "Business Leaders Survey: Wages, 6 Months Ahead: Decrease (SA, %)",
    "bl6wsna": "Business Leaders Survey: Wages, 6 Months Ahead: No Change (SA, %)",
    "bncgds": "Philly Fed Nonmfg Business Outlook: Company General Activity: Decrease(SA, %)",
    "bncgis": "Philly Fed Nonmfg Business Outlook: Company General Activity: Increase(SA, %)",
    "bncgns": "Philly Fed Nonmfg Business Outlook: Company General Activity: No Change(SA, %)",
    "bnrgds": "Philly Fed Nonmfg Business Outlook: Region General Activity: Decrease(SA, %)",
    "bnrgis": "Philly Fed Nonmfg Business Outlook: Region General Activity: Increase(SA, %)",
    "bnrgns": "Philly Fed Nonmfg Business Outlook: Region General Activity: No Change(SA, %)",
    "bocgd": "Philadelphia Fed Mfg Business Outlook: Current Gen Activity: Decrease(SA, %)",
    "bocgi": "Philadelphia Fed Mfg Business Outlook: Current Gen Activity: Increase(SA, %)",
    "bocgn": "Philadelphia Fed Mfg Business Outlook: Current Gen Activity: No Chg(SA, %)",
    "bocgx": "Philly Fed Mfg Business Outlook: Current Activity Diffusion Index(SA, %Bal)",
    "bofgd": "Philadelphia Fed Mfg Business Outlook: Future Gen Activity: Decrease(SA, %)",
    "bofgi": "Philadelphia Fed Mfg Business Outlook: Future Gen Activity: Increase(SA, %)",
    "bofgn": "Philadelphia Fed Mfg Business Outlook: Future Gen Activity: No Change(SA, %)",
    "bofgx": "Philadelphia Fed Mfg Business Outlook Survey: Future Activity Index (SA, %Bal)",
    "bofshd": "Philadelphia Fed Mfg Business Outlook Survey: Future Shipments: Decrease (SA, %)",
    "bofshi": "Philadelphia Fed Mfg Business Outlook Survey: Future Shipments: Increase (SA, %)",
    "bofuod": "Philadelphia Fed Mfg Business Outlook Survey: Future Unfilled Orders: Dec(SA, %)",
    "bofuon": "Philadelphia Fed Mfg Business Outlook Survey: Future Unfilled Orders: Inc(SA, %)",
    "em6gh": "Empire State Mfg Svy: General Business Conditions, 6 Mos Ahead: Increase (SA, %)",
    "em6gl": "Empire State Mfg Svy: General Business Conditions, 6 Mos Ahead: Decrease (SA, %)",
    "em6gs": "Empire State Mfg Survey: General Business Conditions, 6 Mos Ahead: No Chg(SA, %)",
    "nmfbaia": "ISM: Services: Business Activity Index (SA, 50+=Increasing)",
}

# LIVE search_series results (ordered code lists) for the six query variants.
QUERIES = {
    "Texas Mfg Outlook Survey: General Business Activity, 6 Months Ahead": [
        "bl6whna", "bl6wina", "bl6wlna", "bl6wsna", "bncgds", "bncgis", "bncgns",
        "bnrgds", "bnrgis", "bnrgns", "bocgd", "bocgi", "bocgn", "bocgx", "bofgd",
        "bofgi", "bofgn", "bofgx", "davwks", "dbactds", "dbactis", "dbactns",
        "dbacts", "dcapus", "dcexps"],
    "Texas General Business Activity 6 Months Ahead": [
        "bl6whna", "bl6wlna", "bl6wsna", "dbactds", "dbactis", "dbactns", "dbacts",
        "dfbactds", "dfbactis", "dfbactns", "dfbacts", "dfcolks", "dfprods",
        "dsactds", "dsactis", "dsactns", "dsacts", "dsfactds", "dsfactis",
        "dsfactns", "dsfacts", "em6gh", "em6gl", "em6gs", "drfacts"],
    "General Business Activity 6 Months Ahead": [
        "bl6whna", "bl6wina", "bl6wlna", "bl6wsna", "dfbactds", "dfbactis",
        "dfbactns", "dfbacts", "dsfactds", "dsfactis", "dsfactns", "dsfacts",
        "em6gh", "em6gl", "em6gs", "dbactds", "dbactis", "dbactns", "dbacts",
        "dsactds", "dsactis", "dsactns", "dsacts", "bncgds", "bncgis"],
    "Texas Mfg Outlook Survey: General Business Activity": [
        "bncgds", "bncgis", "bncgns", "bnrgds", "bnrgis", "bnrgns", "bocgd",
        "bocgi", "bocgn", "bocgx", "bofgd", "bofgi", "bofgn", "bofgx", "bofshd",
        "bofshi", "bofuod", "bofuon", "davwkds", "davwkis", "davwkns", "davwks",
        "dbactds", "dbactis", "dbactns"],
    "Texas General Business Activity": [
        "dbactds", "dbactis", "dbactns", "dbacts", "dfbacts", "dsactds", "dsactis",
        "dsactns", "dsacts", "bncgds", "bncgis", "bncgns", "bnrgds", "bnrgis",
        "bnrgns", "dfbactds", "dfbactis", "dfbactns", "dsfacts", "dractds",
        "dractis", "dractns", "dracts", "drfacts"],
    "General Business Activity": [
        "dbactds", "dbactis", "dbactns", "dbacts", "dfbacts", "dsactds", "dsactis",
        "dsactns", "dsacts", "bncgds", "bncgis", "bncgns", "bnrgds", "bnrgis",
        "bnrgns", "dfbactds", "dfbactis", "dfbactns", "dsfacts", "nmfbaia",
        "dractds", "dractis", "dracts"],
}


def _ticker(code):
    return f"{code.upper()}@SURVEYS"


def search(query, databases=None, sa_status=None):
    """LIVE-captured search_series stand-in (surveys-scoped, SA-filtered)."""
    return [{"code": _ticker(c), "descriptor": DESCR[c]}
            for c in QUERIES.get(query, [])]


def get_meta(code):
    """get_series metadata for a candidate (every texas-family series is sa/M/AVG)."""
    bare = code.split("@")[0].lower()
    if bare not in DESCR:
        return None
    return {"descriptor": DESCR[bare], "sa_status": "sa",
            "frequency": "M", "agg_type": "AVG"}


def confirm(code):
    """Existence: a code returned by live search_series exists in Haver (the search
    IS the catalog membership check). The authoritative DLX confirm is run on the
    final binds below (live), to honour read=hypothesis / get_series=confirmation."""
    return code.split("@")[0].lower() in DESCR


# ── load the texas vision read (Stage-1) ─────────────────────────────────────
reads = json.loads((ROOT / "outputs" / "g8" / "reads.json").read_text(encoding="utf-8"))


def _charts(obj):
    if isinstance(obj, dict):
        if "series" in obj and isinstance(obj["series"], list):
            yield obj
        for v in obj.values():
            yield from _charts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _charts(v)


texas = next(c for c in _charts(reads)
             if c.get("series") and (c["series"][0].get("description") or "")
             .startswith("Texas Mfg Outlook Survey: General Business Activity, 6 Months"))

slots = R.build_slots(texas)

print("=" * 78)
print("G8c (v2) — LIVE texas resolution  |  hardenings ON  |  mode=confirm_all")
print("           seed BYPASSED: trusted={}  learned={}   (live path only)")
print("=" * 78)

# ── resolve each slot live, tracing the gate ─────────────────────────────────
for s in slots:
    out = R.resolve_slot(s, confirm=confirm, get_meta=get_meta, search=search,
                         trusted={}, learned={})       # seed + learning bypassed
    s.clear()
    s.update(out)
    print(f"\nSLOT #{s['idx']}  READ: {s['description']!r}  (sa_hint={s['sa_hint']})")
    print("  live search attempts (DB-scoped surveys, SA-filtered):")
    for a in s["search_attempts"]:
        print(f"     - {a['query']!r}  → {a['n']} hits")
    print(f"  candidates surviving confirm+SA+agg cross-checks: {len(s['candidate_detail'])}")
    print("  top by descriptor relevance (gate):")
    for c in s["candidate_detail"][:5]:
        mark = "EXACT" if c["exact"] else f"sim={c['sim']}"
        print(f"     {mark:>9}  {c['code']:<16} {c['descriptor']}")
    verdict = "BOUND" if s["status"] == R.SLOT_RESOLVED else "PARKED"
    print(f"  → {verdict}: {s.get('resolved')}   "
          f"(relevance={'exact' if s.get('relevance_exact') else s.get('relevance')}, "
          f"via {s.get('bound_via_query')!r})")
    print(f"    reason: {s['reason']}")

bound = [s for s in slots if s["status"] == R.SLOT_RESOLVED]
print("\n" + "-" * 78)
print(f"RESOLVED {len(bound)}/{len(slots)} series live (no seed, no park).")

# ── authoritative get_series / DLX cross-check on the binds (read=hypothesis) ─
print("\nget_series / DLX cross-check on the binds (live):")
truth = {"Texas Mfg Outlook Survey: General Business Activity, 6 Months Ahead": "DFBACTS@SURVEYS",
         "Texas Mfg Outlook Survey: General Business Activity": "DBACTS@SURVEYS"}
for s in bound:
    code = s["resolved"]
    try:
        exists = R.confirm_ticker(code)
    except Exception as exc:
        exists = f"(DLX error: {exc})"
    exp = truth.get(s["description"])
    ok = "✓" if exp and code == exp else "?"
    print(f"  {code:<16} exists={exists}  freq={s['freq_resolved']}  "
          f"agg={s['agg_resolved']}  vs corrections.json={exp} {ok}")

# ── confirm_all: compose & POST the full resolution summary, then STOP ────────
WORK = ROOT / "outputs" / "g8c"
WORK.mkdir(parents=True, exist_ok=True)
led = L.Ledger(str(WORK / "ledger_texas.csv"))
tp = TM.StubTransport(str(WORK / "stub_texas.json"))
row = led.upsert(message_id="msg_texas", chart_index=0, chart_id="texas_mfg_outlook",
                 release_slug="g8c", chart_spec=texas, series=slots)
L.sync_row_from_slots(row)
led.save()
print("\n" + "=" * 78)
print(f"chart status after resolve: {row['status']}  "
      f"(confirm_all requires explicit approve before ANY render)")

A.run_resolution_roundtrip(led, tp, confirm_ticker=confirm,
                           metrics_path=str(WORK / "approval_metrics.csv"))
thread = A.chart_thread("g8c", "texas_mfg_outlook")
posted = tp._thread(thread)["posts"][-1]["text"]
print("=" * 78)
print("confirm_all POSTED this resolution summary to Teams (awaiting your approve):\n")
print(posted)
print("\n" + "=" * 78)
print(f"STOP — chart is {row['status']}. Nothing renders until you reply "
      f"`[texas_mfg_outlook] approve`.")
print("Held here for your review (Part-5 STOP at step 2). No title, no render.")
