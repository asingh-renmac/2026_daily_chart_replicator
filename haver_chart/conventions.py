"""How this desk has shown a thing before — presentation only, never which series.

WHAT THIS IS FOR
  The Macrobond catalogue holds 1,022 charts the desk actually published, described in
  plain English. What it uniquely knows is not WHAT to plot -- it carries no tickers at all
  -- but HOW this desk plots things. Every claims chart in it is a 4-week moving average,
  weekly, over about three years. Nothing else we have records that: the ratified store maps
  a description to one ticker and has no opinion about presentation.

THE ORDERING IS THE WHOLE DESIGN
  This is deliberately NOT "here are twelve prior charts, pick some". A list offered before
  the model has decided anything is a menu, and it will be treated as one -- that is the
  anchoring the operator explicitly did not want. So the contract is inverted: the caller
  states the chart it has ALREADY designed, and this answers only "has the desk shown these
  things, and how". It can move a transformation or a sample start. It cannot change what
  the chart is about, because that was settled before the call.

  Two consequences follow, and both are intentional:

    * NO chart identifiers are returned. Not a path, not a filename. With no way to name a
      stored chart, "reuse that one" is not expressible -- the constraint is enforced by
      what the payload omits rather than by asking the model nicely.
    * The answer is an AGGREGATE. "9 charts, all 4wma, all weekly" is a convention;
      nine individual charts are nine options. The shape of the reply is the argument.

  Silence is a correct answer. A genuinely novel pairing matches nothing, and that must
  read as ordinary rather than as a failure to find something.

STALENESS
  The catalogue is a snapshot, generated on a date, and nothing in it updates. Every reply
  carries that date so a caller can see it is reasoning from an old picture of the desk.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Optional

# Where the catalogue lives, in preference order. The UNC share is the source of truth --
# one path string that means the same files on the laptop and the AVD -- but it is
# authenticated, and a lane that cannot reach it must degrade rather than fail. Each reply
# says which of these answered, so a fallback to a local snapshot is visible rather than
# silently different from what the pipeline reads.
_PATH_ENV = ("HAVER_CHART_CONVENTIONS_STORE", "MB_CHART_STORE")
_FALLBACKS = (r"\\10.10.10.4\companydata$\Econ\asingh\project\mb_charts_store",
              r"C:\Users\madz\Work\asingh\mb_charts_store",
              r"C:\Users\asingh\new_work\mb_charts_store")

# Words that appear in every economics sentence and so separate nothing. Kept short and
# obvious on purpose: a long hand-tuned list is a ranking model nobody has measured.
_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "its", "are", "was", "were",
    "index", "indices", "rate", "rates", "total", "all", "real", "nominal", "chart",
    "show", "shows", "showing", "versus", "vs", "against", "over", "level", "levels",
    "monthly", "weekly", "quarterly", "annual", "percent", "change", "growth", "data",
    "series", "line", "bar", "plot", "united", "states", "national", "seasonally",
    "adjusted", "year", "month", "quarter", "sa", "nsa", "saar", "us", "usa",
}

_CACHE: Optional[dict] = None


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower())} - _STOP


def _candidate_roots() -> list[Path]:
    out: list[str] = []
    for var in _PATH_ENV:
        v = os.environ.get(var)
        if v:
            out.append(v)
    out.extend(_FALLBACKS)
    seen, roots = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            roots.append(Path(p))
    return roots


def load() -> dict:
    """{charts, generated, source} — or charts=[] when no catalogue is reachable.

    Never raises. This is an enrichment; a share that is down overnight must not be the
    reason an operator cannot draw a chart.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    for root in _candidate_roots():
        for idx in (root / "charts_index.json", root / "US" / "charts_index.json"):
            try:
                if not idx.is_file():
                    continue
                doc = json.loads(idx.read_text(encoding="utf-8"))
                _CACHE = {"charts": doc.get("charts") or [],
                          "generated": doc.get("generated") or "unknown",
                          "source": str(idx.parent)}
                return _CACHE
            except Exception:
                continue
    _CACHE = {"charts": [], "generated": "unavailable", "source": ""}
    return _CACHE


def _blob(entry: dict) -> str:
    """The text a chart is matched on: what it SHOWS, not what it was titled.

    `title` is excluded on purpose. Titles are editorial headlines from the day they were
    written -- 168 of them name a specific month -- so matching on them would rank by last
    autumn's news rather than by subject, and would invite the wording to be reused.
    """
    return " ".join([" ".join(entry.get("subject") or []),
                     " ".join(entry.get("tags") or []),
                     entry.get("use_when") or ""])


def _idf() -> dict:
    """How rare each word is across the catalogue, cached with it.

    Counting shared WORDS does not work, and the failure is not subtle: "the correlation
    between sunspot activity and mortgage delinquency" -- nonsense this desk has certainly
    never charted -- matched 20 charts on its first run, because "activity" appears in
    hundreds of them and two shared words was the bar. A word that common carries no
    information about subject.

    So a match is scored by how SURPRISING the shared words are. "activity" is worth
    almost nothing, "jolts" or "delinquency" a great deal. That is what makes silence
    reachable for a genuinely novel pairing, which is the property this tool needs most.
    """
    cat = load()
    key = "_idf"
    if key in cat:
        return cat[key]
    n = max(len(cat["charts"]), 1)
    df = Counter()
    for e in cat["charts"]:
        df.update(_tokens(_blob(e)))
    # log(N/df), floored at zero so a word in every chart contributes nothing at all.
    import math
    cat[key] = {w: max(0.0, math.log(n / c)) for w, c in df.items()}
    return cat[key]


# A word appearing in ~5% of charts scores about 3.0, so this asks for roughly one
# genuinely specific term, or two moderately specific ones. Tuned to let "jobless claims
# against JOLTS layoffs" through and keep "sunspot activity" out; it has not been measured
# against a labelled set, and it should be if this tool earns its keep.
_MIN_SCORE = 4.0


# A convention is a PATTERN, not an instance. One chart that happens to share a rare word
# is an anecdote, and reporting "the desk shows this as mom, yoy, recession-shaded" off a
# single match would dress a coincidence as a house rule -- with counts of 1 beside it that
# nobody reads closely. Three is the smallest number that can show agreement or spread.
_MIN_CHARTS = 3


def lookup(description: str, min_score: float = _MIN_SCORE, pool: int = 25,
           min_charts: int = _MIN_CHARTS) -> dict:
    """What the desk habitually does when showing `description`."""
    cat = load()
    out = {"matched": 0, "index_generated": cat["generated"], "source": cat["source"],
           "transformations": [], "frequency": [], "chart_type": [], "samples": [],
           "note": ""}
    if not cat["charts"]:
        out["note"] = ("No chart catalogue is reachable, so there is no house convention "
                       "to report. Design the chart as you see fit.")
        return out

    want = _tokens(description)
    if not want:
        out["note"] = "Nothing to match on. Design the chart as you see fit."
        return out

    idf = _idf()
    scored = []
    for e in cat["charts"]:
        if e.get("unreadable"):
            continue
        shared = want & _tokens(_blob(e))
        s = sum(idf.get(w, 0.0) for w in shared)
        if s >= min_score:
            scored.append((s, e))
    scored.sort(key=lambda p: -p[0])
    top = [e for _, e in scored[:pool]]

    if len(top) < min_charts:
        out["note"] = ("The desk has no established way of showing this — "
                       f"{len(top)} prior chart(s) came close, too few to call a "
                       "convention. That is an ordinary outcome, not a gap: design it "
                       "fresh.")
        return out

    def tally(key, listy=False):
        c = Counter()
        for e in top:
            v = e.get(key)
            c.update(v or [] if listy else ([v] if v else []))
        return [{"value": k, "charts": n} for k, n in c.most_common(6)]

    out["matched"] = len(top)
    out["transformations"] = tally("transformations", listy=True)
    out["frequency"] = tally("frequency")
    out["chart_type"] = tally("chart_type")
    out["samples"] = [s for s in (e.get("observed_range") for e in top) if s][:8]
    out["note"] = (f"Counts over {len(top)} prior chart(s) that showed similar things. "
                   "This describes PRESENTATION only — how the desk tends to draw these, "
                   "not what to draw. Adjust a transformation or a sample start if it "
                   "fits; do not change what your chart is about, and do not treat a "
                   "convention as an instruction.")
    return out
