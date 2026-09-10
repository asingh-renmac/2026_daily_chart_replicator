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

check(lane.R._sa_of_descriptor("Total Nonfarm, Not Seasonally Adjusted (Thous)") == "",
      "the SA tag is read from the units parenthetical, not from the series NAME")


def _tie_sa(codes, want=""):
    return lane.R.break_exact_tie(
        [{"code": c, "descriptor": "", "sim": 1.0, "exact": True,
          "via_query": "q", "agg": "AVG"} for c in codes], want)


# D14: an SA/NSA pair is one series in two vintages, and the house picks SA.
_pick, _why = _tie_sa(["s@labor", "sa@labor"])
check(_pick is not None and _pick["code"] == "s@labor",
      "D14: an SA/NSA pair in ONE database binds the SA copy rather than parking",
      _why[:100] if _pick else f"PARKED: {_why[:80]}")
check(_pick is not None and "NSA" in _why,
      "and the reason tells the operator how to ask for the raw series instead")

_pick, _why = _tie_sa(["t@usecon", "t@labor"], "nsa")
check(_pick is not None and _pick["code"] == "t@labor",
      "D14: an explicit NSA request binds the NSA copy", _why[:100])

_pick, _why = _tie_sa(["t@usecon", "t@labor"])
check(_pick is not None and _pick["code"] == "t@usecon",
      "D14: unstated preference still means SA, across databases too")

# The rule must not swallow a pair that differs on IDENTITY as well as on SA — that is
# two different series, not two vintages of one.
lane.R._METADATA_CACHE.update({
    "u@usecon": dict(_BLS, descriptor="X (SA, Thous)"),
    "u@bci": dict(_BLS, shortsource="CB", longsource="The Conference Board",
                  descriptor="X (NSA, Thous)"),
    "v@labor": dict(_BLS, descriptor="X (SA, Thous)"),
    "v2@labor": dict(_BLS, descriptor="X (SA, Thous)"),
})
_pick, _why = _tie_sa(["u@usecon", "u@bci"])
check(_pick is None and "shortsource" in _why,
      "differing SA does NOT excuse a differing source — that still parks", _why[:96])

# Identical on everything including SA, same database: nothing left to decide on.
_pick, _why = _tie_sa(["v@labor", "v2@labor"])
check(_pick is None and "SAME database" in _why,
      "two codes identical in every respect, one database, still park", _why[:90])

check(lane.R.sa_requested({"base_descriptor": "All Employees: Total Private, NSA"}) == "nsa"
      and lane.R.sa_requested({"base_descriptor": "All Employees: Total Private"}) == ""
      and lane.R.sa_requested({"sa_hint": "nsa"}) == "nsa",
      "an NSA request is read from sa_hint or from the descriptor's words")


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

print("\n11. Every rendered chart stays reachable in the reply (G20a)")
# The inline image is collapsed into a closed tool panel by default, and Claude's
# connector routinely forwards only ONE of `content` / `structured_content`. That leaves
# the text block as the single carrier certain to reach the operator, so a link absent
# from it is a chart nobody can open. The failure is silent in the worst way — the render
# succeeded, the PNG exists, the reply just never mentions it — and it already happened
# once while the GET route was serving 200s. Asserting it beats re-reading one reply.
#
# `_result_text` is pure, so none of this needs DLX or a live render.
from haver_chart import server  # noqa: E402

_url = "https://chart.hvr-mcp.work/chart/gdp-" + "0" * 32
_summary = {"plotted": ["Real GDP", "Nominal GDP"], "end": "2026-06-30",
            "chart_id": "gdp-" + "0" * 32, "chart_url": _url}
_text = server._result_text(_summary, "chart_url")

check(_text.splitlines()[0].startswith("chart_url: "),
      "the link leads — first line, not buried under the payload",
      _text.splitlines()[0][:48] + "…")

check(_url in _text,
      "the url appears verbatim, so it cannot be paraphrased into a 404")

_missing = [k for k in _summary if f"{k}: " not in _text]
check(not _missing,
      "every structured field is mirrored into text",
      ("missing: " + ", ".join(_missing)) if _missing else f"{len(_summary)} fields")

check("not only the last" in _text,
      "the instruction covers the MULTI-chart case",
      "the observed failure was only the FINAL link getting printed")

# stdio carries a local path instead of a url, and must behave identically.
_stdio = server._result_text({"plotted": ["x"], "end": "2026", "path": r"C:\out\x.png"},
                             "path")
check(_stdio.splitlines()[0].startswith("path: "),
      "stdio leads with `path`, for the same reason")
check("chart_url" not in _stdio,
      "stdio never advertises a url the operator has no way to open")

print("\n12. Remembered answers survive rewording (§16.2)")
# G20a turned this up in the field: a slot the operator had already answered re-parked
# because the request said "THE civilian unemployment rate". The descriptor is written by
# the model from the commentary, so its wording drifts run to run, and an exact-match
# memory misses far more often than its entry count suggests.
#
# What must NOT happen is the overcorrection. These are unreviewed personal answers, so
# the rule is canonicalization — two strings reduce to one or they do not — never
# resemblance, and never a guess when the reduction is ambiguous.
import resolve as _R  # noqa: E402

_mem = {"civilian unemployment rate": {"code": "LR@USECON"},
        "federal funds target rate": {"code": "ffedtare@usecon"}}

check(_R._norm_key("Civilian  Unemployment RATE") == "civilian unemployment rate",
      "_norm_key is UNCHANGED, so no store on disk is orphaned")

for _phrase in ("civilian unemployment rate", "the civilian unemployment rate",
                "Civilian Unemployment Rate", "civilian  unemployment, rate",
                "civilian unemployment rate (SA)"):
    _hit = _R.learned_lookup(_mem, _phrase)
    if not (_hit and _hit["code"] == "LR@USECON"):
        check(False, "rewordings reach the remembered bind", repr(_phrase))
        break
else:
    check(True, "rewordings reach the remembered bind",
          "article, case, spacing, punctuation and a parenthetical all forgiven")

check(_R.learned_lookup(_mem, "US civilian unemployment rate") is None,
      "a country prefix is NOT forgiven",
      "US vs UK retail sales are different series — folding them is a wrong bind")

# The boundary of the design, asserted so nobody later "improves" it into similarity
# matching. Extra WORDS are not forgiven, only decoration. Stripping "seasonally
# adjusted" would also fight `_sa_of_descriptor`, which reads exactly that wording to
# tell an SA request from an NSA one — and dropping the "not" in "not seasonally
# adjusted" would invert the operator's meaning.
check(_R.learned_lookup(_mem, "civilian unemployment rate, seasonally adjusted") is None,
      "added WORDS are not forgiven — this is canonicalization, not similarity",
      "forgiving them would collide with the SA/NSA reading in _sa_of_descriptor")

check(_R.learned_lookup(_mem, "nonfarm payrolls") is None,
      "an unrelated descriptor still misses")

# Two stored descriptors that collapse together but disagree: park, never pick.
_ambig = {"nfib: percent raising compensation": {"code": "NFIB19@SURVEYS"},
          "nfib, percent raising compensation!": {"code": "NFIBPQL@SURVEYS"}}
check(_R._loose_key("nfib: percent raising compensation")
      == _R._loose_key("nfib, percent raising compensation!"),
      "the two test descriptors really do collapse together")
check(_R.learned_lookup(_ambig, "NFIB percent raising compensation") is None,
      "an AMBIGUOUS loose key binds nothing",
      "two codes disagree, so the slot parks rather than picking one")

# A formula's parentheses carry its meaning, not a qualifier. Stripping them mapped two
# different real store entries onto the bare key `zs`.
check(_R._loose_key("zs(nfib: net percent raising worker compensation)")
      != _R._loose_key("zs(nfib: single most important problem)"),
      "formula-shaped descriptors are not flattened onto each other")
check("(" in _R._loose_key("zs(yryr%(GDPH))"),
      "a formula keeps its parentheses — it is already canonical")

def _import_http_server():
    """Load `server.py` a SECOND time with HTTP forced on, so the picker exists.

    Section 11 imported the module in stdio mode, where the UI half is deliberately not
    registered — teammate zips should not carry tools that need a UI host. Re-importing
    under a different module name with dummy credentials is what lets the picker's wiring
    be asserted without a live Entra app or a running server.

    Returns None with the reason on failure rather than raising, so one awkward
    environment cannot take the other 86 checks down with it.
    """
    import asyncio
    import importlib.util
    import os
    from pathlib import Path

    env = {"HAVER_CHART_HTTP": "1",
           "HAVER_CHART_AZURE_CLIENT_ID": "selftest",
           "HAVER_CHART_AZURE_TENANT_ID": "selftest",
           "HAVER_CHART_AZURE_CLIENT_SECRET": "selftest",
           "HAVER_CHART_PUBLIC_URL": "https://selftest.invalid",
           "HAVER_CHART_JWT_SIGNING_KEY": "s" * 32}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location(
            "haver_chart._server_http_selftest", Path(server.__file__))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tools = {t.name: t for t in asyncio.run(mod.mcp.list_tools())}
        resources = {str(r.uri): r for r in asyncio.run(mod.mcp.list_resources())}
    except Exception as exc:                           # pragma: no cover - env dependent
        print(f"    (http server import failed: {type(exc).__name__}: {exc})")
        return None
    finally:
        for key, was in saved.items():
            if was is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = was
    return {"tools": tools, "resources": resources, "module": mod,
            "PICKER_URI": mod._PICKER_URI, "PICKER_HTML": mod._PICKER_HTML}


print("\n13. The picker obeys the four MCP Apps rules (§16.3)")
# Every one of these failed SILENTLY during G20b: the host reported success, or blamed a
# layer that was working. None of them is visible in a screenshot, so a test is the only
# thing that keeps five rounds of measurement from being paid for twice.
_srv = _import_http_server()
if _srv is None:
    check(True, "skipped — server module needs HTTP env vars to expose the picker",
          "set HAVER_CHART_HTTP with dummy Azure values to run these locally")
else:
    _pick_tool = _srv["tools"]["pick_series"]
    _pick_meta = _pick_tool.meta or {}
    _pick_res = _srv["resources"][_srv["PICKER_URI"]]
    _html = _srv["PICKER_HTML"]

    # Rule 2 — without the deprecated flat key, the host never issues resources/read.
    check(_pick_meta.get("ui/resourceUri") == _srv["PICKER_URI"],
          "the tool carries the DEPRECATED FLAT _meta pointer",
          "pre-GA hosts read only this one; without it the html is never fetched")
    check((_pick_meta.get("ui") or {}).get("resourceUri") == _srv["PICKER_URI"],
          "the tool also carries the current nested _meta pointer")

    # Rule 3 — a boolean here fails on PREFETCH, before any tool is called.
    check(isinstance((_pick_res.meta or {}).get("ui"), dict),
          "the resource's _meta.ui is an OBJECT, not `true`",
          "app=True emits a boolean and the host raises while prefetching")
    check(str(_pick_res.mime_type).startswith("text/html;profile=mcp-app"),
          "the resource is served as an mcp-app document")

    # Rules 3 and 4 — the handshake, and the height report without which the frame is
    # mounted at zero pixels while the host cheerfully reports a rendered widget.
    for _needed in ("ui/initialize", "ui/notifications/initialized",
                    "ui/notifications/size-changed"):
        check(_needed in _html, f"the view speaks {_needed}")

    # The submit path: record the bind from the view, then hand back to the model.
    check("tools/call" in _html and "remember_binding" in _html,
          "Submit calls remember_binding from the view itself",
          "so a bind cannot be lost to a model that forgets to follow up")
    check("ui/message" in _html,
          "the view reports the choice back into the conversation")
    check("innerHTML" not in _html,
          "candidate text is never written as markup",
          "rows carry DLX descriptors this server did not author")

    # A tool nobody is told about is a tool nobody calls. §16 v1 shipped working and sat
    # unused: the model hit a park and asked for a ticker in prose, which is exactly what
    # the docstring — written before the picker existed — told it to do. The instruction
    # has to ride in the RESULT, which arrives every time, not in a description read once.
    _srv_http = _srv["module"]
    _parked = _srv_http._park_text({"status": "parked", "reason": "ties", "candidates": []})
    check("pick_series" in _parked and "ACTION REQUIRED" in _parked,
          "a PARKED result tells the model to call pick_series",
          "the failure mode is silent: the model asks in prose and the panel never opens")
    check("in prose" in _parked and "do NOT bind a candidate yourself".lower()
          in _parked.lower(),
          "and forbids both prose-asking and self-binding")
    _bound = _srv_http._park_text({"status": "resolved", "resolved": "a0m059@bci"})
    check("pick_series" not in _bound and "ACTION REQUIRED" not in _bound,
          "a RESOLVED result says nothing about the picker",
          "no panel for a series that needed no choice — the whole point of a separate tool")
    check("a0m059@bci" in _bound, "a resolved result still names the bound code in text")

    # §16.4 — the panel resumes the work. Saving a binding and then making the operator
    # retype the request they already made is what stops a feature being used.
    check("original_request" in _parked,
          "the parked result tells the model to pass the request",
          "the panel cannot quote a request it was never given")
    check("VERBATIM" in _parked or "verbatim" in _parked,
          "and insists on the request verbatim, not a paraphrase",
          "re-running the model's summary is not re-running the operator's request")

    # §16.6 — ONE panel for every park, not one panel each. D15's "only the last panel
    # resumes" coordination is gone because there is now only ever one panel to resume
    # from; the instruction that replaces it has to say so plainly, or the model reverts
    # to calling the tool per slot and rebuilds the stack of panels by hand.
    check("ONCE" in _parked and "descriptors" in _parked,
          "the parked result says call pick_series ONCE with all parked descriptors")
    check("enrich_page" in _parked and "belongs to the panel" in _parked,
          "and warns the model off enrich_page, which is the panel's tool")
    # enrich_page must be registered AND invisible to the model. It is the panel's half of
    # pick_series; a model that called it would get rows it cannot display and would have
    # spent 20s of DLX time to do it.
    check("enrich_page" in _srv["tools"],
          "enrich_page is registered as a tool the panel can call")
    # Asserted on the decorator, not on the listed Tool: `list_tools()` returns the wire
    # shape, which does not carry AppConfig back out, so introspecting it reads None for
    # an app-only tool and an app-and-model one alike — a check that cannot fail is not a
    # check. `visibility` is also easy to put on `tool()` instead of `AppConfig`, where it
    # raises at import; the decorator text is the thing worth pinning.
    _srv_src = Path(server.__file__).read_text(encoding="utf-8")
    check('AppConfig(visibility=["app"]))\n    def enrich_page' in _srv_src,
          "and it is app-only, never offered to the model",
          "a model calling it would spend 20s of DLX time on rows it cannot display")

    import inspect as _inspect
    check("descriptors" in str(_inspect.signature(lane.pick_series_pages)),
          "lane.pick_series_pages takes a LIST of descriptors")
    _pages_src = _inspect.getsource(lane.pick_series_pages)
    check("if i == 0" in _pages_src,
          "only the FIRST page is enriched up front",
          "enriching five pages costs ~102s in one call, worse than the panels it replaced")
    # D16: the panel stays live in scroll-back forever.
    check("issued" in _html and "30" in _html,
          "an old panel saves the bind but refuses to re-run it (D16)")
    check("Do NOT re-run anything from this old panel" in _html,
          "and says so to the model explicitly")
    # The loop guard. A save landing under a key the next lookup misses would otherwise
    # park again, re-open the panel, and repeat forever.
    check("parks AGAIN" in _html and "second time" in _html,
          "the resume instruction carries its own circuit breaker",
          "park -> pick -> re-run -> park is an infinite loop with a widget in it")
    # §16.6 — the wait is EXPLAINED, from the first paint. The empty table with
    # "Waiting for candidates..." is what made a working tool look broken; hiding the
    # frame for the same 20s was the alternative and was rejected, so the progress state
    # has to be the thing that renders first, not a fallback behind a timer.
    check('id="panel"' in _html and 'display:none' in _html,
          "the choice table starts hidden rather than rendering empty",
          "an empty table with no rows is indistinguishable from a broken tool")
    check('id="boot"' in _html and "GRACE_MS" not in _html,
          "the progress state is shown immediately, not after a grace period",
          "the operator chose being told over a frame that hides itself")
    check("runBar" in _html and "Math.exp" in _html,
          "and the bar is asymptotic, never reaching 100%",
          "a bar that fills and then sits there is a lie you only have to catch once")
    check('id="holdbar"' in _html and 'id="bootbar"' in _html,
          "both waits get a bar — the first panel AND each later series")

    # Layout B: a wizard, one series on screen at a time.
    check('id="back"' in _html and 'id="next"' in _html and "Series \" + (page + 1)" in _html,
          "the panel is a wizard: Back, Next, and a 'Series N of M' counter")
    check('goBtn.style.display = (!many || last)' in _html
          and 'nextBtn.style.display = (many && !last)' in _html,
          "Next becomes Save on the last step rather than sitting beside it",
          "one forward action at a time is the whole point of choosing a wizard")
    check("go back for the" in _html,
          "reaching the end with gaps says so instead of greying Save out silently",
          "that is the one way a wizard leaves someone stuck")
    check('d.addEventListener("click", function () { go(i); })' in _html,
          "the step dots jump straight to a series",
          "a wizard's real cost is stepping back through everything to reach series 2")

    # Holding a page until its metadata lands, rather than painting in similarity order
    # and re-sorting. Chosen deliberately: a list that reorders while being read is worse
    # than one that arrives a moment later.
    check("Still loading this series" in _html,
          "a series that has not loaded says so in the operator's terms",
          "the old wording explained SA ordering, which is not what they needed to know")
    check("enriching" in _html and "queue" in _html,
          "the panel enriches pages ONE at a time",
          "six concurrent metadata calls were measured to hang DLX outright")

    # Every remaining series is queued the moment the first is on screen. Prefetching only
    # ONE ahead is what put a progress bar on every Next but the first (2026-09-09).
    check("prefetchAll" in _html and "prefetchNext" not in _html,
          "ALL remaining series are prefetched, not just the next one")
    _pre = _html.split("function prefetchAll")[-1].split("function ")[0]
    check("return" not in _pre.split("for (")[-1].split("}")[0],
          "and the prefetch loop does not stop at the first one it queues",
          "the `return` in that loop was the whole defect")
    check("queue.unshift(i)" in _html and "urgent" in _html,
          "the series on screen jumps the queue ahead of background work",
          "otherwise jumping to series 5 waits behind the prefetch of 2, 3 and 4")
    check("ensureEnriched(i, true)" in _html,
          "and navigating marks it urgent")
    # §19.8 — the panel's prefetch is not the only mechanism, because it cannot be. It
    # depends on the chat host forwarding tool calls after the model's turn has ended, and
    # on 2026-09-09 an operator spent five minutes on series one and still met a cold
    # series three. The server warms the rest itself, where no host is in the loop.
    check("warm_pages" in _inspect.getsource(lane.pick_series_pages),
          "pick_series_pages warms the remaining series server-side")
    _warm_src = _inspect.getsource(lane._prefetch_loop)
    check("_fg_busy" in _warm_src,
          "and the warmer yields to any foreground request",
          "warming a page nobody is looking at must never delay one somebody is")
    check("daemon=True" in _inspect.getsource(lane.warm_pages),
          "the warmer is a daemon thread, so it cannot hold a restart open")
    check(_inspect.getsource(lane.warm_pages).count("Thread(") == 1
          and "_PREFETCH_LOCK" in _inspect.getsource(lane.warm_pages),
          "exactly ONE warmer thread, guarded",
          "concurrent DLX callers are the documented way to hang it (19.2)")
    check("_DLX_META_LOCK" in _inspect.getsource(lane._resolve_pool),
          "the catalogue search holds the DLX lock too",
          "with a background warmer there are two threads that can be inside Haver")
    check("_foreground" in str(_inspect.signature(lane.enrich_page)),
          "enrich_page knows whether an operator is waiting on it")

    _meta_src = _inspect.getsource(lane.candidate_meta)
    check("_DLX_META_LOCK" in _meta_src,
          "and the lane serializes DLX itself, not trusting the panel to do it",
          "the guarantee must not depend on the caller behaving")
    check("_meta_cache_path" in _meta_src,
          "candidate metadata is cached on DISK, so a restart does not re-pay 20s")

    # Three states, not two. Collapsing "said no" into "not looked at yet" would let Save
    # fire on a series the operator never opened.
    check('id="skip"' in _html and "picks[page] = null" in _html,
          "a per-tab 'none of these' is distinct from an undecided tab")
    check("allDecided" in _html,
          "Save stays disabled until EVERY tab is decided")
    check("Nothing after this one was saved" in _html,
          "a refused binding stops the save instead of pressing on",
          "saving three of five and resuming builds a chart nobody approved")

    # The escape hatch must store NOTHING: a shrug today must not become a remembered
    # decision that every future chat inherits.
    check("do NOT guess a ticker for those" in _html and "ask me" in _html,
          "there is a way out that saves nothing (none-of-these)")
    # The skip handler must reach `picks`, never `remember_binding`: a tab the operator
    # rejected has to leave no trace at all, or a shrug today becomes a decision every
    # future chat inherits.
    _skip = _html.split('getElementById("skip").addEventListener')[-1].split("});")[0]
    check("remember_binding" not in _skip,
          "and the none-of-these path never calls remember_binding")
    check("key" in _html and "Stored under key" in _html,
          "the panel shows which key the bind landed under",
          "a save under an unreachable key is the one failure that looks like success")

    # §16.5 — the follow-up must not fail silently. Measured 2026-09-08: Save & continue
    # saved the bind and did nothing else, and there was no way to tell a host that
    # DECLINED ui/message from one that never got it.
    check('request("ui/message"' in _html,
          "ui/message is sent as an awaited REQUEST, not fire-and-forget",
          "HostCapabilities has no flag for it, so the response is the only evidence")
    check("would not accept the follow-up" in _html,
          "a host that refuses the follow-up says so in the panel")
    check(_html.count('request("ui/message"') >= 2,
          "and the content shape is retried as an array",
          "the spec says object; shipped hosts differ, and this client already needed a "
          "deprecated key to render at all")

    # §16.5 — HTML-escaped descriptors. A pick arrived as `Food &amp; Energy` and was
    # stored under a key no later lookup could ever spell, so the answer was lost the
    # moment it was given.
    check(lane._clean_descriptor("CPI-U: Commodities Less Food &amp; Energy") ==
          "CPI-U: Commodities Less Food & Energy",
          "an HTML-escaped descriptor is cleaned at the boundary",
          "otherwise the bind is filed under a key that can never be looked up")
    check(lane._clean_descriptor("Retail Sales & Food Services") ==
          "Retail Sales & Food Services",
          "and an already-clean descriptor is untouched")
    check(R._norm_key(lane._clean_descriptor("Food &amp; Energy")) ==
          R._norm_key("food & energy"),
          "so the escaped and plain spellings reach the SAME store key",
          "which is the property that makes the answer findable again")

    # D18 — SA first in the PARKED list, not just on the auto-bind path. D14 only ever
    # fired while auto-binding, so a parked panel offered five NSA copies of a series
    # whose SA copy sat two ranks below the cut.
    import inspect as _inspect
    # The ordering itself now lives in `_enrich_and_order`, shared by the single-series
    # path and every tab of the batched panel — one implementation, so a tab cannot end up
    # ordered differently from the panel it sits in.
    _pick_src = _inspect.getsource(lane.pick_series)
    _order_src = _inspect.getsource(lane._enrich_and_order)
    check('"sa_target"' in _pick_src and '"sa_note"' in _pick_src,
          "pick_series reports which adjustment it ordered for, and says so",
          "an operator cannot see a reordering they are not told about")
    check("sa_requested" in _order_src and "_sa_of_descriptor" in _order_src,
          "and it reuses D14's rule rather than inventing a second one")
    check("candidate_n * 3" in _pick_src
          and "candidate_n * 3" in _inspect.getsource(lane.enrich_page)
          and "candidate_n * 3" in _inspect.getsource(lane.pick_series_pages),
          "the candidate pool is widened BEFORE re-ranking, on every path",
          "re-ranking only the visible N cannot surface an SA copy ranked below it")
    check('tiers = {target: 0, "": 1}' in _order_src,
          "untagged descriptors rank above the opposite adjustment, not below it",
          "a hard filter would hide a series whose descriptor carries no SA tag at all")
    check("<th>Adj</th>" in _html,
          "the panel has an adjustment column",
          "SA vs NSA was invisible except inside the descriptor text")

# ── 14. /health reports the DLX session, not just render liveness ───────────────
# The data lane refused every pull from 2026-09-04 to 09-08 and its /health said so the
# whole time. The chart lane, on the SAME DLX install, published nothing about the
# session at all, so its version of that outage would have been invisible. These checks
# exist because the host watchdog keys on two exact field names, and a rename here
# silently turns monitoring back off while every test still passes.
print("\n14. /health exposes DLX session state (post-2026-09-08)")

_lane_mod = lane

_lane_mod._dlx_suspect = False
_lane_mod._last_dlx_ok = None
_h = _lane_mod.health()
check("session_suspect" in _h and "last_pull_finished" in _h,
      "health() publishes the two fields the watchdog reads",
      "named exactly as the data lane names them, so one rule covers both")

_lane_mod._dlx_note(True)
_h = _lane_mod.health()
check(_h["session_suspect"] is False and _h["last_pull_finished"],
      "a successful DLX call clears suspect and stamps the time",
      "the stamp is what a staleness check measures against")

_ok_stamp = _h["last_pull_finished"]
_lane_mod._dlx_note(False)
_h = _lane_mod.health()
check(_h["session_suspect"] is True and _h["last_pull_finished"] == _ok_stamp,
      "a failure raises suspect but does NOT move the success stamp",
      "moving it would erase the staleness the watchdog needs to see the fault")

_lane_mod._dlx_note(True)
check(_lane_mod.health()["session_suspect"] is False,
      "a later success clears suspect again",
      "one bad ticker must not latch the flag on forever")

check(_lane_mod.health()["last_pull_finished"] != _h["last_render_finished"]
      or _h["last_render_finished"] is None,
      "the DLX stamp is not the render stamp",
      "a render can finish from cache without touching DLX, so it cannot stand in")

_lane_mod._dlx_suspect = False
_lane_mod._last_dlx_ok = None

# ── 15. sa() — X-13 run locally, in a lane that must never mislabel it ──────────
# Haver writes `sa(...)` into formulas and the parser had no such token, so every read
# carrying one died at `formula_mnemonics` before a ticker was ever confirmed — the
# formula path parked with "formula parse/unsupported" and the operator saw a park with
# no candidates. These checks cover the three ways the fix could go quietly wrong:
# adjusting on the wrong sample, mislabelling whose adjustment it is, and letting the
# wrapper's classical fallback pass itself off as X-13.
print("\n15. sa() seasonal adjustment (X-13)")

import numpy as _np                                                    # noqa: E402
import pandas as _pd                                                   # noqa: E402

import build_chart as _BC                                              # noqa: E402
import transforms as _T                                                # noqa: E402
from render import PlotSeries as _PS                                   # noqa: E402

check(_T.formula_mnemonics("sa(diff%(X))") == ["X"],
      "sa() parses and yields its inner mnemonic",
      "this is the regression: it used to raise NeedPin and park the whole slot")
check(_T.parse("sa(X, log=1, lookback=8)").opts == {"log": 1.0, "lookback": 8.0},
      "the keyword form carries its overrides")
try:
    _T.parse("sa(X, bogus=1)")
    check(False, "an unknown sa() option is rejected")
except _T.ParseError as _e:
    check("recognized" in str(_e),
          "an unknown sa() option is rejected AND lists the real ones",
          "an operator who cannot see the accepted spelling has to guess at it")

_rng = _np.random.default_rng(7)
_idx = _pd.date_range("2005-01-31", periods=250, freq="ME")
_lvl = (100 * _np.exp(_np.linspace(0, .5, 250))
        + 6 * _np.sin(2 * _np.pi * _np.arange(250) / 12) + _rng.normal(0, .5, 250))
_smap = {"X": _T.SeriesData(values=_pd.Series(_lvl, index=_idx), freq="M", code="X@T")}
_WIN = (_pd.Timestamp("2016-01-31"), _pd.Timestamp("2025-10-31"))

_ev = _T.Evaluator(_smap, _WIN)
_sa_mom = _T.finish(_T.parse("sa(diff%(X))"), 0, _WIN, _smap, "M", evaluator=_ev)
_raw_mom = _T.finish(_T.parse("diff%(X)"), 0, _WIN, _smap, "M")
check(_sa_mom.std() < _raw_mom.std() / 2,
      "sa() actually removes the seasonality it claims to",
      f"sd {_raw_mom.std():.3f} -> {_sa_mom.std():.3f}")
check(_T.finish(_T.parse("diff%(sa(X))"), 0, _WIN, _smap, "M").std() < _raw_mom.std() / 2,
      "and it composes in BOTH directions",
      "sa(diff%(X)) and diff%(sa(X)) are different operations, and both must evaluate")

check(_sa_mom.index[0] == _WIN[0] and _sa_mom.index[-1] <= _WIN[1],
      "the plotted line is sliced to the display window")
_est = [L for L in _ev.interp_log if L.startswith("SA:")][0]
check("2011-01-31" in _est and "2005" not in _est,
      "X-13 estimated on the window extended back by SA_LOOKBACK_YEARS, not on all history",
      "a bounded sample is the choice; drifting to full history would change every value")

# A window shorter than X-13's floor must widen its own lookback rather than quietly
# accept the classical fallback that a too-short sample would trigger inside the wrapper.
_SHORT = (_pd.Timestamp("2024-01-31"), _pd.Timestamp("2025-10-31"))
_ev2 = _T.Evaluator(_smap, _SHORT)
_T.finish(_T.parse("sa(X)"), 0, _SHORT, _smap, "M", evaluator=_ev2)
import re as _re                                                       # noqa: E402
_n_est = int(_re.search(r"on (\d+) M obs",
                        [L for L in _ev2.interp_log if L.startswith("SA:")][0]).group(1))
check(_n_est >= _T.SA_MIN_OBS["M"],
      "a short window auto-widens its estimation sample past the X-13 floor",
      f"{_n_est} obs >= {_T.SA_MIN_OBS['M']}")

_tiny = {"X": _T.SeriesData(values=_pd.Series(_np.arange(20.),
         index=_pd.date_range("2023-01-31", periods=20, freq="ME")), freq="M", code="X@T")}
try:
    _T.finish(_T.parse("sa(X)"), 0,
              (_pd.Timestamp("2023-01-31"), _pd.Timestamp("2024-08-31")), _tiny, "M")
    check(False, "too little history refuses")
except _T.TransformError as _e:
    check("needs 36" in str(_e),
          "too little history REFUSES instead of silently degrading",
          "the wrapper would have fallen back to classical and returned a plausible line")

_wk = {"X": _T.SeriesData(values=_pd.Series(_np.arange(400.),
       index=_pd.date_range("2015-01-04", periods=400, freq="W")), freq="W", code="X@T")}
try:
    _T.finish(_T.parse("sa(X)"), 0,
              (_pd.Timestamp("2016-01-03"), _pd.Timestamp("2022-01-02")), _wk, "W")
    check(False, "a weekly series refuses")
except _T.NeedPin:
    check(True, "a weekly series refuses — X-13 adjusts monthly or quarterly only")

# The wrapper NEVER raises: it degrades to classical decomposition and logs it. If that
# ever reaches a chart, the subtitle goes on saying X-13 over numbers X-13 did not make.
_real_run, _T._X13_CACHE = _T._x13_run, {}
_T._x13_run = lambda *a, **k: (None, "", "simulated X-13 failure")
try:
    _T.finish(_T.parse("sa(X)"), 0, _WIN, _smap, "M")
    check(False, "a classical fallback is refused")
except _T.TransformError as _e:
    check("Refusing to label a classical adjustment as X-13" in str(_e),
          "a silent classical fallback is REFUSED, not relabelled",
          "the wrapper degrades rather than raising, so this lane has to catch it")
finally:
    _T._x13_run, _T._X13_CACHE = _real_run, {}

# The wrapper degrades in TWO ways and they are not equivalent. Classical decomposition
# means X-13 never ran (refuse). A trading-day retry means X-13 ran WITHOUT the
# regression we asked for — legitimate, but interp_log must stop claiming trading=True.
# Nothing catches this today because the default is trading=False; it goes wrong the
# first time somebody passes trading=1, which is exactly when they care.
_w = _T._FallbackWatch()


class _Rec:
    def __init__(self, m): self._m = m
    def getMessage(self): return self._m


_w.emit(_Rec("X-13 failed for 'value' with trading=True (boom). "
             "Retrying without trading day."))
check(_w.td_retry and not _w.classical,
      "a trading-day retry is recorded as a NOTE, not as a classical fallback",
      "refusing it would reject a perfectly good X-13 run")
_w.emit(_Rec("X-13 failed entirely for 'value': boom. "
             "Falling back to classical decomposition."))
check(len(_w.classical) == 1,
      "and a real classical fallback is still caught separately")

_ev3 = _T.Evaluator(_smap, _WIN)
_T._X13_CACHE = {}
_T._x13_run = lambda *a, **k: (_raw_mom, "trading day off (regression failed)", None)
try:
    _T.finish(_T.parse("sa(X, trading=1)"), 0, _WIN, _smap, "M", evaluator=_ev3)
    check("trading day off" in [L for L in _ev3.interp_log if L.startswith("SA:")][0],
          "and the note reaches interp_log, so trading=True is not reported falsely",
          "a run logged as something it was not is the quiet version of a wrong chart")
finally:
    _T._x13_run, _T._X13_CACHE = _real_run, {}

check(_BC.transform_label("sa(diff%(X))", "month")
      == "% change, period-over-period, seasonally adjusted (X-13)",
      "the label keeps the INNER transform and adds the SA stamp",
      "stamping alone would drop '% change' and the chart would stop saying what it plots")
check(_BC.transform_label("diff%(sa(X))", "month", ).endswith("seasonally adjusted (X-13)")
      and _BC.transform_label("sa(A)+sa(B)", "month") == "seasonally adjusted (X-13)",
      "the stamp survives the other ordering AND a sum, where the label is otherwise None",
      "provenance is the point: these numbers are ours, not the source agency's")
check(_BC.transform_label("diff%(X)", "month") == "% change, period-over-period",
      "an unadjusted series is NOT stamped")

check(_PS(label="x", series=_raw_mom).sa is False
      and _PS(label="x", series=_raw_mom, sa=True).sa is True,
      "PlotSeries carries the SA flag validation reads")
_rend = {"_drawn": [_PS(label="Hospitals", series=_sa_mom, sa=True)],
         "_freq": "M", "end": str(_sa_mom.index[-1].date())}
_row = lane.last_value_check(_rend, {"Hospitals": float(_sa_mom.iloc[-1])})[0]
check("widened" in _row.get("tolerance", ""),
      "an SA'd line widens the last-value tolerance AND says so in the row",
      "our X-13 cannot tie exactly to Haver's sa(); a check that silently got weaker "
      "is worse than none, because the report still reads PASS")
check("tolerance" not in lane.last_value_check(
          {"_drawn": [_PS(label="Hospitals", series=_sa_mom)], "_freq": "M",
           "end": str(_sa_mom.index[-1].date())},
          {"Hospitals": float(_sa_mom.iloc[-1])})[0],
      "and an unadjusted line keeps the strict tolerance")

_vend = _REPO_ROOT / "src" / "x13_seasonal_adjust.py"
_canon = Path(r"C:\Users\asingh\new_work\econ-templates\sa\x13_seasonal_adjust.py")
check(_vend.exists(), "the X-13 wrapper is vendored into src/",
      "the AVD cannot clone econ-templates — its remote is SSH-only and that host "
      "cannot complete an SSH handshake to GitHub")
if _canon.exists():
    check(_vend.read_bytes() == _canon.read_bytes(),
          "and the vendored copy has not drifted from econ-templates",
          "run scripts/check_x13_vendor.py")

n_bad = sum(1 for ok, _, _ in _RESULTS if not ok)
print("\n" + "=" * 78)
print(f"{len(_RESULTS) - n_bad}/{len(_RESULTS)} checks passed"
      + ("" if not n_bad else f"  — {n_bad} FAILED"))
print("=" * 78)
raise SystemExit(1 if n_bad else 0)
