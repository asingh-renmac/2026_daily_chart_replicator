"""
build_chart.py — generic RenMac reconstruction from a RESOLVED G8 row.

Turns a confirmed ChartSpec + resolved per-series slots into a rendered figure:
pull each bound code (DLX), evaluate any formula via the G3 transform engine, lift
to the common frequency, clip the x-axis to the DERIVED end (latest plotted period,
the non-forecast rule), shade recessions if the read flagged them, and hand clean
labels to render.py (which fail-loud rejects any raw formula/ticker in a label).

This is the LAST seam and the least battle-tested one (G4 proved the transform math
on five hand-built charts; this generalizes it). The caller renders best-effort and
reports a snag rather than aborting the run.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import transforms as T            # noqa: E402
import haver_search as HS         # noqa: E402
import g4_lib as G                # noqa: E402
import resolve as R               # noqa: E402  (legend-label store + key canon)
from render import PlotSeries, RenderSpec, render  # noqa: E402

# A bracket lag tag, SIGN CAPTURED: `[-4]` (lag 4 obs) vs `[+4]` (lead 4 obs).
_LAG_RE = re.compile(r"\[\s*([-+]?)\s*(\d+)\s*\]")
_BARE_LAG_RE = re.compile(r"([-+]?)\s*(\d+)")


_LEVEL_PHRASES = {"", "level", "levels", "none", "raw", "actual", "index level",
                  "as reported", "not transformed", "no transformation"}

# Haver prints an AGGREGATION + UNITS line under a series name ("Avg, % p.a.",
# "Sum, Mil.$", "EOP, Index") that describes how the series IS, not anything done to
# it. The read hands that line over as `applied_transform`, and `_flat_transform` used
# to see the "%" and the annual stem and emit a CHANGE: 2026-08-11 `_2` plotted the 2y
# Treasury as `difv%(FCM2,1)` — a 1-period % change — where the source shows the plain
# level. So these are recognized as levels BEFORE any change rule can match. They are
# safe to treat this way because a genuine Haver transform always names the operation
# ("% Change - Year to Year", "3-month moving average"); a bare aggregation stem never
# does. `_AGG_UNIT_RE` matches the whole phrase only — "Avg" alone is an aggregation,
# but "3-month moving average" still routes to the MOV family.
_AGG_STEMS = r"avg|average|mean|sum|total|eop|end[\s\-]of[\s\-]period|bop|max|min|last|first"
_UNIT_STEMS = (r"%\s*p\.?\s*a\.?|percent\s+per\s+annum|% ?chg\b(?!.)|%|pct|ppt|index|idx|"
               r"mil\.?\$?|bil\.?\$?|thous\.?|units|persons|\$/\w+|\w+/\w+|sa|nsa|saar")
_AGG_UNIT_RE = re.compile(
    rf"^(?:(?:{_AGG_STEMS})|(?:{_UNIT_STEMS}))"
    rf"(?:\s*[,;]\s*(?:(?:{_AGG_STEMS})|(?:{_UNIT_STEMS})))*$", re.I)


def _is_agg_or_units(t: str) -> bool:
    """True when the phrase is purely Haver's aggregation/units descriptor rather than
    a transform — i.e. every comma-separated part is an aggregation or a unit stem."""
    return bool(t) and bool(_AGG_UNIT_RE.match(t.strip()))


def _flat_transform(t: str, target: str) -> str | None:
    """Map ONE flat (non-composed) normalized Haver transform phrase `t` onto the
    series expression `target` (a bare mnemonic OR a sub-formula like `movv(X,3)`).
    Returns a G3 formula, None for a level, or RAISES on an unrecognized phrase.

    Covers the finite Haver DLX vocabulary: DIF[F|V|A] (×%/L), YRYR (×%/L), MOV[V|A|T],
    ZS, LN — across the period qualifiers year-to-year / annual-rate / period-to-period
    / N-period. Frequency-agnostic: `diff%(x,1)` is one NATIVE period, YRYR/DIFA let G3
    annualize by the series' own frequency (no hard-coded 'month')."""
    if t in _LEVEL_PHRASES:
        return None
    mnum = re.search(r"(\d+)", t)
    n = int(mnum.group(1)) if mnum else 1
    pct = ("%" in t) or ("percent" in t) or ("pct" in t)
    logv = ("log" in t) or ("logarithm" in t) or bool(re.search(r"\bln\b", t))
    p2p = any(k in t for k in (
        "period to period", "period-to-period", "period over period",
        "period-over-period", "month to month", "month-over-month", "mom", "m/m",
        "quarter to quarter", "quarter-over-quarter", "q/q", "week to week", "w/w",
        "sequential", "prior period", "1-period", "one period"))
    yoy = any(k in t for k in (
        "year to year", "year-to-year", "year over year", "year-over-year",
        "yoy", "yr/yr", "y/y", "year ago", "yr ago", "12-month", "12 month"))
    # ANNUALIZED detection must also catch Haver's ABBREVIATED stem. DLX prints
    # "2-qtr %Change-ann" / "3-month %Change-ann" — the truncated "ann" matched NONE of the
    # spelled-out keywords, so the phrase fell through to the plain `diff%` branch and
    # rendered a NON-annualized change that looked plausible (2026-07-30: GDP 2-qtr plotted
    # 3.35 where Haver's own label read 6.81). A silent-wrong, so the stem is matched on a
    # word boundary: `ann`/`ann.`/`annual`/`annualized` after a space, hyphen, or at the end.
    annual = any(k in t for k in (
        "annual rate", "annual-rate", "annualized", "annualised", "saar",
        "at an annual rate", "compound annual", "compounded annual")) or (
        "annual" in t and "rate" in t) or bool(
        re.search(r"(?:^|[\s\-])ann(?:\.|ual(?:ized|ised)?)?(?:[\s\-]|$)", t))
    is_change = ("change" in t or "chg" in t or "difference" in t or "diff" in t
                 or pct or p2p)

    # moving average / sum / annual-rate FIRST (the phrase carries 'average'/'sum').
    if "moving" in t or re.search(r"\d+\s*mma\b", t) or "mov avg" in t or "mov. avg" in t:
        if not mnum:
            raise ValueError(f"moving average/sum needs an N-period window: {t!r}")
        if "sum" in t or "total" in t:
            return f"movt({target},{n})"
        if annual:                          # moving average expressed at an annual rate
            return f"mova({target},{n})"
        return f"movv({target},{n})"

    if any(k in t for k in ("z-score", "z score", "zscore", "standardiz")) or t == "zs":
        return f"zs({target})"

    if not is_change and (t in ("log", "ln", "natural log", "nat log", "log level")
                          or "logarithm" in t):
        return f"ln({target})"

    if yoy and is_change:                   # year-over-year (G3 uses the freq internally)
        return f"yryr%({target})" if pct else (
            f"yryrl({target})" if logv else f"yryr({target})")

    if annual and is_change:                # annualized N-period (default 1 native period)
        return f"difa%({target},{n})" if pct else (
            f"difal({target},{n})" if logv else f"difa({target},{n})")

    if is_change and ("average" in t or "avg" in t) and ("period" in t or pct):
        return f"difv%({target},{n})" if pct else f"difv({target},{n})"   # per-period mean

    if is_change:                           # period-to-period / N-period (DEFAULT family)
        return f"diff%({target},{n})" if pct else (
            f"diffl({target},{n})" if logv else f"diff({target},{n})")

    # recognized-but-unsupported (needs a pin) vs genuinely novel — both fail LOUD.
    if "year to date" in t or "year-to-date" in t or "ytd" in t:
        raise ValueError(f"year-to-date not computed by G3 (NeedPin): {t!r}")
    if "index" in t or "rebase" in t:
        raise ValueError(f"index/rebase needs an explicit base period — resolve as a "
                         f"formula, not a phrase: {t!r}")
    raise ValueError(
        f"unmappable applied_transform {t!r} — extend build_chart._flat_transform "
        "(fail-loud, not silent-level)")


def phrase_to_haver(phrase: str, mnem: str) -> str | None:
    """Translate a vision-read `applied_transform` PHRASE into a G3/Haver formula on
    the bare mnemonic `mnem`. Returns None for a level series, a formula string
    otherwise, and **raises** on a present-but-unrecognized transform.

    This is the renderer↔G3 seam: a description-bound series whose transform was given
    in WORDS must be run through the signed-off evaluator before plotting — NOT plotted
    raw. Fail-loud (never silently fall back to level) because a chart that plots the
    un-transformed series looks right but is wrong — the exact silent-wrong class the
    gate exists to stop.

    Composition: a compound phrase "<transform> of <N>-period moving average" (e.g.
    "% Change - Year to Year of 3-month moving average") is composed as
    head(movv(mnem,N)) — so the moving average is NEVER silently dropped. Any other
    compound falls through to the flat mapper, which fails loud rather than guessing."""
    t = (phrase or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    if t in _LEVEL_PHRASES or _is_agg_or_units(t):
        return None
    # compound "<head> of <N>… moving average" → head(movv(mnem,N)); guards against a
    # flat rule matching the head and SILENTLY dropping the inner moving average.
    parts = t.split(" of ", 1)
    if len(parts) == 2 and "mov" in parts[1]:
        mnum = re.search(r"(\d+)", parts[1])
        if not mnum:
            raise ValueError(f"compound MA needs an N-period window: {phrase!r}")
        inner = f"movv({mnem},{int(mnum.group(1))})"
        return _flat_transform(parts[0].strip(" -,"), inner)
    return _flat_transform(t, mnem)


def _applied_formula(slot: dict, mnem: str) -> str:
    """The formula string to evaluate for a description-bound slot: its
    `applied_transform` translated onto `mnem`, else the bare mnemonic (level)."""
    f = phrase_to_haver(slot.get("applied_transform") or "", mnem)
    return f or mnem


_FREQ_UNIT = {"M": "month", "Q": "quarter", "W": "week", "A": "year", "D": "day"}


def transform_label(formula_str: str, unit: str) -> str | None:
    """A clean, human transform label for the chart (legend/subtitle) derived from the
    OUTER function of the evaluated formula — the visual tripwire that makes a dropped
    transform self-evident (label says "6-month moving average" but the line is raw →
    the contradiction catches the eye). None for a level/bare series or an expression
    with no single outer transform (never returns raw formula syntax — that would fail
    render's clean-label guard)."""
    try:
        node = T.parse(formula_str)
    except Exception:
        return None
    if not isinstance(node, T.Func):
        return None                       # bare Series, sum/BinOp → no single label
    nums = [int(a.value) for a in node.args if isinstance(a, T.Num)]
    n = nums[0] if nums else None
    name = node.name
    log = getattr(node, "log", False)
    kind = "log change" if log else ("% change" if node.pct else "change")

    if name == "MOVV" and n:
        return f"{n}-{unit} moving average"
    if name == "MOVT" and n:
        return f"{n}-{unit} moving sum"
    if name == "MOVA" and n:
        return f"{n}-{unit} moving average, annual rate"
    if name == "YRYR":
        return f"{kind}, year-over-year"
    if name == "DIFA":
        base = f"{n}-{unit} annualized" if n and n > 1 else "annualized"
        return f"{base} {kind}"
    if name == "DIFV":
        return f"average {unit} {kind}" if not n or n == 1 else f"{n}-{unit} average {kind}"
    if name == "DIFF":
        return f"{kind}, period-over-period" if not n or n == 1 else f"{n}-{unit} {kind}"
    if name == "ZS":
        return "z-score"
    if name == "LN":
        return "natural log"
    return None


def _is_st_force(v) -> bool:
    """Truthy for the explicit subtitle-force override (`st_force=true`)."""
    return str(v or "").strip().lower() in ("1", "true", "yes")


def compose_subtitle(base: str, tlabels: list, st_force: bool) -> tuple[str, bool]:
    """Decide the SUBTITLE and whether the transform must ride in the LEGEND instead.
    Returns (subtitle, mixed_needs_legend).

    Default (house tripwire): a transform SHARED by every series is APPENDED to the
    subtitle; a MIXED set routes each transform to its legend label. `st_force` is an
    EXPLICIT human override (title round-trip `st_force=true`): the subtitle is used
    VERBATIM with nothing appended — honored because it was asserted, not detected.
    st_force scopes to the SUBTITLE only; a mixed chart still routes to the legend (a
    separate, orthogonal tripwire)."""
    nonlevel = [t for t in tlabels if t]
    all_same = bool(nonlevel) and all(t == tlabels[0] for t in tlabels)
    if all_same:
        if st_force:
            return base, False                 # verbatim — explicit override
        return (f"{base}, {tlabels[0]}" if base else tlabels[0]), False
    if nonlevel:                               # mixed/different → legend (st_force n/a)
        return base, True
    return base, False                         # all level → nothing to add


_LAG_WORDS_RE = re.compile(r"\b(lag(?:g(?:ed|ing)|s)?|lead(?:s|ing)?|led)\b", re.I)


def _shift_periods(lag) -> int:
    """Native-frequency `pandas.shift` amount for a Haver bracket lag tag.

    Tag convention (Haver's, and what the read carries): `[-n]` = LAG by n native
    observations — at date t the series shows its own value from n periods earlier;
    `[+n]` = LEAD by n. An UNSIGNED `[n]` reads as a lag, since `[-n]` is the form
    Haver actually prints and a lag is the intent behind a bare number.

    Sign discipline — the part that was silently wrong: `shift(k)` with k>0 moves
    values FORWARD in time, which IS a lag, so the shift is the NEGATION of the
    tag's sign (`[-4]` → shift(+4), `[+4]` → shift(-4)). The old parser captured
    only the digits and threw the sign away, so `[+4]` shifted identically to
    `[-4]` — a requested LEAD plotted as a LAG, with no error to notice.

    A non-empty tag that cannot be parsed RAISES. Returning 0 there (the old
    behaviour) silently plots the series unlagged, which looks perfectly
    plausible and is exactly the class of failure this pipeline refuses.
    """
    if lag is None:
        return 0
    if isinstance(lag, bool):
        raise ValueError(f"lag tag must be a bracket tag or number, got {lag!r}")
    if isinstance(lag, (int, float)):
        return int(abs(lag)) if lag <= 0 else -int(lag)   # tag sign, not shift sign
    raw = str(lag).strip()
    if not raw:
        return 0
    m = _LAG_RE.search(raw) or _BARE_LAG_RE.fullmatch(raw)
    if not m:
        raise ValueError(
            f"unparseable lag tag {lag!r} — expected a bracket tag like '[-4]' (lag 4 "
            f"observations) or '[+2]' (lead 2). Not plotting unlagged silently.")
    sign, n = m.group(1), int(m.group(2))
    return -n if sign == "+" else n


def _lag_tag(lag) -> str:
    """Canonical bracket tag for a legend, in the SAME notation the read used."""
    k = _shift_periods(lag)
    if not k:
        return ""
    return f"[-{k}]" if k > 0 else f"[+{-k}]"


def _states_lag(label: str) -> bool:
    """True when a human-ratified legend ALREADY conveys the shift, so appending the
    machine tag would only duplicate it (2026-08-03 rendered "…(%. lagged by 4qtrs)
    [-4]"). The tag still gets appended to any label that is silent on the shift —
    a lagged line must never plot without saying so."""
    return bool(_LAG_WORDS_RE.search(label or "") or _LAG_RE.search(label or ""))


_MONTH_ABBR = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _start_ts(sample_start) -> pd.Timestamp:
    """Parse the read (or a human override) sample-start into the window's LEFT edge.

    Supports a MONTH-level start so a chart can legitimately begin mid-year — `YYYY-MM`
    ('2025-07'), `Mon YYYY` ('Jul 2025'), or `Mon-YY` ('Jul-25') — anchored to the 1st of
    the month so the whole month shows. Falls back to year-only (→ Jan-31 of that year),
    then a 2-digit year, then a safe floor. A month WITHOUT a year (e.g. a stray 'AUG'
    axis-label misread) is NOT a start — it falls through to the floor rather than guessing
    a year."""
    s = str(sample_start or "").strip()
    m = re.search(r"(\d{4})[-/.](\d{1,2})\b", s)         # YYYY-MM
    if m:
        mo = min(max(int(m.group(2)), 1), 12)
        return pd.Timestamp(f"{int(m.group(1))}-{mo:02d}-01")
    mm = re.search(r"([A-Za-z]{3,})[\s\-]+(\d{4})", s)   # Mon YYYY
    if mm and mm.group(1).lower()[:3] in _MONTH_ABBR:
        return pd.Timestamp(f"{int(mm.group(2))}-{_MONTH_ABBR[mm.group(1).lower()[:3]]:02d}-01")
    my = re.search(r"([A-Za-z]{3,})[\s\-]+(\d{2})\b", s)  # Mon-YY
    if my and my.group(1).lower()[:3] in _MONTH_ABBR:
        yy = int(my.group(2))
        yr = 2000 + yy if yy <= 50 else 1900 + yy
        return pd.Timestamp(f"{yr}-{_MONTH_ABBR[my.group(1).lower()[:3]]:02d}-01")
    m = re.search(r"\d{4}", s)                            # year only
    if m:
        return pd.Timestamp(f"{m.group(0)}-01-31")
    m = re.search(r"\d{2}", s)                            # 2-digit year
    if m:
        yy = int(m.group(0))
        yr = 2000 + yy if yy <= 50 else 1900 + yy
        return pd.Timestamp(f"{yr}-01-31")
    return pd.Timestamp("2000-01-31")


def _freq_of(code_at_db: str) -> str:
    meta = HS.get_meta(code_at_db)
    f = ((meta or {}).get("frequency") or "M").strip().upper()
    return f[0] if f and f[0] in "MQWAD" else "M"


def _slot_mnemonics(slot: dict) -> set[str]:
    """Every bare mnemonic the slot binds (from codes + any formula), upper-cased."""
    mnems = {(c.split("@")[0]).upper() for c in (slot.get("codes") or []) if c}
    if slot.get("formula"):
        try:
            mnems.update(m.upper() for m in T.formula_mnemonics(slot["formula"]))
        except Exception:
            pass
    return mnems


def _is_mnemonic_label(text: str, slot: dict) -> bool:
    """True if `text` is nothing but this slot's mnemonics (+ operators/numbers) —
    i.e. a raw ticker string like 'LITRTRDA' or 'NRS - NRSI7', NOT a human
    description. This is the silent-degrade the legend guard must refuse."""
    mnems = _slot_mnemonics(slot)
    toks = [t for t in re.findall(r"[A-Za-z0-9]+", (text or "").upper())
            if not t.isdigit()]
    return bool(toks) and all(t in mnems for t in toks)


def _legend_label(slot: dict, store: dict | None = None,
                  allow_base_key: bool = True) -> str:
    """The confirmed, human-ratified legend label for a slot, with any `[-n]` lag tag
    re-appended.

    Floor (defect fix): a raw CODE/mnemonic must NEVER reach a legend. Order:
      1. this SLOT's own ratified label (`proposed_legend`) — the most specific artifact
         there is: it is what was shown in the resolution ask and approved FOR THIS SLOT.
         Preferring it makes a per-slot label immune to any cross-chart store aliasing
         (the 2026-07-30 same-ticker-two-transforms collision);
      2. the store, transform-qualified key first then the legacy blind key;
      3. else a base_descriptor that is a REAL human description (legacy charts);
      4. else RAISE — never fall back to the ticker (that silent degrade IS the bug)."""
    lag_tag = _lag_tag(slot.get("lag"))
    own = (slot.get("proposed_legend") or "").strip()
    if own and not _is_mnemonic_label(own, slot):
        lab = own
    else:
        hit = R.lookup_legend(R.load_legend() if store is None else store, slot,
                              allow_base=allow_base_key)
        if hit:
            lab = hit["label"].strip()
        else:
            base = _LAG_RE.sub("", slot.get("base_descriptor") or "").strip()
            if base and not _is_mnemonic_label(base, slot):
                lab = base                    # legacy: a genuine description was carried
            else:
                raise ValueError(
                    f"legend label not confirmed for {R.legend_key(slot)} — would emit a "
                    f"raw mnemonic ({base or slot.get('resolved')!r}). Resolve/confirm the "
                    f"legend label (Opus→confirm→store) before rendering; not falling back "
                    f"to the ticker.")
    return f"{lab} {lag_tag}" if lag_tag and not _states_lag(lab) else lab


def _assert_distinct_legends(labels: list[str]) -> None:
    """Two lines on ONE chart may never carry the SAME legend — the reader then cannot tell
    them apart, and it always means a label was aliased rather than authored (2026-07-30:
    both `fsdh@usecon` slots rendered '(1-qtr %chg saar)'). Fail LOUD; the duplicate is the
    symptom of a keying/collision bug, and a chart that ships it looks fine but misinforms."""
    seen: dict = {}
    for i, lab in enumerate(labels):
        k = re.sub(r"\s+", " ", (lab or "").strip().lower())
        if k and k in seen:
            raise ValueError(
                f"duplicate legend label on one chart: slots {seen[k]} and {i} both render "
                f"{lab!r}. Each plotted series needs its OWN label (a same-ticker chart must "
                f"distinguish its transforms) — confirm via the resolution round-trip "
                f"(`legend{i + 1}=…`); not rendering an ambiguous chart.")
        seen[k] = i


def _base_key_counts(slots: list) -> dict:
    """How many slots share each TRANSFORM-BLIND legend key — used to decide whether the
    legacy blind-key lookup is safe for a slot (it is not when the ticker repeats)."""
    counts: dict = {}
    for s in slots:
        try:
            counts[R.legend_key_base(s)] = counts.get(R.legend_key_base(s), 0) + 1
        except Exception:
            pass
    return counts


def _has_confirmed_legend(slot: dict) -> bool:
    """True when this slot's legend is a ratified artifact (its own `proposed_legend`, or a
    store entry) — the same source `_legend_label` draws from. A confirmed label is
    authoritative and already states its transform, so the auto transform-label must not
    be appended on top of it (that duplicates the qualifier and displaces the axis tag)."""
    try:
        if (slot.get("proposed_legend") or "").strip():
            return True
        return R.lookup_legend(R.load_legend(), slot) is not None
    except Exception:
        return False


_PLOT_KINDS = ("line", "bar", "stacked_bar")


def _plot_kind_of(slot: dict) -> str:
    """How this slot is DRAWN: a human/ledger `plot_kind` override wins, else the vision
    read's per-series `plot_kind`, else a line.

    Before 2026-07-30 the pipeline had NO representation for this at all — the read never
    reported it, the spec had no field, and render hard-coded `ax.plot`. So a Haver bar or
    stacked-contribution chart was silently redrawn as lines: not a mis-detection, an
    unrepresented dimension. Unknown values fall back to "line" (never raise: the shape is
    cosmetic, and a human can correct it with `--replot`)."""
    k = str(slot.get("plot_kind") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if "stack" in k:
        return "stacked_bar"
    if k.startswith("bar") or k.startswith("column"):
        return "bar"
    return k if k in _PLOT_KINDS else "line"


_X_PAD_PERIODS = 3          # observations of right-edge breathing room (0 disables)
_X_PAD_MIN_FRAC = 0.02      # …but never LESS than this share of the visible span
_X_PAD_MAX_FRAC = 0.06      # …and never more
# Mean calendar length of one native period, for measuring a pad in a series' OWN
# cadence rather than in the common grid it was interpolated onto.
_PERIOD_DAYS = {f: 365.25 / npy for f, npy in T.FREQ_BASE.items()}


def _x_end_pad(x_end: pd.Timestamp, x_start: pd.Timestamp, plotted: list,
               n_periods=None) -> pd.Timestamp:
    """Extend the DISPLAY right edge a few OBSERVATIONS past the last plotted point.

    Every series ends at its latest print, so the newest observation used to sit flush against
    the frame — exactly the value the reader came for, squeezed into the axis (and for bars,
    half-clipped by it). Padding the xlim by ~3 native periods lifts it off the edge.

    The pad is measured in the OWN period of the series that SETS the right edge, so it stays
    proportional to the spacing visible there — 3 weeks on a weekly chart, 3 months on a
    monthly one, and on a MIXED-frequency chart the cadence of whichever series reaches
    furthest right.

    That figure is then CLAMPED to a share of the visible span, at BOTH ends:
      * `_X_PAD_MAX_FRAC` ceiling — on a short low-frequency chart (say 14 quarterly bars)
        three whole periods is ~20% of the panel, trading a clipped point for a conspicuous
        empty margin. The ceiling still clears a bar's half-width, which is all the goal needs.
      * `_X_PAD_MIN_FRAC` floor — the original rule had NO floor, and "3 observations" is
        relative to the DATA's cadence, not to the CHART's width. On a 40-year monthly chart
        three months is 0.6% of the panel, i.e. a couple of pixels, so every long chart still
        read as flush (2026-08-11: the six charts padded 0.16%–1.27% of width). The eye judges
        the gutter in pixels, not observations, so the floor is what actually delivers the
        breathing room; it also keeps the gutter visually CONSISTENT from chart to chart.

    Display-only: the DATA window is untouched, so nothing is extrapolated and no phantom
    point is ever drawn."""
    n = _X_PAD_PERIODS if n_periods is None else int(n_periods)
    if n <= 0:
        return x_end
    best, span = None, None
    for ps in plotted:
        s = ps.series.dropna() if ps.series is not None else None
        if s is None or len(s) < 2:
            continue
        last = s.index.max()
        if best is None or last > best:        # the series that reaches furthest right
            best = last
            # Prefer the series' NATIVE cadence. The plotted index sits on the chart's
            # COMMON grid, so a quarterly series on a monthly chart has ~30-day spacing
            # and "3 observations" became 3 months instead of 3 quarters (2026-08-03 `_2`:
            # 93 days of pad where 3 quarters was intended).
            span = (_PERIOD_DAYS.get(ps.freq)
                    or float(pd.Series(s.index).diff().dt.days.median() or 0.0))
    if not span or span <= 0:
        return x_end
    pad = n * span
    visible = (x_end - x_start).days
    if visible > 0:
        floor = _X_PAD_MIN_FRAC * visible * (n / _X_PAD_PERIODS)
        pad = min(max(pad, floor), _X_PAD_MAX_FRAC * visible)
    return x_end + pd.Timedelta(days=round(pad))


def _x_start(read_start: pd.Timestamp, plotted: list) -> pd.Timestamp:
    """The DISPLAY left edge: the later of the read start and the first period that actually
    plots, so the panel never opens on a blank region.

    The read's `sample_start` can predate the data (a misread, or a source chart whose axis
    simply starts earlier than the series) — 2026-07-30 `_0` read 1948 while `zs(yryr(YPSVR))`
    begins 1962, leaving a dead decade. The DATA window is untouched (transforms need their
    run-up); only the xlim moves in. MIN across series so the LONGEST line still shows fully."""
    firsts = [s.series.dropna().index.min() for s in plotted
              if s.series is not None and len(s.series.dropna())]
    if not firsts:
        return read_start
    first = min(firsts)
    edge = first if first > read_start else read_start
    # A BAR is centered on its period stamp, so half of the first bar lies LEFT of that stamp.
    # With the edge sitting exactly on it, the frame slices that bar down the middle. Give
    # back a half-period so the opening bar reads whole (lines need no such pad — a line
    # starts AT its first point).
    halves = [0.5 * float(pd.Series(ps.series.dropna().index).diff().dt.days.median() or 0.0)
              for ps in plotted
              if ps.kind in ("bar", "stacked_bar") and ps.series is not None
              and len(ps.series.dropna()) > 1
              and ps.series.dropna().index.min() <= edge]
    if halves and max(halves) > 0:
        return edge - pd.Timedelta(days=round(max(halves)))
    return edge


def _end_anchor(end_series, series_map: dict, default_end: pd.Timestamp) -> pd.Timestamp:
    """Resolve an optional DISPLAY x-axis end pinned to a named series' last observation.

    Some source charts end the right edge where a specific series stops — e.g. a WEEKLY
    series (last obs a few days before month-end) overlaid on a MONTHLY series whose latest
    point anchors to the period-end. Taking the plain window end then lets the monthly point
    overhang the weekly line. `end_series` (a 'code@db' or bare mnemonic) pins the right edge
    to THAT series' last date so the axis matches the source, WITHOUT slicing the other
    series' final point out of the data (render only clamps the xlim, it doesn't re-slice)."""
    if not end_series:
        return default_end
    want = str(end_series).split("@")[0].strip().lower()
    sd = next((v for k, v in series_map.items() if k.lower() == want), None)
    if sd is None:
        raise ValueError(
            f"end_series {end_series!r} is not among the plotted series {list(series_map)}")
    v = sd.values.dropna()
    if not len(v):
        raise ValueError(f"end_series {end_series!r} has no observations")
    return v.index.max()


def _slot_freq(mnemonics: list, series_map: dict) -> str:
    """The native frequency a slot's expression evaluates at — the HIGHEST among its
    operands, matching how `transforms` lifts the lower-frequency side of a BinOp. This
    is the frequency a bracket lag counts in, so `[-4]` on a quarterly slot is 4
    quarters even when the CHART's common grid is monthly."""
    freqs = [series_map[m].freq for m in mnemonics if m in series_map]
    if not freqs:
        raise ValueError("cannot determine a slot frequency with no bound operands")
    return max(freqs, key=lambda f: T.FREQ_RANK[f])


def _window_end(plot_plan: list, series_map: dict) -> pd.Timestamp:
    """Derive the render window's END from what the PLOTTED series can actually cover,
    NOT from the raw max across every pulled operand.

    A composite (e.g. NFIB7 − NFIB6) is defined only where ALL its operands have data,
    so its last date is the MIN of its operands' last dates. Taking the plain max over
    every operand let a single longer operand stretch the x-axis PAST where the composite
    line stops — the line then dangles short of the right edge and its final move (the
    "dip") looks trimmed. So: per plotted slot, end = MIN(operand ends); window end =
    MAX(slot ends) so the longest plotted line still reaches the edge. A cross-operand
    date skew is also surfaced LOUD (usually a stale cache — the real 2026-07-14 cause)."""
    def _last(mn):
        v = series_map[mn].values.dropna()
        return v.index.max() if len(v) else None

    slot_ends = []
    for slot, fstr, _, _ in plot_plan:
        mns = [m for m in T.formula_mnemonics(fstr) if m in series_map]
        ends = [e for m in mns if (e := _last(m)) is not None]
        if not ends:
            continue
        # A LAGGED slot ends LATER than its raw data: `[-4]` carries the last quarterly
        # reading four quarters forward. Without this the window clipped the shift right
        # back off and the line stopped where the unlagged data did (2026-08-03 `_2`:
        # fwill runs to 2026Q3, so lagged it must reach 2027Q3). A LEAD moves the end
        # earlier and the same arithmetic shortens it.
        slot_end = min(ends)
        if (k := _shift_periods((slot or {}).get("lag"))):
            slot_end += T.shift_offset(_slot_freq(mns, series_map), k)
        slot_ends.append(slot_end)
        if len(mns) > 1 and max(ends) != min(ends):
            detail = {m: str(_last(m).date()) for m in mns}
            print(f"  [vintage-skew] {fstr}: operands end on different dates {detail} — "
                  f"the composite is truncated to the earliest; if unexpected, a parquet "
                  f"cache is stale (delete outputs/raw/<code>_*.parquet and re-pull)",
                  file=sys.stderr)
    if slot_ends:
        return max(slot_ends)
    return max(sd.values.index.max() for sd in series_map.values())


def render_row(row: dict, save_path: str) -> dict:
    """Render one resolved chart. Returns {ok, save_path, plotted, end, note}.
    Raises on a genuine failure (caller wraps + reports the snag)."""
    cs = row.get("chart_spec") or {}
    slots = row.get("series") or []
    if not slots or any(s.get("status") != "resolved" for s in slots):
        raise ValueError("render needs every slot resolved")

    # 1) pull every mnemonic (formula addends + plain binds) into one series_map.
    # The legacy transform-BLIND legend key is only a safe lookup when it is unique on the
    # chart; a ticker plotted twice under two transforms must match on its own qualified key.
    lstore = R.load_legend()
    bcounts = _base_key_counts(slots)

    def _lbl(slot: dict) -> str:
        return _legend_label(slot, store=lstore,
                             allow_base_key=bcounts.get(R.legend_key_base(slot), 0) <= 1)

    series_map: dict = {}
    plot_plan = []           # (slot, formula_str, label, axis)
    for s in slots:
        if s.get("formula"):
            mnems = T.formula_mnemonics(s["formula"])
            codes = s.get("codes") or []
            for mn, qc in zip(mnems, codes):
                if mn not in series_map:
                    code, db = qc.split("@")
                    series_map[mn] = G.pull(code, db, _freq_of(qc))
            plot_plan.append((s, s["formula"], _lbl(s), s.get("axis", "shared")))
        else:
            qc = s["resolved"]
            mn = qc.split("@")[0]
            if mn not in series_map:
                code, db = qc.split("@")
                series_map[mn] = G.pull(code, db, _freq_of(qc))
            # apply the read's worded transform (3mma/6mma/yoy%/…) via G3, not raw
            plot_plan.append((s, _applied_formula(s, mn), _lbl(s),
                              s.get("axis", "shared")))

    common = T.common_frequency(series_map)
    start = _start_ts(cs.get("sample_start"))
    end = _window_end(plot_plan, series_map)
    window = (start, end)                 # DATA slice — keep every series' natural last point
    # DISPLAY right edge: default is the data end, but a chart may pin it to a named series'
    # last observation (e.g. a weekly line that stops before a monthly operand's month-end).
    x_end = _end_anchor(cs.get("end_series"), series_map, end)

    # Transform LABELS (house-style, mirrors g4): SAME transform on every series →
    # subtitle; DIFFERENT per-series → each in its legend label; LEVEL → nothing.
    unit = _FREQ_UNIT.get(common, "period")
    tlabels = [transform_label(fstr, unit) for (_, fstr, _, _) in plot_plan]
    subtitle, mixed = compose_subtitle(
        row.get("subtitle") or "", tlabels, _is_st_force(row.get("st_force")))
    if mixed:                                                        # mixed → per-legend
        # A CONFIRMED legend (human/opus/store artifact) is authoritative and already
        # carries its own transform qualifier — appending the auto transform-label to it
        # duplicates it ("MBA … (NSA, y/y %chg)" + ", % change, year-over-year") and shoves
        # the axis tag outside the bracket. So append only to slots WITHOUT a ratified label.
        plot_plan = [(s, fstr,
                      f"{lab}, {tlabels[i]}" if (tlabels[i] and not _has_confirmed_legend(s))
                      else lab, ax)
                     for i, (s, fstr, lab, ax) in enumerate(plot_plan)]

    _assert_distinct_legends([lab for (_, _, lab, _) in plot_plan])

    plotted = []
    for s, formula_str, label, axis in plot_plan:
        series = T.transform(formula_str, series_map, window, common,
                             lag=_shift_periods(s.get("lag")))
        ax = "L" if axis in ("shared", "L", "left") else "R"
        mns = [m for m in T.formula_mnemonics(formula_str) if m in series_map]
        plotted.append(PlotSeries(label=label, series=series, axis=ax,
                                  kind=_plot_kind_of(s),
                                  freq=_slot_freq(mns, series_map) if mns else None))

    # DISPLAY left edge: never open on DEAD SPACE. A read start that predates the data
    # (2026-07-30 `_0`: read 1948 vs YPSVR from 1959) left a decade of empty panel. The DATA
    # window keeps the read start (a z-score/YoY needs the run-up history); only the xlim
    # moves in to the first period that actually plots.
    x_start = _x_start(start, plotted)
    # DISPLAY right edge: lift the newest observation off the frame (display-only, no data
    # is extrapolated). `x_pad_periods` in chart_spec overrides; 0 restores a flush edge.
    x_end = _x_end_pad(x_end, x_start, plotted, cs.get("x_pad_periods"))

    rec = None
    if cs.get("recession_shading"):
        rec = G.pull("RECESSM2", "USECON", "M").values

    axis_mode = cs.get("axis_mode", "shared")
    la = cs.get("left_axis") or {}
    ra = cs.get("right_axis") or {}
    y_left = ((la.get("min"), la.get("max"))
              if la.get("min") is not None and la.get("max") is not None else None)
    y_right = ((ra.get("min"), ra.get("max"))
               if axis_mode == "dual" and ra.get("min") is not None else None)

    # Title: a chosen title always wins (incl. a later --retitle). When the title
    # round-trip choice was `none` (no_title set, no chosen title), render TITLE-LESS —
    # the subtitle (which carries the non-suppressible transform label) is untouched.
    if row.get("no_title") and not row.get("chosen_title"):
        title = ""
    else:
        title = row.get("chosen_title") or row.get("subject") or "Chart"

    spec = RenderSpec(
        title=title,
        subtitle=subtitle,
        axis_mode="dual" if axis_mode == "dual" else "shared",
        recession=rec, x_range=(x_start, x_end), y_left=y_left, y_right=y_right,
        x_tick_years=cs.get("x_tick_years"), x_label_fmt=cs.get("x_label_fmt"))
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    _, info = render(plotted, spec, save_path=save_path)
    # `drawn` carries the PlotSeries actually rendered, so a caller can run
    # validate.check_last_value against the data BEHIND the pixels rather than
    # re-deriving it. Re-deriving means recreating the window, the common frequency
    # and the applied formula by hand, and a window-ZS evaluated over a different
    # window is a different number — the check would then be validating a
    # reconstruction of a reconstruction. Additive: existing callers read
    # save_path/plotted/end and are unaffected.
    return {"ok": True, "save_path": save_path, "plotted": list(info["plotted"]),
            "end": str(end.date()), "drawn": plotted, "window": window,
            "common_freq": common}
