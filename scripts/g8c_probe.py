"""Ad-hoc reachability probes for the recall-miss truths — relaxed filters."""
import sys
from pathlib import Path
from dotenv import load_dotenv

SERVER = Path("C:/Users/asingh/new_work/2026_haver_mcp/server")
load_dotenv(SERVER.parent / "config" / ".env")
sys.path.insert(0, str(SERVER))
import db, queries  # noqa: E402


def probe(query, truth, databases=None, sa_status=None, cap=50):
    sql, params = queries.build_search_query(
        query, databases=databases, sa_status=sa_status, max_results=cap)
    rows = [queries.serialize_row(r) for r in db.run_query(sql, params)]
    codes = [r["ticker"].upper() for r in rows]
    rank = (codes.index(truth.upper()) + 1) if truth.upper() in codes else None
    print(f"  q={query!r} sa={sa_status} db={databases}  n={len(rows)}  "
          f"{truth} rank={rank}")
    if rank:
        print(f"     -> {rows[rank-1]['ticker']}: {rows[rank-1]['descriptor']}")


print("FFEDTAR (read sa_hint=nsa zeroed it) — relaxed:")
probe("Federal Open Market Committee: Fed Funds Target Rate", "FFEDTAR@USECON")
probe("Fed Funds Target Rate", "FFEDTAR@USECON")
probe("Fed Funds Target Rate", "FFEDTAR@USECON", sa_status="nsa")
probe("Federal Funds Target Rate", "FFEDTAR@USECON")
probe("Fed Funds Target Rate EOP", "FFEDTARE@USECON")

print("\nPCUSERH (unreachable in 100) — relaxed / alternate phrasings:")
probe("PCE: Core Services excluding Housing: Chain Price Index", "PCUSERH@USECON")
probe("PCE Core Services ex Housing Chain Price Index", "PCUSERH@USECON")
probe("Core Services excluding Housing Chain Price Index", "PCUSERH@USECON", cap=100)
probe("PCE Services Less Housing Price Index", "PCUSERH@USECON")
