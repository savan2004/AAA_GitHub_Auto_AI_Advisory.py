# config.py — Centralised configuration

import os

# ── Per-user daily AI call limits ────────────────────────────────────────────
TIER_LIMITS = {
    "free": 50,
    "paid": 200,
}

# ── Cache TTLs (seconds) ──────────────────────────────────────────────────────
FRESHNESS_SECONDS  = 3600   # history item freshness
CACHE_TTL_LIVE     = 300    # 5 min  — live prices
CACHE_TTL_FUND     = 3600   # 60 min — fundamentals

# ── AI defaults ───────────────────────────────────────────────────────────────
DEFAULT_MAX_TOKENS = 500

# ── Bot version ───────────────────────────────────────────────────────────────
BOT_VERSION = "5.0_fixed"

# ── AskFuzz AI (Indian market focused) ───────────────────────────────────────
# Set ASKFUZZ_API_KEY env var when the public API becomes available.
# Currently the service is web-only; this key will activate the integration.
ASKFUZZ_API_KEY = os.getenv("ASKFUZZ_API_KEY", "").strip()
