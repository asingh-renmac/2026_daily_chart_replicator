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
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"

for _p in (SRC, SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


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
