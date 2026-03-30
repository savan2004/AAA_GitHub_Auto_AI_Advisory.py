# fundamentals.py — Fixed: uses data_engine as primary source, yfinance only as fallback
#
# FIXES:
#   - Primary source: data_engine.get_info() (Yahoo v8/v10 + NSE + Stooq, rate-limited)
#   - Finnhub as secondary source for missing fundamentals (PE, market cap, EPS)
#   - yfinance only used if both above fail — with proper rate limiting
#   - All values normalized to match main.py expectations

import os
import time
import logging
import random
from typing import Dict, Any, Optional

import requests

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────────
_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_FUND = 6 * 60 * 60  # 6 hours


def _get_cached(key: str, ttl: int):
    d = _CACHE.get(key)
    if d and time.time() - d["ts"] < ttl:
        return d["val"]
    return None


def _set_cached(key: str, val: Any):
    _CACHE[key] = {"val": val, "ts": time.time()}


# ── Helpers ───────────────────────────────────────────────────────────────────

def safe_val(d: dict, *keys, mul: float = 1.0):
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return round(float(v) * mul, 2)
            except Exception:
                pass
    return None


def fmt_cr(val) -> str:
    if val is None:
        return "N/A"
    try:
        cr = float(val) / 1e7  # Rs to Crores
        if cr >= 1_00_000:
            return f"₹{cr/1_00_000:.2f}L Cr"
        if cr >= 1_000:
            return f"₹{cr/1_000:.2f}K Cr"
        return f"₹{cr:.2f} Cr"
    except Exception:
        return "N/A"


# ── Finnhub fundamentals ──────────────────────────────────────────────────────

def _fetch_finnhub(sym: str) -> Optional[dict]:
    """Fetch basic fundamentals from Finnhub (free tier)."""
    key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not key:
        return None
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/stock/metric",
            params={"symbol": f"NSE:{sym}", "metric": "all", "token": key},
            timeout=8,
        ).json()
        m = r.get("metric", {})
        if not m:
            return None
        return {
            "pe":          m.get("peNormalizedAnnual") or m.get("peTTM"),
            "pb":          m.get("pbQuarterly") or m.get("pbAnnual"),
            "roe":         m.get("roeTTM"),          # already in %
            "eps":         m.get("epsTTM"),
            "market_cap":  m.get("marketCapitalization"),  # in millions USD — convert below
            "high52":      m.get("52WeekHigh"),
            "low52":       m.get("52WeekLow"),
            "dividend_yield": m.get("dividendYieldIndicatedAnnual"),
            "beta":        m.get("beta"),
        }
    except Exception as e:
        logger.debug(f"Finnhub fundamentals {sym}: {e}")
        return None


# ── yfinance fallback ─────────────────────────────────────────────────────────

_last_yf_call = 0.0
_YF_DELAY = 3.0


def _rate_limit_yf():
    global _last_yf_call
    elapsed = time.time() - _last_yf_call
    if elapsed < _YF_DELAY:
        time.sleep(_YF_DELAY - elapsed + random.uniform(0.1, 0.5))
    _last_yf_call = time.time()


def _fetch_yfinance(sym: str) -> dict:
    """Last-resort yfinance call — rate-limited."""
    try:
        import yfinance as yf
        from requests import Session
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        _rate_limit_yf()
        session = Session()
        retry   = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.headers["User-Agent"] = random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        ])

        ticker = yf.Ticker(f"{sym}.NS", session=session)
        info   = {}
        try:
            info = dict(ticker.info)
        except Exception:
            pass
        try:
            fi = ticker.fast_info
            for attr, key in [("market_cap","marketCap"), ("fifty_two_week_high","fiftyTwoWeekHigh"),
                               ("fifty_two_week_low","fiftyTwoWeekLow")]:
                val = getattr(fi, attr, None)
                if val is not None:
                    info.setdefault(key, val)
        except Exception:
            pass
        return info
    except Exception as e:
        logger.error(f"yfinance fundamentals {sym}: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED FUNDAMENTALS FETCH
# ══════════════════════════════════════════════════════════════════════════════

def get_fundamentals(sym: str) -> dict:
    """
    Priority: data_engine → Finnhub → yfinance (last resort).
    Returns normalized dict compatible with main.py build_adv().
    """
    sym = sym.upper().replace(".NS", "")
    cache_key = f"fund_{sym}"
    cached = _get_cached(cache_key, CACHE_TTL_FUND)
    if cached:
        return cached

    result = {
        "name": sym, "mcap": None, "rev": None,
        "pe": None, "fwd_pe": None, "pb": None,
        "roe": None, "eps": None, "de": None,
        "div_y": None, "w52h": None, "w52l": None, "beta": None,
    }

    # ── Source 1: data_engine (already rate-limited, multi-source) ────────────
    try:
        from data_engine import get_info
        info = get_info(sym)
        if info:
            result["name"]  = info.get("name") or sym
            result["pe"]    = safe_val(info, "pe")
            result["pb"]    = safe_val(info, "pb")
            # data_engine roe is decimal (0.18 = 18%) → convert to percent
            roe_raw = safe_val(info, "roe")
            result["roe"]   = round(roe_raw * 100, 1) if roe_raw is not None else None
            result["eps"]   = safe_val(info, "eps")
            result["mcap"]  = info.get("market_cap")
            result["w52h"]  = safe_val(info, "high52")
            result["w52l"]  = safe_val(info, "low52")
            # dividend_yield from data_engine is decimal → percent
            div_raw = safe_val(info, "dividend_yield")
            result["div_y"] = round(div_raw * 100, 2) if div_raw is not None else None
            logger.info(f"get_fundamentals {sym}: data_engine OK")
    except Exception as e:
        logger.warning(f"get_fundamentals data_engine {sym}: {e}")

    # ── Source 2: Finnhub (fills gaps — especially PE, ROE, beta) ─────────────
    missing = [k for k in ["pe", "roe", "eps", "w52h", "w52l", "beta", "pb"] if result[k] is None]
    if missing:
        fh = _fetch_finnhub(sym)
        if fh:
            if result["pe"]   is None: result["pe"]   = safe_val(fh, "pe")
            if result["pb"]   is None: result["pb"]   = safe_val(fh, "pb")
            if result["roe"]  is None: result["roe"]  = safe_val(fh, "roe")   # already in %
            if result["eps"]  is None: result["eps"]  = safe_val(fh, "eps")
            if result["beta"] is None: result["beta"] = safe_val(fh, "beta")
            if result["w52h"] is None: result["w52h"] = safe_val(fh, "high52")
            if result["w52l"] is None: result["w52l"] = safe_val(fh, "low52")
            if result["div_y"] is None:
                dv = safe_val(fh, "dividend_yield")
                result["div_y"] = dv  # Finnhub returns as % already
            logger.info(f"get_fundamentals {sym}: Finnhub filled gaps")

    # ── Source 3: yfinance last resort ────────────────────────────────────────
    still_missing = [k for k in ["pe", "roe", "mcap"] if result[k] is None]
    if still_missing:
        yf_info = _fetch_yfinance(sym)
        if yf_info:
            if result["name"]  == sym:   result["name"]  = yf_info.get("longName") or yf_info.get("shortName") or sym
            if result["pe"]    is None:  result["pe"]    = safe_val(yf_info, "trailingPE")
            if result["fwd_pe"] is None: result["fwd_pe"] = safe_val(yf_info, "forwardPE")
            if result["pb"]    is None:  result["pb"]    = safe_val(yf_info, "priceToBook")
            if result["roe"]   is None:
                roe_raw = safe_val(yf_info, "returnOnEquity")
                result["roe"] = round(roe_raw * 100, 1) if roe_raw is not None else None
            if result["eps"]   is None:  result["eps"]   = safe_val(yf_info, "trailingEps")
            if result["mcap"]  is None:  result["mcap"]  = yf_info.get("marketCap")
            if result["rev"]   is None:  result["rev"]   = yf_info.get("totalRevenue")
            if result["de"]    is None:  result["de"]    = safe_val(yf_info, "debtToEquity")
            if result["w52h"]  is None:  result["w52h"]  = safe_val(yf_info, "fiftyTwoWeekHigh")
            if result["w52l"]  is None:  result["w52l"]  = safe_val(yf_info, "fiftyTwoWeekLow")
            if result["beta"]  is None:  result["beta"]  = safe_val(yf_info, "beta")
            if result["div_y"] is None:
                div_raw = safe_val(yf_info, "dividendYield")
                result["div_y"] = round(div_raw * 100, 2) if div_raw is not None else None
            logger.info(f"get_fundamentals {sym}: yfinance filled remaining gaps")

    logger.info(f"get_fundamentals {sym}: filled={[k for k,v in result.items() if v is not None]}")
    _set_cached(cache_key, result)
    return result
