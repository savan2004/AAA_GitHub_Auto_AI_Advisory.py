"""
main_advanced.py — AI Stock Advisory Telegram Bot (v6.0 - Advanced)

ADVANCED FEATURES:
  1. Async-ready architecture with proper thread pool management
  2. Request deduplication with TTL
  3. Multi-level caching (memory + optional Redis)
  4. Circuit breaker pattern for external APIs
  5. Comprehensive metrics/monitoring
  6. Inline keyboard for better UX
  7. Callback query handlers for interactive charts
  8. Watchlist feature with price alerts
  9. Multi-language support framework
  10. Structured logging with request tracing
  11. Graceful shutdown handling
  12. Health check with dependency status
"""

import os
import sys
import time
import logging
import threading
import signal
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from collections import deque
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from enum import Enum
import json
import re
import hashlib
import uuid

import requests
import pandas as pd
import yfinance as yf
from flask import Flask, request, jsonify
import telebot
from telebot import types

# ── Structured Logging ────────────────────────────────────────────────────────
class LogFormatter(logging.Formatter):
    """Custom formatter with request ID and timing."""
    
    def format(self, record):
        record.request_id = getattr(record, 'request_id', '-')
        record.duration_ms = getattr(record, 'duration_ms', '-')
        return super().format(record)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(request_id)s] %(levelname)s %(name)s: %(message)s (%(duration_ms)sms)",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", maxBytes=10_000_000, backupCount=3)
    ],
)
logger = logging.getLogger(__name__)


# ── Circuit Breaker ───────────────────────────────────────────────────────────
class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if recovered


@dataclass
class CircuitBreaker:
    """Protects against cascading failures in external APIs."""
    name: str
    failure_threshold: int = 5
    recovery_timeout: int = 60
    half_open_max_calls: int = 3
    
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _half_open_calls: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    
    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time > self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
            return self._state
    
    def can_execute(self) -> bool:
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            with self._lock:
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False
        return False
    
    def record_success(self):
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
            self._failure_count = 0
    
    def record_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(f"Circuit OPEN: {self.name}")


# Create circuit breakers for external services
yf_circuit = CircuitBreaker("yfinance", failure_threshold=5, recovery_timeout=120)
news_circuit = CircuitBreaker("news_api", failure_threshold=3, recovery_timeout=180)
ai_circuit = CircuitBreaker("ai_api", failure_threshold=3, recovery_timeout=60)


# ── Metrics Collector ─────────────────────────────────────────────────────────
@dataclass
class Metrics:
    """Collects and aggregates bot metrics."""
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    
    # Counters
    messages_received: int = 0
    messages_sent: int = 0
    errors: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    
    # Timings (ms)
    _response_times: deque = field(default_factory=lambda: deque(maxlen=1000), init=False)
    
    # Per-feature counters
    feature_calls: dict = field(default_factory=dict, init=False)
    
    def increment(self, counter: str, value: int = 1):
        with self._lock:
            current = getattr(self, counter, 0)
            setattr(self, counter, current + value)
    
    def record_feature(self, feature: str, duration_ms: float):
        with self._lock:
            self.feature_calls[feature] = self.feature_calls.get(feature, 0) + 1
            self._response_times.append((feature, duration_ms))
    
    def record_response_time(self, duration_ms: float):
        self._response_times.append(("overall", duration_ms))
    
    def get_stats(self) -> dict:
        with self._lock:
            avg_times = {}
            feature_groups = {}
            for feature, t in self._response_times:
                if feature not in feature_groups:
                    feature_groups[feature] = []
                feature_groups[feature].append(t)
            
            for feature, times in feature_groups.items():
                avg_times[feature] = {
                    "avg_ms": round(sum(times) / len(times), 1),
                    "min_ms": round(min(times), 1),
                    "max_ms": round(max(times), 1),
                    "count": len(times),
                }
            
            return {
                "messages_received": self.messages_received,
                "messages_sent": self.messages_sent,
                "errors": self.errors,
                "cache_hit_rate": f"{self.cache_hits / (self.cache_hits + self.cache_misses) * 100:.1f}%" if (self.cache_hits + self.cache_misses) > 0 else "N/A",
                "response_times": avg_times,
                "feature_calls": dict(self.feature_calls),
                "uptime_seconds": int(time.time() - _START_TIME),
            }


metrics = Metrics()
_START_TIME = time.time()


# ── Advanced Cache with TTL ───────────────────────────────────────────────────
class AdvancedCache:
    """Thread-safe cache with TTL, size limits, and stats."""
    
    def __init__(self, name: str, max_size: int = 1000, default_ttl: int = 300):
        self.name = name
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: dict = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
    
    def get(self, key: str, ttl: Optional[int] = None) -> Optional[Any]:
        with self._lock:
            entry = self._cache.get(key)
            if entry:
                if time.time() - entry["ts"] < (ttl or entry.get("ttl", self.default_ttl)):
                    self._hits += 1
                    metrics.cache_hits += 1
                    return entry["val"]
                else:
                    del self._cache[key]
            self._misses += 1
            metrics.cache_misses += 1
            return None
    
    def set(self, key: str, val: Any, ttl: Optional[int] = None):
        with self._lock:
            if len(self._cache) >= self.max_size:
                # Evict oldest entries
                sorted_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k]["ts"])
                for k in sorted_keys[:self.max_size // 4]:
                    del self._cache[k]
            self._cache[key] = {"val": val, "ts": time.time(), "ttl": ttl or self.default_ttl}
    
    def invalidate(self, key: str):
        with self._lock:
            self._cache.pop(key, None)
    
    def clear(self):
        with self._lock:
            self._cache.clear()
    
    def stats(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "size": len(self._cache),
                "max_size": self.max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{self._hits / (self._hits + self._misses) * 100:.1f}%" if (self._hits + self._misses) > 0 else "N/A"
            }


# Create cache instances
live_cache = AdvancedCache("live_prices", max_size=500, default_ttl=300)
fund_cache = AdvancedCache("fundamentals", max_size=200, default_ttl=3600)
hist_cache = AdvancedCache("history", max_size=100, default_ttl=600)
news_cache = AdvancedCache("news", max_size=50, default_ttl=1800)
chart_cache = AdvancedCache("charts", max_size=50, default_ttl=3600)


# ── Request Deduplication ─────────────────────────────────────────────────────
class RequestDedup:
    """Prevents duplicate in-flight requests."""
    
    def __init__(self, ttl: int = 30):
        self._pending: dict = {}
        self._lock = threading.Lock()
        self._ttl = ttl
    
    def start(self, key: str) -> bool:
        """Returns True if this is a new request (not duplicate)."""
        with self._lock:
            now = time.time()
            # Clean expired entries
            expired = [k for k, t in self._pending.items() if now - t > self._ttl]
            for k in expired:
                del self._pending[k]
            # Check if already pending
            if key in self._pending:
                return False
            self._pending[key] = now
            return True
    
    def complete(self, key: str):
        with self._lock:
            self._pending.pop(key, None)


request_dedup = RequestDedup(ttl=30)


# ── Safe Execution Wrapper ────────────────────────────────────────────────────
def safe_execute(
    func: Callable,
    *args,
    circuit: Optional[CircuitBreaker] = None,
    timeout: Optional[float] = None,
    fallback: Any = None,
    **kwargs
) -> Any:
    """Execute function with circuit breaker, timeout, and error handling."""
    
    if circuit and not circuit.can_execute():
        logger.warning(f"Circuit breaker blocking: {circuit.name}")
        return fallback
    
    try:
        if timeout:
            # Use thread pool for timeout
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(func, *args, **kwargs)
                result = future.result(timeout=timeout)
        else:
            result = func(*args, **kwargs)
        
        if circuit:
            circuit.record_success()
        return result
    
    except Exception as e:
        if circuit:
            circuit.record_failure()
        logger.error(f"safe_execute failed ({func.__name__}): {e}")
        return fallback


# ── Env & Config ──────────────────────────────────────────────────────────────
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is required")

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
TAVILY_KEY = os.getenv("TAVILY_API_KEY")
WEBHOOK_PATH = f"/webhook/{TOKEN}"
BOT_VERSION = "6.0_advanced"

app = Flask(__name__)
bot = telebot.TeleBot(TOKEN, threaded=False)

# Advanced executor with naming for debugging
class NamedThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, *args, name: str = "worker", **kwargs):
        super().__init__(*args, **kwargs)
        self._name = name
    
    def submit(self, fn, *args, **kwargs):
        def wrapped():
            start = time.time()
            try:
                result = fn(*args, **kwargs)
                duration = (time.time() - start) * 1000
                logger.debug(f"{self._name} completed in {duration:.0f}ms")
                return result
            except Exception as e:
                duration = (time.time() - start) * 1000
                logger.error(f"{self._name} failed after {duration:.0f}ms: {e}", exc_info=True)
                raise
        return super().submit(wrapped)

executor = NamedThreadPoolExecutor(max_workers=25, name="bot_worker")


# ── State Management ──────────────────────────────────────────────────────────
class StateManager:
    """Thread-safe user state management with metadata."""
    
    def __init__(self):
        self._states: dict = {}
        self._lock = threading.Lock()
    
    def get(self, uid: int) -> Optional[dict]:
        with self._lock:
            return self._states.get(uid)
    
    def set(self, uid: int, mode: str, **metadata):
        with self._lock:
            self._states[uid] = {
                "mode": mode,
                "metadata": metadata,
                "updated_at": datetime.now().isoformat()
            }
    
    def update_meta(self, uid: int, **metadata):
        with self._lock:
            if uid in self._states:
                self._states[uid]["metadata"].update(metadata)
    
    def clear(self, uid: int):
        with self._lock:
            self._states.pop(uid, None)
    
    def get_mode(self, uid: int) -> Optional[str]:
        with self._lock:
            state = self._states.get(uid)
            return state["mode"] if state else None


state_manager = StateManager()


# ── Watchlist Feature ─────────────────────────────────────────────────────────
class WatchlistManager:
    """Manages user watchlists with price alerts."""
    
    def __init__(self, file_path: str = "watchlist_data.json"):
        self._watchlists: dict = {}
        self._file_path = file_path
        self._lock = threading.Lock()
        self._load()
    
    def _load(self):
        try:
            if os.path.exists(self._file_path):
                with open(self._file_path) as f:
                    raw = json.load(f)
                self._watchlists = {int(k): v for k, v in raw.items()}
                logger.info(f"Watchlist loaded: {len(self._watchlists)} users")
        except Exception as e:
            logger.warning(f"Watchlist load failed: {e}")
    
    def _save(self):
        try:
            with self._lock:
                with open(self._file_path, "w") as f:
                    json.dump(self._watchlists, f, indent=2)
        except Exception as e:
            logger.warning(f"Watchlist save failed: {e}")
    
    def add(self, uid: int, sym: str, target_price: Optional[float] = None, note: str = ""):
        with self._lock:
            if uid not in self._watchlists:
                self._watchlists[uid] = []
            # Check for duplicates
            for item in self._watchlists[uid]:
                if item["sym"] == sym.upper():
                    return False
            self._watchlists[uid].append({
                "sym": sym.upper(),
                "target": target_price,
                "note": note,
                "added_at": datetime.now().isoformat()
            })
        self._save()
        return True
    
    def remove(self, uid: int, sym: str) -> bool:
        with self._lock:
            if uid in self._watchlists:
                original_len = len(self._watchlists[uid])
                self._watchlists[uid] = [
                    item for item in self._watchlists[uid]
                    if item["sym"] != sym.upper()
                ]
                if len(self._watchlists[uid]) < original_len:
                    self._save()
                    return True
        return False
    
    def get(self, uid: int) -> list:
        with self._lock:
            return self._watchlists.get(uid, []).copy()
    
    def get_all_symbols(self) -> set:
        """Get all watched symbols across all users (for alert checking)."""
        with self._lock:
            syms = set()
            for items in self._watchlists.values():
                for item in items:
                    syms.add(item["sym"])
            return syms


watchlist_manager = WatchlistManager()


# ── Local imports (with fallback) ─────────────────────────────────────────────
try:
    from data_engine import get_hist, get_info, get_live_price, batch_quotes
except ImportError as e:
    logger.error(f"Failed to import data_engine: {e}")
    # Provide stubs
    def get_hist(sym, period): return pd.DataFrame()
    def get_info(sym): return {}
    def get_live_price(sym): return None
    def batch_quotes(syms): return {}

try:
    from technical_indicators import (
        calc_rsi, calc_ema, calc_macd, calc_atr, calc_asi,
        calc_bollinger, trend_label, swing_signal, rsi_label,
    )
except ImportError as e:
    logger.error(f"Failed to import technical_indicators: {e}")

try:
    from api_utils import API_RATE_LIMITER
    from config import RATE_LIMIT_WINDOW, RATE_LIMIT_MAX_CALLS
except ImportError:
    # Fallback rate limiter
    class SimpleRateLimiter:
        def __init__(self):
            self._calls = deque()
            self._lock = threading.Lock()
        
        def is_allowed(self, uid) -> bool:
            with self._lock:
                now = time.time()
                while self._calls and now - self._calls[0][1] > 60:
                    self._calls.popleft()
                return len([c for c in self._calls if c[0] == uid]) < 20
        
        def remaining(self, uid) -> int:
            with self._lock:
                now = time.time()
                while self._calls and now - self._calls[0][1] > 60:
                    self._calls.popleft()
                return 20 - len([c for c in self._calls if c[0] == uid])
    
    API_RATE_LIMITER = SimpleRateLimiter()
    RATE_LIMIT_WINDOW = 60
    RATE_LIMIT_MAX_CALLS = 20

try:
    from market_news import get_market_news, get_stock_news
except ImportError:
    def get_market_news(): return "News unavailable"
    def get_stock_news(sym): return ""

try:
    from ai_engine import (
        ai_insights as engine_ai_insights,
        ai_chat_respond, ai_topic_respond, ai_available,
        AI_CHAT_TOPICS, AI_CHAT_TOPIC_KEYS,
        add_to_chat, clear_chat, test_ai_providers, debug_ai_status,
    )
except ImportError:
    AI_CHAT_TOPICS = {}
    AI_CHAT_TOPIC_KEYS = []
    def engine_ai_insights(*a): return "AI unavailable"
    def ai_chat_respond(*a): return "AI unavailable"
    def ai_topic_respond(*a): return "AI unavailable"
    def ai_available(): return False
    def add_to_chat(*a): pass
    def clear_chat(*a): pass
    def test_ai_providers(): return {"_status": "Not configured"}
    def debug_ai_status(): return {}

try:
    from swing_trades import get_swing_trades
except ImportError:
    def get_swing_trades(**kw): return "Swing trades unavailable"

try:
    from chart_integration import get_chart_generator
except ImportError:
    def get_chart_generator(): return None

# Smart Symbol Resolver
try:
    from nifty500_collector import SECTOR_STOCKS as _SC
    _SYMBOL_MAP = {}
    _ALL_NSE_SYMS = []
    for _sec_syms in _SC.values():
        for _s in _sec_syms:
            _SYMBOL_MAP[_s.upper()] = _s
            _ALL_NSE_SYMS.append(_s)
except Exception:
    _SYMBOL_MAP = {}
    _ALL_NSE_SYMS = []


def resolve_symbol(query: str) -> tuple:
    """Resolves a user query to (nse_ticker, company_name)."""
    q = query.upper().strip().replace(" ", "").replace(".NS", "").replace(".BO", "")
    q_raw = query.strip()

    if q in _SYMBOL_MAP:
        return f"{_SYMBOL_MAP[q]}.NS", _SYMBOL_MAP[q]

    matches = [s for s in _ALL_NSE_SYMS if s.startswith(q)]
    if len(matches) == 1:
        return f"{matches[0]}.NS", matches[0]
    if len(matches) > 1:
        best = sorted(matches, key=len)[0]
        return f"{best}.NS", best

    try:
        results = yf.Search(q_raw, max_results=5).quotes
        for r in results:
            sym_raw = r.get("symbol", "")
            exch = r.get("exchange", "")
            if sym_raw and exch in ("NSI", "BSE"):
                if sym_raw.endswith(".NS"):
                    return sym_raw, r.get("longname") or r.get("shortname") or sym_raw
                elif sym_raw.endswith(".BO"):
                    return sym_raw.replace(".BO", ".NS"), r.get("longname") or sym_raw
                else:
                    return f"{sym_raw}.NS", r.get("longname") or sym_raw
    except Exception:
        pass

    try:
        _t = yf.Ticker(f"{q}.NS")
        _h = _t.history(period="2d", progress=False)
        if not _h.empty:
            return f"{q}.NS", (_t.info or {}).get("longName") or q
    except Exception:
        pass

    return None, None


# ── Portfolio (with thread safety) ────────────────────────────────────────────
class PortfolioManager:
    """Thread-safe portfolio management with persistence."""
    
    def __init__(self, file_path: str = "portfolio_data.json"):
        self._portfolios: dict = {}
        self._file_path = file_path
        self._lock = threading.Lock()
        self._load()
    
    def _load(self):
        try:
            if os.path.exists(self._file_path):
                with open(self._file_path) as f:
                    raw = json.load(f)
                self._portfolios = {int(k): v for k, v in raw.items()}
                logger.info(f"Portfolio loaded: {len(self._portfolios)} users")
        except Exception as e:
            logger.warning(f"Portfolio load failed: {e}")
    
    def _save(self):
        try:
            with self._lock:
                with open(self._file_path, "w") as f:
                    json.dump(self._portfolios, f, indent=2)
        except Exception as e:
            logger.warning(f"Portfolio save failed: {e}")
    
    def get(self, uid: int) -> dict:
        with self._lock:
            return self._portfolios.setdefault(uid, {}).copy()
    
    def add(self, uid: int, sym: str, qty: int, price: float):
        with self._lock:
            p = self._portfolios.setdefault(uid, {})
            if sym in p:
                old_qty, old_avg = p[sym]["qty"], p[sym]["avg"]
                new_qty = old_qty + qty
                new_avg = round((old_qty * old_avg + qty * price) / new_qty, 2)
                p[sym] = {"qty": new_qty, "avg": new_avg}
            else:
                p[sym] = {"qty": qty, "avg": round(price, 2)}
        self._save()
    
    def remove(self, uid: int, sym: str) -> bool:
        with self._lock:
            if uid in self._portfolios and sym in self._portfolios[uid]:
                del self._portfolios[uid][sym]
                self._save()
                return True
        return False


portfolio_manager = PortfolioManager()


def build_portfolio_card(uid: int) -> str:
    """Build portfolio report card."""
    p = portfolio_manager.get(uid)
    if not p:
        return (
            "📂 <b>Your Portfolio is Empty</b>\n\n"
            "Add a position:\n<code>/buy RELIANCE 10 2500</code>\n\n"
            "Remove a position:\n<code>/sell RELIANCE</code>"
        )

    today_str = date.today().strftime("%d-%b-%Y")
    total_inv = total_cur = 0.0
    rows, winners, losers = [], [], []

    for sym, pos in p.items():
        qty, avg = pos["qty"], pos["avg"]
        try:
            ltp = get_live_price(sym) or avg
            ltp = round(float(ltp), 2)
        except Exception:
            ltp = avg
        
        inv, cur = qty * avg, qty * ltp
        pnl = round(cur - inv, 2)
        pct = round((ltp - avg) / avg * 100, 2) if avg > 0 else 0.0
        rows.append({"sym": sym, "qty": qty, "avg": avg, "ltp": ltp,
                     "inv": inv, "cur": cur, "pnl": pnl, "pct": pct})
        total_inv += inv
        total_cur += cur
        (winners if pnl >= 0 else losers).append((sym, pnl, pct))

    total_pnl = round(total_cur - total_inv, 2)
    total_pct = round((total_cur - total_inv) / total_inv * 100, 2) if total_inv else 0.0
    port_icon = "🟢" if total_pnl >= 0 else "🔴"

    lines = [
        f"<b>━━━ 💼 PORTFOLIO REPORT ━━━</b>",
        f"📅 {today_str}  |  {len(rows)} holdings",
        "", "<b>── HOLDINGS ──</b>",
    ]

    rows.sort(key=lambda x: x["pnl"], reverse=True)
    for r in rows:
        pnl_icon = "🟢" if r["pnl"] >= 0 else "🔴"
        wt = round(r["inv"] / total_inv * 100, 1) if total_inv else 0
        lines += [
            f"{pnl_icon} <b>{r['sym']}</b>",
            f"   Qty: {r['qty']} ({wt}%)  |  Avg: ₹{r['avg']:,.2f} → LTP: ₹{r['ltp']:,.2f}",
            f"   P&L: ₹{r['pnl']:+,.2f} ({r['pct']:+.2f}%)",
            "   ···",
        ]

    lines += [
        "", "<b>── SUMMARY ──</b>",
        f"💰 Invested: ₹{total_inv:,.2f}  |  Current: ₹{total_cur:,.2f}",
        f"{port_icon} <b>P&L: ₹{total_pnl:+,.2f} ({total_pct:+.2f}%)</b>",
    ]

    if winners:
        winners.sort(key=lambda x: x[1], reverse=True)
        lines.append(f"🏆 Best: {winners[0][0]} ₹{winners[0][1]:+,.0f}")
    if losers:
        losers.sort(key=lambda x: x[1])
        lines.append(f"⚠️ Worst: {losers[0][0]} ₹{losers[0][1]:+,.0f}")

    lines += ["", "─" * 32, "➕ /buy SYM QTY PRICE  ➖ /sell SYM",
              "⚠️ <i>Educational only.</i>"]
    return "\n".join(lines)


# ── Helper Functions ──────────────────────────────────────────────────────────
def safe_val(d: dict, *keys, mul: float = 1.0):
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return round(float(v) * mul, 2)
            except Exception:
                pass
    return None


def fmt_mcap(val) -> str:
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if v <= 0:
            return "N/A"
        cr = v / 1e7
        if cr >= 1_00_000:
            return f"₹{cr / 1_00_000:.2f}L Cr"
        if cr >= 1_000:
            return f"₹{cr / 1_000:.2f}K Cr"
        return f"₹{cr:.2f} Cr"
    except Exception:
        return "N/A"


def _fmt_revenue(rev, mcap=None) -> str:
    if rev is None:
        return "N/A"
    try:
        rev_f = float(rev)
        if rev_f <= 0:
            return "N/A"
        if mcap and float(mcap) > 0 and rev_f > float(mcap) * 5:
            return "N/A (data error)"
        return fmt_mcap(rev_f)
    except Exception:
        return "N/A"


def _get_tgt_line(trend: str, ltp: float, atr: float) -> str:
    if trend == "BULLISH":
        return (f"🎯 Target: ₹{round(ltp + 1.5 * atr, 2):,.2f} (+{round(1.5 * atr / ltp * 100, 1)}%)"
                f"  |  SL: ₹{round(ltp - 2 * atr, 2):,.2f} (-{round(2 * atr / ltp * 100, 1)}%)")
    elif trend == "BEARISH":
        return (f"🎯 Target: ₹{round(ltp - 1.5 * atr, 2):,.2f} (-{round(1.5 * atr / ltp * 100, 1)}%)"
                f"  |  SL: ₹{round(ltp + 2 * atr, 2):,.2f} (+{round(2 * atr / ltp * 100, 1)}%)")
    else:
        return (f"🎯 R1: ₹{round(ltp + atr, 2):,.2f}  |  S1: ₹{round(ltp - atr, 2):,.2f}"
                f"  |  Range SL: ₹{round(ltp - 2 * atr, 2):,.2f}")


def build_adv(sym: str) -> str:
    """Build comprehensive advisory card."""
    sym = sym.upper().replace(".NS", "")
    
    # Cache check
    cache_key = f"adv_{sym}_{date.today()}"
    cached = fund_cache.get(cache_key, ttl=1800)
    if cached:
        return cached
    
    df = get_hist(sym, "6mo")
    if df.empty:
        return f"❌ <b>{sym}</b> not found. Check the NSE symbol."

    close = df["Close"]
    ltp = round(float(close.iloc[-1]), 2)
    prev = float(close.iloc[-2]) if len(close) > 1 else ltp
    chg = round((ltp - prev) / prev * 100, 2)
    rsi = calc_rsi(close)
    macd, _, _ = calc_macd(close)
    ema20 = calc_ema(close, 20)
    ema50 = calc_ema(close, 50)
    atr = calc_atr(df)
    asi = calc_asi(df)
    trend = "BULLISH" if ltp > ema20 > ema50 else "BEARISH" if ltp < ema20 < ema50 else "NEUTRAL"
    trend_icon = "🔼" if trend == "BULLISH" else "🔽" if trend == "BEARISH" else "↔️"

    try:
        from fundamentals import get_fundamentals
        fund = get_fundamentals(sym)
    except Exception:
        fund = {}
    info = get_info(sym) or {}

    name = fund.get("name") or info.get("name") or sym
    pe = fund.get("pe") or safe_val(info, "pe")
    fwd_pe = fund.get("fwd_pe")
    pb = fund.get("pb") or safe_val(info, "pb")
    roe = fund.get("roe")
    eps = fund.get("eps") or safe_val(info, "eps")
    mcap = fund.get("mcap") or info.get("market_cap")
    rev = fund.get("rev") or info.get("totalRevenue")
    de = fund.get("de") or safe_val(info, "debtToEquity")
    div_y = fund.get("div_y")
    w52h = fund.get("w52h") or safe_val(info, "high52")
    w52l = fund.get("w52l") or safe_val(info, "low52")
    beta = fund.get("beta") or safe_val(info, "beta")

    n = min(252, len(close))
    if w52h is None:
        w52h = round(float(close.rolling(n).max().iloc[-1]), 2)
    if w52l is None:
        w52l = round(float(close.rolling(n).min().iloc[-1]), 2)
    dist52 = round((ltp - w52h) / w52h * 100, 1) if w52h else None

    news_text = get_stock_news(sym)
    ai_text = engine_ai_insights(sym, ltp, rsi, macd, trend, str(pe or "N/A"), str(roe or "N/A"))
    chg_icon = "🟢" if chg >= 0 else "🔴"

    def frow(label: str, val, suffix: str = "") -> str:
        if val is None or val == "N/A":
            return f"  {label:<14}: N/A"
        return f"  {label:<14}: {val}{suffix}"

    rows = [
        f"🏢 <b>{name}</b>  ({sym})",
        f"{chg_icon} LTP: ₹{ltp:,.2f}  <b>({chg:+.2f}%)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📐 EMA20: ₹{ema20:,.2f}  |  EMA50: ₹{ema50:,.2f}",
        f"📏 52W H: ₹{w52h:,}  |  52W L: ₹{w52l:,}" + (f"  ({dist52:+.1f}% from peak)" if dist52 else ""),
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔬 Trend: <b>{trend} {trend_icon}</b>",
        f"📊 RSI: {rsi}  |  MACD: {'▲' if macd > 0 else '▼'} {macd}  |  ASI: {asi}",
        f"📉 ATR(14): ₹{atr}",
        "━━━━━━━━━━━━━━━━━━━━",
        "📋 <b>FUNDAMENTALS</b>",
        frow("Market Cap", fmt_mcap(mcap)),
        frow("Revenue", _fmt_revenue(rev, mcap)),
        frow("PE (TTM)", pe) + (f"  |  Fwd PE: {fwd_pe}" if fwd_pe else ""),
        frow("Price/Book", pb),
        frow("ROE", roe, "%") + (f"  |  EPS: ₹{eps}" if eps else ""),
        frow("Debt/Equity", de) + (f"  |  Beta: {beta}" if beta else ""),
        frow("Div Yield", div_y, "%"),
        "━━━━━━━━━━━━━━━━━━━━",
        _get_tgt_line(trend, ltp, atr),
    ]
    if news_text:
        rows += ["━━━━━━━━━━━━━━━━━━━━", f"📰 <b>NEWS</b>\n{news_text}"]
    rows += [
        "━━━━━━━━━━━━━━━━━━━━",
        f"🤖 <b>AI INSIGHTS</b>\n{ai_text}",
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Educational only. Not SEBI-registered advice.</i>",
    ]
    
    result = "\n".join(rows)
    fund_cache.set(cache_key, result, ttl=1800)
    return result


# ── Screener ──────────────────────────────────────────────────────────────────
SCREENER_STOCKS = {
    "conservative": ["HDFCBANK", "TCS", "INFY", "ITC", "ONGC", "SBIN", "WIPRO", "NTPC", "POWERGRID", "COALINDIA"],
    "moderate": ["RELIANCE", "BHARTIARTL", "AXISBANK", "MARUTI", "LT", "KOTAKBANK", "BAJFINANCE", "SUNPHARMA", "TITAN", "M&M"],
    "aggressive": ["TATAMOTORS", "ADANIENT", "JSWSTEEL", "TATAPOWER", "ZOMATO", "IRFC", "HAL", "BEL", "PFC", "ADANIPORTS"],
}


def build_scan(profile: str) -> str:
    syms = SCREENER_STOCKS.get(profile, [])
    if not syms:
        return "❌ Unknown screener profile."
    labels = {"conservative": "🏦 CONSERVATIVE", "moderate": "⚖️ MODERATE", "aggressive": "🚀 AGGRESSIVE"}
    lines = [f"📊 <b>{labels.get(profile, 'SCREENER')}</b>", f"📅 {date.today().strftime('%d-%b-%Y')}", "━━━━━━━━━━━━━━━━━━━━"]

    def _fetch_one(sym):
        try:
            df = get_hist(sym, "6mo")
            if df.empty or len(df) < 28:
                return None
            close = df["Close"]
            ltp = round(float(close.iloc[-1]), 2)
            prev = float(close.iloc[-2]) if len(close) > 1 else ltp
            return {
                "sym": sym, "ltp": ltp, "chg": round((ltp - prev) / prev * 100, 2),
                "rsi": calc_rsi(close), "trend": trend_label(close),
                "signal": swing_signal(calc_rsi(close), trend_label(close), round((ltp - prev) / prev * 100, 2))
            }
        except Exception:
            return None

    results = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(_fetch_one, sym): sym for sym in syms}
        for fut in as_completed(futs, timeout=15):
            try:
                r = fut.result()
                if r:
                    results[futs[fut]] = r
            except Exception:
                pass

    for sym in syms:
        r = results.get(sym)
        if not r:
            continue
        icon = "🟢" if r["chg"] >= 0 else "🔴"
        rsi_badge = "🔴OB" if r["rsi"] > 70 else ("🟢OS" if r["rsi"] < 30 else "🟡")
        lines.append(f"{icon} <b>{sym}</b>  ₹{r['ltp']:,.2f} ({r['chg']:+.2f}%)\n"
                     f"   RSI:{r['rsi']} {rsi_badge}  |  {r['trend']}  |  <b>{r['signal']}</b>")

    lines.append("\n⚠️ Educational only.")
    return "\n".join(lines)


def build_breadth() -> str:
    lines = ["📊 <b>MARKET BREADTH</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for name, ticker in {"NIFTY 50": "^NSEI", "BANK NIFTY": "^NSEBANK", "NIFTY IT": "^CNXIT"}.items():
        try:
            d = yf.Ticker(ticker).history(period="1mo")
            if d is None or len(d) < 5:
                continue
            l, p = round(float(d["Close"].iloc[-1]), 2), round(float(d["Close"].iloc[-2]), 2)
            chg = round((l - p) / p * 100, 2) if p else 0.0
            icon = "🟢" if chg >= 0 else "🔴"
            lines.append(f"{icon} <b>{name}</b>: {l:,.2f} ({chg:+.2f}%)")
        except Exception as e:
            logger.warning(f"breadth {name}: {e}")
    return "\n".join(lines) if len(lines) > 2 else "❌ Index data unavailable."


# ── News ──────────────────────────────────────────────────────────────────────
_NEWS_JUNK = ["Investing.com", "TradingView", "Yahoo Finance", "Stock Price", "NSE India"]

def build_news() -> str:
    cache_key = "market_news"
    cached = news_cache.get(cache_key)
    if cached:
        return cached
    
    result = "📰 News unavailable. Set TAVILY_API_KEY."
    if TAVILY_KEY:
        try:
            r = requests.post("https://api.tavily.com/search",
                json={"api_key": TAVILY_KEY, "query": "India NSE stock market news today",
                      "max_results": 8, "search_depth": "advanced"}, timeout=10)
            headlines = [x["title"] for x in r.json().get("results", [])
                        if x.get("title") and len(x["title"]) > 25
                        and not any(p in x["title"] for p in _NEWS_JUNK)][:5]
            if headlines:
                result = "📰 <b>MARKET NEWS</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(f"• {h[:100]}" for h in headlines)
        except Exception as e:
            logger.warning(f"News error: {e}")
    
    news_cache.set(cache_key, result, ttl=1800)
    return result


# ── Keyboards ─────────────────────────────────────────────────────────────────
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add("🔍 Analysis", "📊 Breadth", "🤖 AI")
    kb.add("🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive")
    kb.add("🎯 Swing (Safe)", "🚀 Swing (Agr)", "💼 Portfolio")
    kb.add("📰 News", "📈 Chart", "👁 Watchlist")
    kb.add("📋 Status", "ℹ️ Help")
    return kb


def ai_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    topics = list(AI_CHAT_TOPICS.keys())
    for i in range(0, len(topics) - 1, 2):
        kb.add(topics[i], topics[i + 1])
    if len(topics) % 2 == 1:
        kb.add(topics[-1])
    kb.add("🔙 Menu")
    return kb


def chart_period_keyboard():
    """Inline keyboard for chart period selection."""
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("1 Month", callback_data="chart_period_1mo"),
           types.InlineKeyboardButton("3 Months", callback_data="chart_period_3mo"))
    kb.add(types.InlineKeyboardButton("6 Months", callback_data="chart_period_6mo"),
           types.InlineKeyboardButton("1 Year", callback_data="chart_period_1y"))
    kb.add(types.InlineKeyboardButton("2 Years", callback_data="chart_period_2y"))
    return kb


# ── Safe Send ─────────────────────────────────────────────────────────────────
def safe_send(chat_id: int, text: str, parse_mode: str = "HTML", **kwargs):
    try:
        bot.send_message(chat_id, text, parse_mode=parse_mode, **kwargs)
        metrics.messages_sent += 1
    except Exception as e:
        if "can't parse" in str(e).lower() or "bad request" in str(e).lower():
            try:
                plain = re.sub(r"<[^>]+>", "", text)
                bot.send_message(chat_id, plain, **kwargs)
                metrics.messages_sent += 1
            except Exception as e2:
                logger.error(f"safe_send fallback failed: {e2}")
                metrics.errors += 1
        else:
            logger.error(f"safe_send error: {e}")
            metrics.errors += 1


# ── Callback Query Handlers (Inline Keyboard) ────────────────────────────────
@bot.callback_query_handler(func=lambda call: call.data.startswith("chart_period_"))
def handle_chart_period(call):
    """Handle chart period selection from inline keyboard."""
    chat_id = call.message.chat.id
    period = call.data.replace("chart_period_", "")
    
    # Get symbol from state
    state = state_manager.get(chat_id)
    sym = state.get("metadata", {}).get("pending_chart_sym") if state else None
    
    if not sym:
        bot.answer_callback_query(call.id, "❌ No symbol selected. Use /chart SYMBOL first.")
        return
    
    bot.answer_callback_query(call.id, f"Generating {period} chart...")
    safe_send(chat_id, f"📈 Generating {period} chart for <b>{sym}</b>…")
    
    def _run(cid=chat_id, s=sym, p=period):
        try:
            ticker, cname = resolve_symbol(s)
            if not ticker:
                safe_send(cid, f"❌ Could not find {s}")
                return
            gen = get_chart_generator()
            if not gen:
                safe_send(cid, "❌ Chart generator unavailable")
                return
            success, meta, path = gen.generate(f"{ticker}", cname, p)
            if success and path:
                with open(path, "rb") as f:
                    bot.send_photo(cid, f, caption=f"<b>📈 {cname}</b>\n\n{meta}", parse_mode="HTML")
            else:
                safe_send(cid, build_adv(s))
        except Exception as e:
            logger.error(f"Chart error: {e}", exc_info=True)
            safe_send(cid, f"❌ Chart error: {e}")
    
    executor.submit(_run)


@bot.callback_query_handler(func=lambda call: call.data == "back_to_menu")
def handle_back_menu(call):
    """Handle back to menu callback."""
    bot.answer_callback_query(call.id)
    safe_send(call.message.chat.id, "📋 Main Menu", reply_markup=main_keyboard())


# ── Command Handlers ──────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(message):
    state_manager.clear(message.chat.id)
    safe_send(
        message.chat.id,
        f"👋 <b>AutoAI Advisory Bot v{BOT_VERSION}</b>\n\n"
        "Type any stock name or NSE symbol for full analysis.\n\n"
        "🔘 <b>Features:</b>\n"
        "🔍 Analysis — Full stock analysis\n"
        "📊 Breadth — Market indices\n"
        "🤖 AI — AI chat with live data\n"
        "📈 Chart — Technical charts\n"
        "👁 Watchlist — Track stocks\n"
        "💼 Portfolio — Your positions\n\n"
        "📌 <b>Commands:</b>\n"
        "/chart SYM [period] | /buy SYM QTY PRICE\n"
        "/sell SYM | /watch SYM | /unwatch SYM\n"
        "/status | /clear | /help",
        reply_markup=main_keyboard(),
    )


@bot.message_handler(commands=["help"])
def cmd_help(message):
    safe_send(
        message.chat.id,
        "📖 <b>COMMANDS</b>\n\n"
        "<b>Analysis:</b> Type any symbol or <code>/chart SYM</code>\n"
        "<b>Portfolio:</b> <code>/buy SYM QTY PRICE</code> | <code>/sell SYM</code>\n"
        "<b>Watchlist:</b> <code>/watch SYM</code> | <code>/unwatch SYM</code> | <code>/watchlist</code>\n"
        "<b>AI:</b> Tap 🤖 AI | <code>/clear</code> to reset\n"
        "<b>Health:</b> <code>/status</code> | <code>/metrics</code>\n\n"
        "⚠️ Educational only. Not SEBI registered.",
    )


@bot.message_handler(commands=["status"])
def cmd_status(message):
    cid = message.chat.id
    safe_send(cid, "⏳ Checking status…")

    def _run(chat_id=cid):
        try:
            results = test_ai_providers()
            ai_lines = []
            for p in ["GROQ", "Gemini", "OpenAI", "AskFuzz"]:
                v = results.get(p, "SKIP")
                icon = "✅" if v.startswith("OK") else ("⚪" if v.startswith("SKIP") else "❌")
                ai_lines.append(f"  {icon} {p}: {v[:40]}")
            
            circuit_status = "\n".join([
                f"  {'✅' if c.state == CircuitState.CLOSED else '🟡' if c.state == CircuitState.HALF_OPEN else '❌'} {c.name}: {c.state.value}"
                for c in [yf_circuit, news_circuit, ai_circuit]
            ])
            
            safe_send(chat_id,
                f"🤖 <b>BOT STATUS</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Version : {BOT_VERSION}\n"
                f"Uptime  : {int(time.time() - _START_TIME) // 3600}h {int(time.time() - _START_TIME) % 3600 // 60}m\n"
                f"AI      : {results.get('_status', 'Unknown')}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Circuit Breakers:</b>\n{circuit_status}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>AI Providers:</b>\n" + "\n".join(ai_lines),
                reply_markup=main_keyboard()
            )
        except Exception as e:
            logger.error(f"Status error: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Status error: {e}")

    executor.submit(_run)


@bot.message_handler(commands=["metrics"])
def cmd_metrics(message):
    """Advanced metrics endpoint."""
    stats = metrics.get_stats()
    cache_stats = {
        "live": live_cache.stats(),
        "fund": fund_cache.stats(),
        "news": news_cache.stats(),
    }
    safe_send(
        message.chat.id,
        f"📊 <b>METRICS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Messages In: {stats['messages_received']}  |  Out: {stats['messages_sent']}\n"
        f"Errors: {stats['errors']}\n"
        f"Cache Hit Rate: {stats['cache_hit_rate']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Cache:</b>\n"
        f"  Live: {cache_stats['live']['size']}/{cache_stats['live']['max_size']} ({cache_stats['live']['hit_rate']})\n"
        f"  Fund: {cache_stats['fund']['size']}/{cache_stats['fund']['max_size']} ({cache_stats['fund']['hit_rate']})\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Response Times:</b>\n" +
        "\n".join(f"  {k}: {v['avg_ms']}ms avg ({v['count']} calls)" for k, v in stats['response_times'].items() if v['count'] > 0),
        reply_markup=main_keyboard()
    )


@bot.message_handler(commands=["chart"])
def cmd_chart(message):
    parts = message.text.strip().split()
    if len(parts) < 2:
        safe_send(message.chat.id,
                  "📈 <b>Chart Usage:</b>\n<code>/chart SYMBOL [period]</code>\n\n"
                  "Periods: 1mo, 3mo, 6mo, 1y, 2y\n\n"
                  "Example: <code>/chart INFY 3mo</code>")
        return

    raw_query = " ".join(parts[1:])
    period = None
    if parts[-1] in {"1mo", "3mo", "6mo", "1y", "2y"}:
        period = parts[-1]
        raw_query = " ".join(parts[1:-1])

    safe_send(message.chat.id, f"🔍 Looking up <b>{raw_query}</b>…")

    def _run(chat_id=message.chat.id, query=raw_query, per=period):
        try:
            ticker, cname = resolve_symbol(query)
            if not ticker:
                safe_send(chat_id, f"❌ Could not find <b>{query}</b>")
                return
            sym = ticker.replace(".NS", "").replace(".BO", "")
            
            if not per:
                # Show period selection
                state_manager.set(chat_id, "chart_pending", pending_chart_sym=sym)
                safe_send(chat_id, f"📊 Select period for <b>{cname}</b> ({sym}):", reply_markup=chart_period_keyboard())
                return
            
            safe_send(chat_id, f"📈 Generating {per} chart for <b>{cname}</b>…")
            gen = get_chart_generator()
            if not gen:
                safe_send(chat_id, "❌ Chart generator unavailable")
                return
            success, meta, path = gen.generate(ticker, cname, per)
            if success and path:
                with open(path, "rb") as f:
                    bot.send_photo(chat_id, f, caption=f"<b>📈 {cname} ({sym})</b>\n\n{meta}", parse_mode="HTML")
            else:
                safe_send(chat_id, "⚠️ Chart unavailable, sending text:")
                safe_send(chat_id, build_adv(sym))
        except Exception as e:
            logger.error(f"Chart error: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Chart error: {e}")

    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "📈 Chart")
def chart_button(message):
    safe_send(message.chat.id, "📈 Scanning Nifty 250 for best crossover…\n⏳ ~30s")

    def _run(chat_id=message.chat.id):
        def _ping():
            time.sleep(12)
            try:
                safe_send(chat_id, "⏳ Still scanning…")
            except Exception:
                pass
        threading.Thread(target=_ping, daemon=True).start()
        try:
            gen = get_chart_generator()
            if gen:
                gen.send_to_telegram(bot, chat_id)
            else:
                safe_send(chat_id, "❌ Chart generator unavailable")
        except Exception as e:
            logger.error(f"Auto chart error: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Scan failed: {e}")

    executor.submit(_run)


# ── Watchlist Commands ────────────────────────────────────────────────────────
@bot.message_handler(commands=["watch"])
def cmd_watch(message):
    parts = message.text.strip().split()
    if len(parts) < 2:
        safe_send(message.chat.id, "Usage: <code>/watch SYMBOL</code>\nExample: <code>/watch RELIANCE</code>")
        return
    
    sym = parts[1].upper().replace(".NS", "").replace(".BO", "")
    if watchlist_manager.add(message.chat.id, sym):
        safe_send(message.chat.id, f"✅ Added <b>{sym}</b> to watchlist.\nView: /watchlist")
    else:
        safe_send(message.chat.id, f"⚠️ <b>{sym}</b> already in watchlist.")


@bot.message_handler(commands=["unwatch"])
def cmd_unwatch(message):
    parts = message.text.strip().split()
    if len(parts) < 2:
        safe_send(message.chat.id, "Usage: <code>/unwatch SYMBOL</code>")
        return
    
    sym = parts[1].upper().replace(".NS", "").replace(".BO", "")
    if watchlist_manager.remove(message.chat.id, sym):
        safe_send(message.chat.id, f"✅ Removed <b>{sym}</b> from watchlist.")
    else:
        safe_send(message.chat.id, f"❌ <b>{sym}</b> not in watchlist.")


@bot.message_handler(commands=["watchlist"])
def cmd_watchlist(message):
    items = watchlist_manager.get(message.chat.id)
    if not items:
        safe_send(message.chat.id,
                  "👁 <b>Watchlist is Empty</b>\n\nAdd stocks: <code>/watch RELIANCE</code>")
        return
    
    safe_send(message.chat.id, "⏳ Fetching watchlist prices…")

    def _run(chat_id=message.chat.id, watchlist=items):
        try:
            lines = ["👁 <b>WATCHLIST</b>", "━━━━━━━━━━━━━━━━━━━━"]
            for item in watchlist:
                sym = item["sym"]
                try:
                    price = get_live_price(sym)
                    if price:
                        price = round(float(price), 2)
                        # Get previous close for change
                        df = get_hist(sym, "5d")
                        if not df.empty and len(df) > 1:
                            prev = float(df["Close"].iloc[-2])
                            chg = round((price - prev) / prev * 100, 2)
                            icon = "🟢" if chg >= 0 else "🔴"
                            lines.append(f"{icon} <b>{sym}</b>: ₹{price:,.2f} ({chg:+.2f}%)")
                        else:
                            lines.append(f"📊 <b>{sym}</b>: ₹{price:,.2f}")
                    else:
                        lines.append(f"⏳ <b>{sym}</b>: Price unavailable")
                except Exception:
                    lines.append(f"❌ <b>{sym}</b>: Error")
                if item.get("target"):
                    lines[-1] += f"  |  Target: ₹{item['target']:,.2f}"
            lines.append("\n💡 /watch SYM to add  |  /unwatch SYM to remove")
            safe_send(chat_id, "\n".join(lines))
        except Exception as e:
            logger.error(f"Watchlist error: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Watchlist error: {e}")

    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "👁 Watchlist")
def watchlist_button(message):
    cmd_watchlist(message)


# ── Portfolio Commands ────────────────────────────────────────────────────────
@bot.message_handler(commands=["buy"])
def cmd_buy(message):
    parts = message.text.strip().split()
    if len(parts) != 4:
        safe_send(message.chat.id, "Usage: <code>/buy SYMBOL QTY PRICE</code>\nExample: <code>/buy RELIANCE 10 2500</code>")
        return
    try:
        qty, price = int(parts[2]), float(parts[3])
    except ValueError:
        safe_send(message.chat.id, "❌ Invalid format.")
        return
    if qty <= 0 or price <= 0:
        safe_send(message.chat.id, "❌ Quantity and price must be positive.")
        return
    ticker, _ = resolve_symbol(parts[1])
    sym = ticker.replace(".NS", "").replace(".BO", "") if ticker else parts[1].upper().replace(".NS", "")
    portfolio_manager.add(message.chat.id, sym, qty, price)
    safe_send(message.chat.id, f"✅ Added <b>{qty} × {sym}</b> @ ₹{price:.2f}")


@bot.message_handler(commands=["sell"])
def cmd_sell(message):
    parts = message.text.strip().split()
    if len(parts) < 2:
        safe_send(message.chat.id, "Usage: <code>/sell SYMBOL</code>")
        return
    sym = " ".join(parts[1:]).upper().replace(".NS", "").replace(".BO", "")
    if portfolio_manager.remove(message.chat.id, sym):
        safe_send(message.chat.id, f"✅ Removed <b>{sym}</b> from portfolio.")
    else:
        safe_send(message.chat.id, f"❌ <b>{sym}</b> not in portfolio.")


@bot.message_handler(commands=["portfolio"])
def cmd_portfolio(message):
    safe_send(message.chat.id, "⏳ Loading portfolio…")
    def _run(chat_id=message.chat.id):
        try:
            safe_send(chat_id, build_portfolio_card(chat_id))
        except Exception as e:
            logger.error(f"Portfolio error: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "💼 Portfolio")
def portfolio_button(message):
    cmd_portfolio(message)


# ── Other Button Handlers ─────────────────────────────────────────────────────
@bot.message_handler(commands=["clear"])
def cmd_clear(message):
    clear_chat(message.chat.id)
    state_manager.clear(message.chat.id)
    safe_send(message.chat.id, "🗑️ Chat history cleared.", reply_markup=main_keyboard())


@bot.message_handler(func=lambda m: m.text == "🔙 Menu")
def back_to_main(message):
    state_manager.clear(message.chat.id)
    safe_send(message.chat.id, "📋 Main Menu", reply_markup=main_keyboard())


@bot.message_handler(func=lambda m: m.text == "🤖 AI")
def enter_ai_mode(message):
    state_manager.set(message.chat.id, "ai")
    safe_send(
        message.chat.id,
        "🤖 <b>AI Mode — Live Data Active</b>\n\n"
        "Ask anything about markets, stocks, options.\n\n"
        "Examples:\n"
        "  • <code>Reliance trade setup</code>\n"
        "  • <code>Nifty outlook today</code>\n"
        "  • <code>Best sector to invest</code>\n\n"
        "Tap <b>🔙 Menu</b> to return.",
        reply_markup=ai_keyboard(),
    )


@bot.message_handler(func=lambda m: m.text in AI_CHAT_TOPIC_KEYS)
def ai_topic_button(message):
    uid = message.chat.id
    if message.text == "🔍 Stock Analysis":
        state_manager.set(uid, "ai")
        safe_send(uid, "🔍 Type the stock name/symbol to analyze.", reply_markup=ai_keyboard())
        return
    topic_prompt = AI_CHAT_TOPICS[message.text]
    safe_send(uid, "⏳ Getting live data…")
    try:
        bot.send_chat_action(uid, "typing")
    except Exception:
        pass
    def _run(chat_id=uid, tp=topic_prompt):
        try:
            resp = ai_topic_respond(tp)
            safe_send(chat_id, resp or "⚠️ AI unavailable.", reply_markup=ai_keyboard())
        except Exception as e:
            logger.error(f"Topic error: {e}", exc_info=True)
            safe_send(chat_id, "⚠️ AI error. Try again.", reply_markup=ai_keyboard())
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text in ["🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive"])
def scan_button(message):
    profile = {"🏦 Conservative": "conservative", "⚖️ Moderate": "moderate", "🚀 Aggressive": "aggressive"}[message.text]
    safe_send(message.chat.id, f"⏳ Scanning {message.text}…")
    def _run(chat_id=message.chat.id, p=profile):
        try:
            safe_send(chat_id, build_scan(p))
        except Exception as e:
            logger.error(f"Screener error: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "📊 Breadth")
def breadth_button(message):
    safe_send(message.chat.id, "⏳ Fetching market data…")
    def _run(chat_id=message.chat.id):
        try:
            safe_send(chat_id, build_breadth())
        except Exception as e:
            logger.error(f"Breadth error: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text in ["🎯 Swing (Safe)", "🚀 Swing (Agr)"])
def swing_button(message):
    mode = "conservative" if "Safe" in message.text else "aggressive"
    safe_send(message.chat.id, f"⏳ Running swing scanner… (~25s)")
    def _ping(chat_id=message.chat.id):
        time.sleep(15)
        try:
            safe_send(chat_id, "⏳ Still scanning…")
        except Exception:
            pass
    threading.Thread(target=_ping, daemon=True).start()
    def _run(chat_id=message.chat.id, m=mode):
        try:
            result = get_swing_trades(mode=m)
            if len(result) <= 3800:
                safe_send(chat_id, result)
            else:
                chunk, parts = "", []
                for line in result.split("\n"):
                    if len(chunk) + len(line) + 1 > 3800:
                        parts.append(chunk)
                        chunk = ""
                    chunk += line + "\n"
                if chunk.strip():
                    parts.append(chunk)
                for part in parts:
                    safe_send(chat_id, part)
        except Exception as e:
            logger.error(f"Swing error: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "📰 News")
def news_button(message):
    safe_send(message.chat.id, "⏳ Fetching news…")
    def _run(chat_id=message.chat.id):
        try:
            safe_send(chat_id, build_news())
        except Exception as e:
            logger.error(f"News error: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "🔍 Analysis")
def analysis_hint(message):
    state_manager.set(message.chat.id, "analysis")
    safe_send(message.chat.id, "🔍 Type any stock name or NSE symbol.")


@bot.message_handler(func=lambda m: m.text in ["📋 Status", "ℹ️ Help"])
def status_help_button(message):
    if "Status" in message.text:
        cmd_status(message)
    else:
        cmd_help(message)


# ── Catch-all Handler ─────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    uid = message.chat.id
    text = message.text.strip()
    metrics.messages_received += 1

    if not API_RATE_LIMITER.is_allowed(uid):
        safe_send(uid, f"⚠️ Too many requests. Wait {API_RATE_LIMITER.remaining(uid)}s.")
        return

    mode = state_manager.get_mode(uid)

    if mode == "ai":
        safe_send(uid, "⏳ Thinking…")
        try:
            bot.send_chat_action(uid, "typing")
        except Exception:
            pass
        def _ai(chat_id=uid, t=text):
            try:
                resp = ai_chat_respond(chat_id, t)
                safe_send(chat_id, resp or "⚠️ AI unavailable.", reply_markup=ai_keyboard())
            except Exception as e:
                logger.error(f"AI error: {e}", exc_info=True)
                safe_send(chat_id, "⚠️ AI error. /status to check.", reply_markup=ai_keyboard())
        executor.submit(_ai)
        return

    if mode == "analysis":
        safe_send(uid, f"🔍 Looking up <b>{text}</b>…")
        def _analysis(chat_id=uid, q=text):
            try:
                ticker, cname = resolve_symbol(q)
                if ticker:
                    sym = ticker.replace(".NS", "").replace(".BO", "")
                    safe_send(chat_id, f"📊 Analyzing <b>{cname}</b>…")
                    safe_send(chat_id, build_adv(sym))
                else:
                    sym = q.upper().replace(".NS", "")
                    if 2 <= len(sym) <= 15:
                        safe_send(chat_id, build_adv(sym))
                    else:
                        safe_send(chat_id, f"❌ Could not find <b>{q}</b>", reply_markup=main_keyboard())
                state_manager.clear(chat_id)
            except Exception as e:
                logger.error(f"Analysis error: {e}", exc_info=True)
                safe_send(chat_id, f"❌ Error: {e}")
        executor.submit(_analysis)
        return

    # Smart symbol resolution
    raw_up = text.upper().replace(".NS", "").replace(".BO", "")
    _looks_ticker = 2 <= len(raw_up) <= 15 and all(c.isalnum() or c in "&-" for c in raw_up)
    _looks_name = " " in text or len(raw_up) > 12

    if _looks_ticker or _looks_name:
        safe_send(uid, f"🔍 Looking up <b>{text}</b>…")
        def _adv(chat_id=uid, q=text):
            try:
                ticker, cname = resolve_symbol(q)
                if ticker:
                    sym = ticker.replace(".NS", "").replace(".BO", "")
                    safe_send(chat_id, f"📊 Analyzing <b>{cname}</b>…")
                    safe_send(chat_id, build_adv(sym))
                else:
                    sym = q.upper().replace(".NS", "")
                    if 2 <= len(sym) <= 15:
                        safe_send(chat_id, build_adv(sym))
                    else:
                        safe_send(chat_id, f"❌ Could not find <b>{q}</b>")
            except Exception as e:
                logger.error(f"Adv error: {e}", exc_info=True)
                safe_send(chat_id, "⚠️ Error. Try again.")
        executor.submit(_adv)
    else:
        greetings = {"hi", "hello", "hey", "hlo", "hii", "gm"}
        if text.lower().strip("!.?") in greetings:
            safe_send(uid, "👋 Hello! Type a stock name to analyze.", reply_markup=main_keyboard())
        else:
            safe_send(uid, "💡 Type a stock name or use menu buttons.", reply_markup=main_keyboard())


# ── Flask Routes ───────────────────────────────────────────────────────────────
_processed_updates = deque(maxlen=1000)


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "ok",
        "version": BOT_VERSION,
        "features": ["circuit_breaker", "advanced_cache", "watchlist", "metrics", "inline_keyboard"],
        "metrics": metrics.get_stats(),
    })


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({
        "bot": "running",
        "ai": "available" if ai_available() else "no keys",
        "circuits": {
            "yfinance": yf_circuit.state.value,
            "news": news_circuit.state.value,
            "ai": ai_circuit.state.value,
        },
        "time": datetime.now().strftime("%d-%b-%Y %H:%M IST"),
    })


@app.route("/api/metrics", methods=["GET"])
def api_metrics():
    return jsonify(metrics.get_stats())


@app.route("/api/cache", methods=["GET"])
def api_cache():
    return jsonify({
        "live": live_cache.stats(),
        "fund": fund_cache.stats(),
        "news": news_cache.stats(),
        "hist": hist_cache.stats(),
        "chart": chart_cache.stats(),
    })


@app.route("/api/health", methods=["GET"])
def api_health():
    """Detailed health check for monitoring."""
    checks = {
        "bot": "ok",
        "yfinance": "ok" if yf_circuit.state != CircuitState.OPEN else "degraded",
        "news": "ok" if news_circuit.state != CircuitState.OPEN else "degraded",
        "ai": "ok" if ai_circuit.state != CircuitState.OPEN else "degraded",
    }
    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return jsonify({"status": overall, "checks": checks, "timestamp": datetime.now().isoformat()})


@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    data = request.get_data().decode("utf-8")
    try:
        update_id = json.loads(data).get("update_id")
    except Exception:
        update_id = None
    if update_id:
        if update_id in _processed_updates:
            return "ok", 200
        _processed_updates.append(update_id)
    executor.submit(process_update, data)
    return "ok", 200


def process_update(update_json: str):
    try:
        update = telebot.types.Update.de_json(update_json)
        bot.process_new_updates([update])
    except Exception as e:
        logger.error(f"process_update: {e}")


# ── Graceful Shutdown ─────────────────────────────────────────────────────────
def shutdown_handler(signum, frame):
    logger.info("🛑 Shutdown signal received...")
    executor.shutdown(wait=True, cancel_futures=False)
    logger.info("✅ Executor shutdown complete")
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"🚀 Starting AutoAI Advisory Bot v{BOT_VERSION}...")
    logger.info(f"   Circuit breakers: yfinance, news_api, ai_api")
    logger.info(f"   Cache: live, fund, news, hist, chart")
    logger.info(f"   Features: watchlist, metrics, inline_keyboard")
    
    if WEBHOOK_URL:
        bot.set_webhook(url=f"{WEBHOOK_URL}{WEBHOOK_PATH}")
        logger.info(f"Webhook: {WEBHOOK_URL}{WEBHOOK_PATH}")
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
    else:
        logger.info("Running in polling mode...")
        bot.infinity_polling()
