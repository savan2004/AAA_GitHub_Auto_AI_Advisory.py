"""
main.py  —  AI Stock Advisory Telegram Bot (Render/Cloud Fixed)
====================================================================
Fix: Added User-Agent headers to yfinance to bypass Yahoo Finance blocks.
     This resolves "No Data" issues on Render/Heroku/AWS.
"""
import os
import time
import logging
import threading
import requests
import json
import pandas as pd
import yfinance as yf
from collections import deque
from datetime import datetime
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
TAVILY_API_KEY    = os.getenv("TAVILY_API_KEY", "")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set")

WEBHOOK_PATH = f"/webhook/{TELEGRAM_TOKEN}"

app = Flask(__name__)
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

# ── CRITICAL FIX: User-Agent for Cloud Servers ───────────────────────────────
# Yahoo Finance blocks generic cloud requests. We must masquerade as a browser.
YF_SESSION = requests.Session()
YF_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
})

# ─- Global State & Thread Safety ──────────────────────────────────────────────
_cache = {}
_cache_lock = threading.Lock()
_user_state = {}
_user_state_lock = threading.Lock()
_user_history = {}
_processed_updates = set()
_processed_lock = threading.Lock()

CACHE_TTL = 900

# ─- AI Engine ─────────────────────────────────────────────────────────────────
_groq_client = None
_gemini_model = None
_openai_client = None

def get_groq():
    global _groq_client
    if _groq_client is None and GROQ_API_KEY:
        try:
            from groq import Groq
            _groq_client = Groq(api_key=GROQ_API_KEY)
        except Exception as e: logger.error(f"Groq Init Failed: {e}")
    return _groq_client

def get_gemini():
    global _gemini_model
    if _gemini_model is None and GEMINI_API_KEY:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            _gemini_model = genai.GenerativeModel("gemini-2.0-flash")
        except Exception as e: logger.error(f"Gemini Init Failed: {e}")
    return _gemini_model

def get_openai():
    global _openai_client
    if _openai_client is None and OPENAI_API_KEY:
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=OPENAI_API_KEY)
        except Exception as e: logger.error(f"OpenAI Init Failed: {e}")
    return _openai_client

def ai_available() -> bool:
    return bool(GROQ_API_KEY or GEMINI_API_KEY or OPENAI_API_KEY)

def call_ai(messages: list, max_tokens: int = 500, system: str = "") -> tuple:
    errors = []
    groq = get_groq()
    if groq:
        try:
            msgs = ([{"role": "system", "content": system}] if system else []) + messages
            resp = groq.chat.completions.create(model="llama-3.3-70b-versatile", messages=msgs, max_tokens=max_tokens)
            if resp.choices and resp.choices[0].message.content: return resp.choices[0].message.content.strip(), ""
        except Exception as e: errors.append(f"Groq: {str(e)[:80]}")
    
    gemini = get_gemini()
    if gemini:
        try:
            prompt = (system + "\n\n" if system else "") + "\n".join([f"{m['role']}: {m['content']}" for m in messages])
            resp = gemini.generate_content(prompt)
            if resp.text: return resp.text.strip(), ""
        except Exception as e: errors.append(f"Gemini: {str(e)[:80]}")

    openai = get_openai()
    if openai:
        try:
            msgs = ([{"role": "system", "content": system}] if system else []) + messages
            resp = openai.chat.completions.create(model="gpt-4o-mini", messages=msgs, max_tokens=max_tokens)
            if resp.choices and resp.choices[0].message.content: return resp.choices[0].message.content.strip(), ""
        except Exception as e: errors.append(f"OpenAI: {str(e)[:80]}")
    
    if not errors and not ai_available(): return "", "⚠️ No AI Keys found."
    return "", "\n".join(errors)

def ai_insights(symbol: str, ltp: float, rsi: float, trend: str, pe: str) -> str:
    if not ai_available(): return "⚠️ AI Disabled."
    prompt = (f"Give 3-bullet BULLISH factors and 2-bullet RISKS for {symbol} (NSE).\nData: LTP ₹{ltp}, RSI {rsi}, Trend {trend}, PE {pe}.")
    text, err = call_ai([{"role": "user", "content": prompt}], max_tokens=300)
    return text if text else f"AI Error: {err}"

# ─- Technical Calculations ────────────────────────────────────────────────────
def get_cached(key):
    with _cache_lock:
        item = _cache.get(key)
        if item and time.time() - item["ts"] < CACHE_TTL: return item["val"]
    return None

def set_cached(key, val):
    with _cache_lock: _cache[key] = {"val": val, "ts": time.time()}

def compute_rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1: return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    return round(float((100 - 100 / (1 + rs)).iloc[-1]), 1)

def compute_macd(close: pd.Series):
    line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = line.ewm(span=9, adjust=False).mean()
    return round(float(line.iloc[-1]), 2), round(float(signal.iloc[-1]), 2)

def compute_ema(close: pd.Series, span: int) -> float:
    return round(float(close.ewm(span=span, adjust=False).mean().iloc[-1]), 2)

def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return round(float(tr.rolling(period).mean().iloc[-1]), 2)

def fmt(v, suffix=""): return f"{v:.2f}{suffix}" if v is not None else "N/A"
def crore(v): return f"₹{v/1e7:,.0f} Cr" if v else "N/A"

def _safe_get(d, *keys, mult=1.0):
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                f = float(v)
                if f != 0: return round(f * mult, 2)
            except: pass
    return None

def extract_fundamentals(info: dict) -> dict:
    return {
        "company": info.get("longName") or "N/A",
        "sector": info.get("sector") or "N/A",
        "pe": _safe_get(info, "trailingPE", "forwardPE"),
        "pb": _safe_get(info, "priceToBook"),
        "roe": _safe_get(info, "returnOnEquity", mult=100),
        "mcap": _safe_get(info, "marketCap"),
    }

# ─- Individual Stock Analysis (WITH SESSION FIX) ───────────────────────────────
def fetch_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    """Fetches history using a custom Session to bypass blocks."""
    key = f"hist_{symbol}_{period}"
    cached = get_cached(key)
    if cached is not None: return cached
    
    ticker_str = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
    
    try:
        # FIX: Pass the global YF_SESSION with User-Agent
        df = yf.Ticker(ticker_str, session=YF_SESSION).history(period=period, interval="1d", auto_adjust=True)
        if df.empty or len(df) < 5: return pd.DataFrame()
        set_cached(key, df)
        return df
    except Exception as e:
        logger.error(f"History fetch error {symbol}: {e}")
        return pd.DataFrame()

def fetch_info(symbol: str) -> dict:
    key = f"info_{symbol}"
    cached = get_cached(key)
    if cached is not None: return cached
    info = {}
    try:
        # FIX: Pass the global YF_SESSION with User-Agent
        t = yf.Ticker(f"{symbol}.NS", session=YF_SESSION)
        fi = t.fast_info
        if fi:
            info["marketCap"] = getattr(fi, "market_cap", None)
        raw = t.info
        if raw: info.update(raw)
    except: pass
    set_cached(key, info)
    return info

def build_advisory(symbol: str) -> str:
    symbol = symbol.upper().replace(".NS", "")
    df = fetch_history(symbol)
    info = fetch_info(symbol)
    if df.empty: return f"❌ {symbol} not found."

    close = df["Close"]
    ltp = round(float(close.iloc[-1]), 2)
    prev = round(float(close.iloc[-2]), 2) if len(close) > 1 else ltp
    chg = round(((ltp - prev) / prev) * 100, 2)
    
    f = extract_fundamentals(info)
    rsi = compute_rsi(close)
    macd_l, macd_s = compute_macd(close)
    ema20, ema50 = compute_ema(close, 20), compute_ema(close, 50)
    atr = compute_atr(df)
    trend = "BULLISH" if ltp > ema20 > ema50 else ("BEARISH" if ltp < ema20 < ema50 else "NEUTRAL")
    
    ai_text = ai_insights(symbol, ltp, rsi, trend, fmt(f["pe"]))
    
    lines = [
        f"🏢 <b>{f['company']}</b> ({symbol})", f"💰 LTP: ₹{ltp} ({chg}%)",
        f"━━━━━━━━━━━━━━━━━━━━", f"📊 <b>FUNDAMENTALS</b>",
        f"PE: {fmt(f['pe'])} | ROE: {fmt(f['roe'], '%')}", f"MCap: {crore(f['mcap'])}",
        f"━━━━━━━━━━━━━━━━━━━━", f"🔬 <b>TECHNICALS</b>",
        f"Trend: {trend} | RSI: {rsi}", f"MACD: {'Bullish' if macd_l > macd_s else 'Bearish'}",
        f"━━━━━━━━━━━━━━━━━━━━", f"🎯 <b>TRADE SETUP</b>",
        f"Target: ₹{round(ltp + 1.5*atr, 2)} | SL: ₹{round(ltp - 2*atr, 2)}",
        f"━━━━━━━━━━━━━━━━━━━━", f"🤖 <b>AI INSIGHTS</b>", ai_text,
        f"⚠️ Educational only."
    ]
    return "\n".join(lines)

# ─- Robust Screeners ──────────────────────────────────────────────────────────
def build_portfolio_scan(profile: str) -> str:
    stocks_map = {
        "conservative": ["HDFCBANK", "TCS", "INFY", "ITC", "ONGC", "WIPRO", "SBIN"],
        "moderate": ["RELIANCE", "BHARTIARTL", "AXISBANK", "MARUTI", "TITAN", "BAJFINANCE"],
        "aggressive": ["TATAMOTORS", "ADANIENT", "JSWSTEEL", "TATAPOWER", "DIXON", "IRFC"]
    }
    stocks = stocks_map.get(profile, [])
    if not stocks: return "Invalid profile."
    
    lines = [f"📊 <b>{profile.upper()} PORTFOLIO SCAN</b>", "━━━━━━━━━━━━━━━━━━━━"]
    
    for sym in stocks:
        try:
            # Uses fetch_history which has the User-Agent fix
            df = fetch_history(sym, period="1mo") 
            
            if df.empty or len(df) < 2: 
                lines.append(f"⚪ {sym}: No Data")
                continue
            
            close = df["Close"]
            ltp = round(float(close.iloc[-1]), 2)
            prev = round(float(close.iloc[-2]), 2)
            chg = round((ltp - prev)/prev * 100, 2)
            rsi = compute_rsi(close)
            
            em = "🟢" if chg >= 0 else "🔴"
            lines.append(f"{em} <b>{sym}</b>: ₹{ltp} ({chg}%) RSI:{rsi}")
        except Exception as e:
            logger.error(f"Scan error {sym}: {e}")
            lines.append(f"⚠️ {sym}: Error")

    return "\n".join(lines)

def build_market_breadth() -> str:
    indices = {"NIFTY 50": "^NSEI", "BANK NIFTY": "^NSEBANK"}
    lines = ["📊 <b>MARKET BREADTH</b>", "━━━━━━━━━━━━━━━━━━━━"]
    
    for name, tic in indices.items():
        try:
            # Use session for indices too
            df = yf.Ticker(tic, session=YF_SESSION).history(period="2d")
            if len(df) >= 2:
                ltp = round(float(df["Close"].iloc[-1]), 2)
                prev = round(float(df["Close"].iloc[-2]), 2)
                chg = round((ltp-prev)/prev*100, 2)
                em = "🟢" if chg >= 0 else "🔴"
                lines.append(f"{em} <b>{name}</b>: {ltp:,.2f} ({chg}%)")
        except: pass

    adv, dec = 0, 0
    watchlist = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "ITC", "SBIN", "LT", "AXISBANK", "KOTAKBANK"]
    
    for sym in watchlist:
        try:
            df = fetch_history(sym, period="5d")
            if len(df) >= 2:
                c = df["Close"]
                if c.iloc[-1] > c.iloc[-2]: adv += 1
                else: dec += 1
        except: pass
        
    lines.append(f"\n🔢 Advances: {adv} | Declines: {dec}")
    lines.append("⚠️ Educational only.")
    return "\n".join(lines)

def build_swing_scan() -> str:
    stocks = ["RELIANCE", "TCS", "HDFCBANK", "TATAMOTORS", "SBIN", "BAJFINANCE", "MARUTI", "AXISBANK", "ICICIBANK", "HCLTECH"]
    lines = ["🎯 <b>SWING TRADE SCAN</b>", "━━━━━━━━━━━━━━━━━━━━"]
    
    found = 0
    for sym in stocks:
        try:
            df = fetch_history(sym, period="2mo")
            if df.empty: continue
            
            close = df["Close"]
            rsi = compute_rsi(close)
            ltp = round(float(close.iloc[-1]), 2)
            
            if rsi < 35:
                lines.append(f"🟢 <b>{sym}</b> @ ₹{ltp} (RSI: {rsi} - Oversold)")
                found += 1
            elif rsi > 65:
                lines.append(f"🔴 <b>{sym}</b> @ ₹{ltp} (RSI: {rsi} - Overbought)")
                found += 1
        except: pass
    
    if found == 0: lines.append("No strong setups found right now.")
    return "\n".join(lines)

def build_market_news() -> str:
    if TAVILY_API_KEY:
        try:
            r = requests.post("https://api.tavily.com/search", json={
                "api_key": TAVILY_API_KEY, "query": "Indian stock market news", "max_results": 3
            }, timeout=5).json()
            headlines = [f"📰 {x['title']}" for x in r.get("results", [])]
            if headlines: return "<b>📰 MARKET NEWS</b>\n\n" + "\n".join(headlines)
        except: pass
    return "📰 News unavailable (Set TAVILY_API_KEY)."

# ─- Keyboards & Handlers ──────────────────────────────────────────────────────
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add(types.KeyboardButton("🔍 Stock Analysis"), types.KeyboardButton("📊 Market Breadth"), types.KeyboardButton("🤖 AI Chat"))
    kb.add(types.KeyboardButton("🏦 Conservative"), types.KeyboardButton("⚖️ Moderate"), types.KeyboardButton("🚀 Aggressive"))
    kb.add(types.KeyboardButton("🎯 Swing Scan"), types.KeyboardButton("📰 Market News"), types.KeyboardButton("📋 Usage"))
    return kb

def ai_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("📊 Nifty Valuation"), types.KeyboardButton("💎 Fundamental Picks"), types.KeyboardButton("📈 Nifty Update"), types.KeyboardButton("🔙 Main Menu"))
    return kb

AI_TOPICS = {
    "📊 Nifty Valuation": "What is the current Nifty 50 PE ratio? Is it overvalued?",
    "💎 Fundamental Picks": "Give 3 fundamentally strong NSE stocks with low PE.",
    "📈 Nifty Update": "Give a technical update on Nifty 50.",
}

def set_user_state(uid, state):
    with _user_state_lock: _user_state[uid] = state

def get_user_state(uid):
    with _user_state_lock: return _user_state.get(uid)

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    set_user_state(msg.from_user.id, None)
    bot.send_message(msg.chat.id, "👋 Welcome!\nType a symbol (e.g., RELIANCE) or use menu.", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🔙 Main Menu")
def to_main(msg):
    set_user_state(msg.from_user.id, None)
    bot.send_message(msg.chat.id, "🏠 Main Menu", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🤖 AI Chat")
def enter_ai(msg):
    set_user_state(msg.from_user.id, "ai_chat")
    bot.send_message(msg.chat.id, "🤖 <b>AI Chat Mode</b>", reply_markup=ai_keyboard(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text in AI_TOPICS)
def handle_ai_topic(msg):
    q = AI_TOPICS[msg.text]
    bot.send_message(msg.chat.id, "🤖 Thinking...")
    resp, err = call_ai([{"role": "user", "content": q}])
    bot.send_message(msg.chat.id, resp if resp else f"Error: {err}", reply_markup=ai_keyboard())

@bot.message_handler(func=lambda m: m.text in ["🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive"])
def handle_port(msg):
    profile = msg.text.split(" ")[1].lower()
    bot.send_message(msg.chat.id, f"⏳ Scanning {profile} portfolio...")
    bot.send_message(msg.chat.id, build_portfolio_scan(profile), reply_markup=main_keyboard(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🎯 Swing Scan")
def handle_swing(msg):
    bot.send_message(msg.chat.id, "⏳ Scanning...")
    bot.send_message(msg.chat.id, build_swing_scan(), reply_markup=main_keyboard(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📊 Market Breadth")
def handle_breadth(msg):
    bot.send_message(msg.chat.id, build_market_breadth(), reply_markup=main_keyboard(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📰 Market News")
def handle_news(msg):
    bot.send_message(msg.chat.id, build_market_news(), reply_markup=main_keyboard(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📋 Usage")
def handle_usage(msg):
    uid = msg.from_user.id
    hist = list(_user_history.get(uid, []))
    txt = f"📋 <b>Usage Stats</b>\n\nRecent Symbols:\n" + "\n".join([f"• {s}" for s in hist]) if hist else "No history."
    bot.send_message(uid, txt, reply_markup=main_keyboard(), parse_mode="HTML")

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_all(msg):
    uid = msg.from_user.id
    text = msg.text.strip()
    state = get_user_state(uid)

    if state == "ai_chat":
        bot.send_message(uid, "🤖 Thinking...")
        resp, err = call_ai([{"role": "user", "content": text}])
        bot.send_message(uid, resp if resp else f"Error: {err}", reply_markup=ai_keyboard())
        return

    clean = text.upper().replace(".NS", "")
    if 2 <= len(clean) <= 15 and clean.replace("-", "").isalnum():
        if uid not in _user_history: _user_history[uid] = deque(maxlen=5)
        _user_history[uid].appendleft(clean)
        
        bot.send_message(uid, f"🔍 Analyzing {clean}...")
        try:
            bot.send_message(uid, build_advisory(clean), parse_mode="HTML", reply_markup=main_keyboard())
        except Exception as e:
            bot.send_message(uid, "❌ Analysis failed.", reply_markup=main_keyboard())
    else:
        bot.send_message(uid, "⚠️ Unrecognized. Type a symbol or use menu.", reply_markup=main_keyboard())

# ─- Flask Routes ──────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index(): return jsonify({"status": "running"})

@app.route("/health", methods=["GET"])
def health(): return "OK", 200

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
        
        with _processed_lock:
            if uid in _processed_updates: return "OK", 200
            _processed_updates.add(uid)
            if len(_processed_updates) > 1000: _processed_updates.pop()
        
        threading.Thread(target=process_update_async, args=(json_str,)).start()
        return "OK", 200
    return "Bad Request", 400

if __name__ == "__main__":
    logger.info(f"Starting server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
