"""
g8c_render.py — texas E2E continuation: resolution APPROVE → title round-trip →
render-over-original (x-scale overlay) + standalone RenMac reconstruction.

Runs AFTER g8c_texas.py (which resolved DFBACTS/DBACTS and held at the confirm_all
summary). Here Aman's `[texas_mfg_outlook] approve` flows: the chart ratifies, the
learning store records the approved binds (Part 4), the title round-trip posts Opus
proposals, then the reconstruction is pulled LIVE and laid over the original at the
same x-scale for geometry review (both lines, shared axis, recession months, derived
end). Title is PROVISIONAL (option 1) pending Aman's pick — geometry is the review.
"""
import json
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
import resolve as R           # noqa: E402
import teams as TM            # noqa: E402
import propose as P           # noqa: E402
import transforms as T        # noqa: E402
import g4_lib as G            # noqa: E402
from render import PlotSeries, RenderSpec, render  # noqa: E402
from renmac_chart_style import C_NAVY, C_SECONDARY  # noqa: E402

OUT = ROOT / "outputs" / "g8c"
OUT.mkdir(parents=True, exist_ok=True)
SHOT = str(ROOT / "data" / "backfill_2026-06-29" / "assets" /
           "2026-06-29_a2f643425c_0.png")

# ── load the resolved texas row, reset to RESOLVED, fresh stub ────────────────
led = L.Ledger(str(OUT / "ledger_texas.csv"))
row = led.get("msg_texas", 0)
assert row, "run scripts/g8c_texas.py first (creates the resolved ledger row)"
row["status"] = L.RESOLVED
for k in ("ask_cursor", "ask_sig", "thread_id"):
    row[k] = ""
stub_path = OUT / "stub_render.json"
if stub_path.exists():
    stub_path.unlink()
tp = TM.StubTransport(str(stub_path))
thread = A.chart_thread("g8c", "texas_mfg_outlook")


def title_proposals(r):
    """Opus title proposals against the confirmed ChartSpec (Part 3)."""
    try:
        return P.propose_titles(
            subject="Texas Manufacturing Outlook Survey — general business activity, "
                    "current conditions vs six-months-ahead expectations",
            series=["Texas Mfg Outlook Survey: General Business Activity, 6 Months Ahead",
                    "Texas Mfg Outlook Survey: General Business Activity"],
            transform="level (diffusion index, SA, % balance)",
            span="monthly, 2004-2026", n=2)
    except Exception as exc:
        print(f"  [propose_titles unavailable: {exc} — using provisional fallback]")
        return [{"title": "Texas factory sentiment rebounds as expectations lead the turn",
                 "subtitle": "Dallas Fed survey, SA % balance, monthly"},
                {"title": "Dallas Fed activity firms with six-month outlook out front",
                 "subtitle": "diffusion index, SA % balance"}]


# ── flow: confirm_all approve → RESOLUTION_APPROVED → title round-trip ────────
A.run_resolution_roundtrip(led, tp, confirm_ticker=lambda c: True,
                           metrics_path=str(OUT / "approval_metrics.csv"))   # posts summary
tp.queue_reply(thread, "[texas_mfg_outlook] approve")
A.run_resolution_roundtrip(led, tp, confirm_ticker=lambda c: True,
                           metrics_path=str(OUT / "approval_metrics.csv"))   # approve → ratified
print(f"resolution status after approve: {row['status']}  "
      f"(learning store written; metric logged)")

A.run_title_roundtrip(led, tp, title_proposals, from_status=L.RESOLUTION_APPROVED,
                      gate_status=L.AWAITING_TITLE, parser=TM.parse_title_reply)
opts = row.get("title_options") or []
print(f"title status: {row['status']}  — Opus proposed {len(opts)} option(s):")
for i, o in enumerate(opts, 1):
    print(f"   {i}. {o['title']}" + (f"   — {o['subtitle']}" if o.get('subtitle') else ""))
print("   reply `[texas_mfg_outlook] 1` / `2` / `title=… subtitle=…` to finalize.")

# ── learning-store verification (Part 4) ─────────────────────────────────────
learned = R.load_learned()
for d in ("Texas Mfg Outlook Survey: General Business Activity, 6 Months Ahead",
          "Texas Mfg Outlook Survey: General Business Activity"):
    e = learned.get(R._norm_key(d))
    print(f"  learned: {d!r} → {e.get('code') if e else None}")

# ── pull the two series + recession LIVE, render ─────────────────────────────
print("\nLIVE pull (DLX):")
dfb = G.pull("DFBACTS", "SURVEYS", "M", start="2004-01-01")   # 6 months ahead (navy)
dba = G.pull("DBACTS", "SURVEYS", "M", start="2004-01-01")    # current (light blue)
rec = G.pull("RECESSM2", "USECON", "M", start="2004-01-01")

end = max(dfb.values.index.max(), dba.values.index.max())
start = pd.Timestamp("2004-06-30")
print(f"\nderived display end (latest plotted period) = {end.date()}  "
      f"(read said 'sample_end_read=25' → derived from data, not the read)")
runs = G._recession_runs(rec.values)
print("RECESSM2==1 runs in window:",
      [(s.date().isoformat(), e.date().isoformat()) for s, e in runs if e >= start])

prov = (opts or [{}])[0]
title = prov.get("title") or "Texas manufacturing sentiment"
subtitle = prov.get("subtitle") or "Dallas Fed survey, SA % balance, monthly"

# standalone RenMac reconstruction (PROVISIONAL title)
spec = RenderSpec(
    title=title, subtitle=subtitle, axis_mode="shared",
    recession=rec.values, x_range=(start, end), y_left=(-75, 75))
render([PlotSeries("General Business Activity, 6 months ahead", dfb.values, "L", C_NAVY),
        PlotSeries("General Business Activity", dba.values, "L", C_SECONDARY)],
       spec, save_path=str(OUT / "texas_reconstruction.png"))
print(f"  wrote {OUT/'texas_reconstruction.png'}")

# render-over-original at x-scale (the geometry check)
calib = G.calibrate(SHOT, x_ticks=None,
                    x_years=[2005, 2010, 2015, 2020, 2025],
                    yL_ticks=None, yL_vals=[75, 50, 25, 0, -25, -50, -75],
                    yR_vals=[75, 50, 25, 0, -25, -50, -75])
G.overlay(calib,
          [{"series": dfb.values, "axis": "L", "color": "#E0218A",
            "label": "recon: 6 months ahead (DFBACTS)"},
           {"series": dba.values, "axis": "L", "color": "#FF8C00",
            "label": "recon: current (DBACTS)"}],
          str(OUT / "texas_overlay.png"),
          title="texas_mfg_outlook — reconstruction over original (x-scale); "
                "red = RECESSM2==1",
          debug_grid=True, recession=rec.values)

led.save()
print("\nDONE — geometry render ready for review. Title held at "
      f"{row['status']} (provisional option 1 used in the standalone).")
