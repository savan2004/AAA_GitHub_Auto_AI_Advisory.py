"""
ai_engine.py  —  AI Engine for Stock Advisory Bot
===================================================
Handles ALL AI logic independently from the bot/server code.

Responsibilities:
  - AI client management (GROQ, Gemini, OpenAI) with lazy init
  - Fallback chain: GROQ → Gemini → OpenAI
  - ai_insights()        — brief stock analysis snippet
  - ai_chat_respond()    — live market Q&A with conversation memory
  - get_live_market_context() — real-time Nifty + stock snapshot for AI
  - fetch_news()         — Tavily → Alpha Vantage news headlines
  - AI Chat topic definitions and prompts
  - /test_ai and /debug_ai diagnostic helpers

Usage in main.py:
    from ai_engine import (
        ai_insights, ai_chat_respond, fetch_news,
        get_live_market_context, ai_available,
        AI_CHAT_TOPICS, AI_CHAT_TOPIC_KEYS,
        add_to_chat, clear_chat,
        test_ai_providers, debug_ai_status,
    )
"""

import os
import logging
import time
import requests
import pandas as pd
import yfinance as yf   # kept only as last-resort fallback for index tickers
from datetime import datetime

logger = logging.getLogger(__name__)

# ── API keys (read from environment) ──────────────────────────────────────────
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY    = os.getenv("OPENAI_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
TAVILY_API_KEY    = os.getenv("TAVILY_API_KEY", "")

# ══════════════════════════════════════════════════════════════════════════════
# LAZY CLIENT INIT
# Clients created on first use so env vars are definitely loaded by then.
# ══════════════════════════════════════════════════════════════════════════════

_groq_client  = None
_gemini_model = None
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
    Uses yfinance directly — no dependency on main.py or data_engine.
    """
    from data_engine import _yahoo_v8_hist, get_hist, calc_rsi, batch_quotes
    lines = [f"=== LIVE DATA {datetime.now().strftime('%d-%b-%Y %H:%M IST')} ==="]

    # Nifty 50 — index symbols go through Yahoo v8 directly (data_engine handles it)
    try:
        df = _yahoo_v8_hist("^NSEI", period="5d")
        if df is None or len(df) < 2:
            df = yf.Ticker("^NSEI").history(period="5d")  # last resort
        if df is not None and len(df) >= 2:
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
        df = _yahoo_v8_hist("^NSEBANK", period="5d")
        if df is None or len(df) < 2:
            df = yf.Ticker("^NSEBANK").history(period="2d")
        if df is not None and len(df) >= 2:
            ltp  = round(float(df["Close"].iloc[-1]), 2)
            prev = round(float(df["Close"].iloc[-2]), 2)
            chg  = round((ltp - prev) / prev * 100, 2)
            lines.append(f"BANK NIFTY: {ltp:,.2f} ({chg:+.2f}%)")
    except Exception:
        pass

    # Nifty PE — from Yahoo v8 meta (no yfinance.info call needed)
    try:
        from data_engine import _yahoo_v8_quote
        q  = _yahoo_v8_quote("^NSEI")
        pe = q.get("pe") if q else None
        if pe:
            lines.append(f"NIFTY PE: {round(float(pe), 1)} "
                         f"(10yr avg ~20 | expensive >22 | cheap <18)")
    except Exception:
        pass

    # Top 8 stocks — use batch_quotes for rate-limit-safe fetching
    top8 = ["RELIANCE", "TCS", "HDFCBANK", "INFY",
            "ICICIBANK", "SBIN", "BAJFINANCE", "TATAMOTORS"]
    snap = []
    quotes = batch_quotes(top8)
    for sym in top8:
        try:
            info = quotes.get(sym)
            if not info or not info.get("price"):
                continue
            ltp  = round(float(info["price"]), 2)
            prev = info.get("prev_close")
            chg  = round((ltp - float(prev)) / float(prev) * 100, 2) if prev else 0.0
            # RSI from recent history (cached by data_engine — no extra HTTP call)
            df_hist = get_hist(sym, "5d")
            rsi_v   = calc_rsi(df_hist["Close"]) if not df_hist.empty else 50.0
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
