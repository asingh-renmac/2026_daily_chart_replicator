"""Turn the ticked worksheet into an eval set, and refuse to invent anything.

The worksheet is the human's side of the contract; this is the machine's. It reads the
`pick` marks and emits one entry per alias, with the shape the eval needs:

    {"query", "expected_outcome", "expected_ticker", "acceptable_tickers",
     "frequency", "adjustment", "source", "note"}

Rules, each of which exists because the alternative silently corrupts the set:

  * SEVERAL ticks in a group are not a mistake -- they are the mirror case. The first
    becomes expected_ticker and all of them become acceptable_tickers, because the
    catalogue holds jcdrgi and jcdrgim under one identical descriptor and jcsxeh under two
    databases, and a set that admits only one marks a right answer wrong.
  * A tick on the PARK row makes expected_outcome "park" and expects NO ticker. This is a
    first-class right answer. Dropping these would leave a set in which refusing to guess
    is never correct, and a resolver tuned on that set learns to guess.
  * An UNTICKED group is skipped and counted, never defaulted. A blank means the reviewer
    has not looked yet; turning that into "park", or into the top candidate, would launder
    an absence of judgement into a label.
  * A typed ticker on the NONE row is normalised to CODE@DB, since the catalogue stores
    DB:CODE and mixing the two reports a present series as missing.

    python scripts/compile_alias_evalset.py
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def norm_ticker(raw: str) -> str:
    """CODE@DB, whichever way round the reviewer typed it."""
    t = (raw or "").strip().lower()
    if not t:
        return ""
    if ":" in t and "@" not in t:              # catalogue's DB:CODE
        db, _, code = t.partition(":")
        return f"{code}@{db}"
    return t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default="outputs/alias_worksheet.csv")
    ap.add_argument("--out", default="eval/alias_eval_set.json")
    ap.add_argument("--reviewer", default="")
    args = ap.parse_args()

    rows = list(csv.DictReader((ROOT / args.sheet).open(encoding="utf-8-sig")))
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if (r.get("group") or "").strip():
            groups[r["group"]].append(r)

    out, skipped, parks = [], [], 0
    for gid, grp in groups.items():
        alias = grp[0]["alias"]
        picked = [r for r in grp if re.match(r"^\s*[xX✓y1]", r.get("pick") or "")]
        if not picked:
            skipped.append(alias)
            continue

        if any(r["row_type"].startswith("PARK") for r in picked):
            parks += 1
            out.append({"query": alias, "expected_outcome": "park",
                        "expected_ticker": None, "acceptable_tickers": [],
                        "frequency": "", "adjustment": "",
                        "note": (picked[0].get("note") or "").strip(),
                        "source": f"worksheet:{gid}"})
            continue

        tickers, first = [], None
        for r in picked:
            t = norm_ticker(r.get("ticker"))
            if not t:
                continue
            if first is None:
                first = r
            if t not in tickers:
                tickers.append(t)
        if not tickers:
            skipped.append(alias)             # ticked a NONE row but typed no ticker
            continue

        out.append({"query": alias, "expected_outcome": "bind",
                    "expected_ticker": tickers[0], "acceptable_tickers": tickers,
                    "frequency": (first.get("frequency") or "").strip(),
                    "adjustment": (first.get("adjustment") or "").strip(),
                    "note": (first.get("note") or "").strip(),
                    "source": f"worksheet:{gid}"})

    payload = {"built": date.today().isoformat(), "reviewer": args.reviewer,
               "sheet": args.sheet, "n": len(out), "n_park": parks,
               "n_unreviewed_skipped": len(skipped), "pairs": out}
    dest = ROOT / args.out
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"groups in sheet      : {len(groups)}")
    print(f"labelled             : {len(out)}   (of which park-is-correct: {parks})")
    print(f"skipped, NOT reviewed: {len(skipped)}")
    if skipped:
        print("   e.g. " + ", ".join(skipped[:6]))
    n_mirror = sum(1 for e in out if len(e['acceptable_tickers']) > 1)
    print(f"entries with mirrors : {n_mirror}")
    print(f"written              : {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
