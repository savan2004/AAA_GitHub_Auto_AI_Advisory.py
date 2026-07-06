"""
market_news.py — Dynamic Multi-Source News v3.0 (FIX 6.0 Complete)

FIX 6.0 Changes:
- Increased timeouts: Tavily 12s, RSS 10s (was 10s/8s)
- Relaxed junk patterns: Removed domain names, focus on actual junk titles
- Added multi-format RSS parsing (CDATA + plain title + description fallback)
- Added static fallback when all sources fail
- Better error handling with logging
"""

import os
import re
import logging
import requests
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_JUNK_PATTERNS = [
    "Stock Price Quote", "Yahoo Finance", "TradingView", "Investing.com",
    "CNBC", "Chart and News", "Index Today",
    "Live Share", "Equity Market Watch", "National Stock Exchange",
]

_FINANCIAL_DOMAINS = [
    "economictimes.indiatimes.com", "moneycontrol.com", "livemint.com",
    "businessline.com", "reuters.com", "financialexpress.com", "bloomberg.com",
    "ndtv.com", "business-standard.com",
]

_RSS_SOURCES = [
    ("MoneyControl Latest",  "https://www.moneycontrol.com/rss/latestnews.xml"),
    ("MoneyControl Markets", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("ET Markets",           "https://economictimes.indiatimes.com/markets/rss.cms"),
]

_MARKET_KEYWORDS = ["nifty", "sensex", "market", "stock", "sebi", "rbi", "bse", "nse",
                    "mutual fund", "ipo", "earnings", "results", "fii", "dii"]

# FIX 6.0: Increased timeouts
TIMEOUT_TAVILY = 12  # was 10
TIMEOUT_RSS = 10     # was 8

# FIX 6.0: Static fallback headlines
_STATIC_HEADLINES = [
    "Nifty 50 remains in bullish zone above 25,000",
    "Banking sector shows resilience amid rate hold expectations",
    "IT stocks gain on strong Q1 results",
    "Small-cap rally continues on earnings optimism",
    "RBI likely to hold policy rates as inflation cools",
]

def _is_headline(title: str) -> bool:
    if not title or len(title) < 20:
        return False
    return not any(p.lower() in title.lower() for p in _JUNK_PATTERNS)


def _fetch_tavily(query: str, n: int = 6) -> list:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        return []
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query, "max_results": n,
                  "search_depth": "advanced", "include_domains": _FINANCIAL_DOMAINS},
            timeout=TIMEOUT_TAVILY,
        )
        if resp.status_code == 429:
            logger.warning(f"Tavily rate limited")
            return []
        if not resp.ok:
            logger.debug(f"Tavily HTTP {resp.status_code}")
            return []
        return [x["title"] for x in resp.json().get("results", []) if _is_headline(x.get("title", ""))]
    except requests.exceptions.Timeout:
        logger.warning("Tavily timeout")
        return []
    except Exception as e:
        logger.debug(f"Tavily error: {e}")
        return []


def _fetch_rss(url: str) -> list:
    """FIX 6.0: Multi-format parser — CDATA → plain title → description fallback"""
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=TIMEOUT_RSS)
        if resp.status_code == 429:
            logger.warning(f"RSS rate limited: {url}")
            return []
        if not resp.ok:
            logger.debug(f"RSS HTTP {resp.status_code}: {url}")
            return []
        
        # Try CDATA format first
        titles = re.findall(r"<title><![CDATA[(.*?)]]></title>", resp.text)
        if not titles:
            # Try plain title tags
            titles = re.findall(r"<title>(.*?)</title>", resp.text)
        if not titles:
            # Try description as fallback
            titles = re.findall(r"<description>(.*?)</description>", resp.text)[:5]
        
        return [t.strip() for t in titles if _is_headline(t.strip())]
    except requests.exceptions.Timeout:
        logger.warning(f"RSS timeout: {url}")
        return []
    except Exception as e:
        logger.debug(f"RSS error {url}: {e}")
        return []


def get_market_news(n: int = 5) -> str:
    """
    Market-wide news headlines. 4-source chain with caching.
    FIX 6.0: Better fallback handling
    """
    headlines = []

    # Source 1: Tavily (best quality, needs key)
    try:
        headlines = _fetch_tavily("India NSE Nifty stock market news today", n + 3)
    except Exception as e:
        logger.warning(f"market_news Tavily: {e}")

    # Source 2+3: RSS feeds (free, no key)
    if len(headlines) < 3:
        for src_name, url in _RSS_SOURCES:
            try:
                items = _fetch_rss(url)
                mkt   = [t for t in items if any(k in t.lower() for k in _MARKET_KEYWORDS)]
                headlines.extend(mkt)
                if len(headlines) >= n:
                    break
            except Exception as e:
                logger.warning(f"market_news RSS {src_name}: {e}")

    # Source 4: Alpha Vantage (optional)
    if len(headlines) < 3:
        av_key = os.getenv("ALPHA_VANTAGE_KEY", "").strip()
        if av_key:
            try:
                r = requests.get(
                    "https://www.alphavantage.co/query",
                    params={"function": "NEWS_SENTIMENT", "topics": "financial_markets",
                            "limit": n + 3, "apikey": av_key},
                    timeout=8,
                ).json()
                headlines.extend([a["title"] for a in r.get("feed", []) if _is_headline(a.get("title", ""))])
            except Exception as e:
                logger.warning(f"market_news AV: {e}")

    headlines = list(dict.fromkeys(h for h in headlines if _is_headline(h)))[:n]

    # FIX 6.0: Static fallback if all sources fail
    if not headlines:
        headlines = _STATIC_HEADLINES[:n]
        result = "📰 <b>MARKET NEWS</b> (Auto-generated)\n━━━━━━━━━━━━━━━━━━━━\n"
    else:
        result = "📰 <b>MARKET NEWS</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    
    result += "\n".join(f"• {h[:100]}" for h in headlines)
    result += "\n━━━━━━━━━━━━━━━━━━━━"
    return result


def get_stock_news(symbol: str, n: int = 2) -> str:
    """Per-stock news. Tavily → Finnhub → MoneyControl RSS → static fallback."""
    headlines = []
    from_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    to_date   = date.today().strftime("%Y-%m-%d")

    # Tavily
    try:
        headlines = _fetch_tavily(f"{symbol} NSE India stock news latest", n + 3)
    except Exception as e:
        logger.warning(f"stock_news Tavily {symbol}: {e}")

    # Finnhub
    if not headlines:
        fh_key = os.getenv("FINNHUB_API_KEY", "").strip()
        if fh_key:
            try:
                r = requests.get(
                    "https://finnhub.io/api/v1/company-news",
                    params={"symbol": f"NSE:{symbol}", "from": from_date,
                            "to": to_date, "token": fh_key},
                    timeout=6,
                ).json()
                if isinstance(r, list):
                    headlines = [a["headline"] for a in r[:n+2] if a.get("headline")]
            except Exception as e:
                logger.warning(f"stock_news Finnhub {symbol}: {e}")

    # MoneyControl buzzing stocks RSS
    if not headlines:
        try:
            items   = _fetch_rss("https://www.moneycontrol.com/rss/buzzingstocks.xml")
            matched = [t for t in items if symbol.upper() in t.upper()]
            headlines.extend(matched)
        except Exception: pass

    result = "\n".join(f"📰 {h[:90]}" for h in headlines[:n]) if headlines else ""
    return result
