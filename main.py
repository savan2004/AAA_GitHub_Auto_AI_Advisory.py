import os
import telebot
import google.generativeai as genai
import yfinance as yf
import requests
import pandas as pd
from flask import Flask
from threading import Thread
import time

# --- 1. CONFIGURATION (Read from Render Environment) ---
# We use os.environ.get() so you don't expose keys in your code
API_KEY = os.environ.get("GEMINI_API_KEY")
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
NEWS_KEY = os.environ.get("NEWS_API_KEY")

# --- 2. KEEP-ALIVE SERVER (For Render) ---
app = Flask('')

@app.route('/')
def home():
    return "I am alive! The Sovereign Bot is running."

def run_http():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# --- 3. BOT LOGIC ---
bot = telebot.TeleBot(BOT_TOKEN)
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

def get_data(symbol):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1mo")
        if df.empty: return None
        price = df['Close'].iloc[-1]
        
        # Simple RSI Calc
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs)).iloc[-1]
        return price, rsi
    except:
        return None, None

def get_news(query):
    try:
        url = f"https://newsapi.org/v2/everything?q={query}&apiKey={NEWS_KEY}&pageSize=3"
        data = requests.get(url).json()
        return "\n".join([f"- {a['title']}" for a in data.get('articles', [])])
    except:
        return "No news available."

@bot.message_handler(commands=['start'])
def start(m):
    bot.reply_to(m, "🏛 **Sovereign Bot Online**\nSend a ticker like 'RELIANCE' or 'NIFTY'.")

@bot.message_handler(func=lambda m: True)
def analyze(m):
    symbol_map = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
    user_text = m.text.upper()
    ticker = symbol_map.get(user_text, user_text + ".NS") # Default to NSE stock
    
    bot.send_message(m.chat.id, f"🔍 Analyzing {ticker}...")
    
    price, rsi = get_data(ticker)
    if not price:
        bot.reply_to(m, "❌ Could not find data. Try 'RELIANCE' or 'TCS'.")
        return

    news = get_news(user_text)
    
    prompt = f"Analyze {user_text} at Price: {price}, RSI: {rsi}. News: {news}. Give Buy/Sell advice."
    response = model.generate_content(prompt)
    
    bot.reply_to(m, f"📊 **Analysis for {user_text}**\nPrice: {price:.2f}\nRSI: {rsi:.2f}\n\n{response.text}")

# --- 4. START EVERYTHING ---
if __name__ == "__main__":
    keep_alive()  # Start the fake website
    bot.polling(non_stop=True) # Start the bot
