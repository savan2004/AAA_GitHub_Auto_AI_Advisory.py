import os
import time
import random
import re
from datetime import datetime
from collections import defaultdict, deque

import pandas as pd
import telebot
from telebot import types

from groq import Groq
import google.generativeai as genai
from nsetools import Nse
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

# ========== 1. CONFIG & CLIENTS ==========

TELEGRAM_TOKEN = os.getenv("8461087780:AAE4l58egcDN7LRbqXAp7x7x0nkfX6jTGEc", "")
GROQ_API_KEY = os.getenv("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", "")
GEMINI_API_KEY = os.getenv("AIzaSyCPh8wPC-rmBIyTr5FfV3Mwjb33KeZdRUE", "")

if not TELEGRAM_TOKEN:
    print("WARNING: TELEGRAM_TOKEN not set – bot polling will fail if you start it.")
if not GROQ_API_KEY:
    print("WARNING: GROQ_API_KEY not set – Groq AI will not work.")
if not GEMINI_API_KEY:
    print("WARNING: GEMINI_API_KEY not set – Gemini fallback will not work.")

bot = telebot.TeleBot(TELEGRAM_TOKEN)  # no global parse_mode
genai.configure(api_key=GEMINI_API_KEY)
nse = Nse()

# Simple in-memory cache
CACHE = {}
CACHE_TTL_DEFAULT = 60  # 1 min for live quotes

# yfinance rate limiting
YF_WINDOW_SEC = 60
YF_MAX_CALLS_PER_WINDOW = 20
YF_CALL_TIMES = deque()


# ========== 2. CACHE HELPERS ==========

def cache_get(key, ttl=CACHE_TTL_DEFAULT):
    data = CACHE.get(key)
    if not data:
        return None
    if time.time() - data["ts"] > ttl:
        del CACHE[key]
        return None
    return data["val"]


def cache_set(key, val):
    CACHE[key] = {"val": val, "ts": time.time()}


# ========== 3. SAFE MARKDOWN SENDER (FIX 400 ERROR) ==========

def escape_markdown(text: str) -> str:
    # Escape characters that break Telegram Markdown parsing
    return re.sub(r'([_*`\[])', r'\\\1', text)

def send_markdown(chat_id, text, reply_to=None):
    safe = escape_markdown(text)
    try:
        if reply_to:
            bot.reply_to(reply_to, safe, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, safe, parse_mode="Markdown")
    except telebot.apihelper.ApiTelegramException:
        # Fallback: send without formatting if still broken
        if reply_to:
            bot.reply_to(reply_to, text)
        else:
            bot.send_message(chat_id, text)


# ========== 4. NSE HELPERS (BEL + INDICES) ==========

def get_stock_quote(symbol: str):
    sym = symbol.upper().strip()
    key = f"q:{sym}"
    cached = cache_get(key)
    if cached is not None:
        return cached

    try:
        q = nse.get_quote(sym)  # returns dict or None[web:107]
        if not q:
            return None
        cache_set(key, q)
        return q
    except Exception:
        return None


def basic_stock_snapshot(symbol: str) -> str:
    q = get_stock_quote(symbol)
    if not q:
        return f"❌ Symbol not found or temporarily unavailable: {symbol}"

    sym = q.get("symbol", symbol.upper())
    ltp = q.get("lastPrice", 0.0)
    prev_close = q.get("previousClose", 0.0)
    change = q.get("change", 0.0)
    pchange = q.get("pChange", 0.0)
    high = q.get("dayHigh", 0.0)
    low = q.get("dayLow", 0.0)

    return (
        f"*{sym} (NSE)*\n"
        f"LTP: ₹{ltp:.2f} | {change:.2f} ({pchange:.2f}%)\n"
        f"Prev: ₹{prev_close:.2f} | Range: ₹{low:.2f}–₹{high:.2f}\n"
    )


def get_index_quote_safe(name: str):
    key = f"idx:{name}"
    cached = cache_get(key)
    if cached is not None:
        return cached

    try:
        q = nse.get_index_quote(name)  # dict or None[web:106][web:150]
        if not q:
            return None
        cache_set(key, q)
        return q
    except Exception:
        return None


def market_overview_text() -> str:
    n50 = get_index_quote_safe("NIFTY 50")
    nbk = get_index_quote_safe("NIFTY BANK")

    if not n50 or not nbk:
        return "❌ Could not fetch market indices right now. Please try again later."

    def fmt_idx(d):
        name = d.get("indexSymbol", d.get("index", "Index"))
        last = float(d.get("last", d.get("lastPrice", 0.0)))
        change = float(d.get("variation", d.get("change", 0.0)))
        pchange = float(d.get("percentChange", d.get("pChange", 0.0)))
        return name, last, change, pchange

    n_name, n_last, n_chg, n_pchg = fmt_idx(n50)
    b_name, b_last, b_chg, b_pchg = fmt_idx(nbk)

    return (
        "📈 *Market View*\n"
        f"{n_name}: {n_last:.2f} ({n_chg:.2f}, {n_pchg:.2f}%)\n"
        f"{b_name}: {b_last:.2f} ({b_chg:.2f}, {b_pchg:.2f}%)"
    )


# ========== 5. LIGHT YFINANCE HISTORY (OPTIONAL, SAFE) ==========

def yf_allow_call():
    now = time.time()
    while YF_CALL_TIMES and now - YF_CALL_TIMES[0] > YF_WINDOW_SEC:
        YF_CALL_TIMES.popleft()
    return len(YF_CALL_TIMES) < YF_MAX_CALLS_PER_WINDOW

def yf_register_call():
    YF_CALL_TIMES.append(time.time())

def safe_yf_history(ticker: str, period="6mo", interval="1d") -> pd.Series:
    if not yf_allow_call():
        return pd.Series(dtype=float)

    try:
        yf_register_call()
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if not df.empty:
            return df["Close"]
    except YFRateLimitError:
        return pd.Series(dtype=float)
    except Exception:
        return pd.Series(dtype=float)
    return pd.Series(dtype=float)


# ========== 6. AI LAYER ==========

def ai_call(prompt: str, max_tokens: int = 400) -> str:
    # Groq first
    try:
        gclient = Groq(api_key=GROQ_API_KEY)
        resp = gclient.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return resp.choices[0].message.content
    except Exception:
        pass

    # Gemini fallback
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = model.generate_content(prompt)
        return resp.text
    except Exception:
        return "AI is temporarily unavailable. Please try again later."


# ========== 7. DEEP STOCK ANALYSIS (BEL, ETC.) ==========

def deep_stock_analysis(symbol: str) -> str:
    sym = symbol.upper().strip()
    snap = basic_stock_snapshot(sym)

    q = get_stock_quote(sym)
    if not q:
        # Still give educational text without pretending data is live
        prompt = f"""
User asked about {sym}.
Data API could not return a live quote.

Give 3–5 educational points about how to think about a stock like this
(e.g., if BEL: defense/electronics PSU), including:
- business dependence on government
- cyclicality
- valuation
- key risks
Educational only, no buy/sell calls.
"""
        analysis = ai_call(prompt, max_tokens=350)
        return snap + "\n\n" + analysis

    # Optional yfinance trend
    series = safe_yf_history(f"{sym}.NS", period="6mo", interval="1d")
    trend = "Unknown"
    if len(series) >= 30:
        ltp = float(series.iloc[-1])
        ema200 = series.ewm(span=200, min_periods=50).mean().iloc[-1]
        trend = "Bullish" if ltp > ema200 else "Bearish"

    prompt = f"""
You are an Indian equity analyst.

Stock: {sym} on NSE.
Approx trend vs 200‑day EMA (if data available): {trend}.

1. Short‑term view (1–4 weeks).
2. Medium‑term view (3–6 months).
3. Educational BUY / HOLD / AVOID style view (no direct recommendation).
4. 3 key risks suitable for a PSU / sector stock like this.

Use simple language, about 8–12 sentences.
"""
    analysis = ai_call(prompt, max_tokens=500)
    return snap + f"Trend (approx): {trend}\n\n" + analysis


# ========== 8. TELEGRAM HANDLERS ==========

@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("📈 Market View"), types.KeyboardButton("🔍 Stock"))
    bot.send_message(
        m.chat.id,
        "Menu:\n- 📈 Market View\n- 🔍 Stock (send symbol: e.g., BEL, RELIANCE)",
        reply_markup=kb
    )


@bot.message_handler(func=lambda m: m.text == "📈 Market View")
def handle_market(m):
    text = market_overview_text()
    send_markdown(m.chat.id, text, reply_to=m)


@bot.message_handler(func=lambda m: m.text == "🔍 Stock")
def ask_stock(m):
    msg = bot.reply_to(m, "Send NSE stock symbol (e.g. BEL, RELIANCE):")
    bot.register_next_step_handler(msg, handle_stock_symbol)


def handle_stock_symbol(m):
    sym = (m.text or "").strip().upper()
    if not sym:
        bot.reply_to(m, "Empty symbol. Try again.")
        return
    text = deep_stock_analysis(sym)
    send_markdown(m.chat.id, text, reply_to=m)


@bot.message_handler(func=lambda m: True)
def fallback_stock(m):
    sym = (m.text or "").strip().upper()
    if not sym.isalnum():
        bot.reply_to(m, "Send NSE stock symbol like BEL or use /start.")
        return
    text = deep_stock_analysis(sym)
    send_markdown(m.chat.id, text, reply_to=m)


# ========== 9. SIMULATION & MAIN LOOP ==========

if __name__ == "__main__":
    # --- SIMULATION: run these once to test without Telegram ---
    print("=== SIMULATION: MARKET VIEW ===")
    print(market_overview_text())
    print("\n=== SIMULATION: BEL ANALYSIS (first 600 chars) ===")
    sim_text = deep_stock_analysis("BEL")
    print(sim_text[:600])
    print("\n=== END SIMULATION ===\n")

    # Uncomment below lines only when TELEGRAM_TOKEN is set and you are ready to run the bot:
    # print("Starting Telegram bot polling...")
    # while True:
    #     try:
    #         bot.infinity_polling(skip_pending=True, timeout=60)
    #     except Exception as e:
#         print(f\"Polling error: {e}\")
#         time.sleep(10)
