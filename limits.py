# limits.py — Per-user daily LLM usage tracker
# FIX: file was mis-named "limits.py.py" → renamed to "limits.py"

import os
from datetime import date
from typing import Tuple, Dict

TIER_LIMITS: Dict[str, int] = {
    "free": 50,
    "paid": 200,
}

# In-memory store: { user_id: {"date": "YYYY-MM-DD", "calls": int, "tier": str} }
usage_store: Dict[int, Dict] = {}


def get_today_str() -> str:
    return date.today().isoformat()


def can_use_llm(user_id: int) -> Tuple[bool, int, int]:
    """
    Returns (allowed, remaining, limit).
    Resets counter at midnight automatically.
    """
    today = get_today_str()
    rec   = usage_store.get(user_id)

    if rec is None:
        usage_store[user_id] = {"date": today, "calls": 0, "tier": "free"}
        lim = TIER_LIMITS["free"]
        return True, lim, lim

    # Roll over at new day
    if rec["date"] != today:
        rec["date"]  = today
        rec["calls"] = 0

    lim = TIER_LIMITS.get(rec.get("tier", "free"), TIER_LIMITS["free"])
    rem = lim - rec["calls"]
    return rem > 0, rem, lim


def register_llm_usage(user_id: int) -> None:
    rec = usage_store.get(user_id)
    if rec:
        rec["calls"] += 1
    else:
        usage_store[user_id] = {"date": get_today_str(), "calls": 1, "tier": "free"}


def set_tier(user_id: int, tier: str) -> None:
    """Upgrade/downgrade a user's tier (free / paid)."""
    rec = usage_store.setdefault(user_id, {"date": get_today_str(), "calls": 0, "tier": "free"})
    if tier in TIER_LIMITS:
        rec["tier"] = tier


def get_usage_info(user_id: int) -> Dict:
    """Return current usage stats for a user."""
    allowed, remaining, limit = can_use_llm(user_id)
    rec = usage_store.get(user_id, {})
    return {
        "tier":      rec.get("tier", "free"),
        "calls":     rec.get("calls", 0),
        "limit":     limit,
        "remaining": remaining,
        "date":      rec.get("date", get_today_str()),
    }
