"""
ai_engine.py  —  AI Engine for Stock Advisory Bot (Fixed v4.2)
================================================================
FIXES:
  - Gemini model updated: gemini-1.5-flash -> gemini-2.0-flash (stable)
  - All API keys re-read at call time for live Render env updates
  - GROQ client force-initialized per call to prevent SDK stale state
  - Added GROQ direct HTTP fallback for robustness
  - OpenAI key name sk- (matches Render env var)
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
    groq_key = _key("GROQ_API_KEY")
    if groq_key:
        groq = _get_groq()
        if groq:
            try:
                msgs = ([{"role": "system", "content": system}] if system else []) + messages
                r = groq.chat.completions.create(model="llama-3.3-70b-versatile", messages=msgs, max_tokens=max_tokens)
                txt = (r.choices[0].message.content or "").strip()
                if txt: return txt, ""
            except Exception as e:
                errors.append(f"GROQ SDK: {str(e)[:100]}")
        try:
            msgs = ([{"role": "system", "content": system}] if system else []) + messages
            resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {groq_key}"}, json={"model": "llama-3.3-70b-versatile", "messages": msgs, "max_tokens": max_tokens}, timeout=10).json()
            txt = resp['choices'][0]['message']['content'].strip()
            if txt: return txt, ""
        except Exception as e:
            errors.append(f"GROQ HTTP: {str(e)[:100]}")
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
    openai_key = _key("OPENAI_KEY")
    if openai_key:
        openai = _get_openai()
        if openai:
            try:
                msgs = ([{"role": "system", "content": system}] if system else []) + messages
                r = openai.chat.completions.create(model="gpt-4o-mini", messages=msgs, max_tokens=max_tokens)
                txt = (r.choices[0].message.content or "").strip()
                if txt: return txt, ""
            except Exception as e:
                errors.append(f"OpenAI: {str(e)[:100]}")
    return "", "
".join(errors) or "No AI provider succeeded"

def ai_insights(symbol: str, ltp: float, rsi: float, macd_line: float, trend: str, pe: str, roe: str) -> str:
    if not ai_available(): return "⚠️ AI Keys Missing"
    prompt = f"3 Bulls, 2 Risks for {symbol}. LTP {ltp}, RSI {rsi}, MACD {'UP' if macd_line>0 else 'DN'}, Trend {trend}, PE {pe}, ROE {roe}."
    text, err = _call_ai([{"role": "user", "content": prompt}], system="Concise analyst.")
    return text if text else f"⚠️ AI Error: {err}"

def fetch_news(symbol: str) -> str:
    tk = _key("TAVILY_API_KEY")
    if tk:
        try:
            r = requests.post("https://api.tavily.com/search", json={"api_key": tk, "query": f"{symbol} NSE news", "max_results": 2}, timeout=6).json()
            return "
".join([f"📰 {x['title'][:85]}" for x in r.get("results", []) if x.get("title")])
        except: pass
    return ""

def ai_chat_respond(uid: int, user_message: str) -> str:
    if not ai_available(): return "⚠️ Set AI keys."
    text, err = _call_ai([{"role": "user", "content": user_message}])
    return text if text else f"❌ Error: {err}"

# Keep original history logic
_chat_history = {}
def add_to_chat(uid, role, content):
    if uid not in _chat_history: _chat_history[uid] = []
    _chat_history[uid].append({"role": role, "content": content})
    _chat_history[uid] = _chat_history[uid][-12:]

def get_chat_history(uid): return _chat_history.get(uid, [])

def clear_chat(uid): _chat_history.pop(uid, None)

def test_ai_providers():
    res = {}
    for name, get_fn in [("GROQ", _get_groq), ("Gemini", _get_gemini), ("OpenAI", _get_openai)]:
        try:
            c = get_fn()
            if not c: res[name] = "MISSING"
            else: res[name] = "OK"
        except Exception as e: res[name] = str(e)
    return res

def debug_ai_status():
    return {"groq": bool(_key("GROQ_API_KEY")), "gemini": bool(_key("GEMINI_API_KEY")), "openai": bool(_key("OPENAI_KEY"))}
