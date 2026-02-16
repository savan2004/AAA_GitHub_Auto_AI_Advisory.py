# main.py
"""
AI NSE Advisory Telegram Bot (Groq + Gemini + Financial Advisor framing)

Telegram:
- /start → shows menu.
- "Stock Analysis" → ask for NSE symbol (e.g., BEL, RELIANCE) → AI advisory.

HTTP:
- GET /                    -> "OK" (health check for Render)
- GET /simulate?symbol=BEL -> JSON with AI advisory text (for debugging)

Environment variables:
- TELEGRAM_TOKEN   : Telegram bot token (required)
- GROQ_API_KEY     : Groq API key (optional, preferred if present)
- GEMINI_API_KEY   : Gemini key for google-genai SDK (optional fallback)
- PORT             : Web port (Render sets automatically; default 10000)
"""

import os
import time
import json
import threading
from collections import deque
from typing import List
from urllib.parse import urlparse, parse_qs

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

import telebot
from telebot import types

from groq import Groq
from google import genai  # google-genai SDK [web:271][web:273]

from http.server import BaseHTTPRequestHandler, HTTPServer

# ========= 1. CONFIG & CLIENTS =========

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set. Please set it in the environment.")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode=None)

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

YF_WINDOW_SEC = 60
YF_MAX_CALLS_PER_WINDOW = 10
YF_CALL_TIMES = deque()
YF_CACHE: dict[str, dict] = {}
YF_CACHE_TTL = 900  # 15 minutes


# ========= 2. YFINANCE SAFE LAYER =========

def yf_cache_get(key: str):
    data = YF_CACHE.get(key)
    if not data:
        return None
    if time.time() - data["ts"] > YF_CACHE_TTL:
        del YF_CACHE[key]
        return None
    return data["val"]


def yf_cache_set(key: str, val: pd.DataFrame):
    YF_CACHE[key] = {"val": val, "ts": time.time()}


def yf_allow_call() -> bool:
    now = time.time()
    while YF_CALL_TIMES and now - YF_CALL_TIMES[0] > YF_WINDOW_SEC:
        YF_CALL_TIMES.popleft()
    return len(YF_CALL_TIMES) < YF_MAX_CALLS_PER_WINDOW


def yf_register_call():
    YF_CALL_TIMES.append(time.time())


def safe_history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    key = f"{ticker}:{period}:{interval}"
    cached = yf_cache_get(key)
    if cached is not None:
        return cached

    if not yf_allow_call():
        return cached if cached is not None else pd.DataFrame()

    try:
        yf_register_call()
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if not df.empty:
            yf_cache_set(key, df)
            return df
    except YFRateLimitError:
        return pd.DataFrame()
    except Exception as e:
        print("yfinance error:", e)
        return pd.DataFrame()

    return pd.DataFrame()


# ========= 3. INDICATORS =========

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def bollinger_bands(series: pd.Series, window: int = 20, num_sd: int = 2):
    sma = series.rolling(window).mean()
    rstd = series.rolling(window).std()
    upper = sma + num_sd * rstd
    lower = sma - num_sd * rstd
    return sma, upper, lower


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    plus_dm = high.diff()
    minus_dm = low.diff().abs()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).sum() / atr)
    minus_di = 100 * (minus_dm.rolling(period).sum() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx_val = dx.rolling(period).mean()
    return adx_val


# ========= 4. AI LAYER (GROQ → GEMINI → FALLBACK) =========

def ai_call(prompt: str, max_tokens: int = 600) -> str:
    """
    AI advisory orchestration with financial-advisor framing:
    1) Try Groq.
    2) Try Gemini.
    3) Deterministic fallback.
    """
    if GROQ_API_KEY:
        try:
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.35,
            )
            text = resp.choices[0].message.content or ""
            text = text.strip()
            if text:
                return text
        except Exception as e:
            print("Groq error:", e)

    if gemini_client:
        try:
            resp = gemini_client.models.generate_content(
                model="gemini-3.5-flash",
                contents=prompt,
            )
            text = getattr(resp, "text", "") or ""
            text = text.strip()
            if text:
                return text
        except Exception as e:
            print("Gemini error:", e)

    # Financial-advisor aligned fallback
    return (
        "AI advisory (fallback): External models are temporarily unavailable. "
        "Treat the current trend vs 200 EMA, RSI, and ADX as your main guide. "
        "Short-term traders typically scale positions and use tight stop-losses, "
        "while medium-term investors focus on staggered entries and overall allocation, "
        "keeping in mind that leverage and derivatives can amplify both gains and losses. "
        "Note: This is educational AI analysis only, not a recommendation."
    )


# ========= 5. ADVISORY BUILDER =========

def build_ai_prompt(sym: str, ltp: float, trend: str,
                    rsi_val: float, adx_val: float, quality: int) -> str:
    """
    Builds AI prompt with explicit financial-advisor tone:
    - retail investor audience
    - emphasis on risk, position sizing, and suitability
    """
    bias = "overbought" if rsi_val > 70 else "oversold" if rsi_val < 30 else "neutral"

    return f"""
You are a seasoned Indian financial advisor and equity analyst, explaining in simple language to a retail investor.

Stock: {sym} (NSE)
Last traded price: {ltp:.2f}
Trend vs 200 EMA: {trend}
RSI(14): {rsi_val:.2f} (bias: {bias})
ADX(14): {adx_val:.2f}
Quality score (0–3, higher = cleaner trend): {quality}

Write a concise AI advisory that respects the following:

1) Short-term view (1–4 weeks):
   - 3–4 sentences.
   - Clear directional bias (bullish / bearish / sideways).
   - Distinguish between aggressive traders and conservative traders.
   - Talk about volatility, whipsaws, and the need for stop-losses if someone trades.

2) Medium-term view (3–6 months):
   - 3–4 sentences.
   - Think like a financial advisor talking about portfolio allocation.
   - Explain whether the stock looks suitable as a core holding or only a tactical trading position.
   - Focus on staggered entries, booking partial profits, and avoiding oversized positions.

3) Risk section:
   - 3 key risks as separate lines starting with '-'.
   - Include: market-wide correction risk, company-specific news (results, regulation), and liquidity / gap risk.
   - Optionally mention that leverage and derivatives increase risk and are suitable only for experienced traders.

4) Compliance tone:
   - Do NOT say 'buy now' or 'sell now'. Use phrases like 'aggressive traders may', 'conservative investors often', or 'some participants prefer'.
   - End the full advisory with this exact sentence:
     Note: This is educational AI analysis only, not a recommendation.

Keep the entire answer under 220 words, plain text only (no markdown).
""".strip()


def stock_ai_advisory(symbol: str) -> str:
    """
    Full advisory pipeline for one NSE symbol with financial-advisor framing.
    """
    sym = symbol.upper().strip()
    ticker = f"{sym}.NS"

    df = safe_history(ticker, period="1y", interval="1d")
    if df.empty or "Close" not in df.columns:
        return f"Could not fetch data for {sym}. Try again later."

    close = df["Close"]
    if len(close) < 60:
        return f"Not enough price history for {sym} to run full analysis."

    ltp = float(close.iloc[-1])

    ema20_val = float(ema(close, 20).iloc[-1])
    ema50_val = float(ema(close, 50).iloc[-1])
    ema200_val = float(ema(close, 200).iloc[-1])

    rsi_series = rsi(close, 14)
    rsi_val = float(rsi_series.iloc[-1])

    bb_mid, bb_up, bb_low = bollinger_bands(close, 20, 2)
    bbm = float(bb_mid.iloc[-1])
    bbu = float(bb_up.iloc[-1])
    bbl = float(bb_low.iloc[-1])

    adx_series = adx(df, 14)
    adx_val = float(adx_series.iloc[-1])

    trend = "Bullish" if ltp > ema200_val else "Bearish"
    pos_50 = "above" if ltp > ema50_val else "below"
    pos_200 = "above" if ltp > ema200_val else "below"

    quality = 0
    if ltp > ema200_val:
        quality += 1
    if 40 <= rsi_val <= 60:
        quality += 1
    if adx_val >= 20:
        quality += 1

    snapshot = (
        "STOCK SNAPSHOT\n"
        f"Symbol: {sym} (NSE)\n"
        f"LTP: ₹{ltp:.2f}\n"
        f"Trend vs 200 EMA: {trend}\n"
        f"Price vs 50 EMA: {pos_50}\n"
        f"Price vs 200 EMA: {pos_200}\n"
        f"EMA20 / EMA50 / EMA200: {ema20_val:.2f} / {ema50_val:.2f} / {ema200_val:.2f}\n"
        f"Bollinger Bands (U/M/L): {bbu:.2f} / {bbm:.2f} / {bbl:.2f}\n"
        f"RSI(14): {rsi_val:.2f}\n"
        f"ADX(14): {adx_val:.2f}\n"
        f"Quality Score (rough): {quality}/3\n"
    )

    prompt = build_ai_prompt(sym, ltp, trend, rsi_val, adx_val, quality)
    advisory = ai_call(prompt, max_tokens=380)

    return snapshot + "\n" + f"AI ADVISORY – {sym}\n\n" + advisory


# ========= 6. TELEGRAM LAYER =========

def split_for_telegram(text: str, chunk_size: int = 3500) -> List[str]:
    chunks: List[str] = []
    while text:
        chunks.append(text[:chunk_size])
        text = text[chunk_size:]
    return chunks


@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("Stock Analysis"))
    bot.send_message(
        m.chat.id,
        "AI NSE Advisory Bot (Groq + Gemini + Financial Advisor framing)\n\n"
        "Tap 'Stock Analysis' to get AI analysis for an NSE stock.\n"
        "All output is educational AI analysis only.",
        reply_markup=kb,
    )


@bot.message_handler(func=lambda m: m.text == "Stock Analysis")
def ask_symbol(m):
    msg = bot.reply_to(m, "Send NSE stock symbol (e.g. BEL, RELIANCE):")
    bot.register_next_step_handler(msg, handle_symbol_analysis)


def handle_symbol_analysis(m):
    sym = (m.text or "").strip().upper()
    if not sym:
        bot.reply_to(m, "Empty symbol. Try again.")
        return
    if not sym.isalnum():
        bot.reply_to(m, "Please send a valid NSE symbol like BEL or RELIANCE.")
        return

    try:
        full_text = stock_ai_advisory(sym)
    except Exception as e:
        print("handle_symbol_analysis error:", e)
        full_text = "Unexpected error while generating AI advisory. Please try again later."

    for part in split_for_telegram(full_text):
        bot.reply_to(m, part)
        time.sleep(0.3)


@bot.message_handler(func=lambda m: True)
def fallback(m):
    bot.reply_to(
        m,
        "Use the 'Stock Analysis' button. Send /start if the keyboard is not visible."
    )


# ========= 7. HTTP HEALTH + SIMULATION =========

class RequestHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: str, content_type: str = "text/plain"):
        try:
            self.send_response(code)
            self.send_header("Content-type", content_type)
            self.end_headers()
            self.wfile.write(payload.encode("utf-8"))
        except BrokenPipeError:
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._send(200, "OK")
            return

        if path == "/simulate":
            params = parse_qs(parsed.query)
            symbol = (params.get("symbol", ["BEL"])[0] or "BEL").upper()
            try:
                text = stock_ai_advisory(symbol)
                payload = json.dumps({"symbol": symbol, "analysis": text}, ensure_ascii=False)
                self._send(200, payload, content_type="application/json")
            except Exception as e:
                print("simulate error:", e)
                self._send(500, "Simulation error")
            return

        self._send(404, "Not Found")


def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), RequestHandler)
    print(f"HTTP server running on port {port}")
    server.serve_forever()


# ========= 8. MAIN LOOP =========

if __name__ == "__main__":
    print("Starting AI NSE Advisory Bot (Financial Advisor framed)...")

    threading.Thread(target=run_http_server, daemon=True).start()

    try:
        sim = stock_ai_advisory("BEL")
        print("===== BOOT SIMULATION: BEL (first 600 chars) =====")
        print(sim[:600])
        print("==================================================")
    except Exception as e:
        print("Boot simulation error:", e)

    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            print("Polling error:", e)
            time.sleep(10)
