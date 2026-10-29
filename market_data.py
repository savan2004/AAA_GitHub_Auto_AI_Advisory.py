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


def _clean_frame(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Reject incomplete OHLCV; never synthesize candles from close prices."""
    if df is None or df.empty or not all(col in df.columns for col in _OHLCV):
        return None
    out = df[_OHLCV].copy()
    for col in _OHLCV:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.replace([float("inf"), float("-inf")], float("nan"))
    out = out.dropna(subset=_OHLCV)
    if out.empty:
        return None
    valid = (out["Open"] > 0) & (out["High"] > 0) & (out["Low"] > 0) & (out["Close"] > 0)
    valid &= (out["Volume"] >= 0)
    valid &= (out["High"] >= out[["Open", "Close", "Low"]].max(axis=1))
    valid &= (out["Low"] <= out[["Open", "Close", "High"]].min(axis=1))
    out = out.loc[valid]
    if out.empty:
        return None
    try:
        if getattr(out.index, "tz", None) is not None:
            out.index = out.index.tz_localize(None)
    except (AttributeError, TypeError):
        pass
    return out


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


def _download_chunk(tickers: List[str], period: str, retries: int = 2, adjusted: bool = False) -> Dict[str, pd.DataFrame]:
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
                    progress=False, threads=(attempt == 0 and len(pending) > 1), timeout=30,
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


def bulk_history(
    symbols: Iterable[str],
    period: str = "1y",
    chunk_size: int = 100,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cache_ttl: int = 0,
    adjusted: bool = False,
    retries: int = 2,
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
                result[s] = e["df"]
                continue
        todo.append(s)

    total = len(syms)
    done = len(result)
    for i in range(0, len(todo), chunk_size):
        part = todo[i:i + chunk_size]
        y_map = {to_yahoo(s): s for s in part}
        got = _download_chunk(list(y_map.keys()), period, retries=max(0, min(retries, 2)), adjusted=adjusted)
        for yt, df in got.items():
            plain = y_map.get(yt) or from_yahoo(yt)
            result[plain] = df
            if cache_ttl:
                with _cache_lock:
                    _cache[f"{plain}|{period}|{adjusted}"] = {"df": df, "ts": time.time()}
        done += len(part)
        if progress_cb:
            try:
                progress_cb(min(done, total), total)
            except Exception:
                pass
    return result


def index_history(ticker: str = "^NSEI", period: str = "1y") -> Optional[pd.DataFrame]:
    """Benchmark index history (default Nifty 50). Never raises."""
    try:
        got = _download_chunk([ticker], period, retries=1)
        return got.get(ticker)
    except Exception as e:
        logger.warning(f"[market_data] index {ticker} failed: {e}")
        return None

