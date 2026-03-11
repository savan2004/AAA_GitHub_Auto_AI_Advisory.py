import os
from datetime import date
from typing import Tuple, Dict

TIER_LIMITS = {
    "free": 50,
    "paid": 200
}

usage_store: Dict[int, Dict] = {}

def get_today_str() -> str:
    return date.today().isoformat()

def can_use_llm(user_id: int) -> Tuple[bool, int, int]:
    rec = usage_store.get(user_id)
    today = get_today_str()
    if rec is None:
        usage_store[user_id] = {"date": today, "calls": 0, "tier": "free"}
        lim = TIER_LIMITS["free"]
        return True, lim, lim

    if rec["date"] != today:
        rec["date"] = today
        rec["calls"] = 0

    lim = TIER_LIMITS[rec["tier"]]
    rem = lim - rec["calls"]
    return rem > 0, rem, lim

def register_llm_usage(user_id: int) -> None:
    rec = usage_store.get(user_id)
    if rec:
        rec["calls"] += 1
    else:
        usage_store[user_id] = {"date": get_today_str(), "calls": 1, "tier": "free"}
