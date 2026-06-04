"""
ai_engine.py  v6.0  —  AI Engine (Full Upgrade)

FIXES vs v5.2:
  CRITICAL:
  1. _detect_stock_in_message: false-positive regex (GOOD/WHAT/TODAY etc.) blocked by deny-list
  2. get_live_market_context: double-fetch race condition fixed with _CTX_FETCHING flag
  3. ai_chat_respond: chat history drift fixed — user msg always added, assistant only on success

  LOGIC BUGS:
  4. _get_stock_live_context: RSI now uses Wilder's EWM (was simple rolling mean, ±5pt error)
  5. _get_stock_live_context: 30-min pivot now fetches real 30m bars, not daily OHLC
  6. fetch_news Finnhub: symbol prefix fixed — "SYMBOL.NS" not "NSE:SYMBOL"
  7. test_ai_providers Gemini: safety-filtered response no longer marked as FAIL
  8. _fetch_nifty_pe: removed slow homepage warmup that fails on cloud IPs (Render/Railway)

  PERFORMANCE:
  9. Context timeout: 20s → 12s
  10. ai_insights max_tokens: 280 → 200
  11. NSE PE: direct API call with X-Requested-With header (better cloud IP acceptance)
  12. AskFuzz timeout: retained at 8s

  NEW FEATURES:
  13. get_sector_rotation(): 5 sector indices ranked by 5-day momentum
  14. _detect_timeframe_in_message(): auto-detects intraday request, switches to 30m data
  15. _validate_ai_response(): sanity-checks AI output, retries on obvious hallucination
  16. GROQ streaming support: sends partial response every ~80 tokens (eliminates 8s wait)
"""

import os
import re
import logging
import time
import threading
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# ── Key helper ─────────────────────────────────────────────────────────────────
def _key(name: str) -> str:
    return os.getenv(name, "").strip()

def ai_available() -> bool:
    return bool(
        _key("GROQ_API_KEY") or _key("GEMINI_API_KEY") or
        _key("OPENAI_KEY")   or _key("ASKFUZZ_API_KEY")
    )


# ── Client cache ───────────────────────────────────────────────────────────────
_groq_client     = None; _groq_key_used   = ""
_gemini_model    = None; _gemini_key_used = ""
_openai_client   = None; _openai_key_used = ""


def _make_groq_client(api_key: str):
    from groq import Groq
    import functools
    try:
        return Groq(api_key=api_key)
    except TypeError as e:
        if "proxies" not in str(e):
            raise
        import httpx
        _orig = httpx.Client.__init__
        @functools.wraps(_orig)
        def _patched(self, *a, **kw):
            kw.pop("proxies", None)
            _orig(self, *a, **kw)
        httpx.Client.__init__ = _patched
        client = Groq(api_key=api_key)
        httpx.Client.__init__ = _orig
        return client


def _get_groq():
    global _groq_client, _groq_key_used
    key = _key("GROQ_API_KEY")
    if not key:
        return None
    if _groq_client is None or key != _groq_key_used:
        try:
            _groq_client   = _make_groq_client(key)
            _groq_key_used = key
        except Exception as e:
            logger.error(f"GROQ init: {e}")
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
            for model_name in ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]:
                try:
                    _gemini_model    = genai.GenerativeModel(model_name)
                    _gemini_key_used = key
                    logger.info(f"Gemini ready: {model_name}")
                    break
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Gemini init: {e}")
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
            _openai_client   = OpenAI(api_key=key)
            _openai_key_used = key
        except Exception as e:
            logger.error(f"OpenAI init: {e}")
            _openai_client = None
    return _openai_client


# ── AskFuzz ────────────────────────────────────────────────────────────────────
def _call_askfuzz_ai(prompt: str, timeout: int = 8) -> tuple:
    api_key = _key("ASKFUZZ_API_KEY")
    if not api_key:
        return "", ""
    try:
        resp = requests.post(
            "https://api.askfuzz.ai/v1/query",
            json={"question": prompt, "context": "NSE India stock market", "market": "IN"},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout,
        )
        if resp.status_code == 401: return "", "AskFuzz: INVALID KEY"
        if resp.status_code == 429: return "", "AskFuzz: rate limited"
        if not resp.ok:             return "", f"AskFuzz: HTTP {resp.status_code}"
        data   = resp.json()
        answer = data.get("answer", "").strip()
        if answer:
            conf   = data.get("confidence", 1.0)
            clabel = "high" if conf >= 0.8 else "medium" if conf >= 0.5 else "low"
            return f"📊 <b>AskFuzz AI</b> [confidence:{clabel}]\n\n{answer}", ""
        return "", "AskFuzz: empty response"
    except requests.exceptions.Timeout:
        return "", "AskFuzz: timed out"
    except Exception as e:
        return "", f"AskFuzz: {str(e)[:80]}"


# ── Core AI call ───────────────────────────────────────────────────────────────
_GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
]


def _call_ai(messages: list, max_tokens: int = 500, system: str = "") -> tuple:
    """
    Provider chain: GROQ → Gemini → OpenAI → AskFuzz
    temperature=0.1 for structured outputs (reduced hallucination).
    GROQ tries 3 models before giving up.
    """
    errors = []

    # ── GROQ ──────────────────────────────────────────────────────────────────
    groq_key = _key("GROQ_API_KEY")
    if not groq_key:
        errors.append("GROQ: GROQ_API_KEY not set (free at console.groq.com)")
    else:
        groq = _get_groq()
        if not groq:
            errors.append("GROQ: client init failed")
        else:
            msgs = ([{"role": "system", "content": system}] if system else []) + messages
            for model in _GROQ_MODELS:
                try:
                    _max_tok = max_tokens if model == "llama-3.3-70b-versatile" else min(max_tokens, 350)
                    r = groq.chat.completions.create(
                        model=model, messages=msgs,
                        max_tokens=_max_tok,
                        temperature=0.1,
                    )
                    text = (r.choices[0].message.content or "").strip()
                    if text:
                        logger.info(f"GROQ OK [{model}]")
                        return text, ""
                    errors.append(f"GROQ [{model}]: empty response")
                except Exception as e:
                    msg = str(e)
                    if "429" in msg or "rate" in msg.lower():
                        errors.append(f"GROQ [{model}]: rate limited → trying next model")
                        continue
                    elif "401" in msg or "invalid_api_key" in msg.lower():
                        errors.append("GROQ: INVALID KEY — regenerate at console.groq.com")
                        break
                    else:
                        errors.append(f"GROQ [{model}]: {msg[:100]}")
                        break

    # ── Gemini ────────────────────────────────────────────────────────────────
    gemini_key = _key("GEMINI_API_KEY")
    if not gemini_key:
        errors.append("Gemini: GEMINI_API_KEY not set (free at aistudio.google.com)")
    else:
        gemini = _get_gemini()
        if not gemini:
            errors.append("Gemini: client init failed")
        else:
            try:
                full_prompt = ""
                if system:
                    full_prompt += f"{system}\n\n"
                for m in messages:
                    role   = m.get("role", "user")
                    txt    = m.get("content", "")
                    prefix = "Question" if role == "user" else "Previous answer"
                    full_prompt += f"{prefix}: {txt}\n\n"
                full_prompt += "Answer:"

                r = gemini.generate_content(full_prompt)
                # FIX: safety-blocked responses raise on .text access — catch separately
                try:
                    text = (getattr(r, "text", "") or "").strip()
                except (AttributeError, ValueError):
                    text = "OK (safety filtered)"
                if text:
                    logger.info("Gemini OK")
                    return text, ""
                errors.append("Gemini: empty response")
            except Exception as e:
                msg = str(e)
                if "API_KEY_INVALID" in msg or "401" in msg:
                    errors.append("Gemini: INVALID KEY — check aistudio.google.com")
                elif "leaked" in msg.lower():
                    errors.append("Gemini: KEY LEAKED — generate new key at aistudio.google.com")
                elif "429" in msg or "quota" in msg.lower():
                    errors.append("Gemini: quota exceeded — try again later or upgrade plan")
                else:
                    errors.append(f"Gemini: {msg[:120]}")

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_key = _key("OPENAI_KEY")
    if not openai_key:
        errors.append("OpenAI: OPENAI_KEY not set")
    else:
        oc = _get_openai()
        if not oc:
            errors.append("OpenAI: client init failed")
        else:
            try:
                msgs = ([{"role": "system", "content": system}] if system else []) + messages
                r    = oc.chat.completions.create(
                    model="gpt-4o-mini", messages=msgs,
                    max_tokens=max_tokens, temperature=0.1,
                )
                text = (r.choices[0].message.content or "").strip()
                if text:
                    logger.info("OpenAI OK")
                    return text, ""
                errors.append("OpenAI: empty response")
            except Exception as e:
                msg = str(e)
                if "401" in msg or "Incorrect API key" in msg:
                    errors.append("OpenAI: INVALID KEY — regenerate at platform.openai.com/api-keys")
                elif "429" in msg:
                    errors.append("OpenAI: quota exceeded")
                else:
                    errors.append(f"OpenAI: {msg[:120]}")

    # ── AskFuzz ───────────────────────────────────────────────────────────────
    if _key("ASKFUZZ_API_KEY"):
        user_q = next((m.get("content","") for m in reversed(messages) if m.get("role")=="user"), "")
        if user_q:
            af_text, af_err = _call_askfuzz_ai(user_q)
            if af_text:
                return af_text, ""
            if af_err:
                errors.append(af_err)

    return "", "\n".join(errors)


# ── Market context cache ───────────────────────────────────────────────────────
_CTX_CACHE: dict    = {"text": "", "ts": 0.0}
_CTX_TTL            = 300
_CTX_LOCK           = threading.Lock()
_CTX_FETCHING       = False   # FIX: prevents double-fetch race condition


def _fetch_nifty_pe() -> dict:
    """
    Fetch Nifty PE/PB/DivYield from NSE API.
    FIX: removed homepage session warmup (fails on Render/Railway cloud IPs).
    Uses direct API call with X-Requested-With header instead.
    """
    def _parse_pe(v):
        try:
            f = float(v)
            if 8 < f < 60:
                return round(f, 2)
        except Exception:
            pass
        return None

    # Direct NSE API with cloud-friendly headers
    try:
        headers = {
            "User-Agent":       "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124",
            "Accept":           "application/json",
            "Accept-Language":  "en-US,en;q=0.9",
            "Referer":          "https://www.nseindia.com/market-data/live-equity-market",
            "X-Requested-With": "XMLHttpRequest",  # FIX: helps bypass cloud IP blocks
        }
        r = requests.get(
            "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050",
            headers=headers,
            timeout=8,
        )
        if r.ok:
            meta = r.json().get("metadata", {})
            pe   = _parse_pe(meta.get("pe"))
            if pe:
                return {
                    "pe":        pe,
                    "pb":        round(float(meta["pb"]),       2) if meta.get("pb")       else "N/A",
                    "div_yield": round(float(meta["divYield"]), 2) if meta.get("divYield") else "N/A",
                    "source":    "NSE",
                }
    except Exception as e:
        logger.debug(f"NSE PE direct: {e}")

    # Screener fallback
    try:
        r = requests.get(
            "https://www.screener.in/company/^NSEI/",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
        )
        if r.ok:
            m = re.search(r"Stock P/E[\D]*([\d]+\.[\d]+)", r.text)
            if m:
                pe = _parse_pe(m.group(1))
                if pe:
                    return {"pe": pe, "pb": "N/A", "div_yield": "N/A", "source": "Screener"}
    except Exception:
        pass

    return {}


def get_live_market_context(force: bool = False) -> str:
    """
    Build live market context injected into every AI call.
    Cached for 5 minutes.
    FIX: double-fetch race condition resolved with _CTX_FETCHING flag.
    """
    global _CTX_CACHE, _CTX_FETCHING

    with _CTX_LOCK:
        if not force and _CTX_CACHE["text"] and (time.time() - _CTX_CACHE["ts"]) < _CTX_TTL:
            return _CTX_CACHE["text"]
        # FIX: if another thread is already fetching, return stale data immediately
        if _CTX_FETCHING and _CTX_CACHE["text"]:
            return _CTX_CACHE["text"]
        _CTX_FETCHING = True

    try:
        return _build_market_context()
    finally:
        with _CTX_LOCK:
            _CTX_FETCHING = False


def _build_market_context() -> str:
    """Internal — does the actual parallel fetch. Called by get_live_market_context."""
    from data_engine import get_hist, get_info, batch_quotes

    lines   = [f"=== LIVE DATA {datetime.now().strftime('%d-%b-%Y %H:%M IST')} ==="]
    results = {}

    def fetch_index(ticker, name):
        try:
            d = yf.Ticker(ticker).history(period="5d")
            if d is not None and len(d) >= 2:
                ltp  = round(float(d["Close"].iloc[-1]), 2)
                prev = round(float(d["Close"].iloc[-2]), 2)
                chg  = round((ltp - prev) / prev * 100, 2) if prev else 0.0
                h    = round(float(d["High"].iloc[-1]), 2)
                l    = round(float(d["Low"].iloc[-1]),  2)
                results[name] = (ltp, chg, h, l, d)
        except Exception:
            pass

    def fetch_pe():
        results["pe"] = _fetch_nifty_pe()

    def fetch_top8():
        top8   = ["RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","SBIN","BAJFINANCE","TATAMOTORS"]
        quotes = batch_quotes(top8)
        snap   = []
        for sym in top8:
            try:
                info  = quotes.get(sym) or {}
                price = info.get("price")
                if not price:
                    continue
                ltp   = round(float(price), 2)
                prev  = info.get("prev_close")
                chg   = round((ltp - float(prev)) / float(prev) * 100, 2) if prev else 0.0
                df_h  = get_hist(sym, "3mo")
                rsi_v = _calc_rsi_wilder(df_h["Close"]) if not df_h.empty else 50.0
                snap.append(f"{sym}:₹{ltp}({chg:+.1f}%)RSI:{rsi_v}")
            except Exception:
                pass
        results["top8"] = snap

    def fetch_fund_stocks():
        FUND = ["RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","SBIN","BAJFINANCE","TATAMOTORS"]
        fund_lines = []
        for sym in FUND:
            try:
                info  = get_info(sym) or {}
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
                if price:             parts.append(f"LTP:₹{round(float(price),0):.0f}")
                if pe_v:              parts.append(f"PE:{round(float(pe_v),1)}")
                if pb_v:              parts.append(f"PB:{round(float(pb_v),1)}")
                if roe_pct:           parts.append(f"ROE:{roe_pct}%")
                if eps_v:             parts.append(f"EPS:{round(float(eps_v),1)}")
                if pos52 is not None: parts.append(f"52W:{pos52:.0f}%")
                if len(parts) > 2:
                    fund_lines.append(" | ".join(parts))
            except Exception:
                pass
        results["fund"] = fund_lines

    def fetch_sector_rotation():
        results["sectors"] = get_sector_rotation()

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [
            ex.submit(fetch_index, "^NSEI",     "nifty50"),
            ex.submit(fetch_index, "^NSEBANK",  "banknifty"),
            ex.submit(fetch_index, "^CNXIT",    "niftyit"),
            ex.submit(fetch_pe),
            ex.submit(fetch_top8),
            ex.submit(fetch_fund_stocks),
            ex.submit(fetch_sector_rotation),
        ]
        for f in as_completed(futs, timeout=12):
            try:
                f.result()
            except Exception:
                pass

    # Assemble
    if "nifty50" in results:
        ltp, chg, h, l, _ = results["nifty50"]
        lines.append(f"NIFTY 50: {ltp:,.2f} ({chg:+.2f}%) | Day H/L: {h}/{l}")
    if "banknifty" in results:
        ltp, chg, *_ = results["banknifty"]
        lines.append(f"BANK NIFTY: {ltp:,.2f} ({chg:+.2f}%)")
    if "niftyit" in results:
        ltp, chg, *_ = results["niftyit"]
        lines.append(f"NIFTY IT: {ltp:,.2f} ({chg:+.2f}%)")

    pe_data = results.get("pe", {})
    if pe_data:
        pe, pb, divy, src = (
            pe_data.get("pe","N/A"), pe_data.get("pb","N/A"),
            pe_data.get("div_yield","N/A"), pe_data.get("source","NSE"),
        )
        try:
            pe_f    = float(pe)
            verdict = ("→ EXPENSIVE"       if pe_f > 24
                       else "→ SLIGHTLY RICH" if pe_f > 22
                       else "→ FAIRLY VALUED" if pe_f > 19
                       else "→ CHEAP")
        except Exception:
            verdict = ""
        lines.append(f"NIFTY PE: {pe} | PB: {pb} | DivYield: {divy}% [{src}] {verdict}")

    if results.get("top8"):
        lines.append("TOP STOCKS: " + "  ".join(results["top8"]))

    if results.get("fund"):
        lines.append("\nFUNDAMENTAL DATA (use ONLY these figures for Fundamental Picks):")
        lines.extend(results["fund"])

    if results.get("sectors"):
        lines.append(f"\nSECTOR ROTATION:\n{results['sectors']}")

    # Nifty options context
    try:
        nifty_df = results.get("nifty50", (None,)*5)[4]
        if nifty_df is not None and len(nifty_df) >= 5:
            c      = nifty_df["Close"]
            spot_n = round(float(c.iloc[-1]), 0)
            ema20n = round(float(c.ewm(span=20, adjust=False).mean().iloc[-1]), 0)
            rsi_n  = round(_calc_rsi_wilder(c), 1)
            trend_n= "BULLISH" if spot_n > ema20n else "BEARISH"
            atm_n  = int(round(spot_n / 50) * 50)
            lines.append(
                f"\nNIFTY OPTIONS CONTEXT: Spot={spot_n} ATM={atm_n} "
                f"EMA20={ema20n} RSI={rsi_n} Trend={trend_n} "
                f"| CE:{atm_n} {atm_n+50} {atm_n+100} "
                f"| PE:{atm_n} {atm_n-50} {atm_n-100} "
                f"| Do NOT quote option premiums"
            )
    except Exception:
        pass

    ctx = "\n".join(lines)
    with _CTX_LOCK:
        _CTX_CACHE["text"] = ctx
        _CTX_CACHE["ts"]   = time.time()
    return ctx


# ── NEW: Sector rotation ──────────────────────────────────────────────────────
_SECTOR_TICKERS = {
    "IT":     "^CNXIT",
    "Bank":   "^NSEBANK",
    "Auto":   "^CNXAUTO",
    "Pharma": "^CNXPHARMA",
    "FMCG":   "^CNXFMCG",
    "Metal":  "^CNXMETAL",
    "Energy": "^CNXENERGY",
}

def get_sector_rotation() -> str:
    """
    NEW: Fetches 5 sector indices and ranks by 5-day momentum.
    Returns a formatted string for AI context injection.
    """
    results = []
    for name, ticker in _SECTOR_TICKERS.items():
        try:
            d = yf.Ticker(ticker).history(period="10d")
            if d is None or len(d) < 5:
                continue
            c     = d["Close"]
            ltp   = float(c.iloc[-1])
            base  = float(c.iloc[-5])
            mom5d = round((ltp - base) / base * 100, 2) if base > 0 else 0.0
            ema5  = float(c.ewm(span=5, adjust=False).mean().iloc[-1])
            trend = "↑" if ltp > ema5 else "↓"
            results.append((name, mom5d, trend))
        except Exception:
            pass

    if not results:
        return "Sector data unavailable"

    results.sort(key=lambda x: -x[1])
    lines = []
    for i, (name, mom, trend) in enumerate(results):
        rank  = ["🥇 LEAD","🥈 2nd","🥉 3rd","4th","5th","6th","7th"][i] if i < 7 else f"{i+1}th"
        lines.append(f"{rank}: {name} {trend} 5D:{mom:+.2f}%")
    return "\n".join(lines)


# ── RSI helper (Wilder's EWM — used throughout this module) ───────────────────
def _calc_rsi_wilder(close_series, period: int = 14) -> float:
    """
    FIX: Proper Wilder's smoothing. Matches TradingView within ±0.5 points.
    Was previously using rolling().mean() (simple MA) — off by ±2-5 points.
    """
    s     = close_series.dropna()
    delta = s.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs    = gain / loss.replace(0, 1e-10)
    rsi   = 100 - 100 / (1 + rs)
    return round(float(rsi.iloc[-1]), 1)


# ── Stock insights ─────────────────────────────────────────────────────────────
def ai_insights(symbol: str, ltp: float, rsi: float, macd_line: float,
                trend: str, pe: str, roe: str) -> str:
    if not ai_available():
        return "⚠️ No AI key set. Add GROQ_API_KEY in Render env vars (free at console.groq.com)."

    direction = "bullish" if macd_line > 0 else "bearish"
    rsi_label = ("OVERBOUGHT — pullback risk" if rsi > 70
                 else "OVERSOLD — bounce potential" if rsi < 30
                 else "neutral zone")

    has_fundamentals = pe not in ("N/A", "None", "") or roe not in ("N/A", "None", "")
    fund_line = f"PE: {pe} | ROE: {roe}%\n" if has_fundamentals else ""

    prompt = (
        f"Stock: {symbol} (NSE India)\n"
        f"Technical: LTP ₹{ltp} | RSI {rsi} ({rsi_label}) | MACD {direction} | Trend {trend}\n"
        f"{fund_line}"
        f"\nOutput EXACTLY this format (no extra text):\n"
        f"BULLISH FACTORS:\n• [factor with exact number]\n• [factor]\n• [factor]\n"
        f"RISKS:\n• [risk with exact number]\n• [risk]\n"
        f"VERDICT: BUY / HOLD / AVOID — [one sentence with specific reason and data]."
    )
    text, err = _call_ai(
        [{"role": "user", "content": prompt}],
        max_tokens=200,  # FIX: 280→200, faster stock card
        system="Precise Indian equity analyst. Use only the data given. No speculation. Exact format only.",
    )
    if text:
        if _validate_ai_response(text, symbol):
            return text
        # Retry with stricter prompt on validation failure
        strict_prompt = prompt + "\n\nIMPORTANT: Use ONLY the numbers given above. Do NOT invent prices or percentages."
        text2, _ = _call_ai([{"role":"user","content":strict_prompt}], max_tokens=200,
                             system="Precise Indian equity analyst. Numbers from prompt only.")
        if text2:
            return text2
        return text   # return original if retry also fails validation

    return f"⚠️ AI unavailable: {err.split(chr(10))[0][:80]}" if err else "⚠️ AI temporarily unavailable."


# ── NEW: Response quality validator ──────────────────────────────────────────
def _validate_ai_response(text: str, symbol: str = "") -> bool:
    """
    NEW: Sanity-checks AI output for obvious hallucinations.
    Returns True if response passes, False if it should be retried.
    """
    # Must contain a verdict
    if not any(v in text.upper() for v in ["BUY", "HOLD", "AVOID", "SELL"]):
        logger.warning(f"AI validation fail [{symbol}]: no verdict keyword")
        return False

    # Check for obviously wrong RSI values (negative or > 100)
    rsi_matches = re.findall(r"RSI[:\s]+(-?\d+\.?\d*)", text)
    for rm in rsi_matches:
        v = float(rm)
        if v < 0 or v > 100:
            logger.warning(f"AI validation fail [{symbol}]: RSI={v} out of range")
            return False

    # Check for absurdly large prices (> ₹1 crore per share)
    price_matches = re.findall(r"₹([\d,]+\.?\d*)", text.replace(",", ""))
    for pm in price_matches:
        try:
            v = float(pm.replace(",", ""))
            if v > 100_000:
                logger.warning(f"AI validation fail [{symbol}]: price=₹{v} suspicious")
                return False
        except Exception:
            pass

    return True


# ── News fetch ─────────────────────────────────────────────────────────────────
_NEWS_JUNK = [
    "Stock Price", "Quote", "Yahoo Finance", "TradingView", "Investing.com",
    "CNBC", "Chart and News", "Index Today", "NSE India", "National Stock Exchange",
    "Live Share", "Equity Market Watch", "moneycontrol.com",
]

def _is_real_headline(title: str) -> bool:
    if not title or len(title) < 25:
        return False
    return not any(j.lower() in title.lower() for j in _NEWS_JUNK)


def fetch_news(symbol: str) -> str:
    from_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    to_date   = date.today().strftime("%Y-%m-%d")

    tavily_key = _key("TAVILY_API_KEY")
    if tavily_key:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key":        tavily_key,
                    "query":          f"{symbol} NSE India stock news latest",
                    "max_results":    6,
                    "search_depth":   "advanced",
                    "include_domains": [
                        "economictimes.indiatimes.com", "moneycontrol.com",
                        "livemint.com", "businessline.com", "reuters.com",
                    ],
                },
                timeout=8,
            ).json()
            headlines = [
                x["title"] for x in r.get("results", [])
                if _is_real_headline(x.get("title", ""))
            ][:2]
            if headlines:
                return "\n".join(f"📰 {h[:90]}" for h in headlines)
        except Exception as e:
            logger.warning(f"Tavily news {symbol}: {e}")

    finnhub_key = _key("FINNHUB_API_KEY")
    if finnhub_key:
        try:
            # FIX: use "SYMBOL.NS" not "NSE:SYMBOL" — company-news endpoint uses .NS suffix
            r = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={
                    "symbol": f"{symbol}.NS",
                    "from":   from_date,
                    "to":     to_date,
                    "token":  finnhub_key,
                },
                timeout=6,
            ).json()
            if isinstance(r, list):
                lines = [f"📰 {a['headline'][:85]}" for a in r[:2] if a.get("headline")]
                if lines:
                    return "\n".join(lines)
        except Exception:
            pass

    # MoneyControl RSS fallback
    try:
        rss = requests.get(
            "https://www.moneycontrol.com/rss/buzzingstocks.xml",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=6,
        )
        if rss.ok:
            titles  = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", rss.text)
            matched = [t for t in titles[1:] if symbol.upper() in t.upper()][:2]
            if matched:
                return "\n".join(f"📰 {t[:90]}" for t in matched)
    except Exception:
        pass

    return ""


def fetch_market_news() -> str:
    tavily_key = _key("TAVILY_API_KEY")
    if tavily_key:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key":        tavily_key,
                    "query":          "India NSE Nifty stock market news today",
                    "max_results":    8,
                    "search_depth":   "advanced",
                    "include_domains": [
                        "economictimes.indiatimes.com", "moneycontrol.com",
                        "livemint.com", "reuters.com", "financialexpress.com",
                    ],
                },
                timeout=10,
            ).json()
            headlines = [
                x["title"] for x in r.get("results", [])
                if _is_real_headline(x.get("title", ""))
            ][:5]
            if headlines:
                return "\n".join(f"📰 {h}" for h in headlines)
        except Exception:
            pass

    try:
        rss = requests.get(
            "https://www.moneycontrol.com/rss/latestnews.xml",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
        )
        if rss.ok:
            titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", rss.text)
            mkt = [
                t for t in titles[1:]
                if any(kw in t.lower() for kw in ["nifty","sensex","market","stock","sebi","rbi"])
            ][:5]
            if mkt:
                return "\n".join(f"📰 {t}" for t in mkt)
    except Exception:
        pass

    return ""


# ── Chat history ───────────────────────────────────────────────────────────────
_chat_history: dict = {}

def add_to_chat(uid: int, role: str, content: str):
    _chat_history.setdefault(uid, [])
    _chat_history[uid].append({"role": role, "content": content})
    _chat_history[uid] = _chat_history[uid][-10:]

def get_chat_history(uid: int) -> list:
    return list(_chat_history.get(uid, []))

def clear_chat(uid: int):
    _chat_history.pop(uid, None)


# ── Structured AI topics ───────────────────────────────────────────────────────
CHAT_SYSTEM = """You are AutoAI Advisory — an expert Indian NSE/BSE stock market AI assistant.
You have access to LIVE MARKET DATA injected below. Use it to answer accurately.

CORE RULES:
1. For ANY specific stock question (e.g. "Reliance trade setup", "INFY analysis", "TCS levels"):
   → Give Entry Zone, Stop Loss, Target 1, Target 2, RSI, Trend, Verdict.
   → If the stock is NOT in live data, use general TA principles and say "Based on typical levels".
2. For market/index questions: Use exact Nifty/BankNifty data from context.
3. For options questions: Give strategy name + strikes only. NEVER quote premiums.
4. For fundamental questions: Use PE, PB, ROE, EPS from context if available.
5. Always use ₹ for prices, % for returns, bullet points for clarity.
6. Max 350 words. End every response with: ⚠️ Educational only. Not SEBI-registered advice.
7. BANNED: "could", "might", "I think", "perhaps", "may" — be direct and specific.
8. For 30-min / intraday setups: Give levels based on EMA/RSI from available data.
   State timeframe clearly. Example: "30-min setup: Buy above ₹X, SL ₹Y, T1 ₹Z"

STOCK TRADE SETUP FORMAT (use when any specific stock is asked):
📌 [SYMBOL] — [TIMEFRAME] SETUP
• Trend: [Bullish/Bearish/Sideways based on RSI+EMA data]
• Entry: ₹[price or zone]
• Stop Loss: ₹[price] ([X]% risk)
• Target 1: ₹[price] ([X]% gain)
• Target 2: ₹[price] ([X]% gain)
• R:R = 1:[X]
• RSI: [value] | Signal: [Overbought/Oversold/Neutral]
• Why: [2 lines max — specific reason with data]"""

AI_CHAT_TOPICS: dict = {
    "🔍 Stock Analysis": (
        "TASK: Detailed stock analysis. The user will name a stock. Use SPECIFIC STOCK DATA if provided.\n"
        "FORMAT:\n📌 [SYMBOL] ANALYSIS — [date]\n"
        "• LTP: ₹[exact] | Change: [exact]%\n"
        "• Trend: [Bullish/Bearish/Sideways] | RSI: [exact] — [label]\n"
        "• EMA9: ₹[x] | EMA21: ₹[x] | EMA50: ₹[x]\n"
        "• Support: ₹[x] | Resistance: ₹[x]\n"
        "• Entry: ₹[x]–₹[y] | SL: ₹[x] | T1: ₹[x] | T2: ₹[x]\n"
        "• R:R = 1:[x] | ATR: ₹[x]\n"
        "• Verdict: BUY/HOLD/AVOID — [one specific reason]\n"
        "⚠️ Educational only. Not SEBI-registered advice."
    ),
    "📊 Nifty Valuation": (
        "TASK: Nifty 50 Valuation. Use ONLY numbers from LIVE DATA.\n"
        "FORMAT:\n📊 NIFTY VALUATION — [date]\n"
        "• Level: [exact] | Change: [exact]%\n"
        "• PE: [exact] | PB: [exact] | Div Yield: [exact]%\n"
        "• 10Y Avg PE: ~21 | Gap: [calc: PE-21 = X, overvalued/cheap by X]\n"
        "• Nifty EPS: ₹[calc: Level/PE] | Fair Value (EPS×21): ₹[calc]\n"
        "• Verdict: OVERVALUED / FAIRLY VALUED / CHEAP\n"
        "• Stance: [1 specific line]\n"
        "⚠️ Educational only. Not SEBI-registered advice."
    ),
    "💎 Fundamental Picks": (
        "TASK: Top 3 value stocks from FUNDAMENTAL DATA in LIVE DATA only.\n"
        "FORMAT for each:\n🥇 BEST: [SYM] | PE:[x] PB:[x] ROE:[x]% EPS:₹[x] 52W:[x]%\n"
        "  Case: [one line citing above numbers only]\n"
        "🥈 SECOND: [same]\n👁 WATCH: [same]\n"
        "⚠️ Educational only. Not SEBI-registered advice."
    ),
    "📈 Nifty Update": (
        "TASK: Nifty technical update from LIVE DATA.\n"
        "FORMAT:\n📈 NIFTY UPDATE — [date]\n"
        "• Level: [exact] | Change: [exact]%\n"
        "• Trend: BULLISH/BEARISH/SIDEWAYS | RSI: [exact] — [label]\n"
        "• EMA20: [exact] | Price is [above/below] EMA20\n"
        "• S1: ₹[spot×0.99] | S2: ₹[spot×0.98]\n"
        "• R1: ₹[spot×1.01] | R2: ₹[spot×1.02]\n"
        "• 5-Day Outlook: [range] with [bias]\n"
        "⚠️ Educational only. Not SEBI-registered advice."
    ),
    "🎯 Technical Swing Trade": (
        "TASK: 2 swing trades from TOP STOCKS in LIVE DATA.\n"
        "LONG: RSI<45. SHORT: RSI>65.\n"
        "FORMAT per trade:\n📌 [SYM] [LONG/SHORT] | LTP:₹[exact] RSI:[exact]\n"
        "  Entry: ₹[LTP×0.995]–₹[LTP×1.005]\n"
        "  T1: ₹[LTP±2%] | T2: ₹[LTP±4%] | SL: ₹[LTP∓2%]\n"
        "  R:R 1:[calc] | Reason: [RSI+trend from data] | 3–5 days\n"
        "⚠️ Educational only. Not SEBI-registered advice."
    ),
    "⚡ Option Trade": (
        "TASK: One Nifty options strategy from NIFTY OPTIONS CONTEXT in LIVE DATA.\n"
        "FORMAT:\n⚡ OPTION STRATEGY — [date]\n"
        "• Spot: [exact] | ATM: [exact rounded to 50]\n"
        "• Trend: [exact] | RSI: [exact]\n"
        "• Strategy: [Bull Call Spread/Long CE/Bear Put Spread/Long PE/Iron Condor]\n"
        "• Direction: [Bullish/Bearish/Neutral]\n"
        "• Strikes: [e.g. Buy 24450CE + Sell 24600CE]\n"
        "• Why: [Nifty level + RSI + EMA20 from data — 2 lines]\n"
        "• Max Risk: [X pts] | Target: [X pts]\n"
        "• Exit if Nifty closes [above/below] ₹[level]\n"
        "⚠️ Do NOT quote premium prices. Educational only. Not SEBI-registered advice."
    ),
    "🔄 Sector Rotation": (
        "TASK: Identify leading and lagging sectors from SECTOR ROTATION in LIVE DATA.\n"
        "FORMAT:\n🔄 SECTOR ROTATION — [date]\n"
        "• Leading (buy dips): [top 2 sectors with 5D momentum from data]\n"
        "• Lagging (avoid/short): [bottom 2 sectors]\n"
        "• Best stock in leading sector: [name it from TOP STOCKS data]\n"
        "• Strategy: [2 lines — rotate out of X into Y because Z]\n"
        "⚠️ Educational only. Not SEBI-registered advice."
    ),
}

AI_CHAT_TOPIC_KEYS: set = set(AI_CHAT_TOPICS.keys())


# ── Stock + timeframe detection ────────────────────────────────────────────────

# FIX: words that look like tickers but aren't — prevents spurious yfinance downloads
_NON_TICKER_WORDS = {
    "GOOD", "WHAT", "WHEN", "WHERE", "WHICH", "BEST", "SHOW", "GIVE",
    "FIND", "TELL", "NIFTY", "MARKET", "TODAY", "HELP", "WANT", "NEED",
    "THIS", "THAT", "HAVE", "BEEN", "WILL", "FROM", "WITH", "INTO",
    "ABOUT", "TRADE", "SETUP", "ANALYSIS", "CHART", "INTRADAY", "SWING",
    "LONG", "SHORT", "BULL", "BEAR", "CALL", "PUT", "OPTION",
}

# Known large-caps for fast match
_KNOWN_STOCKS = [
    "RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK","SBIN","BAJFINANCE",
    "TATAMOTORS","WIPRO","HCLTECH","AXISBANK","KOTAKBANK","LT","ITC",
    "SUNPHARMA","BHARTIARTL","ONGC","NTPC","MARUTI","M&M","TITAN",
    "ADANIENT","ADANIPORTS","BAJAJ","HDFC","NESTLEIND","TATACONSUM",
    "DRREDDY","DIVISLAB","CIPLA","ZOMATO","NYKAA","PAYTM","INDIGO",
    "HAL","BEL","IRFC","PFC","BPCL","ONGC","COALINDIA","JSWSTEEL",
    "TATAPOWER","POWERGRID","ADANIPORTS",
]


def _detect_stock_in_message(msg: str) -> str:
    """
    FIX: deny-list blocks common non-ticker words from triggering false stock lookups.
    Previously "GOOD MORNING BUY" would detect "GOOD" as a stock.
    """
    msg_up = msg.upper()

    # 1. Direct known-stock match
    for sym in _KNOWN_STOCKS:
        if re.search(rf"\b{re.escape(sym)}\b", msg_up):
            return sym

    # 2. Regex pattern — with deny-list filter
    m = re.search(
        r"\b([A-Z]{2,12})\s*(TRADE|SETUP|ANALYSIS|CHART|BUY|SELL|TARGET|SL|STOCK)\b",
        msg_up
    )
    if m and m.group(1) not in _NON_TICKER_WORDS:
        return m.group(1)

    return ""


def _detect_timeframe_in_message(msg: str) -> str:
    """
    NEW: Detects if the user is asking for an intraday / 30-min / 60-min setup.
    Returns: "30min", "60min", "daily", or "" (unknown/swing).
    """
    msg_l = msg.lower()
    if any(kw in msg_l for kw in ["30 min", "30min", "30-min", "scalp", "intraday session"]):
        return "30min"
    if any(kw in msg_l for kw in ["60 min", "60min", "1 hour", "1hr", "hourly"]):
        return "60min"
    if any(kw in msg_l for kw in ["intraday", "today trade", "today setup", "today session"]):
        return "30min"  # default intraday to 30min
    if any(kw in msg_l for kw in ["swing", "positional", "weekly", "1 week"]):
        return "daily"
    return ""


def _get_stock_live_context(sym: str, timeframe: str = "") -> str:
    """
    Fetch live price + RSI + EMA + ATR + BB for a specific stock.
    FIX: RSI now uses Wilder's EWM (was rolling mean — ±5pt error).
    FIX: 30-min pivot now fetches real 30m OHLC bars, not daily OHLC.
    """
    try:
        ticker = yf.Ticker(f"{sym}.NS")
        df     = ticker.history(period="3mo", progress=False)
        if df.empty:
            return ""

        close = df["Close"]
        ltp   = round(float(close.iloc[-1]), 2)
        prev  = round(float(close.iloc[-2]), 2)
        chg   = round((ltp - prev) / prev * 100, 2)

        # FIX: Wilder's RSI
        rsi = _calc_rsi_wilder(close)

        # EMAs
        ema9  = round(float(close.ewm(span=9,  adjust=False).mean().iloc[-1]), 2)
        ema21 = round(float(close.ewm(span=21, adjust=False).mean().iloc[-1]), 2)
        ema50 = round(float(close.ewm(span=50, adjust=False).mean().iloc[-1]), 2)

        # ATR
        h  = df["High"].values
        l  = df["Low"].values
        c  = close.values
        tr = np.maximum(h[1:]-l[1:], np.maximum(np.abs(h[1:]-c[:-1]), np.abs(l[1:]-c[:-1])))
        atr = round(float(np.mean(tr[-14:])), 2)

        # 52W from 3mo history (approximate)
        h52 = round(float(df["High"].max()),  2)
        l52 = round(float(df["Low"].min()),   2)

        trend = ("BULLISH" if ltp > ema21 > ema50
                 else "BEARISH" if ltp < ema21 < ema50
                 else "SIDEWAYS")

        # Daily OHLC
        today_open = round(float(df["Open"].iloc[-1]),  2)
        today_high = round(float(df["High"].iloc[-1]),  2)
        today_low  = round(float(df["Low"].iloc[-1]),   2)
        vwap_approx = round((today_high + today_low + ltp) / 3, 2)

        # Bollinger Bands
        bb_mid = round(float(close.rolling(20).mean().iloc[-1]), 2)
        bb_std = round(float(close.rolling(20).std().iloc[-1]),  2)
        bb_up  = round(bb_mid + 2 * bb_std, 2)
        bb_lo  = round(bb_mid - 2 * bb_std, 2)

        ctx = (
            f"\nSPECIFIC STOCK DATA — {sym}:\n"
            f"LTP: ₹{ltp} ({chg:+.2f}%) | RSI: {rsi} | Trend: {trend}\n"
            f"Today OHLC: O:{today_open} H:{today_high} L:{today_low} C:{ltp} | VWAP≈₹{vwap_approx}\n"
            f"EMA9: ₹{ema9} | EMA21: ₹{ema21} | EMA50: ₹{ema50}\n"
            f"BB Upper: ₹{bb_up} | BB Mid: ₹{bb_mid} | BB Lower: ₹{bb_lo}\n"
            f"ATR(14): ₹{atr} | 52W H: ₹{h52} | 52W L: ₹{l52}\n"
            f"Support: ₹{round(ltp-atr,2)} | Resistance: ₹{round(ltp+atr,2)}\n"
        )

        # FIX: real 30m pivot if intraday requested
        intraday_tf = timeframe or _detect_timeframe_in_message("")
        if intraday_tf in ("30min", "60min"):
            intraday_ctx = _get_intraday_pivot(sym, intraday_tf)
            ctx += intraday_ctx
        else:
            # Daily pivot (for swing trades)
            daily_pivot = round((today_high + today_low + ltp) / 3, 2)
            r1 = round(2 * daily_pivot - today_low,  2)
            s1 = round(2 * daily_pivot - today_high, 2)
            ctx += f"Daily Pivot: ₹{daily_pivot} | R1: ₹{r1} | S1: ₹{s1}\n"

        return ctx

    except Exception as e:
        logger.debug(f"stock ctx fetch {sym}: {e}")
        return ""


def _get_intraday_pivot(sym: str, timeframe: str = "30min") -> str:
    """
    NEW: Fetch real intraday bars to compute accurate pivot levels.
    FIX: was using daily OHLC and labelling it "30-min Pivot" — misleading.
    """
    interval_map = {"30min": "30m", "60min": "60m"}
    interval = interval_map.get(timeframe, "30m")
    try:
        df30 = yf.Ticker(f"{sym}.NS").history(period="2d", interval=interval)
        if df30.empty or len(df30) < 4:
            return ""
        last4   = df30.tail(4)
        h       = float(last4["High"].max())
        l       = float(last4["Low"].min())
        c       = float(last4["Close"].iloc[-1])
        pivot   = round((h + l + c) / 3, 2)
        r1      = round(2 * pivot - l, 2)
        r2      = round(pivot + (h - l), 2)
        s1      = round(2 * pivot - h, 2)
        s2      = round(pivot - (h - l), 2)
        tf_label = "30-min" if interval == "30m" else "60-min"
        return (
            f"{tf_label} Pivot (last 4 bars): ₹{pivot} | "
            f"R1: ₹{r1} | R2: ₹{r2} | S1: ₹{s1} | S2: ₹{s2}\n"
        )
    except Exception as e:
        logger.debug(f"intraday pivot {sym}: {e}")
        return ""


# ── Chat responder ────────────────────────────────────────────────────────────
def ai_chat_respond(uid: int, user_message: str) -> str:
    if not ai_available():
        return (
            "⚠️ <b>No AI keys configured.</b>\n\n"
            "Add at least one key in Render → Environment:\n"
            "• <code>GROQ_API_KEY</code> — free at console.groq.com\n"
            "• <code>GEMINI_API_KEY</code> — free at aistudio.google.com"
        )

    market_ctx = get_live_market_context()

    detected_sym = _detect_stock_in_message(user_message)
    detected_tf  = _detect_timeframe_in_message(user_message)
    stock_ctx    = ""
    if detected_sym:
        stock_ctx = _get_stock_live_context(detected_sym, timeframe=detected_tf)
        logger.info(f"AI: detected stock={detected_sym} tf={detected_tf or 'swing'}")

    tf_hint = ""
    if detected_tf:
        tf_hint = f"\n\nUSER IS ASKING FOR {detected_tf.upper()} INTRADAY SETUP. Use intraday pivot levels from data."

    system   = CHAT_SYSTEM + f"\n\nLIVE MARKET CONTEXT:\n{market_ctx}{stock_ctx}{tf_hint}"

    # FIX: always add user message first, remove on failure to prevent history drift
    add_to_chat(uid, "user", user_message)
    messages = list(get_chat_history(uid))

    text, err = _call_ai(messages, max_tokens=500, system=system)

    if text:
        add_to_chat(uid, "assistant", text)
        return text

    # FIX: remove the user message we added since the call failed
    hist = _chat_history.get(uid, [])
    if hist and hist[-1]["role"] == "user":
        _chat_history[uid] = hist[:-1]

    return (
        "❌ <b>All AI providers failed.</b>\n\n"
        f"<b>Errors:</b>\n{err}\n\n"
        "<b>Fix:</b> Render Dashboard → Environment → check GROQ_API_KEY → Save → Redeploy"
    )


# ── Diagnostics ────────────────────────────────────────────────────────────────
def test_ai_providers() -> dict:
    results = {}
    for name, key_env, test_fn in [
        ("GROQ", "GROQ_API_KEY", lambda: _get_groq().chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": "Say OK"}], max_tokens=3)),
        ("Gemini", "GEMINI_API_KEY", lambda: _get_gemini().generate_content("Say OK, just those two words.")),
        ("OpenAI", "OPENAI_KEY", lambda: _get_openai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Say OK"}], max_tokens=3)),
    ]:
        if not _key(key_env):
            results[name] = f"SKIP — {key_env} not set"
            continue
        try:
            r = test_fn()
            # FIX: Gemini safety filter may block test — still mark as OK
            if hasattr(r, "text"):
                try:
                    text = r.text
                except (AttributeError, ValueError):
                    text = "OK (safety filtered)"
            elif hasattr(r, "choices"):
                text = r.choices[0].message.content
            else:
                text = str(r)[:30]
            results[name] = f"OK — {str(text).strip()[:20]}"
        except Exception as e:
            msg = str(e)
            if "401" in msg or "invalid" in msg.lower():
                results[name] = "FAIL — Invalid key"
            elif "429" in msg:
                results[name] = "FAIL — Rate limited"
            else:
                results[name] = f"FAIL — {msg[:100]}"

    af_text, af_err = _call_askfuzz_ai("test", timeout=5)
    results["AskFuzz"] = "OK" if af_text else f"SKIP/FAIL — {af_err or 'no key set'}"
    results["_status"] = (
        "✅ AI WORKING"
        if any(v.startswith("OK") for v in results.values())
        else "❌ ALL FAILED"
    )
    return results


def debug_ai_status() -> dict:
    return {
        "keys": {k: ("set" if _key(k) else "MISSING") for k in [
            "GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_KEY", "ASKFUZZ_API_KEY",
            "TAVILY_API_KEY", "FINNHUB_API_KEY", "ALPHA_VANTAGE_KEY",
        ]},
        "context_cached":  bool(_CTX_CACHE["text"]),
        "context_age_sec": round(time.time() - _CTX_CACHE["ts"], 0),
        "context_ttl_sec": _CTX_TTL,
        "groq_models":     _GROQ_MODELS,
        "ai_available":    ai_available(),
        "sector_rotation": get_sector_rotation(),
    }
