"""Fail when the vendored X-13 wrapper has drifted from the econ-templates original.

`src/x13_seasonal_adjust.py` is a VERBATIM copy of econ-templates/sa/x13_seasonal_adjust.py.
It is vendored rather than imported because the AVD host cannot clone econ-templates: that
repo's remote is SSH-only and the host cannot complete an SSH handshake to GitHub (see the
header of scripts/avd_deploy.ps1). A copy that silently diverges from the canonical wrapper
is the whole risk of vendoring, so this makes the divergence loud.

The canonical file is absent on the AVD, which is not a failure — there is simply nothing to
compare against there. Run this on a machine that has both.

    python scripts/check_x13_vendor.py
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

VENDORED = Path(__file__).resolve().parents[1] / "src" / "x13_seasonal_adjust.py"
CANONICAL = Path(r"C:\Users\asingh\new_work\econ-templates\sa\x13_seasonal_adjust.py")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    if not VENDORED.exists():
        print(f"FAIL  vendored copy missing: {VENDORED}")
        return 1
    if not CANONICAL.exists():
        print(f"SKIP  canonical not on this machine: {CANONICAL}")
        return 0

    a, b = _sha(VENDORED), _sha(CANONICAL)
    if a == b:
        print(f"OK    vendored copy matches econ-templates ({a[:12]})")
        return 0

    print("FAIL  vendored X-13 wrapper has drifted from econ-templates")
    print(f"      vendored : {a}\n      canonical: {b}")
    print(f"\n      Reconcile, then re-copy:\n"
          f"        cp '{CANONICAL}' '{VENDORED}'")
    return 1


if __name__ == "__main__":
    sys.exit(main())
