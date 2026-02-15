import os
import re
import json
import time
import random
import threading
from datetime import datetime
from collections import deque

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError
import requests

import telebot
from telebot import types

from groq import Groq
import google.generativeai as genai

from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer

# ========== 1. CONFIG & ENV ==========

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")

# Global cache for history (Yahoo + Alpha)
HIST_CACHE = {}
HIST_TTL = 900  # 15 minutes

# Global yfinance limiter
YF_WINDOW_SEC = 60
YF_MAX_CALLS_PER_WINDOW = 10  # ~10/min => 600/hour, safer for Yahoo[web:7][web:3][web:14]
YF_CALL_TIMES = deque()


# ========== 2. HELPERS: CACHE & MARKDOWN ==========

def hist_cache_get(key):
    data = HIST_CACHE.get(key)
    if not data:
        return None
    if time.time() - data["ts"] > HIST_TTL:
        del HIST_CACHE[key]
        return None
    return data["val"]


def hist_cache_set(key, val):
    HIST_CACHE[key] = {"val": val, "ts": time.time()}


def escape_markdown(text: str) -> str:
    # Escape characters that break Telegram Markdown parsing[web:162][web:159]
    return re.sub(r'([_*`\[])', r'\\\1', text)


def safe_send(chat_id, text, reply_to=None):
    safe = escape_markdown(text)
    try:
        if reply_to:
            bot.reply_to(reply_to, safe, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, safe, parse_mode="Markdown")
    except telebot.apihelper.ApiTelegramException:
        # Fallback without formatting
        if reply_to:
            bot.reply_to(reply_to, text)
        else:
            bot.send_message(chat_id, text)


# ========== 3. SAFE HISTORY LAYER (YAHOO + ALPHA) ==========

def yf_allow_call():
    now = time.time()
    while YF_CALL_TIMES and now - YF_CALL_TIMES[0] > YF_WINDOW_SEC:
        YF_CALL_TIMES.popleft()
    return len(YF_CALL_TIMES) < YF_MAX_CALLS_PER_WINDOW


def yf_register_call():
    YF_CALL_TIMES.append(time.time())


def safe_history_yf(ticker, period="1y", interval="1d") -> pd.DataFrame:
    """
    Primary: yfinance with global limiter + cache.
    """
    key = f"yf:{ticker}:{period}:{interval}"
    cached = hist_cache_get(key)
    if cached is not None:
        return cached

    if not yf_allow_call():
        # Too many calls; try stale cache
        cached = hist_cache_get(key)
        if cached is not None:
            return cached
        return pd.DataFrame()

    last_exc = None
    for attempt in range(3):
        try:
            yf_register_call()
            df = yf.Ticker(ticker).history(period=period, interval=interval)
            if not df.empty:
                hist_cache_set(key, df)
                return df
            last_exc = RuntimeError("Empty df from yfinance")
        except YFRateLimitError as e:
            last_exc = e
            time.sleep(2 ** attempt + random.uniform(0, 1))
        except Exception as e:
            last_exc = e
            time.sleep(1.0)

    # After failure, try stale cache
    cached = hist_cache_get(key)
    if cached is not None:
        return cached
    return pd.DataFrame()


def safe_history_alpha(ticker, outputsize="compact") -> pd.DataFrame:
    """
    Alpha Vantage fallback for daily data (mostly for non-NSE tickers).
    Free tier ~5 requests/min, 25/day, so use sparingly.[web:166][web:168]
    """
    if not ALPHA_VANTAGE_KEY:
        return pd.DataFrame()

    key = f"av:{ticker}:{outputsize}"
    cached = hist_cache_get(key)
    if cached is not None:
        return cached

    try:
        url = "https://www.alphavantage.co/query"
        params = {
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": ticker,
            "outputsize": outputsize,
            "apikey": ALPHA_VANTAGE_KEY,
        }
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        ts = data.get("Time Series (Daily)", {})
        if not ts:
            return pd.DataFrame()

        rows = []
        for dt, vals in ts.items():
            rows.append({
                "Date": pd.to_datetime(dt),
                "Open": float(vals["1. open"]),
                "High": float(vals["2. high"]),
                "Low": float(vals["3. low"]),
                "Close": float(vals["4. close"]),
                "Adj Close": float(vals["5. adjusted close"]),
                "Volume": float(vals["6. volume"]),
            })
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows).set_index("Date").sort_index()
        hist_cache_set(key, df)
        return df
    except Exception:
        return pd.DataFrame()


def safe_history(ticker, period="1y", interval="1d") -> pd.DataFrame:
    """
    Unified history accessor used everywhere in your bot.
    """
    # 1) Try yfinance (for NSE and others)
    df = safe_history_yf(ticker, period=period, interval=interval)
    if not df.empty:
        return df

    # 2) For non-NSE tickers, try Alpha Vantage as backup
    if not ticker.endswith(".NS") and period in ["6mo", "1y", "2y"]:
        df_av = safe_history_alpha(ticker)
        if not df_av.empty:
            return df_av

    # 3) No data
    return pd.DataFrame()


# ========== 4. COMMON MARKET HELPERS (YOUR OLD LOGIC, USING safe_history) ==========

def price_change(df: pd.DataFrame):
    if df.empty:
        return 0.0, 0.0
    close = df["Close"]
    if len(close) < 2:
        return 0.0, 0.0
    last = close.iloc[-1]
    prev = close.iloc[-2]
    chg = last - prev
    pct = (chg / prev * 100) if prev != 0 else 0.0
    return chg, pct


def ema(series: pd.Series, span: int):
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def quality_score(ltp, ema200, rsi_val, pe, roe):
    score = 0
    if ltp > ema200:
        score += 1
    if 40 <= rsi_val <= 60:
        score += 1
    if pe and pe < 25:
        score += 1
    if roe and roe > 15:
        score += 1
    return score


# ========== 5. AI LAYER: GROQ → GEMINI ==========

def ai_call(prompt: str, max_tokens: int = 600) -> str:
    # 1) Groq
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

    # 2) Gemini
    if GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content(prompt)
            return resp.text
        except Exception:
            pass

    return "AI is temporarily unavailable. Please try again later."


# ========== 6. DEEP STOCK ANALYSIS (ADAPTED TO safe_history) ==========

def deep_stock_analysis(symbol: str) -> str:
    sym = symbol.upper().strip()
    ticker = f"{sym}.NS"

    df = safe_history(ticker, period="1y", interval="1d")
    if df.empty:
        return f"❌ Could not fetch data for {sym}. Try again later."

    close = df["Close"]
    ltp = close.iloc[-1]
    ema50 = ema(close, 50).iloc[-1]
    ema200_val = ema(close, 200).iloc[-1]
    rsi14 = rsi(close).iloc[-1]

    trend = "Bullish" if ltp > ema200_val else "Bearish"
    pos_vs_50 = "above" if ltp > ema50 else "below"
    pos_vs_200 = "above" if ltp > ema200_val else "below"

    # Dummy fundamentals placeholders
    pe = None
    roe = None
    q_score = quality_score(ltp, ema200_val, rsi14, pe, roe)

    tech_summary = (
        f"*{sym}* (NSE)\n"
        f"LTP: ₹{ltp:.2f}\n"
        f"Trend: {trend}\n"
        f"Price vs 50 EMA: {pos_vs_50}\n"
        f"Price vs 200 EMA: {pos_vs_200}\n"
        f"RSI(14): {rsi14:.2f}\n"
        f"Quality Score (approx): {q_score}/4\n"
    )

    prompt = f"""
You are an Indian equity analyst.

Stock: {sym} on NSE.
Last traded price (approx): {ltp:.2f}
Trend vs 200 EMA: {trend}
RSI(14): {rsi14:.2f}
Quality score (0–4): {q_score}

1. Explain short-term view (1–4 weeks).
2. Explain medium-term view (3–6 months).
3. Give an educational BUY / HOLD / AVOID style view (no direct recommendation).
4. Highlight 3–4 key risks for this stock.
5. Suggest what a retail investor should watch (earnings, debt, valuations, etc).

Use simple, clear language, about 10–15 sentences.
"""
    analysis = ai_call(prompt, max_tokens=700)
    return tech_summary + "\n" + analysis


# ========== 7. MARKET ANALYSIS (USING safe_history) ==========

def market_analysis() -> str:
    nifty = safe_history("^NSEI", period="5d")
    bank = safe_history("^NSEBANK", period="5d")

    if nifty.empty or bank.empty:
        return "❌ Could not fetch market indices right now."

    n_chg, n_pct = price_change(nifty)
    b_chg, b_pct = price_change(bank)

    text = (
        "📈 *Market Analysis*\n"
        f"Nifty 50: {nifty['Close'].iloc[-1]:.2f} ({n_chg:+.2f}, {n_pct:+.2f}%)\n"
        f"Bank Nifty: {bank['Close'].iloc[-1]:.2f} ({b_chg:+.2f}, {b_pct:+.2f}%)\n"
    )

    prompt = f"""
Nifty 50 change: {n_chg:+.2f} ({n_pct:+.2f}%)
Bank Nifty change: {b_chg:+.2f} ({b_pct:+.2f}%)

Write a short market commentary (1–2 paragraphs) for Indian traders.
Mention trend, sentiment, and any caution for intraday/swing traders.
No direct trade calls.
"""
    view = ai_call(prompt, max_tokens=400)
    return text + "\n" + view


# ========== 8. PORTFOLIO SCANNER (USING safe_history) ==========

def scan(list_syms):
    rows = []
    for sym in list_syms:
        t = f"{sym}.NS"
        df = safe_history(t, period="6mo", interval="1d")
        if df.empty:
            rows.append((sym, "NO DATA", 0.0, 0.0))
            continue
        close = df["Close"]
        ltp = close.iloc[-1]
        ema200_val = ema(close, 200).iloc[-1]
        rsi14 = rsi(close).iloc[-1]
        trend = "Bullish" if ltp > ema200_val else "Bearish"
        rows.append((sym, trend, ltp, rsi14))
    return rows


def portfolio_scanner() -> str:
    large_caps = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "ITC"]
    mid_caps = ["PERSISTENT", "MOTHERSON", "TRENT", "AUBANK", "TATACOMM"]

    rows_l = scan(large_caps)
    rows_m = scan(mid_caps)

    text = "📊 *Portfolio Scanner (Trend & RSI)*\n\n*Large Caps:*\n"
    for sym, trend, ltp, r in rows_l:
        text += f"{sym}: {trend}, LTP ₹{ltp:.2f}, RSI {r:.1f}\n"

    text += "\n*Mid Caps:*\n"
    for sym, trend, ltp, r in rows_m:
        text += f"{sym}: {trend}, LTP ₹{ltp:.2f}, RSI {r:.1f}\n"

    text += "\nUse this as a quick technical snapshot only. Not investment advice."
    return text


# ========== 9. OPTION STRATEGIES (EDUCATIONAL) ==========

def option_strategies_text() -> str:
    return (
        "🛡️ *OPTION STRATEGIES (EDUCATIONAL)*\n"
        "- Bull Call Spread: Mildly bullish, limited risk & reward.\n"
        "- Bear Put Spread: Mildly bearish, limited risk.\n"
        "- Iron Condor: Range-bound view, time decay friendly.\n"
        "- Long Straddle: Big move expected, any direction.\n\n"
        "Always manage position size and risk. This is NOT investment advice."
    )


# ========== 10. TELEGRAM HANDLERS ==========

@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📈 Market View"),
        types.KeyboardButton("🔍 Stock Analysis"),
    )
    kb.add(
        types.KeyboardButton("📊 Portfolio Scan"),
        types.KeyboardButton("🛡️ Option Ideas"),
    )
    bot.send_message(
        m.chat.id,
        "🤖 *AI Stock Advisory Bot*\n\n"
        "Use the menu or send an NSE symbol like RELIANCE.\n"
        "Note: Data is delayed & educational only.",
        reply_markup=kb,
        parse_mode="Markdown",
    )


@bot.message_handler(func=lambda m: m.text == "📈 Market View")
def handle_market(m):
    txt = market_analysis()
    safe_send(m.chat.id, txt, reply_to=m)


@bot.message_handler(func=lambda m: m.text == "📊 Portfolio Scan")
def handle_scan(m):
    txt = portfolio_scanner()
    safe_send(m.chat.id, txt, reply_to=m)


@bot.message_handler(func=lambda m: m.text == "🛡️ Option Ideas")
def handle_options(m):
    txt = option_strategies_text()
    safe_send(m.chat.id, txt, reply_to=m)


@bot.message_handler(func=lambda m: m.text == "🔍 Stock Analysis")
def ask_symbol(m):
    msg = bot.reply_to(m, "Send NSE stock symbol (e.g. RELIANCE):")
    bot.register_next_step_handler(msg, handle_symbol_analysis)


def handle_symbol_analysis(m):
    sym = (m.text or "").strip().upper()
    if not sym:
        bot.reply_to(m, "Empty symbol. Try again.")
        return
    txt = deep_stock_analysis(sym)
    safe_send(m.chat.id, txt, reply_to=m)


@bot.message_handler(func=lambda m: True)
def fallback_symbol(m):
    sym = (m.text or "").strip().upper()
    if not sym.isalnum() or len(sym) > 15:
        bot.reply_to(m, "I did not understand.\nSend NSE symbol like RELIANCE or use /start.")
        return
    txt = deep_stock_analysis(sym)
    safe_send(m.chat.id, txt, reply_to=m)


# ========== 11. HEALTH SERVER FOR RENDER ==========

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


# ========== 12. MAIN LOOP ==========

if __name__ == "__main__":
    print("🤖 AI Stock Advisory Bot starting...")
    threading.Thread(target=run_health_server, daemon=True).start()

    # Quick simulation in logs
    print("=== SIM TEST: NIFTY HISTORY SHAPE ===")
    print(safe_history("^NSEI", period="5d").shape)
    print("=== SIM TEST: RELIANCE ANALYSIS (first 400 chars) ===")
    print(deep_stock_analysis("RELIANCE")[:400])

    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(10)
