"""
main.py  —  AI Stock Advisory Telegram Bot
Flask webhook server for Render deployment.

Start command : gunicorn main:app --bind 0.0.0.0:$PORT
Environment variables (set in Render dashboard):
  TELEGRAM_TOKEN     — bot token from BotFather
  WEBHOOK_URL        — your Render URL e.g. https://xxx.onrender.com
  GROQ_API_KEY       — primary AI (Llama 3.3 70B, fastest)
  GEMINI_API_KEY     — fallback AI (Gemini 2.0 Flash)
  OPENAI_KEY         — fallback AI (GPT-4o-mini)
  ALPHA_VANTAGE_KEY  — fallback price/fundamental data
  FINNHUB_API_KEY    — fallback price data
  TAVILY_API_KEY     — AI-powered news search
  PORT               — default 10000
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

# ── yfinance rate-limit exception ─────────────────────────────────────────────
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

# ── config — all values from environment, never hard-coded ───────────────────
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
WEBHOOK_URL       = os.getenv("WEBHOOK_URL", "").rstrip("/")
PORT              = int(os.getenv("PORT", 10000))

GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY    = os.getenv("OPENAI_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
FINNHUB_API_KEY   = os.getenv("FINNHUB_API_KEY", "")
TAVILY_API_KEY    = os.getenv("TAVILY_API_KEY", "")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN environment variable is not set")

WEBHOOK_PATH = f"/webhook/{TELEGRAM_TOKEN}"

# ── AI clients (initialised once, used in fallback order) ────────────────────
GROQ_CLIENT = None
try:
    if GROQ_API_KEY:
        from groq import Groq
        GROQ_CLIENT = Groq(api_key=GROQ_API_KEY)
        logger.info("AI: GROQ client ready")
except Exception as e:
    logger.warning(f"GROQ init failed: {e}")

GEMINI_MODEL = None
try:
    if GEMINI_API_KEY:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_MODEL = genai.GenerativeModel("gemini-2.0-flash")
        logger.info("AI: Gemini client ready")
except Exception as e:
    logger.warning(f"Gemini init failed: {e}")

OPENAI_CLIENT = None
try:
    if OPENAI_API_KEY:
        from openai import OpenAI
        OPENAI_CLIENT = OpenAI(api_key=OPENAI_API_KEY)
        logger.info("AI: OpenAI client ready")
except Exception as e:
    logger.warning(f"OpenAI init failed: {e}")

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

# ── user state (tracks what input the bot is waiting for per user) ────────────
# Values: "awaiting_advisory_symbol" | "awaiting_analysis_symbol" | None
_user_state: dict = {}

def set_state(uid: int, state):
    if state is None:
        _user_state.pop(uid, None)
    else:
        _user_state[uid] = state

def get_state(uid: int):
    return _user_state.get(uid)

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

# ── AI with full fallback chain: GROQ → Gemini → OpenAI ──────────────────────
def ai_insights(symbol, ltp, rsi, macd_line, trend, pe, roe) -> str:
    prompt = (
        f"3-bullet bullish factors and 2-bullet risks for {symbol} (NSE India). "
        f"LTP ₹{ltp}, RSI {rsi}, MACD {'bullish' if macd_line>0 else 'bearish'}, "
        f"Trend {trend}, PE {pe}, ROE {roe}%. "
        f"Format exactly:\nBULLISH:\n• ...\n• ...\n• ...\nRISKS:\n• ...\n• ..."
    )
    system = "You are a concise Indian equity analyst."

    # 1. GROQ (fastest — Llama 3.3 70B)
    if GROQ_CLIENT:
        try:
            resp = GROQ_CLIENT.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "system", "content": system},
                          {"role": "user",   "content": prompt}],
                max_tokens=300, temperature=0.4,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                logger.info(f"AI: GROQ used for {symbol}")
                return text
        except Exception as e:
            logger.warning(f"GROQ failed for {symbol}: {e}")

    # 2. Gemini (fallback)
    if GEMINI_MODEL:
        try:
            resp = GEMINI_MODEL.generate_content(f"{system}\n\n{prompt}")
            text = (getattr(resp, "text", "") or "").strip()
            if text:
                logger.info(f"AI: Gemini used for {symbol}")
                return text
        except Exception as e:
            logger.warning(f"Gemini failed for {symbol}: {e}")

    # 3. OpenAI (last resort)
    if OPENAI_CLIENT:
        try:
            resp = OPENAI_CLIENT.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": system},
                          {"role": "user",   "content": prompt}],
                max_tokens=300, temperature=0.4,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                logger.info(f"AI: OpenAI used for {symbol}")
                return text
        except Exception as e:
            logger.warning(f"OpenAI failed for {symbol}: {e}")

    return "⚠️ AI analysis unavailable — all providers failed or keys not set"


# ── news: Tavily (AI search) → Alpha Vantage news ────────────────────────────
def fetch_news(symbol: str) -> str:
    # 1. Tavily — AI-powered, highest quality
    if TAVILY_API_KEY:
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY,
                      "query": f"{symbol} NSE India stock news",
                      "max_results": 3,
                      "search_depth": "basic"},
                timeout=6,
            ).json()
            results = resp.get("results", [])
            if results:
                lines = [f"📰 {r['title'][:80]}" for r in results[:2] if r.get("title")]
                if lines:
                    logger.info(f"News: Tavily used for {symbol}")
                    return "\n".join(lines)
        except Exception as e:
            logger.warning(f"Tavily news failed: {e}")

    # 2. Alpha Vantage news sentiment
    if ALPHA_VANTAGE_KEY:
        try:
            resp = requests.get(
                "https://www.alphavantage.co/query",
                params={"function": "NEWS_SENTIMENT", "tickers": f"NSE:{symbol}",
                        "limit": 3, "apikey": ALPHA_VANTAGE_KEY},
                timeout=6,
            ).json()
            feed = resp.get("feed", [])
            if feed:
                lines = [f"📰 {a['title'][:80]}" for a in feed[:2] if a.get("title")]
                if lines:
                    logger.info(f"News: Alpha Vantage used for {symbol}")
                    return "\n".join(lines)
        except Exception as e:
            logger.warning(f"Alpha Vantage news failed: {e}")

    return ""


# ── price fallback: yfinance → Finnhub → Alpha Vantage ───────────────────────
def fetch_ltp_fallback(symbol: str) -> float | None:
    """Try alternative sources when yfinance returns empty."""

    # Finnhub
    if FINNHUB_API_KEY:
        try:
            resp = requests.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": f"NSE:{symbol}", "token": FINNHUB_API_KEY},
                timeout=5,
            ).json()
            price = float(resp.get("c", 0))
            if price > 0:
                logger.info(f"Price: Finnhub used for {symbol}")
                return round(price, 2)
        except Exception as e:
            logger.warning(f"Finnhub price failed {symbol}: {e}")

    # Alpha Vantage
    if ALPHA_VANTAGE_KEY:
        try:
            resp = requests.get(
                "https://www.alphavantage.co/query",
                params={"function": "GLOBAL_QUOTE",
                        "symbol": f"NSE:{symbol}",
                        "apikey": ALPHA_VANTAGE_KEY},
                timeout=6,
            ).json()
            price = float(resp.get("Global Quote", {}).get("05. price", 0))
            if price > 0:
                logger.info(f"Price: Alpha Vantage used for {symbol}")
                return round(price, 2)
        except Exception as e:
            logger.warning(f"Alpha Vantage price failed {symbol}: {e}")

    return None

# ── advisory builder ──────────────────────────────────────────────────────────
def build_advisory(symbol: str) -> str:
    symbol = symbol.upper().replace(".NS", "")
    df     = fetch_history(symbol)
    info   = fetch_info(symbol)

    if df.empty or len(df) < 20:
        # Try alternative price sources before giving up
        fallback_price = fetch_ltp_fallback(symbol)
        if fallback_price:
            return (
                f"⚠️ <b>{symbol}</b> — limited historical data available.\n"
                f"💵 LTP: ₹{fallback_price} (via Finnhub/AlphaVantage)\n\n"
                f"Full technical analysis requires at least 20 days of history.\n"
                f"Try again later when yfinance data is available."
            )
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


# ── deep AI advisory ──────────────────────────────────────────────────────────
def build_ai_advisory(symbol: str) -> str:
    """
    Full AI-written investment advisory using GROQ -> Gemini -> OpenAI fallback.
    Provides a structured 7-section report unlike the quick ai_insights snippet.
    """
    symbol = symbol.upper().replace(".NS", "")
    df     = fetch_history(symbol)
    info   = fetch_info(symbol)
    price_line = None

    if df.empty or len(df) < 20:
        fallback = fetch_ltp_fallback(symbol)
        if not fallback:
            return f"\u274c No data found for <b>{symbol}</b>. Check the symbol and try again."
        f = extract_fundamentals(info)
        prompt = (
            f"Write a professional investment advisory for {symbol} (NSE India).\n"
            f"Available data: PE={fmt(f['pe'])}, PB={fmt(f['pb'])}, "
            f"ROE={fmt(f['roe'],'%')}, Div={fmt(f['div'],'%')}, "
            f"MCap={crore(f['mcap'])}, Sector={f['sector']}.\n"
            f"Technical data unavailable. Focus on fundamentals only.\n"
            f"Include: verdict (Buy/Hold/Sell), key strengths, key risks, "
            f"suitable investor profile. Under 250 words."
        )
        price_line = f"LTP \u20b9{fallback} (via fallback — limited technical data)"
    else:
        close               = df["Close"]
        ltp                 = round(float(close.iloc[-1]), 2)
        f                   = extract_fundamentals(info)
        rsi_v               = compute_rsi(close)
        macd_line, macd_sig = compute_macd(close)
        ema20               = compute_ema(close, 20)
        ema50               = compute_ema(close, 50)
        ema200              = compute_ema(close, 200)
        atr                 = compute_atr(df)
        trend               = ("BULLISH" if ltp > ema20 > ema50 else
                               "BEARISH" if ltp < ema20 < ema50 else "NEUTRAL")
        _, score_str        = quality_score(f, rsi_v, trend)
        tgt_1m = round(ltp + atr * 3,   2)
        tgt_3m = round(ltp + atr * 6,   2)
        tgt_1y = round(ltp * 1.20, 2)
        sl     = round(ltp - 2 * atr, 2)
        news_ctx = fetch_news(symbol)

        prompt = (
            f"Write a professional detailed investment advisory for "
            f"{symbol} ({f['company']}, NSE India, {f['sector']}).\n\n"
            f"MARKET DATA:\n"
            f"\u2022 LTP: \u20b9{ltp} | Trend: {trend} | RSI: {rsi_v}\n"
            f"\u2022 MACD: {macd_line} vs Signal {macd_sig}\n"
            f"\u2022 EMA20: {ema20} | EMA50: {ema50} | EMA200: {ema200} | ATR: {atr}\n\n"
            f"FUNDAMENTALS:\n"
            f"\u2022 PE: {fmt(f['pe'])} | PB: {fmt(f['pb'])} | ROE: {fmt(f['roe'],'%')}\n"
            f"\u2022 D/E: {fmt(f['de'])} | Div: {fmt(f['div'],'%')} | EPS: {fmt(f['eps'])}\n"
            f"\u2022 MCap: {crore(f['mcap'])}\n\n"
            f"TARGETS: 1M \u20b9{tgt_1m} | 3M \u20b9{tgt_3m} | 1Y \u20b9{tgt_1y} | SL \u20b9{sl}\n"
            f"QUALITY SCORE: {score_str}\n"
            + (f"\nNEWS:\n{news_ctx}\n" if news_ctx else "") +
            f"\nWrite using exactly these 7 sections (use the bold headers):\n"
            f"<b>1. EXECUTIVE SUMMARY</b> — 2-3 sentences, verdict: Strong Buy/Buy/Hold/Sell/Avoid\n"
            f"<b>2. BUSINESS OVERVIEW</b> — company & sector context\n"
            f"<b>3. FUNDAMENTAL ANALYSIS</b> — evaluate PE, ROE, debt vs sector norms\n"
            f"<b>4. TECHNICAL OUTLOOK</b> — RSI, MACD, trend, key support/resistance\n"
            f"<b>5. INVESTMENT RECOMMENDATION</b> — entry zone, targets, stop loss, time horizon\n"
            f"<b>6. KEY RISKS</b> — 3 specific risks for this stock\n"
            f"<b>7. SUITABLE FOR</b> — investor profile (conservative/moderate/aggressive)\n\n"
            f"Max 400 words. Professional tone. Use \u20b9 for prices."
        )

    ai_text = _call_ai_advisory(prompt)

    header = (
        "\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557\n"
        "\u2551   \U0001f916 AI INVESTMENT ADVISORY          \u2551\n"
        "\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d\n"
        f"\U0001f4c5 {datetime.now().strftime('%d-%b-%Y %H:%M')}\n"
        f"\U0001f4ca <b>{symbol}</b>\n"
    )
    if price_line:
        header += f"\u26a0\ufe0f {price_line}\n"
    header += "\n"

    footer = (
        "\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\u26a0\ufe0f AI-generated educational content only.\n"
        "Not SEBI-registered advice. DYOR."
    )
    return header + ai_text + footer


def _call_ai_advisory(prompt: str) -> str:
    """Call AI providers in order: GROQ -> Gemini -> OpenAI."""
    system = (
        "You are a senior Indian equity research analyst at a top brokerage. "
        "Write clear, professional, data-driven investment advisories in HTML-friendly format."
    )
    if GROQ_CLIENT:
        try:
            resp = GROQ_CLIENT.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "system", "content": system},
                          {"role": "user",   "content": prompt}],
                max_tokens=600, temperature=0.3,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                logger.info("AI Advisory: GROQ")
                return text + "\n\n"
        except Exception as e:
            logger.warning(f"GROQ advisory failed: {e}")

    if GEMINI_MODEL:
        try:
            resp = GEMINI_MODEL.generate_content(f"{system}\n\n{prompt}")
            text = (getattr(resp, "text", "") or "").strip()
            if text:
                logger.info("AI Advisory: Gemini")
                return text + "\n\n"
        except Exception as e:
            logger.warning(f"Gemini advisory failed: {e}")

    if OPENAI_CLIENT:
        try:
            resp = OPENAI_CLIENT.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": system},
                          {"role": "user",   "content": prompt}],
                max_tokens=600, temperature=0.3,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                logger.info("AI Advisory: OpenAI")
                return text + "\n\n"
        except Exception as e:
            logger.warning(f"OpenAI advisory failed: {e}")

    return "\u26a0\ufe0f All AI providers unavailable. Check your API keys.\n\n"

# ── portfolios ───────────────────────────────────────────────────────────────
PORTFOLIOS = {
    "conservative": {
        "label": "🏦 Conservative",
        "desc":  "Low-risk, dividend-focused large-cap blue chips",
        "stocks": ["HDFCBANK", "TCS", "INFY", "ITC", "ONGC",
                   "POWERGRID", "COALINDIA", "SBIN", "WIPRO", "LT"],
    },
    "moderate": {
        "label": "⚖️ Moderate",
        "desc":  "Balanced growth + stability, large & mid cap mix",
        "stocks": ["RELIANCE", "BHARTIARTL", "AXISBANK", "MARUTI", "TITAN",
                   "BAJFINANCE", "HCLTECH", "KOTAKBANK", "SUNPHARMA", "NTPC"],
    },
    "aggressive": {
        "label": "🚀 Aggressive",
        "desc":  "High-growth momentum, mid & small cap",
        "stocks": ["TATAMOTORS", "ADANIENT", "JSWSTEEL", "TATAPOWER", "DIXON",
                   "PERSISTENT", "COFORGE", "BEL", "IRFC", "ZOMATO"],
    },
}

# ── user history & usage ──────────────────────────────────────────────────────
from collections import deque
_user_history: dict = {}
_usage_stats:  dict = {}

def record_history(uid: int, symbol: str):
    if uid not in _user_history:
        _user_history[uid] = deque(maxlen=5)
    if symbol not in list(_user_history[uid]):
        _user_history[uid].appendleft(symbol)

def get_history(uid: int) -> list:
    return list(_user_history.get(uid, []))

def record_usage(uid: int):
    if uid not in _usage_stats:
        _usage_stats[uid] = {"queries": 0,
                              "first_seen": datetime.now().strftime("%d-%b-%Y")}
    _usage_stats[uid]["queries"] += 1

def build_usage(uid: int) -> str:
    s = _usage_stats.get(uid, {"queries": 0, "first_seen": "Today"})
    h = get_history(uid)
    lines = [
        "📋 <b>YOUR USAGE STATS</b>",
        f"📅 Member since: {s['first_seen']}",
        f"🔍 Total queries: {s['queries']}",
        "",
        "🕐 <b>Recent Symbols:</b>",
    ]
    lines += [f"  {i+1}. {sym}" for i, sym in enumerate(h)] or ["  None yet."]
    lines += ["", "⚠️ Stats reset on server restart (free tier)."]
    return "\n".join(lines)

# ── portfolio scanner ─────────────────────────────────────────────────────────
def build_portfolio(profile: str) -> str:
    p     = PORTFOLIOS[profile]
    lines = [
        f"{p['label']} <b>PORTFOLIO</b>",
        f"📅 {datetime.now().strftime('%d-%b-%Y %H:%M')}",
        f"📝 {p['desc']}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    total_score = 0
    count = 0
    for sym in p["stocks"]:
        try:
            df   = fetch_history(sym, period="5d")
            info = fetch_info(sym)
            if df.empty:
                lines.append(f"  • <b>{sym}</b>: ⚠️ No data"); continue
            close  = df["Close"]
            ltp    = round(float(close.iloc[-1]), 2)
            prev   = round(float(close.iloc[-2]), 2) if len(df) > 1 else ltp
            chg    = round(((ltp - prev) / prev) * 100, 2)
            rsi_v  = compute_rsi(close) if len(close) >= 14 else 50.0
            f_data = extract_fundamentals(info)
            trend  = ("BULLISH" if len(close) >= 3 and
                      float(close.iloc[-1]) > float(close.iloc[-3]) else "NEUTRAL")
            _, score_str = quality_score(f_data, rsi_v, trend)
            score_num    = int(score_str.split("/")[0])
            total_score += score_num
            count        += 1
            chg_em = "🟢" if chg >= 0 else "🔴"
            rsi_em = "🟢" if rsi_v < 40 else ("🔴" if rsi_v > 65 else "⚪")
            lines.append(
                f"  {chg_em} <b>{sym}</b>: \u20b9{ltp} "
                f"({'+' if chg>=0 else ''}{chg}%)"
                f"  RSI:{rsi_v}{rsi_em}  Score:{score_num}/100"
            )
        except Exception as e:
            logger.error(f"Portfolio error {sym}: {e}")
            lines.append(f"  • <b>{sym}</b>: ⚠️ Error")
    avg = round(total_score / count, 1) if count else 0
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        f"📊 Avg Score: {avg}/100  |  {count}/{len(p['stocks'])} stocks loaded",
        "",
        "⚠️ Educational only. Not SEBI-registered advice.",
    ]
    return "\n".join(lines)

# ── market breadth ────────────────────────────────────────────────────────────
NIFTY_INDICES = {
    "NIFTY 50":     "^NSEI",
    "BANK NIFTY":   "^NSEBANK",
    "NIFTY IT":     "^CNXIT",
    "NIFTY AUTO":   "^CNXAUTO",
    "NIFTY PHARMA": "^CNXPHARMA",
    "NIFTY FMCG":   "^CNXFMCG",
}
BREADTH_STOCKS = [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK",
    "ITC","SBIN","BHARTIARTL","KOTAKBANK","LT",
    "WIPRO","HCLTECH","MARUTI","TATAMOTORS","TITAN",
    "SUNPHARMA","ONGC","NTPC","BAJFINANCE","AXISBANK",
]

def build_market_breadth() -> str:
    lines = [
        "📊 <b>MARKET BREADTH</b>",
        f"📅 {datetime.now().strftime('%d-%b-%Y %H:%M')}",
        "━━━━━━━━━━━━━━━━━━━━",
        "📈 <b>INDICES</b>",
    ]
    for name, ticker in NIFTY_INDICES.items():
        try:
            df = yf.Ticker(ticker).history(period="2d", interval="1d")
            if df.empty or len(df) < 2:
                lines.append(f"  • {name}: N/A"); continue
            ltp  = round(float(df["Close"].iloc[-1]), 2)
            prev = round(float(df["Close"].iloc[-2]), 2)
            chg  = round(((ltp - prev) / prev) * 100, 2)
            em   = "🟢" if chg >= 0 else "🔴"
            lines.append(f"  {em} <b>{name}</b>: {ltp:,.2f} ({'+' if chg>=0 else ''}{chg}%)")
        except Exception as e:
            logger.warning(f"Index {ticker}: {e}")
            lines.append(f"  • {name}: N/A")

    adv = dec = unch = 0
    overbought, oversold = [], []
    for sym in BREADTH_STOCKS:
        try:
            df = fetch_history(sym, period="5d")
            if df.empty or len(df) < 2: unch += 1; continue
            close = df["Close"]
            chg   = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
            rsi_v = compute_rsi(close) if len(close) >= 14 else 50.0
            if chg > 0.1: adv += 1
            elif chg < -0.1: dec += 1
            else: unch += 1
            if rsi_v > 70: overbought.append(f"{sym}({rsi_v})")
            if rsi_v < 30: oversold.append(f"{sym}({rsi_v})")
        except Exception: unch += 1

    total = adv + dec + unch
    ratio = round(adv / dec, 2) if dec > 0 else float(adv)
    mood  = ("🟢 BULLISH" if adv > dec * 1.5 else
             "🔴 BEARISH" if dec > adv * 1.5 else "⚪ NEUTRAL")
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔢 <b>BREADTH ({total} stocks)</b>",
        f"  🟢 Adv: {adv}  🔴 Dec: {dec}  ⚪ Unch: {unch}",
        f"  A/D Ratio: {ratio}  |  Mood: {mood}",
    ]
    if overbought: lines.append(f"  🔴 Overbought: {', '.join(overbought[:5])}")
    if oversold:   lines.append(f"  🟢 Oversold:   {', '.join(oversold[:5])}")
    lines += ["", "⚠️ Educational only. Not SEBI-registered advice."]
    return "\n".join(lines)

# ── market news ───────────────────────────────────────────────────────────────
def build_market_news() -> str:
    headlines = []
    if TAVILY_API_KEY:
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY,
                      "query": "Indian stock market NSE Nifty news today",
                      "max_results": 5, "search_depth": "basic"},
                timeout=8,
            ).json()
            headlines = [f"📰 {r['title'][:90]}" for r in resp.get("results",[])[:5] if r.get("title")]
        except Exception as e:
            logger.warning(f"Tavily news: {e}")
    if not headlines and ALPHA_VANTAGE_KEY:
        try:
            resp = requests.get(
                "https://www.alphavantage.co/query",
                params={"function":"NEWS_SENTIMENT","topics":"financial_markets",
                        "limit":5,"apikey":ALPHA_VANTAGE_KEY},
                timeout=8,
            ).json()
            headlines = [f"📰 {a['title'][:90]}" for a in resp.get("feed",[])[:5] if a.get("title")]
        except Exception as e:
            logger.warning(f"AV news: {e}")
    if not headlines:
        return ("📰 <b>MARKET NEWS</b>\n\n"
                "⚠️ No news available. Set TAVILY_API_KEY or ALPHA_VANTAGE_KEY.")
    lines = ["📰 <b>MARKET NEWS</b>",
             f"📅 {datetime.now().strftime('%d-%b-%Y %H:%M')}",
             "━━━━━━━━━━━━━━━━━━━━", ""]
    lines.extend(headlines)
    lines += ["", "⚠️ Educational only. Not SEBI-registered advice."]
    return "\n".join(lines)

# ── keyboard ──────────────────────────────────────────────────────────────────
def main_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("🔍 Stock Analysis"),
        types.KeyboardButton("📊 Market Breadth"),
        types.KeyboardButton("🏦 Conservative"),
        types.KeyboardButton("⚖️ Moderate"),
        types.KeyboardButton("🚀 Aggressive"),
        types.KeyboardButton("📈 Swing (Conservative)"),
        types.KeyboardButton("📉 Swing (Aggressive)"),
        types.KeyboardButton("📰 Market News"),
        types.KeyboardButton("🕐 History"),
        types.KeyboardButton("📋 Usage"),
    )
    return kb

MENU_LABELS = {
    "🔍 STOCK ANALYSIS", "📊 MARKET BREADTH",
    "🏦 CONSERVATIVE", "⚖️ MODERATE", "🚀 AGGRESSIVE",
    "📈 SWING (CONSERVATIVE)", "📉 SWING (AGGRESSIVE)",
    "📰 MARKET NEWS", "🕐 HISTORY", "📋 USAGE", "ℹ️ HELP",
}

def send(chat_id, text, parse_mode="HTML", reply_markup=None):
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        bot.send_message(chat_id, chunk, parse_mode=parse_mode, reply_markup=reply_markup)

# ── bot handlers ──────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    send(msg.chat.id,
         f"👋 Welcome <b>{msg.from_user.first_name or 'Investor'}</b>!\n\n"
         "Type any NSE symbol: <code>RELIANCE</code>  <code>TCS</code>  <code>BEL</code>\n\n"
         "<b>Menu:</b>\n"
         "• 🔍 Stock Analysis  📊 Market Breadth\n"
         "• 🏦 Conservative  ⚖️ Moderate  🚀 Aggressive\n"
         "• 📈/📉 Swing Trades  📰 News\n"
         "• 🕐 History  📋 Usage",
         reply_markup=main_kb())

@bot.message_handler(commands=["help"])
def cmd_help(msg):
    send(msg.chat.id,
         "📖 <b>HOW TO USE</b>\n\n"
         "Type any NSE symbol: <code>RELIANCE</code>  <code>BEL</code>\n\n"
         "• 🔍 <b>Stock Analysis</b> — technical + fundamental card\n"
         "• 📊 <b>Market Breadth</b> — indices + A/D ratio + RSI signals\n"
         "• 🏦 <b>Conservative</b> — large-cap blue chip portfolio\n"
         "• ⚖️ <b>Moderate</b> — balanced growth portfolio\n"
         "• 🚀 <b>Aggressive</b> — high-growth portfolio\n"
         "• 📈 <b>Swing (Conservative)</b> — 6+/8 score setups\n"
         "• 📉 <b>Swing (Aggressive)</b> — 5+/8 score setups\n"
         "• 📰 <b>Market News</b> — latest headlines\n"
         "• 🕐 <b>History</b> — your last 5 symbols\n"
         "• 📋 <b>Usage</b> — your query stats\n\n"
         "⚠️ Educational only. Not SEBI-registered advice.",
         reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "🔍 Stock Analysis")
def btn_analysis(msg):
    set_state(msg.from_user.id, "awaiting_analysis_symbol")
    send(msg.chat.id, "🔍 Enter an NSE symbol:\n<code>RELIANCE</code>  <code>TCS</code>  <code>BEL</code>")

@bot.message_handler(func=lambda m: m.text == "📊 Market Breadth")
def btn_breadth(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Scanning market breadth…")
    try:
        send(msg.chat.id, build_market_breadth(), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Breadth error: {e}")
        send(msg.chat.id, "❌ Market breadth failed. Try again.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "🏦 Conservative")
def btn_conservative(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Scanning Conservative portfolio…")
    try:
        send(msg.chat.id, build_portfolio("conservative"), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Conservative error: {e}")
        send(msg.chat.id, "❌ Portfolio scan failed. Try again.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "⚖️ Moderate")
def btn_moderate(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Scanning Moderate portfolio…")
    try:
        send(msg.chat.id, build_portfolio("moderate"), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Moderate error: {e}")
        send(msg.chat.id, "❌ Portfolio scan failed. Try again.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "🚀 Aggressive")
def btn_aggressive(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Scanning Aggressive portfolio…")
    try:
        send(msg.chat.id, build_portfolio("aggressive"), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Aggressive error: {e}")
        send(msg.chat.id, "❌ Portfolio scan failed. Try again.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "📈 Swing (Conservative)")
def btn_swing_con(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Scanning conservative swing setups (6+/8)…")
    try:
        from swing_trades import get_swing_trades
        send(msg.chat.id, get_swing_trades("conservative"), reply_markup=main_kb())
    except ImportError:
        send(msg.chat.id, "⚠️ swing_trades.py not found.", reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Swing con error: {e}")
        send(msg.chat.id, "❌ Swing scan failed. Try again.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "📉 Swing (Aggressive)")
def btn_swing_agg(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Scanning aggressive swing setups (5+/8)…")
    try:
        from swing_trades import get_swing_trades
        send(msg.chat.id, get_swing_trades("aggressive"), reply_markup=main_kb())
    except ImportError:
        send(msg.chat.id, "⚠️ swing_trades.py not found.", reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Swing agg error: {e}")
        send(msg.chat.id, "❌ Swing scan failed. Try again.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "📰 Market News")
def btn_news(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Fetching market news…")
    try:
        send(msg.chat.id, build_market_news(), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"News error: {e}")
        send(msg.chat.id, "❌ News fetch failed. Try again.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "🕐 History")
def btn_history(msg):
    uid = msg.from_user.id
    history = get_history(uid)
    if not history:
        send(msg.chat.id,
             "🕐 <b>Your History</b>\n\nNo symbols yet. Type a symbol to get started!",
             reply_markup=main_kb()); return
    lines = ["🕐 <b>RECENT SYMBOLS</b>\n", "Tap a symbol to analyse it:\n"]
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    for sym in history:
        lines.append(f"  • <code>{sym}</code>")
        kb.add(types.KeyboardButton(sym))
    kb.add(types.KeyboardButton("🔙 Main Menu"))
    send(msg.chat.id, "\n".join(lines), reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "📋 Usage")
def btn_usage(msg):
    send(msg.chat.id, build_usage(msg.from_user.id), reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "ℹ️ Help")
def btn_help(msg):
    cmd_help(msg)

@bot.message_handler(func=lambda m: m.text == "🔙 Main Menu")
def btn_back(msg):
    send(msg.chat.id, "🏠 Main menu", reply_markup=main_kb())

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(msg):
    text  = msg.text.strip()
    uid   = msg.from_user.id
    clean = text.upper().replace(" ", "").replace(".NS", "").replace("&", "A")

    if text.upper() in MENU_LABELS:
        return

    if not (2 <= len(clean) <= 15 and clean.replace("-", "").isalnum()):
        set_state(uid, None)
        send(msg.chat.id, "❓ Type a valid NSE symbol like <code>RELIANCE</code>",
             reply_markup=main_kb()); return

    if is_rate_limited(uid):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return

    record_usage(uid)
    record_history(uid, clean)
    state = get_state(uid)
    set_state(uid, None)

    if state == "awaiting_advisory_symbol":
        send(msg.chat.id, f"🤖 Generating AI advisory for <b>{clean}</b>… ⏳ (10-15s)")
        try:
            send(msg.chat.id, build_ai_advisory(clean), reply_markup=main_kb())
        except Exception as e:
            logger.error(f"AI advisory error {clean}: {e}")
            send(msg.chat.id, f"❌ AI advisory failed for {clean}. Try again.",
                 reply_markup=main_kb())
    else:
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
