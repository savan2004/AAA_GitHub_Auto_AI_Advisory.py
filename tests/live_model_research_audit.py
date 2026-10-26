"""Bounded REAL-DATA research audit for MSK model portfolios.

Runs the actual research_upgrade.evaluate/rank functions on fresh public Yahoo
responses. No mock company facts, no trade, no Telegram, no secret, no write.
Failure to obtain price or fundamentals is a printed INCOMPLETE result, NEVER
a fabricated pass. Designed for a single safe GitHub Actions diagnosis.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import requests

from research_upgrade import evaluate, rank
from model_portfolios import select_model

SAMPLE = (
    ("RELIANCE", "Reliance Industries", "Energy"),
    ("INFY", "Infosys", "Technology"),
    ("ITC", "ITC", "Consumer"),
    ("SUNPHARMA", "Sun Pharmaceutical Industries", "Healthcare"),
    ("LT", "Larsen & Toubro", "Industrials"),
)
HEADERS = {"User-Agent": "Mozilla/5.0 (MSK model portfolio research data audit)",
           "Accept": "application/json"}
REQUEST_TIMEOUT = (4, 7)


def raw(d, name):
    x = d.get(name) if isinstance(d, dict) else None
    return x.get("raw") if isinstance(x, dict) else x


def request_json(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"
        return response.json(), "200"
    except requests.Timeout:
        return None, "timeout"
    except (requests.RequestException, ValueError):
        return None, "unavailable or invalid JSON"


def finite(v):
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def build_history(obj):
    try:
        chart = obj["chart"]["result"][0]
        timestamps = chart["timestamp"]
        quote = chart["indicators"]["quote"][0]
        candles = []
        for i, stamp in enumerate(timestamps):
            close = finite(quote["close"][i])
            volume = finite(quote["volume"][i])
            if close is not None and volume is not None and close > 0 and volume >= 0:
                date = datetime.fromtimestamp(stamp, timezone.utc).date()
                candles.append((date, close, volume))
        if not candles:
            return None, chart.get("meta", {})
        frame = pd.DataFrame(
            [{"Date": pd.Timestamp(day), "Close": close, "Volume": volume}
             for day, close, volume in candles]
        ).set_index("Date")
        return frame, chart.get("meta", {})
    except (TypeError, IndexError, KeyError, ValueError):
        return None, {}


def make_fundamentals(quote_response, meta, sector, fetched):
    try:
        root = quote_response["quoteSummary"]["result"][0]
    except (TypeError, IndexError, KeyError):
        root = {}
    sd = root.get("summaryDetail") or {}
    ks = root.get("defaultKeyStatistics") or {}
    fd = root.get("financialData") or {}
    price = root.get("price") or {}
    profile = root.get("summaryProfile") or {}
    roe = finite(raw(fd, "returnOnEquity"))
    growth = finite(raw(fd, "revenueGrowth"))
    de = finite(raw(fd, "debtToEquity"))
    cap = finite(raw(price, "marketCap"))
    return {
        "name": meta.get("longName") or meta.get("shortName") or "?",
        "sector": profile.get("sector") or sector,  # sector fallback from local curated list
        "fetched_at": fetched,
        "pe": finite(raw(sd, "trailingPE") or raw(price, "trailingPE")),
        "roe_pct": roe * 100 if roe is not None else None,
        "revenue_growth_pct": growth * 100 if growth is not None else None,
        "debt_equity_ratio": de * 0.01 if de is not None else None,
        "market_cap": cap,
        "sources": {
            "quoteSummary": "Yahoo Finance quoteSummary v10, live HTTP",
            "history": "Yahoo Finance chart v8, live HTTP",
            "sector": "provider" if profile.get("sector") else "repository sector map",
        },
    }


def fmt(value, places=2):
    x = finite(value)
    return "Unavailable" if x is None else f"{x:,.{places}f}"


def audit():
    now = time.time()
    rows = []
    print("=== MSK REAL-STOCK RESEARCH AUDIT ===")
    print("UTC checked:", datetime.fromtimestamp(now, timezone.utc).isoformat())
    print("Source: Yahoo Finance public chart v8 + quoteSummary v10 (no credentials)")
    print("Real NSE symbols; NOT a ten-stock investment portfolio.")
    for symbol, description, sector in SAMPLE:
        yahoo = symbol + ".NS"
        histurl = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo}?range=6mo&interval=1d"
        fundurl = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
                   f"{yahoo}?modules=summaryDetail,defaultKeyStatistics,financialData,price,summaryProfile")
        history_obj, histstatus = request_json(histurl)
        funda_obj, fundstatus = request_json(fundurl)
        frame, meta = build_history(history_obj) if history_obj is not None else (None, {})
        # fetched_at records time of THIS HTTP request, not financial statement age.
        fetched_at = time.time()
        info = make_fundamentals(funda_obj, meta, sector, fetched_at) if funda_obj else {
            "sector": sector, "fetched_at": fetched_at}
        row, reason = evaluate(symbol, info, frame, now=fetched_at)
        if row:
            rows.append(row)
        print("\nSTOCK:", description, "(", symbol, ")")
        print("CHART_SOURCE:", histurl, "STATUS:", histstatus)
        print("FUND_SOURCE:", fundurl, "STATUS:", fundstatus)
        print("SECTOR:", info.get("sector"), "SOURCE:", info.get("sources", {}).get("sector", "repository map"))
        print("HISTORY_ASOF:", row["asof"] if row else (
            str(frame.index[-1].date()) if frame is not None and not frame.empty else "Unavailable"))
        print("CLOSE_INR:", fmt(row["price"] if row else (
            frame["Close"].iloc[-1] if frame is not None and not frame.empty else None)))
        print("ROE_PCT:", fmt(info.get("roe_pct")))
        print("REVENUE_GROWTH_PCT:", fmt(info.get("revenue_growth_pct")))
        print("DEBT_EQUITY:", fmt(info.get("debt_equity_ratio")))
        print("PE:", fmt(info.get("pe")))
        print("MARKET_CAP_RUPEES:", fmt(info.get("market_cap"), 0))
        print("RESEARCH_RESULT:", "VALIDATED" if row else "INCOMPLETE / REJECTED")
        print("REASON:", reason or "All available evidence fields validated")
        if row:
            for profile in ("conservative", "moderate", "aggressive"):
                eligible = bool(rank([row], profile))
                print("PROFILE", profile.upper(), "PASS" if eligible else "FAIL",
                      "score=" + str(rank([row], profile)[0]["score"]) if eligible else "threshold mismatch")

    for profile in ("conservative", "moderate", "aggressive"):
        model = select_model(rows, profile, now=time.time())
        print("\nFINAL_MODEL", profile.upper(),
              "status=", model["status"], "count=", model["qualified"],
              "publishable=", bool(model["holdings"]))
    print("\nREAL_DATA_AUDIT_COMPLETE: Actual public responses inspected; "
          "missing data is never invented.")
    print("NOTE: This checks data availability, NOT financial accuracy against audited filings.")
    print("NOTE: No 10-stock result can be proven from a five-stock audit.")


if __name__ == "__main__":
    audit()
