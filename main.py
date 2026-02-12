import os
import threading
import time
import json
import re
import logging
from datetime import datetime

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
if not OPENAI_API_KEY:
    logging.warning("OPENAI_API_KEY missing. AI features disabled.")

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

# --- 3. ADVANCED TECHNICAL ENGINE ---

def calculate_rsi(series, period=14):
    if len(series) < period + 1: return 50.0
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-9)
    return float((100 - (100 / (1 + rs))).iloc[-1])

def calculate_macd(series):
    """Returns MACD Line, Signal Line, Histogram"""
    exp12 = series.ewm(span=12, adjust=False).mean()
    exp26 = series.ewm(span=26, adjust=False).mean()
    macd_line = exp12 - exp26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line.iloc[-1], signal_line.iloc[-1], hist.iloc[-1]

def calculate_bollinger_bands(series, period=20):
    """Returns Upper, Middle, Lower"""
    sma = series.rolling(window=period).mean().iloc[-1]
    std = series.rolling(window=period).std().iloc[-1]
    upper = sma + (std * 2)
    lower = sma - (std * 2)
    return upper, sma, lower

def calculate_atr(df, period=14):
    """Average True Range - Crucial for SL/Target"""
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
    """
    Advanced Scoring Model (0-100)
    - Trend (40 pts)
    - Momentum (30 pts)
    - Volatility Risk (30 pts)
    """
    score = 0
    
    # 1. TREND ANALYSIS (40 Points)
    if ltp > ema_200: score += 20 # Long Term Bullish
    if ltp > ema_50: score += 10  # Short Term Bullish
    if ltp > ema_50 and ema_50 > ema_200: score += 10 # Golden Cross Alignment
    
    # 2. MOMENTUM ANALYSIS (30 Points)
    if macd_hist > 0: score += 15 # Bullish Momentum
    if 40 <= rsi <= 60: score += 15 # Safe Zone
    elif 30 <= rsi < 40 or 60 < rsi <= 70: score += 5 # Reversal Zone
    
    # 3. VOLATILITY RISK (30 Points) - Lower Volatility is preferred for Swing
    if atr_pct < 2.0: score += 30 # Low Volatility (Stable)
    elif 2.0 <= atr_pct < 4.0: score += 15 # Normal
    # High volatility gets 0 points (Risky)
    
    return min(score, 100)

STATIC_NOTES = {
    "DLF": "Real estate leader. Cyclical earnings. High beta.",
    "RELIANCE": "Conglomerate. Jio driving growth. Defensive play.",
    "HDFCBANK": "Private sector leader. Strong asset quality."
}

# --- 4. DEEP ANALYSIS REPORT ---

def get_sk_deep_report(symbol):
    try:
        sym = symbol.upper().strip()
        # Ticker Mapping
        ticker_sym = sym
        if sym in ["NIFTY", "NIFTY50"]: ticker_sym = "^NSEI"
        elif sym == "BANKNIFTY": ticker_sym = "^NSEBANK"
        elif sym == "SENSEX": ticker_sym = "^BSESN"
        elif not sym.endswith(".NS"): ticker_sym = f"{sym}.NS"

        stock = yf.Ticker(ticker_sym)
        # Fetch 1 year data for Deep Analysis
        df = stock.history(period="1y", auto_adjust=True)
        info = stock.info

        if df.empty or len(df) < 50:
            return f"❌ **Error:** Not enough historical data for `{sym}` to perform Deep Analysis."

        # --- DATA CALCULATIONS ---
        close = df['Close']
        high = df['High']
        low = df['Low']
        
        ltp = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        high_prev = float(high.iloc[-2])
        low_prev = float(low.iloc[-2])

        # Technicals
        ema_50 = close.ewm(span=50).mean().iloc[-1]
        ema_200 = close.ewm(span=200).mean().iloc[-1]
        rsi = calculate_rsi(close)
        macd_val, sig_val, macd_hist = calculate_macd(close)
        bb_upper, bb_mid, bb_lower = calculate_bollinger_bands(close)
        atr = calculate_atr(df)
        atr_pct = (atr / ltp) * 100 # Volatility Percentage

        # Pivots
        pp, r1, s1, r2, s2 = calculate_pivots(high_prev, low_prev, prev_close)

        # Fundamentals
        sector = info.get('sector', 'N/A')
        mcap = float(info.get('marketCap', 0) or 0)
        pe = float(info.get('trailingPE', 0) or 0)
        roe = float((info.get('returnOnEquity', 0) or 0) * 100)
        
        # --- SCORING ---
        asi_score = compute_asr_score(ltp, ema_50, ema_200, rsi, macd_hist, atr_pct)
        
        # --- RISK MANAGEMENT (ATR Based) ---
        # SL = 1.5 * ATR, Target = 3 * ATR (Risk Reward 1:2)
        sl_price = ltp - (atr * 1.5)
        target_price = ltp + (atr * 3)
        
        # --- AI DEEP REASONING ---
        ai_conclusion = "AI analysis unavailable."
        if AI_ENABLED:
            prompt = (
                f"You are a Quantitative Analyst. Analyze {sym} deeply.\n"
                f"Data: LTP={ltp:.2f}, RSI={rsi:.2f}, MACD_Hist={macd_hist:.4f}, ATR%={atr_pct:.2f}%.\n"
                f"Trend: {'Bullish' if ltp > ema_200 else 'Bearish'}.\n"
                f"1. Interpret the MACD crossover and Bollinger Band squeeze/expansion.\n"
                f"2. Assess current Volatility risk (High/Low).\n"
                f"3. Provide a precise Conclusion for a Swing Trader (3-7 days).\n"
                f"Keep it professional and concise."
            )
            try:
                resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}])
                ai_conclusion = resp.choices[0].message.content
            except: pass

        # --- VERDICT LOGIC ---
        verdict = "⚠️ WAIT"
        if asi_score >= 75 and macd_hist > 0: verdict = "🚀 STRONG BUY"
        elif asi_score >= 60 and ltp > ema_50: verdict = "✅ BUY"
        elif rsi > 70: verdict = "📉 OVERBOUGHT"
        
        # --- FORMATTING OUTPUT ---
        return (
            f"🔬 **DEEP ASI ANALYSIS: {sym}**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {datetime.now().strftime('%d-%b %H:%M')} | 🏛 **ASI SCORE:** {asi_score}/100\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            
            f"💰 **PRICE:** ₹{ltp:.2f}\n"
            f"📊 **INDICATORS:**\n"
            f"  • RSI(14): {rsi:.2f} {'🔥' if rsi>70 else '❄️' if rsi<30 else '⚖️'}\n"
            f"  • MACD: {'📈 Bullish' if macd_hist > 0 else '📉 Bearish'} ({macd_hist:.2f})\n"
            f"  • Volatility (ATR): {atr_pct:.2f}% {'(High Risk)' if atr_pct > 3 else '(Stable)'}\n"
            
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **VERDICT:** {verdict}\n"
            f"🛡 **RISK MANAGEMENT:**\n"
            f"  • SL: ₹{sl_price:.2f} (ATR Protected)\n"
            f"  • Target: ₹{target_price:.2f} (1:2 RR)\n"
            
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📐 **PIVOTS:**\n"
            f"  R1: {r1:.2f} | PP: {pp:.2f} | S1: {s1:.2f}\n"
            
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 **AI STRATEGIST:**\n{ai_conclusion}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "_SK AUTO AI ADVISORY - Deep Mode_"
        )

    except Exception as e:
        logging.error(f"Deep Report Error: {e}")
        return f"⚠️ Analysis Error: {str(e)}"


# --- 5. SMART OPTIONS STRATEGY ---

def get_nifty_option_trade(budget, spot):
    try:
        # Fetch Volatility Data
        hist = yf.Ticker("^NSEI").history(period="10d")
        if hist.empty: return "⚠️ Nifty Data Error"
        
        spot_price = float(hist['Close'].iloc[-1])
        daily_return = hist['Close'].pct_change()
        volatility = daily_return.std() * np.sqrt(252) * 100 # Annualized Vol
        
        strike = round(spot_price / 50) * 50
        option_type = "CALL" if spot_price > hist['Close'].iloc[-2] else "PUT"
        
        # Simple Premium Estimation
        # In high vol, premiums are expensive
        premium_mult = 1.2 if volatility > 15 else 1.0
        est_premium = 100 * premium_mult 
        
        lots = max(1, int(budget / (est_premium * 75)))
        
        # Calculate target based on movement
        target_premium = round(est_premium * 1.5, 2)
        sl_premium = round(est_premium * 0.5, 2)
        capital_req = round(est_premium * 75 * lots, 2)

        return (
            f"⚙️ **OPTIONS SNIPER ENGINE**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Spot:** {spot_price:.2f} | **Vol:** {volatility:.1f}%\n"
            f"🎯 **Trade:** {strike} {option_type}\n"
            f"💰 **Entry:** ~₹{est_premium:.0f} (Est)\n"
            f"🚀 **Target:** ₹{target_premium}\n"
            f"🛑 **SL:** ₹{sl_premium}\n"
            f"📦 **Lots:** {lots} (Cap: ₹{capital_req})\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"_Strategy: Directional Momentum_"
        )
    except Exception as e:
        return f"⚠️ Error: {e}"


# --- 6. SMART PORTFOLIO SCANNER ---

def get_smart_portfolio():
    try:
        large_caps = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK']
        mid_caps = ['PERSISTENT', 'TRENT', 'TATACONSUM', 'AUBANK']
        small_caps = ['SUZLON', 'TANLA', 'HEG']

        def scan_list(stocks, category_name):
            picks = []
            for sym in stocks:
                try:
                    df = yf.Ticker(f"{sym}.NS").history(period="60d", auto_adjust=True)
                    if len(df) < 30: continue
                    
                    close = df['Close']
                    ltp = close.iloc[-1]
                    ema20 = close.ewm(span=20).mean().iloc[-1]
                    rsi = calculate_rsi(close)
                    macd, sig, hist = calculate_macd(close)
                    
                    # Deep Filter: Trend + MACD Crossover
                    score = 0
                    if ltp > ema20: score += 50
                    if rsi > 50 and rsi < 70: score += 20
                    if hist > 0: score += 30 # Momentum Positive
                        
                    if score >= 70:
                        picks.append(f"✅ **{sym}** | RSI: {rsi:.0f} | Score: {score}")
                except: continue
            
            if not picks: return f"❌ No strong setups in {category_name}."
            return "\n".join(picks[:2])

        return (
            "💎 **SMART PORTFOLIO (Deep Scan)**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🏢 **LARGE CAP:**\n{scan_list(large_caps, 'Large')}\n\n"
            f"🏫 **MID CAP:**\n{scan_list(mid_caps, 'Mid')}\n\n"
            f"🚗 **SMALL CAP:**\n{scan_list(small_caps, 'Small')}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "_Analysis: Trend + MACD Confirmation_"
        )
    except Exception as e:
        return f"Error: {e}"

# --- 7. TELEGRAM HANDLERS ---

@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("🔬 Deep Analysis", "💎 Smart Portfolio")
    markup.add("⚙️ Options Sniper", "📊 Market Pulse")
    bot.send_message(m.chat.id, 
        "🚀 **SK AUTO AI ADVISORY**\n\n"
        "⚡ **Deep Analysis Mode Activated.**\n"
        "Select an option to begin:", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🔬 Deep Analysis")
def ask_symbol(m):
    msg = bot.send_message(m.chat.id, "📝 Enter Symbol for Deep Scan (e.g., RELIANCE, TCS):")
    bot.register_next_step_handler(msg, process_deep_analysis)

def process_deep_analysis(m):
    bot.send_chat_action(m.chat.id, 'typing')
    # Simple Symbol Cleaner
    sym = m.text.upper().replace(" ", "")
    bot.send_message(m.chat.id, get_sk_deep_report(sym), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "💎 Smart Portfolio")
def show_port(m):
    bot.send_chat_action(m.chat.id, 'typing')
    bot.send_message(m.chat.id, get_smart_portfolio(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⚙️ Options Sniper")
def ask_opt_budget(m):
    msg = bot.send_message(m.chat.id, "💵 Enter Capital for Options Trade (INR):")
    bot.register_next_step_handler(msg, process_options)

def process_options(m):
    try:
        budget = float(m.text.replace("₹", "").replace(",", ""))
        spot_df = yf.Ticker("^NSEI").history(period="1d")
        spot = float(spot_df['Close'].iloc[-1])
        bot.send_message(m.chat.id, get_nifty_option_trade(budget, spot), parse_mode="Markdown")
    except:
        bot.send_message(m.chat.id, "❌ Invalid amount. Please enter numbers only.")

@bot.message_handler(func=lambda m: m.text == "📊 Market Pulse")
def market_pulse(m):
    try:
        nifty = yf.Ticker("^NSEI").history(period="2d")
        bank = yf.Ticker("^NSEBANK").history(period="2d")
        
        n_change = ((nifty['Close'].iloc[-1] - nifty['Close'].iloc[-2]) / nifty['Close'].iloc[-2]) * 100
        b_change = ((bank['Close'].iloc[-1] - bank['Close'].iloc[-2]) / bank['Close'].iloc[-2]) * 100
        
        bot.send_message(m.chat.id,
            f"📊 **MARKET PULSE**\n"
            f"NIFTY 50: {nifty['Close'].iloc[-1]:.2f} ({n_change:+.2f}%)\n"
            f"BANK NIFTY: {bank['Close'].iloc[-1]:.2f} ({b_change:+.2f}%)\n"
            f"_Sentiment: {'Bullish' if n_change > 0 else 'Bearish'}_", parse_mode="Markdown")
    except:
        bot.send_message(m.chat.id, "⚠️ Market Data Unavailable.")

# --- 8. RENDER HEALTH CHECK ---

def run_health_server():
    import http.server
    import socketserver
    port = int(os.environ.get("PORT", 10000))
    
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"SK AI ADVISORY ONLINE")
    
    with socketserver.TCPServer(("", port), Handler) as httpd:
        httpd.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    logging.info("🚀 SK AUTO AI ADVISORY Started...")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
