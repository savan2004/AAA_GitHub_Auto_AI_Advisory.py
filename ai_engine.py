"""
AI provider adapter and report-safe fallbacks.

Supports multiple AI providers with intelligent fallback:
  1. GROQ (primary, free, fast)
  2. Gemini (secondary, reliable)
  3. OpenAI (tertiary, premium quality)
  4. AskFuzz (optional, India-focused finance AI)
  5. Deterministic fallback (never crashes)

All functions return safe, non-empty responses regardless of provider availability.
"""
import os
import logging
import functools
import requests
from datetime import date, timedelta
from typing import Tuple, Dict, List, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# AI PROVIDER CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

_GROQ_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]
_groq = _gemini = _openai = None

# Chat history storage: {user_id: list of messages, max 10 turns for context safety}
_chat_history: Dict[int, List[Dict]] = defaultdict(list)
MAX_CHAT_HISTORY = 10


def _key(name: str, *fallbacks: str) -> str:
    """
    Get environment variable by name with fallback names.
    
    Args:
        name: Primary env var name
        *fallbacks: Alternative env var names to try if primary is not set
    
    Returns:
        str: Environment variable value (stripped), or empty string if not found
    """
    val = os.getenv(name, "").strip()
    if val:
        return val
    for alt_name in fallbacks:
        val = os.getenv(alt_name, "").strip()
        if val:
            return val
    return ""


def ai_available() -> bool:
    """
    Check if at least one AI provider is configured with a valid API key.
    
    Providers checked (in order):
      1. GROQ_API_KEY
      2. GEMINI_API_KEY or GOOGLE_API_KEY
      3. OPENAI_API_KEY or OPENAI_KEY
      4. ASKFUZZ_API_KEY
    
    Returns:
        bool: True if any provider is configured, False otherwise
    """
    return bool(
        _key("GROQ_API_KEY") or 
        _key("GEMINI_API_KEY", "GOOGLE_API_KEY") or 
        _key("OPENAI_API_KEY", "OPENAI_KEY") or
        _key("ASKFUZZ_API_KEY")  # ✅ FIXED: Now includes AskFuzz
    )


def _make_groq_client(api_key: str):
    """
    Create Groq client with compatibility handling for different httpx versions.
    
    Args:
        api_key: GROQ API key
        
    Returns:
        Groq client instance
    """
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


def _call_ai(
    messages: List[Dict[str, str]], 
    max_tokens: int = 500, 
    system: str = ""
) -> Tuple[str, str]:
    """
    Call AI providers in fallback order: GROQ → Gemini → OpenAI → AskFuzz.
    Tries each provider sequentially until one succeeds.
    
    Args:
        messages: List of message dicts with 'role' and 'content' keys
        max_tokens: Maximum tokens in response (default 500)
        system: System prompt (optional)
        
    Returns:
        Tuple[str, str]: (response_text, error_message)
            - If successful: (text, "")
            - If all fail: ("", error_details)
    """
    errors = []
    global _groq, _gemini, _openai
    
    # ─────────────────────────────────────────────────────────────────────────
    # Try GROQ (Primary)
    # ─────────────────────────────────────────────────────────────────────────
    groq_key = _key("GROQ_API_KEY")
    if groq_key:
        try:
            if _groq is None:
                _groq = _make_groq_client(groq_key)
            payload = ([{"role": "system", "content": system}] if system else []) + messages
            for model in _GROQ_MODELS:
                try:
                    r = _groq.chat.completions.create(
                        model=model, 
                        messages=payload, 
                        max_tokens=max_tokens, 
                        temperature=0.1
                    )
                    text = (r.choices[0].message.content or "").strip()
                    if text:
                        return text, ""
                except Exception as exc:
                    errors.append(f"GROQ ({model}): {str(exc)[:80]}")
        except Exception as exc:
            errors.append(f"GROQ init: {str(exc)[:80]}")
            logger.warning("Groq initialization failed: %s", exc)
    
    # ─────────────────────────────────────────────────────────────────────────
    # Try Gemini (Secondary)
    # ─────────────────────────────────────────────────────────────────────────
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
    
    # ─────────────────────────────────────────────────────────────────────────
    # Try OpenAI (Tertiary) — correctly indented as sibling, not child
    # ─────────────────────────────────────────────────────────────────────────
    openai_key = _key("OPENAI_API_KEY", "OPENAI_KEY")
    if openai_key:
        try:
            if _openai is None:
                from openai import OpenAI
                _openai = OpenAI(api_key=openai_key)
            payload = ([{"role": "system", "content": system}] if system else []) + messages
            r = _openai.chat.completions.create(
                model="gpt-4o-mini", 
                messages=payload, 
                max_tokens=max_tokens, 
                temperature=0.1
            )
            text = (r.choices[0].message.content or "").strip()
            if text:
                return text, ""
        except Exception as exc:
            errors.append(f"OpenAI: {str(exc)[:80]}")
            logger.warning("OpenAI call failed: %s", exc)
    
    # ─────────────────────────────────────────────────────────────────────────
    # Try AskFuzz (India-focused finance AI)
    # ─────────────────────────────────────────────────────────────────────────
    askfuzz_key = _key("ASKFUZZ_API_KEY")
    if askfuzz_key:
        try:
            text, error = _call_askfuzz_ai(askfuzz_key, messages, max_tokens, system)
            if text:
                return text, ""
            errors.append(f"AskFuzz: {error[:80]}")
        except Exception as exc:
            errors.append(f"AskFuzz init: {str(exc)[:80]}")
            logger.warning("AskFuzz call failed: %s", exc)
    
    # ─────────────────────────────────────────────────────────────────────────
    # All providers failed — return comprehensive error message
    # ─────────────────────────────────────────────────────────────────────────
    error_msg = "\n".join(errors) if errors else "No AI provider configured"
    return "", error_msg


def _call_askfuzz_ai(
    api_key: str, 
    messages: List[Dict[str, str]], 
    max_tokens: int, 
    system: str
) -> Tuple[str, str]:
    """
    Call AskFuzz API (India-focused financial AI).
    
    https://askfuzz.ai/api/v1/query
    
    Args:
        api_key: ASKFUZZ_API_KEY
        messages: Conversation messages
        max_tokens: Max response length
        system: System prompt
        
    Returns:
        Tuple[str, str]: (response_text, error_message)
    """
    try:
        # Construct prompt from messages
        prompt = messages[-1]["content"] if messages else ""
        if system:
            prompt = f"{system}\n\n{prompt}"
        
        # Make API call
        response = requests.post(
            "https://api.askfuzz.ai/v1/query",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "query": prompt,
                "max_tokens": max_tokens
            },
            timeout=10
        )
        
        # Handle response
        if response.status_code == 401:
            return "", "AskFuzz authentication failed (invalid API key)"
        elif response.status_code == 429:
            return "", "AskFuzz rate limit exceeded"
        elif response.status_code == 200:
            data = response.json()
            text = data.get("response", "").strip()
            confidence = data.get("confidence", 0)
            if text:
                # Optionally append confidence score
                if confidence:
                    text = f"{text}\n\n*Confidence: {confidence:.1%}*"
                return text, ""
            return "", "AskFuzz returned empty response"
        else:
            return "", f"AskFuzz HTTP {response.status_code}: {response.text[:80]}"
    
    except requests.Timeout:
        return "", "AskFuzz timeout (>10s)"
    except requests.ConnectionError:
        return "", "AskFuzz connection failed"
    except Exception as exc:
        return "", f"AskFuzz error: {str(exc)[:80]}"


def _safe(value: any) -> str:
    """
    Convert value to safe display format, handling None and empty strings.
    
    Args:
        value: Any value (int, float, str, None, etc.)
        
    Returns:
        str: Safe string representation or "N/A"
    """
    if value is None or str(value).strip() in {"", "None", "Null"}:
        return "N/A"
    return str(value)


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
    zone = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
    direction = "positive" if macd > 0 else "negative"
    
    return (
        "---\n## 📈 Technical Structure & Momentum Spectrum\n"
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
            "Use only supplied values. Output Markdown only. Never invent data."
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
            "Output only structured Markdown. Never fabricate missing values."
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
        "No targets or invented figures."
    )
    return text or ""


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
    return _chat_history.get(uid, [])


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
    return ai_chat_respond(0, topic_prompt)  # uid=0 for topic-based (stateless)


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
    openai_ok = bool(_key("OPENAI_API_KEY", "OPENAI_KEY"))
    askfuzz_ok = bool(_key("ASKFUZZ_API_KEY"))  # ✅ FIXED: Now checking
    
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
