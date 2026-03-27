# market_news.py
import time
import logging
from typing import List, Dict

import requests

logger = logging.getLogger(__name__)

_NEWS_CACHE: Dict[str, Dict] = {}
_NEWS_TTL = 5 * 60  # 5 minutes


def _get_cached(key: str, ttl: int):
    d = _NEWS_CACHE.get(key)
    if d and time.time() - d["ts"] < ttl:
        return d["val"]
    return None


def _set_cached(key: str, val):
    _NEWS_CACHE[key] = {"val": val, "ts": time.time()}


def fetch_latest_market_news(limit: int = 4) -> List[str]:
    """
    Fetch latest Indian market news headlines.
    Currently placeholder – plug your real API here.
    Returns a list of short headline strings.
    """
    cache_key = f"news_{limit}"
    cached = _get_cached(cache_key, _NEWS_TTL)
    if cached:
        return cached

    headlines: List[str] = []

    try:
        # TODO: Replace this block with your real news API.
        # Example shape:
        # resp = requests.get("https://your-news-api.example.com/market", timeout=5)
        # resp.raise_for_status()
        # data = resp.json()
        # headlines = [item["title"] for item in data["articles"]][:limit]

        # TEMP fallback sample headlines (so bot never breaks)
        headlines = [
            "Taking Stock: Market fails to hold on to day's gains, ends marginally higher",
            "Sensex, Nifty gain for third day in a row; easing volatility to support bull trend",
            "Mid-day Mood | Cooling volatility sparks market rally, India VIX sees steepest fall in 4 years",
            "Sensex, Nifty extend gains to 3rd day, Q4 results to guide stock-specific action",
        ][:limit]

    except Exception as e:
        logger.error(f"Error fetching market news: {e}")
        # On failure, keep headlines empty – section will be skipped in main.

    _set_cached(cache_key, headlines)
    return headlines
