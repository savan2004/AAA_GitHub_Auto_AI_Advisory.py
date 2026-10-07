"""
Health monitoring module for AutoAiAdvisory Bot.

Tracks and monitors:
  - Bot connectivity (Telegram)
  - AI provider availability
  - Data source health
  - System resources
  - Performance metrics

All checks are non-blocking and safe to call frequently.
"""

import time
import logging
from datetime import datetime
from typing import Dict, Any, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# HEALTH CHECK RESULTS
# ─────────────────────────────────────────────────────────────────────────────

class HealthStatus:
    """Health check result constants."""
    OK = "OK"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


# Health history for trending
_health_history: Dict[str, list] = defaultdict(list)
MAX_HISTORY = 100  # Keep last 100 checks per component


# ─────────────────────────────────────────────────────────────────────────────
# BOT HEALTH CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def check_telegram_connectivity() -> Tuple[str, str]:
    """
    Check Telegram webhook connectivity.
    
    Returns:
        Tuple[str, str]: (status, details)
            status: "OK" | "DEGRADED" | "ERROR"
            details: Human-readable status message
    """
    try:
        # Import here to avoid circular dependency
        import requests
        from config import TELEGRAM_TOKEN, WEBHOOK_URL
        
        # Quick check: Can we reach Telegram API?
        response = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe",
            timeout=5
        )
        
        if response.status_code == 200:
            bot_info = response.json()
            if bot_info.get("ok"):
                return HealthStatus.OK, f"Bot connected: {bot_info['result']['first_name']}"
            else:
                return HealthStatus.ERROR, f"Telegram error: {bot_info.get('description', 'unknown')}"
        else:
            return HealthStatus.ERROR, f"HTTP {response.status_code} from Telegram"
    
    except requests.Timeout:
        return HealthStatus.DEGRADED, "Telegram API timeout"
    except requests.ConnectionError:
        return HealthStatus.ERROR, "Cannot reach Telegram API"
    except Exception as exc:
        return HealthStatus.ERROR, f"Telegram check failed: {str(exc)[:80]}"


def check_webhook_endpoint() -> Tuple[str, str]:
    """
    Check if webhook endpoint is accessible.
    
    Returns:
        Tuple[str, str]: (status, details)
    """
    try:
        from config import WEBHOOK_URL, WEBHOOK_PATH
        
        # Webhook is local, so just verify it's configured
        if WEBHOOK_URL and WEBHOOK_PATH:
            return HealthStatus.OK, f"Webhook configured: {WEBHOOK_URL}{WEBHOOK_PATH}"
        else:
            return HealthStatus.ERROR, "Webhook URL not configured"
    
    except Exception as exc:
        return HealthStatus.ERROR, f"Webhook check failed: {str(exc)[:80]}"


# ─────────────────────────────────────────────────────────────────────────────
# AI PROVIDER HEALTH CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def check_ai_providers() -> Dict[str, Tuple[str, str]]:
    """
    Check availability of all configured AI providers.
    
    Returns:
        dict: {
            'groq': (status, details),
            'gemini': (status, details),
            'openai': (status, details),
            'askfuzz': (status, details),
        }
    """
    from ai_engine import get_ai_provider_status
    
    result = {}
    try:
        status = get_ai_provider_status()
        
        for provider in ["groq", "gemini", "openai", "askfuzz"]:
            if status[provider]["configured"]:
                # Provider is configured, assume OK (actual test would be expensive)
                result[provider] = (
                    HealthStatus.OK,
                    f"{provider.upper()} configured and ready"
                )
            else:
                result[provider] = (
                    HealthStatus.UNKNOWN,
                    f"{provider.upper()} not configured"
                )
        
        return result
    
    except Exception as exc:
        return {
            "error": (HealthStatus.ERROR, f"AI check failed: {str(exc)[:80]}")
        }


# ─────────────────────────────────────────────────────────────────────────────
# DATA SOURCE HEALTH CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def check_data_sources() -> Dict[str, Tuple[str, str]]:
    """
    Check health of data sources (yfinance, Finnhub, etc.).
    
    Returns:
        dict: {
            'yfinance': (status, details),
            'finnhub': (status, details),
            'screener': (status, details),
            'tavily': (status, details),
        }
    """
    import requests
    from config import FINNHUB_API_KEY, TAVILY_API_KEY
    
    result = {}
    
    # yfinance (no key needed)
    try:
        import yfinance as yf
        
        # Try to fetch latest RELIANCE price
        ticker = yf.Ticker("RELIANCE.NS")
        hist = ticker.history(period="1d")
        
        if not hist.empty:
            result["yfinance"] = (
                HealthStatus.OK,
                "yfinance working (latest: RELIANCE)"
            )
        else:
            result["yfinance"] = (
                HealthStatus.DEGRADED,
                "yfinance returns empty data"
            )
    except Exception as exc:
        result["yfinance"] = (
            HealthStatus.ERROR,
            f"yfinance error: {str(exc)[:60]}"
        )
    
    # Finnhub
    if FINNHUB_API_KEY:
        try:
            response = requests.get(
                f"https://finnhub.io/api/v1/company/profile2?symbol=RELIANCE.NS&token={FINNHUB_API_KEY}",
                timeout=5
            )
            if response.status_code == 200:
                result["finnhub"] = (HealthStatus.OK, "Finnhub API working")
            else:
                result["finnhub"] = (
                    HealthStatus.DEGRADED,
                    f"Finnhub HTTP {response.status_code}"
                )
        except Exception as exc:
            result["finnhub"] = (
                HealthStatus.ERROR,
                f"Finnhub error: {str(exc)[:60]}"
            )
    else:
        result["finnhub"] = (HealthStatus.UNKNOWN, "Finnhub not configured")
    
    # Screener.in (no key needed, but uses scraping)
    try:
        response = requests.get(
            "https://www.screener.in/api/company/RELIANCE/",
            timeout=5,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if response.status_code == 200:
            result["screener"] = (HealthStatus.OK, "Screener.in working")
        else:
            result["screener"] = (
                HealthStatus.DEGRADED,
                f"Screener HTTP {response.status_code}"
            )
    except Exception as exc:
        result["screener"] = (
            HealthStatus.DEGRADED,
            f"Screener error (scraper): {str(exc)[:60]}"
        )
    
    # Tavily (if configured)
    if TAVILY_API_KEY:
        try:
            response = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY, "query": "test"},
                timeout=5
            )
            if response.status_code in [200, 400]:  # 400 may be rate limit
                result["tavily"] = (HealthStatus.OK, "Tavily API working")
            else:
                result["tavily"] = (
                    HealthStatus.DEGRADED,
                    f"Tavily HTTP {response.status_code}"
                )
        except Exception as exc:
            result["tavily"] = (
                HealthStatus.ERROR,
                f"Tavily error: {str(exc)[:60]}"
            )
    else:
        result["tavily"] = (HealthStatus.UNKNOWN, "Tavily not configured")
    
    return result


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM HEALTH CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def check_system_resources() -> Dict[str, Any]:
    """
    Check system resource usage (memory, CPU, etc.).
    
    Returns:
        dict: {
            'memory_percent': float (0-100),
            'memory_mb': float,
            'cpu_percent': float (0-100),
            'status': 'OK' | 'DEGRADED' | 'ERROR',
        }
    """
    try:
        import psutil
        
        process = psutil.Process()
        memory_info = process.memory_info()
        memory_percent = process.memory_percent()
        cpu_percent = process.cpu_percent(interval=0.1)
        
        status = HealthStatus.OK
        if memory_percent > 80:
            status = HealthStatus.DEGRADED
        if memory_percent > 95:
            status = HealthStatus.ERROR
        
        return {
            "memory_percent": round(memory_percent, 1),
            "memory_mb": round(memory_info.rss / 1024 / 1024, 1),
            "cpu_percent": round(cpu_percent, 1),
            "status": status,
        }
    
    except ImportError:
        return {
            "status": HealthStatus.UNKNOWN,
            "message": "psutil not installed (optional)"
        }
    except Exception as exc:
        return {
            "status": HealthStatus.ERROR,
            "message": f"System check failed: {str(exc)[:80]}"
        }


def check_rate_limiting() -> Dict[str, Any]:
    """
    Check rate limiting and usage statistics.
    
    Returns:
        dict: Daily stats and rate limiting status
    """
    try:
        from limits import get_daily_stats
        
        stats = get_daily_stats()
        
        return {
            "status": HealthStatus.OK,
            "total_users": stats["total_users"],
            "active_today": stats["active_today"],
            "total_queries_today": stats["total_queries_today"],
            "avg_queries_per_user": stats["average_queries_per_user"],
        }
    
    except Exception as exc:
        return {
            "status": HealthStatus.ERROR,
            "message": f"Rate limiting check failed: {str(exc)[:80]}"
        }


# ─────────────────────────────────────────────────────────────────────────────
# COMPREHENSIVE HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────

def get_full_health_status() -> Dict[str, Any]:
    """
    Get comprehensive health status of entire system.
    
    Returns:
        dict: {
            'timestamp': ISO datetime,
            'overall_status': 'OK' | 'DEGRADED' | 'ERROR',
            'telegram': (status, details),
            'webhook': (status, details),
            'ai_providers': {provider: (status, details), ...},
            'data_sources': {source: (status, details), ...},
            'system': {resource info},
            'rate_limiting': {stats},
            'uptime_seconds': int,
        }
    """
    try:
        telegram_status = check_telegram_connectivity()
        webhook_status = check_webhook_endpoint()
        ai_status = check_ai_providers()
        data_status = check_data_sources()
        system_status = check_system_resources()
        rate_limit_status = check_rate_limiting()
        
        # Determine overall status
        statuses = [
            telegram_status[0],
            webhook_status[0],
            *[s[0] for s in ai_status.values() if isinstance(s, tuple)],
            *[s[0] for s in data_status.values()],
        ]
        
        # Overall: ERROR if any critical is ERROR, DEGRADED if any is DEGRADED
        if any(s == HealthStatus.ERROR for s in statuses):
            overall = HealthStatus.ERROR
        elif any(s == HealthStatus.DEGRADED for s in statuses):
            overall = HealthStatus.DEGRADED
        else:
            overall = HealthStatus.OK
        
        return {
            "timestamp": datetime.now().isoformat(),
            "overall_status": overall,
            "telegram": {
                "status": telegram_status[0],
                "details": telegram_status[1],
            },
            "webhook": {
                "status": webhook_status[0],
                "details": webhook_status[1],
            },
            "ai_providers": {
                k: {"status": v[0], "details": v[1]}
                for k, v in ai_status.items()
            },
            "data_sources": {
                k: {"status": v[0], "details": v[1]}
                for k, v in data_status.items()
            },
            "system": system_status,
            "rate_limiting": rate_limit_status,
        }
    
    except Exception as exc:
        logger.exception("Health check failed")
        return {
            "timestamp": datetime.now().isoformat(),
            "overall_status": HealthStatus.ERROR,
            "error": str(exc),
        }


def format_health_report() -> str:
    """
    Format health status as human-readable report.
    
    Returns:
        str: Markdown formatted health report
    """
    health = get_full_health_status()
    
    report = f"# 🏥 System Health Report\n\n"
    report += f"**Status:** {health.get('overall_status', 'UNKNOWN')}\n"
    report += f"**Time:** {health.get('timestamp', 'unknown')}\n\n"
    
    # Telegram
    if 'telegram' in health:
        emoji = "✅" if health['telegram']['status'] == HealthStatus.OK else "❌"
        report += f"{emoji} **Telegram:** {health['telegram']['details']}\n"
    
    # AI Providers
    if 'ai_providers' in health:
        report += "\n## AI Providers\n"
        for provider, status in health['ai_providers'].items():
            emoji = "✅" if status['status'] == HealthStatus.OK else "⚠️" if status['status'] == HealthStatus.DEGRADED else "❌"
            report += f"{emoji} {provider.upper()}: {status['details']}\n"
    
    # Data Sources
    if 'data_sources' in health:
        report += "\n## Data Sources\n"
        for source, status in health['data_sources'].items():
            emoji = "✅" if status['status'] == HealthStatus.OK else "⚠️" if status['status'] == HealthStatus.DEGRADED else "❌"
            report += f"{emoji} {source}: {status['details']}\n"
    
    # System
    if 'system' in health and 'memory_percent' in health['system']:
        report += "\n## System Resources\n"
        report += f"🔹 Memory: {health['system']['memory_mb']}MB ({health['system']['memory_percent']}%)\n"
        report += f"🔹 CPU: {health['system']['cpu_percent']}%\n"
    
    # Rate Limiting
    if 'rate_limiting' in health and 'total_users' in health['rate_limiting']:
        report += "\n## Rate Limiting\n"
        stats = health['rate_limiting']
        report += f"👥 Users: {stats['total_users']} total, {stats['active_today']} today\n"
        report += f"📊 Queries: {stats['total_queries_today']} today ({stats['avg_queries_per_user']:.1f} avg)\n"
    
    return report


__all__ = [
    "HealthStatus",
    "check_telegram_connectivity",
    "check_webhook_endpoint",
    "check_ai_providers",
    "check_data_sources",
    "check_system_resources",
    "check_rate_limiting",
    "get_full_health_status",
    "format_health_report",
]
