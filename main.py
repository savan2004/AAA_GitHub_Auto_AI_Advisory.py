import os
import time
import random
import threading
from collections import defaultdict, deque
from datetime import datetime

import pandas as pd
import telebot
from telebot import types

from groq import Groq
import google.generativeai as genai
from nsetools import Nse
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer

# ========== 1. CONFIG ==========

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

if not TELEGRAM_TOKEN or not GROQ_API_KEY or not GEMINI_API_KEY:
    raise RuntimeError("Set TELEGRAM_TOKEN, GROQ_API_KEY, GEMINI_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")
genai.configure(api_key=GEMINI_API_KEY)
nse = Nse()

# Cache
CACHE = {}
CACHE_TTL_DEFAULT = 900  # 15 min
cache_lock = threading.Lock()

# Per-user quotas
USER_QUOTA = defaultdict(lambda: {"day": "", "month": "", "day_count": 0, "month_count": 0})
MAX_PER_DAY = 10
MAX_PER_MONTH = 30

# VIPs (higher limits)
VIP_USERS = set()  # add your own chat_id here

# yfinance global limiter & breaker
YF_MAX_RETRIES = 4
YF_WINDOW_SEC = 60
YF_MAX_CALLS_PER_WINDOW = 40  # more aggressive but controlled

YF_CALL_TIMES = deque()
YF_BREAKER_COOLDOWN = 300
YF_FAIL_THRESHOLD = 5
YF_STATE = {"breaker_open": False, "breaker_until": 0, "fail_count": 0}

# System load indicator for AI depth
SYSTEM_LOAD = {"score": 0.0}

# Precomputed bulletins
DAILY_BULLETINS = {"morning": "", "midday": "", "close": ""}


# ========== 2. CACHE HELPERS ==========

def cache_get(key, ttl=CACHE_TTL_DEFAULT):
    with cache_lock:
        data = CACHE.get(key)
        if not data:
            return None
        if time.time() - data["ts"] > ttl:
            del CACHE[key]
            return None
        return data["val"]


def cache_set(key, val):
    with cache_lock:
        CACHE[key] = {"val": val, "ts": time.time()}


# ========== 3. QUOTAS ==========

def is_vip(user_id: int) -> bool:
    return user_id in VIP_USERS


def check_and_inc_user_quota(user_id: int):
    today = datetime.utcnow().date()
    day_str = today.isoformat()
    month_str = today.strftime("%Y-%m")

    u = USER_QUOTA[user_id]

    # reset day
    if u["day"] != day_str:
        u["day"] = day_str
        u["day_count"] = 0

    # reset month
    if u["month"] != month_str:
        u["month"] = month_str
        u["month_count"] = 0

    if is_vip(user_id):
        max_day, max_month = 100, 300
    else:
        max_day, max_month = MAX_PER_DAY, MAX_PER_MONTH

    if u["day_count"] >= max_day:
        return False, f"🌙 Daily limit reached ({max_day}/day). Try again tomorrow."
    if u["month_count"] >= max_month:
        return False, f"📅 Monthly limit reached ({max_month}/month)."

    u["day_count"] += 1
    u["month_count"] += 1
    return True, ""


# ========== 4. YFINANCE RESILIENCE ==========

def yf_allow_call() -> bool:
    now = time.time()
    while YF_CALL_TIMES and now - YF_CALL_TIMES[0] > YF_WINDOW_SEC:
        YF_CALL_TIMES.popleft()
    return len(YF_CALL_TIMES) < YF_MAX_CALLS_PER_WINDOW


def yf_register_call():
    YF_CALL_TIMES.append(time.time())


def breaker_allows() -> bool:
    if not YF_STATE["breaker_open"]:
        return True
    return time.time() >= YF_STATE["breaker_until"]


def breaker_success():
    YF_STATE["fail_count"] = 0
    YF_STATE["breaker_open"] = False
    YF_STATE["breaker_until"] = 0


def breaker_failure():
    YF_STATE["fail_count"] += 1
    if YF_STATE["fail_count"] >= YF_FAIL_THRESHOLD:
        YF_STATE["breaker_open"] = True
        YF_STATE["breaker_until"] = time.time() + YF_BREAKER_COOLDOWN


def safe_sleep(base: float, jitter: float = 0.5):
    time.sleep(base + random.uniform(0, jitter))


def nse_history_fallback(ticker: str) -> pd.DataFrame:
    sym = ticker.replace(".NS", "").upper()
    try:
        q = nse.get_quote(sym)
    except Exception:
        return pd.DataFrame()

    if not q:
        return pd.DataFrame()

    ltp = float(q.get("lastPrice", 0.0))
    row = {
        "Close": ltp,
        "Open": float(q.get("open", ltp)),
        "High": float(q.get("dayHigh", ltp)),
        "Low": float(q.get("dayLow", ltp)),
        "Volume": float(q.get("quantityTraded", 0)),
    }
    df = pd.DataFrame([row], index=[pd.Timestamp(datetime.now().date())])
    cache_set(f"fb:{ticker}", df)
    return df


def safe_yf_history(ticker: str, period="6mo", interval="1d") -> pd.DataFrame:
    key = f"yf:{ticker}:{period}:{interval}"
    cached = cache_get(key, ttl=900)
    if cached is not None:
        return cached

    if not breaker_allows() or not yf_allow_call():
        cached = cache_get(key, ttl=86400)
        if cached is not None:
            return cached
        return nse_history_fallback(ticker)

    last_exc = None
    t0 = time.time()
    for attempt in range(YF_MAX_RETRIES):
        try:
            yf_register_call()
            df = yf.Ticker(ticker).history(period=period, interval=interval)
            if not df.empty:
                cache_set(key, df)
                breaker_success()
                update_load(True, time.time() - t0)
                return df
            last_exc = RuntimeError("Empty yfinance df")
        except YFRateLimitError as e:
            last_exc = e
            safe_sleep(2 ** attempt, jitter=1.0)
        except Exception as e:
            last_exc = e
            safe_sleep(1.0, jitter=0.5)

    breaker_failure()
    update_load(False, time.time() - t0)

    cached = cache_get(key, ttl=86400)
    if cached is not None:
        return cached
    return nse_history_fallback(ticker)


# ========== 5. NSE SNAPSHOT HELPERS ==========

def get_stock_quote(symbol: str):
    sym = symbol.upper().strip()
    key = f"q:{sym}"
    cached = cache_get(key, ttl=30)
    if cached:
        return cached
    try:
        data = nse.get_quote(sym)
        cache_set(key, data)
        return data
    except Exception:
        return None


def get_index_quote(name: str):
    key = f"idx:{name}"
    cached = cache_get(key, ttl=30)
    if cached:
        return cached
    try:
        data = nse.get_index_quote(name)
        cache_set(key, data)
        return data
    except Exception:
        return None


def basic_stock_snapshot(symbol: str) -> str:
    q = get_stock_quote(symbol)
    if not q:
        return f"❌ Symbol not found or NSE down: {symbol}"

    sym = q.get("symbol", symbol.upper())
    ltp = q.get("lastPrice", 0.0)
    prev_close = q.get("previousClose", 0.0)
    change = q.get("change", 0.0)
    pchange = q.get("pChange", 0.0)
    day_high = q.get("dayHigh", 0.0)
    day_low = q.get("dayLow", 0.0)

    return (
        f"*{sym} (NSE)*\n"
        f"LTP: ₹{ltp:.2f} | {change:.2f} ({pchange:.2f}%)\n"
        f"Prev: ₹{prev_close:.2f} | Range: ₹{day_low:.2f}–₹{day_high:.2f}\n"
    )


# ========== 6. SYSTEM LOAD & AI ==========

def update_load(success: bool, latency: float):
    if not success or latency > 3.0:
        SYSTEM_LOAD["score"] = min(1.0, SYSTEM_LOAD["score"] + 0.1)
    else:
        SYSTEM_LOAD["score"] = max(0.0, SYSTEM_LOAD["score"] - 0.05)


def smart_ai_call(prompt: str) -> str:
    load = SYSTEM_LOAD["score"]
    if load < 0.3:
        max_tokens = 800
    elif load < 0.7:
        max_tokens = 500
    else:
        prompt = "Give a concise 3–5 line educational answer only.\n" + prompt
        max_tokens = 250

    t0 = time.time()
    try:
        gclient = Groq(api_key=GROQ_API_KEY)
        resp = gclient.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        txt = resp.choices[0].message.content
        update_load(True, time.time() - t0)
        return txt
    except Exception:
        pass

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = model.generate_content(prompt)
        txt = resp.text
        update_load(True, time.time() - t0)
        return txt
    except Exception:
        update_load(False, time.time() - t0)
        return "AI is temporarily unavailable. Please try again later."


# ========== 7. ANALYSIS LOGIC ==========

def get_price_series_for_analysis(symbol: str) -> pd.Series:
    sym = symbol.upper().strip()
    ticker = f"{sym}.NS"
    df = safe_yf_history(ticker, period="6mo", interval="1d")
    if not df.empty and len(df) >= 30:
        return df["Close"]

    q = get_stock_quote(sym)
    if q:
        ltp = float(q.get("lastPrice", 0.0))
        idx = pd.date_range(end=datetime.today(), periods=60, freq="B")
        return pd.Series([ltp] * len(idx), index=idx, name="Close")

    return pd.Series(dtype=float)


def deep_stock_analysis(symbol: str, heavy: bool = True) -> str:
    sym = symbol.upper().strip()
    snap = basic_stock_snapshot(sym)

    if not heavy:
        prompt = f"""
User requested a quick view on {sym} (NSE).

Using only latest quote info:
{snap}

Give 3–5 educational points (no targets, no advice) about how to think about
this kind of stock (business, risk, valuation).
"""
        analysis = smart_ai_call(prompt)
        return snap + "\n" + analysis

    series = get_price_series_for_analysis(sym)
    if series.empty or len(series) < 5:
        prompt = f"""
We only have limited data for {sym}.
User snapshot:
{snap}

Explain what an investor should typically check (earnings trend, debt, cash flow,
industry position) without giving direct buy/sell advice.
"""
        analysis = smart_ai_call(prompt)
        return snap + "\n" + analysis

    ltp = series.iloc[-1]
    ema200 = series.ewm(span=200, min_periods=50).mean().iloc[-1]
    trend = "Bullish" if ltp > ema200 else "Bearish"

    prompt = f"""
You are an Indian equity analyst.

Stock: {sym} on NSE.
Approx last price: {ltp:.2f}
Approx trend vs 200‑day EMA: {trend}

1. Short‑term view (1–4 weeks).
2. Medium‑term view (3–6 months).
3. BUY / HOLD / AVOID style educational view (no direct advice).
4. 3 key risks.
Use simple language.
"""
    analysis = smart_ai_call(prompt)
    return snap + f"Trend (approx): {trend}\n\n" + analysis


def market_overview_bulletin() -> str:
    n50 = get_index_quote("NIFTY 50")
    nbk = get_index_quote("NIFTY BANK")
    if not n50 or not nbk:
        return "❌ Could not fetch market indices."

    def fmt_index(d):
        name = d.get("indexSymbol", d.get("index", "Index"))
        last = d.get("last", d.get("lastPrice", 0.0))
        change = d.get("variation", d.get("change", 0.0))
        pchange = d.get("percentChange", d.get("pChange", 0.0))
        return name, float(last), float(change), float(pchange)

    n_name, n_last, n_chg, n_pchg = fmt_index(n50)
    b_name, b_last, b_chg, b_pchg = fmt_index(nbk)

    base = (
        f"📈 *Market Overview*\n"
        f"{n_name}: {n_last:.2f} ({n_chg:.2f}, {n_pchg:.2f}%)\n"
        f"{b_name}: {b_last:.2f} ({b_chg:.2f}, {b_pchg:.2f}%)\n"
    )

    prompt = f"""
Nifty 50: {n_last} ({n_chg}, {n_pchg}%)
Bank Nifty: {b_last} ({b_chg}, {b_pchg}%)

Give a short market commentary (1–2 paragraphs) for Indian equity traders.
Mention trend, sentiment, sectors in focus, and caution points.
Educational only, no trading calls.
"""
    view = smart_ai_call(prompt)
    return base + "\n" + view


# ========== 8. BACKGROUND JOBS ==========

POPULAR_SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "ITC"]

def background_refresh():
    while True:
        try:
            for sym in POPULAR_SYMBOLS:
                _ = safe_yf_history(f"{sym}.NS", period="6mo", interval="1d")
            time.sleep(180)
        except Exception as e:
            print("Background refresh error:", e)
            time.sleep(60)


def bulletin_refresher():
    while True:
        try:
            text = market_overview_bulletin()
            hr = datetime.now().hour
            if hr < 11:
                DAILY_BULLETINS["morning"] = text
            elif hr < 15:
                DAILY_BULLETINS["midday"] = text
            else:
                DAILY_BULLETINS["close"] = text
            time.sleep(900)
        except Exception as e:
            print("Bulletin refresh error:", e)
            time.sleep(120)


# ========== 9. OPTION TEXT ==========

def option_strategies_text() -> str:
    return (
        "🛡️ *OPTION STRATEGIES (EDUCATIONAL)*\n"
        "- Bull Call Spread: Mildly bullish, limited risk & reward.\n"
        "- Bear Put Spread: Mildly bearish, limited risk.\n"
        "- Iron Condor: Range‑bound view, time decay friendly.\n"
        "- Long Straddle: Big move expected, any direction.\n\n"
        "Always manage position size and risk. This is NOT investment advice."
    )


# ========== 10. TELEGRAM HANDLERS ==========

@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📈 Market View"),
        types.KeyboardButton("⚡ Quick View"),
        types.KeyboardButton("🔍 Deep Analysis"),
    )
    kb.add(
        types.KeyboardButton("🛡️ Option Ideas"),
        types.KeyboardButton("ℹ️ How to Use"),
    )
    bot.send_message(
        m.chat.id,
        "🤖 *High‑Traffic Indian AI Stock Bot*\n\n"
        "Quick View: light, almost unlimited.\n"
        "Deep Analysis: limited to 10/day, 30/month per user.\n"
        "All content is educational.\n",
        reply_markup=kb,
    )


@bot.message_handler(func=lambda m: m.text == "📈 Market View")
def handle_market(m):
    hr = datetime.now().hour
    if hr < 11:
        txt = DAILY_BULLETINS["morning"] or market_overview_bulletin()
    elif hr < 15:
        txt = DAILY_BULLETINS["midday"] or market_overview_bulletin()
    else:
        txt = DAILY_BULLETINS["close"] or market_overview_bulletin()
    bot.send_chat_action(m.chat.id, "typing")
    bot.reply_to(m, txt)


@bot.message_handler(func=lambda m: m.text == "🛡️ Option Ideas")
def handle_options(m):
    bot.reply_to(m, option_strategies_text())


@bot.message_handler(func=lambda m: m.text == "ℹ️ How to Use")
def handle_help(m):
    bot.reply_to(
        m,
        "Use:\n"
        "- ⚡ Quick View: send symbol or tap button → light, fast overview.\n"
        "- 🔍 Deep Analysis: uses history & AI, limited per user.\n"
        "Examples: RELIANCE, TCS, HDFCBANK.\n"
        "All info is educational only.",
    )


@bot.message_handler(func=lambda m: m.text == "⚡ Quick View")
def ask_quick(m):
    msg = bot.reply_to(m, "Send NSE stock symbol for quick view (e.g. RELIANCE):")
    bot.register_next_step_handler(msg, quick_view_handler)


def quick_view_handler(m):
    sym = (m.text or "").strip().upper()
    if not sym:
        bot.reply_to(m, "Empty symbol. Try again.")
        return
    bot.send_chat_action(m.chat.id, "typing")
    txt = deep_stock_analysis(sym, heavy=False)
    bot.reply_to(m, txt)


@bot.message_handler(func=lambda m: m.text == "🔍 Deep Analysis")
def ask_deep(m):
    allowed, msg = check_and_inc_user_quota(m.from_user.id)
    if not allowed:
        bot.reply_to(m, msg)
        return
    msg2 = bot.reply_to(m, "Send NSE stock symbol for deep analysis (e.g. RELIANCE):")
    bot.register_next_step_handler(msg2, deep_view_handler)


def deep_view_handler(m):
    sym = (m.text or "").strip().upper()
    if not sym:
        bot.reply_to(m, "Empty symbol. Try again.")
        return

    allowed, msg = check_and_inc_user_quota(m.from_user.id)
    if not allowed:
        bot.reply_to(m, msg)
        return

    bot.send_chat_action(m.chat.id, "typing")
    txt = deep_stock_analysis(sym, heavy=True)
    bot.reply_to(m, txt)


@bot.message_handler(func=lambda m: True)
def fallback_symbol(m):
    sym = (m.text or "").strip().upper()
    if not sym.isalnum() or not (2 <= len(sym) <= 10):
        bot.reply_to(m, "I did not understand.\nSend symbol or use /start.")
        return
    # default: quick view (cheap)
    bot.send_chat_action(m.chat.id, "typing")
    txt = deep_stock_analysis(sym, heavy=False)
    bot.reply_to(m, txt)


# ========== 11. HEALTH SERVER ==========

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


# ========== 12. MAIN ==========

if __name__ == "__main__":
    print("🤖 High‑Traffic Indian AI Stock Bot starting...")
    threading.Thread(target=run_health_server, daemon=True).start()
    threading.Thread(target=background_refresh, daemon=True).start()
    threading.Thread(target=bulletin_refresher, daemon=True).start()
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(10)
