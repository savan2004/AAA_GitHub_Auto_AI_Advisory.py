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

# --- yfinance safe layer (rate limiting & cache) ---
YF_WINDOW_SEC = 60
YF_MAX_CALLS_PER_WINDOW = 10
YF_CALL_TIMES = deque()

CACHE = {}
CACHE_TTL = 900  # 15 minutes

def cache_get(key: str):
    data = CACHE.get(key)
    if not data: return None
    if time.time() - data['ts'] > CACHE_TTL:
        del CACHE[key]
        return None
    return data['val']

def cache_set(key: str, val):
    CACHE[key] = {'val': val, 'ts': time.time()}

def yf_allow_call() -> bool:
    now = time.time()
    while YF_CALL_TIMES and now - YF_CALL_TIMES[0] > YF_WINDOW_SEC:
        YF_CALL_TIMES.popleft()
    return len(YF_CALL_TIMES) < YF_MAX_CALLS_PER_WINDOW

def yf_register_call():
    YF_CALL_TIMES.append(time.time())

def safe_history(ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    key = f"sw_{ticker}_{period}_{interval}"
    cached = cache_get(key)
    if cached is not None:
        return cached

    if not yf_allow_call():
        cached = cache_get(key)
        if cached is not None: return cached
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
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()

def bollinger_bands(series: pd.Series, window: int = 20, num_sd: int = 2):
    sma = series.rolling(window).mean()
    rstd = series.rolling(window).std()
    upper = sma + num_sd * rstd
    lower = sma - num_sd * rstd
    return sma, upper, lower

def adx(df: pd.DataFrame, period: int = 14):
    high = df['High']
    low = df['Low']
    close = df['Close']
    
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

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
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

def swing_score(df: pd.DataFrame, side: str = "LONG") -> dict:
    """Evaluate a stock for LONG or SHORT. Returns dict with:
    - score (0-8): number of conditions met
    - details: list of met conditions
    - price, indicators for display
    """
    if df.empty or len(df) < 200:
        return {"score": 0, "details": [], "ltp": None}

    close = df['Close']
    ltp = float(close.iloc[-1])
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
    vol_avg = df['Volume'].rolling(20).mean().iloc[-1]
    vol_last = df['Volume'].iloc[-1]
    recent_high = close.rolling(20).max().iloc[-1]
    recent_low = close.rolling(20).min().iloc[-1]

    conditions = []
    score = 0

    # --- strict swing criteria (8 checks) ---
    if side == "LONG":
        if ltp > ema50 > ema200: conditions.append("Trend (price > 50EMA > 200EMA)"); score += 1
        if bb_lower < ltp < bb_mid: conditions.append("Price within lower-mid BB"); score += 1
        if adx_last > 25 and plus_di_last > minus_di_last: conditions.append(f"ADX ({adx_last:.1f}) > 25, +DI > -DI"); score += 1
        if vol_last > vol_avg: conditions.append("Volume > 20-day avg"); score += 1
        if 40 < rsi_val < 70: conditions.append(f"RSI ({rsi_val:.1f}) in 40-70"); score += 1
        if macd_last > signal_last: conditions.append("MACD above signal"); score += 1
        if ltp > recent_high * 0.97: conditions.append("Near recent 20-day high"); score += 1
        if abs(ltp - ema50)/ema50 < 0.03: conditions.append("Price near 50EMA support"); score += 1
    else:  # SHORT
        if ltp < ema50 < ema200: conditions.append("Trend (price < 50EMA < 200EMA)"); score += 1
        if bb_mid < ltp < bb_upper: conditions.append("Price within mid-upper BB"); score += 1
        if adx_last > 25 and minus_di_last > plus_di_last: conditions.append(f"ADX ({adx_last:.1f}) > 25, -DI > +DI"); score += 1
        if vol_last > vol_avg: conditions.append("Volume > 20-day avg"); score += 1
        if 30 < rsi_val < 60: conditions.append(f"RSI ({rsi_val:.1f}) in 30-60"); score += 1
        if macd_last < signal_last: conditions.append("MACD below signal"); score += 1
        if ltp < recent_low * 1.03: conditions.append("Near recent 20-day low"); score += 1
        if abs(ltp - ema50)/ema50 < 0.03: conditions.append("Price near 50EMA resistance"); score += 1

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
def ai_call(prompt: str, max_tokens: int = 600) -> str:
    if GROQ_API_KEY:
        try:
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.35,
            )
            return resp.choices[0].message.content or "".strip()
        except Exception as e:
            logger.warning(f"Groq swing error: {e}")
            
    if GEMINI_API_KEY:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel('gemini-1.5-flash')
            resp = model.generate_content(prompt)
            return getattr(resp, "text", "") or "".strip()
        except Exception as e:
            logger.warning(f"Gemini swing error: {e}")
            
    return ""
