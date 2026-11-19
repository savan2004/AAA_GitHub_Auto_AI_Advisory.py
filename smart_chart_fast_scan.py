"""Bounded real-price scan for the MSK automatic Smart Chart.

A provider failure must not become a fabricated ticker, quote, or trading signal.
The retry path only inspects the original deterministic sample; it never claims
to rank unscanned Nifty constituents.
"""
import math
import random
import time

import pandas as pd


class ChartScanUnavailable(RuntimeError):
    pass


_REQUIRED = ("Open", "High", "Low", "Close", "Volume")


def _ticker_frame(prices, symbol, ticker_count):
    """Handle both yfinance MultiIndex orientations and single-ticker frames."""
    if prices is None or getattr(prices, "empty", True):
        return None
    columns = prices.columns
    if getattr(columns, "nlevels", 1) >= 2:
        for level in (0, 1):
            if symbol in columns.get_level_values(level):
                return prices.xs(symbol, axis=1, level=level)
        return None
    return prices if ticker_count == 1 else None


def _completed_frame(frame, session_key):
    """Accept only real, finite, internally coherent completed-session bars."""
    if frame is None or getattr(frame, "empty", True):
        return None
    if not all(column in frame.columns for column in _REQUIRED):
        return None
    try:
        result = frame.loc[:, list(_REQUIRED)].copy()
        index = pd.DatetimeIndex(pd.to_datetime(result.index, errors="coerce"))
        if index.tz is not None:
            index = index.tz_convert("Asia/Kolkata").tz_localize(None)
        result.index = index.normalize()
        result = result.loc[~result.index.isna()]
        result = result.sort_index()
        result = result.loc[~result.index.duplicated(keep="last")]
        # Never incorporate a future, still-open or misdated bar into indicators.
        result = result.loc[result.index <= pd.Timestamp(session_key)]
        for column in _REQUIRED:
            result[column] = pd.to_numeric(result[column], errors="coerce")
        result = result.replace([float("inf"), float("-inf")], float("nan")).dropna()
        result = result.loc[
            (result["Low"] > 0) & (result["High"] >= result["Low"]) &
            (result["Low"] <= result["Open"]) &
            (result["Open"] <= result["High"]) &
            (result["Low"] <= result["Close"]) &
            (result["Close"] <= result["High"]) &
            (result["Volume"] >= 0)
        ]
        if len(result) < 55 or str(result.index[-1].date()) != str(session_key):
            return None
        return result
    except (ValueError, TypeError, OverflowError, AttributeError, KeyError):
        return None


def choose_auto_chart(universe, *, download, scorer, session_key,
                      sample_limit=60, fallback_history=None, min_score=None,
                      require_confirmed_setup=False, scan_all=False):
    """Return (winning real candidate, sampled, verified, total universe).

    The full-sample batch is attempted once. Only when it yields zero verified
    candidates is a single small non-threaded retry allowed, followed by at
    most two official NSE history requests if provided by the caller.

    There is no stale EOD fallback, no cross-symbol substitutions, and no
    full-universe claim. All paths use the same timestamp/price/score gates.
    """
    distinct = {}
    for symbol, name in universe:
        if isinstance(symbol, str) and symbol.endswith(".NS") and symbol not in distinct:
            distinct[symbol] = name
    whole = len(distinct)
    if whole == 0:
        raise ChartScanUnavailable("No current NSE chart universe")
    if not isinstance(sample_limit, int) or not 1 <= sample_limit <= 120:
        raise ValueError("Sample size must be between 1 and 120")

    selected = (list(distinct.items()) if scan_all else
                random.Random(str(session_key)).sample(
                    list(distinct.items()), min(whole, sample_limit)))
    names = dict(selected)
    symbols = list(names)
    candidates = {}
    diagnostics = {"rejected": 0, "scorer": 0, "download_failures": 0}

    def consider(symbol, frame):
        if symbol in candidates:
            return
        complete = _completed_frame(frame, session_key)
        if complete is None:
            diagnostics["rejected"] += 1
            return
        try:
            candidate = scorer(symbol, names[symbol], df=complete.copy())
            if not isinstance(candidate, dict) or candidate.get("sym") != symbol:
                diagnostics["scorer"] += 1
                return
            close = float(candidate.get("close", math.nan))
            score = float(candidate.get("score", math.nan))
            if not (math.isfinite(close) and close > 0 and
                    math.isfinite(score) and
                    math.isclose(close, float(complete["Close"].iloc[-1]),
                                 rel_tol=0.00001)):
                diagnostics["scorer"] += 1
                return
            if min_score is not None and score < min_score:
                diagnostics["scorer"] += 1
                return
            if require_confirmed_setup and not candidate.get("setup_confirmed"):
                diagnostics["scorer"] += 1
                return
            candidate["_daily_history"] = complete
            candidates[symbol] = candidate
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            diagnostics["scorer"] += 1

    def bulk(tickers, *, threads, timeout):
        try:
            prices = download(
                tickers=tickers, period="1y", interval="1d", progress=False,
                auto_adjust=True, group_by="ticker", threads=threads,
                timeout=timeout,
            )
        except Exception:
            diagnostics["download_failures"] += 1
            return
        for sym in tickers:
            try:
                consider(sym, _ticker_frame(prices, sym, len(tickers)))
            except (KeyError, TypeError, ValueError, AttributeError):
                diagnostics["rejected"] += 1

    # Bounded full-universe scan. Small batches avoid 500-ticker Yahoo timeouts
    # and keep failures isolated. Stop after 55s; never claim full coverage.
    if scan_all:
        started = time.monotonic()
        for start in range(0, len(symbols), 40):
            if time.monotonic() - started > 55:
                break
            bulk(symbols[start:start + 40], threads=True, timeout=9)
    else:
        bulk(symbols, threads=True, timeout=12)
    if not candidates:
        # Yahoo can rate-limit a 60-name request while serving a 10-name
        # serial batch. Retry once, without concurrent ticker workers.
        bulk(symbols[:min(10, len(symbols))], threads=False, timeout=9)

    if not candidates and fallback_history is not None:
        # A narrowly bounded official NSE historical source is independent
        # of Yahoo's batch endpoint. No more than two calls (<=15s each).
        preferred = ("RELIANCE.NS", "SBIN.NS", "TCS.NS", "HDFCBANK.NS",
                     "ICICIBANK.NS", "INFY.NS", "ITC.NS", "LT.NS")
        fallback_symbols = sorted(
            symbols, key=lambda sym: (sym not in preferred, symbols.index(sym)))
        for sym in fallback_symbols[:2]:
            try:
                consider(sym, fallback_history(sym))
            except Exception:
                diagnostics["download_failures"] += 1

    if not candidates:
        raise ChartScanUnavailable(
            ("No verified 12+/20 breakout or trend-confirmation setup " if min_score is not None
             else "No valid real price bars in bounded chart sample ")
            f"(session={session_key}; sampled={len(selected)}; "
            f"rejected={diagnostics['rejected']}; "
            f"scorer_rejected={diagnostics['scorer']}; "
            f"provider_errors={diagnostics['download_failures']})."
        )

    # Sort by the FINAL 11-check score, not by preliminary EMA crossover.
    winner = max(candidates.values(), key=lambda c: float(c["score"]))
    return winner, (len(selected) if not scan_all else len(symbols)), len(candidates), whole
