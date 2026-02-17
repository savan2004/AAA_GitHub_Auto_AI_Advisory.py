# config.py - Configuration constants

# Tier limits for AI calls
TIER_LIMITS = {
    "free": 50,
    "paid": 200,
}

# Cache freshness (seconds)
FRESHNESS_SECONDS = 3600  # 1 hour

# Market hours (IST)
MARKET_START_HOUR = 9
MARKET_START_MINUTE = 15
MARKET_END_HOUR = 15
MARKET_END_MINUTE = 30

# Update intervals (seconds)
MARKET_UPDATE_INTERVAL = 1800  # 30 minutes
NEWS_UPDATE_INTERVAL = 3600    # 1 hour