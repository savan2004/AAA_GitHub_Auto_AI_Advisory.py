"""
main.py  —  AI Stock Advisory Telegram Bot (Refactored & Fixed)
====================================================================
Deployment: gunicorn main:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120
Env Vars:   TELEGRAM_TOKEN, WEBHOOK_URL, GROQ_API_KEY, GEMINI_API_KEY,
            OPENAI_KEY, ALPHA_VANTAGE_KEY, FINNHUB_API_KEY, TAVILY_API_KEY
"""

import os
import time
import logging
import threading
import requests
import json
from collections import deque
from datetime import datetime

import pandas as pd
import yfinance as yf
from flask import Flask, request, jsonify
import telebot
from telebot import types

# ── Logging Setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config & Constants ────────────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
WEBHOOK_URL       = os.getenv("WEBHOOK_URL", "").rstrip("/")
PORT              = int(os.getenv("PORT", 8000))

# API Keys
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY    = os.getenv("OPENAI_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
FINNHUB_API_KEY   = os.getenv("FINNHUB_API_KEY", "")
TAVILY_API_KEY    = os.getenv("TAVILY_API_KEY", "")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set")

WEBHOOK_PATH = f"/webhook/{TELEGRAM_TOKEN}"

app = Flask(__name__)
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

# ── Global State & Thread Safety ──────────────────────────────────────────────
_cache = {}
_cache_lock = threading.Lock()
_user_state = {}
_user_state_lock = threading.Lock()
_user_history = {}
_usage_stats = {}
_chat_history = {}
_processed_updates = set()
_processed_lock = threading.Lock()

CACHE_TTL = 900  # 15 minutes

# ── AI Client Globals (Lazy Init) ─────────────────────────────────────────────
_groq_client = None
_gemini_model = None
_openai_client = None

# ══════════════════════════════════════════════════════════════════════════════
# AI ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def get_groq():
    global _groq_client
    if _groq_client is None and GROQ_API_KEY:
        try:
            from groq import Groq
            _groq_client = Groq(api_key=GROQ_API_KEY)
            logger.info("AI Engine: Groq Client Initialized")
        except Exception as e:
            logger.error(f"AI Engine: Groq Init Failed - {e}")
    return _groq_client

def get_gemini():
    global _gemini_model
    if _gemini_model is None and GEMINI_API_KEY:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            _gemini_model = genai.GenerativeModel("gemini-2.0-flash")
            logger.info("AI Engine: Gemini Client Initialized")
        except Exception as e:
            logger.error(f"AI Engine: Gemini Init Failed - {e}")
    return _gemini_model

def get_openai():
    global _openai_client
    if _openai_client is None and OPENAI_API_KEY:
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=OPENAI_API_KEY)
            logger.info("AI Engine: OpenAI Client Initialized")
        except Exception as e:
            logger.error(f"AI Engine: OpenAI Init Failed - {e}")
    return _openai_client

def ai_available() -> bool:
    return bool(GROQ_API_KEY or GEMINI_API_KEY or OPENAI_API_KEY)

def call_ai(messages: list, max_tokens: int = 500, system: str = "") -> tuple:
    """
    Robust AI calling function with fallback chain.
    Returns: (response_text, error_message)
    """
    errors = []

    # 1. Try Groq (Fastest)
    groq = get_groq()
    if groq:
        try:
            msgs = ([{"role": "system", "content": system}] if system else []) + messages
            resp = groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=msgs,
                max_tokens=max_tokens,
                temperature=0.5
            )
            if resp.choices and resp.choices[0].message.content:
                return resp.choices[0].message.content.strip(), ""
        except Exception as e:
            err_str = str(e)
            if "401" in err_str or "invalid" in err_str.lower():
                errors.append("Groq: Invalid API Key")
            else:
                errors.append(f"Groq: {err_str[:80]}")
    
    # 2. Try Gemini
    gemini = get_gemini()
    if gemini:
        try:
            prompt = (system + "\n\n" if system else "") + "\n".join([f"{m['role']}: {m['content']}" for m in messages])
            resp = gemini.generate_content(prompt)
            if resp.text:
                return resp.text.strip(), ""
        except Exception as e:
            err_str = str(e)
            if "API_KEY_INVALID" in err_str:
                errors.append("Gemini: Invalid API Key")
            else:
                errors.append(f"Gemini: {err_str[:80]}")

    # 3. Try OpenAI
    openai = get_openai()
    if openai:
        try:
            msgs = ([{"role": "system", "content": system}] if system else []) + messages
            resp = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=msgs,
                max_tokens=max_tokens,
                temperature=0.5
            )
            if resp.choices and resp.choices[0].message.content:
                return resp.choices[0].message.content.strip(), ""
        except Exception as e:
            err_str = str(e)
            if "401" in err_str:
                errors.append("OpenAI: Invalid API Key")
            else:
                errors.append(f"OpenAI: {err_str[:80]}")

    # If all failed
    if not errors:
        if not ai_available():
            return "", "⚠️ No AI Keys configured. Add GROQ_API_KEY or GEMINI_API_KEY in environment variables."
        errors.append("All providers failed or keys missing.")
    
    return "", "\n".join(errors)

def ai_insights(symbol: str, ltp: float, rsi: float, macd_line: float, trend: str, pe: str, roe: str) -> str:
    """Generate brief insight snippet for stock card."""
    if not ai_available():
        return "⚠️ AI Disabled: Configure API Keys."

    prompt = (
        f"Give 3-bullet BULLISH factors and 2-bullet RISKS for {symbol} (NSE India).\n"
        f"Data: LTP ₹{ltp}, RSI {rsi}, MACD {'bullish' if macd_line > 0 else 'bearish'}, "
        f"Trend {trend}, PE {pe}, ROE {roe}%.\n"
        f"Format exactly:\nBULLISH:\n• ...\n• ...\n• ...\nRISKS:\n• ...\n• ..."
    )
    
    text, err = call_ai(
        [{"role": "user", "content": prompt}],
        max_tokens=300,
        system="You are a concise Indian equity analyst."
    )
    
    if text:
        return text
    return f"⚠️ AI Analysis Error:\n{err}"

# ══════════════════════════════════════════════════════════════════════════════
# DATA FETCHING & ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def get_cached(key):
    with _cache_lock:
        item = _cache.get(key)
        if item and time.time() - item["ts"] < CACHE_TTL:
            return item["val"]
    return None

def set_cached(key, val):
    with _cache_lock:
        _cache[key] = {"val": val, "ts": time.time()}

def fetch_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    key = f"hist_{symbol}_{period}"
    cached = get_cached(key)
    if cached is not None:
        return cached

    ticker = f"{symbol}.NS"
    try:
        df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
        if df.empty or len(df) < 5:
            return pd.DataFrame()
        set_cached(key, df)
        return df
    except Exception as e:
        logger.error(f"yfinance error {symbol}: {e}")
        return pd.DataFrame()

def fetch_info(symbol: str) -> dict:
    key = f"info_{symbol}"
    cached = get_cached(key)
    if cached is not None:
        return cached

    ticker = f"{symbol}.NS"
    info = {}
    try:
        t = yf.Ticker(ticker)
        # Try fast_info first (reliable for price/mcap)
        fi = t.fast_info
        if fi:
            info["marketCap"] = getattr(fi, "market_cap", None)
            info["fiftyTwoWeekHigh"] = getattr(fi, "year_high", None)
            info["fiftyTwoWeekLow"] = getattr(fi, "year_low", None)
        
        # Try regular info (for PE/ROE)
        raw_info = t.info
        if raw_info:
            info.update(raw_info)
            
    except Exception as e:
        logger.warning(f"Info fetch failed {symbol}: {e}")
    
    set_cached(key, info)
    return info

def compute_rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1: return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    val = (100 - 100 / (1 + rs)).iloc[-1]
    return round(float(val), 1) if pd.notna(val) else 50.0

def compute_macd(close: pd.Series):
    line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = line.ewm(span=9, adjust=False).mean()
    return round(float(line.iloc[-1]), 2), round(float(signal.iloc[-1]), 2)

def compute_ema(close: pd.Series, span: int) -> float:
    return round(float(close.ewm(span=span, adjust=False).mean().iloc[-1]), 2)

def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return round(float(val), 2) if pd.notna(val) else 0.0

def _safe_get(d, *keys, mult=1.0):
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                f = float(v)
                if f != 0:
                    return round(f * mult, 2)
            except: pass
    return None

def extract_fundamentals(info: dict) -> dict:
    return {
        "company": info.get("longName") or info.get("shortName") or "N/A",
        "sector": info.get("sector") or "N/A",
        "pe": _safe_get(info, "trailingPE", "forwardPE"),
        "pb": _safe_get(info, "priceToBook"),
        "roe": _safe_get(info, "returnOnEquity", mult=100),
        "div": _safe_get(info, "dividendYield", mult=100),
        "mcap": _safe_get(info, "marketCap"),
        "high_52w": _safe_get(info, "fiftyTwoWeekHigh"),
        "low_52w": _safe_get(info, "fiftyTwoWeekLow"),
    }

def fmt(v, suffix=""):
    return f"{v:.2f}{suffix}" if v is not None else "N/A"

def crore(v):
    if v is None: return "N/A"
    c = v / 1e7
    return f"₹{c/1e5:.2f}L Cr" if c >= 1e5 else f"₹{c:,.0f} Cr"

def quality_score(f: dict, rsi: float, trend: str) -> tuple:
    score = 0
    # Simple scoring
    if f["pe"] and f["pe"] < 25: score += 15
    if f["roe"] and f["roe"] > 15: score += 15
    if trend == "BULLISH": score += 20
    if 40 < rsi < 60: score += 10
    
    if score >= 75: verdict = "STRONG BUY"
    elif score >= 50: verdict = "BUY"
    elif score >= 30: verdict = "HOLD"
    else: verdict = "AVOID"
    
    return score, f"{score}/100  |  {verdict}"

# ══════════════════════════════════════════════════════════════════════════════
# MESSAGE BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def build_advisory(symbol: str) -> str:
    symbol = symbol.upper().replace(".NS", "")
    df = fetch_history(symbol)
    info = fetch_info(symbol)

    if df.empty:
        return f"❌ <b>{symbol}</b> not found or data unavailable."

    close = df["Close"]
    ltp = round(float(close.iloc[-1]), 2)
    prev = round(float(close.iloc[-2]), 2) if len(close) > 1 else ltp
    chg = round(((ltp - prev) / prev) * 100, 2)
    
    f = extract_fundamentals(info)
    
    # Technicals
    rsi = compute_rsi(close)
    macd_l, macd_s = compute_macd(close)
    ema20 = compute_ema(close, 20)
    ema50 = compute_ema(close, 50)
    atr = compute_atr(df)
    
    trend = "BULLISH" if ltp > ema20 > ema50 else ("BEARISH" if ltp < ema20 < ema50 else "NEUTRAL")
    
    # Targets
    sl = round(ltp - 2*atr, 2)
    tgt1 = round(ltp + 1.5*atr, 2)
    tgt2 = round(ltp + 3*atr, 2)
    
    # AI Insights
    ai_text = ai_insights(symbol, ltp, rsi, macd_l, trend, fmt(f["pe"]), fmt(f["roe"]))
    
    # Score
    score_num, score_str = quality_score(f, rsi, trend)
    
    # Output
    lines = [
        f"🏢 <b>{f['company']}</b> ({symbol})",
        f"🏭 Sector: {f['sector']}",
        f"💰 LTP: ₹{ltp} ({'+' if chg>=0 else ''}{chg}%)",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📊 <b>FUNDAMENTALS</b>",
        f"PE: {fmt(f['pe'])}x | PB: {fmt(f['pb'])}x",
        f"ROE: {fmt(f['roe'], '%')} | Div: {fmt(f['div'], '%')}",
        f"MCap: {crore(f['mcap'])}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"🔬 <b>TECHNICALS</b>",
        f"Trend: {trend}",
        f"RSI: {rsi} | MACD: {'Bullish' if macd_l > macd_s else 'Bearish'}",
        f"EMA20: {ema20} | EMA50: {ema50}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"🎯 <b>TRADE SETUP</b>",
        f"Target 1: ₹{tgt1} | Target 2: ₹{tgt2}",
        f"Stop Loss: ₹{sl}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"🤖 <b>AI INSIGHTS</b>",
        ai_text,
        f"━━━━━━━━━━━━━━━━━━━━",
        f"🏆 <b>QUALITY</b>: {score_str}",
        f"⚠️ Educational only."
    ]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# PORTFOLIOS & CHAT TOPICS
# ══════════════════════════════════════════════════════════════════════════════

PORTFOLIOS = {
    "conservative": ["HDFCBANK", "TCS", "INFY", "ITC", "ONGC"],
    "moderate": ["RELIANCE", "BHARTIARTL", "AXISBANK", "MARUTI", "TITAN"],
    "aggressive": ["TATAMOTORS", "ADANIENT", "JSWSTEEL", "TATAPOWER", "DIXON"]
}

AI_CHAT_TOPICS = {
    "📊 Nifty Valuation": "What is the current Nifty 50 PE ratio? Is it overvalued?",
    "💎 Fundamental Picks": "Give 3 fundamentally strong NSE stocks with low PE.",
    "📈 Nifty Update": "Give a technical update on Nifty 50.",
    "🎯 Swing Trade": "Suggest a swing trade setup for NSE.",
    "⚡ Option Trade": "Suggest an option trade for Nifty/BankNifty.",
}

def build_portfolio_scan(profile: str) -> str:
    stocks = PORTFOLIOS.get(profile, [])
    lines = [f"📊 <b>{profile.upper()} PORTFOLIO SCAN</b>", "━━━━━━━━━━━━━━━━━━━━"]
    
    # Simple batch logic (yfinance download can be used here for optimization)
    for sym in stocks:
        df = fetch_history(sym, period="1mo")
        if df.empty:
            lines.append(f"• {sym}: Data error")
            continue
        
        close = df["Close"]
        ltp = round(float(close.iloc[-1]), 2)
        prev = round(float(close.iloc[-2]), 2)
        chg = round((ltp - prev)/prev * 100, 2)
        rsi = compute_rsi(close)
        
        em = "🟢" if chg >= 0 else "🔴"
        lines.append(f"{em} <b>{sym}</b>: ₹{ltp} ({chg}%) RSI:{rsi}")
        
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM BOT HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("🔍 Stock Analysis"),
        types.KeyboardButton("🤖 AI Chat"),
        types.KeyboardButton("🏦 Conservative"),
        types.KeyboardButton("⚖️ Moderate"),
        types.KeyboardButton("🚀 Aggressive")
    )
    return kb

def ai_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📊 Nifty Valuation"),
        types.KeyboardButton("💎 Fundamental Picks"),
        types.KeyboardButton("📈 Nifty Update"),
        types.KeyboardButton("🔙 Main Menu")
    )
    return kb

def set_user_state(uid, state):
    with _user_state_lock:
        _user_state[uid] = state

def get_user_state(uid):
    with _user_state_lock:
        return _user_state.get(uid)

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    set_user_state(msg.from_user.id, None)
    bot.send_message(msg.chat.id, 
        "👋 Welcome to AI Stock Advisor!\n\n"
        "Type a symbol (e.g., RELIANCE) or use the menu.", 
        reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🔙 Main Menu")
def to_main(msg):
    set_user_state(msg.from_user.id, None)
    bot.send_message(msg.chat.id, "Main Menu", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🤖 AI Chat")
def enter_ai_chat(msg):
    set_user_state(msg.from_user.id, "ai_chat")
    bot.send_message(msg.chat.id, "🤖 <b>AI Chat Mode</b>\nAsk anything about the market or use quick buttons.", reply_markup=ai_keyboard(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text in AI_CHAT_TOPICS)
def handle_ai_topic(msg):
    uid = msg.from_user.id
    q = AI_CHAT_TOPICS[msg.text]
    bot.send_message(msg.chat.id, "🤖 Analyzing...")
    resp, err = call_ai([{"role": "user", "content": q}])
    if not resp: resp = f"Error: {err}"
    bot.send_message(msg.chat.id, resp, reply_markup=ai_keyboard())

@bot.message_handler(func=lambda m: m.text in ["🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive"])
def handle_portfolio(msg):
    profile = msg.text.split(" ")[1].lower()
    bot.send_message(msg.chat.id, f"⏳ Scanning {profile} portfolio...")
    bot.send_message(msg.chat.id, build_portfolio_scan(profile), reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_all(msg):
    uid = msg.from_user.id
    text = msg.text.strip()
    state = get_user_state(uid)

    # 1. Handle AI Chat Mode
    if state == "ai_chat":
        bot.send_message(msg.chat.id, "🤖 Thinking...")
        resp, err = call_ai([{"role": "user", "content": text}])
        if not resp: resp = f"Error: {err}"
        bot.send_message(msg.chat.id, resp, reply_markup=ai_keyboard())
        return

    # 2. Assume Stock Symbol
    clean = text.upper().replace(".NS", "")
    if len(clean) < 10 and clean.isalpha():
        bot.send_message(msg.chat.id, f"🔍 Analyzing {clean}...")
        try:
            advisory = build_advisory(clean)
            bot.send_message(msg.chat.id, advisory, parse_mode="HTML", reply_markup=main_keyboard())
        except Exception as e:
            logger.error(f"Analysis error: {e}")
            bot.send_message(msg.chat.id, "❌ Error analyzing symbol.", reply_markup=main_keyboard())
        return

    # 3. Fallback
    bot.send_message(msg.chat.id, "⚠️ Unrecognized input. Type a stock symbol or use the menu.", reply_markup=main_keyboard())

# ══════════════════════════════════════════════════════════════════════════════
# FLASK & WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "running", "time": datetime.utcnow().isoformat()})

@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

def process_update_async(json_str):
    try:
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        logger.error(f"Async update error: {e}")

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data().decode("utf-8")
        update = json.loads(json_str)
        uid = update.get("update_id")
        
        # Dedup
        with _processed_lock:
            if uid in _processed_updates:
                return "OK", 200
            _processed_updates.add(uid)
            if len(_processed_updates) > 1000: _processed_updates.pop()
        
        # Process in background thread to return 200 immediately
        threading.Thread(target=process_update_async, args=(json_str,)).start()
        return "OK", 200
    return "Bad Request", 400

@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    if not WEBHOOK_URL:
        return "WEBHOOK_URL not set", 500
    url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
    bot.remove_webhook()
    time.sleep(1)
    if bot.set_webhook(url=url):
        return f"Webhook set to {url}", 200
    return "Failed to set webhook", 500

if __name__ == "__main__":
    logger.info(f"Starting server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
