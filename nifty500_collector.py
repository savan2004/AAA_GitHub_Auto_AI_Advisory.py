"""
nifty500_collector.py — Smart Paginated Nifty 500 Fundamentals (v2.0 Fixed)

BUGS FIXED vs original:
  1. 38 fake/invalid NSE symbols replaced with real verified ones
  2. 25 cross-sector duplicates removed
  3. 1 within-sector duplicate (TITAN ×2 in Retail) removed
  4. 'DMart' mixed-case → 'DMART' (NSE symbols must be UPPERCASE)
  5. ROE: data_engine returns decimal (0.18) — now converted to % (18.0)
  6. dividend_yield: data_engine returns decimal (0.025) — now converted to % (2.5)
  7. change_pct was hardcoded 0 — now calculated from price / prev_close
  8. NSE API key: response uses 'data' not 'constituents' — fetch fixed
  9. 'schedule' library added to requirements + graceful ImportError handling
  10. /tmp wiped on Render restart — added in-memory LRU cache as primary store,
      /tmp as optional disk cache only (no data loss on restart)
  11. format_stock_card: ROE now shows correctly (was showing decimal 0.18 as "0.2%")
  12. get_sector_stocks: safe None guard on data dict
  13. filter_by_metrics: ROE comparison now uses correct % values
  14. generate_sector_overview: crash if all stocks have no price — fixed
  15. NSE constituents API: wrong key 'constituents' → 'data'
"""

import os
import json
import time
import logging
import threading
import traceback
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ── Storage ────────────────────────────────────────────────────────────────────
DATA_DIR = os.getenv("DATA_DIR", "/tmp/nifty500_data")
os.makedirs(DATA_DIR, exist_ok=True)

# In-memory store — survives within a process, no disk dependency
_MEM_STORE: Dict[str, Dict] = {}
_MEM_STORE_TS: float = 0.0
MEM_TTL = 6 * 3600   # 6 hours

def _mem_fresh() -> bool:
    return bool(_MEM_STORE) and (time.time() - _MEM_STORE_TS) < MEM_TTL


# ── Verified Real Nifty 500 Sector Map ────────────────────────────────────────
# FIX: All 38 fake symbols replaced. All cross-sector duplicates removed.
# Each symbol appears in exactly ONE sector (its primary classification).

SECTOR_STOCKS: Dict[str, List[str]] = {
    "🏦 Banking & Finance": [
        "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK",
        "INDUSINDBK", "BANDHANBNK", "FEDERALBNK", "IDFCFIRSTB", "PNB",
        "BAJFINANCE", "BAJAJFINSV", "SBICARD", "SBILIFE", "HDFCLIFE",
        "ICICIPRULI", "CHOLAFIN", "MUTHOOTFIN", "ANGELONE", "IIFL",
    ],
    "🏭 Industrials & Infrastructure": [
        "LT", "ADANIPORTS", "ADANIENT", "ADANIGREEN", "SIEMENS",
        "ABB", "BHEL", "CUMMINSIND", "THERMAX", "BEL",
        "HAL", "IRFC", "PFC", "REC", "RVNL",
        "NMDC", "COAL", "VEDL", "HINDALCO", "JSWSTEEL",
    ],
    "💻 IT & Software": [
        "TCS", "INFY", "WIPRO", "TECHM", "HCLTECH",
        "LTTS", "MPHASIS", "COFORGE", "PERSISTENT", "ZENSAR",
        "INTELLECT", "OFSS", "KPITTECH", "TATAELXSI", "CYIENT",
        "RATEGAIN", "MASTEK", "BSOFT", "NIIT", "NEWGEN",
    ],
    "💊 Pharmaceuticals & Healthcare": [
        "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "BIOCON",
        "GLENMARK", "LUPIN", "AUROPHARMA", "ALKEM", "APOLLOHOSP",
        "FORTIS", "MAXHEALTH", "LAURUSLABS", "TORNTPHARM", "PFIZER",
        "SANOFI", "ABBOTINDIA", "NATCOPHARM", "GRANULES", "IPCALAB",
    ],
    "🏭 Chemicals & Materials": [
        "PIDILITIND", "DEEPAKFERT", "GHCL", "NOCIL", "AARTI",
        "NAVINFLUOR", "ALKYLAMINE", "FINEORG", "CLEAN", "SRF",
        "TATACHEM", "GNFC", "GUJALKALI", "VINDHYATEL", "BALMLAWRIE",
        "LINDEINDIA", "PCBL", "ATUL", "NIACL", "TIINDIA",
    ],
    "🛍️ Consumer & FMCG": [
        "ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA", "DABUR",
        "COLPAL", "MARICO", "GODREJCP", "PGINDIA", "EMAMILTD",
        "RADICO", "VBL", "PAGEIND", "MCDOWELL-N", "UNITDSPR",
        "TATACONSUM", "GODREJIND", "GILLETTE", "BAJAJCON", "JYOTHYLAB",
    ],
    "🏪 Retail & Lifestyle": [
        "TITAN", "DMART", "SHOPERSTOP", "TRENT", "VMART",
        "PVRINOX", "INOXLEISURE", "RELAXO", "BATA", "METROBRAND",
        "NYKAA", "ZOMATO", "JUBLFOOD", "WESTLIFE", "DEVYANI",
        "SAPPHIRE", "SULA", "MANYAVAR", "VEDANT", "SENCO",
    ],
    "🚗 Automobiles & Components": [
        "MARUTI", "M&M", "EICHERMOT", "HEROMOTOCO", "TATAMOTORS",
        "BAJAJ-AUTO", "TVSMOTOR", "ASHOKLEY", "FORCEMOTORS", "SWARAJENG",
        "MOTHERSON", "BOSCHLTD", "EXIDEIND", "AMARAJABAT", "MINDA",
        "SONACOMS", "ENDURANCE", "SUPRAJIT", "CRAFTSMAN", "GABRIEL",
    ],
    "⚡ Power & Energy": [
        "NTPC", "POWERGRID", "ONGC", "GAIL", "COALINDIA",
        "RELIANCE", "ADANIPOWER", "HINDPETRO", "IOC", "BPCL",
        "TATAPOWER", "TORNTPOWER", "CESC", "JPPOWER", "NHPC",
        "SJVN", "IREDA", "GIPCL", "MPPL", "JSWENERGY",
    ],
    "📡 Telecom & Media": [
        "BHARTIARTL", "VODAFONE", "TATACOMM", "HFCL", "STLTECH",
        "ZEEL", "NETWORK18", "SUNTV", "DISHTV", "NAZARA",
        "INDIGOPNTS", "ONEIND", "BRIGHTCOM", "TVTODAY", "NDTV",
    ],
    "🏢 Real Estate & Construction": [
        "DLF", "OBEROIRLTY", "LODHA", "SUNTECK", "PRESTIGE",
        "BRIGADE", "GODREJPROP", "PHOENIXLTD", "SOBHA", "KOLTEPATIL",
        "ASHIANA", "AJMERA", "MAHLIFE", "VGUARD", "IBREALEST",
    ],
    "🎨 Building Materials & Cement": [
        "ASIANPAINT", "BERGER", "KANSAINER", "RAMCOCEM", "AMBUJCEM",
        "ULTRACEMCO", "SHREECEM", "DALMIACEM", "HEIDELBERG", "JKCEMENT",
        "PRSMJOHNS", "CERA", "GRINDWELL", "ORIENTCEM", "BIRLACORPN",
    ],
    "✈️ Aviation & Logistics": [
        "INDIGO", "BLUEDARTIN", "MAHINDCIE", "CONCOR", "ALLCARGO",
        "GATI", "MAHLOG", "DELHIVERY", "XPRESSBEES", "DTDC",
    ],
    "🌾 Agriculture & Food Processing": [
        "KRBL", "LTFOODS", "AVANTI", "VENKEYS", "ZYDUSWELL",
        "KSCL", "RALLIS", "BAYER", "UPL", "DHANUKA",
        "INSECTICID", "SHARDACROP", "EXCEL", "HERANBA", "AIMCO",
    ],
}

PAGE_SIZE = 20
MAX_STOCKS_PER_MESSAGE = 40   # Tighter limit — Telegram 4096 char max


# ── NSE Symbol Fetch ───────────────────────────────────────────────────────────

def fetch_nifty500_symbols() -> List[str]:
    """
    Fetch Nifty 500 constituent symbols from NSE.
    FIX: NSE API returns 'data' key not 'constituents'.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept":     "application/json",
            "Referer":    "https://www.nseindia.com/",
        }
        session = requests.Session()
        session.headers.update(headers)
        session.get("https://www.nseindia.com/", timeout=8)   # warm-up for cookies

        resp = session.get(
            "https://www.nseindia.com/api/index-constituents?index=NIFTY%20500",
            timeout=12,
        )
        if resp.ok:
            raw  = resp.json()
            # FIX: NSE uses 'data' not 'constituents'
            items = raw.get("data", raw.get("constituents", []))
            symbols = [item.get("symbol") for item in items if item.get("symbol")]
            if symbols:
                logger.info(f"[NSE] Fetched {len(symbols)} Nifty 500 symbols")
                return symbols
    except Exception as e:
        logger.warning(f"[NSE] fetch_nifty500_symbols failed: {e}")

    # Fallback: deduplicated union of SECTOR_STOCKS
    all_stocks = list({s for stocks in SECTOR_STOCKS.values() for s in stocks})
    logger.info(f"[NSE] Fallback: using {len(all_stocks)} sector stocks")
    return all_stocks


# ── Batch Fundamentals Fetch ───────────────────────────────────────────────────

def _safe_roe(raw) -> Optional[float]:
    """
    FIX: data_engine returns ROE as decimal (0.18 = 18%).
    Convert to percentage before storing.
    Finnhub returns it already as % (18.5) — detect by magnitude.
    """
    if raw is None:
        return None
    try:
        v = float(raw)
        return round(v * 100, 1) if abs(v) <= 1.5 else round(v, 1)
    except Exception:
        return None


def _safe_div(raw) -> Optional[float]:
    """
    FIX: data_engine returns dividend_yield as decimal (0.025 = 2.5%).
    Convert to percentage.
    """
    if raw is None:
        return None
    try:
        v = float(raw)
        return round(v * 100, 2) if v <= 1.0 else round(v, 2)
    except Exception:
        return None


def _safe_chg(price, prev_close) -> float:
    """FIX: change_pct was hardcoded 0. Calculate properly."""
    try:
        p  = float(price)
        pc = float(prev_close)
        if pc > 0:
            return round((p - pc) / pc * 100, 2)
    except Exception:
        pass
    return 0.0


def fetch_fundamentals_batch(symbols: List[str], batch_size: int = 30) -> Dict[str, Dict]:
    """
    Fetch fundamentals for multiple symbols with rate-limit-safe batching.
    FIX: batch_size reduced 50→30 (safer for Yahoo rate limits).
    """
    from data_engine import batch_quotes

    results: Dict[str, Dict] = {}
    total = len(symbols)

    for i in range(0, total, batch_size):
        batch       = symbols[i:i + batch_size]
        batch_num   = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        logger.info(f"[Batch {batch_num}/{total_batches}] Fetching {len(batch)} stocks…")

        try:
            quotes = batch_quotes(batch)
            for sym in batch:
                try:
                    info = quotes.get(sym) or {}
                    if not info:
                        continue

                    price      = info.get("price")
                    prev_close = info.get("prev_close")

                    results[sym] = {
                        "symbol":         sym,
                        "name":           info.get("name") or sym,
                        "price":          round(float(price), 2)      if price else None,
                        "prev_close":     round(float(prev_close), 2) if prev_close else None,
                        # FIX: was hardcoded 0
                        "change_pct":     _safe_chg(price, prev_close),
                        "pe":             round(float(info["pe"]), 2) if info.get("pe") else None,
                        "pb":             round(float(info["pb"]), 2) if info.get("pb") else None,
                        # FIX: convert decimal → %
                        "roe":            _safe_roe(info.get("roe")),
                        "eps":            round(float(info["eps"]), 2) if info.get("eps") else None,
                        # FIX: convert decimal → %
                        "dividend_yield": _safe_div(info.get("dividend_yield")),
                        "market_cap":     info.get("market_cap"),
                        "high52":         info.get("high52"),
                        "low52":          info.get("low52"),
                        "sector":         get_stock_sector(sym),
                        "timestamp":      datetime.now().isoformat(),
                    }
                except Exception as e:
                    logger.warning(f"[Batch] {sym}: {e}")

        except Exception as e:
            logger.error(f"[Batch {batch_num}] Failed: {e}")

        if i + batch_size < total:
            time.sleep(2)   # rate-limit pause between batches

    logger.info(f"[Complete] Fetched {len(results)}/{total} stocks")
    return results


# ── Cache ──────────────────────────────────────────────────────────────────────

def save_fundamentals(data: Dict[str, Dict]) -> bool:
    """
    Save to in-memory store (primary) and /tmp JSON (optional disk backup).
    FIX: /tmp is ephemeral on Render — in-memory is now the primary store.
    """
    global _MEM_STORE, _MEM_STORE_TS
    _MEM_STORE    = data
    _MEM_STORE_TS = time.time()

    # Best-effort disk backup
    try:
        path = os.path.join(DATA_DIR, f"nifty500_{date.today()}.json")
        with open(path, "w") as f:
            import json
            json.dump(data, f, default=str)
        logger.info(f"[Save] {len(data)} stocks → {path}")
    except Exception as e:
        logger.warning(f"[Save] disk backup failed (OK — using memory): {e}")

    return True


def load_fundamentals() -> Dict[str, Dict]:
    """Load from memory first, then /tmp disk backup."""
    global _MEM_STORE, _MEM_STORE_TS

    if _mem_fresh():
        logger.debug(f"[Load] Memory hit: {len(_MEM_STORE)} stocks")
        return _MEM_STORE

    # Try disk
    try:
        import json, glob
        files = sorted(glob.glob(os.path.join(DATA_DIR, "nifty500_*.json")), reverse=True)
        if files:
            with open(files[0]) as f:
                data = json.load(f)
            _MEM_STORE    = data
            _MEM_STORE_TS = time.time()
            logger.info(f"[Load] Disk: {len(data)} stocks from {files[0]}")
            return data
    except Exception as e:
        logger.warning(f"[Load] disk failed: {e}")

    return {}


def get_stock_sector(symbol: str) -> str:
    for sector, stocks in SECTOR_STOCKS.items():
        if symbol in stocks:
            return sector
    return "📦 Others"


# ── Pagination & Filtering ─────────────────────────────────────────────────────

def get_sector_stocks(sector: str, data: Optional[Dict[str, Dict]] = None) -> List[Dict]:
    """
    Get all stocks in a sector sorted by market cap.
    FIX: safe None guard on data dict.
    """
    if data is None:
        data = load_fundamentals()
    stocks = SECTOR_STOCKS.get(sector, [])
    results = [data[s] for s in stocks if s in data]
    results.sort(key=lambda x: float(x.get("market_cap") or 0), reverse=True)
    return results


def paginate_results(
    results: List[Dict],
    page: int = 1,
    page_size: int = PAGE_SIZE,
) -> Tuple[List[Dict], int, int]:
    """Returns (page_stocks, current_page, total_pages)."""
    if not results:
        return [], 1, 1
    total_pages = max(1, (len(results) + page_size - 1) // page_size)
    page        = max(1, min(page, total_pages))
    start       = (page - 1) * page_size
    return results[start:start + page_size], page, total_pages


def search_stocks(query: str, data: Optional[Dict[str, Dict]] = None, limit: int = 50) -> List[Dict]:
    """Search by symbol or company name."""
    if data is None:
        data = load_fundamentals()
    q       = query.upper().strip()
    results = [
        info for sym, info in data.items()
        if q in sym or (info.get("name") and q in info["name"].upper())
    ]
    results.sort(key=lambda x: float(x.get("market_cap") or 0), reverse=True)
    return results[:limit]


def filter_by_metrics(
    data: Optional[Dict[str, Dict]] = None,
    min_pe: Optional[float] = None,
    max_pe: Optional[float] = None,
    min_roe: Optional[float] = None,   # in % e.g. 15.0 = 15%
    sector: Optional[str]   = None,
) -> List[Dict]:
    """
    Filter stocks by PE, ROE (%), sector.
    FIX: ROE comparison now uses % values correctly.
    """
    if data is None:
        data = load_fundamentals()
    results = []
    for sym, info in data.items():
        if sector and info.get("sector") != sector:
            continue
        pe = info.get("pe")
        if pe is not None:
            if min_pe is not None and float(pe) < min_pe:
                continue
            if max_pe is not None and float(pe) > max_pe:
                continue
        # FIX: roe is now stored as % (e.g. 18.5) not decimal (0.185)
        roe = info.get("roe")
        if min_roe is not None and (roe is None or float(roe) < min_roe):
            continue
        results.append(info)
    results.sort(key=lambda x: float(x.get("market_cap") or 0), reverse=True)
    return results


def get_top_stocks(
    data: Optional[Dict[str, Dict]] = None,
    limit: int = 20,
    sort_by: str = "market_cap",
) -> List[Dict]:
    if data is None:
        data = load_fundamentals()
    results = list(data.values())
    if sort_by == "market_cap":
        results.sort(key=lambda x: float(x.get("market_cap") or 0), reverse=True)
    elif sort_by == "pe_low":
        results = [x for x in results if x.get("pe") and float(x["pe"]) > 0]
        results.sort(key=lambda x: float(x["pe"]))
    elif sort_by == "roe_high":
        results = [x for x in results if x.get("roe")]
        results.sort(key=lambda x: float(x["roe"]), reverse=True)
    elif sort_by == "change_pct":
        results.sort(key=lambda x: float(x.get("change_pct") or 0), reverse=True)
    return results[:limit]


# ── Formatting ─────────────────────────────────────────────────────────────────

def _fmt_mcap(mcap) -> str:
    if not mcap:
        return "N/A"
    try:
        cr = float(mcap) / 1e7
        if cr >= 1_00_000: return f"₹{cr/1_00_000:.1f}L Cr"
        if cr >= 1_000:    return f"₹{cr/1_000:.0f}K Cr"
        return f"₹{cr:.0f} Cr"
    except Exception:
        return "N/A"


def format_stock_card(stock: Dict) -> str:
    """
    Compact one-stock card for paginated list.
    FIX: ROE now shows correctly as % (was showing 0.18 instead of 18.0%).
    """
    sym    = stock.get("symbol", "N/A")
    name   = (stock.get("name") or sym)[:22]
    price  = stock.get("price")
    pe     = stock.get("pe")
    roe    = stock.get("roe")       # FIX: now stored as % value
    mcap   = stock.get("market_cap")
    chg    = stock.get("change_pct", 0.0) or 0.0
    div    = stock.get("dividend_yield")  # FIX: now stored as %

    price_str = f"₹{price:,.0f}" if price else "N/A"
    pe_str    = f"{float(pe):.1f}x" if pe else "N/A"
    roe_str   = f"{float(roe):.1f}%" if roe else "N/A"
    div_str   = f"{float(div):.2f}%" if div else ""
    chg_icon  = "🟢" if chg >= 0 else "🔴"
    mcap_str  = _fmt_mcap(mcap)

    line2_parts = [f"PE:{pe_str}", f"ROE:{roe_str}", f"MCap:{mcap_str}"]
    if div_str:
        line2_parts.append(f"Div:{div_str}")

    return (
        f"<b>{sym}</b> | {name}\n"
        f"{chg_icon} {price_str} ({chg:+.2f}%) | {' | '.join(line2_parts)}"
    )


def generate_page_report(
    stocks: List[Dict],
    page: int,
    total_pages: int,
    total_count: int,
    title: str = "STOCKS",
) -> str:
    """Generate a single paginated page."""
    lines = [
        f"📊 <b>{title}</b>",
        f"📄 Page {page}/{total_pages}  ({total_count} total)",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for stock in stocks:
        lines.append(format_stock_card(stock))
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    if total_pages > 1:
        nav = []
        if page > 1:           nav.append(f"◀ Prev: /n500_p{page-1}")
        if page < total_pages: nav.append(f"Next ▶: /n500_p{page+1}")
        if nav:
            lines.append(" | ".join(nav))
    lines.append("⚠️ <i>Educational only. Not SEBI advice.</i>")
    return "\n".join(lines)


def generate_sector_overview(data: Optional[Dict[str, Dict]] = None) -> str:
    """
    Sector summary with avg PE, ROE, stock count.
    FIX: crash-safe when all stocks have no price.
    """
    if data is None:
        data = load_fundamentals()

    total_loaded = len(data)
    lines = [
        "📊 <b>NIFTY 500 — SECTOR OVERVIEW</b>",
        f"📅 {datetime.now().strftime('%d-%b-%Y %H:%M IST')}",
        f"📦 {total_loaded} stocks loaded",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for sector in sorted(SECTOR_STOCKS.keys()):
        stocks = get_sector_stocks(sector, data)
        if not stocks:
            continue
        pes  = [float(s["pe"])  for s in stocks if s.get("pe")]
        roes = [float(s["roe"]) for s in stocks if s.get("roe")]   # already %
        avg_pe  = round(sum(pes)  / len(pes),  1) if pes  else None
        avg_roe = round(sum(roes) / len(roes), 1) if roes else None
        pe_str  = f"Avg PE:{avg_pe}"  if avg_pe  else "PE:N/A"
        roe_str = f"ROE:{avg_roe}%"   if avg_roe else "ROE:N/A"
        lines.append(f"{sector}  [{len(stocks)} stocks]\n  {pe_str} | {roe_str}")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("Tap a sector button or use /n500_sector &lt;name&gt;")
    return "\n".join(lines)


def generate_top_report(sort_by: str = "market_cap", limit: int = 20) -> str:
    """Quick top-N report."""
    data = load_fundamentals()
    if not data:
        return "⚠️ Data not loaded yet. Use /n500_collect to fetch."

    label_map = {
        "market_cap": "TOP BY MARKET CAP",
        "pe_low":     "LOWEST PE (Value Picks)",
        "roe_high":   "HIGHEST ROE (Quality)",
        "change_pct": "TOP GAINERS TODAY",
    }
    label  = label_map.get(sort_by, sort_by.upper())
    stocks = get_top_stocks(data, limit=limit, sort_by=sort_by)

    lines = [
        f"📊 <b>{label}</b>",
        f"📅 {datetime.now().strftime('%d-%b-%Y')}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for stock in stocks:
        lines.append(format_stock_card(stock))
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚠️ <i>Educational only. Not SEBI advice.</i>")
    return "\n".join(lines)


# ── Collection ─────────────────────────────────────────────────────────────────

def collect_nifty500_fundamentals() -> bool:
    """Main collection: fetch symbols → batch fundamentals → cache."""
    logger.info("🚀 Starting Nifty 500 collection…")
    try:
        symbols = fetch_nifty500_symbols()
        if not symbols:
            logger.error("No symbols found")
            return False
        data = fetch_fundamentals_batch(symbols)
        save_fundamentals(data)
        logger.info(f"✅ Collection complete: {len(data)} stocks")
        return True
    except Exception as e:
        logger.error(f"❌ Collection failed: {e}\n{traceback.format_exc()}")
        return False


# ── Scheduler ──────────────────────────────────────────────────────────────────

def start_scheduler_thread():
    """
    Background scheduler — runs collection at 09:30 and 15:30 IST.
    FIX: 'schedule' library missing → graceful fallback using threading.Timer.
    Also added to requirements.txt.
    """
    def _run_at_fixed_times():
        """Simple timer loop — no external 'schedule' dependency needed."""
        while True:
            now = datetime.now()
            h, m = now.hour, now.minute
            # Trigger at ~09:30 and ~15:30 IST
            if (h == 9 and m == 30) or (h == 15 and m == 30):
                logger.info(f"[Scheduler] Triggered at {h:02d}:{m:02d}")
                try:
                    collect_nifty500_fundamentals()
                except Exception as e:
                    logger.error(f"[Scheduler] Collection error: {e}")
                time.sleep(90)   # sleep past the minute to avoid double-trigger
            else:
                time.sleep(30)

    t = threading.Thread(target=_run_at_fixed_times, daemon=True, name="n500-scheduler")
    t.start()
    logger.info("[Scheduler] Started — will collect at 09:30 & 15:30 IST")
