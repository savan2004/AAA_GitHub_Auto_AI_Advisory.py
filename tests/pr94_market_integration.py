"""Opt-in real Indian equity-data smoke check; NEVER run as a unit test.

Uses provider HTTP requests, not mocks. The script writes real coverage evidence.
Requires runner internet access. Provider refusals stay failures, not made-up prices.
"""
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import data_engine as engine
import market_data as bulk

# Representative NSE equities; not a claim of live index membership.
SYMBOLS = (
    "RELIANCE HDFCBANK ICICIBANK INFY TCS SBIN ITC BHARTIARTL LT AXISBANK "
    "KOTAKBANK HINDUNILVR MARUTI SUNPHARMA NTPC POWERGRID ULTRACEMCO ASIANPAINT "
    "HCLTECH TECHM WIPRO TATACONSUM NESTLEIND JSWSTEEL TATASTEEL COALINDIA "
    "ONGC BAJFINANCE BAJAJFINSV TITAN APOLLOHOSP CIPLA DRREDDY EICHERMOT "
    "HEROMOTOCO INDUSINDBK M&M SHRIRAMFIN GRASIM HINDALCO TRENT BEL "
    "ADANIENT ADANIPORTS ETERNAL SBILIFE HDFCLIFE DIVISLAB BRITANNIA BPCL"
).split()
INDEXES = ("^NSEI", "^NSEBANK")
FIELDS = ("Open", "High", "Low", "Close", "Volume")
MAX_CALENDAR_AGE_DAYS = 7


def validate(frame):
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return False, "missing", None
    if any(col not in frame.columns for col in FIELDS):
        return False, "missing_ohlcv_columns", None
    f = frame[list(FIELDS)].apply(pd.to_numeric, errors="coerce")
    if not all(math.isfinite(float(n)) for n in f.to_numpy().ravel()):
        return False, "nonfinite_ohlcv", None
    if not ((f[["Open", "High", "Low", "Close"]] > 0).all().all()
            and (f["Volume"] >= 0).all()):
        return False, "invalid_ohlcv", None
    if not ((f["High"] >= f[["Open", "Low", "Close"]].max(axis=1)).all()
            and (f["Low"] <= f[["Open", "High", "Close"]].min(axis=1)).all()):
        return False, "inconsistent_candles", None
    try:
        idx = pd.to_datetime(frame.index, errors="raise", utc=True)
        last = idx.max().date()
        age = (datetime.now(timezone.utc).date() - last).days
    except (TypeError, ValueError, OverflowError, AttributeError):
        return False, "invalid_timestamp", None
    if age < 0:
        return False, "future_candle", last.isoformat()
    if age > MAX_CALENDAR_AGE_DAYS:
        return False, "stale", last.isoformat()
    return True, "ok", last.isoformat()


def main():
    if os.getenv("MSK_ALLOW_EXTERNAL_MARKET_TEST") != "YES":
        raise SystemExit("External test not opted in; set MSK_ALLOW_EXTERNAL_MARKET_TEST=YES")
    if len(SYMBOLS) != 50:
        raise SystemExit(f"Incorrect fixture universe: {len(SYMBOLS)}")
    logging.basicConfig(level=logging.WARNING)
    sources = {}
    original_bulk = bulk._download_chunk
    original_recovery = bulk._recover_missing_symbol

    def tracked_bulk(tickers, period, retries=2, adjusted=False):
        result = original_bulk(tickers, period, retries=retries, adjusted=adjusted)
        for ticker in result:
            sources[bulk.from_yahoo(ticker)] = "yfinance_bulk"
        return result

    def tracked_recovery(symbol, period):
        result = original_recovery(symbol, period)
        if result is not None and not result.empty:
            sources.setdefault(symbol, "fallback_unknown")
        return result

    def record_provider(name, fn, symbol_transform):
        def wrapped(symbol, *args, **kwargs):
            result = fn(symbol, *args, **kwargs)
            if isinstance(result, pd.DataFrame) and not result.empty:
                sources[symbol_transform(symbol)] = name
            return result
        return wrapped

    started = time.monotonic()
    with patch.object(bulk, "_download_chunk", tracked_bulk), \
         patch.object(bulk, "_recover_missing_symbol", tracked_recovery), \
         patch.object(engine, "_nse_hist",
                      record_provider("NSE_historical", engine._nse_hist, str.upper)), \
         patch.object(engine, "_yahoo_v8_hist",
                      record_provider("Yahoo_direct_chart", engine._yahoo_v8_hist,
                                      lambda s: bulk.from_yahoo(s))), \
         patch.object(engine, "_stooq_hist",
                      record_provider("Stooq", engine._stooq_hist, bulk.from_yahoo)):
        results = bulk.bulk_history(SYMBOLS, period="1mo", chunk_size=25,
                                    retries=0, cache_ttl=0)
        index_results = {}
        for index in INDEXES:
            # Direct index API: never append ".NS" to the ^-prefixed symbol.
            index_results[index] = engine.get_hist(index, period="1mo")

    records = []
    for symbol in SYMBOLS:
        good, reason, latest = validate(results.get(symbol))
        records.append({"symbol": symbol, "asset": "NSE_equity", "ok": good,
                        "reason": reason, "last_candle": latest,
                        "source": sources.get(symbol, "unavailable")})
    for symbol in INDEXES:
        good, reason, latest = validate(index_results.get(symbol))
        records.append({"symbol": symbol, "asset": "Indian_index", "ok": good,
                        "reason": reason, "last_candle": latest,
                        "source": sources.get(symbol, "unavailable")})
    equities = [x for x in records if x["asset"] == "NSE_equity"]
    success = sum(x["ok"] for x in equities)
    indexes_ok = sum(x["ok"] for x in records if x["asset"] == "Indian_index")
    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "test_kind": "real_external_market_provider_requests",
        "requested_equities": len(equities),
        "returned_fresh_valid_equities": success,
        "equity_coverage_percent": round(100 * success / len(equities), 2),
        "requested_indexes": len(INDEXES),
        "returned_fresh_valid_indexes": indexes_ok,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "source_counts": {
            key: sum(x["source"] == key and x["ok"] for x in records)
            for key in sorted(set(x["source"] for x in records))
        },
        "records": records,
    }
    output = Path(os.getenv("MSK_MARKET_TEST_REPORT", "pr94-market-coverage.json"))
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    min_coverage = float(os.getenv("MSK_MIN_COVERAGE_PERCENT", "70"))
    if success / len(equities) * 100 < min_coverage:
        print("FAIL: actual market coverage below acceptance threshold", file=sys.stderr)
        return 1
    if indexes_ok != len(INDEXES):
        print("FAIL: at least one requested index did not return valid recent candles",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
