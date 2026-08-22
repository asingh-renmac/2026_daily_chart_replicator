"""One-shot machine setup for a teammate's copy of the haver-chart lane (G9e step 4).

Ships at the ROOT of the teammate zip, one level above `repo/`. It does the three
things that are otherwise fiddly to get right by hand, in the order that fails
cheapest first:

  1. Check the interpreter actually has the lane's imports.
  2. Write `vendor/haver_mcp/config/.env` with the read-only Neon URL.
  3. Merge a `haver-chart` entry into Claude Desktop's config, keeping any MCP
     servers already there and backing the file up first.

It writes ABSOLUTE paths resolved from its own location, which is the whole point:
the three env vars exist so this package can sit anywhere on any machine, and a
teammate should never have to hand-edit a path into JSON.

    python configure.py --neon-url "postgresql://...neon.tech/haver?sslmode=require"
    python configure.py --show          # print what it WOULD write, touch nothing
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):          # cp1252 consoles mangle the report glyphs
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

PKG = Path(__file__).resolve().parent
REPO = PKG / "repo"
CHARTS = PKG / "vendor" / "charts"
SERVER = PKG / "vendor" / "haver_mcp" / "server"
ENV_FILE = SERVER.parent / "config" / ".env"          # haver_search loads this exact path
DEFAULT_KNOWLEDGE = r"P:\Public\RenMac_Chart_Knowledge"

REQUIRED = ("fastmcp", "matplotlib", "pandas", "psycopg", "dotenv", "Haver")


def _claude_config() -> Path:
    """Where Claude Desktop reads its config — which depends on how it was installed.

    The MSIX/Store build does NOT use %APPDATA%\\Claude; it redirects to a per-package
    LocalCache tree. Writing the plain path on an MSIX machine creates a file Claude
    never reads, and the failure is silent — you restart, no tools appear, and nothing
    says why. So prefer whichever config ALREADY EXISTS, and only fall back to creating
    the plain path when neither does.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    roaming = Path(os.environ.get("APPDATA", Path.home()))
    msix = sorted((local / "Packages").glob(
        "Claude_*/LocalCache/Roaming/Claude/claude_desktop_config.json"))
    plain = roaming / "Claude" / "claude_desktop_config.json"
    if plain.exists():
        return plain
    if msix:
        return msix[0]
    # Neither exists: an MSIX install still has the package folder even before the
    # config is written, so look for that before defaulting to the plain path.
    pkg_dirs = sorted((local / "Packages").glob("Claude_*/LocalCache/Roaming/Claude"))
    return (pkg_dirs[0] / "claude_desktop_config.json") if pkg_dirs else plain


def _check_interpreter(py: Path) -> list[str]:
    """Return the names of REQUIRED modules `py` cannot import.

    `importlib.util` is a submodule: `import importlib` alone does not load it, and
    the resulting AttributeError used to make this function report EVERY module as
    missing and abort before writing the Claude config. Import the submodule
    explicitly, and surface stderr if the probe itself dies.
    """
    code = ("import importlib.util, sys\n"
            "print(','.join(m for m in sys.argv[1:]"
            " if importlib.util.find_spec(m) is None))")
    out = subprocess.run([str(py), "-c", code, *REQUIRED],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        err = (out.stderr or out.stdout or "").strip() or f"exit {out.returncode}"
        raise RuntimeError(f"interpreter probe failed: {err}")
    return [m for m in out.stdout.strip().split(",") if m]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default=sys.executable,
                    help="interpreter Claude should launch (default: the one running this)")
    ap.add_argument("--neon-url", default="",
                    help="read-only catalog URL; skip to leave any existing .env alone")
    ap.add_argument("--knowledge", default=DEFAULT_KNOWLEDGE,
                    help=f"shared store folder (default: {DEFAULT_KNOWLEDGE})")
    ap.add_argument("--show", action="store_true", help="print the plan, write nothing")
    args = ap.parse_args()

    py = Path(args.python).resolve()
    cfg_path = _claude_config()
    entry = {
        "command": str(py).replace("\\", "/"),
        "args": [str(REPO / "haver_chart" / "server.py").replace("\\", "/")],
        "env": {
            "ECON_TEMPLATES_CHARTS": str(CHARTS).replace("\\", "/"),
            "HAVER_MCP_SERVER": str(SERVER).replace("\\", "/"),
            "CLARIFIED_KNOWLEDGE_DIR": args.knowledge,
        },
    }

    print("=" * 78)
    print("haver-chart — machine setup")
    print("=" * 78)
    print(f"\npackage      : {PKG}")
    print(f"interpreter  : {py}")
    print(f"claude config: {cfg_path}")
    print(f"\nMCP entry to install:\n{json.dumps({'haver-chart': entry}, indent=2)}")

    if args.show:
        print("\n--show — nothing written.")
        return 0

    print("\n1. Interpreter")
    if not py.exists():
        print(f"  FAIL  no such interpreter: {py}")
        return 2
    try:
        missing = _check_interpreter(py)
    except RuntimeError as exc:
        print(f"  FAIL  {exc}")
        print("        If the packages are already installed, paste the JSON above "
              "into Claude → Settings → Developer → Edit Config (see README_FIRST.md).")
        return 2
    if missing:
        print(f"  FAIL  missing module(s): {', '.join(missing)}")
        print(f"        {py} -m pip install -r "
              f"{REPO / 'haver_chart' / 'requirements.txt'}")
        if "Haver" in missing:
            print("        `Haver` is the vendor package and needs a Haver DLX "
                  "entitlement — ask IT if pip cannot see it.")
        print("        Or paste the JSON above into Claude → Settings → Developer "
              "→ Edit Config (see README_FIRST.md).")
        return 2
    print(f"  ok    all {len(REQUIRED)} required modules import")

    print("\n2. Catalog credential")
    if args.neon_url:
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        ENV_FILE.write_text(f"NEON_READONLY_DATABASE_URL={args.neon_url}\n", encoding="utf-8")
        print(f"  ok    wrote {ENV_FILE}")
    elif ENV_FILE.exists():
        print(f"  ok    kept existing {ENV_FILE}")
    else:
        # Not fatal: haver_search degrades to "catalog unavailable" and description-only
        # slots park. That is a usable, if annoying, lane — worth saying out loud rather
        # than blocking setup on a credential the teammate may still be waiting for.
        print(f"  WARN  no {ENV_FILE}; catalog search will be dark and every "
              "description-only series will park until you re-run with --neon-url")

    print("\n3. Shared knowledge store")
    kn = Path(args.knowledge)
    if kn.is_dir():
        n = len(list(kn.glob("*.json")))
        print(f"  ok    {kn} ({n} store file(s))")
    else:
        print(f"  WARN  {kn} unreachable; the lane still runs but you get no learned "
              "binds or ratified legends — map the drive and re-run")

    print("\n4. Claude Desktop config")
    cfg: dict = {}
    if cfg_path.exists():
        backup = cfg_path.with_suffix(f".backup-{datetime.now():%Y%m%d-%H%M%S}.json")
        shutil.copyfile(cfg_path, backup)
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  FAIL  existing config is not valid JSON ({exc}); "
                  f"backup at {backup} — fix or delete it, then re-run")
            return 2
        print(f"  ok    backed up to {backup.name}")
    servers = cfg.setdefault("mcpServers", {})
    kept = sorted(k for k in servers if k != "haver-chart")
    servers["haver-chart"] = entry
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print("  ok    wrote haver-chart" + (f"; kept {', '.join(kept)}" if kept else ""))

    print("\nNext:")
    print(f"  1. {py} {REPO / 'haver_chart' / 'selftest.py'}")
    print("  2. Quit Claude Desktop from the tray (closing the window is not enough), "
          "then reopen it.")
    print("  3. Ask Claude: \"what haver-chart tools do you have?\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
