HIST_PERIOD_SWING: str = _get_env("HIST_PERIOD_SWING", "6mo")
TG_CHUNK_SIZE: int = int(_get_env("TG_CHUNK_SIZE", "3800"))   # Telegram hard limit is 4096

TIMEOUT_YAHOO: int = int(_get_env("TIMEOUT_YAHOO", "10"))
TIMEOUT_NSE: int = int(_get_env("TIMEOUT_NSE", "10"))
TIMEOUT_SCREENER: int = int(_get_env("TIMEOUT_SCREENER", "8"))
TIMEOUT_FINNHUB: int = int(_get_env("TIMEOUT_FINNHUB", "8"))

CACHE_TTL_LIVE: int = int(_get_env("CACHE_TTL_LIVE", "300"))       # 5 min
CACHE_TTL_HIST: int = int(_get_env("CACHE_TTL_HIST", "600"))       # 10 min
CACHE_TTL_FUND: int = int(_get_env("CACHE_TTL_FUND", "14400"))     # 4 h

REVENUE_MAX_MCAP_RATIO: float = float(_get_env("REVENUE_MAX_MCAP_RATIO", "5"))


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
    if WEBHOOK_URL and not _get_env("TELEGRAM_WEBHOOK_SECRET"):
        issues.append("❌ CRITICAL: TELEGRAM_WEBHOOK_SECRET missing for webhook mode")
    
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


# ────────────────────────────────────────────────────────────────
# EXPORT ALL CONFIGURATION
# ────────────────────────────────────────────────────────────────

__all__ = [
    # Telegram
    "TELEGRAM_TOKEN",
    "BRAND_NAME",
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

    # Retry & Rate Limiting
    "RETRY_MAX_ATTEMPTS",
    "RETRY_BASE_DELAY",
    "RETRY_BACKOFF",
    "RATE_LIMIT_WINDOW",
    "RATE_LIMIT_MAX_CALLS",
    
    # Functions
