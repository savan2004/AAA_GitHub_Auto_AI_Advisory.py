# fundamentals.py
import time
import logging
import random
from typing import Dict, Any, Optional

import requests
import yfinance as yf
from requests import Session
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ------------------ Local cache ------------------ #

_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_FUND = 6 * 60 * 60  # 6 hours


def _get_cached(key: str, ttl: int):
    d = _CACHE.get(key)
    if d and time.time() - d["ts"] < ttl:
        return d["val"]
    return None


def _set_cached(key: str, val: Any):
    _CACHE[key] = {"val": val, "ts": time.time()}


# ------------------ yfinance helpers ------------------ #

_last_yf_call = 0.0
_YF_DELAY = 2.0


def _rate_limit_yf():
    global _last_yf_call
    now = time.time()
    elapsed = now - _last_yf_call
    if elapsed < _YF_DELAY:
        time.sleep(_YF_DELAY - elapsed + random.uniform(0.1, 0.4))
    _last_yf_call = time.time()


def _create_yf_session():
    session = Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    uas = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    ]
    session.headers["User-Agent"] = random.choice(uas)
    return session


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


# ------------------ Other fundamentals API hook ------------------ #
# Keep this ready for future plug-in.

OTHER_API_BASE = ""  # e.g. "https://your-backend.example.com"


def _fetch_other_api(sym: str) -> Optional[dict]:
    """
    Optional second source for fundamentals (custom backend / NSE proxy).
    Disabled by default until OTHER_API_BASE is set.
    Expected shape:
    {
      "company_name": "...",
      "market_cap": 8.73e12,
      "pe_ratio": 32.1,
      "book_value": 940.5,
      "dividend_yield": 1.5,
      "roe": 24.3,
      "eps": 102.3,
      "revenue": 2200000000000
    }
    """
    if not OTHER_API_BASE:
        return None

    try:
        url = f"{OTHER_API_BASE}/stock/fundamentals"
        params = {"symbol": sym.upper()}
        r = requests.get(url, params=params, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"Other fundamentals API failed for {sym}: {e}")
        return None


# ------------------ Unified fundamentals fetch ------------------ #

def get_fundamentals(sym: str) -> dict:
    """
    Unified fundamentals:
    1) yfinance (info + fast_info)
    2) Optional other API to fill gaps
    Returns a normalized dict, safe to print in main.py.
    """
    sym = sym.upper().replace(".NS", "")
    cache_key = f"fund_{sym}"
    cached = _get_cached(cache_key, CACHE_TTL_FUND)
    if cached:
        return cached

    info: dict = {}

    # 1) yfinance
    try:
        _rate_limit_yf()
        ticker = yf.Ticker(f"{sym}.NS", session=_create_yf_session())
        try:
            info = dict(ticker.info)
        except Exception:
            info = {}

        try:
            fi = ticker.fast_info
            mapping = {
                "market_cap": "marketCap",
                "fifty_two_week_high": "fiftyTwoWeekHigh",
                "fifty_two_week_low": "fiftyTwoWeekLow",
                "last_price": "currentPrice",
                "previous_close": "previousClose",
            }
            for src, dst in mapping.items():
                val = getattr(fi, src, None)
                if val is not None:
                    info.setdefault(dst, val)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"yfinance fundamentals failed for {sym}: {e}")

    # 2) optional other API enrichment
    other = _fetch_other_api(sym) or {}
    if other:
        info.setdefault("longName", other.get("company_name"))
        info.setdefault("marketCap", other.get("market_cap"))
        info.setdefault("trailingPE", other.get("pe_ratio"))
        info.setdefault("priceToBook", other.get("book_value"))
        info.setdefault("dividendYield", other.get("dividend_yield"))
        info.setdefault("returnOnEquity", other.get("roe"))
        info.setdefault("trailingEps", other.get("eps"))
        info.setdefault("totalRevenue", other.get("revenue"))

    name = info.get("longName") or info.get("shortName") or sym

    mcap = info.get("marketCap")
    rev = info.get("totalRevenue")
    pe = safe_val(info, "trailingPE")
    fwd_pe = safe_val(info, "forwardPE")
    pb = safe_val(info, "priceToBook")
    roe = safe_val(info, "returnOnEquity", mul=100)
    eps = safe_val(info, "trailingEps")
    de = safe_val(info, "debtToEquity", "debtEquity")
    div_y = safe_val(info, "dividendYield", "dividendRate", mul=100)
    w52h = safe_val(info, "fiftyTwoWeekHigh")
    w52l = safe_val(info, "fiftyTwoWeekLow")
    beta = safe_val(info, "beta")

    result = {
        "name": name,
        "mcap": mcap,
        "rev": rev,
        "pe": pe,
        "fwd_pe": fwd_pe,
        "pb": pb,
        "roe": roe,
        "eps": eps,
        "de": de,
        "div_y": div_y,
        "w52h": w52h,
        "w52l": w52l,
        "beta": beta,
    }

    logger.info(f"get_fundamentals {sym}: keys="
                f"{[k for k, v in result.items() if v is not None]}")
    _set_cached(cache_key, result)
    return result
