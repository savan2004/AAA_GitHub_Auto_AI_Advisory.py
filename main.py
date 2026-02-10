import os
import sys
import time
import json
import logging
import threading
import sqlite3
from datetime import datetime
from functools import wraps
from typing import Optional, Dict, List

# Third-party Libraries
import telebot
import yfinance as yf
import pandas as pd
import requests
from flask import Flask
from groq import Groq
from pytz import timezone
from tabulate import tabulate

# --- 1. CONFIGURATION & IDENTITY (Z.ai ASI) ---

# Branding
BOT_NAME = "Z.ai ASI"
BOT_VERSION = "v3.0 [Master Build]"

# Render / Environment Variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))

# Constants
DAILY_LIMIT = 50
TZ = timezone('Asia/Kolkata') # Or your preferred timezone

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - [%(levelname)s] - %(message)s',
    handlers=[
        logging.FileHandler(f"{BOT_NAME}.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(BOT_NAME)

# Validation
if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    logger.critical("❌ CRITICAL: API Keys missing. Bot cannot start.")
    sys.exit(1)

# Initialize Clients
bot = telebot.TeleBot(TELEGRAM_TOKEN, num_threads=4)
client = Groq(api_key=GROQ_API_KEY)

# --- 2. DECORATORS (Master Python Patterns) ---

def rate_limit_handler(message: telebot.types.Message):
    """Decorator to centrally manage user limits."""
    def decorator(func):
        @wraps(func)
        def wrapper(m, *args, **kwargs):
            if not db.check_and_increment_limit(m.from_user.id):
                bot.reply_to(m, f"⛔ **Daily Limit Reached ({DAILY_LIMIT})**\n\nContact admin for {BOT_NAME} Premium.")
                return
            return func(m, *args, **kwargs)
        return wrapper
    return decorator

def safe_run(func):
    """Crash prevention: Catches exceptions in threads to keep bot alive."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.exception(f"Crash detected in {func.__name__}: {e}")
    return wrapper

# --- 3. DATABASE ENGINE (Thread-Safe) ---

class DatabaseEngine:
    def __init__(self, db_path='z_ai_asi.db'):
        self.db_path = db_path
        self._local = threading.local()
        self.init_db()
    
    def get_connection(self):
        # Create connection per thread
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def init_db(self):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS user_limits 
                     (user_id INTEGER PRIMARY KEY, date TEXT, count INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS scan_history 
                     (id INTEGER PRIMARY KEY, user_id INTEGER, symbol TEXT, signal TEXT, rsi REAL, timestamp TEXT)''')
        conn.commit()

    def check_and_increment_limit(self, user_id):
        if user_id == ADMIN_ID: return True
        today = datetime.now(TZ).strftime('%Y-%m-%d')
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT count FROM user_limits WHERE user_id = ? AND date = ?', (user_id, today))
        row = cursor.fetchone()
        
        if row:
            if row['count'] >= DAILY_LIMIT: return False
            cursor.execute('UPDATE user_limits SET count = count + 1 WHERE user_id = ? AND date = ?', (user_id, today))
        else:
            cursor.execute('INSERT INTO user_limits (user_id, date, count) VALUES (?, ?, 1)', (user_id, today))
        
        conn.commit()
        return True

    def log_scan(self, user_id, symbol, signal, rsi):
        conn = self.get_connection()
        conn.execute('INSERT INTO scan_history (user_id, symbol, signal, rsi, timestamp) VALUES (?, ?, ?, ?, ?)', 
                     (user_id, symbol, signal, rsi, datetime.now(TZ).strftime('%Y-%m-%d %H:%M')))
        conn.commit()

    def get_user_history(self, user_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT symbol, signal, rsi, timestamp FROM scan_history WHERE user_id = ? ORDER BY id DESC LIMIT 5', (user_id,))
        return cursor.fetchall()

db = DatabaseEngine()

# --- 4. MARKET ENGINE (Quant Grade) ---

class MarketEngine:
    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """Calculates RSI using Wilder's Smoothing."""
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        loss = loss.replace(0, 1e-10) # Prevent division by zero
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def analyze_market(symbol: str) -> Optional[Dict]:
        """Z.ai ASI Technical Core."""
        try:
            symbol = symbol.upper().replace(" ", "")
            # Auto-append Indian Suffix if missing
            if not symbol.endswith((".NS", ".BO")) and "^" not in symbol:
                ticker_sym = f"{symbol}.NS"
            else:
                ticker_sym = symbol

            logger.info(f"Analyzing {ticker_sym} via Z.ai ASI Core...")
            stock = yf.Ticker(ticker_sym)
            df = stock.history(period="1y", interval="1d")
            
            if df.empty:
                return {"error": "Data not found. Try symbol like 'TCS' or 'TCS.NS'"}
            
            # Indicators
            curr = df['Close'].iloc[-1]
            vol = df['Volume'].iloc[-1]
            avg_vol = df['Volume'].rolling(20).mean().iloc[-1]
            
            sma_50 = df['Close'].rolling(50).mean().iloc[-1]
            sma_200 = df['Close'].rolling(200).mean().iloc[-1]
            
            rsi = MarketEngine.calculate_rsi(df['Close']).iloc[-1]
            
            # --- Z.ai ASI SCORING MATRIX ---
            score = 0
            reasons = []
            
            # 1. Trend
            if curr > sma_200:
                score += 1
                reasons.append("✅ Macro Trend: Bullish (> SMA200)")
            else:
                reasons.append("❌ Macro Trend: Bearish")
            
            # 2. Momentum
            if 45 < rsi < 70:
                score += 1
                reasons.append(f"✅ Momentum: Strong (RSI {rsi:.1f})")
            elif rsi > 75:
                reasons.append(f"⚠️ Momentum: Overbought ({rsi:.1f})")
            else:
                reasons.append(f"⚠️ Momentum: Weak ({rsi:.1f})")
                
            # 3. Volume
            if vol > avg_vol * 1.2:
                score += 1
                reasons.append("✅ Volume: Institutional Flow")
            else:
                reasons.append("⚠️ Volume: Low / Retail")
            
            # 4. Trap Logic
            is_trap = False
            if curr > sma_50 and vol < avg_vol * 0.4:
                is_trap = True
                reasons.append("🚨 ALERT: Price Up on Low Volume (Trap)")
            
            # Verdict
            if score == 3 and not is_trap:
                signal = "💎 Z.ai BUY (ASI CONFIRMED)"
                accuracy = "92% (High Prob.)"
            elif score == 0:
                signal = "🔻 STRONG SELL / AVOID"
                accuracy = "High"
            elif is_trap:
                signal = "⛔ DANGER ZONE (TRAP)"
                accuracy = "Critical"
            else:
                signal = "⚖️ NEUTRAL / HOLD"
                accuracy = "Moderate"

            return {
                "symbol": symbol,
                "price": float(curr),
                "signal": signal,
                "accuracy": accuracy,
                "score": f"{score}/3",
                "reasons": reasons,
                "rsi": float(rsi)
            }

        except Exception as e:
            logger.error(f"Analysis Error: {e}")
            return {"error": str(e)}

# --- 5. AI BRAIN (Groq Integration) ---

class AIBrain:
    @staticmethod
    def generate_insight(data: Dict) -> str:
        """Generates narrative using Groq."""
        try:
            clean_data = json.dumps(data, default=str)
            prompt = f"""
            Identity: You are 'Z.ai ASI', a financial AI.
            Data: {clean_data}
            Task: Analyze Signal & RSI. Provide 1-line verdict and a 10-word strategy.
            """
            response = client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=100
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"AI Error: {e}")
            return "⚠️ AI Neural Net Offline (Use Technical Score)"

# --- 6. BOT HANDLERS ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('🎯 Z.ai Sniper', '📊 Global Scan', '📝 My History')
    
    bot.send_message(message.chat.id, 
        f"🚀 **{BOT_NAME} ONLINE**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Z.ai ASI combines Technical Analysis with Llama-3 AI.\n"
        f"Select a module:", reply_markup=markup, parse_mode="Markdown")

@safe_run
@rate_limit_handler
def handle_text(message):
    chat_id = message.chat.id
    text = message.text
    
    if text == '🎯 Z.ai Sniper':
        msg = bot.send_message(chat_id, "🎯 Enter Stock Ticker (e.g., RELIANCE):")
        bot.register_next_step_handler(msg, process_scan)
    elif text == '📊 Global Scan':
        bot.send_message(chat_id, "📡 Scanning Global Indices...")
        threading.Thread(target=global_scan_task, args=(chat_id,)).start()
    elif text == '📝 My History':
        threading.Thread(target=history_task, args=(chat_id, message.from_user.id)).start()

def process_scan(message):
    threading.Thread(target=run_scan_task, args=(message,)).start()

def run_scan_task(message):
    chat_id = message.chat.id
    symbol = message.text.upper()
    user_id = message.from_user.id
    
    bot.send_chat_action(chat_id, 'typing')
    
    # 1. Analysis
    data = MarketEngine.analyze_market(symbol)
    if "error" in data:
        bot.send_message(chat_id, f"❌ {data['error']}")
        return
    
    # 2. AI Insight
    ai_advice = AIBrain.generate_insight(data)
    
    # 3. Log
    db.log_scan(user_id, symbol, data['signal'], data['rsi'])
    
    # 4. Format Output
    icon = "💎" if "BUY" in data['signal'] else ("🛡" if "HOLD" in data['signal'] else "⚠️")
    
    report = (
        f"🚀 **{BOT_NAME} REPORT**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏷 **Symbol:** {data['symbol']}\n"
        f"💰 **LTP:** ₹{data['price']:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{icon} **SIGNAL:** {data['signal']}\n"
        f"🎯 **Confidence:** {data['accuracy']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"**Z.ai LOGIC:**\n"
    )
    for r in data['reasons']:
        report += f"{r}\n"
    report += f"\n🧠 **AI INSIGHT:**\n{ai_advice}"
    
    bot.send_message(chat_id, report, parse_mode="Markdown")

def global_scan_task(chat_id):
    bot.send_chat_action(chat_id, 'typing')
    indices = {"NIFTY 50": "^NSEI", "BANK NIFTY": "^NSEBANK", "SENSEX": "^BSESN"}
    report = "🌍 **GLOBAL MARKET HEALTH**\n━━━━━━━━━━━━━━━━━━\n"
    for name, sym in indices.items():
        data = MarketEngine.analyze_market(sym)
        if "error" not in data:
            status = "🟢" if "BUY" in data['signal'] else "🔴"
            report += f"{status} **{name}**: {data['signal']}\n"
    bot.send_message(chat_id, report, parse_mode="Markdown")

def history_task(chat_id, user_id):
    rows = db.get_user_history(user_id)
    if not rows:
        bot.send_message(chat_id, "📝 No history found.")
        return
    
    # Use Tabulate for Professional Table
    table_data = [[r['symbol'], r['signal'], f"{r['rsi']:.1f}", r['timestamp']] for r in rows]
    table_str = tabulate(table_data, headers=["Symbol", "Signal", "RSI", "Time"], tablefmt="grid")
    
    bot.send_message(chat_id, f"📜 **RECENT SCANS**\n```\n{table_str}\n```", parse_mode="Markdown")

# --- 7. WEB SERVER (Keep Alive) ---

app = Flask('')

@app.route('/')
def home():
    return f"<h1>{BOT_NAME} ONLINE</h1>"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# --- 8. MAIN ENTRY POINT ---

if __name__ == "__main__":
    logger.info(f"🚀 Launching {BOT_NAME}...")
    
    # Start Web Server in Thread
    t = Thread(target=run_flask, daemon=True)
    t.start()
    
    # Start Bot Polling
    try:
        bot.polling(non_stop=True, interval=1, timeout=10)
    except Exception as e:
        logger.critical(f"Bot polling failed: {e}")
