# news_fetcher.py - Finnhub news integration

import requests
import os
import logging
from datetime import datetime, timedelta
import time

logger = logging.getLogger(__name__)

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

_news_cache = {
    "data": None,
    "timestamp": None,
    "last_update": None
}

def should_update_news():
    if _news_cache["last_update"] is None:
        return True
    now = datetime.now()
    last = _news_cache["last_update"]
    return (now - last).total_seconds() >= 3600

def get_finnhub_news():
    """Fetch Indian market news from Finnhub"""
    if not FINNHUB_API_KEY:
        logger.warning("FINNHUB_API_KEY not set")
        return []
    
    news_list = []
    
    try:
        url = f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_API_KEY}"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                india_keywords = ['india', 'nse', 'bse', 'rupee', 'mumbai', 'sensex', 
                                 'nifty', 'rbi', 'modi', 'gst', 'indian', 'delhi']
                
                for item in data[:15]:
                    headline = item.get('headline', '')
                    summary = item.get('summary', '')
                    
                    if any(keyword in headline.lower() or keyword in summary.lower() 
                           for keyword in india_keywords):
                        news_list.append({
                            'title': headline[:150],
                            'summary': summary[:200],
                            'source': item.get('source', 'Finnhub'),
                            'url': item.get('url', '#'),
                            'date': datetime.fromtimestamp(
                                item.get('datetime', int(time.time()))
                            ).strftime('%Y-%m-%d')
                        })
                        
                        if len(news_list) >= 5:
                            break
        
        return news_list[:5]
        
    except Exception as e:
        logger.error(f"Finnhub error: {e}")
        return []

def format_news(news_list: list) -> str:
    if not news_list:
        return "📰 No recent market news available."
    
    text = "📰 <b>Market News</b>\n\n"
    
    for i, item in enumerate(news_list, 1):
        title = item.get('title', 'No title')
        source = item.get('source', 'Unknown')
        date = item.get('date', '')
        url = item.get('url', '#')
        
        if len(title) > 100:
            title = title[:97] + "..."
        
        text += f"{i}. <a href='{url}'>{title}</a>\n"
        text += f"   📌 {source} | {date}\n\n"
    
    text += "📊 <i>Powered by Finnhub</i>"
    return text

def get_market_news(force_refresh=False):
    global _news_cache
    
    if force_refresh or should_update_news() or _news_cache["data"] is None:
        try:
            news = get_finnhub_news()
            if news:
                _news_cache["data"] = news
                _news_cache["timestamp"] = datetime.now()
                _news_cache["last_update"] = datetime.now()
        except Exception as e:
            logger.error(f"News update failed: {e}")
    
    if _news_cache["data"]:
        return format_news(_news_cache["data"])
    else:
        return "📰 News temporarily unavailable."