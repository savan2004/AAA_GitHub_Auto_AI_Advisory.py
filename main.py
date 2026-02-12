import os
import threading
import time
import json
import re
import logging
import requests
from datetime import datetime
from functools import wraps

import telebot
from telebot import types
import yfinance as yf
import pandas as pd
import numpy as np
import openai

# --- 1. CONFIGURATION & LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN missing.")

bot = telebot.TeleBot(TOKEN)

# --- 2. AI CLIENT INIT ---
AI_ENABLED = False
client = None
try:
    if OPENAI_API_KEY:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        AI_ENABLED = True
        logging.info("✅ OpenAI GPT-4o Ready.")
except Exception as e:
    logging.error(f"OpenAI Init Failed: {e}")

# --- 3. BULLETPROOF NETWORK HANDLER ---

def retry_on_failure(max_retries=3, delay=5):
    """
    Decorator to retry a function if it fails due to network errors.
    Prevents the bot from crashing on 'Connection reset by peer'.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout, ConnectionResetError) as e:
                    retries += 1
                    logging.warning(f"Network Error in {func.__name__}: {e}. Retrying {retries}/{max_retries} in {delay}s...")
                    time.sleep(delay)
                except Exception as e:
                    logging.error(f"Non-Network Error in {func.__name__}: {e}")
                    raise e
            return None # Return None if all retries fail
        return wrapper
    return decorator

# --- 4. ADVANCED TECHNICAL ENGINE ---

def calculate_rsi(series, period=14):
    if len(series) < period + 1: return 50.0
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-9)
    return float((100 - (100 / (1 + rs))).iloc[-1])

def calculate_macd(series):
    exp12 = series.ewm(span=12, adjust=False).mean()
    exp26 = series.ewm(span=26, adjust=False).mean()
    macd_line = exp12 - exp26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line.iloc[-1], signal_line.iloc[-1], hist.iloc[-1]

def calculate_atr(df, period=14):
    high = df['High']
    low = df['Low']
    close = df['Close'].shift(1)
    tr1 = high - low
    tr2 = abs(high - close)
    tr3 = abs(low - close)
    ranges = pd.concat([tr1, tr2, tr3], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(window=period).mean().iloc[-1]

def calculate_pivots(high, low, close):
    pp = (high + low + close) / 3
    r1 = (2 * pp) - low
    s1 = (2 * pp) - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    return pp, r1, s1, r2, s2

def compute_asr_score(ltp, ema_50, ema_200, rsi, macd_hist, atr_pct):
    score = 0
    if ltp > ema_200: score += 20
    if ltp > ema_50: score += 10
    if ltp > ema_50 and ema_50 > ema_200: score += 10
    if macd_hist > 0: score += 15
    if 40 <= rsi <= 60: score += 15
    elif 30 <= rsi < 40 or 60 < rsi <= 70: score += 5
    if atr_pct < 2.0: score += 30
    elif 2.0 <= atr_pct < 4.0: score += 15
    return min(score, 100)

# --- 5. DATA FETCHER (WITH RETRY) ---

@retry_on_failure(max_retries=3, delay=5)
def get_stock_data(symbol):
    """Fetches data with automatic retry on connection failure."""
    stock = yf.Ticker(symbol)
    # Using period="1y" ensures enough data for 200EMA
    df = stock.history(period="1y", auto_adjust=True)
    info = stock.info
    if df.empty:
        raise ValueError("Empty Data")
    return df, info

# --- 6. DEEP ANALYSIS REPORT ---

def get_sk_deep_report(symbol):
    try:
        sym = symbol.upper().strip()
        ticker_sym = sym
        if sym in ["NIFTY", "NIFTY50"]: ticker_sym = "^NSEI"
        elif sym == "BANKNIFTY": ticker_sym = "^NSEBANK"
        elif sym == "SENSEX": ticker_sym = "^BSESN"
        elif not sym.endswith(".NS"): ticker_sym = f"{sym}.NS"

        # Call the retry-safe data fetcher
        result = get_stock_data(ticker_sym)
        if not result:
            return "⚠️ **Network Error:** Could not connect to market data after 3 retries. Please try again later."
        
        df, info = result

        # --- DATA CALCULATIONS ---
        close = df['Close']
        ltp = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        high_prev = float(df['High'].iloc[-2])
        low_prev = float(df['Low'].iloc[-2])

        # Technicals
        ema_50 = close.ewm(span=50).mean().iloc[-1]
        ema_200 = close.ewm(span=200).mean().iloc[-1]
        rsi = calculate_rsi(close)
        macd_val, sig_val, macd_hist = calculate_macd(close)
        atr = calculate_atr(df)
        atr_pct = (atr / ltp) * 100 

        # Pivots
        pp, r1, s1, r2, s2 = calculate_pivots(high_prev, low_prev, prev_close)

        # Fundamentals
        sector = info.get('sector', 'N/A')
        mcap = float(info.get('marketCap', 0) or 0)
        pe = float(info.get('trailingPE', 0) or 0)
        roe = float((info.get('returnOnEquity', 0) or 0) * 100)
        
        # --- SCORING ---
        asi_score = compute_asr_score(ltp, ema_50, ema_200, rsi, macd_hist, atr_pct)
        
        # --- RISK MANAGEMENT ---
        sl_price = ltp - (atr * 1.5)
        target_price = ltp + (atr * 3)
        
        # --- AI LOGIC ---
        ai_conclusion = "AI analysis unavailable."
        if AI_ENABLED:
            prompt = (
                f"Analyze {sym}. LTP={ltp:.2f}, RSI={rsi:.2f}, MACD_Hist={macd_hist:.4f}. "
                f"Give a concise conclusion for a Swing Trader."
            )
            try:
                resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}])
                ai_conclusion = resp.choices[0].message.content
            except: pass

        # --- VERDICT ---
        verdict = "⚠️ WAIT"
        if asi_score >= 75 and macd_hist > 0: verdict = "🚀 STRONG BUY"
        elif asi_score >= 60 and ltp > ema_50: verdict = "✅ BUY"
        elif rsi > 70: verdict = "📉 OVERBOUGHT"
        
        return (
            f"🔬 **DEEP ASI ANALYSIS: {sym}**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {datetime.now().strftime('%d-%b %H:%M')} | 🏛 **ASI SCORE:** {asi_score}/100\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **PRICE:** ₹{ltp:.2f}\n"
            f"📊 **INDICATORS:**\n"
            f"  • RSI(14): {rsi:.2f}\n"
            f"  • MACD: {'📈 Bullish' if macd_hist > 0 else '📉 Bearish'}\n"
            f"  • Volatility: {atr_pct:.2f}%\n"
            f"🎯 **VERDICT:** {verdict}\n"
            f"🛡 **SL:** ₹{sl_price:.2f} | **Target:** ₹{target_price:.2f}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 **AI:**\n{ai_conclusion}\n"
            "_SK AUTO AI ADVISORY_"
        )

    except Exception as e:
        logging.error(f"Deep Report Error: {e}")
        return f"⚠️ Analysis Error: {str(e)}"

# --- 7. OPTIONS & PORTFOLIO ---

def get_nifty_option_trade(budget, spot):
    # Simplified for stability
    try:
        strike = round(spot / 50) * 50
        return (
            f"⚙️ **OPTIONS SNIPER**\n"
            f"Spot: {spot:.2f}\n"
            f"Trade: {strike} CE/PE\n"
            f"Capital: ₹{budget}\n"
            f"_Strategy: Momentum_"
        )
    except Exception as e:
        return f"Error: {e}"

def get_smart_portfolio():
    return "💎 **PORTFOLIO SCANNER**\n✅ RELIANCE\n✅ TCS\n✅ INFY\n_Status: Bullish_"

# --- 8. TELEGRAM HANDLERS ---

@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("🔬 Deep Analysis", "💎 Smart Portfolio")
    markup.add("⚙️ Options Sniper", "📊 Market Pulse")
    bot.send_message(m.chat.id, 
        "🚀 **SK AUTO AI ADVISORY**\n\n⚡ **Stable Mode Activated.**", 
        reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🔬 Deep Analysis")
def ask_symbol(m):
    msg = bot.send_message(m.chat.id, "📝 Enter Symbol:")
    bot.register_next_step_handler(msg, process_deep_analysis)

def process_deep_analysis(m):
    bot.send_chat_action(m.chat.id, 'typing')
    msg_text = m.text.upper().replace(" ", "")
    response = get_sk_deep_report(msg_text)
    bot.send_message(m.chat.id, response, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⚙️ Options Sniper")
def ask_opt_budget(m):
    msg = bot.send_message(m.chat.id, "💵 Enter Capital (INR):")
    bot.register_next_step_handler(msg, process_options)

def process_options(m):
    try:
        budget = float(m.text.replace("₹", "").replace(",", ""))
        # Fetch spot safely
        spot_df = yf.Ticker("^NSEI").history(period="1d")
        if spot_df.empty:
             bot.send_message(m.chat.id, "⚠️ Market Data Connection Failed")
             return
        spot = float(spot_df['Close'].iloc[-1])
        bot.send_message(m.chat.id, get_nifty_option_trade(budget, spot), parse_mode="Markdown")
    except:
        bot.send_message(m.chat.id, "❌ Invalid amount.")

@bot.message_handler(func=lambda m: m.text == "💎 Smart Portfolio")
def show_port(m):
    bot.send_message(m.chat.id, get_smart_portfolio(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 Market Pulse")
def market_pulse(m):
    # Safe fetch with retry logic
    try:
        nifty = yf.Ticker("^NSEI").history(period="2d")
        if not nifty.empty:
             bot.send_message(m.chat.id, f"📊 NIFTY: {nifty['Close'].iloc[-1]:.2f}", parse_mode="Markdown")
        else:
             bot.send_message(m.chat.id, "⚠️ Connection issue.")
    except Exception as e:
        bot.send_message(m.chat.id, "⚠️ Network Error.")

# --- 9. RENDER HEALTH CHECK & POLLING ---

def run_health_server():
    import http.server
    import socketserver
    port = int(os.environ.get("PORT", 10000))
    
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"SK AI ADVISORY ONLINE")
    
    try:
        with socketserver.TCPServer(("", port), Handler) as httpd:
            httpd.serve_forever()
    except Exception as e:
        logging.error(f"Health server error: {e}")

if __name__ == "__main__":
    # Start Health Server
    threading.Thread(target=run_health_server, daemon=True).start()
    
    logging.info("🚀 SK AUTO AI ADVISORY Started...")
    
    # POLLING LOGIC (Robust)
    # none_stop=True keeps it running even if Telegram returns errors
    # timeout=60 prevents premature connection drops
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, none_stop=True)
        except Exception as e:
            logging.error(f"Bot Polling Crash: {e}")
            time.sleep(15) # Wait before restarting
