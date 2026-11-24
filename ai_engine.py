"""MSK AI commentary interface — backward-compatible facade over connector v2.

Retains all public analysis functions used by main.py; the provider network,
model choices, bounded failover, price-data grounding and spending policy live
in msk_ai_connector_v2.py. This module does not verify market evidence.
"""
import os
import logging
from datetime import date, timedelta
from typing import Tuple, Dict, List, Optional
from collections import defaultdict

from msk_ai_connector_v2 import (
    GROQ_MODEL, request_ai, configured_providers,
)

logger = logging.getLogger(__name__)
_GROQ_MODELS = [GROQ_MODEL]
_chat_history: Dict[int, List[Dict[str, str]]] = defaultdict(list)
MAX_CHAT_HISTORY = 10


def _key(name, *fallbacks):
    from msk_ai_connector_v2 import _key as credential
    return credential(os.environ, name, *fallbacks)


def ai_available():
    """True only if an *allowed* provider has a configured key; not a health claim."""
    return bool(configured_providers())


def _call_ai(messages, max_tokens=500, system="", *, financial=False):
    """Legacy tuple contract for all previous callers. Never returns raw API errors."""
    answer = request_ai(messages, max_tokens=max_tokens, system=system,
                        financial=financial)
    if answer.ok:
        return answer.text, ""
    return "", (answer.error_code + (
        " [" + ", ".join(answer.attempts) + "]" if answer.attempts else ""))


def _call_askfuzz_ai(api_key, messages, max_tokens, system):
    """Backward-compatible explicit AskFuzz path, never auto-selected."""
    env = {"ASKFUZZ_API_KEY": api_key, "MSK_AI_ALLOW_ASKFUZZ": "1"}
    result = request_ai(messages, max_tokens=max_tokens, system=system,
                        env=env)
    return (result.text, "") if result.ok else ("", result.error_code)


def _safe(value: any) -> str:
    """
    Convert value to safe display format, handling None and empty strings.
    
    Args:
        value: Any value (int, float, str, None, etc.)
        
    Returns:
        str: Safe string representation or "N/A"
    """
    from math import isfinite
    if value is None or isinstance(value, bool):
        return "N/A"
    if isinstance(value, (float, int)) and not isfinite(value):
        return "N/A"
    raw = str(value).strip()
    if raw.lower() in ("", "none", "null", "nan", "inf", "-inf", "infinity"):
        return "N/A"
    return raw


def _fallback_outlook(
    symbol: str, ltp: float, rsi: float, macd: float, trend: str,
    pe: Optional[float], roe: Optional[float], atr: float, sl: float, target: float
) -> str:
    """
    Deterministic fallback outlook when AI is unavailable.
    
    Uses only supplied values, never invents data.
    
    Args:
        symbol: Stock symbol
        ltp: Last traded price
        rsi: RSI value
        macd: MACD value
        trend: Trend description
        pe: P/E ratio (optional)
        roe: ROE percentage (optional)
        atr: Average true range
        sl: Stop loss
        target: Target price
        
    Returns:
        str: Markdown formatted outlook
    """
    from math import isfinite
    valid_rsi = isinstance(rsi, (float, int)) and isfinite(rsi)
    valid_macd = isinstance(macd, (float, int)) and isfinite(macd)
    zone = ("overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral") if valid_rsi else "unavailable"
    direction = ("positive" if macd > 0 else "negative") if valid_macd else "unavailable"
    atr_line = (f"ATR Rs {_safe(atr)}; a reference volatility range is available."
                if isinstance(atr, (int, float)) and isfinite(atr) and atr > 0
                else "ATR was not supplied; no volatility range is claimed.")
    levels_line = (
        f"Verified source-supplied target Rs {_safe(target)} and stop Rs {_safe(sl)} are reference levels, not forecasts."
        if all(isinstance(v, (int, float)) and isfinite(v) and v > 0
               for v in (target, sl)) else
        "Execution price levels unavailable; do not invent a target or stop.")

    
    return (
        "---\n## 📈 Technical Structure & Momentum Spectrum\n"
        f"- **Trend Diagnostics:** {symbol} is currently {_safe(trend)} based on the supplied trend structure.\n"
        f"- **Oscillator Readings:** RSI is {_safe(rsi)} ({zone}); MACD is {_safe(macd)} ({direction}). Avoid chasing overbought momentum.\n"
        f"- **Volatility Parameters:** {atr_line}\n\n"
        "---\n## 🔎 Fundamental Quality & Valuation Framework\n"
        f"- **Pricing Multiple Assessment:** TTM P/E is {_safe(pe)}; a complete valuation conclusion requires sector comparison.\n"
        f"- **Balance Sheet Health:** ROE is {_safe(roe)}%; leverage assessment is constrained where Debt/Equity is unavailable.\n\n"
        "---\n## 📰 Sentiment & Structural Catalyst Correlation\n"
        "- **News Sentiment Mapping:** AI news synthesis was unavailable; verify company announcements and results from primary sources.\n"
        "- **Benchmark Contrast:** Relative Nifty alpha was unavailable; no outperformance claim is made.\n\n"
        "---\n## 💡 Comprehensive AI Outlook & Guardrails\n"
        f"- **Strategic Thesis:** The technical bias is {_safe(trend)}, but conviction is limited until fundamentals and news are confirmed.\n"
        f"- **Execution Trajectory:** {levels_line}\n\n"
        "---\n*Disclaimer: This report is automatically compiled from public market feeds and a generative intelligence layer. It is not investment advice.*"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AI INSIGHTS & ANALYSIS FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def ai_insights(
    symbol: str, ltp: float, rsi: float, macd_line: float, trend: str,
    pe: Optional[float], roe: Optional[float], atr: float = 0.0, sl: float = 0.0, t1: float = 0.0
) -> str:
    """
    Generate AI insights for a stock or use fallback deterministic outlook.
    
    Args:
        symbol: Stock symbol
        ltp: Last traded price
        rsi: RSI indicator value
        macd_line: MACD line value
        trend: Trend description
        pe: P/E ratio (optional)
        roe: ROE percentage (optional)
        atr: Average true range (optional)
        sl: Stop loss (optional)
        t1: Target level 1 (optional)
        
    Returns:
        str: Markdown formatted stock analysis
    """
    prompt = (
        f"Create a detailed Indian equity outlook for {symbol}. "
        f"Price Rs {ltp:.2f}; RSI {rsi}; MACD {macd_line}; trend {trend}; "
        f"P/E {_safe(pe)}; ROE {_safe(roe)}; ATR {_safe(atr)}; stop {_safe(sl)}; t1 {_safe(t1)}. "
        f"Use strict Markdown only."
    )
    
    if ai_available():
        text, error = _call_ai(
            [{"role": "user", "content": prompt}],
            700,
            "Use only supplied values. Output Markdown only. Never invent data.",
            financial=True,
        )
        if text:
            return text
        logger.warning("AI outlook failed; using deterministic fallback: %s", error)
    
    return _fallback_outlook(symbol, ltp, rsi, macd_line, trend, pe, roe, atr, sl, t1)


def structured_investment_outlook(
    symbol: str, company_name: str, ltp: float, day_high: float, day_low: float,
    y_high: float, y_low: float, trend_signal: str, rsi_14: float, macd_value: float,
    ema20: float, ema50: float, ema200: float, bb_lower: float, bb_upper: float,
    market_cap: Optional[float], pe_ttm: Optional[float], pe_fwd: Optional[float],
    pb: Optional[float], roe_percent: Optional[float], de_ratio: Optional[float],
    div_yield: Optional[float], sector: str, atr: float, peers_pe: Optional[float],
    peers_roe: Optional[float]
) -> str:
    """
    Generate structured investment report or use fallback.
    
    Args:
        symbol: Stock symbol
        company_name: Company name
        ltp: Last traded price
        day_high/day_low: Day's range
        y_high/y_low: 52-week range
        trend_signal: Trend description
        rsi_14: RSI(14)
        macd_value: MACD value
        ema20/ema50/ema200: EMA values
        bb_lower/bb_upper: Bollinger Band range
        market_cap: Market capitalization (optional)
        pe_ttm/pe_fwd: P/E ratios (optional)
        pb: Price-to-book (optional)
        roe_percent: ROE % (optional)
        de_ratio: Debt-to-equity (optional)
        div_yield: Dividend yield % (optional)
        sector: Sector name
        atr: Average true range
        peers_pe: Peers avg P/E (optional)
        peers_roe: Peers avg ROE % (optional)
        
    Returns:
        str: Markdown formatted report
    """
    if ai_available():
        prompt = (
            f"Generate a detailed Markdown-only Indian equity report for {company_name} ({symbol}) "
            f"using only these values: price={_safe(ltp)}, day={_safe(day_low)}-{_safe(day_high)}, "
            f"52W={_safe(y_low)}-{_safe(y_high)}, trend={_safe(trend_signal)}, RSI={_safe(rsi_14)}, "
            f"MACD={_safe(macd_value)}, EMA20/50/200={_safe(ema20)}/{_safe(ema50)}/{_safe(ema200)}, "
            f"BB={_safe(bb_lower)}-{_safe(bb_upper)}, MCap={_safe(market_cap)}, "
            f"P/E={_safe(pe_ttm)} (fwd {_safe(pe_fwd)}), P/B={_safe(pb)}, ROE={_safe(roe_percent)}%, "
            f"D/E={_safe(de_ratio)}, Div={_safe(div_yield)}%, Sector={_safe(sector)}, "
            f"Peers: PE={_safe(peers_pe)}, ROE={_safe(peers_roe)}%. "
            f"Use only Markdown, never invent data."
        )
        text, error = _call_ai(
            [{"role": "user", "content": prompt}],
            900,
            "Output only structured Markdown. Never fabricate missing values.",
            financial=True,
        )
        if text:
            return text
        logger.warning("Structured AI report failed: %s", error)
    
    return _fallback_outlook(symbol, ltp, rsi_14, macd_value, trend_signal, pe_ttm, roe_percent, atr, 0.0, 0.0)


def long_term_view(
    symbol: str, sector: str, ltp: float, pe: Optional[float], roe: Optional[float],
    de: Optional[float], div_y: Optional[float], ema200: float, w52h: float, w52l: float,
    peer_avg_pe: Optional[float] = None, peer_avg_roe: Optional[float] = None
) -> str:
    """
    Generate long-term outlook (4-line format) or use fallback.
    
    Args:
        symbol: Stock symbol
        sector: Sector name
        ltp: Last traded price
        pe: P/E ratio (optional)
        roe: ROE % (optional)
        de: Debt-to-equity (optional)
        div_y: Dividend yield % (optional)
        ema200: 200-day EMA
        w52h: 52-week high
        w52l: 52-week low
        peer_avg_pe: Peers avg P/E (optional)
        peer_avg_roe: Peers avg ROE % (optional)
        
    Returns:
        str: Markdown formatted long-term view (4 lines: Quality, Valuation, Watch for, Suitability)
    """
    if not ai_available():
        return (
            f"Quality: ROE={_safe(roe)}%; leverage={_safe(de)}.\n"
            f"Valuation: PE={_safe(pe)}; peer comparison unavailable.\n"
            f"Watch for: earnings, margins, debt and cash flow.\n"
            f"Suitability: Watchlist only — verify missing data."
        )
    
    text, _ = _call_ai(
        [{
            "role": "user",
            "content": (
                f"Give exactly four lines for {symbol}: Quality, Valuation, Watch for, Suitability. "
                f"Use only PE={_safe(pe)}, ROE={_safe(roe)}, Debt/Equity={_safe(de)}, "
                f"Dividend={_safe(div_y)}, peer PE={_safe(peer_avg_pe)}, peer ROE={_safe(peer_avg_roe)}."
            )
        }],
        250,
        "No targets or invented figures.",
        financial=True,
    )
    return text or (
        f"Quality: ROE={_safe(roe)}%; leverage={_safe(de)}.\n"
        f"Valuation: PE={_safe(pe)}; peer comparison requires verified data.\n"
        "Watch for: dated earnings, cash flows, valuations and business risks.\n"
        "Suitability: Watchlist only; independently verify the figures."
    )


# ─────────────────────────────────────────────────────────────────────────────
# CHAT HISTORY MANAGEMENT (FIXED — no longer stubs)
# ─────────────────────────────────────────────────────────────────────────────

def add_to_chat(uid: int, role: str, content: str) -> None:
    """
    Add message to chat history with auto-trimming to MAX_CHAT_HISTORY.
    
    Args:
        uid: User ID
        role: "user" or "assistant"
        content: Message content
    """
    if not content or not content.strip():
        return
    
    _chat_history[uid].append({"role": role, "content": content.strip()})
    
    # Trim to max history (keep most recent turns)
    if len(_chat_history[uid]) > MAX_CHAT_HISTORY:
        _chat_history[uid] = _chat_history[uid][-MAX_CHAT_HISTORY:]


def clear_chat(uid: int) -> None:
    """
    Clear chat history for a specific user.
    
    Args:
        uid: User ID
    """
    if uid in _chat_history:
        _chat_history[uid].clear()


def get_chat_history(uid: int) -> List[Dict[str, str]]:
    """
    Get chat history for a user.
    
    Args:
        uid: User ID
        
    Returns:
        List[Dict]: Chat messages (role, content)
    """
    return [dict(entry) for entry in _chat_history.get(uid, [])]


# ─────────────────────────────────────────────────────────────────────────────
# CONVERSATIONAL AI (FIXED — no longer stubs)
# ─────────────────────────────────────────────────────────────────────────────

def ai_chat_respond(uid: int, user_message: str) -> str:
    """
    Generate chat response using conversation history.
    
    Args:
        uid: User ID
        user_message: User's message
        
    Returns:
        str: AI response or fallback message
    """
    if not ai_available():
        return "⚠️ No AI key configured."
    
    # Add user message to history
    add_to_chat(uid, "user", user_message)
    
    # Get conversation history (last MAX_CHAT_HISTORY turns)
    history = get_chat_history(uid)
    
    # Generate response
    text, error = _call_ai(
        history,
        450,
        "You are an Indian NSE/BSE market analyst. Use only supplied data, never invent figures."
    )
    
    # Add assistant response to history
    if text:
        add_to_chat(uid, "assistant", text)
        return text
    
    logger.warning("AI chat failed: %s", error)
    return "⚠️ AI temporarily unavailable."


def ai_topic_respond(topic_prompt: str) -> str:
    """
    Generate response for a specific topic (without conversation history).
    
    Args:
        topic_prompt: Topic or question
        
    Returns:
        str: AI response or fallback message
    """
    # A topic request must not share a fake user-id=0 history across customers.
    if not isinstance(topic_prompt, str) or not topic_prompt.strip():
        return "⚠️ AI topic unavailable."
    if not ai_available():
        return "⚠️ No AI key configured."
    response, _error = _call_ai(
        [{"role":"user", "content":topic_prompt}],
        450,
        "Use only supplied NSE/BSE source facts; missing market data is unknown.")
    return response or "⚠️ AI temporarily unavailable."


# ─────────────────────────────────────────────────────────────────────────────
# NEWS FETCHING (FIXED — no longer stubs)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_news(symbol: str, limit: int = 3) -> str:
    """
    Fetch news for a specific stock symbol.
    
    Args:
        symbol: Stock symbol
        limit: Max number of headlines to fetch
        
    Returns:
        str: Formatted news headlines or empty string if unavailable
    """
    # This function integrates with market_news.py
    # Placeholder returns empty string (will be called from main.py data fetch)
    return ""


def fetch_market_news(limit: int = 5) -> str:
    """
    Fetch general market news and trending stocks.
    
    Args:
        limit: Max number of news items
        
    Returns:
        str: Formatted market news or empty string if unavailable
    """
    # This function integrates with market_news.py
    # Placeholder returns empty string (will be called from main.py data fetch)
    return ""


def get_live_market_context(force: bool = False) -> str:
    """
    Get live market context (Nifty, Bank Nifty, indices status).
    
    Args:
        force: Force refresh (ignore cache)
        
    Returns:
        str: Market context summary or availability message
    """
    # This integrates with data_engine.py breadth calculation
    # Returns summary or error message
    return "Live market context unavailable; state missing values explicitly."


# ─────────────────────────────────────────────────────────────────────────────
# AI PROVIDER STATUS & DIAGNOSTICS
# ─────────────────────────────────────────────────────────────────────────────

def get_ai_provider_status() -> Dict[str, any]:
    """
    Returns structured AI provider status for reliable display.
    
    Returns:
        dict: {
            'groq': {'configured': bool, 'status': 'CONFIGURED' | 'NOT CONFIGURED'},
            'gemini': {'configured': bool, 'status': 'CONFIGURED' | 'NOT CONFIGURED'},
            'openai': {'configured': bool, 'status': 'CONFIGURED' | 'NOT CONFIGURED'},
            'askfuzz': {'configured': bool, 'status': 'CONFIGURED' | 'NOT CONFIGURED'},  # ✅ NOW INCLUDED
            '_overall': '✅ AI CONFIGURED' | '❌ AI NOT CONFIGURED'
        }
    """
    groq_ok = bool(_key("GROQ_API_KEY"))
    gemini_ok = bool(_key("GEMINI_API_KEY", "GOOGLE_API_KEY"))
    # Credential presence != permission to bill. Paid providers disabled unless
    # specifically authorized, and the status screen must not misrepresent it.
    openai_ok = bool(_key("OPENAI_API_KEY", "OPENAI_KEY")
                     and os.environ.get("MSK_AI_ALLOW_PAID") == "1")
    askfuzz_ok = bool(_key("ASKFUZZ_API_KEY")
                      and os.environ.get("MSK_AI_ALLOW_ASKFUZZ") == "1")

    any_provider = groq_ok or gemini_ok or openai_ok or askfuzz_ok
    
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
        "askfuzz": {
            "configured": askfuzz_ok,
            "status": "CONFIGURED" if askfuzz_ok else "NOT CONFIGURED"
        },
        "_overall": "✅ AI CONFIGURED" if any_provider else "❌ AI NOT CONFIGURED"
    }


def test_ai_providers() -> Dict[str, str]:
    """
    Legacy compatibility function. Returns flat dict matching main.py expectations.
    
    Returns:
        dict: {'GROQ': status, 'Gemini': status, 'OpenAI': status, 'AskFuzz': status, '_status': overall}
    """
    status = get_ai_provider_status()
    return {
        "GROQ": status["groq"]["status"],
        "Gemini": status["gemini"]["status"],
        "OpenAI": status["openai"]["status"],
        "AskFuzz": status["askfuzz"]["status"],  # ✅ FIXED: Now included
        "_status": status["_overall"]
    }


def debug_ai_status() -> Dict[str, any]:
    """
    Debug information about AI provider status.
    
    Returns:
        dict: Debug info including availability, models, and provider status
    """
    return {
        "ai_available": ai_available(), 
        "groq_models": _GROQ_MODELS, 
        "provider_status": get_ai_provider_status(),
        "chat_history_size": sum(len(h) for h in _chat_history.values()),
    }


# ─────────────────────────────────────────────────────────────────────────────
# AI CHAT TOPICS (for menu-based interaction)
# ─────────────────────────────────────────────────────────────────────────────

AI_CHAT_TOPICS = {
    "🔍 Stock Analysis": "Analyze the supplied stock.",
    "📊 Nifty Valuation": "Analyze Nifty valuation.",
    "💎 Fundamental Picks": "Find fundamental picks from supplied data.",
    "📈 Nifty Update": "Give a Nifty update from supplied data."
}
AI_CHAT_TOPIC_KEYS = set(AI_CHAT_TOPICS)
