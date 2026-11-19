"""Nifty 500 symbol membership from the published NSE index CSV.

No stale hardcoded 250-stock list is silently represented as 500 stocks.
The conservative curated subset is only a labelled fallback when the index
constituent source is unavailable.
"""
from io import StringIO
import re

import pandas as pd
import requests

NSE_LIST_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"


def load_nifty500_universe():
    try:
        response = requests.get(
            NSE_LIST_URL, timeout=12, headers={"User-Agent": "Mozilla/5.0",
            "Accept": "text/csv,*/*"})
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError("Nifty constituent CSV exceeded maximum size")
        frame = pd.read_csv(StringIO(response.text))
        column = next((x for x in frame.columns
                       if str(x).strip().lower() == "symbol"), None)
        if column is None or not 450 <= len(frame) <= 550:
            raise ValueError("Nifty 500 index constituent coverage unavailable")
        distinct = {}
        for raw in frame[column].dropna():
            sym = str(raw).strip().upper()
            if re.fullmatch(r"[A-Z0-9&_-]{2,25}", sym):
                distinct.setdefault(sym, (f"{sym}.NS", sym))
        if not 450 <= len(distinct) <= 550:
            raise ValueError("Nifty 500 constituent symbol validation failed")
        return list(distinct.values()), "NSE_NIFTY500_CONSTITUENTS_CSV"
    except (requests.RequestException, ValueError, KeyError, OSError,
            pd.errors.ParserError):
        from nifty500_collector import SECTOR_STOCKS
        symbols = sorted({sym for group in SECTOR_STOCKS.values() for sym in group
                          if re.fullmatch(r"[A-Z0-9&_-]{2,25}", sym)})
        return [(f"{sym}.NS", sym) for sym in symbols], "CURATED_PARTIAL_UNIVERSE_FALLBACK"
