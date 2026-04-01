"""
ai_engine.py  —  AI Engine for Stock Advisory Bot (Fixed v4.1)
================================================================
FIXES:
  - Gemini model updated: gemini-2.0-flash → gemini-1.5-flash (stable)
  - All API keys re-read at call time (not just import time) so Render env
    vars are always picked up after a redeploy without a full restart
  - GROQ client created fresh each call — avoids stale init from bad key
  - Better error messages distinguish "key missing" vs "key invalid" vs "quota"
  - Finnhub added as news source fallback (FINNHUB_API_KEY env var)
"""

import os
import logging
import time
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime

logger = logging.getLogger(__name__)

# ── API keys — re-read every time so Render env updates are live ───────────
def _key(name: str) -> str:
    return os.getenv(name, "").strip()

# ══════════════════════════════════════════════════════════════════════════════
# LAZY CLIENT INIT — re-created if key changes
# ══════════════════════════════════════════════════════════════════════════════

_groq_client        = None
_groq_key_used      = ""
_gemini_model       = None
_gemini_key_used    = ""
_openai_client      = None
_openai_key_used    = ""


def _make_groq_client(api_key: str):
    """
    Safe Groq client factory.
    groq>=0.9 uses httpx which rejects the 'proxies' kwarg that some
    environments (Render, corporate proxies) inject via requests/urllib3.
    We patch it out before construction.
    """
    from groq import Groq
    import inspect, functools
    try:
        # Try normal init first
        return Groq(api_key=api_key)
    except TypeError as e:
        if "proxies" not in str(e):
            raise
        # Strip proxies from httpx transport — monkey-patch approach
        try:
            import httpx
            _orig_init = httpx.Client.__init__
            @functools.wraps(_orig_init)
            def _patched_init(self, *a, **kw):
                kw.pop("proxies", None)   # drop the offending kwarg
                _orig_init(self, *a, **kw)
            httpx.Client.__init__ = _patched_init
            client = Groq(api_key=api_key)
            httpx.Client.__init__ = _orig_init  # restore
            return client
        except Exception as patch_err:
            logger.warning(f"ai_engine: httpx patch failed ({patch_err}), trying groq without httpx")
            raise


def _get_groq():
    global _groq_client, _groq_key_used
    key = _key("GROQ_API_KEY")
    if not key:
        return None
    if _groq_client is None or key != _groq_key_used:
        try:
            _groq_client  = _make_groq_client(key)
            _groq_key_used = key
            logger.info("ai_engine: GROQ client ready")
        except Exception as e:
            logger.error(f"ai_engine: GROQ SDK init failed - {e}")
            _groq_client = None
    return _groq_client


def _get_gemini():
    global _gemini_model, _gemini_key_used
    key = _key("GEMINI_API_KEY")
    if not key:
        return None
    if _gemini_model is None or key != _gemini_key_used:
        try:
            import google.generativeai as genai
            genai.configure(api_key=key)
            # Use gemini-1.5-flash — stable, free tier, fast
            _gemini_model    = genai.GenerativeModel("gemini-1.5-flash")
            _gemini_key_used = key
            logger.info("ai_engine: Gemini client ready (gemini-1.5-flash)")
        except Exception as e:
            logger.error(f"ai_engine: Gemini init failed — {e}")
            _gemini_model = None
    return _gemini_model


def _get_openai():
    global _openai_client, _openai_key_used
    key = _key("OPENAI_KEY")
    if not key:
        return None
    if _openai_client is None or key != _openai_key_used:
        try:
            from openai import OpenAI
            _openai_client  = OpenAI(api_key=key)
            _openai_key_used = key
            logger.info("ai_engine: OpenAI client ready")
        except Exception as e:
            logger.error(f"ai_engine: OpenAI init failed — {e}")
            _openai_client = None
    return _openai_client


def ai_available() -> bool:
    return bool(_key("GROQ_API_KEY") or _key("GEMINI_API_KEY") or _key("OPENAI_KEY"))


# ══════════════════════════════════════════════════════════════════════════════
# CORE AI CALL — GROQ → Gemini → OpenAI fallback
# ══════════════════════════════════════════════════════════════════════════════

def _call_ai(messages: list, max_tokens: int = 500, system: str = "") -> tuple[str, str]:
    errors = []

    # ── 1. GROQ ───────────────────────────────────────────────────────────────
    groq_key = _key("GROQ_API_KEY")
    if not groq_key:
        errors.append("GROQ: key not set — add GROQ_API_KEY in Render env vars")
    else:
        groq = _get_groq()
        if not groq:
            errors.append("GROQ: client failed to init — check key format (should start with gsk_)")
        else:
            try:
                msgs = ([{"role": "system", "content": system}] if system else []) + messages
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

    # ── 2. Gemini ─────────────────────────────────────────────────────────────
    gemini_key = _key("GEMINI_API_KEY")
    if not gemini_key:
        errors.append("Gemini: key not set — add GEMINI_API_KEY in Render env vars")
    else:
        gemini = _get_gemini()
        if not gemini:
            errors.append("Gemini: client failed to init — check key at aistudio.google.com")
        else:
            try:
                full = ((system + "\n\n") if system else "") + \
                       "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
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
                elif "429" in msg or "quota" in msg.lower() or "Resource" in msg:
                    errors.append("Gemini: quota/rate limit exceeded — try again later")
                else:
                    errors.append(f"Gemini: {msg[:120]}")

    # ── 3. OpenAI ─────────────────────────────────────────────────────────────
    openai_key = _key("OPENAI_KEY")
    if not openai_key:
        errors.append("OpenAI: key not set — add OPENAI_KEY in Render env vars")
    else:
        openai_client = _get_openai()
        if not openai_client:
            errors.append("OpenAI: client failed to init — check key")
        else:
            try:
                msgs = ([{"role": "system", "content": system}] if system else []) + messages
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
# STOCK INSIGHTS
# ══════════════════════════════════════════════════════════════════════════════

def ai_insights(symbol: str, ltp: float, rsi: float, macd_line: float,
                trend: str, pe: str, roe: str) -> str:
    if not ai_available():
        return "⚠️ No AI keys set — add GROQ_API_KEY in Render environment vars"

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
# NEWS  (Tavily → Finnhub → Alpha Vantage)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_news(symbol: str) -> str:
    tavily_key = _key("TAVILY_API_KEY")
    if tavily_key:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": tavily_key,
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

    # Finnhub news fallback
    finnhub_key = _key("FINNHUB_API_KEY")
    if finnhub_key:
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": f"NSE:{symbol}", "from": "2024-01-01",
                        "to": datetime.now().strftime("%Y-%m-%d"), "token": finnhub_key},
                timeout=6,
            ).json()
            if isinstance(r, list):
                lines = [f"📰 {a['headline'][:85]}" for a in r[:2] if a.get("headline")]
                if lines:
                    return "\n".join(lines)
        except Exception as e:
            logger.warning(f"ai_engine Finnhub news {symbol}: {e}")

    alpha_key = _key("ALPHA_VANTAGE_KEY")
    if alpha_key:
        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={"function": "NEWS_SENTIMENT",
                        "tickers": f"NSE:{symbol}",
                        "limit": 3, "apikey": alpha_key},
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
    headlines = []
    tavily_key = _key("TAVILY_API_KEY")
    if tavily_key:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": tavily_key,
                      "query": "Indian stock market NSE Nifty news today",
                      "max_results": 5, "search_depth": "basic"},
                timeout=8,
            ).json()
            headlines = [f"📰 {x['title'][:90]}"
                         for x in r.get("results", [])[:5] if x.get("title")]
        except Exception as e:
            logger.warning(f"ai_engine Tavily market news: {e}")

    if not headlines:
        finnhub_key = _key("FINNHUB_API_KEY")
        if finnhub_key:
            try:
                r = requests.get(
                    "https://finnhub.io/api/v1/news",
                    params={"category": "general", "token": finnhub_key},
                    timeout=6,
                ).json()
                if isinstance(r, list):
                    headlines = [f"📰 {a['headline'][:90]}" for a in r[:5] if a.get("headline")]
            except Exception as e:
                logger.warning(f"ai_engine Finnhub market news: {e}")

    if not headlines:
        alpha_key = _key("ALPHA_VANTAGE_KEY")
        if alpha_key:
            try:
                r = requests.get(
                    "https://www.alphavantage.co/query",
                    params={"function": "NEWS_SENTIMENT",
                            "topics": "financial_markets",
                            "limit": 5, "apikey": alpha_key},
                    timeout=8,
                ).json()
                headlines = [f"📰 {a['title'][:90]}"
                             for a in r.get("feed", [])[:5] if a.get("title")]
            except Exception as e:
                logger.warning(f"ai_engine AV market news: {e}")

    return "\n".join(headlines) if headlines else ""


# ══════════════════════════════════════════════════════════════════════════════
# AI CHAT
# ══════════════════════════════════════════════════════════════════════════════

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
2. FUNDAMENTAL PICKS — use ONLY the fundamental data provided in context; never invent metrics
3. NIFTY UPDATE — index levels, trend, support/resistance, weekly outlook
4. TECHNICAL SWING TRADES — entry zone, target 1, target 2, stop loss
5. OPTION STRATEGIES — strategy name, strikes, direction, reasoning based on Nifty levels/trend

CRITICAL RULES:
- Use ONLY numbers from the live data context. Never hallucinate prices, PE, ROE or premiums.
- For fundamentals: pick stocks from the FUNDAMENTAL DATA section only. Quote exact figures given.
- For options: NEVER quote a specific premium price (you don't have live options chain data).
  Instead: recommend a STRATEGY (Bull Call Spread / Bear Put Spread / Long CE / Long PE / Straddle etc.)
  with strike selection based on Nifty spot, key levels, and trend. State why you chose that strike.
- For swing trades: stock name, entry zone, T1, T2, SL, timeframe.
- Keep responses under 400 words to avoid Telegram message limits.
- End with: ⚠️ Educational only. Not SEBI-registered advice."""

AI_CHAT_TOPICS: dict[str, str] = {
    "📊 Nifty Valuation":
        "Using the LIVE DATA provided, give a complete Nifty 50 valuation analysis. "
        "Quote the exact PE, PB, Div Yield from the data. Compare PE to 10-year historical avg (~21). "
        "Calculate fair value = Nifty EPS × 21. State clearly: overvalued / fairly valued / cheap. "
        "Include current Nifty level, % gap from fair value, and your investment stance.",

    "💎 Fundamental Picks":
        "Using ONLY the FUNDAMENTAL DATA section in the live context provided, "
        "identify the 3 best value stocks. For each stock in the data: "
        "show PE, ROE%, Debt/Equity, 52W position, and a one-line investment case. "
        "Rank them: Best Pick / Second Pick / Watch. "
        "Do NOT invent any numbers — use only what is in the context.",

    "📈 Nifty Update":
        "Using the LIVE DATA provided, give a complete Nifty 50 technical update. "
        "Quote exact: current level, day change%, RSI, key support (S1/S2), key resistance (R1/R2). "
        "State trend direction (bullish/bearish/sideways). "
        "Give specific support and resistance levels in numbers. "
        "Outlook for next 5–7 trading days with a price range.",

    "🎯 Technical Swing Trade":
        "Using the TOP STOCKS data in the live context, identify 2 swing trade setups. "
        "Pick stocks where RSI signals opportunity (oversold <40 for buy, overbought >65 for sell). "
        "For each: Stock name, current price, Entry zone, Target 1, Target 2, Stop Loss, "
        "Risk:Reward ratio, timeframe (3–7 days), and the technical reason.",

    "⚡ Option Trade": (
        "Based on Nifty level trend RSI and pivot levels in the LIVE DATA "
        "recommend an options STRATEGY for this week expiry. "
        "Format: STRATEGY(Bull Call Spread/Long PE/Bear Put Spread/Iron Condor) "
        "DIRECTION(Bullish/Bearish/Neutral) "
        "STRIKES(e.g. Buy 23000CE Sell 23300CE round to nearest 50) "
        "WHY(2-3 lines Nifty trend+RSI+key level) "
        "MAX RISK(in points not rupees) TARGET(in points profit) "
        "EXIT IF(Nifty crosses level X) "
        "CRITICAL: Do NOT quote any premium price you have no live options chain data."
    ),
}

AI_CHAT_TOPIC_KEYS: set = set(AI_CHAT_TOPICS.keys())


def _fetch_nifty_pe() -> dict:
    """
    Fetch official Nifty 50 PE, PB, Dividend Yield.

    Source priority (all logged at WARNING so failures are visible in Render logs):
      1. NSE India equity-stockIndices API  (requires cookie warmup)
      2. NSE India indices overview API     (lighter, no cookie needed)
      3. Screener.in Nifty 50 page          (scrape — PE visible in HTML)
      4. Yahoo Finance ^NSEI trailingPE     (last resort — often wrong, but logged)

    Returns dict: {pe, pb, div_yield, source} or {} on total failure.
    """
    _NSE_H = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36"),
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer":         "https://www.nseindia.com/",
    }

    def _parse_pe(val):
        """Return float or None — rejects 0, negative, implausible values."""
        try:
            v = float(val)
            if 8 < v < 60:   # sanity range for Nifty PE
                return round(v, 2)
        except Exception:
            pass
        return None

    # ── Source 1: NSE equity-stockIndices (cookie-based) ──────────────────────
    try:
        s = requests.Session()
        s.headers.update(_NSE_H)
        s.get("https://www.nseindia.com/", timeout=8)   # get cookies
        r = s.get(
            "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050",
            timeout=10,
        )
        if r.ok:
            meta = r.json().get("metadata", {})
            pe = _parse_pe(meta.get("pe"))
            if pe:
                logger.info(f"Nifty PE: {pe} [NSE equity-stockIndices]")
                return {"pe": pe,
                        "pb":        round(float(meta["pb"]), 2) if meta.get("pb") else "N/A",
                        "div_yield": round(float(meta["divYield"]), 2) if meta.get("divYield") else "N/A",
                        "source": "NSE"}
            logger.warning(f"NSE stockIndices returned no valid PE — raw meta: {meta}")
        else:
            logger.warning(f"NSE stockIndices HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"_fetch_nifty_pe NSE-stockIndices failed: {e}")

    # ── Source 2: NSE indices overview (lighter endpoint, no cookie needed) ───
    try:
        r = requests.get(
            "https://www.nseindia.com/api/allIndices",
            headers=_NSE_H, timeout=10,
        )
        if r.ok:
            for idx in r.json().get("data", []):
                if idx.get("index") == "NIFTY 50":
                    pe = _parse_pe(idx.get("pe") or idx.get("P/E"))
                    if pe:
                        logger.info(f"Nifty PE: {pe} [NSE allIndices]")
                        return {"pe": pe,
                                "pb":        _parse_pe(idx.get("pb") or idx.get("P/B")) or "N/A",
                                "div_yield": _parse_pe(idx.get("divYield")) or "N/A",
                                "source": "NSE-allIndices"}
            logger.warning("NSE allIndices: NIFTY 50 entry found but no valid PE")
        else:
            logger.warning(f"NSE allIndices HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"_fetch_nifty_pe NSE-allIndices failed: {e}")

    # ── Source 3: Screener.in — PE visible without login ──────────────────────
    try:
        r = requests.get(
            "https://www.screener.in/company/^NSEI/",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if r.ok:
            import re
            # Screener shows: Stock P/E  19.45
            m = re.search(r"Stock P/E[\D]*([\d]+\.[\d]+)", r.text)
            if m:
                pe = _parse_pe(m.group(1))
                if pe:
                    logger.info(f"Nifty PE: {pe} [Screener.in]")
                    return {"pe": pe, "pb": "N/A", "div_yield": "N/A", "source": "Screener"}
    except Exception as e:
        logger.warning(f"_fetch_nifty_pe Screener failed: {e}")

    # ── Source 4: Yahoo Finance ^NSEI trailingPE (last resort, often wrong) ───
    try:
        from data_engine import _yahoo_v8_quote
        q  = _yahoo_v8_quote("^NSEI")
        pe = _parse_pe(q.get("pe") if q else None)
        if pe:
            logger.warning(f"Nifty PE: {pe} [Yahoo — may be inaccurate, all NSE sources failed]")
            return {"pe": pe, "pb": "N/A", "div_yield": "N/A", "source": "Yahoo-fallback"}
        logger.warning("Yahoo ^NSEI also returned no valid PE")
    except Exception as e:
        logger.warning(f"_fetch_nifty_pe Yahoo failed: {e}")

    logger.error("_fetch_nifty_pe: ALL sources failed — Nifty PE unavailable")
    return {}


def get_live_market_context() -> str:
    from data_engine import _yahoo_v8_hist, _yahoo_v8_quote, get_hist, get_info, calc_rsi, batch_quotes
    lines = [f"=== LIVE DATA {datetime.now().strftime('%d-%b-%Y %H:%M IST')} ==="]

    # Nifty 50
    try:
        df = _yahoo_v8_hist("^NSEI", period="5d")
        if df is None or len(df) < 2:
            df = yf.Ticker("^NSEI").history(period="5d")
        if df is not None and len(df) >= 2:
            ltp  = round(float(df["Close"].iloc[-1]), 2)
            prev = round(float(df["Close"].iloc[-2]), 2)
            chg  = round((ltp - prev) / prev * 100, 2)
            h    = round(float(df["High"].iloc[-1]), 2)
            l    = round(float(df["Low"].iloc[-1]),  2)
            lines.append(f"NIFTY 50: {ltp:,.2f} ({chg:+.2f}%) | Day H/L: {h}/{l}")
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

    # Nifty PE — from NSE official API (most accurate), Yahoo as fallback
    try:
        pe_data = _fetch_nifty_pe()
        if pe_data:
            pe   = pe_data.get("pe")
            pb   = pe_data.get("pb")
            divy = pe_data.get("div_yield")
            src  = pe_data.get("source", "NSE")
            pe_line = f"NIFTY PE: {pe} | PB: {pb} | Div Yield: {divy}% [src:{src}]"
            # Valuation context for AI
            try:
                pe_f = float(pe)
                if   pe_f > 24: pe_line += " → EXPENSIVE (historical avg ~21)"
                elif pe_f > 22: pe_line += " → SLIGHTLY OVERVALUED (historical avg ~21)"
                elif pe_f > 19: pe_line += " → FAIRLY VALUED (historical avg ~21)"
                else:           pe_line += " → CHEAP / UNDERVALUED (historical avg ~21)"
            except Exception:
                pass
            lines.append(pe_line)
    except Exception:
        pass

    # Top 8 stocks
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
            df_hist = get_hist(sym, "5d")
            rsi_v   = calc_rsi(df_hist["Close"]) if not df_hist.empty else 50.0
            snap.append(f"{sym}:₹{ltp}({chg:+.1f}%)RSI:{rsi_v}")
        except Exception:
            pass

    if snap:
        lines.append("TOP STOCKS: " + "  ".join(snap))

    # ── Fundamental data for top 12 stocks (powers Fundamental Picks topic) ───
    FUND_STOCKS = [
        "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK",
        "SBIN","BAJFINANCE","ITC","TATAMOTORS","MARUTI","SUNPHARMA","LT",
    ]
    try:
        fund_lines = []
        for sym in FUND_STOCKS:
            try:
                info = get_info(sym)
                if not info:
                    continue
                price = info.get("price")
                pe_v  = info.get("pe")
                pb_v  = info.get("pb")
                roe_v = info.get("roe")
                eps_v = info.get("eps")
                h52   = info.get("high52")
                l52   = info.get("low52")
                roe_pct = None
                if roe_v is not None:
                    rv = float(roe_v)
                    roe_pct = round(rv * 100, 1) if abs(rv) <= 1 else round(rv, 1)
                pos52 = None
                if price and h52 and l52:
                    span = float(h52) - float(l52)
                    if span > 0:
                        pos52 = round((float(price) - float(l52)) / span * 100, 0)
                parts = [sym]
                if price:             parts.append(f"LTP:Rs{round(float(price),0):.0f}")
                if pe_v:              parts.append(f"PE:{round(float(pe_v),1)}")
                if pb_v:              parts.append(f"PB:{round(float(pb_v),1)}")
                if roe_pct:           parts.append(f"ROE:{roe_pct}%")
                if eps_v:             parts.append(f"EPS:{round(float(eps_v),1)}")
                if pos52 is not None: parts.append(f"52W:{pos52:.0f}%")
                if len(parts) > 2:
                    fund_lines.append(" | ".join(parts))
            except Exception:
                pass
        if fund_lines:
            lines.append("\nFUNDAMENTAL DATA (use ONLY these exact numbers for Fundamental Picks):")
            lines.extend(fund_lines)
    except Exception as ex:
        logger.warning(f"get_live_market_context fundamental section: {ex}")

    # ── Nifty options context ─────────────────────────────────────────────────
    try:
        df_opt = _yahoo_v8_hist("^NSEI", period="1mo")
        if df_opt is not None and len(df_opt) >= 15:
            c       = df_opt["Close"]
            rsi_n   = calc_rsi(c)
            ema20n  = round(float(c.ewm(span=20, adjust=False).mean().iloc[-1]), 0)
            spot_n  = round(float(c.iloc[-1]), 0)
            trend_n = "BULLISH" if spot_n > ema20n else "BEARISH"
            atm_n   = int(round(spot_n / 50) * 50)
            lines.append(
                f"\nNIFTY OPTIONS CONTEXT:"
                f" Spot={spot_n} ATM={atm_n}"
                f" EMA20={ema20n} RSI={round(rsi_n,1)} Trend={trend_n}"
                f" | CE strikes nearby: {atm_n} {atm_n+50} {atm_n+100}"
                f" | PE strikes nearby: {atm_n} {atm_n-50} {atm_n-100}"
                f" | IMPORTANT: Do NOT quote option premiums - suggest strategy and strikes only"
            )
    except Exception:
        pass

    return "\n".join(lines)


def ai_chat_respond(uid: int, user_message: str) -> str:
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

    text, err = _call_ai(messages, max_tokens=800, system=system)

    if text:
        add_to_chat(uid, "user",      user_message)
        add_to_chat(uid, "assistant", text)
        return text

    return (
        "❌ <b>All AI providers failed.</b>\n\n"
        f"<b>Details:</b>\n{err}\n\n"
        "<b>Fix:</b>\n"
        "1. Render Dashboard → Environment\n"
        "2. Update <code>GROQ_API_KEY</code> (free at console.groq.com)\n"
        "3. Save → Redeploy"
    )


# ══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTICS
# ══════════════════════════════════════════════════════════════════════════════

def test_ai_providers() -> dict:
    results = {}

    groq_key = _key("GROQ_API_KEY")
    if not groq_key:
        results["GROQ"] = "SKIP — GROQ_API_KEY not set (free at console.groq.com)"
    else:
        try:
            g = _get_groq()
            if not g:
                results["GROQ"] = "FAIL — client did not initialize (check key format: gsk_...)"
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

    gemini_key = _key("GEMINI_API_KEY")
    if not gemini_key:
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

    openai_key = _key("OPENAI_KEY")
    if not openai_key:
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
    return {
        "keys_configured": {
            "GROQ_API_KEY":   "set" if _key("GROQ_API_KEY")   else "MISSING",
            "GEMINI_API_KEY": "set" if _key("GEMINI_API_KEY") else "MISSING",
            "OPENAI_KEY":     "set" if _key("OPENAI_KEY")     else "MISSING",
            "TAVILY_API_KEY": "set" if _key("TAVILY_API_KEY") else "MISSING",
            "ALPHA_VANTAGE":  "set" if _key("ALPHA_VANTAGE_KEY") else "MISSING",
            "FINNHUB":        "set" if _key("FINNHUB_API_KEY") else "MISSING",
        },
        "clients_initialized": {
            "groq":   "ready" if _get_groq()   else "not initialized",
            "gemini": "ready" if _get_gemini() else "not initialized",
            "openai": "ready" if _get_openai() else "not initialized",
        },
        "ai_available": ai_available(),
        "note": "Visit /test_ai to actually call each provider",
    }
