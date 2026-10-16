"""
universe.py — "Top 1000 stocks" universe for Stocks Buzz
────────────────────────────────────────────────────────────────────────────────
Priority (first one that yields enough symbols wins):

  0. OVERRIDE FILE  — env BUZZ_UNIVERSE_FILE (default: universe_override.txt).
                      One NSE symbol per line. Use this to pin the exact list
                      you want, or if NSE blocks your host's IP.
  1. NSE bhavcopy   — one file with every EQ-series stock's traded value and
                      delivery %. Ranked by turnover → the 1000 most-traded
                      stocks ("top 1000 by liquidity"). Also gives delivery %,
                      which Stocks Buzz uses as a confirmation signal.
  2. NSE index CSVs — Nifty 500 + Nifty Microcap 250 (= 750 stocks).
  3. Built-in map   — SECTOR_STOCKS from nifty500_collector (~290 stocks).
                      Last resort so the feature never fully breaks.

The result always includes `source` so the bot can tell the user honestly
which tier produced the list.
"""

import io
import os
import csv
import time
import logging
import threading
from datetime import date, timedelta
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)

TARGET_SIZE = int(os.getenv("BUZZ_UNIVERSE_SIZE", "1000"))
MIN_ACCEPTABLE = 400            # below this a tier is considered "degraded"
CACHE_TTL = 12 * 3600
OVERRIDE_FILE = os.getenv("BUZZ_UNIVERSE_FILE", "universe_override.txt")

_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept": "text/csv,text/plain,*/*",
    "Referer": "https://www.nseindia.com/",
}

_BHAV_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv"
_INDEX_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_niftymicrocap250_list.csv",
]

_lock = threading.Lock()
_cache: Dict = {"ts": 0.0, "data": None}


def _valid_symbol(s: str) -> bool:
    s = (s or "").strip().upper()
    return 1 < len(s) <= 20 and all(ch.isalnum() or ch in "&-" for ch in s)


def _from_override() -> Dict:
    try:
        if not os.path.exists(OVERRIDE_FILE):
            return {}
        with open(OVERRIDE_FILE, encoding="utf-8") as f:
            syms = []
            for line in f:
                s = line.split("#")[0].strip().upper().replace(".NS", "")
                if _valid_symbol(s) and s not in syms:
                    syms.append(s)
        if syms:
            return {"symbols": syms[:TARGET_SIZE], "source": f"override file ({OVERRIDE_FILE})",
                    "delivery": {}}
    except Exception as e:
        logger.warning(f"[universe] override read failed: {e}")
    return {}


def _from_bhavcopy() -> Dict:
    """Most-traded EQ stocks from the latest available NSE bhavcopy."""
    sess = requests.Session()
    sess.headers.update(_UA)
    d = date.today()
    for back in range(0, 9):                    # skip weekends/holidays
        day = d - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        url = _BHAV_URL.format(d=day.strftime("%d%m%Y"))
        try:
            r = sess.get(url, timeout=25)
            if not r.ok or len(r.content) < 50_000:
                continue
            reader = csv.DictReader(io.StringIO(r.text))
            reader.fieldnames = [(h or "").strip().upper() for h in (reader.fieldnames or [])]
            rows = []
            for row in reader:
                row = {(k or "").strip().upper(): (v or "").strip() for k, v in row.items()}
                if row.get("SERIES") != "EQ":
                    continue
                sym = row.get("SYMBOL", "").upper()
                if not _valid_symbol(sym):
                    continue
                try:
                    turnover = float(row.get("TURNOVER_LACS", "0") or 0)
                except ValueError:
                    continue
                try:
                    deliv = float(row.get("DELIV_PER", "")) if row.get("DELIV_PER", "") not in ("", "-") else None
                except ValueError:
                    deliv = None
                rows.append((sym, turnover, deliv))
            if len(rows) < 500:
                continue
            rows.sort(key=lambda x: x[1], reverse=True)
            top = rows[:TARGET_SIZE]
            return {
                "symbols": [r[0] for r in top],
                "delivery": {r[0]: r[2] for r in top if r[2] is not None},
                "source": f"NSE bhavcopy {day.strftime('%d-%b-%Y')} (top {len(top)} by turnover)",
            }
        except Exception as e:
            logger.info(f"[universe] bhavcopy {day} failed: {str(e)[:100]}")
    return {}


def _from_index_csvs() -> Dict:
    syms: List[str] = []
    sess = requests.Session()
    sess.headers.update(_UA)
    for url in _INDEX_URLS:
        try:
            r = sess.get(url, timeout=20)
            if not r.ok:
                continue
            reader = csv.DictReader(io.StringIO(r.text))
            for row in reader:
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                s = row.get("symbol", "").upper()
                if _valid_symbol(s) and s not in syms:
                    syms.append(s)
        except Exception as e:
            logger.info(f"[universe] index csv failed {url}: {str(e)[:100]}")
    if len(syms) >= MIN_ACCEPTABLE:
        return {"symbols": syms[:TARGET_SIZE], "delivery": {},
                "source": f"NSE index lists (Nifty 500 + Microcap 250, {len(syms)} stocks)"}
    return {}


def _from_builtin() -> Dict:
    syms: List[str] = []
    try:
        from nifty500_collector import SECTOR_STOCKS
        for lst in SECTOR_STOCKS.values():
            for s in lst:
                s = s.upper()
                if _valid_symbol(s) and s not in syms:
                    syms.append(s)
    except Exception:
        pass
    return {"symbols": syms, "delivery": {},
            "source": f"built-in fallback list ({len(syms)} stocks — NSE lists unreachable)"}


def get_universe(force: bool = False) -> Dict:
    """Returns {"symbols": [...], "delivery": {SYM: pct}, "source": str, "degraded": bool}."""
    with _lock:
        if not force and _cache["data"] and time.time() - _cache["ts"] < CACHE_TTL:
            return _cache["data"]

    data: Dict = {}
    for loader in (_from_override, _from_bhavcopy, _from_index_csvs):
        data = loader()
        if data and data.get("symbols"):
            break
    if not data or not data.get("symbols"):
        data = _from_builtin()

    is_override = str(data.get("source", "")).startswith("override")
    data["degraded"] = (len(data["symbols"]) < MIN_ACCEPTABLE) and not is_override
    logger.info(f"[universe] {len(data['symbols'])} symbols via {data['source']}")
    with _lock:
        _cache["data"] = data
        _cache["ts"] = time.time()
    return data
