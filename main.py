"""
main.py — AI Stock Advisory Bot (Fixed & Perfected v3.2)
- Fixed SyntaxError in f-strings
- Fixed Rate Limiting by increasing Cache TTL and adding request delays
- Fixed Render build errors by moving pip installation to build command
- Added exponential backoff to yfinance retries
- Implemented Batch Downloading for scan functions to minimize API hits
"""
import os
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
import json
import numpy as np
import pandas as pd
import yfinance as yf
from collections import deque
from datetime import datetime
from functools import lru_cache
from flask import Flask, request, jsonify
import telebot
from telebot import types

# ── Configuration & Setup ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Required environment variables
REQUIRED_VARS = ["TELEGRAM_TOKEN"]
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    logger.error("TELEGRAM_TOKEN missing")
    raise RuntimeError("TELEGRAM_TOKEN environment variable is required")

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
GROQ_KEY = os.getenv("GROQ_API_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_KEY")
TAVILY_KEY = os.getenv("TAVILY_API_KEY")

if not (GROQ_KEY or GEMINI_KEY or OPENAI_KEY):
    logger.warning("No AI API keys configured – AI features will not work")

WEBHOOK_PATH = f"/webhook/{TOKEN}"
app = Flask(__name__)
bot = telebot.TeleBot(TOKEN, threaded=False)

# Render ThreadPool limits to prevent Memory/RAM Out-of-Memory crashes
executor = ThreadPoolExecutor(max_workers=5)

# ── Cache & State ─────────────────────────────────────────────────────────
_cache = {}
_state = {}
_processed_updates = set()
_lock = threading.Lock()
CACHE_TTL = 3600 # Increased to 1 hour to prevent Rate Limiting

# ── AI Engine ──────────────────────────────────────────────────────────────
_groq = None
_gemini = None
_openai = None

def init_ai_clients():
    global _groq, _gemini, _openai
    if GROQ_KEY and not _groq:
        try:
            from groq import Groq
            _groq = Groq(api_key=GROQ_KEY)
        except ImportError:
            pass
    if GEMINI_KEY and not _gemini:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_KEY)
            _gemini = genai.GenerativeModel("gemini-2.0-flash")
        except ImportError:
            pass
    if OPENAI_KEY and not _openai:
        try:
            from openai import OpenAI
            _openai = OpenAI(api_key=OPENAI_KEY)
        except ImportError:
            pass
    return _groq, _gemini, _openai

def get_live_context():
    ctx = []
    try:
        n = yf.Ticker("^NSEI").history(period="2d")
        if len(n) >= 2:
            l = round(float(n['Close'].iloc[-1]), 2)
            p = round(float(n['Close'].iloc[-2]), 2)
            chg = round((l - p) / p * 100, 2)
            ctx.append(f"NIFTY 50: {l} ({chg}%)")
    except Exception:
        pass
    try:
        b = yf.Ticker("^NSEBANK").history(period="2d")
        if len(b) >= 2:
            l = round(float(b['Close'].iloc[-1]), 2)
            p = round(float(b['Close'].iloc[-2]), 2)
            chg = round((l - p) / p * 100, 2)
            ctx.append(f"BANK NIFTY: {l} ({chg}%)")
    except Exception:
        pass
    return "\n".join(ctx) if ctx else "Market data unavailable."

def call_ai(messages, max_tokens=400, system="", use_context=False):
    errs = []
    groq, gemini, openai = init_ai_clients()
    sys_prompt = system
    if use_context:
        live_data = get_live_context()
        sys_prompt += f"\n\nLIVE MARKET DATA (Use these numbers in your answer):\n{live_data}"

    msgs = []
    if sys_prompt:
        msgs.append({"role": "system", "content": sys_prompt})
    msgs.extend(messages)

    if groq:
        try:
            r = groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=msgs,
                max_tokens=max_tokens,
                timeout=10
            )
            return r.choices[0].message.content.strip(), ""
        except Exception as e:
            errs.append(f"Groq: {e}")
    if gemini:
        try:
            prompt_parts = [sys_prompt] if sys_prompt else []
            prompt_parts.extend([f"{m['role'].upper()}: {m['content']}" for m in messages])
            r = gemini.generate_content("\n\n".join(prompt_parts))
            return r.text.strip(), ""
        except Exception as e:
            errs.append(f"Gemini: {e}")
    if openai:
        try:
            r = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=msgs,
                max_tokens=max_tokens,
                timeout=10
            )
            return r.choices[0].message.content.strip(), ""
        except Exception as e:
            errs.append(f"OpenAI: {e}")
    return "", "\n".join(errs) if errs else "No AI keys configured."

def ai_insights(sym, ltp, rsi, trend, pe, asi):
    if not (GROQ_KEY or GEMINI_KEY or OPENAI_KEY):
        return "⚠️ AI Disabled – no API keys"
    prompt = f"Give 3 bullish bullets and 2 risk bullets for {sym} (NSE). LTP:{ltp}, RSI:{rsi}, Trend:{trend}, PE:{pe}, ASI:{asi}."
    resp, err = call_ai([{"role": "user", "content": prompt}], max_tokens=250)
    return resp if resp else f"AI Error: {err}"

# ── Data & Technicals with Exponential Backoff ────────────────────────────
def retry_on_failure(func, *args, retries=3, delay=2, **kwargs):
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            err_msg = str(e).lower()
            if "too many requests" in err_msg or "rate limit" in err_msg or "429" in err_msg:
                wait_time = delay * (2 ** i)  # 2s, 4s, 8s
                logger.warning(f"Rate limited. Waiting {wait_time}s...")
                time.sleep(wait_time)
            if i == retries - 1:
                raise
            time.sleep(delay * (i + 1))

def get_cached(key):
    with _lock:
        d = _cache.get(key)
        if d and time.time() - d["ts"] < CACHE_TTL:
            return d["val"]
    return None

def set_cached(key, val):
    with _lock:
        _cache[key] = {"val": val, "ts": time.time()}

def get_hist(sym, period="1y"):
    key = f"h_{sym}_{period}"
    cached = get_cached(key)
    if cached is not None:
        return cached
    try:
        ticker = yf.Ticker(f"{sym}.NS")
        df = retry_on_failure(ticker.history, period=period, auto_adjust=True)
        if df.empty or len(df) < 5:
            return pd.DataFrame()
        set_cached(key, df)
        return df
    except Exception as e:
        logger.error(f"Error fetching history for {sym}: {e}")
        return pd.DataFrame()

def get_info(sym):
    key = f"i_{sym}"
    cached = get_cached(key)
    if cached:
        return cached
    try:
        ticker = yf.Ticker(f"{sym}.NS")
        info = retry_on_failure(lambda: ticker.info)
        if hasattr(ticker, "fast_info"):
            info["mcap"] = ticker.fast_info.get("marketCap", None)
        set_cached(key, info)
        return info
    except Exception as e:
        logger.error(f"Error fetching info for {sym}: {e}")
        return {}

def calc_rsi(close_series):
    if len(close_series) < 15:
        return 50.0
    delta = close_series.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    loss = loss.replace(0, 1e-10)  # Fix division by zero
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 1)

def calc_macd(close):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    return round(float(macd.iloc[-1]), 2)

def calc_ema(close, span):
    ema = close.ewm(span=span, adjust=False).mean()
    return round(float(ema.iloc[-1]), 2)

def calc_atr(df):
    high, low, close = df['High'], df['Low'], df['Close']
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    return round(float(atr.iloc[-1]), 2)

def calc_asi(df):
    if len(df) < 2:
        return 0.0
    O, H, L, C = df['Open'], df['High'], df['Low'], df['Close']
    O_prev, C_prev = O.shift(1), C.shift(1)
    A, B = (H - C_prev).abs(), (L - C_prev).abs()
    C_diff, D = (H - L).abs(), (C_prev - O_prev).abs()
    R = pd.Series(0.0, index=df.index)
    cond_A = (A >= B) & (A >= C_diff)
    cond_B = (B >= A) & (B >= C_diff) & ~cond_A
    cond_C = ~(cond_A | cond_B)
    R.loc[cond_A] = A[cond_A] + 0.5 * B[cond_A] + 0.25 * D[cond_A]
    R.loc[cond_B] = B[cond_B] + 0.5 * A[cond_B] + 0.25 * D[cond_B]
    R.loc[cond_C] = C_diff[cond_C] + 0.25 * D[cond_C]
    R = R.replace(0, 1e-10)
    K = pd.concat([A, B], axis=1).max(axis=1)
    limit_move = (C_prev * 0.20).replace(0, 1e-10)
    num = (C - C_prev) + 0.5 * (C_prev - O) + 0.25 * (C_prev - O_prev)
    SI = 50 * (num / R) * (K / limit_move)
    ASI = SI.cumsum()
    return round(float(ASI.iloc[-1]), 2)

def safe(d, *keys, multiplier=1.0):
    for k in keys:
        val = d.get(k)
        if val is not None:
            try:
                return round(float(val) * multiplier, 2)
            except:
                pass
    return None

# ── Message Builders ───────────────────────────────────────────────────────
def build_adv(sym):
    sym = sym.upper().replace(".NS", "")
    df = get_hist(sym)
    if df.empty:
        return f"❌ {sym} not found. Check symbol or try again later."
    close = df['Close']
    ltp = round(float(close.iloc[-1]), 2)
    prev_close = float(close.iloc[-2]) if len(close) > 1 else ltp
    chg = round((ltp - prev_close) / prev_close * 100, 2)
    rsi, macd = calc_rsi(close), calc_macd(close)
    ema20, ema50 = calc_ema(close, 20), calc_ema(close, 50)
    atr, asi = calc_atr(df), calc_asi(df)
    trend = "BULLISH" if ltp > ema20 > ema50 else "BEARISH" if ltp < ema20 < ema50 else "NEUTRAL"
    info = get_info(sym)
    name = info.get("longName", sym)
    pe = safe(info, "trailingPE")
    roe = safe(info, "returnOnEquity", multiplier=100)
    ai_text = ai_insights(sym, ltp, rsi, trend, pe or "N/A", asi)
    return "\n".join([
        f"🏢 <b>{name}</b> ({sym})",
        f"💰 LTP: ₹{ltp} ({chg}%)",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📊 PE: {pe if pe else 'N/A'} | ROE: {roe if roe else 'N/A'}%",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔬 Trend: {trend} | RSI: {rsi} | ASI: {asi}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🎯 Target: ₹{round(ltp + 1.5 * atr, 2)} | SL: ₹{round(ltp - 2 * atr, 2)}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🤖 AI:\n{ai_text}"
    ])

def build_scan(profile):
    profile_map = {
        "conservative": ["HDFCBANK", "TCS", "INFY", "ITC", "ONGC"],
        "moderate": ["RELIANCE", "BHARTIARTL", "AXISBANK", "MARUTI"],
        "aggressive": ["TATAMOTORS", "ADANIENT", "JSWSTEEL", "TATAPOWER"]
    }
    symbols = profile_map.get(profile, [])
    # Batch Download to prevent rate limits
    tickers = [f"{s}.NS" for s in symbols]
    try:
        data = yf.download(tickers, period="5d", group_by="ticker", auto_adjust=True, progress=False)
    except:
        return "❌ Batch fetch failed. Try later."

    lines = [f"📊 {profile.upper()} SCAN", "━━━━━━━━━━━━━━━━━━━━"]
    for sym in symbols:
        try:
            df = data[f"{sym}.NS"]
            if df.empty:
                continue
            ltp = round(float(df['Close'].iloc[-1]), 2)
            prev = float(df['Close'].iloc[-2])
            chg = round((ltp - prev) / prev * 100, 2)
            icon = "🟢" if chg >= 0 else "🔴"
            lines.append(f"{icon} <b>{sym}</b>: ₹{ltp} ({chg}%)")
        except:
            pass
    return "\n".join(lines)

def build_breadth():
    lines = ["📊 MARKET BREADTH", "━━━━━━━━━━━━━━━━━━━━"]
    indices = {"NIFTY": "^NSEI", "BANK NIFTY": "^NSEBANK"}
    for name, ticker in indices.items():
        try:
            d = yf.Ticker(ticker).history(period="2d")
            if len(d) >= 2:
                l, p = round(float(d['Close'].iloc[-1]), 2), round(float(d['Close'].iloc[-2]), 2)
                chg = round((l - p) / p * 100, 2)
                lines.append(f"{'🟢' if chg >= 0 else '🔴'} {name}: {l:,.2f} ({chg}%)")
        except:
            pass
    return "\n".join(lines)

def build_news():
    if not TAVILY_KEY:
        return "Set TAVILY_KEY for news."
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_KEY, "query": "India stock market", "max_results": 3},
            timeout=5
        )
        return "\n".join([f"📰 {x['title']}" for x in r.json().get("results", [])])
    except:
        return "News fetch error."

# ── Telegram Handlers ──────────────────────────────────────────────────────
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add("🔍 Analysis", "📊 Breadth", "🤖 AI")
    kb.add("🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive")
    kb.add("🎯 Swing", "📰 News")
    return kb

def ai_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📊 Nifty", "💎 Picks", "🔙 Menu")
    return kb

@bot.message_handler(commands=["start"])
def cmd_start(message):
    _state[message.chat.id] = None
    bot.send_message(
        message.chat.id,
        "👋 Ready! Type a symbol (e.g., RELIANCE) or use menu.",
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "🔙 Menu")
def back_to_main(message):
    _state[message.chat.id] = None
    bot.send_message(message.chat.id, "Main Menu", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🤖 AI")
def enter_ai_mode(message):
    _state[message.chat.id] = "ai"
    bot.send_message(message.chat.id, "Ask AI anything (live market data included):", reply_markup=ai_keyboard())

@bot.message_handler(func=lambda m: m.text in ["📊 Nifty", "💎 Picks"])
def ai_predefined(message):
    bot.send_message(message.chat.id, "Thinking...")
    prompts = {
        "📊 Nifty": "Analyze Nifty 50 trend.",
        "💎 Picks": "Suggest 2 swing trading stocks."
    }
    resp, err = call_ai([{"role": "user", "content": prompts.get(message.text, "")}], use_context=True)
    bot.send_message(message.chat.id, resp or f"Err: {err}", reply_markup=ai_keyboard())

@bot.message_handler(func=lambda m: m.text in ["🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive"])
def scan_button(message):
    profile = message.text.split()[1].lower()
    bot.send_message(message.chat.id, build_scan(profile), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📊 Breadth")
def breadth_button(message):
    bot.send_message(message.chat.id, build_breadth(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🎯 Swing")
def swing_button(message):
    candidates = ["RELIANCE", "TCS", "HDFCBANK", "TATAMOTORS"]
    lines = ["🎯 SWING (RSI <35)", "━━━━━━━━━━━━━━━━━━━━"]
    found = False
    for sym in candidates:
        df = get_hist(sym, "2mo")
        if not df.empty:
            rsi = calc_rsi(df['Close'])
            if rsi < 35:
                lines.append(f"🟢 {sym} RSI:{rsi}")
                found = True
        time.sleep(0.5)  # Prevent rate limit in loop
    if not found:
        lines.append("None found.")
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📰 News")
def news_button(message):
    bot.send_message(message.chat.id, build_news())

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    uid, text = message.chat.id, message.text.strip()
    if _state.get(uid) == "ai":
        resp, err = call_ai([{"role": "user", "content": text}], use_context=True)
        bot.send_message(uid, resp or f"Err: {err}", reply_markup=ai_keyboard())
        return
    sym = text.upper().replace(".NS", "")
    if 2 <= len(sym) <= 15:
        bot.send_message(uid, f"🔍 Analyzing {sym}...")
        bot.send_message(uid, build_adv(sym), parse_mode="HTML")

# ── Flask Webhook ─────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok", "version": "3.1_fixed"})

def process_update(update_json):
    try:
        update = telebot.types.Update.de_json(update_json)
        bot.process_new_updates([update])
    except Exception as e:
        logger.error(f"Error: {e}")

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    data = request.get_data().decode("utf-8")
    try:
        update_id = json.loads(data).get("update_id")
        with _lock:
            if update_id in _processed_updates:
                return "OK", 200
            _processed_updates.add(update_id)
            if len(_processed_updates) > 200:
                _processed_updates.discard(min(_processed_updates))
    except:
        pass
    executor.submit(process_update, data)
    return "OK", 200

if __name__ == "__main__":
    # Warm up yfinance
    try:
        _ = yf.Ticker("^NSEI").history(period="1d")
    except:
        pass
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
