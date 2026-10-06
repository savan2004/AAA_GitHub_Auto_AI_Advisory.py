"""AI provider adapter and report-safe fallbacks."""
import os
import logging
import functools
import requests

logger = logging.getLogger(__name__)
_GROQ_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]
_groq = _gemini = _openai = None


def _key(name, *fallbacks):
    """
    Get environment variable by name with fallback names.
    
    Args:
        name: Primary env var name
        *fallbacks: Alternative env var names to try if primary is not set
    
    Returns:
        str: Environment variable value (stripped), or empty string
    """
    val = os.getenv(name, "").strip()
    if val:
        return val
    for alt_name in fallbacks:
        val = os.getenv(alt_name, "").strip()
        if val:
            return val
    return ""


def ai_available():
    """Check if at least one AI provider is configured with a valid API key."""
    return bool(
        _key("GROQ_API_KEY") or 
        _key("GEMINI_API_KEY", "GOOGLE_API_KEY") or 
        _key("OPENAI_API_KEY", "OPENAI_KEY")
    )


def _make_groq_client(api_key):
    """Create Groq client across httpx versions used by Render."""
    from groq import Groq
    try:
        return Groq(api_key=api_key)
    except TypeError as exc:
        if "proxies" not in str(exc).lower():
            raise
        import httpx
        original = httpx.Client.__init__

        @functools.wraps(original)
        def compatible_init(self, *args, **kwargs):
            kwargs.pop("proxies", None)
            return original(self, *args, **kwargs)

        httpx.Client.__init__ = compatible_init
        try:
            return Groq(api_key=api_key)
        finally:
            httpx.Client.__init__ = original


def _call_ai(messages, max_tokens=500, system=""):
    """
    Call AI providers in fallback order: GROQ → Gemini → OpenAI.
    Tries each provider sequentially until one succeeds.
    """
    errors = []
    global _groq, _gemini, _openai
    
    # Try GROQ
    groq_key = _key("GROQ_API_KEY")
    if groq_key:
        try:
            if _groq is None:
                _groq = _make_groq_client(groq_key)
            payload = ([{"role": "system", "content": system}] if system else []) + messages
            for model in _GROQ_MODELS:
                try:
                    r = _groq.chat.completions.create(model=model, messages=payload, max_tokens=max_tokens, temperature=0.1)
                    text = (r.choices[0].message.content or "").strip()
                    if text:
                        return text, ""
                except Exception as exc:
                    errors.append(f"GROQ ({model}): {str(exc)[:80]}")
        except Exception as exc:
            errors.append(f"GROQ init: {str(exc)[:80]}")
            logger.warning("Groq initialization failed: %s", exc)
    
    # Try Gemini
    gemini_key = _key("GEMINI_API_KEY", "GOOGLE_API_KEY")
    if gemini_key:
        try:
            if _gemini is None:
                import google.generativeai as genai
                genai.configure(api_key=gemini_key)
                _gemini = genai.GenerativeModel("gemini-1.5-flash")
            prompt = (system + "\n\n" if system else "") + messages[-1]["content"]
            r = _gemini.generate_content(prompt)
            text = (getattr(r, "text", "") or "").strip()
            if text:
                return text, ""
        except Exception as exc:
            errors.append(f"Gemini: {str(exc)[:80]}")
            logger.warning("Gemini call failed: %s", exc)
    
    # Try OpenAI (with fallback to OPENAI_KEY for backward compatibility)
    openai_key = _key("OPENAI_API_KEY", "OPENAI_KEY")
    if openai_key:
        try:
            if _openai is None:
                from openai import OpenAI
                _openai = OpenAI(api_key=openai_key)
            payload = ([{"role": "system", "content": system}] if system else []) + messages
            r = _openai.chat.completions.create(model="gpt-4o-mini", messages=payload, max_tokens=max_tokens, temperature=0.1)
            text = (r.choices[0].message.content or "").strip()
            if text:
                return text, ""
        except Exception as exc:
            errors.append(f"OpenAI: {str(exc)[:80]}")
            logger.warning("OpenAI call failed: %s", exc)
    
    error_msg = "\n".join(errors) if errors else "No AI provider configured"
    return "", error_msg


def _safe(value):
    """Convert value to safe display format, handling None and empty strings."""
    return "N/A" if value is None or str(value).strip() in {"", "None", "Null"} else str(value)


def _fallback_outlook(symbol, ltp, rsi, macd, trend, pe, roe, atr, sl, target):
    """Deterministic fallback outlook when AI is unavailable."""
    zone = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
    direction = "positive" if macd > 0 else "negative"
    return ("---\n## 📈 Technical Structure & Momentum Spectrum\n"
            f"- **Trend Diagnostics:** {symbol} is currently {_safe(trend)} based on the supplied trend structure.\n"
            f"- **Oscillator Readings:** RSI is {_safe(rsi)} ({zone}); MACD is {_safe(macd)} ({direction}). Avoid chasing overbought momentum.\n"
            f"- **Volatility Parameters:** ATR is Rs {_safe(atr)}, defining the available risk-band reference.\n\n"
            "---\n## 🔎 Fundamental Quality & Valuation Framework\n"
            f"- **Pricing Multiple Assessment:** TTM P/E is {_safe(pe)}; a complete valuation conclusion requires sector comparison.\n"
            f"- **Balance Sheet Health:** ROE is {_safe(roe)}%; leverage assessment is constrained where Debt/Equity is unavailable.\n\n"
            "---\n## 📰 Sentiment & Structural Catalyst Correlation\n"
            "- **News Sentiment Mapping:** AI news synthesis was unavailable; verify company announcements and results from primary sources.\n"
            "- **Benchmark Contrast:** Relative Nifty alpha was unavailable; no outperformance claim is made.\n\n"
            "---\n## 💡 Comprehensive AI Outlook & Guardrails\n"
            f"- **Strategic Thesis:** The technical bias is {_safe(trend)}, but conviction is limited until fundamentals and news are confirmed.\n"
            f"- **Execution Trajectory:** Reference target is Rs {_safe(target)} and protective stop is Rs {_safe(sl)}. If support fails, reduce exposure and wait for confirmation.\n\n"
            "---\n*Disclaimer: This report is automatically compiled from public market feeds and a generative intelligence layer. It is not investment advice.*")


def ai_insights(symbol, ltp, rsi, macd_line, trend, pe, roe, atr=0.0, sl=0.0, t1=0.0):
    """Generate AI insights or use fallback deterministic outlook."""
    prompt = f"Create a detailed Indian equity outlook for {symbol}. Price Rs {ltp:.2f}; RSI {rsi}; MACD {macd_line}; trend {trend}; P/E {_safe(pe)}; ROE {_safe(roe)}; ATR {_safe(atr)}; stop {_safe(sl)}; t1 {_safe(t1)}. Use strict Markdown only."
    if ai_available():
        text, error = _call_ai([{"role": "user", "content": prompt}], 700, "Use only supplied values. Output Markdown only.")
        if text:
            return text
        logger.warning("AI outlook failed; using deterministic fallback: %s", error)
    return _fallback_outlook(symbol, ltp, rsi, macd_line, trend, pe, roe, atr, sl, t1)


def structured_investment_outlook(symbol, company_name, ltp, day_high, day_low, y_high, y_low, trend_signal, rsi_14, macd_value, ema20, ema50, ema200, bb_lower, bb_upper, market_cap, pe_ttm, pe_fwd, pb, roe_percent, de_ratio, div_yield, sector, atr, peers_pe, peers_roe):
    """Generate structured investment report or use fallback."""
    if ai_available():
        prompt = f"Generate a detailed Markdown-only Indian equity report for {company_name} ({symbol}) using only these values: price={_safe(ltp)}, day={_safe(day_low)}-{_safe(day_high)}, 52W={_safe(y_low)}-{_safe(y_high)}, trend={_safe(trend_signal)}, RSI={_safe(rsi_14)}, MACD={_safe(macd_value)}, EMA20/50/200={_safe(ema20)}/{_safe(ema50)}/{_safe(ema200)}, BB={_safe(bb_lower)}-{_safe(bb_upper)}, MCap={_safe(market_cap)}, P/E={_safe(pe_ttm)} (fwd {_safe(pe_fwd)}), P/B={_safe(pb)}, ROE={_safe(roe_percent)}%, D/E={_safe(de_ratio)}, Div={_safe(div_yield)}%, Sector={_safe(sector)}, Peers: PE={_safe(peers_pe)}, ROE={_safe(peers_roe)}%. Use only Markdown, never invent data."
        text, error = _call_ai([{"role": "user", "content": prompt}], 900, "Output only structured Markdown. Never fabricate missing values.")
        if text:
            return text
        logger.warning("Structured AI report failed: %s", error)
    return _fallback_outlook(symbol, ltp, rsi_14, macd_value, trend_signal, pe_ttm, roe_percent, "N/A", "N/A", "N/A")


def long_term_view(symbol, sector, ltp, pe, roe, de, div_y, ema200, w52h, w52l, peer_avg_pe=None, peer_avg_roe=None):
    """Generate long-term outlook or use fallback."""
    if not ai_available():
        return f"Quality: ROE={_safe(roe)}%; leverage={_safe(de)}.\nValuation: PE={_safe(pe)}; peer comparison unavailable.\nWatch for: earnings, margins, debt and cash flow.\nSuitability: Watchlist only — verify missing data."
    text, _ = _call_ai([{"role": "user", "content": f"Give exactly four lines for {symbol}: Quality, Valuation, Watch for, Suitability. Use only PE={_safe(pe)}, ROE={_safe(roe)}, Debt/Equity={_safe(de)}, Dividend={_safe(div_y)}, peer PE={_safe(peer_avg_pe)}, peer ROE={_safe(peer_avg_roe)}."}], 250, "No targets or invented figures.")
    return text or ""


def get_live_market_context(force=False): 
    """Get live market context (stub)."""
    return "Live market context unavailable; state missing values explicitly."


def ai_chat_respond(uid, user_message):
    """Chat response handler with AI or fallback message."""
    if not ai_available(): 
        return "⚠️ No AI key configured."
    text, _ = _call_ai([{"role": "user", "content": user_message}], 450, "Indian NSE/BSE analyst. Use supplied data only.")
    return text or "⚠️ AI temporarily unavailable."


def ai_topic_respond(topic_prompt): 
    """Topic-based response handler."""
    return ai_chat_respond(0, topic_prompt)


def add_to_chat(uid, role, content): 
    """Add message to chat history (stub)."""
    pass


def clear_chat(uid): 
    """Clear chat history for user (stub)."""
    pass


AI_CHAT_TOPICS = {
    "🔍 Stock Analysis": "Analyze the supplied stock.",
    "📊 Nifty Valuation": "Analyze Nifty valuation.",
    "💎 Fundamental Picks": "Find fundamental picks from supplied data.",
    "📈 Nifty Update": "Give a Nifty update from supplied data."
}
AI_CHAT_TOPIC_KEYS = set(AI_CHAT_TOPICS)


def get_ai_provider_status():
    """
    Returns structured AI provider status for reliable display.
    
    Returns:
        dict: {
            'groq': {'configured': bool, 'status': 'CONFIGURED' | 'NOT CONFIGURED'},
            'gemini': {'configured': bool, 'status': 'CONFIGURED' | 'NOT CONFIGURED'},
            'openai': {'configured': bool, 'status': 'CONFIGURED' | 'NOT CONFIGURED'},
            '_overall': '✅ AI CONFIGURED' | '❌ AI NOT CONFIGURED'
        }
        
    Note: AskFuzz removed from status as it's non-functional and not actively used.
    """
    groq_ok = bool(_key("GROQ_API_KEY"))
    gemini_ok = bool(_key("GEMINI_API_KEY", "GOOGLE_API_KEY"))
    openai_ok = bool(_key("OPENAI_API_KEY", "OPENAI_KEY"))
    
    any_provider = groq_ok or gemini_ok or openai_ok
    
    return {
        "groq": {
            "configured": groq_ok,
            "status": "CONFIGURED" if groq_ok else "NOT CONFIGURED"
        },
        "gemini": {
            "configured": gemini_ok,
            "status": "CONFIGURED" if gemini_ok else "NOT CONFIGURED"
        },
        "openai": {
            "configured": openai_ok,
            "status": "CONFIGURED" if openai_ok else "NOT CONFIGURED"
        },
        "_overall": "✅ AI CONFIGURED" if any_provider else "❌ AI NOT CONFIGURED"
    }


def test_ai_providers():
    """
    Legacy compatibility function. Returns flat dict matching main.py expectations.
    Uses new structured status internally to ensure consistency.
    
    Removed AskFuzz from output since it's non-functional.
    """
    status = get_ai_provider_status()
    return {
        "GROQ": status["groq"]["status"],
        "Gemini": status["gemini"]["status"],
        "OpenAI": status["openai"]["status"],
        "_status": status["_overall"]
    }


def debug_ai_status(): 
    """Debug information about AI provider status."""
    return {
        "ai_available": ai_available(), 
        "groq_models": _GROQ_MODELS, 
        "provider_status": get_ai_provider_status()
    }


def fetch_news(symbol): 
    """Fetch news for symbol (stub)."""
    return ""


def fetch_market_news(): 
    """Fetch market news (stub)."""
    return ""
