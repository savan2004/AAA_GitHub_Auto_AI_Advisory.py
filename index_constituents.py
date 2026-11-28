"""Current official NSE index membership for MSK Top-5 market-cap buttons.

Never silently substitute arbitrary tickers when membership is unavailable.
The source is the official NSE constituent CSV, not a third-party watchlist.
"""
import csv
import io
import json
import os
from pathlib import Path
import tempfile
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
        "name": "Nifty Midcap 100", "cap": "Mid Cap", "count": 100, "tolerance": 5,
        "filename": "ind_niftymidcap100list.csv",
    },
    "aggressive": {
        "name": "Nifty Smallcap 100", "cap": "Small Cap", "count": 100, "tolerance": 5,
        "filename": "ind_niftysmallcap100list.csv",
    },
}
_HOSTS = ("https://nsearchives.nseindia.com/content/indices/",
          "https://archives.nseindia.com/content/indices/")
_OFFICIAL_MIRROR = "https://www.niftyindices.com/IndexConstituent/"
_NIFTY100_OFFICIAL_MIRROR = _OFFICIAL_MIRROR + "ind_nifty100list.csv"
_COMPONENTS = ("ind_nifty50list.csv", "ind_niftynext50list.csv")
_TTL = 4 * 60 * 60
_MAX_STALE = 7 * 24 * 60 * 60
_LOCK = threading.Lock()
_CACHE = {}


def parse_constituents(content, profile, *, exact_count=None):
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
            if exact_count is not None:
                raise ValueError("duplicate official constituent")
            continue
        symbols.append(sym)
        industries[sym] = (str(row.get(industry_col) or "").strip() if industry_col else "") or "Unclassified"
    if (len(symbols) != exact_count if exact_count is not None else
            abs(len(symbols) - meta["count"]) > meta["tolerance"]):
        raise ValueError("NSE constituent count outside expected range")
    return tuple(symbols), industries


def _official_csv(url, profile, *, exact_count=None):
    res = requests.get(url, timeout=(5, 12), headers={
        "User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"})
    res.raise_for_status()
    return parse_constituents(res.content,profile,exact_count=exact_count)


def _component_membership():
    """Two independently verified, disjoint 50-member official lists."""
    all_symbols, all_industries, sources = [], {}, []
    for filename in _COMPONENTS:
        for host in (*_HOSTS, _OFFICIAL_MIRROR):
            url=host+filename
            try:
                symbols,industries=_official_csv(url,"conservative",exact_count=50)
                if set(symbols)&set(all_symbols):
                    raise ValueError("overlapping component membership")
                all_symbols.extend(symbols)
                all_industries.update(industries)
                sources.append(url)
                break
            except (requests.RequestException,ValueError,UnicodeError,csv.Error,OSError):
                continue
        else:
            return None
    return tuple(all_symbols),all_industries,sources


def _snapshot_path():
    path=os.environ.get("MSK_TOP5_MEMBERSHIP_SNAPSHOT","").strip()
    return Path(path) if path else None


def _save_snapshot(value):
    path=_snapshot_path()
    if path is None:
        return
    tmp=None
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,
                                         prefix=".top5-",delete=False) as stream:
            tmp=Path(stream.name)
            json.dump(dict(value,captured_at=datetime.now(IST).isoformat()),stream)
        os.replace(tmp,path)
    except OSError:
        pass
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _load_snapshot():
    path=_snapshot_path()
    if path is None:
        return None
    try:
        if path.stat().st_size>40_000:
            return None
        value=json.loads(path.read_text(encoding="utf-8"))
        stamp=datetime.fromisoformat(value["captured_at"])
        now=datetime.now(IST)
        if (stamp.tzinfo is None or stamp.astimezone(IST).date()!=now.date()
                or not 0<=(now-stamp).total_seconds()<=24*3600):
            return None
        symbols=value["symbols"]
        industries=value["industries"]
        urls=value["source_urls"]
        direct=[host+INDEXES["conservative"]["filename"] for host in (*_HOSTS,_OFFICIAL_MIRROR)]
        components=[[host+filename for host in (*_HOSTS,_OFFICIAL_MIRROR)] for filename in _COMPONENTS]
        if not (isinstance(urls,list) and
                ((len(urls)==1 and urls[0] in direct) or
                 (len(urls)==2 and all(url in allowed for url,allowed in zip(urls,components))))):
            return None
        if (not isinstance(symbols,list) or len(symbols)!=100
                or any(not isinstance(x,str) or not re.fullmatch(r"[A-Z0-9&_.-]{1,30}",x) for x in symbols)
                or len(set(symbols))!=100 or not isinstance(industries,dict)
                or set(industries)!=set(symbols) or any(not isinstance(x,str) for x in industries.values())):
            return None
        return dict(name=INDEXES["conservative"]["name"],cap=INDEXES["conservative"]["cap"],
                    symbols=tuple(symbols),industries=industries,asof=stamp.astimezone(IST).strftime("%d-%b-%Y %H:%M IST"),
                    source="Official NSE same-day constituent snapshot",source_urls=urls,cached=True)
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        return None


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
    urls = [host + meta["filename"] for host in _HOSTS]
    # Official index-provider mirror also supports Midcap/Smallcap.
    urls.append(_OFFICIAL_MIRROR+meta["filename"])
    for url in urls:
        try:
            symbols,industries=_official_csv(url,profile,
                exact_count=100 if profile=="conservative" else None)
            value = {
                "name": meta["name"], "cap": meta["cap"], "symbols": symbols,
                "industries": industries,
                "asof": datetime.now(IST).strftime("%d-%b-%Y %H:%M IST"),
                "source": "Official NSE constituent CSV",
                "source_urls": [url],
            }
            if profile=="conservative":
                _save_snapshot(value)
            with _LOCK:
                _CACHE[profile] = {"ts": time.time(), "value": value}
            LOG.info("MSK index membership %s: %d validated constituents", profile, len(symbols))
            return dict(value, cached=False)
        except (requests.RequestException, ValueError, UnicodeError, csv.Error) as exc:
            LOG.warning("MSK index membership %s provider unavailable (%s)",
                        profile, type(exc).__name__)
    if profile=="conservative":
        combined=_component_membership()
        if combined is not None:
            symbols,industries,sources=combined
            value=dict(name=meta["name"],cap=meta["cap"],symbols=symbols,
                       industries=industries,asof=datetime.now(IST).strftime("%d-%b-%Y %H:%M IST"),
                       source="Official NSE Nifty 50 + Next 50",source_urls=sources)
            _save_snapshot(value)
            with _LOCK:
                _CACHE[profile]={"ts":time.time(),"value":value}
            return dict(value,cached=False)
        snapshot=_load_snapshot()
        if snapshot is not None:
            # Do not reset the official capture timestamp by reading a cache.
            return snapshot
    with _LOCK:
        recent = _CACHE.get(profile)
        if recent and now - recent["ts"] <= _MAX_STALE:
            return dict(recent["value"], cached=True, stale=True)
    raise RuntimeError("Official NSE index membership unavailable; no index stock recommendations issued")
