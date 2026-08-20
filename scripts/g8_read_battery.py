"""
g8_read_battery.py — G8a: run the Stage-1 VISION READ over every raw-Haver chart
in the sample set (+ the ingested texas_mfg_outlook chart) and emit a REVIEWABLE
per-field report for human ground-truthing.

No self-grading: it prints what vision read per field (axis tick ranges → derived
axis_mode, start + end-derive behavior, per-series base_descriptor / applied
transform / native-MA flag / formula / axis / lag / SA). If a corrections file is
present (outputs/g8/corrections.json — Aman's image ground truth), it overlays it
and scores read-vs-truth for the overridden fields. The reads.json output is the
artifact reviewed at the G8a STOP.

CORRECTION FILE SCHEMA (outputs/g8/corrections.json) — override only what you set,
keyed by battery label + series index (a string):

  {
    "<label>": {
      "axis_mode":    "shared" | "dual",        # optional, chart-level
      "sample_start": "1992",                    # optional, chart-level
      "series": {
        "0": { "ticker": "code@db",              # resolved series (esp. description-only)
               "axis":   "L" | "R" | "shared",   # per-series axis side
               "freq":   "monthly"|"quarterly"|"weekly"|"annual"|"daily",  # native freq
               "native_ma": true | false },      # true = the MA/%chg IS the native name
        "1": { ... }
      }
    }
  }

Labels are exactly as printed, e.g. "sample1_chart1.png", "sample3.docx:image3.png",
"ingested:2026-06-29_a2f643425c_0.png".

Usage:
  python scripts/g8_read_battery.py                 # all raw-Haver charts
  python scripts/g8_read_battery.py --only sample1  # subset by label substring
  python scripts/g8_read_battery.py --limit 3       # first N (quick/cheap check)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

import chartspec as CS          # noqa: E402
import resolve as R             # noqa: E402
from extract_assets import extract_docx_images  # noqa: E402

NOTES = ROOT / "notes"
OUT = ROOT / "outputs" / "g8"
CORR_FILE = OUT / "corrections.json"

LOOSE = [f"sample{n}_chart{c}.png" for n, cs in
         {1: 3, 2: 3, 6: 3, 7: 3, 9: 2}.items() for c in range(1, cs + 1)]
DOCX_RAW = {
    "sample3.docx": ["image3.png"],
    "sample4.docx": ["image1.png"],
    "sample5.docx": ["image1.png", "image2.png"],
    "sample8.docx": ["image1.png", "image2.png", "image3.png"],
}
INGESTED = ROOT / "data" / "backfill_2026-06-29" / "assets"


def _charts() -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for name in LOOSE:
        p = NOTES / name
        if p.exists():
            out.append((name, p.read_bytes()))
    for docx, members in DOCX_RAW.items():
        imgs = {img.name: img.data for img in extract_docx_images(NOTES / docx)}
        for m in members:
            if m in imgs:
                out.append((f"{docx}:{m}", imgs[m]))
    if INGESTED.exists():
        for p in sorted(INGESTED.glob("*.png")):
            out.append((f"ingested:{p.name}", p.read_bytes()))
    return out


def _ax(a) -> str:
    if a is None or not a.has_range:
        return "—"
    lab = f" {a.label}" if a.label else ""
    return f"[{a.min:g}…{a.max:g}]{lab}"


def _print_spec(label: str, spec: CS.ChartSpec) -> None:
    end = ("(derive at pull)" if spec.end_mode == "derive_at_pull"
           else f"{spec.sample_end_read} (forecast)")
    print(f"\n[{label}]  n_series={spec.n_series}  axis_mode={spec.axis_mode.upper()}"
          f"  L={_ax(spec.left_axis)}  R={_ax(spec.right_axis)}")
    print(f"     x: {spec.sample_start} → {end}   units={spec.units!r}  "
          f"recession={'Y' if spec.recession_shading else 'N'}")
    for i, s in enumerate(spec.series):
        route = "DLX-confirm" if s.resolvable_headless else "→search/Teams"
        flag = "  ⚠NATIVE-MA?" if s.native_ma_ambiguous else ""
        print(f"  S{i} axis={s.axis:6} lag={str(s.lag or '-'):5} "
              f"freq={s.freq_hint:9} sa={s.sa_hint:7} "
              f"conf={s.confidence:.2f}  [{route}]{flag}")
        print(f"       desc    : {s.description!r}")
        if s.formula:
            print(f"       formula : {s.formula!r}   (ticker_read advisory: {s.ticker_read!r})")
        else:
            print(f"       base    : {s.base_descriptor!r}")
            print(f"       applied : {s.applied_transform!r}")


# ───────────────────────────── scoring vs truth ────────────────────────────
def _code(t: str) -> str:
    return (t or "").split("@")[0].strip().lower()


def _score(results: list[dict], corr: dict) -> None:
    by_label = {r["label"]: r for r in results if "spec" in r}
    hits = misses = 0
    print("\n" + "=" * 78)
    print("READ vs GROUND TRUTH (corrections.json) — overridden fields only")
    print("=" * 78)
    for label, c in corr.items():
        if label.startswith("_"):       # doc/schema keys in the file
            continue
        r = by_label.get(label)
        if not r:
            print(f"\n[{label}]  (no read in this run)")
            continue
        spec = r["spec"]
        print(f"\n[{label}]")
        if "axis_mode" in c:
            ok = spec.get("axis_mode") == c["axis_mode"]
            hits, misses = hits + ok, misses + (not ok)
            print(f"  axis_mode : read={spec.get('axis_mode')!r:8} truth={c['axis_mode']!r:8} "
                  f"{'OK' if ok else 'XX'}")
        if "sample_start" in c:
            rs = str(spec.get("sample_start") or "")
            ok = str(c["sample_start"]) in rs or rs in str(c["sample_start"])
            hits, misses = hits + ok, misses + (not ok)
            print(f"  start     : read={rs!r:10} truth={str(c['sample_start'])!r:10} "
                  f"{'OK' if ok else 'XX'}")
        for idx, sc in (c.get("series") or {}).items():
            srs = (spec.get("series") or [])
            try:
                s = srs[int(idx)]
            except (ValueError, IndexError):
                print(f"  S{idx}: (no read series at this index)")
                continue
            if "axis" in sc:
                ok = (s.get("axis") == sc["axis"])
                hits, misses = hits + ok, misses + (not ok)
                print(f"  S{idx} axis : read={str(s.get('axis'))!r:8} truth={sc['axis']!r:8} "
                      f"{'OK' if ok else 'XX'}")
            if "freq" in sc:
                rf, tf = R._freq_norm(s.get("freq_hint")), R._freq_norm(sc["freq"])
                ok = bool(rf) and rf == tf
                hits, misses = hits + ok, misses + (not ok)
                print(f"  S{idx} freq : read={str(s.get('freq_hint'))!r:11} "
                      f"truth={sc['freq']!r:11} {'OK' if ok else 'XX'}")
            if "ticker" in sc:
                truth_t = sc["ticker"]
                read_t = s.get("ticker_read")
                if isinstance(truth_t, list):       # sum/expression → addend list
                    print(f"  S{idx} tckr : sum truth={truth_t}  formula="
                          f"{s.get('formula')!r}  [Stage-2 confirms each addend]")
                elif not read_t:
                    print(f"  S{idx} tckr : read=None (description-only) → truth fills "
                          f"{truth_t!r}  [Stage-2 resolves]")
                else:
                    ok = _code(read_t) == _code(truth_t)
                    hits, misses = hits + ok, misses + (not ok)
                    print(f"  S{idx} tckr : read={read_t!r:14} truth={truth_t!r:18} "
                          f"{'OK' if ok else 'XX'}")
            if "native_ma" in sc:
                amb = s.get("native_ma_ambiguous")
                verdict = ("flagged→confirm" if amb else
                           ("stripped" if s.get("applied_transform") else "kept"))
                print(f"  S{idx} MA   : truth native_ma={sc['native_ma']}  "
                      f"read={verdict} (applied={s.get('applied_transform')!r})")
    tot = hits + misses
    print("\n" + "-" * 78)
    print(f"  scored fields: {hits}/{tot} match"
          + (f"  ({100*hits/tot:.0f}%)" if tot else ""))
    print("-" * 78)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default="", help="filter labels by substring")
    ap.add_argument("--limit", type=int, default=0, help="only first N charts")
    ap.add_argument("--model", default=CS.DEFAULT_MODEL)
    ap.add_argument("--from-cache", action="store_true",
                    help="score the saved reads.json instead of re-calling vision "
                         "(deterministic; scores the exact reads you reviewed)")
    args = ap.parse_args(argv)

    if args.from_cache:
        reads_p = OUT / "reads.json"
        if not reads_p.exists():
            print(f"ERROR: {reads_p} not found — run a live read first.", file=sys.stderr)
            return 2
        results = json.loads(reads_p.read_text(encoding="utf-8"))
        print("=" * 78)
        print(f"G8a SCORING from cache ({reads_p.name}) — {len(results)} chart(s)")
        print("=" * 78)
        if CORR_FILE.exists():
            _score(results, json.loads(CORR_FILE.read_text(encoding="utf-8")))
        else:
            print(f"  no {CORR_FILE.name} present — nothing to score.")
        return 0

    charts = _charts()
    if args.only:
        charts = [c for c in charts if args.only in c[0]]
    if args.limit:
        charts = charts[: args.limit]

    print("=" * 78)
    print(f"G8a VISION-READ BATTERY (v2: tick-derived axis_mode + end-derive) — "
          f"{len(charts)} chart(s), model={args.model}")
    print("  reads are HYPOTHESES for review — no ticker is bound here; Stage 2 confirms")
    print("=" * 78)

    results, n_series, n_formula, n_desc, n_dual, n_amb, n_err = [], 0, 0, 0, 0, 0, 0
    for label, data in charts:
        try:
            spec = CS.read_chart_spec(data, model=args.model)
        except Exception as e:
            n_err += 1
            print(f"\n[{label}]  READ ERROR: {e}")
            results.append({"label": label, "error": str(e)})
            continue
        _print_spec(label, spec)
        n_series += len(spec.series)
        n_formula += sum(1 for s in spec.series if s.resolvable_headless)
        n_desc += sum(1 for s in spec.series if not s.resolvable_headless)
        n_dual += int(spec.axis_mode == "dual")
        n_amb += sum(1 for s in spec.series if s.native_ma_ambiguous)
        results.append({"label": label, "spec": spec.to_dict()})

    print("\n" + "-" * 78)
    print(f"  charts read : {len(charts) - n_err}/{len(charts)}  (read errors: {n_err})")
    print(f"  axis_mode   : {n_dual} dual, {len(charts) - n_err - n_dual} shared (tick-derived)")
    print(f"  series      : {n_series} total  →  {n_formula} formula (DLX-confirm)  |  "
          f"{n_desc} description-only (→search/Teams)")
    print(f"  native-MA?  : {n_amb} series flagged for bot confirmation")
    print("-" * 78)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "reads.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"  wrote {OUT / 'reads.json'}")

    if CORR_FILE.exists():
        corr = json.loads(CORR_FILE.read_text(encoding="utf-8"))
        _score(results, corr)
    else:
        print(f"  (no {CORR_FILE.name} yet — add your ground-truth key to score; "
              "schema in this file's docstring)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
