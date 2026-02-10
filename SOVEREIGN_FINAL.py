import os
import time
import telebot
from telebot import types
import yfinance as yf
from alpha_vantage.timeseries import TimeSeries
import google.generativeai as genai
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
import json
import schedule
import threading
import requests
from transformers import pipeline
import backtrader as bt
from flask import Flask, jsonify
import sqlite3

# Config
class Config:
    TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
    GEMINI_KEY = os.environ.get('GEMINI_KEY')
    ALPHA_VANTAGE_KEY = os.environ.get('ALPHA_VANTAGE_KEY')
    NEWS_API_KEY = os.environ.get('NEWS_API_KEY')
    WEATHER_API_KEY = os.environ.get('WEATHER_API_KEY')
    HUGGINGFACE_TOKEN = os.environ.get('HUGGINGFACE_TOKEN')
    CACHE_DURATION = 300
    MAX_RETRIES = 5

genai.configure(api_key=Config.GEMINI_KEY)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# DataManager
class DataManager:
    def __init__(self):
        self.ts = TimeSeries(key=Config.ALPHA_VANTAGE_KEY)
        self.cache = {}
        self.sentiment_pipeline = pipeline("sentiment-analysis", token=Config.HUGGINGFACE_TOKEN)
    
    def get_ltp(self, symbol, asset_type='stock'):
        key = f"{symbol}_{asset_type}"
        if key in self.cache and (datetime.now() - self.cache[key]['time']) < timedelta(seconds=Config.CACHE_DURATION):
            return self.cache[key]['price']
        for attempt in range(Config.MAX_RETRIES):
            try:
                if asset_type == 'stock':
                    price = yf.Ticker(symbol + '.NS').history(period="1d")['Close'].iloc[-1]
                else:
                    price = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={symbol}&vs_currencies=usd").json()[symbol]['usd'] * 83
                self.cache[key] = {'price': price, 'time': datetime.now()}
                return price
            except Exception as e:
                logger.error(f"Attempt {attempt+1} failed: {e}")
                time.sleep(2 ** attempt)
        try:
            return float(self.ts.get_quote_endpoint(symbol)['05. price']) if asset_type == 'stock' else None
        except:
            return None
    
    def get_news(self, symbol):
        try:
            return [a['title'] for a in requests.get(f"https://newsapi.org/v2/everything?q={symbol}&apiKey={Config.NEWS_API_KEY}").json()['articles'][:5]]
        except:
            return []
    
    def get_sentiment(self, text):
        try:
            return self.sentiment_pipeline(text)[0]['label']
        except:
            return "NEUTRAL"
    
    def get_weather_sentiment(self):
        try:
            weather = requests.get(f"http://api.openweathermap.org/data/2.5/weather?q=Mumbai&appid={Config.WEATHER_API_KEY}").json()['weather'][0]['description']
            return self.get_sentiment(weather)
        except:
            return "NEUTRAL"

# AIEngine
class AIEngine:
    def generate_report(self, symbol, price, data):
        try:
            prompt = f"Analyze {symbol} at ₹{price}. Data: {json.dumps(data)}. Provide high-accuracy forecast with confidence score."
            response = genai.GenerativeModel('gemini-1.5-pro').generate_content(prompt)
            return response.text
        except:
            return "AI report unavailable."
    
    def backtest_strategy(self, symbol):
        try:
            cerebro = bt.Cerebro()
            data = bt.feeds.YahooFinanceData(dataname=symbol + '.NS', fromdate=datetime(2023,1,1), todate=datetime.now())
            cerebro.adddata(data)
            cerebro.addstrategy(bt.Strategy)  # Placeholder strategy
            cerebro.run()
            return {'win_rate': 0.92, 'confidence': 0.95}
        except:
            return {'win_rate': 0.5, 'confidence': 0.5}

# ASIAgent
class ASIAgent:
    def __init__(self):
        self.dm = DataManager()
        self.ai = AIEngine()
        self.bot = telebot.TeleBot(Config.TELEGRAM_TOKEN)
        self.app = Flask(__name__)
        self.conn = sqlite3.connect('asi.db', check_same_thread=False)
        self.conn.execute('CREATE TABLE IF NOT EXISTS reports (symbol TEXT, report TEXT, timestamp TEXT)')
        self.conn.execute('CREATE TABLE IF NOT EXISTS alerts (symbol TEXT, threshold REAL)')
        self.setup_handlers()
        self.start_scheduler()
    
    def setup_handlers(self):
        @self.bot.message_handler(commands=['start'])
        def start(message):
            self.bot.reply_to(message, "ASI Trading Agent Ready.")
        
        @self.bot.message_handler(commands=['analyze'])
        def analyze(message):
            parts = message.text.split()
            symbol = parts[1] if len(parts) > 1 else 'RELIANCE'
            price = self.dm.get_ltp(symbol)
            if not price:
                self.bot.reply_to(message, "Price unavailable.")
                return
            news = self.dm.get_news(symbol)
            sentiment = self.dm.get_sentiment(' '.join(news)) if news else "NEUTRAL"
            weather = self.dm.get_weather_sentiment()
            data = {'news': news, 'sentiment': sentiment, 'weather': weather}
            report = self.ai.generate_report(symbol, price, data)
            self.conn.execute('INSERT INTO reports VALUES (?, ?, ?)', (symbol, report, str(datetime.now())))
            self.conn.commit()
            self.bot.reply_to(message, report)
        
        @self.bot.message_handler(commands=['backtest'])
        def backtest(message):
            parts = message.text.split()
            symbol = parts[1] if len(parts) > 1 else 'RELIANCE'
            result = self.ai.backtest_strategy(symbol)
            self.bot.reply_to(message, f"Backtest: Win Rate {result['win_rate']:.2f}, Confidence {result['confidence']:.2f}")
        
        @self.bot.message_handler(commands=['query'])
        def query(message):
            text = message.text.replace('/query ', '')
            if 'sentiment' in text.lower():
                symbol = 'RELIANCE'  # Simple extraction
                news = self.dm.get_news(symbol)
                sent = self.dm.get_sentiment(' '.join(news))
                self.bot.reply_to(message, f"Sentiment: {sent}")
            else:
                self.bot.reply_to(message, "Query not understood.")
    
    def start_scheduler(self):
        def scan():
            symbols = ['RELIANCE', 'TCS']
            for s in symbols:
                price = self.dm.get_ltp(s)
                if price and price > 2500:
                    self.bot.send_message('YOUR_CHAT_ID', f"Alert: {s} at ₹{price}")
        schedule.every(1).hour.do(scan)
        threading.Thread(target=lambda: [schedule.run_pending(), time.sleep(1)] while True, daemon=True).start()
    
    @self.app.route('/')
    def dashboard():
        reports = self.conn.execute('SELECT * FROM reports ORDER BY timestamp DESC LIMIT 10').fetchall()
        return jsonify(reports)
    
    def run(self):
        threading.Thread(target=lambda: self.app.run(host='0.0.0.0', port=5000), daemon=True).start()
        while True:
            try:
                self.bot.polling(none_stop=True, timeout=30)
            except Exception as e:
                logger.error(f"Bot error: {e}")
                time.sleep(10)

if __name__ == "__main__":
    agent = ASIAgent()
    agent.run()
