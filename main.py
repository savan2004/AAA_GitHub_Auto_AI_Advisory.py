import os
import json
import time
import telebot
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
from openai import OpenAI

# --- Configuration & Environment Setup ---
def load_config():
    if os.path.exists('config.json'):
        with open('config.json') as f:
            local_cfg = json.load(f)    else:
        local_cfg = {}

    return {
        "BOT_TOKEN": os.getenv('TELEGRAM_BOT_TOKEN', local_cfg.get('TELEGRAM_BOT_TOKEN')),
        "OPENAI_KEY": os.getenv('OPENAI_API_KEY', local_cfg.get('OPENAI_API_KEY')),
        "GROQ_KEY": os.getenv('GROQ_API_KEY', local_cfg.get('GROQ_API_KEY')),
        "NEWS_KEY": os.getenv('NEWS_API_KEY', local_cfg.get('NEWS_API_KEY')),
        "WATCHLIST": json.loads(os.getenv('WATCHLIST_JSON', json.dumps(local_cfg.get('WATCHLIST_JSON', {}))))
    }

cfg = load_config()
bot = telebot.TeleBot(cfg['BOT_TOKEN'])
oa_client = OpenAI(api_key=cfg['OPENAI_KEY'])
groq_client = OpenAI(api_key=cfg['GROQ_KEY'], base_url="https://api.groq.com/openai/v1")

# --- AI Insight Logic with Auto-Healing Fallback ---
def get_ai_insight(prompt):
    try:
        # Primary: OpenAI
        response = oa_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": "You are a pro stock market analyst."}, {"role": "user", "content": prompt}],
            timeout=10
        )
        return response.choices[0].message.content    except Exception as e:
        print(f"OpenAI Failed: {e}. Falling back to Groq...")
        try:
            # Fallback: Groq (Llama 3)
            response = groq_client.chat.completions.create(
                model="llama-3.1-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                timeout=10
            )
            return response.choices[0].message.content
        except Exception as ge:
            return f"⚠️ AI Analysis unavailable. (Error: {str(ge)})"

# --- Data Fetching with Retries ---
def fetch_stock_data(symbol):
    for _ in range(3):
        try:
            t = yf.Ticker(symbol)
            h = t.history(period="1y")
            if not h.empty: return t, h
        except: time.sleep(1)
    return None, None# --- Bot Command Handlers ---

@bot.message_handler(commands=['start'])
def welcome(message):
    help_text = (
        "🚀 *AI Stock Analysis Bot*\n\n"
        "🔍 /search `SYMBOL` - Full Report\n"
        "📡 /scan - Market Health\n"
        "🎯 /options `BUDGET` `SPOT` - AI Strategy\n"
        "💼 /portfolio - Track Watchlist\n"
        "📰 /news `SYMBOL` - Sentiment\n"
        "📈 /tech `SYMBOL` - Indicators\n"
        "🎭 /sentiment - Market Mood\n"
        "🏗️ /builder - AI Portfolio"
    )
    bot.reply_to(message, help_text, parse_mode='Markdown')

@bot.message_handler(commands=['search'])
def cmd_search(message):
    try:
        symbol = message.text.split()[1].upper()
        ticker, hist = fetch_stock_data(symbol)
        if ticker is None: return bot.reply_to(message, "❌ Symbol not found.")
        
        info = ticker.info
        close = hist['Close']
        rsi = ta.rsi(close).iloc[-1]
        ema50 = ta.ema(close, 50).iloc[-1]
        
        insight = get_ai_insight(f"Analyze {symbol}: Price {info.get('currentPrice')}, RSI {rsi:.2f}, PE {info.get('trailingPE')}.")
        
        res = (
            f"📊 *{symbol}* | LTP: ₹{info.get('currentPrice')}\n"
            f"• RSI: {rsi:.2f} | EMA50: {ema50:.2f}\n"
            f"• 52W H/L: {info.get('fiftyTwoWeekHigh')} / {info.get('fiftyTwoWeekLow')}\n"
            f"• Fundamentals: PE: {info.get('trailingPE')} | ROE: {info.get('returnOnEquity', 0)*100:.1f}%\n\n"
            f"🤖 *AI Insight:* {insight}"
        )
        bot.reply_to(message, res, parse_mode='Markdown')
    except: bot.reply_to(message, "Usage: /search RELIANCE.NS")

@bot.message_handler(commands=['scan'])
def cmd_scan(message):
    bot.send_chat_action(message.chat.id, 'typing')
    report = "📡 *Market Scan (Watchlist)*\n\n"
    for cat, stocks in cfg['WATCHLIST'].items():
        bullish = 0
        for s in stocks:
            _, h = fetch_stock_data(s)
            if h is not None and h['Close'].iloc[-1] > ta.sma(h['Close'], 20).iloc[-1]: bullish += 1
        report += f"• *{cat}*: {bullish}/{len(stocks)} Bullish\n"
    bot.reply_to(message, report, parse_mode='Markdown')

@bot.message_handler(commands=['options'])
def cmd_options(message):
    try:
        _, budget, spot = message.text.split()
        prompt = f"Provide a Nifty option strategy for budget {budget} at spot {spot}. Include Greeks and Risk-Reward."
        bot.reply_to(message, f"🎯 *Option Strategy*\n\n{get_ai_insight(prompt)}", parse_mode='Markdown')
    except: bot.reply_to(message, "Usage: /options 50000 24500")

@bot.message_handler(commands=['portfolio'])
def cmd_portfolio(message):
    report = "💼 *Watchlist Performance*\n\n"
    for s in cfg['WATCHLIST']['LARGE_CAP'][:3]:
        t, _ = fetch_stock_data(s)
        report += f"• {s}: ₹{t.info.get('currentPrice')} ({t.info.get('regularMarketChangePercent', 0):.2f}%)\n"
    bot.reply_to(message, report, parse_mode='Markdown')

@bot.message_handler(commands=['news'])
def cmd_news(message):
    try:
        symbol = message.text.split()[1].upper()
        url = f"https://newsapi.org/v2/everything?q={symbol}&apiKey={cfg['NEWS_KEY']}&pageSize=3"
        articles = requests.get(url).json().get('articles', [])
        titles = "\n".join([f"🔹 {a['title']}" for a in articles])
        insight = get_ai_insight(f"Sentiment analysis for {symbol} news: {titles}")
        bot.reply_to(message, f"📰 *{symbol} News*\n\n{titles}\n\n🤖 *Sentiment:* {insight}", parse_mode='Markdown')
    except: bot.reply_to(message, "Usage: /news RELIANCE.NS")

@bot.message_handler(commands=['tech'])
def cmd_tech(message):
    try:
        symbol = message.text.split()[1].upper()
        _, h = fetch_stock_data(symbol)
        c = h['Close']
        macd = ta.macd(c)
        bb = ta.bbands(c)
        res = (
            f"📈 *{symbol} Technicals*\n"
            f"• RSI: {ta.rsi(c).iloc[-1]:.2f}\n"            f"• MACD: {macd['MACD_12_26_9'].iloc[-1]:.2f}\n"
            f"• BB Upper: {bb['BBU_5_2.0'].iloc[-1]:.2f}\n"
            f"• BB Lower: {bb['BBL_5_2.0'].iloc[-1]:.2f}"
        )
        bot.reply_to(message, res, parse_mode='Markdown')
    except: bot.reply_to(message, "❌ Technical data error.")

@bot.message_handler(commands=['sentiment', 'builder'])
def cmd_ai_tools(message):
    prompt = "Current Indian market mood and Nifty levels" if "sentiment" in message.text else "Build 10-stock fundamental portfolio for 3 years"
    bot.reply_to(message, f"🤖 *AI Analysis*\n\n{get_ai_insight(prompt)}", parse_mode='Markdown')# --- Main Execution with Auto-Healing Polling ---
if __name__ == "__main__":
    while True:
        try:
            print("Bot is running...")
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except Exception as e:
            print(f"Restarting bot due to error: {e}")
            time.sleep(5)
