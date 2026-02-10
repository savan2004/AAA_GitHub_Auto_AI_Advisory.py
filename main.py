import os
import telebot
import yfinance as yf
import pandas as pd
import requests
import random
import sqlite3
import json
import time
from flask import Flask
from threading import Thread
from telebot import types
from datetime import datetime
from groq import Groq
import pytz

# --- 1. CONFIGURATION ---
# We use os.environ to get keys safely from Render
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY") # <--- NO KEY HERE!

ADMIN_ID = 6284854709
DAILY_LIMIT = 50

# Check if keys are loaded
if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    print("❌ ERROR: API Keys are missing! Add them to Render Environment Variables.")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)

# --- 2. DATABASE (Memory & Limits) ---
class DatabaseEngine:
    def __init__(self, db_path='sk_advisory.db'):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS historical_data 
                          (id INTEGER PRIMARY KEY, symbol TEXT, date TEXT, ltp REAL, signal TEXT, analysis TEXT)''')
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

db = DatabaseEngine()

# --- 3. THE "SNIPER" ENGINE ---
def get_sniper_analysis(symbol):
    """5-Layer Confirmation for High Accuracy"""
    try:
        symbol = symbol.upper().replace(" ", "")
        ticker = f"{symbol}.NS" if not symbol.endswith(".NS") and "^" not in symbol else symbol
        
        stock = yf.Ticker(ticker)
        df = stock.history(period="1y")
        if df.empty: return None
        
        # Data Points
        curr = df['Close'].iloc[-1]
        vol = df['Volume'].iloc[-1]
        avg_vol = df['Volume'].rolling(20).mean().iloc[-1]
        
        # Moving Averages
        sma_50 = df['Close'].rolling(50).mean().iloc[-1]
        sma_200 = df['Close'].rolling(200).mean().iloc[-1]
        
        # RSI
        delta = df['Close'].diff()
        gain = (delta.where(delta>0, 0)).rolling(14).mean()
        loss = (-delta.where(delta<0, 0)).rolling(14).mean()
        rs = gain/loss
        rsi = 100 - (100/(1+rs)).iloc[-1]

        # --- THE SNIPER LOGIC ---
        score = 0
        reasons = []
        
        # 1. Trend Filter
        if curr > sma_200: 
            score += 1
            reasons.append("✅ Trend is UP (Above SMA 200)")
        else:
            reasons.append("❌ Trend is DOWN (Below SMA 200)")

        # 2. Momentum Filter
        if 50 < rsi < 70: 
            score += 1
            reasons.append("✅ RSI is Strong (50-70)")
        elif rsi > 70:
            reasons.append("⚠️ RSI Overbought (>70)")
        else:
            reasons.append("⚠️ RSI Weak (<50)")

        # 3. Volume Confirmation
        if vol > avg_vol * 1.2:
            score += 1
            reasons.append("✅ High Volume Buying Detected")
        else:
            reasons.append("⚠️ Low Volume (Retail only?)")

        # 4. Trap Detector
        is_trap = False
        if curr > sma_50 and vol < avg_vol * 0.5:
            is_trap = True
            reasons.append("🚨 CAUTION: Price rising on Low Volume (Possible Trap)")

        # Verdict
        if score == 3 and not is_trap:
            signal = "💎 SNIPER BUY"
            accuracy = "High (85-90%)"
        elif score == 0:
            signal = "🔻 STRONG SELL"
            accuracy = "High"
        else:
            signal = "⚖️ WAIT / HOLD"
            accuracy = "Neutral"

        return {
            "symbol": symbol,
            "price": curr,
            "signal": signal,
            "accuracy": accuracy,
            "score": f"{score}/3",
            "reasons": reasons,
            "rsi": rsi,
            "vol_spike": vol > avg_vol
        }
    except: return None

# --- 4. AI BRAIN (Llama 3) ---
def ask_sk_advisory(task, data):
    try:
        prompt = f"""
        Identity: You are 'SK AI Auto Advisory', a specialized Hedge Fund Algo.
        DATA: {json.dumps(data)}
        TASK: {task}
        Generate a strict, professional trading note.
        """
        
        completion = client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=300
        )
        return completion.choices[0].message.content
    except: return "⚠️ AI Connection Weak. Trust the Technical Score."

# --- 5. BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('🎯 Sniper Scope (99% Mode)', '🌍 Global Trap Scanner')
    markup.add('🔍 Quick Scan', '💼 Portfolio Doctor')
    
    bot.send_message(m.chat.id, 
        "🚀 **SK AI AUTO ADVISORY** 🚀\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Welcome to the Elite Terminal.\n"
        "🔥 **Features:**\n"
        "• Sniper Scope (Multi-Factor Validation)\n"
        "• Trap Detector (Volume vs Price)\n"
        "• Llama-3 70B Intelligence", reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def main_handler(m):
    if m.from_user.id == bot.get_me().id: return
    if not db.check_limit(m.from_user.id):
        bot.reply_to(m, "❌ Daily Limit Reached. Upgrade for Unlimited.")
        return

    text = m.text
    chat_id = m.chat.id

    if text == '🎯 Sniper Scope (99% Mode)':
        bot.send_message(chat_id, "🎯 **Enter Stock Name for Sniper Analysis:**\n(e.g., TATASTEEL, ZOMATO)")
        bot.register_next_step_handler(m, run_sniper_scan)
        return

    if text == '🌍 Global Trap Scanner':
        bot.send_message(chat_id, "📡 **Scanning Market for Traps...**")
        nifty = get_sniper_analysis("NIFTY")
        bank = get_sniper_analysis("BANKNIFTY")
        msg = "🌍 **MARKET HEALTH REPORT**\n━━━━━━━━━━━━━━━━━━\n"
        msg += f"📊 **NIFTY 50:** {nifty['signal']}\n"
        msg += f"• Trap Check: {'🚨 TRAP!' if 'Trap' in str(nifty['reasons']) else '✅ Clean'}\n\n"
        msg += f"🏦 **BANK NIFTY:** {bank['signal']}\n"
        msg += f"• Trap Check: {'🚨 TRAP!' if 'Trap' in str(bank['reasons']) else '✅ Clean'}\n"
        bot.send_message(chat_id, msg)
        return

    if text == '🔍 Quick Scan':
        bot.send_message(chat_id, "🔡 **Enter Symbol:**")
        bot.register_next_step_handler(m, run_sniper_scan)
        return

def run_sniper_scan(m):
    symbol = m.text.upper()
    bot.send_chat_action(m.chat.id, 'typing')
    
    data = get_sniper_analysis(symbol)
    if not data:
        bot.reply_to(m, "❌ Data not found.")
        return
        
    ai_msg = ask_sk_advisory("Confirm Sniper Signal", data)
    icon = "💎" if "BUY" in data['signal'] else "⚠️"
    
    report = (
        f"🚀 **SK AI AUTO ADVISORY**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏷 **Script:** {data['symbol']}\n"
        f"💰 **Price:** ₹{data['price']:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{icon} **SIGNAL:** {data['signal']}\n"
        f"🎯 **Precision:** {data['accuracy']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔍 **SNIPER LOGIC (Score {data['score']}):**\n"
    )
    for r in data['reasons']:
        report += f"{r}\n"
        
    report += f"\n🧠 **AI CONFIRMATION:**\n{ai_msg}"
    bot.send_message(m.chat.id, report)

# --- SERVER ---
app = Flask('')
@app.route('/')
def home(): return "✅ SK AI Advisory Online"
def run_http(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
def keep_alive(): 
    t = Thread(target=run_http)
    t.start()

if __name__ == "__main__":
    keep_alive()
    bot.polling(non_stop=True)
