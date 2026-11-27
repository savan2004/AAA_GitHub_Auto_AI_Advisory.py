"""
market_data.py — Bulk OHLCV downloader (shared by Stocks Buzz + Research Report)
────────────────────────────────────────────────────────────────────────────────
Why this exists
  data_engine.get_hist() is built for ONE symbol at a time and is deliberately
  throttled (8 Yahoo calls / minute) to avoid bans. That is perfect for a single
  stock lookup but makes scanning 1000 stocks impossible (~2 hours).

  yfinance's multi-ticker download() pulls many symbols per call, so a full
  1000-stock scan finishes in a couple of minutes instead.

Public API
  to_yahoo(sym)                           -> "RELIANCE" -> "RELIANCE.NS"
  bulk_history(symbols, period, ...)      -> {SYM: DataFrame[Open,High,Low,Close,Volume]}
  index_history(ticker="^NSEI", period)   -> DataFrame | None
"""

import time
import logging
import threading
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_OHLCV = ["Open", "High", "Low", "Close", "Volume"]

_cache: Dict[str, dict] = {}
_cache_lock = threading.Lock()
_download_lock = threading.Lock()


def to_yahoo(sym: str) -> str:
    s = str(sym).strip().upper()
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return f"{s}.NS"


def from_yahoo(ticker: str) -> str:
    t = str(ticker).strip().upper()
    for suf in (".NS", ".BO"):
        if t.endswith(suf):
            return t[: -len(suf)]
    return t


from msk_history_quality import clean_daily_history as _clean_frame


def _split_multi(raw: pd.DataFrame, tickers: List[str]) -> Dict[str, pd.DataFrame]:
    """Turn yfinance's wide multi-ticker frame into {ticker: OHLCV frame}.
    Handles both (ticker, field) and (field, ticker) column orders, plus the
    single-ticker flat-column case, because yfinance has changed this between
    versions."""
    out: Dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out

    if isinstance(raw.columns, pd.MultiIndex):
        lvl0 = set(map(str, raw.columns.get_level_values(0)))
        lvl1 = set(map(str, raw.columns.get_level_values(1)))
        tick_set = set(tickers)
        if tick_set & lvl0:          # (ticker, field)
            for t in tickers:
                if t in lvl0:
                    try:
                        f = _clean_frame(raw[t])
                    except Exception:
                        f = None
                    if f is not None:
                        out[t] = f
        elif tick_set & lvl1:        # (field, ticker)
            for t in tickers:
                if t in lvl1:
                    try:
                        f = _clean_frame(raw.xs(t, axis=1, level=1))
                    except Exception:
                        f = None
                    if f is not None:
                        out[t] = f
    else:
        # single ticker, flat columns
        if len(tickers) == 1:
            f = _clean_frame(raw)
            if f is not None:
                out[tickers[0]] = f
    return out


def _download_chunk(tickers: List[str], period: str, retries: int = 2, adjusted: bool = False, timeout: int = 30) -> Dict[str, pd.DataFrame]:
    import yfinance as yf
    result = {}
    pending = list(dict.fromkeys(tickers))
    last_err = None
    for attempt in range(retries + 1):
        try:
            # yfinance shares download/cache state. Serialize separate callers,
            # but retain its internal parallelism on the initial bulk request.
            with _download_lock:
                raw = yf.download(
                    tickers=pending if len(pending) > 1 else pending[0],
                    period=period, interval="1d",
                    group_by="ticker", auto_adjust=adjusted,
                    progress=False, threads=(attempt == 0 and len(pending) > 1), timeout=timeout,
                )
            result.update(_split_multi(raw, pending))
            pending = [t for t in pending if t not in result]
            if not pending:
                return result
        except Exception as e:
            last_err = e
            logger.warning(f"[market_data] chunk attempt {attempt + 1} failed: {str(e)[:120]}")
        if attempt < retries:
            time.sleep(2.0 * (attempt + 1))
    if last_err:
        logger.warning(f"[market_data] chunk incomplete: {str(last_err)[:120]}")
    return result



def _recover_missing_symbol(symbol: str, period: str) -> Optional[pd.DataFrame]:
    """Bounded alternate-provider attempt for symbols missed by Yahoo bulk API.

    NSE is used only for eligible NSE equities; index tickers never get
    incorrectly suffixed or treated as individual equities. No synthetic bars.
    """
    # The overnight EOD worker deliberately has no Telegram credential.
    # Legacy data_engine imports bot configuration, so an unavailable
    # optional recovery provider must not abort the entire index scan.
    try:
        from data_engine import _nse_hist, _stooq_hist, _yahoo_v8_hist
    except (ImportError, ValueError) as exc:
        logger.warning("[market_data] optional fallback disabled for %s (%s)",
                       symbol, type(exc).__name__)
        return None

    ticker = to_yahoo(symbol)
    if ticker.startswith("^"):
        providers = [("Yahoo v8 index", lambda: _yahoo_v8_hist(ticker, period=period))]
    elif ticker.endswith(".NS"):
        plain = from_yahoo(ticker)
        providers = [
            ("NSE equity", lambda: _nse_hist(plain)),
            ("Yahoo v8", lambda: _yahoo_v8_hist(ticker, period=period)),
            ("Stooq", lambda: _stooq_hist(ticker)),
        ]
    else:
        providers = [("Yahoo v8", lambda: _yahoo_v8_hist(ticker, period=period))]
    for provider, fetch in providers:
        try:
            frame = _clean_frame(fetch())
            if frame is not None and not frame.empty:
                logger.info("[market_data] %s recovered via %s", ticker, provider)
                return frame
        except Exception as exc:
            logger.warning("[market_data] fallback %s failed for %s: %s",
                           provider, ticker, type(exc).__name__)
    return None


def bulk_history(
    symbols: Iterable[str],
    period: str = "1y",
    chunk_size: int = 100,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cache_ttl: int = 0,
    adjusted: bool = False,
    retries: int = 2,
    max_fallbacks: int = 120,
    download_timeout: int = 30,
) -> Dict[str, pd.DataFrame]:
    """
    Download daily OHLCV for many NSE symbols.
    Returns {PLAIN_SYMBOL: DataFrame}. Symbols that fail are simply absent.
    progress_cb(done, total) is called after each chunk.
    cache_ttl > 0 enables a small in-memory cache keyed by (symbol, period).
    """
    syms = []
    seen = set()
    for s in symbols:
        s = str(s).strip().upper()
        if s and s not in seen:
            seen.add(s)
            syms.append(s)

    result: Dict[str, pd.DataFrame] = {}
    todo: List[str] = []
    now = time.time()
    for s in syms:
        if cache_ttl:
            with _cache_lock:
                e = _cache.get(f"{s}|{period}|{adjusted}")
            if e and now - e["ts"] < cache_ttl:
                result[s] = e["df"].copy()
                continue
        todo.append(s)

    total = len(syms)
    done = len(result)
    # Cap each chunk and the entire scan: bound latency while allowing later chunks recovery.
    remaining_fallbacks = min(len(todo), max(0, min(max_fallbacks, 120)))
    for i in range(0, len(todo), chunk_size):
        chunk_budget = min(12, remaining_fallbacks)
        part = todo[i:i + chunk_size]
        y_map = {}
        for s in part:
            y_map.setdefault(to_yahoo(s), []).append(s)
        got = _download_chunk(list(y_map.keys()), period, retries=max(0, min(retries, 2)), adjusted=adjusted, timeout=download_timeout)
        for yt, df in got.items():
            for plain in y_map.get(yt, [from_yahoo(yt)]):
                result[plain] = df.copy()
                if cache_ttl:
                    with _cache_lock:
                        _cache[f"{plain}|{period}|{adjusted}"] = {"df": df.copy(), "ts": time.time()}
        # When Yahoo bulk returns HTTP 401/missing symbols, try a small number
        # via independent NSE equity history or direct chart API. Never synthesize
        # missing candles, never retry the entire failed universe indefinitely.
        for missing in part:
            if adjusted or chunk_budget <= 0:
                break  # Alternate sources cannot establish adjusted OHLC parity.
            if missing in result:
                continue
            chunk_budget -= 1
            remaining_fallbacks -= 1
            recovered = _recover_missing_symbol(missing, period)
            if recovered is None:
                continue
            result[missing] = recovered
            if cache_ttl:
                with _cache_lock:
                    _cache[f"{missing}|{period}|{adjusted}"] = {
                        "df": recovered.copy(), "ts": time.time()
                    }
        # Progress represents validated/returned coverage, not symbols attempted.
        done = len(result)
        if progress_cb:
            try:
                progress_cb(min(done, total), total)
            except Exception:
                pass
    return result


def index_history(ticker: str = "^NSEI", period: str = "1y", *, retries: int = 1, timeout: int = 30) -> Optional[pd.DataFrame]:
    """Benchmark index history (default Nifty 50). Never raises."""
    try:
        got = _download_chunk([ticker], period, retries=retries, timeout=timeout)
        return got.get(ticker)
    except Exception as e:
        logger.warning(f"[market_data] index {ticker} failed: {e}")
        return None



def recover_verified_eod_frames(primary_frames, symbols, *, now):
    """Shared, request-local NSE one-session repair for raw daily equity bars.

    Never logs holdings, writes cache, returns stale bars or changes existing
    complete candles. The official parser checks exact symbol and session.
    """
    import re
    from portfolio_doctor_bhavcopy import recover_from_public_bhavcopy
    wanted = list(dict.fromkeys(symbols))
    if not 1 <= len(wanted) <= 10 or not all(
        isinstance(s, str) and re.fullmatch(r"[A-Z0-9&-]{2,25}", s) for s in wanted
    ):
        return {}
    return recover_from_public_bhavcopy(primary_frames, wanted, now=now)


def crosscheck_doctor_current_frames(frames, symbols, *, now):
    """Request-local official comparison of completed NSE Doctor candles."""
    from portfolio_doctor_bhavcopy import crosscheck_completed_doctor_frames
    return crosscheck_completed_doctor_frames(frames, symbols, now=now)


def recover_missing_public_histories(symbols, frames, *, session):
    """Bounded public QA/research retry for wholly missing daily histories.

    At most three explicitly requested NSE equities. Never replace a supplied
    history, cache customer data, or accept an incomplete/other-symbol result.
    """
    import re
    from msk_chart_release import validated_daily_history
    from smart_chart_eod_recovery import yahoo_host_history
    wanted = list(dict.fromkeys(symbols))
    if not 1 <= len(wanted) <= 10 or not all(
        isinstance(s, str) and re.fullmatch(r"[A-Z0-9&-]{2,25}", s) for s in wanted
    ):
        return {}
    absent = [s for s in wanted if frames.get(s) is None or frames[s].empty][:3]
    result = {}
    for symbol in absent:
        ticker = to_yahoo(symbol)
        frame = _download_chunk([ticker], "1y", retries=0, adjusted=False,
                                timeout=6).get(ticker)
        verified = validated_daily_history(frame, str(session))
        if verified is None:
            verified = validated_daily_history(yahoo_host_history(ticker, str(session)),
                                               str(session))
        if verified is not None:
            result[symbol] = verified
    return result
