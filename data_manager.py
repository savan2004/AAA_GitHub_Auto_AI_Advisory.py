import time
import requests
from typing import Optional, List
import yfinance as yf
from config import Config

class DataManager:
    """Multi-source data with retry and throttling."""
    
    def __init__(self):
        self.yahoo = YahooFinanceManager()
        self.alpha = AlphaVantageManager()
        self.last_request_time = 0
        self.throttle_delay = 1  # 1 second between requests
    
    def _throttle(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < self.throttle_delay:
            time.sleep(self.throttle_delay - elapsed)
        self.last_request_time = time.time()
    
    def get_ltp(self, symbol: str) -> Optional[float]:
        self._throttle()
        price = self.yahoo.get_ltp(symbol)
        if price is None:
            price = self.alpha.get_ltp(symbol)
        return price
    
    def get_news(self, symbol: str) -> List[str]:
        self._throttle()
        return self.alpha.get_news(symbol)

class YahooFinanceManager:
    def get_ltp(self, symbol: str) -> Optional[float]:
        try:
            ticker = yf.Ticker(symbol + ".NS")
            data = ticker.history(period="1d", interval="1m")
            return data['Close'].iloc[-1] if not data.empty else None
        except Exception as e:
            print(f"Yahoo LTP Error: {e}")
            return None

class AlphaVantageManager:
    def __init__(self):
        self.api_key = Config.ALPHA_VANTAGE_KEY
        self.news_key = Config.NEWSAPI_KEY
        self.base_url = "https://www.alphavantage.co/query"
        self.news_url = "https://newsapi.org/v2/everything"
    
    def get_ltp(self, symbol: str) -> Optional[float]:
        for attempt in range(Config.MAX_RETRIES):
            try:
                params = {"function": "GLOBAL_QUOTE", "symbol": symbol + ".NS", "apikey": self.api_key}
                response = requests.get(self.base_url, params=params, timeout=Config.TIMEOUT)
                data = response.json()
                if "Global Quote" in data:
                    return float(data["Global Quote"]["05. price"])
                return None
            except Exception as e:
                print(f"Alpha LTP Attempt {attempt + 1} failed: {e}")
                time.sleep(1)
        return None
    
    def get_news(self, symbol: str) -> List[str]:
        for attempt in range(Config.MAX_RETRIES):
            try:
                params = {"q": symbol, "apiKey": self.news_key, "sortBy": "publishedAt", "pageSize": 3}
                response = requests.get(self.news_url, params=params, timeout=Config.TIMEOUT)
                data = response.json()
                return [article["title"] for article in data.get("articles", [])] if "articles" in data else []
            except Exception as e:
                print(f"NewsAPI Attempt {attempt + 1} failed: {e}")
                time.sleep(1)
        return []