\"\"\"
ai_engine.py — AI Engine for Stock Advisory Bot (Fixed v4.4)
================================================================
FIXES:
 - GROQ: Strict key format validation (must start with gsk_)
 - Gemini: Model updated to gemini-1.5-flash (more stable) with 2.0-flash fallback
 - OpenAI: Strict key format validation (must start with sk-)
 - Better Error Reporting: Specific messages for initialization failures
 - Live Key Sync: os.getenv called at runtime inside helper functions
\"\"\"
import os
import logging
import time
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime

logger = logging.getLogger(__name__)

def _key(name: str) -> str:
    return os.getenv(name, \"\").strip()

# ══════════════════════════════════════════════════════════════════════════════
# AI CLIENT HELPERS (Live Sync)
# ══════════════════════════════════════════════════════════════════════════════

def _get_groq():
    key = _key(\"GROQ_API_KEY\")
    if not key: return None
    if not key.startswith(\"gsk_\"):
        logger.error(\"ai_engine: GROQ_API_KEY must start with 'gsk_'\")
        return None
    try:
        from groq import Groq
        return Groq(api_key=key)
    except Exception as e:
        logger.error(f\"ai_engine: GROQ SDK init failed - {e}\")
        return None

def _get_gemini():
    key = _key(\"GEMINI_API_KEY\")
    if not key: return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        return genai.GenerativeModel(\"gemini-1.5-flash\")
    except Exception as e:
        logger.error(f\"ai_engine: Gemini init failed - {e}\")
        return None

def _get_openai():
    key = _key(\"OPENAI_KEY\")
    if not key: return None
    if not key.startswith(\"sk-\"):
        logger.error(\"ai_engine: OPENAI_KEY must start with 'sk-'\")
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=key)
    except Exception as e:
        logger.error(f\"ai_engine: OpenAI init failed - {e}\")
        return None

def ai_available() -> bool:
    return bool(_key(\"GROQ_API_KEY\") or _key(\"GEMINI_API_KEY\") or _key(\"OPENAI_KEY\"))

# ══════════════════════════════════════════════════════════════════════════════
# CORE AI CALL (GROQ -> Gemini -> OpenAI)
# ══════════════════════════════════════════════════════════════════════════════

def _call_ai(messages: list, max_tokens: int = 500, system: str = \"\") -> tuple[str, str]:
    errors = []
    
    # 1. GROQ
    groq_key = _key(\"GROQ_API_KEY\")
    if groq_key:
        if not groq_key.startswith(\"gsk_\"):
            errors.append(\"GROQ: Key must start with 'gsk_'\")
        else:
            groq_sdk = _get_groq()
            if groq_sdk:
                try:
                    msgs = ([{\"role\": \"system\", \"content\": system}] if system else []) + messages
                    r = groq_sdk.chat.completions.create(
                        model=\"llama-3.3-70b-versatile\",
                        messages=msgs,
                        max_tokens=max_tokens,
                        temperature=0.4,
                    )
                    txt = (r.choices[0].message.content or \"\").strip()
                    if txt: return txt, \"\"
                except Exception as e:
                    errors.append(f\"GROQ SDK: {str(e)[:80]}\")
            
            try:
                msgs = ([{\"role\": \"system\", \"content\": system}] if system else []) + messages
                resp = requests.post(
                    \"https://api.groq.com/openai/v1/chat/completions\",
                    headers={\"Authorization\": f\"Bearer {groq_key}\"},
                    json={\"model\": \"llama-3.3-70b-versatile\", \"messages\": msgs, \"max_tokens\": max_tokens},
                    timeout=12
                ).json()
                if 'choices' in resp:
                    txt = resp['choices'][0]['message']['content'].strip()
                    if txt: return txt, \"\"
                else:
                    errors.append(f\"GROQ HTTP: {resp.get('error', {}).get('message', 'Auth fail')[:60]}\")
            except Exception as e:
                errors.append(f\"GROQ HTTP: {str(e)[:80]}\")

    # 2. Gemini
    gemini_key = _key(\"GEMINI_API_KEY\")
    if gemini_key:
        gemini = _get_gemini()
        if gemini:
            try:
                full = ((system + \"\
\") if system else \"\") + \"\
\".join(f\"{m['role'].upper()}: {m['content']}\" for m in messages)
                r = gemini.generate_content(full)
                txt = (getattr(r, \"text\", \"\") or \"\").strip()
                if txt: return txt, \"\"
            except Exception as e:
                if \"404\" in str(e) or \"not found\" in str(e).lower():
                    try:
                        import google.generativeai as genai
                        m2 = genai.GenerativeModel(\"gemini-2.0-flash\")
                        r = m2.generate_content(full)
                        txt = (getattr(r, \"text\", \"\") or \"\").strip()
                        if txt: return txt, \"\"
                    except Exception as e2:
                        errors.append(f\"Gemini (1.5 & 2.0 Fail): {str(e2)[:80]}\")
                else:
                    errors.append(f\"Gemini: {str(e)[:80]}\")

    # 3. OpenAI
    openai_key = _key(\"OPENAI_KEY\")
    if openai_key:
        if not openai_key.startswith(\"sk-\"):
            errors.append(\"OpenAI: Key must start with 'sk-'\")
        else:
            openai_client = _get_openai()
            if openai_client:
                try:
                    msgs = ([{\"role\": \"system\", \"content\": system}] if system else []) + messages
                    r = openai_client.chat.completions.create(
                        model=\"gpt-4o-mini\",
                        messages=msgs,
                        max_tokens=max_tokens,
                    )
                    txt = (r.choices[0].message.content or \"\").strip()
                    if txt: return txt, \"\"
                except Exception as e:
                    errors.append(f\"OpenAI: {str(e)[:80]}\")

    return \"\", \"\
\".join(errors) or \"No AI provider succeeded\"

# ══════════════════════════════════════════════════════════════════════════════
# EXPORTED LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def ai_insights(symbol, ltp, rsi, macd_line, trend, pe, roe) -> str:
    if not ai_available(): return \"⚠️ AI Keys Missing\"
    prompt = f\"3 Bulls, 2 Risks for {symbol} (NSE India). LTP {ltp}, RSI {rsi}, MACD {'UP' if macd_line>0 else 'DN'}, Trend {trend}, PE {pe}, ROE {roe}.\"
    text, err = _call_ai([{\"role\": \"user\", \"content\": prompt}], system=\"Concise Indian equity analyst.\")
    return text if text else f\"⚠️ AI Error:\
{err}\"

def fetch_news(symbol) -> str:
    tk = _key(\"TAVILY_API_KEY\")
    if tk:
        try:
            r = requests.post(\"https://api.tavily.com/search\", json={\"api_key\": tk, \"query\": f\"{symbol} NSE stock news\", \"max_results\": 2}, timeout=6).json()
            return \"\
\".join([f\"📰 {x['title'][:85]}\" for x in r.get(\"results\", []) if x.get(\"title\")])
        except: pass
    return \"\"

def get_live_market_context() -> str:
    try:
        from data_engine import _yahoo_v8_hist
        lines = [f\"=== LIVE DATA {datetime.now().strftime('%d-%b-%Y %H:%M IST')} ===\"]
        df = _yahoo_v8_hist(\"^NSEI\", period=\"2d\")
        if df is not None and not df.empty:
            l = round(float(df['Close'].iloc[-1]), 2)
            p = round(float(df['Close'].iloc[-2]), 2)
            lines.append(f\"NIFTY 50: {l} ({((l-p)/p*100):+.2f}%)\")
        return \"\
\".join(lines)
    except Exception as e:
        return f\"=== LIVE DATA ERR: {str(e)[:40]} ===\"

_chat_history = {}
def add_to_chat(uid, role, content):
    if uid not in _chat_history: _chat_history[uid] = []
    _chat_history[uid].append({\"role\": role, \"content\": content})
    _chat_history[uid] = _chat_history[uid][-12:]

def get_chat_history(uid): return _chat_history.get(uid, [])
def clear_chat(uid): _chat_history.pop(uid, None)

def ai_chat_respond(uid, user_message):
    if not ai_available(): return \"⚠️ No AI keys.\"
    text, err = _call_ai(get_chat_history(uid) + [{\"role\": \"user\", \"content\": user_message}])
    if text:
        add_to_chat(uid, \"user\", user_message)
        add_to_chat(uid, \"assistant\", text)
        return text
    return f\"❌ Error: {err}\"

AI_CHAT_TOPICS = {\"📊 Nifty Valuation\": \"...\", \"💎 Fundamental Picks\": \"...\", \"📈 Nifty Update\": \"...\", \"🎯 Technical Swing Trade\": \"...\", \"⚡ Option Trade\": \"...\"}
AI_CHAT_TOPIC_KEYS = set(AI_CHAT_TOPICS.keys())

def test_ai_providers():
    res = {}
    for n, f in [(\"GROQ\", _get_groq), (\"Gemini\", _get_gemini), (\"OpenAI\", _get_openai)]:
        try:
            c = f()
            res[n] = \"OK\" if c else \"MISSING\"
        except Exception as e: res[n] = str(e)
    return res

def debug_ai_status():
    return {\"groq\": bool(_key(\"GROQ_API_KEY\")), \"gemini\": bool(_key(\"GEMINI_API_KEY\")), \"openai\": bool(_key(\"OPENAI_KEY\"))}
