"""
g5_demo.py — prove the G5 Teams loop OFFLINE on a low-stakes scripted case.

Exercises both round-trips end-to-end through StubTransport (no live Graph):
  * unresolved-ticker: park → ask → BAD reply (rejected by confirm) stays parked
    → GOOD reply (confirmed) un-parks → RESOLVED;
  * title: RESOLVED → ask numbered options (the g4_desired _var1/_var2 pairs) →
    pick "2" → APPROVED with that title/subtitle;
  * non-blocking: a row with NO reply stays awaiting across the run (Q1).

This is the dry-run G7 will repeat against GraphTransport once the channel is set.
Run:  python scripts/g5_demo.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ledger as L          # noqa: E402
import teams as TM          # noqa: E402
import approval as A        # noqa: E402

WORK = ROOT / "outputs" / "g5"
WORK.mkdir(parents=True, exist_ok=True)

# Fake Haver-metadata confirmation (stands in for get_series at runtime).
_KNOWN = {"pa413121@usecon", "nmscnx@usecon", "jcsrm@usna"}
def confirm_ticker(code_at_db: str) -> bool:
    return code_at_db.lower() in _KNOWN

# Title proposals = the g4_desired _var1/_var2 pairs (Opus proposer in prod).
_PROPOSALS = {
    "sample2_chart2": [
        {"title": "Housing inflation normalizes while core goods and services stay elevated",
         "subtitle": "Z-score"},
        {"title": "Housing inflation normalizes while core goods and services stay elevated",
         "subtitle": "Z-score of y/y%chg"},
    ],
}
def title_proposals(row):
    return _PROPOSALS.get(row["chart_id"],
                          [{"title": row.get("subject", "Chart"), "subtitle": ""}])


def show(led, tag):
    print(f"\n--- ledger after {tag} ---")
    for r in led.rows:
        print(f"  {r['chart_id']:<16} {r['status']:<18} "
              f"unresolved={r.get('unresolved')} resolved={r.get('resolved_ticker')} "
              f"title={r.get('chosen_title') or '-'}")


def main():
    led_path = WORK / "ledger_demo.csv"
    stub_path = WORK / "teams_stub_demo.json"
    for p in (led_path, stub_path):
        if p.exists():
            p.unlink()

    led = L.Ledger(str(led_path))
    tr = TM.StubTransport(str(stub_path))

    # seed: A parks on an ambiguous PPI (the live "PPI: Manufacturing Industries"
    # case), B is resolved & ready for a title, C parks and never gets a reply.
    led.upsert(message_id="m1", chart_index="3", chart_id="sample1_chart3",
               release_slug="durable_goods", subject="Durable goods",
               status=L.AWAITING_TICKER, unresolved=["PPI: Manufacturing Industries"])
    led.upsert(message_id="m1", chart_index="2", chart_id="sample2_chart2",
               release_slug="durable_goods", subject="PCE inflation",
               status=L.RESOLVED, resolved_ticker={"Housing": "jcsrm@usna"})
    led.upsert(message_id="m2", chart_index="1", chart_id="sample9_chart1",
               release_slug="ism", subject="ISM",
               status=L.AWAITING_TICKER, unresolved=["Some unknown index"])
    led.save(); show(led, "seed")

    # PASS 1: post both questions (ticker for A & C, title for B)
    A.run_ticker_roundtrip(led, tr, confirm_ticker)
    A.run_title_roundtrip(led, tr, title_proposals)
    led.save(); show(led, "pass 1 (questions posted)")

    # Human replies: A gets a BAD code first, B picks option 2. C stays silent.
    a_thread = A.chart_thread("durable_goods", "sample1_chart3")
    b_thread = A.chart_thread("durable_goods", "sample2_chart2")
    tr.queue_reply(a_thread, "PPI: Manufacturing Industries = bogus@nope")
    tr.queue_reply(b_thread, "2")

    A.run_ticker_roundtrip(led, tr, confirm_ticker)
    A.run_title_roundtrip(led, tr, title_proposals)
    led.save(); show(led, "pass 2 (bad ticker rejected, title chosen)")

    # A now gets the correct code (resolve-by-definition handoff).
    tr.queue_reply(a_thread, "pa413121@usecon")
    A.run_ticker_roundtrip(led, tr, confirm_ticker)
    led.save(); show(led, "pass 3 (good ticker confirmed)")

    print("\n--- stub Teams thread transcript ---")
    for key, t in tr.data["threads"].items():
        print(f"  thread {key}")
        for p in t["posts"]:
            print("    POST:", p["text"].splitlines()[0], "…")
        for rp in t["replies"]:
            print("    REPLY:", rp["text"])

    # assertions — the loop must end in the expected states
    by_id = {r["chart_id"]: r for r in led.rows}
    assert by_id["sample1_chart3"]["status"] == L.RESOLVED, "A should resolve"
    assert by_id["sample1_chart3"]["resolved_ticker"]["PPI: Manufacturing Industries"] \
        == "pa413121@usecon"
    assert by_id["sample2_chart2"]["status"] == L.APPROVED, "B should be approved"
    assert by_id["sample2_chart2"]["subtitle"] == "Z-score of y/y%chg"
    assert by_id["sample9_chart1"]["status"] == L.AWAITING_TICKER, "C stays parked"
    print("\nOK — both round-trips proven; no-reply row stayed parked.")


if __name__ == "__main__":
    main()
