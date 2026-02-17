#!/usr/bin/env python3
# main.py - Main bot file with self-healing capabilities

import os
import sys
import time
import json
import logging
import threading
import sqlite3
import signal
import atexit
from datetime import datetime, date
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any

import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, jsonify, request

# Import local modules
from config import *
from database import Database
from ai_fallback import AIProvider
from health_monitor import HealthMonitor
from scheduler import Scheduler
import market_breadth
import news_fetcher
import portfolio
import swing_trades

# -------------------- CONFIGURATION --------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
PORT = int(os.getenv("PORT", 8080))
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")
ENVIRONMENT = os.getenv("ENVIRONMENT", "production")

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO if ENVIRONMENT == "production" else logging.DEBUG
)
logger = logging.getLogger(__name__)

# Validate required keys
if not TELEGRAM_TOKEN:
    logger.critical("TELEGRAM_TOKEN not set. Exiting.")
    sys.exit(1)

# Initialize components
bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML", num_threads=4)
db = Database("bot_data.db")
ai = AIProvider(groq_key=GROQ_API_KEY, gemini_key=GEMINI_API_KEY, deepseek_key=DEEPSEEK_API_KEY)
health = HealthMonitor()
scheduler = Scheduler()

# -------------------- SELF-HEALING CLASS --------------------
class BotSelfHealer:
    def __init__(self):
        self.last_heartbeat = datetime.now()
        self.error_count = 0
        self.max_errors = 5
        self.restart_count = 0
        self.components = {
            "telegram": True,
            "database": True,
            "ai": True,
            "market": True
        }
        
    def heartbeat(self):
        self.last_heartbeat = datetime.now()
        self.error_count = max(0, self.error_count - 1)
        
    def record_error(self, component: str):
        self.error_count += 1
        self.components[component] = False
        logger.error(f"Error in {component}. Count: {self.error_count}")
        
        if self.error_count >= self.max_errors:
            self.emergency_restart()
            
    def emergency_restart(self):
        logger.critical("Emergency restart triggered!")
        self.restart_count += 1
        
        if ADMIN_CHAT_ID:
            try:
                bot.send_message(
                    ADMIN_CHAT_ID,
                    f"🚨 *Emergency Restart*\nRestart #{self.restart_count}\nTime: {datetime.now()}"
                )
            except:
                pass
        
        self.shutdown()
        os.execv(sys.executable, ['python'] + sys.argv)
        
    def shutdown(self):
        logger.info("Graceful shutdown initiated")
        db.close()
        sys.exit(0)

healer = BotSelfHealer()

# -------------------- SIGNAL HANDLERS --------------------
def signal_handler(sig, frame):
    logger.info(f"Received signal {sig}")
    healer.shutdown()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# -------------------- USAGE TRACKING --------------------
def can_use_llm(user_id: int) -> Tuple[bool, int, int]:
    try:
        record = db.get_usage(user_id)
        today = date.today().isoformat()
        
        if record is None:
            tier = "free"
            limit = TIER_LIMITS[tier]
            db.update_usage(user_id, today, 0, tier)
            return True, limit, limit
            
        if record["date"] != today:
            limit = TIER_LIMITS[record["tier"]]
            db.update_usage(user_id, today, 0, record["tier"])
            return True, limit, limit
            
        limit = TIER_LIMITS[record["tier"]]
        remaining = limit - record["calls"]
        return remaining > 0, remaining, limit
        
    except Exception as e:
        logger.error(f"Usage check error: {e}")
        return True, 999, 999

def register_llm_usage(user_id: int) -> bool:
    try:
        record = db.get_usage(user_id)
        today = date.today().isoformat()
        
        if record is None:
            db.update_usage(user_id, today, 1, "free")
        elif record["date"] == today:
            db.update_usage(user_id, today, record["calls"] + 1, record["tier"])
        else:
            db.update_usage(user_id, today, 1, record["tier"])
        return True
        
    except Exception as e:
        logger.error(f"Usage registration error: {e}")
        return False

# -------------------- SAFE AI CALL --------------------
def safe_ai_call(prompt: str, max_tokens: int = 600) -> Tuple[bool, str]:
    try:
        response = ai.call(prompt, max_tokens)
        if response:
            return True, response
    except Exception as e:
        logger.error(f"AI call failed: {e}")
        healer.record_error("ai")
    return False, ""

def call_llm_with_limits(user_id: int, prompt: str, item_type: str = "analysis") -> str:
    allowed, remaining, limit = can_use_llm(user_id)
    
    if not allowed:
        return f"❌ You've used all {limit} AI analyses for today.\n\nPlease try again tomorrow."
    
    success, response = safe_ai_call(prompt)
    
    if not success:
        return f"⚠️ AI service unavailable. Your quota was not used.\n\nYou still have {remaining} calls left."
    
    if register_llm_usage(user_id):
        try:
            db.add_history(user_id, int(time.time()), prompt[:100], response, item_type)
        except:
            pass
    
    if remaining - 1 <= 3:
        response += f"\n\n⚠️ You have {remaining-1} AI calls left today."
    
    return response

# -------------------- STOCK ANALYSIS --------------------
def stock_ai_advisory(symbol: str) -> str:
    import yfinance as yf
    import pandas as pd
    
    sym = symbol.upper().strip()
    try:
        logger.info(f"Analyzing {sym}...")
        
        ticker = yf.Ticker(f"{sym}.NS")
        df = ticker.history(period="1y", interval="1d")
        
        if df.empty:
            return f"❌ No data found for {sym}."
        
        close = df['Close']
        if len(close) < 60:
            return f"❌ Insufficient history for {sym}."
        
        ltp = float(close.iloc[-1])
        prev = float(df['Close'].iloc[-2]) if len(df) > 1 else ltp
        
        # Get fundamental data
        info = ticker.info
        fund = {
            'sector': info.get('sector', 'N/A'),
            'industry': info.get('industry', 'N/A'),
            'company_name': info.get('longName', sym),
            'market_cap': info.get('marketCap', 0),
            'pe_ratio': info.get('trailingPE', 0),
            'pb_ratio': info.get('priceToBook', 0),
            'roe': info.get('returnOnEquity', 0) * 100 if info.get('returnOnEquity') else 0,
            'dividend_yield': info.get('dividendYield', 0) * 100 if info.get('dividendYield') else 0,
            'high_52w': info.get('fiftyTwoWeekHigh', 0),
            'low_52w': info.get('fiftyTwoWeekLow', 0)
        }
        
        # Technical indicators
        def ema(s, span):
            return s.ewm(span=span, adjust=False).mean()
        
        def rsi(s, period=14):
            d = s.diff()
            up = d.clip(lower=0).rolling(period).mean()
            down = (-d.clip(upper=0)).rolling(period).mean()
            rs = up / down
            return 100 - (100 / (1 + rs))
        
        ema20 = ema(close, 20).iloc[-1]
        ema50 = ema(close, 50).iloc[-1]
        ema200 = ema(close, 200).iloc[-1]
        rsi_val = rsi(close, 14).iloc[-1]
        trend = "Bullish" if ltp > ema200 else "Bearish"
        
        # Quality score (simplified)
        quality = 50
        if ltp > ema200:
            quality += 15
        if 40 <= rsi_val <= 60:
            quality += 10
        if fund.get('pe_ratio', 0) and fund['pe_ratio'] < 25:
            quality += 15
        if fund.get('roe', 0) > 15:
            quality += 10
        quality = min(quality, 100)
        
        stars = "⭐" * (quality // 20) + "☆" * (5 - (quality // 20))
        
        # Build response
        output = f"""📊 DEEP ANALYSIS: {sym}
━━━━━━━━━━━━━━━━━━━━
🏢 {fund['company_name']}
🏭 Sector: {fund['sector']}
💰 LTP: ₹{ltp:.2f} (Prev: ₹{prev:.2f})
📈 52W Range: ₹{fund['low_52w']:.2f} - ₹{fund['high_52w']:.2f}

━━━━━━━━━━━━━━━━━━━━
📊 FUNDAMENTALS
🏦 MCap: ₹{fund['market_cap']/10000000:.1f} Cr
📈 P/E: {fund['pe_ratio']:.2f} | P/B: {fund['pb_ratio']:.2f}
📊 ROE: {fund['roe']:.1f}% | Div Yield: {fund['dividend_yield']:.2f}%

━━━━━━━━━━━━━━━━━━━━
📌 TECHNICALS
RSI(14): {rsi_val:.1f}
EMA20: {ema20:.2f} | EMA50: {ema50:.2f} | EMA200: {ema200:.2f}
Trend: {trend}

━━━━━━━━━━━━━━━━━━━━
📊 QUALITY SCORE: {quality}/100 {stars}

━━━━━━━━━━━━━━━━━━━━
⚠️ Educational purpose only."""
        
        healer.heartbeat()
        return output
        
    except Exception as e:
        logger.exception(f"Error analyzing {sym}")
        return f"❌ Unable to analyze {sym}. Please try again."

# -------------------- TELEGRAM HANDLERS --------------------
@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🔍 Stock Analysis"), KeyboardButton("📊 Market Breadth"))
    kb.add(KeyboardButton("💼 Conservative"), KeyboardButton("💼 Moderate"), KeyboardButton("💼 Aggressive"))
    kb.add(KeyboardButton("📈 Swing (Conservative)"), KeyboardButton("📈 Swing (Aggressive)"))
    kb.add(KeyboardButton("📰 Market News"), KeyboardButton("📋 History"), KeyboardButton("📊 Usage"))
    
    bot.send_message(
        m.chat.id,
        "🤖 *AI Stock Advisor Pro*\n\n"
        "• Stock Analysis: Technical + Fundamental\n"
        "• Market Breadth: Nifty A/D ratio, sectors\n"
        "• Portfolio: 3 risk profiles\n"
        "• Swing Trades: 8/8 strict criteria\n"
        "• Market News: Latest headlines\n"
        "• History: Reuse previous queries\n\n"
        "Select an option below:",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda m: m.text == "🔍 Stock Analysis")
def ask_symbol(m):
    msg = bot.reply_to(m, "📝 Send NSE symbol (e.g. RELIANCE, TCS):")
    bot.register_next_step_handler(msg, process_symbol)

def process_symbol(m):
    sym = m.text.strip().upper()
    if not sym.isalnum():
        bot.reply_to(m, "❌ Invalid symbol. Use letters only.")
        return
    
    bot.send_chat_action(m.chat.id, 'typing')
    health.record_request()
    
    analysis = stock_ai_advisory(sym)
    
    if "❌" not in analysis:
        allowed, remaining, limit = can_use_llm(m.from_user.id)
        if allowed:
            register_llm_usage(m.from_user.id)
            db.add_history(m.from_user.id, int(time.time()), f"Stock: {sym}", analysis, "stock")
    
    bot.reply_to(m, analysis)

@bot.message_handler(func=lambda m: m.text == "📊 Market Breadth")
def market_breadth_cmd(m):
    bot.send_chat_action(m.chat.id, 'typing')
    try:
        text = market_breadth.format_market_breadth()
    except Exception as e:
        logger.error(f"Market breadth error: {e}")
        text = "📊 Market data temporarily unavailable."
    bot.reply_to(m, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text in ["💼 Conservative", "💼 Moderate", "💼 Aggressive"])
def portfolio_cmd(m):
    risk = m.text.split()[1].lower()
    bot.send_chat_action(m.chat.id, 'typing')
    try:
        text = portfolio.get_portfolio_suggestion(risk)
    except Exception as e:
        logger.error(f"Portfolio error: {e}")
        text = "💼 Portfolio suggestions temporarily unavailable."
    bot.reply_to(m, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text in ["📈 Swing (Conservative)", "📈 Swing (Aggressive)"])
def swing_cmd(m):
    risk = "conservative" if "Conservative" in m.text else "aggressive"
    bot.send_chat_action(m.chat.id, 'typing')
    try:
        text = swing_trades.get_swing_trades(risk_tolerance=risk)
    except Exception as e:
        logger.error(f"Swing trade error: {e}")
        text = "📈 Swing trade analysis temporarily unavailable."
    bot.reply_to(m, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📰 Market News")
def news_cmd(m):
    bot.send_chat_action(m.chat.id, 'typing')
    try:
        text = news_fetcher.get_market_news()
    except Exception as e:
        logger.error(f"News error: {e}")
        text = "📰 News temporarily unavailable."
    bot.reply_to(m, text, parse_mode="HTML", disable_web_page_preview=True)

@bot.message_handler(commands=["usage"])
@bot.message_handler(func=lambda m: m.text == "📊 Usage")
def usage_cmd(m):
    user_id = m.from_user.id
    allowed, remaining, limit = can_use_llm(user_id)
    used = limit - remaining if allowed else limit
    
    text = f"📊 *Usage*\n\nUsed: {used}/{limit}\nRemaining: {remaining}"
    bot.reply_to(m, text, parse_mode="Markdown")

@bot.message_handler(commands=["history"])
@bot.message_handler(func=lambda m: m.text == "📋 History")
def show_history(m):
    user_id = m.from_user.id
    try:
        items = db.get_recent_history(user_id, limit=5)
    except:
        bot.reply_to(m, "History unavailable.")
        return
    
    if not items:
        bot.reply_to(m, "No history.")
        return
    
    markup = InlineKeyboardMarkup()
    for item in items:
        preview = item["prompt"][:30] + ("…" if len(item["prompt"]) > 30 else "")
        button = InlineKeyboardButton(text=preview, callback_data=f"hist_{item['id']}")
        markup.add(button)
    
    bot.send_message(m.chat.id, "Recent queries:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("hist_"))
def history_callback(call):
    user_id = call.from_user.id
    item_id = int(call.data.split("_")[1])
    item = db.get_history_item(user_id, item_id)
    
    if not item:
        bot.answer_callback_query(call.id, "Item not found.")
        return
    
    bot.send_message(user_id, f"📎 {item['response']}")
    bot.answer_callback_query(call.id)

# -------------------- FLASK HEALTH SERVER --------------------
app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "healthy",
        "time": datetime.now().isoformat(),
        "uptime": health.get_uptime()
    })

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "requests_today": health.get_requests_today(),
        "errors_today": health.get_errors_today(),
        "memory_usage": health.get_memory_usage()
    })

def run_flask():
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# -------------------- MAIN --------------------
if __name__ == "__main__":
    logger.info("Starting AI Stock Advisor Pro")
    
    bot.remove_webhook()
    time.sleep(1)
    
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask server on port {PORT}")
    
    while True:
        try:
            bot.infinity_polling()
        except Exception as e:
            logger.error(f"Polling error: {e}")
            healer.record_error("telegram")
            time.sleep(5)