"""Independent NSE daily OHLCV fallback for the MSK research chart runner.

No Telegram credentials, database, user holdings, or synthetic bars. Called
only for two symbols from the original bounded auto-chart sample when Yahoo
batch and serial downloads both lack completed-session evidence.
"""
from datetime import date, timedelta
from io import StringIO
import re

import pandas as pd
import requests


def official_nse_chart_history(yahoo_symbol, session_key):
    """Return exchange-labelled daily bars for the exact NSE EQ symbol or None."""
    if not isinstance(yahoo_symbol, str) or not re.fullmatch(
            r"[A-Z0-9&_-]{2,25}\.NS", yahoo_symbol):
        return None
    try:
        session = date.fromisoformat(str(session_key))
        symbol = yahoo_symbol.removesuffix(".NS")
        start = session - timedelta(days=385)
        url = ("https://www.nseindia.com/api/historical/cm/equity"
               f"?symbol={symbol}&series=[%22EQ%22]"
               f"&from={start:%d-%m-%Y}&to={session:%d-%m-%Y}&csv=true")
        browser = requests.Session()
        browser.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                          "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/csv,application/json,*/*",
            "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=" + symbol,
        })
        try:
            browser.get("https://www.nseindia.com/", timeout=4)
        except requests.RequestException:
            pass  # A required session cookie will still be checked by the CSV GET.
        response = browser.get(url, timeout=12)
        if response.status_code != 200 or len(response.content) > 3_000_000:
            return None
        frame = pd.read_csv(StringIO(response.text), low_memory=False)
        frame.columns = [str(c).strip().upper() for c in frame.columns]
        # Do not silently accept an equity CSV without provider-reported identity.
        ticker_column = next(
            (c for c in ("SYMBOL", "CH_SYMBOL") if c in frame.columns), None)
        if ticker_column is None or frame.empty:
            return None
        identities = frame[ticker_column].astype(str).str.strip().str.upper()
        if not identities.eq(symbol).all():
            return None
        if "SERIES" in frame.columns and not frame["SERIES"].astype(str).str.strip().eq("EQ").all():
            return None
        mapping = {
            "DATE1": "Date", "CH_TIMESTAMP": "Date", "DATE": "Date",
            "OPEN PRICE": "Open", "CH_OPENING_PRICE": "Open",
            "HIGH PRICE": "High", "CH_TRADE_HIGH_PRICE": "High",
            "LOW PRICE": "Low", "CH_TRADE_LOW_PRICE": "Low",
            "CLOSE PRICE": "Close", "CH_CLOSING_PRICE": "Close",
            "TTL TRD QNTY": "Volume", "CH_TOT_TRADED_QTY": "Volume",
        }
        output = {}
        for raw, target in mapping.items():
            if raw in frame.columns and target not in output:
                output[target] = frame[raw]
        if set(output) != {"Date", "Open", "High", "Low", "Close", "Volume"}:
            return None
        result = pd.DataFrame(output)
        result["Date"] = pd.to_datetime(result["Date"], dayfirst=True, errors="coerce")
        for column in ("Open", "High", "Low", "Close", "Volume"):
            result[column] = pd.to_numeric(
                result[column].astype(str).str.replace(",", "", regex=False),
                errors="coerce")
        result = result.dropna().set_index("Date").sort_index()
        return result if len(result) >= 55 else None
    except (requests.RequestException, ValueError, TypeError, KeyError, OSError,
            pd.errors.ParserError):
        return None
