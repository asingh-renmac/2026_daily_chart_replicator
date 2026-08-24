"""G10d — prove the transport swap without changing what stdio does (plan.md §14.4, §14.9).

Three checks, and the FIRST one is the point. `HAVER_CHART_HTTP` is a switch bolted onto a
server that a dozen teammate zips already spawn over stdio, so the thing most worth
proving is not that HTTP works — it is that the default path is untouched.

  1. stdio unchanged     a real JSON-RPC handshake with no new environment: `initialize`,
                         `tools/list`, both tools present. This is what every teammate's
                         Claude Desktop does.
  2. HTTP fails fast     `HAVER_CHART_HTTP=1` with the auth secrets missing must REFUSE to
                         start and name every missing variable. A server that came up
                         unauthenticated on a port a tunnel is about to publish is worse
                         than one that will not come up (§14.7).
  3. HTTP serves         with credentials present it binds loopback and `/health` answers
                         the render-liveness payload — not a bare 200, which is exactly
                         how a wedged renderer would hide (§14.16).

Check 3 uses throwaway Entra values. It proves the process wires up, binds and routes; it
does NOT prove a real sign-in, which needs the tunnel and the app registration and is
G10e's job.

    python scripts/g10d_http_check.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "haver_chart" / "server.py"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = int(os.environ.get("G10D_PORT", "8123"))   # not 8100: never collide with a real one
_RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, what: str, detail: str = "") -> None:
    _RESULTS.append((bool(ok), what, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {what}" + (f"  — {detail}" if detail else ""))


def _env(**over: str) -> dict:
    """Parent environment plus overrides, with the HTTP switch cleared unless asked.

    Cleared explicitly so a stray variable in the developer's shell cannot make check 1
    silently test the wrong transport."""
    env = os.environ.copy()
    for k in list(env):
        if k.startswith("HAVER_CHART_"):
            del env[k]
    env.update(over)
    return env


# ───────────────────────────── 1. stdio is unchanged ─────────────────────────────
print("\n1. stdio transport (the default every teammate zip uses)")


def stdio_handshake() -> tuple[bool, str]:
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)], cwd=str(ROOT), env=_env(),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", bufsize=1)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "g10d", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    try:
        proc.stdin.write("".join(json.dumps(m) + "\n" for m in msgs))
        proc.stdin.flush()
        deadline = time.time() + 90
        tools: list[str] = []
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue                       # not JSON-RPC: vendor chatter, ignore
            if msg.get("id") == 2:
                tools = [t["name"] for t in msg.get("result", {}).get("tools", [])]
                break
        return bool(tools), ", ".join(sorted(tools)) or "no tools/list response"
    finally:
        proc.kill()
        proc.wait(timeout=10)


try:
    ok, detail = stdio_handshake()
    check(ok, "server answers a JSON-RPC handshake over stdio", detail)
    check("resolve_series" in detail and "render_chart" in detail,
          "both tools are advertised", detail)
except Exception as exc:
    check(False, "server answers a JSON-RPC handshake over stdio",
          f"{type(exc).__name__}: {exc}")


# ─────────────────────── 2. HTTP refuses to start unauthenticated ────────────────
print("\n2. HTTP mode fails fast without credentials (§14.7)")

proc = subprocess.run([sys.executable, str(SERVER)], cwd=str(ROOT),
                      env=_env(HAVER_CHART_HTTP="1"),
                      capture_output=True, text=True, timeout=120)
err = (proc.stderr or "") + (proc.stdout or "")
check(proc.returncode != 0, "the process refuses to start", f"exit {proc.returncode}")
named = [v for v in ("HAVER_CHART_AZURE_CLIENT_ID", "HAVER_CHART_AZURE_TENANT_ID",
                     "HAVER_CHART_AZURE_CLIENT_SECRET", "HAVER_CHART_PUBLIC_URL",
                     "HAVER_CHART_JWT_SIGNING_KEY") if v in err]
check(len(named) == 5, "it names every missing variable", f"{len(named)}/5 named")


# ──────────────────────────── 3. HTTP binds and serves ───────────────────────────
print(f"\n3. HTTP mode binds 127.0.0.1:{PORT} and /health reports render liveness")

http_env = _env(
    HAVER_CHART_HTTP="1",
    HAVER_CHART_HTTP_PORT=str(PORT),
    HAVER_CHART_PUBLIC_URL=f"http://127.0.0.1:{PORT}",
    HAVER_CHART_JWT_SIGNING_KEY="g10d-throwaway-signing-key-not-a-secret",
    HAVER_CHART_AZURE_CLIENT_ID="00000000-0000-0000-0000-000000000001",
    HAVER_CHART_AZURE_TENANT_ID="00000000-0000-0000-0000-000000000002",
    HAVER_CHART_AZURE_CLIENT_SECRET="g10d-throwaway",
)
srv = subprocess.Popen([sys.executable, str(SERVER)], cwd=str(ROOT), env=http_env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")
try:
    body, last = None, ""
    deadline = time.time() + 120
    while time.time() < deadline:
        if srv.poll() is not None:
            last = "process exited: " + (srv.stdout.read() or "")[-400:]
            break
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=3) as r:
                body = json.loads(r.read().decode("utf-8"))
                break
        except Exception as exc:               # not up yet, or refused
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0)

    check(body is not None, "/health answers over HTTP", last if body is None else "200 OK")
    if body is not None:
        check(body.get("status") == "ok", "status is ok", json.dumps(body))
        # The whole point of this endpoint: a wedged renderer must be distinguishable
        # from a healthy one, so the payload has to carry render state.
        check("rendering" in body and "last_render_finished" in body,
              "payload reports render liveness, not just process liveness",
              f"rendering={body.get('rendering')} "
              f"last_render_finished={body.get('last_render_finished')}")
    # An unauthenticated tool call must NOT be served. /health is deliberately open;
    # the MCP endpoint behind it is the thing Entra guards.
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/mcp", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            check(False, "/mcp rejects an unauthenticated request", f"got {r.status}")
    except urllib.error.HTTPError as exc:
        check(exc.code in (400, 401, 403, 406),
              "/mcp rejects an unauthenticated request", f"HTTP {exc.code}")
    except Exception as exc:
        check(False, "/mcp rejects an unauthenticated request",
              f"{type(exc).__name__}: {exc}")
finally:
    srv.kill()
    srv.wait(timeout=10)

n_bad = sum(1 for ok, _, _ in _RESULTS if not ok)
print("\n" + "=" * 78)
print(f"G10d: {len(_RESULTS) - n_bad}/{len(_RESULTS)} checks passed"
      + ("" if not n_bad else f"  — {n_bad} FAILED"))
print("=" * 78)
raise SystemExit(1 if n_bad else 0)
