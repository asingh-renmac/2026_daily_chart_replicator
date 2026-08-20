"""
g8c_finalize.py — texas title approve (option 1, Aman's subtitle) → APPROVED →
final RenMac render. Renders ONLY after BOTH approves (resolution earlier, title now).
"""
import sys
from pathlib import Path

import pandas as pd

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import approval as A          # noqa: E402
import ledger as L            # noqa: E402
import teams as TM            # noqa: E402
import g4_lib as G            # noqa: E402
from render import PlotSeries, RenderSpec, render  # noqa: E402
from renmac_chart_style import C_NAVY, C_SECONDARY  # noqa: E402

OUT = ROOT / "outputs" / "g8c"
TITLE = "Texas factories see brighter days ahead despite soft present"
SUBTITLE = "current vs six-months-ahead (diffusion index, SA)"

led = L.Ledger(str(OUT / "ledger_texas.csv"))
row = led.get("msg_texas", 0)
assert row, "run scripts/g8c_render.py first (posts the title options)"
tp = TM.StubTransport(str(OUT / "stub_render.json"))
thread = row.get("thread_id") or A.chart_thread("g8c", "texas_mfg_outlook")

# Aman's title pick: option 1 title + his subtitle (override form)
tp.queue_reply(thread, f"[texas_mfg_outlook] title={TITLE} subtitle={SUBTITLE}")
A.run_title_roundtrip(led, tp, lambda r: r.get("title_options") or [],
                      from_status=L.RESOLUTION_APPROVED, gate_status=L.AWAITING_TITLE,
                      parser=TM.parse_title_reply)
print(f"status: {row['status']}  title={row.get('chosen_title')!r}  "
      f"subtitle={row.get('subtitle')!r}")
assert row["status"] == L.APPROVED, f"expected APPROVED, got {row['status']}"

# final render (both approves in hand) — data from the g4_lib parquet cache
dfb = G.pull("DFBACTS", "SURVEYS", "M", start="2004-01-01")
dba = G.pull("DBACTS", "SURVEYS", "M", start="2004-01-01")
rec = G.pull("RECESSM2", "USECON", "M", start="2004-01-01")
end = max(dfb.values.index.max(), dba.values.index.max())
spec = RenderSpec(title=row["chosen_title"], subtitle=row["subtitle"],
                  axis_mode="shared", recession=rec.values,
                  x_range=(pd.Timestamp("2004-06-30"), end), y_left=(-75, 75))
render([PlotSeries("General Business Activity, 6 months ahead", dfb.values, "L", C_NAVY),
        PlotSeries("General Business Activity", dba.values, "L", C_SECONDARY)],
       spec, save_path=str(OUT / "texas_reconstruction.png"))
led.save()
print(f"FINAL render written: {OUT/'texas_reconstruction.png'}  (status={row['status']})")
