"""Seed the ratified store from the DLX-vetted series book.

econ_commentary/config/haver_series holds 229 headline US series, each located in the
catalogue, confirmed with get_series, then confirmed again against live DLX. Measured
2026-09-17, retrieval finds 162 of them in its top 30 lexically and 121 by vector -- 190
by either, so 39 are unreachable by any retriever we have, and that is with the CURATOR's
full phrasing ("Real GDP, chained 2017 dollars"), which is more specific than anyone
types. Labor market and inflation, the two domains this desk uses most, are the worst at
59% and 62%.

`learned_lookup` is consulted BEFORE any search, so an entry here stops a series depending
on retrieval at all. That is not a shortcut past the guards: a store hit is still
re-confirmed against DLX and still runs `_meta_reject`, so this widens what reaches the
checks, never what escapes them.

Two keys per series, because the store is looked up by whatever the CALLER happened to
call the series:

  name       "Nonfarm payrolls, total (establishment survey level)"  -- the desk's phrasing
  descriptor "All Employees: Total Nonfarm (SA, Thous)"              -- the catalogue's

The catalogue wording would usually be found by search anyway; the desk's wording is the
one that parks. Both are cheap, and which one a chart legend resembles is not knowable here.

`adjustment` is carried from the book's `sa` field because `_adj_compatible` rejects only
an entry that CONTRADICTS the request -- so recording it is what stops an SA series
answering a request that explicitly asked for NSA. Omitting it would be silently laxer.

NEVER overwrites an existing entry that disagrees. The 102 entries already there were
ratified by a human against a real chart; a bulk import is weaker evidence than that, and
a disagreement is a thing to look at rather than resolve by import order.

    python scripts/seed_store_from_series_book.py             # dry run
    python scripts/seed_store_from_series_book.py --apply
"""
from __future__ import annotations

import argparse
import glob
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import resolve as R  # noqa: E402

BOOK = Path("C:/Users/asingh/new_work/econ_commentary/config/haver_series")


def load_book() -> list[dict]:
    out = []
    for f in sorted(glob.glob(str(BOOK / "*.json"))):
        doc = json.load(open(f, encoding="utf-8"))
        dom = doc["_meta"].get("domain", Path(f).stem)
        for e in doc["series"]:
            out.append({**e, "_domain": dom, "_file": Path(f).name})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    store_path = R.CLARIFIED_DIR / R._LEARNED_FILE
    store = json.loads(store_path.read_text(encoding="utf-8")) if store_path.exists() else {}
    before = len(store)

    book = load_book()
    now = datetime.now(timezone.utc).isoformat()
    stats = Counter()
    conflicts: list[str] = []

    # Descriptors that name more than one series, computed across the WHOLE book before
    # anything is written. CH@USECON (quarterly) and CBHM@USECON (monthly) share the
    # descriptor "Real Personal Consumption Expenditures (SAAR, Bil.Chn.2017$)"
    # byte-for-byte; only the book's own `name` separates them. Seeding that key would
    # bind whichever file happened to load first and be wrong half the time -- and it
    # would look deliberate, because a store hit reads as a ratified human answer.
    #
    # The store already applies this rule on lookup: `learned_lookup` refuses a loose key
    # that identifies two codes, because an ambiguous memory is not a licence to pick one.
    # Writing has to honour it too, or the refusal never gets the chance to fire.
    #
    # One case in 229 today. The check stays because the book grows and the next collision
    # will not announce itself.
    desc_owners: dict[str, set] = {}
    for e in book:
        k = R._norm_key(e.get("descriptor") or "")
        if k:
            desc_owners.setdefault(k, set()).add((e.get("ticker") or "").strip().lower())
    ambiguous = {k for k, v in desc_owners.items() if len(v) > 1}

    for e in book:
        code = (e.get("ticker") or "").strip()
        descriptor = (e.get("descriptor") or "").strip()
        if not code:
            stats["skipped (no ticker)"] += 1
            continue

        keys = []
        for text in (e.get("name"), descriptor):
            k = R._norm_key(text or "")
            if not k or k in keys:
                continue
            if k in ambiguous:
                # The name still gets seeded; only the shared descriptor is dropped, so
                # the series stays reachable by the one phrasing that identifies it.
                stats["descriptor key skipped (ambiguous)"] += 1
                continue
            keys.append(k)

        for k in keys:
            existing = store.get(k)
            if isinstance(existing, dict) and existing.get("code"):
                if existing["code"].strip().lower() == code.lower():
                    stats["already present, agrees"] += 1
                else:
                    # Ratified by a human against a real chart. Louder than an import.
                    stats["CONFLICT, left alone"] += 1
                    conflicts.append(f"{k[:44]!r}\n       store={existing['code']}  "
                                     f"book={code}  ({e['_domain']})")
                continue
            store[k] = {"code": code, "descriptor": descriptor,
                        "adjustment": R._sa_norm(e.get("sa") or ""),
                        "added": now,
                        "source": f"series_book:{e['_file']}"}
            stats["added"] += 1

    for k, v in sorted(stats.items()):
        print(f"  {k:26} {v}")
    print(f"\n  store entries: {before} -> {len(store)}")
    if conflicts:
        print(f"\n  {len(conflicts)} CONFLICT(S) -- left as they were, look at these:")
        for c in conflicts[:10]:
            print(f"     {c}")

    if not args.apply:
        print("\n  DRY RUN -- nothing written. Re-run with --apply.")
        return 0

    backup = store_path.with_suffix(f".bak-{datetime.now():%Y%m%d-%H%M%S}.json")
    shutil.copy2(store_path, backup)
    store_path.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  backup : {backup.name}")
    print(f"  written: {store_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
