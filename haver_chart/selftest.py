"""Pre-flight check for the haver-chart MCP — run this BEFORE touching Claude config.

Checks the environment, the §13.5 import boundary and the §13.6 read-only store rule,
then does one live resolve and one live render. If this passes and Claude Desktop still
does not see the tool, the problem is the config file or the restart — not the server.

    C:/Users/asingh/envs/shared-3.10/Scripts/python.exe haver_chart/selftest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Modules the chat lane must never pull in (§13.5). ingest/classify are the email
# front end, ledger/teams/approval the Teams round-trip, propose the Opus drafting —
# all replaced by the conversation. Importing one would drag Graph credentials and
# ledger state into a lane that is supposed to be stateless.
FORBIDDEN = ("ingest", "classify", "ledger", "teams", "teams_auth", "approval",
             "propose", "chartspec", "folder_ingest", "extract_assets")

_RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, what: str, detail: str = "") -> None:
    _RESULTS.append((bool(ok), what, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {what}" + (f"  — {detail}" if detail else ""))


print("=" * 78)
print("haver-chart MCP — pre-flight selftest")
print("=" * 78)

print(f"\n1. Interpreter\n  {sys.executable}")
for mod in ("fastmcp", "matplotlib", "pandas", "Haver"):
    try:
        __import__(mod)
        check(True, f"{mod} importable")
    except Exception as exc:
        check(False, f"{mod} importable", f"{type(exc).__name__}: {exc}")

print("\n2. Import boundary (§13.5)")
from haver_chart import lane  # noqa: E402

leaked = sorted(m for m in FORBIDDEN if m in sys.modules)
check(not leaked, "chat lane imports none of the daily-lane-only modules",
      f"leaked: {leaked}" if leaked else "ingest/classify/ledger/teams/approval/propose absent")

print("\n3. Learning stores are READ-ONLY (§13.6)")
import resolve as R  # noqa: E402

for reader in ("load_legend", "load_learned", "load_trusted", "load_clarified"):
    try:
        getattr(R, reader)()
        check(True, f"{reader}() readable")
    except Exception as exc:
        check(False, f"{reader}() readable", f"{type(exc).__name__}: {exc}")

from haver_chart.bootstrap import StoreWriteAttempted  # noqa: E402

for saver in ("save_legend", "save_learned", "save_trusted", "save_clarified"):
    try:
        getattr(R, saver)({})
        check(False, f"{saver}() is sealed", "it did NOT raise — the store is writable")
    except StoreWriteAttempted:
        check(True, f"{saver}() is sealed")
    except Exception as exc:
        check(False, f"{saver}() is sealed", f"raised {type(exc).__name__} instead")

print("\n4. Live resolve (catalog + DLX)")
try:
    r = lane.resolve_one(base_descriptor="Philly Fed Mfg Business Outlook: Current "
                                         "Activity Diffusion Index",
                         applied_transform="Z-Score", sa_hint="sa")
    check(r["status"] == "resolved" and r["resolved"] == "bocgx@surveys",
          "resolve_series binds the Philly Fed diffusion index",
          f"{r['status']} -> {r['resolved']} (exact={r['exact_token_match']})")
    check(bool(r["candidates"]), "candidates returned with scores",
          f"{len(r['candidates'])} candidate(s)")
except Exception as exc:
    check(False, "resolve_series live call", f"{type(exc).__name__}: {exc}")
    r = None

print("\n5. Live render")
if r and r["status"] == "resolved":
    try:
        out = lane.render([dict(r["slot"], proposed_legend="Philly Fed Mfg Current "
                                                           "Activity (SA, z-score)")],
                          filename="selftest", title="haver-chart selftest",
                          subtitle="Z-score", st_force=True, sample_start="2000")
        png = Path(out["path"])
        check(png.exists() and png.stat().st_size > 10_000,
              "render_chart wrote a PNG", f"{png} ({png.stat().st_size // 1024} KB)")
        check(str(png).replace("\\", "/").find("/outputs/chat/") > 0,
              "render landed in the chat lane's own namespace")
    except Exception as exc:
        check(False, "render_chart live call", f"{type(exc).__name__}: {exc}")
else:
    check(False, "render_chart live call", "skipped — resolve did not bind")

print("\n6. Guardrails still raise (§13.7)")
try:
    lane.render([{"resolved": "BOCGX@SURVEYS", "applied_transform": "flurgle transform",
                  "proposed_legend": "x"}], filename="_never")
    check(False, "an unmapped transform raises", "it rendered instead")
except Exception as exc:
    check("flurgle" in str(exc).lower() or "transform" in str(exc).lower(),
          "an unmapped transform raises", f"{type(exc).__name__}")
try:
    lane.render([{"applied_transform": "Z-Score", "proposed_legend": "x"}],
                filename="_never")
    check(False, "a slot with no ticker raises", "it rendered instead")
except ValueError as exc:
    check("resolve_series" in str(exc), "a slot with no ticker raises")

n_bad = sum(1 for ok, _, _ in _RESULTS if not ok)
print("\n" + "=" * 78)
print(f"{len(_RESULTS) - n_bad}/{len(_RESULTS)} checks passed"
      + ("" if not n_bad else f"  — {n_bad} FAILED"))
print("=" * 78)
raise SystemExit(1 if n_bad else 0)
