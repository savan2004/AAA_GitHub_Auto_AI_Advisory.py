"""
main.py  —  AI Stock Advisory Telegram Bot (single-file deployment)
====================================================================
Start : gunicorn main:app --bind 0.0.0.0:8000 --workers 1 --timeout 120
Env   : TELEGRAM_TOKEN, WEBHOOK_URL, GROQ_API_KEY, GEMINI_API_KEY,
        OPENAI_KEY, ALPHA_VANTAGE_KEY, FINNHUB_API_KEY, TAVILY_API_KEY, PORT
"""

import os, time, logging, threading, requests
from collections import deque
from datetime import datetime

import pandas as pd
import yfinance as yf
from flask import Flask, request, jsonify
import telebot
from telebot import types

try:
    from yfinance.exceptions import YFRateLimitError
except ImportError:
    class YFRateLimitError(Exception):
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
WEBHOOK_URL       = os.getenv("WEBHOOK_URL", "").rstrip("/")
PORT              = int(os.getenv("PORT", 8000))
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

# ══ AI ENGINE ══════════════════════════════════════════════════════════════

# ── AI client globals (declared here, assigned lazily on first use) ───────────
_groq_client   = None
_gemini_model  = None
_openai_client = None

def _get_groq():
    global _groq_client
    if _groq_client is None and GROQ_API_KEY:
        try:
            from groq import Groq
            _groq_client = Groq(api_key=GROQ_API_KEY)
            logger.info("ai_engine: GROQ client ready")
        except Exception as e:
            logger.error(f"ai_engine: GROQ init failed — {e}")
    return _groq_client


def _get_gemini():
    global _gemini_model
    if _gemini_model is None and GEMINI_API_KEY:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            _gemini_model = genai.GenerativeModel("gemini-2.0-flash")
            logger.info("ai_engine: Gemini client ready")
        except Exception as e:
            logger.error(f"ai_engine: Gemini init failed — {e}")
    return _gemini_model


def _get_openai():
    global _openai_client
    if _openai_client is None and OPENAI_API_KEY:
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=OPENAI_API_KEY)
            logger.info("ai_engine: OpenAI client ready")
        except Exception as e:
            logger.error(f"ai_engine: OpenAI init failed — {e}")
    return _openai_client


def ai_available() -> bool:
    """True if at least one AI key is configured."""
    return bool(GROQ_API_KEY or GEMINI_API_KEY or OPENAI_API_KEY)


# ══════════════════════════════════════════════════════════════════════════════
# CORE AI CALL — GROQ → Gemini → OpenAI fallback
# ══════════════════════════════════════════════════════════════════════════════

def _call_ai(messages: list, max_tokens: int = 500,
             system: str = "") -> tuple[str, str]:
    """
    Call AI providers in order: GROQ → Gemini → OpenAI.

    Returns:
        (text, error_summary)
        text          — AI response if any provider succeeded, else ""
        error_summary — human-readable string of what failed and why
    """
    errors = []

    # ── 1. GROQ (Llama 3.3 70B — fastest, free) ───────────────────────────────
    groq = _get_groq()
    if not GROQ_API_KEY:
        errors.append("GROQ: key not set — add GROQ_API_KEY in Render env vars")
    elif not groq:
        errors.append("GROQ: client failed to init — check key format")
    else:
        try:
            msgs = ([{"role": "system", "content": system}]
                    if system else []) + messages
            r = groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=msgs,
                max_tokens=max_tokens,
                temperature=0.4,
            )
            text = (r.choices[0].message.content or "").strip()
            if text:
                logger.info("ai_engine: GROQ responded OK")
                return text, ""
            errors.append("GROQ: empty response")
        except Exception as e:
            msg = str(e)
            logger.error(f"ai_engine GROQ failed: {e}")
            if "401" in msg or "invalid_api_key" in msg or "Incorrect API key" in msg:
                errors.append("GROQ: INVALID KEY — regenerate at console.groq.com")
            elif "429" in msg or "rate" in msg.lower():
                errors.append("GROQ: rate limited — try again in 60s")
            else:
                errors.append(f"GROQ: {msg[:120]}")

    # ── 2. Gemini (2.0 Flash — fast, free) ────────────────────────────────────
    gemini = _get_gemini()
    if not GEMINI_API_KEY:
        errors.append("Gemini: key not set — add GEMINI_API_KEY in Render env vars")
    elif not gemini:
        errors.append("Gemini: client failed to init — check key")
    else:
        try:
            full = ((system + "\n\n") if system else "") + \
                   "\n".join(f"{m['role'].upper()}: {m['content']}"
                             for m in messages)
            r    = gemini.generate_content(full)
            text = (getattr(r, "text", "") or "").strip()
            if text:
                logger.info("ai_engine: Gemini responded OK")
                return text, ""
            errors.append("Gemini: empty response")
        except Exception as e:
            msg = str(e)
            logger.error(f"ai_engine Gemini failed: {e}")
            if "API_KEY_INVALID" in msg or "401" in msg:
                errors.append("Gemini: INVALID KEY — check aistudio.google.com")
            elif "leaked" in msg.lower() or "reported" in msg.lower():
                errors.append("Gemini: KEY LEAKED — generate a new key at aistudio.google.com")
            elif "429" in msg or "quota" in msg.lower():
                errors.append("Gemini: quota/rate limit exceeded")
            else:
                errors.append(f"Gemini: {msg[:120]}")

    # ── 3. OpenAI (GPT-4o-mini — paid fallback) ───────────────────────────────
    openai_client = _get_openai()
    if not OPENAI_API_KEY:
        errors.append("OpenAI: key not set — add OPENAI_KEY in Render env vars")
    elif not openai_client:
        errors.append("OpenAI: client failed to init — check key")
    else:
        try:
            msgs = ([{"role": "system", "content": system}]
                    if system else []) + messages
            r = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=msgs,
                max_tokens=max_tokens,
                temperature=0.4,
            )
            text = (r.choices[0].message.content or "").strip()
            if text:
                logger.info("ai_engine: OpenAI responded OK")
                return text, ""
            errors.append("OpenAI: empty response")
        except Exception as e:
            msg = str(e)
            logger.error(f"ai_engine OpenAI failed: {e}")
            if "401" in msg or "invalid_api_key" in msg or "Incorrect API key" in msg:
                errors.append("OpenAI: INVALID KEY — regenerate at platform.openai.com/api-keys")
            elif "429" in msg or "quota" in msg.lower():
                errors.append("OpenAI: rate/quota limit exceeded")
            else:
                errors.append(f"OpenAI: {msg[:120]}")

    return "", "\n".join(errors)


# ══════════════════════════════════════════════════════════════════════════════
# STOCK INSIGHTS  (brief snippet used in advisory card)
# ══════════════════════════════════════════════════════════════════════════════

def ai_insights(symbol: str, ltp: float, rsi: float, macd_line: float,
                trend: str, pe: str, roe: str) -> str:
    """
    3-bullet bullish + 2-bullet risk snippet for the stock analysis card.
    Called by build_advisory() in main.py.
    """
    if not ai_available():
        return "⚠️ No AI keys set — add GROQ_API_KEY in Render environment"

    prompt = (
        f"Give 3-bullet BULLISH factors and 2-bullet RISKS for {symbol} (NSE India).\n"
        f"Data: LTP ₹{ltp}, RSI {rsi}, MACD {'bullish' if macd_line > 0 else 'bearish'}, "
        f"Trend {trend}, PE {pe}, ROE {roe}%.\n"
        f"Format exactly:\nBULLISH:\n• ...\n• ...\n• ...\nRISKS:\n• ...\n• ..."
    )
    text, err = _call_ai(
        [{"role": "user", "content": prompt}],
        max_tokens=300,
        system="You are a concise Indian equity analyst. Be specific and data-driven.",
    )
    if text:
        return text
    if err:
        return f"⚠️ AI unavailable:\n{err}"
    return "⚠️ AI analysis temporarily unavailable"


# ══════════════════════════════════════════════════════════════════════════════
# NEWS FETCH  (Tavily → Alpha Vantage)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_news(symbol: str) -> str:
    """Fetch 2 recent headlines for a stock symbol."""
    if TAVILY_API_KEY:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY,
                      "query": f"{symbol} NSE India stock news",
                      "max_results": 3, "search_depth": "basic"},
                timeout=6,
            ).json()
            lines = [f"📰 {x['title'][:85]}"
                     for x in r.get("results", [])[:2] if x.get("title")]
            if lines:
                return "\n".join(lines)
        except Exception as e:
            logger.warning(f"ai_engine Tavily news {symbol}: {e}")

    if ALPHA_VANTAGE_KEY:
        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={"function": "NEWS_SENTIMENT",
                        "tickers": f"NSE:{symbol}",
                        "limit": 3, "apikey": ALPHA_VANTAGE_KEY},
                timeout=6,
            ).json()
            lines = [f"📰 {a['title'][:85]}"
                     for a in r.get("feed", [])[:2] if a.get("title")]
            if lines:
                return "\n".join(lines)
        except Exception as e:
            logger.warning(f"ai_engine AV news {symbol}: {e}")

    return ""


def fetch_market_news() -> str:
    """Fetch general Indian market headlines."""
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
            logger.warning(f"ai_engine Tavily market news: {e}")

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
            logger.warning(f"ai_engine AV market news: {e}")

    return "\n".join(headlines) if headlines else ""


# ══════════════════════════════════════════════════════════════════════════════
# AI CHAT — Live Market Q&A
# ══════════════════════════════════════════════════════════════════════════════

# Conversation history per user (last 12 messages = 6 exchanges)
_chat_history: dict = {}


def add_to_chat(uid: int, role: str, content: str):
    if uid not in _chat_history:
        _chat_history[uid] = []
    _chat_history[uid].append({"role": role, "content": content})
    _chat_history[uid] = _chat_history[uid][-12:]


def get_chat_history(uid: int) -> list:
    return _chat_history.get(uid, [])


def clear_chat(uid: int):
    _chat_history.pop(uid, None)


CHAT_SYSTEM = """You are an expert Indian stock market AI assistant with access to LIVE market data.
You specialize in:
1. NIFTY VALUATION — PE analysis, fair value, over/undervalued assessment
2. FUNDAMENTAL PICKS — stocks with strong ROE, low PE, solid balance sheet
3. NIFTY UPDATE — index levels, trend, support/resistance, weekly outlook
4. TECHNICAL SWING TRADES — entry zone, target 1, target 2, stop loss
5. OPTION TRADES — strike, expiry, entry premium, target, SL for Nifty/BankNifty

RULES:
- Always reference the live data provided. Quote specific numbers.
- For swing trades: stock name, entry zone, T1, T2, SL, timeframe.
- For options: index, CE/PE, strike, expiry, entry premium, target, SL.
- Be specific. No vague answers.
- End with: ⚠️ Educational only. Not SEBI-registered advice."""

# Quick-topic buttons and their full prompts
AI_CHAT_TOPICS: dict[str, str] = {
    "📊 Nifty Valuation":
        "What is the current Nifty 50 PE ratio? Is it overvalued or undervalued historically? "
        "Give specific numbers, compare to 10-year average, and give your assessment.",

    "💎 Fundamental Picks":
        "Based on current market conditions, give me 3 fundamentally strong NSE stocks. "
        "Criteria: PE < 25, ROE > 15%, low debt, consistent earnings growth. "
        "For each: name, current price range, key metrics, and why it's attractive now.",

    "📈 Nifty Update":
        "Give me a complete Nifty 50 technical update using the live data provided. "
        "Include: current level, trend direction, key support levels, key resistance levels, "
        "RSI reading, and your outlook for the next 5–7 trading days.",

    "🎯 Technical Swing Trade":
        "Give me 2 specific technical swing trade setups for NSE stocks right now. "
        "For each: stock name, current price, entry zone, target 1, target 2, stop loss, "
        "expected timeframe, and the technical reason for the setup.",

    "⚡ Option Trade":
        "Give me a specific option trade for Nifty or BankNifty for the current week expiry. "
        "Include: which index, CE or PE, specific strike price, current premium estimate, "
        "target premium, stop loss premium, max risk in rupees, and your reasoning.",
}

AI_CHAT_TOPIC_KEYS: set = set(AI_CHAT_TOPICS.keys())


def get_live_market_context() -> str:
    """
    Build a real-time snapshot of Nifty + top stocks to inject into AI prompts.
    Runs fast — uses cached yfinance data where possible.
    """
    import yfinance as yf

    # Import compute functions from main only if available, else skip
    try:
        from data_engine import fetch_history, compute_rsi
    except ImportError:
        fetch_history = None
        compute_rsi   = None

    from datetime import datetime
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
            w_h  = round(float(df["High"].max()), 2)
            w_l  = round(float(df["Low"].min()),  2)
            lines.append(f"NIFTY 50: {ltp:,.2f} ({chg:+.2f}%) | Day H/L: {h}/{l}")
            lines.append(f"NIFTY 5D Range: {w_l} – {w_h}")
    except Exception:
        lines.append("NIFTY 50: data unavailable")

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
            lines.append(f"NIFTY PE: {round(float(pe), 1)} "
                         f"(10yr avg ~20 | expensive >22 | cheap <18)")
    except Exception:
        pass

    # Top 8 stocks snapshot
    snap = []
    top8 = ["RELIANCE","TCS","HDFCBANK","INFY",
            "ICICIBANK","SBIN","BAJFINANCE","TATAMOTORS"]

    for sym in top8:
        try:
            ticker = f"{sym}.NS"
            df = yf.Ticker(ticker).history(period="5d", interval="1d")
            if df.empty or len(df) < 2:
                continue
            ltp  = round(float(df["Close"].iloc[-1]), 2)
            prev = round(float(df["Close"].iloc[-2]), 2)
            chg  = round((ltp - prev) / prev * 100, 2)
            # Simple RSI without importing data_engine
            close = df["Close"]
            if len(close) >= 15:
                delta = close.diff()
                gain  = delta.clip(lower=0).rolling(14).mean()
                loss  = (-delta.clip(upper=0)).rolling(14).mean()
                rs    = gain / loss.replace(0, float("nan"))
                rsi_v = round(float((100 - 100 / (1 + rs)).iloc[-1]), 1)
            else:
                rsi_v = 50.0
            snap.append(f"{sym}:₹{ltp}({chg:+.1f}%)RSI:{rsi_v}")
        except Exception:
            pass

    if snap:
        lines.append("TOP STOCKS: " + "  ".join(snap))

    return "\n".join(lines)


def ai_chat_respond(uid: int, user_message: str) -> str:
    """
    Respond to a user's chat message with live market context.
    Maintains per-user conversation history for follow-up questions.
    """
    if not ai_available():
        return (
            "⚠️ <b>No AI keys configured.</b>\n\n"
            "Add at least one key in Render Dashboard → Environment:\n"
            "• <code>GROQ_API_KEY</code> — free at console.groq.com\n"
            "• <code>GEMINI_API_KEY</code> — free at aistudio.google.com"
        )

    market_ctx = get_live_market_context()
    system     = CHAT_SYSTEM + f"\n\nLIVE MARKET CONTEXT:\n{market_ctx}"
    history    = get_chat_history(uid)
    messages   = list(history) + [{"role": "user", "content": user_message}]

    text, err = _call_ai(messages, max_tokens=550, system=system)

    if text:
        add_to_chat(uid, "user",      user_message)
        add_to_chat(uid, "assistant", text)
        return text

    # Detailed error message so user knows exactly what to fix
    return (
        "❌ <b>All AI providers failed.</b>\n\n"
        f"<b>Details:</b>\n{err}\n\n"
        "<b>Fix:</b>\n"
        "1. Render Dashboard → Environment\n"
        "2. Update <code>GROQ_API_KEY</code> (free at console.groq.com)\n"
        "3. Save → Redeploy"
    )


# ══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTIC HELPERS  (used by Flask /test_ai and /debug_ai routes)
# ══════════════════════════════════════════════════════════════════════════════

def test_ai_providers() -> dict:
    """
    Live test all AI providers with a trivial prompt.
    Returns a dict suitable for jsonify().
    """
    results = {}

    # GROQ
    if not GROQ_API_KEY:
        results["GROQ"] = "SKIP — GROQ_API_KEY not set (free at console.groq.com)"
    else:
        try:
            g = _get_groq()
            if not g:
                results["GROQ"] = "FAIL — client did not initialize"
            else:
                r = g.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": "Say OK in one word."}],
                    max_tokens=5,
                )
                results["GROQ"] = f"OK — {r.choices[0].message.content.strip()}"
        except Exception as e:
            msg = str(e)
            if "401" in msg or "Incorrect API key" in msg:
                results["GROQ"] = "FAIL — Invalid key. Regenerate at console.groq.com"
            else:
                results["GROQ"] = f"FAIL — {msg[:200]}"

    # Gemini
    if not GEMINI_API_KEY:
        results["Gemini"] = "SKIP — GEMINI_API_KEY not set (free at aistudio.google.com)"
    else:
        try:
            gm = _get_gemini()
            if not gm:
                results["Gemini"] = "FAIL — client did not initialize"
            else:
                r = gm.generate_content("Say OK in one word.")
                results["Gemini"] = f"OK — {(getattr(r, 'text', '') or '').strip()[:20]}"
        except Exception as e:
            msg = str(e)
            if "leaked" in msg.lower():
                results["Gemini"] = "FAIL — KEY LEAKED. Generate new key at aistudio.google.com"
            elif "API_KEY_INVALID" in msg or "401" in msg:
                results["Gemini"] = "FAIL — Invalid key. Check aistudio.google.com"
            else:
                results["Gemini"] = f"FAIL — {msg[:200]}"

    # OpenAI
    if not OPENAI_API_KEY:
        results["OpenAI"] = "SKIP — OPENAI_KEY not set"
    else:
        try:
            oc = _get_openai()
            if not oc:
                results["OpenAI"] = "FAIL — client did not initialize"
            else:
                r = oc.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": "Say OK in one word."}],
                    max_tokens=5,
                )
                results["OpenAI"] = f"OK — {r.choices[0].message.content.strip()}"
        except Exception as e:
            msg = str(e)
            if "401" in msg or "Incorrect API key" in msg:
                results["OpenAI"] = "FAIL — Invalid key. Regenerate at platform.openai.com/api-keys"
            else:
                results["OpenAI"] = f"FAIL — {msg[:200]}"

    any_ok = any(v.startswith("OK") for v in results.values())
    results["_status"] = "AI WORKING" if any_ok else "ALL PROVIDERS FAILED"
    results["_fix"]    = (
        "Update keys in Render Dashboard → Environment → Save → Redeploy"
        if not any_ok else "No action needed"
    )
    return results


def debug_ai_status() -> dict:
    """Returns current key presence and client init status without making API calls."""
    return {
        "keys_configured": {
            "GROQ_API_KEY":   "set" if GROQ_API_KEY   else "MISSING",
            "GEMINI_API_KEY": "set" if GEMINI_API_KEY else "MISSING",
            "OPENAI_KEY":     "set" if OPENAI_API_KEY else "MISSING",
            "TAVILY_API_KEY": "set" if TAVILY_API_KEY else "MISSING",
            "ALPHA_VANTAGE":  "set" if ALPHA_VANTAGE_KEY else "MISSING",
        },
        "clients_initialized": {
            "groq":   "ready" if _get_groq()    else "not initialized",
            "gemini": "ready" if _get_gemini()  else "not initialized",
            "openai": "ready" if _get_openai()  else "not initialized",
        },
        "ai_available": ai_available(),
        "note": "Visit /test_ai to actually call each provider",
    }


# ══ PORTFOLIOS / WATCHLISTS ════════════════════════════════════════════════
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


# ══ IN-MEMORY STATE ════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# IN-MEMORY STATE
# ══════════════════════════════════════════════════════════════════════════════
_rate:         dict = {}
_user_state:   dict = {}
_user_history: dict = {}
_usage_stats:  dict = {}
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

# Portfolio-specific rate limiter — 1 scan per 3 minutes per user
_portfolio_last: dict = {}

def is_portfolio_rate_limited(uid: int) -> bool:
    """Prevent duplicate portfolio scans — minimum 180s between scans per user."""
    last = _portfolio_last.get(uid, 0)
    if time.time() - last < 180:
        remaining = int(180 - (time.time() - last))
        return remaining
    _portfolio_last[uid] = time.time()
    return 0

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
# DATA FETCHING — FIXED
# ══════════════════════════════════════════════════════════════════════════════


# ══ DATA FETCHING ═══════════════════════════════════════════════════════════
# Global rate-limit cooldown — shared across all fetch_history calls
_yf_rate_limited_until: float = 0.0

def fetch_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    """Fetch OHLCV with exponential backoff and global rate-limit cooldown."""
    global _yf_rate_limited_until
    key    = f"hist_{symbol}_{period}"
    cached = _cget(key)
    if cached is not None:
        return cached

    # Honour global cooldown set by any previous rate-limited call
    now = time.time()
    if now < _yf_rate_limited_until:
        wait = _yf_rate_limited_until - now
        logger.info(f"yfinance cooldown: sleeping {wait:.1f}s before {symbol}")
        time.sleep(wait)

    ticker   = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
    backoffs = [3, 12, 35]   # seconds between attempts (exponential)

    for attempt in range(3):
        try:
            df = yf.Ticker(ticker).history(
                period=period, interval="1d", auto_adjust=True
            )
            if df.empty:
                if attempt < 2:
                    time.sleep(backoffs[attempt])
                    continue
                return pd.DataFrame()
            if float(df["Close"].iloc[-1]) < 0.5:
                return pd.DataFrame()
            _cset(key, df)
            return df
        except YFRateLimitError:
            cooldown = backoffs[attempt] * 4
            logger.warning(f"Rate limited: {ticker}, cooling down {cooldown}s")
            _yf_rate_limited_until = time.time() + cooldown
            time.sleep(cooldown)
        except Exception as e:
            err = str(e)
            if "too many requests" in err.lower() or "rate limit" in err.lower():
                cooldown = backoffs[attempt] * 4
                logger.warning(f"Rate limited (str): {ticker}, cooling {cooldown}s")
                _yf_rate_limited_until = time.time() + cooldown
                time.sleep(cooldown)
            else:
                logger.error(f"History {ticker} attempt {attempt+1}: {e}")
                if attempt < 2:
                    time.sleep(backoffs[attempt])
    return pd.DataFrame()


def fetch_info(symbol: str) -> dict:
    """
    3-layer fundamental data fetch:
    1. yfinance fast_info  — price/52W/mcap (always works)
    2. yfinance .info      — PE/ROE/div (unreliable for NSE, try anyway)
    3. Alpha Vantage OVERVIEW — reliable PE/ROE fallback when .info fails
    """
    key    = f"info_{symbol}"
    cached = _cget(key)
    if cached is not None:
        return cached

    ticker_str = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
    t      = yf.Ticker(ticker_str)
    merged: dict = {}

    # Layer 1: fast_info — always reliable for price/mcap/52W
    try:
        fi = t.fast_info
        mapping = {
            "marketCap":                  getattr(fi, "market_cap",                 None),
            "fiftyTwoWeekHigh":           getattr(fi, "year_high",                  None),
            "fiftyTwoWeekLow":            getattr(fi, "year_low",                   None),
            "regularMarketPreviousClose": getattr(fi, "previous_close",             None),
            "regularMarketVolume":        getattr(fi, "three_month_average_volume", None),
            "averageVolume":              getattr(fi, "three_month_average_volume", None),
        }
        merged.update({k: v for k, v in mapping.items() if v is not None})
        logger.info(f"fast_info OK {symbol}: mcap={merged.get('marketCap')}")
    except Exception as e:
        logger.warning(f"fast_info {ticker_str}: {e}")

    # Layer 2: .info — has PE/ROE but unreliable for NSE stocks
    for attempt in range(2):
        try:
            info = t.info or {}
            if info and len(info) > 5:
                merged.update(info)
                has_pe = info.get("trailingPE") or info.get("forwardPE")
                logger.info(f".info OK {symbol}: {len(info)} keys, PE={info.get('trailingPE')}, ROE={info.get('returnOnEquity')}")
                if has_pe:
                    break
            else:
                logger.warning(f".info {symbol} attempt {attempt+1}: only {len(info)} keys")
            if attempt == 0:
                time.sleep(1.5)
        except Exception as e:
            logger.warning(f".info {ticker_str} attempt {attempt+1}: {e}")
            if attempt == 0:
                time.sleep(2)

    # Layer 3: Alpha Vantage OVERVIEW — fills PE/ROE when .info fails
    # Symbol formats tried in order: BSE:{sym}, {sym}.BSE, {sym}
    pe_missing = not (merged.get("trailingPE") or merged.get("forwardPE"))
    if pe_missing and ALPHA_VANTAGE_KEY:
        def _av(val, mult=1.0):
            try:
                v = float(val)
                return round(v * mult, 2) if v != 0 else None
            except (TypeError, ValueError):
                return None

        def _av_parse(r: dict) -> dict | None:
            if not r.get("PERatio") or r["PERatio"] in ("None", "0", "", None):
                return None
            return {
                "longName":         r.get("Name"),
                "sector":           r.get("Sector"),
                "industry":         r.get("Industry"),
                "trailingPE":       _av(r.get("PERatio")),
                "priceToBook":      _av(r.get("PriceToBookRatio")),
                "returnOnEquity":   _av(r.get("ReturnOnEquityTTM")),
                "trailingEps":      _av(r.get("EPS")),
                "dividendYield":    _av(r.get("DividendYield")),
                "marketCap":        _av(r.get("MarketCapitalization")),
                "fiftyTwoWeekHigh": _av(r.get("52WeekHigh")),
                "fiftyTwoWeekLow":  _av(r.get("52WeekLow")),
                "debtToEquity":     _av(r.get("DebtToEquityRatio")),
            }

        # Try multiple symbol formats — AV is inconsistent with NSE stocks
        av_symbol_formats = [f"BSE:{symbol}", f"{symbol}.BSE", symbol]
        for av_sym in av_symbol_formats:
            try:
                r = requests.get(
                    "https://www.alphavantage.co/query",
                    params={"function": "OVERVIEW", "symbol": av_sym,
                            "apikey": ALPHA_VANTAGE_KEY},
                    timeout=8,
                ).json()
                av_data = _av_parse(r)
                if av_data:
                    for k, v in av_data.items():
                        if v is not None and not merged.get(k):
                            merged[k] = v
                    logger.info(f"Alpha Vantage OK {symbol} (fmt={av_sym}): "
                                f"PE={av_data.get('trailingPE')}")
                    break
                else:
                    logger.debug(f"Alpha Vantage {symbol} fmt={av_sym}: no PE")
            except Exception as e:
                logger.warning(f"Alpha Vantage overview {symbol} fmt={av_sym}: {e}")
                break  # network error — don't retry other formats

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


# ══ TECHNICAL INDICATORS ════════════════════════════════════════════════════
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


# ══ FUNDAMENTALS ════════════════════════════════════════════════════════════
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


# ══ QUALITY SCORE ═══════════════════════════════════════════════════════════
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

# ══════════════════════════════════════════════════════════════════════════════
# STOCK ADVISORY BUILDER
# ══════════════════════════════════════════════════════════════════════════════


# ══ ADVISORY BUILDER ════════════════════════════════════════════════════════
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
# PORTFOLIO SCANNER
# ══════════════════════════════════════════════════════════════════════════════


# ══ PORTFOLIO / BREADTH / NEWS BUILDERS ═════════════════════════════════════
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
    for i, sym in enumerate(p["stocks"]):
        # Small delay between stocks to avoid yfinance rate limiting
        if i > 0:
            time.sleep(1.2)
        try:
            df = fetch_history(sym, period="1mo")
            if df.empty or len(df) < 2:
                lines.append(f"  • <b>{sym}</b>: ⚠️ No data")
                continue
            close  = df["Close"]
            ltp    = round(float(close.iloc[-1]), 2)
            prev   = round(float(close.iloc[-2]), 2)
            chg    = round(((ltp - prev) / prev) * 100, 2)
            rsi_v  = compute_rsi(close)
            # For portfolio we skip fetch_info to avoid extra rate limit hits.
            # Quality score from technicals only.
            trend  = ("BULLISH" if len(close) >= 3 and
                      float(close.iloc[-1]) > float(close.iloc[-3]) else "NEUTRAL")
            score_num, _ = quality_score({
                "pe": None, "pb": None, "roe": None, "div": None, "de": None
            }, rsi_v, trend)
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
            df = fetch_history(sym, period="1mo")
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


def build_market_news() -> str:
    """Fetch and format market news using ai_engine.fetch_market_news()."""
    headlines_text = fetch_market_news()
    if not headlines_text:
        return (
            "📰 <b>MARKET NEWS</b>\n\n"
            "⚠️ No news available. Set TAVILY_API_KEY or ALPHA_VANTAGE_KEY in Render env vars."
        )
    lines = [
        "📰 <b>MARKET NEWS</b>",
        f"📅 {datetime.now().strftime('%d-%b-%Y %H:%M')}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    lines.extend(headlines_text.split("\n"))
    lines += ["", "⚠️ Educational only. Not SEBI-registered advice."]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════════════════


# ══ KEYBOARDS + SEND ════════════════════════════════════════════════════════
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


# ══ BOT HANDLERS ════════════════════════════════════════════════════════════
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
    wait = is_portfolio_rate_limited(msg.from_user.id)
    if wait:
        send(msg.chat.id, f"⏳ Portfolio scan cooling down. Try again in {wait}s."); return
    send(msg.chat.id, "⏳ Scanning Conservative portfolio…\n⚠️ Takes ~30s — please wait, don't tap again.")
    try:
        send(msg.chat.id, build_portfolio("conservative"), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Conservative: {e}")
        send(msg.chat.id, "❌ Portfolio scan failed. Try again.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "⚖️ Moderate")
def btn_moderate(msg):
    wait = is_portfolio_rate_limited(msg.from_user.id)
    if wait:
        send(msg.chat.id, f"⏳ Portfolio scan cooling down. Try again in {wait}s."); return
    send(msg.chat.id, "⏳ Scanning Moderate portfolio…\n⚠️ Takes ~30s — please wait, don't tap again.")
    try:
        send(msg.chat.id, build_portfolio("moderate"), reply_markup=main_kb())
    except Exception as e:
        logger.error(f"Moderate: {e}")
        send(msg.chat.id, "❌ Portfolio scan failed. Try again.", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "🚀 Aggressive")
def btn_aggressive(msg):
    wait = is_portfolio_rate_limited(msg.from_user.id)
    if wait:
        send(msg.chat.id, f"⏳ Portfolio scan cooling down. Try again in {wait}s."); return
    send(msg.chat.id, "⏳ Scanning Aggressive portfolio…\n⚠️ Takes ~30s — please wait, don't tap again.")
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
    text  = msg.text.strip()
    uid   = msg.from_user.id
    state = get_state(uid)   # read state FIRST before any other logic

    # ── 1. Skip menu button labels already handled by specific handlers ────────
    if text.upper() in MENU_LABELS or text.upper() in AI_MENU_LABELS:
        return

    # ── 2. AI Chat mode: ALL free text → AI, regardless of content ────────────
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

    # ── 3. Validate as NSE symbol ──────────────────────────────────────────────
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
    set_state(uid, None)   # clear state after use

    # ── 4. Run stock analysis (always — state was analysis or default) ─────────
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


# ══ FLASK ROUTES ════════════════════════════════════════════════════════════
@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "ok", "service": "AI Stock Advisory Bot",
        "time": datetime.utcnow().isoformat() + "Z"
    })

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
    """Check which AI keys and clients are ready (no API calls)."""
    return jsonify(debug_ai_status())

@app.route("/test_ai", methods=["GET"])
def test_ai():
    """Live test all AI providers — visit in browser to diagnose key issues."""
    return jsonify(test_ai_providers())

@app.route("/debug_info/<symbol>", methods=["GET"])
def debug_info(symbol):
    """Show raw yfinance data for a symbol. Usage: /debug_info/SBIN"""
    symbol = symbol.upper().replace(".NS", "")
    ticker_str = f"{symbol}.NS"
    t = yf.Ticker(ticker_str)
    result = {"symbol": symbol, "ticker": ticker_str}
    try:
        fi = t.fast_info
        result["fast_info"] = {
            "market_cap": getattr(fi, "market_cap",    None),
            "year_high":  getattr(fi, "year_high",     None),
            "year_low":   getattr(fi, "year_low",      None),
            "prev_close": getattr(fi, "previous_close", None),
        }
    except Exception as e:
        result["fast_info_error"] = str(e)
    try:
        info = t.info or {}
        result["info_key_count"] = len(info)
        result["info_fundamentals"] = {
            "longName":       info.get("longName"),
            "sector":         info.get("sector"),
            "trailingPE":     info.get("trailingPE"),
            "forwardPE":      info.get("forwardPE"),
            "priceToBook":    info.get("priceToBook"),
            "returnOnEquity": info.get("returnOnEquity"),
            "dividendYield":  info.get("dividendYield"),
            "trailingEps":    info.get("trailingEps"),
            "marketCap":      info.get("marketCap"),
        }
    except Exception as e:
        result["info_error"] = str(e)
    return jsonify(result)

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
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
