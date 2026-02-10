import os
import telebot
import yfinance as yf
import google.generativeai as genai
import pandas as pd
import requests
from flask import Flask
from threading import Thread
from telebot import types
from datetime import datetime
import pytz

# --- 1. CONFIGURATION & KEYS ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyCPh8wPC-rmBIyTr5FfV3Mwjb33KeZdRUE")
NEWS_KEY = os.environ.get("NEWS_API_KEY", "47fb3f33527944ed982e6e48cc856b23")

# Initialize AI & Bot
bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_KEY)

# Try to use the best model, fallback if needed
try:
    model = genai.GenerativeModel('gemini-1.5-pro-latest') # Smartest for deep analysis
except:
    model = genai.GenerativeModel('gemini-pro')

# --- 2. ADVANCED DATA ENGINE ---
def get_full_analysis(ticker_symbol):
    try:
        stock = yf.Ticker(ticker_symbol)
        
        # A. Fetch Historical Data (1 Year for SMA 200)
        hist = stock.history(period="1y")
        if hist.empty: return None

        # Current Price & Basic Info
        current_price = hist['Close'].iloc[-1]
        prev_close = hist['Close'].iloc[-2]
        
        # B. Calculate Technical Indicators (SMAs)
        hist['SMA_20'] = hist['Close'].rolling(window=20).mean()
        hist['SMA_50'] = hist['Close'].rolling(window=50).mean()
        hist['SMA_200'] = hist['Close'].rolling(window=200).mean()
        
        sma_20 = hist['SMA_20'].iloc[-1]
        sma_50 = hist['SMA_50'].iloc[-1]
        sma_200 = hist['SMA_200'].iloc[-1]

        # C. Calculate RSI
        delta = hist['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs)).iloc[-1]

        # D. Pivot Points (Classic)
        high = hist['High'].iloc[-1]
        low = hist['Low'].iloc[-1]
        close = hist['Close'].iloc[-1]
        pp = (high + low + close) / 3
        r1 = (2 * pp) - low
        s1 = (2 * pp) - high
        r2 = pp + (high - low)
        s2 = pp - (high - low)
        r3 = high + 2 * (pp - low)
        s3 = low - 2 * (high - pp)

        # E. Fetch Fundamentals (Info Dict)
        info = stock.info
        market_cap = info.get('marketCap', 0) / 10000000 # Convert to Cr
        pe_ratio = info.get('trailingPE', 0)
        roe = info.get('returnOnEquity', 0) * 100
        fifty_high = info.get('fiftyTwoWeekHigh', 0)
        fifty_low = info.get('fiftyTwoWeekLow', 0)
        sector = info.get('sector', 'Unknown')
        
        # F. Calculate Targets (Automated Math)
        # Target 1 (Short Term): 1.5% move
        # Target 2 (Mid Term): 5% move
        if current_price > sma_50: # Bullish Bias
            tgt1 = current_price * 1.015
            tgt2 = current_price * 1.05
            direction_bias = "BULLISH"
        else: # Bearish Bias
            tgt1 = current_price * 0.985
            tgt2 = current_price * 0.95
            direction_bias = "BEARISH"

        return {
            "price": current_price,
            "prev_close": prev_close,
            "change": ((current_price - prev_close)/prev_close)*100,
            "rsi": rsi,
            "sma_20": sma_20,
            "sma_50": sma_50,
            "sma_200": sma_200,
            "pp": pp, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3,
            "mcap": market_cap,
            "pe": pe_ratio,
            "roe": roe,
            "52_high": fifty_high,
            "52_low": fifty_low,
            "sector": sector,
            "tgt1": tgt1,
            "tgt2": tgt2,
            "bias": direction_bias
        }
    except Exception as e:
        print(f"Analysis Error: {e}")
        return None

def get_news(query):
    try:
        url = f"https://newsapi.org/v2/everything?q={query}&sortBy=publishedAt&apiKey={NEWS_KEY}&language=en&pageSize=3"
        data = requests.get(url).json()
        articles = data.get("articles", [])
        return "\n".join([f"👉 {a['title']}" for a in articles[:3]])
    except:
        return "👉 No major news found."

# --- 3. AI REPORT GENERATOR ---
def generate_report(symbol_name, ticker):
    data = get_full_analysis(ticker)
    if not data: return "⚠️ Error: Could not fetch deep data."
    
    news = get_news(symbol_name)
    
    # Get Current Time IST
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    date_str = now.strftime("%d-%b-%Y")
    time_str = now.strftime("%H:%M")

    # Ask AI for Qualitative Pros/Cons only (We did the math already)
    prompt = f"""
    Analyze {symbol_name}.
    Data: Price {data['price']}, RSI {data['rsi']}, PE {data['pe']}, ROE {data['roe']}.
    Trend is {data['bias']} because price is relative to SMA 50 ({data['sma_50']}).
    News: {news}
    
    Task:
    1. List 2 Positive Points (Pros)
    2. List 2 Negative Points (Cons)
    3. One line conclusion.
    
    Keep it strictly professional and short.
    """
    
    try:
        ai_response = model.generate_content(prompt).text
    except:
        ai_response = "AI Analysis Unavailable."

    # --- FINAL MESSAGE FORMATTING (Strictly requested format) ---
    report = (
        f"🚀 *SK AUTO AI ADVISORY* 🚀\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 *DATE:* {date_str} | ⏰ *TIME:* {time_str} (IST)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 *SYMBOL:* {symbol_name}\n"
        f"🏛 *SECTOR:* {data['sector']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *LTP:* ₹{data['price']:.2f} ({data['change']:.2f}%)\n"
        f"📊 *RSI:* {data['rsi']:.1f} | 📏 *52W H/L:* {data['52_high']} / {data['52_low']}\n"
        f"📈 *TREND:* {data['bias']} (SMA200: {data['sma_200']:.1f})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *TARGETS (Short Term)*\n"
        f"🚀 *TGT 1:* ₹{data['tgt1']:.2f} (1.5%)\n"
        f"🚀 *TGT 2:* ₹{data['tgt2']:.2f} (5.0%)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *FUNDAMENTAL LEVELS*\n"
        f"* Market Cap: {data['mcap']:.0f} Cr\n"
        f"* P/E Ratio: {data['pe']:.1f}x | ROE: {data['roe']:.1f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏗 *DEEP TECHNICAL LEVELS (Pivots)*\n"
        f"🔴 R3: {data['r3']:.1f} | R2: {data['r2']:.1f}\n"
        f"🔴 R1: {data['r1']:.1f} | 🟢 PP: {data['pp']:.1f}\n"
        f"🟢 S1: {data['s1']:.1f} | S2: {data['s2']:.1f} | S3: {data['s3']:.1f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 *AI INTELLIGENCE*\n"
        f"{ai_response}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📰 *LATEST NEWS:*\n"
        f"{news}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ *RISK:* High Volatility. Consult Financial Advisor."
    )
    return report

# --- 4. FLASK KEEP ALIVE ---
app = Flask('')
@app.route('/')
def home(): return "✅ SK Sovereign Bot Running 24/7"
def run_http(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
def keep_alive(): 
    t = Thread(target=run_http)
    t.start()

# --- 5. TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('🚀 NIFTY 50', '📈 BANK NIFTY', '⛽ RELIANCE', '🏦 HDFC BANK')
    bot.send_message(m.chat.id, "🏛 **SK AI System Online**\nSelect Asset for Deep Scan:", reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def handle(m):
    # Symbol Mapping
    map_sym = {
        '🚀 NIFTY 50': ('NIFTY 50', '^NSEI'),
        '📈 BANK NIFTY': ('BANK NIFTY', '^NSEBANK'),
        '⛽ RELIANCE': ('RELIANCE IND', 'RELIANCE.NS'),
        '🏦 HDFC BANK': ('HDFC BANK', 'HDFCBANK.NS')
    }
    
    # Custom Symbol Handler (e.g. user types "TCS")
    if m.text in map_sym:
        name, ticker = map_sym[m.text]
    else:
        # Try to guess Indian stock
        name = m.text.upper()
        ticker = f"{name}.NS"
    
    bot.send_chat_action(m.chat.id, 'typing')
    bot.send_message(m.chat.id, f"🔍 **Scanning Fundamentals & Technicals for {name}...**")
    
    report = generate_report(name, ticker)
    bot.send_message(m.chat.id, report, parse_mode="Markdown")

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
