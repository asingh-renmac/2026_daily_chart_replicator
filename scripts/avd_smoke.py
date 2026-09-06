"""Verify the deployed code on the host WITHOUT touching DLX.

`selftest.py` needs a live Haver session, which only madz's account has, so it cannot run
over SSH. Most of what the 15 work changed does not need one: the phrase parser is pure
string handling, and the chat store is pure JSON. Checking those here means the RDP step
is left with only the part that genuinely requires DLX, instead of being the first time
anyone finds out whether the new files import at all.

`bootstrap` is imported first and deliberately: run standalone the vendored style package
is not on the path, and every import below fails with a misleading ModuleNotFoundError.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import haver_chart.bootstrap  # noqa: F401,E402  puts vendored packages on the path
import build_chart as BC      # noqa: E402
import haver_chart.chat_store as CHAT  # noqa: E402

cases = json.loads((ROOT / "fixtures" / "transform_phrases.json").read_text(encoding="utf-8"))
cases = cases.get("cases", cases)
if isinstance(cases, dict):
    cases = [dict(v, phrase=k) for k, v in cases.items() if not k.startswith("_")]

passed = failed = 0
for case in cases:
    phrase = case.get("phrase") or case.get("input") or ""
    want = case.get("expect")
    kwargs = {"freq": case["freq"]} if case.get("freq") else {}
    try:
        got, err = BC.phrase_to_haver(phrase, "X", **kwargs), None
    except Exception as exc:
        got, err = None, exc
    if case.get("raises"):
        ok = err is not None
    else:
        ok = err is None and got == want
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL {phrase!r}\n       want={want!r} raises={bool(case.get('raises'))}"
              f"\n       got ={got!r} err={err!r}")

print(f"transform parser : {passed} passed, {failed} failed  (of {len(cases)})")

store = CHAT.describe()
print(f"chat store       : operator={store['operator']} entries={store['entries']}")
print(f"                   {store['path']}")
print(f"                   exists={store['exists']}")
print(f"RESULT           : {'OK' if failed == 0 else 'FAILURES ABOVE'}")
sys.exit(1 if failed else 0)
