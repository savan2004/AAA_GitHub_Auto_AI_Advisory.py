"""
Configuration module for AutoAiAdvisory Bot.

All configuration is loaded from environment variables (secure, 12-factor app compliant).
No secrets should be hardcoded in this file.

Environment Variables Required:
    TELEGRAM_TOKEN          - Bot authentication token from @BotFather
    WEBHOOK_URL            - Your Render/hosting service HTTPS endpoint

Environment Variables Optional (AI Providers):
    GROQ_API_KEY           - GROQ LLM (recommended primary)
    GEMINI_API_KEY         - Google Gemini (or GOOGLE_API_KEY)
    OPENAI_API_KEY         - OpenAI GPT-4o-mini (or OPENAI_KEY)
    ASKFUZZ_API_KEY        - AskFuzz India-focused finance AI

Environment Variables Optional (Data Sources):
    FINNHUB_API_KEY        - Stock fundamentals + news
    ALPHA_VANTAGE_KEY      - Time-series data + news
    TAVILY_API_KEY         - Live news search

Infrastructure:
    PORT                   - Server port (default: 8000)
    PYTHON_VERSION         - Python version (default: 3.11.9)
    LOG_LEVEL              - Logging level (default: INFO)
    DEBUG_MODE             - Enable debug logging (default: false)
"""

import os
import logging
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _get_env(key: str, default: str = "", required: bool = False) -> str:
    """
    Get environment variable with optional fallbacks.
    
    Args:
        key: Environment variable name
        default: Default value if not found
        required: If True, raise error if not found
        
    Returns:
        str: Environment variable value (stripped)
        
    Raises:
        ValueError: If required and not found
    """
    value = os.getenv(key, default).strip()
    if required and not value:
        raise ValueError(f"Required environment variable not set: {key}")
    return value


def _get_env_with_fallbacks(primary: str, *fallbacks: str, default: str = "") -> str:
    """
    Get environment variable with fallback names.
    
    Args:
        primary: Primary env var name
        *fallbacks: Alternative env var names
        default: Default value if none found
        
    Returns:
        str: Value of first found env var
    """
    value = _get_env(primary, "")
    if value:
        return value
    
    for alt in fallbacks:
        value = _get_env(alt, "")
        if value:
            return value
    
    return default


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN: str = _get_env("TELEGRAM_TOKEN", required=True)
"""
Bot token from @BotFather.

To get:
  1. Open Telegram, find @BotFather
  2. Send /newbot
  3. Follow prompts to create bot
  4. Copy token
  5. Set as TELEGRAM_TOKEN environment variable

Example: 6372193741:AAHx8wHwHwHwHw...
"""

WEBHOOK_URL: str = _get_env("WEBHOOK_URL", required=True)
"""
Your Render/hosting service HTTPS endpoint.

Format: https://your-app-name.onrender.com (no trailing slash)

Telegram will POST updates to: {WEBHOOK_URL}/webhook

To set in Render:
  1. Deploy app to Render
  2. Copy service URL from dashboard
  3. Add to Render Environment Variables
"""

WEBHOOK_PATH: str = "/webhook"
"""
Path for webhook endpoint. Full URL: {WEBHOOK_URL}{WEBHOOK_PATH}
"""


# ─────────────────────────────────────────────────────────────────────────────
# AI PROVIDER CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

GROQ_API_KEY: str = _get_env("GROQ_API_KEY")
"""
GROQ API key (primary AI provider - free tier recommended).

To get:
  1. Go to https://console.groq.com
  2. Sign up / Log in
  3. Create API key
  4. Set as GROQ_API_KEY environment variable

Free tier: Unlimited calls (rate limited to ~100 req/min)
Recommended as primary provider (fast, reliable, free)

Example: gsk_sR...
"""

GEMINI_API_KEY: str = _get_env_with_fallbacks(
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY"
)
"""
Google Gemini API key (fallback AI provider).

To get:
  1. Go to https://aistudio.google.com
  2. Sign in with Google account
  3. Create API key
  4. Set as GEMINI_API_KEY (or GOOGLE_API_KEY)

Free tier: 60 calls/min
Reliable secondary fallback provider

Example: AIza...
"""

OPENAI_KEY: str = _get_env_with_fallbacks(
    "OPENAI_API_KEY",
    "OPENAI_KEY"
)
"""
OpenAI API key (tertiary AI provider - premium, costs money).

To get:
  1. Go to https://platform.openai.com
  2. Create account / log in
  3. Create API key
  4. Set billing limit to prevent surprise charges
  5. Set as OPENAI_KEY (or OPENAI_API_KEY)

Paid: ~$0.15 per 1M input tokens, $0.60 per 1M output tokens
Use as last resort (higher cost but best quality)

Example: sk-proj-...

WARNING: Set monthly spend limit in OpenAI dashboard!
"""

ASKFUZZ_API_KEY: str = _get_env("ASKFUZZ_API_KEY")
"""
AskFuzz API key (India-focused financial AI - optional).

To get:
  1. Go to https://askfuzz.ai
  2. Sign up / log in
  3. Create API key
  4. Set as ASKFUZZ_API_KEY

Free tier: Limited calls/month
Specialized for Indian stock market analysis

Only used if explicitly configured (won't error if missing)

Example: ask_...
"""

# ─────────────────────────────────────────────────────────────────────────────
# DATA SOURCE CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

FINNHUB_API_KEY: str = _get_env("FINNHUB_API_KEY")
"""
Finnhub API key (stock fundamentals + news).

To get:
  1. Go to https://finnhub.io
  2. Sign up (free tier available)
  3. Create API key
  4. Set as FINNHUB_API_KEY

Free tier: 1000 API calls/month
Used for: P/E, ROE, D/E, Market Cap, News

Optional but recommended for better fundamentals data
"""

ALPHA_VANTAGE_KEY: str = _get_env("ALPHA_VANTAGE_KEY")
"""
Alpha Vantage API key (time-series data + news fallback).

To get:
  1. Go to https://www.alphavantage.co
  2. Get free API key (instant)
  3. Set as ALPHA_VANTAGE_KEY

Free tier: 5 requests/min, 500/day
Used as fallback for: technical data, news

Optional, used only as fallback
"""

TAVILY_API_KEY: str = _get_env("TAVILY_API_KEY")
"""
Tavily API key (live news search).

To get:
  1. Go to https://tavily.com
  2. Sign up / log in
  3. Create API key
  4. Set as TAVILY_API_KEY

Free tier: 100 searches/month
Used for: Real-time stock news and headlines

Optional, used for live market news
"""


# ─────────────────────────────────────────────────────────────────────────────
# INFRASTRUCTURE CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

PORT: int = int(_get_env("PORT", "8000"))
"""
Web server port.

Default: 8000
Render typically runs on 8000
"""

HOST: str = _get_env("HOST", "0.0.0.0")
"""
Web server host/bind address.

Default: 0.0.0.0 (listen on all interfaces)
Use 127.0.0.1 for localhost only (development)
"""

PYTHON_VERSION: str = _get_env("PYTHON_VERSION", "3.11.9")
"""
Python version (informational only, set in Render build settings).

Recommended: 3.11.9 or later
Used for: Rendering, dependency compatibility
"""

LOG_LEVEL: str = _get_env("LOG_LEVEL", "INFO")
"""
Logging level for console output.

Options: DEBUG, INFO, WARNING, ERROR, CRITICAL
Default: INFO

Set to DEBUG for troubleshooting
"""

DEBUG_MODE: bool = _get_env("DEBUG_MODE", "false").lower() == "true"
"""
Enable debug mode (more verbose logging, stack traces).

Default: false
Set to true only for development/troubleshooting
"""


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE FLAGS & LIMITS
# ─────────────────────────────────────────────────────────────────────────────

ENABLE_OPENAI: bool = bool(OPENAI_KEY) and _get_env("ENABLE_OPENAI", "true").lower() == "true"
"""
Enable OpenAI as fallback provider.

Default: true (if OPENAI_KEY is set)
Set to false to disable OpenAI (cost control)
"""

ENABLE_ASKFUZZ: bool = bool(ASKFUZZ_API_KEY) and _get_env("ENABLE_ASKFUZZ", "true").lower() == "true"
"""
Enable AskFuzz as fallback provider.

Default: true (if ASKFUZZ_API_KEY is set)
Set to false to disable AskFuzz
"""

CACHE_TTL_PRICES: int = int(_get_env("CACHE_TTL_PRICES", "300"))
"""
Cache TTL for live stock prices (seconds).

Default: 300 (5 minutes)
Higher = fewer API calls, but more stale data
"""

CACHE_TTL_FUNDAMENTALS: int = int(_get_env("CACHE_TTL_FUNDAMENTALS", "86400"))
"""
Cache TTL for fundamentals (P/E, ROE, etc.) (seconds).

Default: 86400 (24 hours)
Fundamentals change slowly, safe to cache long
"""

CACHE_TTL_NEWS: int = int(_get_env("CACHE_TTL_NEWS", "3600"))
"""
Cache TTL for news articles (seconds).

Default: 3600 (1 hour)
News should be relatively fresh
"""

MAX_USERS_PER_DAY: int = int(_get_env("MAX_USERS_PER_DAY", "1000"))
"""
Maximum unique users per day (for analytics/limiting).

Default: 1000
Set higher to allow more users
"""


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION & STARTUP CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def validate_configuration() -> tuple[bool, list[str]]:
    """
    Validate that all required configuration is set correctly.
    
    Returns:
        tuple[bool, list[str]]: (is_valid, [list of warnings/errors])
    """
    issues = []
    
    # Check required variables
    if not TELEGRAM_TOKEN:
        issues.append("❌ CRITICAL: TELEGRAM_TOKEN not set")
    if not WEBHOOK_URL:
        issues.append("❌ CRITICAL: WEBHOOK_URL not set")
    
    # Check AI providers
    ai_providers = [GROQ_API_KEY, GEMINI_API_KEY, OPENAI_KEY, ASKFUZZ_API_KEY]
    if not any(ai_providers):
        issues.append("⚠️ WARNING: No AI provider configured (GROQ/Gemini/OpenAI/AskFuzz)")
    else:
        if GROQ_API_KEY:
            issues.append("✅ GROQ configured")
        if GEMINI_API_KEY:
            issues.append("✅ Gemini configured")
        if OPENAI_KEY:
            issues.append("⚠️ OpenAI configured (costs money)")
        if ASKFUZZ_API_KEY:
            issues.append("✅ AskFuzz configured")
    
    # Check data sources
    if not FINNHUB_API_KEY:
        issues.append("⚠️ WARNING: FINNHUB_API_KEY not set (fundamentals may be limited)")
    if not TAVILY_API_KEY:
        issues.append("⚠️ WARNING: TAVILY_API_KEY not set (news will use fallback)")
    
    # Check infrastructure
    if PORT < 1 or PORT > 65535:
        issues.append(f"❌ ERROR: Invalid PORT value ({PORT})")
    
    is_valid = not any(s.startswith("❌") for s in issues)
    return is_valid, issues


def print_configuration_status() -> None:
    """Print configuration status to logs."""
    logger = logging.getLogger(__name__)
    is_valid, issues = validate_configuration()
    
    logger.info("=" * 80)
    logger.info("CONFIGURATION STATUS")
    logger.info("=" * 80)
    for issue in issues:
        if "❌" in issue:
            logger.error(issue)
        elif "⚠️" in issue:
            logger.warning(issue)
        else:
            logger.info(issue)
    logger.info("=" * 80)
    
    if not is_valid:
        logger.error("Configuration validation FAILED. Fix issues above.")
        raise RuntimeError("Configuration validation failed")
    else:
        logger.info("✅ Configuration validation PASSED")


# ─────────────────────────────────────────────────────────────────────────────
# EXPORT ALL CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    # Telegram
    "TELEGRAM_TOKEN",
    "WEBHOOK_URL",
    "WEBHOOK_PATH",
    "HOST",
    "PORT",
    
    # AI Providers
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_KEY",
    "ASKFUZZ_API_KEY",
    "ENABLE_OPENAI",
    "ENABLE_ASKFUZZ",
    
    # Data Sources
    "FINNHUB_API_KEY",
    "ALPHA_VANTAGE_KEY",
    "TAVILY_API_KEY",
    
    # Infrastructure
    "PYTHON_VERSION",
    "LOG_LEVEL",
    "DEBUG_MODE",
    
    # Caching & Limits
    "CACHE_TTL_PRICES",
    "CACHE_TTL_FUNDAMENTALS",
    "CACHE_TTL_NEWS",
    "MAX_USERS_PER_DAY",
    
    # Functions
    "validate_configuration",
    "print_configuration_status",
]
