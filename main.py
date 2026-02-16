import os
import time
import json
import logging
import threading
from datetime import datetime, timedelta
from collections import defaultdict
from functools import lru_cache

import pandas as pd
import yfinance as yf
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from groq import Groq
import google.generativeai as genai
import requests
from cachetools import TTLCache
from flask import Flask, request

# -------------------- CONFIGURATION --------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # e.g. https://your-app.onrender.com
PORT = int(os.getenv("PORT", 8443))
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Validate token
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set")

# Initialize bot
bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")

# Configure AI clients
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Cache setup (in-memory TTL cache, 5 minutes)
cache = TTLCache(maxsize=1000, ttl=300)

# Rate limiter (per user, 10 requests/minute)
rate_limits = defaultdict(list)

# -------------------- HELPER FUNCTIONS --------------------
def check_rate_limit(user_id: int) -> bool:
    now = time.time()
    # Clean old entries
    rate_limits[user_id] = [t for t in rate_limits[user_id] if now - t < 60]
    if len(rate_limits[user_id]) >= 10:
        return False
    rate_limits[user_id].append(now)
    return True

def safe_request(url, params=None, headers=None, timeout=10):
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Request failed: {url} - {e}")
        return None

# -------------------- MARKET BREADTH --------------------
def get_market_breadth():
    indices = {
        "NIFTY 50": "^NSEI",
        "BANK NIFTY": "^NSEBANK",
        "NIFTY IT": "^CNXIT"
    }
    data = {}
    for name, symbol in indices.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1d")
            if not hist.empty:
                last = hist['Close'].iloc[-1]
                prev = hist['Close'].iloc[-2] if len(hist) > 1 else last
                change = ((last - prev) / prev) * 100
                data[name] = (last, change)
        except Exception as e:
            logger.error(f"Error fetching {name}: {e}")
    # Placeholder for advance/decline (you can replace with real data)
    ad = {"advances": 1250, "declines": 750, "unchanged": 100}
    return data, ad

def format_market_breadth():
    indices, ad = get_market_breadth()
    text = "📊 <b>Market Breadth (NSE)</b>\n\n"
    for name, (last, chg) in indices.items():
        arrow = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
        text += f"{arrow} {name}: {last:.2f} ({chg:+.2f}%)\n"
    text += f"\n📈 Advances: {ad['advances']}\n📉 Declines: {ad['declines']}\n⚖️ Unchanged: {ad['unchanged']}\n"
    text += f"\n🔄 A/D Ratio: {ad['advances']/ad['declines']:.2f}"
    return text

# -------------------- PORTFOLIO SUGGESTION (AI SCORE) --------------------
def calculate_stock_score(symbol: str) -> dict:
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.info
        hist = ticker.history(period="6mo")
        if hist.empty:
            return None

        close = hist['Close']
        latest_price = close.iloc[-1]
        ema20 = close.ewm(span=20).mean().iloc[-1]
        ema50 = close.ewm(span=50).mean().iloc[-1]
        ema200 = close.ewm(span=200).mean().iloc[-1]

        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean().iloc[-1]
        avg_loss = loss.rolling(14).mean().iloc[-1]
        rs = avg_gain / avg_loss if avg_loss != 0 else 100
        rsi = 100 - (100 / (1 + rs))

        # Technical score (max 10)
        tech_score = 0
        if latest_price > ema200:
            tech_score += 3
        if latest_price > ema50:
            tech_score += 2
        if latest_price > ema20:
            tech_score += 1
        if 40 <= rsi <= 60:
            tech_score += 2
        elif (30 <= rsi < 40) or (60 < rsi <= 70):
            tech_score += 1

        # Fundamental score (max 10)
        pe = info.get('trailingPE', 25)
        pb = info.get('priceToBook', 2)
        roe = info.get('returnOnEquity', 0.1) * 100
        debt_eq = info.get('debtToEquity', 0.5)

        fund_score = 0
        if pe and pe < 20:
            fund_score += 2
        elif pe and pe < 30:
            fund_score += 1
        if pb and pb < 3:
            fund_score += 2
        elif pb and pb < 5:
            fund_score += 1
        if roe and roe > 15:
            fund_score += 3
        elif roe and roe > 10:
            fund_score += 2
        if debt_eq and debt_eq < 1:
            fund_score += 2
        elif debt_eq and debt_eq < 2:
            fund_score += 1

        # Sentiment score (placeholder, you can integrate AI here)
        sentiment_score = 5

        # Total (weighted)
        total = (tech_score * 0.4) + (fund_score * 0.4) + (sentiment_score * 0.2)
        rating = "Strong Buy" if total >= 8 else "Buy" if total >= 6 else "Hold" if total >= 4 else "Avoid"

        return {
            "score": round(total, 1),
            "rating": rating,
            "tech": tech_score,
            "fund": fund_score,
            "sentiment": sentiment_score,
            "reason": f"Technical: {tech_score}/10, Fundamental: {fund_score}/10, Sentiment: {sentiment_score}/10"
        }
    except Exception as e:
        logger.error(f"Score calculation error for {symbol}: {e}")
        return None

def suggest_portfolio():
    candidates = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "ITC", "SBIN", "BHARTIARTL"]
    scored = []
    for sym in candidates:
        score_data = calculate_stock_score(sym)
        if score_data and score_data["score"] >= 6:
            scored.append((sym, score_data))
    scored.sort(key=lambda x: x[1]["score"], reverse=True)
    total_score = sum(s[1]["score"] for s in scored)
    portfolio = []
    for sym, data in scored:
        weight = (data["score"] / total_score) * 100 if total_score else 0
        portfolio.append({
            "symbol": sym,
            "score": data["score"],
            "rating": data["rating"],
            "allocation": round(weight, 1),
            "reason": data["reason"]
        })
    return portfolio

def format_portfolio_suggestion(portfolio):
    text = "💼 <b>AI-Powered Portfolio Suggestion</b>\n"
    text += "Based on technical, fundamental & sentiment analysis (score out of 10):\n\n"
    for item in portfolio:
        text += f"• {item['symbol']} – <b>{item['score']}/10</b> ({item['rating']})\n"
        text += f"  Allocation: {item['allocation']}%\n"
        text += f"  <i>{item['reason']}</i>\n\n"
    text += "⚠️ Educational purpose only. Consult your advisor."
    return text

# -------------------- AI ADVISORY (with caching) --------------------
def get_ai_analysis(symbol: str) -> str:
    cache_key = f"ai:{symbol}"
    if cache_key in cache:
        logger.info(f"Cache hit for {symbol}")
        return cache[cache_key]
    result = stock_ai_advisory(symbol)  # your existing function
    cache[cache_key] = result
    return result

def stock_ai_advisory(symbol: str) -> str:
    # This is a placeholder – replace with your actual detailed analysis function
    # For brevity, I'm returning a simplified message. You can copy your full function here.
    return f"📊 Analysis for {symbol} will appear here. (Your full analysis function goes here.)"

# -------------------- TELEGRAM HANDLERS --------------------
@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    if not check_rate_limit(m.from_user.id):
        bot.reply_to(m, "⏳ Rate limit exceeded. Please wait a minute.")
        return
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🔍 Stock Analysis"), KeyboardButton("📊 Market Breadth"))
    kb.add(KeyboardButton("💼 Portfolio Suggestion"), KeyboardButton("📰 Market News"))
    bot.send_message(
        m.chat.id,
        "🤖 <b>AI Stock Advisor Pro</b>\n\n"
        "• Stock Analysis: detailed tech+fundamental+AI\n"
        "• Market Breadth: Nifty indices, A/D ratio\n"
        "• Portfolio Suggestion: AI-scored allocation\n"
        "• Market News: latest headlines\n\n"
        "Select an option below:",
        reply_markup=kb
    )

@bot.message_handler(func=lambda m: m.text == "🔍 Stock Analysis")
def ask_symbol(m):
    if not check_rate_limit(m.from_user.id):
        bot.reply_to(m, "⏳ Rate limit exceeded. Please wait.")
        return
    msg = bot.reply_to(m, "📝 Send NSE symbol (e.g. RELIANCE, TCS):")
    bot.register_next_step_handler(msg, process_symbol)

def process_symbol(m):
    sym = m.text.strip().upper()
    if not sym.isalnum():
        bot.reply_to(m, "❌ Invalid symbol. Use letters only.")
        return
    bot.send_chat_action(m.chat.id, 'typing')
    try:
        analysis = get_ai_analysis(sym)
        if len(analysis) > 4096:
            for x in range(0, len(analysis), 4096):
                bot.send_message(m.chat.id, analysis[x:x+4096])
        else:
            bot.reply_to(m, analysis)
    except Exception as e:
        logger.exception("Error in process_symbol")
        bot.reply_to(m, "❌ Analysis failed. Please try again later.")

@bot.message_handler(func=lambda m: m.text == "📊 Market Breadth")
def market_breadth_cmd(m):
    if not check_rate_limit(m.from_user.id):
        bot.reply_to(m, "⏳ Rate limit exceeded.")
        return
    bot.send_chat_action(m.chat.id, 'typing')
    text = format_market_breadth()
    bot.reply_to(m, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "💼 Portfolio Suggestion")
def portfolio_cmd(m):
    if not check_rate_limit(m.from_user.id):
        bot.reply_to(m, "⏳ Rate limit exceeded.")
        return
    bot.send_chat_action(m.chat.id, 'typing')
    portfolio = suggest_portfolio()
    text = format_portfolio_suggestion(portfolio)
    bot.reply_to(m, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📰 Market News")
def news_cmd(m):
    if not check_rate_limit(m.from_user.id):
        bot.reply_to(m, "⏳ Rate limit exceeded.")
        return
    bot.send_chat_action(m.chat.id, 'typing')
    if not NEWS_API_KEY:
        bot.reply_to(m, "📰 News API not configured.")
        return
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": "Indian stock market OR NSE",
        "apiKey": NEWS_API_KEY,
        "pageSize": 5,
        "sortBy": "publishedAt",
        "language": "en"
    }
    data = safe_request(url, params=params)
    if not data or not data.get("articles"):
        bot.reply_to(m, "❌ Could not fetch news.")
        return
    articles = data["articles"][:5]
    text = "📰 <b>Latest Market News</b>\n\n"
    for i, art in enumerate(articles, 1):
        title = art["title"]
        source = art["source"]["name"]
        date = art["publishedAt"][:10]
        text += f"{i}. <a href='{art['url']}'>{title}</a>\n   📌 {source} | {date}\n\n"
    bot.reply_to(m, text, parse_mode="HTML", disable_web_page_preview=True)

# -------------------- FLASK WEBHOOK SERVER --------------------
app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return "Bot is running", 200

@app.route('/health', methods=['GET'])
def health():
    return {"status": "healthy", "time": datetime.now().isoformat()}, 200

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    return 'Bad request', 400

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# -------------------- WEBHOOK SETUP --------------------
def set_webhook():
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    logger.info(f"Webhook set to {WEBHOOK_URL}/webhook")

# -------------------- MAIN --------------------
if __name__ == "__main__":
    logger.info("Starting AI Stock Advisor Pro")

    if WEBHOOK_URL:
        # Webhook mode
        set_webhook()
        # Run Flask in a separate thread (or use gunicorn in production)
        threading.Thread(target=run_flask, daemon=True).start()
        # Keep main thread alive (optional heartbeat)
        while True:
            time.sleep(60)
            if ADMIN_CHAT_ID:
                try:
                    bot.send_message(ADMIN_CHAT_ID, "💓 Bot heartbeat OK")
                except Exception as e:
                    logger.error(f"Heartbeat failed: {e}")
    else:
        # Polling mode – ensure no webhook is active
        logger.info("No WEBHOOK_URL set, removing any existing webhook and starting polling...")
        bot.remove_webhook()
        time.sleep(1)
        bot.infinity_polling()
