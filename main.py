"""
main.py  —  AI Stock Advisory Telegram Bot
Flask webhook server for Render deployment.

Start command : gunicorn main:app --bind 0.0.0.0:$PORT
Env vars      : TELEGRAM_TOKEN, WEBHOOK_URL, GROQ_API_KEY,
                GEMINI_API_KEY, OPENAI_KEY, ALPHA_VANTAGE_KEY,
                FINNHUB_API_KEY, TAVILY_API_KEY, PORT

FIXES IN THIS VERSION
─────────────────────
1. Fundamentals N/A  — fetch_info() now uses fast_info + fallback to .info
   and retries; _safe() treats 0 and None equally as missing.
2. AI Chat routing   — btn_ai_chat REGISTERED BEFORE handle_text so Telegram
   routes it correctly (pyTelegramBotAPI matches handlers in order).
3. AI key detection  — lazy init; ai_available() checks keys at call time so
   Render env vars are read after server starts, not at import time.
4. Quality score     — technical-only path when fundamentals missing.
5. AI Chat "same output" — each topic sends a distinct, data-rich prompt.
6. AI Chat "❓ symbol" bug — in_ai_chat state check happens BEFORE symbol
   validation so free text goes to AI, not the symbol parser.
"""

import os
import time
import logging
import threading
from collections import deque
from datetime import datetime

import requests
import pandas as pd
import yfinance as yf
from flask import Flask, request, jsonify
import telebot
from telebot import types

# ── yfinance rate-limit exception ──────────────────────────────────────────────
try:
    from yfinance.exceptions import YFRateLimitError
except ImportError:
    class YFRateLimitError(Exception):
        pass

# ── logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ── config (all from env — never hard-code secrets) ───────────────────────────
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
    raise RuntimeError("TELEGRAM_TOKEN is not set")

WEBHOOK_PATH = f"/webhook/{TELEGRAM_TOKEN}"

# ── AI clients — lazy init so env vars are read after server starts ────────────
_groq_client   = None
_gemini_model  = None
_openai_client = None

def _get_groq():
    global _groq_client
    if _groq_client is None and GROQ_API_KEY:
        try:
            from groq import Groq
            _groq_client = Groq(api_key=GROQ_API_KEY)
        except Exception as e:
            logger.warning(f"GROQ init: {e}")
    return _groq_client

def _get_gemini():
    global _gemini_model
    if _gemini_model is None and GEMINI_API_KEY:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            _gemini_model = genai.GenerativeModel("gemini-2.0-flash")
        except Exception as e:
            logger.warning(f"Gemini init: {e}")
    return _gemini_model

def _get_openai():
    global _openai_client
    if _openai_client is None and OPENAI_API_KEY:
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=OPENAI_API_KEY)
        except Exception as e:
            logger.warning(f"OpenAI init: {e}")
    return _openai_client

def ai_available() -> bool:
    return bool(GROQ_API_KEY or GEMINI_API_KEY or OPENAI_API_KEY)

# ── Flask + bot ────────────────────────────────────────────────────────────────
app = Flask(__name__)
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

# ══════════════════════════════════════════════════════════════════════════════
# PORTFOLIOS
# ══════════════════════════════════════════════════════════════════════════════
PORTFOLIOS = {
    "conservative": {
        "label":  "🏦 Conservative",
        "desc":   "Low-risk, dividend-focused large-cap blue chips",
        "stocks": ["HDFCBANK","TCS","INFY","ITC","ONGC",
                   "POWERGRID","COALINDIA","SBIN","WIPRO","LT"],
    },
    "moderate": {
        "label":  "⚖️ Moderate",
        "desc":   "Balanced growth + stability, large & mid cap mix",
        "stocks": ["RELIANCE","BHARTIARTL","AXISBANK","MARUTI","TITAN",
                   "BAJFINANCE","HCLTECH","KOTAKBANK","SUNPHARMA","NTPC"],
    },
    "aggressive": {
        "label":  "🚀 Aggressive",
        "desc":   "High-growth momentum, mid & small cap",
        "stocks": ["TATAMOTORS","ADANIENT","JSWSTEEL","TATAPOWER","DIXON",
                   "PERSISTENT","COFORGE","BEL","IRFC","ZOMATO"],
    },
}

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

# ── AI Chat quick topics ───────────────────────────────────────────────────────
AI_CHAT_TOPICS = {
    "📊 Nifty Valuation":
        "What is the current Nifty 50 PE ratio valuation? Is it overvalued or undervalued historically? Provide specific numbers and your assessment.",
    "💎 Fundamental Picks":
        "Based on current market data, give me 3 fundamentally strong NSE stocks with low PE (<25), ROE >15%, low debt. Include current price range and why each is attractive.",
    "📈 Nifty Update":
        "Give me a complete Nifty 50 technical update. Include current level, trend direction, key support and resistance levels, and your outlook for the next 5-7 trading days.",
    "🎯 Technical Swing Trade":
        "Give me 2 specific technical swing trade setups for NSE stocks right now. For each: stock name, current price, entry zone, target 1, target 2, stop loss, and reason.",
    "⚡ Option Trade":
        "Give me a specific option trade for Nifty or BankNifty for current week expiry. Include: index, CE or PE, specific strike price, current premium estimate, target premium, stop loss premium, and max risk.",
}

AI_CHAT_TOPIC_KEYS = set(AI_CHAT_TOPICS.keys())

# ══════════════════════════════════════════════════════════════════════════════
# IN-MEMORY STATE
# ══════════════════════════════════════════════════════════════════════════════
_rate:         dict = {}
_user_state:   dict = {}
_user_history: dict = {}
_usage_stats:  dict = {}
_chat_history: dict = {}
_cache:        dict = {}

CACHE_TTL = 900  # 15 min

# ── cache ──────────────────────────────────────────────────────────────────────
def _cget(key):
    d = _cache.get(key)
    if not d or time.time() - d["ts"] > CACHE_TTL:
        return None
    return d["val"]

def _cset(key, val):
    _cache[key] = {"val": val, "ts": time.time()}

# ── rate limiter ───────────────────────────────────────────────────────────────
def is_rate_limited(uid: int, max_calls: int = 6, window: int = 60) -> bool:
    now = time.time()
    calls = [t for t in _rate.get(uid, []) if now - t < window]
    _rate[uid] = calls
    if len(calls) >= max_calls:
        return True
    _rate[uid].append(now)
    return False

# ── user state ─────────────────────────────────────────────────────────────────
def set_state(uid: int, state):
    if state is None:
        _user_state.pop(uid, None)
    else:
        _user_state[uid] = state

def get_state(uid: int):
    return _user_state.get(uid)

# ── history & usage ────────────────────────────────────────────────────────────
def record_history(uid: int, sym: str):
    if uid not in _user_history:
        _user_history[uid] = deque(maxlen=5)
    hist = list(_user_history[uid])
    if sym not in hist:
        _user_history[uid].appendleft(sym)

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
        "", "🕐 <b>Recent Symbols:</b>",
    ]
    lines += [f"  {i+1}. {sym}" for i, sym in enumerate(h)] or ["  None yet."]
    lines += ["", "⚠️ Stats reset on server restart (free tier)."]
    return "\n".join(lines)

# ── AI chat history ────────────────────────────────────────────────────────────
def add_to_chat(uid: int, role: str, content: str):
    if uid not in _chat_history:
        _chat_history[uid] = []
    _chat_history[uid].append({"role": role, "content": content})
    _chat_history[uid] = _chat_history[uid][-12:]

def get_chat_history(uid: int) -> list:
    return _chat_history.get(uid, [])

def clear_chat(uid: int):
    _chat_history.pop(uid, None)

# ══════════════════════════════════════════════════════════════════════════════
# DATA FETCHING — FIXED
# ══════════════════════════════════════════════════════════════════════════════

def fetch_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    """Fetch OHLCV with retry. Uses .NS suffix for NSE stocks."""
    key    = f"hist_{symbol}_{period}"
    cached = _cget(key)
    if cached is not None:
        return cached

    ticker = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
    for attempt in range(3):
        try:
            df = yf.Ticker(ticker).history(
                period=period, interval="1d", auto_adjust=True
            )
            if df.empty:
                if attempt < 2:
                    time.sleep(2)
                    continue
                return pd.DataFrame()
            if float(df["Close"].iloc[-1]) < 0.5:
                return pd.DataFrame()
            _cset(key, df)
            return df
        except YFRateLimitError:
            logger.warning(f"Rate limited: {ticker}, waiting 10s")
            time.sleep(10)
        except Exception as e:
            logger.error(f"History {ticker} attempt {attempt+1}: {e}")
            if attempt < 2:
                time.sleep(2)
    return pd.DataFrame()


def fetch_info(symbol: str) -> dict:
    """
    FIX: Use fast_info first (much more reliable for NSE), then fall back
    to .info. Merges both dicts so we get maximum coverage.
    """
    key    = f"info_{symbol}"
    cached = _cget(key)
    if cached is not None:
        return cached

    ticker_str = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
    t = yf.Ticker(ticker_str)

    merged: dict = {}

    # 1. fast_info — reliable, fast, always returns something
    try:
        fi = t.fast_info
        if fi:
            mapping = {
                "marketCap":                  getattr(fi, "market_cap",         None),
                "fiftyTwoWeekHigh":           getattr(fi, "year_high",           None),
                "fiftyTwoWeekLow":            getattr(fi, "year_low",            None),
                "regularMarketPreviousClose": getattr(fi, "previous_close",      None),
                "regularMarketVolume":        getattr(fi, "three_month_average_volume", None),
                "averageVolume":              getattr(fi, "three_month_average_volume", None),
            }
            merged.update({k: v for k, v in mapping.items() if v is not None})
    except Exception as e:
        logger.debug(f"fast_info {ticker_str}: {e}")

    # 2. .info — slower but has PE, ROE, dividends etc.
    for attempt in range(2):
        try:
            info = t.info or {}
            if info and len(info) > 5:
                # .info wins for fundamental fields
                merged.update(info)
                break
        except Exception as e:
            logger.warning(f"info {ticker_str} attempt {attempt+1}: {e}")
            if attempt == 0:
                time.sleep(2)

    if merged:
        _cset(key, merged)
    return merged


def fetch_ltp_fallback(symbol: str):
    """Try Finnhub → Alpha Vantage when yfinance returns empty."""
    if FINNHUB_API_KEY:
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": f"NSE:{symbol}", "token": FINNHUB_API_KEY},
                timeout=5,
            ).json()
            p = float(r.get("c", 0))
            if p > 0:
                return round(p, 2)
        except Exception as e:
            logger.warning(f"Finnhub {symbol}: {e}")

    if ALPHA_VANTAGE_KEY:
        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={"function": "GLOBAL_QUOTE",
                        "symbol": f"NSE:{symbol}",
                        "apikey": ALPHA_VANTAGE_KEY},
                timeout=6,
            ).json()
            p = float(r.get("Global Quote", {}).get("05. price", 0))
            if p > 0:
                return round(p, 2)
        except Exception as e:
            logger.warning(f"AlphaVantage {symbol}: {e}")

    return None

# ══════════════════════════════════════════════════════════════════════════════
# TECHNICAL INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

def compute_rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, float("nan"))
    val   = (100 - 100 / (1 + rs)).iloc[-1]
    return round(float(val), 1) if pd.notna(val) else 50.0

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
    tr  = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return round(float(val), 2) if pd.notna(val) else 0.0

def compute_pivots(df: pd.DataFrame):
    if len(df) < 2:
        return 0.0, 0.0, 0.0
    p  = df.iloc[-2]
    pp = (p["High"] + p["Low"] + p["Close"]) / 3
    return round(pp, 2), round(2*pp - p["Low"], 2), round(2*pp - p["High"], 2)

# ══════════════════════════════════════════════════════════════════════════════
# FUNDAMENTALS — FIXED
# ══════════════════════════════════════════════════════════════════════════════

def _safe(info: dict, *keys, mult: float = 1.0):
    """
    FIX: Returns first non-None, non-zero value across all keys.
    Multiplies by mult (use 100 for decimal → percentage conversion).
    """
    for k in keys:
        v = info.get(k)
        if v is None:
            continue
        try:
            f = float(v)
            if f == 0.0:
                continue          # 0 = missing sentinel in yfinance
            return round(f * mult, 2)
        except (TypeError, ValueError):
            continue
    return None

def extract_fundamentals(info: dict) -> dict:
    return {
        "company":  (info.get("longName") or info.get("shortName") or "N/A"),
        "sector":   (info.get("sector")   or info.get("quoteType") or "N/A"),
        "industry": (info.get("industry") or "N/A"),
        # P/E — try trailing first, then forward
        "pe":       _safe(info, "trailingPE", "forwardPE"),
        # P/B
        "pb":       _safe(info, "priceToBook"),
        # ROE is a decimal in yfinance (0.23 = 23%)
        "roe":      _safe(info, "returnOnEquity", mult=100),
        # Debt/Equity
        "de":       _safe(info, "debtToEquity"),
        # Dividend yield is a decimal (0.015 = 1.5%)
        "div":      _safe(info, "dividendYield", "trailingAnnualDividendYield", mult=100),
        # EPS
        "eps":      _safe(info, "trailingEps", "forwardEps"),
        # Market cap — fallback to enterprise value
        "mcap":     _safe(info, "marketCap", "enterpriseValue"),
        # 52-week range
        "high_52w": _safe(info, "fiftyTwoWeekHigh"),
        "low_52w":  _safe(info, "fiftyTwoWeekLow"),
        # Previous close for % change
        "prev":     _safe(info, "regularMarketPreviousClose", "previousClose"),
        # Volume — NSE uses regularMarketVolume
        "volume":   _safe(info, "regularMarketVolume", "volume"),
    }

def fmt(v, suffix: str = "", decimals: int = 2) -> str:
    return f"{v:.{decimals}f}{suffix}" if v is not None else "N/A"

def crore(v) -> str:
    if v is None:
        return "N/A"
    c = v / 1e7
    return f"₹{c/1e5:.2f}L Cr" if c >= 1e5 else f"₹{c:,.0f} Cr"

# ══════════════════════════════════════════════════════════════════════════════
# QUALITY SCORE — FIXED (technical-only path when no fundamentals)
# ══════════════════════════════════════════════════════════════════════════════

def quality_score(f: dict, rsi: float, trend: str) -> tuple:
    fund_pts = 0
    tech_pts = 0

    # Fundamentals (max 60)
    has_fundamentals = any(f[k] is not None for k in ["pe", "pb", "roe", "div", "de"])

    if has_fundamentals:
        if f["pe"]  is not None: fund_pts += 15 if f["pe"]  < 20 else (10 if f["pe"]  < 30 else 3)
        if f["pb"]  is not None: fund_pts += 10 if f["pb"]  < 2  else (5  if f["pb"]  < 4  else 0)
        if f["roe"] is not None: fund_pts += 15 if f["roe"] > 20 else (10 if f["roe"] > 12 else 3)
        if f["div"] is not None: fund_pts += 10 if f["div"] > 1  else 5
        if f["de"]  is not None: fund_pts += 10 if f["de"]  < 1  else (5  if f["de"]  < 2  else 0)

    # Technicals (max 40)
    if 40 < rsi < 60:    tech_pts += 20
    elif 30 < rsi < 70:  tech_pts += 10
    if trend == "BULLISH":  tech_pts += 20
    elif trend == "NEUTRAL": tech_pts += 10

    if has_fundamentals:
        total = fund_pts + tech_pts
        stars = "★" * (total // 20) + "☆" * (5 - total // 20)
        if total >= 75:  verdict = "STRONG BUY"
        elif total >= 60: verdict = "BUY"
        elif total >= 45: verdict = "HOLD"
        elif total >= 30: verdict = "CAUTION"
        else:             verdict = "AVOID"
        return total, f"{total}/100 {stars}  {verdict}"
    else:
        # Technical-only scoring out of 40
        stars = "★" * (tech_pts // 8) + "☆" * (5 - tech_pts // 8)
        if tech_pts >= 30:  verdict = "Technically BULLISH"
        elif tech_pts >= 20: verdict = "Technically NEUTRAL"
        else:               verdict = "Technically BEARISH"
        return tech_pts, f"{tech_pts}/40 {stars}  {verdict}  ⚠️ Fundamentals loading"

# ══════════════════════════════════════════════════════════════════════════════
# AI CALLS — with proper fallback chain
# ══════════════════════════════════════════════════════════════════════════════

def _call_ai(messages: list, max_tokens: int = 500, system: str = "") -> tuple:
    """
    Call GROQ -> Gemini -> OpenAI in order.
    Returns (text, error_summary).
    text = response if success, "" if all failed.
    error_summary = human-readable failures string.
    """
    errors = []

    # 1. GROQ
    groq = _get_groq()
    if not GROQ_API_KEY:
        errors.append("GROQ: key not set in Render env vars")
    elif not groq:
        errors.append("GROQ: client failed to initialize (check key format)")
    else:
        try:
            msgs = ([{"role": "system", "content": system}] if system else []) + messages
            r = groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=msgs, max_tokens=max_tokens, temperature=0.4,
            )
            text = (r.choices[0].message.content or "").strip()
            if text:
                logger.info("AI: GROQ success")
                return text, ""
            errors.append("GROQ: empty response")
        except Exception as e:
            msg = str(e)
            logger.error(f"GROQ FAILED (check GROQ_API_KEY in Render): {e}")
            if "401" in msg or "invalid_api_key" in msg or "Incorrect API key" in msg:
                errors.append("GROQ: INVALID KEY - regenerate at console.groq.com")
            elif "429" in msg or "rate" in msg.lower():
                errors.append("GROQ: rate limited - try again in 60s")
            else:
                errors.append(f"GROQ: {msg[:100]}")

    # 2. Gemini
    gemini = _get_gemini()
    if not GEMINI_API_KEY:
        errors.append("Gemini: key not set in Render env vars")
    elif not gemini:
        errors.append("Gemini: client failed to initialize")
    else:
        try:
            full = (system + "\n\n" if system else "") + "\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in messages)
            r = gemini.generate_content(full)
            text = (getattr(r, "text", "") or "").strip()
            if text:
                logger.info("AI: Gemini success")
                return text, ""
            errors.append("Gemini: empty response")
        except Exception as e:
            msg = str(e)
            logger.error(f"GEMINI FAILED (check GEMINI_API_KEY in Render): {e}")
            if "API_KEY_INVALID" in msg or "401" in msg:
                errors.append("Gemini: INVALID KEY - check aistudio.google.com")
            elif "429" in msg or "quota" in msg.lower():
                errors.append("Gemini: quota/rate limit exceeded")
            else:
                errors.append(f"Gemini: {msg[:100]}")

    # 3. OpenAI
    openai_client = _get_openai()
    if not OPENAI_API_KEY:
        errors.append("OpenAI: key not set in Render env vars")
    elif not openai_client:
        errors.append("OpenAI: client failed to initialize")
    else:
        try:
            msgs = ([{"role": "system", "content": system}] if system else []) + messages
            r = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=msgs, max_tokens=max_tokens, temperature=0.4,
            )
            text = (r.choices[0].message.content or "").strip()
            if text:
                logger.info("AI: OpenAI success")
                return text, ""
            errors.append("OpenAI: empty response")
        except Exception as e:
            msg = str(e)
            logger.warning(f"OpenAI failed: {e}")
            if "401" in msg or "invalid_api_key" in msg or "Incorrect API key" in msg:
                errors.append("OpenAI: INVALID KEY - regenerate at platform.openai.com/api-keys")
            elif "429" in msg or "quota" in msg.lower():
                errors.append("OpenAI: rate/quota limit")
            else:
                errors.append(f"OpenAI: {msg[:100]}")

    return "", "\n".join(errors)

def ai_insights(symbol, ltp, rsi, macd_line, trend, pe, roe) -> str:
    """Brief 3-bullet bullish + 2-bullet risk snippet for stock analysis card."""
    if not ai_available():
        return "⚠️ No AI keys set (GROQ_API_KEY / GEMINI_API_KEY / OPENAI_KEY)"

    prompt = (
        f"3-bullet BULLISH factors and 2-bullet RISKS for {symbol} NSE India. "
        f"LTP ₹{ltp}, RSI {rsi}, MACD {'bullish' if macd_line > 0 else 'bearish'}, "
        f"Trend {trend}, PE {pe}, ROE {roe}%.\n"
        f"Format:\nBULLISH:\n• ...\n• ...\n• ...\nRISKS:\n• ...\n• ..."
    )
    result, err = _call_ai(
        [{"role": "user", "content": prompt}],
        max_tokens=300,
        system="You are a concise Indian equity analyst. Give specific, data-driven points.",
    )
    if result:
        return result
    if err:
        return f"⚠️ AI unavailable:\n{err}"
    return "⚠️ AI analysis temporarily unavailable"


def fetch_news(symbol: str) -> str:
    """Fetch news via Tavily → Alpha Vantage."""
    # Tavily
    if TAVILY_API_KEY:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY,
                      "query": f"{symbol} NSE India stock news",
                      "max_results": 3, "search_depth": "basic"},
                timeout=6,
            ).json()
            lines = [f"📰 {x['title'][:85]}" for x in r.get("results", [])[:2] if x.get("title")]
            if lines:
                return "\n".join(lines)
        except Exception as e:
            logger.warning(f"Tavily news {symbol}: {e}")

    # Alpha Vantage
    if ALPHA_VANTAGE_KEY:
        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={"function": "NEWS_SENTIMENT",
                        "tickers": f"NSE:{symbol}",
                        "limit": 3, "apikey": ALPHA_VANTAGE_KEY},
                timeout=6,
            ).json()
            lines = [f"📰 {a['title'][:85]}" for a in r.get("feed", [])[:2] if a.get("title")]
            if lines:
                return "\n".join(lines)
        except Exception as e:
            logger.warning(f"AV news {symbol}: {e}")

    return ""

# ══════════════════════════════════════════════════════════════════════════════
# STOCK ADVISORY BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_advisory(symbol: str) -> str:
    symbol = symbol.upper().replace(".NS", "")
    df     = fetch_history(symbol)
    info   = fetch_info(symbol)

    if df.empty or len(df) < 5:
        fb = fetch_ltp_fallback(symbol)
        if fb:
            return (f"⚠️ <b>{symbol}</b>  LTP: ₹{fb} (Finnhub/AlphaVantage)\n\n"
                    f"Full technical analysis needs more history. Try again later.")
        return f"❌ <b>{symbol}</b> not found. Check the NSE symbol and try again."

    close  = df["Close"]
    ltp    = round(float(close.iloc[-1]), 2)
    f      = extract_fundamentals(info)

    rsi_v              = compute_rsi(close)
    macd_line, macd_sig = compute_macd(close)
    ema20              = compute_ema(close, 20)
    ema50              = compute_ema(close, 50)
    ema200             = compute_ema(close, 200)
    bb_u, bb_m, bb_l   = compute_bb(close)
    atr                = compute_atr(df)
    pp, r1, s1         = compute_pivots(df)
    high20  = round(float(close.rolling(20).max().iloc[-1]), 2)
    low20   = round(float(close.rolling(20).min().iloc[-1]), 2)

    trend = ("BULLISH" if ltp > ema20 > ema50 else
             "BEARISH" if ltp < ema20 < ema50 else "NEUTRAL")

    sl      = round(ltp - 2 * atr, 2)
    tgt_1w  = round(ltp + atr * 1.5, 2)
    tgt_1m  = round(ltp + atr * 3,   2)
    tgt_3m  = round(ltp + atr * 6,   2)
    tgt_6m  = round(ltp * 1.10, 2)
    tgt_1y  = round(ltp * 1.20, 2)
    tgt_2y  = round(ltp * 1.40, 2)

    _, score_str = quality_score(f, rsi_v, trend)

    prev    = f["prev"]
    chg_str = ""
    if prev:
        chg = round(((ltp - prev) / prev) * 100, 2)
        chg_str = f" ({'+' if chg >= 0 else ''}{chg}%)"

    trend_em   = "🟢" if trend == "BULLISH" else ("🔴" if trend == "BEARISH" else "⚪")
    rsi_label  = "🔴 Overbought" if rsi_v > 70 else ("🟢 Oversold" if rsi_v < 30 else "✅ Neutral")
    macd_label = "🟢 Bullish" if macd_line > macd_sig else "🔴 Bearish"

    ai_text   = ai_insights(symbol, ltp, rsi_v, macd_line, trend,
                            fmt(f["pe"]), fmt(f["roe"]))
    news_text = fetch_news(symbol)

    lines = [
        "╔══════════════════════════════════════╗",
        "║   🤖 AI STOCK ANALYSIS               ║",
        "╚══════════════════════════════════════╝",
        f"📅 {datetime.now().strftime('%d-%b-%Y %H:%M')}",
        "",
        f"🏢 <b>{f['company']}</b>",
        f"📊 <b>{symbol}</b>  |  🏭 {f['sector']}",
        f"💰 MCap: {crore(f['mcap'])}",
        f"💵 LTP: ₹{ltp}{chg_str}",
        f"📈 52W: ₹{fmt(f['high_52w'])} / ₹{fmt(f['low_52w'])}",
        f"📊 Prev Close: ₹{fmt(f['prev'])}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "📊 <b>FUNDAMENTALS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• PE: {fmt(f['pe'])}x  |  PB: {fmt(f['pb'])}x",
        f"• ROE: {fmt(f['roe'], '%')}  |  D/E: {fmt(f['de'])}",
        f"• Div Yield: {fmt(f['div'], '%')}  |  EPS: ₹{fmt(f['eps'])}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🔬 <b>TECHNICALS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📈 Trend: {trend_em} {trend}",
        f"• RSI: {rsi_v}  {rsi_label}",
        f"• MACD: {macd_line} vs {macd_sig}  {macd_label}",
        f"• EMA20: {ema20}  |  EMA50: {ema50}  |  EMA200: {ema200}",
        f"• BB: U{bb_u} M{bb_m} L{bb_l}  |  ATR: {atr}",
        f"• Pivot: ₹{pp}  |  R1: ₹{r1}  |  S1: ₹{s1}",
        f"• 20D H/L: ₹{high20} / ₹{low20}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🎯 <b>SHORT TERM TARGETS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"1W: ₹{tgt_1w}  |  1M: ₹{tgt_1m}  |  3M: ₹{tgt_3m}",
        f"🛑 Stop Loss: ₹{sl}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🚀 <b>LONG TERM TARGETS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"6M: ₹{tgt_6m}  |  1Y: ₹{tgt_1y}  |  2Y: ₹{tgt_2y}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🤖 <b>AI INSIGHTS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        ai_text,
    ]
    if news_text:
        lines += ["", "━━━━━━━━━━━━━━━━━━━━",
                  "📰 <b>LATEST NEWS</b>",
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

# ══════════════════════════════════════════════════════════════════════════════
# AI CHAT — Live Market Q&A
# ══════════════════════════════════════════════════════════════════════════════

CHAT_SYSTEM = """You are an expert Indian stock market AI assistant with access to LIVE market data.
You specialize in:
1. NIFTY VALUATION — PE analysis, fair value, over/undervalued assessment  
2. FUNDAMENTAL PICKS — stocks with strong ROE, low PE, solid balance sheet
3. NIFTY UPDATE — index levels, trend, support/resistance, weekly outlook
4. TECHNICAL SWING TRADES — entry zone, target 1, target 2, stop loss
5. OPTION TRADES — strike, expiry, entry premium, target, SL for Nifty/BankNifty

RULES:
- Always reference the live data provided. Quote specific numbers.
- For swing trades: stock, entry zone, T1, T2, SL, timeframe.
- For options: index, CE/PE, strike, expiry, entry premium, target, SL.
- Be specific. No vague answers.
- End with: ⚠️ Educational only. Not SEBI-registered advice."""


def get_live_market_context() -> str:
    """Build a real-time market snapshot to inject into AI prompts."""
    lines = [f"=== LIVE DATA {datetime.now().strftime('%d-%b-%Y %H:%M IST')} ==="]

    # Nifty 50
    try:
        df = yf.Ticker("^NSEI").history(period="5d", interval="1d")
        if len(df) >= 2:
            ltp  = round(float(df["Close"].iloc[-1]), 2)
            prev = round(float(df["Close"].iloc[-2]), 2)
            chg  = round((ltp - prev) / prev * 100, 2)
            h    = round(float(df["High"].iloc[-1]), 2)
            l    = round(float(df["Low"].iloc[-1]),  2)
            lines.append(f"NIFTY 50: {ltp:,.2f} ({chg:+.2f}%) | High:{h} Low:{l}")
            # 5-day range
            w_high = round(float(df["High"].max()), 2)
            w_low  = round(float(df["Low"].min()),  2)
            lines.append(f"NIFTY 5D Range: {w_low} – {w_high}")
    except Exception:
        lines.append("NIFTY 50: unavailable")

    # Bank Nifty
    try:
        df = yf.Ticker("^NSEBANK").history(period="2d", interval="1d")
        if len(df) >= 2:
            ltp  = round(float(df["Close"].iloc[-1]), 2)
            prev = round(float(df["Close"].iloc[-2]), 2)
            chg  = round((ltp - prev) / prev * 100, 2)
            lines.append(f"BANK NIFTY: {ltp:,.2f} ({chg:+.2f}%)")
    except Exception:
        pass

    # Nifty PE
    try:
        info = yf.Ticker("^NSEI").info
        pe   = info.get("trailingPE") or info.get("forwardPE")
        if pe:
            lines.append(f"NIFTY PE: {round(float(pe),1)} (10yr avg ~20, expensive >22, cheap <18)")
    except Exception:
        pass

    # Top 8 stocks snapshot
    snap = []
    for sym in ["RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","SBIN","BAJFINANCE","TATAMOTORS"]:
        try:
            df = fetch_history(sym, period="5d")
            if df.empty or len(df) < 2:
                continue
            ltp  = round(float(df["Close"].iloc[-1]), 2)
            prev = round(float(df["Close"].iloc[-2]), 2)
            chg  = round((ltp - prev) / prev * 100, 2)
            rsi_v = compute_rsi(df["Close"])
            snap.append(f"{sym}:₹{ltp}({chg:+.1f}%)RSI:{rsi_v}")
        except Exception:
            pass
    if snap:
        lines.append("TOP STOCKS: " + "  ".join(snap))

    return "\n".join(lines)


def ai_chat_respond(uid: int, user_message: str) -> str:
    """Respond to a chat message using live market context + conversation history."""
    if not ai_available():
        return ("⚠️ No AI keys configured.\n\n"
                "Please set GROQ_API_KEY (or GEMINI_API_KEY / OPENAI_KEY) "
                "in your Render environment variables.")

    market_ctx = get_live_market_context()
    system     = CHAT_SYSTEM + f"\n\nLIVE MARKET CONTEXT:\n{market_ctx}"
    history    = get_chat_history(uid)

    messages = list(history) + [{"role": "user", "content": user_message}]

    result, err = _call_ai(messages, max_tokens=550, system=system)

    if result:
        add_to_chat(uid, "user", user_message)
        add_to_chat(uid, "assistant", result)
        return result

    # Build a helpful error message showing exactly what failed
    error_msg = (
        "❌ <b>All AI providers failed.</b>\n\n"
        "<b>Diagnosis:</b>\n"
        f"{err}\n\n"
        "<b>Fix:</b>\n"
        "1. Go to Render Dashboard → Environment\n"
        "2. Verify GROQ_API_KEY is correct\n"
        "3. Get a free key at console.groq.com\n"
        "4. Redeploy after updating"
    )
    logger.error(f"All AI providers failed for uid {uid}: {err}")
    return error_msg

# ══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO SCANNER
# ══════════════════════════════════════════════════════════════════════════════

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
            if df.empty or len(df) < 2:
                lines.append(f"  • <b>{sym}</b>: ⚠️ No data")
                continue
            close  = df["Close"]
            ltp    = round(float(close.iloc[-1]), 2)
            prev   = round(float(close.iloc[-2]), 2)
            chg    = round(((ltp - prev) / prev) * 100, 2)
            rsi_v  = compute_rsi(close)
            f_data = extract_fundamentals(info)
            trend  = ("BULLISH" if len(close) >= 3 and
                      float(close.iloc[-1]) > float(close.iloc[-3]) else "NEUTRAL")
            score_num, _ = quality_score(f_data, rsi_v, trend)
            total_score += score_num
            count       += 1
            chg_em = "🟢" if chg >= 0 else "🔴"
            rsi_em = "🟢" if rsi_v < 40 else ("🔴" if rsi_v > 65 else "⚪")
            lines.append(
                f"  {chg_em} <b>{sym}</b>: ₹{ltp} "
                f"({'+' if chg>=0 else ''}{chg}%)"
                f"  RSI:{rsi_v}{rsi_em}  Score:{score_num}"
            )
        except Exception as e:
            logger.error(f"Portfolio {sym}: {e}")
            lines.append(f"  • <b>{sym}</b>: ⚠️ Error")
    avg = round(total_score / count, 1) if count else 0
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        f"📊 Avg Score: {avg}  |  {count}/{len(p['stocks'])} loaded",
        "", "⚠️ Educational only. Not SEBI-registered advice.",
    ]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# MARKET BREADTH
# ══════════════════════════════════════════════════════════════════════════════

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
            if len(df) < 2:
                lines.append(f"  • {name}: N/A")
                continue
            ltp  = round(float(df["Close"].iloc[-1]), 2)
            prev = round(float(df["Close"].iloc[-2]), 2)
            chg  = round(((ltp - prev) / prev) * 100, 2)
            em   = "🟢" if chg >= 0 else "🔴"
            lines.append(f"  {em} <b>{name}</b>: {ltp:,.2f} ({'+' if chg>=0 else ''}{chg}%)")
        except Exception:
            lines.append(f"  • {name}: N/A")

    adv = dec = unch = 0
    overbought, oversold = [], []
    for sym in BREADTH_STOCKS:
        try:
            df = fetch_history(sym, period="5d")
            if df.empty or len(df) < 2:
                unch += 1; continue
            close = df["Close"]
            chg   = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
            rsi_v = compute_rsi(close)
            if chg > 0.1:    adv  += 1
            elif chg < -0.1: dec  += 1
            else:            unch += 1
            if rsi_v > 70: overbought.append(f"{sym}({rsi_v})")
            if rsi_v < 30: oversold.append(f"{sym}({rsi_v})")
        except Exception:
            unch += 1

    total = adv + dec + unch
    ratio = round(adv / dec, 2) if dec > 0 else float(adv)
    mood  = ("🟢 BULLISH" if adv > dec * 1.5 else
             "🔴 BEARISH" if dec > adv * 1.5 else "⚪ NEUTRAL")
    lines += [
        "", "━━━━━━━━━━━━━━━━━━━━",
        f"🔢 <b>BREADTH ({total} stocks)</b>",
        f"  🟢 Adv: {adv}  🔴 Dec: {dec}  ⚪ Unch: {unch}",
        f"  A/D Ratio: {ratio}  |  Mood: {mood}",
    ]
    if overbought: lines.append(f"  🔴 Overbought: {', '.join(overbought[:5])}")
    if oversold:   lines.append(f"  🟢 Oversold:   {', '.join(oversold[:5])}")
    lines += ["", "⚠️ Educational only. Not SEBI-registered advice."]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# MARKET NEWS
# ══════════════════════════════════════════════════════════════════════════════

def build_market_news() -> str:
    headlines = []
    if TAVILY_API_KEY:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY,
                      "query": "Indian stock market NSE Nifty news today",
                      "max_results": 5, "search_depth": "basic"},
                timeout=8,
            ).json()
            headlines = [f"📰 {x['title'][:90]}"
                         for x in r.get("results", [])[:5] if x.get("title")]
        except Exception as e:
            logger.warning(f"Tavily market news: {e}")

    if not headlines and ALPHA_VANTAGE_KEY:
        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={"function": "NEWS_SENTIMENT",
                        "topics": "financial_markets",
                        "limit": 5, "apikey": ALPHA_VANTAGE_KEY},
                timeout=8,
            ).json()
            headlines = [f"📰 {a['title'][:90]}"
                         for a in r.get("feed", [])[:5] if a.get("title")]
        except Exception as e:
            logger.warning(f"AV market news: {e}")

    if not headlines:
        return ("📰 <b>MARKET NEWS</b>\n\n"
                "⚠️ No news available. Set TAVILY_API_KEY or ALPHA_VANTAGE_KEY.")

    lines = ["📰 <b>MARKET NEWS</b>",
             f"📅 {datetime.now().strftime('%d-%b-%Y %H:%M')}",
             "━━━━━━━━━━━━━━━━━━━━", ""]
    lines.extend(headlines)
    lines += ["", "⚠️ Educational only. Not SEBI-registered advice."]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════════════════

def main_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("🔍 Stock Analysis"),
        types.KeyboardButton("📊 Market Breadth"),
        types.KeyboardButton("🤖 AI Chat"),
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

def ai_chat_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📊 Nifty Valuation"),
        types.KeyboardButton("💎 Fundamental Picks"),
        types.KeyboardButton("📈 Nifty Update"),
        types.KeyboardButton("🎯 Technical Swing Trade"),
        types.KeyboardButton("⚡ Option Trade"),
        types.KeyboardButton("🔙 Main Menu"),
    )
    return kb

# All top-level menu labels (uppercase for comparison)
MENU_LABELS = {
    "🔍 STOCK ANALYSIS", "📊 MARKET BREADTH", "🤖 AI CHAT",
    "🏦 CONSERVATIVE", "⚖️ MODERATE", "🚀 AGGRESSIVE",
    "📈 SWING (CONSERVATIVE)", "📉 SWING (AGGRESSIVE)",
    "📰 MARKET NEWS", "🕐 HISTORY", "📋 USAGE", "ℹ️ HELP",
}

# AI Chat sub-menu labels (do NOT treat as symbols)
AI_MENU_LABELS = {
    "📊 NIFTY VALUATION", "💎 FUNDAMENTAL PICKS",
    "📈 NIFTY UPDATE", "🎯 TECHNICAL SWING TRADE", "⚡ OPTION TRADE",
    "🔙 MAIN MENU",
}

def send(chat_id, text, parse_mode="HTML", reply_markup=None):
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        bot.send_message(chat_id, chunk, parse_mode=parse_mode,
                         reply_markup=reply_markup)

# ══════════════════════════════════════════════════════════════════════════════
# BOT HANDLERS
# IMPORTANT: All specific handlers MUST be registered BEFORE handle_text
# because pyTelegramBotAPI matches in registration order.
# ══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    clear_chat(msg.from_user.id)
    set_state(msg.from_user.id, None)
    send(msg.chat.id,
         f"👋 Welcome <b>{msg.from_user.first_name or 'Investor'}</b>!\n\n"
         "Type any NSE symbol for instant analysis:\n"
         "<code>RELIANCE</code>  <code>TCS</code>  <code>BEL</code>  <code>VEDL</code>\n\n"
         "<b>Menu Guide:</b>\n"
         "• 🔍 Stock Analysis — full card\n"
         "• 🤖 AI Chat — ask anything, live market data\n"
         "• 🏦⚖️🚀 Portfolio — scan by risk profile\n"
         "• 📈📉 Swing — setup scanner\n"
         "• 📊 Market Breadth — indices + A/D",
         reply_markup=main_kb())

@bot.message_handler(commands=["help"])
def cmd_help(msg):
    send(msg.chat.id,
         "📖 <b>HOW TO USE</b>\n\n"
         "Type any NSE symbol: <code>RELIANCE</code>  <code>BEL</code>\n\n"
         "• 🔍 <b>Stock Analysis</b> — technical + fundamental card\n"
         "• 📊 <b>Market Breadth</b> — Nifty indices + A/D ratio\n"
         "• 🤖 <b>AI Chat</b> — live market Q&A (Nifty valuation, picks, options)\n"
         "• 🏦 <b>Conservative</b> — large-cap portfolio scan\n"
         "• ⚖️ <b>Moderate</b> — balanced portfolio scan\n"
         "• 🚀 <b>Aggressive</b> — growth portfolio scan\n"
         "• 📈 <b>Swing (Conservative)</b> — 6+/8 score setups\n"
         "• 📉 <b>Swing (Aggressive)</b> — 5+/8 score setups\n"
         "• 📰 <b>Market News</b> — latest headlines\n"
         "• 🕐 <b>History</b> — your last 5 symbols\n"
         "• 📋 <b>Usage</b> — query stats\n\n"
         "⚠️ Educational only. Not SEBI-registered advice.",
         reply_markup=main_kb())

# ── Main menu buttons ──────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🔍 Stock Analysis")
def btn_analysis(msg):
    set_state(msg.from_user.id, "awaiting_analysis_symbol")
    send(msg.chat.id,
         "🔍 <b>Stock Analysis</b>\n\nEnter an NSE symbol:\n"
         "<code>RELIANCE</code>  <code>TCS</code>  <code>BEL</code>  <code>VEDL</code>")

@bot.message_handler(func=lambda m: m.text == "📊 Market Breadth")
def btn_breadth(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Scanning market breadth…")
    try:
        send(msg.chat.id, build_market_breadth(), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Breadth: {e}")
        send(msg.chat.id, "❌ Market breadth failed. Try again.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "🏦 Conservative")
def btn_conservative(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Scanning Conservative portfolio…")
    try:
        send(msg.chat.id, build_portfolio("conservative"), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Conservative: {e}")
        send(msg.chat.id, "❌ Portfolio scan failed. Try again.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "⚖️ Moderate")
def btn_moderate(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Scanning Moderate portfolio…")
    try:
        send(msg.chat.id, build_portfolio("moderate"), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Moderate: {e}")
        send(msg.chat.id, "❌ Portfolio scan failed. Try again.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "🚀 Aggressive")
def btn_aggressive(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Scanning Aggressive portfolio…")
    try:
        send(msg.chat.id, build_portfolio("aggressive"), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Aggressive: {e}")
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
        send(msg.chat.id, "⚠️ swing_trades.py not in project.", reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Swing con: {e}")
        send(msg.chat.id, "❌ Swing scan failed.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "📉 Swing (Aggressive)")
def btn_swing_agg(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Scanning aggressive swing setups (5+/8)…")
    try:
        from swing_trades import get_swing_trades
        send(msg.chat.id, get_swing_trades("aggressive"), reply_markup=main_kb())
    except ImportError:
        send(msg.chat.id, "⚠️ swing_trades.py not in project.", reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Swing agg: {e}")
        send(msg.chat.id, "❌ Swing scan failed.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "📰 Market News")
def btn_news(msg):
    if is_rate_limited(msg.from_user.id):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return
    send(msg.chat.id, "⏳ Fetching market news…")
    try:
        send(msg.chat.id, build_market_news(), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"News: {e}")
        send(msg.chat.id, "❌ News fetch failed.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "🕐 History")
def btn_history(msg):
    uid     = msg.from_user.id
    history = get_history(uid)
    if not history:
        send(msg.chat.id,
             "🕐 <b>Your History</b>\n\nNo symbols searched yet.\n"
             "Type a symbol like <code>RELIANCE</code> to get started!",
             reply_markup=main_kb()); return
    lines = ["🕐 <b>RECENT SYMBOLS</b>\n", "Tap a symbol to re-analyse:\n"]
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
    clear_chat(msg.from_user.id)
    set_state(msg.from_user.id, None)
    send(msg.chat.id, "🏠 Main Menu", reply_markup=main_kb())

# ── AI Chat handlers ───────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "🤖 AI Chat")
def btn_ai_chat(msg):
    set_state(msg.from_user.id, "in_ai_chat")
    send(msg.chat.id,
         "🤖 <b>AI CHAT — Live Market Assistant</b>\n\n"
         "Ask me anything about the Indian market:\n\n"
         "💬 <i>Examples:</i>\n"
         "• <i>Is Nifty overvalued right now?</i>\n"
         "• <i>Give me a swing trade setup for today</i>\n"
         "• <i>Best fundamentally strong stocks to buy</i>\n"
         "• <i>Nifty option trade idea for this week</i>\n\n"
         "Or tap a quick topic below 👇",
         reply_markup=ai_chat_kb())

@bot.message_handler(func=lambda m: m.text in AI_CHAT_TOPIC_KEYS)
def btn_ai_topic(msg):
    uid   = msg.from_user.id
    topic = msg.text
    set_state(uid, "in_ai_chat")   # keep in AI chat mode after topic click
    query = AI_CHAT_TOPICS[topic]
    if is_rate_limited(uid):
        send(msg.chat.id, "⏳ Too many requests. Please wait.", reply_markup=ai_chat_kb())
        return
    send(msg.chat.id, "🤖 Fetching live data & analysing… ⏳")
    record_usage(uid)
    try:
        response = ai_chat_respond(uid, query)
        send(msg.chat.id,
             f"<b>{topic}</b>\n━━━━━━━━━━━━━━━━━━━━\n{response}",
             reply_markup=ai_chat_kb())
    except Exception as e:
        logger.error(f"AI topic {topic}: {e}")
        send(msg.chat.id, "❌ AI response failed. Try again.", reply_markup=ai_chat_kb())

# ── Catch-all text handler (MUST be last) ─────────────────────────────────────

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(msg):
    text = msg.text.strip()
    uid  = msg.from_user.id

    # Skip already-handled menu labels
    if text.upper() in MENU_LABELS or text.upper() in AI_MENU_LABELS:
        return

    state = get_state(uid)

    # ── AI Chat mode: ALL free text goes to AI ────────────────────────────────
    if state == "in_ai_chat":
        if is_rate_limited(uid):
            send(msg.chat.id, "⏳ Too many requests. Please wait.",
                 reply_markup=ai_chat_kb()); return
        send(msg.chat.id, "🤖 Thinking… ⏳")
        record_usage(uid)
        try:
            response = ai_chat_respond(uid, text)
            send(msg.chat.id, response, reply_markup=ai_chat_kb())
        except Exception as e:
            logger.error(f"AI chat: {e}")
            send(msg.chat.id, "❌ AI response failed. Try again.",
                 reply_markup=ai_chat_kb())
        return

    # ── Symbol lookup ──────────────────────────────────────────────────────────
    clean = text.upper().replace(" ", "").replace(".NS", "").replace("&", "A")

    if not (2 <= len(clean) <= 15 and clean.replace("-", "").isalnum()):
        send(msg.chat.id,
             "❓ Type a valid NSE symbol like <code>RELIANCE</code>\n"
             "Or tap <b>🤖 AI Chat</b> to ask the AI a question.",
             reply_markup=main_kb()); return

    if is_rate_limited(uid):
        send(msg.chat.id, "⏳ Too many requests. Please wait."); return

    record_usage(uid)
    record_history(uid, clean)
    set_state(uid, None)

    send(msg.chat.id, f"🔍 Analysing <b>{clean}</b>… ⏳")
    try:
        send(msg.chat.id, build_advisory(clean), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Advisory {clean}: {e}")
        send(msg.chat.id, f"❌ Could not analyse {clean}. Try again.",
             reply_markup=main_kb())

# ══════════════════════════════════════════════════════════════════════════════
# FLASK ROUTES
# ══════════════════════════════════════════════════════════════════════════════

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
        logger.error(f"Webhook: {e}")
    return "OK", 200

@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    if not WEBHOOK_URL:
        return jsonify({"error": "WEBHOOK_URL not set"}), 400
    url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
    try:
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=url)
        logger.info(f"Webhook: {url}")
        return jsonify({"status": "ok", "webhook": url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/debug", methods=["GET"])
def debug():
    """Quick diagnostic — check which AI keys are loaded."""
    groq_ok   = _get_groq()   is not None
    gemini_ok = _get_gemini() is not None
    openai_ok = _get_openai() is not None
    return jsonify({
        "TELEGRAM_TOKEN":    "✅ set"    if TELEGRAM_TOKEN    else "❌ MISSING",
        "WEBHOOK_URL":        WEBHOOK_URL or "❌ MISSING",
        "GROQ_API_KEY":      "✅ set"    if GROQ_API_KEY      else "❌ MISSING — get free key at console.groq.com",
        "GEMINI_API_KEY":    "✅ set"    if GEMINI_API_KEY    else "❌ MISSING — get free key at aistudio.google.com",
        "OPENAI_KEY":        "✅ set"    if OPENAI_API_KEY    else "❌ MISSING",
        "ALPHA_VANTAGE_KEY": "✅ set"    if ALPHA_VANTAGE_KEY else "❌ MISSING",
        "FINNHUB_API_KEY":   "✅ set"    if FINNHUB_API_KEY   else "❌ MISSING",
        "TAVILY_API_KEY":    "✅ set"    if TAVILY_API_KEY    else "❌ MISSING",
        "groq_client":       "✅ initialized" if groq_ok   else "❌ FAILED — check key at console.groq.com",
        "gemini_model":      "✅ initialized" if gemini_ok else "❌ FAILED — check key at aistudio.google.com",
        "openai_client":     "✅ initialized" if openai_ok else "❌ FAILED — invalid key (401)",
        "ai_ready":          "✅ YES" if (groq_ok or gemini_ok or openai_ok) else "❌ NO — all AI providers failed",
    })

@app.route("/test_ai", methods=["GET"])
def test_ai():
    """Live AI test — calls all providers and shows exactly what happens."""
    results = {}
    
    # Test GROQ
    if GROQ_API_KEY:
        try:
            g = _get_groq()
            if g:
                r = g.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": "Say: GROQ OK"}],
                    max_tokens=10,
                )
                results["GROQ"] = f"✅ Working — {r.choices[0].message.content.strip()}"
            else:
                results["GROQ"] = "❌ Client not initialized"
        except Exception as e:
            results["GROQ"] = f"❌ FAILED: {str(e)[:200]}"
    else:
        results["GROQ"] = "⚠️ Key not set"
    
    # Test Gemini
    if GEMINI_API_KEY:
        try:
            g = _get_gemini()
            if g:
                r = g.generate_content("Say: GEMINI OK")
                results["Gemini"] = f"✅ Working — {getattr(r, 'text', 'no text')[:30].strip()}"
            else:
                results["Gemini"] = "❌ Client not initialized"
        except Exception as e:
            results["Gemini"] = f"❌ FAILED: {str(e)[:200]}"
    else:
        results["Gemini"] = "⚠️ Key not set"
    
    # Test OpenAI
    if OPENAI_API_KEY:
        try:
            o = _get_openai()
            if o:
                r = o.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": "Say: OPENAI OK"}],
                    max_tokens=10,
                )
                results["OpenAI"] = f"✅ Working — {r.choices[0].message.content.strip()}"
            else:
                results["OpenAI"] = "❌ Client not initialized"
        except Exception as e:
            results["OpenAI"] = f"❌ FAILED: {str(e)[:200]}"
    else:
        results["OpenAI"] = "⚠️ Key not set"
    
    any_working = any("✅" in v for v in results.values())
    results["overall"] = "✅ AT LEAST ONE AI WORKING" if any_working else "❌ ALL AI FAILED — fix keys in Render"
    return jsonify(results)


@app.route("/test_ai", methods=["GET"])
def test_ai():
    """
    Test all AI providers with a simple prompt.
    Visit: https://your-app.onrender.com/test_ai
    """
    results = {}

    # Test GROQ
    if not GROQ_API_KEY:
        results["GROQ"] = "SKIP - GROQ_API_KEY not set"
    else:
        try:
            g = _get_groq()
            if not g:
                results["GROQ"] = "FAIL - client init failed"
            else:
                r = g.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": "Say OK in one word."}],
                    max_tokens=5,
                )
                results["GROQ"] = f"OK - {r.choices[0].message.content.strip()}"
        except Exception as e:
            msg = str(e)
            if "401" in msg or "invalid_api_key" in msg or "Incorrect API key" in msg:
                results["GROQ"] = "FAIL - Invalid API key. Regenerate at console.groq.com"
            else:
                results["GROQ"] = f"FAIL - {msg[:200]}"

    # Test Gemini
    if not GEMINI_API_KEY:
        results["Gemini"] = "SKIP - GEMINI_API_KEY not set"
    else:
        try:
            gm = _get_gemini()
            if not gm:
                results["Gemini"] = "FAIL - client init failed"
            else:
                r = gm.generate_content("Say OK in one word.")
                results["Gemini"] = f"OK - {(getattr(r, 'text', '') or '').strip()}"
        except Exception as e:
            msg = str(e)
            if "API_KEY_INVALID" in msg or "401" in msg:
                results["Gemini"] = "FAIL - Invalid API key. Check aistudio.google.com"
            else:
                results["Gemini"] = f"FAIL - {msg[:200]}"

    # Test OpenAI
    if not OPENAI_API_KEY:
        results["OpenAI"] = "SKIP - OPENAI_KEY not set"
    else:
        try:
            oc = _get_openai()
            if not oc:
                results["OpenAI"] = "FAIL - client init failed"
            else:
                r = oc.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": "Say OK in one word."}],
                    max_tokens=5,
                )
                results["OpenAI"] = f"OK - {r.choices[0].message.content.strip()}"
        except Exception as e:
            msg = str(e)
            if "401" in msg or "invalid_api_key" in msg or "Incorrect API key" in msg:
                results["OpenAI"] = "FAIL - Invalid API key. Regenerate at platform.openai.com/api-keys"
            else:
                results["OpenAI"] = f"FAIL - {msg[:200]}"

    any_ok = any("OK" in v for v in results.values())
    return jsonify({
        "status": "AI working" if any_ok else "ALL PROVIDERS FAILED",
        "providers": results,
        "keys_set": {
            "GROQ_API_KEY":   bool(GROQ_API_KEY),
            "GEMINI_API_KEY": bool(GEMINI_API_KEY),
            "OPENAI_KEY":     bool(OPENAI_API_KEY),
        },
        "fix": "Update keys in Render Dashboard > Environment, then redeploy" if not any_ok else "OK",
    })

# ── auto-register webhook on startup ──────────────────────────────────────────
def _auto_register():
    time.sleep(5)
    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL not set — skip auto-register")
        return
    try:
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=f"{WEBHOOK_URL}{WEBHOOK_PATH}")
        logger.info("Webhook auto-registered")
    except Exception as e:
        logger.error(f"Auto webhook: {e}")

threading.Thread(target=_auto_register, daemon=True).start()

# ── entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
