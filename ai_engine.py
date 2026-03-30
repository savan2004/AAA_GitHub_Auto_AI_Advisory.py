"""
ai_engine.py  —  AI Engine for Stock Advisory Bot (Fixed v4.2)
================================================================
FIXES:
  - Gemini model updated: gemini-1.5-flash -> gemini-2.0-flash (stable)
  - All API keys re-read at call time for live Render env updates
  - GROQ client force-initialized per call to prevent SDK stale state
  - Added GROQ direct HTTP fallback for robustness
  - OpenAI key name updatedSk (matches Render env var)
"""

import os
import logging
import time
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime

logger = logging.getLogger(__name__)

def _key(name: str) -> str:
    return os.getenv(name, "").strip()

# ══════════════════════════════════════════════════════════════════════════════
# AI CLIENT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _get_groq():
    key = _key("GROQ_API_KEY")
    if not key: return None
    try:
        from groq import Groq
        return Groq(api_key=key)
    except Exception as e:
        logger.error(f"ai_engine: GROQ init failed - {e}")
        return None

def _get_gemini():
    key = _key("GEMINI_API_KEY")
    if not key: return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        # Using gemini-2.0-flash as gemini-1.5-flash is often 404/deprecated on v1beta
        return genai.GenerativeModel("gemini-2.0-flash")
    except Exception as e:
        logger.error(f"ai_engine: Gemini init failed - {e}")
        return None

def _get_openai():
    key = _key("OPENAI_KEY")
    if not key: return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=key)
    except Exception as e:
        logger.error(f"ai_engine: OpenAI init failed - {e}")
        return None

def ai_available() -> bool:
    return bool(_key("GROQ_API_KEY") or _key("GEMINI_API_KEY") or _key("OPENAI_KEY"))

# ══════════════════════════════════════════════════════════════════════════════
# CORE AI CALL
# ══════════════════════════════════════════════════════════════════════════════

def _call_ai(messages: list, max_tokens: int = 500, system: str = "") -> tuple[str, str]:
    errors = []

    # 1. GROQ (SDK + HTTP Fallback)
    groq_key = _key("GROQ_API_KEY")
    if groq_key:
        # Try SDK first
        groq = _get_groq()
        if groq:
            try:
                msgs = ([{"role": "system", "content": system}] if system else []) + messages
                r = groq.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=msgs,
                    max_tokens=max_tokens,
                    temperature=0.4,
                )
                txt = (r.choices[0].message.content or "").strip()
                if txt: return txt, ""
            except Exception as e:
                errors.append(f"GROQ SDK: {str(e)[:100]}")
        
        # HTTP Fallback
        try:
            msgs = ([{"role": "system", "content": system}] if system else []) + messages
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json={"model": "llama-3.3-70b-versatile", "messages": msgs, "max_tokens": max_tokens},
                timeout=10
            ).json()
            txt = resp['choices'][0]['message']['content'].strip()
            if txt: return txt, ""
        except Exception as e:
            errors.append(f"GROQ HTTP: {str(e)[:100]}")

    # 2. Gemini
    gemini_key = _key("GEMINI_API_KEY")
    if gemini_key:
        gemini = _get_gemini()
        if gemini:
            try:
                full = ((system + "

") if system else "") + "
".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
                r = gemini.generate_content(full)
                txt = (getattr(r, "text", "") or "").strip()
                if txt: return txt, ""
            except Exception as e:
                errors.append(f"Gemini: {str(e)[:100]}")

    # 3. OpenAI
    openai_key = _key("OPENAI_KEY")
    if openai_key:
        openai = _get_openai()
        if openai:
            try:
                msgs = ([{"role": "system", "content": system}] if system else []) + messages
                r = openai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=msgs,
                    max_tokens=max_tokens,
                )
                txt = (r.choices[0].message.content or "").strip()
                if txt: return txt, ""
            except Exception as e:
                errors.append(f"OpenAI: {str(e)[:100]}")

    return "", "
".join(errors) or "No AI provider succeeded"

def ai_insights(symbol: str, ltp: float, rsi: float, macd_line: float, trend: str, pe: str, roe: str) -> str:
    if not ai_available(): return "⚠️ AI Keys Missing in Render Env"
    prompt = f"3 Bulls, 2 Risks for {symbol} (NSE India). Data: LTP {ltp}, RSI {rsi}, MACD {'UP' if macd_line>0 else 'DN'}, Trend {trend}, PE {pe}, ROE {roe}."
    text, err = _call_ai([{"role": "user", "content": prompt}], system="Concise analyst.")
    return text if text else f"⚠️ AI Error: {err}"

def fetch_news(symbol: str) -> str:
    return "" # Placeholder for logic in full file

def ai_chat_respond(uid: int, user_message: str) -> str:
    if not ai_available(): return "⚠️ Set AI keys in Render."
    text, err = _call_ai([{"role": "user", "content": user_message}], system="Indian Market Expert.")
    return text if text else f"❌ Error: {err}"
