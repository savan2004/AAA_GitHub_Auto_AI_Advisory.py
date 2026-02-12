# Telegram Stock Analysis Bot

## main.py
```python
import json
import logging
import requests
import yfinance as yf
import pandas as pd
import telebot
from openai import OpenAI
from time import sleep

# Load configuration
with open('config.json') as config_file:
    config = json.load(config_file)

API_KEY = config['API_KEY']
NEWS_API_KEY = config['NEWS_API_KEY']
bot = telebot.TeleBot(API_KEY)

# Setup logging
logging.basicConfig(level=logging.INFO)

# Error handling decorator
def error_handler(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logging.error(f"Error in {func.__name__}: {e}")
            return None
    return wrapper

@error_handler
def smart_search(symbol):
    stock = yf.Ticker(symbol)
    data = stock.history(period="1d")
    ltp = data['Close'].iloc[-1]
    # Additional calculations for RSI, ASI, etc.
    return f"**{symbol}**: LTP: {ltp} 📈"

@error_handler
def market_scan():
    # Scan logic for large, mid, small cap stocks
    return "Market Scan Overview: ..."

@error_handler
def nifty_options(budget, spot):
    # Logic for suggesting trades
    return "Nifty Options Suggestions: ..."

@error_handler
def portfolio_tracker(user_stocks):
    # Logic to track user stocks
    return "Portfolio Tracker: ..."

@error_handler
def news_alerts(symbol):
    response = requests.get(f"https://newsapi.org/v2/everything?q={symbol}&apiKey={NEWS_API_KEY}")
    articles = response.json().get('articles', [])
    return f"News for {symbol}: {len(articles)} articles found."

@error_handler
def technical_indicators(symbol):
    # Detailed analysis for a symbol
    return "Technical Indicators: ..."

@error_handler
def market_sentiment():
    # NSE sentiment analysis
    return "Market Sentiment: ..."

@error_handler
def ai_portfolio_builder():
    # Build a 10-stock portfolio
    return "AI Portfolio Builder: ..."

@bot.message_handler(commands=['start'])
def start_command(message):
    bot.reply_to(message, "Welcome to the Stock Analysis Bot! Use /help for commands.")

@bot.message_handler(commands=['smart_search'])
def handle_smart_search(message):
    symbol = message.text.split()[1]
    result = smart_search(symbol)
    bot.reply_to(message, result)

@bot.message_handler(commands=['market_scan'])
def handle_market_scan(message):
    result = market_scan()
    bot.reply_to(message, result)

@bot.message_handler(commands=['nifty_options'])
def handle_nifty_options(message):
    budget, spot = map(float, message.text.split()[1:])
    result = nifty_options(budget, spot)
    bot.reply_to(message, result)

@bot.message_handler(commands=['portfolio_tracker'])
def handle_portfolio_tracker(message):
    user_stocks = message.text.split()[1:]
    result = portfolio_tracker(user_stocks)
    bot.reply_to(message, result)

@bot.message_handler(commands=['news_alerts'])
def handle_news_alerts(message):
    symbol = message.text.split()[1]
    result = news_alerts(symbol)
    bot.reply_to(message, result)

@bot.message_handler(commands=['technical_indicators'])
def handle_technical_indicators(message):
    symbol = message.text.split()[1]
    result = technical_indicators(symbol)
    bot.reply_to(message, result)

@bot.message_handler(commands=['market_sentiment'])
def handle_market_sentiment(message):
    result = market_sentiment()
    bot.reply_to(message, result)

@bot.message_handler(commands=['ai_portfolio_builder'])
def handle_ai_portfolio_builder(message):
    result = ai_portfolio_builder()
    bot.reply_to(message, result)

if __name__ == "__main__":
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            sleep(5)
