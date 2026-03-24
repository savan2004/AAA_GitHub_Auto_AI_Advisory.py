"""
main.py — AI Stock Advisory Bot (Final Fixed Version)
Fixes:
1. SyntaxError in RSI calculation.
2. AI now receives Live Market Context to avoid "No real-time data" answers.
"""
import os, time, logging, threading, requests, json, pandas as pd, yfinance as yf
from collections import deque
from datetime import datetime
from flask import Flask, request, jsonify
import telebot
from telebot import types

# ── Config & Setup ───────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_KEY = os.getenv("OPENAI_KEY", "")
TAVILY_KEY = os.getenv("TAVILY_API_KEY", "")

if not TOKEN: raise RuntimeError("TELEGRAM_TOKEN missing")
WEBHOOK_PATH = f"/webhook/{TOKEN}"

app = Flask(__name__)
bot = telebot.TeleBot(TOKEN, threaded=False)

# ── Sessions & State ─────────────────────────────────────────────────────────
# CRITICAL: Custom User-Agent to bypass Yahoo Finance blocks on Render/Cloud
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})

_cache, _state, _history, _processed = {}, {}, {}, set()
_lock = threading.Lock()
CACHE_TTL = 900

# ── AI Engine ─────────────────────────────────────────────────────────────────
_groq, _gemini, _openai = None, None, None

def get_ai_clients():
    global _groq, _gemini, _openai
    if not _groq and GROQ_KEY:
        try:
            from groq import Groq
            _groq = Groq(api_key=GROQ_KEY)
        except: pass
    if not _gemini and GEMINI_KEY:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_KEY)
            _gemini = genai.GenerativeModel("gemini-2.0-flash")
        except: pass
    if not _openai and OPENAI_KEY:
        try:
            from openai import OpenAI
            _openai = OpenAI(api_key=OPENAI_KEY)
        except: pass
    return _groq, _gemini, _openai

def get_live_context():
    """Fetches live Nifty/BankNifty levels for AI context."""
    ctx = []
    try:
        n = yf.Ticker("^NSEI", session=SESSION).history(period="2d")
        if len(n) >= 2:
            l, p = round(float(n.Close.iloc[-1]),2), round(float(n.Close.iloc[-2]),2)
            ctx.append(f"NIFTY 50: {l} ({round((l-p)/p*100,2)}%)")
    except: pass
    try:
        b = yf.Ticker("^NSEBANK", session=SESSION).history(period="2d")
        if len(b) >= 2:
            l, p = round(float(b.Close.iloc[-1]),2), round(float(b.Close.iloc[-2]),2)
            ctx.append(f"BANK NIFTY: {l} ({round((l-p)/p*100,2)}%)")
    except: pass
    return "\n".join(ctx) if ctx else "Market data unavailable."

def call_ai(messages, max_tokens=400, system="", use_context=False):
    errs = []
    g, gm, o = get_ai_clients()
    
    # Inject Live Data if requested
    sys_prompt = system
    if use_context:
        live_data = get_live_context()
        sys_prompt += f"\n\nLIVE MARKET DATA (Use these numbers in your answer):\n{live_data}"

    msgs = ([{"role": "system", "content": sys_prompt}] if sys_prompt else []) + messages
    
    if g:
        try:
            r = g.chat.completions.create(model="llama-3.3-70b-versatile", messages=msgs, max_tokens=max_tokens)
            if r.choices[0].message.content: return r.choices[0].message.content.strip(), ""
        except Exception as e: errs.append(f"Groq: {str(e)[:50]}")
    
    if gm:
        try:
            p = (sys_prompt+"\n\n" if sys_prompt else "") + "\n".join([f"{m['role']}: {m['content']}" for m in messages])
            r = gm.generate_content(p)
            if r.text: return r.text.strip(), ""
        except Exception as e: errs.append(f"Gemini: {str(e)[:50]}")

    if o:
        try:
            r = o.chat.completions.create(model="gpt-4o-mini", messages=msgs, max_tokens=max_tokens)
            if r.choices[0].message.content: return r.choices[0].message.content.strip(), ""
        except Exception as e: errs.append(f"OpenAI: {str(e)[:50]}")
        
    return "", "\n".join(errs) if errs else "No AI Keys Configured."

def ai_insights(sym, ltp, rsi, trend, pe):
    if not (GROQ_KEY or GEMINI_KEY or OPENAI_KEY): return "⚠️ AI Disabled"
    p = f"Give 3 bullish bullets and 2 risk bullets for {sym} (NSE). LTP:{ltp}, RSI:{rsi}, Trend:{trend}, PE:{pe}."
    t, e = call_ai([{"role": "user", "content": p}], max_tokens=250)
    return t if t else f"AI Error: {e}"

# ── Data & Technicals ─────────────────────────────────────────────────────────
def get_cached(k):
    with _lock:
        d = _cache.get(k)
        return d["val"] if d and time.time() - d["ts"] < CACHE_TTL else None

def set_cached(k, v):
    with _lock: _cache[k] = {"val": v, "ts": time.time()}

def get_hist(sym, period="1y"):
    c = get_cached(f"h_{sym}")
    if c is not None: return c
    try:
        t = yf.Ticker(f"{sym}.NS", session=SESSION)
        df = t.history(period=period, auto_adjust=True)
        if df.empty or len(df) < 5: return pd.DataFrame()
        set_cached(f"h_{sym}", df)
        return df
    except: return pd.DataFrame()

def get_info(sym):
    c = get_cached(f"i_{sym}")
    if c: return c
    try:
        t = yf.Ticker(f"{sym}.NS", session=SESSION)
        i = t.info or {}
        i.update({"mcap": getattr(t.fast_info, "market_cap", None)})
        set_cached(f"i_{sym}", i)
        return i
    except: return {}

def calc_rsi(c):
    """Fixed RSI Calculation to avoid syntax errors."""
    if len(c) < 15: return 50.0
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    # RSI Formula
    rsi_series = 100 - (100 / (1 + rs))
    return round(float(rsi_series.iloc[-1]), 1)

def calc_macd(c): return round(float((c.ewm(12).mean() - c.ewm(26).mean()).iloc[-1]), 2)
def calc_ema(c, span): return round(float(c.ewm(span=span).mean().iloc[-1]), 2)
def calc_atr(df): return round(float(pd.concat([(df.High-df.Low), (df.High-df.Close.shift()), (df.Low-df.Close.shift())], axis=1).max(axis=1).rolling(14).mean().iloc[-1]), 2)

def safe(d, *k, m=1.0):
    for x in k:
        v = d.get(x)
        if v: return round(float(v)*m, 2)
    return None

# ─- Message Builders ─────────────────────────────────────────────────────────
def build_adv(sym):
    sym = sym.upper().replace(".NS", "")
    df, info = get_hist(sym), get_info(sym)
    if df.empty: return f"❌ {sym} not found."
    
    c = df.Close
    ltp = round(float(c.iloc[-1]), 2)
    chg = round((ltp - float(c.iloc[-2])) / float(c.iloc[-2]) * 100, 2) if len(c)>1 else 0
    rsi = calc_rsi(c)
    macd = calc_macd(c)
    e20, e50 = calc_ema(c, 20), calc_ema(c, 50)
    atr = calc_atr(df)
    trend = "BULLISH" if ltp > e20 > e50 else ("BEARISH" if ltp < e20 < e50 else "NEUTRAL")
    
    f = {"name": info.get("longName", sym), "pe": safe(info, "trailingPE"), "roe": safe(info, "returnOnEquity", m=100), "mcap": info.get("mcap")}
    ai_t = ai_insights(sym, ltp, rsi, trend, f["pe"] or "N/A")
    
    return "\n".join([
        f"🏢 <b>{f['name']}</b> ({sym})", f"💰 LTP: ₹{ltp} ({chg}%)",
        f"━━━━━━━━━━━━━━━━━━━━", f"📊 PE: {f['pe']} | ROE: {f['roe']}%",
        f"━━━━━━━━━━━━━━━━━━━━", f"🔬 Trend: {trend} | RSI: {rsi}",
        f"━━━━━━━━━━━━━━━━━━━━", f"🎯 Target: ₹{round(ltp+1.5*atr,2)} | SL: ₹{round(ltp-2*atr,2)}",
        f"━━━━━━━━━━━━━━━━━━━━", f"🤖 AI:\n{ai_t}"
    ])

def build_scan(profile):
    logger.info(f"Starting scan for profile: {profile}")
    p = {
        "conservative": ["HDFCBANK","TCS","INFY","ITC","ONGC"],
        "moderate": ["RELIANCE","BHARTIARTL","AXISBANK","MARUTI"],
        "aggressive": ["TATAMOTORS","ADANIENT","JSWSTEEL","TATAPOWER"]
    }.get(profile, [])
    lines = [f"📊 {profile.upper()} SCAN", "━━━━━━━━━━━━━━━━━━━━"]
    for s in p:
        df = get_hist(s, "1mo")
        if df.empty: lines.append(f"⚪ {s}: No Data"); continue
        c = df.Close
        ltp = round(float(c.iloc[-1]),2)
        chg = round((ltp-float(c.iloc[-2]))/float(c.iloc[-2])*100,2)
        lines.append(f"{'🟢' if chg>=0 else '🔴'} <b>{s}</b>: ₹{ltp} ({chg}%)")
    return "\n".join(lines)

def build_breadth():
    lines = ["📊 MARKET BREADTH", "━━━━━━━━━━━━━━━━━━━━"]
    idx = {"NIFTY":"^NSEI", "BANK NIFTY":"^NSEBANK"}
    adv, dec = 0, 0
    for n,t in idx.items():
        try:
            d = yf.Ticker(t, session=SESSION).history(period="2d")
            if len(d)>=2:
                l,p = round(float(d.Close.iloc[-1]),2), round(float(d.Close.iloc[-2]),2)
                c = round((l-p)/p*100,2)
                lines.append(f"{'🟢' if c>=0 else '🔴'} {n}: {l:,.2f} ({c}%)")
        except: pass
    for s in ["RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK"]:
        df = get_hist(s, "5d")
        if len(df)>=2 and df.Close.iloc[-1] > df.Close.iloc[-2]: adv += 1
        else: dec += 1
    lines.append(f"\n🔢 Adv:{adv} Dec:{dec}")
    return "\n".join(lines)

def build_news():
    if not TAVILY_KEY: return "Set TAVILY_KEY for news."
    try:
        r = requests.post("https://api.tavily.com/search", json={"api_key":TAVILY_KEY, "query":"India stock market", "max_results":3}, timeout=5).json()
        return "\n".join([f"📰 {x['title']}" for x in r.get("results",[])])
    except: return "News fetch error."

# ─- Handlers ────────────────────────────────────────────────────────────────
def kb_main():
    k = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    k.add("🔍 Analysis", "📊 Breadth", "🤖 AI")
    k.add("🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive")
    k.add("🎯 Swing", "📰 News")
    return k

def kb_ai():
    k = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    k.add("📊 Nifty", "💎 Picks", "🔙 Menu")
    return k

# Prompts now explicitly demand current numbers
AI_T = {
    "📊 Nifty": "Give me the exact Nifty 50 level and trend analysis based on the provided live data.", 
    "💎 Picks": "Suggest 2 stocks for swing trading based on current market conditions."
}

@bot.message_handler(commands=["start"])
def cmd_start(m):
    _state[m.chat.id] = None
    bot.send_message(m.chat.id, "👋 Ready! Type symbol or use menu.", reply_markup=kb_main())

@bot.message_handler(func=lambda m: m.text == "🔙 Menu")
def to_main(m): 
    _state[m.chat.id]=None
    bot.send_message(m.chat.id, "Main Menu", reply_markup=kb_main())

@bot.message_handler(func=lambda m: m.text == "🤖 AI")
def to_ai(m): _state[m.chat.id]="ai"; bot.send_message(m.chat.id, "Ask AI (Live Data Enabled):", reply_markup=kb_ai())

@bot.message_handler(func=lambda m: m.text in AI_T)
def ai_btn(m):
    bot.send_message(m.chat.id, "Thinking...")
    # Pass use_context=True to inject live market data
    r,e = call_ai([{"role":"user","content":AI_T[m.text]}], use_context=True)
    bot.send_message(m.chat.id, r or f"Err: {e}", reply_markup=kb_ai())

@bot.message_handler(func=lambda m: m.text in ["🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive"])
def scan_p(m): 
    logger.info(f"Scan button pressed: {m.text}")
    profile = m.text.split()[1].lower()
    bot.send_message(m.chat.id, build_scan(profile), parse_mode="HTML", reply_markup=kb_main())

@bot.message_handler(func=lambda m: m.text == "📊 Breadth")
def scan_b(m): bot.send_message(m.chat.id, build_breadth(), parse_mode="HTML", reply_markup=kb_main())

@bot.message_handler(func=lambda m: m.text == "🎯 Swing")
def scan_s(m):
    lines = ["🎯 SWING (RSI <35)", "━━━━━━━━━━━━━━━━━━━━"]
    for s in ["RELIANCE","TCS","HDFCBANK","TATAMOTORS"]:
        df=get_hist(s,"2mo")
        if df.empty: continue
        r=calc_rsi(df.Close)
        if r<35: lines.append(f"🟢 {s} RSI:{r}")
    if len(lines)==2: lines.append("None found.")
    bot.send_message(m.chat.id, "\n".join(lines), parse_mode="HTML", reply_markup=kb_main())

@bot.message_handler(func=lambda m: m.text == "📰 News")
def news(m): bot.send_message(m.chat.id, build_news(), reply_markup=kb_main())

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_all(m):
    uid = m.chat.id
    txt = m.text.strip()
    
    if _state.get(uid) == "ai":
        # Chat also gets context
        r,e = call_ai([{"role":"user","content":txt}], use_context=True)
        return bot.send_message(uid, r or f"Err: {e}", reply_markup=kb_ai())

    sym = txt.upper().replace(".NS","")
    if 2<=len(sym)<=15 and sym.replace("-","").isalnum():
        logger.info(f"Analyzing symbol: {sym}")
        bot.send_message(uid, f"🔍 Analyzing {sym}...")
        bot.send_message(uid, build_adv(sym), parse_mode="HTML", reply_markup=kb_main())
    else:
        bot.send_message(uid, "⚠️ Type a symbol (e.g. RELIANCE) or use menu.", reply_markup=kb_main())

# ─- Flask & Webhook ─────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def idx(): return jsonify({"status":"ok"})

def proc_upd(js):
    try: bot.process_new_updates([telebot.types.Update.de_json(js)])
    except Exception as e: logger.error(f"Proc err: {e}")

@app.route(WEBHOOK_PATH, methods=["POST"])
def hook():
    if request.headers.get("content-type") == "application/json":
        js = request.get_data().decode("utf-8")
        uid = json.loads(js).get("update_id")
        with _lock:
            if uid in _processed: return "OK", 200
            _processed.add(uid)
            if len(_processed)>200: _processed.discard(min(_processed))
        threading.Thread(target=proc_upd, args=(js,)).start()
    return "OK", 200

if __name__ == "__main__":
    logger.info("Starting server...")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)), debug=False)
