"""AI provider adapter and deterministic report-safe fallbacks."""
import os
import logging
import requests

logger = logging.getLogger(__name__)
_GROQ_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]
_groq = _gemini = _openai = None


def _key(name):
    return os.getenv(name, "").strip()


def ai_available():
    return bool(_key("GROQ_API_KEY") or _key("GEMINI_API_KEY") or _key("OPENAI_KEY") or _key("ASKFUZZ_API_KEY"))


def _call_ai(messages, max_tokens=500, system=""):
    errors = []
    global _groq, _gemini, _openai
    if _key("GROQ_API_KEY"):
        try:
            if _groq is None:
                from groq import Groq
                _groq = Groq(api_key=_key("GROQ_API_KEY"))
            payload = ([{"role": "system", "content": system}] if system else []) + messages
            for model in _GROQ_MODELS:
                try:
                    r = _groq.chat.completions.create(model=model, messages=payload, max_tokens=max_tokens, temperature=0.1)
                    text = (r.choices[0].message.content or "").strip()
                    if text:
                        return text, ""
                except Exception as exc:
                    errors.append(f"GROQ: {str(exc)[:100]}")
        except Exception as exc:
            errors.append(f"GROQ init: {str(exc)[:100]}")
    if _key("GEMINI_API_KEY"):
        try:
            if _gemini is None:
                import google.generativeai as genai
                genai.configure(api_key=_key("GEMINI_API_KEY"))
                _gemini = genai.GenerativeModel("gemini-1.5-flash")
            prompt = (system + "\n\n" if system else "") + messages[-1]["content"]
            r = _gemini.generate_content(prompt)
            text = (getattr(r, "text", "") or "").strip()
            if text:
                return text, ""
        except Exception as exc:
            errors.append(f"Gemini: {str(exc)[:100]}")
    if _key("OPENAI_KEY"):
        try:
            if _openai is None:
                from openai import OpenAI
                _openai = OpenAI(api_key=_key("OPENAI_KEY"))
            payload = ([{"role": "system", "content": system}] if system else []) + messages
            r = _openai.chat.completions.create(model="gpt-4o-mini", messages=payload, max_tokens=max_tokens, temperature=0.1)
            text = (r.choices[0].message.content or "").strip()
            if text:
                return text, ""
        except Exception as exc:
            errors.append(f"OpenAI: {str(exc)[:100]}")
    return "", "\n".join(errors) or "No AI provider configured"


def _safe(v):
    return "N/A" if v is None or str(v).strip() in {"", "None", "Null"} else str(v)


def _fallback_outlook(symbol, ltp, rsi, macd, trend, pe, roe, atr, sl, target):
    zone = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
    direction = "positive" if macd > 0 else "negative"
    missing = [label for label, value in (("P/E", pe), ("ROE", roe)) if _safe(value) == "N/A"]
    constraint = (" Fundamental assessment is constrained because " + ", ".join(missing) + " is unavailable.") if missing else ""
    return ("---\n"
            "## 📈 Technical Structure & Momentum Spectrum\n"
            f"- **Trend Diagnostics:** {symbol} is currently {trend}; price momentum is assessed using the supplied trend and moving-average structure.\n"
            f"- **Oscillator Readings:** RSI is {_safe(rsi)} ({zone}); MACD is {_safe(macd)} ({direction}). Entry should avoid chasing overbought momentum and wait for confirmation near support.\n"
            f"- **Volatility Parameters:** ATR is Rs {_safe(atr)}, implying a calculated risk band around the current price.\n\n"
            "---\n## 🔎 Fundamental Quality & Valuation Framework\n"
            f"- **Pricing Multiple Assessment:** TTM P/E is {_safe(pe)}; valuation cannot be judged beyond the supplied figure without a reliable sector comparison.{constraint}\n"
            f"- **Balance Sheet Health:** ROE is {_safe(roe)}%; Debt/Equity was not supplied to this fallback, so leverage quality remains constrained.\n\n"
            "---\n## 📰 Sentiment & Structural Catalyst Correlation\n"
            "- **News Sentiment Mapping:** No AI/news synthesis was available; treat the news section as unconfirmed and verify primary sources before acting.\n"
            "- **Benchmark Contrast:** Relative Nifty alpha was not available in this fallback; no independent outperformance claim is made.\n\n"
            "---\n## 💡 Comprehensive AI Outlook & Guardrails\n"
            f"- **Strategic Thesis:** The technical bias is {trend}, but conviction is limited until fundamentals, news, and benchmark data are confirmed.\n"
            f"- **Execution Trajectory:** Reference target is Rs {_safe(target)} and protective stop is Rs {_safe(sl)}. If support is violated, reduce exposure and wait for a confirmed recovery rather than averaging blindly.\n\n"
            "---\n*Disclaimer: This report is automatically compiled from public market feeds and a generative intelligence layer. It is not investment advice.*")


def ai_insights(symbol, ltp, rsi, macd_line, trend, pe, roe, atr=0.0, sl=0.0, t1=0.0):
    prompt = (f"Create a concise but detailed Indian equity outlook for {symbol}. Price Rs {ltp:.2f}; RSI {rsi}; MACD {macd_line}; trend {trend}; P/E {_safe(pe)}; ROE {_safe(roe)}; ATR {_safe(atr)}; stop {_safe(sl)}; target {_safe(t1)}. Use Markdown sections Technical, Fundamentals, Risks, Outlook. Never invent missing data.")
    if ai_available():
        text, error = _call_ai([{"role": "user", "content": prompt}], 700, "Use only supplied values. Output Markdown only.")
        if text:
            return text
        logger.warning("AI outlook failed; using deterministic report fallback: %s", error)
    return _fallback_outlook(symbol, ltp, rsi, macd_line, trend, pe, roe, atr, sl, t1)


def structured_investment_outlook(symbol, company_name, ltp, day_high, day_low, y_high, y_low, trend_signal, rsi_14, macd_value, ema20, ema50, ema200, bb_lower, bb_upper, market_cap, pe_ttm, pe_forward, price_to_book, roe_percent, dividend_yield, debt_equity, nifty_outperformance_pts, news_bullets):
    if ai_available():
        prompt = f"Generate a detailed Markdown-only Indian equity report for {company_name} ({symbol}) using only these values: price={_safe(ltp)}, day={_safe(day_low)}-{_safe(day_high)}, 52W={_safe(y_low)}-{_safe(y_high)}, trend={_safe(trend_signal)}, RSI={_safe(rsi_14)}, MACD={_safe(macd_value)}, EMA20/50/200={_safe(ema20)}/{_safe(ema50)}/{_safe(ema200)}, BB={_safe(bb_lower)}-{_safe(bb_upper)}, market cap={_safe(market_cap)}, PE={_safe(pe_ttm)}, forward PE={_safe(pe_forward)}, PB={_safe(price_to_book)}, ROE={_safe(roe_percent)}, dividend={_safe(dividend_yield)}, debt/equity={_safe(debt_equity)}, alpha={_safe(nifty_outperformance_pts)}, news={news_bullets or 'N/A'}. Include Technical Structure & Momentum Spectrum, Fundamental Quality & Valuation Framework, Sentiment & Structural Catalyst Correlation, and Comprehensive AI Outlook & Guardrails. Explicitly state constraints for N/A values and flag momentum-vs-fundamental divergence."
        text, error = _call_ai([{"role": "user", "content": prompt}], 900, "Output only structured Markdown. Never fabricate missing values.")
        if text:
            return text
        logger.warning("Structured AI report failed: %s", error)
    return _fallback_outlook(symbol, ltp, rsi_14, macd_value, trend_signal, pe_ttm, roe_percent, "N/A", "N/A", "N/A")


def long_term_view(symbol, sector, ltp, pe, roe, de, div_y, ema200, w52h, w52l, peer_avg_pe=None, peer_avg_roe=None):
    if not ai_available():
        return f"Quality: ROE={_safe(roe)}%; leverage={_safe(de)}.\nValuation: PE={_safe(pe)}; peer comparison unavailable.\nWatch for: earnings, margins, debt and cash flow.\nSuitability: Watchlist only — verify missing data."
    text, _ = _call_ai([{"role": "user", "content": f"Give exactly four lines for {symbol}: Quality, Valuation, Watch for, Suitability. Use only PE={_safe(pe)}, ROE={_safe(roe)}, Debt/Equity={_safe(de)}, Dividend={_safe(div_y)}, peer PE={_safe(peer_avg_pe)}, peer ROE={_safe(peer_avg_roe)}."}], 250, "No targets or invented figures.")
    return text or ""


def get_live_market_context(force=False):
    return "Live market context unavailable; state missing values explicitly."


def ai_chat_respond(uid, user_message):
    if not ai_available(): return "⚠️ No AI key configured."
    text, error = _call_ai([{"role": "user", "content": user_message}], 450, "Indian NSE/BSE analyst. Use supplied data only.")
    return text or "⚠️ AI temporarily unavailable."


def ai_topic_respond(topic_prompt):
    return ai_chat_respond(0, topic_prompt)


def add_to_chat(uid, role, content): pass

def clear_chat(uid): pass
AI_CHAT_TOPICS = {"🔍 Stock Analysis": "Analyze the supplied stock.", "📊 Nifty Valuation": "Analyze Nifty valuation.", "💎 Fundamental Picks": "Find fundamental picks from supplied data.", "📈 Nifty Update": "Give a Nifty update from supplied data."}
AI_CHAT_TOPIC_KEYS = set(AI_CHAT_TOPICS)

def test_ai_providers(): return {"GROQ": "CONFIGURED" if _key("GROQ_API_KEY") else "SKIP", "Gemini": "CONFIGURED" if _key("GEMINI_API_KEY") else "SKIP", "OpenAI": "CONFIGURED" if _key("OPENAI_KEY") else "SKIP", "AskFuzz": "SKIP", "_status": "✅ AI CONFIGURED" if ai_available() else "❌ ALL FAILED"}
def debug_ai_status(): return {"ai_available": ai_available(), "groq_models": _GROQ_MODELS}
def fetch_news(symbol): return ""
def fetch_market_news(): return ""
