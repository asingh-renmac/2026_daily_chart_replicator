"""Check every hand-typed ticker in the worksheet against the catalogue.

The worksheet was built so labelling would be TICKING, and for the headline series it is
mostly not: the first seven labels were all typed into the NONE row, because retrieval put
the right answer outside its top 200 every time (lexical 2/7 inside the top 30, vector
1/7). Hand-typed codes are where typos live, and these rows are not destined for a report
-- they are destined for the ratified store, where a wrong ticker becomes a wrong chart
that nobody re-checks because it was "confirmed".

So each typed code is read back from the catalogue and its descriptor printed next to the
alias it claims to mean. Nothing here decides whether the binding is RIGHT -- only a human
reading those two lines together can -- but it does catch the three failures that are not
judgement calls at all: a code that does not exist, one whose frequency contradicts the
sheet, and one that is discontinued.

    python scripts/verify_worksheet_tickers.py
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import haver_search as HS  # noqa: E402


def norm(raw: str) -> str:
    t = (raw or "").strip().lower()
    if ":" in t and "@" not in t:
        db, _, code = t.partition(":")
        return f"{code}@{db}"
    return t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default="eval/alias_worksheet.csv")
    args = ap.parse_args()

    rows = [r for r in csv.DictReader((ROOT / args.sheet).open(encoding="utf-8-sig"))
            if (r.get("pick") or "").strip() and (r.get("ticker") or "").strip()]
    if not rows:
        print("nothing labelled yet")
        return 0

    print(f"{len(rows)} labelled rows\n")
    bad = 0
    for r in rows:
        tick, alias = norm(r["ticker"]), r["alias"]
        typed = r["row_type"].startswith("NONE")
        try:
            meta = HS.get_meta(tick)
        except Exception as e:
            meta = None
            print(f"  [ERR ] {alias:26} {tick:18} lookup failed: {e}")
            bad += 1
            continue
        if not meta:
            # The one unambiguous error: there is no such series. Nearly always a typo in
            # a code typed from memory, and invisible until a chart refuses to render.
            print(f"  [MISS] {alias:26} {tick:18} NOT IN CATALOGUE — check the spelling")
            bad += 1
            continue
        flags = []
        if meta.get("is_discontinued"):
            flags.append("DISCONTINUED")
        sheet_freq = (r.get("frequency") or "").strip().upper()
        if sheet_freq and meta.get("frequency") and sheet_freq != meta["frequency"]:
            flags.append(f"freq {meta['frequency']} != sheet {sheet_freq}")
        mark = "TYPED" if typed else "ticked"
        note = ("  <- " + ", ".join(flags)) if flags else ""
        if flags:
            bad += 1
        print(f"  [{'WARN' if flags else ' ok '}] {alias:26} {tick:18} {mark:6} "
              f"{meta.get('frequency', '?'):2} {meta.get('sa_status', '?'):5} "
              f"{(meta.get('descriptor') or '')[:42]}{note}")

    print(f"\n  {len(rows) - bad} clean, {bad} needing a look.")
    print("  'ok' means the code EXISTS and is current — read the descriptor against the")
    print("  alias yourself; nothing here can tell you the binding is the right one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
