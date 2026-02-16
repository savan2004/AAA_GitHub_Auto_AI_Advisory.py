# limits.py
import time
from datetime import date
from typing import Dict, Tuple

# In-memory store: user_id -> {"date": str, "calls": int, "tier": str}
usage_store: Dict[int, Dict] = {}

def get_today_str() -> str:
    return date.today().isoformat()

def can_use_llm(user_id: int) -> Tuple[bool, int, int]:
    """
    Returns (allowed, remaining, daily_limit)
    """
    from config import TIER_LIMITS   # import here to avoid circular imports

    record = usage_store.get(user_id)
    today = get_today_str()

    if record is None:
        # First use – assume free tier
        tier = "free"
        limit = TIER_LIMITS[tier]
        usage_store[user_id] = {"date": today, "calls": 0, "tier": tier}
        return True, limit, limit

    if record["date"] != today:
        # New day – reset
        record["date"] = today
        record["calls"] = 0
        limit = TIER_LIMITS[record["tier"]]
        return True, limit, limit

    limit = TIER_LIMITS[record["tier"]]
    remaining = limit - record["calls"]
    allowed = remaining > 0
    return allowed, remaining, limit

def register_llm_usage(user_id: int) -> None:
    """Call after a successful LLM API call."""
    record = usage_store.get(user_id)
    if record:
        record["calls"] += 1
    else:
        # Fallback – should not happen if can_use_llm was called first
        from config import TIER_LIMITS
        usage_store[user_id] = {"date": get_today_str(), "calls": 1, "tier": "free"}