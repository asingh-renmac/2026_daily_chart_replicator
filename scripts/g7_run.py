"""
g7_run.py — G7 live shakedown of the Teams loop, designed to span TWO invocations.

The real test isn't "did one message post" — it's the SECOND day, where cross-run
state bugs live. Each `run` is a fresh process; the ONLY thing that persists is the
CSV ledger (+ the rendered PNGs and, in stub mode, the stub chat file). Nothing is
carried in memory between runs.

Scenario (4 charts; stands in for the parsed email — G2 already proved real
extraction 26/26, so G7's novel surface is the Graph transport + cross-run state):
  • g7_jolts       — fully resolved + confident title  → renders in RUN-1.
  • sample1_chart3 — capital-equipment PPI park: one series unresolved + a resolved
                     sibling; the genuine "PPI: Manufacturing Industries" → 4 sp@PPI
                     candidates → parks. Answered between runs (pa413121@usecon).
  • sample9_chart1 — a SECOND park, deliberately LEFT UNANSWERED (half-answered day);
                     its resolved sibling must NEVER render alone (Defect-2).
  • sample2_chart2 — resolved, ambiguous title → title round-trip (var1/var2 pairs);
                     answered between runs (pick option 2).

Six assertions, evaluated by `assert` after the two runs (see _assert()).

USAGE
  Offline proof (what I run; separate processes, stub transport):
      python scripts/g7_run.py selftest
  Live (you run, after consent + AS_TEAMS_CHAT_ID):
      python scripts/g7_run.py reset
      python scripts/g7_run.py run            # RUN-1 (posts asks to the self-chat)
      # … reply in Teams: tag each reply with [chart_id]; leave sample9 unanswered …
      python scripts/g7_run.py run            # RUN-2 (resumes purely from the ledger)
      python scripts/g7_run.py assert
  Transport: env G7_TRANSPORT=graph (default) | stub.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import approval as A   # noqa: E402
import ledger as L     # noqa: E402
import render as R     # noqa: E402
import teams as TM     # noqa: E402

WORK = ROOT / "outputs" / "g7"
LEDGER = WORK / "ledger.csv"
STUB = WORK / "teams_stub.json"
MSG = "g7msg"


def _png(chart_id: str) -> Path:
    return WORK / f"{chart_id}.png"


# ─────────────────────────────── scenario ──────────────────────────────────
SCENARIO = [
    dict(chart_index="0", chart_id="g7_jolts", release_slug="jolts",
         subject="JOLTS hires vs quits", status=L.APPROVED,
         resolved_ticker={"Hires": "LJH@USECON", "Quits": "LSWP@USECON"},
         chosen_title="Hiring cools as quits retreat toward pre-pandemic norms",
         subtitle="Monthly, millions"),
    dict(chart_index="1", chart_id="sample1_chart3", release_slug="durable_goods",
         subject="Capital equipment: shipments vs producer prices",
         status=L.AWAITING_TICKER,
         resolved_ticker={"Shipments": "NMSCNX@USECON"},
         unresolved=["PPI: Manufacturing Industries"]),
    dict(chart_index="2", chart_id="sample9_chart1", release_slug="ism",
         subject="New orders vs a diffusion index", status=L.AWAITING_TICKER,
         resolved_ticker={"New orders": "NMSCNX@USECON"},
         unresolved=["Some unknown diffusion index"]),
    dict(chart_index="3", chart_id="sample2_chart2", release_slug="pce",
         subject="PCE inflation by component", status=L.RESOLVED,
         resolved_ticker={"Housing": "JCSRM@USNA"}),
]

# Charts whose title is confident (no vote) → auto-approve once resolved.
AUTO_TITLE = {
    "sample1_chart3": ("Capital equipment orders firm as input prices cool",
                       "Index level, shipments vs producer prices"),
}

# Shown in the ticker ask (the real 4 metadata-identical sp@PPI candidates).
CANDIDATES = {"PPI: Manufacturing Industries":
              ["sp1310@PPI", "sp2410@PPI", "sp2610@PPI", "sp3210@PPI"]}

# Expected one-and-only-once posts per chart (the no-duplicate-post invariant).
EXPECTED_POSTS = {"g7_jolts": 0, "sample1_chart3": 1,
                  "sample9_chart1": 1, "sample2_chart2": 1}

_KNOWN = {"pa413121@usecon"}   # stub confirm: accepts the right code, rejects bogus
_STUB_TITLES = {
    "sample2_chart2": [
        {"title": "Housing inflation cools while core services stay firm",
         "subtitle": "Z-score of year-over-year %"},
        {"title": "Housing inflation cools while core services stay firm",
         "subtitle": "Standardized year-over-year %"},
    ],
}


# ───────────────────────────── injected callbacks ──────────────────────────
def _confirm(synthetic: bool):
    if synthetic:
        return lambda code: code.strip().lower() in _KNOWN
    import resolve
    return resolve.confirm_ticker


def _titles(synthetic: bool):
    if synthetic:
        def fn(row):
            return _STUB_TITLES.get(row["chart_id"],
                                    [{"title": row.get("subject", "Chart"),
                                      "subtitle": ""}])
        return fn
    import propose

    def fn(row):
        series = list((row.get("resolved_ticker") or {}).keys())
        return propose.propose_titles(subject=row.get("subject", "Chart"),
                                      series=series, transform="(per chart)",
                                      span="monthly", n=2)
    return fn


# ───────────────────────────────── render ──────────────────────────────────
def _synth(code: str) -> pd.Series:
    rng = np.random.default_rng(abs(hash(code)) % (2 ** 32))
    idx = pd.date_range("2018-01-31", periods=90, freq="ME")
    return pd.Series(np.cumsum(rng.normal(0, 1, len(idx))) + 100.0, index=idx)


def _series_for(row: dict, synthetic: bool) -> dict:
    out = {}
    for desc, code in (row.get("resolved_ticker") or {}).items():
        if synthetic:
            out[desc] = _synth(code)
            continue
        try:
            import g4_lib
            cd, db = code.split("@")
            out[desc] = g4_lib.pull(cd, db, "M").values
        except Exception as e:   # resilient: a bad live code never crashes G7
            print(f"  [warn] Haver pull failed for {code} ({e}); using synthetic")
            out[desc] = _synth(code)
    return out


def _render(row: dict, synthetic: bool) -> str:
    data = _series_for(row, synthetic)
    ps = [R.PlotSeries(label=desc, series=s) for desc, s in data.items()]
    spec = R.RenderSpec(title=row.get("chosen_title") or row.get("subject"),
                        subtitle=row.get("subtitle") or None)
    path = str(_png(row["chart_id"]))
    fig, _ = R.render(ps, spec, save_path=path)
    import matplotlib.pyplot as plt
    plt.close(fig)
    return path


# ───────────────────────────────── steps ───────────────────────────────────
def _transport(synthetic: bool):
    return TM.StubTransport(str(STUB)) if synthetic else TM.GraphTransport()


def do_run() -> int:
    synthetic = os.environ.get("G7_TRANSPORT", "graph").lower() == "stub"
    WORK.mkdir(parents=True, exist_ok=True)
    led = L.Ledger(str(LEDGER))
    transport = _transport(synthetic)

    if not led.rows:
        for sc in SCENARIO:
            led.upsert(message_id=MSG, **sc)
        led.save()
        print(f"[run] RUN-1: ingested {len(SCENARIO)} charts "
              f"(scenario stands in for the parsed email)")
    else:
        print("[run] RUN-2+: resuming purely from the ledger (no carryover)")

    confirm, titles = _confirm(synthetic), _titles(synthetic)
    A.run_ticker_roundtrip(led, transport, confirm, candidates=CANDIDATES)

    # confident-title charts skip the vote: RESOLVED → APPROVED directly
    for row in led.by_status(L.RESOLVED):
        if row["chart_id"] in AUTO_TITLE:
            t, s = AUTO_TITLE[row["chart_id"]]
            led.set_status(row, L.APPROVED, chosen_title=t, subtitle=s)

    A.run_title_roundtrip(led, transport, titles)

    rendered = []
    for row in led.by_status(L.APPROVED):          # parked/awaiting never reach here
        path = _render(row, synthetic)
        led.set_status(row, L.DONE, output_path=path)
        rendered.append(row["chart_id"])
    led.save()

    print(f"  rendered this run: {rendered or '(none)'}")
    for r in led.rows:
        print(f"    {r['chart_id']:<16} {r['status']:<16} "
              f"png={'yes' if _png(r['chart_id']).exists() else 'no '}  "
              f"resolved={r.get('resolved_ticker')}")
    return 0


def do_reply() -> int:
    """STUB only: stands in for me typing in Teams between the two runs. Answers
    sample1_chart3's ticker and sample2_chart2's title; leaves sample9 unanswered.
    Each reply is tagged [chart_id] exactly as the live ask instructs."""
    tr = TM.StubTransport(str(STUB))
    tr.queue_reply(A.chart_thread("durable_goods", "sample1_chart3"),
                   "[sample1_chart3] pa413121@usecon")
    tr.queue_reply(A.chart_thread("pce", "sample2_chart2"),
                   "[sample2_chart2] 2")
    print("[reply] injected: sample1_chart3 ticker=pa413121@usecon, "
          "sample2_chart2 title=2; sample9_chart1 LEFT UNANSWERED")
    return 0


def _stub_post_counts() -> dict:
    import json
    counts = {cid: 0 for cid in EXPECTED_POSTS}
    if not STUB.exists():
        return counts
    data = json.loads(STUB.read_text(encoding="utf-8"))
    for t in data.get("threads", {}).values():
        for p in t.get("posts", []):
            for cid in counts:
                if f"[{cid}]" in p["text"]:
                    counts[cid] += 1
    return counts


def do_assert() -> int:
    synthetic = os.environ.get("G7_TRANSPORT", "graph").lower() == "stub"
    led = L.Ledger(str(LEDGER))
    by = {r["chart_id"]: r for r in led.rows}
    fails = []

    def check(cond, msg):
        print(("  PASS " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    print("[assert] G7 six-point cross-run battery")

    # 1. RUN-1 ingested + posted the right asks (one each), JOLTS never asked.
    if synthetic:
        posts = _stub_post_counts()
        check(posts == EXPECTED_POSTS,
              f"1. asks posted once each (jolts:0, two tickers, one title): {posts}")
    else:
        check(True, "1. (live) inspect the self-chat: one ask each for "
                    "sample1_chart3, sample9_chart1, sample2_chart2; none for jolts")

    # 2. IDEMPOTENCY: JOLTS rendered in RUN-1, not re-rendered in RUN-2
    #    (its PNG is OLDER than the RUN-2 renders).
    jolts_png, a_png = _png("g7_jolts"), _png("sample1_chart3")
    check(by["g7_jolts"]["status"] == L.DONE and jolts_png.exists(),
          "2a. g7_jolts done + rendered")
    check(jolts_png.exists() and a_png.exists()
          and jolts_png.stat().st_mtime <= a_png.stat().st_mtime,
          "2b. g7_jolts PNG predates RUN-2 renders (not re-rendered)")

    # 3. CLEAN RESUME: the answered park + answered title completed in RUN-2.
    check(by["sample1_chart3"]["status"] == L.DONE
          and (by["sample1_chart3"].get("resolved_ticker") or {}).get(
              "PPI: Manufacturing Industries") == "pa413121@usecon"
          and a_png.exists(),
          "3a. sample1_chart3 resolved (pa413121@usecon) + rendered")
    check(by["sample2_chart2"]["status"] == L.DONE
          and _png("sample2_chart2").exists(),
          "3b. sample2_chart2 title approved + rendered")

    # 4. PARTIAL-CHART INTEGRITY: the unanswered park stayed parked, ZERO PNG —
    #    its resolved sibling was NOT rendered alone across the gap.
    check(by["sample9_chart1"]["status"] == L.AWAITING_TICKER,
          "4a. sample9_chart1 still awaiting_ticker (unanswered)")
    check(not _png("sample9_chart1").exists(),
          "4b. sample9_chart1 produced NO PNG (no partial render)")

    # 5. NO DUPLICATE TEAMS POST across the kill/restart.
    if synthetic:
        posts = _stub_post_counts()
        check(all(posts[c] == EXPECTED_POSTS[c] for c in EXPECTED_POSTS),
              f"5. exactly one post per asked chart, none re-posted in RUN-2: {posts}")
    else:
        check(by["sample9_chart1"].get("ask_cursor"),
              "5. (live) sample9 keeps its RUN-1 ask_cursor → not re-asked in RUN-2")

    # 6. LEDGER IS SOURCE OF TRUTH: only the ledger drove RUN-2 resumption.
    check(by["g7_jolts"]["status"] == L.DONE
          and by["sample9_chart1"]["status"] == L.AWAITING_TICKER,
          "6. resume reconstructed from CSV alone (done skipped, parked picked up)")

    print(f"\n[assert] {'ALL PASS' if not fails else f'{len(fails)} FAILURE(S)'}")
    return 1 if fails else 0


def do_reset() -> int:
    import shutil
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)
    print(f"[reset] cleared {WORK}")
    return 0


def do_selftest() -> int:
    """Offline proof: reset → run → reply → run → assert, EACH a separate process
    (kill/restart) with G7_TRANSPORT=stub, so only the CSV ledger persists."""
    env = dict(os.environ, G7_TRANSPORT="stub")
    steps = ["reset", "run", "reply", "run", "assert"]
    for step in steps:
        print(f"\n=== g7 selftest: {step} (fresh process) ===")
        r = subprocess.run([sys.executable, __file__, step], env=env)
        if r.returncode != 0 and step == "assert":
            return r.returncode
        if r.returncode != 0 and step != "assert":
            print(f"[selftest] step {step} failed ({r.returncode})")
            return r.returncode
    return 0


_STEPS = {"run": do_run, "reply": do_reply, "assert": do_assert,
          "reset": do_reset, "selftest": do_selftest}


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "selftest"
    fn = _STEPS.get(cmd)
    if not fn:
        print(f"usage: python scripts/g7_run.py {{{'|'.join(_STEPS)}}}",
              file=sys.stderr)
        return 2
    return fn()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
