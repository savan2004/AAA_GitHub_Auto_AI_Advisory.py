# swing_trades.py - Swing trade scanner with 8-point criteria

import yfinance as yf
import pandas as pd
import logging
from datetime import date
from collections import deque

logger = logging.getLogger(__name__)

# Rate limiting
YF_WINDOW_SEC = 60
YF_MAX_CALLS_PER_WINDOW = 10
YF_CALL_TIMES = deque()

# Cache
CACHE = {}
CACHE_TTL = 900

def cache_get(key):
    data = CACHE.get(key)
    if not data:
        return None
    if date.today().isoformat() != data.get("date"):
        del CACHE[key]
        return None
    return data.get("val")

def cache_set(key, val):
    CACHE[key] = {"val": val, "date": date.today().isoformat()}

def yf_allow_call():
    now = date.today()
    while YF_CALL_TIMES and (now - YF_CALL_TIMES[0]).days > 0:
        YF_CALL_TIMES.popleft()
    return len(YF_CALL_TIMES) < YF_MAX_CALLS_PER_WINDOW

def safe_history(ticker, period="6mo", interval="1d"):
    key = f"{ticker}:{period}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if not df.empty:
            cache_set(key, df)
            return df
    except Exception as e:
        logger.error(f"yfinance error for {ticker}: {e}")
    
    return pd.DataFrame()

WATCHLIST = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "ITC",
    "LT", "AXISBANK", "KOTAKBANK", "BHARTIARTL", "HINDUNILVR", "MARUTI",
    "TATAMOTORS", "TITAN", "SUNPHARMA", "ONGC", "NTPC", "M&M"
]

def swing_score(df: pd.DataFrame, side: str = "LONG") -> dict:
    """Score a stock for swing trade (0-8)"""
    if df.empty or len(df) < 200:
        return {"score": 0, "details": []}
    
    close = df["Close"]
    ltp = float(close.iloc[-1])
    
    # Indicators
    ema50 = close.ewm(span=50).mean().iloc[-1]
    ema200 = close.ewm(span=200).mean().iloc[-1]
    
    # Bollinger Bands
    sma = close.rolling(20).mean()
    std = close.rolling(20).std()
    bb_mid = sma.iloc[-1]
    bb_lower = (sma - 2*std).iloc[-1]
    bb_upper = (sma + 2*std).iloc[-1]
    
    # ADX
    high = df["High"]
    low = df["Low"]
    
    plus_dm = high.diff()
    minus_dm = low.diff().abs()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    
    atr = tr.rolling(14).mean()
    plus_di = 100 * (plus_dm.rolling(14).sum() / atr)
    minus_di = 100 * (minus_dm.rolling(14).sum() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.rolling(14).mean().iloc[-1]
    
    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    rsi = (100 - (100 / (1 + rs))).iloc[-1]
    
    # Volume
    vol_avg = df["Volume"].rolling(20).mean().iloc[-1]
    vol_last = df["Volume"].iloc[-1]
    
    # Recent high/low
    recent_high = close.rolling(20).max().iloc[-1]
    recent_low = close.rolling(20).min().iloc[-1]
    
    conditions = []
    score = 0
    
    if side == "LONG":
        if ltp > ema50 > ema200:
            conditions.append("✓ Trend: price > 50EMA > 200EMA")
            score += 1
        if bb_lower <= ltp <= bb_mid:
            conditions.append("✓ Price within lower-mid BB")
            score += 1
        if adx > 25 and plus_di.iloc[-1] > minus_di.iloc[-1]:
            conditions.append(f"✓ ADX {adx:.1f} >25, +DI > -DI")
            score += 1
        if vol_last > vol_avg:
            conditions.append("✓ Volume > 20-day avg")
            score += 1
        if 40 <= rsi <= 70:
            conditions.append(f"✓ RSI {rsi:.1f} in 40-70")
            score += 1
        if ltp >= recent_high * 0.97:
            conditions.append("✓ Near recent high")
            score += 1
        if abs(ltp - ema50) / ema50 < 0.03:
            conditions.append("✓ Near 50EMA support")
            score += 1
            
    else:  # SHORT
        if ltp < ema50 < ema200:
            conditions.append("✓ Trend: price < 50EMA < 200EMA")
            score += 1
        if bb_mid <= ltp <= bb_upper:
            conditions.append("✓ Price within mid-upper BB")
            score += 1
        if adx > 25 and minus_di.iloc[-1] > plus_di.iloc[-1]:
            conditions.append(f"✓ ADX {adx:.1f} >25, -DI > +DI")
            score += 1
        if vol_last > vol_avg:
            conditions.append("✓ Volume > 20-day avg")
            score += 1
        if 30 <= rsi <= 60:
            conditions.append(f"✓ RSI {rsi:.1f} in 30-60")
            score += 1
        if ltp <= recent_low * 1.03:
            conditions.append("✓ Near recent low")
            score += 1
        if abs(ltp - ema50) / ema50 < 0.03:
            conditions.append("✓ Near 50EMA resistance")
            score += 1
    
    return {
        "score": score,
        "details": conditions[:8],
        "ltp": ltp,
        "adx": adx,
        "rsi": rsi
    }

_cached_swing = {"date": None, "text": ""}

def get_swing_trades(risk_tolerance: str = "conservative") -> str:
    """Get swing trade recommendations"""
    today = date.today().isoformat()
    
    if _cached_swing["date"] == today and _cached_swing["text"]:
        return _cached_swing["text"]
    
    strict_trades = []
    near_trades = []
    
    for sym in WATCHLIST:
        ticker = f"{sym}.NS"
        df = safe_history(ticker, period="6mo", interval="1d")
        if df.empty:
            continue
        
        long_score = swing_score(df, "LONG")
        short_score = swing_score(df, "SHORT")
        
        if long_score["score"] == 8:
            strict_trades.append(("LONG", sym, long_score))
        elif long_score["score"] >= 6:
            near_trades.append(("LONG", sym, long_score))
        
        if short_score["score"] == 8:
            strict_trades.append(("SHORT", sym, short_score))
        elif short_score["score"] >= 6:
            near_trades.append(("SHORT", sym, short_score))
    
    lines = []
    
    if strict_trades:
        lines.append("✅ STRICT TRADES (Score 8/8)")
        lines.append("━━━━━━━━━━━━━━━━")
        for side, sym, data in strict_trades[:3]:
            lines.append(f"{sym} – {side}")
            lines.append(f"Price: ₹{data['ltp']:.2f} | ADX: {data['adx']:.1f}")
            for cond in data['details'][:4]:
                lines.append(f"  {cond}")
            lines.append("")
    
    elif near_trades and risk_tolerance == "aggressive":
        lines.append("⚠️ NEAR TRADES (Score 6-7)")
        lines.append("━━━━━━━━━━━━━━━━━━")
        for side, sym, data in near_trades[:3]:
            lines.append(f"{sym} – {side} (Score {data['score']}/8)")
            lines.append(f"Price: ₹{data['ltp']:.2f}")
            lines.append("")
    
    else:
        lines.append("📭 NO TRADES TODAY")
        if near_trades:
            lines.append("Try Aggressive mode for near-trades (score 6-7)")
    
    lines.append("\n⚠️ Educational only")
    
    final = "\n".join(lines)
    _cached_swing["date"] = today
    _cached_swing["text"] = final
    return final