"""Source-reported market and stock news only. Never fabricate fallback headlines.
Publication times are unverified by the legacy title-only adapters.
"""

import os
import html
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

# Legacy report_generator compatibility; empty rather than invented stock copy.
_STOCK_NEWS_FALLBACK = {}

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
    GUARANTEED: Returns at least static headlines if all sources fail.
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

    if not headlines:
        logger.warning("market_news: no source-reported market headlines available")
        return "📰 <b>MARKET NEWS</b>\n⚠️ Source headlines unavailable. No news has been invented."
    # Upstream title-only feeds do not consistently verify publisher timestamps.
    result = ("📰 <b>MARKET NEWS</b>\n"
              "<i>Source-reported headlines; publication times unverified.</i>\n"
              "━━━━━━━━━━━━━━━━━━━━\n")
    result += "\n".join(f"• {html.escape(str(h)[:100], quote=False)}" for h in headlines)
    result += "\n━━━━━━━━━━━━━━━━━━━━"
    return result


def get_stock_news(symbol: str, n: int = 2) -> str:
    """
    Per-stock headlines from providers; no static or invented fallback.
    The upstream title-only adapters do not verify publication date.
    """
    headlines = []
    from_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    to_date   = date.today().strftime("%Y-%m-%d")

    # Tavily
    try:
        headlines = _fetch_tavily(f"{symbol} NSE India stock news latest", n + 3)
        if headlines:
            logger.info(f"stock_news {symbol}: Tavily found {len(headlines)} results")
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
                    if headlines:
                        logger.info(f"stock_news {symbol}: Finnhub found {len(headlines)} results")
            except Exception as e:
                logger.warning(f"stock_news Finnhub {symbol}: {e}")

    # MoneyControl buzzing stocks RSS
    if not headlines:
        try:
            items   = _fetch_rss("https://www.moneycontrol.com/rss/buzzingstocks.xml")
            matched = [t for t in items if symbol.upper() in t.upper()]
            if matched:
                headlines.extend(matched)
                logger.info(f"stock_news {symbol}: MoneyControl RSS found {len(matched)} results")
        except Exception as e:
            logger.debug(f"stock_news MoneyControl RSS {symbol}: {e}")

    # Validate: if we have headlines, format & return
    if headlines:
        result = "\n".join(
            "📰 " + html.escape(str(h)[:87] + ("…" if len(str(h)) > 87 else ""), quote=False) + " [date unverified]"
            for h in headlines[:n]
        )
        logger.info(f"stock_news {symbol}: Returning {len(headlines[:n])} headlines")
        return result

    logger.warning("stock_news %s: no source-reported headlines; no fallback", symbol)
    return ""
