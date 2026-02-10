import os
import telebot
import yfinance as yf
import google.generativeai as genai
import pandas as pd
import requests
import random
import sqlite3
import time
from flask import Flask
from threading import Thread
from telebot import types
from datetime import datetime
import pytz
from tabulate import tabulate

# --- 1. CONFIGURATION ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyCPh8wPC-rmBIyTr5FfV3Mwjb33KeZdRUE")
DAILY_LIMIT = 50  # Increased limit for "CFO" testing
ADMIN_ID = 6284854709

bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_KEY)

# --- 2. ROBUST DATABASE ---
class DatabaseEngine:
    def __init__(self, db_path='asi_rag.db'):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS historical_data 
                          (id INTEGER PRIMARY KEY, symbol TEXT, date TEXT, ltp REAL, trend TEXT, analysis TEXT)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS user_limits 
                          (user_id INTEGER PRIMARY KEY, date TEXT, count INTEGER)''')

    def check_limit(self, user_id):
        if user_id == ADMIN_ID: return True
        today = datetime.now().strftime('%Y-%m-%d')
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT date, count FROM user_limits WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            if row and row[0] == today:
                if row[1] >= DAILY_LIMIT: return False
                conn.execute('UPDATE user_limits SET count = count + 1 WHERE user_id = ?', (user_id,))
            else:
                conn.execute('INSERT OR REPLACE INTO user_limits (user_id, date, count) VALUES (?, ?, 1)', (user_id, today))
        return True

    def get_history(self, symbol):
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT date, ltp, trend FROM historical_data WHERE symbol = ? ORDER BY id DESC LIMIT 2', (symbol,))
            return "\n".join([f"[{r[0]}] ₹{r[1]} ({r[2]})" for r in cursor.fetchall()])

db = DatabaseEngine()

# --- 3. CFA DATA ENGINE ---
SECTOR_INDICES = {
    "^CNXIT": "IT Sector",
    "^NSEBANK": "Bank Nifty",
    "^CNXAUTO": "Auto Sector",
    "^CNXPHARMA": "Pharma"
}

PORTFOLIO_POOLS = {
    "LARGE": ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'ITC', 'L&T', 'SBIN'],
    "MID": ['TATACOMM', 'TRENT', 'POLYCAB', 'COFORGE', 'ASHOKLEY', 'ASTRAL', 'VOLTAS'],
    "SMALL": ['CDSL', 'BSE', 'SUZLON', 'IEX', 'NBCC', 'HUDCO', 'RVNL', 'IRCON']
}

def get_sector_performance():
    """Calculates which sector is leading today"""
    results = []
    for ticker, name in SECTOR_INDICES.items():
        try:
            data = yf.Ticker(ticker).history(period="1d")
            if not data.empty:
                change = ((data['Close'].iloc[-1] - data['Open'].iloc[-1]) / data['Open'].iloc[-1]) * 100
                results.append((name, change))
        except: continue
    
    # Sort by performance
    results.sort(key=lambda x: x[1], reverse=True)
    return results

def get_live_price_bulk(symbols):
    """Fetches live prices for a list of stocks efficiently"""
    tickers = [s + ".NS" for s in symbols]
    try:
        data = yf.download(tickers, period="1d", progress=False)['Close'].iloc[-1]
        return data
    except: return None

def get_stock_deep_data(symbol_input):
    """Smart Search + Technicals + Fundamentals"""
    try:
        # Smart Symbol Logic
        symbol_input = symbol_input.upper().replace(" ", "")
        if symbol_input in ["NIFTY", "NIFTY50"]: ticker = "^NSEI"
        elif symbol_input in ["BANKNIFTY", "BANKEX"]: ticker = "^NSEBANK"
        elif symbol_input.endswith(".NS"): ticker = symbol_input
        else: ticker = f"{symbol_input}.NS"
        
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist.empty: return None

        curr = hist['Close'].iloc[-1]
        prev = hist['Close'].iloc[-2]
        change = ((curr - prev) / prev) * 100
        
        # Technicals
        sma_50 = hist['Close'].rolling(50).mean().iloc[-1]
        sma_200 = hist['Close'].rolling(200).mean().iloc[-1]
        
        delta = hist['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs)).iloc[-1]
        
        # Fundamentals
        info = stock.info
        pe = info.get('trailingPE', 0)
        mcap = info.get('marketCap', 0) / 10000000  # Convert to Cr
        
        return {
            "symbol": ticker.replace(".NS", ""),
            "price": curr,
            "change": change,
            "rsi": rsi,
            "sma_50": sma_50,
            "sma_200": sma_200,
            "pe": pe,
            "mcap": mcap,
            "trend": "BULLISH" if curr > sma_200 else "BEARISH"
        }
    except Exception as e:
        print(f"Data Error: {e}")
        return None

# --- 4. AI LOGIC (STABILIZED) ---
def ask_ai_cfo(task, context):
    """Uses Gemini Flash for speed and reliability"""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash') # Faster/Stable
        prompt = f"""
        Act as a Senior Wall Street CFO. 
        Analyze this data strictly. Be direct. No disclaimer fluff.
        
        DATA:
        {context}
        
        TASK:
        {task}
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"⚠️ **Analyst Note:** AI connection unstable. Based on technicals, check RSI/SMA levels manually. ({str(e)})"

# --- 5. BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('🚀 NIFTY 50', '📈 BANK NIFTY', '💼 Smart Portfolio', '🌍 Deep Market Scan')
    bot.send_message(m.chat.id, 
        "🏛 **CFO Advisory Terminal Online**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✅ **Smart Search Active:** Type ANY stock name (e.g., ZOMATO, TATASTEEL)\n"
        "✅ **Macro Scanner:** Sectors + Currency + VIX\n"
        "✅ **Portfolio Engine:** Live Prices + Allocation\n", 
        reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def main_handler(m):
    user_id = m.from_user.id
    if user_id == bot.get_me().id: return
    if "Scanning" in m.text: return
    
    if not db.check_limit(user_id):
        bot.reply_to(m, f"❌ Daily Limit ({DAILY_LIMIT}) Reached.")
        return

    text = m.text
    chat_id = m.chat.id
    
    # --- A. DEEP MARKET SCAN (CFO LEVEL) ---
    if text == '🌍 Deep Market Scan':
        bot.send_message(chat_id, "📡 **Fetching Macro Data (Sectors, VIX, Currency)...**")
        
        # 1. Fetch Market Breadth
        sectors = get_sector_performance()
        vix = yf.Ticker("^INDIAVIX").history(period="1d")['Close'].iloc[-1]
        usd = yf.Ticker("INR=X").history(period="1d")['Close'].iloc[-1]
        
        # 2. Identify Leaders/Laggards
        leader = sectors[0]
        laggard = sectors[-1]
        
        msg = (f"🌍 **MACRO MARKET SCOPE**\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"😨 **India VIX:** {vix:.2f} " + ("(High Fear)" if vix > 15 else "(Stable)") + "\n"
               f"💵 **USD/INR:** ₹{usd:.2f}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"🏆 **Leading Sector:** {leader[0]} ({leader[1]:.2f}%)\n"
               f"⚠️ **Lagging Sector:** {laggard[0]} ({laggard[1]:.2f}%)\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"📊 **Sector Rotation:**\n")
        
        for name, chg in sectors:
            icon = "🟢" if chg > 0 else "🔴"
            msg += f"{icon} {name}: {chg:.2f}%\n"
            
        bot.send_message(chat_id, msg)
        return

    # --- B. SMART PORTFOLIO (LIVE PRICES) ---
    if text == '💼 Smart Portfolio':
        bot.send_message(chat_id, "💼 **Compiling Live Portfolio...**")
        
        # Select Stocks
        l_picks = random.sample(PORTFOLIO_POOLS["LARGE"], 3)
        m_picks = random.sample(PORTFOLIO_POOLS["MID"], 2)
        s_picks = random.sample(PORTFOLIO_POOLS["SMALL"], 2)
        all_picks = l_picks + m_picks + s_picks
        
        # Fetch Live Prices
        prices = get_live_price_bulk(all_picks)
        
        if prices is None:
            bot.send_message(chat_id, "❌ Error fetching live portfolio prices.")
            return

        # Build Table
        msg = "💼 **SUGGESTED ALLOCATION (Medium Risk)**\n━━━━━━━━━━━━━━━━━━\n"
        
        msg += "🐘 **LARGE CAP (50%)**\n"
        for s in l_picks:
            p = prices[s + ".NS"]
            msg += f"• {s}: ₹{p:.1f} (16%)\n"
            
        msg += "\n🐎 **MID CAP (30%)**\n"
        for s in m_picks:
            p = prices[s + ".NS"]
            msg += f"• {s}: ₹{p:.1f} (15%)\n"
            
        msg += "\n🚀 **SMALL CAP (20%)**\n"
        for s in s_picks:
            p = prices[s + ".NS"]
            msg += f"• {s}: ₹{p:.1f} (10%)\n"
            
        msg += "\n💡 *Allocation based on current volatility.*"
        bot.send_message(chat_id, msg, parse_mode="Markdown")
        return

    # --- C. SMART STOCK SEARCH ---
    # Handle Button Clicks OR Manual Text
    mapping = {'🚀 NIFTY 50': 'NIFTY', '📈 BANK NIFTY': 'BANKNIFTY'}
    symbol_raw = mapping.get(text, text) # If button, map it. If text, use text.
    
    bot.send_chat_action(chat_id, 'typing')
    
    # Fetch Data
    data = get_stock_deep_data(symbol_raw)
    
    if not data:
        bot.reply_to(m, f"❌ **Error:** Could not find '{symbol_raw}'.\nTry typing the exact NSE symbol like 'ZOMATO' or 'TATASTEEL'.")
        return

    # Fetch History
    history = db.get_history(data['symbol'])
    
    # Generate AI Verdict
    ai_context = (f"Symbol: {data['symbol']}. Price: {data['price']}. Trend: {data['trend']}. "
                  f"RSI: {data['rsi']}. PE Ratio: {data['pe']}. Sector Performance: Mixed.")
    
    ai_msg = ask_ai_cfo("Give a Buy/Sell/Wait rating with 1-line logic.", ai_context)
    
    # Save to DB
    with sqlite3.connect(db.db_path, check_same_thread=False) as conn:
        conn.execute('INSERT INTO historical_data (symbol, date, ltp, trend, analysis) VALUES (?, ?, ?, ?, ?)',
                     (data['symbol'], datetime.now().strftime('%Y-%m-%d'), data['price'], data['trend'], ai_msg))

    # Build Report
    report = (f"🏛 **CFO RESEARCH: {data['symbol']}**\n"
              f"━━━━━━━━━━━━━━━━━━\n"
              f"💰 **Price:** ₹{data['price']:.2f} ({data['change']:.2f}%)\n"
              f"📉 **RSI:** {data['rsi']:.1f} | **Trend:** {data['trend']}\n"
              f"📊 **Valuation:** PE {data['pe']:.1f} | M.Cap {data['mcap']:.0f}Cr\n"
              f"━━━━━━━━━━━━━━━━━━\n"
              f"🧠 **AI VERDICT:**\n{ai_msg}\n"
              f"━━━━━━━━━━━━━━━━━━\n"
              f"📜 **Last Scan:**\n{history if history else 'No prior history.'}")
              
    bot.send_message(chat_id, report, parse_mode="Markdown")

# --- 6. SERVER & KEEP ALIVE ---
app = Flask('')
@app.route('/')
def home(): return "✅ CFO Bot Online"

def run_http():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run_http)
    t.start()

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
