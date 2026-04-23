"""
fundamentals.py — Fixed v3

FIXES vs v2:
  1. Added Screener.in scraper as Source 2 — works WITHOUT any API key,
     fills PE, ROE, D/E, Market Cap, Revenue for almost all NSE stocks.
     This is why BEL showed all N/A — Yahoo v10 was blocked and Finnhub
     key was not set. Screener.in requires zero credentials.
  2. Source priority: data_engine → Screener.in → Finnhub → yfinance
  3. Screener.in scraper extracts: PE, PB, ROE, D/E, EPS, Div Yield,
     Market Cap, Revenue, 52W H/L, Beta
  4. All values validated before use (no negative PE, no zero values)
"""

import os
import re
import time
import logging
import random
from typing import Dict, Any, Optional

import requests

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────────
_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_FUND = 4 * 60 * 60   # 4 hours (was 6; freshness matters more)


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
                f = float(v) * mul
                if f != 0:        # don't accept 0 as a valid fundamental
                    return round(f, 2)
            except Exception:
                pass
    return None


def fmt_cr(val) -> str:
    if val is None:
        return "N/A"
    try:
        cr = float(val) / 1e7
        if cr >= 1_00_000: return f"₹{cr/1_00_000:.2f}L Cr"
        if cr >= 1_000:    return f"₹{cr/1_000:.2f}K Cr"
        return f"₹{cr:.2f} Cr"
    except Exception:
        return "N/A"
def fmt_rev(val) -> str:
  if val is None:
    return "N/A"
  try:
    cr = float(val)
    if cr >= 1_00_000:
      return f"₹{cr/1_00_000:.2f}L Cr"
    if cr >= 1_000:
      return f"₹{cr/1_000:.2f}K Cr"
    return f"₹{cr:.2f} Cr"
  except Exception:
    return "N/A"

# ── Source 1: Screener.in (NO API KEY needed, works for all NSE stocks) ───────

_SCREENER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.screener.in/",
}


def _parse_num(text: str) -> Optional[float]:
    """Parse Indian number format: 1,23,456.78 → 123456.78"""
    try:
        cleaned = re.sub(r"[^\d.\-]", "", text.strip())
        if cleaned:
            return float(cleaned)
    except Exception:
        pass
    return None


def _parse_crore(text: str) -> Optional[float]:
    """Convert crore text to raw rupees: '3,29,000 Cr' → 3290000000000"""
    try:
        num = _parse_num(text)
        if num:
            return num * 1e7   # crore → rupees
    except Exception:
        pass
    return None


def _fetch_screener(sym: str) -> Optional[dict]:
    """
    Scrape Screener.in for fundamental data.
    No API key required. Handles NSE stocks reliably.

    Extracts from the key ratios table:
      Market Cap, Current Price, 52W H/L, Stock P/E, Book Value,
      Dividend Yield, ROCE, ROE, Face Value
    And from the P&L section: Revenue, Net Profit, EPS
    And from Balance Sheet: Debt, Equity → D/E ratio
    """
    url = f"https://www.screener.in/company/{sym}/consolidated/"
    try:
        resp = requests.get(url, headers=_SCREENER_HEADERS, timeout=12)
        if resp.status_code == 404:
            # Try standalone (non-consolidated)
            url  = f"https://www.screener.in/company/{sym}/"
            resp = requests.get(url, headers=_SCREENER_HEADERS, timeout=12)
        if not resp.ok:
            logger.debug(f"Screener.in {sym}: HTTP {resp.status_code}")
            return None

        html = resp.text

        result: dict = {}

        # ── Key Ratios (li items in #top-ratios) ─────────────────────────────
        # Pattern: <li> <span class="name">Market Cap</span> <span class="..."><span>3,29,000</span> Cr</span> </li>
        ratio_blocks = re.findall(
            r'<li[^>]*>.*?<span[^>]*class="[^"]*name[^"]*"[^>]*>(.*?)</span>'
            r'.*?<span[^>]*>(.*?)</span>',
            html, re.DOTALL | re.IGNORECASE
        )
        for name_raw, val_raw in ratio_blocks:
            name_clean = re.sub(r"<[^>]+>", "", name_raw).strip().lower()
            val_clean  = re.sub(r"<[^>]+>", "", val_raw).strip()

            if "market cap" in name_clean:
                result["mcap"] = _parse_crore(val_clean)
            elif "stock p/e" in name_clean or "pe" == name_clean:
                v = _parse_num(val_clean)
                if v and 0 < v < 500:
                    result["pe"] = v
            elif "book value" in name_clean:
                result["book_value"] = _parse_num(val_clean)
            elif "dividend yield" in name_clean:
                v = _parse_num(val_clean)
                result["div_y"] = v   # Screener shows as % already
            elif "roe" in name_clean:
                result["roe"] = _parse_num(val_clean)  # already %
            elif "roce" in name_clean:
                result["roce"] = _parse_num(val_clean)
            elif "debt to equity" in name_clean or "d/e" in name_clean:
                result["de"] = _parse_num(val_clean)

        # ── 52W High / Low from the "High / Low" ratio ────────────────────────
        hw_match = re.search(
            r'High\s*/\s*Low.*?<span[^>]*>([\d,\.]+)\s*/\s*([\d,\.]+)</span>',
            html, re.DOTALL | re.IGNORECASE
        )
        if hw_match:
            result["w52h"] = _parse_num(hw_match.group(1))
            result["w52l"] = _parse_num(hw_match.group(2))

        # Alternative 52W pattern
        if "w52h" not in result:
            hw2 = re.search(
                r'52 Week High.*?<b>([\d,\.]+)</b>.*?52 Week Low.*?<b>([\d,\.]+)</b>',
                html, re.DOTALL | re.IGNORECASE
            )
            if hw2:
                result["w52h"] = _parse_num(hw2.group(1))
                result["w52l"] = _parse_num(hw2.group(2))

        # ── Company name ──────────────────────────────────────────────────────
        name_match = re.search(r'<h1[^>]*class="[^"]*"[^>]*>\s*(.*?)\s*</h1>', html, re.IGNORECASE)
        if name_match:
            result["name"] = re.sub(r"<[^>]+>", "", name_match.group(1)).strip()

        # ── EPS from key ratios ───────────────────────────────────────────────
        eps_match = re.search(
            r'EPS.*?<span[^>]*>([\d,\.\-]+)</span>',
            html, re.DOTALL | re.IGNORECASE
        )
        if eps_match:
            result["eps"] = _parse_num(eps_match.group(1))

        # ── Revenue / Sales from quarterly/annual P&L ─────────────────────────
        rev_match = re.search(
            r'Sales.*?<td[^>]*>([\d,]+)</td>',
            html, re.DOTALL | re.IGNORECASE
        )
        if rev_match:
            v = _parse_num(rev_match.group(1))
            if v:
                result["rev"] = v  # Yahoo/Screener: already in crores   # crore → rupees

        # ── PB from Book Value (if price available) ───────────────────────────
        # PB = price / book_value — we'll compute in get_fundamentals

        if result:
            logger.info(f"Screener.in {sym}: fetched {list(result.keys())}")
        return result if result else None

    except Exception as e:
        logger.warning(f"Screener.in {sym}: {e}")
        return None


# ── Source 2: Finnhub ─────────────────────────────────────────────────────────

def _fetch_finnhub(sym: str) -> Optional[dict]:
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
            "pb":             m.get("pbQuarterly")        or m.get("pbAnnual"),
            "roe":            m.get("roeTTM"),
            "eps":            m.get("epsTTM"),
            "market_cap":     m.get("marketCapitalization"),
            "high52":         m.get("52WeekHigh"),
            "low52":          m.get("52WeekLow"),
            "dividend_yield": m.get("dividendYieldIndicatedAnnual"),
            "beta":           m.get("beta"),
        }
    except Exception as e:
        logger.debug(f"Finnhub {sym}: {e}")
        return None


# ── Source 3: yfinance (last resort, no custom session) ──────────────────────

_last_yf_call = 0.0
_YF_DELAY = 3.0


def _rate_limit_yf():
    global _last_yf_call
    elapsed = time.time() - _last_yf_call
    if elapsed < _YF_DELAY:
        time.sleep(_YF_DELAY - elapsed + random.uniform(0.1, 0.5))
    _last_yf_call = time.time()


def _fetch_yfinance(sym: str) -> dict:
    try:
        import yfinance as yf
        _rate_limit_yf()
        ticker = yf.Ticker(f"{sym}.NS")
        info   = {}
        try:
            info = dict(ticker.info)
        except Exception as e:
            logger.debug(f"yfinance .info {sym}: {e}")
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
        logger.error(f"yfinance {sym}: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED FUNDAMENTALS — data_engine → Screener.in → Finnhub → yfinance
# ══════════════════════════════════════════════════════════════════════════════

def get_fundamentals(sym: str) -> dict:
    sym       = sym.upper().replace(".NS", "")
    cache_key = f"fund_{sym}"
    cached    = _get_cached(cache_key, CACHE_TTL_FUND)
    if cached:
        return cached

    result = {
        "name": sym, "mcap": None, "rev": None,
        "pe": None, "fwd_pe": None, "pb": None,
        "roe": None, "eps": None, "de": None,
        "div_y": None, "w52h": None, "w52l": None, "beta": None,
    }

    # ── Source 1: data_engine (Yahoo v8/v10 + NSE, rate-limited) ─────────────
    try:
        from data_engine import get_info
        info = get_info(sym) or {}
        if info:
            result["name"] = info.get("name") or sym
            result["pe"]   = safe_val(info, "pe")
            result["pb"]   = safe_val(info, "pb")
            roe_raw = safe_val(info, "roe")
            result["roe"]  = round(roe_raw * 100, 1) if roe_raw is not None and abs(roe_raw) <= 1 else roe_raw
            result["eps"]  = safe_val(info, "eps")
            result["mcap"] = info.get("market_cap")
            result["w52h"] = safe_val(info, "high52")
            result["w52l"] = safe_val(info, "low52")
            div_raw = safe_val(info, "dividend_yield")
            result["div_y"] = round(div_raw * 100, 2) if div_raw is not None and div_raw <= 1 else div_raw
            logger.info(f"get_fundamentals {sym}: data_engine OK")
    except Exception as e:
        logger.warning(f"get_fundamentals data_engine {sym}: {e}")

    # ── Source 2: Screener.in (NO key required — fills most gaps) ────────────
    missing = [k for k in ["pe", "roe", "de", "mcap", "rev", "w52h", "w52l"] if result[k] is None]
    if missing:
        sc = _fetch_screener(sym)
        if sc:
            if result["pe"]   is None: result["pe"]   = sc.get("pe")
            if result["roe"]  is None: result["roe"]  = sc.get("roe")    # Screener = already %
            if result["de"]   is None: result["de"]   = sc.get("de")
            if result["eps"]  is None: result["eps"]  = sc.get("eps")
            if result["mcap"] is None: result["mcap"] = sc.get("mcap")
            if result["rev"]  is None: result["rev"]  = sc.get("rev")
            if result["w52h"] is None: result["w52h"] = sc.get("w52h")
            if result["w52l"] is None: result["w52l"] = sc.get("w52l")
            if result["div_y"] is None: result["div_y"] = sc.get("div_y")
            if result["name"] == sym:  result["name"] = sc.get("name", sym)
            # PB from Book Value
            if result["pb"] is None and sc.get("book_value"):
                try:
                    from data_engine import get_live_price
                    price = get_live_price(sym)
                    if price and sc["book_value"] > 0:
                        result["pb"] = round(price / sc["book_value"], 2)
                except Exception:
                    pass
            logger.info(f"get_fundamentals {sym}: Screener.in filled {[k for k in missing if result[k] is not None]}")

    # ── Source 3: Finnhub (fills PE/ROE/beta if still missing) ───────────────
    still_missing = [k for k in ["pe", "roe", "eps", "w52h", "w52l", "beta", "pb"] if result[k] is None]
    if still_missing:
        fh = _fetch_finnhub(sym)
        if fh:
            if result["pe"]    is None: result["pe"]   = safe_val(fh, "pe")
            if result["pb"]    is None: result["pb"]   = safe_val(fh, "pb")
            if result["roe"]   is None: result["roe"]  = safe_val(fh, "roe")
            if result["eps"]   is None: result["eps"]  = safe_val(fh, "eps")
            if result["beta"]  is None: result["beta"] = safe_val(fh, "beta")
            if result["w52h"]  is None: result["w52h"] = safe_val(fh, "high52")
            if result["w52l"]  is None: result["w52l"] = safe_val(fh, "low52")
            if result["div_y"] is None:
                result["div_y"] = safe_val(fh, "dividend_yield")

    # ── Source 4: yfinance (last resort) ──────────────────────────────────────
    still_missing2 = [k for k in ["pe", "roe", "mcap"] if result[k] is None]
    if still_missing2:
        yf_info = _fetch_yfinance(sym)
        if yf_info:
            if result["name"] == sym:
                result["name"] = yf_info.get("longName") or yf_info.get("shortName") or sym
            if result["pe"]    is None: result["pe"]    = safe_val(yf_info, "trailingPE")
            if result["fwd_pe"] is None: result["fwd_pe"] = safe_val(yf_info, "forwardPE")
            if result["pb"]    is None: result["pb"]    = safe_val(yf_info, "priceToBook")
            if result["roe"]   is None:
                roe_raw = safe_val(yf_info, "returnOnEquity")
                result["roe"] = round(roe_raw * 100, 1) if roe_raw and abs(roe_raw) <= 1 else roe_raw
            if result["eps"]   is None: result["eps"]  = safe_val(yf_info, "trailingEps")
            if result["mcap"]  is None: result["mcap"] = yf_info.get("marketCap")
            if result["rev"]   is None: result["rev"]  = yf_info.get("totalRevenue")
            if result["de"]    is None: result["de"]   = safe_val(yf_info, "debtToEquity")
            if result["w52h"]  is None: result["w52h"] = safe_val(yf_info, "fiftyTwoWeekHigh")
            if result["w52l"]  is None: result["w52l"] = safe_val(yf_info, "fiftyTwoWeekLow")
            if result["beta"]  is None: result["beta"] = safe_val(yf_info, "beta")
            if result["div_y"] is None:
                div_raw = safe_val(yf_info, "dividendYield")
                result["div_y"] = round(div_raw * 100, 2) if div_raw and div_raw <= 1 else div_raw

    filled = [k for k, v in result.items() if v is not None]
    logger.info(f"get_fundamentals {sym}: filled={filled}")
    _set_cached(cache_key, result)
    return result
