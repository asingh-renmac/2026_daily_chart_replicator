"""Copy the laptop's mb_charts_store to the UNC share both runners read.

WHY THIS EXISTS
  MB_CHART_STORE is a repository variable pointing at
  \\\\10.10.10.4\\companydata$\\Econ\\asingh\\project\\mb_charts_store -- a UNC path
  deliberately chosen so the laptop and the AVD resolve the same string to the same files.
  What was never written down is how the share LEARNS about a re-export. Today nothing
  syncs it, so a store rebuilt on the laptop leaves the share on an older index and both
  runners rank against descriptions that no longer match the PNGs beside them, silently.

WHAT IT REFUSES TO DO
  It will not copy a source that does not parse. An index truncated mid-write is worse
  than a stale one: stale at least ranks correctly against what it has, whereas half a JSON
  file makes every lookup fail with no clue why. So the source is opened and counted first,
  and a mismatch between the index's chart_count and the PNGs on disk is reported before
  anything is written.

  It also records WHEN it ran, in a sync_stamp.json beside the index. Staleness that
  nothing announces is the failure this script is here to prevent; a copy that leaves no
  trace would just move the problem.

    python scripts/sync_chart_store.py              # inspect both ends, copy nothing
    python scripts/sync_chart_store.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(os.environ.get("MB_CHART_STORE_SRC",
                          r"C:\Users\asingh\new_work\mb_charts_store"))
DEST = Path(os.environ.get("MB_CHART_STORE_DEST",
                           r"\\10.10.10.4\companydata$\Econ\asingh\project\mb_charts_store"))
INDEX = "charts_index.json"


def describe(root: Path, label: str) -> dict | None:
    """Chart count, PNG count and index date for one end, or None if unusable."""
    us = root / "US"
    idx = us / INDEX
    if not root.exists():
        print(f"  {label:9} UNREACHABLE  {root}")
        return None
    if not idx.is_file():
        print(f"  {label:9} no {INDEX} under {us}")
        return None
    try:
        doc = json.loads(idx.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  {label:9} index does not parse: {type(e).__name__}: {e}")
        return None
    pngs = sum(1 for _ in us.rglob("*.png"))
    info = {"root": root, "charts": len(doc.get("charts") or []),
            "declared": doc.get("chart_count"), "generated": doc.get("generated"),
            "pngs": pngs}
    print(f"  {label:9} {info['charts']:5} charts  {pngs:5} pngs  "
          f"generated {info['generated']}")
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    print("source and destination:\n")
    src = describe(SRC, "laptop")
    dst = describe(DEST, "share")
    if not src:
        print("\n  Source unusable. Nothing copied -- a half-written index would be worse\n"
              "  than a stale one, because every lookup would fail with no clue why.")
        return 2

    # The index counts itself; disagreeing with the PNGs beside it means a partial export.
    if src["declared"] is not None and src["declared"] != src["charts"]:
        print(f"\n  WARNING: index declares {src['declared']} charts but lists "
              f"{src['charts']}.")
    if src["pngs"] < src["charts"]:
        print(f"\n  ABORT: {src['charts']} charts indexed but only {src['pngs']} PNGs on "
              "disk.\n  That is a partial export; fix the source before syncing.")
        return 3

    if dst:
        if dst["generated"] == src["generated"]:
            print(f"\n  The share already holds this export ({src['generated']}).")
        else:
            print(f"\n  share is at {dst['generated']}, laptop at {src['generated']}")

    if not args.apply:
        print("\n  DRY RUN -- nothing copied. Re-run with --apply.")
        return 0

    dest_us = DEST / "US"
    print(f"\n  copying {src['pngs']} files to {dest_us} ...", flush=True)
    DEST.mkdir(parents=True, exist_ok=True)
    # dirs_exist_ok rather than delete-then-copy: a wipe that fails halfway leaves the
    # share with nothing, and the readers have no fallback.
    shutil.copytree(SRC / "US", dest_us, dirs_exist_ok=True)

    stamp = {"synced": datetime.now(timezone.utc).isoformat(),
             "from": str(SRC), "index_generated": src["generated"],
             "charts": src["charts"], "pngs": src["pngs"]}
    (dest_us / "sync_stamp.json").write_text(json.dumps(stamp, indent=2), encoding="utf-8")

    after = describe(DEST, "share")
    ok = bool(after) and after["charts"] == src["charts"]
    print(f"\n  {'OK' if ok else 'MISMATCH — check the share'}: "
          f"{(after or {}).get('charts')} charts now on the share")
    print(f"  stamp: {dest_us / 'sync_stamp.json'}")
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
