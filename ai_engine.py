"""AI engine for AutoAI Advisory.

This module intentionally keeps provider imports lazy so the bot can boot even
when an optional AI SDK or API key is unavailable.
"""
import os
import logging
import threading
from datetime import date, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_GROQ_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]
_groq_client = None
_groq_key = ""
_gemini_model = None
_gemini_key = ""
_openai_client = None
_openai_key = ""
_CTX_CACHE = {"text": "", "ts": 0.0}
_CTX_LOCK = threading.Lock()


def _key(name: str) -> str:
    return os.getenv(name, "").strip()


def ai_available() -> bool:
    return bool(_key("GROQ_API_KEY") or _key("GEMINI_API_KEY") or
                _key("OPENAI_KEY") or _key("ASKFUZZ_API_KEY"))


def _get_groq():
    global _groq_client, _groq_key
    key = _key("GROQ_API_KEY")
    if not key:
        return None
    if _groq_client is None or key != _groq_key:
        try:
            from groq import Groq
            _groq_client, _groq_key = Groq(api_key=key), key
        except Exception as exc:
            logger.warning("Groq initialization failed: %s", exc)
            _groq_client = None
    return _groq_client


def _get_gemini():
    global _gemini_model, _gemini_key
    key = _key("GEMINI_API_KEY")
    if not key:
        return None
    if _gemini_model is None or key != _gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=key)
            for model_name in ("gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"):
                try:
                    _gemini_model, _gemini_key = genai.GenerativeModel(model_name), key
                    break
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("Gemini initialization failed: %s", exc)
            _gemini_model = None
    return _gemini_model


def _get_openai():
    global _openai_client, _openai_key
    key = _key("OPENAI_KEY")
    if not key:
        return None
    if _openai_client is None or key != _openai_key:
        try:
            from openai import OpenAI
            _openai_client, _openai_key = OpenAI(api_key=key), key
        except Exception as exc:
            logger.warning("OpenAI initialization failed: %s", exc)
            _openai_client = None
    return _openai_client


def _call_ai(messages: list, max_tokens: int = 500, system: str = "") -> tuple:
    """Return (text, error); providers are tried in a predictable fallback order."""
    errors = []
    groq = _get_groq()
    if groq:
        payload = ([{"role": "system", "content": system}] if system else []) + messages
        for model in _GROQ_MODELS:
            try:
                response = groq.chat.completions.create(
                    model=model, messages=payload, max_tokens=max_tokens, temperature=0.1
                )
                text = (response.choices[0].message.content or "").strip()
                if text:
                    return text, ""
            except Exception as exc:
                errors.append(f"GROQ {model}: {str(exc)[:100]}")
    elif _key("GROQ_API_KEY"):
        errors.append("GROQ client unavailable")

    gemini = _get_gemini()
    if gemini:
        try:
            prompt = (system + "\n\n" if system else "") + "\n\n".join(
                f"{m.get('role', 'user').upper()}: {m.get('content', '')}" for m in messages
            )
            response = gemini.generate_content(prompt)
            text = (getattr(response, "text", "") or "").strip()
            if text:
                return text, ""
        except Exception as exc:
            errors.append(f"Gemini: {str(exc)[:100]}")

    openai = _get_openai()
    if openai:
        try:
            payload = ([{"role": "system", "content": system}] if system else []) + messages
            response = openai.chat.completions.create(
                model="gpt-4o-mini", messages=payload, max_tokens=max_tokens, temperature=0.1
            )
            text = (response.choices[0].message.content or "").strip()
            if text:
                return text, ""
        except Exception as exc:
            errors.append(f"OpenAI: {str(exc)[:100]}")

    if _key("ASKFUZZ_API_KEY"):
        try:
            question = messages[-1].get("content", "")
            response = requests.post(
                "https://api.askfuzz.ai/v1/query",
                json={"question": question, "context": "NSE India stock market", "market": "IN"},
                headers={"Authorization": f"Bearer {_key('ASKFUZZ_API_KEY')}"}, timeout=8,
            )
            text = response.json().get("answer", "").strip()
            if text:
                return text, ""
        except Exception as exc:
            errors.append(f"AskFuzz: {str(exc)[:100]}")

    if not errors:
        errors.append("No AI provider is configured")
    return "", "\n".join(errors)


def _friendly_ai_error(error: str) -> str:
    low = (error or "").lower()
    if "429" in low or "rate" in low:
        return "⏳ AI provider rate limit reached. Please try again shortly."
    if "key" in low or "401" in low or "authentication" in low:
        return "❌ AI provider authentication failed. Check the configured API key."
    return "⚠️ AI temporarily unavailable. Please try again shortly."


def ai_insights(symbol: str, ltp: float, rsi: float, macd_line: float,
                trend: str, pe: str, roe: str, atr: float = 0.0,
                sl: float = 0.0, t1: float = 0.0) -> str:
    if not ai_available():
        return "⚠️ No AI key configured."
    zone = "OVERBOUGHT" if rsi > 70 else "OVERSOLD" if rsi < 30 else "NEUTRAL"
    prompt = (
        f"STOCK: {symbol} (NSE India)\nPRICE: Rs {ltp:.2f} exactly\n"
        f"SIGNAL: RSI={rsi:.1f} ({zone}) | MACD={macd_line:.2f} | Trend={trend}\n"
        f"Fundamentals: PE={pe} | ROE={roe}%\nATR={atr} | Stop={sl} | Target={t1}\n\n"
        "Respond in exactly five lines:\n"
        f"📌 {symbol} — [BULLISH/BEARISH/NEUTRAL]\n"
        "• Strength: [specific technical reason]\n• Risk: [specific risk]\n"
        "• Catalyst: [one catalyst]\n• Verdict: [BUY/HOLD/SELL] — [one sentence]\n"
        "• Horizon: [swing/positional/avoid]"
    )
    text, error = _call_ai([{"role": "user", "content": prompt}], 220,
                           "Use only supplied values. Never invent missing data.")
    return text or _friendly_ai_error(error)


def long_term_view(symbol: str, sector: str, ltp: float, pe, roe, de, div_y,
                   ema200: float, w52h: float, w52l: float,
                   peer_avg_pe: float = None, peer_avg_roe: float = None) -> str:
    if not ai_available():
        return ""
    prompt = (
        f"STOCK: {symbol} | Sector: {sector} | CMP: Rs {ltp:.2f}\n"
        f"EMA200: {ema200} | 52W: {w52l}-{w52h}\nPE: {pe or 'N/A'} | ROE: {roe or 'N/A'}% | "
        f"Debt/Equity: {de or 'N/A'} | Dividend Yield: {div_y or 'N/A'}%\n"
        f"Peer PE: {peer_avg_pe or 'N/A'} | Peer ROE: {peer_avg_roe or 'N/A'}\n\n"
        "Return exactly four lines:\nQuality: [line]\nValuation: [line]\n"
        "Watch for: [line]\nSuitability: [Core holding / Accumulate on dips / Watchlist only / Not for long-term] — [why]"
    )
    text, _ = _call_ai([{"role": "user", "content": prompt}], 220,
                       "Do not give short-term targets. Use only supplied numbers.")
    return text or ""


def structured_investment_outlook(symbol: str, company_name: str, ltp: float,
    day_high: float, day_low: float, y_high: float, y_low: float,
    trend_signal: str, rsi_14: float, macd_value: float, ema20: float,
    ema50: float, ema200: float, bb_lower: float, bb_upper: float,
    market_cap, pe_ttm, pe_forward, price_to_book, roe_percent,
    dividend_yield, debt_equity, nifty_outperformance_pts, news_bullets: str) -> str:
    """Generate the structured Markdown research outlook without fabricating N/A data."""
    if not ai_available():
        return "⚠️ No AI key configured."
    def val(value):
        return "N/A" if value is None or str(value).strip() in {"", "None", "Null"} else str(value)
    prompt = f"""You are an expert Automated Equity Research System for Indian NSE/BSE stocks.

RAW DATA:
Ticker: {company_name} ({symbol})
Price: Rs {val(ltp)}; Day range: {val(day_high)}-{val(day_low)}; 52-week range: {val(y_low)}-{val(y_high)}
Technicals: Trend={val(trend_signal)}, RSI={val(rsi_14)}, MACD={val(macd_value)}, EMA20={val(ema20)}, EMA50={val(ema50)}, EMA200={val(ema200)}, Bollinger={val(bb_lower)}-{val(bb_upper)}
Fundamentals: Market Cap={val(market_cap)} Cr, TTM PE={val(pe_ttm)}, Forward PE={val(pe_forward)}, PB={val(price_to_book)}, ROE={val(roe_percent)}%, Dividend Yield={val(dividend_yield)}%, Debt/Equity={val(debt_equity)}
Nifty outperformance: {val(nifty_outperformance_pts)} points
News:
{news_bullets or 'No material news available.'}

Rules: Never invent N/A or Null metrics. State that assessment is constrained when data is missing. If bullish/overbought technicals conflict with negative news, explicitly label Momentum vs Fundamental Divergence as high risk.

Output Markdown only, using exactly these sections and bullets:
---
## 📈 Technical Structure & Momentum Spectrum
- **Trend Diagnostics:** [EMA20/50/200 interpretation]
- **Oscillator Readings:** [RSI classification and entry implication]
- **Volatility Parameters:** [Bollinger squeeze or expansion]

---
## 🔎 Fundamental Quality & Valuation Framework
- **Pricing Multiple Assessment:** [PE/PB assessment]
- **Balance Sheet Health:** [ROE/debt assessment]

---
## 📰 Sentiment & Structural Catalyst Correlation
- **News Sentiment Mapping:** [catalysts and headwinds]
- **Benchmark Contrast:** [alpha versus Nifty]

---
## 💡 Comprehensive AI Outlook & Guardrails
- **Strategic Thesis:** [forward-looking thesis]
- **Execution Trajectory:** [targets based only on supplied high/low and defensive invalidation strategy]

---
*Disclaimer: This report is automatically compiled by a programmatic algorithmic pipeline from public market feeds and generative intelligence layer. It does not constitute formal financial, SEBI-registered, or legal investment advice.*"""
    text, error = _call_ai([{"role": "user", "content": prompt}], 700,
                           "Use only supplied data. Output only the requested Markdown.")
    return text or _friendly_ai_error(error)


def get_live_market_context(force: bool = False) -> str:
    with _CTX_LOCK:
        if _CTX_CACHE["text"] and not force:
            return _CTX_CACHE["text"]
    context = "Live market context is temporarily unavailable; state missing data clearly."
    with _CTX_LOCK:
        _CTX_CACHE.update(text=context, ts=0.0)
    return context


def ai_chat_respond(uid: int, user_message: str) -> str:
    if not ai_available():
        return "⚠️ No AI key configured."
    text, error = _call_ai([{"role": "user", "content": user_message}], 450,
                           "You are AutoAI Advisory, an Indian NSE/BSE research assistant. Use supplied data only.")
    return text or _friendly_ai_error(error)


def ai_topic_respond(topic_prompt: str) -> str:
    if not ai_available():
        return "⚠️ No AI key configured."
    text, error = _call_ai([{"role": "user", "content": topic_prompt}], 400,
                           "You are an Indian equity analyst. Be precise and never invent values.")
    return text or _friendly_ai_error(error)


def add_to_chat(uid: int, role: str, content: str):
    return None


def clear_chat(uid: int):
    return None


AI_CHAT_TOPICS = {
    "🔍 Stock Analysis": "Provide a structured Indian stock analysis using exact available data.",
    "📊 Nifty Valuation": "Provide a Nifty 50 valuation analysis using exact available data.",
    "💎 Fundamental Picks": "Identify fundamental picks only from supplied data.",
    "📈 Nifty Update": "Provide a Nifty technical update using exact available data.",
}
AI_CHAT_TOPIC_KEYS = set(AI_CHAT_TOPICS.keys())


def test_ai_providers() -> dict:
    status = {"GROQ": "SKIP", "Gemini": "SKIP", "OpenAI": "SKIP", "AskFuzz": "SKIP"}
    status["_status"] = "✅ AI CONFIGURED" if ai_available() else "❌ ALL FAILED"
    return status


def debug_ai_status() -> dict:
    keys = ["GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_KEY", "ASKFUZZ_API_KEY",
            "TAVILY_API_KEY", "FINNHUB_API_KEY", "ALPHA_VANTAGE_KEY"]
    return {"keys": {key: ("set" if _key(key) else "MISSING") for key in keys},
            "ai_available": ai_available(), "groq_models": _GROQ_MODELS}


def fetch_news(symbol: str) -> str:
    return ""


def fetch_market_news() -> str:
    return ""
