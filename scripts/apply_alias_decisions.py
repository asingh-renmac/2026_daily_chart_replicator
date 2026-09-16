"""Write the adjudication in notes/alias_worksheet_decisions.csv into the worksheet.

The decisions sheet is the reasoning; the worksheet is the machine-readable answer, and
the two had drifted -- the sheet's `applied` column describes writes that never reached
eval/alias_worksheet.csv. This performs them, from the sheet, so the record and the file
agree and the walk from one to the other is reproducible rather than remembered.

Three things it does that are not a straight transcription, each forced by the data:

  RENAMES ARE APPLIED TO THE WHOLE BLOCK. alias_new differs from alias_old in 155 of 170
  rows and that text IS the resolver's query at eval time, so it is load-bearing. It also
  has to land on EVERY row of a block: the compiler keys on (group, alias), so a half-done
  rename splits one block in two and the half without the tick is reported as unreviewed.

  MIRRORS THAT WERE NEVER OFFERED GO IN A NEW COLUMN. Five `tick` tickers across four
  blocks are not in their block's candidate list -- ipmfg@usecon beside ipmfg@ip,
  pcums/icums@usecon beside ums@cpidata. In every case at least one listed ticker WAS
  candidate #1, so `tick` is right and retrieval did find the series; the extras are
  database mirrors supplied from knowledge. They cannot be ticked because they are not
  rows, so `also_acceptable` carries them and the compiler merges them into
  acceptable_tickers. Dropping them would score a resolver WRONG for returning the
  usecon mirror of the very series we accepted.

  TWO BLOCKS ARE MARKED UNSCORABLE RATHER THAN DELETED. US0291 binds ycomp@usecon, whose
  twin ycompr@usecon carries a byte-identical descriptor, so no retriever can be asked to
  choose; US0329 is unambiguous but absent from the catalog, which is a licensing fact
  rather than a resolver failure. Keeping them with `exclude` set preserves the judgement
  and the reason; deleting them would silently shrink the denominator.

    python scripts/apply_alias_decisions.py            # dry run, changes nothing
    python scripts/apply_alias_decisions.py --apply
"""
from __future__ import annotations

import argparse
import csv
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "eval" / "alias_worksheet.csv"
DECISIONS = ROOT / "notes" / "alias_worksheet_decisions.csv"

# Judgements that are real but unscorable. Kept in the sheet, excluded from the metric.
UNSCORABLE = {
    "US0291": "ycomp and ycompr carry byte-identical descriptors; no retriever can choose",
    "US0329": "unambiguous but absent from the catalog -- a licence fact, not a miss",
}


def norm(raw: str) -> str:
    t = (raw or "").strip().lower()
    if ":" in t and "@" not in t:
        db, _, code = t.partition(":")
        return f"{code}@{db}"
    return t


def tickers_of(dec: dict) -> list[str]:
    return [norm(t) for t in (dec.get("tickers") or "").split(";") if t.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rows = list(csv.DictReader(SHEET.open(encoding="utf-8-sig")))
    fields = list(rows[0].keys())
    for extra in ("also_acceptable", "exclude"):
        if extra not in fields:
            fields.append(extra)
    for r in rows:
        r.setdefault("also_acceptable", "")
        r.setdefault("exclude", "")

    decisions = list(csv.DictReader(DECISIONS.open(encoding="utf-8-sig")))
    by_key = {(d["group"], d["alias_old"]): d for d in decisions}

    blocks: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if (r.get("group") or "").strip():
            blocks[(r["group"], r["alias"])].append(r)

    already = {k for k, v in blocks.items() if any((x.get("pick") or "").strip() for x in v)}

    keep: set[int] = set()
    stats = defaultdict(int)
    unmatched_tick: list[str] = []

    for (gid, alias), block in blocks.items():
        dec = by_key.get((gid.split("-", 1)[-1], alias))
        if dec is None:
            if (gid, alias) in already:
                stats["kept (already labelled by hand)"] += 1
                keep.update(id(r) for r in block)
            else:
                # Not adjudicated and not hand-marked: a within-group duplicate spelling,
                # dropped before adjudication. 21 of these, by the sheet's own arithmetic.
                stats["dropped (duplicate spelling)"] += 1
            continue

        action = dec["action"].strip().lower()
        if action == "delete":
            stats["deleted (semantic duplicate)"] += 1
            continue

        new_alias = (dec.get("alias_new") or alias).strip() or alias
        for r in block:                       # every row, or the block splits
            r["alias"] = new_alias
            if gid.split("-", 1)[-1] in UNSCORABLE:
                r["exclude"] = "y"
        if new_alias != alias:
            stats["renamed"] += 1

        want = tickers_of(dec)
        note = (dec.get("note") or "").strip()

        if action == "park":
            row = next((r for r in block if r["row_type"].startswith("PARK")), None)
            if row is not None:
                row["pick"], row["note"] = "x", note
                stats["park"] += 1
        elif action == "none":
            row = next((r for r in block if r["row_type"].startswith("NONE")), None)
            if row is not None and want:
                row["pick"], row["ticker"], row["note"] = "x", want[0], note
                row["also_acceptable"] = ";".join(want[1:])
                stats["none (retrieval missed)"] += 1
        elif action == "tick":
            hit = [r for r in block
                   if r["row_type"] == "candidate" and norm(r["ticker"]) in want]
            for r in hit:
                r["pick"] = "x"
            if hit:
                hit[0]["note"] = note
                offered = {norm(r["ticker"]) for r in hit}
                extras = [t for t in want if t not in offered]
                if extras:
                    hit[0]["also_acceptable"] = ";".join(extras)
                    stats["tick, with unoffered mirrors"] += 1
                stats["tick (retrieval found it)"] += 1
            else:
                # Marked tick but nothing matched -- would silently vanish. Never seen in
                # the 2026-09-15 pass, and it must stay never-silent if it ever happens.
                unmatched_tick.append(dec["block_id"])
        keep.update(id(r) for r in block)

    kept_rows = [r for r in rows if id(r) in keep or not (r.get("group") or "").strip()]
    # Drop spacer runs left stranded by a removed block.
    out_rows: list[dict] = []
    for r in kept_rows:
        if not (r.get("group") or "").strip() and (not out_rows or
                                                   not (out_rows[-1].get("group") or "").strip()):
            continue
        out_rows.append(r)

    final_blocks = {(r["group"], r["alias"]) for r in out_rows if (r.get("group") or "").strip()}
    for k, v in sorted(stats.items()):
        print(f"  {k:34} {v}")
    print(f"\n  blocks before : {len(blocks)}")
    print(f"  blocks after  : {len(final_blocks)}")
    if unmatched_tick:
        print(f"\n  WARNING: {len(unmatched_tick)} 'tick' block(s) matched no candidate row:")
        for b in unmatched_tick[:6]:
            print(f"     {b}")

    if not args.apply:
        print("\n  DRY RUN -- nothing written. Re-run with --apply.")
        return 0

    # Writability FIRST, before the backup. Excel holds an exclusive lock on an open
    # workbook, so the naive order takes a backup, then fails on the write, and leaves a
    # stray .bak that is byte-identical to a file nothing changed -- litter that looks
    # like evidence of a half-finished edit. Checked by opening for append, which needs
    # the same lock the real write will need but changes nothing if it succeeds.
    try:
        with SHEET.open("a", encoding="utf-8"):
            pass
    except PermissionError:
        print(f"\n  ERROR: {SHEET.name} is locked -- it is open in Excel.")
        print("  Close the workbook and re-run. Nothing has been changed.")
        return 3

    backup = SHEET.with_suffix(f".bak-{datetime.now():%Y%m%d-%H%M%S}.csv")
    shutil.copy2(SHEET, backup)
    with SHEET.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    print(f"\n  backup : {backup.name}")
    print(f"  written: {SHEET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
