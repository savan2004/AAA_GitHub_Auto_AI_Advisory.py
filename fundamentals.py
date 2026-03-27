# fundamentals.py
import time
import logging
import random
from requests import Session
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import yfinance as yf

logger = logging.getLogger(__name__)

_last_yf_call = 0
_yf_call_delay = 2.0

def _rate_limit_yf():
    global _last_yf_call
    now = time.time()
    elapsed = now - _last_yf_call
    if elapsed < _yf_call_delay:
        time.sleep(_yf_call_delay - elapsed + random.uniform(0.1, 0.5))
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
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    ]
    session.headers["User-Agent"] = random.choice(user_agents)
    return session

_cache = {}
_CACHE_TTL_FUND = 21600  # 6h

def _get_cached(key: str, ttl: int):
    d = _cache.get(key)
    if d and time.time() - d["ts"] < ttl:
        return d["val"]
    return None

def _set_cached(key: str, val):
    _cache[key] = {"val": val, "ts": time.time()}

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

def get_fundamentals(sym: str) -> dict:
    """
    Unified fundamentals fetch:
    1) yfinance info/fast_info
    2) (placeholder) other API (NSE, etc.) — can be added later
    Returns a dict ready for printing.
    """
    sym = sym.upper().replace(".NS", "")
    key = f"fund_{sym}"
    cached = _get_cached(key, _CACHE_TTL_FUND)
    if cached:
        return cached

    info = {}
    try:
        _rate_limit_yf()
        ticker = yf.Ticker(f"{sym}.NS", session=_create_yf_session())
        try:
            info = dict(ticker.info)
        except Exception:
            pass

        # fast_info fallback for some keys
        try:
            fi = ticker.fast_info
            mapping = {
                "market_cap": "marketCap",
                "fifty_two_week_high": "fiftyTwoWeekHigh",
                "fifty_two_week_low": "fiftyTwoWeekLow",
                "last_price": "currentPrice",
                "previous_close": "previousClose",
            }
            for src_attr, dst_key in mapping.items():
                val = getattr(fi, src_attr, None)
                if val is not None:
                    info.setdefault(dst_key, val)
        except Exception:
            pass

        # HERE you can plug a second API later and update info[...]

        name = info.get("longName") or info.get("shortName") or sym
        pe = safe_val(info, "trailingPE")
        fwd_pe = safe_val(info, "forwardPE")
        roe = safe_val(info, "returnOnEquity", mul=100)
        eps = safe_val(info, "trailingEps")
        mcap = info.get("marketCap")
        rev = info.get("totalRevenue")
        de = safe_val(info, "debtToEquity", "debtEquity")
        div_y = safe_val(info, "dividendYield", "dividendRate", mul=100)
        w52h = safe_val(info, "fiftyTwoWeekHigh")
        w52l = safe_val(info, "fiftyTwoWeekLow")
        beta = safe_val(info, "beta")
        pb = safe_val(info, "priceToBook")

        data = {
            "name": name,
            "pe": pe,
            "fwd_pe": fwd_pe,
            "roe": roe,
            "eps": eps,
            "mcap": mcap,
            "rev": rev,
            "de": de,
            "div_y": div_y,
            "w52h": w52h,
            "w52l": w52l,
            "beta": beta,
            "pb": pb,
        }
        _set_cached(key, data)
        return data
    except Exception as e:
        logger.error(f"get_fundamentals {sym}: {e}")
        return {}
