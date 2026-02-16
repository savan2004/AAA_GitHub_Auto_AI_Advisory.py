import os
import time
import json
import threading
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer

import pandas as pd
import yfinance as yf
import telebot
from groq import Groq
import google.generativeai as genai

# ---------- CONFIG ----------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode=None)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ---------- TA HELPERS ----------

def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).rolling(period).mean()
    down = (-d.clip(upper=0)).rolling(period).mean()
    rs = up / down
    return 100 - (100 / (1 + rs))

# ---------- AI LAYER ----------

def ai_call(prompt: str, max_tokens: int = 600) -> str:
    # 1) GROQ
    if GROQ_API_KEY:
        try:
            c = Groq(api_key=GROQ_API_KEY)
            r = c.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.35,
            )
            t = (r.choices[0].message.content or "").strip()
            if t:
                return t
        except Exception as e:
            print("Groq error:", e)

    # 2) GEMINI
    if GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            t = (getattr(response, "text", "") or "").strip()
            if t:
                return t
        except Exception as e:
            print("Gemini error:", e)

    # 3) FALLBACK
    return (
        "AI advisory (fallback): External models are unavailable. "
        "Use trend vs 200 EMA and RSI as basic guides. "
        "Note: This is educational AI analysis only, not a recommendation."
    )

def build_prompt(sym: str, ltp: float, trend: str, rsi_val: float) -> str:
    bias = "overbought" if rsi_val > 70 else "oversold" if rsi_val < 30 else "neutral"
    return f"""
You are an Indian equity advisor.

Stock: {sym} (NSE)
LTP: {ltp:.2f}
Trend vs 200 EMA: {trend}
RSI(14): {rsi_val:.2f} ({bias})

In under 180 words:
- Give a 1–4 week view for aggressive vs conservative traders.
- Give a 3–6 month view for investors (allocation, staggered entries, partial profit booking).
- List 3 key risks as bullet lines.
End with: Note: This is educational AI analysis only, not a recommendation.
""".strip()

def stock_ai_advisory(symbol: str) -> str:
    sym = symbol.upper().strip()
    try:
        df = yf.Ticker(f"{sym}.NS").history(period="1y", interval="1d")
        if df.empty or "Close" not in df.columns:
            return f"Could not fetch data for {sym}. Try again later."

        close = df["Close"]
        if len(close) < 60:
            return f"Not enough price history for {sym}."

        ltp = float(close.iloc[-1])
        ema200 = float(ema(close, 200).iloc[-1])
        rsi_val = float(rsi(close, 14).iloc[-1])
        trend = "Bullish" if ltp > ema200 else "Bearish"

        snap = (
            "STOCK SNAPSHOT\n"
            f"Symbol: {sym} (NSE)\n"
            f"LTP: ₹{ltp:.2f}\n"
            f"Trend vs 200 EMA: {trend}\n"
            f"RSI(14): {rsi_val:.2f}\n"
        )

        prompt = build_prompt(sym, ltp, trend, rsi_val)
        adv = ai_call(prompt, max_tokens=320)
        return snap + "\nAI ADVISORY – " + sym + "\n\n" + adv
    except Exception as e:
        print("stock_ai_advisory error:", e)
        return f"An error occurred: {e}"

# ---------- TELEGRAM ----------

@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    print("Received /start from", m.chat.id)
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(telebot.types.KeyboardButton("Stock Analysis"))
    kb.add(telebot.types.KeyboardButton("Swing Trades"))
    bot.send_message(
        m.chat.id,
        "AI NSE Advisory Bot\n\nTap 'Stock Analysis' and send NSE symbol.",
        reply_markup=kb,
    )

@bot.message_handler(func=lambda m: m.text == "Stock Analysis")
def ask_symbol(m):
    print("User requested Stock Analysis:", m.chat.id)
    msg = bot.reply_to(m, "Send NSE symbol (e.g. BEL, RELIANCE):")
    bot.register_next_step_handler(msg, handle_symbol)

def handle_symbol(m):
    sym = (m.text or "").strip().upper()
    print("handle_symbol received:", sym, "from", m.chat.id)

    if not sym or not sym.isalnum():
        bot.reply_to(m, "Send a valid NSE symbol like BEL or RELIANCE.")
        return
    try:
        txt = stock_ai_advisory(sym)
    except Exception as e:
        print("handle_symbol error:", e)
        txt = "Error generating AI advisory. Try again."
    bot.reply_to(m, txt[:4000])

@bot.message_handler(func=lambda m: m.text == "Swing Trades")
def swing_trades(m):
    print("User requested Swing Trades:", m.chat.id)
    bot.reply_to(m, "Swing Trades feature is under development.")

@bot.message_handler(func=lambda m: True)
def fallback(m):
    print("Fallback from", m.chat.id, "text:", m.text)
    bot.reply_to(m, "Use the 'Stock Analysis' or 'Swing Trades' button or /start.")

# ---------- HTTP ----------

class RequestHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: str, ct: str = "text/plain"):
        self.send_response(code)
        self.send_header("Content-type", ct)
        self.end_headers()
        self.wfile.write(payload.encode("utf-8"))

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/":
            self._send(200, "OK")
        elif p.path == "/simulate":
            sym = (parse_qs(p.query).get("symbol", ["BEL"])[0] or "BEL").upper()
            try:
                txt = stock_ai_advisory(sym)
                self._send(
                    200,
                    json.dumps({"symbol": sym, "analysis": txt}, ensure_ascii=False),
                    ct="application/json",
                )
            except Exception as e:
                print("simulate error:", e)
                self._send(500, "Simulation error")
        else:
            self._send(404, "Not Found")

def run_http():
    port = int(os.environ.get("PORT", 10000))
    srv = HTTPServer(("0.0.0.0", port), RequestHandler)
    print(f"HTTP server on {port}")
    srv.serve_forever()

# ---------- MAIN ----------

if __name__ == "__main__":
    print("Starting short AI NSE Advisory Bot...")
    threading.Thread(target=run_http, daemon=True).start()
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            print("Polling error:", e)
            time.sleep(10)
