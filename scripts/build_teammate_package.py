"""Build the distributable teammate zip for the chat lane (G9e step 4).

    python scripts/build_teammate_package.py
    python scripts/build_teammate_package.py --list   # print the manifest, write nothing

Produces `dist/haver-chart-<sha>-<date>.zip`:

    haver-chart/
      README_FIRST.md      <- haver_chart/TEAMMATE_SETUP.md
      configure.py         <- haver_chart/configure.py, hoisted to the root
      repo/                <- this repo, tracked files only, internal docs stripped
      vendor/charts/       <- renmac_chart_style.py
      vendor/haver_mcp/    <- db.py + queries.py + a .env EXAMPLE

WHY VENDOR the two external modules instead of telling teammates to clone
`econ-templates` and `2026_haver_mcp`: both are small, pure-python and imported by
path, and neither repo is one a teammate should need commit access to. Copying the
three files that are actually imported turns a three-checkout setup into an unzip.
The cost is drift, which `--list` makes visible: the manifest prints each vendored
file's mtime so a stale copy is obvious at build time.

WHY `git ls-tree` and not a folder walk: the working tree holds `notes/` (client
email content), `data/` and `outputs/` (rendered charts, ledgers) and `.env` files.
Shipping only COMMITTED, tracked files means nothing untracked can ride along, and
the `EXCLUDE` list then drops the internal design docs on top of that.

The zip carries NO credential. `configure.py --neon-url` writes it on the teammate's
machine from whatever Aman sends them out of band.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

for _s in (sys.stdout, sys.stderr):          # cp1252 consoles mangle the report glyphs
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

# Internal-only: the design record and the agent config. A teammate needs the user
# guide, not the plan that produced it, and `prompt.md`/`plan.md` name unshipped work.
EXCLUDE = {"plan.md", "prompt.md", "sandbox.json"}
EXCLUDE_DIRS = (".cursor/",)

VENDOR = (
    (Path("C:/Users/asingh/new_work/econ-templates/charts/renmac_chart_style.py"),
     "vendor/charts/renmac_chart_style.py"),
    (Path("C:/Users/asingh/new_work/2026_haver_mcp/server/__init__.py"),
     "vendor/haver_mcp/server/__init__.py"),
    (Path("C:/Users/asingh/new_work/2026_haver_mcp/server/db.py"),
     "vendor/haver_mcp/server/db.py"),
    (Path("C:/Users/asingh/new_work/2026_haver_mcp/server/queries.py"),
     "vendor/haver_mcp/server/queries.py"),
)

ENV_EXAMPLE = """\
# Read-only Haver catalog (Neon). Ask Aman for the URL; it is deliberately NOT in
# the zip. `configure.py --neon-url "..."` writes this file for you.
#
# Without it the lane still renders, but catalog search goes dark and every series
# given only as a description will park instead of binding.
NEON_READONLY_DATABASE_URL=
"""


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    keep = []
    for rel in out.stdout.splitlines():
        if rel in EXCLUDE or any(rel.startswith(d) for d in EXCLUDE_DIRS):
            continue
        keep.append(rel)
    return keep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="print the manifest, write nothing")
    args = ap.parse_args()

    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.strip()

    files = tracked()
    setup = ROOT / "haver_chart" / "TEAMMATE_SETUP.md"
    configure = ROOT / "haver_chart" / "configure.py"

    print(f"repo   : {ROOT}  @ {sha}")
    print(f"tracked: {len(files)} file(s) after excluding "
          f"{', '.join(sorted(EXCLUDE))} and {', '.join(EXCLUDE_DIRS)}")
    print("\nvendored (mtime shows drift):")
    missing = [src for src, _ in VENDOR if not src.exists()] + \
              [p for p in (setup, configure) if not p.exists()]
    for src, dest in VENDOR:
        when = (datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d")
                if src.exists() else "MISSING")
        print(f"  {when}  {dest:<44} <- {src}")
    if missing:
        print("\nERROR: cannot build, missing:\n  " +
              "\n  ".join(str(m) for m in missing))
        return 2

    if dirty:
        # The zip is built from HEAD, so uncommitted edits are silently NOT shipped.
        # Warn rather than block: a dirty plan.md is normal and irrelevant here.
        n = len(dirty.splitlines())
        print(f"\nNOTE: {n} uncommitted change(s) — the zip ships HEAD ({sha}), not "
              f"your working tree.")

    if args.list:
        print("\n--list — nothing written.")
        return 0

    DIST.mkdir(exist_ok=True)
    out = DIST / f"haver-chart-{sha}-{datetime.now():%Y%m%d}.zip"
    top = "haver-chart"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(ROOT / rel, f"{top}/repo/{rel}")
        for src, dest in VENDOR:
            z.write(src, f"{top}/{dest}")
        z.writestr(f"{top}/vendor/haver_mcp/config/.env.example", ENV_EXAMPLE)
        z.write(setup, f"{top}/README_FIRST.md")
        z.write(configure, f"{top}/configure.py")

    size = out.stat().st_size
    print(f"\nWrote {out}  ({size / 1024:.0f} KB, "
          f"{len(files) + len(VENDOR) + 3} entries)")

    # Separate, tiny zip: Claude's skill uploader wants an archive whose ROOT is a
    # folder named exactly like the `name:` in SKILL.md's frontmatter, so it cannot
    # just be a path inside the package zip. Mirrors haver-data-pull.zip next door.
    skill_src = ROOT / "haver_chart" / "SKILL.md"
    name = ""
    for line in skill_src.read_text(encoding="utf-8").splitlines()[:6]:
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
            break
    if not name:
        print("WARNING: no `name:` in SKILL.md frontmatter — skill zip not built")
        return 0
    skill_out = DIST / f"{name}.zip"
    with zipfile.ZipFile(skill_out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}/SKILL.md", skill_src.read_text(encoding="utf-8"))
    print(f"Wrote {skill_out}  ({skill_out.stat().st_size:,} bytes) — optional skill")

    print("\nSend both zips. Send the Neon URL separately — it is not in either.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
