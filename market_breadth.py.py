# market_breadth.py - Live market data with correct timestamps

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import logging
from collections import defaultdict
import pytz

logger = logging.getLogger(__name__)

# Cache for market data
_market_cache = {
    "data": None,
    "timestamp": None,
    "last_update": None
}

# Indian timezone
IST = pytz.timezone('Asia/Kolkata')

def is_market_hours():
    """Check if current time is within market hours (9:15 AM - 3:30 PM IST)"""
    now = datetime.now(IST)
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    if now.weekday() >= 5:
        return False
    
    return market_start <= now <= market_end

def should_update_cache():
    """Check if cache should be updated (every 30 min during market hours)"""
    if _market_cache["last_update"] is None:
        return True
    
    now = datetime.now(IST)
    last = _market_cache["last_update"]
    
    if not is_market_hours():
        return False
    
    return (now - last).total_seconds() >= 1800

def get_nifty_constituents():
    """Return current Nifty 50 constituents"""
    return [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "ITC",
        "KOTAKBANK", "SBIN", "BHARTIARTL", "LT", "WIPRO", "HCLTECH", "ASIANPAINT",
        "MARUTI", "TATAMOTORS", "TITAN", "SUNPHARMA", "ONGC", "NTPC", "M&M",
        "POWERGRID", "ULTRACEMCO", "BAJFINANCE", "BAJAJFINSV", "TATACONSUM",
        "HDFCLIFE", "SBILIFE", "BRITANNIA", "INDUSINDBK", "CIPLA", "DRREDDY",
        "DIVISLAB", "GRASIM", "HINDALCO", "JSWSTEEL", "TECHM", "BPCL", "IOC",
        "HEROMOTOCO", "EICHERMOT", "COALINDIA", "SHREECEM", "UPL", "ADANIPORTS",
        "AXISBANK", "BAJAJ-AUTO", "NESTLE", "TATASTEEL"
    ]

def get_market_data():
    """Fetch fresh market data"""
    indices = {
        "NIFTY 50": "^NSEI",
        "BANK NIFTY": "^NSEBANK",
        "NIFTY IT": "^CNXIT",
        "NIFTY AUTO": "^CNXAUTO"
    }
    
    ind_data = {}
    
    for name, sym in indices.items():
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="1d", interval="1m")
            
            if not hist.empty:
                last = hist['Close'].iloc[-1]
                today_data = ticker.history(period="2d")
                
                if len(today_data) >= 2:
                    prev_close = today_data['Close'].iloc[-2]
                else:
                    prev_close = hist['Open'].iloc[0] if not hist.empty else last
                
                change = ((last - prev_close) / prev_close) * 100 if prev_close != 0 else 0
                ind_data[name] = (last, change)
            else:
                ind_data[name] = (0, 0)
        except Exception as e:
            logger.error(f"Error fetching {name}: {e}")
            ind_data[name] = (0, 0)
    
    constituents = get_nifty_constituents()
    advances = declines = unchanged = 0
    sector_perf = defaultdict(lambda: {'adv': 0, 'dec': 0, 'total': 0})
    
    for sym in constituents:
        try:
            ticker = yf.Ticker(f"{sym}.NS")
            hist = ticker.history(period="2d")
            
            if len(hist) >= 2:
                prev_close = hist['Close'].iloc[-2]
                last_price = hist['Close'].iloc[-1]
                change = last_price - prev_close
                
                if change > 0.01:
                    advances += 1
                elif change < -0.01:
                    declines += 1
                else:
                    unchanged += 1
                
                info = ticker.info
                sector = info.get('sector', 'Other')
                
                if change > 0.01:
                    sector_perf[sector]['adv'] += 1
                elif change < -0.01:
                    sector_perf[sector]['dec'] += 1
                sector_perf[sector]['total'] += 1
        except Exception as e:
            continue
    
    total_stocks = advances + declines + unchanged
    
    return {
        "indices": ind_data,
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "total": total_stocks,
        "sector_perf": dict(sector_perf),
        "timestamp": datetime.now(IST)
    }

def format_market_breadth():
    """Return formatted market breadth"""
    global _market_cache
    
    if should_update_cache() or _market_cache["data"] is None:
        try:
            _market_cache["data"] = get_market_data()
            _market_cache["timestamp"] = datetime.now(IST)
            _market_cache["last_update"] = datetime.now(IST)
        except Exception as e:
            logger.error(f"Failed to update market data: {e}")
            if _market_cache["data"] is None:
                return "📊 Market data temporarily unavailable."
    
    data = _market_cache["data"]
    timestamp = data["timestamp"].strftime("%d-%b-%Y %I:%M %p")
    
    text = f"📊 <b>Market Breadth (NSE)</b> – {timestamp}\n\n"
    
    for name, (last, chg) in data["indices"].items():
        arrow = "🟢" if chg > 0.1 else "🔴" if chg < -0.1 else "⚪"
        text += f"{arrow} {name}: {last:,.2f} ({chg:+.2f}%)\n"
    
    adv = data["advances"]
    dec = data["declines"]
    unc = data["unchanged"]
    total = data["total"]
    
    text += f"\n📈 Advances: {adv}\n📉 Declines: {dec}\n⚖️ Unchanged: {unc}\n"
    
    ratio = adv / dec if dec > 0 else float(adv)
    text += f"🔄 A/D Ratio: {ratio:.2f} (out of {total} stocks)\n\n"
    
    text += "🏭 <b>Sector Snapshot</b>\n"
    
    sector_net = {s: p['adv'] - p['dec'] for s, p in data["sector_perf"].items()}
    sorted_sectors = sorted(sector_net.items(), key=lambda x: x[1], reverse=True)[:5]
    
    for sector, net in sorted_sectors:
        perf = data["sector_perf"][sector]
        arrow = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"
        text += f"{arrow} {sector}: {perf['adv']} up, {perf['dec']} down\n"
    
    if is_market_hours():
        next_update = _market_cache["last_update"] + timedelta(minutes=30)
        time_to_next = (next_update - datetime.now(IST)).seconds // 60
        text += f"\n⏱️ Next update in {time_to_next} minutes"
    else:
        text += "\n⏱️ Market closed"
    
    return text