"""
main.py  —  AI Stock Advisory Telegram Bot
Flask webhook server for Render deployment.

Start command : gunicorn main:app --bind 0.0.0.0:$PORT
Environment   : TELEGRAM_TOKEN, GROQ_API_KEY, NEWS_API_KEY, WEBHOOK_URL
"""

import os
import time
import logging
import threading
from datetime import datetime

import requests
import pandas as pd
import yfinance as yf
from flask import Flask, request, jsonify
import telebot
from telebot import types

# ── optional AI ───────────────────────────────────────────────────────────────
try:
    from groq import Groq
    GROQ_CLIENT = Groq(api_key=os.getenv("GROQ_API_KEY", ""))
except Exception:
    GROQ_CLIENT = None

try:
    from yfinance.exceptions import YFRateLimitError
except ImportError:
    class YFRateLimitError(Exception):
        pass

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ── config (from environment — never hard-code secrets) ───────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL", "").rstrip("/")
NEWS_API_KEY   = os.getenv("NEWS_API_KEY", "")
PORT           = int(os.getenv("PORT", 10000))

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN environment variable is not set")

WEBHOOK_PATH = f"/webhook/{TELEGRAM_TOKEN}"

WATCHLIST = {
    "LARGE_CAP": ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC",
                  "ICICIBANK", "SBIN", "BHARTIARTL", "KOTAKBANK", "LT"],
    "MID_CAP":   ["DIXON", "TATAPOWER", "PERSISTENT", "MPHASIS", "COFORGE"],
    "SMALL_CAP": ["MASTEK", "TANLA"],
}

# ── Flask + bot ───────────────────────────────────────────────────────────────
app = Flask(__name__)
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

# ── rate limiter ──────────────────────────────────────────────────────────────
_rate: dict = {}

def is_rate_limited(uid: int, max_calls: int = 5, window: int = 60) -> bool:
    now   = time.time()
    calls = [t for t in _rate.get(uid, []) if now - t < window]
    _rate[uid] = calls
    if len(calls) >= max_calls:
        return True
    _rate[uid].append(now)
    return False

# ── cache ─────────────────────────────────────────────────────────────────────
_cache: dict = {}
CACHE_TTL = 900

def _cache_get(key):
    d = _cache.get(key)
    if not d or time.time() - d["ts"] > CACHE_TTL:
        return None
    return d["val"]

def _cache_set(key, val):
    _cache[key] = {"val": val, "ts": time.time()}

# ── yfinance fetch ────────────────────────────────────────────────────────────
def fetch_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    key    = f"hist_{symbol}_{period}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    ticker = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
    try:
        df = yf.Ticker(ticker).history(period=period, interval="1d")
        if not df.empty and float(df["Close"].iloc[-1]) > 1:
            _cache_set(key, df)
        return df
    except YFRateLimitError:
        logger.warning(f"Rate limited: {ticker}")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"History error {ticker}: {e}")
        return pd.DataFrame()

def fetch_info(symbol: str) -> dict:
    key    = f"info_{symbol}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    ticker = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
    try:
        info = yf.Ticker(ticker).info or {}
        if info:
            _cache_set(key, info)
        return info
    except Exception as e:
        logger.error(f"Info error {ticker}: {e}")
        return {}

# ── indicators ────────────────────────────────────────────────────────────────
def compute_rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, float("nan"))
    return round(float((100 - 100 / (1 + rs)).iloc[-1]), 1)

def compute_macd(close: pd.Series):
    line   = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = line.ewm(span=9, adjust=False).mean()
    return round(float(line.iloc[-1]), 2), round(float(signal.iloc[-1]), 2)

def compute_ema(close: pd.Series, span: int) -> float:
    return round(float(close.ewm(span=span, adjust=False).mean().iloc[-1]), 2)

def compute_bb(close: pd.Series, window: int = 20):
    mid = close.rolling(window).mean()
    std = close.rolling(window).std()
    return (round(float((mid + 2*std).iloc[-1]), 2),
            round(float(mid.iloc[-1]),            2),
            round(float((mid - 2*std).iloc[-1]), 2))

def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return round(float(tr.rolling(period).mean().iloc[-1]), 2)

def compute_pivots(df: pd.DataFrame):
    p  = df.iloc[-2]
    pp = (p["High"] + p["Low"] + p["Close"]) / 3
    return round(pp, 2), round(2*pp - p["Low"], 2), round(2*pp - p["High"], 2)

# ── fundamentals ──────────────────────────────────────────────────────────────
def _safe(info, *keys, mult=1.0):
    for k in keys:
        v = info.get(k)
        if v is not None:
            try:
                f = float(v)
                if f != 0:
                    return round(f * mult, 2)
            except (TypeError, ValueError):
                continue
    return None

def extract_fundamentals(info: dict) -> dict:
    return {
        "company": info.get("longName") or info.get("shortName") or "N/A",
        "sector":  info.get("sector")   or "N/A",
        "pe":      _safe(info, "trailingPE", "forwardPE"),
        "pb":      _safe(info, "priceToBook"),
        "roe":     _safe(info, "returnOnEquity", mult=100),
        "de":      _safe(info, "debtToEquity"),
        "div":     _safe(info, "dividendYield", "trailingAnnualDividendYield", mult=100),
        "eps":     _safe(info, "trailingEps"),
        "mcap":    _safe(info, "marketCap", "enterpriseValue"),
        "high_52w":_safe(info, "fiftyTwoWeekHigh"),
        "low_52w": _safe(info, "fiftyTwoWeekLow"),
        "prev":    _safe(info, "regularMarketPreviousClose", "previousClose"),
    }

def fmt(v, suffix="", decimals=2):
    return f"{v:.{decimals}f}{suffix}" if v is not None else "N/A"

def crore(v):
    if v is None: return "N/A"
    c = v / 1e7
    return f"₹{c/1e5:.2f}L Cr" if c >= 1e5 else f"₹{c:,.0f} Cr"

# ── quality score ─────────────────────────────────────────────────────────────
def quality_score(f: dict, rsi: float, trend: str):
    s = 0
    if f["pe"]  is not None: s += 15 if f["pe"]  < 20 else (10 if f["pe"]  < 30 else 5)
    if f["pb"]  is not None: s += 10 if f["pb"]  < 2  else (5  if f["pb"]  < 4  else 0)
    if f["roe"] is not None: s += 15 if f["roe"] > 20 else (10 if f["roe"] > 12 else 3)
    if f["div"] is not None: s += 10 if f["div"] > 1  else 5
    if f["de"]  is not None: s += 10 if f["de"]  < 1  else (5  if f["de"]  < 2  else 0)
    if 40 < rsi < 60:        s += 15
    elif 30 < rsi < 70:      s += 8
    if trend == "BULLISH":   s += 15
    elif trend == "NEUTRAL": s += 7
    stars   = "★" * (s // 20) + "☆" * (5 - s // 20)
    verdict = ("STRONG BUY" if s >= 75 else "BUY" if s >= 60 else
               "HOLD"       if s >= 45 else "CAUTION" if s >= 30 else "AVOID")
    return s, f"{s}/100 {stars}  {verdict}"

# ── AI ────────────────────────────────────────────────────────────────────────
def ai_insights(symbol, ltp, rsi, macd_line, trend, pe, roe) -> str:
    if not GROQ_CLIENT:
        return "⚠️ AI unavailable — set GROQ_API_KEY"
    prompt = (
        f"3-bullet bullish factors and 2-bullet risks for {symbol} (NSE India). "
        f"LTP ₹{ltp}, RSI {rsi}, MACD {'bullish' if macd_line>0 else 'bearish'}, "
        f"Trend {trend}, PE {pe}, ROE {roe}%. "
        f"Format: BULLISH:\\n• ...\\nRISKS:\\n• ..."
    )
    try:
        resp = GROQ_CLIENT.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "Concise Indian equity analyst."},
                      {"role": "user",   "content": prompt}],
            max_tokens=300, temperature=0.4,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"AI error: {e}")
        return "⚠️ AI analysis unavailable"

def fetch_news(symbol: str) -> str:
    if not NEWS_API_KEY:
        return ""
    try:
        r = requests.get(
            "https://newsapi.org/v2/everything",
            params={"q": f"{symbol} NSE India", "sortBy": "publishedAt",
                    "pageSize": 2, "apiKey": NEWS_API_KEY},
            timeout=5,
        ).json()
        return "\n".join(f"📰 {a['title'][:80]}"
                         for a in r.get("articles", [])[:2] if a.get("title"))
    except Exception:
        return ""

# ── advisory builder ──────────────────────────────────────────────────────────
def build_advisory(symbol: str) -> str:
    symbol = symbol.upper().replace(".NS", "")
    df     = fetch_history(symbol)
    info   = fetch_info(symbol)

    if df.empty or len(df) < 20:
        return f"❌ No data for <b>{symbol}</b>. Check the symbol and try again."

    close              = df["Close"]
    ltp                = round(float(close.iloc[-1]), 2)
    f                  = extract_fundamentals(info)
    rsi_v              = compute_rsi(close)
    macd_line, macd_sig = compute_macd(close)
    ema20              = compute_ema(close, 20)
    ema50              = compute_ema(close, 50)
    ema200             = compute_ema(close, 200)
    bb_u, bb_m, bb_l   = compute_bb(close)
    atr                = compute_atr(df)
    pp, r1, s1         = compute_pivots(df)
    high20             = round(float(close.rolling(20).max().iloc[-1]), 2)
    low20              = round(float(close.rolling(20).min().iloc[-1]), 2)

    trend  = ("BULLISH" if ltp > ema20 > ema50 else
              "BEARISH" if ltp < ema20 < ema50 else "NEUTRAL")

    sl     = round(ltp - 2 * atr, 2)
    tgt_1w = round(ltp + atr * 1.5, 2)
    tgt_1m = round(ltp + atr * 3,   2)
    tgt_3m = round(ltp + atr * 6,   2)
    tgt_6m = round(ltp * 1.10, 2)
    tgt_1y = round(ltp * 1.20, 2)
    tgt_2y = round(ltp * 1.40, 2)

    _, score_str = quality_score(f, rsi_v, trend)

    prev    = f["prev"]
    chg_str = ""
    if prev:
        chg = round(((ltp - prev) / prev) * 100, 2)
        chg_str = f" ({'+' if chg >= 0 else ''}{chg}%)"

    trend_em   = "🟢" if trend == "BULLISH" else ("🔴" if trend == "BEARISH" else "⚪")
    rsi_label  = "🔴 Overbought" if rsi_v > 70 else ("🟢 Oversold" if rsi_v < 30 else "✅ Neutral")
    macd_label = "🟢 Bullish" if macd_line > macd_sig else "🔴 Bearish"

    ai_text   = ai_insights(symbol, ltp, rsi_v, macd_line, trend, fmt(f["pe"]), fmt(f["roe"]))
    news_text = fetch_news(symbol)

    lines = [
        "╔══════════════════════════════════════╗",
        "║   🤖 AI STOCK ANALYSIS               ║",
        "╚══════════════════════════════════════╝",
        f"📅 {datetime.now().strftime('%d-%b-%Y %H:%M')}",
        "",
        f"🏢 <b>{f['company']}</b>",
        f"📊 <b>{symbol}</b> | 🏭 {f['sector']}",
        f"💰 MCap: {crore(f['mcap'])}",
        f"💵 LTP: ₹{ltp}{chg_str}",
        f"📈 52W: ₹{fmt(f['high_52w'])} / ₹{fmt(f['low_52w'])}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "📊 <b>FUNDAMENTALS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• PE: {fmt(f['pe'], 'x')} | PB: {fmt(f['pb'], 'x')}",
        f"• ROE: {fmt(f['roe'], '%')} | D/E: {fmt(f['de'])}",
        f"• Div Yield: {fmt(f['div'], '%')} | EPS: {fmt(f['eps'])}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🔬 <b>TECHNICALS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📈 Trend: {trend_em} {trend}",
        f"• RSI: {rsi_v}  {rsi_label}",
        f"• MACD: {macd_line} vs {macd_sig}  {macd_label}",
        f"• EMA20: {ema20} | EMA50: {ema50} | EMA200: {ema200}",
        f"• BB: U{bb_u} M{bb_m} L{bb_l} | ATR: {atr}",
        f"• Pivot: ₹{pp} | R1: ₹{r1} | S1: ₹{s1}",
        f"• 20D H/L: ₹{high20} / ₹{low20}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🎯 <b>SHORT TERM TARGETS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"1W: ₹{tgt_1w} | 1M: ₹{tgt_1m} | 3M: ₹{tgt_3m}",
        f"🛑 Stop Loss: ₹{sl}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🚀 <b>LONG TERM TARGETS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"6M: ₹{tgt_6m} | 1Y: ₹{tgt_1y} | 2Y: ₹{tgt_2y}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🤖 <b>AI INSIGHTS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        ai_text,
    ]
    if news_text:
        lines += ["", "━━━━━━━━━━━━━━━━━━━━", "📰 <b>LATEST NEWS</b>",
                  "━━━━━━━━━━━━━━━━━━━━", news_text]
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🏆 <b>QUALITY SCORE</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        score_str,
        "",
        "⚠️ Educational only. Not SEBI-registered advice. DYOR.",
    ]
    return "\n".join(lines)

# ── watchlist ─────────────────────────────────────────────────────────────────
def build_watchlist() -> str:
    lines = [f"📋 <b>WATCHLIST</b>  —  {datetime.now().strftime('%d-%b-%Y %H:%M')}\n"]
    for cap, symbols in WATCHLIST.items():
        lines.append(f"<b>{cap}</b>")
        for sym in symbols:
            try:
                df = fetch_history(sym, period="5d")
                if df.empty:
                    lines.append(f"  • {sym}: N/A"); continue
                ltp  = round(float(df["Close"].iloc[-1]), 2)
                prev = round(float(df["Close"].iloc[-2]), 2) if len(df) > 1 else ltp
                chg  = round(((ltp - prev) / prev) * 100, 2)
                rsi_v = compute_rsi(df["Close"])
                sig  = "🟢" if rsi_v < 40 else ("🔴" if rsi_v > 65 else "⚪")
                lines.append(
                    f"  • <b>{sym}</b>: ₹{ltp} ({'+' if chg>=0 else ''}{chg}%)"
                    f"  RSI:{rsi_v} {sig}"
                )
            except Exception as e:
                logger.error(f"Watchlist error {sym}: {e}")
                lines.append(f"  • {sym}: ⚠️ Error")
        lines.append("")
    lines.append("⚠️ Educational only. Not SEBI-registered advice.")
    return "\n".join(lines)

# ── keyboard ──────────────────────────────────────────────────────────────────
def main_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📊 Stock Analysis"),
        types.KeyboardButton("📋 My Watchlist"),
        types.KeyboardButton("🇮🇳 Market"),
        types.KeyboardButton("📈 Swing Trades"),
        types.KeyboardButton("ℹ️ Help"),
    )
    return kb

def send(chat_id, text, parse_mode="HTML", reply_markup=None):
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        bot.send_message(chat_id, chunk, parse_mode=parse_mode, reply_markup=reply_markup)

# ── bot handlers ──────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    send(msg.chat.id,
         f"👋 Welcome <b>{msg.from_user.first_name or 'Investor'}</b>!\n\n"
         "Type any NSE symbol for analysis:\n"
         "<code>RELIANCE</code>  <code>TCS</code>  <code>BEL</code>\n\n"
         "Or use the menu below.",
         reply_markup=main_kb())

@bot.message_handler(commands=["help"])
def cmd_help(msg):
    send(msg.chat.id,
         "📖 <b>HOW TO USE</b>\n\n"
         "• Type any NSE symbol: <code>RELIANCE</code>\n"
         "• 📊 Stock Analysis → enter a symbol\n"
         "• 📋 My Watchlist  → quick overview\n"
         "• 📈 Swing Trades   → scan setups\n\n"
         "⚠️ Educational only. Not SEBI-registered advice.")

@bot.message_handler(func=lambda m: m.text == "📊 Stock Analysis")
def btn_analysis(msg):
    send(msg.chat.id, "Enter an NSE symbol e.g. <code>RELIANCE</code>")

@bot.message_handler(func=lambda m: m.text == "📋 My Watchlist")
def btn_watchlist(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Building watchlist…")
    send(msg.chat.id, build_watchlist())

@bot.message_handler(func=lambda m: m.text == "🇮🇳 Market")
def btn_market(msg):
    try:
        from swing_trades import get_swing_trades
        send(msg.chat.id, "⏳ Fetching market data…")
        send(msg.chat.id, get_swing_trades("conservative"))
    except ImportError:
        send(msg.chat.id, "⚠️ Market module not available.")

@bot.message_handler(func=lambda m: m.text == "📈 Swing Trades")
def btn_swing(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    try:
        from swing_trades import get_swing_trades
        send(msg.chat.id, "⏳ Scanning swing setups…")
        send(msg.chat.id, get_swing_trades("conservative"))
    except ImportError:
        send(msg.chat.id, "⚠️ Swing trades module not available.")

@bot.message_handler(func=lambda m: m.text == "ℹ️ Help")
def btn_help(msg):
    cmd_help(msg)

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(msg):
    text  = msg.text.strip().upper()
    clean = text.replace(" ", "").replace(".NS", "").replace("&", "A")
    if not (2 <= len(clean) <= 15 and clean.isalnum()):
        send(msg.chat.id,
             "❓ Type a valid NSE symbol like <code>RELIANCE</code>",
             reply_markup=main_kb())
        return
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, f"🔍 Analysing <b>{clean}</b>… ⏳")
    try:
        send(msg.chat.id, build_advisory(clean), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Advisory error {clean}: {e}")
        send(msg.chat.id, f"❌ Could not analyse {clean}. Try again.",
             reply_markup=main_kb())

# ── Flask routes ──────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok", "service": "AI Stock Advisory Bot",
                    "time": datetime.utcnow().isoformat() + "Z"})

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if request.content_type != "application/json":
        return "Bad Request", 400
    try:
        bot.process_new_updates(
            [telebot.types.Update.de_json(request.get_data(as_text=True))]
        )
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    return "OK", 200

@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    if not WEBHOOK_URL:
        return jsonify({"error": "WEBHOOK_URL env var not set"}), 400
    url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
    try:
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=url)
        logger.info(f"Webhook set: {url}")
        return jsonify({"status": "ok", "webhook": url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── auto-register webhook on startup ─────────────────────────────────────────
def _auto_register():
    time.sleep(5)
    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL not set — skipping auto webhook registration.")
        return
    try:
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=f"{WEBHOOK_URL}{WEBHOOK_PATH}")
        logger.info("Webhook auto-registered.")
    except Exception as e:
        logger.error(f"Auto webhook failed: {e}")

threading.Thread(target=_auto_register, daemon=True).start()

# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
