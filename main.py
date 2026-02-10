import os
import telebot
import yfinance as yf
import google.generativeai as genai
import pandas as pd
import requests
import random
import time
from flask import Flask
from threading import Thread
from telebot import types
from datetime import datetime
import pytz

# --- 1. CONFIGURATION ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyCPh8wPC-rmBIyTr5FfV3Mwjb33KeZdRUE")
NEWS_KEY = os.environ.get("NEWS_API_KEY", "47fb3f33527944ed982e6e48cc856b23")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_KEY)

# Use Stable Model
try:
    model = genai.GenerativeModel('gemini-pro')
except:
    model = None

# --- 2. DATA UNIVERSE (CFA LEVEL) ---
LARGE_CAPS = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'HINDUNILVR', 'ITC', 'SBIN', 'BHARTIARTL', 'LICI']
MID_CAPS = ['TATACOMM', 'TRENT', 'POLYCAB', 'COFORGE', 'L&TFH', 'ASHOKLEY', 'ASTRAL', 'JUBLFOOD', 'PERSISTENT', 'MRF']
SMALL_CAPS = ['CDSL', 'BSE', 'SUZLON', 'IDEA', 'IEX', 'NBCC', 'HUDCO', 'IRFC', 'RVNL', 'SJVN']

# --- 3. ADVANCED ANALYTICS ENGINE ---
def get_market_breadth():
    """Estimates market sentiment using India VIX and Index movement"""
    try:
        nifty = yf.Ticker("^NSEI").history(period="5d")
        vix = yf.Ticker("^INDIAVIX").history(period="5d")
        
        if nifty.empty or vix.empty: return "Neutral"
        
        nifty_change = ((nifty['Close'].iloc[-1] - nifty['Close'].iloc[-2]) / nifty['Close'].iloc[-2]) * 100
        vix_val = vix['Close'].iloc[-1]
        
        if nifty_change > 0.5 and vix_val < 15: return "🟢 ULTRA BULLISH (Risk On)"
        if nifty_change > 0: return "🟢 MILDLY BULLISH"
        if nifty_change < -0.5 and vix_val > 20: return "🔴 EXTREME FEAR (Risk Off)"
        if nifty_change < 0: return "🔴 BEARISH"
        return "⚖️ SIDEWAYS / CHOPPY"
    except:
        return "Unavailable"

def get_full_analysis(symbol_input):
    """Smart Search + Technicals + Fundamentals"""
    try:
        # 1. Smart Symbol Logic
        symbol_input = symbol_input.upper().replace(" ", "")
        if symbol_input in ["NIFTY", "NIFTY50"]: ticker = "^NSEI"
        elif symbol_input in ["BANKNIFTY", "BANKEX"]: ticker = "^NSEBANK"
        elif symbol_input.endswith(".NS"): ticker = symbol_input
        else: ticker = f"{symbol_input}.NS"
        
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        
        if hist.empty: return None

        # 2. Extract Data
        curr = hist['Close'].iloc[-1]
        high_52 = hist['High'].max()
        low_52 = hist['Low'].min()
        
        # 3. Indicators
        sma_50 = hist['Close'].rolling(50).mean().iloc[-1]
        sma_200 = hist['Close'].rolling(200).mean().iloc[-1]
        
        delta = hist['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs)).iloc[-1]
        
        # 4. Fundamentals (Stocks only)
        info = stock.info
        is_index = '^' in ticker
        mcap = info.get('marketCap', 0) / 10000000 if not is_index else 0
        pe = info.get('trailingPE', 0) if not is_index else 0
        
        return {
            "symbol": ticker.replace(".NS", ""),
            "price": curr,
            "change": ((curr - hist['Close'].iloc[-2]) / hist['Close'].iloc[-2]) * 100,
            "rsi": rsi,
            "sma_50": sma_50,
            "sma_200": sma_200,
            "52h": high_52,
            "52l": low_52,
            "mcap": mcap,
            "pe": pe,
            "is_index": is_index
        }
    except Exception as e:
        print(e)
        return None

def generate_portfolio():
    """Generates a 50/35/15 Portfolio"""
    l = random.sample(LARGE_CAPS, 3)
    m = random.sample(MID_CAPS, 2)
    s = random.sample(SMALL_CAPS, 2)
    return l, m, s

# --- 4. AI AGENT (CFA PERSONA) ---
def ask_cfa_ai(task, data_context):
    prompt = f"""
    You are a CFA Charterholder and Chief Technical Strategist.
    
    CONTEXT:
    {data_context}
    
    TASK:
    {task}
    
    GUIDELINES:
    - Be precise, professional, and risk-aware.
    - Use terms like "Alpha", "Beta", "Hedging", "Theta Decay" where applicable.
    - For Options: Suggest Strikes based on the Price.
    """
    try:
        return model.generate_content(prompt).text
    except:
        return "⚠️ CFA AI Brain Offline."

# --- 5. BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    b1 = types.KeyboardButton('🚀 NIFTY 50')
    b2 = types.KeyboardButton('📈 BANK NIFTY')
    b3 = types.KeyboardButton('⚡ Option Strategy')
    b4 = types.KeyboardButton('💼 Portfolio Builder')
    b5 = types.KeyboardButton('🧠 Deep Market Scan')
    markup.add(b1, b2, b3, b4, b5)
    
    bot.send_message(m.chat.id, 
        "🏛 **Sovereign AI Advisory (CFA Edition)**\n"
        "Advanced Institutional Grade Analytics Online.\n\n"
        "Select a module:", reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def main_handler(m):
    # 1. Anti-Loop Logic
    if m.from_user.id == bot.get_me().id: return
    if "Scanning" in m.text or "Analyzing" in m.text: return
    
    text = m.text
    chat_id = m.chat.id
    
    # --- A. DEEP MARKET SCAN ---
    if text == '🧠 Deep Market Scan':
        bot.send_message(chat_id, "📡 **Scanning Institutional Data...**")
        breadth = get_market_breadth()
        vix_data = yf.Ticker("^INDIAVIX").history(period="1d")
        vix = vix_data['Close'].iloc[-1] if not vix_data.empty else 0
        
        prompt = f"Market Breadth: {breadth}. India VIX: {vix}. Analyze FII Sentiment and Sector Rotation."
        analysis = ask_cfa_ai("Provide a Market Health Report (FII View, Fear Index, Sector Focus).", prompt)
        
        msg = (f"🌍 **DEEP MARKET RESEARCH**\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"🌡 **Market Mood:** {breadth}\n"
               f"😨 **India VIX:** {vix:.2f}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"{analysis}")
        bot.send_message(chat_id, msg, parse_mode="Markdown")
        return

    # --- B. PORTFOLIO BUILDER ---
    if text == '💼 Portfolio Builder':
        l, m, s = generate_portfolio()
        msg = (f"💼 **AI GENERATED PORTFOLIO (Aggressive)**\n"
               f"_(50% Large / 35% Mid / 15% Small)_\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"🐘 **Large Cap (Stable):**\n"
               f"1. {l[0]} (20%)\n2. {l[1]} (15%)\n3. {l[2]} (15%)\n\n"
               f"🐎 **Mid Cap (Growth):**\n"
               f"1. {m[0]} (20%)\n2. {m[1]} (15%)\n\n"
               f"🚀 **Small Cap (Alpha):**\n"
               f"1. {s[0]} (10%)\n2. {s[1]} (5%)\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"⚠️ *Rebalance quarterly.*")
        bot.send_message(chat_id, msg, parse_mode="Markdown")
        return

    # --- C. OPTION STRATEGY ---
    if text == '⚡ Option Strategy':
        msg = bot.send_message(chat_id, "🔡 **Enter Symbol for Strategy** (e.g., NIFTY, RELIANCE):")
        bot.register_next_step_handler(msg, process_option_request)
        return

    # --- D. STANDARD STOCK/INDEX ANALYSIS ---
    # Handle Buttons or Manual Text
    mapping = {'🚀 NIFTY 50': 'NIFTY', '📈 BANK NIFTY': 'BANKNIFTY'}
    symbol = mapping.get(text, text)
    
    bot.send_chat_action(chat_id, 'typing')
    data = get_full_analysis(symbol)
    
    if not data:
        bot.reply_to(m, "❌ Symbol not found. Try 'TCS' or 'INFY'.")
        return

    # AI Verdict
    task = "Give Buy/Sell Verdict, Support/Resistance levels, and Red Flags."
    context = f"Symbol: {data['symbol']}. Price: {data['price']}. RSI: {data['rsi']}. SMA200: {data['sma_200']}."
    ai_msg = ask_cfa_ai(task, context)
    
    report = (f"🏛 **CFA RESEARCH NOTE: {data['symbol']}**\n"
              f"━━━━━━━━━━━━━━━━━━\n"
              f"💰 **LTP:** ₹{data['price']:.2f} ({data['change']:.2f}%)\n"
              f"📊 **Score:** {int(data['rsi'])}/100 | **PE:** {data['pe']:.1f}\n"
              f"📉 **52W High/Low:** {data['52h']:.0f} / {data['52l']:.0f}\n"
              f"━━━━━━━━━━━━━━━━━━\n"
              f"{ai_msg}\n"
              f"━━━━━━━━━━━━━━━━━━")
    bot.send_message(chat_id, report, parse_mode="Markdown")

def process_option_request(m):
    symbol = m.text
    data = get_full_analysis(symbol)
    if not data:
        bot.send_message(m.chat.id, "❌ Invalid Symbol.")
        return
        
    vix = yf.Ticker("^INDIAVIX").history(period="1d")['Close'].iloc[-1]
    
    task = f"Suggest an Option Strategy (Bull Call Spread, Iron Condor, etc) for {data['symbol']}."
    context = f"Spot: {data['price']}. Trend: {'Bullish' if data['price']>data['sma_50'] else 'Bearish'}. VIX: {vix}."
    
    strat = ask_cfa_ai(task, context)
    bot.send_message(m.chat.id, f"⚡ **OPTION STRATEGY: {data['symbol']}**\n\n{strat}", parse_mode="Markdown")

# --- 6. SERVER ---
app = Flask('')
@app.route('/')
def home(): return "✅ CFA Bot Online"
def run_http(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
def keep_alive(): 
    t = Thread(target=run_http)
    t.start()

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
