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
    """Stable lookup key for a descriptor: lower-cased, whitespace-collapsed.

    Deliberately unchanged. Every text-keyed store on disk is filed under this exact
    function, so loosening it here would not widen matching — it would orphan every
    entry already written. The forgiving behaviour lives in `_loose_key` instead, as a
    SECOND lookup, which needs no migration.
    """
    return re.sub(r"\s+", " ", (text or "").strip().lower())


_ARTICLES = ("the ", "a ", "an ")
_FORMULA_SHAPED = re.compile(r"^[a-z][a-z0-9_%]*\s*\(.*\)\s*$")


def _loose_key(text: str) -> str:
    """A forgiving second-chance key for descriptor lookups (plan.md §16.2).

    `_norm_key` forgives case and spacing and nothing else, so a single leading article
    re-parks a series the operator has already answered. That bites hard here because the
    descriptor is WRITTEN BY THE MODEL from the commentary, so its wording drifts between
    runs. Measured against the real store: `civilian unemployment rate` hits, while
    `the civilian unemployment rate` and `civilian unemployment rate (SA)` both miss.

    Drops leading articles, parenthetical qualifiers and punctuation. It deliberately does
    NOT drop a leading country or region word — `US retail sales` and `UK retail sales`
    are different series, and folding those together would be a silent wrong bind of the
    worst kind, which is exactly what this lane refuses to do.

    This is canonicalization, NOT similarity: two descriptors either reduce to the same
    string or they do not. Nothing here matches on resemblance, because these are
    unreviewed personal answers and a plausible-looking wrong match is worse than a park.
    """
    s = _norm_key(text)
    # A formula-shaped descriptor is ALREADY canonical, and its parentheses carry the
    # substance rather than a qualifier. Stripping them reduced `zs(nfib: net percent
    # raising worker compensation...)` and `zs(nfib: single most important problem...)`
    # to the same key, `zs` — two different series, one key. `learned_lookup` refused to
    # pick between them so nothing was mis-bound, but relying on that is backwards: the
    # right move is not to mangle a formula in the first place.
    if _FORMULA_SHAPED.match(s):
        return s
    s = re.sub(r"\([^)]*\)", " ", s)                 # qualifiers like "(SA)"
    s = re.sub(r"[^a-z0-9%+&/ ]+", " ", s)           # punctuation out; unit chars kept
    s = re.sub(r"\s+", " ", s).strip()
    for article in _ARTICLES:
        if s.startswith(article):
            return s[len(article):]
    return s


# The SA/NSA twins of one series share a descriptor once the units parenthetical is
# stripped, so `_norm_key` alone files both under ONE key: whichever is answered second
# destroys the first, and the descriptor re-parks forever after because whatever is
# stored is wrong for half the requests. Measured 2026-09-11 on a colleague's store,
# where `CPI-U: Commodities Less Food and Energy Commodities (Core Goods)` held the SA
# code under the key an NSA request also reaches.
#
# U+241F is the PRINTABLE "symbol for unit separator" -- visible when someone opens the
# JSON, and outside the character set any descriptor or `_norm_key` output can contain,
# so it cannot collide with a real key. Keys stay unqualified when the adjustment is
# unknown, which is what every store written before this contains: those keep resolving
# through the legacy branch below and are superseded naturally as they are re-answered.
_SA_KEY_SEP = "\u241f"


def store_key(descriptor: str, adjustment: str = "") -> str:
    """The key a descriptor is filed under, qualified by seasonal adjustment when known."""
    base = _norm_key(descriptor)
    adj = _sa_norm(adjustment)
    return f"{base}{_SA_KEY_SEP}{adj}" if adj else base


def _key_base(key: str) -> str:
    """The descriptor half of a stored key, qualified or not."""
    return key.split(_SA_KEY_SEP, 1)[0]


def _adj_compatible(entry, want: str) -> bool:
    """False only when the entry states an adjustment that CONTRADICTS the request.

    Silence is not disagreement: a legacy entry records no adjustment and must keep
    working, and a request with no hint is not asking for one. Both fall through to the
    `_meta_reject` cross-check, which re-reads the adjustment from DLX anyway.
    """
    if not want or not isinstance(entry, dict):
        return True
    got = _sa_norm(entry.get("adjustment") or "")
    if not got:
        return True
    if want in ("sa", "saar") and got in ("sa", "saar"):
        return True
    return want == got


def learned_lookup(learned: dict, descriptor: str, sa_hint: str = "") -> Optional[dict]:
    """Exact key first, then one forgiving retry (§16.2).

    The retry fires only when the loose key identifies exactly ONE code. If two stored
    descriptors collapse together and disagree about the ticker, this returns nothing and
    the slot parks: an ambiguous memory is not a licence to pick one.

    A loose hit is not a shortcut past the guards. Callers still re-confirm the code
    against DLX and still run `_meta_reject` and `double_transform_reason` over it, so
    this widens what reaches the checks, never what escapes them.

    `sa_hint` picks between SA/NSA twins filed under the same descriptor. It is a
    PREFERENCE, not a filter: an unqualified legacy entry still answers, because
    refusing one would re-ask every park recorded before the key carried adjustment.
    """
    if not learned:
        return None
    want = _sa_norm(sa_hint)
    if want:
        exact = learned.get(store_key(descriptor, want))
        if exact is not None:
            return exact
    exact = learned.get(_norm_key(descriptor))
    if exact is not None and _adj_compatible(exact, want):
        return exact
    loose = _loose_key(descriptor)
    if not loose:
        return None
    hits: dict[str, object] = {}
    for key, val in learned.items():
        if _loose_key(_key_base(key)) == loose and _adj_compatible(val, want):
            code = val.get("code") if isinstance(val, dict) else val
            if code:
                hits[str(code).strip().lower()] = val
    return next(iter(hits.values())) if len(hits) == 1 else None


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


def _formula_transform_shape(formula: str) -> str:
    """The transform tag implied by an explicit G3 `formula`: operands replaced by `#`.

    Returns "" when the expression applies NO transform — a bare series (`GDPH`) or a
    composite operand (`NRS - NRSI7`), whose identity already lives in `legend_key_base`
    via `expr_key`. Only a Func at the ROOT is a transform, so that test is the gate;
    without it every composite would gain a spurious `|#-#` suffix and stop matching its
    warmed store entry."""
    import transforms as _T                  # local: build_chart imports resolve
    node = _T.parse(formula)                 # raises → caller falls back to the phrase
    if not isinstance(node, _T.Func):
        return ""
    shape = formula
    # Longest-first so a mnemonic that is a prefix of another (NRS vs NRSI7) cannot
    # partially consume it. The optional `@db` is swallowed with the code, so a
    # qualified operand canonicalizes the same as a bare one.
    for mn in sorted(_T.formula_mnemonics(formula), key=len, reverse=True):
        shape = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(mn)}(?:@[A-Za-z0-9_]+)?"
                       rf"(?![A-Za-z0-9_])", "#", shape)
    # Collapse a composite OPERAND to a single `#`. The tag names the transform chain;
    # WHICH series it wraps is already in `legend_key_base` (`expr:NFIB7-NFIB6`), so
    # leaving the arithmetic here would both duplicate it and change every warmed
    # composite key — `ZS((#-#))` where the store holds `ZS(#)`. Each rule tells a
    # function's ARGUMENT parens from a GROUPING paren by what precedes the `(`;
    # treating them alike eats the call's own parens and yields a malformed `ZS(YRYR%#)`.
    _CALL = r"(?<=[A-Za-z0-9_%])"
    _GROUP = r"(?<![A-Za-z0-9_%])"
    _OPERANDS = r"\(\s*#(?:\s*[-+*/]\s*#)+\s*\)"
    # A composite operand also appears UNPARENTHESIZED as a call's first argument —
    # `difa%(LGTPRIVA/PCU,3)` → `DIFA%(#/#,3)`. The `+` needs one operator, so a plain
    # `movv(#,3)` cannot match and lose its window argument.
    _BARE_OPERANDS = r"(?<=[A-Za-z0-9_%]\()\s*#(?:\s*[-+*/]\s*#)+\s*(?=[,)])"
    while True:
        collapsed = re.sub(_CALL + _OPERANDS, "(#)", shape)      # f(#-#)  → f(#)
        collapsed = re.sub(_BARE_OPERANDS, "#", collapsed)       # f(#/#,3)→ f(#,3)
        collapsed = re.sub(_GROUP + _OPERANDS, "#", collapsed)   # (#-#)   → #
        collapsed = re.sub(_GROUP + r"\(\s*#\s*\)", "#", collapsed)  # ((#)) → (#)
        if collapsed == shape:
            break
        shape = collapsed
    return re.sub(r"\s+", "", shape.upper())


def transform_key(slot: dict) -> str:
    """Canonical TRANSFORM tag for a slot, or "" for a level series.

    Canonicalized on the G3 FORMULA (operands replaced by `#`), not the read's wording,
    so two spellings of the same math ("2-qtr %Change-ann" / "2-quarter % change
    annualized" → `difa%(#,2)`) share one label, while genuinely different math does not.

    The slot's OWN formula wins when it has one (§15.1a). Deriving the tag from the
    phrase instead is under-qualified whenever the phrase is compound: `render_row`
    ignores `applied_transform` once a `formula` is present, so a slot that RENDERED
    `zs(yryr%(GDPH))` was keyed `ZS(#)` off the words "% Change - Year to Year, Z-Score"
    — the y/y silently dropped by the flat phrase mapper. Two slots on one ticker, one a
    z-score of the level and one a z-score of the y/y change, then collided on a single
    key: the 2026-07-30 defect this qualification exists to prevent, reintroduced through
    the phrase. The formula is what was actually drawn, so it is the honest source."""
    formula = (slot.get("formula") or "").strip()
    if formula:
        try:
            return _formula_transform_shape(formula)
        except Exception:
            pass                             # unparseable formula → fall back to words
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
    seasonally adjusted; the real guard is sa-vs-nsa.

    `sa_status` comes from the CATALOG. `Haver.metadata` has no such field (§17.2), and
    this function is also called with DLX metadata — from `_meta_reject` on the learned
    -entry path — where `got` was therefore always "" and the check always passed. A
    hard cross-check documented as catching a silent-wrong mis-bind was, on that path,
    doing nothing at all. Falling back to the descriptor parenthetical is the same
    signal D18 already trusts to tell an SA/NSA twin apart, so this adds no new
    assumption; it just stops throwing the signal away when the catalog is not the
    source.
    """
    want = _sa_norm(sa_hint)
    got = (_sa_norm((meta or {}).get("sa_status") or (meta or {}).get("sa"))
           or _sa_of_descriptor((meta or {}).get("descriptor") or ""))
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


# ──────────────────── Exact-tie discrimination (§15.2) ───────────────────────
# Fields that describe the DATA. If any of these differs, the two candidates are not
# the same series and no amount of descriptor similarity says otherwise.
#
# `enddate` is deliberately absent: a mirror can be refreshed on a different schedule,
# so a later end date means fresher, not different. SA status is absent because it
# lives in the descriptor's units parenthetical, and units themselves surface here as
# `magnitude` (Thous vs Mil), which is sharper than comparing parenthetical text.
_DATA_BEARING = ("shortsource", "longsource", "startdate", "numobs",
                 "frequency", "aggtype", "magnitude", "datatype", "diftype")


def _sa_of_descriptor(descriptor: str) -> str:
    """Seasonal-adjustment status from the descriptor's UNITS parenthetical.

    `Haver.metadata` has no SA field — the only signal is the `(NSA, Thous)` tail, and
    `descriptor_exact` strips exactly that tail to make matching robust. So an SA/NSA
    twin arrives as a descriptor-exact tie and, on every OTHER field, is identical:
    `lapriv@labor` and `lapriva@labor` are both BLS, both 1052 observations from 1939,
    both AVG, both magnitude 3. G19f caught this — the first reading of §15.2 assumed
    units would surface as `magnitude` and separate them, and measurement says they do
    not. Without this the tie-break would call an SA/NSA pair a mirror and bind one at
    random, which is a silently wrong chart of exactly the kind §15 exists to prevent.

    Token-level on the LAST parenthetical, so "Not Seasonally Adjusted" in a series
    NAME cannot be mistaken for the units tag.
    """
    groups = re.findall(r"\(([^()]*)\)", descriptor or "")
    if not groups:
        return ""
    tokens = {t.strip().lower() for t in re.split(r"[,;]", groups[-1])}
    for tag in ("saar", "nsa", "sa"):
        if tag in tokens:
            return tag
    return ""
# Catalog bookkeeping, NOT properties of the data. `lanagra@usecon` and `lanagra@labor`
# are the same BLS series, same 1052 observations from 1939, and differ ONLY on `group`
# (E30 vs E40) and the minute they were refreshed. Treating either as evidence would
# park a genuine mirror — the exact over-parking this section exists to stop.
_CATALOG_ONLY = ("group", "decprecision", "datetimemod", "geography1", "geography2")

# Fields that make a series what it IS. An SA and NSA twin of one series agrees on all
# of them; `numobs` and `startdate` are left out on purpose, because a seasonally
# adjusted copy legitimately begins later than its raw sibling.
_IDENTITY_FIELDS = ("shortsource", "longsource", "frequency", "aggtype", "magnitude",
                    "datatype", "diftype")

# Only the NSA direction is detected from words. An unstated preference already defaults
# to seasonally adjusted, so a matching `\bsa\b` would change nothing while risking a
# false positive on an abbreviation inside a series name.
_NSA_WORDS = re.compile(
    r"\b(nsa|not[\s-]+seasonally[\s-]+adjusted|non[\s-]*seasonally[\s-]+adjusted)\b",
    re.I)

_METADATA_CACHE: dict[str, Optional[dict]] = {}


def _meta_value(v) -> str:
    """Comparable, printable form of one DLX metadata cell (dates, Timestamps, ints)."""
    return "" if v is None else str(v).strip()


def haver_metadata(code_at_db: str) -> Optional[dict]:
    """DLX's OWN metadata for `code@db` — 18 fields, ~200 ms measured, cached per process.

    Distinct from `haver_search.get_meta`, which reads the Neon catalog mirror and
    returns FOUR fields (descriptor, frequency, agg_type, sa_status). Those four are too
    thin to separate two database mirrors, which is why an exact tie had no evidence to
    break it. These come from DLX itself, so `enddate` is also the live one rather than
    the catalog's stale copy.

    Returns None when DLX cannot answer; every caller must treat that as "cannot judge"
    and fall back to parking, never to a guess."""
    key = (code_at_db or "").strip().lower()
    if not key:
        return None
    if key in _METADATA_CACHE:
        return _METADATA_CACHE[key]
    out = None
    try:
        code, _, db = key.partition("@")
        if code and db:
            frame = _haver().metadata(code, db)
            # Haver answers a failed metadata query with an ErrorReport DICT rather than
            # raising, so a truthiness check alone would sail straight past it.
            if not isinstance(frame, dict) and frame is not None and len(frame):
                out = {c: frame[c].iloc[0] for c in frame.columns}
    except Exception:
        out = None
    _METADATA_CACHE[key] = out
    return out


def metadata_summary(code_at_db: str) -> str:
    """One line an economist can judge: source, span, observation count."""
    m = haver_metadata(code_at_db)
    if not m:
        return f"{code_at_db} (DLX metadata unavailable)"
    return (f"{code_at_db}: {_meta_value(m.get('shortsource')) or '?'}, "
            f"{_meta_value(m.get('startdate'))} to {_meta_value(m.get('enddate'))}, "
            f"{_meta_value(m.get('numobs'))} obs, {_meta_value(m.get('frequency'))}")


def mirror_differences(codes: list[str]) -> Optional[dict]:
    """{field: {code: value}} for every DATA-BEARING field on which `codes` disagree.

    `{}` means they are true database mirrors of one series. `None` means DLX could not
    be reached for at least one of them, so nothing was proved either way."""
    metas = {c: haver_metadata(c) for c in codes}
    if any(m is None for m in metas.values()):
        return None
    diffs: dict[str, dict] = {}
    for field in _DATA_BEARING:
        values = {c: _meta_value(m.get(field)) for c, m in metas.items()}
        if len(set(values.values())) > 1:
            diffs[field] = values
    # Derived, because DLX does not expose it as a field of its own.
    sa = {c: _sa_of_descriptor(_meta_value(m.get("descriptor"))) for c, m in metas.items()}
    if len(set(sa.values())) > 1:
        diffs["seasonal_adjustment"] = sa
    return diffs


def sa_requested(slot: dict) -> str:
    """`"sa"`, `"nsa"`, or `""` when the read says nothing — what the OPERATOR asked for.

    An explicit `sa_hint` wins. Failing that, only the NSA direction is read out of the
    descriptor, because an unstated preference already resolves to seasonally adjusted
    (D14) and so detecting "SA" in words would change no outcome."""
    hint = _sa_norm(slot.get("sa_hint") or "")
    if hint == "nsa":
        return "nsa"
    if hint in ("sa", "saar"):
        return "sa"
    text = f"{slot.get('base_descriptor') or ''} {slot.get('description') or ''}"
    return "nsa" if _NSA_WORDS.search(text) else ""


def _pick_sa_twin(exact: list[dict], want: str) -> tuple[Optional[dict], str]:
    """Choose between the SA and NSA copies of ONE series (decision D14).

    House rule: seasonally adjusted unless the read asks for raw. Commentary charts are
    about momentum, and an NSA line answers a different question than the one the words
    ask — chart 5 of the G19f baseline ("payroll momentum") would have plotted NSA
    payrolls. Raw is still reachable, but only by saying so.
    """
    def sa_of(v) -> str:
        desc = _meta_value((haver_metadata(v["code"]) or {}).get("descriptor"))
        tag = _sa_of_descriptor(desc)
        return "sa" if tag == "saar" else tag

    target = want if want in ("sa", "nsa") else "sa"
    matches = [v for v in exact if sa_of(v) == target]
    fell_back = False
    if not matches and target == "sa":
        # "Only if there is no seasonally adjusted version, use the raw one."
        matches = [v for v in exact if sa_of(v) == "nsa"]
        fell_back = True
    if not matches:
        return None, (f"asked for {target.upper()} but no candidate's descriptor says so: "
                      + ", ".join(v["code"] for v in exact))

    # More than one copy of the right vintage means it is ALSO a database mirror; reuse
    # the usecon preference rather than inventing a second ordering.
    in_usecon = [v for v in matches
                 if v["code"].split("@")[-1].lower() == "usecon"] or matches
    pick = max(in_usecon, key=lambda v: _meta_value(
        (haver_metadata(v["code"]) or {}).get("enddate")))
    others = [v["code"] for v in exact if v["code"] != pick["code"]]
    if fell_back:
        return pick, (f"no seasonally adjusted copy exists, so bound the raw one "
                      f"({pick['code']}) over {', '.join(others)}")
    return pick, (f"the candidates are the seasonally adjusted and raw copies of one "
                  f"series; bound the {target.upper()} one ({pick['code']}) over "
                  f"{', '.join(others)}"
                  + ("" if want else " — say \"NSA\" in the descriptor to get the raw one"))


def break_exact_tie(exact: list[dict],
                    sa_want: str = "") -> tuple[Optional[dict], str]:
    """Separate two-or-more descriptor-exact candidates using DLX metadata (§15.2).

    Returns `(pick, reason)`; `pick` is None when the slot must still park, and the
    reason is written to be ANSWERABLE — the old message ("both scored 1.0, similarity
    is not evidence") stated a fact the operator could not act on, when the evidence to
    act on was one 200 ms call away."""
    codes = [v["code"] for v in exact]
    diffs = mirror_differences(codes)

    if diffs is None:
        return None, ("descriptor-exact on more than one candidate and DLX metadata was "
                      "unavailable to separate them: " + ", ".join(codes))

    # D14 — the candidates are the SA and NSA copies of ONE series. They agree on every
    # identity field, so the only question is which vintage the operator wants, and the
    # house has an answer. This runs BEFORE the same-database park below, because that
    # park is what an SA/NSA pair in one database would otherwise hit.
    if diffs and "seasonal_adjustment" in diffs and not (set(diffs) & set(_IDENTITY_FIELDS)):
        return _pick_sa_twin(exact, sa_want)

    if diffs:                                  # decision D2: any data difference parks
        return None, (
            f"{len(codes)} candidates match the descriptor exactly but DLX says they are "
            f"different series (differ on {', '.join(sorted(diffs))}) — "
            + "; ".join(metadata_summary(c) for c in codes))

    # Identical on everything INCLUDING seasonal adjustment. Two such codes in ONE
    # database cannot be mirrors — a database does not hold one series twice under two
    # names — and with nothing left to tell them apart there is nothing to decide on.
    databases = [c.split("@")[-1].lower() for c in codes]
    if len(set(databases)) < len(databases):
        return None, (
            f"{len(codes)} candidates match the descriptor exactly and live in the SAME "
            f"database, so they are different series rather than mirrors — "
            + "; ".join(f"{c} ({(haver_metadata(c) or {}).get('descriptor', '?')})"
                        for c in codes))

    # True mirrors. `usecon` holds only US series, so its PRESENCE in the tie is the
    # evidence that the request is US-scoped; this can never reach for `usecon` on a
    # non-US request, because it would not be a candidate.
    in_usecon = [v for v in exact if v["code"].split("@")[-1].lower() == "usecon"]
    if in_usecon:
        # More than one usecon candidate is possible (two codes, one database); the
        # fresher copy wins, which is the only thing `enddate` is allowed to decide.
        pick = max(in_usecon, key=lambda v: _meta_value(
            (haver_metadata(v["code"]) or {}).get("enddate")))
        return pick, (f"identical on every data-bearing field in DLX, so a database "
                      f"mirror of one series ({', '.join(codes)}); bound the usecon copy")

    # Mirrors with no usecon among them. Do NOT invent an ordering between `labor` and
    # `empl` — a preference nobody has reasoned about is not evidence.
    return None, (
        f"the same series mirrored across {', '.join(sorted(c.split('@')[-1] for c in codes))} "
        f"with no usecon copy — identical on every data-bearing field, so pick a database: "
        + ", ".join(codes))


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
    lk = learned_lookup(learned, read_desc, slot.get("sa_hint") or "")
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
    tie_reason = ""
    if len(exact) == 1:
        pick = exact[0]
    elif len(exact) > 1:
        # Two exact matches used to reach no branch at all and park — served strictly
        # worse than ZERO exact matches, which at least got the similarity fallback.
        # `descriptor_exact` compares normalized token sets with the units parenthetical
        # stripped, so Haver's database mirrors are indistinguishable BY CONSTRUCTION;
        # the catalog cannot break this tie and was never going to. DLX can (§15.2).
        pick, tie_reason = break_exact_tie(exact, sa_requested(slot))
        slot["tie_metadata"] = {v["code"]: {
            k: _meta_value(val) for k, val in (haver_metadata(v["code"]) or {}).items()
        } for v in exact}
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
                    + f" via search {pick['via_query']!r}"
                    + (f"; {tie_reason}" if tie_reason else ""))
        _record_meta_of_record(slot, pick["code"], get_meta)
    else:
        if tie_reason:
            # The tie-break already looked at DLX and can say WHICH evidence separates
            # the candidates. Anything more generic would throw that away.
            why = tie_reason
        elif not viable:
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
