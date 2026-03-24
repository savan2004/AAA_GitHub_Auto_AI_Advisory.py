"""
main.py — AI Stock Advisory Bot (Fixed & Perfected)
- Added Accumulation Swing Index (ASI) technical indicator
- Pre-initialized yfinance to fix curl_cffi thread-safety issues on Render
- Fixed RSI division by zero bugs
- Implemented ThreadPoolExecutor to prevent RAM exhaustion from infinite threads
- Updated fast_info parsing
- Added robust error handling and API fallback logic
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

CACHE_TTL = 900           # 15 minutes

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
            full_prompt = "\n\n".join(prompt_parts)
            r = gemini.generate_content(full_prompt)
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

# ── Data & Technicals with Retries ────────────────────────────────────────
def retry_on_failure(func, *args, retries=3, delay=1, **kwargs):
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
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
        
        # Fast Info fix: using standard dictionary `.get()` method 
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
    
    # Division by zero fix for continuous uptrends
    loss = loss.replace(0, 1e-10)
    
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
    """Calculate Accumulation Swing Index (ASI) using Pandas"""
    if len(df) < 2:
        return 0.0
    
    # Vectorized computation of standard Swing Index components
    O, H, L, C = df['Open'], df['High'], df['Low'], df['Close']
    O_prev, C_prev = O.shift(1), C.shift(1)
    
    A = (H - C_prev).abs()
    B = (L - C_prev).abs()
    C_diff = (H - L).abs()
    D = (C_prev - O_prev).abs()
    
    R = pd.Series(0.0, index=df.index)
    cond_A = (A >= B) & (A >= C_diff)
    cond_B = (B >= A) & (B >= C_diff) & ~cond_A
    cond_C = ~(cond_A | cond_B)
    
    R.loc[cond_A] = A[cond_A] + 0.5 * B[cond_A] + 0.25 * D[cond_A]
    R.loc[cond_B] = B[cond_B] + 0.5 * A[cond_B] + 0.25 * D[cond_B]
    R.loc[cond_C] = C_diff[cond_C] + 0.25 * D[cond_C]
    R = R.replace(0, 1e-10) # Protect zero division
    
    K = pd.concat([A, B], axis=1).max(axis=1)
    
    # Using 20% limit move standard approximation for NSE circuits
    limit_move = C_prev * 0.20
    limit_move = limit_move.replace(0, 1e-10)
    
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
            except (ValueError, TypeError):
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

    rsi = calc_rsi(close)
    macd = calc_macd(close)
    ema20 = calc_ema(close, 20)
    ema50 = calc_ema(close, 50)
    atr = calc_atr(df)
    asi = calc_asi(df) # Generate Accumulation Swing Index (ASI)

    if ltp > ema20 > ema50:
        trend = "BULLISH"
    elif ltp < ema20 < ema50:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

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
    lines = [f"📊 {profile.upper()} SCAN", "━━━━━━━━━━━━━━━━━━━━"]
    for sym in symbols:
        df = get_hist(sym, "1mo")
        if df.empty:
            lines.append(f"⚪ {sym}: No Data")
            continue
        close = df['Close']
        ltp = round(float(close.iloc[-1]), 2)
        prev = float(close.iloc[-2]) if len(close) > 1 else ltp
        chg = round((ltp - prev) / prev * 100, 2)
        icon = "🟢" if chg >= 0 else "🔴"
        lines.append(f"{icon} <b>{sym}</b>: ₹{ltp} ({chg}%)")
    return "\n".join(lines)

def build_breadth():
    lines = ["📊 MARKET BREADTH", "━━━━━━━━━━━━━━━━━━━━"]
    indices = {"NIFTY": "^NSEI", "BANK NIFTY": "^NSEBANK"}
    for name, ticker in indices.items():
        try:
            d = yf.Ticker(ticker).history(period="2d")
            if len(d) >= 2:
                l = round(float(d['Close'].iloc[-1]), 2)
                p = round(float(d['Close'].iloc[-2]), 2)
                chg = round((l - p) / p * 100, 2)
                icon = "🟢" if chg >= 0 else "🔴"
                lines.append(f"{icon} {name}: {l:,.2f} ({chg}%)")
        except Exception:
            pass

    adv, dec = 0, 0
    top_stocks = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]
    for sym in top_stocks:
        df = get_hist(sym, "5d")
        if len(df) >= 2 and df['Close'].iloc[-1] > df['Close'].iloc[-2]:
            adv += 1
        else:
            dec += 1
    lines.append(f"\n🔢 Adv:{adv} Dec:{dec}")
    return "\n".join(lines)

def build_news():
    if not TAVILY_KEY:
        return "Set TAVILY_KEY for news."
    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_KEY, "query": "India stock market", "max_results": 3},
            timeout=5
        )
        data = response.json()
        results = data.get("results", [])
        return "\n".join([f"📰 {x['title']}" for x in results])
    except Exception as e:
        logger.error(f"News fetch error: {e}")
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
        "👋 Ready! Type a stock symbol (e.g., RELIANCE) or use the menu.",
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
        "📊 Nifty": "Give me the exact Nifty 50 level and trend analysis based on the provided live data.",
        "💎 Picks": "Suggest 2 stocks for swing trading based on current market conditions."
    }
    prompt = prompts.get(message.text, "")
    resp, err = call_ai([{"role": "user", "content": prompt}], use_context=True)
    bot.send_message(message.chat.id, resp or f"Err: {err}", reply_markup=ai_keyboard())

@bot.message_handler(func=lambda m: m.text in ["🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive"])
def scan_button(message):
    profile = message.text.split()[1].lower()
    bot.send_message(message.chat.id, build_scan(profile), parse_mode="HTML", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "📊 Breadth")
def breadth_button(message):
    bot.send_message(message.chat.id, build_breadth(), parse_mode="HTML", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🎯 Swing")
def swing_button(message):
    lines = ["🎯 SWING (RSI <35)", "━━━━━━━━━━━━━━━━━━━━"]
    candidates = ["RELIANCE", "TCS", "HDFCBANK", "TATAMOTORS"]
    found = False
    for sym in candidates:
        df = get_hist(sym, "2mo")
        if not df.empty:
            rsi = calc_rsi(df['Close'])
            asi = calc_asi(df)
            if rsi < 35:
                lines.append(f"🟢 {sym} RSI:{rsi} | ASI:{asi}")
                found = True
    if not found:
        lines.append("None found.")
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "📰 News")
def news_button(message):
    bot.send_message(message.chat.id, build_news(), reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    uid = message.chat.id
    text = message.text.strip()

    if _state.get(uid) == "ai":
        resp, err = call_ai([{"role": "user", "content": text}], use_context=True)
        bot.send_message(uid, resp or f"Err: {err}", reply_markup=ai_keyboard())
        return

    sym = text.upper().replace(".NS", "")
    if 2 <= len(sym) <= 15 and sym.replace("-", "").isalnum():
        bot.send_message(uid, f"🔍 Analyzing {sym}...")
        result = build_adv(sym)
        bot.send_message(uid, result, parse_mode="HTML", reply_markup=main_keyboard())
    else:
        bot.send_message(uid, "⚠️ Type a valid symbol (e.g., RELIANCE) or use the menu.", reply_markup=main_keyboard())

# ── Flask Webhook ─────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok", "version": "3.0_perfect_asi"})

def process_update(update_json):
    """Process a single update securely within the ThreadPool."""
    try:
        update = telebot.types.Update.de_json(update_json)
        bot.process_new_updates([update])
    except Exception as e:
        logger.error(f"Error processing update: {e}")

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if request.headers.get("content-type") != "application/json":
        return "Unsupported Media Type", 415
    data = request.get_data().decode("utf-8")
    try:
        update_id = json.loads(data).get("update_id")
        with _lock:
            if update_id in _processed_updates:
                return "OK", 200
            _processed_updates.add(update_id)
            if len(_processed_updates) > 200:
                _processed_updates.discard(min(_processed_updates))
    except Exception:
        pass

    # Use ThreadPool to prevent crashing Render RAM limits
    executor.submit(process_update, data)
    return "OK", 200

if __name__ == "__main__":
    logger.info("Pre-initializing yfinance session to fix curl_cffi threading bug...")
    try:
        _ 
