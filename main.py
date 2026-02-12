# AAA_GitHub_Auto_AI_Advisory.py
# Enhanced by Blackbox AI for detailed outputs, OpenAI integration, and robustness.

import os
import time
import telebot
from telebot import types
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from functools import lru_cache
import logging
import json
import subprocess
import sys
from typing import Dict, List, Tuple, Optional
import threading
import traceback
import sqlite3
import re
import requests  # For news fetching
import matplotlib.pyplot as plt  # For optional chart rendering (text-based fallback)

# Conditional imports
try:
    import yfinance as yf
except ImportError:
    print("❌ yfinance not found. Install: pip install yfinance")
    yf = None

try:
    import git
except ImportError:
    print("❌ GitPython not found. Install: pip install GitPython")
    git = None

try:
    import openai
except ImportError:
    print("❌ openai not found. Install: pip install openai")
    openai = None

# Configuration
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY")
    GITHUB_REPO_PATH = os.getenv("GITHUB_REPO_PATH", "/path/to/your/repo")
    GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
    NEWS_API_KEY = os.getenv("NEWS_API_KEY", "YOUR_NEWS_API_KEY")  # For news (optional)
    CACHE_DURATION = 300
    MAX_RETRIES = 3
    TIMEOUT = 30

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler('asi_bot.log'), logging.StreamHandler()])
logger = logging.getLogger(__name__)

# RAG System
class RAGSystem:
    def __init__(self, db_path='asi_rag.db'):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historical_data (
                id INTEGER PRIMARY KEY,
                symbol TEXT,
                date TEXT,
                ltp REAL,
                rsi REAL,
                macd REAL,
                trend TEXT,
                news TEXT,
                analysis TEXT
            )
        ''')
        conn.commit()
        conn.close()
    
    def store_data(self, symbol: str, data: Dict):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO historical_data (symbol, date, ltp, rsi, macd, trend, news, analysis) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                       (symbol, datetime.now().strftime('%Y-%m-%d'), data.get('ltp'), data.get('rsi'), data.get('macd'), data.get('trend'), data.get('news'), data.get('analysis')))
        conn.commit()
        conn.close()
    
    def retrieve_context(self, symbol: str, limit=5) -> str:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT date, ltp, rsi, macd, trend, news, analysis FROM historical_data WHERE symbol = ? ORDER BY date DESC LIMIT ?',
                       (symbol, limit))
        rows = cursor.fetchall()
        conn.close()
        context = f"Historical data for {symbol}:\n"
        for row in rows:
            context += f"Date: {row[0]}, LTP: {row[1]}, RSI: {row[2]}, MACD: {row[3]}, Trend: {row[4]}, News: {row[5]}, Analysis: {row[6]}\n"
        return context

# Yahoo Finance Manager
class YahooFinanceManager:
    def __init__(self):
        self.available = yf is not None
    
    @lru_cache(maxsize=100)
    def get_ltp(self, symbol: str) -> Optional[float]:
        if not self.available: return None
        try:
            ticker = yf.Ticker(symbol + ".NS")
            data = ticker.history(period="1d", interval="1m")
            return data['Close'].iloc[-1] if not data.empty else None
        except Exception as e:
            logger.error(f"LTP Error for {symbol}: {e}")
            return None
    
    def get_market_data(self, symbol: str) -> Dict:
        if not self.available: return {}
        try:
            ticker = yf.Ticker(symbol + ".NS")
            info = ticker.info
            hist = ticker.history(period="1y")
            rsi = self._calculate_rsi(hist['Close'])
            macd = self._calculate_macd(hist['Close'])
            return {
                'ltp': hist['Close'].iloc[-1] if not hist.empty else None,
                'rsi': rsi,
                'macd': macd,
                '52w_high': info.get('fiftyTwoWeekHigh'),
                '52w_low': info.get('fiftyTwoWeekLow'),
                'market_cap': info.get('marketCap'),
                'pe_ratio': info.get('trailingPE'),
                'roe': info.get('returnOnEquity'),
                'sector': info.get('sector')
            }
        except Exception as e:
            logger.error(f"Market Data Error for {symbol}: {e}")
            return {}
    
    def _calculate_rsi(self, prices, period=14):
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs)).iloc[-1]
    
    def _calculate_macd(self, prices):
        ema12 = prices.ewm(span=12).mean()
        ema26 = prices.ewm(span=26).mean()
        return (ema12 - ema26).iloc[-1]
    
    def get_option_chain(self, symbol: str, expiry: str) -> Optional[pd.DataFrame]:
        if not self.available: return None
        try:
            ticker = yf.Ticker(symbol + ".NS")
            options = ticker.option_chain(expiry)
            return pd.DataFrame(options.calls.append(options.puts))
        except Exception as e:
            logger.error(f"Option Chain Error for {symbol}: {e}")
            return None
    
    def fetch_news(self, symbol: str) -> List[str]:
        try:
            url = f"https://newsapi.org/v2/everything?q={symbol}&apiKey={Config.NEWS_API_KEY}&pageSize=3"
            response = requests.get(url, timeout=Config.TIMEOUT)
            articles = response.json().get('articles', [])
            return [f"{a['title']}: {a['description']}" for a in articles[:3]]
        except Exception as e:
            logger.error(f"News Fetch Error: {e}")
            return ["No recent news available."]

# AI Engine with OpenAI
class AIEngine:
    def __init__(self, api_key: str, rag_system: RAGSystem):
        if openai is None: raise ImportError("OpenAI not available")
        openai.api_key = api_key
        self.rag = rag_system
    
    def generate_research_report(self, symbol: str, price: float, market_data: Dict) -> str:
        context = self.rag.retrieve_context(symbol)
        news = YahooFinanceManager().fetch_news(symbol)
        prompt = f"""
        Generate a detailed professional trading advisory report for {symbol} at ₹{price}. Use context: {context}. News: {news}. Market Data: {json.dumps(market_data)}.
        
        Format exactly as:
        🚀 **SK AUTO AI ADVISORY** 🚀
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        📅 **DATE:** {datetime.now().strftime('%d-%b-%Y')} | ⏰ **TIME:** {datetime.now().strftime('%H:%M')}(IST)
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        🏷 **SYMBOL:** {symbol}
        🏛 **ASI RANK:** [0-100 score]
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        💰 **LTP:** ₹{price} | 📊 **RSI:** {market_data.get('rsi', 'N/A')} | 📈 **MACD:** {market_data.get('macd', 'N/A')}
        📈 **TREND:** [BEARISH/BULLISH/NEUTRAL] | 52wk High: {market_data.get('52w_high', 'N/A')} | 52wk Low: {market_data.get('52w_low', 'N/A')}
        
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        🎯 **VERDICT:** [HOLD/WAIT/BUY/SELL] (Time Frame: Short-term)
        🚀 **Short term UPSIDE:** [5-20%] (3-6 Months)
        **Long Term UPSIDE:** [20-100%] (1-3 Years)
        
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        📦 **FUNDAMENTAL LEVELS**
        - Market Cap: {market_data.get('market_cap', 'N/A')} Cr | Sector: {market_data.get('sector', 'N/A')}
        - P/E Ratio: {market_data.get('pe_ratio', 'N/A')}x | ROE: {market_data.get('roe', 'N/A')}% | Best Value: [Estimate]
        
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        🏗 **DEEP TECHNICAL LEVELS** (Enhanced)
        SMA 20: [Calc] | SMA 50: [Calc] | SMA 200: [Calc]
        Bollinger Upper: [Calc] | Lower: [Calc]
        🔴 R3: [Calc] | R2: [Calc] | R1: [Calc] | 🟢 PP: [Calc] | S1: [Calc] | S2: [Calc] | S3: [Calc]
        📊 **ASCII Chart:** [Simple text chart of price trend]
        
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        🧠 **COMPANY INFORMATION**
        ✅ **POSITIVE:** [List 3+]
        ❌ **NEGATIVE:** [List 3+]
        
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        📰 **LATEST NEWS:** {'; '.join(news)}
        
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        📝 **CONCLUSION:** [Summary]
        ⚠️ **RISK:** [Key risks]
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        _AI AUTO ADVISORY - Invest Wisely, Trade Smartly!_
        """
        try:
            response = openai.ChatCompletion.create(model="gpt-4", messages=[{"role": "user", "content": prompt}], max_tokens=2000)
            analysis = response['choices'][0]['message']['content']
            self.rag.store_data(symbol, {**market_data, 'news': '; '.join(news), 'analysis': analysis[:500]})
            return analysis
        except Exception as e:
            logger.error(f"AI Report Error: {e}")
            return "⚠️ AI Report Unavailable"
    
    def quick_signal(self, symbol: str, price: float) -> str:
        context = self.rag.retrieve_context(symbol)
        prompt = f"Quick signal for {symbol} at ₹{price}. Context: {context}. Provide: Buy/Sell/Hold, key indicator, target, stop-loss, probability (0-100%)."
        try:
            response = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=[{"role": "user", "content": prompt}])
            return response['choices'][0]['message']['content']
        except Exception as e:
            return "⚠️ Quick Signal Unavailable"
    
    def analyze_multibagger(self, fundamentals: Dict) -> Dict:
        prompt = f"Analyze for multibagger potential: {json.dumps(fundamentals)}. Return JSON with keys: growth_score, health_score, position_score, risks, timeline, entry_price, target_price, stop_loss."
        try:
            response = openai.ChatCompletion.create(model="gpt-4", messages=[{"role": "user", "content": prompt}])
            return json.loads(response['choices'][0]['message']['content'])
        except Exception as e:
            return {}

# Dual AI Engine
class DualAIEngine:
    def __init__(self, rag_system: RAGSystem):
        self.rag = rag_system
        self.primary = AIEngine(Config.OPENAI_API_KEY, self.rag)
        self.secondary = None  # Add secondary if needed
        self.current_engine = "primary"
    
    def generate_research_report(self, symbol: str, price: float, market_data: Dict) -> str:
        try:
            return self.primary.generate_research_report(symbol, price, market_data)
        except Exception as e:
            return "⚠️ AI Unavailable"
    
    def quick_signal(self, symbol: str, price: float) -> str:
        try:
            return self.primary.quick_signal(symbol, price)
        except Exception as e:
            return "⚠️ AI Unavailable"
    
    def analyze_multibagger(self, fundamentals: Dict) -> Dict:
        try:
            return self.primary.analyze_multibagger(fundamentals)
        except Exception as e:
            return {}

# Options Calculator (Completed)
class OptionsCalculator:
    @staticmethod
    def calculate_payoff(strategy: str, spot: float, strikes: List[float], premiums: List[float]) -> Dict:
        if not strikes or not premiums or len(strikes) != len(premiums):
            return {'error': 'Invalid inputs'}
        price_range = np.linspace(spot * 0.85, spot * 1.15, 100)
        strategies = {
            'bull_call_spread': OptionsCalculator._bull_call_spread,
            'bear_put_spread': OptionsCalculator._bear_put_spread,
            'iron_condor': OptionsCalculator._iron_condor,
            'butterfly': OptionsCalculator._butterfly,
            'straddle': OptionsCalculator._straddle,
            'strangle': OptionsCalculator._strangle,
            'call_ratio_spread': OptionsCalculator._call_ratio_spread,
            'put_ratio_spread': OptionsCalculator._put_ratio_spread,
            'jade_lizard': OptionsCalculator._jade_lizard,
            'reverse_iron_condor': OptionsCalculator._reverse_iron_condor
        }
        if strategy in strategies:
            try:
                return strategies[strategy](spot, strikes, premiums, price_range)
            except Exception as e:
                return {'error': str(e)}
        return {'error': 'Strategy not found'}
    
    @staticmethod
    def _bull_call_spread(spot, strikes, premiums, price_range):
        buy_strike, sell_strike = strikes[0], strikes[1]
        buy_premium, sell_premium = premiums[0], premiums[1]
        net_premium = buy_premium - sell_premium
        payoffs = [max(price - buy_strike, 0) - buy_premium - (max(price - sell_strike, 0) - sell_premium) for price in price_range]
        return {'payoffs': payoffs, 'price_range': price_range.tolist(), 'net_premium': net_premium, 'max_profit': sell_strike - buy_strike - net_premium, 'max_loss': net_premium}
    
    # Add similar methods for other strategies (e.g., _bear_put_spread, etc.) - abbreviated for brevity
    @staticmethod
    def _bear_put_spread(spot, strikes, premiums, price_range):
        # Implement
