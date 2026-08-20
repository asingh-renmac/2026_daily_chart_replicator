"""
selftest_workflow.py — offline proof of the three May-12 workflow features, no
Graph/Haver/Anthropic (StubTransport + hand-built ledger rows):

  1. THREAD-SCOPED quote-back dedup (ingest.dedup_threads): a reply/forward that
     re-quotes earlier charts (same conversationId + same content-hash) is marked
     duplicate and dropped from raw_haver; the genuinely-new chart survives; an
     identical hash in an UNRELATED thread is never clobbered.
  2. `approve all` BULK token at BOTH gates (resolution + title), with the
     fully-bound guardrail (never sweeps a parked chart) and the cursor gate (a
     stale approve-all posted before the asks doesn't count). A per-chart reply
     wins over the bulk sweep.
  3. --retitle re-opens a FINISHED chart's title round-trip (tickers untouched) and
     drives it back to APPROVED with the new title.

Run:  python scripts/selftest_workflow.py
"""

import shutil
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import ingest as I        # noqa: E402
import approval as A      # noqa: E402
import ledger as L        # noqa: E402
import resolve as R       # noqa: E402
import teams as TM        # noqa: E402
import run_daily as RD    # noqa: E402

WORK = ROOT / "outputs" / "g8c_workflow"
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True, exist_ok=True)

# HERMETIC: point the learning store at a throwaway dir BEFORE any round-trip runs —
# the resolution bulk-approve path calls _learn_from_row, which would otherwise write
# the test's fake binds (Series A/B → AAA/BBB@DB) into the REAL clarified-knowledge
# store and poison it. (This was the store-pollution bug found on 2026-07-01.)
R.CLARIFIED_DIR = WORK / "clarified-knowledge"
R.CLARIFIED_DIR.mkdir(parents=True, exist_ok=True)

_N = [0]
_F = [0]


def check(name, cond, extra=""):
    _N[0] += 1
    if not cond:
        _F[0] += 1
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  — {extra}" if extra else ""))


# ─────────────────────────── shared builders ───────────────────────────────
def resolved_slot(idx, code, desc):
    return {"idx": idx, "description": desc, "base_descriptor": desc,
            "applied_transform": None, "formula": None, "axis": "shared", "lag": None,
            "candidates": [], "candidate_detail": [], "resolved": code, "codes": [code],
            "freq_resolved": "M", "relevance": 1.0, "relevance_exact": True,
            "status": R.SLOT_RESOLVED, "needs_clarification": False, "reason": "auto"}


def pending_slot(idx, desc):
    return {"idx": idx, "description": desc, "base_descriptor": desc,
            "applied_transform": None, "formula": None, "axis": "shared", "lag": None,
            "candidates": [], "candidate_detail": [], "resolved": None, "codes": [],
            "status": R.SLOT_PENDING, "needs_clarification": False, "reason": "parked"}


def ledger(tag):
    return L.Ledger(str(WORK / f"ledger_{tag}.csv"))


def stub(tag):
    return TM.StubTransport(str(WORK / f"stub_{tag}.json"))


def seed(led, cid, slots, slug="g8c"):
    row = led.upsert(message_id=f"msg_{cid}", chart_index=0, chart_id=cid,
                     release_slug=slug, chart_spec={"axis_mode": "shared", "series": []},
                     series=slots)
    L.sync_row_from_slots(row)
    led.save()
    return row


def confirm(_c):
    return True


def titles_for(row):
    return [{"title": f"Proposed {row['chart_id']}", "subtitle": "sub"},
            {"title": "Alternate title", "subtitle": "alt-sub"}]


# ═══════════════════ 1. thread-scoped quote-back dedup ══════════════════════
print("=== 1. thread-scoped quote-back dedup ===")


def _asset(sha, verdict="raw_haver", src="attachment:img"):
    return I.Asset(email_id="e", subject="for the daily", received="", source=src,
                   name="img", verdict=verdict, reason="", sha1=sha)


def _er(conv, received, assets):
    e = I.EmailResult(id=f"m{received}", subject="for the daily", received=received,
                      sender="n", conversation_id=conv)
    e.assets = list(assets)
    return e


HA, HB, HC = "hashA", "hashB", "hashC"
other = _er("THREAD-2", "2026-05-12T10:54:00Z", [_asset(HA)])           # unrelated, same HA
orig = _er("THREAD-1", "2026-05-12T13:16:00Z", [_asset(HA), _asset(HB)])  # A,B originals
reply = _er("THREAD-1", "2026-05-12T13:19:00Z",
            [_asset(HA), _asset(HB), _asset(HC)])                       # re-quote A,B + new C
n_dup = I.dedup_threads([other, orig, reply])   # ascending received order

check("reply's requoted A,B flagged duplicate (same thread + hash)",
      reply.assets[0].duplicate and reply.assets[1].duplicate)
check("reply's NEW chart C survives (unique hash)",
      not reply.assets[2].duplicate)
check("reply.raw_haver keeps ONLY the new chart C",
      [a.sha1 for a in reply.raw_haver] == [HC])
check("originals A,B are NOT duplicates (first in their thread)",
      not orig.assets[0].duplicate and not orig.assets[1].duplicate)
check("unrelated thread with identical hash NOT clobbered (thread-scoped)",
      not other.assets[0].duplicate)
check("dedup counted exactly the 2 quote-backs", n_dup == 2, f"n={n_dup}")

# same-message twins don't self-mark (only EARLIER-message quote-backs count)
twins = _er("THREAD-3", "2026-05-12T09:00:00Z", [_asset(HA), _asset(HA)])
I.dedup_threads([twins])
check("two identical images in the SAME message are not self-deduped",
      not twins.assets[0].duplicate and not twins.assets[1].duplicate)


# ═══════════════════ 2a. is_approve_all token ══════════════════════════════
print("\n=== 2a. approve-all token parsing ===")
check("`approve all` is the bulk token", TM.is_approve_all("approve all"))
check("`approve *` is the bulk token", TM.is_approve_all("approve *"))
check("`approve-all` is the bulk token", TM.is_approve_all("approve-all"))
check("case/space tolerant", TM.is_approve_all("  Approve   All "))
check("a chart-tagged `[id] approve` is NOT global", not TM.is_approve_all("[c1] approve"))
check("bare `approve` is NOT the bulk token", not TM.is_approve_all("approve"))
check("`approve c1` is NOT the bulk token", not TM.is_approve_all("approve c1"))


# ═══════════════════ 2b. bulk approve at the RESOLUTION gate ════════════════
print("\n=== 2b. `approve all` at the resolution gate (+ guardrail, cursor gate) ===")
led, tp = ledger("res"), stub("res")
rA = seed(led, "cleanA", [resolved_slot(0, "AAA@DB", "Series A")])
rB = seed(led, "cleanB", [resolved_slot(0, "BBB@DB", "Series B")])
rP = seed(led, "parkedP", [resolved_slot(0, "CCC@DB", "C1"), pending_slot(1, "C2 parked")])
check("parked chart sits at AWAITING_TICKER (a slot pending)",
      rP["status"] == L.AWAITING_TICKER, rP["status"])

A.run_resolution_roundtrip(led, tp, confirm_ticker=confirm)   # pass1: post summaries
check("both clean charts posted to the resolution gate",
      rA["status"] == L.AWAITING_RESOLUTION and rB["status"] == L.AWAITING_RESOLUTION)

tp.queue_reply("global-bucket", "approve all")               # one untagged global token
A.run_resolution_roundtrip(led, tp, confirm_ticker=confirm)   # pass2: sweep
check("approve all → cleanA ratified", rA["status"] == L.RESOLUTION_APPROVED, rA["status"])
check("approve all → cleanB ratified", rB["status"] == L.RESOLUTION_APPROVED, rB["status"])
check("GUARDRAIL: parked chart NOT swept by approve all",
      rP["status"] == L.AWAITING_TICKER, rP["status"])

# cursor gate: a STALE approve-all posted BEFORE the asks must not count
led2, tp2 = ledger("stale"), stub("stale")
tp2.queue_reply("global-bucket", "approve all")               # stale (posted first)
rS = seed(led2, "staleC", [resolved_slot(0, "DDD@DB", "Series D")])
A.run_resolution_roundtrip(led2, tp2, confirm_ticker=confirm)  # posts ask AFTER the token
A.run_resolution_roundtrip(led2, tp2, confirm_ticker=confirm)  # harvest
check("cursor gate: stale approve-all (pre-ask) does NOT ratify",
      rS["status"] == L.AWAITING_RESOLUTION, rS["status"])


# ═══════════════════ 2c. bulk approve at the TITLE gate ═════════════════════
print("\n=== 2c. `approve all` at the title gate (proposed option; per-chart wins) ===")
led, tp = ledger("title"), stub("title")
t1 = seed(led, "titA", [resolved_slot(0, "AAA@DB", "S")])
t2 = seed(led, "titB", [resolved_slot(0, "BBB@DB", "S")])
for r in (t1, t2):
    led.set_status(r, L.RESOLUTION_APPROVED)
led.save()

A.run_title_roundtrip(led, tp, titles_for, from_status=L.RESOLUTION_APPROVED,
                      gate_status=L.AWAITING_TITLE, parser=TM.parse_title_reply)  # post
check("both charts posted to the title gate",
      t1["status"] == L.AWAITING_TITLE and t2["status"] == L.AWAITING_TITLE)

# per-chart reply on titB (pick alternate) must WIN over the bulk sweep
tp.queue_reply(A.chart_thread("g8c", "titB"), "[titB] 2")
tp.queue_reply("global-bucket", "approve all")
A.run_title_roundtrip(led, tp, titles_for, from_status=L.RESOLUTION_APPROVED,
                      gate_status=L.AWAITING_TITLE, parser=TM.parse_title_reply)  # harvest
check("approve all → titA accepts PROPOSED (option 1)",
      t1["status"] == L.APPROVED and t1["chosen_title"] == "Proposed titA",
      f"{t1['status']}/{t1.get('chosen_title')}")
check("per-chart reply WINS: titB took the ALTERNATE, not the proposed",
      t2["status"] == L.APPROVED and t2["chosen_title"] == "Alternate title",
      f"{t2['status']}/{t2.get('chosen_title')}")


# ═══════════════════ 2d. `none` at the title gate → title-less ═════════════
print("\n=== 2d. `[id] none` in the title round-trip → title-less (transform kept) ===")
# parse_title_reply recognizes none / no-title as a title-less choice
for tok in ("none", "no-title", "[nid] none"):
    ch = TM.parse_title_reply([tok], [{"title": "Proposed", "subtitle": "sub"}])
    check(f"parse_title_reply({tok!r}) → title-less choice",
          ch is not None and ch.get("no_title") and ch["title"] == "",
          repr(ch))
# a bare custom title is NOT swallowed by the none branch
ch = TM.parse_title_reply(["My Custom Title"], [])
check("custom free-text title still works (not treated as none)",
      ch is not None and ch["title"] == "My Custom Title" and not ch.get("no_title"),
      repr(ch))

led, tp = ledger("none"), stub("none")
n1 = seed(led, "noneA", [resolved_slot(0, "AAA@DB", "S")])   # will pick `none`
n2 = seed(led, "titC", [resolved_slot(0, "BBB@DB", "S")])    # will pick a real title
for r in (n1, n2):
    led.set_status(r, L.RESOLUTION_APPROVED)
led.save()
A.run_title_roundtrip(led, tp, titles_for, from_status=L.RESOLUTION_APPROVED,
                      gate_status=L.AWAITING_TITLE, parser=TM.parse_title_reply)  # post
tp.queue_reply(A.chart_thread("g8c", "noneA"), "[noneA] none")
tp.queue_reply(A.chart_thread("g8c", "titC"), "[titC] title=Real Title")
A.run_title_roundtrip(led, tp, titles_for, from_status=L.RESOLUTION_APPROVED,
                      gate_status=L.AWAITING_TITLE, parser=TM.parse_title_reply)  # harvest
check("`none` → APPROVED, title-less (chosen_title empty + no_title flag set)",
      n1["status"] == L.APPROVED and n1.get("chosen_title") == ""
      and n1.get("no_title") == "1", f"{n1['status']}/{n1.get('no_title')!r}")
check("a real title on a sibling clears no_title (titled render)",
      n2["status"] == L.APPROVED and n2.get("chosen_title") == "Real Title"
      and not n2.get("no_title"), f"{n2['status']}/{n2.get('no_title')!r}")

# empty proposals (no commentary to draft from) must not break the ask or `none`
led3, tp3 = ledger("nc"), stub("nc")
nc = seed(led3, "ncA", [resolved_slot(0, "CCC@DB", "S")])
led3.set_status(nc, L.RESOLUTION_APPROVED)
led3.save()
A.run_title_roundtrip(led3, tp3, lambda r: [], from_status=L.RESOLUTION_APPROVED,
                      gate_status=L.AWAITING_TITLE, parser=TM.parse_title_reply)  # post
check("no-commentary chart still posts a title ask (empty proposals)",
      nc["status"] == L.AWAITING_TITLE, nc["status"])
tp3.queue_reply(A.chart_thread("g8c", "ncA"), "[ncA] none")
A.run_title_roundtrip(led3, tp3, lambda r: [], from_status=L.RESOLUTION_APPROVED,
                      gate_status=L.AWAITING_TITLE, parser=TM.parse_title_reply)  # harvest
check("no-commentary `none` → APPROVED title-less (no error on empty proposals)",
      nc["status"] == L.APPROVED and nc.get("no_title") == "1",
      f"{nc['status']}/{nc.get('no_title')!r}")


# ═══════════════════ 3. --retitle a finished chart ═════════════════════════
print("\n=== 3. --retitle re-opens a finished chart's title round-trip ===")
DATE = "2099-01-01"
RD.DATA = WORK                       # point the dated-ledger + stub under the temp dir
import os
os.environ["G7_TRANSPORT"] = "stub"  # _transport() → StubTransport (offline)

led = L.Ledger(str(RD._dated_ledger(DATE)))
rowD = led.upsert(message_id="msg_fin", chart_index=0, chart_id="fin_chart",
                  release_slug="ret", chart_spec={"axis_mode": "shared", "series": []},
                  series=[resolved_slot(0, "AAA@DB", "Finished series")])
L.sync_row_from_slots(rowD)
led.set_status(rowD, L.DONE, chosen_title="Old Title", subtitle="old sub",
               output_path="x.png")
led.save()

rc = RD._retitle(DATE, "nope-not-here", do_render=False)
check("retitle unknown chart_id → returns error (1)", rc == 1, f"rc={rc}")

RD._retitle(DATE, "fin_chart", do_render=False)   # pass1: reset + re-fire title ask
led = L.Ledger(str(RD._dated_ledger(DATE)))
rowD = next(r for r in led.rows if r["chart_id"] == "fin_chart")
check("retitle pass1: finished chart reset into the title gate",
      rowD["status"] == L.AWAITING_TITLE, rowD["status"])
check("retitle pass1: tickers untouched (series still resolved)",
      rowD["series"][0]["status"] == R.SLOT_RESOLVED
      and rowD["series"][0]["resolved"] == "AAA@DB")
check("retitle pass1: old title cleared pending the new pick",
      not rowD.get("chosen_title"))

# reply with a new title into the SAME stub the retitle transport uses
stub_path = str(WORK / f"backfill_{DATE}" / "stub.json")
tp = TM.StubTransport(stub_path)
tp.queue_reply(A.chart_thread("ret", "fin_chart"), "[fin_chart] title=Brand New Title")

RD._retitle(DATE, "fin_chart", do_render=False)   # pass2: harvest → APPROVED
led = L.Ledger(str(RD._dated_ledger(DATE)))
rowD = next(r for r in led.rows if r["chart_id"] == "fin_chart")
check("retitle pass2: new title accepted → APPROVED (ready to re-render)",
      rowD["status"] == L.APPROVED and rowD["chosen_title"] == "Brand New Title",
      f"{rowD['status']}/{rowD.get('chosen_title')}")


# ═══════════════ 4. legend labels: store round-trip, override, persist, floor ═
print("\n=== 4. legend labels (Opus\u2192confirm\u2192store; code/expr key; assert floor) ===")
import build_chart as BC     # noqa: E402  (uses the SAME resolve module → temp store)

# 4a canonicalization + save→load round-trip (the "why is it asking again" guard)
check("code_key upper-cases both sides", R.code_key("letpriva@usecon") == "LETPRIVA@USECON")
check("expr_key strips whitespace + prefixes", R.expr_key("NRS - NRSI7") == "expr:NRS-NRSI7")
R.save_legend(R.code_key("AAA@DB"), "Alpha (SA)")
check("save→load round-trips on the canonical key",
      R.load_legend().get("AAA@DB", {}).get("label") == "Alpha (SA)")
check("legend_key(single) is code_key", R.legend_key(resolved_slot(0, "aaa@db", "AAA")) == "AAA@DB")
_comp = resolved_slot(0, "NRS@SURVEYS", "NRS - NRSI7")
_comp["codes"] = ["NRS@SURVEYS", "NRSI7@USECON"]; _comp["formula"] = "NRS - NRSI7"
check("legend_key(composite) is expr_key", R.legend_key(_comp) == "expr:NRS-NRSI7")

# 4b legend override parse (coexists with code/field corrections)
appr, corr, skp = TM.parse_resolution_reply(["[c] legend2=Retail sales ex gas (SA)"], 3)
check("legendN= → corrections[pos]['legend']",
      corr.get(2, {}).get("legend") == "Retail sales ex gas (SA)" and not appr and not skp)
appr, corr, skp = TM.parse_resolution_reply(["[c] 1=BBB@DB", "[c] legend1=Beta"], 3)
check("legend + code corrections coexist",
      corr.get(1, {}).get("code") == "BBB@DB" and corr.get(1, {}).get("legend") == "Beta")

# 4c summary shows the proposed legend line
_s = resolved_slot(0, "CCC@DB", "desc"); _s["proposed_legend"] = "Prop C"; _s["legend_source"] = "opus"
check("resolution summary shows the proposed legend",
      "legend: Prop C" in TM.format_resolution_summary("cid", [_s], {}))

# 4d full round-trip: label_fn proposes → approve persists → same code renders SILENTLY
led, tp = ledger("legend"), stub("legend")
_calls = []
def _legfn(slot):
    _calls.append(R.legend_key(slot))
    return "AHE: X (SA)"
gA = seed(led, "legA", [resolved_slot(0, "DDD@DB", "avg hourly earnings")])
led.set_status(gA, L.RESOLVED); led.save()
A.run_resolution_roundtrip(led, tp, confirm_ticker=lambda c: True, label_fn=_legfn)   # post/propose
tp.queue_reply(A.chart_thread("g8c", "legA"), "[legA] approve")
A.run_resolution_roundtrip(led, tp, confirm_ticker=lambda c: True, label_fn=_legfn)   # approve→persist
check("approve persisted the legend to the store",
      R.load_legend().get("DDD@DB", {}).get("label") == "AHE: X (SA)")
_n = len(_calls)
gB = seed(led, "legB", [resolved_slot(0, "DDD@DB", "avg hourly earnings")])
led.set_status(gB, L.RESOLVED); led.save()
A.run_resolution_roundtrip(led, tp, confirm_ticker=lambda c: True, label_fn=_legfn)   # store hit
check("store hit → label_fn NOT called again (silent reuse)", len(_calls) == _n)

# 4e _legend_label FLOOR: a raw mnemonic must never reach a legend
_mn = resolved_slot(0, "ZZZ@DB", "ZZZ")           # base_descriptor IS the mnemonic
try:
    BC._legend_label(_mn); _raised = False
except ValueError:
    _raised = True
check("floor: mnemonic-only label w/ no store entry RAISES (no ticker fallback)", _raised)
R.save_legend(R.code_key("ZZZ@DB"), "Zeta index (SA)")
check("floor: store hit renders the confirmed label", BC._legend_label(_mn) == "Zeta index (SA)")
check("legacy: a genuine description renders without a store entry",
      BC._legend_label(resolved_slot(0, "QQQ@DB", "Nonfarm payrolls (SA)")) == "Nonfarm payrolls (SA)")
try:
    BC._legend_label(_comp); _craised = False
except ValueError:
    _craised = True
check("floor: composite mnemonic expr w/ no store entry RAISES", _craised)
R.save_legend(R.expr_key("NRS - NRSI7"), "Retail sales ex gas (SA)")
check("composite: expr-keyed store hit renders", BC._legend_label(_comp) == "Retail sales ex gas (SA)")


# ═══════════════ 5. st_force: verbatim subtitle vs default auto-label append ══
print("\n=== 5. st_force (subtitle-force): explicit verbatim override vs default append ===")
# 5a parse: st_force=true is captured AND stripped (never leaks into the subtitle)
ch = TM.parse_title_reply(
    ["[c] title=Wage growth is cooling subtitle=y/y% chg st_force=true"], [])
check("st_force=true parsed; subtitle is clean (no 'st_force' leak)",
      ch and ch.get("st_force") is True and ch["subtitle"] == "y/y% chg"
      and ch["title"] == "Wage growth is cooling", repr(ch))
ch = TM.parse_title_reply(["[c] title=Foo subtitle=bar"], [])
check("no flag → st_force falsy (default append path)",
      ch and not ch.get("st_force") and ch["subtitle"] == "bar", repr(ch))
ch = TM.parse_title_reply(["[c] subtitle=z st_force=false"], [])
check("st_force=false → falsy (explicit default)", ch and not ch.get("st_force"), repr(ch))

# 5b compose_subtitle: the actual render decision
import build_chart as _BC   # noqa: E402
same = ["% change, year-over-year", "% change, year-over-year"]
sub, mixed = _BC.compose_subtitle("y/y% chg", same, st_force=False)
check("default: shared transform APPENDS to subtitle (tripwire on)",
      sub == "y/y% chg, % change, year-over-year" and not mixed, f"{sub!r}")
sub, mixed = _BC.compose_subtitle("y/y% chg", same, st_force=True)
check("st_force: subtitle VERBATIM, nothing appended", sub == "y/y% chg" and not mixed, f"{sub!r}")
# _1 non-regression: no flag, subtitle silent on z-score → still appends "z-score"
sub, mixed = _BC.compose_subtitle("3m %chg saar of 3mma", ["z-score", "z-score"], st_force=False)
check("_1 non-regression: z-score still appended when no flag",
      sub == "3m %chg saar of 3mma, z-score" and not mixed, f"{sub!r}")
# mixed transforms still route to the LEGEND regardless of st_force (orthogonal tripwire)
sub, mixed = _BC.compose_subtitle("base", ["a-label", "b-label"], st_force=True)
check("mixed transforms → legend even under st_force (subtitle stays base)",
      sub == "base" and mixed, f"{sub!r}/{mixed}")
# st_force persists through the title round-trip onto the row
_led, _tp = ledger("stforce"), stub("stforce")
_r = seed(_led, "sfA", [resolved_slot(0, "AAA@DB", "s")]); _led.set_status(_r, L.RESOLUTION_APPROVED)
_led.save()
A.run_title_roundtrip(_led, _tp, titles_for, from_status=L.RESOLUTION_APPROVED,
                      gate_status=L.AWAITING_TITLE, parser=TM.parse_title_reply)  # post
_tp.queue_reply(A.chart_thread("g8c", "sfA"), "[sfA] title=T subtitle=y/y% st_force=true")
A.run_title_roundtrip(_led, _tp, titles_for, from_status=L.RESOLUTION_APPROVED,
                      gate_status=L.AWAITING_TITLE, parser=TM.parse_title_reply)  # harvest
check("st_force persisted on the row at approve",
      _r["status"] == L.APPROVED and _r.get("st_force") == "1"
      and _r.get("subtitle") == "y/y%", f"{_r.get('st_force')!r}/{_r.get('subtitle')!r}")


# ══════════════════════════════════════════════════════════════════════════════
# 6. phrase_to_haver — the FINITE Haver transform vocabulary maps to G3, with a
#    fail-loud floor for anything novel and safe composition of "<x> of N-mma".
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 6. transform vocabulary (phrase_to_haver → G3) ──")
_VOCAB = [
    ("% Change - Period to Period", "diff%(x,1)", "% change, period-over-period"),
    ("Change - Period to Period",   "diff(x,1)",  "change, period-over-period"),
    ("% Change - Year to Year",     "yryr%(x)",   "% change, year-over-year"),
    ("Change - Year to Year",       "yryr(x)",    "change, year-over-year"),
    ("% Change - Annual Rate",      "difa%(x,1)", "annualized % change"),
    ("6-month annualized % change", "difa%(x,6)", "6-month annualized % change"),
    ("3-month moving average",      "movv(x,3)",  "3-month moving average"),
    ("6-month moving sum",          "movt(x,6)",  "6-month moving sum"),
    ("z-score",                     "zs(x)",      "z-score"),
    ("Level",                       None,         None),
]
for _ph, _f, _lbl in _VOCAB:
    _got = _BC.phrase_to_haver(_ph, "x")
    _gotlbl = _BC.transform_label(_got, "month") if _got else None
    check(f"vocab: {_ph!r} → {_f}", _got == _f and _gotlbl == _lbl,
          f"formula={_got!r} label={_gotlbl!r}")
# composition never drops the inner moving average
_comp = _BC.phrase_to_haver("% Change - Year to Year of 3-month moving average", "letpriva")
check("compound: yoy% of 3mma composes (MA not dropped)",
      _comp == "yryr%(movv(letpriva,3))", f"{_comp!r}")
# fail-loud floor: genuinely novel phrase RAISES, never silent-level
_raised = False
try:
    _BC.phrase_to_haver("some novel wiggle", "x")
except ValueError:
    _raised = True
check("floor: novel phrase RAISES (never silent-level)", _raised)


# ══════════════════════════════════════════════════════════════════════════════
# 7. human-supplied ticker DROPS the read's stale formula (the 2026-07-09 legal-
#    services snag: a `yryr%(<description>)` read-formula rode into render → ParseError).
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 7. human-ticker rebind drops the stale read-formula ──")
# (a) bogus formula wrapping a DESCRIPTION + applied_transform present → formula dropped,
#     transform preserved (render rebuilds yryr%(mnem) from applied_transform).
_slot = {"formula": "yryr%(CPI-U: Legal Services (NSA, Dec-86=100))",
         "applied_transform": "% Change - Year to Year", "base_descriptor": "CPI-U: Legal Services"}
_u = R.rebind_human_ticker(_slot, "R5411@PPIR")
check("human-ticker: description-formula dropped, applied_transform kept",
      _u["formula"] is None and _u["resolved"] == "R5411@PPIR" and _u["codes"] == ["R5411@PPIR"],
      f"{_u.get('formula')!r}")
# the repaired slot now renders through phrase_to_haver on the supplied mnemonic
_merged = dict(_slot); _merged.update(_u)
_f = _BC._applied_formula(_merged, _merged["resolved"].split("@")[0])
check("human-ticker: renders as yryr%(mnem), parses clean", _f == "yryr%(R5411)", f"{_f!r}")
# (b) single-mnemonic PARSEABLE read-formula, NO applied_transform → rewrite the token
#     (transform must not be silently dropped).
_slot2 = {"formula": "yryr%(XYZ)", "applied_transform": None}
_u2 = R.rebind_human_ticker(_slot2, "XYZ@ABC")
check("human-ticker: parseable formula, no applied_transform → mnemonic rewritten",
      _u2["formula"] == "yryr%(XYZ)", f"{_u2.get('formula')!r}")  # XYZ already the mnem
_slot2b = {"formula": "yryr%(OLD)", "applied_transform": None}
_u2b = R.rebind_human_ticker(_slot2b, "NEW@DB")
check("human-ticker: mnemonic swapped to the supplied one",
      _u2b["formula"] == "yryr%(NEW)", f"{_u2b.get('formula')!r}")
# (c) no formula at all → plain bind, nothing to strip
_u3 = R.rebind_human_ticker({"formula": None, "applied_transform": None}, "AAA@DB")
check("human-ticker: no formula → plain single-ticker bind",
      _u3["formula"] is None if "formula" in _u3 else True, f"{_u3}")


# ══════════════════════════════════════════════════════════════════════════════
# 8. --relegend re-opens a FINISHED chart's resolution summary for LEGEND edits only
#    (title/tickers preserved; confirmed label persists; chart returns to APPROVED).
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 8. --relegend re-opens a finished chart for legend edits ──")
_ld = L.Ledger(str(RD._dated_ledger(DATE)))                 # DATE/RD.DATA set in section 3
_lg = _ld.upsert(message_id="msg_leg", chart_index=0, chart_id="leg_chart",
                 release_slug="leg", chart_spec={"axis_mode": "shared", "series": []},
                 series=[resolved_slot(0, "AAA@DB", "Finished legend series")])
L.sync_row_from_slots(_lg)
_ld.set_status(_lg, L.DONE, chosen_title="Keep This Title", subtitle="keep sub",
               output_path="x.png")
_ld.save()
R.save_legend(R.code_key("AAA@DB"), "Old Legend")          # warm store (deterministic)

check("relegend unknown chart_id → error (1)",
      RD._relegend(DATE, "nope", do_render=False) == 1)

RD._relegend(DATE, "leg_chart", do_render=False)           # pass1: reset → summary re-posts
_ld = L.Ledger(str(RD._dated_ledger(DATE)))
_lg = next(r for r in _ld.rows if r["chart_id"] == "leg_chart")
check("relegend pass1: finished chart back in the resolution gate",
      _lg["status"] == L.AWAITING_RESOLUTION, _lg["status"])
check("relegend pass1: title + tickers preserved",
      _lg.get("chosen_title") == "Keep This Title"
      and _lg["series"][0]["resolved"] == "AAA@DB", _lg.get("chosen_title"))

_tpL = TM.StubTransport(str(WORK / f"backfill_{DATE}" / "stub.json"))
_thL = A.chart_thread("leg", "leg_chart")
_tpL.queue_reply(_thL, "[leg_chart] legend1=Brand New Legend")   # pass2: the override
RD._relegend(DATE, "leg_chart", do_render=False)
_ld = L.Ledger(str(RD._dated_ledger(DATE)))
_lg = next(r for r in _ld.rows if r["chart_id"] == "leg_chart")
check("relegend pass2: legend override applied (human), still awaiting approve",
      _lg["series"][0].get("proposed_legend") == "Brand New Legend"
      and _lg["series"][0].get("legend_source") == "human", _lg["status"])

_tpL = TM.StubTransport(str(WORK / f"backfill_{DATE}" / "stub.json"))  # reload latest cursor
_tpL.queue_reply(_thL, "[leg_chart] approve")                    # pass3: ratify
RD._relegend(DATE, "leg_chart", do_render=False)
_ld = L.Ledger(str(RD._dated_ledger(DATE)))
_lg = next(r for r in _ld.rows if r["chart_id"] == "leg_chart")
check("relegend pass3: ratified → APPROVED (title still intact, ready to re-render)",
      _lg["status"] == L.APPROVED and _lg.get("chosen_title") == "Keep This Title",
      f"{_lg['status']}/{_lg.get('chosen_title')}")
check("relegend: confirmed label persisted to the code-keyed store",
      R.load_legend().get(R.code_key("AAA@DB"), {}).get("label") == "Brand New Legend",
      R.load_legend().get(R.code_key("AAA@DB")))


# ══════════════════════════════════════════════════════════════════════════════
print("\n── 9. render window END is composite-aware (no phantom-trim on vintage skew) ──")
import io                                                        # noqa: E402
import contextlib                                                # noqa: E402
import build_chart as BC                                         # noqa: E402
import transforms as T                                          # noqa: E402
import pandas as _pd                                            # noqa: E402


def _sd(last, code):
    idx = _pd.date_range("2024-01-31", last, freq="ME")
    return T.SeriesData(values=_pd.Series([1.0] * len(idx), index=idx), freq="M",
                        code=code)


# series2 = a difference whose operands END ON DIFFERENT DATES (the 2026-07-14 bug:
# nfib7 had June, nfib6's stale cache ended May) + a shorter single series1.
_pp = [(None, "zs(yryr%(LXNFA))", "s1", "L"),
       (None, "zs((NFIB7 - NFIB6))", "s2", "L")]
_sm_skew = {"LXNFA": _sd("2026-03-31", "LXNFA@USECON"),
            "NFIB7": _sd("2026-06-30", "NFIB7@SURVEYS"),
            "NFIB6": _sd("2026-05-31", "NFIB6@SURVEYS")}
_err = io.StringIO()
with contextlib.redirect_stderr(_err):
    _end = BC._window_end(_pp, _sm_skew)
# composite ends at the EARLIER operand (May), NOT the raw max (NFIB7's June) — so the
# grey line reaches the right edge instead of dangling short of a phantom June axis.
check("window end = composite overlap (May), not the longest raw operand (June)",
      str(_end.date()) == "2026-05-31", str(_end.date()))
check("cross-operand vintage skew is surfaced LOUD on stderr",
      "vintage-skew" in _err.getvalue(), _err.getvalue()[:80])

# healthy case: both operands fresh (same June end) → composite reaches June; the
# longer of {composite June, single Mar} wins so nothing is needlessly clipped.
_sm_ok = dict(_sm_skew, NFIB6=_sd("2026-06-30", "NFIB6@SURVEYS"))
_err2 = io.StringIO()
with contextlib.redirect_stderr(_err2):
    _end_ok = BC._window_end(_pp, _sm_ok)
check("fresh operands → window end = June (composite not clipped)",
      str(_end_ok.date()) == "2026-06-30", str(_end_ok.date()))
check("no skew warning when operands agree",
      "vintage-skew" not in _err2.getvalue())


# ══════════════════════════════════════════════════════════════════════════════
print("\n── 10. legend: multi-override parse, axis-side tag, multi-row wrap ──")
import render as RR                                              # noqa: E402

# (a) parser: SEVERAL `legendN=` on ONE line each stop at the next marker (the
# 2026-07-15 greedy defect that welded slot-0's label to "… legend2=MBA …").
_appr, _corr, _skip = TM.parse_resolution_reply(
    ["[c] legend1=MBA: 30Y FRM Contract Rate (%, y/y change) "
     "legend2=MBA Purchase Loan Apps Index (NSA, y/y %chg)"], n_slots=2)
check("multi-legend line: slot1 label stops before legend2=",
      _corr.get(1, {}).get("legend") == "MBA: 30Y FRM Contract Rate (%, y/y change)",
      _corr.get(1, {}).get("legend"))
check("multi-legend line: slot2 label parsed independently",
      _corr.get(2, {}).get("legend") == "MBA Purchase Loan Apps Index (NSA, y/y %chg)",
      _corr.get(2, {}).get("legend"))

# (b) axis-side tag: inside a trailing bracket if present, else a fresh one; idempotent.
check("axis tag inside an existing trailing bracket (RHS)",
      RR._axis_side_label("MBA Purchase Loan Apps Index (NSA, y/y %chg)", "R")
      == "MBA Purchase Loan Apps Index (NSA, y/y %chg, RHS)")
check("axis tag as a fresh bracket when none present (LHS)",
      RR._axis_side_label("ISM Mfg: PMI Composite Index", "L")
      == "ISM Mfg: PMI Composite Index (LHS)")
check("axis tag is idempotent (no double-tag on re-render)",
      RR._axis_side_label("ISM Mfg: PMI Composite Index (LHS)", "L")
      == "ISM Mfg: PMI Composite Index (LHS)")

# (c) layout: long labels collapse to one column (stack vertically); short ones pack wider.
_long = ["MBA: 30Y FRM Contract Rate (%, y/y change, LHS)",
         "MBA Purchase Loan Apps Index (NSA, y/y %chg, RHS)"]
_ncol_long, _nrows_long = RR._legend_layout(_long, plot_w_in=6.6, cap=2)
check("long dual labels → 1 column, stacked vertically",
      (_ncol_long, _nrows_long) == (1, 2), (_ncol_long, _nrows_long))
_short = ["CPI (SA)", "PCE (SA)"]
_ncol_short, _ = RR._legend_layout(_short, plot_w_in=6.6, cap=2)
check("short labels → pack into multiple columns", _ncol_short == 2, _ncol_short)

# (d) confirmed legend suppresses the auto transform-append (no "(NSA, y/y %chg), % chg…").
R.save_legend(R.code_key("ZZZ@DB"), "Widget Index (NSA, y/y %chg)")
_slot_conf = resolved_slot(0, "ZZZ@DB", "Widget raw")
check("a store-confirmed legend is detected as authoritative",
      BC._has_confirmed_legend(_slot_conf) is True)
check("a slot with no store label is NOT treated as confirmed",
      BC._has_confirmed_legend(resolved_slot(0, "QQQ@DB", "Other raw")) is False)


# ══════════════════════════════════════════════════════════════════════════════
print("\n── 11. classify: a SPARSE Haver chart (thin blue lines) is not skipped ──")
import numpy as _np                                             # noqa: E402
from PIL import Image as _Img                                   # noqa: E402
import classify as _CL                                          # noqa: E402


def _pc(arr):
    return _CL.preclassify(_Img.fromarray(arr, "RGB"))[0]


# (a) the 2026-07-20 defect: a genuine Haver chart with thin navy lines + whitespace has
# colored_frac BELOW the "no plotted series" floor, but ~100% of it is blue → must be
# RESCUED to raw_haver, not skipped. (A single 1px navy line ≈ 0.003 < T_COLORED_MIN.)
_sparse = _np.full((260, 300, 3), 255, _np.uint8)
for _y in range(230):
    _sparse[_y, 60 + (_y % 40)] = (20, 40, 120)
check("sub-floor blue-dominant chart → raw_haver (not 'no plotted series')",
      _pc(_sparse) == "raw_haver", _pc(_sparse))

# (b) a colorless text/table screenshot (no series of any palette) → still skip.
_text = _np.full((260, 300, 3), 255, _np.uint8)
_text[::6, ::4] = (0, 0, 0)
check("colorless text/table → skip (floor still guards)", _pc(_text) == "skip", _pc(_text))

# (c) a stray sub-threshold blue speck (not a series) → not rescued → skip.
_speck = _np.full((260, 300, 3), 255, _np.uint8)
_speck[:8, 10] = (20, 40, 120)
check("tiny blue speck below T_BLUE_MIN → skip (no false rescue)",
      _pc(_speck) == "skip", _pc(_speck))

# (d) a finished multi-color chart (orange series present) → skip WINS over its blue line.
_multi = _np.full((260, 300, 3), 255, _np.uint8)
for _y in range(240):
    _multi[_y, 40 + (_y % 30)] = (20, 40, 120)      # a blue line too
    _multi[_y, 150 + (_y % 30)] = (240, 140, 20)    # …but orange dominates the verdict
check("orange series present → skip even with a co-present blue line",
      _pc(_multi) == "skip", _pc(_multi))


# ══════════════════════════════════════════════════════════════════════════════
print("\n── 12. haver_search: Neon cold-start DB timeout is retried, not fatal ──")
import types as _types                                          # noqa: E402
import time as _tt                                              # noqa: E402
import haver_search as _HS                                      # noqa: E402
import psycopg as _pg                                           # noqa: E402

# inject a fake `db` module so _run_query's `import db` resolves without a live Neon.
_fake_db = _types.ModuleType("db")
_st = {"n": 0}


def _flaky(sql, params=None):
    _st["n"] += 1
    if _st["n"] < 3:                       # first two calls "time out" (cold cache)
        raise _pg.errors.QueryCanceled("canceling statement due to statement timeout")
    return [{"ok": 1}]


_fake_db.run_query = _flaky
sys.modules["db"] = _fake_db
_HS._DB_RETRIES = 4
_orig_sleep = _tt.sleep
_tt.sleep = lambda *_a, **_k: None         # don't actually wait in the test
try:
    _res = _HS._run_query("SELECT 1", ())
    check("cold-start statement-timeout retried then succeeded",
          _res == [{"ok": 1}] and _st["n"] == 3, (_res, _st["n"]))

    _st["n"] = 0

    def _hard(sql, params=None):
        _st["n"] += 1
        raise ValueError("bad sql")        # a real bug, not a warm-up blip

    _fake_db.run_query = _hard
    _raised = False
    try:
        _HS._run_query("x", ())
    except ValueError:
        _raised = True
    check("non-transient DB error raised immediately (no retry, fail-loud)",
          _raised and _st["n"] == 1, _st["n"])
finally:
    _tt.sleep = _orig_sleep
    sys.modules.pop("db", None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n── 13. transform: Haver's ABBREVIATED '-ann' stem annualizes (difa%, not diff%) ──")
# The 2026-07-30 silent-wrong: DLX prints "2-qtr %Change-ann" / "3-month %Change-ann"; the
# truncated "ann" matched none of the spelled-out annual keywords, so the phrase fell to the
# plain diff% branch and plotted a NON-annualized change that looked plausible (GDP 2-qtr
# rendered 3.35 where Haver's own on-chart label read 6.81).
for _ph, _want in (("2-qtr %Change-ann", "difa%(X,2)"),
                   ("3-month %Change-ann", "difa%(X,3)"),
                   ("6-month %Change-ann", "difa%(X,6)"),
                   ("1-qtr %Change-ann", "difa%(X,1)"),
                   ("%Chg-Ann", "difa%(X,1)"),
                   ("2-qtr %Change-Annualized", "difa%(X,2)"),
                   ("% Change - Annual Rate", "difa%(X,1)")):
    _got = BC.phrase_to_haver(_ph, "X")
    check(f"{_ph!r} → {_want}", _got == _want, _got)

# the abbreviation must NOT bleed into the non-annualized families (no false positives).
for _ph, _want in (("% Change - Period to Period", "diff%(X,1)"),
                   ("% Change - Year to Year", "yryr%(X)"),
                   ("3-month moving average", "movv(X,3)")):
    _got = BC.phrase_to_haver(_ph, "X")
    check(f"no over-match: {_ph!r} → {_want}", _got == _want, _got)

# the MATH itself: difa% is ((x_t/x_{t-n})**(npy/n) - 1)*100 — the user-stated formula.
import pandas as _pd                                            # noqa: E402
import transforms as _T                                         # noqa: E402
_qidx = _pd.date_range("2020-03-31", periods=12, freq="QE")
_qs = _pd.Series([100.0 * (1.01 ** i) for i in range(12)], index=_qidx)
_sm = {"X": _T.SeriesData(values=_qs, freq="Q")}
_w = (_qidx[0], _qidx[-1])
_got2q = _T.transform("difa%(X,2)", _sm, _w, "Q").dropna()
_exp2q = ((_qs / _qs.shift(2)) ** 2 - 1.0) * 100.0        # npy/n = 4/2 = 2
check("difa%(X,2) on QUARTERLY == ((x_t/x_t-2)^2 - 1)*100",
      bool(_np.allclose(_got2q.values, _exp2q.dropna().values)), round(_got2q.iloc[-1], 4))
_midx = _pd.date_range("2024-01-31", periods=18, freq="ME")
_ms = _pd.Series([100.0 * (1.002 ** i) for i in range(18)], index=_midx)
_smm = {"X": _T.SeriesData(values=_ms, freq="M")}
_got3m = _T.transform("difa%(X,3)", _smm, (_midx[0], _midx[-1]), "M").dropna()
_exp3m = ((_ms / _ms.shift(3)) ** 4 - 1.0) * 100.0        # npy/n = 12/3 = 4
check("difa%(X,3) on MONTHLY == ((x_t/x_t-3)^4 - 1)*100",
      bool(_np.allclose(_got3m.values, _exp3m.dropna().values)), round(_got3m.iloc[-1], 4))


# ══════════════════════════════════════════════════════════════════════════════
print("\n── 14. legend key is TRANSFORM-aware (one ticker, two transforms) ──")
# The 2026-07-30 defect: the store key was the bare CODE@DB while the stored label TEXT
# carried a transform qualifier — so a chart plotting ONE ticker under TWO transforms wrote
# both labels to one key (last write won) and then BOTH lines rendered the loser's label.
_yoy = resolved_slot(0, "FSDH@DB", "Real Final Sales")
_yoy["applied_transform"] = "% Change - Year to Year"
_saar = resolved_slot(1, "FSDH@DB", "Real Final Sales")
_saar["applied_transform"] = "% Change - Annual Rate"
check("same ticker + DIFFERENT transform → DIFFERENT legend keys",
      R.legend_key(_yoy) != R.legend_key(_saar), (R.legend_key(_yoy), R.legend_key(_saar)))
_lvl = resolved_slot(0, "FSDH@DB", "Real Final Sales")
check("a LEVEL slot keeps the legacy bare CODE@DB key (warmed stores keep hitting)",
      R.legend_key(_lvl) == R.code_key("FSDH@DB"), R.legend_key(_lvl))
# two spellings of the SAME math share one key (canonicalized on the G3 formula).
_alt = resolved_slot(0, "FSDH@DB", "Real Final Sales")
_alt["applied_transform"] = "1-qtr %Change-ann"
check("two spellings of the same math → ONE key",
      R.legend_key(_alt) == R.legend_key(_saar), R.legend_key(_alt))

R.save_legend(R.legend_key(_yoy), "Real Final Sales (y/y %chg)")
R.save_legend(R.legend_key(_saar), "Real Final Sales (1-qtr %chg saar)")
check("each transform resolves its OWN label",
      BC._legend_label(_yoy) == "Real Final Sales (y/y %chg)"
      and BC._legend_label(_saar) == "Real Final Sales (1-qtr %chg saar)",
      (BC._legend_label(_yoy), BC._legend_label(_saar)))

# back-compat: a PRE-EXISTING blind-key entry still hits when the ticker is unambiguous…
R.save_legend(R.code_key("LEGACY@DB"), "Legacy Series (SA)")
_leg = resolved_slot(0, "LEGACY@DB", "Legacy raw")
_leg["applied_transform"] = "% Change - Year to Year"
check("legacy blind-key entry still hits (no re-ask storm)",
      BC._legend_label(_leg) == "Legacy Series (SA)", BC._legend_label(_leg))
# …but NOT when that same base key appears twice on one chart (the ambiguous case).
check("blind-key fallback is refused when the ticker repeats on the chart",
      R.lookup_legend(R.load_legend(), _leg, allow_base=False) is None)

# a slot's OWN ratified label outranks any store entry (per-slot beats cross-chart cache).
_own = resolved_slot(0, "FSDH@DB", "Real Final Sales")
_own["applied_transform"] = "% Change - Annual Rate"
_own["proposed_legend"] = "Hand-authored Label (1-qtr %chg saar)"
check("slot's own ratified legend outranks the store",
      BC._legend_label(_own) == "Hand-authored Label (1-qtr %chg saar)", BC._legend_label(_own))

# the FLOOR: two identical legends on one chart is unreadable → fail loud.
_dup = False
try:
    BC._assert_distinct_legends(["Same Label (saar)", "Other", "same label (saar)"])
except ValueError:
    _dup = True
check("duplicate legend labels on one chart → RAISE (never ship an ambiguous chart)", _dup)
check("distinct labels pass the guard",
      BC._assert_distinct_legends(["A (y/y)", "A (saar)"]) is None)


# ══════════════════════════════════════════════════════════════════════════════
print("\n── 15. bar / stacked-bar plot kinds (previously an UNREPRESENTED dimension) ──")
# Before 2026-07-30 nothing in the pipeline could express "this series is a bar": the read
# never reported it, ChartSpec had no field, and render hard-coded ax.plot — so a Haver
# stacked-contribution chart was silently redrawn as lines.
import chartspec as _CS                                         # noqa: E402
check("ChartSpec.SeriesSpec carries plot_kind (default line)",
      _CS.SeriesSpec().plot_kind == "line")
for _raw, _want in (("stacked_bar", "stacked_bar"), ("stacked bar", "stacked_bar"),
                    ("Stacked-Bars", "stacked_bar"), ("bar", "bar"), ("bars", "bar"),
                    ("column", "bar"), ("line", "line"), (None, "line"), ("wat", "line")):
    check(f"read plot_kind {_raw!r} → {_want}", _CS._plot_kind(_raw) == _want,
          _CS._plot_kind(_raw))
check("the read PROMPT asks for plot_kind (so future charts detect bars)",
      "plot_kind" in _CS._PROMPT and "stacked_bar" in _CS._PROMPT)
check("a ledger slot can override the plot kind",
      BC._plot_kind_of({"plot_kind": "stacked_bar"}) == "stacked_bar"
      and BC._plot_kind_of({}) == "line")

# MIXED-SIGN stacking (the econ-templates rule): positives stack up from the running
# POSITIVE total, negatives hang down from the running NEGATIVE total. Naive single-bottom
# stacking would place +2 on top of -1 (i.e. starting at -1) — a silently-wrong chart.
_b0 = RR._stacked_bottoms([], [1.0, -1.0, 2.0])
check("first component always sits on zero", _b0 == [0.0, 0.0, 0.0], _b0)
_b1 = RR._stacked_bottoms([[1.0, -1.0, 2.0]], [2.0, -2.0, -1.0])
check("mixed-sign bottoms: + stacks on +, − hangs below −",
      _b1 == [1.0, -1.0, 0.0], _b1)
# the stack ENVELOPE identity: pos_top + neg_bot == column sum (the template's assertion).
_comp = [[1.0, -1.0, 2.0], [2.0, -2.0, -1.0], [-0.5, 3.0, 0.5]]
for _j in range(3):
    _col = [c[_j] for c in _comp]
    _pos = sum(v for v in _col if v > 0)
    _neg = sum(v for v in _col if v < 0)
    check(f"stack envelope period {_j}: pos_top+neg_bot == sum",
          abs((_pos + _neg) - sum(_col)) < 1e-9)
# bar width scales with the series' own period (quarterly wide, monthly narrow).
_wq = RR._bar_width_days(_pd.date_range("2020-03-31", periods=8, freq="QE"))
_wm = RR._bar_width_days(_pd.date_range("2020-01-31", periods=8, freq="ME"))
check("bar width derives from period spacing (Q wider than M)", _wq > _wm, (_wq, _wm))


# ══════════════════════════════════════════════════════════════════════════════
print("\n── 16. display x-start never opens on DEAD SPACE ──")
# 2026-07-30 `_0`: the read said 1948 but zs(yryr(YPSVR)) only begins 1960 — a dead decade.
# The DATA window keeps the read start (transforms need the run-up); only the xlim moves in.
_late = RR.PlotSeries(label="L", series=_pd.Series(
    [1.0, 2.0], index=_pd.to_datetime(["1960-03-31", "1990-03-31"])))
check("read start EARLIER than data → xlim moves to first plotted point",
      BC._x_start(_pd.Timestamp("1948-01-31"), [_late]) == _pd.Timestamp("1960-03-31"))
_early = RR.PlotSeries(label="E", series=_pd.Series(
    [1.0, 2.0], index=_pd.to_datetime(["1948-03-31", "1990-03-31"])))
check("read start LATER than data → read start is honored (a deliberate zoom)",
      BC._x_start(_pd.Timestamp("1970-01-31"), [_early]) == _pd.Timestamp("1970-01-31"))
check("MIN across series so the LONGEST line still shows fully",
      BC._x_start(_pd.Timestamp("1900-01-31"), [_late, _early]) == _pd.Timestamp("1948-03-31"))
_empty = RR.PlotSeries(label="N", series=_pd.Series(dtype=float))
check("no plotted points → read start unchanged (no crash)",
      BC._x_start(_pd.Timestamp("1948-01-31"), [_empty]) == _pd.Timestamp("1948-01-31"))


# ══════════════════════════════════════════════════════════════════════════════
print("\n── 17. per-chart x-axis tick interval + label format ──")
import matplotlib.dates as _md                                  # noqa: E402
import matplotlib.ticker as _mt                                 # noqa: E402


def _axis_for(lo, hi, **kw):
    _f, _a = RR.plt.subplots()
    _a.set_xlim(_pd.Timestamp(lo), _pd.Timestamp(hi))
    RR._style_time_axis(_a, **kw)
    _labs = [_a.xaxis.get_major_formatter()(t) for t in _a.get_xticks()]
    RR.plt.close(_f)
    return _a, _labs


# (a) tick_years forces the major interval (the 2026-07-30 `_0` ask: 5y, not the auto decade).
_ax5, _ = _axis_for("1960-03-31", "2026-06-30", tick_years=5)
check("tick_years=5 → YearLocator(5)",
      isinstance(_ax5.xaxis.get_major_locator(), _md.YearLocator))
_yrs5 = [d.year for d in _md.num2date(_ax5.get_xticks())]
check("5-year ticks land on multiples of 5",
      all(y % 5 == 0 for y in _yrs5) and len(_yrs5) > 10, _yrs5[:4])
_ax10, _ = _axis_for("1960-03-31", "2026-06-30")
check("default (no override) is still the AutoDateLocator",
      isinstance(_ax10.xaxis.get_major_locator(), _md.AutoDateLocator))

# (b) label formats.
_, _lab_y = _axis_for("2023-03-31", "2026-06-30", label_fmt="year")
check("label_fmt='year' → bare years on a SHORT span (overrides the Mmm-YY default)",
      all(_l.isdigit() and len(_l) == 4 for _l in _lab_y if _l), _lab_y[:3])
_, _lab_m = _axis_for("2023-03-31", "2026-06-30", label_fmt="month")
check("label_fmt='month' → Mmm-YY", bool(_lab_m) and "-" in _lab_m[1], _lab_m[:3])
_ax_q, _lab_q = _axis_for("2023-02-01", "2026-08-01", label_fmt="quarter")
check("label_fmt='quarter' → Qn-YY", _lab_q[0].startswith("Q") and "-" in _lab_q[0],
      _lab_q[:3])

# (c) THE ALIGNMENT TRAP: a quarterly obs is stamped at the quarter END (Q1 → Mar-31), so
# ticking quarter STARTS would put the Apr-1 tick beside the Mar-31 bar and label that bar
# "Q2" when it is Q1 — a plausible-looking off-by-one-quarter mislabel.
check("quarter ticks are pinned to quarter ENDS (FixedLocator, not month starts)",
      isinstance(_ax_q.xaxis.get_major_locator(), _mt.FixedLocator))
_qe = RR._quarter_ends(_md.date2num(_pd.Timestamp("2023-02-01")),
                       _md.date2num(_pd.Timestamp("2026-08-01")))
check("first quarter tick is Mar-31 (a quarter END, matching the obs stamp)",
      _qe[0] == _pd.Timestamp("2023-03-31"), _qe[0])
check("Mar-31 is labelled Q1 (not Q2)",
      RR._quarter_tick(_md.date2num(_pd.Timestamp("2023-03-31"))) == "Q1-23")
for _d, _w in (("2023-06-30", "Q2-23"), ("2023-09-30", "Q3-23"),
               ("2023-12-31", "Q4-23"), ("2026-06-30", "Q2-26")):
    check(f"{_d} → {_w}", RR._quarter_tick(_md.date2num(_pd.Timestamp(_d))) == _w)
# every tick coincides with a real quarter-end stamp of the plotted series.
_stamps = set(_pd.date_range("2023-03-31", "2026-06-30", freq="QE"))
check("every quarter tick coincides with an actual observation stamp",
      all(_t in _stamps for _t in RR._quarter_ends(
          _md.date2num(_pd.Timestamp("2023-03-31")),
          _md.date2num(_pd.Timestamp("2026-06-30")))))

# (d) thinning on a long span, and a bad format fails loud.
_long_q = RR._quarter_ends(_md.date2num(_pd.Timestamp("1960-01-01")),
                           _md.date2num(_pd.Timestamp("2026-06-30")))
check("a 66-year span thins quarter ticks to <= the cap",
      0 < len(_long_q) <= RR._MAX_QUARTER_TICKS, len(_long_q))
_badfmt = False
try:
    _axis_for("2023-03-31", "2026-06-30", label_fmt="weekly-ish")
except ValueError:
    _badfmt = True
check("an unknown x_label_fmt RAISES (fail-loud, not a silent default)", _badfmt)


# ══════════════════════════════════════════════════════════════════════════════
print("\n── 18. right-edge pad: the newest observation isn't flush against the frame ──")


def _ps(start, periods, freq, kind="line"):
    idx = _pd.date_range(start, periods=periods, freq=freq)
    return RR.PlotSeries(label="S", series=_pd.Series(range(periods), index=idx,
                                                      dtype=float), kind=kind)


# (a) the pad is measured in the OWN period of the series that sets the right edge.
_m = _ps("2022-01-31", 54, "ME")                       # monthly, 4.5y span
_end_m, _start_m = _pd.Timestamp("2026-06-30"), _pd.Timestamp("2022-01-31")
_pad_m = (BC._x_end_pad(_end_m, _start_m, [_m]) - _end_m).days
check("monthly chart pads ~3 months (long enough span that the cap doesn't bite)",
      80 <= _pad_m <= 95, f"{_pad_m}d")
_w = _ps("2024-01-07", 130, "W")                       # weekly, 2.5y span
_end_w, _start_w = _pd.Timestamp("2026-06-28"), _pd.Timestamp("2024-01-07")
_pad_w = (BC._x_end_pad(_end_w, _start_w, [_w]) - _end_w).days
check("weekly chart pads ~3 weeks (not 3 months)", 18 <= _pad_w <= 24, f"{_pad_w}d")
check("the weekly pad is much smaller than the monthly one", _pad_w < _pad_m, (_pad_w, _pad_m))

# (b) MIXED frequency: the cadence of whichever series reaches FURTHEST RIGHT wins — asserted
# in BOTH directions, since "furthest right" is the whole rule.
def _pad_of(seriess, start="2024-01-07"):
    end = max(s.series.index.max() for s in seriess)
    return (BC._x_end_pad(end, _pd.Timestamp(start), seriess) - end).days


_mo = _ps("2024-01-31", 30, "ME")                      # monthly, ends 2026-06-30
_wk_later = _ps("2024-01-07", 135, "W")                # weekly,  ends 2026-08-02 (furthest)
check("weekly reaches furthest right → pad follows the WEEKLY cadence",
      _wk_later.series.index.max() > _mo.series.index.max()
      and _pad_of([_mo, _wk_later]) <= 24, f"{_pad_of([_mo, _wk_later])}d")
_wk_earlier = _ps("2024-01-07", 100, "W")              # weekly ends 2025-11-30 (monthly wins)
check("monthly reaches furthest right → pad follows the MONTHLY cadence",
      _mo.series.index.max() > _wk_earlier.series.index.max()
      and _pad_of([_mo, _wk_earlier]) > 24, f"{_pad_of([_mo, _wk_earlier])}d")

# (c) the CAP: 3 whole quarters on a short quarterly chart would be ~20% of the panel.
_q = _ps("2023-03-31", 14, "QE")                       # 14 quarterly bars, ~3.3y
_end_q, _start_q = _pd.Timestamp("2026-06-30"), _pd.Timestamp("2023-03-31")
_pad_q = (BC._x_end_pad(_end_q, _start_q, [_q]) - _end_q).days
_vis_q = (_end_q - _start_q).days
check("short quarterly chart: pad is CAPPED, not 3 full quarters",
      _pad_q < 3 * 91 and _pad_q <= round(BC._X_PAD_MAX_FRAC * _vis_q) + 1, f"{_pad_q}d")
check("…but the cap still clears a bar's half-width (so the last bar reads whole)",
      _pad_q > 0.39 * 91 * 0.5, f"{_pad_q}d")

# (d) opt-out and no-op cases.
check("x_pad_periods=0 restores a flush right edge",
      BC._x_end_pad(_end_m, _start_m, [_m], 0) == _end_m)
check("an explicit x_pad_periods is honored",
      (BC._x_end_pad(_end_m, _start_m, [_m], 1) - _end_m).days
      < (BC._x_end_pad(_end_m, _start_m, [_m], 3) - _end_m).days)
_single = RR.PlotSeries(label="one", series=_pd.Series(
    [1.0], index=_pd.to_datetime(["2026-06-30"])))
check("a single-point series has no inferable period → edge unchanged (no crash)",
      BC._x_end_pad(_end_m, _start_m, [_single]) == _end_m)

# (e) DISPLAY-ONLY: padding must never touch the plotted data (no extrapolated point).
_before = _m.series.copy()
BC._x_end_pad(_end_m, _start_m, [_m])
check("padding is display-only — the series is not extended or mutated",
      _m.series.equals(_before) and _m.series.index.max() == _pd.Timestamp("2026-06-30"))

# (f) BARS are centered on their stamp, so the LEFT edge also needs a half-period or the
# opening bar is sliced in two. Lines start AT their first point and need no pad.
_bar = _ps("2023-03-31", 14, "QE", kind="bar")
_ls = BC._x_start(_pd.Timestamp("2020-01-01"), [_ps("2023-03-31", 14, "QE")])
_bs = BC._x_start(_pd.Timestamp("2020-01-01"), [_bar])
check("a LINE chart's left edge sits exactly on the first point",
      _ls == _pd.Timestamp("2023-03-31"), _ls)
check("a BAR chart's left edge backs off ~half a period (first bar reads whole)",
      _pd.Timedelta(days=35) <= (_ls - _bs) <= _pd.Timedelta(days=50), (_ls - _bs).days)
_stk = _ps("2023-03-31", 14, "QE", kind="stacked_bar")
check("stacked bars get the same half-period left pad",
      BC._x_start(_pd.Timestamp("2020-01-01"), [_stk]) == _bs)


# ══════════════════════════════════════════════════════════════════════════════
print("\n── 19. signed bracket lag/lead + HTML-entity decode (2026-08-03) ──")

# (a) SIGN. The old parser captured only the digits, so `[+4]` shifted the same way as
# `[-4]`: a requested LEAD plotted as a LAG, silently. `_shift_periods` returns a pandas
# shift, whose sign is the NEGATION of the tag's (shift(+n) moves values forward = lag).
check("[-4] → shift +4 (LAG: value at t comes from t-4)", BC._shift_periods("[-4]") == 4)
check("[+4] → shift -4 (LEAD) — NOT the same as [-4]",
      BC._shift_periods("[+4]") == -4
      and BC._shift_periods("[+4]") != BC._shift_periods("[-4]"))
check("an UNSIGNED [4] reads as a lag (Haver prints [-n])", BC._shift_periods("[4]") == 4)
check("magnitude is carried, not just the sign", BC._shift_periods("[-12]") == 12)
check("whitespace inside the brackets is tolerated", BC._shift_periods("[ -4 ]") == 4)
check("bare signed forms follow the same tag convention",
      (BC._shift_periods("-4"), BC._shift_periods("+2")) == (4, -2))
for _empty in (None, "", "   "):
    check(f"no lag tag ({_empty!r}) → no shift", BC._shift_periods(_empty) == 0)

# (b) FAIL-LOUD floor. Returning 0 for an unparseable tag plots the series unlagged and
# looks entirely plausible — the exact silent-wrong class this pipeline refuses.
for _bad in ("[-]", "lagged", "[four]", "[]"):
    _raised = False
    try:
        BC._shift_periods(_bad)
    except ValueError:
        _raised = True
    check(f"an unparseable lag tag {_bad!r} RAISES (never silently unlagged)", _raised)

# (c) the tag round-trips into the legend in the SAME notation the read used — the old
# code hard-coded a minus, so a lead would have been captioned "[-4]".
check("lag tag renders as [-4]", BC._lag_tag("[-4]") == "[-4]")
check("lead tag renders as [+4] (not a hard-coded minus)", BC._lag_tag("[+4]") == "[+4]")
check("no tag → nothing appended", BC._lag_tag(None) == "")

# (d) ORDER: transform first, THEN shift. A z-score must be computed over the series' own
# history and only then slid, so the plotted z-values are the UNSHIFTED ones, just moved.
_idx = _pd.date_range("2015-03-31", periods=40, freq="QE")
_sm = {"X@T": T.SeriesData(values=_pd.Series(range(40), index=_idx, dtype=float),
                           freq="Q", code="X@T", inception=_idx[0])}
_w = (_idx[0], _idx[-1])
_4q = T.shift_offset("Q", 4)
_z0 = T.transform("zs(X@T)", _sm, _w, "Q", lag=0)
# The window must span the shift, else the slice clips the extension back off.
_zlag = T.transform("zs(X@T)", _sm, (_idx[0], _idx[-1] + _4q), "Q",
                    lag=BC._shift_periods("[-4]"))
_zled = T.transform("zs(X@T)", _sm, (_idx[0] - _4q, _idx[-1]), "Q",
                    lag=BC._shift_periods("[+4]"))
check("zs-then-lag: the plotted values ARE the unshifted z-scores, just relabelled",
      _zlag.equals(_z0.shift(1, freq=_4q)))
check("[-4] LAG: value at t equals the z-score from t-4",
      abs(_zlag.loc[_idx[20]] - _z0.loc[_idx[16]]) < 1e-12)
check("[+4] LEAD: value at t equals the z-score from t+4",
      abs(_zled.loc[_idx[20]] - _z0.loc[_idx[24]]) < 1e-12)
check("lag and lead move the line in OPPOSITE directions",
      _zlag.dropna().index[-1] > _z0.dropna().index[-1]
      and _zled.dropna().index[-1] < _z0.dropna().index[-1])
check("a lag/lead never renormalizes the z-score (mu/sigma from the pre-shift series)",
      abs(_zlag.dropna().std() - _z0.dropna().std()) < 1e-12)
check("the newest reading is CARRIED to its lagged date, not dropped",
      abs(_zlag.loc[_idx[-1] + _4q] - _z0.loc[_idx[-1]]) < 1e-12)

# (e) legend: append the tag ONLY when the ratified label is silent on the shift, or the
# chart reads "…(%. lagged by 4qtrs) [-4]" as it did on 2026-08-03.
def _leg(desc, lag):
    return BC._legend_label({"resolved": "fwill@surveys", "proposed_legend": desc,
                             "base_descriptor": desc, "lag": lag})


check("a label SILENT on the shift still gets the tag (tripwire intact)",
      _leg("Banks Willingness to Lend (%)", "[-4]") == "Banks Willingness to Lend (%) [-4]")
check("a label that already SAYS 'lagged by 4qtrs' is not double-tagged",
      _leg("Banks Willingness to Lend (%. lagged by 4qtrs)", "[-4]")
      == "Banks Willingness to Lend (%. lagged by 4qtrs)")
check("'lead'/'led' wording also suppresses the duplicate tag",
      _leg("Willingness to Lend (leads payrolls 4q)", "[+4]")
      == "Willingness to Lend (leads payrolls 4q)")
check("a label carrying its own bracket tag isn't tagged twice",
      _leg("Willingness to Lend [-4]", "[-4]") == "Willingness to Lend [-4]")
check("no lag → label untouched", _leg("Willingness to Lend (%)", None)
      == "Willingness to Lend (%)")

# (f) HTML entities. Graph returns chat bodies as HTML, so a typed "C&I" arrives as
# "C&amp;I"; stripping tags alone left the entity literal and it rendered on the canvas.
check("&amp; decodes to & (the 2026-08-03 title defect)",
      TM._strip_html("Demand for C&amp;I credit keeps firming")
      == "Demand for C&I credit keeps firming")
check("entities inside real markup decode after the tags are stripped",
      TM._strip_html("<p>Banks &amp; thrifts</p>") == "Banks & thrifts")
check("quote/angle entities decode too",
      TM._strip_html("&quot;core&quot; &lt;2%&gt;") == '"core" <2%>')
check("&nbsp; still becomes a plain space", TM._strip_html("a&nbsp;b") == "a b")
check("text with no entities is unchanged", TM._strip_html("Wage growth is cooling")
      == "Wage growth is cooling")
_tr = TM.parse_title_reply(["[c1] title=Demand for C&amp;I credit keeps firming"],
                           [{"title": "opt", "subtitle": "sub"}])
check("a title reply carrying '&' decodes on the way into the ledger",
      (_tr or {}).get("title") == "Demand for C&I credit keeps firming", _tr)


# ══════════════════════════════════════════════════════════════════════════════
print("\n── 20. a lag EXTENDS the window; pad measured in NATIVE cadence (2026-08-03) ──")

# (a) the window end must follow the SHIFT, or the slice clips the extension straight
# back off and the line stops where the unlagged data did.
_qidx = _pd.date_range("2020-03-31", periods=26, freq="QE")     # ends 2026-06-30
_midx = _pd.date_range("2020-01-31", periods=76, freq="ME")     # ends 2026-04-30
_wsm = {"Q@T": T.SeriesData(values=_pd.Series(range(26), index=_qidx, dtype=float),
                            freq="Q", code="Q@T"),
        "M@T": T.SeriesData(values=_pd.Series(range(76), index=_midx, dtype=float),
                            freq="M", code="M@T")}


def _wend(lag_q, lag_m=None):
    plan = [({"lag": lag_q}, "Q@T", "q", "shared"), ({"lag": lag_m}, "M@T", "m", "shared")]
    return BC._window_end(plan, _wsm)


check("no lag → window ends at the latest raw observation",
      _wend(None) == _pd.Timestamp("2026-06-30"), _wend(None))
check("[-4] on the QUARTERLY slot carries the end 4 QUARTERS forward",
      _wend("[-4]") == _pd.Timestamp("2027-06-30"), _wend("[-4]"))
check("the lag counts in the slot's OWN period, not the chart's monthly grid",
      _wend("[-4]") - _pd.Timestamp("2026-06-30") > _pd.Timedelta(days=300))
check("[+4] LEAD shortens the quarterly slot, so the monthly slot sets the end",
      _wend("[+4]") == _pd.Timestamp("2026-04-30"), _wend("[+4]"))
check("a lag on the MONTHLY slot extends by MONTHS",
      _wend(None, "[-3]") == _pd.Timestamp("2026-07-31"), _wend(None, "[-3]"))
check("_slot_freq takes the HIGHEST operand frequency (how a BinOp lifts)",
      (BC._slot_freq(["Q@T"], _wsm), BC._slot_freq(["Q@T", "M@T"], _wsm)) == ("Q", "M"))

# (b) the pad is measured in the series' NATIVE cadence. The plotted index sits on the
# chart's COMMON grid, so a quarterly line on a monthly chart looked monthly and "3
# observations" quietly became 3 months instead of 3 quarters.
_qi_lifted = _pd.date_range("2020-01-31", periods=91, freq="ME")   # Q series, monthly grid
_lifted = RR.PlotSeries(label="quarterly-but-lifted",
                        series=_pd.Series(range(91), index=_qi_lifted, dtype=float),
                        freq="Q")
_naive = RR.PlotSeries(label="same values, freq unknown",
                       series=_pd.Series(range(91), index=_qi_lifted, dtype=float))
# Measured on an ~11-year span, where the min/max clamp leaves both cadences free to
# differ. (On a much longer span the FLOOR dominates and the two collapse to the same
# value — correct behaviour, and asserted separately in (d) below.)
_e, _s = _qi_lifted.max(), _pd.Timestamp("2016-09-30")
_pad_native = (BC._x_end_pad(_e, _s, [_lifted]) - _e).days
_pad_naive = (BC._x_end_pad(_e, _s, [_naive]) - _e).days
check("native freq 'Q' → a QUARTERLY-sized pad even on a monthly grid",
      _pad_native >= 200, f"{_pad_native}d")
check("without it the pad collapses to the grid cadence (~3 months) — the defect",
      80 <= _pad_naive <= 95 and _pad_naive < _pad_native, (_pad_naive, _pad_native))
check("a monthly-native series still pads ~3 months (no over-correction)",
      80 <= (BC._x_end_pad(_e, _s, [RR.PlotSeries(
          label="m", series=_naive.series, freq="M")]) - _e).days <= 95)
check("the 6%-of-span cap still applies to a native-cadence pad",
      (BC._x_end_pad(_e, _pd.Timestamp("2024-01-31"), [_lifted]) - _e).days < _pad_native)

# (c) end-to-end: transform + lag + lag-aware window keeps every observation AND lands
# the newest reading at its lagged date.
_w = (_pd.Timestamp("2020-03-31"), _wend("[-4]"))
_out = T.transform("Q@T", _wsm, _w, "M", lag=BC._shift_periods("[-4]")).dropna()
check("the lagged line reaches the extended window end",
      _out.index.max() == _pd.Timestamp("2027-06-30"), _out.index.max())
check("its final value is the newest RAW reading, carried forward (not dropped)",
      abs(_out.iloc[-1] - 25.0) < 1e-9, _out.iloc[-1])

# (d) the pad FLOOR. "3 observations" is relative to the DATA's cadence, not the CHART's
# width: on a 40-year monthly chart 3 months is ~0.6% of the panel — a couple of pixels —
# so every long chart still read as flush (2026-08-11 measured 0.16%–1.27% across six
# charts). The floor is what actually delivers the gutter, and it makes the gutter
# CONSISTENT chart-to-chart, which is how the eye judges it.
def _pad_frac(years, freq, n=None):
    end = _pd.Timestamp("2026-06-30")
    start = end - _pd.Timedelta(days=round(365.25 * years))
    idx = _pd.date_range(start, end, freq="ME")
    ps = RR.PlotSeries(label="s", series=_pd.Series(range(len(idx)), index=idx,
                                                    dtype=float), freq=freq)
    return (BC._x_end_pad(end, start, [ps], n) - end).days / (end - start).days


# the pad is rounded to whole days, so allow a few hundredths of a point of slack.
_RND = 5e-4
for _yrs in (5, 15, 27, 42):
    _f = _pad_frac(_yrs, "M")
    check(f"a {_yrs}-year monthly chart gets at least the {BC._X_PAD_MIN_FRAC:.0%} floor",
          _f >= BC._X_PAD_MIN_FRAC - _RND, f"{_f:.3%}")
check("the gutter is CONSISTENT across wildly different spans (that's the point)",
      max(_pad_frac(y, "M") for y in (15, 27, 42))
      - min(_pad_frac(y, "M") for y in (15, 27, 42)) < 0.005)
check("the floor bites only where 3 observations is too small to see",
      _pad_frac(5, "M") > BC._X_PAD_MIN_FRAC + _RND
      and _pad_frac(42, "M") <= BC._X_PAD_MIN_FRAC + _RND,
      (f"{_pad_frac(5, 'M'):.2%}", f"{_pad_frac(42, 'M'):.2%}"))
check("the ceiling still wins over the floor on a short low-frequency chart",
      _pad_frac(3, "Q") <= BC._X_PAD_MAX_FRAC + _RND, f"{_pad_frac(3, 'Q'):.2%}")
check("floor <= ceiling always (the clamp can never invert)",
      BC._X_PAD_MIN_FRAC < BC._X_PAD_MAX_FRAC)
# the opt-out must survive the floor — a floor that ignored x_pad_periods=0 would make a
# flush edge unreachable.
check("x_pad_periods=0 still restores a flush edge (floor does not override the opt-out)",
      _pad_frac(42, "M", 0) == 0.0)
check("a reduced x_pad_periods scales the floor down rather than snapping to it",
      _pad_frac(42, "M", 1) < _pad_frac(42, "M", 3))


# ══════════════════════════════════════════════════════════════════════════════
print("\n── 21. Haver's aggregation/units line is NOT a transform (2026-08-11) ──")

# Haver prints "Avg, % p.a." under a series name to describe how the series IS. Read as
# an `applied_transform`, the "%" + annual stem matched a CHANGE rule and the 2y Treasury
# plotted as `difv%(FCM2,1)` instead of its level — silent-wrong, and it survived approval
# because the legend said "(Avg, % p.a.)", which reads perfectly reasonable.
for _agg in ("Avg, % p.a.", "Avg", "Average", "Mean", "% p.a.", "percent per annum",
             "Sum, Mil.$", "EOP, Index", "Total", "SA, Index", "NSA", "$/bbl", "Avg, %"):
    check(f"aggregation/units {_agg!r} → LEVEL (no transform)",
          BC.phrase_to_haver(_agg, "X") is None, BC.phrase_to_haver(_agg, "X"))
check("the exact 2026-08-11 defect: 'Avg, % p.a.' is no longer a % change",
      BC.phrase_to_haver("Avg, % p.a.", "FCM2") != "difv%(FCM2,1)")

# NO OVER-MATCH: a phrase that NAMES an operation must still map to that operation. This
# is the load-bearing half — a units guard that swallowed real transforms would convert a
# loud failure into a silent level, which is strictly worse than the bug it fixes.
for _phrase, _want in (("% Change - Year to Year", "yryr%(X)"),
                       ("% Change - Period to Period", "diff%(X,1)"),
                       ("% Change - Annual Rate", "difa%(X,1)"),
                       ("2-qtr %Change-ann", "difa%(X,2)"),
                       ("3-month moving average", "movv(X,3)"),
                       ("moving average, 6-month", "movv(X,6)"),
                       ("Z-Score", "zs(X)"),
                       ("% change, year-over-year", "yryr%(X)")):
    check(f"real transform {_phrase!r} still maps to {_want}",
          BC.phrase_to_haver(_phrase, "X") == _want, BC.phrase_to_haver(_phrase, "X"))
check("a moving AVERAGE is not swallowed by the bare 'average' stem",
      BC.phrase_to_haver("3-month moving average", "X") is not None)

# the fail-loud floor for genuinely novel phrasing is untouched
for _novel in ("flurgle transform", "year-to-date"):
    _raised = False
    try:
        BC.phrase_to_haver(_novel, "X")
    except ValueError:
        _raised = True
    check(f"novel phrase {_novel!r} still RAISES", _raised)


# ─────────────────────────────── tally ─────────────────────────────────────
print(f"\n=== workflow offline proof: {_N[0] - _F[0]}/{_N[0]} checks passed ===")
sys.exit(1 if _F[0] else 0)
