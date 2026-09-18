"""Point "weekly initial claims" at the WEEKLY series, and keep the monthly reachable.

CFNAI's table reads "Weekly Initial Claims For Unemployment Insurance, SA, Thousands" and
maps it to LICM@USECON. That is right FOR CFNAI -- a monthly index needs the monthly
weekly-average -- and wrong for this desk, where "weekly initial claims" means the weekly
series. The catalogue makes the difference plain:

    licm@usecon  Initial Claims ..., Wkly Avg (SA, Thous)          frequency M
    lic@weekly   Unemployment Insurance: Initial Claims ... (SA)   frequency W

So the ingest imported the source's FREQUENCY CONVENTION along with its mapping, and the
description kept wording that now points the wrong way. This corrects that one entry and,
rather than simply discarding the monthly, re-files it under its own catalogue descriptor
so both remain reachable by the words that actually distinguish them.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import haver_search as HS  # noqa: E402
import resolve as R  # noqa: E402

APPLY = "--apply" in sys.argv
path = R.CLARIFIED_DIR / R._LEARNED_FILE
store = json.loads(path.read_text(encoding="utf-8"))
now = datetime.now(timezone.utc).isoformat()

KEY = R._norm_key("Weekly Initial Claims For Unemployment Insurance, SA, Thousands")
before = store.get(KEY)
print(f"key   : {KEY!r}")
print(f"was   : {before}\n")

weekly = HS.get_meta("lic@weekly")
monthly = HS.get_meta("licm@usecon")
if not weekly or not monthly:
    print("ERROR: could not read both series from the catalogue; nothing changed.")
    raise SystemExit(2)

store[KEY] = {"code": "lic@weekly", "descriptor": weekly["descriptor"],
              "adjustment": R._sa_norm(weekly.get("sa_status") or ""),
              "added": now,
              "source": "indicator_list:CFNAI (frequency corrected by hand)"}
print(f"now   : {store[KEY]}\n")

# The monthly is a real series somebody may still want; file it under the wording that
# actually says so, rather than leaving it only under a description that says "weekly".
mkey = R._norm_key(monthly["descriptor"])
if mkey in store:
    print(f"monthly already filed under {mkey[:56]!r}")
else:
    store[mkey] = {"code": "licm@usecon", "descriptor": monthly["descriptor"],
                   "adjustment": R._sa_norm(monthly.get("sa_status") or ""),
                   "added": now, "source": "indicator_list:CFNAI"}
    print(f"monthly re-filed under {mkey[:60]!r}")

if not APPLY:
    print("\nDRY RUN -- nothing written. Re-run with --apply.")
    raise SystemExit(0)

backup = path.with_suffix(f".bak-{datetime.now():%Y%m%d-%H%M%S}.json")
shutil.copy2(path, backup)
path.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nbackup : {backup.name}\nwritten: {path}  ({len(store)} entries)")
