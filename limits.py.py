# limits.py

from datetime import date
from typing import Dict, Tuple

usage_store: Dict[int, Dict] = {}


def get_today_str() -> str:
    return date.today().isoformat()


def can_use_llm(user_id: int) -> Tuple[bool, int, int]:
    from config import TIER_LIMITS

    record = usage_store.get(user_id)
    today = get_today_str()

    if record is None:
        tier = "free"
        limit = TIER_LIMITS[tier]
        usage_store[user_id] = {"date": today, "calls": 0, "tier": tier}
        return True, limit, limit

    if record["date"] != today:
        record["date"] = today
        record["calls"] = 0
        limit = TIER_LIMITS[record["tier"]]
        return True, limit, limit

    limit = TIER_LIMITS[record["tier"]]
    remaining = limit - record["calls"]
    return remaining > 0, remaining, limit


def register_llm_usage(user_id: int) -> None:
    record = usage_store.get(user_id)
    if record:
        record["calls"] += 1
    else:
        usage_store[user_id] = {"date": get_today_str(), "calls": 1, "tier": "free"}
