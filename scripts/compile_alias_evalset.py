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
    ap.add_argument("--sheet", default="eval/alias_worksheet.csv")
    ap.add_argument("--out", default="eval/alias_eval_set.json")
    ap.add_argument("--reviewer", default="")
    args = ap.parse_args()

    reader = csv.DictReader((ROOT / args.sheet).open(encoding="utf-8-sig"))
    rows = list(reader)

    # Header check, loud, BEFORE anything reads a field. Every lookup below is by column
    # NAME, so a renamed or deleted header does not raise -- it returns None, every row
    # then looks like a blank spacer, and this writes a well-formed eval set containing
    # nothing, with exit code 0. A day of labelling would appear to have evaporated, and
    # the output would look like a clean run. Measured: renaming `group` to `block_id`
    # printed "groups in sheet: 0" and exited successfully.
    needed = {"group", "alias", "row_type", "pick", "ticker"}
    found = set(reader.fieldnames or [])
    if not needed <= found:
        print(f"ERROR: {args.sheet} is missing required column(s): "
              f"{', '.join(sorted(needed - found))}")
        print(f"       columns found: {', '.join(reader.fieldnames or ['<none>'])}")
        print("       The header row names the columns this script reads. Rename the")
        print("       VALUES in a column freely, but leave row 1 alone.")
        return 2

    # Headers fine and still nothing to group on: the sheet is empty, or every row's
    # column A was cleared. Blank column A means 'spacer', so clearing it on a real row
    # silently deletes that row from the eval set -- worth saying rather than reporting 0.
    if rows and not any((r.get("group") or "").strip() for r in rows):
        print(f"ERROR: {args.sheet} has {len(rows)} rows but column `group` is blank on "
              "every one.")
        print("       A blank `group` marks a spacer row, so nothing here can be read.")
        return 2
    # Keyed on (group, alias), not group. entry_id names the SERIES and several aliases share
    # one -- "labor force" and "LF" are both US0142 -- so grouping on it alone silently drops
    # every alias but the first. Every row carries its own alias, so the pair is exact.
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if (r.get("group") or "").strip():
            groups[(r["group"], r.get("alias") or "")].append(r)

    # Rewording an alias is legitimate -- column B IS the query the resolver will be given --
    # but it has to be done to EVERY row of the block, and Excel makes it very easy to edit
    # the one row you happened to be reading. Since the key includes the alias, a half-done
    # rename splits one block into two, and the half holding no tick is then reported as
    # unreviewed. That is a confusing way to lose a label, so name it outright. Detected on
    # CONTIGUOUS runs rather than on the group id, which cannot tell a partial rename from the
    # duplicated entry_ids the source CSV genuinely has.
    split: list[str] = []
    run: list[dict] = []
    for r in rows + [{"group": ""}]:
        if (r.get("group") or "").strip():
            run.append(r)
            continue
        names = {x.get("alias") or "" for x in run}
        if len(names) > 1:
            split.append(f"{run[0].get('group')}: " + " / ".join(sorted(names)))
        run = []
    if split:
        print(f"WARNING: {len(split)} block(s) hold more than one alias -- a partial rename?")
        for s in split[:5]:
            print(f"   {s}")
        print("   Fix: make column B identical on every row of the block, then re-run.\n")

    out, skipped, parks = [], [], 0
    for (gid, alias), grp in groups.items():
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
