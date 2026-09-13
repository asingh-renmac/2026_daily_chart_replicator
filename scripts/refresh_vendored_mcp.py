"""Refresh the chart lane's VENDORED copy of the metadata server modules from a checkout.

Why this exists. `haver_chart/bootstrap.py` points HAVER_MCP_SERVER at
`<pkg>/vendor/haver_mcp/server` whenever the variable is unset, and on the AVD it is
unset -- so `src/haver_search.py` imports its search SQL from a COPY, not from git.
Nothing updates that copy: a `git pull` of either repo leaves it untouched. Found
2026-09-12 with the copy still dated 2026-06-14, three months stale, which meant a
ranker fix that shipped to the shared droplet never reached the resolver it was written
for.

Run with --check in a health sweep to see the drift; run with --apply to close it.
Reports every file and never copies silently.

    python scripts/refresh_vendored_mcp.py --check
    python scripts/refresh_vendored_mcp.py --apply
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

REPO_SERVER = Path("C:/Users/madz/Work/asingh/haver-data/repo/server")
VENDOR_SERVER = Path("C:/Users/madz/Work/asingh/haver-chart/vendor/haver_mcp/server")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else ""


def verify(vendor: Path) -> int:
    """Import the vendored module the way haver_search does and check it renders.

    Matching hashes prove the bytes arrived; they do not prove the module imports or
    that the SQL it builds is the banded form. This is the difference between a file
    being copied and a fix being live.
    """
    import sys

    sys.path.insert(0, str(vendor))
    try:
        import queries
    except Exception as exc:
        print(f"FAIL  vendored queries.py will not import: {type(exc).__name__}: {exc}")
        return 1
    sql, params = queries.build_search_query("payrolls")
    banded = (getattr(queries, "FTS_BAND_FLOOR", None) is not None
              and "GREATEST(" in sql and "similarity(" in sql)
    print(f"  imports        : ok")
    print(f"  banded ranking : {banded}")
    print(f"  bind count     : {len(params)} (4 rank + 3 WHERE = 7 when banded)")
    if not banded:
        print("FAIL  the vendored ranker is still the old saturated ts_rank form")
        return 1
    print("vendored search ranking is live")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report drift and change nothing (the default)")
    ap.add_argument("--apply", action="store_true", help="copy differing files")
    ap.add_argument("--verify-only", action="store_true",
                    help="import the vendored module and check the ranking is banded")
    ap.add_argument("--repo", type=Path, default=REPO_SERVER)
    ap.add_argument("--vendor", type=Path, default=VENDOR_SERVER)
    args = ap.parse_args()

    if not args.repo.is_dir():
        print(f"FAIL  checkout not found: {args.repo}")
        return 1
    if not args.vendor.is_dir():
        print(f"FAIL  vendored dir not found: {args.vendor}")
        return 1

    # Before the drift walk: --verify-only asks a different question (is the fix live?)
    # and must not fall through the "nothing stale, exit 0" path below.
    if args.verify_only:
        return verify(args.vendor)

    # Refresh what is vendored; never ADD. A file absent from the vendor dir is absent
    # on purpose -- the chart lane imports `queries` and `db` and nothing else, so
    # server.py and embedding.py were deliberately left out. Treating "missing" as
    # "stale" would quietly widen the vendored surface and drag in voyageai behind it.
    stale, same, absent = [], 0, []
    for src in sorted(args.repo.glob("*.py")):
        dst = args.vendor / src.name
        if not dst.is_file():
            absent.append(src.name)
            continue
        d_src, d_dst = digest(src), digest(dst)
        if d_src == d_dst:
            same += 1
            continue
        stale.append((src, dst, d_src, d_dst))
        print(f"  DRIFT {src.name:16} repo={d_src}  vendor={d_dst}")

    if absent:
        print(f"  not vendored (left alone, by design): {', '.join(absent)}")
    print(f"\n{same} file(s) already current, {len(stale)} stale")
    if not stale:
        print("vendored copy matches the checkout")
        return 0

    if not args.apply:
        print("re-run with --apply to copy (the lane must restart afterwards to reload)")
        return 1

    for src, dst, _, _ in stale:
        # copy2 keeps mtime, so the vendored file's date reflects the SOURCE commit
        # rather than the moment of copying -- future drift checks stay meaningful.
        shutil.copy2(src, dst)
        print(f"  copied {src.name} -> {dst}")
    print(f"\ncopied {len(stale)} file(s). RESTART the chart lane to load them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
