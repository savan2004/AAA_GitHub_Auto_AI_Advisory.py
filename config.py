import os

class Config:
    """Centralized configuration with environment variables for security."""
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
    GEMINI_KEY = os.getenv("GEMINI_KEY", "YOUR_GEMINI_KEY")
    OPENAI_KEY = os.getenv("OPENAI_KEY", "YOUR_OPENAI_KEY")
    ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "YOUR_ALPHA_VANTAGE_KEY")
    NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "YOUR_NEWSAPI_KEY")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 123456789))
    FREE_QUERIES_PER_DAY = int(os.getenv("FREE_QUERIES_PER_DAY", 5))
    PREMIUM_PRICE = float(os.getenv("PREMIUM_PRICE", 99.0))
    CACHE_DURATION = 300
    MAX_RETRIES = 3
    TIMEOUT = 30