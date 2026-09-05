"""Gate G19f — park count AND park quality on the baseline commentary (plan.md §15.5).

Replays every series slot in `fixtures/g19f_baseline_commentary.md` through the chat
lane's resolver and reports, per slot: bound or parked, the ticker, the G3 formula the
transform phrase produced, and for a park whether the reason NAMES the evidence that
separates the candidates.

Park count alone is not the gate. §15 was never trying to bind everything -- the
2026-09-04 slot A park was correct, because BLS and the Conference Board publish
different series under one descriptor. What §15 owes the operator is that every park is
answerable. A park saying "similarity is not evidence" is a failure even though it is
true; a park naming two sources, two start dates and two observation counts is a success
even though it is still a park.

    python scripts/g19f_baseline.py

Read-only: resolves and reports, never writes any store.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with contextlib.redirect_stdout(io.StringIO()):
    from haver_chart import lane

# The 10 charts, transcribed from the fixture. `transform` is passed as the operator
# would word it -- these are the phrases, NOT formulas, which is the whole point: the
# words-only path is what §15.1 repaired.
CHARTS: list[tuple[str, list[tuple[str, str]]]] = [
    ("1. The workweek is stretching again led by factories", [
        ("Average Weekly Hours: Total Private", ""),
        ("Average Weekly Hours: Manufacturing", "")]),
    ("2. Labor income growth is barely outrunning the policy rate", [
        ("Aggregate Weekly Payrolls: Total Private", "% Change - Year to Year"),
        ("Federal Funds Target Rate", "")]),
    ("3. Hours growth has firmed off last year's lows", [
        ("Aggregate Weekly Hours Index: Total Private Industries",
         "3-month %Change-ann")]),
    ("4. Total labor income is growing like it's the 2010s again", [
        ("Aggregate Weekly Payrolls: Production and Nonsupervisory Workers",
         "2-quarter annualized growth")]),          # §15.1d: 2 quarters on a monthly series
    ("5. Payroll momentum has increased since summer lull", [
        ("All Employees: Total Nonfarm", "3-month moving average of the monthly change"),
        ("All Employees: Total Private", "3-month moving average of the monthly change")]),
    ("6. Underemployment keeps sliding with the jobless rate flat", [
        ("Civilian Unemployment Rate", ""),
        ("Unemployment Rate: U-6 Total Unemployed Plus All Marginally Attached", "")]),
    ("7. Wage growth is cooling but the pace may not last", [
        ("Average Hourly Earnings: Total Private", "% Change - Year to Year"),
        ("Average Hourly Earnings: Total Private", "3-month %Change-ann")]),
    ("8. Construction and factories are both adding jobs again", [
        ("All Employees: Construction", "3-month moving average of the monthly change"),
        ("All Employees: Manufacturing", "3-month moving average of the monthly change")]),
    ("9. Hiring breadth is the widest since late 2024", [
        ("One-Month Diffusion Index: Total Private", ""),
        ("One-Month Diffusion Index: Manufacturing", "")]),
    ("10. August's bounce mostly reversed the summer give-back", [
        ("All Employees: Food Services and Drinking Places", "monthly change"),
        ("All Employees: Local Government Education", "monthly change")]),
]

# A park is ANSWERABLE when its reason cites something the operator can act on: a
# differing metadata field, a named source, a span, an observation count, or a concrete
# instruction. The old "similarity isn't evidence" message contains none of these.
EVIDENCE_TOKENS = ("shortsource", "longsource", "startdate", "numobs", "frequency",
                   "aggtype", "magnitude", "datatype", "diftype", "obs", "differ",
                   "mirror", "usecon", "supplies", "DLX", "no confident")


def main() -> int:
    bound = parked = answerable = 0
    transform_fail: list[str] = []
    rows: list[tuple] = []

    for title, slots in CHARTS:
        print(f"\n{title}")
        for desc, phrase in slots:
            # The formula the WORDS produce, resolved independently of the bind so a
            # transform defect cannot hide behind a park.
            try:
                formula = lane.BC.phrase_to_haver(phrase, "X") if phrase else None
                formula_note = formula or ("level" if not phrase else "level")
            except Exception as exc:
                formula_note = f"RAISES: {str(exc)[:60]}"
                transform_fail.append(f"{phrase!r}: {exc}")

            try:
                r = lane.resolve_one(desc, applied_transform=phrase)
            except Exception as exc:
                print(f"   ERROR  {desc[:58]:58}  {type(exc).__name__}: {str(exc)[:50]}")
                rows.append((desc, phrase, "error", "", formula_note, str(exc)[:70]))
                continue

            reason = (r.get("reason") or "").strip()
            if r["status"] == "resolved":
                bound += 1
                print(f"   BOUND  {desc[:58]:58}  {r['resolved']}")
                print(f"          transform: {formula_note}")
            else:
                parked += 1
                ok = any(t.lower() in reason.lower() for t in EVIDENCE_TOKENS)
                answerable += bool(ok)
                print(f"   PARK   {desc[:58]:58}  "
                      f"[{'answerable' if ok else 'UNANSWERABLE'}]")
                print(f"          transform: {formula_note}")
                print(f"          reason: {reason[:150]}")
                for c in (r.get("candidates") or [])[:2]:
                    print(f"            - {c['code']}  exact={c['exact_token_match']}  "
                          f"sim={c['similarity']}")
            rows.append((desc, phrase, r["status"], r.get("resolved") or "",
                         formula_note, reason[:90]))

    total = bound + parked
    print("\n" + "=" * 78)
    print(f"G19f  slots={total}  bound={bound}  parked={parked}"
          f"  ({100*bound//total if total else 0}% bound)")
    print(f"      parks that NAME their evidence: {answerable}/{parked}"
          f"  {'<- gate is this line' if parked else ''}")
    if transform_fail:
        print(f"      transform phrases that failed to map: {len(transform_fail)}")
        for t in transform_fail:
            print(f"        {t[:110]}")
    else:
        print("      every transform phrase mapped to a formula")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
