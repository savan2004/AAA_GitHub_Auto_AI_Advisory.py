# fundamentals.py — Fixed v2
#
# FIXES:
#   - yfinance: NEVER pass session= (yfinance >= 0.2.55 requires curl_cffi internally)
#     "Yahoo API requires curl_cffi session not requests.Session" → remove session entirely
#   - Primary source: data_engine.get_info() (Yahoo v8/v10 + NSE + Stooq, rate-limited)
#   - Finnhub as secondary source for missing fundamentals (PE, market cap, EPS)
#   - yfinance only used if both above fail — NO custom session, let yfinance handle it

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
        cr = float(val) / 1e7
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
            "pe":             m.get("peNormalizedAnnual") or m.get("peTTM"),
            "pb":             m.get("pbQuarterly") or m.get("pbAnnual"),
            "roe":            m.get("roeTTM"),       # already in %
            "eps":            m.get("epsTTM"),
            "market_cap":     m.get("marketCapitalization"),
            "high52":         m.get("52WeekHigh"),
            "low52":          m.get("52WeekLow"),
            "dividend_yield": m.get("dividendYieldIndicatedAnnual"),
            "beta":           m.get("beta"),
        }
    except Exception as e:
        logger.debug(f"Finnhub fundamentals {sym}: {e}")
        return None


# ── yfinance fallback — NO custom session ─────────────────────────────────────

_last_yf_call = 0.0
_YF_DELAY = 3.0


def _rate_limit_yf():
    global _last_yf_call
    elapsed = time.time() - _last_yf_call
    if elapsed < _YF_DELAY:
        time.sleep(_YF_DELAY - elapsed + random.uniform(0.1, 0.5))
    _last_yf_call = time.time()


def _fetch_yfinance(sym: str) -> dict:
    """
    Last-resort yfinance call.
    CRITICAL: Do NOT pass session= to yfinance >= 0.2.55.
    It requires curl_cffi internally and rejects requests.Session.
    """
    try:
        import yfinance as yf
        _rate_limit_yf()

        # No session= kwarg — let yfinance use its own curl_cffi session
        ticker = yf.Ticker(f"{sym}.NS")
        info = {}
        try:
            info = dict(ticker.info)
        except Exception as e:
            logger.debug(f"yfinance .info {sym}: {e}")

        # fast_info as supplemental (also no session needed)
        try:
            fi = ticker.fast_info
            for attr, key in [
                ("market_cap",          "marketCap"),
                ("fifty_two_week_high", "fiftyTwoWeekHigh"),
                ("fifty_two_week_low",  "fiftyTwoWeekLow"),
            ]:
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
# Priority: data_engine → Finnhub → yfinance (last resort, no session)
# ══════════════════════════════════════════════════════════════════════════════

def get_fundamentals(sym: str) -> dict:
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
            result["roe"]   = round(roe_raw * 100, 1) if roe_raw is not None and abs(roe_raw) <= 1 else roe_raw
            result["eps"]   = safe_val(info, "eps")
            result["mcap"]  = info.get("market_cap")
            result["w52h"]  = safe_val(info, "high52")
            result["w52l"]  = safe_val(info, "low52")
            div_raw = safe_val(info, "dividend_yield")
            result["div_y"] = round(div_raw * 100, 2) if div_raw is not None and div_raw <= 1 else div_raw
            logger.info(f"get_fundamentals {sym}: data_engine OK")
    except Exception as e:
        logger.warning(f"get_fundamentals data_engine {sym}: {e}")

    # ── Source 2: Finnhub (fills gaps — PE, ROE, beta) ────────────────────────
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
                result["div_y"] = safe_val(fh, "dividend_yield")  # Finnhub: already %
            logger.info(f"get_fundamentals {sym}: Finnhub filled gaps")

    # ── Source 3: yfinance last resort — NO session kwarg ─────────────────────
    still_missing = [k for k in ["pe", "roe", "mcap"] if result[k] is None]
    if still_missing:
        yf_info = _fetch_yfinance(sym)
        if yf_info:
            if result["name"] == sym:
                result["name"] = yf_info.get("longName") or yf_info.get("shortName") or sym
            if result["pe"]    is None: result["pe"]    = safe_val(yf_info, "trailingPE")
            if result["fwd_pe"] is None: result["fwd_pe"] = safe_val(yf_info, "forwardPE")
            if result["pb"]    is None: result["pb"]    = safe_val(yf_info, "priceToBook")
            if result["roe"]   is None:
                roe_raw = safe_val(yf_info, "returnOnEquity")
                # yfinance returns decimal (0.18 = 18%)
                result["roe"] = round(roe_raw * 100, 1) if roe_raw is not None and abs(roe_raw) <= 1 else roe_raw
            if result["eps"]   is None: result["eps"]   = safe_val(yf_info, "trailingEps")
            if result["mcap"]  is None: result["mcap"]  = yf_info.get("marketCap")
            if result["rev"]   is None: result["rev"]   = yf_info.get("totalRevenue")
            if result["de"]    is None: result["de"]    = safe_val(yf_info, "debtToEquity")
            if result["w52h"]  is None: result["w52h"]  = safe_val(yf_info, "fiftyTwoWeekHigh")
            if result["w52l"]  is None: result["w52l"]  = safe_val(yf_info, "fiftyTwoWeekLow")
            if result["beta"]  is None: result["beta"]  = safe_val(yf_info, "beta")
            if result["div_y"] is None:
                div_raw = safe_val(yf_info, "dividendYield")
                result["div_y"] = round(div_raw * 100, 2) if div_raw is not None and div_raw <= 1 else div_raw
            logger.info(f"get_fundamentals {sym}: yfinance filled remaining gaps")

    logger.info(f"get_fundamentals {sym}: filled={[k for k,v in result.items() if v is not None]}")
    _set_cached(cache_key, result)
    return result
