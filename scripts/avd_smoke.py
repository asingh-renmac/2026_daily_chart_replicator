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

# A pull can add a dependency, and nothing upstream of here notices: the lane imports
# statsmodels lazily, so a host short of it starts fine and serves fine until the first
# sa() formula. Checking distribution names rather than import names sidesteps the
# Haver/psycopg[binary]/python-dotenv naming mismatches for free.
import importlib.metadata as _md  # noqa: E402
import re as _re                  # noqa: E402

missing = []
for line in (ROOT / "haver_chart" / "requirements.txt").read_text(encoding="utf-8").splitlines():
    line = line.split("#")[0].strip()
    if not line:
        continue
    name, _, want = line.partition("==")
    name = _re.sub(r"\[.*\]", "", name).strip()
    try:
        have = _md.version(name)
    except _md.PackageNotFoundError:
        missing.append(f"{name} MISSING (want {want})")
        continue
    if want and have != want:
        missing.append(f"{name} {have} (want {want})")

if missing:
    failed += 1
    print("dependencies     : " + str(len(missing)) + " problem(s)")
    for m in missing:
        print(f"  {m}")
    print('  fix: & "C:\\Users\\madz\\envs\\haver-chart\\Scripts\\python.exe" -m pip '
          "install -r haver_chart\\requirements.txt")
else:
    print("dependencies     : all pinned versions present")

# The X-13 binary is the half pip cannot supply. Pure filesystem probe, so it reports
# honestly even when statsmodels is the thing that is missing.
import os as _os  # noqa: E402

_cands = [_os.environ.get("X13PATH")] + [
    r"C:\Users\asingh\tools\winx13\x13as", r"C:\Users\madz\Work\asingh\tools\winx13\x13as"]
_x13 = next((c for c in _cands if c and (Path(c) / "x13as.exe").exists()), None)
print(f"X-13 binary      : {_x13 or 'NOT FOUND -- run scripts/avd_install_x13.ps1 -Apply'}")

store = CHAT.describe()
print(f"chat store       : operator={store['operator']} entries={store['entries']}")
print(f"                   {store['path']}")
print(f"                   exists={store['exists']}")
print(f"RESULT           : {'OK' if failed == 0 else 'FAILURES ABOVE'}")
sys.exit(1 if failed else 0)
