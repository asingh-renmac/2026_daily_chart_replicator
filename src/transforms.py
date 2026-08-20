"""
transforms.py — Haver formula-bar parser + evaluator (plan.md §4).

Implements the PINNED map (§4.2) and the evaluation order signed off at G3 (§4.6):

  * eval(node, window) -> (value, freq): every math transform runs at the node's
    NATIVE frequency (native n, native freq_base). The transform NEVER touches a
    common grid (Decision 1, REVISED — transform-then-interpolate).
  * BinOp lifts the lower-frequency *series* operand up to the higher operand's
    frequency before the op; a scalar operand (freq None) is broadcast, no lift
    (item 2 fold-in).
  * ZS computes mu/sigma over the DISPLAY WINDOW only but RETURNS z over the full
    buffered range it received (Bug A) — so an outer op with lookback > EDGE_TOL
    is not silently starved. Nested ZS is supported (Decision 4).
  * INDEX is native-units only; an APPLIED index whose base precedes the pull
    raises NeedRebuffer (Decision 5).
  * Interpolation to common_freq happens in finish(), AFTER transform+lag, with
    the low-freq obs anchored at its PERIOD-END month (QUARTER_ANCHOR, G4-pinned),
    never extrapolating past the series' native first/last obs.
  * finish() anchors the first-valid tolerance guard on eff_inception = MAX
    inception across ALL series in the subtree (Bug B).

Anything not in the pinned map (YTD/DYTD or any novel token) raises NeedPin so the
caller can route the chart to Teams and park it — never a guessed formula.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Constants (plan.md §4.2 / §4.5 / §4.6)
# --------------------------------------------------------------------------- #

FREQ_BASE = {"A": 1, "Q": 4, "M": 12, "W": 52}      # npy — periods per year
FREQ_RANK = {"A": 1, "Q": 4, "M": 12, "W": 52, "D": 365}   # for "highest freq"
EDGE_TOL = {"M": 4, "Q": 2, "W": 13}                # native periods (Dec.2)

# Period-END pandas offset aliases (QUARTER_ANCHOR — low-freq obs sit at the end
# of their period). 'ME'/'QE'/'YE' are the modern (pandas >= 2.2) spellings.
_PANDAS_FREQ_END = {"A": "YE", "Q": "QE", "M": "ME", "W": "W"}
_PANDAS_FREQ_END_LEGACY = {"A": "A", "Q": "Q", "M": "M", "W": "W"}


# --------------------------------------------------------------------------- #
# Errors — fail loud, never silently wrong (plan.md §4.3a)
# --------------------------------------------------------------------------- #

class TransformError(Exception):
    """Base for all parser/evaluator failures."""


class ParseError(TransformError):
    """Malformed formula string."""


class NeedPin(TransformError):
    """Token absent from the pinned map (e.g. YTD/DYTD, or anything novel).

    The caller routes the chart to Teams ("not supported; confirm how to handle")
    and parks it — same route as an unresolved ticker. Never a guessed formula.
    """

    def __init__(self, token: str, detail: str = ""):
        self.token = token
        super().__init__(
            f"NeedPin: '{token}' is not in the pinned map; route to Teams"
            + (f" ({detail})" if detail else "")
        )


class NeedRebuffer(TransformError):
    """An applied INDEX whose base year precedes the pulled buffer (§4.6 Dec.5)."""

    def __init__(self, code: str, base: str):
        self.code = code
        self.base = base
        super().__init__(
            f"NeedRebuffer: INDEX base '{base}' for series '{code}' precedes the "
            f"pulled buffer; re-pull back to the base period before rebasing"
        )


# --------------------------------------------------------------------------- #
# Series container
# --------------------------------------------------------------------------- #

@dataclass
class SeriesData:
    """A native-frequency, lookback-buffered series and its metadata.

    `values` is a pandas Series indexed by **period-end** timestamps at the
    native frequency (e.g. quarterly obs dated Mar/Jun/Sep/Dec — QUARTER_ANCHOR).
    `inception` is the series' true first observation date (from get_series); it
    defaults to the first valid index of `values` if not supplied.
    """

    values: pd.Series
    freq: str                       # 'M' | 'Q' | 'W' | 'A'
    code: str = ""
    inception: Optional[pd.Timestamp] = None

    def __post_init__(self):
        if self.freq not in FREQ_RANK:
            raise TransformError(f"unknown frequency {self.freq!r} for {self.code!r}")
        if self.inception is None:
            fv = self.values.first_valid_index()
            self.inception = pd.Timestamp(fv) if fv is not None else None


# --------------------------------------------------------------------------- #
# AST
# --------------------------------------------------------------------------- #

@dataclass
class Series:
    code: str                       # raw mnemonic as written, may include @db


@dataclass
class Num:
    value: float


@dataclass
class Func:
    name: str                       # canonical stem: DIFF/DIFV/DIFA/YRYR/MOVV/...
    pct: bool                       # '%' present
    log: bool                       # 'L' (log) type present
    centered: bool                  # trailing 'C'
    args: list                      # [node, ...]; n (if any) is an int literal arg
    index_base: Optional[str] = None
    index_value: Optional[float] = None


@dataclass
class BinOp:
    op: str
    left: object
    right: object


Node = Union[Series, Num, Func, BinOp]

# Function-name grammar (case-insensitive). C = centered, % = percent, L = log.
_RE_DIF = re.compile(r"^DIF([FVA])(%|L)?(C)?$")
_RE_YRYR = re.compile(r"^YRYR(%|L)?$")
_RE_MOV = re.compile(r"^MOV([VAT])(C)?$")
_SIMPLE_FUNCS = {"INDEX", "ZS", "LN", "ABS", "NA2Z", "Z2NA", "SETNA", "FX", "HP"}
_OUT_OF_SCOPE = {"YTD", "DYTD"}     # named but not computed → NeedPin (§4.3a)


def _classify_func(word: str):
    """Return (canonical_name, pct, log, centered) or None if not a known func."""
    w = word.upper()
    m = _RE_DIF.match(w)
    if m:
        mode, typ, cen = m.group(1), m.group(2), m.group(3)
        return ("DIF" + mode, typ == "%", typ == "L", cen == "C")
    m = _RE_YRYR.match(w)
    if m:
        typ = m.group(1)
        return ("YRYR", typ == "%", typ == "L", False)
    m = _RE_MOV.match(w)
    if m:
        return ("MOV" + m.group(1), False, False, m.group(2) == "C")
    if w in _SIMPLE_FUNCS or w in _OUT_OF_SCOPE:
        return (w, False, False, False)
    return None


# --------------------------------------------------------------------------- #
# Tokenizer
# --------------------------------------------------------------------------- #

_TOK_RE = re.compile(
    r"""
    \s+                         # whitespace (skipped)
  | (?P<NUMBER>\d+\.\d+|\.\d+|\d+)
  | (?P<WORD>[A-Za-z_][A-Za-z0-9_@.]*%?)
  | (?P<LPAREN>\()
  | (?P<RPAREN>\))
  | (?P<COMMA>,)
  | (?P<EQ>=)
  | (?P<OP>[+\-*/])
    """,
    re.VERBOSE,
)


def _tokenize(s: str):
    toks = []
    pos = 0
    for m in _TOK_RE.finditer(s):
        if m.start() != pos:
            raise ParseError(f"unexpected char at {pos} in {s!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind is not None:
            toks.append((kind, m.group()))
    if pos != len(s):
        raise ParseError(f"unexpected char at {pos} in {s!r}")
    toks.append(("EOF", ""))
    return toks


# --------------------------------------------------------------------------- #
# Recursive-descent parser (grammar §4.1)
#   expr   := term (('+'|'-') term)*
#   term   := factor (('*'|'/') factor)*
#   factor := NUMBER | WORD['(' args ')'] | '(' expr ')'
# --------------------------------------------------------------------------- #

class _Parser:
    def __init__(self, toks):
        self.toks = toks
        self.i = 0

    def _peek(self):
        return self.toks[self.i]

    def _next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def _expect(self, kind):
        t = self._next()
        if t[0] != kind:
            raise ParseError(f"expected {kind}, got {t[0]} {t[1]!r}")
        return t

    def parse(self) -> Node:
        node = self._expr()
        if self._peek()[0] != "EOF":
            raise ParseError(f"trailing tokens: {self._peek()[1]!r}")
        return node

    def _expr(self):
        node = self._term()
        while self._peek()[0] == "OP" and self._peek()[1] in "+-":
            op = self._next()[1]
            node = BinOp(op, node, self._term())
        return node

    def _term(self):
        node = self._factor()
        while self._peek()[0] == "OP" and self._peek()[1] in "*/":
            op = self._next()[1]
            node = BinOp(op, node, self._factor())
        return node

    def _factor(self):
        kind, val = self._peek()
        if kind == "OP" and val == "-":            # unary minus
            self._next()
            return BinOp("-", Num(0.0), self._factor())
        if kind == "NUMBER":
            self._next()
            return Num(float(val))
        if kind == "LPAREN":
            self._next()
            node = self._expr()
            self._expect("RPAREN")
            return node
        if kind == "WORD":
            self._next()
            if self._peek()[0] == "LPAREN":
                return self._func(val)
            return Series(val)                     # bare mnemonic
        raise ParseError(f"unexpected token {kind} {val!r}")

    def _func(self, word):
        cls = _classify_func(word)
        if cls is None:
            raise NeedPin(word, "unknown function token")
        name, pct, log, centered = cls
        if name in _OUT_OF_SCOPE:
            raise NeedPin(name, "out of scope (YTD/DYTD)")
        self._expect("LPAREN")
        if name == "INDEX":
            return self._index_args(name, pct, log, centered)
        args = []
        if self._peek()[0] != "RPAREN":
            args.append(self._expr())
            while self._peek()[0] == "COMMA":
                self._next()
                args.append(self._expr())
        self._expect("RPAREN")
        return Func(name, pct, log, centered, args)

    def _index_args(self, name, pct, log, centered):
        """INDEX(expr, BASE=VALUE) — BASE is a year or year+period label."""
        arg = self._expr()
        self._expect("COMMA")
        # base label: collect tokens until '='
        base_parts = []
        while self._peek()[0] not in ("EQ", "RPAREN", "EOF"):
            base_parts.append(self._next()[1])
        self._expect("EQ")
        value = float(self._expect("NUMBER")[1])
        self._expect("RPAREN")
        return Func(name, pct, log, centered, [arg],
                    index_base="".join(base_parts), index_value=value)


def parse(formula: str) -> Node:
    """Parse a Haver formula string into an AST (raises ParseError / NeedPin)."""
    if formula is None or not formula.strip():
        raise ParseError("empty formula")
    return _Parser(_tokenize(formula)).parse()


# --------------------------------------------------------------------------- #
# Evaluator (§4.6)
# --------------------------------------------------------------------------- #

@dataclass
class Evaluator:
    series_map: dict                # code -> SeriesData
    window: Optional[tuple] = None  # (start_ts, end_ts) display window
    interp_log: list = field(default_factory=list)
    fx_resolver: Optional[Callable[[pd.Series, str], pd.Series]] = None

    # -- helpers ----------------------------------------------------------- #
    def _series(self, code: str) -> SeriesData:
        if code not in self.series_map:
            raise TransformError(f"series {code!r} not provided in series_map")
        return self.series_map[code]

    @staticmethod
    def _dif(a: pd.Series, b: pd.Series, k: float, pct: bool, log: bool):
        """Unified DIF engine: k = {F:1, V:1/n, A:npy/n}. (§4.2)

        difference : (a-b)*k ; percent : ((a/b)**k - 1)*100 ; log : ln(a/b)*k*100
        """
        if log:
            with np.errstate(divide="ignore", invalid="ignore"):
                return np.log(a / b) * k * 100.0
        if pct:
            with np.errstate(divide="ignore", invalid="ignore"):
                return ((a / b) ** k - 1.0) * 100.0
        return (a - b) * k

    @staticmethod
    def _dif_endpoints(x: pd.Series, n: int, centered: bool):
        """Return (a, b) endpoints n apart; centered straddles t (recent NaN)."""
        if centered:
            a = x.shift(-(n // 2))
            b = x.shift(n - (n // 2))
        else:
            a = x
            b = x.shift(n)
        return a, b

    @staticmethod
    def _arg_int(node, default=None):
        if node is None:
            if default is None:
                raise ParseError("missing integer argument")
            return default
        if isinstance(node, Num):
            return int(round(node.value))
        raise ParseError("expected an integer literal argument")

    # -- main dispatch ----------------------------------------------------- #
    def ev(self, node: Node):
        if isinstance(node, Num):
            return node.value, None
        if isinstance(node, Series):
            sd = self._series(node.code)
            return sd.values.astype(float), sd.freq
        if isinstance(node, BinOp):
            return self._ev_binop(node)
        if isinstance(node, Func):
            return self._ev_func(node)
        raise TransformError(f"unknown node {node!r}")

    def _ev_binop(self, node: BinOp):
        l, fl = self.ev(node.left)
        r, fr = self.ev(node.right)
        # item 2: series <op> scalar — broadcast, no lift.
        if fl is None or fr is None:
            out = self._apply_op(node.op, l, r)
            return out, (fl if fr is None else fr)
        if fl != fr:                                  # mixed-freq SERIES
            hi = fl if FREQ_RANK[fl] >= FREQ_RANK[fr] else fr
            if FREQ_RANK[fl] < FREQ_RANK[hi]:
                l = self._lift(l, fl, hi)
            if FREQ_RANK[fr] < FREQ_RANK[hi]:
                r = self._lift(r, fr, hi)
            out = self._apply_op(node.op, l, r)
            return out, hi
        return self._apply_op(node.op, l, r), fl

    @staticmethod
    def _apply_op(op, l, r):
        if op == "+":
            return l + r
        if op == "-":
            return l - r
        if op == "*":
            return l * r
        if op == "/":
            with np.errstate(divide="ignore", invalid="ignore"):
                return l / r
        raise TransformError(f"unknown op {op!r}")

    def _ev_func(self, node: Func):
        name = node.name

        if name == "INDEX":
            return self._ev_index(node)

        # single-series functions: evaluate the first arg
        sub, f = self.ev(node.args[0])
        if not isinstance(sub, pd.Series):
            raise TransformError(f"{name} expects a series, got scalar")
        fb = FREQ_BASE[f]

        if name in ("DIFF", "DIFV", "DIFA"):
            n = self._arg_int(node.args[1] if len(node.args) > 1 else None, default=1)
            k = {"DIFF": 1.0, "DIFV": 1.0 / n, "DIFA": fb / n}[name]
            a, b = self._dif_endpoints(sub, n, node.centered)
            return self._dif(a, b, k, node.pct, node.log), f

        if name == "YRYR":
            a, b = self._dif_endpoints(sub, fb, False)
            return self._dif(a, b, 1.0, node.pct, node.log), f

        if name in ("MOVV", "MOVT", "MOVA"):
            n = self._arg_int(node.args[1] if len(node.args) > 1 else None)
            roll = sub.rolling(n, center=node.centered)
            if name == "MOVV":
                return roll.mean(), f
            tot = roll.sum()
            return (tot if name == "MOVT" else tot * (fb / n)), f

        if name == "ZS":
            return self._ev_zs(sub), f

        if name == "LN":
            with np.errstate(divide="ignore", invalid="ignore"):
                return np.log(sub), f
        if name == "ABS":
            return sub.abs(), f
        if name == "NA2Z":
            return sub.fillna(0.0), f
        if name == "Z2NA":
            return sub.replace(0.0, np.nan), f
        if name == "SETNA":
            val = self._arg_int(node.args[1]) if len(node.args) > 1 else None
            if val is None:
                raise NeedPin("SETNA", "value-to-mask not specified")
            return sub.replace(float(val), np.nan), f
        if name == "FX":
            if self.fx_resolver is None:
                raise NeedPin("FX", "no fx-rate resolver supplied")
            cur = node.args[1].code if len(node.args) > 1 and isinstance(node.args[1], Series) else ""
            return self.fx_resolver(sub, cur), f
        if name == "HP":
            return self._ev_hp(sub), f

        raise NeedPin(name)

    def _ev_zs(self, sub: pd.Series) -> pd.Series:
        """Z-score: mu/sigma over the WINDOW only; return over the FULL buffered
        range (Bug A — do NOT truncate to EDGE_TOL)."""
        if self.window is not None:
            w = sub.loc[self.window[0]:self.window[1]]
        else:
            w = sub
        mu, sigma = w.mean(), w.std()
        if not np.isfinite(sigma) or sigma == 0:
            raise TransformError("ZS: zero/undefined std over the display window")
        return (sub - mu) / sigma

    def _ev_hp(self, sub: pd.Series) -> pd.Series:
        try:
            import statsmodels.api as sm
        except Exception as exc:                       # pragma: no cover
            raise NeedPin("HP", f"statsmodels unavailable: {exc}")
        lam = {"A": 6.25, "Q": 1600, "M": 129600, "W": 129600 * 12}
        # default to the cyclical component; freq inferred by caller's series
        clean = sub.dropna()
        cyc, _trend = sm.tsa.filters.hpfilter(clean, lamb=1600)
        return cyc.reindex(sub.index)

    def _ev_index(self, node: Func) -> tuple:
        sub, f = self.ev(node.args[0])
        if not isinstance(sub, pd.Series):
            raise TransformError("INDEX expects a series")
        base, value = node.index_base, node.index_value
        base_slice = self._index_base_slice(sub, base)
        if base_slice.dropna().empty:
            # base period precedes the pulled buffer → must re-pull (Dec.5)
            raise NeedRebuffer(_first_series_code(node) or "?", base)
        return sub / base_slice.mean() * value, f

    @staticmethod
    def _index_base_slice(sub: pd.Series, base: str) -> pd.Series:
        """Select the base-period observations. YYYY -> whole year (avg);
        YYYYMM / YYYYQn -> the specific period."""
        digits = re.sub(r"\D", "", base or "")
        idx = sub.index
        if len(digits) == 4:                           # full year → average
            return sub[idx.year == int(digits)]
        if len(digits) == 6:                           # YYYYMM → that month
            y, m = int(digits[:4]), int(digits[4:6])
            return sub[(idx.year == y) & (idx.month == m)]
        if "Q" in (base or "").upper() and len(digits) == 5:   # YYYYQn
            y, q = int(digits[:4]), int(digits[4])
            return sub[(idx.year == y) & (idx.quarter == q)]
        # fallback: treat as a year
        return sub[idx.year == int(digits[:4])] if digits else sub.iloc[0:0]

    # -- frequency lift (interpolate-up; period-end anchor; no extrapolation) #
    def _lift(self, s: pd.Series, from_freq: str, to_freq: str) -> pd.Series:
        out = _lift_series(s, to_freq)
        n_filled = int(out.notna().sum() - s.reindex(out.index).notna().sum())
        self.interp_log.append(
            {"from": from_freq, "to": to_freq,
             "range": (str(out.index.min()), str(out.index.max())),
             "n_filled": n_filled}
        )
        return out


def _target_freq_alias(to_freq: str) -> str:
    """Return a usable pandas period-end alias for the installed pandas."""
    alias = _PANDAS_FREQ_END[to_freq]
    try:
        pd.date_range("2020-01-31", periods=1, freq=alias)
        return alias
    except (ValueError, KeyError):                      # pragma: no cover
        return _PANDAS_FREQ_END_LEGACY[to_freq]


def _lift_series(s: pd.Series, to_freq: str) -> pd.Series:
    """Linearly interpolate a low-freq series up to `to_freq`, anchoring each obs
    at its period-end month (QUARTER_ANCHOR) and never extrapolating past the
    series' first/last native obs (limit_area='inside')."""
    s = s.dropna()
    if s.empty:
        return s
    alias = _target_freq_alias(to_freq)
    target = pd.date_range(s.index.min(), s.index.max(), freq=alias)
    union = s.index.union(target)
    dense = s.reindex(union).interpolate(method="time", limit_area="inside")
    return dense.reindex(target)


def _walk_series_codes(node: Node):
    if isinstance(node, Series):
        yield node.code
    elif isinstance(node, Func):
        for a in node.args:
            yield from _walk_series_codes(a)
    elif isinstance(node, BinOp):
        yield from _walk_series_codes(node.left)
        yield from _walk_series_codes(node.right)


def _first_series_code(node: Node):
    for c in _walk_series_codes(node):
        return c
    return None


def formula_mnemonics(formula: str) -> list[str]:
    """Every series mnemonic in a formula, in order, de-duped (G8b resolution walks
    these — a sum like BEEM1+BEEM2+BEEM3 must confirm ALL addends, not just the
    first). Raises ParseError/NeedPin on a malformed/unsupported formula."""
    seen, out = set(), []
    for c in _walk_series_codes(parse(formula)):
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


# --------------------------------------------------------------------------- #
# finish() + edge validators (§4.6 / §8.2 / §8.10)
# --------------------------------------------------------------------------- #

@dataclass
class EdgeCheck:
    ok: bool
    detail: str


def finish(node: Node, lag: int, window: tuple, series_map: dict,
           common_freq: str, evaluator: Optional[Evaluator] = None) -> pd.Series:
    """Evaluate `node`, apply the native-freq bracket lag, interpolate up to
    `common_freq` (after transform+lag), and slice to the window (§4.6).

    `lag` is a SIGNED native-period shift (see build_chart._shift_periods, which
    translates the bracket tag): +n shifts FORWARD n periods — a `[-n]` LAG, the
    value at t comes from t-n (Q6); -n shifts BACKWARD — a `[+n]` LEAD. It is
    applied AFTER the formula evaluates, so a `zs(...)[-4]` z-scores over the
    series' own history and only THEN slides, never z-scoring a shifted window.
    Returns the final plotted (common-grid) series.
    """
    ev = evaluator or Evaluator(series_map, window)
    val, f = ev.ev(node)
    if not isinstance(val, pd.Series):
        raise TransformError("formula reduces to a scalar; nothing to plot")
    s = val
    if lag:
        # Shift the INDEX, not the values inside a fixed index. A plain `shift(n)`
        # keeps the index and therefore DROPS the last n observations off the end —
        # on a lagged leading indicator those are the newest readings, i.e. exactly
        # what the chart is for (2026-08-03: `zs(fwill)[-4]` lost 2025Q4–2026Q3 and
        # the line stopped a year early). Moving the index instead preserves every
        # observation and carries the series forward to where the lag puts it.
        if f is None:
            raise TransformError(
                f"cannot apply a {lag}-period shift: the expression has no native "
                f"frequency (a lag is defined in native periods)")
        s = s.shift(1, freq=shift_offset(f, int(lag)))          # native freq (§4.4)
    if f is not None and FREQ_RANK[f] < FREQ_RANK[common_freq]:
        s = _lift_series(s, common_freq)                       # AFTER transform+lag
    if window is not None:
        s = s.loc[window[0]:window[1]]
    return s


def effective_inception(node: Node, series_map: dict) -> Optional[pd.Timestamp]:
    """Bug B: a combined expression starts at the LATEST-starting operand."""
    incs = [series_map[c].inception for c in _walk_series_codes(node)
            if c in series_map and series_map[c].inception is not None]
    return max(incs) if incs else None


def check_first_valid(s: pd.Series, node: Node, window: tuple, series_map: dict,
                      common_freq: str) -> EdgeCheck:
    """§8.2 first-valid tolerance band, anchored on eff_inception (Bug B)."""
    fv = s.first_valid_index()
    if fv is None:
        return EdgeCheck(False, "series is entirely NaN over the window")
    tol = pd.DateOffset(**_offset_kwargs(common_freq, EDGE_TOL[common_freq]))
    eff = effective_inception(node, series_map)
    anchor = window[0]
    if eff is not None and eff > anchor:
        anchor = eff
    lo, hi = anchor - tol, anchor + tol
    ok = lo <= pd.Timestamp(fv) <= hi
    return EdgeCheck(ok, f"first_valid={pd.Timestamp(fv).date()} "
                         f"anchor={pd.Timestamp(anchor).date()} band=±{EDGE_TOL[common_freq]}")


def check_last_value(s: pd.Series, window: tuple, common_freq: str,
                     screenshot_flag: Optional[float] = None,
                     value_tol: float = 0.05) -> EdgeCheck:
    """§8.10 last-valid within EDGE_TOL of visual_end; optional value match."""
    lv = s.last_valid_index()
    if lv is None:
        return EdgeCheck(False, "series is entirely NaN over the window")
    tol = pd.DateOffset(**_offset_kwargs(common_freq, EDGE_TOL[common_freq]))
    if not (window[1] - tol <= pd.Timestamp(lv) <= window[1] + tol):
        return EdgeCheck(False, f"last_valid={pd.Timestamp(lv).date()} "
                                f"outside ±{EDGE_TOL[common_freq]} of {window[1].date()}")
    if screenshot_flag is not None:
        val = float(s.loc[lv])
        denom = abs(screenshot_flag) if screenshot_flag else 1.0
        if abs(val - screenshot_flag) / denom > value_tol:
            return EdgeCheck(False, f"last value {val:.4g} != flag "
                                    f"{screenshot_flag:.4g} (>{value_tol:.0%})")
    return EdgeCheck(True, f"last_valid={pd.Timestamp(lv).date()}")


def _offset_kwargs(freq: str, n: int) -> dict:
    return {"M": {"months": n}, "Q": {"months": 3 * n},
            "W": {"weeks": n}, "A": {"years": n}}[freq]


def shift_offset(freq: str, n: int):
    """`n` native periods as a pandas offset that PRESERVES a period-end stamp.

    Anchored offsets are used deliberately instead of `DateOffset(months=…)`, which
    walks the day-of-month and silently drifts off the period end: Sep-30 + 3 months
    is Dec-**30**, not Dec-31, so a quarterly stamp would land one day shy of the
    anchor and no longer align with the rest of the grid. `QuarterEnd`/`MonthEnd`/
    `YearEnd` snap to the true period end. Weekly uses an exact 7-day multiple so the
    index's own weekday anchor (W-SAT vs W-SUN) is kept.
    """
    if freq in ("W", "D"):
        return pd.Timedelta(weeks=n) if freq == "W" else pd.Timedelta(days=n)
    try:
        return {"Q": pd.offsets.QuarterEnd, "M": pd.offsets.MonthEnd,
                "A": pd.offsets.YearEnd}[freq](n)
    except KeyError:
        raise TransformError(f"no shift offset defined for frequency {freq!r}")


# --------------------------------------------------------------------------- #
# Convenience
# --------------------------------------------------------------------------- #

def common_frequency(series_map: dict) -> str:
    """Highest frequency among the provided series (§4.6 common_freq)."""
    if not series_map:
        raise TransformError("no series provided")
    return max((sd.freq for sd in series_map.values()), key=lambda f: FREQ_RANK[f])


def transform(formula: str, series_map: dict, window: tuple, common_freq: str,
              lag: int = 0) -> pd.Series:
    """Parse + evaluate + finish a single series' formula → final plotted series."""
    node = parse(formula)
    return finish(node, lag, window, series_map, common_freq)
