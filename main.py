import os
import telebot
import yfinance as yf
import google.generativeai as genai
import pandas as pd
import requests
from flask import Flask
from threading import Thread
from telebot import types

# --- 1. CONFIGURATION (Keys & Settings) ---
# We use os.environ to read from Render, but added your keys as defaults so it works instantly.
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyCPh8wPC-rmBIyTr5FfV3Mwjb33KeZdRUE")
NEWS_KEY = os.environ.get("NEWS_API_KEY", "47fb3f33527944ed982e6e48cc856b23")

# Bot Configuration
bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_KEY)
ai_model = genai.GenerativeModel('gemini-1.5-flash')

# --- 2. FLASK KEEP-ALIVE SERVER (For Render) ---
# This creates a tiny website so Render knows your bot is "alive"
app = Flask('')

@app.route('/')
def home():
    return "✅ Sovereign AI Bot is Running 24/7!"

def run_http():
    # Runs on port 8080 or whatever Render assigns
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# --- 3. DATA ENGINE (Yahoo Finance) ---
def get_market_data(symbol_ticker):
    """Fetches Price, RSI, and SMA"""
    try:
        ticker = yf.Ticker(symbol_ticker)
        df = ticker.history(period="1mo")
        
        if df.empty: return None

        # Live Price
        price = df['Close'].iloc[-1]
        
        # Calculate RSI (14)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs)).iloc[-1]

        # Calculate SMA (20)
        sma = df['Close'].rolling(window=20).mean().iloc[-1]

        return {
            "price": round(price, 2),
            "rsi": round(rsi, 2) if not pd.isna(rsi) else 50.0,
            "sma": round(sma, 2) if not pd.isna(sma) else price
        }
    except Exception as e:
        print(f"Data Error: {e}")
        return None

def get_news(query):
    """Fetches top 3 news headlines"""
    url = f"https://newsapi.org/v2/everything?q={query}&sortBy=publishedAt&apiKey={NEWS_KEY}&language=en&pageSize=3"
    try:
        data = requests.get(url).json()
        articles = data.get("articles", [])
        return "\n".join([f"• {a['title']}" for a in articles])
    except:
        return "No recent news available."

# --- 4. AI ANALYSIS CORE ---
def generate_ai_signal(symbol_name, ticker):
    # 1. Fetch Data
    data = get_market_data(ticker)
    if not data: 
        return "⚠️ Error: Market data unavailable. (Market might be closed)"

    # 2. Fetch News
    news = get_news(symbol_name)

    # 3. Construct AI Prompt
    prompt = f"""
    Act as a Senior Market Analyst AI.
    Analyze {symbol_name} based on this real-time data:
    - Price: {data['price']}
    - RSI (14): {data['rsi']} (Over 70=Overbought, Under 30=Oversold)
    - SMA (20): {data['sma']}
    - News Context: {news}

    OUTPUT FORMAT:
    1. Trend Verdict: [BULLISH / BEARISH / NEUTRAL]
    2. Action: [BUY / SELL / WAIT]
    3. Reasoning: One concise paragraph explaining why, using the RSI and News.
    """

    try:
        response = ai_model.generate_content(prompt)
        return (f"🏛 **SOVEREIGN AI REPORT: {symbol_name}**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💰 **Price:** ₹{data['price']}\n"
                f"📉 **RSI:** {data['rsi']}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"{response.text}")
    except Exception as e:
        return f"⚠️ AI Error: {e}"

# --- 5. TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start'])
def start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn1 = types.KeyboardButton('🚀 NIFTY 50')
    btn2 = types.KeyboardButton('📈 BANK NIFTY')
    btn3 = types.KeyboardButton('⛽ RELIANCE')
    markup.add(btn1, btn2, btn3)
    
    bot.send_message(
        message.chat.id, 
        "🏛 **Sovereign AI Online**\nConnected to Cloud.\nSelect an asset to analyze:", 
        reply_markup=markup
    )

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    # Map button text to Ticker Symbols
    mapping = {
        '🚀 NIFTY 50': ('NIFTY 50', '^NSEI'),
        '📈 BANK NIFTY': ('BANK NIFTY', '^NSEBANK'),
        '⛽ RELIANCE': ('RELIANCE', 'RELIANCE.NS')
    }
    
    if message.text in mapping:
        name, ticker = mapping[message.text]
        bot.send_message(message.chat.id, f"🔍 **Analyzing {name}...**\n_Reading market data & news..._")
        
        report = generate_ai_signal(name, ticker)
        bot.send_message(message.chat.id, report, parse_mode="Markdown")
    else:
        bot.reply_to(message, "Please use the buttons provided.")

# --- 6. STARTUP ---
if __name__ == "__main__":
    print("✅ System Starting...")
    keep_alive()  # Starts the web server
    bot.polling(non_stop=True) # Starts the bot
