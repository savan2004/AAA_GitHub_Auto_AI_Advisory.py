"""
market_news.py — Dynamic Multi-Source News v2.0

Copilot Fix #Bug-Market-News: was static with single fallback.
Now has 4 live sources + MoneyControl RSS (free, no key).
"""

import os
import re
import logging
import requests
from datetime import date, timedelta
from api_utils import NEWS_CACHE, with_retry, raise_if_transient, TransientError
from config import TIMEOUT_TAVILY, TIMEOUT_RSS, NEWS_LOOKBACK_DAYS

logger = logging.getLogger(__name__)

_JUNK_PATTERNS = [
    "Stock Price","Quote","Yahoo Finance","TradingView","Investing.com",
    "CNBC","Chart and News","Index Today","NSE India","National Stock Exchange",
    "Live Share","Equity Market Watch","moneycontrol.com","livemint.com",
    "economictimes.indiatimes.com",
]

_FINANCIAL_DOMAINS = [
    "economictimes.indiatimes.com","moneycontrol.com","livemint.com",
    "businessline.com","reuters.com","financialexpress.com","bloomberg.com",
    "ndtv.com","business-standard.com",
]

_RSS_SOURCES = [
    ("MoneyControl Latest",  "https://www.moneycontrol.com/rss/latestnews.xml"),
    ("MoneyControl Markets", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("ET Markets",           "https://economictimes.indiatimes.com/markets/rss.cms"),
]

_MARKET_KEYWORDS = ["nifty","sensex","market","stock","sebi","rbi","bse","nse",
                     "mutual fund","ipo","earnings","results","fii","dii"]


def _is_headline(title: str) -> bool:
    if not title or len(title) < 20:
        return False
    return not any(p.lower() in title.lower() for p in _JUNK_PATTERNS)


@with_retry(max_attempts=2)
def _fetch_tavily(query: str, n: int = 6) -> list:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        return []
    resp = requests.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "max_results": n,
              "search_depth": "advanced", "include_domains": _FINANCIAL_DOMAINS},
        timeout=TIMEOUT_TAVILY,
    )
    raise_if_transient(resp)
    return [x["title"] for x in resp.json().get("results", []) if _is_headline(x.get("title",""))]


@with_retry(max_attempts=2)
def _fetch_rss(url: str) -> list:
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=TIMEOUT_RSS)
    raise_if_transient(resp)
    titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", resp.text)
    clean  = re.findall(r"<title>(.*?)</title>", resp.text)
    return [t for t in (titles or clean[1:]) if _is_headline(t)]


def get_market_news(n: int = 5) -> str:
    """
    Market-wide news headlines. 4-source chain with caching.
    Sources: Tavily → MoneyControl RSS → ET Markets RSS → Alpha Vantage
    """
    cache_key = f"mkt_news_{date.today()}"
    cached    = NEWS_CACHE.get(cache_key)
    if cached:
        return cached

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

    # Source 4: Alpha Vantage news sentiment (needs key)
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
                headlines.extend([a["title"] for a in r.get("feed", []) if _is_headline(a.get("title",""))])
            except Exception as e:
                logger.warning(f"market_news AV: {e}")

    headlines = list(dict.fromkeys(h for h in headlines if _is_headline(h)))[:n]

    if not headlines:
        result = "📰 News unavailable. Set TAVILY_API_KEY for live news."
    else:
        lines  = ["📰 <b>MARKET NEWS</b>", "━━━━━━━━━━━━━━━━━━━━"]
        lines += [f"• {h[:100]}" for h in headlines]
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        result = "\n".join(lines)

    NEWS_CACHE.set(cache_key, result)
    return result


def get_stock_news(symbol: str, n: int = 2) -> str:
    """Per-stock news. Tavily → Finnhub → MoneyControl RSS."""
    cache_key = f"news_{symbol}_{date.today()}"
    cached    = NEWS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    headlines = []
    from_date = (date.today() - timedelta(days=NEWS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
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
    NEWS_CACHE.set(cache_key, result)
    return result
