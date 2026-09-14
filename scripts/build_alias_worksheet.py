"""Build a tick-to-label worksheet turning alias review into reading, not recall.

The CSV of aliases has no Haver ticker and cannot be scored without one, but asking anyone
to supply 200 tickers from memory is how a labelling job quietly never finishes. So this
does the looking-up: every alias gets the candidates the RESOLVER itself would see, each
with the catalogue's own descriptor, frequency, adjustment and database, and the reviewer
marks a row rather than recalling a code.

Ticking is also how the three answers that matter get expressed, which is the point of the
row layout:

  * tick ONE candidate                -> that is the series.
  * tick SEVERAL                      -> they are all acceptable. This is not a hedge: the
                                         catalogue really does hold jcdrgi and jcdrgim under
                                         one identical descriptor, and jcsxeh under both
                                         usecon and usna. An eval set that admits one of a
                                         mirror pair marks a correct answer wrong.
  * tick the PARK row                 -> the alias is genuinely ambiguous and the resolver
                                         SHOULD refuse. 29 rows are typed "Ambiguous
                                         shorthand"; scoring those as failures would teach
                                         the resolver to guess, which is how "establishment
                                         survey" ended up bound to a reference-week marker.
  * tick NONE and type a ticker       -> retrieval missed it entirely. These are the rows
                                         worth the most: each one is an alias-table entry.

Picking a candidate also fills in ticker, frequency, adjustment and database by itself, so
the reviewer never types those.

    python scripts/build_alias_worksheet.py --n 200
    -> eval/alias_worksheet.csv   (open in Excel, put x in `pick`)
       eval/ and NOT outputs/, which is gitignored: a day of human labelling is not a
       build artefact and must not sit where a clean checkout drops it.
       then: python scripts/compile_alias_evalset.py
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, "C:/Users/asingh/new_work/2026_haver_mcp/server")

import haver_search as HS  # noqa: E402
import resolve as R  # noqa: E402

FIELDS = ["group", "alias", "means", "row_type", "pick", "ticker", "descriptor",
          "frequency", "adjustment", "database", "sim", "note"]


def stratified(rows: list[dict], n: int, seed: int = 20260913) -> list[dict]:
    by = defaultdict(list)
    for r in rows:
        by[r.get("category") or "?"].append(r)
    rng = random.Random(seed)
    out: list[dict] = []
    for _, group in sorted(by.items()):
        rng.shuffle(group)
        out.extend(group[:max(1, round(n * len(group) / len(rows)))])
    out.sort(key=lambda r: (r.get("category") or "", r.get("alias") or ""))
    return out[:n]


def candidates_for(alias: str, top: int) -> list[dict]:
    """Exactly what the resolver would see: the union over its own search attempts."""
    slot = {"description": alias, "base_descriptor": alias}
    seen, pool = set(), []
    for att in R.build_search_attempts(slot):
        for h in R._do_search(HS.search, att["query"], att["databases"], att["sa_status"]):
            code = (h.get("code") or "").strip()
            if code and code.lower() not in seen:
                seen.add(code.lower())
                pool.append(h)
    for h in pool:
        h["_sim"] = R.descriptor_similarity(alias, h.get("descriptor") or "")
    # Ranked by the resolver's OWN similarity, so the reviewer reads them in the order the
    # resolver would weigh them rather than in catalogue order.
    pool.sort(key=lambda h: -h["_sim"])
    return pool[:top]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--csv", default="fixtures/econ_aliases.csv")
    ap.add_argument("--out", default="eval/alias_worksheet.csv")
    args = ap.parse_args()

    rows = list(csv.DictReader((ROOT / args.csv).open(encoding="utf-8-sig")))
    sample = stratified(rows, args.n)
    print(f"{len(rows)} aliases; worksheet sample = {len(sample)}", flush=True)

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_rows: list[dict] = []
    t0 = time.perf_counter()

    for i, row in enumerate(sample, 1):
        alias = row["alias"]
        means = row.get("underlying_series") or ""
        # NOT entry_id on its own: it identifies the SERIES, not the alias, and 1,265 rows
        # share 529 of them ("labor force" and "LF" are both US0142). Keyed on it alone, two
        # aliases land in one group and the compiler keeps whichever it reads first.
        gid = f"{i:04d}-{row.get('entry_id') or 'NA'}"
        for c in candidates_for(alias, args.top):
            code = c.get("code") or ""
            out_rows.append({
                "group": gid, "alias": alias, "means": means, "row_type": "candidate",
                "pick": "", "ticker": code, "descriptor": c.get("descriptor") or "",
                "frequency": c.get("frequency") or "", "adjustment": c.get("sa_status") or "",
                "database": code.partition("@")[2], "sim": round(c.get("_sim", 0.0), 3),
                "note": "",
            })
        out_rows.append({"group": gid, "alias": alias, "means": means,
                         "row_type": "NONE - type the ticker here", "pick": "",
                         "ticker": "", "descriptor": "", "frequency": "",
                         "adjustment": "", "database": "", "sim": "", "note": ""})
        out_rows.append({"group": gid, "alias": alias, "means": means,
                         "row_type": "PARK is correct - alias is ambiguous", "pick": "",
                         "ticker": "", "descriptor": "", "frequency": "",
                         "adjustment": "", "database": "", "sim": "", "note": ""})
        out_rows.append({k: "" for k in FIELDS})          # blank spacer between groups

        if i % 10 == 0 or i == len(sample):
            el = time.perf_counter() - t0
            print(f"  {i:4}/{len(sample)}  {el/60:5.1f}min  "
                  f"eta {el/i*(len(sample)-i)/60:5.1f}min", flush=True)
            _write(out_path, out_rows)

    _write(out_path, out_rows)
    print(f"\nworksheet: {out_path}\n{len(out_rows)} rows for {len(sample)} aliases")
    print("Put x in `pick`. Several x in one group = all acceptable (mirrors/twins).")
    return 0


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
