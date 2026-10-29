"""Current official NSE index membership for MSK Top-5 market-cap buttons.

Never silently substitute arbitrary tickers when membership is unavailable.
The source is the official NSE constituent CSV, not a third-party watchlist.
"""
import csv
import io
import logging
import re
import threading
import time
from datetime import datetime, timezone, timedelta

import requests

LOG = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
INDEXES = {
    "conservative": {
        "name": "Nifty 100", "cap": "Large Cap", "count": 100, "tolerance": 5,
        "filename": "ind_nifty100list.csv",
    },
    "moderate": {
        "name": "Nifty Midcap 150", "cap": "Mid Cap", "count": 150, "tolerance": 10,
        "filename": "ind_niftymidcap150list.csv",
    },
    "aggressive": {
        "name": "Nifty Smallcap 250", "cap": "Small Cap", "count": 250, "tolerance": 15,
        "filename": "ind_niftysmallcap250list.csv",
    },
}
_HOSTS = ("https://nsearchives.nseindia.com/content/indices/",
          "https://archives.nseindia.com/content/indices/")
_TTL = 4 * 60 * 60
_MAX_STALE = 7 * 24 * 60 * 60
_LOCK = threading.Lock()
_CACHE = {}


def parse_constituents(content, profile):
    """Validate the CSV header, symbol syntax, unique count and series."""
    meta = INDEXES[profile]
    if not isinstance(content, (str, bytes)):
        raise ValueError("invalid CSV payload")
    raw = content.decode("utf-8-sig") if isinstance(content, bytes) else content.lstrip("\ufeff")
    if len(raw) > 2_000_000 or "<html" in raw[:600].lower():
        raise ValueError("NSE responded with HTML or an excessive payload")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        raise ValueError("NSE constituent CSV missing header")
    columns = {key.strip().casefold(): key for key in reader.fieldnames if key}
    symbol_col = columns.get("symbol")
    if not symbol_col:
        raise ValueError("NSE constituent CSV missing Symbol column")
    industry_col = columns.get("industry")
    series_col = columns.get("series")
    symbols, industries = [], {}
    for row in reader:
        sym = str(row.get(symbol_col) or "").strip().upper()
        if series_col and str(row.get(series_col) or "").strip().upper() not in ("EQ", ""):
            continue
        if not re.fullmatch(r"[A-Z0-9&_.-]{1,30}", sym):
            continue
        if sym in industries:
            continue
        symbols.append(sym)
        industries[sym] = (str(row.get(industry_col) or "").strip() if industry_col else "") or "Unclassified"
    if abs(len(symbols) - meta["count"]) > meta["tolerance"]:
        raise ValueError("NSE constituent count outside expected range")
    return tuple(symbols), industries


def get_index_universe(profile):
    """Fetch current official members; optionally reuse a recent validated copy."""
    if profile not in INDEXES:
        raise ValueError("Unknown investment profile")
    now = time.time()
    with _LOCK:
        entry = _CACHE.get(profile)
        if entry and now - entry["ts"] < _TTL:
            return dict(entry["value"], cached=True)
    meta = INDEXES[profile]
    for host in _HOSTS:
        try:
            res = requests.get(
                host + meta["filename"], timeout=(5, 12),
                headers={"User-Agent": "Mozilla/5.0 (compatible; MSKIndexReader/1.0)",
                         "Accept": "text/csv,text/plain,*/*"},
            )
            res.raise_for_status()
            symbols, industries = parse_constituents(res.content, profile)
            value = {
                "name": meta["name"], "cap": meta["cap"], "symbols": symbols,
                "industries": industries,
                "asof": datetime.now(IST).strftime("%d-%b-%Y %H:%M IST"),
                "source": "Official NSE constituent CSV",
            }
            with _LOCK:
                _CACHE[profile] = {"ts": time.time(), "value": value}
            LOG.info("MSK index membership %s: %d validated constituents", profile, len(symbols))
            return dict(value, cached=False)
        except (requests.RequestException, ValueError, UnicodeError, csv.Error) as exc:
            LOG.warning("MSK index membership %s provider unavailable (%s)",
                        profile, type(exc).__name__)
    with _LOCK:
        recent = _CACHE.get(profile)
        if recent and now - recent["ts"] <= _MAX_STALE:
            return dict(recent["value"], cached=True, stale=True)
    raise RuntimeError("Official NSE index membership unavailable; no index stock recommendations issued")
