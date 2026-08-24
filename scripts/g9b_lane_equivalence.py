"""G9b gate — prove the chat lane renders the SAME pixels as the daily lane (§13.10.1).

Takes a finished `data/ledger_backfill_*.csv` row that the daily lane already rendered,
pushes the same specification through `render_chart`'s PUBLIC argument surface, and
compares the two PNGs pixel by pixel.

Going through the tool's arguments is the point. Handing `render_row` the ledger's row
dict wholesale would prove nothing — it would test `render_row`, which is not the thing
under test. The wrapper's job is to reconstruct that row from flat tool arguments, and
any key it drops, renames or mistypes shows up here as different pixels.

    python scripts/g9b_lane_equivalence.py [ledger_date] [chart_id_suffix]
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Windows consoles default to cp1252, which cannot encode the arrows/dashes below.
# Set it here so the harness runs without PYTHONIOENCODING in the environment.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from haver_chart import lane  # noqa: E402

# argv is read inside main() so `g10f_remote_parity` can import `compare` and
# `daily_control` without this module consuming ITS command line.

# Slot keys the renderer consumes. Anything else in the ledger slot (search_attempts,
# candidate_detail, relevance…) is resolution bookkeeping and must NOT reach render.
SLOT_KEYS = ("resolved", "codes", "formula", "applied_transform", "axis", "lag",
             "plot_kind", "proposed_legend", "base_descriptor", "description")


def tool_args(row: dict) -> dict:
    """Ledger row → the flat kwargs an operator would pass to `render_chart`."""
    cs = json.loads(row["chart_spec"] or "{}")
    slots = json.loads(row["series"] or "[]")
    la, ra = cs.get("left_axis") or {}, cs.get("right_axis") or {}
    return dict(
        series=[{k: s.get(k) for k in SLOT_KEYS if s.get(k) not in (None, "", [])}
                for s in slots],
        title=row.get("chosen_title") or "",
        subtitle=row.get("subtitle") or "",
        st_force=bool(row.get("st_force")),
        no_title=bool(row.get("no_title")),
        sample_start=str(cs.get("sample_start") or ""),
        axis_mode=cs.get("axis_mode") or "shared",
        recession_shading=bool(cs.get("recession_shading")),
        left_min=la.get("min"), left_max=la.get("max"),
        right_min=ra.get("min"), right_max=ra.get("max"),
        x_tick_years=cs.get("x_tick_years"),
        x_label_fmt=cs.get("x_label_fmt") or "",
        end_series=cs.get("end_series") or "",
        x_pad_periods=cs.get("x_pad_periods"),
    )


def daily_control(row: dict, cid: str) -> str:
    """Re-render the row through the DAILY seam, right now, with today's data.

    Needed because the archived daily PNG carries the data vintage of the day it was
    made. A chart whose series has since printed a new observation will differ from it
    for a reason that has nothing to do with the wrapper. Comparing the chat render
    against a same-vintage daily render separates the two: chat == fresh daily means
    the wrapper is faithful and the archive is simply older."""
    import build_chart as BC
    control = ROOT / "outputs" / "chat" / "_control" / f"{cid}.png"
    control.parent.mkdir(parents=True, exist_ok=True)
    BC.render_row({"chart_spec": json.loads(row["chart_spec"]),
                   "series": json.loads(row["series"]),
                   "subtitle": row.get("subtitle") or "",
                   "st_force": row.get("st_force"),
                   "no_title": row.get("no_title"),
                   "chosen_title": row.get("chosen_title") or ""}, str(control))
    return str(control)


def compare(a: Path, b: Path) -> tuple[bool, str]:
    """Pixel comparison. Byte equality is too strict — matplotlib stamps PNG metadata,
    so two identical figures differ in bytes. Pixels are what the operator sees."""
    from PIL import Image, ImageChops
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return False, f"size {ia.size} vs {ib.size}"
    diff = ImageChops.difference(ia, ib)
    box = diff.getbbox()
    if box is None:
        return True, f"identical ({ia.size[0]}x{ia.size[1]} px)"
    hist = diff.convert("L").histogram()
    n_diff = sum(hist[1:])
    return False, (f"{n_diff} differing px of {ia.size[0] * ia.size[1]}, "
                   f"bbox={box}, max delta={max(i for i, c in enumerate(hist) if c)}")


def main() -> int:
    DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-08-11"
    ONLY = sys.argv[2] if len(sys.argv) > 2 else ""
    LEDGER = ROOT / "data" / f"ledger_backfill_{DATE}.csv"
    RENDERS = ROOT / "data" / f"backfill_{DATE}" / "renders"

    rows = list(csv.DictReader(LEDGER.open(encoding="utf-8")))
    rows = [r for r in rows if r.get("chart_spec") and r.get("series")]
    if ONLY:
        rows = [r for r in rows if r["chart_id"].endswith(ONLY)]
    if not rows:
        print(f"no usable rows in {LEDGER.name}")
        return 2

    print("=" * 78)
    print(f"G9b lane equivalence — {LEDGER.name}  ({len(rows)} chart(s))")
    print("  daily render  vs  chat lane render_chart(), pixel diff")
    print("=" * 78)

    n_ok = n_bad = 0
    for row in rows:
        cid = row["chart_id"]
        daily = RENDERS / f"{cid}.png"
        if not daily.exists():
            print(f"\n{cid}: SKIP — no daily render at {daily}")
            continue
        try:
            out = lane.render(filename=f"g9b_{cid}", **tool_args(row))
        except Exception as exc:
            print(f"\n{cid}: FAIL — chat lane raised {type(exc).__name__}: {exc}")
            n_bad += 1
            continue
        same, detail = compare(daily, Path(out["path"]))
        print(f"\n{cid}: {'MATCH' if same else 'DIFFER vs archive'} — {detail}")
        print(f"   daily: {daily}")
        print(f"   chat : {out['path']}")
        print(f"   plotted={out['plotted']}  end={out['end']}")
        if not same:
            ctl = daily_control(row, cid)
            same_ctl, detail_ctl = compare(Path(ctl), Path(out["path"]))
            verdict = ("MATCH vs same-vintage daily → archive is stale data, "
                       "wrapper is faithful" if same_ctl
                       else "STILL DIFFERS vs same-vintage daily → WRAPPER BUG")
            print(f"   control: {verdict} — {detail_ctl}")
            print(f"            {ctl}")
            same = same_ctl
        n_ok, n_bad = (n_ok + 1, n_bad) if same else (n_ok, n_bad + 1)

    print("\n" + "=" * 78)
    print(f"G9b: {n_ok} match, {n_bad} differ")
    print("=" * 78)
    return 0 if n_bad == 0 and n_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
