"""Can this host serve an MCP Apps UI resource at all? (G20b, step 1 of 2)

G20b asks whether a `ui://` resource survives the Cloudflare tunnel and Entra. Before
that can be answered there is a cheaper question: does the serving venv even have the
pieces? FastMCP ships the `ui://` plumbing in the base package but the prefab widget
components -- including the built-in `Choice` provider, which is the parked-slot picker's
exact shape -- live behind the `fastmcp[apps]` extra.

Read-only. Imports nothing that touches DLX, so it runs over SSH.
"""
import importlib
import sys

print("python      :", sys.executable)

try:
    import fastmcp
    print("fastmcp     :", fastmcp.__version__)
except Exception as exc:
    print("fastmcp     : MISSING", exc)
    raise SystemExit(1)

try:
    from fastmcp.utilities.mime import UI_MIME_TYPE
    print("ui mime     :", UI_MIME_TYPE)
except Exception as exc:
    print("ui mime     : unavailable", exc)

for name in ("prefab_ui", "fastmcp.apps", "fastmcp.apps.app"):
    try:
        importlib.import_module(name)
        print(f"{name:<12}: importable")
    except Exception as exc:
        print(f"{name:<12}: NO -- {type(exc).__name__}")

try:
    from fastmcp.apps.choice import Choice  # noqa: F401
    print("Choice      : importable -- the built-in picker is available")
except Exception as exc:
    print(f"Choice      : NO -- {type(exc).__name__}; needs pip install 'fastmcp[apps]'")

# A bare ui:// resource needs no prefab components at all -- it is just HTML at a
# ui:// uri. Worth knowing separately, because if this works the transport question
# can be answered without installing anything into the serving venv.
try:
    from fastmcp import FastMCP
    probe = FastMCP("probe")

    @probe.resource("ui://probe/hello")
    def _hello() -> str:
        return "<!doctype html><h1>hello</h1>"

    print("ui:// route : registered OK on a throwaway server")
except Exception as exc:
    print(f"ui:// route : FAILED -- {type(exc).__name__}: {exc}")
