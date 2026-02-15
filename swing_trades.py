# swing_trades.py
import os
import time
from datetime import date
from collections import deque

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from groq import Groq
import google.generativeai as genai

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- yfinance safe layer (independent from main) ---

YF_WINDOW_SEC = 60
YF_MAX_CALLS_PER_WINDOW = 10
YF_CALL_TIMES = deque()

CACHE = {}
CACHE_TTL = 900  # 15 min

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
    return adx_val

# --- strict swing rule ---

def swing_signal(df: pd.DataFrame):
    if df.empty or len(df) < 250:
        return {"signal": "NONE"}

    close = df["Close"]
    ltp = float(close.iloc[-1])

    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    ema200 = ema(close, 200)
    bb_mid, bb_up, bb_low = bollinger_bands(close, 20, 2)
    adx_val = adx(df, 14)

    e20 = float(ema20.iloc[-1])
    e50 = float(ema50.iloc[-1])
    e200 = float(ema200.iloc[-1])
    bbm = float(bb_mid.iloc[-1])
    bbu = float(bb_up.iloc[-1])
    bbl = float(bb_low.iloc[-1])
    adx_last = float(adx_val.iloc[-1])

    # Example long filter: uptrend + pullback + strong trend
    long_trend_ok = (ltp > e200) and (e50 > e200)
    long_pullback = (bbl <= ltp <= bbm) and (ltp >= e20 * 0.98)
    long_adx = adx_last >= 25

    if long_trend_ok and long_pullback and long_adx:
        return {
            "signal": "LONG",
            "ltp": ltp,
            "ema20": e20,
            "ema50": e50,
            "ema200": e200,
            "bb_mid": bbm,
            "bb_up": bbu,
            "bb_low": bbl,
            "adx": adx_last,
        }

    # Example short filter: downtrend + pullback + strong trend
    short_trend_ok = (ltp < e200) and (e50 < e200)
    short_pullback = (bbm <= ltp <= bbu) and (ltp <= e20 * 1.02)
    short_adx = adx_last >= 25

    if short_trend_ok and short_pullback and short_adx:
        return {
            "signal": "SHORT",
            "ltp": ltp,
            "ema20": e20,
            "ema50": e50,
            "ema200": e200,
            "bb_mid": bbm,
            "bb_up": bbu,
            "bb_low": bbl,
            "adx": adx_last,
        }

    return {"signal": "NONE"}

# --- AI explanation ---

def _ai_explain_swing(symbol: str, sig: dict) -> str:
    side = sig["signal"]
    ltp = sig["ltp"]
    adx_val = sig["adx"]
    e20, e50, e200 = sig["ema20"], sig["ema50"], sig["ema200"]
    bbm, bbu, bbl = sig["bb_mid"], sig["bb_up"], sig["bb_low"]

    prompt = f"""
You are an Indian swing trader.

Stock: {symbol} on NSE.
Latest price: {ltp:.2f}
Signal: {side}
EMA20: {e20:.2f}, EMA50: {e50:.2f}, EMA200: {e200:.2f}
BB upper/mid/lower: {bbu:.2f} / {bbm:.2f} / {bbl:.2f}
ADX(14): {adx_val:.2f}

Explain WHY this looks like a potential {side} swing setup:
- confirm trend direction and strength (ADX, EMAs, BB)
- describe ideal entry zone and invalidation zone qualitatively (no exact price)
- mention 3 key risks
- end with a strong disclaimer: purely educational, not a recommendation.

Max 200 words.
"""

    if GROQ_API_KEY:
        try:
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
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

# --- public API for bot ---

_cached_daily = {"date": None, "text": ""}

WATCHLIST = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK",
    "SBIN", "INFY", "ITC", "LT", "AXISBANK", "KOTAKBANK",
]

def get_daily_swing_trades() -> str:
    today = date.today().isoformat()
    if _cached_daily["date"] == today and _cached_daily["text"]:
        return _cached_daily["text"]

    candidates = []
    for sym in WATCHLIST:
        t = f"{sym}.NS"
        df = safe_history(t, period="6mo", interval="1d")
        sig = swing_signal(df)
        if sig["signal"] in ["LONG", "SHORT"]:
            candidates.append((sym, sig))

    if not candidates:
        text = "📊 Swing Trades\nNo high-confidence setups today as per EMA20/50/200 + BB + ADX rules."
        _cached_daily["date"] = today
        _cached_daily["text"] = text
        return text

    ideas = candidates[:2]
    lines = ["📊 Swing Trades (Rules-based, Educational)\n"]
    for sym, sig in ideas:
        explanation = _ai_explain_swing(sym, sig)
        lines.append(f"*{sym}* – {sig['signal']} setup\n{explanation}\n")

    final = "\n".join(lines) + "\nDisclaimer: Educational technical analysis only, not trade advice."
    _cached_daily["date"] = today
    _cached_daily["text"] = final
    return final
