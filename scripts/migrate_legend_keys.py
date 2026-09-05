"""Re-file legend-store entries whose key was derived from the PHRASE (plan.md §15.1a).

`resolve.transform_key` used to canonicalize a slot's `applied_transform` wording. For a
slot carrying an explicit `formula` that was the wrong source: `render_row` ignores the
phrase once a formula is present, and the flat phrase mapper silently drops the inner
half of a compound ("% Change - Year to Year, Z-Score" -> `zs(#)`), so a slot that
RENDERED `zs(yryr%(GDPH))` was filed under `ZS(#)`.

`transform_key` now reads the formula. The stored LABELS are still correct and still
human-approved -- only the name they are filed under is stale, so the fixed code would
look for `ZS(YRYR%(#))`, miss, and silently discard an approved label in favour of a
generated one. This moves each entry to the key the fixed code asks for.

The old->new mapping is DERIVED from the ledger, never hand-typed: every slot the daily
lane actually rendered is replayed through both the old and new key functions.

    python scripts/migrate_legend_keys.py            # dry run, prints the plan
    python scripts/migrate_legend_keys.py --apply    # writes, after a backup

Idempotent: an entry already sitting on the new key is skipped, so a second --apply is a
no-op rather than a double move.
"""

from __future__ import annotations

import contextlib
import csv
import glob
import io
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
csv.field_size_limit(10 ** 8)

with contextlib.redirect_stdout(io.StringIO()):      # importing Haver prints
    import build_chart as BC
    import resolve as R


def old_transform_key(slot: dict) -> str:
    """`transform_key` exactly as it behaved before §15.1a — phrase only.

    Note this calls TODAY's `phrase_to_haver`, so run the migration BEFORE §15.1b
    changes the phrase mapper, or the reconstruction of the old key stops being
    faithful. The build order in plan.md §15.4 exists for this reason.
    """
    phrase = (slot.get("applied_transform") or "").strip()
    if not phrase:
        return ""
    try:
        formula = BC.phrase_to_haver(phrase, "#")
    except Exception:
        formula = None
    if not formula:
        return ""
    return re.sub(r"\s+", "", formula.upper())


def old_legend_key(slot: dict) -> str:
    base = R.legend_key_base(slot)
    tf = old_transform_key(slot)
    return f"{base}|{tf}" if tf else base


def mapping_from_ledger() -> tuple[dict[str, str], set[str]]:
    """({old_key: new_key} for keys that moved, {every key the fixed code will ask for}).

    The second set is what makes this safe. An old key can serve MORE than one slot:
    `JCSXEHM@USECON|YRYR%(#)` was the key both for a slot carrying `zs(yryr%(JCSXEHM))`
    (which now asks for `ZS(YRYR%(#))`) and for a phrase-only y/y slot (which still asks
    for `YRYR%(#)`). Moving that entry would strand the second slot on the bare base key
    and hand it a `3m %chg saar` label. When the old key is still live, COPY."""
    moved: dict[str, str] = {}
    live: set[str] = set()
    for path in sorted(glob.glob(str(ROOT / "data" / "ledger_*.csv"))):
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh):
                try:
                    series = json.loads(row.get("series") or "[]")
                except Exception:
                    continue
                for slot in series if isinstance(series, list) else []:
                    if not isinstance(slot, dict):
                        continue
                    if not (slot.get("resolved") or slot.get("codes")):
                        continue
                    old, new = old_legend_key(slot), R.legend_key(slot)
                    live.add(new)
                    if old == new:
                        continue
                    # A key that two slots disagree about is not safe to re-file blind.
                    if moved.get(old, new) != new:
                        raise SystemExit(
                            f"ambiguous: {old!r} maps to both {moved[old]!r} and {new!r}")
                    moved[old] = new
    return moved, live


def main() -> int:
    apply = "--apply" in sys.argv
    store_path = R.CLARIFIED_DIR / "legend_labels.json"
    store = R.load_legend()
    mapping, live = mapping_from_ledger()
    moves, skipped, absent = [], [], 0

    for old, new in sorted(mapping.items()):
        if old not in store:
            absent += 1                       # rendered, but no approved label stored
            continue
        if new in store:
            skipped.append((old, new))        # already migrated, or independently earned
            continue
        moves.append((old, new, old in live))

    print(f"legend store : {store_path}")
    print(f"entries      : {len(store)}")
    print(f"to re-file   : {len(moves)}  ({sum(1 for m in moves if m[2])} copy, "
          f"{sum(1 for m in moves if not m[2])} move)")
    print(f"already ok   : {len(skipped)}")
    print(f"not stored   : {absent}   (key moved, but no approved label to carry)\n")

    for old, new, copy in moves:
        label = (store[old] or {}).get("label", "")
        print(f"  {'COPY' if copy else 'MOVE'}  {old}")
        print(f"        -> {new}")
        print(f"        label: {label!r}")
        if copy:
            print("        (old key still serves a phrase-only slot, so it stays)")
        print()
    for old, new in skipped:
        print(f"  SKIP (target exists) {old} -> {new}")

    if not apply:
        print("\ndry run. re-run with --apply to write.")
        return 0
    if not moves:
        print("nothing to do.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = store_path.with_suffix(f".{stamp}.bak.json")
    shutil.copy2(store_path, backup)
    for old, new, copy in moves:
        store[new] = dict(store[old]) if copy else store.pop(old)
    store_path.write_text(json.dumps(store, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    print(f"\nbackup : {backup}")
    print(f"wrote  : {store_path}  ({len(store)} entries, {len(moves)} re-filed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
