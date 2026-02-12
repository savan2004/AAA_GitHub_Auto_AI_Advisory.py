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
            local_cfg = json.load(f)
    else:        local_cfg = {}

    watchlist_raw = os.getenv('WATCHLIST_JSON')
    if watchlist_raw:
        watchlist = json.loads(watchlist_raw)
    else:
        watchlist = local_cfg.get('WATCHLIST_JSON', {"LARGE_CAP": ["RELIANCE.NS"], "MID_CAP": [], "SMALL_CAP": []})

    return {
        "BOT_TOKEN": os.getenv('TELEGRAM_BOT_TOKEN', local_cfg.get('TELEGRAM_BOT_TOKEN')),
        "OPENAI_KEY": os.getenv('OPENAI_API_KEY', local_cfg.get('OPENAI_API_KEY')),
        "GROQ_KEY": os.getenv('GROQ_API_KEY', local_cfg.get('GROQ_API_KEY')),
        "NEWS_KEY": os.getenv('NEWS_API_KEY', local_cfg.get('NEWS_API_KEY')),
        "WATCHLIST": watchlist
    }

cfg = load_config()
bot = telebot.TeleBot(cfg['BOT_TOKEN'])
oa_client = OpenAI(api_key=cfg['OPENAI_KEY'])
groq_client = OpenAI(api_key=cfg['GROQ_KEY'], base_url="https://api.groq.com/openai/v1")

# --- AI Insight Logic ---
def get_ai_insight(prompt):
    try:
        response = oa_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": "You are a pro stock analyst."}, {"role": "user", "content": prompt}],
            timeout=10
        )
        return response.choices[0].message.content
    except Exception:
        try:
            response = groq_client.chat.completions.create(
                model="llama-3.1-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                timeout=10
            )
            return response.choices[0].message.content
        except Exception as ge:
            return f"⚠️ AI Analysis unavailable: {str(ge)}"

# --- Data Fetching ---
def fetch_stock_data(symbol):
    for _ in range(3):
        try:
            t = yf.Ticker(symbol)
            h = t.history(period="1y")
            if not h.empty:
                return t, h
        except:
            time.sleep(1)
    return None, None

# --- Bot Command Handlers ---

@bot.message_handler(commands=['start'])def welcome(message):
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
        parts = message.text.split()
        if len(parts) < 2:
            return bot.reply_to(message, "Usage: /search RELIANCE.NS")
        symbol = parts[1].upper()
        ticker, hist = fetch_stock_data(symbol)
        if ticker is None:
            return bot.reply_to(message, "❌ Symbol not found.")
        
        info = ticker.info
        close = hist['Close']
        rsi = ta.rsi(close).iloc[-1]
        ema50 = ta.ema(close, 50).iloc[-1]
        
        prompt = f"Analyze {symbol}: Price {info.get('currentPrice')}, RSI {rsi:.2f}, PE {info.get('trailingPE')}."
        insight = get_ai_insight(prompt)
        
        res = (
            f"📊 *{symbol}* | LTP: ₹{info.get('currentPrice')}\n"
            f"• RSI: {rsi:.2f} | EMA50: {ema50:.2f}\n"
            f"• 52W H/L: {info.get('fiftyTwoWeekHigh')} / {info.get('fiftyTwoWeekLow')}\n"
            f"• PE: {info.get('trailingPE')} | ROE: {info.get('returnOnEquity', 0)*100:.1f}%\n\n"
            f"🤖 *AI Insight:* {insight}"
        )
        bot.reply_to(message, res, parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['scan'])
def cmd_scan(message):
    bot.send_chat_action(message.chat.id, 'typing')
    report = "📡 *Market Scan (Watchlist)*\n\n"
    for cat, stocks in cfg['WATCHLIST'].items():
        bullish = 0
        for s in stocks:
            _, h = fetch_stock_data(s)
            if h is not None and h['Close'].iloc[-1] > ta.sma(h['Close'], 20).iloc[-1]:
                bullish += 1
        report += f"• *{cat}*: {bullish}/{len(stocks)} Bullish\n"
    bot.reply_to(message, report, parse_mode='Markdown')

@bot.message_handler(commands=['options'])
def cmd_options(message):
    try:
        parts = message.text.split()
        budget, spot = parts[1], parts[2]        prompt = f"Provide a Nifty option strategy for budget {budget} at spot {spot}. Include Greeks and Risk-Reward."
        bot.reply_to(message, f"🎯 *Option Strategy*\n\n{get_ai_insight(prompt)}", parse_mode='Markdown')
    except:
        bot.reply_to(message, "Usage: /options 50000 24500")

@bot.message_handler(commands=['portfolio'])
def cmd_portfolio(message):
    report = "💼 *Watchlist Performance*\n\n"
    # Scans first 5 items from LARGE_CAP
    for s in cfg['WATCHLIST'].get('LARGE_CAP', [])[:5]:
        t, _ = fetch_stock_data(s)
        if t:
            price = t.info.get('currentPrice', 'N/A')
            report += f"• {s}: ₹{price}\n"
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
    except:
        bot.reply_to(message, "Usage: /news RELIANCE.NS")@bot.message_handler(commands=['tech'])
def cmd_tech(message):
    try:        symbol = message.text.split()[1].upper()
        _, h = fetch_stock_data(symbol)
        c = h['Close']
        macd = ta.macd(c)
        bb = ta.bbands(c)
        res = (
            f"📈 *{symbol} Technicals*\n"
            f"• RSI: {ta.rsi(c).iloc[-1]:.2f}\n"
            f"• MACD: {macd['MACD_12_26_9'].iloc[-1]:.2f}\n"
            f"• BB Upper: {bb['BBU_5_2.0'].iloc[-1]:.2f}\n"
            f"• BB Lower: {bb['BBL_5_2.0'].iloc[-1]:.2f}"
        )        bot.reply_to(message, res, parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Technical data error.")

@bot.message_handler(commands=['sentiment', 'builder'])
def cmd_ai_tools(message):
    if "sentiment" in message.text:
        prompt = "Analyze current Indian market sentiment and Nifty outlook."
    else:
        prompt = "Suggest a 10-stock fundamental portfolio for 3 years."
    bot.reply_to(message, f"🤖 *AI Analysis*\n\n{get_ai_insight(prompt)}", parse_mode='Markdown')

# --- Main Execution ---
if __name__ == "__main__":
    while True:
        try:
            print("Bot is polling...")
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except Exception as e:
            print(f"Error: {e}. Restarting...")
            time.sleep(5)
