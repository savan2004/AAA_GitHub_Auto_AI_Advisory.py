"""
Per-user daily LLM usage tracker with rate limiting.

Features:
  - Per-user daily usage limits (free: 50, paid: 200)
  - Automatic reset at midnight (IST)
  - Thread-safe with lock protection
  - Tier management (free/paid)
  - Usage analytics

Usage:
    from limits import can_use_llm, register_llm_usage, set_tier
    
    # Check if user can make a query
    allowed, remaining, limit = can_use_llm(user_id)
    if not allowed:
        return "Daily limit reached"
    
    # Record the usage
    register_llm_usage(user_id)
    
    # Upgrade user to paid tier
    set_tier(user_id, "paid")
"""

import os
import threading
from datetime import date
from typing import Tuple, Dict, Optional

# Thread-safe lock for concurrent access
_usage_lock = threading.Lock()

# In-memory store: { user_id: {"date": "YYYY-MM-DD", "calls": int, "tier": str} }
# Consider Redis for production with multiple instances
usage_store: Dict[int, Dict] = {}

# Tier-based daily limits
TIER_LIMITS: Dict[str, int] = {
    "free": 50,      # Free tier: 50 AI calls per day
    "paid": 200,     # Paid tier: 200 AI calls per day
    "enterprise": 1000,  # Enterprise: 1000 AI calls per day
}

# Default tier for new users
DEFAULT_TIER: str = "free"


def get_today_str() -> str:
    """
    Get today's date as ISO format string (YYYY-MM-DD).
    
    Returns:
        str: ISO format date (e.g., "2026-10-07")
    """
    return date.today().isoformat()


def can_use_llm(user_id: int) -> Tuple[bool, int, int]:
    """
    Check if a user can make an LLM query based on daily limits.
    
    Automatically resets counter at midnight.
    Thread-safe.
    
    Args:
        user_id: User's Telegram ID
        
    Returns:
        Tuple[bool, int, int]: (allowed, remaining, limit)
            - allowed: True if user hasn't hit limit
            - remaining: Number of queries remaining today
            - limit: Total daily limit for this tier
    """
    with _usage_lock:
        today = get_today_str()
        rec = usage_store.get(user_id)
        
        # New user
        if rec is None:
            usage_store[user_id] = {
                "date": today,
                "calls": 0,
                "tier": DEFAULT_TIER
            }
            lim = TIER_LIMITS[DEFAULT_TIER]
            return True, lim, lim
        
        # Roll over at midnight (new day)
        if rec["date"] != today:
            rec["date"] = today
            rec["calls"] = 0
        
        # Get limit for user's tier
        tier = rec.get("tier", DEFAULT_TIER)
        lim = TIER_LIMITS.get(tier, TIER_LIMITS[DEFAULT_TIER])
        
        # Calculate remaining
        remaining = max(0, lim - rec["calls"])
        
        return remaining > 0, remaining, lim


def register_llm_usage(user_id: int) -> None:
    """
    Register that a user made an LLM query (increment counter).
    
    Thread-safe. Resets at midnight automatically.
    
    Args:
        user_id: User's Telegram ID
    """
    with _usage_lock:
        today = get_today_str()
        rec = usage_store.get(user_id)
        
        if rec is None:
            usage_store[user_id] = {
                "date": today,
                "calls": 1,
                "tier": DEFAULT_TIER
            }
        else:
            # Reset if new day
            if rec["date"] != today:
                rec["date"] = today
                rec["calls"] = 0
            
            rec["calls"] += 1


def set_tier(user_id: int, tier: str) -> bool:
    """
    Set a user's tier (free, paid, enterprise).
    
    Thread-safe. Initializes user record if doesn't exist.
    
    Args:
        user_id: User's Telegram ID
        tier: Tier name ('free', 'paid', 'enterprise')
        
    Returns:
        bool: True if successful, False if invalid tier
    """
    if tier not in TIER_LIMITS:
        return False
    
    with _usage_lock:
        rec = usage_store.setdefault(
            user_id,
            {
                "date": get_today_str(),
                "calls": 0,
                "tier": DEFAULT_TIER
            }
        )
        rec["tier"] = tier
    
    return True


def get_usage_info(user_id: int) -> Dict:
    """
    Get comprehensive usage information for a user.
    
    Thread-safe.
    
    Args:
        user_id: User's Telegram ID
        
    Returns:
        dict: {
            'tier': 'free' | 'paid' | 'enterprise',
            'calls': int (calls made today),
            'limit': int (daily limit for tier),
            'remaining': int (calls left today),
            'date': 'YYYY-MM-DD' (today's date),
            'allowed': bool (can user make a query?)
        }
    """
    allowed, remaining, limit = can_use_llm(user_id)
    
    with _usage_lock:
        rec = usage_store.get(user_id, {})
        return {
            "tier": rec.get("tier", DEFAULT_TIER),
            "calls": rec.get("calls", 0),
            "limit": limit,
            "remaining": remaining,
            "date": rec.get("date", get_today_str()),
            "allowed": allowed,
        }


def get_daily_stats() -> Dict[str, any]:
    """
    Get aggregated daily statistics across all users.
    
    Useful for monitoring and analytics.
    
    Returns:
        dict: {
            'total_users': int,
            'active_today': int,
            'total_queries_today': int,
            'tier_breakdown': {'free': count, 'paid': count, ...},
            'average_queries_per_user': float,
        }
    """
    today = get_today_str()
    
    with _usage_lock:
        total_users = len(usage_store)
        active_today = sum(1 for r in usage_store.values() if r.get("date") == today)
        total_queries_today = sum(
            r.get("calls", 0)
            for r in usage_store.values()
            if r.get("date") == today
        )
        
        tier_breakdown = {}
        for tier in TIER_LIMITS.keys():
            tier_breakdown[tier] = sum(
                1 for r in usage_store.values()
                if r.get("tier") == tier
            )
        
        avg_queries = (
            total_queries_today / active_today
            if active_today > 0
            else 0
        )
        
        return {
            "total_users": total_users,
            "active_today": active_today,
            "total_queries_today": total_queries_today,
            "tier_breakdown": tier_breakdown,
            "average_queries_per_user": round(avg_queries, 2),
        }


def reset_user_usage(user_id: int) -> None:
    """
    Force reset a user's usage (admin function).
    
    Thread-safe.
    
    Args:
        user_id: User's Telegram ID
    """
    with _usage_lock:
        if user_id in usage_store:
            usage_store[user_id]["calls"] = 0
            usage_store[user_id]["date"] = get_today_str()


def reset_all_usage() -> None:
    """
    Force reset all users' usage (admin function).
    
    WARNING: Use only for maintenance/debugging.
    Thread-safe.
    """
    with _usage_lock:
        today = get_today_str()
        for user_id in usage_store:
            usage_store[user_id]["calls"] = 0
            usage_store[user_id]["date"] = today


def cleanup_old_records(days: int = 30) -> int:
    """
    Clean up old usage records (users not active in N days).
    
    Useful for memory optimization over time.
    Thread-safe.
    
    Args:
        days: Remove records older than N days (default 30)
        
    Returns:
        int: Number of records removed
    """
    from datetime import timedelta
    
    cutoff_date = (date.today() - timedelta(days=days)).isoformat()
    
    with _usage_lock:
        to_remove = [
            user_id
            for user_id, rec in usage_store.items()
            if rec.get("date", "") < cutoff_date
        ]
        
        for user_id in to_remove:
            del usage_store[user_id]
        
        return len(to_remove)


def format_limit_message(user_id: int) -> str:
    """
    Format a user-friendly message about their usage limits.
    
    Args:
        user_id: User's Telegram ID
        
    Returns:
        str: Formatted message for Telegram
    """
    info = get_usage_info(user_id)
    
    allowed_emoji = "✅" if info["allowed"] else "❌"
    tier_emoji = "🟢" if info["tier"] == "free" else "🔵" if info["tier"] == "paid" else "⭐"
    
    return (
        f"{tier_emoji} **Tier:** {info['tier'].capitalize()}\n"
        f"📊 **Today's Queries:** {info['calls']}/{info['limit']}\n"
        f"⏳ **Remaining:** {info['remaining']}\n"
        f"{allowed_emoji} **Status:** "
        f"{'✅ Can query' if info['allowed'] else '❌ Limit reached'}\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# MIGRATION HELPER (for upgrading from old limits.py)
# ─────────────────────────────────────────────────────────────────────────────

def migrate_from_dict(old_data: Dict) -> None:
    """
    Migrate usage data from old format if needed.
    
    Args:
        old_data: Old usage_store format dictionary
    """
    global usage_store
    
    if old_data:
        with _usage_lock:
            usage_store.update(old_data)


__all__ = [
    "TIER_LIMITS",
    "DEFAULT_TIER",
    "can_use_llm",
    "register_llm_usage",
    "set_tier",
    "get_usage_info",
    "get_daily_stats",
    "reset_user_usage",
    "reset_all_usage",
    "cleanup_old_records",
    "format_limit_message",
    "migrate_from_dict",
]
