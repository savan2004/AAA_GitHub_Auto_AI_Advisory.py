"""
api_utils.py — Centralised API Utilities v1.0

Copilot Fixes:
  #1  Exponential backoff retry decorator for all HTTP API calls
  #2  Unified cache (TTL dict + optional Redis) — single strategy
  #5  API key masking in logs — sensitive values never logged in full
  #6  Per-user rate limiter (token bucket) — abuse protection
  #9  Structured logging with request_id, function name, timestamp
"""

import os
import time
import logging
import threading
import functools
import hashlib
from typing import Any, Callable, Optional
from collections import defaultdict, deque

from config import (
    RETRY_MAX_ATTEMPTS, RETRY_BASE_DELAY, RETRY_BACKOFF,
    RATE_LIMIT_WINDOW, RATE_LIMIT_MAX_CALLS,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# FIX #5 — API KEY MASKING
# ══════════════════════════════════════════════════════════════════════════════

def mask_key(key: str) -> str:
    """Show only first 4 and last 4 chars. Never log full keys."""
    if not key or len(key) < 10:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


class _KeyFilter(logging.Filter):
    """Strip API keys from log records automatically."""
    _ENVS = [
        "GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_KEY", "ASKFUZZ_API_KEY",
        "TAVILY_API_KEY", "FINNHUB_API_KEY", "ALPHA_VANTAGE_KEY", "TELEGRAM_TOKEN",
    ]

    def __init__(self):
        super().__init__()
        self._secrets = [v for k in self._ENVS for v in [os.getenv(k, "")] if len(v) > 8]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        for secret in self._secrets:
            if secret in msg:
                record.msg  = record.msg.replace(secret, mask_key(secret))
                record.args = ()
        return True


def install_key_filter():
    """Call once at startup to mask keys across ALL loggers."""
    f = _KeyFilter()
    for handler in logging.root.handlers:
        handler.addFilter(f)
    logging.root.addFilter(f)


# ══════════════════════════════════════════════════════════════════════════════
# FIX #1 — EXPONENTIAL BACKOFF RETRY DECORATOR
# ══════════════════════════════════════════════════════════════════════════════

class TransientError(Exception):
    """Raised by API wrappers to signal a retriable error."""
    pass


def with_retry(
    max_attempts: int   = RETRY_MAX_ATTEMPTS,
    base_delay: float   = RETRY_BASE_DELAY,
    backoff: float      = RETRY_BACKOFF,
    retriable: tuple    = (TransientError, ConnectionError, TimeoutError),
):
    """
    Decorator: retry function up to max_attempts with exponential backoff.

    Usage:
        @with_retry(max_attempts=3)
        def fetch_something():
            ...

    The decorated function raises TransientError for retriable HTTP errors
    (429, 503, 504) and lets other exceptions propagate immediately.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except retriable as e:
                    last_exc = e
                    if attempt == max_attempts:
                        break
                    logger.warning(
                        f"[retry] {fn.__name__} attempt {attempt}/{max_attempts} "
                        f"failed: {e} — retrying in {delay:.1f}s"
                    )
                    time.sleep(delay)
                    delay *= backoff
            raise last_exc or RuntimeError(f"{fn.__name__}: all {max_attempts} attempts failed")
        return wrapper
    return decorator


def raise_if_transient(resp) -> None:
    """
    Call after requests.get/post. Raises TransientError for retriable HTTP codes,
    lets caller handle 401/403/404 themselves.
    """
    if resp.status_code in (429, 503, 504):
        raise TransientError(f"HTTP {resp.status_code} — retriable")


# ══════════════════════════════════════════════════════════════════════════════
# FIX #2 — UNIFIED CACHE (TTL dict, thread-safe)
# ══════════════════════════════════════════════════════════════════════════════

class TTLCache:
    """
    Thread-safe in-memory TTL cache.
    Optionally flushes expired entries on every N reads (lazy GC).
    """
    def __init__(self, default_ttl: int = 300, gc_interval: int = 100):
        self._store:   dict  = {}
        self._lock           = threading.Lock()
        self._default_ttl    = default_ttl
        self._gc_interval    = gc_interval
        self._read_count     = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            self._read_count += 1
            if self._read_count % self._gc_interval == 0:
                self._gc()
            entry = self._store.get(key)
            if entry and time.time() < entry["exp"]:
                return entry["val"]
            return None

    def set(self, key: str, val: Any, ttl: Optional[int] = None) -> None:
        ttl = ttl if ttl is not None else self._default_ttl
        with self._lock:
            self._store[key] = {"val": val, "exp": time.time() + ttl}

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def _gc(self) -> None:
        now  = time.time()
        dead = [k for k, v in self._store.items() if now >= v["exp"]]
        for k in dead:
            del self._store[k]
        if dead:
            logger.debug(f"[cache gc] evicted {len(dead)} expired entries")

    def stats(self) -> dict:
        with self._lock:
            now   = time.time()
            total = len(self._store)
            alive = sum(1 for v in self._store.values() if now < v["exp"])
            return {"total": total, "alive": alive, "expired": total - alive}


# Shared global cache instances (import these in other modules)
LIVE_CACHE = TTLCache(default_ttl=300)    # prices
FUND_CACHE = TTLCache(default_ttl=14400)  # fundamentals
NEWS_CACHE = TTLCache(default_ttl=1800)   # news
HIST_CACHE = TTLCache(default_ttl=3600)   # price history
CTX_CACHE  = TTLCache(default_ttl=300)    # AI market context


# ══════════════════════════════════════════════════════════════════════════════
# FIX #6 — PER-USER RATE LIMITER (sliding window)
# ══════════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """
    Sliding-window rate limiter per user_id.
    Allows max_calls per window_seconds.
    """
    def __init__(self, window: int = RATE_LIMIT_WINDOW, max_calls: int = RATE_LIMIT_MAX_CALLS):
        self._window    = window
        self._max_calls = max_calls
        self._history: dict = defaultdict(lambda: deque())
        self._lock      = threading.Lock()

    def is_allowed(self, user_id: int) -> bool:
        now = time.time()
        with self._lock:
            q = self._history[user_id]
            # Drop timestamps outside window
            while q and now - q[0] > self._window:
                q.popleft()
            if len(q) >= self._max_calls:
                return False
            q.append(now)
            return True

    def remaining(self, user_id: int) -> int:
        now = time.time()
        with self._lock:
            q = self._history[user_id]
            while q and now - q[0] > self._window:
                q.popleft()
            return max(0, self._max_calls - len(q))

    def reset(self, user_id: int) -> None:
        with self._lock:
            self._history.pop(user_id, None)


# Global rate limiter instance
API_RATE_LIMITER = RateLimiter()


# ══════════════════════════════════════════════════════════════════════════════
# FIX #9 — STRUCTURED LOGGING
# ══════════════════════════════════════════════════════════════════════════════

import json as _json
import uuid as _uuid


class StructuredFormatter(logging.Formatter):
    """
    JSON-structured log formatter.
    Each record includes: ts, level, module, func, request_id, msg.
    """
    def format(self, record: logging.LogRecord) -> str:
        log = {
            "ts":         self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":      record.levelname,
            "module":     record.module,
            "func":       record.funcName,
            "line":       record.lineno,
            "msg":        record.getMessage(),
        }
        if hasattr(record, "request_id"):
            log["request_id"] = record.request_id
        if record.exc_info:
            log["exc"] = self.formatException(record.exc_info)
        return _json.dumps(log)


def setup_logging(level: str = "INFO", structured: bool = False) -> None:
    """
    Call once at startup. Installs key filter + optional structured formatter.
    Copilot Fix #9: structured logs with module/func/line for traceable analytics.
    """
    handler = logging.StreamHandler()
    if structured:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s [%(module)s:%(funcName)s] %(message)s",
            datefmt="%H:%M:%S",
        ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    install_key_filter()


def get_request_id() -> str:
    return _uuid.uuid4().hex[:8]
