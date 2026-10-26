"""Read-only, bounded Yahoo Finance diagnostic. Uses no private credentials.

It identifies endpoint-specific 401 vs chart-data availability and whether the
installed yfinance cookie/crumb manager can retrieve Indian-stock fundamentals.
Never logs cookies, crumbs, headers or raw exception/request data.
"""
import json
import sys
import time
from urllib.parse import quote

SYMBOL = "INFY.NS"


def direct_probe():
    import requests
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    for category, url in [
        ("chart-v8", f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}?range=5d&interval=1d"),
        ("fundamentals-v10-raw", f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{SYMBOL}?modules=financialData,summaryDetail,price"),
    ]:
        try:
            r = requests.get(url, headers=headers, timeout=(4, 8))
            print(category, "http_status=", r.status_code, "content_type=", r.headers.get("Content-Type", "?"))
            if category == "chart-v8" and r.ok:
                obj = r.json()
                row = (obj.get("chart", {}).get("result") or [{}])[0]
                print("chart-v8", "data_points=", len(row.get("timestamp") or []),
                      "currency=", row.get("meta", {}).get("currency", "?"))
        except Exception as error:
            print(category, "error_class=", type(error).__name__)


def yf_probe():
    try:
        import yfinance as yf
        print("yfinance_version=", yf.__version__)
        import curl_cffi
        print("curl_cffi_version=", getattr(curl_cffi, "__version__", "unknown"))
    except Exception as error:
        print("yfinance_load_error_class=", type(error).__name__)
        return
    ticker = yf.Ticker(SYMBOL)
    try:
        info = ticker.get_info()
        if not isinstance(info, dict):
            print("yf_get_info", "invalid_type=", type(info).__name__)
        else:
            fields = ["trailingPE", "returnOnEquity", "revenueGrowth",
                      "debtToEquity", "marketCap", "sector", "longName"]
            found = {field: info.get(field) is not None for field in fields}
            print("yf_get_info", "count=", len(info), "field_available=", json.dumps(found))
    except Exception as error:
        print("yf_get_info", "error_class=", type(error).__name__)
    try:
        hist = ticker.history(period="5d", timeout=8, raise_errors=True)
        print("yf_history", "rows=", 0 if hist is None else len(hist))
    except Exception as error:
        print("yf_history", "error_class=", type(error).__name__)


if __name__ == "__main__":
    print("YAHOO_DIAGNOSTIC_START", "symbol=", SYMBOL)
    direct_probe()
    yf_probe()
    print("YAHOO_DIAGNOSTIC_END")
