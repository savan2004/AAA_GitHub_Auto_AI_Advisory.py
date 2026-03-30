import os
import time
import logging
from datetime import date
import pandas as pd

logger = logging.getLogger(__name__)

# ── Data engine (rate-limit resistant multi-source fetcher) ──────────────────
from data_engine import get_hist, calc_rsi, calc_ema

try:
    from groq import Groq
except ImportError:
    Groq = None  # fallback

try:
    import google.generativeai as genai
except ImportError:
    genai = None

GROQ_API_KEY    = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")

# Gemini configured lazily inside ai_call() to avoid startup crash

# safe_history is now a thin wrapper around data_engine.get_hist
# (keeps the existing call-sites working without changes)
def safe_history(ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """
    Fetch OHLCV history via data_engine (multi-source, rate-limit safe).
    `ticker` is expected in Yahoo Finance format (e.g. "RELIANCE.NS").
    `interval` is currently unused because data_engine returns daily data;
    intraday intervals can be added to data_engine later.
    """
    sym = ticker.replace(".NS", "").replace(".NSE", "")
    return get_hist(sym, period=period)

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
    if df.empty or len(df) < 100:
        return {"score": 0, "details": [], "ltp": None}

    close = df['Close']
    ltp = float(close.iloc[-1])
    n = len(close)
    ema50  = ema(close, min(50,  n-1)).iloc[-1]
    ema200 = ema(close, min(200, n-1)).iloc[-1]
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

    # Real ATR calculation
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr_val = float(tr.rolling(14).mean().iloc[-1])

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
        "atr_val": atr_val,
    }

# --- AI explanation (optional) ---
def ai_call(prompt: str, max_tokens: int = 600) -> str:
    # Re-read keys at call time so Render env updates are picked up
    groq_key   = os.getenv("GROQ_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()

    if groq_key and Groq:
        try:
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role":"system","content":"You are an Indian equity analyst. Be concise."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=0.35,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            logger.warning(f"Groq swing error: {e}")

    if gemini_key and genai:
        try:
            genai.configure(api_key=gemini_key)
            # Use gemini-1.5-flash (stable); gemini-2.0-flash was deprecated
            for model_name in ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]:
                try:
                    model = genai.GenerativeModel(model_name)
                    resp = model.generate_content(prompt)
                    text = (getattr(resp, "text", "") or "").strip()
                    if text:
                        return text
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Gemini swing error: {e}")

    return ""

# ─────────────────────────────────────────
# SWING TRADE SCANNER
# ─────────────────────────────────────────
CANDIDATES = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS", "LT.NS",
    "WIPRO.NS", "HCLTECH.NS", "ASIANPAINT.NS", "MARUTI.NS", "TATAMOTORS.NS",
    "TITAN.NS", "SUNPHARMA.NS", "ONGC.NS", "NTPC.NS", "M&M.NS",
    "BAJFINANCE.NS", "AXISBANK.NS", "TECHM.NS", "DRREDDY.NS", "DIVISLAB.NS",
    "HINDALCO.NS", "JSWSTEEL.NS", "TATASTEEL.NS", "BPCL.NS", "EICHERMOT.NS",
]

def _display_sym(sym: str) -> str:
    """Strip .NS suffix for clean display in messages."""
    return sym.replace(".NS", "")

def get_swing_trades(mode: str = "conservative") -> str:
    """
    Scan CANDIDATES for swing trade setups.
    mode="conservative" -> requires score >= 6/8
    mode="aggressive"   -> requires score >= 5/8
    Returns a formatted Telegram message string.
    """
    # threshold: conservative=6, aggressive=5
    threshold = 6 if mode == "conservative" else 5
    today = date.today().strftime("%d-%b-%Y")

    long_picks = []
    short_picks = []

    for sym in CANDIDATES:
        try:
            df = safe_history(sym, period="1y", interval="1d")
            if df.empty or len(df) < 200:
                continue
            for side in ["LONG", "SHORT"]:
                result = swing_score(df, side)
                if result["score"] >= threshold:
                    result["symbol"] = sym
                    result["side"] = side
                    if side == "LONG":
                        long_picks.append(result)
                    else:
                        short_picks.append(result)
        except Exception:
            continue

    # Sort by score descending
    long_picks.sort(key=lambda x: x["score"], reverse=True)
    short_picks.sort(key=lambda x: x["score"], reverse=True)

    label = "Conservative (6+/8)" if mode == "conservative" else "Aggressive (5+/8)"
    lines = [
        f"📈 <b>Swing Trades – {label}</b>",
        f"📅 {today}  |  Threshold: {threshold}/8 conditions\n",
    ]

    if not long_picks and not short_picks:
        # Show closest-to-qualifying stocks instead of empty message
        all_results = []
        for sym in CANDIDATES:
            try:
                df = safe_history(sym, period="1y", interval="1d")
                if df.empty or len(df) < 100: continue
                for side in ["LONG", "SHORT"]:
                    r = swing_score(df, side)
                    if r["ltp"]:
                        r["symbol"] = sym
                        r["side"] = side
                        all_results.append(r)
            except Exception:
                continue
        all_results.sort(key=lambda x: x["score"], reverse=True)
        lines.append(f"⚠️ No setups met threshold ({threshold}/8) today.\n")
        lines.append("📊 <b>Closest setups (informational):</b>")
        for p in all_results[:3]:
            lines.append(
                f"  • {_display_sym(p['symbol'])} ({p['side']}) — Score: {p['score']}/8 | ₹{p['ltp']:.2f}"
            )
        lines.append("\n⚠️ Educational only.")
        return "\n".join(lines)

    if long_picks:
        lines.append("🟢 <b>LONG Setups</b>")
        for p in long_picks[:5]:
            sym = _display_sym(p["symbol"])
            ltp = p["ltp"]
            score = p["score"]
            details = ", ".join(p["details"][:3])
            atr_val = p.get("atr_val") or ltp * 0.02
            sl   = round(ltp - 2 * atr_val, 2)
            tgt1 = round(ltp + 2 * atr_val, 2)
            tgt2 = round(ltp + 4 * atr_val, 2)
            lines.append(
                f"  • <b>{sym}</b> | Score: {score}/8 | LTP: ₹{ltp:.2f}\n"
                f"    🎯 T1: ₹{tgt1} | T2: ₹{tgt2} | 🛑 SL: ₹{sl}\n"
                f"    ✅ {details}"
            )
        lines.append("")

    if short_picks:
        lines.append("🔴 <b>SHORT Setups</b>")
        for p in short_picks[:5]:
            sym = _display_sym(p["symbol"])
            ltp = p["ltp"]
            score = p["score"]
            details = ", ".join(p["details"][:3])
            atr_val = p.get("atr_val") or ltp * 0.02
            sl   = round(ltp + 2 * atr_val, 2)
            tgt1 = round(ltp - 2 * atr_val, 2)
            tgt2 = round(ltp - 4 * atr_val, 2)
            lines.append(
                f"  • <b>{sym}</b> | Score: {score}/8 | LTP: ₹{ltp:.2f}\n"
                f"    🎯 T1: ₹{tgt1} | T2: ₹{tgt2} | 🛑 SL: ₹{sl}\n"
                f"    ✅ {details}"
            )
        lines.append("")

    lines.append("⚠️ Educational only. Not SEBI-registered advice.")
    return "\n".join(lines)
