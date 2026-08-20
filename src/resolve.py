"""
resolve.py — ticker confirmation + G8b Stage-2 series resolution (§4e, §12 G8).

Two layers:

  * `confirm_ticker(code@db)` — the runtime validator the Teams loop uses to
    accept/reject a human's free-text reply: it verifies the code actually
    resolves to real Haver data (the silent-typo / wrong-db guard). Haver-DLX
    native, headless.

  * G8b resolver (`resolve_slot` / `resolve_chart`) — turns a Stage-1 vision read
    (chartspec.ChartSpec) into N per-series SLOTS and resolves each:
      - formula series → walk the G3 parser's Series nodes and confirm EVERY
        mnemonic via DLX (a sum like BEEM1+BEEM2+BEEM3 must confirm all three);
      - description-only series → search_series (authoring MCP, injected) →
        confirm + cross-check SA → bind only on a single confident match;
      - native-MA ambiguity → consult the clarified-knowledge store, else park to
        the bot (never guess whether "3-Mo Mov Avg" is the series name);
    A read is a HYPOTHESIS; get_series/confirm is the CONFIRMATION. Nothing
    auto-binds without a confirm pass (the system's highest silent-wrong risk).

Division of labour (Model A): the haver-metadata MCP (`search_series`/`get_series`)
runs at AUTHORING time to build candidate lists + cross-check metadata; `confirm`
runs headless at run time. Both are INJECTED into the resolver so it stays
testable offline (the G8b demo wires deterministic stubs).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_TICKER = re.compile(r"^([A-Za-z0-9_]+)@([A-Za-z0-9_]+)$")
_HAVER_READY = False

# Slot status strings — kept as literals (not imported from ledger) so resolve.py
# has no heavy import; they MATCH ledger's RESOLVED/PENDING/SKIPPED values.
SLOT_RESOLVED = "resolved"
SLOT_PENDING = "pending"
SLOT_SKIPPED = "skipped"


def _haver():
    global _HAVER_READY
    import Haver  # noqa: N813
    if not _HAVER_READY:
        Haver.direct("on")
        _HAVER_READY = True
    return Haver


def confirm_ticker(code_at_db: str, start: str = "2015-01-01") -> bool:
    """True iff `code@db` is well-formed AND Haver returns finite observations."""
    m = _TICKER.match((code_at_db or "").strip())
    if not m:
        return False
    code, db = m.group(1), m.group(2)
    try:
        df = _haver().data([code], db, startdate=start)
    except Exception:
        return False
    if df is None or isinstance(df, dict):
        return False
    try:
        return len(df.dropna()) > 0
    except Exception:
        return False


# ─────────────────── clarified-knowledge + trusted-ticker store ──────────────
# Human-confirmed answers persist so the SAME descriptor never re-asks (G8a #4).
#   native_ma.json     : normalized descriptor → {native_ma: bool, ...}
#   trusted_tickers.json: bare mnemonic (lower) → "code@db"  (attaches @db to
#                         formula mnemonics so they confirm headless)
#   learned_descriptors.json: normalized DESCRIPTION → {code, ...}  (the confirm_all
#                         learning store — an approved resolution pre-fills the same
#                         description next time so the proposal is one-tap; Part 4)
CLARIFIED_DIR = Path(os.environ.get(
    "CLARIFIED_KNOWLEDGE_DIR",
    "C:/Users/asingh/new_work/knowledge_repo/clarified-knowledge"))
_NATIVE_MA_FILE = "native_ma.json"
_TRUSTED_FILE = "trusted_tickers.json"
_LEARNED_FILE = "learned_descriptors.json"


def _norm_key(text: str) -> str:
    """Stable lookup key for a descriptor: lower-cased, whitespace-collapsed."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_clarified(directory: Optional[Path] = None) -> dict:
    """native-MA clarifications: {normalized descriptor → {native_ma: bool, ...}}."""
    d = Path(directory or CLARIFIED_DIR)
    return _read_json(d / _NATIVE_MA_FILE)


def save_clarified(descriptor: str, native_ma: bool, note: str = "",
                   directory: Optional[Path] = None) -> dict:
    """Persist a human's native-MA answer (write-once-and-reuse, never re-ask)."""
    d = Path(directory or CLARIFIED_DIR)
    d.mkdir(parents=True, exist_ok=True)
    store = _read_json(d / _NATIVE_MA_FILE)
    store[_norm_key(descriptor)] = {
        "native_ma": bool(native_ma), "descriptor": descriptor,
        "note": note, "added": datetime.now(timezone.utc).isoformat()}
    (d / _NATIVE_MA_FILE).write_text(
        json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    return store


def load_trusted(directory: Optional[Path] = None) -> dict:
    """{bare mnemonic (lower) → 'code@db'} for headless @db attachment."""
    d = Path(directory or CLARIFIED_DIR)
    return {k.lower(): v for k, v in _read_json(d / _TRUSTED_FILE).items()}


def save_trusted(mnemonic: str, code_at_db: str,
                 directory: Optional[Path] = None) -> dict:
    """Persist a confirmed bare-mnemonic → code@db mapping (mnemonic-honest: key on
    the bound code's OWN mnemonic, not the descriptor)."""
    d = Path(directory or CLARIFIED_DIR)
    d.mkdir(parents=True, exist_ok=True)
    store = _read_json(d / _TRUSTED_FILE)
    store[(mnemonic or "").lower()] = code_at_db
    (d / _TRUSTED_FILE).write_text(
        json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    return store


def load_learned(directory: Optional[Path] = None) -> dict:
    """{normalized description → {code, descriptor, added}} — the confirm_all
    learning store (Part 4). Consulted FIRST in the description path so a recurring
    series resolves instantly (still re-confirmed + cross-checked before binding)."""
    d = Path(directory or CLARIFIED_DIR)
    return _read_json(d / _LEARNED_FILE)


def save_learned(description: str, code_at_db: str,
                 directory: Optional[Path] = None) -> dict:
    """Record an approved description → code bind so the same description pre-fills
    next time (the equilibrium: approve → store learns → fewer corrections)."""
    d = Path(directory or CLARIFIED_DIR)
    d.mkdir(parents=True, exist_ok=True)
    store = _read_json(d / _LEARNED_FILE)
    store[_norm_key(description)] = {
        "code": code_at_db, "descriptor": description,
        "added": datetime.now(timezone.utc).isoformat()}
    (d / _LEARNED_FILE).write_text(
        json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    return store


# ─────────────────────── legend-label store (code-keyed) ────────────────────
# Two canonicalizers, by KEY KIND (a single shared function is impossible because
# the stores key on different things): `_norm_key` for TEXT-keyed stores
# (native_ma, learned); `code_key`/`expr_key` for the CODE-keyed legend store.
# The discipline is the same — normalize once, at both save AND lookup — so a
# case/whitespace drift can't cause the "why is it asking me again" silent re-ask
# (the class that bit native-MA). save→load round-trip is regression-locked.
_LEGEND_FILE = "legend_labels.json"


def code_key(code_at_db: str) -> str:
    """Canonical key for a SINGLE resolved series: `CODE@DB`, upper-cased both
    sides. Stable across the case Haver/vision emit codes in."""
    s = (code_at_db or "").strip()
    if "@" in s:
        code, db = s.split("@", 1)
        return f"{code.strip().upper()}@{db.strip().upper()}"
    return s.upper()


def expr_key(expression: str) -> str:
    """Canonical key for a COMPOSITE operand expression (e.g. 'NRS - NRSI7'):
    upper-cased, whitespace-removed, `expr:`-prefixed so it can never collide with
    a single `CODE@DB` key. So the SAME combination reuses its label, but a
    different combination of the same series does NOT wrongly inherit it."""
    norm = re.sub(r"\s+", "", (expression or "").upper())
    return f"expr:{norm}"


def legend_key_base(slot: dict) -> str:
    """The TRANSFORM-BLIND legend key (the pre-2026-07-30 format): a composite (>1 bound
    code) keys on its operand EXPRESSION; a single series keys on its `CODE@DB`. Kept as
    the back-compat lookup key so already-warmed stores don't re-ask."""
    codes = slot.get("codes") or []
    if len(codes) > 1:                       # composite operand expression
        expr = slot.get("base_descriptor") or slot.get("formula") or ""
        return expr_key(expr)
    code = codes[0] if codes else (slot.get("resolved") or "")
    return code_key(code)


def transform_key(slot: dict) -> str:
    """Canonical TRANSFORM tag for a slot, or "" for a level series.

    Canonicalized on the derived G3 FORMULA (mnemonics replaced by `#`), not the read's
    wording, so two spellings of the same math ("2-qtr %Change-ann" / "2-quarter % change
    annualized" → `difa%(#,2)`) share one label, while genuinely different math does not."""
    phrase = (slot.get("applied_transform") or "").strip()
    if not phrase:
        return ""
    try:
        import build_chart as _BC          # local: build_chart imports resolve
        formula = _BC.phrase_to_haver(phrase, "#")
    except Exception:
        formula = None
    if not formula:                          # level (or unmappable) → no qualifier
        return ""
    return re.sub(r"\s+", "", formula.upper())


def legend_key(slot: dict) -> str:
    """The legend-store key for a resolved slot, QUALIFIED BY ITS TRANSFORM.

    Why the transform belongs in the KEY (2026-07-30 defect): the stored label TEXT carries
    a transform qualifier ("… (y/y %chg)" vs "… (1-qtr %chg saar)"), so a transform-blind
    key had finer-grained VALUES than KEYS. A chart plotting ONE ticker under TWO transforms
    (`fsdh@usecon` y/y + 1-qtr saar) wrote both labels to the same key — last write won — and
    then BOTH lines rendered the loser's label. Qualifying the key keeps them distinct.

    A LEVEL series keys exactly as before (bare `CODE@DB`/`expr:`), so every warmed
    level-series entry keeps hitting; transformed slots get a `|<formula>` suffix."""
    base = legend_key_base(slot)
    tf = transform_key(slot)
    return f"{base}|{tf}" if tf else base


def lookup_legend(store: dict, slot: dict, allow_base: bool = True) -> Optional[dict]:
    """Resolve a slot's legend entry: the transform-qualified key first, then (optionally)
    the legacy transform-blind key so PRE-EXISTING store entries still hit and are never
    re-asked. `allow_base` must be False when the same base key appears more than once on
    one chart — that is exactly the ambiguous case where a blind hit could serve another
    transform's label."""
    hit = store.get(legend_key(slot))
    if hit and (hit.get("label") or "").strip():
        return hit
    if allow_base:
        hit = store.get(legend_key_base(slot))
        if hit and (hit.get("label") or "").strip():
            return hit
    return None


def load_legend(directory: Optional[Path] = None) -> dict:
    """{legend_key → {label, source_descriptor, added}} — confirmed, human-ratified
    brief legend labels. A store hit renders SILENTLY (no Opus, no re-confirm)."""
    d = Path(directory or CLARIFIED_DIR)
    return _read_json(d / _LEGEND_FILE)


def save_legend(key: str, label: str, source_descriptor: str = "",
                directory: Optional[Path] = None) -> dict:
    """Persist a confirmed legend label (write-once-and-reuse). `key` is already a
    canonical `code_key`/`expr_key` (callers key via `legend_key`)."""
    d = Path(directory or CLARIFIED_DIR)
    d.mkdir(parents=True, exist_ok=True)
    store = _read_json(d / _LEGEND_FILE)
    store[key] = {"label": label, "source_descriptor": source_descriptor,
                  "added": datetime.now(timezone.utc).isoformat()}
    (d / _LEGEND_FILE).write_text(
        json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    return store


# ───────────────────────────── SA cross-check ───────────────────────────────
def _sa_norm(x: Optional[str]) -> str:
    s = (x or "").lower()
    if "saar" in s:
        return "saar"
    if "nsa" in s or "not seasonally" in s:
        return "nsa"
    if s in ("sa", "seasonally adjusted", "seas adj") or "seasonally adj" in s:
        return "sa"
    return ""


def sa_matches(meta: Optional[dict], sa_hint: str) -> bool:
    """SA cross-check: read sa_hint vs resolved metadata. Unknown on either side →
    can't disprove → True (don't block on missing info). saar~sa are both
    seasonally adjusted; the real guard is sa-vs-nsa."""
    want = _sa_norm(sa_hint)
    got = _sa_norm((meta or {}).get("sa_status") or (meta or {}).get("sa"))
    if not want or not got:
        return True
    if want in ("sa", "saar") and got in ("sa", "saar"):
        return True
    return want == got


# ─────────────────── FREQ (advisory — freq-of-record = get_meta) ─────────────
def _freq_norm(x: Optional[str]) -> str:
    """Normalize a frequency word/code to one of M/Q/W/A/D (or '')."""
    s = (x or "").strip().lower()
    if not s:
        return ""
    for key, code in (("quarter", "Q"), ("month", "M"), ("week", "W"),
                      ("annual", "A"), ("year", "A"), ("dai", "D")):
        if key in s:
            return code
    c = s[0].upper()
    return c if c in ("M", "Q", "W", "A", "D") else ""


def meta_freq(meta: Optional[dict]) -> str:
    """The AUTHORITATIVE freq-of-record from get_meta (M/Q/W/A/D or '')."""
    return _freq_norm((meta or {}).get("frequency") or (meta or {}).get("freq"))


def freq_advisory_mismatch(meta: Optional[dict], freq_hint: str) -> bool:
    """True iff the vision freq_hint DISAGREES with get_meta's freq-of-record.
    ADVISORY ONLY — diagnostic, never a reject reason. The reframe (2026-06-30):
    freq-of-record is get_meta (authoritative); vision freq_hint is advisory. A
    park must fire on REAL ambiguity (no cadence cue → multiple candidates,
    EOP-vs-AVG aggregation, SA-undetermined), NOT on a spurious read-vs-meta freq
    mismatch the metadata already resolves. Genuine freq ambiguity (two same-named
    series at different freqs) is caught by CANDIDATE COUNT (both confirm → park),
    not by this flag."""
    want = _freq_norm(freq_hint)
    got = meta_freq(meta)
    return bool(want) and bool(got) and want != got


# ───────────────────── AGGREGATION cross-check (EOP / AVG) ───────────────────
# "EOP"/"AVG" in a descriptor is a RESOLUTION CONSTRAINT, not decoration: when two
# same-name, same-frequency series differ only by aggregation method (e.g. Fed Funds
# Target Rate EOP = FFEDTARE@USECON vs the monthly AVG = FFEDTAR@USECON), the cue
# picks which one. get_series exposes `agg_type` (EOP/AVG/SUM/…). Binding the AVG
# when the chart says EOP is a silent-wrong bind between two plausible series — the
# class we never allow — so this is a HARD reject like SA, not advisory.
# Tight cues. EOP is the cue that actually appears in Haver descriptors (e.g.
# "...Target Rate (EOP, %)"); AVG is usually the unmarked default (FFEDTAR is just
# "(%)"), so AVG is matched ONLY in explicit tag/phrase forms — crucially NOT as the
# " avg" inside a "Moving Average"/"3-Mo Mov Avg", which is a TRANSFORM, not an
# aggregation method. (That false-positive parked WGTO/MEDWG in the first cut.)
_AGG_CUES = (
    ("eop", ("(eop", " eop ", " eop,", " eop)", "eop)", "end of period",
             "end-of-period", "end of month", "end of quarter", "period end",
             "period-end")),
    ("avg", ("(avg", "avg)", "(average", "period average", "monthly average",
             "quarterly average", "weekly average", "annual average")),
)
# Moving-average phrasing is stripped before AVG detection so it can never be read
# as an aggregation cue.
_MOV_AVG_RE = re.compile(r"\b(?:\d+[\-\s]?mo(?:nth)?s?\s+)?mov(?:ing)?\.?\s*avg\w*\b"
                         r"|\bmoving\s+average\b", re.I)


def _agg_from_descriptor(text: str) -> str:
    """Detect an EOP/AVG aggregation cue in a descriptor ('' = no constraint)."""
    s = f" {(text or '').lower()} "
    if any(c in s for c in _AGG_CUES[0][1]):
        return "eop"
    s_no_ma = _MOV_AVG_RE.sub(" ", s)
    if any(c in s_no_ma for c in _AGG_CUES[1][1]):
        return "avg"
    return ""


def _agg_norm(x: Optional[str]) -> str:
    s = (x or "").strip().lower()
    if not s:
        return ""
    if "eop" in s or "end" in s:
        return "eop"
    if "avg" in s or "aver" in s or "mean" in s:
        return "avg"
    if "sum" in s or "tot" in s:
        return "sum"
    return s


def meta_agg(meta: Optional[dict]) -> str:
    """The aggregation method from get_meta (`agg_type`, normalized)."""
    return _agg_norm((meta or {}).get("agg_type")
                     or (meta or {}).get("aggregation") or (meta or {}).get("agg"))


def agg_matches(meta: Optional[dict], descriptor: str) -> bool:
    """HARD aggregation cross-check. If the descriptor carries an EOP/AVG cue, the
    candidate's `agg_type` MUST match it. If the cue is present but the candidate's
    aggregation can't be confirmed, that's REAL ambiguity (we can't tell EOP from
    AVG) → reject (park-safe). No cue → no constraint → True."""
    want = _agg_from_descriptor(descriptor)
    if not want:
        return True
    got = meta_agg(meta)
    if not got:
        return False        # cue demands EOP/AVG but candidate's agg unconfirmable → park
    return want == got


def _meta_reject(meta: Optional[dict], slot: dict) -> Optional[str]:
    """Short mismatch reason if a HARD cross-check fails, else None. Hard checks =
    SA and AGGREGATION (both defining, both silent-wrong if mis-bound). FREQUENCY is
    NOT here — freq-of-record is get_meta (authoritative); the read freq_hint is
    advisory and never rejects (a spurious read mismatch must not over-park)."""
    if not sa_matches(meta, slot.get("sa_hint")):
        return f"SA\u2260{slot.get('sa_hint')}"
    desc = slot.get("description") or slot.get("base_descriptor") or ""
    if not agg_matches(meta, desc):
        return f"agg\u2260{_agg_from_descriptor(desc) or '?'}(meta={meta_agg(meta) or '?'})"
    return None


# ────────── DOUBLE-TRANSFORM GUARD (transform-appropriateness, 2026-07-01) ────
# The descriptor-relevance gate matches on "does the descriptor match", NOT "is this
# the right KIND of series for the transform". Haver ships BOTH the level index and a
# pre-computed %Chg of it under near-identical descriptors, so a high-similarity bind
# can land on a series that is ALREADY a rate/change; applying the read's ADDITIONAL
# %/yryr/difa on top double-transforms (yoy-of-an-already-differenced series → garbage
# vertical spikes). That's a SILENT-wrong class (mpcuhsro CPI-lodging, sim 0.818). When
# both conditions hold, PARK for a human-supplied raw-LEVEL ticker instead of binding.
_CAND_CHANGE_RE = re.compile(
    r"%\s*chg|%\s*change|percent\s+change|m/m|y/y|\bmom\b|\byoy\b|"
    r"year[-\s]?over[-\s]?year|year\s+to\s+year|chg[-\s]?ann|\bdifa\b|\byryr\b", re.I)
_READ_PCT_RE = re.compile(
    r"%\s*change|percent\s+change|year\s+to\s+year|year[-\s]?over[-\s]?year|"
    r"\byoy\b|annualiz|%change|\bdifa\b|\byryr\b", re.I)


def _cand_already_change(cand_desc: Optional[str], cand_agg: Optional[str]) -> bool:
    """True if a candidate is ALREADY a rate/change per its get_meta descriptor or its
    agg_type (Haver `NDF` flags a pre-differenced series — mpcuhsro's 'SA, M/M %Chg')."""
    if _CAND_CHANGE_RE.search(cand_desc or ""):
        return True
    return (cand_agg or "").strip().lower() in {"ndf"}


def _read_wants_pct(slot: dict) -> bool:
    """True if the READ applies an ADDITIONAL %/yryr/difa transform to this series
    (a worded `applied_transform`, or a yryr%/difa%/diff% formula)."""
    if _READ_PCT_RE.search(slot.get("applied_transform") or ""):
        return True
    return any(k in (slot.get("formula") or "").lower()
               for k in ("yryr%", "difa%", "diff%"))


def double_transform_reason(slot: dict, cand_desc: Optional[str],
                            cand_agg: Optional[str]) -> Optional[str]:
    """Park reason if binding this candidate would double-transform (the read applies a
    %/yryr/difa transform onto a series that is already a rate/change), else None."""
    if _read_wants_pct(slot) and _cand_already_change(cand_desc, cand_agg):
        return (f"double-transform guard: candidate is ALREADY a rate/change "
                f"({(cand_desc or '').strip()!r}) but the read applies "
                f"{slot.get('applied_transform') or 'a %/yryr/difa transform'!r} — "
                f"park for a raw-LEVEL ticker (avoid yoy-of-an-already-differenced series)")
    return None


# ─────────────── DESCRIPTOR-RELEVANCE GATE (Part 1A — safety floor) ──────────
# The description path must NOT bind on "single confident match" alone (SA saving
# texas at G8c was luck). Binding REQUIRES that the candidate's get_series descriptor
# actually matches the vision read — so every recall failure is a non-bind, never a
# wrong-bind. Primary signal: EXACT normalized token-set equality (uniquely separates
# the headline series from its near-identical directional decompositions, e.g.
# "General Business Activity" vs "...: Worsened" — one extra token that Jaccard can't
# resolve). Fallback: Jaccard with a threshold + a margin over the runner-up.
SIM_THRESHOLD = 0.5      # min Jaccard to be "relevant" (fallback path)
SIM_MARGIN = 0.15        # top must lead runner-up by this (fallback path)

_STOP_TOK = {"of", "the", "a", "an", "for", "to", "in", "and", "on", "by",
             "with", "at", "vs"}
# unit/seasonal tokens that are not concept discriminators (dropped for matching)
_UNIT_TOK = {"sa", "nsa", "saar", "bal", "pct", "percent", "eop", "avg", "us"}
_ABBR = {"mfg": "manufacturing", "manuf": "manufacturing", "mfr": "manufacturers",
         "mfrs": "manufacturers", "svc": "service", "svcs": "services",
         "svy": "survey", "svys": "surveys", "gen": "general", "bus": "business",
         "mos": "months", "mo": "month", "indx": "index", "ind": "index",
         "empl": "employment", "exp": "expectations", "chg": "change",
         "nondef": "nondefense", "tot": "total", "ret": "retail"}


def _descr_tokens(text: str) -> set:
    """Normalized token SET for descriptor matching: drop the trailing units
    parenthetical, lowercase, expand abbreviations, drop stop/unit tokens."""
    s = re.sub(r"\([^)]*\)", " ", (text or "").lower())   # strip "(SA, %Bal)"
    out = set()
    for t in re.findall(r"[a-z0-9]+", s):
        t = _ABBR.get(t, t)
        if t in _STOP_TOK or t in _UNIT_TOK:
            continue
        out.add(t)
    return out


def descriptor_similarity(read_desc: str, cand_desc: str) -> float:
    """Jaccard over normalized token sets (0..1)."""
    a, b = _descr_tokens(read_desc), _descr_tokens(cand_desc)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def descriptor_exact(read_desc: str, cand_desc: str) -> bool:
    """True iff the normalized token SETS are identical — the strongest match (and
    the only reliable separator of a headline series from its directional siblings)."""
    a, b = _descr_tokens(read_desc), _descr_tokens(cand_desc)
    return bool(a) and a == b


# ─────────────────── SEARCH RECALL (Part 1B — bounded) ───────────────────────
# Verbatim Haver descriptors resolve poorly (the colon structure + "Mfg" abbrev +
# generic words out-rank the exact match, and an un-scoped search returns global
# series). Strategy: DB-scope by a keyword heuristic, and issue a few query VARIANTS
# (verbatim, geography-lead + after-colon discriminator, discriminator alone) whose
# results are UNIONED — maximize recall; the gate above supplies precision. Bounded:
# we don't chase the last 10% (those park + get learned).
_DB_HINTS = (
    (("outlook survey", "business outlook", "diffusion index", "%bal",
      "business conditions", "business leaders survey"), "surveys"),
)


def _detect_db(text: str) -> Optional[str]:
    s = (text or "").lower()
    for keys, db in _DB_HINTS:
        if any(k in s for k in keys):
            return db
    return None


# Bounded recall window (Aman 2026-06-30: top-30, NOT 50 — widen recall modestly,
# keep the added reject-load small; the exact-token-set gate stays the bind floor).
SEARCH_CAP = 30

# A bracket lag tag ([-4], [+2]) is a DISPLAY attribute, not series identity — strip
# it before searching AND before descriptor matching so it never pollutes the query
# or the token-set gate (the JOLTS "[-4]" case).
_LAG_RE = re.compile(r"\s*\[[-+]?\d+\]\s*")


def _strip_lag(text: str) -> str:
    return _LAG_RE.sub(" ", text or "").strip()


def build_search_attempts(slot: dict) -> list[dict]:
    """Ordered, de-duped search attempts for a description-only slot. Each is
    {query, databases, sa_status}; the resolver issues all and unions the hits."""
    desc = _strip_lag(slot.get("base_descriptor") or slot.get("description") or "")
    db = _detect_db(slot.get("description") or desc)
    sa = _sa_norm(slot.get("sa_hint"))
    sa = sa if sa in ("sa", "nsa", "saar") else None
    out, seen = [], set()

    def add(q: str):
        q = re.sub(r"\s+", " ", (q or "")).strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append({"query": q, "databases": [db] if db else None,
                        "sa_status": sa})

    add(desc)                                        # verbatim
    if ":" in desc:
        lead = desc.split(":")[0].split()
        after = desc.split(":")[-1].replace(",", " ")
        geo = lead[0] if lead else ""                # geography/source lead word
        add(f"{geo} {after}")                        # geo + discriminator
        add(after)                                   # discriminator alone
    return out


def _do_search(search: Callable, query: str, databases, sa_status,
               cap: Optional[int] = None) -> list:
    """Call the injected search with optional filters + the bounded result cap,
    degrading gracefully for stubs that accept fewer kwargs."""
    cap = SEARCH_CAP if cap is None else cap
    for kw in ({"databases": databases, "sa_status": sa_status, "max_results": cap},
               {"databases": databases, "sa_status": sa_status},
               {}):
        try:
            return search(query, **kw) or []
        except TypeError:
            continue
    return []


# ───────────────────────────── slot construction ────────────────────────────
def slot_from_series(idx: int, s: dict) -> dict:
    """Build a per-series resolution SLOT from one Stage-1 SeriesSpec dict."""
    return {
        "idx": idx,
        "description": (s.get("description") or "").strip(),
        "base_descriptor": s.get("base_descriptor") or None,
        "applied_transform": s.get("applied_transform") or None,
        "formula": s.get("formula") or None,
        "axis": s.get("axis") or "shared",
        "lag": s.get("lag") or None,
        "freq_hint": s.get("freq_hint") or "unknown",
        "sa_hint": s.get("sa_hint") or "unknown",
        "native_ma_ambiguous": bool(s.get("native_ma_ambiguous", False)),
        "candidates": [],
        "candidate_detail": [],         # [{code, descriptor, sim, exact, via_query}]
        "search_attempts": [],          # recall attempts issued (query/db/n)
        "relevance": None,              # bound candidate's descriptor similarity
        "relevance_exact": False,
        "bound_via_query": "",
        "resolved": None,
        "codes": [],
        "freq_resolved": "",            # AUTHORITATIVE freq-of-record from get_meta
        "agg_resolved": "",             # aggregation method (agg_type) of the bind
        "freq_advisory_mismatch": False,  # diagnostic: read freq_hint != meta freq
        "status": SLOT_PENDING,
        "needs_clarification": False,
        "reason": "",
    }


def rebind_human_ticker(slot: dict, code: str, *,
                        reason: str = "human-supplied code@db (confirmed)") -> dict:
    """Slot-field updates for a HUMAN binding ONE confirmed `code@db` to a slot.

    The human reduced the series to a single ticker, so a read-level `formula` that
    referenced the (now-superseded) description or mnemonic must NOT ride into render —
    it may not even parse (`yryr%(CPI-U: Legal Services (NSA, Dec-86=100))` → ParseError
    at render, the 2026-07-09 legal-services snag). The TRANSFORM is preserved without
    the stale operand:
      * applied_transform present → drop the formula; render rebuilds it from the
        applied_transform on the supplied mnemonic (build_chart.phrase_to_haver — the one
        phrase→G3 source of truth);
      * no applied_transform, but the read-formula is a single-mnemonic parseable
        expression → rewrite its one series token to the supplied bare mnemonic (keeps
        the transform, e.g. `yryr%(XYZ)` → `yryr%(<mnem>)`);
      * otherwise → drop the formula (nothing reliable to carry over — an unparseable
        formula wrapped a description, which the read pairs with applied_transform)."""
    upd = {"resolved": code, "codes": [code], "status": SLOT_RESOLVED,
           "candidates": [], "reason": reason}
    f = slot.get("formula")
    if not f:
        return upd
    if slot.get("applied_transform"):
        upd["formula"] = None
        return upd
    mnem = code.split("@")[0]
    try:
        import transforms
        mnems = transforms.formula_mnemonics(f)
    except Exception:
        mnems = []
    if len(mnems) == 1:
        upd["formula"] = re.sub(
            rf"(?<![A-Za-z0-9_@.]){re.escape(mnems[0])}(?![A-Za-z0-9_@.])", mnem, f)
    else:
        upd["formula"] = None
    return upd


def _record_meta_of_record(slot: dict, code: str,
                           get_meta: Optional[Callable]) -> None:
    """Stamp the AUTHORITATIVE freq/agg from get_meta onto a just-bound slot, and
    flag (diagnostic only) when the advisory vision freq_hint disagreed."""
    if not get_meta or not code:
        return
    meta = get_meta(code)
    if not meta:
        return
    slot["freq_resolved"] = meta_freq(meta)
    slot["agg_resolved"] = meta_agg(meta)
    slot["freq_advisory_mismatch"] = freq_advisory_mismatch(meta, slot.get("freq_hint"))


def build_slots(chart_spec: dict) -> list[dict]:
    """ChartSpec.to_dict() → list of N per-series slots (one per plotted series)."""
    return [slot_from_series(i, s)
            for i, s in enumerate(chart_spec.get("series", []) or [])]


def _qualify(code: str, trusted: dict,
             search: Optional[Callable]) -> Optional[str]:
    """Attach @db to a bare formula mnemonic via the trusted map, then search."""
    if "@" in code:
        return code
    key = code.lower()
    if trusted and key in trusted:
        return trusted[key]
    if search:
        for h in search(code) or []:
            hc = h.get("code") if isinstance(h, dict) else h
            if hc and hc.split("@")[0].lower() == key:
                return hc
    return None


def _hit_code(h) -> Optional[str]:
    return (h.get("code") if isinstance(h, dict) else h) or None


# ───────────────────────────── Stage-2 resolution ───────────────────────────
def resolve_slot(slot: dict, *,
                 confirm: Callable[[str], bool],
                 get_meta: Optional[Callable[[str], Optional[dict]]] = None,
                 search: Optional[Callable[[str], list]] = None,
                 clarified: Optional[dict] = None,
                 trusted: Optional[dict] = None,
                 learned: Optional[dict] = None) -> dict:
    """Resolve ONE series slot in place-ish (returns the updated slot).

    Policy (fail-loud, never silent-bind):
      1. native-MA ambiguity → clarified store, else park asking the bot.
      2. formula present → confirm EVERY mnemonic (walk the parser); any
         unconfirmed addend / hard-meta mismatch → park the slot.
      3. description-only → search → confirm + hard-meta cross-check; bind only on
         a single confident match, else park with candidates for the human.

    HARD cross-checks (reject → park): SA, and AGGREGATION (an EOP/AVG cue in the
    descriptor must match get_meta `agg_type` — Fed Funds EOP vs AVG is the canonical
    silent-wrong-between-two-plausible-series case). FREQUENCY is ADVISORY: freq-of-
    record is get_meta (recorded as `freq_resolved`); the vision `freq_hint` only
    sets a `freq_advisory_mismatch` diagnostic and never rejects. Genuine freq
    ambiguity (two same-named series at different freqs) is caught by candidate count
    (both confirm → park), not by the advisory read.
    """
    slot = dict(slot)
    clarified = clarified or {}
    trusted = trusted or {}
    learned = learned or {}

    # 1) native-MA ambiguity — ask the human (once), then reuse the answer.
    if slot.get("native_ma_ambiguous"):
        key = _norm_key(slot.get("base_descriptor") or slot.get("description"))
        ans = clarified.get(key)
        if ans is None:
            slot.update(status=SLOT_PENDING, needs_clarification=True,
                        reason="native-MA ambiguity — confirm whether the moving "
                               "average / %-change is the native series name")
            return slot
        slot["native_ma_ambiguous"] = False
        slot["native_ma_resolved"] = bool(ans.get("native_ma"))
        slot["needs_clarification"] = False

    # 2) formula path — confirm every mnemonic (sum/nested safe).
    if slot.get("formula"):
        try:
            import transforms
            mnems = transforms.formula_mnemonics(slot["formula"])
        except Exception as exc:
            slot.update(status=SLOT_PENDING,
                        reason=f"formula parse/unsupported: {exc}")
            return slot
        qualified, bad = [], []
        for code in mnems:
            q = _qualify(code, trusted, search)
            if not q or not confirm(q):
                bad.append(code)
                continue
            if get_meta:
                why = _meta_reject(get_meta(q), slot)
                if why:
                    bad.append(f"{q}({why})")
                    continue
            qualified.append(q)
        if not bad and qualified:
            slot.update(codes=qualified,
                        resolved=(qualified[0] if len(qualified) == 1
                                  else slot["formula"]),
                        status=SLOT_RESOLVED,
                        reason="formula confirmed: " + " + ".join(qualified))
            _record_meta_of_record(slot, qualified[0], get_meta)
        else:
            slot.update(status=SLOT_PENDING, candidates=qualified,
                        reason="unconfirmed mnemonic(s): " + ", ".join(bad))
        return slot

    # 3) description-only path — multi-attempt search (recall) → confirm + meta
    # cross-check → DESCRIPTOR-RELEVANCE GATE (never bind a candidate whose descriptor
    # doesn't match the read). A clarified native-MA name matches on the FULL read.
    read_desc = _strip_lag(slot.get("description") or slot.get("base_descriptor") or "")

    # 3a) LEARNED fast-path (Part 4): a previously-approved description binds instantly
    # — but still re-confirmed + hard-meta cross-checked (a stale code or a metadata
    # change must never silent-serve from the cache).
    lk = learned.get(_norm_key(read_desc)) if learned else None
    lcode = (lk or {}).get("code") if isinstance(lk, dict) else lk
    if lcode and confirm(lcode):
        lmeta = get_meta(lcode) if get_meta else None
        if not (get_meta and _meta_reject(lmeta, slot)):
            # defense-in-depth: the guard runs on the learned code too, so even a
            # stale/poisoned learned entry (an already-% series under a % read) parks
            # rather than silently serving the double-transform from cache.
            dt = (double_transform_reason(slot, (lmeta or {}).get("descriptor"),
                                          meta_agg(lmeta)) if get_meta else None)
            if dt:
                slot.update(status=SLOT_PENDING, candidates=[lcode],
                            reason=dt + " (learned entry)")
                return slot
            slot.update(resolved=lcode, codes=[lcode], status=SLOT_RESOLVED,
                        relevance=1.0, relevance_exact=True, bound_via_query="(learned)",
                        candidates=[lcode], reason="learned from a prior approval")
            _record_meta_of_record(slot, lcode, get_meta)
            return slot

    if not search:
        slot.update(status=SLOT_PENDING, reason="no search surface available")
        return slot

    attempts = build_search_attempts(slot)
    hitmap: dict = {}
    attempt_log = []
    for att in attempts:
        res = _do_search(search, att["query"], att["databases"], att["sa_status"])
        retried = False
        if not res and att["sa_status"]:
            # SA-advisory retry: a READ sa hint must NOT hard-filter search to zero
            # (the read can be wrong, and the catalog may tag SA differently). Retry
            # unfiltered — the SA cross-check (_meta_reject) still guards the bind, so
            # safety is unchanged. (Fed Funds: the nsa filter zeroed a real series.)
            res = _do_search(search, att["query"], att["databases"], None)
            retried = True
        attempt_log.append({"query": att["query"], "databases": att["databases"],
                            "sa_status": None if retried else att["sa_status"],
                            "n": len(res), "retried_unfiltered": retried})
        for h in res:
            code = _hit_code(h)
            if code and code not in hitmap:
                hitmap[code] = (h, att["query"])
    slot["search_attempts"] = attempt_log

    viable = []
    for code, (h, via) in hitmap.items():
        if not confirm(code):
            continue
        meta = get_meta(code) if get_meta else None
        if get_meta and _meta_reject(meta, slot):
            continue
        cand_desc = ((meta or {}).get("descriptor")
                     or (h.get("descriptor") if isinstance(h, dict) else "") or "")
        viable.append({
            "code": code, "descriptor": cand_desc, "via_query": via,
            "sim": round(descriptor_similarity(read_desc, cand_desc), 3),
            "exact": descriptor_exact(read_desc, cand_desc),
            "freq": meta_freq(meta), "agg": meta_agg(meta),
        })
    viable.sort(key=lambda v: (v["exact"], v["sim"]), reverse=True)
    slot["candidate_detail"] = viable          # full diagnostics (all viable)
    # Human-facing candidates = descriptor-RELEVANT ones only (don't surface sub-
    # threshold noise as if it were a plausible answer).
    relevant_all = [v for v in viable if v["exact"] or v["sim"] >= SIM_THRESHOLD]
    slot["candidates"] = [v["code"] for v in relevant_all]

    exact = [v for v in viable if v["exact"]]
    pick = None
    if len(exact) == 1:
        pick = exact[0]
    elif not exact:
        relevant = [v for v in viable if v["sim"] >= SIM_THRESHOLD]
        if len(relevant) == 1:
            pick = relevant[0]
        elif len(relevant) > 1 and relevant[0]["sim"] - relevant[1]["sim"] >= SIM_MARGIN:
            pick = relevant[0]

    if pick:
        dt = double_transform_reason(slot, pick["descriptor"], pick["agg"])
        if dt:                                 # transform-appropriateness → park, don't bind
            slot.update(status=SLOT_PENDING,
                        candidates=[v["code"] for v in relevant_all], reason=dt)
            return slot
        slot.update(resolved=pick["code"], codes=[pick["code"]],
                    status=SLOT_RESOLVED, relevance=pick["sim"],
                    relevance_exact=pick["exact"], bound_via_query=pick["via_query"],
                    reason=("exact descriptor match" if pick["exact"]
                            else f"descriptor match sim={pick['sim']}")
                    + f" via search {pick['via_query']!r}")
        _record_meta_of_record(slot, pick["code"], get_meta)
    else:
        if not viable:
            why = "no confident/relevant match — human supplies code@db"
        elif not any(v["sim"] >= SIM_THRESHOLD or v["exact"] for v in viable):
            why = (f"{len(viable)} candidate(s) but none descriptor-relevant "
                   f"(best sim={viable[0]['sim']}) — human supplies code@db")
        else:
            why = (f"{len([v for v in viable if v['sim'] >= SIM_THRESHOLD])} "
                   f"descriptor-similar candidates — human disambiguates")
        slot.update(status=SLOT_PENDING, reason=why)
    return slot


def resolve_chart(slots: list[dict], **kw) -> list[dict]:
    """Resolve every slot of a chart (Stage 2). Per-series independent."""
    return [resolve_slot(s, **kw) for s in slots]


def chart_status_from_slots(slots: list[dict]) -> str:
    """Roll N slot states up to a chart status string (matches ledger constants):
    any pending → 'awaiting_ticker'; all skipped → 'skipped'; else 'resolved'.
    (Defect 2: a chart is RESOLVED only when EVERY series is bound — never partial.)"""
    if not slots:
        return SLOT_PENDING
    if any(s.get("status") == SLOT_PENDING for s in slots):
        return "awaiting_ticker"
    if all(s.get("status") == SLOT_SKIPPED for s in slots):
        return SLOT_SKIPPED
    return SLOT_RESOLVED
