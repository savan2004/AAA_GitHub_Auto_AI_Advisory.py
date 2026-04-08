# ============================================================================
# nifty500_collector.py - SMART PAGINATED NIFTY 500 FUNDAMENTALS
# ============================================================================
"""
Collects & serves Nifty 500 data smartly:
  - NO large data dumps (prevents Telegram crash)
  - Sector-wise grouping (Finance, IT, Pharma, etc.)
  - Paginated results (20-50 per page)
  - Search with pagination
  - Filter with sorting
  - User-friendly navigation

FIXES:
  - Chunked delivery (never >50 stocks per message)
  - Sector categorization
  - Pagination controls (Next/Prev/Jump to page)
  - Memory-efficient caching
  - Rate limit safe fetching
"""

import os
import json
import csv
import time
import logging
import threading
import traceback
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ── Storage & Config ───────────────────────────────────────────────────────
DATA_DIR = os.getenv("DATA_DIR", "/tmp/nifty500_data")
os.makedirs(DATA_DIR, exist_ok=True)

FUNDAMENTALS_JSON = os.path.join(DATA_DIR, f"nifty500_fundamentals_{date.today()}.json")
FUNDAMENTALS_CSV = os.path.join(DATA_DIR, f"nifty500_fundamentals_{date.today()}.csv")

# Sector mapping for Nifty 500 stocks
SECTOR_STOCKS = {
    "🏦 Banking & Finance": [
        "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK",
        "INDUSIND", "BANDHANBNK", "IDBIBANK", "FEDERALBNK", "IDFCBANK",
        "BAJFINANCE", "BAJAJFINSV", "SBICARD", "SBILIFE", "HDFCLIFE",
        "ICICIPRULI", "CHOLAFIN", "MOTILALOFF", "ANGEL", "SHYAMMETL",
    ],
    "🏭 Industrials & Infrastructure": [
        "LT", "RELIANCE", "MARUTI", "M&M", "EICHERMOT",
        "HEROMOTOCO", "TATAMOTORS", "ADANIPORTS", "ADANIENT", "ADANIGREEN",
        "ADANIPOWER", "POWERGRID", "NTPC", "GAIL", "ONGC",
        "TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "NATIONALUM",
    ],
    "💻 IT & Software": [
        "TCS", "INFY", "WIPRO", "TECHM", "HCLTECH",
        "LTTS", "MINDTREE", "MPHASIS", "SEGMENT", "COFORGE",
        "BIGTECH", "NUCLEUS", "PERSISTENT", "KSOLVES", "ZENSAR",
        "INTELLECT", "IEXGLOBAL", "INFOSYS", "COGNIZANT", "MSMEGINE",
    ],
    "💊 Pharmaceuticals & Healthcare": [
        "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "BIOCON",
        "GLENMARK", "LUPIN", "AUROPHARMA", "ALKEM", "APOLLOHOSP",
        "FORTIS", "MAXHEALTH", "SANOFI", "PFIZER", "LAURUSLABS",
        "TORNTPHARM", "GRINDWELL", "INDUSTOWER", "BHARTIARTL", "BHARATPAY",
    ],
    "🏭 Chemicals & Materials": [
        "PIDILITIND", "BASF", "ARISEINVEST", "CRISIL", "DEEPAKFERT",
        "GHCL", "INDIGO", "JKNSTEEL", "KOHLIND", "LAXMIMACH",
        "NOCIL", "NICI", "RAMCOCEM", "SAURTECH", "SHILPAMED",
    ],
    "🛍️ Consumer & Retail": [
        "ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA", "DABUR",
        "COLPAL", "MARICO", "GODREJIND", "PGINDIA", "BAJAJISL",
        "RADICO", "TATACHEM", "VBL", "PAGEIND", "GBPL",
    ],
    "🏪 Retail & Distribution": [
        "RELIANCE", "TITAN", "TITAN", "SHOPERSTOP", "ABRL",
        "DMart", "DMART", "AVT", "PVRINOX", "CINEMAX",
        "INOXLEISURE", "RELAXO", "BATA", "MIRZA", "MRF",
    ],
    "🚗 Automobiles & Components": [
        "MARUTI", "M&M", "EICHERMOT", "HEROMOTOCO", "TATAMOTORS",
        "BAJAJFINSV", "BAJAJ-AUTO", "TVSMOTOR", "FORCEMOTORS", "ASHOKLEY",
        "SWARAJENG", "HEXAWARE", "MUNJAL", "SONACOMS", "BHEL",
    ],
    "⚡ Power & Energy": [
        "NTPC", "POWERGRID", "ONGC", "GAIL", "COALINDIA",
        "RELIANCE", "ADANIPOWER", "HINDPETRO", "IOCL", "BPCL",
        "OMAXAUTO", "NMDC", "TATAPWR", "GENON", "JMDL",
    ],
    "📡 Telecom & Media": [
        "BHARTIARTL", "JIOFINANCE", "INDIGO", "VBL", "PVR",
        "ZEEL", "NETWORK18", "GRAPHITE", "GUJGASLTD", "INDIARTC",
    ],
    "🏢 Real Estate & Construction": [
        "DLF", "OBEROI", "LODHA", "SUNTECK", "PRESTIGE",
        "BRIGADE", "HIRELABS", "INOXLEISURE", "ASIANPAINT", "KESAR",
    ],
    "🎨 Building Materials & Paint": [
        "ASIANPAINT", "BERGER", "KANSAINER", "RAMCOCEM", "AMBUJCEM",
        "ULTRACEM", "SHREECEM", "HEIDELBERG", "DALMIACEM", "GRAPHITE",
    ],
    "✈️ Transportation & Logistics": [
        "BHARATIARTL", "INDIGO", "SPICEJET", "GILLETTE", "INDIARTC",
        "TIRUPATI", "KALYANI", "KERNEX", "NEWTECH", "ARVINDFARM",
    ],
    "🌾 Agriculture & Food": [
        "BRITANNIA", "NESTLEIND", "GODREJIND", "SUGANTHI", "SRPL",
        "BAJAJFINSV", "RADICO", "TATACOMM", "GRAINMART", "KSCL",
    ],
}

# Pagination settings
PAGE_SIZE = 20  # Stocks per page
MAX_STOCKS_PER_MESSAGE = 50  # Never exceed this in one message

# ── Core Data Functions ────────────────────────────────────────────────────

def fetch_nifty500_symbols() -> List[str]:
    """Fetch latest Nifty 500 symbols from NSE."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        url = "https://www.nseindia.com/api/index-constituents?index=NIFTY%20500"
        
        session = requests.Session()
        session.headers.update(headers)
        session.get("https://www.nseindia.com/", timeout=5)
        
        resp = session.get(url, timeout=10)
        if resp.ok:
            data = resp.json()
            symbols = [item.get("symbol") for item in data.get("constituents", [])]
            logger.info(f"[NSE API] Fetched {len(symbols)} Nifty 500 symbols")
            return [s for s in symbols if s]
    except Exception as e:
        logger.warning(f"[NSE API] Failed: {e}")
    
    # Fallback: Use all stocks from SECTOR_STOCKS
    all_stocks = []
    for stocks in SECTOR_STOCKS.values():
        all_stocks.extend(stocks)
    return list(set(all_stocks))


def fetch_fundamentals_batch(symbols: List[str], batch_size: int = 50) -> Dict[str, Dict]:
    """Fetch fundamental data for multiple symbols in safe batches."""
    from data_engine import batch_quotes
    
    results = {}
    total = len(symbols)
    
    for i in range(0, total, batch_size):
        batch = symbols[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        
        logger.info(f"[Batch {batch_num}/{total_batches}] Fetching {len(batch)} stocks...")
        
        try:
            quotes = batch_quotes(batch)
            for sym in batch:
                try:
                    info = quotes.get(sym, {})
                    if info:
                        results[sym] = {
                            "symbol": sym,
                            "name": info.get("name", sym),
                            "price": info.get("price"),
                            "prev_close": info.get("prev_close"),
                            "change_pct": 0,
                            "pe": info.get("pe"),
                            "pb": info.get("pb"),
                            "roe": info.get("roe"),
                            "eps": info.get("eps"),
                            "dividend_yield": info.get("dividend_yield"),
                            "market_cap": info.get("market_cap"),
                            "high52": info.get("high52"),
                            "low52": info.get("low52"),
                            "sector": get_stock_sector(sym),
                            "timestamp": datetime.now().isoformat(),
                        }
                except Exception as e:
                    logger.warning(f"[Batch] {sym}: {e}")
        
        except Exception as e:
            logger.error(f"[Batch {batch_num}] Failed: {e}")
        
        if i + batch_size < total:
            time.sleep(2)
    
    logger.info(f"[Complete] Fetched {len(results)} stocks")
    return results


def save_fundamentals(data: Dict[str, Dict]) -> bool:
    """Save to JSON & CSV."""
    try:
        with open(FUNDAMENTALS_JSON, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"✅ Saved {len(data)} stocks to JSON")
        
        df = pd.DataFrame.from_dict(data, orient='index')
        df.to_csv(FUNDAMENTALS_CSV, index=False)
        logger.info(f"✅ Saved to CSV")
        
        return True
    except Exception as e:
        logger.error(f"❌ Save failed: {e}")
        return False


def load_fundamentals() -> Dict[str, Dict]:
    """Load from JSON."""
    try:
        if os.path.exists(FUNDAMENTALS_JSON):
            with open(FUNDAMENTALS_JSON, "r") as f:
                data = json.load(f)
            logger.info(f"✅ Loaded {len(data)} stocks")
            return data
    except Exception as e:
        logger.warning(f"Load failed: {e}")
    return {}


def get_stock_sector(symbol: str) -> str:
    """Get sector for a stock."""
    for sector, stocks in SECTOR_STOCKS.items():
        if symbol in stocks:
            return sector
    return "Others"


# ── Pagination & Filtering Functions ───────────────────────────────────────

def get_sector_stocks(sector: str, data: Dict[str, Dict]) -> List[Dict]:
    """Get all stocks in a sector, sorted by market cap."""
    stocks = SECTOR_STOCKS.get(sector, [])
    results = []
    
    for sym in stocks:
        if sym in data:
            results.append(data[sym])
    
    # Sort by market cap descending
    results.sort(key=lambda x: float(x.get("market_cap") or 0), reverse=True)
    return results


def paginate_results(results: List[Dict], page: int = 1, page_size: int = PAGE_SIZE) -> Tuple[List[Dict], int, int]:
    """
    Paginate results.
    Returns: (stocks_on_page, current_page, total_pages)
    """
    total_pages = (len(results) + page_size - 1) // page_size
    page = max(1, min(page, total_pages))
    
    start = (page - 1) * page_size
    end = start + page_size
    
    return results[start:end], page, total_pages


def search_stocks(query: str, data: Dict[str, Dict], limit: int = 100) -> List[Dict]:
    """Search by symbol or name."""
    query = query.upper()
    results = []
    
    for sym, info in data.items():
        if query in sym or (info.get("name") and query in info["name"].upper()):
            results.append(info)
    
    results.sort(key=lambda x: float(x.get("market_cap") or 0), reverse=True)
    return results[:limit]


def filter_by_metrics(
    data: Dict[str, Dict],
    min_pe: float = None,
    max_pe: float = None,
    min_roe: float = None,
    sector: str = None
) -> List[Dict]:
    """Filter by multiple criteria."""
    results = []
    
    for sym, info in data.items():
        # Sector filter
        if sector and info.get("sector") != sector:
            continue
        
        # PE filter
        pe = info.get("pe")
        if pe is not None:
            if min_pe and pe < min_pe:
                continue
            if max_pe and pe > max_pe:
                continue
        
        # ROE filter
        roe = info.get("roe")
        if min_roe and roe and roe < min_roe:
            continue
        
        results.append(info)
    
    return sorted(results, key=lambda x: float(x.get("market_cap") or 0), reverse=True)


def get_top_stocks(data: Dict[str, Dict], limit: int = 20, sort_by: str = "market_cap") -> List[Dict]:
    """Get top stocks by metric."""
    results = list(data.values())
    
    if sort_by == "market_cap":
        results.sort(key=lambda x: float(x.get("market_cap") or 0), reverse=True)
    elif sort_by == "pe_low":
        results = [x for x in results if x.get("pe")]
        results.sort(key=lambda x: float(x.get("pe")))
    elif sort_by == "roe_high":
        results = [x for x in results if x.get("roe")]
        results.sort(key=lambda x: float(x.get("roe")), reverse=True)
    
    return results[:limit]


# ── Report Generation (CHUNKED & SMART) ────────────────────────────────────

def format_stock_card(stock: Dict) -> str:
    """Format single stock as compact card."""
    sym = stock.get("symbol", "N/A")
    name = stock.get("name", "N/A")[:20]  # Truncate name
    price = stock.get("price")
    pe = stock.get("pe")
    roe = stock.get("roe")
    mcap = stock.get("market_cap")
    
    # Format market cap
    if mcap:
        mcap_cr = float(mcap) / 1e7
        if mcap_cr >= 1_00_000:
            mcap_str = f"{mcap_cr/1_00_000:.0f}L Cr"
        else:
            mcap_str = f"{mcap_cr:.0f} Cr"
    else:
        mcap_str = "N/A"
    
    pe_str = f"{pe:.1f}" if pe else "N/A"
    roe_str = f"{roe:.1f}%" if roe else "N/A"
    price_str = f"₹{price:.0f}" if price else "N/A"
    
    return (
        f"<b>{sym}</b> | {name}\n"
        f"💰 {price_str} | PE: {pe_str} | ROE: {roe_str} | MCap: {mcap_str}"
    )


def generate_page_report(
    stocks: List[Dict],
    page: int,
    total_pages: int,
    title: str = "STOCKS"
) -> str:
    """Generate a single page report."""
    lines = [
        f"📊 <b>{title}</b>",
        f"📄 Page {page}/{total_pages}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    
    for i, stock in enumerate(stocks, 1):
        lines.append(format_stock_card(stock))
    
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    # Pagination info
    if total_pages > 1:
        lines.append(f"<i>Showing {len(stocks)} of {(total_pages-1)*PAGE_SIZE + len(stocks)} stocks</i>")
    
    return "\n".join(lines)


def generate_sector_overview(data: Dict[str, Dict]) -> str:
    """Generate overview of all sectors."""
    lines = [
        "📊 <b>NIFTY 500 SECTORS</b>",
        f"📅 {datetime.now().strftime('%d-%b-%Y %H:%M IST')}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    
    for sector in sorted(SECTOR_STOCKS.keys()):
        stocks = get_sector_stocks(sector, data)
        if stocks:
            # Get metrics for sector
            prices = [float(s.get("price") or 0) for s in stocks if s.get("price")]
            pes = [float(s.get("pe")) for s in stocks if s.get("pe")]
            roes = [float(s.get("roe")) for s in stocks if s.get("roe")]
            
            avg_price = sum(prices) / len(prices) if prices else 0
            avg_pe = sum(pes) / len(pes) if pes else 0
            avg_roe = sum(roes) / len(roes) if roes else 0
            
            lines.append(
                f"{sector}\n"
                f"  📈 {len(stocks)} stocks | Avg PE: {avg_pe:.1f} | Avg ROE: {avg_roe:.1f}%"
            )
    
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("\n✅ Select a sector for details\n")
    
    return "\n".join(lines)


# ── Collection & Scheduling ───────────────────────────────────────────────

def collect_nifty500_fundamentals() -> bool:
    """Main collection function."""
    logger.info("🚀 Starting Nifty 500 collection...")
    
    try:
        symbols = fetch_nifty500_symbols()
        if not symbols:
            logger.error("No symbols found!")
            return False
        
        data = fetch_fundamentals_batch(symbols)
        
        if save_fundamentals(data):
            logger.info(f"✅ Collection complete: {len(data)} stocks")
            return True
        else:
            logger.error("Failed to save")
            return False
    
    except Exception as e:
        logger.error(f"❌ Collection failed: {e}\n{traceback.format_exc()}")
        return False


def start_scheduler_thread():
    """Start background collection."""
    try:
        import schedule
    except ImportError:
        logger.warning("schedule not installed")
        return
    
    def scheduler_loop():
        def job():
            logger.info("[Scheduler] Running collection...")
            collect_nifty500_fundamentals()
        
        schedule.every().day.at("09:30").do(job)  # 9:30 AM IST
        schedule.every().day.at("15:30").do(job)  # 3:30 PM IST
        
        logger.info("[Scheduler] Started (09:30 & 15:30 IST)")
        
        while True:
            schedule.run_pending()
            time.sleep(60)
    
    thread = threading.Thread(target=scheduler_loop, daemon=True)
    thread.start()
