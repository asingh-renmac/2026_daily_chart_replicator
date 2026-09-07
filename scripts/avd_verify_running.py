"""Is the RUNNING chart server actually serving the tools the checkout defines?

Two different questions get conflated when a tool "isn't there":
  1. does the code on disk define it?           -- a git question
  2. did the running PROCESS load that code?    -- a restart question

Answering only the first is how a restart gets repeated pointlessly. This reports the
file's write time against the process start time, then imports the deployed module in
HTTP mode and lists what it registers, so the on-disk answer is measured rather than
assumed.

Run from the repo root. Read-only, no DLX, no port bound (mcp.run is under __main__).
"""
import asyncio
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Derived from the WORKING DIRECTORY, not __file__: this gets copied to a scratch path
# outside the checkout (a scratch file inside it blocks the next `git pull` as untracked),
# so __file__ would point at the wrong tree entirely.
ROOT = Path(os.environ.get("HAVER_CHART_REPO") or Path.cwd()).resolve()
if not (ROOT / "haver_chart" / "server.py").exists():
    sys.exit(f"run this from the repo root, or set HAVER_CHART_REPO; got {ROOT}")
sys.path.insert(0, str(ROOT))

print("commit      :", subprocess.run(
    ["git", "-c", f"safe.directory={ROOT.as_posix()}", "log", "-1", "--format=%h %s"],
    capture_output=True, text=True).stdout.strip()[:70])

src = ROOT / "haver_chart" / "server.py"
written = datetime.fromtimestamp(src.stat().st_mtime)
print("server.py   :", written)

ps = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "(Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
     "Where-Object { $_.CommandLine -like '*haver_chart\\server.py*' } | "
     "Sort-Object CreationDate | Select-Object -First 1)."
     # Formatted sortably in PowerShell rather than parsed in Python: the default
     # rendering is LOCALE-dependent ("Sunday, September 6, 2026 8:59:02 PM"), which no
     # fixed strptime format survives.
     "CreationDate.ToString('yyyy-MM-dd HH:mm:ss')"],
    capture_output=True, text=True)
started_raw = ps.stdout.strip()
print("started     :", started_raw or "(no running server found)")
if started_raw:
    started = datetime.strptime(started_raw[:19], "%Y-%m-%d %H:%M:%S")
    stale = written > started
    print("VERDICT     :", "STALE — restart needed, the process predates the file"
          if stale else "process started AFTER the file was written — code IS live")

# HTTP mode is what the AVD serves; the probe is registered only under it.
os.environ["HAVER_CHART_HTTP"] = "1"
from haver_chart import server  # noqa: E402

names = sorted(t.name for t in asyncio.run(server.mcp.list_tools()))
print("http tools  :", names)
uris = sorted(str(r.uri) for r in asyncio.run(server.mcp.list_resources()))
print("resources   :", uris)
for tool in ("ui_probe", "ui_probe_plain"):
    print(f"  {tool:<15}", "registered" if tool in names else "ABSENT")
