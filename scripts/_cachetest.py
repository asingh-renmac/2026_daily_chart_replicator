import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haver_chart import lane  # noqa: E402

p = lane._meta_cache_path()
print("path   :", p)
print("parent :", p.parent, "exists=", p.parent.exists())
print("file   : exists=", p.exists())

try:
    tmp = p.with_suffix(".json.tmp")
    print("tmp    :", tmp)
    tmp.write_text('{"probe": 1}', encoding="utf-8")
    tmp.replace(p)
    print("WRITE OK, file now exists =", p.exists())
    p.unlink()
except Exception as exc:
    print(f"WRITE FAILED: {type(exc).__name__}: {exc}")

# And through the real path, with the swallow removed for one call.
try:
    meta = lane.candidate_meta("LR@USECON")
    print("candidate_meta ok, keys:", sorted(meta)[:5])
    print("cache file exists after real call =", p.exists())
except Exception as exc:
    print(f"candidate_meta FAILED: {type(exc).__name__}: {exc}")
