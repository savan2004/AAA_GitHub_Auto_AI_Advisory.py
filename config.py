"""
config.py — Centralised Configuration v2.0
All hardcoded values, TTLs, limits, and constants live here.
Copilot Fix #3: No more scattered magic numbers across modules.
"""
import os

# ── Bot ────────────────────────────────────────────────────────────────────────
BOT_VERSION     = "5.3_copilot_fixed"
LOG_LEVEL       = os.getenv("LOG_LEVEL", "INFO")

# ── Tier limits (daily AI calls per user) ─────────────────────────────────────
TIER_LIMITS: dict = {"free": 50, "paid": 200}

# ── Cache TTLs (seconds) ──────────────────────────────────────────────────────
CACHE_TTL_LIVE      = int(os.getenv("CACHE_TTL_LIVE",  "300"))   # 5 min  — live price
CACHE_TTL_FUND      = int(os.getenv("CACHE_TTL_FUND",  "14400")) # 4 hr   — fundamentals
CACHE_TTL_CONTEXT   = int(os.getenv("CACHE_TTL_CTX",   "300"))   # 5 min  — AI market context
CACHE_TTL_NEWS      = int(os.getenv("CACHE_TTL_NEWS",  "1800"))  # 30 min — news
CACHE_TTL_HIST      = int(os.getenv("CACHE_TTL_HIST",  "3600"))  # 1 hr   — price history
CACHE_TTL_NSE_PE    = int(os.getenv("CACHE_TTL_PE",    "3600"))  # 1 hr   — Nifty PE

# ── AI defaults ───────────────────────────────────────────────────────────────
DEFAULT_MAX_TOKENS      = int(os.getenv("MAX_TOKENS", "500"))
AI_TEMPERATURE          = float(os.getenv("AI_TEMP",  "0.1"))   # Low = strict format
AI_CHAT_MAX_TOKENS      = int(os.getenv("CHAT_TOKENS","600"))
CHAT_HISTORY_MAX_TURNS  = 10     # per user

# ── HTTP timeouts (seconds) ──────────────────────────────────────────────────
TIMEOUT_GROQ        = int(os.getenv("TIMEOUT_GROQ",    "15"))
TIMEOUT_GEMINI      = int(os.getenv("TIMEOUT_GEMINI",  "15"))
TIMEOUT_OPENAI      = int(os.getenv("TIMEOUT_OPENAI",  "15"))
TIMEOUT_ASKFUZZ     = int(os.getenv("TIMEOUT_ASKFUZZ",  "8"))
TIMEOUT_YAHOO       = int(os.getenv("TIMEOUT_YAHOO",   "12"))
TIMEOUT_NSE         = int(os.getenv("TIMEOUT_NSE",      "8"))
TIMEOUT_SCREENER    = int(os.getenv("TIMEOUT_SCREENER", "8"))
TIMEOUT_FINNHUB     = int(os.getenv("TIMEOUT_FINNHUB",  "6"))
TIMEOUT_TAVILY      = int(os.getenv("TIMEOUT_TAVILY",   "8"))
TIMEOUT_RSS         = int(os.getenv("TIMEOUT_RSS",       "6"))

# ── Retry policy ──────────────────────────────────────────────────────────────
RETRY_MAX_ATTEMPTS  = int(os.getenv("RETRY_MAX",   "3"))
RETRY_BASE_DELAY    = float(os.getenv("RETRY_BASE","1.0"))   # seconds
RETRY_BACKOFF       = float(os.getenv("RETRY_BACKOFF","2.0"))# multiplier

# ── Rate limits ───────────────────────────────────────────────────────────────
YAHOO_MIN_INTERVAL  = float(os.getenv("YAHOO_INTERVAL","1.5"))  # seconds between Yahoo calls
RATE_LIMIT_WINDOW   = int(os.getenv("RATE_WINDOW",   "60"))     # seconds
RATE_LIMIT_MAX_CALLS= int(os.getenv("RATE_MAX_CALLS","30"))     # per window per user

# ── Data ──────────────────────────────────────────────────────────────────────
NEWS_LOOKBACK_DAYS  = int(os.getenv("NEWS_DAYS",     "30"))
RSI_PERIOD          = 14
MACD_FAST           = 12
MACD_SLOW           = 26
MACD_SIGNAL         = 9
ATR_PERIOD          = 14
ADX_PERIOD          = 14
HIST_PERIOD_ADV     = "1y"      # advisory card history
HIST_PERIOD_SCAN    = "6mo"     # screener history (was 3mo — too short for RSI)
HIST_PERIOD_SWING   = "1y"      # swing scan history

# ── Telegram ──────────────────────────────────────────────────────────────────
TG_MAX_MSG_CHARS    = 4000      # Telegram limit is 4096 — leave margin
TG_CHUNK_SIZE       = 3800      # split messages at this length

# ── Nifty PE valuation benchmarks ────────────────────────────────────────────
NIFTY_PE_AVG_10Y    = 21.0      # 10-year historical average
NIFTY_PE_EXPENSIVE  = 24.0
NIFTY_PE_FAIR_HI    = 22.0
NIFTY_PE_FAIR_LO    = 19.0

# ── AskFuzz ───────────────────────────────────────────────────────────────────
ASKFUZZ_API_KEY     = os.getenv("ASKFUZZ_API_KEY", "").strip()
ASKFUZZ_ENDPOINT    = "https://api.askfuzz.ai/v1/query"

# ── Revenue sanity guard ──────────────────────────────────────────────────────
REVENUE_MAX_MCAP_RATIO = 5.0    # revenue > 5× mcap = likely data error
