"""Nifty 500 symbol membership from the published NSE index CSV.

No stale hardcoded 250-stock list is silently represented as 500 stocks.
The conservative curated subset is only a labelled fallback when the index
constituent source is unavailable.
"""
from io import StringIO
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import tempfile

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


NSE_NIFTY100_URLS = (
    "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty100list.csv",
)
# The official Nifty 100 is Nifty 50 plus Nifty Next 50. Verify both exact,
# disjoint 50-member lists rather than substitute an arbitrary market-cap list.
NSE_COMPONENT_URLS = (
    "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_niftynext50list.csv",
)
MAX_SNAPSHOT_AGE = timedelta(hours=24)
IST = timezone(timedelta(hours=5, minutes=30))


def _valid_members(symbols, count):
    return (isinstance(symbols, (list, tuple)) and len(symbols) == count
            and all(isinstance(s, str) and re.fullmatch(r"[A-Z0-9&_-]{2,25}", s)
                    for s in symbols) and len(set(symbols)) == count)


def _fetch_members(url, count):
    response = requests.get(url, timeout=8, headers={
        "User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"})
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError("Constituent CSV exceeded maximum size")
    frame = pd.read_csv(StringIO(response.text))
    column = next((x for x in frame.columns if str(x).strip().lower() == "symbol"), None)
    if column is None or len(frame) != count:
        raise ValueError("Unexpected constituent count")
    symbols = [str(raw).strip().upper() for raw in frame[column]]
    if not _valid_members(symbols, count):
        raise ValueError("Invalid or duplicate constituents")
    return symbols


def _cache_path():
    # Explicitly configured public-only snapshot; no private portfolio data.
    value = os.environ.get("MSK_NIFTY100_SNAPSHOT", "").strip()
    return Path(value) if value else None


def _save_members(path, symbols, sources, now):
    if path is None:
        return
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                dir=path.parent, prefix=".nifty100-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"captured_at": now.isoformat(), "sources": list(sources),
                       "symbols": symbols}, stream)
        os.replace(temporary, path)
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _read_members(path, now):
    if path is None:
        return None
    try:
        if path.stat().st_size > 20_000:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        captured = datetime.fromisoformat(value["captured_at"])
        if captured.tzinfo is None or not 0 <= (now-captured).total_seconds() <= MAX_SNAPSHOT_AGE.total_seconds():
            return None
        # Same IST calendar date only: never carry membership over a rebalance day.
        if captured.astimezone(IST).date() != now.astimezone(IST).date():
            return None
        sources = value["sources"]
        allowed = ([url] for url in NSE_NIFTY100_URLS)
        if not (sources in allowed or sources == list(NSE_COMPONENT_URLS)):
            return None
        if not _valid_members(value["symbols"], 100):
            return None
        return value["symbols"], captured.astimezone(IST).isoformat()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def load_nifty100_universe():
    """Official membership, with explicitly dated same-day snapshot fallback."""
    path = _cache_path()
    now = datetime.now(IST)
    for url in NSE_NIFTY100_URLS:
        try:
            symbols = _fetch_members(url, 100)
            _save_members(path, symbols, [url], now)
            return [(f"{sym}.NS", sym) for sym in symbols], "NSE_NIFTY100_CONSTITUENTS_CSV"
        except (requests.RequestException, ValueError, KeyError, OSError,
                pd.errors.ParserError):
            continue
    try:
        symbols = _fetch_members(NSE_COMPONENT_URLS[0], 50) + _fetch_members(NSE_COMPONENT_URLS[1], 50)
        if _valid_members(symbols, 100):
            _save_members(path, symbols, NSE_COMPONENT_URLS, now)
            return [(f"{sym}.NS", sym) for sym in symbols], "NSE_NIFTY50_PLUS_NEXT50"
    except (requests.RequestException, ValueError, KeyError, OSError,
            pd.errors.ParserError):
        pass
    cached = _read_members(path, now)
    if cached is not None:
        symbols, captured = cached
        return [(f"{sym}.NS", sym) for sym in symbols], "NSE_NIFTY100_SNAPSHOT_" + captured
    raise RuntimeError("Verified Nifty 100 constituent list unavailable")
