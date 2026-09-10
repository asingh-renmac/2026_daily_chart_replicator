"""
X-13ARIMA-SEATS SEASONAL ADJUSTMENT — REFERENCE TEMPLATE
========================================================

Production-grade X-13 seasonal adjustment via statsmodels, with all the
Windows-specific workarounds we've accumulated.

GOTCHAS THIS FILE ENCODES
-------------------------
1. THE BINARY NAME MISMATCH (Census ships an HTML build; statsmodels wants x13as.exe)
   The Census Bureau distributes X-13 as an HTML-output variant, the default
   since ~2024, named `x13ashtml.exe` in older builds and `x13as_html.exe`
   (underscore) in winx13html_v3-3. statsmodels' `_find_x12()` only
   recognizes `x13as.exe` or `x12a.exe` and otherwise silently falls back
   to classical decomposition. setup_x13() handles this by copying whichever
   spelling it finds -> x13as.exe on first run. This is the silent-wrong-answer
   failure mode that triggered this file's most recent update.

2. THE .err.html BUG (statsmodels#8392)
   statsmodels tries to read a temp .err.html file that X-13 only creates
   when there's an actual ERROR. Successful runs don't create it, but
   statsmodels crashes trying to open it:
       [Errno 2] No such file or directory: '...tmp...err.html'
   Fix: monkey-patch _open_and_read with a safe version that returns ""
   on FileNotFoundError. Always restore the original in a finally block.

3. TMP/TEMP ENVIRONMENT VARIABLES
   X-13 writes scratch files during execution. If TMP points to a
   restricted directory, X-13 fails with permission errors. Point both
   TMP and TEMP at a dedicated writable working directory before running,
   restore after.

4. TRADING-DAY REGRESSION FAILURES
   Some series (survey indices, financial prices, rates) are NOT affected
   by business-day count. X-13's trading-day regression then fails or
   returns nonsense. Two-pass approach: try with trading=True first, retry
   with trading=False on failure.

5. MIN OBS
   X-13 needs ≥36 monthly obs (3 years) or ≥24 quarterly obs (6 years) to
   estimate. Shorter series fall through to classical decomposition.

6. CLASSICAL DECOMPOSITION FALLBACK
   When X-13 fails entirely, fall back to statsmodels.seasonal_decompose.
   Quality is worse but never crashes. Log the fallback so you can
   investigate after.

7. LEVELS vs GROWTH RATES
   Apply SA to LEVELS, then compute growth rates AFTER. Do not SA growth
   rates directly — the seasonality is in the level pattern, and SA-ing
   growth rates introduces an artifact at the year boundary.

8. LOG TRANSFORM (multiplicative model)
   Use log=True for series that grow over time at proportional rates
   (nominal levels — payrolls, retail sales $, GDP $). Use log=False
   (additive) for rates, indices, and growth-rate inputs.

USAGE EXAMPLES
--------------
    from x13_seasonal_adjust import setup_x13, seasonal_adjust

    # ALWAYS call setup_x13() once at script start — it auto-resolves
    # the Census-x13ashtml.exe vs statsmodels-x13as.exe mismatch and
    # verifies discoverability. Without it, SA silently falls back
    # to classical decomposition.
    setup_x13()

    # Monthly nominal retail sales (level that grows over time)
    sa = seasonal_adjust(retail_nsa, freq="M", trading=True, log=True)

    # Monthly survey index (NSA→SA, no trading day, additive)
    sa = seasonal_adjust(ism_nsa, freq="M", trading=False, log=False)

    # Quarterly real GDP
    sa = seasonal_adjust(gdp_nsa, freq="Q", trading=True, log=True)

CHANGELOG / accumulated knowledge:
- 2026-02 (Legion Pro 7 migration): X-13 binary moved from old Downloads
  location to C:\\Users\\asingh\\tools\\winx13\\x13as. Census Bureau also
  switched to shipping x13ashtml.exe as default. Added setup_x13()
  pre-flight that handles both — the path resolution and the binary rename.
  Failure mode pre-fix: 9 SA-flagged series silently fell back to classical
  decomposition because statsmodels couldn't find the renamed binary.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# =============================================================================
# CONFIGURATION
# =============================================================================

# Default X-13 install directory. Override via X13PATH env var or by passing
# x13_dir to setup_x13() explicitly.
DEFAULT_X13_DIR = Path(r"C:\Users\asingh\tools\winx13\x13as")

# Binary name conventions:
#   - Modern Census Bureau install: the HTML-output build, default since ~2024
#   - statsmodels expects:          x13as.exe    (or x12a.exe legacy)
# setup_x13() copies whichever shipped binary it finds -> x13as.exe.
#
# The shipped name is NOT stable across releases: winx13html_v3-3.zip (July 2025,
# X-13ARIMA-SEATS v1.1 build 62) ships `x13as_html.exe` WITH an underscore, while
# earlier HTML builds shipped `x13ashtml.exe` without one. Matching only one spelling
# turns a fresh unzip into a FileNotFoundError on a machine where nobody has hand-copied
# the binary yet -- which is precisely the setup this function exists to make reliable.
SHIPPED_BINARIES = ("x13ashtml.exe", "x13as_html.exe")
EXPECTED_BINARY = "x13as.exe"

# Dedicated working directory for X-13 scratch files. MUST be writable.
X13_WORK_DIR = str(Path.home() / "x13_temp")
os.makedirs(X13_WORK_DIR, exist_ok=True)

# Resolved at setup_x13() time. Used by seasonal_adjust() if not overridden.
_RESOLVED_X13_DIR: Optional[Path] = None
_RESOLVED_X13_PATH: Optional[str] = None  # path to the x13as.exe binary

# Min observations needed for X-13 to estimate properly.
MIN_OBS_MONTHLY = 36   # 3 years
MIN_OBS_QUARTERLY = 24  # 6 years

logger = logging.getLogger(__name__)


# =============================================================================
# PRE-FLIGHT SETUP (call once per script before any seasonal_adjust() call)
# =============================================================================

def setup_x13(
    x13_dir: Optional[Path | str] = None,
    verify: bool = True,
    quiet: bool = False,
) -> Path:
    """
    Resolve X-13 install location and ensure statsmodels can find the binary.

    Call this ONCE at the start of any script that does SA. Without it,
    statsmodels silently falls back to classical decomposition when it can't
    locate the X-13 binary — a silent-wrong-answer failure mode.

    Side effects:
    - Locates X-13 install (x13_dir arg > X13PATH env var > DEFAULT_X13_DIR)
    - If x13ashtml.exe exists but x13as.exe doesn't, copies the former
      to the latter so statsmodels' `_find_x12()` succeeds.
    - Sets X13PATH env var so statsmodels picks up the directory.
    - Stores the resolved path so seasonal_adjust() uses it by default.

    Args:
        x13_dir: Optional override for X-13 install directory. If None,
                 reads X13PATH env var, then falls back to DEFAULT_X13_DIR.
        verify: If True, run a discovery check after setup and raise if
                X-13 still can't be located. Set False to skip (e.g.,
                test environments without X-13 installed).
        quiet: If True, suppress informational prints.

    Returns:
        Path to the resolved X-13 install directory.

    Raises:
        FileNotFoundError: if X-13 install or its binary can't be found.
        RuntimeError: if verify=True and statsmodels still can't locate
                       the binary after setup.
    """
    global _RESOLVED_X13_DIR, _RESOLVED_X13_PATH

    # 1. Resolve install location
    if x13_dir is None:
        env_path = os.environ.get("X13PATH")
        x13_dir = Path(env_path) if env_path else DEFAULT_X13_DIR
    x13_dir = Path(x13_dir).resolve()

    if not x13_dir.is_dir():
        raise FileNotFoundError(
            f"X-13 install directory not found: {x13_dir}\n"
            f"Set X13PATH env var, pass x13_dir explicitly to setup_x13(), "
            f"or update DEFAULT_X13_DIR in this file."
        )

    # 2. Ensure x13as.exe exists. If a Census-shipped HTML-output binary is what's
    # installed, copy it to the statsmodels-expected name.
    expected_path = x13_dir / EXPECTED_BINARY
    shipped_path = next((x13_dir / n for n in SHIPPED_BINARIES
                         if (x13_dir / n).exists()), None)

    if not expected_path.exists():
        if shipped_path is None:
            raise FileNotFoundError(
                f"None of {EXPECTED_BINARY} or {', '.join(SHIPPED_BINARIES)} found in "
                f"{x13_dir}. Verify the X-13 install — expected at least "
                f"one binary in the directory."
            )
        # Copy the shipped binary -> x13as.exe so statsmodels finds it.
        # copy2 preserves metadata; statsmodels just needs the name to match.
        shutil.copy2(shipped_path, expected_path)
        if not quiet:
            print(f"setup_x13: copied {shipped_path.name} -> {EXPECTED_BINARY} "
                  f"in {x13_dir}")

    # 3. Set X13PATH so statsmodels' _find_x12() picks up the directory.
    os.environ["X13PATH"] = str(x13_dir)

    # 4. Cache resolved paths for seasonal_adjust() to use as defaults.
    _RESOLVED_X13_DIR = x13_dir
    _RESOLVED_X13_PATH = str(expected_path)

    # 5. Verify discoverability if requested.
    if verify:
        _verify_x13_discoverable()

    if not quiet:
        print(f"setup_x13: X-13 discoverable at {expected_path}")

    return x13_dir


def _verify_x13_discoverable() -> None:
    """Call statsmodels' internal locator to confirm X-13 is findable."""
    try:
        from statsmodels.tsa.x13 import _find_x12
    except ImportError:
        # Older statsmodels versions may have moved this; treat as warning,
        # not failure — seasonal_adjust() will catch it downstream.
        logger.warning("Couldn't import _find_x12; skipping verification.")
        return

    binary = _find_x12()
    if binary is None:
        raise RuntimeError(
            "X-13 setup completed but statsmodels still can't locate the "
            f"binary. X13PATH = {os.environ.get('X13PATH', '(unset)')}\n"
            f"Verify x13as.exe exists in that directory."
        )


# =============================================================================
# THE MONKEY-PATCH (statsmodels#8392)
# =============================================================================

def _open_and_read_safe(fname: str) -> str:
    """
    Drop-in replacement for statsmodels.tsa.x13._open_and_read.
    Returns empty string when the .err file doesn't exist (the SUCCESS case
    on Windows), instead of raising FileNotFoundError.
    """
    try:
        with open(fname, "r", encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return ""
    except Exception:  # pragma: no cover
        return ""


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def seasonal_adjust(
    series: pd.Series,
    freq: str = "M",
    trading: bool = True,
    log: bool = True,
    outlier: bool = True,
    x13_path: Optional[str] = None,
    work_dir: str = X13_WORK_DIR,
) -> pd.Series:
    """
    Seasonally adjust a single series using X-13ARIMA-SEATS.

    NOTE: Call setup_x13() once at the start of your script before invoking
    this function. Without setup_x13(), the binary may not be discoverable
    and statsmodels will silently fall back to classical decomposition.

    Falls back automatically:
      Attempt 1: X-13 with trading-day regression
      Attempt 2: X-13 without trading-day regression (if Attempt 1 failed)
      Attempt 3: Classical seasonal_decompose (if X-13 failed entirely)

    Parameters
    ----------
    series : pd.Series
        NSA series with a DatetimeIndex at monthly or quarterly frequency.
    freq : 'M' or 'Q'
        Frequency. Determines period (12 vs 4) for both X-13 and fallback.
    trading : bool
        Include trading-day regression. Set False for survey indices,
        financial prices, rates — anything not affected by business-day count.
    log : bool
        Use multiplicative model (log transform). True for levels that grow
        over time (nominal $, employment). False for rates, indices, growth
        rates already differenced.
    outlier : bool
        Detect AO/LS/TC outliers automatically. Default True.
    x13_path : str, optional
        Path to x13as.exe. If None, uses the path resolved by setup_x13().
        If setup_x13() hasn't been called, raises RuntimeError.
    work_dir : str
        Scratch directory for X-13 temp files.

    Returns
    -------
    pd.Series
        Seasonally adjusted series, same index as input.

    Notes
    -----
    DO NOT apply SA to growth-rate series. Apply to LEVELS, then differentiate.
    """
    import statsmodels.api as sm
    import statsmodels.tsa.x13 as x13_module

    # Resolve x13_path from setup_x13() cache if not explicitly provided.
    if x13_path is None:
        if _RESOLVED_X13_PATH is None:
            raise RuntimeError(
                "seasonal_adjust() called without setup_x13() having been "
                "called first. Either call setup_x13() at script start, "
                "or pass x13_path= explicitly."
            )
        x13_path = _RESOLVED_X13_PATH

    series_clean = series.dropna()
    min_obs = MIN_OBS_MONTHLY if freq == "M" else MIN_OBS_QUARTERLY
    if len(series_clean) < min_obs:
        logger.warning(
            f"'{series.name}' has only {len(series_clean)} obs (< {min_obs}). "
            f"Falling back to classical decomposition."
        )
        return _seasonal_adjust_fallback(series_clean, freq, log)

    # --- Environment and monkey-patch setup ---
    original_tmp = os.environ.get("TMP")
    original_temp = os.environ.get("TEMP")
    original_dir = os.getcwd()
    original_open_and_read = x13_module._open_and_read

    os.environ["TMP"] = work_dir
    os.environ["TEMP"] = work_dir
    os.environ["X13PATH"] = os.path.dirname(x13_path)
    os.chdir(work_dir)
    x13_module._open_and_read = _open_and_read_safe

    try:
        # statsmodels wants a fresh Series with a recognised name
        temp_series = pd.Series(
            series_clean.values, index=series_clean.index, name="value"
        )

        # --- Attempt 1: with trading-day regression ---
        try:
            result = sm.tsa.x13_arima_analysis(
                endog=temp_series,
                x12path=x13_path,
                outlier=outlier,
                trading=trading,
                log=log,
                forecast_periods=0,
                print_stdout=False,
                freq=freq,
            )
            sa = pd.Series(
                result.seasadj.values.flatten(),
                index=series_clean.index,
                name=series.name,
            )
            logger.info(
                f"X-13 SA succeeded for '{series.name}' (trading={trading})."
            )
            return sa
        except Exception as e1:
            if trading:
                logger.warning(
                    f"X-13 failed for '{series.name}' with trading=True ({e1}). "
                    f"Retrying without trading day."
                )
                # --- Attempt 2: without trading-day regression ---
                try:
                    result = sm.tsa.x13_arima_analysis(
                        endog=temp_series,
                        x12path=x13_path,
                        outlier=outlier,
                        trading=False,
                        log=log,
                        forecast_periods=0,
                        print_stdout=False,
                        freq=freq,
                    )
                    sa = pd.Series(
                        result.seasadj.values.flatten(),
                        index=series_clean.index,
                        name=series.name,
                    )
                    logger.info(
                        f"X-13 SA succeeded for '{series.name}' (trading=False)."
                    )
                    return sa
                except Exception as e2:
                    logger.warning(
                        f"X-13 failed entirely for '{series.name}': {e2}. "
                        f"Falling back to classical decomposition."
                    )
            else:
                logger.warning(
                    f"X-13 failed for '{series.name}': {e1}. "
                    f"Falling back to classical decomposition."
                )
    finally:
        # Restore environment in all cases
        x13_module._open_and_read = original_open_and_read
        os.chdir(original_dir)
        if original_tmp is not None:
            os.environ["TMP"] = original_tmp
        elif "TMP" in os.environ:
            del os.environ["TMP"]
        if original_temp is not None:
            os.environ["TEMP"] = original_temp
        elif "TEMP" in os.environ:
            del os.environ["TEMP"]

    # --- Attempt 3: classical fallback ---
    return _seasonal_adjust_fallback(series_clean, freq, log)


# =============================================================================
# CLASSICAL DECOMPOSITION FALLBACK
# =============================================================================

def _seasonal_adjust_fallback(
    series: pd.Series, freq: str = "M", log: bool = False
) -> pd.Series:
    """
    Classical seasonal_decompose fallback. Lower quality than X-13 (no
    outlier handling, no trading-day adjustment) but never crashes.
    """
    from statsmodels.tsa.seasonal import seasonal_decompose

    period = 4 if freq == "Q" else 12
    model = "multiplicative" if log else "additive"

    try:
        decomposition = seasonal_decompose(
            series, model=model, period=period, extrapolate_trend="freq"
        )
        if log:
            # Multiplicative: SA = original / seasonal
            adjusted = series / decomposition.seasonal
        else:
            # Additive: SA = original - seasonal
            adjusted = series - decomposition.seasonal
        adjusted.name = series.name
        logger.info(
            f"Classical {model} decomposition applied to '{series.name}'."
        )
        return adjusted.dropna()
    except Exception as e:
        logger.error(
            f"Classical decomposition also failed for '{series.name}': {e}. "
            f"Returning original series UNADJUSTED."
        )
        return series


# =============================================================================
# BATCH HELPER
# =============================================================================

def seasonal_adjust_batch(
    df: pd.DataFrame,
    freq: str = "M",
    trading: bool = True,
    log: bool = True,
    keep_failures: bool = True,
) -> pd.DataFrame:
    """
    SA every column of a wide DataFrame. Returns SA DataFrame with same
    columns. Logs per-column outcome. If `keep_failures` is False, columns
    that completely failed (returned identical to input) are dropped.

    NOTE: Call setup_x13() once before invoking this function.
    """
    out = {}
    for col in df.columns:
        sa = seasonal_adjust(df[col], freq=freq, trading=trading, log=log)
        out[col] = sa
    sa_df = pd.DataFrame(out)
    return sa_df


# =============================================================================
# QUICK DIAGNOSTICS
# =============================================================================

def quick_seasonality_test(series: pd.Series, freq: str = "M") -> dict:
    """
    Rough check for whether a series even has seasonality worth adjusting.
    Returns a small dict of diagnostics. Use as a sanity check before SA.
    """
    period = 4 if freq == "Q" else 12
    series_clean = series.dropna()
    if len(series_clean) < 2 * period:
        return {"insufficient_obs": True}

    # Compare same-month-across-years standard deviation
    by_period = series_clean.groupby(series_clean.index.month if freq == "M"
                                     else series_clean.index.quarter)
    period_means = by_period.mean()
    period_stds = by_period.std()
    overall_std = series_clean.std()

    return {
        "n_obs": len(series_clean),
        "period_to_overall_std_ratio": float(period_stds.mean() / overall_std),
        "max_period_mean_spread": float(period_means.max() - period_means.min()),
        "needs_sa_heuristic": float(period_stds.mean() / overall_std) > 0.3,
    }


# =============================================================================
# SMOKE TEST / DEMO
# =============================================================================

if __name__ == "__main__":
    # Always start with setup_x13() — this validates the install and handles
    # the Census-x13ashtml.exe vs statsmodels-x13as.exe binary mismatch.
    print("Setting up X-13...")
    setup_x13()
    print()

    # Demo with synthetic seasonal data
    rng = np.random.default_rng(42)
    dates = pd.date_range("2010-01-01", periods=180, freq="MS")
    trend = np.linspace(100, 200, 180)
    seasonal = 10 * np.sin(2 * np.pi * np.arange(180) / 12)
    noise = rng.normal(0, 2, 180)
    nsa = pd.Series(trend + seasonal + noise, index=dates, name="demo_series")

    sa = seasonal_adjust(nsa, freq="M", trading=False, log=False)
    print(f"NSA last 6: {nsa.tail(6).values.round(2)}")
    print(f"SA  last 6: {sa.tail(6).values.round(2)}")
    print(f"Seasonality test: {quick_seasonality_test(nsa, freq='M')}")