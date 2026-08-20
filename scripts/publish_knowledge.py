"""Publish the ratified knowledge stores to the shared P: drive (G9e step 3).

Teammates point `CLARIFIED_KNOWLEDGE_DIR` at the published folder and inherit every
bind and legend label the DAILY lane has approved. They cannot write back: the chat
lane seals `resolve.save_*` (plan.md §13.6), so a shared copy can go stale but can
never diverge.

Why a publish step instead of pointing the daily lane straight at P:
  * `resolve.save_legend` does an unguarded read-modify-write with no atomic rename.
    Over a network share a reader can catch a truncated file mid-write; `_read_json`
    swallows that and returns {}, which silently costs every legend label for that
    call. Publishing a finished file avoids the window entirely.
  * A write failure inside `save_*` would raise in the middle of a Teams approval.
    The daily lane should not gain a network dependency on its approval path.

Why an ALLOWLIST instead of copying the folder:
  `knowledge_repo` also holds the post-ship commentary notes, which are personal and
  must not be shared. Naming the four files means a new file appearing beside them
  can never be published by accident.

    python scripts/publish_knowledge.py            # publish to the default share
    python scripts/publish_knowledge.py --dry-run  # show what would happen
    python scripts/publish_knowledge.py --dest "P:/Public/Some_Other_Folder"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import resolve as R  # noqa: E402

DEFAULT_DEST = Path("P:/Public/RenMac_Chart_Knowledge")

# The ONLY files that may be published. Everything else in the source folder stays
# private, by construction rather than by care.
STORES = ("learned_descriptors.json", "legend_labels.json",
          "trusted_tickers.json", "native_ma.json")

README = """RenMac chart knowledge — published stores
=========================================

Four JSON files that let the `haver-chart` Claude Desktop tool resolve Haver series
the way the production daily pipeline does.

  learned_descriptors.json  a series description -> its approved code@database
  legend_labels.json        a ticker (or formula) -> its ratified chart legend
  trusted_tickers.json      a bare mnemonic -> its code@database
  native_ma.json            series whose NAME already contains a moving average

HOW TO USE
  Set the environment variable CLARIFIED_KNOWLEDGE_DIR to this folder:

    setx CLARIFIED_KNOWLEDGE_DIR "{dest}"

  Then restart Claude Desktop (quit from the TRAY ICON, not just the window).

DO NOT EDIT THESE FILES BY HAND
  They are republished from the daily pipeline and any hand edit is overwritten.
  The chat tool cannot write to them: a chart binding made in a chat is approved by
  eye, not through the Teams approval workflow, so it must never become something
  the daily pipeline trusts.

  If a series resolves wrongly, say so in the chat and pick the right candidate.
  The fix belongs in the daily pipeline, not in this folder.

Published {when} from {src}
by scripts/publish_knowledge.py in 2026_daily_chart_replicator.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ap.add_argument("--src", default=str(R.CLARIFIED_DIR))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src, dest = Path(args.src), Path(args.dest)
    print(f"source : {src}")
    print(f"dest   : {dest}")
    print(f"mode   : {'DRY RUN' if args.dry_run else 'publish'}\n")

    if not src.is_dir():
        print(f"ERROR: source folder does not exist: {src}")
        return 2

    # Validate BEFORE touching the destination: publishing a corrupt store would
    # break every teammate at once, and the reader swallows JSON errors as {}.
    payload = {}
    for name in STORES:
        p = src / name
        if not p.exists():
            print(f"ERROR: missing store {name}")
            return 2
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"ERROR: {name} is not valid JSON ({exc}) — nothing published")
            return 2
        if not isinstance(data, dict):
            print(f"ERROR: {name} is not a JSON object — nothing published")
            return 2
        payload[name] = (p, len(data))
        print(f"  ok  {name:<28} {len(data):>4} entries  {p.stat().st_size:>7,} bytes")

    skipped = sorted(q.name for q in src.iterdir() if q.name not in STORES)
    if skipped:
        print(f"\n  not published (not on the allowlist): {', '.join(skipped)}")

    if args.dry_run:
        print("\nDRY RUN — nothing written.")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    for name, (p, _n) in payload.items():
        # temp-then-replace so a reader on the share never sees a half-written file
        tmp = dest / f".{name}.tmp"
        shutil.copyfile(p, tmp)
        os.replace(tmp, dest / name)
    (dest / "README.txt").write_text(
        README.format(dest=str(dest).replace("/", "\\"),
                      when=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                      src=src), encoding="utf-8")

    print(f"\nPublished {len(payload)} store(s) + README.txt to {dest}")
    print("Teammates: set CLARIFIED_KNOWLEDGE_DIR to that folder, then restart Claude.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
