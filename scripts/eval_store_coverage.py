"""Against the alias eval set: how many does the STORE answer, and does it agree?

The eval set records what retrieval managed (`retrieval_found`). This asks the question the
store work was actually for: with 914 ratified entries, how many of these aliases never
need to reach a retriever at all -- and where the store does answer, is it the answer the
human chose?

A disagreement here is the finding that matters. The store is consulted BEFORE search and
its answers read as human-confirmed, so a wrong entry is worse than a missing one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import resolve as R  # noqa: E402

pairs = json.loads((ROOT / "eval" / "alias_eval_set.json").read_text(encoding="utf-8"))["pairs"]
store = json.loads((R.CLARIFIED_DIR / R._LEARNED_FILE).read_text(encoding="utf-8"))
print(f"{len(pairs)} labelled aliases, store has {len(store)} entries\n")

hit = miss = agree = disagree = 0
rows = []
for e in pairs:
    if e["expected_outcome"] != "bind":
        continue
    want = {t.lower() for t in (e.get("acceptable_tickers") or [])}
    got = R.learned_lookup(store, e["query"], e.get("adjustment") or "")
    code = ((got or {}).get("code") or "").lower()
    if not code:
        miss += 1
        rows.append(("miss ", e["query"], "-", sorted(want)[:1]))
        continue
    hit += 1
    if code in want:
        agree += 1
    else:
        disagree += 1
        rows.append(("WRONG", e["query"], code, sorted(want)[:1]))

n = hit + miss
print(f"  store answered      : {hit}/{n} ({hit / n:.0%})")
print(f"     agreed with human: {agree}")
print(f"     DISAGREED        : {disagree}")
print(f"  store silent        : {miss}/{n} ({miss / n:.0%})\n")

retr = sum(1 for e in pairs if e["expected_outcome"] == "bind" and e.get("retrieval_found"))
print(f"  for comparison, retrieval had it in its pool for {retr}/{n} ({retr / n:.0%})\n")

for tag, q, code, want in rows:
    print(f"  [{tag}] {q[:38]:38} store={code:18} human={want}")
