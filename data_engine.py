"""
data_engine.py — Yahoo Finance Rate-Limit Resistant Data Layer
================================================================
Drop-in replacement for all yfinance calls in main.py and swing_trades.py.

STRATEGY (in priority order):
  1. In-memory cache  → instant (TTL: 5 min live prices, 60 min fundamentals)
  2. Disk cache       → fast, survives restarts (SQLite via shelve)
  3. Yahoo Finance v8 chart API (direct HTTP, no yfinance library)
  4. Yahoo Finance v10 quoteSummary (fundamentals)
  5. NSE India API    → official source, never rate-limits retail users
  6. Stooq.com        → free, no key, good coverage for NSE stocks
  7. yfinance library → last resort, wrapped with long backoff
"""

import os
import time
import json
import shelve
import random
import logging
import threading
from collections import deque
from datetime import datetime, date
from typing import Optional, Dict, List
from io import StringIO

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

CACHE_DIR       = os.getenv("CACHE_DIR", "/tmp")
CACHE_FILE      = os.path.join(CACHE_DIR, "stock_cache")   # shelve appends .db/.dir/.bak

TTL_PRICE       = 300      # 5 min  — live price / OHLCV
TTL_HIST        = 600      # 10 min — historical candles
TTL_FUND        = 3_600    # 60 min — fundamentals (PE, ROE, etc.)

# Yahoo rate-limit guard: max 8 calls per 60-second window
YF_WINDOW_SEC   = 60
YF_MAX_PER_WIN  = 8

# Browser-identical request headers (critical for Yahoo Finance)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://finance.yahoo.com/",
    "Origin":          "https://finance.yahoo.com",
}

# ─────────────────────────────────────────────────────────────────────────────
# MEMORY CACHE
# ─────────────────────────────────────────────────────────────────────────────

_mem: Dict[str, dict] = {}
_mem_lock = threading.Lock()


def _mem_get(key: str, ttl: int):
    with _mem_lock:
        entry = _mem.get(key)
        if entry and time.time() - entry["ts"] < ttl:
            return entry["val"]
    return None


def _mem_set(key: str, val, ttl: int):  # noqa: ARG001
    with _mem_lock:
        _mem[key] = {"val": val, "ts": time.time()}


# ─────────────────────────────────────────────────────────────────────────────
# DISK CACHE  (shelve — survives process restarts)
# ─────────────────────────────────────────────────────────────────────────────

_disk_lock = threading.Lock()


def _disk_get(key: str, ttl: int):
    try:
        with _disk_lock:
            with shelve.open(CACHE_FILE, flag="r") as db:
                entry = db.get(key)
        if entry and time.time() - entry["ts"] < ttl:
            return entry["val"]
    except Exception:
        pass
    return None


def _disk_set(key: str, val, ttl: int = 0):  # noqa: ARG001
    try:
        with _disk_lock:
            with shelve.open(CACHE_FILE, flag="c") as db:
                db[key] = {"val": val, "ts": time.time()}
    except Exception:
        pass


def cached_get(key: str, ttl: int):
    """Check memory cache first, then disk cache."""
    v = _mem_get(key, ttl)
    if v is not None:
        return v
    v = _disk_get(key, ttl)
    if v is not None:
        _mem_set(key, v, ttl)   # promote to memory
    return v


def cached_set(key: str, val, ttl: int):
    _mem_set(key, val, ttl)
    _disk_set(key, val, ttl)


# ─────────────────────────────────────────────────────────────────────────────
# RATE LIMITER  (token bucket for Yahoo Finance calls)
# ─────────────────────────────────────────────────────────────────────────────

_yf_calls: deque = deque()
_rate_lock = threading.Lock()


def _wait_for_rate_slot():
    """Block until there is a free slot in the Yahoo rate-limit window."""
    while True:
        now = time.time()
        with _rate_lock:
            # Remove calls older than the window
            while _yf_calls and now - _yf_calls[0] > YF_WINDOW_SEC:
                _yf_calls.popleft()
            if len(_yf_calls) < YF_MAX_PER_WIN:
                _yf_calls.append(now)
                return
            wait = YF_WINDOW_SEC - (now - _yf_calls[0]) + 0.1
        logger.debug(f"[RateLimit] Waiting {wait:.1f}s for Yahoo slot")
        time.sleep(wait)


def _jitter(base: float, factor: float = 0.3) -> float:
    """Add ±factor random jitter to a delay to avoid thundering herd."""
    return base * (1 + random.uniform(-factor, factor))


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 1: Yahoo Finance v8 chart  (direct HTTP — most reliable)
# ─────────────────────────────────────────────────────────────────────────────

def _yahoo_v8_hist(symbol: str, period: str = "6mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV history from Yahoo Finance v8 chart API directly.
    Uses browser headers so Yahoo doesn't block us.
    """
    _wait_for_rate_slot()
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval={interval}&range={period}&includePrePost=false"
    )
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=12)
        if resp.status_code == 429:
            logger.warning(f"[Yahoo v8] 429 on {symbol} — backing off 30s")
            time.sleep(_jitter(30))
            return None
        if not resp.ok:
            logger.debug(f"[Yahoo v8] HTTP {resp.status_code} for {symbol}")
            return None

        data = resp.json()
        result = data.get("chart", {}).get("result", [None])[0]
        if not result:
            return None

        timestamps = result.get("timestamp", [])
        q = result.get("indicators", {}).get("quote", [{}])[0]

        if not timestamps or not q.get("close"):
            return None

        rows = []
        for i, ts in enumerate(timestamps):
            o = q.get("open",   [None])[i]
            h = q.get("high",   [None])[i]
            l = q.get("low",    [None])[i]
            c = q.get("close",  [None])[i]
            v = q.get("volume", [None])[i]
            if all(x is not None for x in [o, h, l, c, v]):
                rows.append({
                    "Date":   datetime.utcfromtimestamp(ts).date(),
                    "Open":   float(o),
                    "High":   float(h),
                    "Low":    float(l),
                    "Close":  float(c),
                    "Volume": int(v),
                })

        if not rows:
            return None

        df = pd.DataFrame(rows).set_index("Date")
        df.index = pd.to_datetime(df.index)
        return df

    except requests.exceptions.RequestException as e:
        logger.warning(f"[Yahoo v8] Network error for {symbol}: {e}")
        return None
    except Exception as e:
        logger.warning(f"[Yahoo v8] Parse error for {symbol}: {e}")
        return None


def _yahoo_v8_quote(symbol: str) -> Optional[dict]:
    """Fetch live quote (price, prevClose, 52W H/L, PE, EPS) from Yahoo v8 meta."""
    _wait_for_rate_slot()
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval=1d&range=2d"
    )
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        if resp.status_code == 429:
            time.sleep(_jitter(30))
            return None
        if not resp.ok:
            return None

        data = resp.json()
        result = data.get("chart", {}).get("result", [None])[0]
        if not result:
            return None

        meta = result.get("meta", {})
        return {
            "price":       meta.get("regularMarketPrice"),
            "prev_close":  meta.get("chartPreviousClose") or meta.get("previousClose"),
            "high52":      meta.get("fiftyTwoWeekHigh"),
            "low52":       meta.get("fiftyTwoWeekLow"),
            "pe":          meta.get("trailingPE"),
            "eps":         meta.get("epsTrailingTwelveMonths"),
            "market_cap":  meta.get("marketCap"),
            "currency":    meta.get("currency", "INR"),
            "name":        meta.get("longName") or meta.get("shortName") or symbol,
        }
    except Exception as e:
        logger.debug(f"[Yahoo v8 quote] {symbol}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 2: Yahoo Finance v10 quoteSummary  (richer fundamentals)
# ─────────────────────────────────────────────────────────────────────────────

def _yahoo_v10_fundamentals(symbol: str) -> Optional[dict]:
    """
    Fetch PE, PB, ROE, EPS, dividend yield from Yahoo quoteSummary.
    Tries both query1 and query2 endpoints — Yahoo periodically blocks one.
    """
    _wait_for_rate_slot()
    modules = "summaryDetail,defaultKeyStatistics,financialData,price"
    for host in ["query2", "query1"]:
        url = (
            f"https://{host}.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
            f"?modules={modules}&corsDomain=finance.yahoo.com&formatted=false"
        )
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=12)
            if resp.status_code in (401, 403):
                logger.debug(f"[Yahoo v10] {host} blocked for {symbol} ({resp.status_code})")
                continue
            if resp.status_code == 429:
                time.sleep(_jitter(30))
                return None
            if not resp.ok:
                continue

            data = resp.json()
            result = data.get("quoteSummary", {}).get("result", [None])[0]
            if not result:
                continue

            sd = result.get("summaryDetail",         {})
            ks = result.get("defaultKeyStatistics",  {})
            fd = result.get("financialData",         {})
            pr = result.get("price",                 {})

            def raw(d, key):
                v = d.get(key)
                return v.get("raw") if isinstance(v, dict) else v

            out = {
                "pe":             raw(sd, "trailingPE")    or raw(pr, "trailingPE"),
                "pb":             raw(ks, "priceToBook"),
                "roe":            raw(fd, "returnOnEquity"),
                "eps":            raw(ks, "trailingEps")   or raw(fd, "revenuePerShare"),
                "dividend_yield": raw(sd, "dividendYield") or raw(sd, "trailingAnnualDividendYield"),
                "high52":         raw(sd, "fiftyTwoWeekHigh"),
                "low52":          raw(sd, "fiftyTwoWeekLow"),
                "market_cap":     raw(pr, "marketCap"),
                "name":           raw(pr, "longName") or raw(pr, "shortName") or symbol,
                # FIX: previously missing — needed for advisory card
                "totalRevenue":   raw(fd, "totalRevenue"),
                "debtToEquity":   raw(fd, "debtToEquity"),
                "beta":           raw(ks, "beta"),
                "forwardPE":      raw(sd, "forwardPE"),
            }
            if out.get("pe") is not None or out.get("market_cap") is not None:
                return out
        except Exception as e:
            logger.debug(f"[Yahoo v10] {host} {symbol}: {e}")

    return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 3: NSE India  (official, never rate-limits retail)
# ─────────────────────────────────────────────────────────────────────────────

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://www.nseindia.com/",
}

_nse_session: Optional[requests.Session] = None
_nse_session_ts: float = 0.0
_nse_lock = threading.Lock()


def _get_nse_session() -> requests.Session:
    """Return a warmed-up NSE session (cookies initialised)."""
    global _nse_session, _nse_session_ts
    with _nse_lock:
        age = time.time() - _nse_session_ts
        if _nse_session is None or age > 300:          # refresh every 5 min
            s = requests.Session()
            s.headers.update(_NSE_HEADERS)
            try:
                s.get("https://www.nseindia.com/", timeout=8)  # warm-up for cookies
            except Exception:
                pass
            _nse_session    = s
            _nse_session_ts = time.time()
        return _nse_session


def _nse_quote(symbol: str) -> Optional[dict]:
    """Fetch live price from NSE India quote API."""
    try:
        sess = _get_nse_session()
        url  = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
        resp = sess.get(url, timeout=10)
        if not resp.ok:
            return None
        data = resp.json()
        pd_  = data.get("priceInfo", {})
        md   = data.get("metadata",  {})
        return {
            "price":      pd_.get("lastPrice"),
            "prev_close": pd_.get("previousClose") or pd_.get("close"),
            "high52":     pd_.get("weekHighLow", {}).get("max"),
            "low52":      pd_.get("weekHighLow", {}).get("min"),
            "name":       md.get("companyName", symbol),
            "pe":         None,
            "eps":        None,
        }
    except Exception as e:
        logger.debug(f"[NSE] {symbol}: {e}")
        return None


def _nse_hist(symbol: str, series: str = "EQ") -> Optional[pd.DataFrame]:
    """Fetch ~1 year OHLCV history from NSE historical data API."""
    try:
        end   = date.today()
        start = date(end.year - 1, end.month, end.day)
        sess  = _get_nse_session()
        url   = (
            "https://www.nseindia.com/api/historical/cm/equity"
            f"?symbol={symbol}&series=[%22{series}%22]"
            f"&from={start.strftime('%d-%m-%Y')}&to={end.strftime('%d-%m-%Y')}&csv=true"
        )
        resp = sess.get(url, timeout=15)
        if not resp.ok:
            return None

        df = pd.read_csv(StringIO(resp.text))
        col_map = {
            "DATE1":        "Date",
            "OPEN PRICE":   "Open",
            "HIGH PRICE":   "High",
            "LOW PRICE":    "Low",
            "CLOSE PRICE":  "Close",
            "TTL TRD QNTY": "Volume",
        }
        df = df.rename(
            columns={
                c: col_map[c.strip()]
                for c in df.columns
                if c.strip() in col_map
            }
        )
        if "Date" not in df.columns:
            return None

        df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
        df = df.dropna(subset=["Date"]).set_index("Date").sort_index()

        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", ""),
                    errors="coerce",
                )

        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        return df if not df.empty else None

    except Exception as e:
        logger.debug(f"[NSE hist] {symbol}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 4: Stooq.com  (free, no API key, good NSE coverage)
# ─────────────────────────────────────────────────────────────────────────────

def _stooq_hist(symbol: str, period_days: int = 365) -> Optional[pd.DataFrame]:
    """
    Stooq provides free OHLCV data.
    NSE symbols on Stooq use the format: RELIANCE.NS  (same as Yahoo)
    """
    try:
        end_dt   = date.today()
        start_dt = date(end_dt.year - (period_days // 365 + 1), end_dt.month, end_dt.day)
        url = (
            f"https://stooq.com/q/d/l/?s={symbol.lower()}"
            f"&d1={start_dt.strftime('%Y%m%d')}"
            f"&d2={end_dt.strftime('%Y%m%d')}"
            f"&i=d"
        )
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        if not resp.ok or "No data" in resp.text[:50]:
            return None

        df = pd.read_csv(StringIO(resp.text))
        df.columns = [c.strip().title() for c in df.columns]
        if "Date" not in df.columns:
            return None
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        cutoff = pd.Timestamp(start_dt)
        df = df[df.index >= cutoff]
        return df if not df.empty else None

    except Exception as e:
        logger.debug(f"[Stooq] {symbol}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 5: yfinance library  (last resort with long backoff)
# ─────────────────────────────────────────────────────────────────────────────

def _yfinance_hist(symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
    """Call yfinance with exponential backoff — used only when all else fails."""
    try:
        import yfinance as yf
    except ImportError:
        logger.debug("[yfinance] not installed")
        return None

    for attempt in range(1, 4):
        try:
            _wait_for_rate_slot()
            tk = yf.Ticker(symbol)
            df = tk.history(period=period, auto_adjust=True, timeout=15)
            if not df.empty:
                df = df[["Open", "High", "Low", "Close", "Volume"]]
                return df
        except Exception as e:
            msg = str(e).lower()
            if "too many requests" in msg or "rate limit" in msg or "429" in msg:
                wait = _jitter(30 * (2 ** (attempt - 1)))   # 30s, 60s, 120s
                logger.warning(f"[yfinance] 429 on {symbol} attempt {attempt} — sleeping {wait:.0f}s")
                time.sleep(wait)
            else:
                logger.warning(f"[yfinance] {symbol} attempt {attempt}: {e}")
                time.sleep(_jitter(5 * attempt))
    return None


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API — drop-in replacements for get_hist() and get_info()
# ─────────────────────────────────────────────────────────────────────────────

def get_hist(symbol: str, period: str = "1y") -> pd.DataFrame:
    """
    Fetch OHLCV history for `symbol` (NSE ticker without .NS suffix).
    Returns a DataFrame with columns [Open, High, Low, Close, Volume].
    """
    sym_clean = symbol.upper().replace(".NS", "").replace(".NSE", "")
    yahoo_sym = f"{sym_clean}.NS"

    ttl = TTL_HIST if period not in ("5d", "2d", "1d") else TTL_PRICE
    cache_key = f"hist_{yahoo_sym}_{period}"

    cached = cached_get(cache_key, ttl)
    if cached is not None:
        logger.debug(f"[Cache HIT] {cache_key}")
        return cached

    df: Optional[pd.DataFrame]

    yf_period = {
        "1y": "1y", "6mo": "6mo", "3mo": "3mo",
        "2mo": "3mo", "1mo": "1mo", "5d": "5d", "2d": "5d",
    }.get(period, "1y")
    df = _yahoo_v8_hist(yahoo_sym, period=yf_period)

    if df is None or df.empty:
        logger.info(f"[DataEngine] Yahoo v8 failed for {sym_clean} — trying NSE")
        df = _nse_hist(sym_clean)

    if df is None or df.empty:
        logger.info(f"[DataEngine] NSE failed for {sym_clean} — trying Stooq")
        days_map = {
            "1y": 365, "6mo": 180, "3mo": 90,
            "2mo": 60, "1mo": 30, "5d": 5, "2d": 2,
        }
        df = _stooq_hist(yahoo_sym, period_days=days_map.get(period, 365))

    if df is None or df.empty:
        logger.info(f"[DataEngine] Stooq failed for {sym_clean} — trying yfinance (last resort)")
        time.sleep(_jitter(3))
        df = _yfinance_hist(yahoo_sym, period=period)

    if df is not None and not df.empty:
        cached_set(cache_key, df, ttl)
        logger.info(f"[DataEngine] {sym_clean} history: {len(df)} rows fetched")
        return df

    logger.error(f"[DataEngine] ALL sources failed for {sym_clean}")
    return pd.DataFrame()


def get_info(symbol: str) -> dict:
    """
    Fetch live quote + fundamentals for `symbol` (NSE ticker without .NS suffix).
    Keys: price, prev_close, high52, low52, pe, pb, roe,
          eps, dividend_yield, market_cap, name.
    """
    sym_clean = symbol.upper().replace(".NS", "").replace(".NSE", "")
    yahoo_sym = f"{sym_clean}.NS"
    cache_key = f"info_{yahoo_sym}"

    cached = cached_get(cache_key, TTL_FUND)
    if cached is not None:
        logger.debug(f"[Cache HIT] {cache_key}")
        return cached

    info: dict = {}

    v10 = _yahoo_v10_fundamentals(yahoo_sym)
    if v10:
        info.update({k: v for k, v in v10.items() if v is not None})

    if not info.get("price"):
        v8 = _yahoo_v8_quote(yahoo_sym)
        if v8:
            for k, v in v8.items():
                if v is not None and k not in info:
                    info[k] = v

    if not info.get("price"):
        nse = _nse_quote(sym_clean)
        if nse:
            for k, v in nse.items():
                if v is not None and k not in info:
                    info[k] = v

    missing_fund = not info.get("pe") and not info.get("roe")
    if missing_fund:
        finnhub_key = os.getenv("FINNHUB_API_KEY", "").strip()
        if finnhub_key:
            try:
                r = requests.get(
                    "https://finnhub.io/api/v1/stock/metric",
                    params={
                        "symbol": f"NSE:{sym_clean}",
                        "metric": "all",
                        "token": finnhub_key,
                    },
                    timeout=8,
                ).json()
                m = r.get("metric", {})
                if m:
                    if not info.get("pe"):
                        info["pe"] = m.get("peNormalizedAnnual") or m.get("peTTM")
                    if not info.get("pb"):
                        info["pb"] = m.get("pbQuarterly") or m.get("pbAnnual")
                    if not info.get("roe"):
                        info["roe"] = m.get("roeTTM")
                    if not info.get("eps"):
                        info["eps"] = m.get("epsTTM")
                    if not info.get("high52"):
                        info["high52"] = m.get("52WeekHigh")
                    if not info.get("low52"):
                        info["low52"] = m.get("52WeekLow")
                    logger.info(f"[DataEngine] {sym_clean}: Finnhub filled missing fundamentals")
            except Exception as e:
                logger.debug(f"[DataEngine] Finnhub {sym_clean}: {e}")

    if info:
        cached_set(cache_key, info, TTL_FUND)

    return info


def get_live_price(symbol: str) -> Optional[float]:
    """
    Return only the current market price (₹).
    Uses a 5-minute TTL — suitable for screener and swing scanner loops.
    """
    sym_clean = symbol.upper().replace(".NS", "").replace(".NSE", "")
    yahoo_sym = f"{sym_clean}.NS"
    cache_key = f"price_{yahoo_sym}"

    cached = cached_get(cache_key, TTL_PRICE)
    if cached is not None:
        return cached

    q = _yahoo_v8_quote(yahoo_sym)
    price = q.get("price") if q else None

    if price is None:
        nq = _nse_quote(sym_clean)
        price = nq.get("price") if nq else None

    if price is not None:
        try:
            price = float(price)
            cached_set(cache_key, price, TTL_PRICE)
        except (TypeError, ValueError):
            price = None

    return price


def batch_quotes(symbols: List[str]) -> Dict[str, Optional[dict]]:
    """
    Fetch live quotes for multiple symbols with automatic rate-limit spacing.
    Returns { symbol: info_dict_or_None }.
    """
    results: Dict[str, Optional[dict]] = {}
    for i, sym in enumerate(symbols):
        if i > 0:
            time.sleep(_jitter(1.5))
        try:
            results[sym] = get_info(sym)
        except Exception as e:
            logger.warning(f"[batch_quotes] {sym}: {e}")
            results[sym] = None
    return results


def clear_cache(symbol: Optional[str] = None):
    """
    Clear cache entries.
    If symbol is None, clears the entire in-memory cache.
    Disk cache entries expire naturally via TTL.
    """
    with _mem_lock:
        if symbol is None:
            _mem.clear()
            logger.info("[DataEngine] In-memory cache cleared")
        else:
            sym_clean = symbol.upper().replace(".NS", "")
            keys_to_remove = [k for k in _mem if sym_clean in k]
            for k in keys_to_remove:
                del _mem[k]
            logger.info(f"[DataEngine] Cache cleared for {sym_clean} ({len(keys_to_remove)} keys)")


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def calc_rsi(close: pd.Series, period: int = 14) -> float:
    """RSI(14) — self-contained so callers don't need to import calc functions."""
    if len(close) < period + 1:
        return 50.0
    delta     = close.diff()
    gain      = delta.clip(lower=0)
    loss      = -delta.clip(upper=0)
    avg_gain  = gain.rolling(period).mean()
    avg_loss  = loss.rolling(period).mean()
    avg_loss  = avg_loss.replace(0, 1e-10)
    rsi       = 100 - (100 / (1 + avg_gain / avg_loss))
    val       = rsi.iloc[-1]
    return round(float(val), 1) if pd.notna(val) else 50.0


def calc_ema(close: pd.Series, span: int) -> float:
    ema = close.ewm(span=span, adjust=False).mean()
    val = ema.iloc[-1]
    return round(float(val), 2) if pd.notna(val) else float(close.iloc[-1])


# ─────────────────────────────────────────────────────────────────────────────
# SELF-TEST  (run directly: python data_engine.py)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    test_syms = ["TCS", "RELIANCE", "HDFCBANK"]

    for sym in test_syms:
        print(f"\n{'='*50}")
        print(f"Testing: {sym}")
        df = get_hist(sym, "3mo")
        if df.empty:
            print("  ❌ History: FAILED")
        else:
            print(f"  ✅ History: {len(df)} rows | Last close: ₹{df['Close'].iloc[-1]:.2f}")
            rsi = calc_rsi(df["Close"])
            print(f"  📊 RSI(14): {rsi}")

        info = get_info(sym)
        price = info.get("price")
        pe    = info.get("pe")
        print(f"  {'✅' if price else '❌'} Quote: ₹{price} | PE: {pe}")

    print("\n✅ Self-test complete")
