"""Session-aware Yahoo Finance fundamentals for MSK data_engine.

yfinance manages Yahoo's cookie + crumb. Raw /v10/quoteSummary often 401s.
Output preserves the existing data_engine field names and makes no claim that
retrieval time equals fiscal statement date. No credentials are logged.
Yahoo/yfinance data-use terms must be reviewed before public redistribution.
"""
import logging
import math
import re
import time

logger = logging.getLogger(__name__)

_NSE_SYMBOL = re.compile(r"^[A-Z0-9&-]{1,25}\.NS$")


def _finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _positive(value):
    f = _finite(value)
    return f if f is not None and f > 0 else None


def normalize_yfinance_info(info, symbol, fetched_at=None):
    """Normalize units strictly; keep fiscal-year labels absent if unknown."""
    if not _NSE_SYMBOL.fullmatch(symbol) or not isinstance(info, dict):
        return None
    currency = info.get("currency") or info.get("financialCurrency")
    if currency != "INR":
        return None
    roe_raw = _finite(info.get("returnOnEquity"))  # provider decimal: 0.20 = 20%
    growth_raw = _finite(info.get("revenueGrowth")) # provider decimal, period unspecified
    de_pct = _finite(info.get("debtToEquity"))     # Yahoo percent: 25 = 0.25 ratio
    stamp = time.time() if fetched_at is None else float(fetched_at)
    if not math.isfinite(stamp):
        return None
    result = {
        "name": str(info.get("longName") or info.get("shortName") or symbol)[:120],
        "price": _positive(info.get("regularMarketPrice") or info.get("currentPrice")),
        "prev_close": _positive(info.get("regularMarketPreviousClose") or info.get("previousClose")),
        "pe": _positive(info.get("trailingPE")),
        "pb": _positive(info.get("priceToBook")),
        "roe": roe_raw,
        "roe_pct": roe_raw * 100 if roe_raw is not None else None,
        "eps": _finite(info.get("trailingEps")),
        "dividend_yield": _finite(info.get("dividendYield")),
        "market_cap": _positive(info.get("marketCap")),
        "totalRevenue": _positive(info.get("totalRevenue")),
        "debtToEquity": de_pct,
        "debt_equity_ratio": de_pct * 0.01 if de_pct is not None and de_pct >= 0 else None,
        "revenueGrowth": growth_raw,
        "revenue_growth_pct": growth_raw * 100 if growth_raw is not None else None,
        "high52": _positive(info.get("fiftyTwoWeekHigh")),
        "low52": _positive(info.get("fiftyTwoWeekLow")),
        "beta": _finite(info.get("beta")),
        "forwardPE": _positive(info.get("forwardPE")),
        "earningsGrowth": _finite(info.get("earningsGrowth")),
        "avg_volume": _positive(info.get("averageVolume")),
        "sector": str(info.get("sector") or "").strip() or None,
        "currency": "INR",
        "fetched_at": stamp,
        "provider": "Yahoo Finance (yfinance cookie/session)",
        "provider_url": f"https://finance.yahoo.com/quote/{symbol}/",
        "financial_period": "Yahoo provider-defined; NOT independently verified annual YoY",
    }
    # No accepted quote/fundamental signal means treating this as failure.
    if result["pe"] is None and result["market_cap"] is None and result["roe"] is None:
        return None
    return result


def get_yahoo_session_fundamentals(symbol, ticker_factory=None):
    if not isinstance(symbol, str) or not _NSE_SYMBOL.fullmatch(symbol):
        return None
    try:
        if ticker_factory is None:
            import yfinance as yf
            ticker_factory = yf.Ticker
        ticker = ticker_factory(symbol)
        info = ticker.get_info() if hasattr(ticker, "get_info") else ticker.info
        return normalize_yfinance_info(info, symbol)
    except Exception as exc:
        # Some yfinance exceptions include the request URL or session details:
        # report only the exception class, never the raw object/string.
        logger.warning("Yahoo session fundamentals failed for %s: %s",
                       symbol, type(exc).__name__)
        return None
