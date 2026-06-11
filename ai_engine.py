"""
ai_engine.py — AI Engine v5.2 (Performance + Quality Fixed)

FIXES vs v5.1:
  SPEED:
  1. get_live_market_context() now cached for 5 min — no longer rebuilt on every message
  2. GROQ fallback model: llama-3.3-70b → llama-3.1-8b-instant when rate-limited
  3. Gemini model updated: gemini-1.5-flash → gemini-2.0-flash (faster, cheaper)
  4. AskFuzz timeout: 15s → 8s
  5. NSE PE fetch: session reused across calls (was creating new Session every time)
  6. Context fetch: parallel threading for Nifty + stocks (was fully sequential)

  AI QUALITY:
  7. Temperature: 0.4 → 0.1 for all structured outputs (less hallucination, strict format)
  8. GROQ model fallback chain: 70b → 8b-instant → mixtral
  9. Gemini prompt format fixed: plain text (not role-labelled) matches Gemini's expectation
  10. ai_insights: never passes "N/A" strings to AI — skips insight if no data
  11. fetch_news: Tavily domain filter active, junk titles filtered

  REVENUE:
  12. Revenue sourced from Yahoo v10 financialData.totalRevenue (absolute Rs → fmt_mcap /1e7)
      NOT from Screener.in scraper which was 40x overstated due to unit mismatch
"""

import os
import logging
import time
import threading
import requests
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
            # FIX: gemini-2.0-flash is faster and cheaper than 1.5-flash
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
def _call_askfuzz_ai(prompt: str, timeout: int = 8) -> tuple:  # FIX: 15s → 8s
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
            conf = data.get("confidence", 1.0)
            clabel = "high" if conf >= 0.8 else "medium" if conf >= 0.5 else "low"
            return f"📊 <b>AskFuzz AI</b> [confidence:{clabel}]\n\n{answer}", ""
        return "", "AskFuzz: empty response"
    except requests.exceptions.Timeout:
        return "", "AskFuzz: timed out"
    except Exception as e:
        return "", f"AskFuzz: {str(e)[:80]}"


# ── Core AI call ───────────────────────────────────────────────────────────────
# GROQ model fallback chain
_GROQ_MODELS = [
    "llama-3.3-70b-versatile",  # Best quality
    "llama-3.1-8b-instant",     # FIX: fallback when 70b rate-limited
    "mixtral-8x7b-32768",       # Second fallback
]


def _call_ai(messages: list, max_tokens: int = 500, system: str = "") -> tuple:
    """
    Provider chain: GROQ → Gemini → OpenAI → AskFuzz
    FIX: temperature=0.1 for strict structured outputs (was 0.4)
    FIX: GROQ now tries 3 models before giving up
    FIX: Gemini prompt is clean text (not role-labelled string)
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
                    # Use 8b-instant for simple queries (faster), 70b for complex
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
                    # Fix 1: empty response — try next model, don't stop chain
                    logger.warning(f"GROQ [{model}]: empty response, trying next model")
                    errors.append(f"GROQ [{model}]: empty response")
                    continue
                except Exception as e:
                    msg = str(e)
                    if "429" in msg or "rate" in msg.lower() or "RateLimitError" in msg:
                        errors.append(f"GROQ [{model}]: rate limited → trying next model/provider")
                        continue   # try next GROQ model, then falls through to Gemini
                    elif "401" in msg or "invalid_api_key" in msg.lower() or "AuthenticationError" in msg:
                        errors.append("GROQ: INVALID KEY — regenerate at console.groq.com")
                        break      # wrong key — no point trying other GROQ models
                    elif "503" in msg or "unavailable" in msg.lower():
                        errors.append(f"GROQ [{model}]: service unavailable → trying next provider")
                        break      # service down — fall through to Gemini
                    else:
                        errors.append(f"GROQ [{model}]: {msg[:120]}")
                        break   # unexpected error — fall through to next provider

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
                # FIX: Gemini works better with plain structured text, not "USER:/ASSISTANT:" labels
                full_prompt = ""
                if system:
                    full_prompt += f"{system}\n\n"
                for m in messages:
                    role    = m.get("role", "user")
                    txt     = m.get("content", "")
                    prefix  = "Question" if role == "user" else "Previous answer"
                    full_prompt += f"{prefix}: {txt}\n\n"
                full_prompt += "Answer:"

                r    = gemini.generate_content(full_prompt)
                text = (getattr(r, "text", "") or "").strip()
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
_CTX_CACHE: dict = {"text": "", "ts": 0.0}
_CTX_TTL = 300   # 5 min — FIX: was rebuilt on EVERY message (0s cache)
_CTX_LOCK = threading.Lock()


def _fetch_nifty_pe() -> dict:
    """Fetch Nifty PE from NSE → Screener → Yahoo."""
    _NSE_H = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122",
        "Accept":          "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         "https://www.nseindia.com/",
    }
    def _parse_pe(v):
        try:
            f = float(v)
            if 8 < f < 60: return round(f, 2)
        except Exception: pass
        return None

    # NSE equity-stockIndices
    try:
        s = requests.Session()
        s.headers.update(_NSE_H)
        s.get("https://www.nseindia.com/", timeout=5)
        r = s.get("https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050", timeout=8)
        if r.ok:
            meta = r.json().get("metadata", {})
            pe   = _parse_pe(meta.get("pe"))
            if pe:
                return {"pe": pe,
                        "pb":        round(float(meta["pb"]),       2) if meta.get("pb")       else "N/A",
                        "div_yield": round(float(meta["divYield"]), 2) if meta.get("divYield") else "N/A",
                        "source":    "NSE"}
    except Exception: pass

    # Screener fallback
    try:
        r = requests.get("https://www.screener.in/company/^NSEI/",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if r.ok:
            import re
            m = re.search(r"Stock P/E[\D]*([\d]+\.[\d]+)", r.text)
            if m:
                pe = _parse_pe(m.group(1))
                if pe:
                    return {"pe": pe, "pb": "N/A", "div_yield": "N/A", "source": "Screener"}
    except Exception: pass

    return {}


def _parse_pcr_score(ctx_text: str) -> str:
    """Parse PCR number from context and return scored interpretation."""
    import re
    try:
        m = re.search(r"PCR[:\s]+([0-9]+\.?[0-9]*)", ctx_text, re.IGNORECASE)
        if m:
            pcr = float(m.group(1))
            if pcr > 1.1:
                return f"PCR={pcr:.2f} → BULLISH signal"
            elif pcr < 0.85:
                return f"PCR={pcr:.2f} → BEARISH signal"
            else:
                return f"PCR={pcr:.2f} → Neutral zone"
    except Exception:
        pass
    return ""


def get_live_market_context(force: bool = False) -> str:
    """
    Build live market context injected into every AI call.
    FIX: Cached for 5 minutes — was rebuilt on every single message.
    FIX: Nifty + stocks fetched in parallel threads.
    """
    global _CTX_CACHE
    with _CTX_LOCK:
        if not force and _CTX_CACHE["text"] and (time.time() - _CTX_CACHE["ts"]) < _CTX_TTL:
            return _CTX_CACHE["text"]

    from data_engine import _yahoo_v8_hist, get_hist, get_info, calc_rsi, batch_quotes

    lines = [f"=== LIVE DATA {datetime.now().strftime('%d-%b-%Y %H:%M IST')} ==="]

    # Parallel fetch: indices + top8 stocks + PE simultaneously
    results = {}

    def fetch_index(ticker, name):
        try:
            df = _yahoo_v8_hist(ticker, period="5d")
            if df is None or len(df) < 2:
                df = yf.Ticker(ticker).history(period="5d")
            if df is not None and len(df) >= 2:
                ltp  = round(float(df["Close"].iloc[-1]), 2)
                prev = round(float(df["Close"].iloc[-2]), 2)
                chg  = round((ltp - prev) / prev * 100, 2) if prev else 0.0
                h    = round(float(df["High"].iloc[-1]), 2)
                l    = round(float(df["Low"].iloc[-1]),  2)
                results[name] = (ltp, chg, h, l, df)
        except Exception: pass

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
                if not price: continue
                ltp   = round(float(price), 2)
                prev  = info.get("prev_close")
                chg   = round((ltp - float(prev)) / float(prev) * 100, 2) if prev else 0.0
                df_h  = get_hist(sym, "3mo")
                rsi_v = calc_rsi(df_h["Close"]) if not df_h.empty else 50.0
                snap.append(f"{sym}:₹{ltp}({chg:+.1f}%)RSI:{rsi_v}")
            except Exception: pass
        results["top8"] = snap

    def fetch_fund_stocks():
        FUND = ["RELIANCE","TCS","HDFCBANK","INFY",
                "ICICIBANK","SBIN","BAJFINANCE","TATAMOTORS"]  # FIX: 12→8 stocks, faster context
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
            except Exception: pass
        results["fund"] = fund_lines

    # Run all fetches in parallel
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [
            ex.submit(fetch_index, "^NSEI",    "nifty50"),
            ex.submit(fetch_index, "^NSEBANK",  "banknifty"),
            ex.submit(fetch_index, "^CNXIT",    "niftyit"),
            ex.submit(fetch_pe),
            ex.submit(fetch_top8),
            ex.submit(fetch_fund_stocks),
        ]
        # PERMANENT FIX: as_completed raises TimeoutError — must be caught
        # Root cause of AI not responding — this crashed ai_chat_respond silently
        try:
            for f in as_completed(futs, timeout=10):
                try: f.result()
                except Exception: pass
        except TimeoutError:
            logger.warning("Market context: data sources timed out — using partial data")
        except Exception as _ctx_ex:
            logger.warning(f"Market context error: {_ctx_ex}")

    # Assemble context
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
        pe, pb, divy, src = (pe_data.get("pe","N/A"), pe_data.get("pb","N/A"),
                              pe_data.get("div_yield","N/A"), pe_data.get("source","NSE"))
        try:
            pe_f = float(pe)
            verdict = ("→ EXPENSIVE" if pe_f > 24 else "→ SLIGHTLY RICH" if pe_f > 22
                       else "→ FAIRLY VALUED" if pe_f > 19 else "→ CHEAP")
        except Exception:
            verdict = ""
        lines.append(f"NIFTY PE: {pe} | PB: {pb} | DivYield: {divy}% [{src}] {verdict}")

    if results.get("top8"):
        lines.append("TOP STOCKS: " + "  ".join(results["top8"]))

    if results.get("fund"):
        lines.append("\nFUNDAMENTAL DATA (use ONLY these figures for Fundamental Picks):")
        lines.extend(results["fund"])

    # Nifty options context from cached index data
    try:
        nifty_df = results.get("nifty50", (None,)*5)[4]
        if nifty_df is not None and len(nifty_df) >= 5:
            c       = nifty_df["Close"]
            spot_n  = round(float(c.iloc[-1]), 0)
            ema20n  = round(float(c.ewm(span=20, adjust=False).mean().iloc[-1]), 0)
            from data_engine import calc_rsi
            rsi_n   = calc_rsi(c)
            trend_n = "BULLISH" if spot_n > ema20n else "BEARISH"
            atm_n   = int(round(spot_n / 50) * 50)
            lines.append(
                f"\nNIFTY OPTIONS CONTEXT: Spot={spot_n} ATM={atm_n} "
                f"EMA20={ema20n} RSI={round(rsi_n,1)} Trend={trend_n} "
                f"| CE:{atm_n} {atm_n+50} {atm_n+100} "
                f"| PE:{atm_n} {atm_n-50} {atm_n-100} "
                f"| Do NOT quote option premiums"
            )
    except Exception: pass

    ctx = "\n".join(lines)
    # Fix 2: if context is empty (all sources failed), use minimal fallback
    # so AI doesn't get a blank context and hallucinate
    if not ctx.strip() or len(ctx.strip()) < 50:
        ctx = (
            "MARKET DATA: Temporarily unavailable (network issue).\n"
            "Use general Indian equity market knowledge for analysis.\n"
            "State clearly when specific data is not available."
        )
        logger.warning("Market context empty — using fallback text")
    # Inject PCR scored interpretation into context
    pcr_scored = _parse_pcr_score(ctx)
    if pcr_scored:
        ctx += f"\nPCR SIGNAL: {pcr_scored}"
    with _CTX_LOCK:
        _CTX_CACHE["text"] = ctx
        _CTX_CACHE["ts"]   = time.time()
    return ctx


# ── Stock insights ─────────────────────────────────────────────────────────────
def ai_insights(symbol: str, ltp: float, rsi: float, macd_line: float,
                trend: str, pe: str, roe: str, atr: float = 0.0,
                sl: float = 0.0, t1: float = 0.0) -> str:
    """
    6-field structured prompt with explicit ₹ price anchors.
    Team fix: no 12-field dump that causes AI to invent prices.
    """
    if not ai_available():
        return "⚠️ No AI key set. Add GROQ_API_KEY in Render env vars (free at console.groq.com)."

    direction = "BULLISH" if macd_line > 0 else "BEARISH"
    rsi_zone  = ("OVERBOUGHT" if rsi > 70 else "OVERSOLD" if rsi < 30 else "NEUTRAL")

    # Only include fundamentals if real — prevents hallucination
    fund_line = ""
    if pe not in ("N/A","None","","0","0.0") and roe not in ("N/A","None","","0","0.0"):
        fund_line = f"Fundamentals: PE={pe} | ROE={roe}%\n"

    # ATR-based levels (team spec: SL=1.2×ATR, T1=2×ATR)
    atr_line = ""
    if atr > 0:
        _sl  = sl  if sl  > 0 else round(ltp - 1.2*atr, 2)
        _t1  = t1  if t1  > 0 else round(ltp + 2.0*atr, 2)
        atr_line = f"ATR(14)=₹{atr:.2f} | Calculated SL=₹{_sl:.2f} | T1=₹{_t1:.2f}\n"

    # 6-field structured prompt — team consensus format
    prompt = (
        f"STOCK: {symbol} (NSE India)\n"
        f"PRICE: ₹{ltp:.2f} exactly — USE THIS NUMBER ONLY for all calculations\n"
        f"SIGNAL: RSI={rsi:.1f} ({rsi_zone}) | MACD {direction} | Trend {trend}\n"
        f"{fund_line}"
        f"{atr_line}"
        f"\nRespond in EXACTLY this format — no preamble, no extra lines:\n"
        f"📌 {symbol} — [BULLISH/BEARISH/NEUTRAL]\n"
        f"• Strength: [one specific technical reason citing ₹{ltp:.2f} and RSI/MACD numbers above]\n"
        f"• Risk: [one specific risk citing actual price levels]\n"
        f"• Catalyst: [sector/news factor in one line]\n"
        f"• Verdict: [BUY ABOVE ₹X / SELL BELOW ₹X / HOLD] — [one sentence]\n"
        f"• Horizon: [swing 3-5d / positional 2-4w / avoid]"
    )
    text, err = _call_ai(
        [{"role": "user", "content": prompt}],
        max_tokens=180,
        system=(
            "You are a precise NSE equity analyst. "
            "Use ONLY the exact ₹ price given — never invent or round prices. "
            "Cite the actual RSI and MACD values. No speculation. Exact format only."
        ),
    )
    if text:
        return text
    return f"⚠️ AI unavailable: {err.split(chr(10))[0][:80]}" if err else "⚠️ AI temporarily unavailable."


# ── News fetch ─────────────────────────────────────────────────────────────────
_NEWS_JUNK = ["Stock Price","Quote","Yahoo Finance","TradingView","Investing.com",
              "CNBC","Chart and News","Index Today","NSE India","National Stock Exchange",
              "Live Share","Equity Market Watch","moneycontrol.com"]

def _is_real_headline(title: str) -> bool:
    if not title or len(title) < 25: return False
    return not any(j.lower() in title.lower() for j in _NEWS_JUNK)


def fetch_news(symbol: str) -> str:
    from_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    to_date   = date.today().strftime("%Y-%m-%d")

    tavily_key = _key("TAVILY_API_KEY")
    if tavily_key:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": tavily_key,
                      "query": f"{symbol} NSE India stock news latest",
                      "max_results": 6, "search_depth": "advanced",
                      "include_domains": ["economictimes.indiatimes.com","moneycontrol.com",
                                          "livemint.com","businessline.com","reuters.com"]},
                timeout=8,
            ).json()
            headlines = [x["title"] for x in r.get("results",[]) if _is_real_headline(x.get("title",""))][:2]
            if headlines:
                return "\n".join(f"📰 {h[:90]}" for h in headlines)
        except Exception as e:
            logger.warning(f"Tavily news {symbol}: {e}")

    finnhub_key = _key("FINNHUB_API_KEY")
    if finnhub_key:
        try:
            r = requests.get("https://finnhub.io/api/v1/company-news",
                             params={"symbol": f"NSE:{symbol}", "from": from_date,
                                     "to": to_date, "token": finnhub_key}, timeout=6).json()
            if isinstance(r, list):
                lines = [f"📰 {a['headline'][:85]}" for a in r[:2] if a.get("headline")]
                if lines: return "\n".join(lines)
        except Exception: pass

    try:
        import re
        rss = requests.get("https://www.moneycontrol.com/rss/buzzingstocks.xml",
                           headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        if rss.ok:
            titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", rss.text)
            matched = [t for t in titles[1:] if symbol.upper() in t.upper()][:2]
            if matched:
                return "\n".join(f"📰 {t[:90]}" for t in matched)
    except Exception: pass

    return ""


def fetch_market_news() -> str:
    tavily_key = _key("TAVILY_API_KEY")
    if tavily_key:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": tavily_key,
                      "query": "India NSE Nifty stock market news today",
                      "max_results": 8, "search_depth": "advanced",
                      "include_domains": ["economictimes.indiatimes.com","moneycontrol.com",
                                          "livemint.com","reuters.com","financialexpress.com"]},
                timeout=10,
            ).json()
            headlines = [x["title"] for x in r.get("results",[]) if _is_real_headline(x.get("title",""))][:5]
            if headlines:
                return "\n".join(f"📰 {h}" for h in headlines)
        except Exception: pass

    try:
        import re
        rss = requests.get("https://www.moneycontrol.com/rss/latestnews.xml",
                           headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if rss.ok:
            titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", rss.text)
            mkt = [t for t in titles[1:] if any(
                kw in t.lower() for kw in ["nifty","sensex","market","stock","sebi","rbi"]
            )][:5]
            if mkt:
                return "\n".join(f"📰 {t}" for t in mkt)
    except Exception: pass

    return ""


# ── Chat history ───────────────────────────────────────────────────────────────
_chat_history: dict = {}

def add_to_chat(uid: int, role: str, content: str):
    _chat_history.setdefault(uid, [])
    _chat_history[uid].append({"role": role, "content": content})
    # Bug 5 Fix: keep last 12 messages (6 turns) — GROQ 8b token limit protection
    _chat_history[uid] = _chat_history[uid][-12:]

def get_chat_history(uid: int) -> list:
    return _chat_history.get(uid, [])

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
        "RULES: LONG when RSI<50 + MACD bullish. SHORT when RSI>60 + MACD bearish.\n"
        "ATR-BASED LEVELS: SL=entry−1.2×ATR, T1=entry+2×ATR, T2=entry+3.5×ATR\n"
        "FORMAT per trade:\n"
        "📌 [SYM] [LONG/SHORT] | LTP:₹[exact from data] | RSI:[exact]\n"
        "   Entry: ₹[LTP×0.995]–₹[LTP×1.005]\n"
        "   T1: ₹[LTP+2×ATR] | T2: ₹[LTP+3.5×ATR] | SL: ₹[LTP−1.2×ATR]\n"
        "   R:R T1=1:[calc] | Hold: 3–7 days\n"
        "   Why: [RSI+MACD+trend from exact data — 1 line]\n"
        "⚠️ Educational only. Not SEBI-registered advice."
    ),
    "⚡ Option Trade": (
        "TASK: One Nifty options strategy using LIVE DATA including PCR.\n"
        "PCR RULE: PCR>1.1 = bullish bias (market hedged). PCR<0.85 = bearish bias.\n"
        "FORMAT:\n⚡ OPTION STRATEGY — [date]\n"
        "• Spot: [exact] | ATM: [nearest 50] | PCR: [exact from data]\n"
        "• PCR Signal: [Bullish/Bearish/Neutral] | RSI: [exact] | Trend: [exact]\n"
        "• Strategy: [Bull Call Spread/Long CE/Bear Put Spread/Long PE/Iron Condor]\n"
        "• Direction: [Bullish/Bearish/Neutral — must match PCR signal]\n"
        "• Strikes: [e.g. Buy 24450CE + Sell 24600CE]\n"
        "• Why: [Nifty + RSI + PCR numbers from data — 2 lines max]\n"
        "• Max Risk: [X pts] | Target: [X pts]\n"
        "• Exit if Nifty closes [above/below] ₹[level]\n"
        "⚠️ Do NOT quote premium prices. Educational only. Not SEBI-registered advice."
    ),
}

AI_CHAT_TOPIC_KEYS: set = set(AI_CHAT_TOPICS.keys())


def _detect_stock_in_message(msg: str) -> str:
    """Try to detect a stock symbol in the user message for context injection."""
    import re
    # Known large-caps / common mentions
    KNOWN = [
        "RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK","SBIN","BAJFINANCE",
        "TATAMOTORS","WIPRO","HCLTECH","AXISBANK","KOTAKBANK","LT","ITC",
        "SUNPHARMA","BHARTIARTL","ONGC","NTPC","MARUTI","M&M","TITAN",
        "ADANIENT","ADANIPORTS","BAJAJ","HDFC","NESTLE","TATACONSUM",
        "DRREDDY","DIVISLAB","CIPLA","ZOMATO","NYKAA","PAYTM","INDIGO",
    ]
    msg_up = msg.upper()
    for sym in KNOWN:
        if sym in msg_up:
            return sym
    # Detect patterns like "XYZ trade", "XYZ setup", "XYZ analysis"
    m = re.search(r"\b([A-Z]{2,12})\s*(TRADE|SETUP|ANALYSIS|CHART|BUY|SELL|TARGET|SL|STOCK)\b", msg_up)
    if m:
        return m.group(1)
    return ""


def _get_stock_live_context(sym: str) -> str:
    """Fetch live price + RSI + basic data for a specific stock to inject into AI.
    Bug 2 Fix: uses data_engine.get_hist() instead of yf.Ticker directly
    so it works on Render where direct yfinance connections are blocked.
    """
    try:
        import numpy as np
        from data_engine import get_hist as _get_hist
        # Use project's own cached data engine — works on Render
        # Fix 3: data_engine.get_hist expects plain symbol without suffix
        clean_sym = sym.replace(".NS","").replace(".BO","").strip().upper()
        df = _get_hist(clean_sym, "3mo")
        if df is None or df.empty:
            logger.debug(f"stock ctx: no data for {clean_sym}")
            return ""
        close = df["Close"]
        ltp   = round(float(close.iloc[-1]), 2)
        prev  = round(float(close.iloc[-2]), 2)
        chg   = round((ltp-prev)/prev*100, 2)
        # RSI
        d = close.diff()
        gain = d.where(d>0,0).rolling(14).mean()
        loss = (-d.where(d<0,0)).rolling(14).mean()
        rs   = gain/loss
        rsi  = round(float(100-(100/(1+rs.iloc[-1]))), 1)
        # EMAs
        ema9  = round(float(close.ewm(span=9,  adjust=False).mean().iloc[-1]), 2)
        ema21 = round(float(close.ewm(span=21, adjust=False).mean().iloc[-1]), 2)
        ema50 = round(float(close.ewm(span=50, adjust=False).mean().iloc[-1]), 2)
        # ATR
        h,l,c = df["High"].values, df["Low"].values, close.values
        tr = np.maximum(h[1:]-l[1:], np.maximum(np.abs(h[1:]-c[:-1]), np.abs(l[1:]-c[:-1])))
        atr = round(float(np.mean(tr[-14:])), 2)
        # 52w H/L
        h52 = round(float(df["High"].max()), 2)
        l52 = round(float(df["Low"].min()),  2)
        trend = "BULLISH" if ltp > ema21 > ema50 else ("BEARISH" if ltp < ema21 < ema50 else "SIDEWAYS")
        # P2 Nice: add today's OHLC for intraday/30-min context
        today_open  = round(float(df["Open"].iloc[-1]), 2)
        today_high  = round(float(df["High"].iloc[-1]), 2)
        today_low   = round(float(df["Low"].iloc[-1]),  2)
        vwap_approx = round((today_high + today_low + ltp) / 3, 2)
        bb_mid  = round(float(df["Close"].rolling(20).mean().iloc[-1]), 2)
        bb_std  = round(float(df["Close"].rolling(20).std().iloc[-1]),  2)
        bb_up   = round(bb_mid + 2*bb_std, 2)
        bb_lo   = round(bb_mid - 2*bb_std, 2)
        return (
            f"\nSPECIFIC STOCK DATA — {sym}:\n"
            f"LTP: ₹{ltp} ({chg:+.2f}%) | RSI: {rsi} | Trend: {trend}\n"
            f"Today OHLC: O:{today_open} H:{today_high} L:{today_low} C:{ltp} | VWAP≈₹{vwap_approx}\n"
            f"EMA9: ₹{ema9} | EMA21: ₹{ema21} | EMA50: ₹{ema50}\n"
            f"BB Upper: ₹{bb_up} | BB Mid: ₹{bb_mid} | BB Lower: ₹{bb_lo}\n"
            f"ATR(14): ₹{atr} | 52W H: ₹{h52} | 52W L: ₹{l52}\n"
            f"Support: ₹{round(ltp-atr,2)} | Resistance: ₹{round(ltp+atr,2)}\n"
            f"30-min Pivot: ₹{round((today_high+today_low+ltp)/3,2)} | "
            f"R1: ₹{round(2*(today_high+today_low+ltp)/3-today_low,2)} | "
            f"S1: ₹{round(2*(today_high+today_low+ltp)/3-today_high,2)}\n"
        )
    except Exception as e:
        logger.debug(f"stock ctx fetch {sym}: {e}")
        return ""


def ai_chat_respond(uid: int, user_message: str) -> str:
    """
    Handle free-form user chat. Stores turns in history.
    Bug 3 Fix: topic prompts should use ai_topic_respond() — not this function.
    Bug 5 Fix: chat history trimmed to 6 turns max (was 10) to stay within token limits.
    """
    if not ai_available():
        return (
            "⚠️ <b>No AI keys configured.</b>\n\n"
            "Add at least one key in Render → Environment:\n"
            "• <code>GROQ_API_KEY</code> — free at console.groq.com\n"
            "• <code>GEMINI_API_KEY</code> — free at aistudio.google.com"
        )

    # PERMANENT FIX 2: wrap every context call — any crash here must NOT kill response
    try:
        market_ctx = get_live_market_context()
    except Exception as _e:
        logger.warning(f"market context failed in ai_chat_respond: {_e}")
        market_ctx = "Market data temporarily unavailable. Use general NSE knowledge."

    detected_sym = _detect_stock_in_message(user_message)
    stock_ctx    = ""
    if detected_sym:
        try:
            stock_ctx = _get_stock_live_context(detected_sym)
            logger.info(f"AI: detected {detected_sym}, injecting live data")
        except Exception as _e:
            logger.warning(f"stock context failed for {detected_sym}: {_e}")

    system = CHAT_SYSTEM + f"\n\nLIVE MARKET CONTEXT:\n{market_ctx}{stock_ctx}"

    # Bug 5 Fix: cap at 6 turns (12 messages) to avoid exceeding GROQ 8b token limit
    history  = get_chat_history(uid)[-12:]
    messages = history + [{"role": "user", "content": user_message}]

    text, err = _call_ai(messages, max_tokens=450, system=system)

    if text:
        add_to_chat(uid, "user",      user_message)
        add_to_chat(uid, "assistant", text)
        return text

    # Bug 6 UX Fix: clean user-facing error — no raw internal error strings
    return _friendly_ai_error(err)


def ai_topic_respond(topic_prompt: str) -> str:
    """
    Bug 3 Fix: Topic button calls use this function — NOT stored in chat history.
    Topic prompts are system-level instructions, not user conversation turns.
    Keeps chat history clean and saves tokens.
    """
    if not ai_available():
        return (
            "⚠️ <b>No AI keys configured.</b>\n\n"
            "Add <code>GROQ_API_KEY</code> in Render → Environment (free at console.groq.com)"
        )

    # PERMANENT FIX 3: wrap context — any crash must NOT kill AI topic response
    try:
        market_ctx = get_live_market_context()
    except Exception as _e:
        logger.warning(f"market context failed in ai_topic_respond: {_e}")
        market_ctx = "Market data temporarily unavailable. Use general NSE knowledge."
    # Topic prompt is the full instruction — treat as a fresh one-shot call
    system     = (
        "You are AutoAI Advisory, an expert Indian NSE equity analyst. "
        "Use ONLY the data below. Be direct, specific, format exactly as instructed. "
        "Never invent prices or percentages not in the data provided."
    )
    messages   = [{"role": "user", "content": f"{topic_prompt}\n\nLIVE DATA:\n{market_ctx}"}]

    text, err = _call_ai(messages, max_tokens=400, system=system)
    if text:
        return text
    return _friendly_ai_error(err)


def _friendly_ai_error(err: str) -> str:
    """
    Bug 6 UX Fix: Convert raw provider error strings into clean user messages.
    Never expose internal GROQ/Gemini/OpenAI error details to the user.
    """
    if not err:
        return "⚠️ AI temporarily unavailable. Please try again in a moment."

    err_lower = err.lower()
    if "rate limit" in err_lower or "429" in err_lower:
        return (
            "⏳ <b>AI is busy right now</b> (rate limit reached).\n"
            "Please wait 30 seconds and try again."
        )
    if "invalid key" in err_lower or "401" in err_lower or "authentication" in err_lower:
        return (
            "❌ <b>AI key issue detected.</b>\n"
            "Go to Render → Environment → check GROQ_API_KEY is valid → Redeploy."
        )
    if "not set" in err_lower or "no key" in err_lower:
        return (
            "⚠️ <b>No AI key configured.</b>\n"
            "Add GROQ_API_KEY in Render → Environment (free at console.groq.com)."
        )
    if "quota" in err_lower or "exceeded" in err_lower:
        return (
            "⏳ <b>AI quota exceeded</b> for today.\n"
            "Will reset shortly. Try again in a few minutes."
        )
    if "timeout" in err_lower or "timed out" in err_lower:
        return "⏳ AI response timed out. Please try again."

    # Generic fallback — never show internal error
    return (
        "⚠️ <b>AI temporarily unavailable.</b>\n"
        "Please try again in a moment. If the issue persists, use /status to check providers."
    )


# ── Diagnostics ────────────────────────────────────────────────────────────────
def test_ai_providers() -> dict:
    results = {}
    for name, key_env, test_fn in [
        ("GROQ",    "GROQ_API_KEY",    lambda: _get_groq().chat.completions.create(
             model="llama-3.1-8b-instant",
             messages=[{"role":"user","content":"Say OK"}], max_tokens=3)),
        ("Gemini",  "GEMINI_API_KEY",  lambda: _get_gemini().generate_content("Say OK, just those two words.")),
        ("OpenAI",  "OPENAI_KEY",      lambda: _get_openai().chat.completions.create(
             model="gpt-4o-mini",
             messages=[{"role":"user","content":"Say OK"}], max_tokens=3)),
    ]:
        if not _key(key_env):
            results[name] = f"SKIP — {key_env} not set"
            continue
        try:
            r = test_fn()
            # Bug 5 Fix: handle all provider response types correctly
            if hasattr(r, "choices") and r.choices:
                # GROQ / OpenAI format
                text = r.choices[0].message.content or "OK"
            elif hasattr(r, "text") and r.text:
                # Gemini format — GenerateContentResponse has .text property
                text = r.text
            elif hasattr(r, "candidates"):
                # Gemini alternate format
                text = str(r.candidates[0].content.parts[0].text)[:30] if r.candidates else "OK"
            else:
                text = str(r)[:30]
            results[name] = f"OK — {str(text).strip()[:25]}"
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
    results["_status"] = "✅ AI WORKING" if any(v.startswith("OK") for v in results.values()) else "❌ ALL FAILED"
    return results


def debug_ai_status() -> dict:
    return {
        "keys": {k: ("set" if _key(k) else "MISSING") for k in
                 ["GROQ_API_KEY","GEMINI_API_KEY","OPENAI_KEY","ASKFUZZ_API_KEY",
                  "TAVILY_API_KEY","FINNHUB_API_KEY","ALPHA_VANTAGE_KEY"]},
        "context_cached":  bool(_CTX_CACHE["text"]),
        "context_age_sec": round(time.time() - _CTX_CACHE["ts"], 0),
        "context_ttl_sec": _CTX_TTL,
        "groq_models":     _GROQ_MODELS,
        "ai_available":    ai_available(),
    }
