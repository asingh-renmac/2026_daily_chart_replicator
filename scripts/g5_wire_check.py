"""
g5_wire_check.py — prove the two LIVE callouts are off their stubs.

  * confirm_ticker → Haver DLX: a real code resolves, a bogus/malformed one does not.
  * propose_titles → Opus: real title/subtitle options come back in the right register.

Only the Graph transport remains stubbed after this (waiting on the chat/scope
decision). Run:  python scripts/g5_wire_check.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import resolve as R       # noqa: E402
import propose as P       # noqa: E402


def main():
    print("== confirm_ticker (Haver DLX) ==")
    cases = [
        ("pa413121@usecon", True),   # the resolve-by-definition PPI
        ("LSWP@USECON", True),       # ECI wages (G4)
        ("bogus@nope", False),       # well-formed but nonexistent
        ("not-a-ticker", False),     # malformed
    ]
    ok = True
    for code, expect in cases:
        got = R.confirm_ticker(code)
        flag = "ok" if got == expect else "FAIL"
        ok = ok and got == expect
        print(f"  [{flag}] confirm_ticker({code!r}) = {got} (expected {expect})")

    print("\n== propose_titles (Opus) ==")
    opts = P.propose_titles(
        subject="Core capital goods new orders vs shipments",
        series=["New orders", "Value of shipments"],
        transform="3-month annualized % (3-mo MA)",
        span="monthly, 1992-present", n=2)
    for i, o in enumerate(opts, 1):
        print(f"  {i}. {o['title']}  —  {o['subtitle']}")
    assert opts and all(":" not in o["title"] for o in opts), \
        "titles must be colon-free headlines"
    assert ok, "confirm_ticker cases failed"
    print("\nOK — confirm_ticker + propose_titles are live (only Graph remains stubbed).")


if __name__ == "__main__":
    main()
