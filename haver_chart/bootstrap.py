"""Import bootstrap + boundary enforcement for the chat lane (plan.md §13.5-13.6).

Three jobs, all of which have to happen BEFORE the wrapped modules are imported:

1. **sys.path.** `src/build_chart.py` does a bare `import transforms` / `import resolve`,
   so `src/` must be importable; it adds `scripts/` itself for `g4_lib`. Claude Desktop
   spawns the server by absolute path with no PYTHONPATH, so we do it here.

2. **stdout is the JSON-RPC channel.** `g4_lib.pull`, `haver_search` and the Haver
   package itself print progress to STDOUT. On a stdio MCP that is not noise, it is
   protocol corruption — a stray "  pulled ..." line lands mid-frame and the client
   drops the connection with no useful error. Everything the wrapped code prints is
   diagnostic, so it is redirected to stderr (which Claude Desktop captures into
   `mcp-server-haver-chart.log`).

3. **The stores are READ-ONLY from this lane (§13.6).** A chat bind is ratified by a
   human looking at a chart, but NOT through `confirm_all`, and `run_daily` depends on
   those files. Rather than trusting the wrapper never to call `save_*`, the savers are
   replaced with a tripwire that raises. This is process-local — it patches the module
   object inside THIS interpreter only, so the daily lane is untouched.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"

for _p in (SRC, SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# --- The three relocatable paths (G9e step 1) --------------------------------------
# `render.py`, `haver_search.py` and `resolve.py` each read one variable AT IMPORT TIME,
# defaulting to a path on the machine this repo was written on. Both steps below must
# therefore run before those modules are imported, which is why they execute at module
# scope here rather than in a function a caller has to remember to invoke.

# Teammate zip layout: this file is <pkg>/repo/haver_chart/bootstrap.py.
_PKG_ROOT = REPO_ROOT.parent
_VENDORED = {
    "ECON_TEMPLATES_CHARTS": _PKG_ROOT / "vendor" / "charts",
    "HAVER_MCP_SERVER": _PKG_ROOT / "vendor" / "haver_mcp" / "server",
    "CLARIFIED_KNOWLEDGE_DIR": _PKG_ROOT / "knowledge",
}

#: Variables filled in from the package layout, for `selftest.py` to report honestly —
#: otherwise an adopted path is indistinguishable from one the operator actually set.
ADOPTED_FROM_PACKAGE: set[str] = set()


def _load_env_file() -> None:
    """Read `config/.env` if present.

    On a laptop the paths arrive in the `env` block Claude Desktop injects. A server has
    no MCP client spawning it, so its environment has to come from a file (§14.8). Safe
    unconditionally: `load_dotenv` never overrides a variable already in the process, so
    the injected block still wins and stdio is unchanged. An absent file is the normal
    case, not an error.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / "config" / ".env")
    except ImportError:          # degrade, don't die: only the catalog needs it
        print("[haver-chart] python-dotenv not installed; config/.env not read",
              file=sys.stderr)


def _adopt_vendored_paths() -> None:
    """Point a variable at the copy vendored inside the package when it is unset, or
    set to somewhere that no longer exists.

    The defaults in `src/` are absolute paths on ONE developer's machine, so without
    this the lane runs only where something supplies all three by hand. That gap hides
    in normal teammate use — Claude Desktop injects them — and surfaces the moment
    anything else drives the lane: `selftest.py` from a shell, or a server started
    directly. Since the zip already carries these files, requiring the operator to
    re-state where they are is a configuration step that can only be got wrong.

    A variable pointing at a missing directory is treated as absent rather than
    honoured. Moving the package after setup leaves exactly that — Claude's config
    still holds the old absolute paths — and it is the difference between a startup
    crash on `renmac_chart_style` and a lane that simply keeps working. An explicit
    path that RESOLVES always wins; the substitution is announced on stderr, because
    quietly using different files than you were told to is its own kind of bug.

    Probing for the directory keeps this inert in the source checkout, which has no
    `vendor/` sibling.
    """
    for var, path in _VENDORED.items():
        current = os.environ.get(var)
        if current and Path(current).is_dir():
            continue                                   # explicit and real
        if not path.is_dir():
            continue                                   # nothing vendored to offer
        if current:
            print(f"[haver-chart] {var}={current} does not exist; falling back to the "
                  f"copy in this package ({path})", file=sys.stderr)
        os.environ[var] = str(path)
        ADOPTED_FROM_PACKAGE.add(var)


_load_env_file()
_adopt_vendored_paths()


@contextlib.contextmanager
def quiet_stdout():
    """Route anything the wrapped code prints to stderr, keeping stdout clean for
    JSON-RPC. Must wrap every tool body AND the initial imports (importing Haver
    prints 'Haver path setting remains unchanged.')."""
    with contextlib.redirect_stdout(sys.stderr):
        yield


class StoreWriteAttempted(RuntimeError):
    """A chat-lane call tried to WRITE a knowledge store. Hard stop per §13.6/G9d."""


def seal_stores(resolve_module) -> None:
    """Replace every `save_*` on the resolve module with a raising tripwire.

    §13.6 is a correctness property, not a coding convention: an unratified chat bind
    written into `learned_descriptors.json` is the May-12 `mpcuhsro` poisoning class
    with a new entry point, and the 2026-07-30 `ptfneh` incident showed a wrong bind
    can look entirely plausible on screen. Sealing makes the violation impossible to
    reach by accident (a future edit, a refactor, a copied helper) rather than merely
    discouraged. Reads — `load_legend` / `load_learned` / `load_trusted` /
    `load_clarified` — are untouched, so the lane still inherits every ratified label.
    """
    for name in [n for n in dir(resolve_module) if n.startswith("save_")]:
        def _sealed(*_a, __name=name, **_kw):
            raise StoreWriteAttempted(
                f"resolve.{__name}() is disabled in the chat lane: the learning stores "
                f"are READ-ONLY here (plan.md §13.6). A chat binding is ratified by eye, "
                f"not through confirm_all, and run_daily depends on these files. "
                f"Writing them is gated behind G9d."
            )
        setattr(resolve_module, name, _sealed)
