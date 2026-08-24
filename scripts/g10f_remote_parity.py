"""G10f — prove the REMOTE lane draws the same pixels as the local one (plan.md §14.10).

G9b proved the wrapper: ledger row → `render_chart`'s argument surface → identical PNG.
It drives `lane.render()` in-process, so it never touches HTTP, the tunnel or Entra.
This harness closes that gap by taking a PNG that came back **through the connector** and
diffing it against a local render made **right now**.

Why "right now" is the whole design. The remote chart was drawn from data the server
pulled at that moment. Compare it to anything rendered earlier and a revision to IP or a
survey index shows up as differing pixels, which reads exactly like a transport bug. The
control render removes the only variable that matters — the data vintage — so a
difference that survives it is a real difference.

    # 1. render through the connector, save the PNG, and have Claude print the
    #    render_chart arguments it used as JSON
    # 2. then:
    python scripts/g10f_remote_parity.py remote.png --spec spec.json

    # or, for a chart that exists as a finished ledger row:
    python scripts/g10f_remote_parity.py remote.png --ledger 2026-08-11 --chart-id <id>

`--spec` is a JSON object of `render_chart` keyword arguments — the `series` list plus
whatever else was passed (`title`, `subtitle`, `axis_mode`, `sample_start`, …). Ask
Claude for it verbatim rather than retyping: a spec you reconstruct by hand tests your
transcription, not the lane.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from g9b_lane_equivalence import compare, daily_control  # noqa: E402
from haver_chart import lane  # noqa: E402


def local_from_spec(spec_path: Path) -> str:
    """Render the same specification locally, now."""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if "series" not in spec:
        raise SystemExit(f"{spec_path.name} has no `series` key — it should be the "
                         f"render_chart arguments as a JSON object.")
    # A remote result carries `chart_id`/`chart_url` where a local one carries `path`,
    # and `filename` would collide the control with the thing under test. Drop all three
    # rather than making the operator hand-edit the JSON Claude gave them.
    for k in ("chart_id", "chart_url", "path", "filename"):
        spec.pop(k, None)
    return lane.render(filename="g10f_control", **spec)["path"]


def local_from_ledger(date: str, chart_id: str) -> str:
    ledger = ROOT / "data" / f"ledger_backfill_{date}.csv"
    rows = [r for r in csv.DictReader(ledger.open(encoding="utf-8"))
            if r.get("chart_id", "").endswith(chart_id) and r.get("chart_spec")]
    if not rows:
        raise SystemExit(f"no row ending {chart_id!r} in {ledger.name}")
    return daily_control(rows[0], f"g10f_{rows[0]['chart_id']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("remote", type=Path, help="the PNG saved from the connector")
    ap.add_argument("--spec", type=Path, help="JSON of the render_chart arguments used")
    ap.add_argument("--ledger", help="ledger date, e.g. 2026-08-11")
    ap.add_argument("--chart-id", default="", help="chart_id (or its suffix)")
    args = ap.parse_args()

    if not args.remote.is_file():
        raise SystemExit(f"no such file: {args.remote}")
    if bool(args.spec) == bool(args.ledger):
        raise SystemExit("pass exactly one of --spec or --ledger")

    print("=" * 78)
    print("G10f remote parity — connector render  vs  same-vintage local render")
    print("=" * 78)

    control = (local_from_spec(args.spec) if args.spec
               else local_from_ledger(args.ledger, args.chart_id))

    same, detail = compare(Path(control), args.remote)
    print(f"\n  remote : {args.remote}")
    print(f"  control: {control}")
    print(f"\n  {'MATCH' if same else 'DIFFER'} — {detail}")

    if same:
        print("\n  The remote lane renders the same pixels as the local one. Transport,\n"
              "  auth and the server install change nothing about the chart.")
    else:
        # Naming the likely causes matters more than the pixel count: the failure modes
        # here are few and specific, and a stale knowledge store is by far the commonest.
        print("\n  Investigate before accepting. In rough order of likelihood:\n"
              "    * the server's knowledge/ copy is older than this machine's, so a\n"
              "      legend label differs — compare the two legend_labels.json\n"
              "    * a different matplotlib or font version on the server\n"
              "    * the spec sent remotely was not the spec rendered here\n"
              "    * a genuine wrapper difference in the HTTP path")
    print("=" * 78)
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
