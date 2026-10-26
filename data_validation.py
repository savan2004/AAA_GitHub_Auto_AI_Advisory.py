"""Safe optional data fetches. Never present fallback text as verified market news."""
import logging
import math

logger = logging.getLogger(__name__)


def get_stock_news_robust(sym):
    """Return source text when available, or empty string (not invented news)."""
    try:
        from market_news import get_stock_news
        news = get_stock_news(sym, n=3)
        if isinstance(news, str) and news.strip():
            fallback_markers = ("Market conditions favorable", "Latest market conditions",
                                "Recommend checking company announcements")
            try:
                from market_news import _STOCK_NEWS_FALLBACK
                fallback_markers += tuple(str(v) for v in _STOCK_NEWS_FALLBACK.values())
            except (ImportError, AttributeError):
                pass
            if not any(marker.lower() in news.lower() for marker in fallback_markers):
                return news
    except Exception as exc:
        logger.warning("Stock news error for %s: %s", sym, exc)
    return ""


def get_fundamentals_safe(sym):
    """A missing provider response is never replaced with synthetic ratios."""
    try:
        from fundamentals import get_fundamentals
        data = get_fundamentals(sym)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Fundamentals error for %s: %s", sym, exc)
        return {}


def validate_fundamental(sym, field, value):
    """Accept finite numbers including legitimate zero; absent values return None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).strip().replace(",", "").replace("₹", "").replace("%", "")
        if not text or text.upper() in {"N/A", "NA", "NONE", "NULL", "-"}:
            return None
        number = float(text)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        logger.debug("Invalid fundamental %s %s: %r", sym, field, value)
        return None
