import sqlite3
import yfinance as yf
import requests
import pandas as pd
import pandas_ta as ta  # We will use simple calculation if lib missing, but pandas is standard
from datetime import datetime
from config import DB_NAME, NEWS_API_KEY

class RAGSystem:
    """Handles Long-term Memory (SQLite)"""
    def __init__(self):
        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        with self.conn:
            self.conn.execute('''
                CREATE TABLE IF NOT EXISTS analysis_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    date TEXT,
                    price REAL,
                    ai_prediction TEXT
                )
            ''')

    def save_log(self, symbol, price, prediction):
        with self.conn:
            self.conn.execute(
                "INSERT INTO analysis_log (symbol, date, price, ai_prediction) VALUES (?, ?, ?, ?)",
                (symbol, datetime.now().strftime("%Y-%m-%d %H:%M"), price, prediction)
            )

    def get_context(self, symbol, limit=3):
        cursor = self.conn.execute(
            "SELECT date, price, ai_prediction FROM analysis_log WHERE symbol = ? ORDER BY id DESC LIMIT ?",
            (symbol, limit)
        )
        rows = cursor.fetchall()
        if not rows:
            return "No historical context available."
        
        context = "Previous AI Analyses:\n"
        for row in rows:
            context += f"- Date: {row[0]} | Price: {row[1]} | Prediction: {row[2][:100]}...\n"
        return context

class MarketData:
    """Handles Real-time Data & News"""
    
    @staticmethod
    def get_stock_data(symbol):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="1mo")
            
            if df.empty:
                return None
            
            # Current Price
            current_price = df['Close'].iloc[-1]
            
            # Calculate RSI (14) manually using pandas
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs)).iloc[-1]
            
            # Simple Moving Average (20)
            sma_20 = df['Close'].rolling(window=20).mean().iloc[-1]
            
            return {
                "price": round(current_price, 2),
                "rsi": round(rsi, 2) if not pd.isna(rsi) else 50.0,
                "sma_20": round(sma_20, 2) if not pd.isna(sma_20) else current_price,
                "volume": df['Volume'].iloc[-1]
            }
        except Exception as e:
            print(f"Stock Data Error: {e}")
            return None

    @staticmethod
    def get_market_news(query="Stock Market India"):
        """Fetches top 3 news headlines"""
        url = f"https://newsapi.org/v2/everything?q={query}&sortBy=publishedAt&apiKey={NEWS_API_KEY}&language=en&pageSize=3"
        try:
            resp = requests.get(url).json()
            articles = resp.get("articles", [])
            news_summary = ""
            for art in articles:
                news_summary += f"- {art['title']} (Source: {art['source']['name']})\n"
            return news_summary if news_summary else "No relevant news found."
        except Exception as e:
            return f"News Error: {e}"
