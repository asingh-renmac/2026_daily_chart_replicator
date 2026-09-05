"""Pre-flight check for the haver-chart MCP — run this BEFORE touching Claude config.

Checks the environment, the §13.5 import boundary and the §13.6 read-only store rule,
then does one live resolve and one live render. If this passes and Claude Desktop still
does not see the tool, the problem is the config file or the restart — not the server.

    C:/Users/asingh/envs/shared-3.10/Scripts/python.exe haver_chart/selftest.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Must precede section 2, which reports on the environment this import establishes.
# Importing it here rather than letting `lane` pull it in later is the difference
# between the selftest checking the real configuration and checking a bare shell.
from haver_chart import bootstrap  # noqa: E402

# Modules the chat lane must never pull in (§13.5). ingest/classify are the email
# front end, ledger/teams/approval the Teams round-trip, propose the Opus drafting —
# all replaced by the conversation. Importing one would drag Graph credentials and
# ledger state into a lane that is supposed to be stateless.
FORBIDDEN = ("ingest", "classify", "ledger", "teams", "teams_auth", "approval",
             "propose", "chartspec", "folder_ingest", "extract_assets")

_RESULTS: list[tuple[bool, str, str]] = []
_render_out: dict | None = None            # set by section 6, consumed by section 10


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

print("\n2. External paths (relocatable per G9e step 1)")
# Three dependencies live outside this repo. Resolve them BEFORE the live calls so a
# misconfigured machine fails here, with the variable to set, rather than at the first
# render. Only the style module fails hard; the other two degrade, so they are reported
# as their real consequence instead of as a pass/fail.
import os  # noqa: E402

for var, default, consequence in (
    ("ECON_TEMPLATES_CHARTS", "C:/Users/asingh/new_work/econ-templates/charts",
     "HARD — render.py cannot import renmac_chart_style and the lane will not start"),
    ("HAVER_MCP_SERVER", "C:/Users/asingh/new_work/2026_haver_mcp/server",
     "degrades — catalog search goes dark and every description-only series parks"),
    ("CLARIFIED_KNOWLEDGE_DIR",
     "C:/Users/asingh/new_work/knowledge_repo/clarified-knowledge",
     "degrades — no learned binds or ratified legends; label every series explicitly"),
):
    path = Path(os.environ.get(var, default))
    if var in bootstrap.ADOPTED_FROM_PACKAGE:
        source = "package"          # vendored in the zip and found without being told
    elif os.environ.get(var):
        source = "env"
    else:
        source = "default"
    if var == "ECON_TEMPLATES_CHARTS":
        check(path.is_dir(), f"{var} resolves ({source})",
              str(path) if path.is_dir() else f"MISSING {path} -> {consequence}")
    elif not path.is_dir():
        print(f"  [WARN] {var} ({source}) missing: {path}\n         {consequence}")
    else:
        check(True, f"{var} resolves ({source})", str(path))

print("\n3. Import boundary (§13.5)")
from haver_chart import lane  # noqa: E402

leaked = sorted(m for m in FORBIDDEN if m in sys.modules)
check(not leaked, "chat lane imports none of the daily-lane-only modules",
      f"leaked: {leaked}" if leaked else "ingest/classify/ledger/teams/approval/propose absent")

print("\n4. Learning stores are READ-ONLY (§13.6)")
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

print("\n5. Live resolve (catalog + DLX)")
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

print("\n6. Live render")
if r and r["status"] == "resolved":
    try:
        out = lane.render([dict(r["slot"], proposed_legend="Philly Fed Mfg Current "
                                                           "Activity (SA, z-score)")],
                          filename="selftest", title="haver-chart selftest",
                          subtitle="Z-score", st_force=True, sample_start="2000")
        png = Path(out["path"])
        _render_out = out                  # section 10 resolves this exact file by id
        check(png.exists() and png.stat().st_size > 10_000,
              "render_chart wrote a PNG", f"{png} ({png.stat().st_size // 1024} KB)")
        check(str(png).replace("\\", "/").find("/outputs/chat/") > 0,
              "render landed in the chat lane's own namespace")
    except Exception as exc:
        check(False, "render_chart live call", f"{type(exc).__name__}: {exc}")
else:
    check(False, "render_chart live call", "skipped — resolve did not bind")

print("\n7. Guardrails still raise (§13.7)")
try:
    lane.render([{"resolved": "BOCGX@SURVEYS", "applied_transform": "flurgle transform",
                  "proposed_legend": "x"}], filename="_never")
    check(False, "an unmapped transform raises", "it rendered instead")
except Exception as exc:
    check("flurgle" in str(exc).lower() or "transform" in str(exc).lower(),
          "an unmapped transform raises", f"{type(exc).__name__}")
    # The raise names what it REJECTED; the operator also needs what is accepted, and
    # not the shared message's instruction to go edit the mapper.
    told = lane.explain(exc)
    check("Z-Score" in told, "the rejection tells the operator the accepted wordings")
    check("extend build_chart" not in told,
          "the developer hint is stripped from the operator's message")
try:
    lane.render([{"applied_transform": "Z-Score", "proposed_legend": "x"}],
                filename="_never")
    check(False, "a slot with no ticker raises", "it rendered instead")
except ValueError as exc:
    check("resolve_series" in str(exc), "a slot with no ticker raises")

print("\n8. Operator-facing contract")
# The advertised wordings are a hand-kept list (the mapper is branch logic, not a
# table), so assert each one still maps. Without this the list rots silently and the
# error message starts recommending phrases the renderer rejects.
unmapped = []
for phrase in lane.TRANSFORM_PHRASES:
    try:
        lane.BC.phrase_to_haver(phrase, "X")
    except Exception as exc:
        unmapped.append(f"{phrase!r} -> {type(exc).__name__}")
check(not unmapped, "every advertised transform wording still maps",
      "; ".join(unmapped) if unmapped else f"{len(lane.TRANSFORM_PHRASES)} wordings")

# The compositional parser (§15.1b) against its fixture (§15.1c). The fixture holds
# every applied_transform the daily lane has ever rendered — flat ones pinned to the
# output the pre-parser mapper gave (the G19a no-drift proof) and compound ones pinned
# to the formula that ACTUALLY SHIPPED, so the ledger judges, not the author. It also
# mirrors the phrase cases asserted in scripts/selftest_workflow.py: running only ONE
# suite is how a comma-rule regression on "moving average, 6-month" reached a green
# board once already.
_fixture = _REPO_ROOT / "fixtures" / "transform_phrases.json"
check(_fixture.exists(), "the transform-phrase fixture is present", str(_fixture))
if _fixture.exists():
    _cases = json.loads(_fixture.read_text(encoding="utf-8"))["cases"]
    _wrong, _flat = [], 0
    for _c in _cases:
        _want, _raises, _freq = _c.get("expect"), _c.get("raises", False), _c.get("freq")
        if _c.get("source") == "ledger-flat":
            _flat += 1
        try:
            _got = lane.BC.phrase_to_haver(_c["phrase"], "X", _freq)
            if _raises:
                _wrong.append(f"{_c['phrase']!r} should raise, gave {_got!r}")
            elif _got != _want:
                _wrong.append(f"{_c['phrase']!r} -> {_got!r}, want {_want!r}")
        except ValueError as _exc:
            if not _raises:
                _wrong.append(f"{_c['phrase']!r} raised: {_exc}")
            elif "'" not in str(_exc):
                # A raise that does not QUOTE the fragment it choked on sends the
                # operator back to re-guess the whole phrase.
                _wrong.append(f"{_c['phrase']!r} raised without quoting the bad part")
    check(not _wrong, "every fixture phrase maps to its pinned formula",
          "; ".join(_wrong[:3]) if _wrong else f"{len(_cases)} phrases, {_flat} of them "
          f"flat and byte-identical to the pre-§15.1b mapper")

# §15.1d: a window is stated in some UNIT but G3 counts it in periods of the SERIES.
check(lane.BC.phrase_to_haver("2-quarter annualized growth", "X", "M") == "difa%(X,6)",
      "a 2-quarter window on a monthly series becomes 6 native periods")
check(lane.BC.phrase_to_haver("3-month moving average", "X", "M") == "movv(X,3)",
      "a window already in the series' own unit is left alone")

# The exact-match tie-break (§15.2). DLX metadata is INJECTED rather than fetched, so
# these assert the decision rules and not the state of two particular Haver series —
# a live pair can be re-sourced or re-based and turn a logic test into a flake.
_BLS = {"shortsource": "BLS", "longsource": "Bureau of Labor Statistics",
        "startdate": "1939-01-31", "enddate": "2026-08-31", "numobs": 1052,
        "frequency": "M", "aggtype": "AVG", "magnitude": 3, "datatype": "Units",
        "diftype": 0, "group": "E30", "decprecision": 0, "geography1": "111",
        "datetimemod": "2026-09-04 08:30:00"}
lane.R._METADATA_CACHE.update({
    # A genuine mirror: differs ONLY on catalog bookkeeping and refresh time.
    "m@usecon": dict(_BLS), "m@labor": dict(_BLS, group="E40", enddate="2026-07-31",
                                            datetimemod="2026-09-04 08:37:00"),
    # Mirrors of one series, neither of them in usecon.
    "n@labor": dict(_BLS), "n@empl": dict(_BLS, group="E55"),
    # Two different series that share a descriptor (the 2026-09-04 slot A shape).
    "d@usecon": dict(_BLS), "d@bci": dict(_BLS, shortsource="CB",
                                          longsource="The Conference Board",
                                          startdate="1945-01-31", numobs=979),
    "gone@usecon": None, "gone@bci": None,      # DLX unreachable
})


def _tie(codes):
    return lane.R.break_exact_tie(
        [{"code": c, "descriptor": "", "sim": 1.0, "exact": True,
          "via_query": "q", "agg": "AVG"} for c in codes])


_pick, _why = _tie(["m@usecon", "m@labor"])
check(_pick is not None and _pick["code"] == "m@usecon",
      "a database mirror binds the usecon copy instead of parking",
      _why[:88] if _pick else f"PARKED: {_why[:70]}")

_pick, _why = _tie(["d@usecon", "d@bci"])
check(_pick is None, "two different series sharing a descriptor still park")
check(_pick is None and "shortsource" in _why and "979" in _why,
      "the park names the evidence that separates them", _why[:96])

_pick, _why = _tie(["n@labor", "n@empl"])
check(_pick is None and "usecon" in _why,
      "mirrors with no usecon copy park rather than invent a database order")

_pick, _why = _tie(["gone@usecon", "gone@bci"])
check(_pick is None and "unavailable" in _why,
      "unreachable DLX metadata parks — it never falls back to a guess")

# The G19f defect. An SA/NSA twin is descriptor-exact and IDENTICAL on every field DLX
# exposes — same source, same 1052 observations from 1939, same AVG, same magnitude.
# The first §15.2 assumed units would surface as `magnitude` and separate them; they do
# not. Two guards now cover it, and the same-database one does not depend on having
# found a discriminating field at all.
lane.R._METADATA_CACHE.update({
    "s@labor": dict(_BLS, descriptor="All Employees: Total Private (SA, Thous)"),
    "sa@labor": dict(_BLS, descriptor="All Employees: Total Private (NSA, Thous)"),
    "t@usecon": dict(_BLS, descriptor="All Employees: Total Private (SA, Thous)"),
    "t@labor": dict(_BLS, descriptor="All Employees: Total Private (NSA, Thous)"),
})
check(lane.R._sa_of_descriptor("All Employees: Total Private (NSA, Thous)") == "nsa"
      and lane.R._sa_of_descriptor("All Employees: Total Private (SA, Thous)") == "sa",
      "SA status is read from the descriptor's units parenthetical")

_pick, _why = _tie(["s@labor", "sa@labor"])
check(_pick is None and "SAME database" in _why,
      "two codes in ONE database are never mirrors, whatever the metadata says",
      _why[:90])

_pick, _why = _tie(["t@usecon", "t@labor"])
check(_pick is None and "seasonal_adjustment" in _why,
      "an SA/NSA pair ACROSS databases parks on seasonal adjustment, not binds usecon",
      _why[:100])


# ── §15.3 chat memory ────────────────────────────────────────────────────────────
# Redirected to a temp directory: these must never write the shared store, and a test
# that depends on what happens to be in the real one is a flake waiting to happen.
_CHAT = lane.CHAT
_real_dir = lane.R.CLARIFIED_DIR
_tmp_dir = Path(tempfile.mkdtemp(prefix="haver-chat-selftest-"))
try:
    lane.R.CLARIFIED_DIR = _tmp_dir
    _slug, _claims = _CHAT.operator_identity()
    check(_slug == "local" and _claims == {},
          "stdio has no token, so the operator slug falls back to 'local'", _slug)
    check(_CHAT.store_path().name == "chat_learned.local.json",
          "the store is named per operator", _CHAT.store_path().name)
    check(_CHAT.load() == {}, "a store that does not exist reads as empty memory")

    _CHAT.remember("Some Series Nobody Has", "ABC@USECON", note="selftest")
    _loaded = _CHAT.load()
    check(list(_loaded) == ["some series nobody has"],
          "the entry keys on the NORMALIZED descriptor, as resolve looks it up",
          str(list(_loaded)))
    check(_loaded["some series nobody has"]["code"] == "ABC@USECON",
          "and it is shaped like a learned_descriptors entry, so it drops into learned=")

    # G19g: a wrong answer must be revocable without hand-editing JSON on a share.
    check(_CHAT.forget("SOME SERIES nobody has") is True,
          "forget removes the entry and is case/space-insensitive like the lookup")
    check(_CHAT.load() == {}, "G19g: after forget, the memory is empty again")
    check(_CHAT.forget("never stored") is False,
          "forgetting something absent reports false rather than raising")

    # An unreadable store degrades to NO memory, never to a crash — the lane has to
    # survive a half-written file on a network share.
    _CHAT.store_path().write_text("{ this is not json", encoding="utf-8")
    check(_CHAT.load() == {}, "a corrupt store reads as empty rather than taking the lane down")

    # The atomic write leaves nothing behind. A stray `.tmp` on the share is how a
    # later reader picks up a half-written file.
    _CHAT.remember("Another Series", "DEF@USECON")
    check(not [p for p in _tmp_dir.iterdir() if p.suffix == ".tmp"],
          "the atomic write leaves no temp file behind",
          str([p.name for p in _tmp_dir.iterdir()]))

    # G19h: the chat entry must WIN over the daily store, or correcting a wrong
    # auto-bind (D5) is impossible.
    _merged = {**{"another series": {"code": "WRONG@USECON"}}, **_CHAT.load()}
    check(_merged["another series"]["code"] == "DEF@USECON",
          "G19h: the chat entry overrides the daily learned entry it shadows")
finally:
    lane.R.CLARIFIED_DIR = _real_dir
    shutil.rmtree(_tmp_dir, ignore_errors=True)

# §13.6 is NOT relaxed by any of the above. The chat lane gained a store of its own;
# it did not gain write access to the daily one.
_sealed = False
try:
    lane.R.save_learned("x", "Y@USECON")
except Exception as _exc:
    _sealed = type(_exc).__name__ == "StoreWriteAttempted"
check(_sealed, "the daily learned store is STILL sealed against chat writes (§13.6)")

for raw, want in (("bar", "bar"), ("columns", "bar"), ("stacked bars", "stacked_bar"),
                  ("line", "line"), ("", "line")):
    try:
        got = lane._plot_kind(raw, "check")
        check(got == want, f"plot_kind {raw!r} normalizes to {want!r}", f"got {got!r}")
    except Exception as exc:
        check(False, f"plot_kind {raw!r} normalizes to {want!r}",
              f"{type(exc).__name__}: {exc}")
try:
    lane._plot_kind("area", "check")
    check(False, "an unrecognized plot_kind raises", "it returned a line instead")
except ValueError as exc:
    check("area" in str(exc) and "stacked_bar" in str(exc),
          "an unrecognized plot_kind raises")

print("\n9. Server-safety properties (§14.5)")
# These are invisible on a short-lived stdio process and load-bearing on a long-lived
# one. They are asserted here rather than only in the remote lane because the failure
# they prevent — one caller receiving another caller's chart — is reachable with a
# SINGLE user: Claude issues parallel tool calls within one turn.
p1, p2 = lane._save_path("samename"), lane._save_path("samename")
check(p1 != p2, "two renders of the same filename get different paths",
      f"{p1.name} vs {p2.name}")
check(p1.parent == p2.parent and p1.name.startswith("samename-"),
      "the readable stem survives the uniqueness suffix", p1.name)

import matplotlib.pyplot as plt  # noqa: E402

check(not plt.get_fignums(), "no matplotlib figures left open after the renders above",
      f"open figure ids: {plt.get_fignums()}" if plt.get_fignums() else "pyplot is clean")

check(isinstance(getattr(lane, "_RENDER_LOCK", None), type(__import__("threading").Lock())),
      "renders are serialized by a process-wide lock")

# The sweep deletes directories, so prove BOTH halves: it removes what it should and
# leaves alone what it must (the harnesses keep `_control` beside the date folders).
old = lane.OUT_ROOT / "1999-01-01"
keep = lane.OUT_ROOT / "_selftest_keep"
try:
    old.mkdir(parents=True, exist_ok=True)
    (old / "stale.txt").write_text("x", encoding="utf-8")
    keep.mkdir(parents=True, exist_ok=True)
    lane._swept_on = None                      # force a sweep in this process
    lane._sweep_outputs(__import__("datetime").date.today().isoformat())
    check(not old.exists(), "retention sweep drops folders past the cutoff",
          f"RETENTION_DAYS={lane.RETENTION_DAYS}")
    check(keep.exists(), "retention sweep leaves non-date folders alone", keep.name)
finally:
    __import__("shutil").rmtree(keep, ignore_errors=True)
    __import__("shutil").rmtree(old, ignore_errors=True)

print("\n10. Served-chart id resolution (§14.20)")
# `/chart/<id>` is the one route that turns text off the public internet into a
# filesystem path, so the guard gets tested rather than assumed. The render in section 6
# already wrote a real PNG — resolve THAT, so this exercises the same naming the server
# will serve rather than a fixture built to pass.
served = Path(_render_out["path"]) if _render_out else lane._save_path("idcheck")
if _render_out:
    check(lane.chart_path(served.stem) == served.resolve(),
          "a real rendered chart resolves by its id", served.stem[:24] + "…")
else:
    check(False, "a real rendered chart resolves by its id", "skipped — no render above")

check(len(served.stem.rsplit("-", 1)[-1]) == 32,
      "the id carries a full uuid4, so the URL is not guessable",
      f"{len(served.stem.rsplit('-', 1)[-1])} hex chars")

# Every spelling of "leave the output tree" that a URL can express. `%2e%2e%2f` is
# decoded by the ASGI server before we see it, so `..` covers that case too.
for evil in ("../../../../Windows/System32/drivers/etc/hosts", "..\\..\\secret",
             "C:/Windows/win.ini", "/etc/passwd", "a/b", "", "..", ".",
             "x" * 129, "chart\x00.png"):
    if lane.chart_path(evil) is not None:
        check(False, "chart_path refuses paths outside the output tree", repr(evil))
        break
else:
    check(True, "chart_path refuses paths outside the output tree",
          "9 traversal spellings all rejected")

check(lane.chart_path("no-such-chart-" + "0" * 32) is None,
      "an unknown id is a plain miss, not an error")

n_bad = sum(1 for ok, _, _ in _RESULTS if not ok)
print("\n" + "=" * 78)
print(f"{len(_RESULTS) - n_bad}/{len(_RESULTS)} checks passed"
      + ("" if not n_bad else f"  — {n_bad} FAILED"))
print("=" * 78)
raise SystemExit(1 if n_bad else 0)
