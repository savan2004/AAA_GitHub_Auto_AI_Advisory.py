# main.py
import os
import time
import threading
from collections import deque

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

import telebot
from telebot import types

from groq import Groq
import google.generativeai as genai

from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer

# ========= 1. CONFIG =========

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

bot = telebot.TeleBot(TELEGRAM_TOKEN)  # text only

YF_WINDOW_SEC = 60
YF_MAX_CALLS_PER_WINDOW = 10
YF_CALL_TIMES = deque()

CACHE = {}
CACHE_TTL = 900  # 15 min


# ========= 2. CACHE & HISTORY =========

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

def safe_history(ticker, period="1y", interval="1d") -> pd.DataFrame:
    key = f"hist:{ticker}:{period}:{interval}"
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


# ========= 3. INDICATORS =========

def ema(series: pd.Series, span: int):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

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


# ========= 4. OPTIONAL AI =========

def ai_call(prompt: str, max_tokens: int = 600) -> str:
    # 1) Try Groq
    if GROQ_API_KEY:
        try:
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.3,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print("Groq error:", e)

    # 2) Try Gemini
    if GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content(prompt)
            return (resp.text or "").strip()
        except Exception as e:
            print("Gemini error:", e)

    # 3) Fallback simple text
    return "AI explanation not available on server. Use the indicator snapshot above as primary view."


# ========= 5. STOCK ANALYSIS (COMBINED MESSAGE) =========

def deep_stock_analysis(symbol: str) -> str:
    sym = symbol.upper().strip()
    ticker = f"{sym}.NS"

    df = safe_history(ticker, period="1y", interval="1d")
    if df.empty:
        return f"Could not fetch data for {sym}. Try again later."

    close = df["Close"]
    ltp = float(close.iloc[-1])
    ema20_val = float(ema(close, 20).iloc[-1])
    ema50_val = float(ema(close, 50).iloc[-1])
    ema200_val = float(ema(close, 200).iloc[-1])
    rsi14 = float(rsi(close, 14).iloc[-1])
    bb_mid, bb_up, bb_low = bollinger_bands(close, 20, 2)
    bbm = float(bb_mid.iloc[-1])
    bbu = float(bb_up.iloc[-1])
    bbl = float(bb_low.iloc[-1])
    adx_val = float(adx(df, 14).iloc[-1])

    trend = "Bullish" if ltp > ema200_val else "Bearish"
    pos_50 = "above" if ltp > ema50_val else "below"
    pos_200 = "above" if ltp > ema200_val else "below"

    quality = 0
    if ltp > ema200_val:
        quality += 1
    if 40 <= rsi14 <= 60:
        quality += 1
    if adx_val >= 20:
        quality += 1

    snapshot = (
        "STOCK SNAPSHOT\n"
        f"Symbol: {sym} (NSE)\n"
        f"LTP: ₹{ltp:.2f}\n"
        f"Trend vs 200 EMA: {trend}\n"
        f"Price vs 50 EMA: {pos_50}\n"
        f"Price vs 200 EMA: {pos_200}\n"
        f"EMA20 / EMA50 / EMA200: {ema20_val:.2f} / {ema50_val:.2f} / {ema200_val:.2f}\n"
        f"Bollinger Bands (U/M/L): {bbu:.2f} / {bbm:.2f} / {bbl:.2f}\n"
        f"RSI(14): {rsi14:.2f}\n"
        f"ADX(14): {adx_val:.2f}\n"
        f"Quality Score (rough): {quality}/3\n"
    )

    short_bias = "overbought" if rsi14 > 70 else "oversold" if rsi14 < 30 else "neutral"

    prompt = f"""
You are an Indian equity analyst.

Stock: {sym} on NSE.
Latest price: {ltp:.2f}
Trend vs 200 EMA: {trend}
RSI(14): {rsi14:.2f} (bias: {short_bias})
ADX(14): {adx_val:.2f}
Quality score (0–3): {quality}

Write a SHORT, structured view:
1) Short-term view (1–4 weeks) in 3–4 sentences.
2) Medium-term view (3–6 months) in 3–4 sentences.
3) 3 key risks as bullet-style lines (starting with '-').
4) End with: "Note: This is educational analysis only, not a recommendation."

Total length under 220 words.
"""
    explanation = ai_call(prompt, max_tokens=350)

    combined = (
        snapshot
        + "\n"
        + f"ANALYST VIEW – {sym}\n\n"
        + explanation
    )
    return combined


# ========= 6. SWING TRADES (SIMPLE) =========

WATCHLIST = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK",
    "SBIN", "INFY", "ITC", "LT", "AXISBANK", "KOTAKBANK",
]

def swing_signal(df: pd.DataFrame):
    if df.empty or len(df) < 250:
        return {"signal": "NONE"}

    close = df["Close"]
    ltp = float(close.iloc[-1])

    ema20_val = ema(close, 20)
    ema50_val = ema(close, 50)
    ema200_val = ema(close, 200)
    bb_mid, bb_up, bb_low = bollinger_bands(close, 20, 2)
    adx_val = adx(df, 14)

    e20 = float(ema20_val.iloc[-1])
    e50 = float(ema50_val.iloc[-1])
    e200 = float(ema200_val.iloc[-1])
    bbm = float(bb_mid.iloc[-1])
    bbu = float(bb_up.iloc[-1])
    bbl = float(bb_low.iloc[-1])
    adx_last = float(adx_val.iloc[-1])

    long_trend_ok = (ltp > e200) and (e50 > e200)
    long_pullback = (bbl <= ltp <= bbm)
    long_adx = adx_last >= 25

    if long_trend_ok and long_pullback and long_adx:
        return {"signal": "LONG", "ltp": ltp, "adx": adx_last}

    short_trend_ok = (ltp < e200) and (e50 < e200)
    short_pullback = (bbm <= ltp <= bbu)
    short_adx = adx_last >= 25

    if short_trend_ok and short_pullback and short_adx:
        return {"signal": "SHORT", "ltp": ltp, "adx": adx_last}

    return {"signal": "NONE"}

def get_daily_swing_trades() -> str:
    lines = ["Swing Trades (Rules-based, Educational)\n"]
    for sym in WATCHLIST:
        t = f"{sym}.NS"
        df = safe_history(t, period="6mo", interval="1d")
        sig = swing_signal(df)
        if sig["signal"] != "NONE":
            side = sig["signal"]
            ltp = sig["ltp"]
            adx_val = sig["adx"]
            lines.append(
                f"{sym}: {side} bias, LTP ~ ₹{ltp:.2f}, ADX14 ~ {adx_val:.1f}."
            )

    if len(lines) == 1:
        return (
            "Swing Trades\n"
            "No high-confidence EMA20/50/200 + Bollinger + ADX setups today."
        )

    lines.append(
        "\nNote: Educational technical analysis only, not trade advice."
    )
    return "\n".join(lines)


# ========= 7. MARKET & OPTIONS =========

def market_analysis() -> str:
    return "Market View\n(Plug your own Nifty / sector commentary here.)"

def option_strategies_text() -> str:
    return (
        "OPTION STRATEGIES (EDUCATIONAL)\n"
        "- Bull Call Spread: Mildly bullish, limited risk & reward.\n"
        "- Bear Put Spread: Mildly bearish, limited risk.\n"
        "- Iron Condor: Range-bound view, time decay friendly.\n"
        "- Long Straddle: Big move expected, any direction.\n"
        "Always manage risk. Not a recommendation."
    )


# ========= 8. TELEGRAM HANDLERS =========

@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("Market View"),
        types.KeyboardButton("Stock Analysis"),
    )
    kb.add(
        types.KeyboardButton("Swing Trades"),
        types.KeyboardButton("Option Ideas"),
    )
    bot.send_message(
        m.chat.id,
        "AI Stock Advisory Bot\n\n"
        "Menu:\n"
        "- Market View\n"
        "- Stock Analysis\n"
        "- Swing Trades\n"
        "- Option Ideas\n\n"
        "All content is educational only.",
        reply_markup=kb,
    )

@bot.message_handler(func=lambda m: m.text == "Market View")
def handle_market(m):
    bot.reply_to(m, market_analysis())

@bot.message_handler(func=lambda m: m.text == "Stock Analysis")
def ask_symbol(m):
    msg = bot.reply_to(m, "Send NSE stock symbol (e.g. RELIANCE):")
    bot.register_next_step_handler(msg, handle_symbol_analysis)

def handle_symbol_analysis(m):
    sym = (m.text or "").strip().upper()
    if not sym:
        bot.reply_to(m, "Empty symbol. Try again.")
        return
    if not sym.isalnum():
        bot.reply_to(m, "Please send a valid NSE symbol like RELIANCE.")
        return
    text = deep_stock_analysis(sym)
    bot.reply_to(m, text)

@bot.message_handler(func=lambda m: m.text == "Swing Trades")
def handle_swing(m):
    txt = get_daily_swing_trades()
    bot.reply_to(m, txt)

@bot.message_handler(func=lambda m: m.text == "Option Ideas")
def handle_options(m):
    bot.reply_to(m, option_strategies_text())

@bot.message_handler(func=lambda m: True)
def fallback(m):
    bot.reply_to(
        m,
        "Use the menu: Market View, Stock Analysis, Swing Trades, Option Ideas."
    )


# ========= 9. HEALTH SERVER & SIMULATION =========

def run_health_server():
    port = int(os.environ.get("PORT", 10000))

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bot is running")

    TCPServer.allow_reuse_address = True
    with TCPServer(("0.0.0.0", port), Handler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    print("Bot starting...")

    # Simulation: BEL analysis in logs
    try:
        sim_text = deep_stock_analysis("BEL")
        print("===== SIMULATION: BEL ANALYSIS (first 600 chars) =====")
        print(sim_text[:600])
        print("======================================================")
    except Exception as e:
        print("Simulation error:", e)

    threading.Thread(target=run_health_server, daemon=True).start()

    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            print("Polling error:", e)
            time.sleep(10)
