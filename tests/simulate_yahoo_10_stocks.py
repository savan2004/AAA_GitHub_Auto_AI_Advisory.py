"""One-shot real-data Yahoo Finance simulator for TEN DIFFERENT NSE companies.

Run: python -u tests/simulate_yahoo_10_stocks.py
Uses actual repo-pinned yfinance / curl_cffi; no fixtures, no API keys,
no Telegram messages, no stock recommendations and no data-file writes.
Displays each value's availability. The Yahoo revenueGrowth field's reporting
period is provider-defined, NOT independently verified FY annual sales growth.
"""
from datetime import datetime, timezone
import json
import math
import time
import warnings

from yahoo_session_fundamentals import normalize_yfinance_info

# Real, distinct NSE issuers across finance, IT, energy, consumer, healthcare,
# industrials, vehicles and utilities. Selection is purely for feed diagnosis.
SYMBOLS = (
    "RELIANCE", "TCS", "INFY", "ITC", "LT",
    "SUNPHARMA", "HINDUNILVR", "MARUTI", "NTPC", "HDFCBANK",
)
REQUIRED_FIELDS = (
    "pe", "roe_pct", "revenue_growth_pct", "debt_equity_ratio", "market_cap",
)


def as_numeric(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError, OverflowError):
        return None


def probe_stock(symbol, yf_module):
    ticker = yf_module.Ticker(symbol + ".NS")
    outcome = {
        "symbol": symbol, "company": None,
        "asof": None, "historical_close_inr": None,
        "quote_inr": None, "pe": None, "roe_pct": None,
        "revenue_growth_pct": None, "debt_equity_ratio": None,
        "market_cap_inr": None, "currency": None,
        "price_ok": False, "fundamentals_ok": False,
        "complete_fundamentals": False,
        "missing": [], "errors": [],
    }
    try:
        # Yahoo's library handles cookie+crumb; this exercises the exact
        # fallback introduced in yahoo_session_fundamentals.py.
        info = ticker.get_info()
        clean = normalize_yfinance_info(info, symbol + ".NS")
        if clean is None:
            outcome["errors"].append("no normalized INR fundamentals")
        else:
            outcome["company"] = clean["name"]
            outcome["currency"] = clean["currency"]
            outcome["quote_inr"] = clean["price"]
            for field in ("pe", "roe_pct", "revenue_growth_pct", "debt_equity_ratio"):
                outcome[field] = clean[field]
            outcome["market_cap_inr"] = clean["market_cap"]
            outcome["fundamentals_ok"] = any(
                as_numeric(clean[field]) is not None for field in REQUIRED_FIELDS
            )
            outcome["missing"] = [
                name for name in REQUIRED_FIELDS
                if as_numeric(clean[name]) is None
            ]
            outcome["complete_fundamentals"] = not outcome["missing"]
    except Exception as error:
        outcome["errors"].append("fundamentals:" + type(error).__name__)

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            hist = ticker.history(period="5d", timeout=8)
        if hist is not None and not hist.empty:
            close = as_numeric(hist["Close"].iloc[-1])
            when = hist.index[-1]
            ccy = outcome["currency"]
            # Only report a numeric historical close when data has a positive
            # price; history itself has no audited financial-statement period.
            if close is not None and close > 0:
                outcome["historical_close_inr"] = round(close, 2)
                outcome["asof"] = when.strftime("%Y-%m-%d")
                outcome["price_ok"] = ccy in (None, "INR")
                if not outcome["price_ok"]:
                    outcome["errors"].append("history:non-INR currency")
        else:
            outcome["errors"].append("history:no candles")
    except Exception as error:
        outcome["errors"].append("history:" + type(error).__name__)
    return outcome


def main():
    import yfinance as yf
    import curl_cffi
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("MSK_YAHOO_TEN_REAL_COMPANIES_START")
    print("run_utc:", now)
    print("yfinance_version:", yf.__version__,
          "curl_cffi_version:", getattr(curl_cffi, "__version__", "unknown"))
    print("source: Yahoo Finance via yfinance authenticated cookie/crumb session")
    print("provider revenue growth: period UNVERIFIED; no real investment model selected")
    results = []
    for index, symbol in enumerate(SYMBOLS, start=1):
        result = probe_stock(symbol, yf)
        results.append(result)
        print("COMPANY_RESULT", index, json.dumps(result, sort_keys=True, ensure_ascii=False), flush=True)
        # Keep one company at a time to avoid request bursts.
        if index < len(SYMBOLS):
            time.sleep(1)
    prices = sum(row["price_ok"] for row in results)
    financials = sum(row["fundamentals_ok"] for row in results)
    full = sum(row["price_ok"] and row["complete_fundamentals"] for row in results)
    print("SUMMARY_JSON", json.dumps({
        "companies": len(results),
        "with_price_history": prices,
        "with_any_fundamental": financials,
        "with_all_five_fundamentals_and_history": full,
        "incomplete": len(results) - full,
        "missing_by_symbol": {
            row["symbol"]: row["missing"]
            for row in results if not (row["price_ok"] and row["complete_fundamentals"])
        },
    }, sort_keys=True))
    print("MSK_YAHOO_TEN_REAL_COMPANIES_END")
    # Do not make CI green contingent on external Yahoo availability:
    # actual evidence counts are printed rather than a dishonest pass flag.
    assert len({r["symbol"] for r in results}) == 10, "The sample must have 10 unique NSE issuers."


if __name__ == "__main__":
    main()
