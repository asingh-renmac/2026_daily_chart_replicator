"""Entries whose DESCRIPTION implies a frequency the TICKER does not have.

Prompted by weekly initial claims. CFNAI's table reads "Weekly Initial Claims For
Unemployment Insurance, SA, Thousands" and maps it to LICM@USECON -- whose catalogue
descriptor is "Initial Claims ..., Wkly Avg (SA, Thous)" at MONTHLY frequency. CFNAI is a
monthly index, so the monthly weekly-average is the right series FOR THEM. It is not what
this desk means when it says weekly claims, which is LIC@WEEKLY.

The general risk: ingesting a published list imports that institution's frequency
conventions along with its mappings, and the description keeps wording that now points the
wrong way. This finds every entry where the two disagree, so they can be judged by eye.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import haver_search as HS  # noqa: E402
import resolve as R  # noqa: E402

WORD_FREQ = [
    (re.compile(r"\bweekly\b", re.I), "W"),
    (re.compile(r"\bmonthly\b", re.I), "M"),
    (re.compile(r"\bquarterly\b", re.I), "Q"),
    (re.compile(r"\bannual(ly)?\b", re.I), "A"),
]

store = json.loads((R.CLARIFIED_DIR / R._LEARNED_FILE).read_text(encoding="utf-8"))
ingested = {k: v for k, v in store.items()
            if isinstance(v, dict) and str(v.get("source", "")).startswith("indicator_list")}
print(f"{len(ingested)} entries from the indicator lists\n")

flagged = []
for key, ent in ingested.items():
    implied = next((f for rx, f in WORD_FREQ if rx.search(key)), None)
    if not implied:
        continue
    meta = HS.get_meta(ent["code"]) or {}
    actual = (meta.get("frequency") or "").upper()
    # SAAR is an annualisation, not a frequency claim, so "annual" alone is not evidence.
    if actual and actual != implied and not (implied == "A" and "saar" in key.lower()):
        flagged.append((key, ent["code"], implied, actual, meta.get("descriptor", "")))

print(f"{len(flagged)} description/frequency disagreement(s):\n")
for key, code, implied, actual, desc in flagged:
    print(f"  says {implied}, series is {actual}")
    print(f"    key   : {key[:72]}")
    print(f"    ticker: {code}  {desc[:56]}")
    print()
