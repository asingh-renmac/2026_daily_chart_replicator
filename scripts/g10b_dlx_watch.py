"""G10b — does DLX keep serving after the desktop session is disconnected? (plan.md §14.9)

This is the decisive Phase 0 test, and the one that can end §14. A remote chart lane is a
long-lived process on a machine nobody is looking at. If Haver only answers while a human
is signed in and looking at the DLX window, the whole proposal needs a different host and
a different licence story — and that is worth knowing before a line of §14.4 gets written.

It probes TWO shapes, because the server is both of them at different moments:

  in-process   one interpreter that called `Haver.direct("on")` once and keeps pulling.
               This is the server between restarts.
  subprocess   a fresh interpreter that imports Haver and pulls from cold, every cycle.
               This is the server AFTER a restart, and the stricter test — a cached
               handle can outlive the credential that would be needed to re-establish it.

Either can fail alone and it means something different. In-process fails but subprocess
survives: the long-lived handle rots and the server needs periodic recycling. Subprocess
fails but in-process survives: the server works until the first restart and then cannot
come back unattended, which is worse, because it fails at 3am and not in front of you.

How to run it (the disconnect is the point — do not skip it):

    python scripts/g10b_dlx_watch.py --hours 16

  1. Start it in the AVD session.
  2. Wait for the first cycle to print PASS.
  3. Close the remote-desktop window. DISCONNECT, do not SIGN OUT — signing out ends the
     session and kills this process, which proves nothing.
  4. Reconnect later and read the log. The last PASS timestamp is the durability number,
     and that number is the argument for or against G10i.

Writes `outputs/g10b/dlx_watch_<host>_<start>.log`, appended and flushed every cycle, so a
killed session still leaves the evidence on disk.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "g10b"

# Windows consoles default to cp1252, which cannot encode the box characters below.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# A deliberately tiny pull: one short monthly series. The question is whether DLX ANSWERS,
# not whether it is fast, and a big pull would confound a timeout with a refusal.
TICKER, DATABASE, START = "LR", "USECON", "2025-01-01"

# Run by the subprocess probe. Kept as source text rather than a module so this file stays
# a single self-contained thing you can copy onto a bare host.
CHILD = f"""
import json, warnings
out = {{"ok": False, "rows": 0, "last": None, "error": None}}
try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import Haver
    Haver.direct("on")
    df = Haver.data(["{TICKER}"], "{DATABASE}", startdate="{START}")
    if df is None or isinstance(df, dict):
        out["error"] = "Haver returned %r instead of a frame" % (df,)
    else:
        out["ok"] = True
        out["rows"] = int(len(df))
        out["last"] = str(df.index[-1])
except Exception as exc:
    out["error"] = "%s: %s" % (type(exc).__name__, exc)
print("PROBE" + json.dumps(out))
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Log:
    """Print and append in one call, flushing every line — the session may die mid-run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("a", encoding="utf-8")

    def __call__(self, line: str = "") -> None:
        print(line)
        self.fh.write(line + "\n")
        self.fh.flush()

    def close(self) -> None:
        self.fh.close()


def probe_in_process() -> dict:
    """Pull through the Haver handle this interpreter already holds."""
    out = {"ok": False, "rows": 0, "last": None, "error": None}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import Haver  # noqa: N813 - Haver's own casing
        # Idempotent per process; the first call is the one that binds to DLX.
        Haver.direct("on")
        df = Haver.data([TICKER], DATABASE, startdate=START)
        if df is None or isinstance(df, dict):
            out["error"] = f"Haver returned {df!r} instead of a frame"
        else:
            out.update(ok=True, rows=int(len(df)), last=str(df.index[-1]))
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def probe_subprocess(python: str, timeout: int) -> dict:
    """Pull from a cold interpreter — the 'server restarted unattended' case."""
    try:
        proc = subprocess.run([python, "-c", CHILD], capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "rows": 0, "last": None,
                "error": f"timed out after {timeout}s (a login prompt would look like this)"}
    for line in (proc.stdout or "").splitlines():
        if line.startswith("PROBE"):
            return json.loads(line[len("PROBE"):])
    tail = ((proc.stderr or proc.stdout or "").strip().splitlines() or ["no output"])[-1]
    return {"ok": False, "rows": 0, "last": None, "error": f"no probe line; last output: {tail}"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=float, default=16.0, help="how long to watch (default 16)")
    ap.add_argument("--every", type=int, default=10, help="minutes between cycles (default 10)")
    ap.add_argument("--timeout", type=int, default=120, help="subprocess probe timeout, seconds")
    ap.add_argument("--python", default=sys.executable, help="interpreter for the cold probe")
    args = ap.parse_args()

    import getpass
    import socket

    started = datetime.now()
    stamp = started.strftime("%Y%m%d_%H%M%S")
    host = socket.gethostname()
    log = Log(OUT / f"dlx_watch_{host}_{stamp}.log")

    deadline = started + timedelta(hours=args.hours)
    log("=" * 78)
    log("G10b DLX durability watch  (plan.md §14.9 Phase 0)")
    log(f"host      {host}   user {getpass.getuser()}")
    log(f"python    {args.python}")
    log(f"probe     {TICKER}@{DATABASE} from {START}")
    log(f"started   {_now()}   until {deadline:%Y-%m-%d %H:%M:%S}  every {args.every} min")
    log("=" * 78)
    log("")
    log("DISCONNECT the remote-desktop window now — do NOT sign out.")
    log("")
    log(f"{'cycle':>5}  {'time':19}  {'elapsed':>9}  {'in-process':>10}  {'subprocess':>10}  note")
    log("-" * 78)

    cycle = 0
    last_both_ok = None
    first_failure = None

    try:
        while datetime.now() < deadline:
            cycle += 1
            t0 = datetime.now()
            elapsed = t0 - started

            inproc = probe_in_process()
            cold = probe_subprocess(args.python, args.timeout)

            hh, rem = divmod(int(elapsed.total_seconds()), 3600)
            mm = rem // 60
            note = ""
            if inproc["ok"] and cold["ok"]:
                last_both_ok = t0
                note = f"{cold['rows']} rows, last {cold['last']}"
            else:
                if first_failure is None:
                    first_failure = (t0, elapsed)
                note = (inproc["error"] or cold["error"] or "")[:60]

            log(f"{cycle:>5}  {t0:%Y-%m-%d %H:%M:%S}  {hh:>6}h{mm:02d}m  "
                f"{'PASS' if inproc['ok'] else 'FAIL':>10}  "
                f"{'PASS' if cold['ok'] else 'FAIL':>10}  {note}")

            # Sleep the remainder of the interval so cycles stay on a clean cadence even
            # when a probe blocks for a minute.
            spent = (datetime.now() - t0).total_seconds()
            time.sleep(max(0.0, args.every * 60 - spent))
    except KeyboardInterrupt:
        log("")
        log("interrupted by operator")

    log("-" * 78)
    log("")
    log("VERDICT")
    if first_failure is None:
        held = datetime.now() - started
        log(f"  DLX answered every cycle for {held.total_seconds() / 3600:.1f}h, including from a")
        log("  cold interpreter. G10b PASSES on durability. Record this number in §14.11.")
    else:
        t_fail, e_fail = first_failure
        log(f"  First failure at {t_fail:%Y-%m-%d %H:%M:%S}, "
            f"{e_fail.total_seconds() / 3600:.1f}h after start.")
        if last_both_ok:
            log(f"  Last clean cycle {last_both_ok:%Y-%m-%d %H:%M:%S}.")
        log("  That interval is the pilot's usable window, and the direct argument for a")
        log("  dedicated always-on host (G10i). Check the log column that failed: a cold")
        log("  subprocess failing alone is the worse case — the server cannot restart itself.")
    log("")
    log(f"  log: {log.path}")
    log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
