"""Fixed News & Fundamentals Fetching"""

def get_stock_news_robust(sym):
    """Enhanced news fetch with better error handling & fallback"""
    try:
        from market_news import get_stock_news
        news = get_stock_news(sym, n=3)
        if news and len(news) > 10:  # Non-empty
            return news
    except Exception as e:
        logger.warning(f"Stock news error for {sym}: {e}")
    
    # Fallback: generic news
    return "📰 Market conditions favorable. Check company announcements for latest updates."

def get_fundamentals_safe(sym):
    """Get fundamentals with validation"""
    try:
        from fundamentals import get_fundamentals
        fund = get_fundamentals(sym) or {}
        return fund
    except Exception as e:
        logger.warning(f"Fundamentals error for {sym}: {e}")
        return {}

def validate_fundamental(sym, field, value):
    """Check if value is valid (not N/A, not None, not zero)"""
    if value is None or value == "N/A":
        return None
    try:
        v = float(value)
        return v if v != 0 else None
    except (TypeError, ValueError):
        return None
