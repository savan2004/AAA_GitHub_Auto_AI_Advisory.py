# swing_trades.py
import os
import time
from datetime import date
from collections import deque, defaultdict

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError
from groq import Groq
import google.generativeai as genai

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- yfinance safe layer (rate limiting + cache) ---
YF_WINDOW_SEC = 60
YF_MAX_CALLS_PER_WINDOW = 10
YF_CALL_TIMES = deque()

CACHE = {}
CACHE_TTL = 900  # 15 minutes

def cache_get(key):
    data = CACHE.get(key)
    if not data:
        return None
    if time.time() - data["ts"] > CACHE_TTL:
        del CACHE[key]
        return None
    return data["val"]

def cache_set(key, val):
    CACHE[key] = {"val": val, "ts": time.time()}

def yf_allow_call():
    now = time.time()
    while YF_CALL_TIMES and now - YF_CALL_TIMES[0] > YF_WINDOW_SEC:
        YF_CALL_TIMES.popleft()
    return len(YF_CALL_TIMES) < YF_MAX_CALLS_PER_WINDOW

def yf_register_call():
    YF_CALL_TIMES.append(time.time())

def safe_history(ticker, period="6mo", interval="1d") -> pd.DataFrame:
    key = f"sw:{ticker}:{period}:{interval}"
    cached = cache_get(key)
    if cached is not None:
        return cached

    if not yf_allow_call():
        cached = cache_get(key)
        if cached is not None:
            return cached
        return pd.DataFrame()

    try:
        yf_register_call()
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if not df.empty:
            cache_set(key, df)
            return df
    except YFRateLimitError:
        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame()

# --- indicators ---
def ema(series: pd.Series, span: int):
    return series.ewm(span=span, adjust=False).mean()

def sma(series: pd.Series, window: int):
    return series.rolling(window).mean()

def bollinger_bands(series: pd.Series, window=20, num_sd=2):
    sma = series.rolling(window).mean()
    rstd = series.rolling(window).std()
    upper = sma + num_sd * rstd
    lower = sma - num_sd * rstd
    return sma, upper, lower

def adx(df: pd.DataFrame, period: int = 14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    plus_dm = high.diff()
    minus_dm = low.diff().abs()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).sum() / atr)
    minus_di = 100 * (minus_dm.rolling(period).sum() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx_val = dx.rolling(period).mean()
    return adx_val, plus_di, minus_di

def rsi(series: pd.Series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def macd(series: pd.Series):
    exp1 = series.ewm(span=12, adjust=False).mean()
    exp2 = series.ewm(span=26, adjust=False).mean()
    macd_line = exp1 - exp2
    signal = macd_line.ewm(span=9, adjust=False).mean()
    return macd_line, signal

# --- strict swing criteria (8 checks) ---
def swing_score(df: pd.DataFrame, side: str = "LONG") -> dict:
    """
    Evaluate a stock for LONG or SHORT.
    Returns dict with:
        - score (0-8): number of conditions met
        - details: list of met conditions
        - price, indicators for display
    """
    if df.empty or len(df) < 200:
        return {"score": 0, "details": [], "ltp": None}

    close = df["Close"]
    ltp = float(close.iloc[-1])

    # Indicators
    ema50 = ema(close, 50).iloc[-1]
    ema200 = ema(close, 200).iloc[-1]
    bb_mid, bb_upper, bb_lower = bollinger_bands(close, 20, 2)
    bb_mid = bb_mid.iloc[-1]
    bb_upper = bb_upper.iloc[-1]
    bb_lower = bb_lower.iloc[-1]

    adx_val, plus_di, minus_di = adx(df, 14)
    adx_last = adx_val.iloc[-1]
    plus_di_last = plus_di.iloc[-1]
    minus_di_last = minus_di.iloc[-1]

    rsi_val = rsi(close, 14).iloc[-1]
    macd_line, signal_line = macd(close)
    macd_last = macd_line.iloc[-1]
    signal_last = signal_line.iloc[-1]

    vol_avg = df["Volume"].rolling(20).mean().iloc[-1]
    vol_last = df["Volume"].iloc[-1]

    # Recent swing high/low (20-day)
    recent_high = close.rolling(20).max().iloc[-1]
    recent_low = close.rolling(20).min().iloc[-1]

    # Conditions list (8)
    conditions = []
    score = 0

    if side == "LONG":
        # 1. Trend: price > EMA50 > EMA200
        if ltp > ema50 > ema200:
            conditions.append("✓ Trend: price > 50EMA > 200EMA")
            score += 1
        # 2. Price within BB (between lower and mid)
        if bb_lower <= ltp <= bb_mid:
            conditions.append("✓ Price within lower–mid BB")
            score += 1
        # 3. ADX > 25 and +DI > -DI
        if adx_last > 25 and plus_di_last > minus_di_last:
            conditions.append(f"✓ ADX {adx_last:.1f} >25, +DI > -DI")
            score += 1
        # 4. Volume > 20-day avg
        if vol_last > vol_avg:
            conditions.append("✓ Volume > 20-day avg")
            score += 1
        # 5. RSI between 40-70
        if 40 <= rsi_val <= 70:
            conditions.append(f"✓ RSI {rsi_val:.1f} in 40-70")
            score += 1
        # 6. MACD line above signal
        if macd_last > signal_last:
            conditions.append("✓ MACD above signal")
            score += 1
        # 7. Price near recent high (within 3% of 20-day high)
        if ltp >= recent_high * 0.97:
            conditions.append("✓ Near recent 20-day high")
            score += 1
        # 8. Price within 3% of 50EMA (pullback to support)
        if abs(ltp - ema50) / ema50 < 0.03:
            conditions.append("✓ Price near 50EMA support")
            score += 1

    else:  # SHORT
        # 1. Trend: price < EMA50 < EMA200
        if ltp < ema50 < ema200:
            conditions.append("✓ Trend: price < 50EMA < 200EMA")
            score += 1
        # 2. Price within BB (between mid and upper)
        if bb_mid <= ltp <= bb_upper:
            conditions.append("✓ Price within mid–upper BB")
            score += 1
        # 3. ADX > 25 and -DI > +DI
        if adx_last > 25 and minus_di_last > plus_di_last:
            conditions.append(f"✓ ADX {adx_last:.1f} >25, -DI > +DI")
            score += 1
        # 4. Volume > 20-day avg
        if vol_last > vol_avg:
            conditions.append("✓ Volume > 20-day avg")
            score += 1
        # 5. RSI between 30-60
        if 30 <= rsi_val <= 60:
            conditions.append(f"✓ RSI {rsi_val:.1f} in 30-60")
            score += 1
        # 6. MACD line below signal
        if macd_last < signal_last:
            conditions.append("✓ MACD below signal")
            score += 1
        # 7. Price near recent low (within 3% of 20-day low)
        if ltp <= recent_low * 1.03:
            conditions.append("✓ Near recent 20-day low")
            score += 1
        # 8. Price near 50EMA resistance
        if abs(ltp - ema50) / ema50 < 0.03:
            conditions.append("✓ Price near 50EMA resistance")
            score += 1

    return {
        "score": score,
        "details": conditions,
        "ltp": ltp,
        "ema50": ema50,
        "ema200": ema200,
        "adx": adx_last,
        "rsi": rsi_val,
        "macd": macd_last,
        "signal": signal_last,
        "volume": vol_last,
        "avg_volume": vol_avg,
        "bb_mid": bb_mid,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "recent_high": recent_high,
        "recent_low": recent_low,
    }

# --- AI explanation (optional) ---
def _ai_call(prompt: str, max_tokens: int = 600) -> str:
    if GROQ_API_KEY:
        try:
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.3,
            )
            return resp.choices[0].message.content
        except Exception:
            pass
    if GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content(prompt)
            return resp.text
        except Exception:
            pass
    return "AI explanation temporarily unavailable."

def explain_swing(symbol: str, side: str, score_data: dict) -> str:
    prompt = f"""
You are an expert swing trader. Analyze {symbol} (NSE) for a potential {side} swing trade.

Current price: ₹{score_data['ltp']:.2f}
Score: {score_data['score']}/8 conditions met.
Key indicators:
- 50EMA: ₹{score_data['ema50']:.2f}, 200EMA: ₹{score_data['ema200']:.2f}
- ADX: {score_data['adx']:.1f}, RSI: {score_data['rsi']:.1f}
- MACD: {score_data['macd']:.2f}, Signal: {score_data['signal']:.2f}
- Volume: {score_data['volume']:.0f} (avg {score_data['avg_volume']:.0f})
- Bollinger Bands: mid {score_data['bb_mid']:.2f}, upper {score_data['bb_upper']:.2f}, lower {score_data['bb_lower']:.2f}
- Recent high: {score_data['recent_high']:.2f}, recent low: {score_data['recent_low']:.2f}

Met conditions:
{chr(10).join(score_data['details'])}

Explain briefly:
- Why this looks like a {side} setup (strengths)
- Entry zone, stop-loss, and target (qualitative, not exact numbers)
- Key risks
- End with "Note: Educational example, not a recommendation."

Max 180 words.
"""
    return _ai_call(prompt, max_tokens=350)

# --- watchlist ---
WATCHLIST = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "ITC",
    "LT", "AXISBANK", "KOTAKBANK", "BHARTIARTL", "HINDUNILVR", "MARUTI",
    "TATAMOTORS", "TITAN", "SUNPHARMA", "ONGC", "NTPC", "M&M", "POWERGRID",
    "ULTRACEMCO", "BAJFINANCE", "BAJAJFINSV", "WIPRO", "HCLTECH", "ASIANPAINT",
    "ADANIPORTS", "GRASIM", "JSWSTEEL", "TATASTEEL"
]

# --- main function called by bot ---
_cached_swing = {"date": None, "text": ""}

def get_swing_trades(risk_tolerance: str = "conservative") -> str:
    """
    Returns swing trade analysis.
    risk_tolerance: 'conservative' (only strict 8/8) or 'aggressive' (includes scores >=6)
    """
    today = date.today().isoformat()
    if _cached_swing["date"] == today and _cached_swing["text"]:
        return _cached_swing["text"]

    strict_trades = []   # score == 8
    near_trades = []     # score >=6

    for sym in WATCHLIST:
        ticker = f"{sym}.NS"
        df = safe_history(ticker, period="6mo", interval="1d")
        if df.empty:
            continue
        # Check both sides
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
        lines.append("✅ STRICT SWING TRADES (Score 8/8 – High Conviction)")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        for side, sym, data in strict_trades:
            lines.append(f"{sym} – {side}")
            lines.append(f"Price: ₹{data['ltp']:.2f} | ADX: {data['adx']:.1f} | RSI: {data['rsi']:.1f}")
            lines.append("✓ Conditions met:")
            for cond in data['details']:
                lines.append(f"  {cond}")
            if GROQ_API_KEY or GEMINI_API_KEY:
                ai_text = explain_swing(sym, side, data)
                lines.append(f"🧠 AI Insight:\n{ai_text}")
            lines.append("")
    elif near_trades and risk_tolerance == "aggressive":
        lines.append("⚠️ NEAREST POSSIBLE TRADES (Score 6-7 – For Aggressive Traders Only)")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        for side, sym, data in near_trades[:3]:  # limit to 3
            lines.append(f"{sym} – {side} (Score {data['score']}/8)")
            lines.append(f"Price: ₹{data['ltp']:.2f} | ADX: {data['adx']:.1f} | RSI: {data['rsi']:.1f}")
            lines.append("✓ Conditions met:")
            for cond in data['details']:
                lines.append(f"  {cond}")
            lines.append("")
    else:
        lines.append("📭 NO SWING TRADES TODAY")
        lines.append("Strict criteria (8/8) not met by any stock in watchlist.")
        if risk_tolerance == "aggressive":
            lines.append("No stocks scored ≥6 either. Check again later.")

    lines.append("\n⚠️ Disclaimer: Educational technical analysis only. Not investment advice.")
    final = "\n".join(lines)
    _cached_swing["date"] = today
    _cached_swing["text"] = final
    return final
