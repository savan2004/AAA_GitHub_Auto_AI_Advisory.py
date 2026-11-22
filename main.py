"""
main.py — MSK Bot (v6.3)
v6.2: removed News + AI chat buttons · added 🔥 Stocks Buzz (top-1000 breakout radar)
      · institutional-grade research PDF report.
"""

import os
import re
import json
import time
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, date, timedelta, timezone
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
import threading
import hmac
import html
import math

import requests
import pandas as pd
import yfinance as yf
from flask import Flask, request, jsonify, Response
import msk_usage as usage_analytics
import telebot
from telebot import types

# ── Local Module Imports ──────────────────────────────────────────────────────
from data_engine import get_hist, get_info, get_live_price, get_verified_quote, normalized_debt_equity_ratio, batch_quotes
from financial_quality import financial_source_summary
from technical_price_safety import technical_price_context, guarded_target_line
from technical_indicators import (
    calc_rsi, calc_ema, calc_macd, calc_atr, calc_asi,
    calc_bollinger, trend_label, swing_signal, rsi_label,
)
from api_utils import API_RATE_LIMITER
from postgres_portfolio import build_portfolio_backend
from msk_durable_state import build_durable_bot_state
from config import RATE_LIMIT_WINDOW, RATE_LIMIT_MAX_CALLS
from market_news import get_stock_news

from ai_engine import (
    ai_insights as engine_ai_insights,
    ai_available,
    test_ai_providers,
)
from swing_trades import get_swing_trades
from msk_intraday_trade import build_nifty200_report
from chart_integration import get_chart_generator
from report_generator import generate_stock_report_pdf
import stocks_buzz
import sector_report
from group_stock_requests import extract_stock_request
from group_member_filter import is_member_message
from conservative_screener import (
    CONSERVATIVE_CANDIDATES, evaluate_conservative, pick_diversified,
)
from investment_screener import evaluate_candidate, format_shortlist
from investment_scan_runner import run_investment_scan
from index_top5 import request_index_top5
from msk_daily_reports import parse_request as parse_daily_report_request, render_latest_profile
from analysis_quality import (
    BEES_ETFS, bees_etf_symbol, format_etf_profile, format_snapshot, inspect_history,
)

# ── Logging Setup (Render & Local Safe) ──────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler("bot.log", maxBytes=10_000_000, backupCount=3)
    ]
)
logger = logging.getLogger(__name__)

# ── Environment & Config ──────────────────────────────────────────────────────
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN environment variable is required")

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
TAVILY_KEY = os.getenv("TAVILY_API_KEY")
# An independent secret is mandatory whenever webhook mode is used.
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
if WEBHOOK_URL and (len(WEBHOOK_SECRET) < 16 or not WEBHOOK_SECRET.replace("_", "").replace("-", "").isalnum()):
    raise RuntimeError("Set TELEGRAM_WEBHOOK_SECRET (16+ letters/digits/_/-) before webhook deployment")
WEBHOOK_PATH = "/webhook/telegram"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 256_000

# Authenticated internal REPORT ONLY bridge to the separate Channel product.
# Never modifies either Telegram webhook or user-facing MSK handlers.
from internal_report_bridge import bp as full_research_bp
app.register_blueprint(full_research_bp)
bot = telebot.TeleBot(TOKEN, threaded=False)
executor = ThreadPoolExecutor(max_workers=12, thread_name_prefix="msk-heavy")
# Reserve a separate pool for Telegram update intake.
webhook_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="msk-webhook")
# Separate, bounded and entirely best-effort analytics; never hold webhook responses.
usage_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="msk-usage")
_usage_slots = threading.BoundedSemaphore(64)
_usage_stop = threading.Event()
# This measures only the current Python process lifespan, NOT 24-hour service uptime.
_PROCESS_START_MONOTONIC = time.monotonic()

# ── Smart Symbol Resolver (yfinance version-safe) ────────────────────────────
_SYMBOL_MAP = {}
_ALL_NSE_SYMS = []
try:
    from nifty500_collector import SECTOR_STOCKS as _SC
    for _sec_syms in _SC.values():
        for _s in _sec_syms:
            _SYMBOL_MAP[_s.upper()] = _s
            _ALL_NSE_SYMS.append(_s)
except Exception:
    pass


def resolve_symbol(query: str) -> tuple:
    """Resolve only candidate NSE tickers; a name match is not price validation."""
    raw = (query or "").strip()
    if not raw or len(raw) > 90:
        return None, None
    q = raw.upper().replace("NSE:", "").replace(".NS", "").replace(".BO", "").replace(" ", "")
    if not re.fullmatch(r"[A-Z0-9&_.-]{1,30}", q):
        return None, None
    if q in _SYMBOL_MAP:
        return f"{_SYMBOL_MAP[q]}.NS", _SYMBOL_MAP[q]
    matches = sorted(set(stock for stock in _ALL_NSE_SYMS if stock.startswith(q)))
    if len(matches) == 1:
        return f"{matches[0]}.NS", matches[0]
    if len(matches) > 1:
        return None, None
    try:
        if hasattr(yf, "Search"):
            quotes = getattr(yf.Search(raw, max_results=8), "quotes", []) or []
            exact, named = [], []
            for candidate in quotes:
                ticker = str(candidate.get("symbol") or "").upper()
                exchange = str(candidate.get("exchange") or "")
                if not ticker.endswith(".NS") or exchange not in ("NSI", "NSE", ""):
                    continue
                bare = ticker[:-3]
                if not re.fullmatch(r"[A-Z0-9&_.-]{1,30}", bare):
                    continue
                name = candidate.get("longname") or candidate.get("shortname") or bare
                if bare == q:
                    exact.append((ticker, name))
                elif len(raw) > 3 and raw.casefold() in str(name).casefold():
                    named.append((ticker, name))
            if exact:
                return exact[0]
            if len(named) == 1:
                return named[0]
            if len(named) > 1:
                return None, None
    except Exception:
        logger.debug("NSE symbol search unavailable")
    if " " not in raw and 2 <= len(q) <= 15:
        return f"{q}.NS", q  # provisional; Analysis validates market candles
    return None, None

# ── Thread-Safe State Manager ────────────────────────────────────────────────
class StateManager:
    def __init__(self):
        self._states = {}
        self._lock = threading.Lock()

    def get(self, uid):
        with self._lock:
            return self._states.get(uid)

    def set(self, uid, val):
        with self._lock:
            self._states[uid] = val

    def clear(self, uid):
        with self._lock:
            self._states.pop(uid, None)


state = StateManager()
_processed_updates = deque(maxlen=1000)
_processed_updates_set = set()
_webhook_lock = threading.Lock()
_webhook_slots = threading.BoundedSemaphore(value=16)
# Enabled only by an explicit DB-backed release flag; fails startup if it
# cannot provide durable deduplication. Existing production defaults unchanged.
_durable_update_ledger = build_durable_bot_state()


# ── Thread-Safe Portfolio Manager ────────────────────────────────────────────
class PortfolioManager:
    """Legacy JSON storage. Atomic on this filesystem, NOT durable on Render free."""
    def __init__(self, file_path="portfolio_data.json", allow_empty_init=False):
        self._data = {}
        self._file = file_path
        self._lock = threading.Lock()
        self._recovery_required = False
        self._allow_empty_init = allow_empty_init
        self._load()

    def _load(self):
        if not os.path.exists(self._file):
            # Missing holdings after a redeploy cannot be distinguished from a fresh install.
            # Never implicitly allow writes or report an empty account as authoritative.
            if self._allow_empty_init:
                self._persist_locked({})  # Explicit operator-approved first-run bootstrap only.
                return
            self._recovery_required = True
            logger.error("Portfolio storage missing at %s: restore required before reads or writes", self._file)
            return
        try:
            import math
            with open(self._file, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("Portfolio root must be an object")
            parsed = {}
            for uid, positions in raw.items():
                if not isinstance(positions, dict):
                    raise ValueError("Invalid user portfolio")
                account = {}
                for symbol, pos in positions.items():
                    if (not isinstance(symbol, str) or not isinstance(pos, dict)
                            or set(pos) != {"qty", "avg"}):
                        raise ValueError("Invalid holding structure")
                    qty, avg = pos["qty"], pos["avg"]
                    if (type(qty) not in (int, float)
                            or type(avg) not in (int, float)
                            or not math.isfinite(qty) or not math.isfinite(avg)
                            or qty <= 0 or avg < 0):
                        raise ValueError("Invalid holding value")
                    account[symbol] = {"qty": qty, "avg": avg}
                parsed[int(uid)] = account
            self._data = parsed
        except Exception as exc:
            logger.exception("Portfolio loading failed: operator recovery required")
            raise RuntimeError("Portfolio data is invalid; manual recovery required") from exc

    def _persist_locked(self, snapshot):
        """Caller holds the lock; commit new state only after atomic file replacement."""
        import tempfile
        directory = os.path.dirname(os.path.abspath(self._file))
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             prefix=".msk-holdings-", suffix=".tmp",
                                             dir=directory, delete=False) as handle:
                temp_name = handle.name
                os.chmod(temp_name, 0o600)
                json.dump(snapshot, handle, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._file)
        except Exception as exc:
            logger.exception("Portfolio storage write failed")
            raise RuntimeError("Portfolio changes could not be saved") from exc
        finally:
            if temp_name and os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    logger.warning("Could not remove portfolio temporary file")

    def _require_available(self):
        if self._recovery_required:
            raise RuntimeError("Portfolio storage missing; restore required before use")

    def _save(self):
        with self._lock:
            self._require_available()
            self._persist_locked(self._data)

    def get(self, uid):
        with self._lock:
            self._require_available()
            return {s: p.copy() for s, p in self._data.get(uid, {}).items()}

    def add(self, uid, sym, qty, price):
        import math
        import re
        if (type(qty) is not int or not 0 < qty <= 10**9
                or type(price) not in (int, float)
                or not math.isfinite(price) or not 0 < price <= 10**12
                or not isinstance(sym, str)
                or not re.fullmatch(r"[A-Za-z0-9&._-]{1,30}", sym)):
            raise ValueError("Invalid portfolio holding")
        with self._lock:
            self._require_available()
            proposed = {u: {k: v.copy() for k, v in items.items()}
                        for u, items in self._data.items()}
            positions = proposed.setdefault(uid, {})
            if sym in positions:
                prev = positions[sym]
                newqty = prev["qty"] + qty
                if newqty > 10**9:
                    raise ValueError("Holding quantity exceeds limit")
                positions[sym] = {
                    "qty": newqty,
                    "avg": round((prev["qty"] * prev["avg"] + qty * price) / newqty, 2),
                }
            else:
                if len(positions) >= 10:
                    raise ValueError("Portfolio maximum is 10 distinct holdings")
                positions[sym] = {"qty": qty, "avg": round(price, 2)}
            self._persist_locked(proposed)
            self._data = proposed

    def remove(self, uid, sym):
        with self._lock:
            self._require_available()
            if uid not in self._data or sym not in self._data[uid]:
                return False
            proposed = {u: {k: v.copy() for k, v in items.items()}
                        for u, items in self._data.items()}
            del proposed[uid][sym]
            self._persist_locked(proposed)
            self._data = proposed
            return True


portfolio = build_portfolio_backend(PortfolioManager)

# ── Safe Formatting Helpers ──────────────────────────────────────────────────
def safe_val(d, *keys, mul=1.0):
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return round(float(v) * mul, 2)
            except (TypeError, ValueError):
                pass
    return None


def fmt_mcap(val):
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if v <= 0:
            return "N/A"
        cr = v / 1e7
        if cr >= 100000:
            return f"₹{cr / 100000:.2f}L Cr"
        if cr >= 1000:
            return f"₹{cr / 1000:.2f}K Cr"
        return f"₹{cr:.2f} Cr"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_revenue(rev, mcap=None):
    if rev is None:
        return "N/A"
    try:
        r = float(rev)
        if r <= 0:
            return "N/A"
        if mcap is not None:
            m = float(mcap)
            if m > 0 and r > m * 5:
                return "N/A (data err)"
        return fmt_mcap(r)
    except (TypeError, ValueError):
        return "N/A"


def _get_tgt_line(trend, ltp, atr):
    if atr is None or atr <= 0 or ltp <= 0:
        return "🎯 Target/SL: Insufficient data"
    if trend == "BULLISH":
        return (f"🎯 Target: ₹{round(ltp + 1.5 * atr, 2):,.2f} (+{round(1.5 * atr / ltp * 100, 1)}%)"
                f"  |  SL: ₹{round(ltp - 2 * atr, 2):,.2f} (-{round(2 * atr / ltp * 100, 1)}%)")
    if trend == "BEARISH":
        return (f"🎯 Target: ₹{round(ltp - 1.5 * atr, 2):,.2f} (-{round(1.5 * atr / ltp * 100, 1)}%)"
                f"  |  SL: ₹{round(ltp + 2 * atr, 2):,.2f} (+{round(2 * atr / ltp * 100, 1)}%)")
    return (f"🎯 R1: ₹{round(ltp + atr, 2):,.2f}  |  S1: ₹{round(ltp - atr, 2):,.2f}"
            f"  |  Range SL: ₹{round(ltp - 2 * atr, 2):,.2f}")


def frow(label, val, suffix=""):
    if val is None or val == "N/A":
        return f"  {label:<14}: N/A"
    return f"  {label:<14}: {val}{suffix}"


# ── Build Advisory Card ──────────────────────────────────────────────────────
def build_adv(sym):
    sym = str(sym).upper().replace(".NS", "").replace(".BO", "")
    try:
        df = get_hist(sym, "6mo")
    except Exception as e:
        return f"❌ Error fetching history for {sym}: {e}"

    if df is None or df.empty:
        return f"❌ <b>{sym}</b> not found."

    if len(df) < 2:
        return f"❌ <b>{sym}</b> insufficient historical data."

    # The last daily candle is NOT live LTP. Reject undated/stale history,
    # and permit targets only with independently timestamp-verified quotes.
    try:
        observed = get_verified_quote(sym)
    except Exception:
        observed = None
    market = technical_price_context(df, observed)
    if market is None:
        return f"⚠️ <b>{sym}</b>: historical candles undated, stale or invalid; analysis withheld."
    close = df["Close"]
    ltp = market["historical_close"]
    prev = market["previous_close"]
    chg = round((ltp - prev) / prev * 100, 2)
    rsi = calc_rsi(close)
    macd, _, _ = calc_macd(close)
    ema20 = calc_ema(close, 20)
    ema50 = calc_ema(close, 50)
    atr = calc_atr(df)
    asi = calc_asi(df)
    trend = "BULLISH" if ltp > ema20 > ema50 else "BEARISH" if ltp < ema20 < ema50 else "NEUTRAL"
    t_icon = "🔼" if trend == "BULLISH" else "🔽" if trend == "BEARISH" else "↔️"

    # Fundamentals
    fund = {}
    try:
        from fundamentals import get_fundamentals
        fund = get_fundamentals(sym) or {}
    except Exception:
        pass

    info = {}
    try:
        info = get_info(sym) or {}
    except Exception:
        pass

    name = fund.get("name") or info.get("name") or sym
    pe = fund.get("pe") or safe_val(info, "pe")
    fwd_pe = fund.get("fwd_pe")
    pb = fund.get("pb") or safe_val(info, "pb")
    roe = fund.get("roe")
    eps = fund.get("eps") or safe_val(info, "eps")
    mcap = fund.get("mcap") or info.get("market_cap")
    rev = fund.get("rev") or info.get("totalRevenue")
    # Yahoo debtToEquity is percent-scaled (125 means 1.25x), while
    # Screener/Finnhub normalized aggregations are already unitless.
    de = normalized_debt_equity_ratio({"debt_equity_ratio": fund.get("de")})
    if de is None:
        de = normalized_debt_equity_ratio(info)
    div_y = fund.get("div_y")
    w52h = fund.get("w52h") or safe_val(info, "high52")
    w52l = fund.get("w52l") or safe_val(info, "low52")
    beta = fund.get("beta") or safe_val(info, "beta")

    n = min(252, len(close))
    if w52h is None:
        try:
            w52h = round(float(close.rolling(n).max().iloc[-1]), 2)
        except Exception:
            w52h = None
    if w52l is None:
        try:
            w52l = round(float(close.rolling(n).min().iloc[-1]), 2)
        except Exception:
            w52l = None

    dist52 = None
    if w52h is not None and w52h > 0:
        dist52 = round((ltp - w52h) / w52h * 100, 1)

    # News & AI
    news_text = ""
    try:
        news_text = get_stock_news(sym) or ""
    except Exception:
        pass

    ai_text = ""
    if market["target_price"] is None:
        ai_text = "AI price-target insight withheld: no verified current-session price."
    else:
        try:
            ai_text = engine_ai_insights(
                sym, market["target_price"], rsi, macd, trend,
                str(pe if pe is not None else "N/A"),
                str(roe if roe is not None else "N/A")
            ) or ""
        except Exception:
            ai_text = "AI insights unavailable."

    chg_icon = "🟢" if chg >= 0 else "🔴"

    rows = [
        f"🏢 <b>{name}</b>  ({sym})",
        market["historical_line"],
        market["verified_quote_line"],
        f"{chg_icon} Historical close change: <b>{chg:+.2f}%</b> vs prior candle",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📐 EMA20: ₹{ema20:,.2f}  |  EMA50: ₹{ema50:,.2f}",
        f"📏 52W H: ₹{w52h or 'N/A'}  |  52W L: ₹{w52l or 'N/A'}" + (f"  ({dist52:+.1f}% from peak)" if dist52 is not None else ""),
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔬 Trend: <b>{trend} {t_icon}</b>",
        f"📊 RSI: {rsi}  |  MACD: {'▲' if macd > 0 else '▼'} {macd}  |  ASI: {asi}",
        f"📉 ATR(14): ₹{atr if atr else 'N/A'}",
        "━━━━━━━━━━━━━━━━━━━━",
        "📋 <b>FUNDAMENTALS</b>",
        frow("Market Cap", fmt_mcap(mcap)),
        frow("Revenue", _fmt_revenue(rev, mcap)),
        frow("PE (TTM)", pe) + (f"  |  Fwd PE: {fwd_pe}" if fwd_pe else ""),
        frow("Price/Book", pb),
        frow("ROE", roe, "%") + (f"  |  EPS: ₹{eps}" if eps else ""),
        frow("Debt/Equity", f"{de:g}" if de is not None else None, "x") + (f"  |  Beta: {beta}" if beta else ""),
        frow("Div Yield", div_y, "%"),
        "━━━━━━━━━━━━━━━━━━━━",
        financial_source_summary(fund),
        "━━━━━━━━━━━━━━━━━━━━",
        guarded_target_line(_get_tgt_line, trend, atr, market),
    ]
    if news_text:
        rows += ["━━━━━━━━━━━━━━━━━━━━", f"📰 <b>NEWS</b>\n{news_text}"]
    rows += [
        "━━━━━━━━━━━━━━━━━━━━",
        f"🤖 <b>AI INSIGHTS</b>\n{ai_text}",
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Educational only. Not SEBI-registered advice.</i>",
    ]
    return "\n".join(rows)


# ── Modular Cards (Technical-only / Fundamental-only) ────────────────────────
# Both reuse the exact same fetch + formatting helpers as build_adv() above,
# so numbers always agree across the quick-menu options and the full card /
# PDF report. Kept as separate functions (not a slice of build_adv) so each
# can be sent standalone from the post-lookup selection menu.
def build_technical_text(sym, cname=None):
    sym = str(sym).upper().replace(".NS", "").replace(".BO", "")
    try:
        df = get_hist(sym, "6mo")
    except Exception as e:
        return f"❌ Error fetching history for {sym}: {e}"
    if df is None or df.empty or len(df) < 2:
        return f"❌ <b>{sym}</b> not found or insufficient data."

    # The last daily candle is NOT live LTP. Reject undated/stale history,
    # and permit targets only with independently timestamp-verified quotes.
    try:
        observed = get_verified_quote(sym)
    except Exception:
        observed = None
    market = technical_price_context(df, observed)
    if market is None:
        return f"⚠️ <b>{sym}</b>: historical candles undated, stale or invalid; analysis withheld."
    close = df["Close"]
    ltp = market["historical_close"]
    prev = market["previous_close"]
    chg = round((ltp - prev) / prev * 100, 2)
    rsi = calc_rsi(close)
    macd, _, _ = calc_macd(close)
    ema20 = calc_ema(close, 20)
    ema50 = calc_ema(close, 50)
    atr = calc_atr(df)
    asi = calc_asi(df)
    trend = "BULLISH" if ltp > ema20 > ema50 else "BEARISH" if ltp < ema20 < ema50 else "NEUTRAL"
    t_icon = "🔼" if trend == "BULLISH" else "🔽" if trend == "BEARISH" else "↔️"

    closing_prices = [float(price) for price in close.dropna()]
    range_line = "📏 Closing range unavailable"
    if closing_prices and all(math.isfinite(price) and price > 0 for price in closing_prices):
        high, low = max(closing_prices), min(closing_prices)
        distance = 100 * (ltp / high - 1)
        range_line = (f"📏 Closing range ({len(closing_prices)} candles): "
                      f"₹{low:,.2f}–₹{high:,.2f}  ({distance:+.1f}% from closing peak)")

    name = cname or sym
    chg_icon = "🟢" if chg >= 0 else "🔴"
    rows = [
        f"📊 <b>{name}</b>  ({sym}) — Technical Analysis",
        market["historical_line"],
        market["verified_quote_line"],
        f"{chg_icon} Historical close change: <b>{chg:+.2f}%</b> vs prior candle",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📐 EMA20: ₹{ema20:,.2f}  |  EMA50: ₹{ema50:,.2f}",
        range_line,
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔬 Trend: <b>{trend} {t_icon}</b>",
        f"📊 RSI: {rsi}  |  MACD: {'▲' if macd > 0 else '▼'} {macd}  |  ASI: {asi}",
        f"📉 ATR(14): ₹{atr if atr else 'N/A'}",
        "━━━━━━━━━━━━━━━━━━━━",
        guarded_target_line(_get_tgt_line, trend, atr, market),
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Educational only. Not SEBI-registered advice.</i>",
    ]
    return "\n".join(rows)


def build_fundamental_text(sym, cname=None):
    sym = str(sym).upper().replace(".NS", "").replace(".BO", "")
    fund = {}
    try:
        from fundamentals import get_fundamentals
        fund = get_fundamentals(sym) or {}
    except Exception:
        pass
    info = {}
    try:
        info = get_info(sym) or {}
    except Exception:
        pass

    name = cname or fund.get("name") or info.get("name") or sym
    pe = fund.get("pe") or safe_val(info, "pe")
    fwd_pe = fund.get("fwd_pe")
    pb = fund.get("pb") or safe_val(info, "pb")
    roe = fund.get("roe")
    eps = fund.get("eps") or safe_val(info, "eps")
    mcap = fund.get("mcap") or info.get("market_cap")
    rev = fund.get("rev") or info.get("totalRevenue")
    # Yahoo debtToEquity is percent-scaled (125 means 1.25x), while
    # Screener/Finnhub normalized aggregations are already unitless.
    de = normalized_debt_equity_ratio({"debt_equity_ratio": fund.get("de")})
    if de is None:
        de = normalized_debt_equity_ratio(info)
    div_y = fund.get("div_y")
    beta = fund.get("beta") or safe_val(info, "beta")

    if all(v is None for v in (pe, pb, roe, mcap, rev, de, div_y, beta)):
        return f"❌ Fundamentals unavailable for <b>{name}</b> ({sym}) right now. Try again shortly."

    rows = [
        f"💰 <b>{name}</b>  ({sym}) — Fundamental Analysis",
        "━━━━━━━━━━━━━━━━━━━━",
        frow("Market Cap", fmt_mcap(mcap)),
        frow("Revenue", _fmt_revenue(rev, mcap)),
        frow("PE (TTM)", pe) + (f"  |  Fwd PE: {fwd_pe}" if fwd_pe else ""),
        frow("Price/Book", pb),
        frow("ROE", roe, "%") + (f"  |  EPS: ₹{eps}" if eps else ""),
        frow("Debt/Equity", f"{de:g}" if de is not None else None, "x") + (f"  |  Beta: {beta}" if beta else ""),
        frow("Div Yield", div_y, "%"),
        "━━━━━━━━━━━━━━━━━━━━",
        financial_source_summary(fund),
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Educational only. Not SEBI-registered advice.</i>",
    ]
    return "\n".join(rows)


# ── Screener Card ─────────────────────────────────────────────────────────
# A single bounded pool and gate prevent repeated scans from accumulating
# network threads. A 15s presentation deadline does NOT cancel running I/O.
SCREENER_STOCKS = {
    "conservative": [sym for sym, _sector in CONSERVATIVE_CANDIDATES],
    "moderate": ["RELIANCE", "BHARTIARTL", "AXISBANK", "MARUTI", "LT", "KOTAKBANK", "BAJFINANCE", "SUNPHARMA", "TITAN", "M&M",
                 "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "HINDUNILVR", "ASIANPAINT", "ULTRACEMCO", "POWERGRID", "NTPC"],
    # Exchange-approved new symbols following the 2025 corporate actions.
    "aggressive": ["TMPV", "ADANIENT", "JSWSTEEL", "TATAPOWER", "ETERNAL", "IRFC", "HAL", "BEL", "PFC", "ADANIPORTS",
                   "TRENT", "DIXON", "BSE", "CDSL", "RVNL", "MAZDOCK", "COCHINSHIP", "SOLARINDS", "POLYCAB", "INDIGO"],
}
_SCAN_TIMEOUT_SECONDS = 15
_SCAN_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="msk-scan")
_SCAN_GATE = threading.BoundedSemaphore(value=1)


def build_scan(profile):
    syms = SCREENER_STOCKS.get(profile, [])
    if not syms:
        return "❌ Unknown profile."
    if profile in ("moderate", "aggressive"):
        return run_investment_scan(profile, syms)
    if not _SCAN_GATE.acquire(blocking=False):
        return "⏳ A stock scan is still running. Please retry when the previous scan completes."

    labels = {"conservative": "🏦 CONSERVATIVE", "moderate": "⚖️ MODERATE", "aggressive": "🚀 AGGRESSIVE"}
    today_ist = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    title = (labels[profile] + " | BULLISH WATCHLIST") if profile == "conservative" else labels[profile]
    lines = [f"📊 <b>{title}</b>",
             f"📅 {today_ist.strftime('%d-%b-%Y')}", "━━━━━━━━━━━━━━━━━━━━"]
    futures = {}
    deferred_release = False

    def _fetch(sym):
        try:
            df = get_hist(sym, "6mo")
            if df is None or df.empty or len(df) < 28:
                return None
            if profile == "conservative":
                return evaluate_conservative(sym, df, today_ist)
            return evaluate_candidate(sym, df, profile, datetime.now(timezone(timedelta(hours=5, minutes=30))))
        except Exception:
            # A single upstream quote failure must never abort the whole scan.
            logger.debug("Screener quote unavailable for %s", sym)
            return None

    try:
        futures = {_SCAN_POOL.submit(_fetch, sym): sym for sym in syms}
        deadline = 25 if profile == "conservative" else _SCAN_TIMEOUT_SECONDS
        completed, unfinished = wait(futures, timeout=deadline)
        results = {}
        for future in completed:
            sym = futures[future]
            try:
                row = future.result()
                if row:
                    results[sym] = row
            except Exception:
                logger.debug("Screener worker failed for %s", sym)
        if unfinished:
            for future in unfinished:
                future.cancel()
            logger.warning("Screener %s partial: %d/%d futures unfinished at deadline",
                           profile, len(unfinished), len(futures))

        if profile == "conservative":
            picks = pick_diversified(
                [{"sym": sym, **row} for sym, row in results.items()], limit=10
            )
            lines.append(
                f"✅ <b>{len(picks)}/10 qualified bullish candidates</b> "
                f"(checks finished: {len(completed)}/{len(syms)})"
            )
            if not picks:
                lines.append("⚪ No stock passed every trend, liquidity and risk filter. Do not force a pick.")
            for rank, row in enumerate(picks, 1):
                sym = html.escape(row["sym"], quote=False)
                lines.append(
                    f"🟢 <b>{rank}. {sym}</b> [{row['sector']}]"
                    f"\n   Last completed close: ₹{row['ltp']:,.2f} ({row['chg']:+.2f}%)"
                    f"\n   EMA20 > EMA50 > EMA100 ✅ | RSI {row['rsi']}"
                    f"\n   20D: {row['move20_pct']:+.1f}% | ATR: {row['atr_pct']:.2f}%"
                    f" | Session: {row['asof']}"
                )
            if len(picks) < 10:
                lines.append(
                    f"ℹ️ Only {len(picks)} passed. Bearish/volatile stocks are excluded; "
                    "missing data is not treated as a pass."
                )
            if unfinished:
                lines.append(
                    f"⏳ {len(unfinished)} data checks missed the deadline; "
                    "results are incomplete."
                )
            lines.append(
                "\n⚠️ Technical shortlist only — NOT 10 verified safe investments. "
                "Financial strength, valuation, source dates and suitability "
                "need separate review before any client recommendation."
            )
            return "\n".join(lines)

        return format_shortlist(profile, list(results.values()), len(completed), len(syms), len(unfinished))
    finally:
        # Do not block the Telegram response while network calls finish.
        # Keep the scan gate closed until every outstanding task terminates;
        # this prevents a flood of abandoned per-request pools/threads.
        if futures and any(not f.done() for f in futures):
            def _release_on_completion(tasks=tuple(futures)):
                try:
                    wait(tasks)
                finally:
                    _SCAN_GATE.release()
            threading.Thread(target=_release_on_completion, daemon=True,
                             name="msk-scan-drain").start()
            deferred_release = True
        if not deferred_release:
            _SCAN_GATE.release()


# ── Build Market Health (flows, derivatives, macro + validated breadth) ────────
def build_breadth():
    # Preserve the callable for owner-only acceptance and old menu messages.
    from msk_market_health import build_market_health
    return build_market_health()


# ── Build Portfolio Card ─────────────────────────────────────────────────────
def build_portfolio_card(uid):
    """Never treat an unavailable quote as the purchase price or zero P&L."""
    import math
    import html
    p = portfolio.get(uid)
    if not p:
        return "📂 <b>Portfolio Empty</b>\n\nAdd: <code>/buy RELIANCE 10 2500</code>"

    rows, t_inv, priced_basis, priced_value = [], 0.0, 0.0, 0.0
    missing_quotes = []
    for sym, pos in p.items():
        qty, avg = pos["qty"], pos["avg"]
        ltp = None
        evidence = None
        try:
            quote = get_verified_quote(sym)
            if quote is not None:
                candidate = float(quote["price"])
                source = quote["quote_source"]
                asof = quote["quote_asof_ist"]
                status = quote["quote_status"]
                if (math.isfinite(candidate) and candidate > 0
                        and source in ("Yahoo Finance v8", "NSE India")
                        and isinstance(asof, str) and len(asof) >= 16
                        and status in ("recent_observation", "last_observed_session")):
                    ltp = round(candidate, 2)
                    session_label = ("recent observation" if status == "recent_observation"
                                     else "last observed session")
                    evidence = (f"{source} | {asof[:16].replace('T', ' ')} IST "
                                f"| {session_label}")
        except (TypeError, ValueError, KeyError, OverflowError):
            pass
        except Exception:
            logger.warning("Portfolio market quote lookup failed for %s", sym)
        inv = qty * avg
        t_inv += inv
        if ltp is None:
            missing_quotes.append(sym)
            rows.append((sym, qty, avg, inv, None, None, None, None))
            continue
        cur = qty * ltp
        priced_basis += inv
        priced_value += cur
        rows.append((sym, qty, avg, inv, ltp, cur - inv,
                     ((ltp - avg) / avg * 100) if avg > 0 else None, evidence))

    lines = ["<b>━━━ 💼 PORTFOLIO ━━━</b>",
             f"📅 {date.today().strftime('%d-%b-%Y')}  |  {len(rows)} holdings",
             "", "<b>── HOLDINGS ──</b>"]
    for sym, qty, avg, inv, ltp, pnl, pct, evidence in sorted(
        rows, key=lambda r: (r[5] is not None, r[5] or 0), reverse=True
    ):
        wt = inv / t_inv * 100 if t_inv > 0 else 0
        safe_sym = html.escape(str(sym), quote=False)
        if ltp is None:
            lines.extend([f"⚪ <b>{safe_sym}</b>",
                          f"   Qty:{qty} ({wt:.1f}%) Avg:₹{avg:,.2f} → LTP:Unavailable",
                          "   P&L: Unavailable (excluded from totals)", "   ···"])
        else:
            lines.extend([
                f"{'🟢' if pnl >= 0 else '🔴'} <b>{safe_sym}</b>",
                f"   Qty:{qty} ({wt:.1f}%) Avg:₹{avg:,.2f} → Observed:₹{ltp:,.2f}",
                f"   Source: {evidence}",
                f"   P&L: ₹{pnl:+,.2f} ({pct:+.2f}%)" if pct is not None else
                f"   P&L: ₹{pnl:+,.2f} (percentage unavailable)", "   ···",
            ])
    if priced_basis > 0:
        pnl = priced_value - priced_basis
        pct = pnl / priced_basis * 100
        label = "Partial P&L" if missing_quotes else "Total P&L"
        lines.append(f"\n{'🟢' if pnl >= 0 else '🔴'} <b>{label}: ₹{pnl:+,.2f} ({pct:+.2f}%)</b>")
    else:
        lines.append("\n⚪ <b>P&L unavailable — no valid market quotes</b>")
    lines.extend(["─" * 32,
                  "⏱️ P&L is indicative from dated quotes, not guaranteed real-time.",
                  "➕ /buy SYM QTY PRICE  ➖ /sell SYM",
                  "⚠️ <i>Educational only.</i>"])
    if missing_quotes:
        lines.append("⚠️ Missing quotes: " + ", ".join(html.escape(sym) for sym in missing_quotes)
                     + ". Excluded from P&L.")
        lines.append(f"📌 Cost basis: ₹{t_inv:,.2f}; priced holdings: ₹{priced_basis:,.2f}.")
    return "\n".join(lines)

# ── Keyboards ────────────────────────────────────────────────────────────────
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add("🔍 Analysis", "🩺 Market Health", "🔥 Stocks Buzz")
    kb.add("🏦 Large Cap Top 5", "⚖️ Mid Cap Top 5", "🚀 Small Cap Top 5")
    kb.add("🎯 Swing (Safe)", "🚀 Swing (Agr)", "⚡ Intraday Trade")
    kb.add("📈 Chart", "📄 Report")
    kb.add("🏭 Sector Report", "🩺 Portfolio Doctor", "🏗️ Portfolio Builder")
    kb.add("🆕 New Version")
    return kb


def analysis_choice_keyboard(sym):
    """Inline menu shown after a stock is found — lets the user pick exactly
    what they want instead of receiving one long combined message."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    if sym in BEES_ETFS:
        kb.add(
            types.InlineKeyboardButton("📊 Technical", callback_data=f"tech|{sym}"),
            types.InlineKeyboardButton("📈 Chart", callback_data=f"chart|{sym}"),
        )
        kb.add(types.InlineKeyboardButton("💼 ETF Profile", callback_data=f"fund|{sym}"))
        return kb
    kb.add(
        types.InlineKeyboardButton("📊 Technical", callback_data=f"tech|{sym}"),
        types.InlineKeyboardButton("💰 Fundamental", callback_data=f"fund|{sym}"),
    )
    kb.add(
        types.InlineKeyboardButton("📈 Chart", callback_data=f"chart|{sym}"),
        types.InlineKeyboardButton("📄 Full Report (PDF)", callback_data=f"pdf|{sym}"),
    )
    return kb


# ── Safe Sender ──────────────────────────────────────────────────────────────
def _record_telegram_api_ack(chat_id, result, kind):
    """Only a successful Bot API response may create a durable receipt.

    A message ID is an API acknowledgment, not end-user delivery/read proof.
    No raw message text, chart, PDF or private Telegram username is persisted.
    """
    message_id = getattr(result, "message_id", None)
    if type(message_id) is not int or message_id <= 0:
        return False
    logger.info("MSK_TELEGRAM_API_ACK kind=%s message_id=%d", kind, message_id)
    import os as _os  # function is also extracted in isolated delivery tests
    if _os.getenv("MSK_LIVE_ACCEPTANCE_RUN_ID"):
        try:
            from msk_live_acceptance import capture_telegram_ack
            # Store the actual Telegram-assigned file ID for document/photo
            # acceptance, never invent one from a local filename.
            file_id = None
            if kind == "document":
                file_id = getattr(getattr(result, "document", None), "file_id", None)
            elif kind == "photo":
                photos = getattr(result, "photo", None) or []
                if photos:
                    file_id = getattr(photos[-1], "file_id", None)
            capture_telegram_ack(kind, message_id, file_id=file_id,
                                 message_text=(getattr(result, "text", None)
                                               if kind == "text" else None))
        except Exception:
            logger.warning("MSK owner acceptance receipt recording unavailable")
    ledger = globals().get("_durable_update_ledger")
    if ledger is None:
        return False
    try:
        ledger.record_delivery_ack(chat_id, message_id, kind)
        return True
    except Exception as exc:
        # Do not resend a potentially delivered Telegram message on an ACK
        # storage error: doing so would create a duplicate customer post.
        logger.error("Telegram API receipt persistence failed (%s)",
                     type(exc).__name__)
        return False


def safe_send(chat_id, text, parse_mode="HTML", **kwargs):
    if text is None:
        return None
    from msk_telegram_text_safety import send_text_once
    sent = send_text_once(bot, chat_id, text, parse_mode, kwargs, logger)
    if sent is not None:
        _record_telegram_api_ack(chat_id, sent, "text")
    return sent


def _allow_heavy(m):
    """Guard direct commands/buttons that bypass the rate-limited text handler.
    Clear a stale analysis/report prompt when the user navigates elsewhere.
    """
    state.clear(m.chat.id)
    user_id = getattr(getattr(m, "from_user", None), "id", None)
    uid = user_id if user_id is not None else m.chat.id
    if API_RATE_LIMITER.is_allowed(uid):
        return True
    safe_send(m.chat.id, f"⚠️ Rate limited. Wait {RATE_LIMIT_WINDOW}s.")
    return False


# ── Command Handlers ─────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(m):
    from msk_release_stamp import release_banner
    state.clear(m.chat.id)
    safe_send(m.chat.id,
              "👋 <b>MSK Bot</b>\n" + release_banner() + "\n\n" +
              "Type any stock name or symbol for analysis.\n"
              "🔥 <b>Stocks Buzz</b> scans the top 1000 NSE stocks for fresh breakouts.\n"
              "⚡ <b>Intraday Trade</b> checks Nifty and Bank Nifty 15-minute "
              "setups; no trade if feeds are stale or NSE is closed.\n"              "🏦 Large / ⚖️ Mid / 🚀 Small Cap Top 5: NSE index-based bullish shortlists.\n"
              "📄 <code>/report SYMBOL</code> builds an institutional-style research PDF.\n"
              "🏭 <b>Sector Report</b> = Index &amp; sectoral update PDF (rotation, breadth, Nifty levels).\n"
              "Use menu buttons below.\n"
              "🔒 Usage count records Telegram ID, public name, feature and time; not message text.",

              reply_markup=main_keyboard())


@bot.message_handler(commands=["menu"])
@bot.message_handler(func=lambda m: (m.text or "").strip() == "🆕 New Version")
def cmd_menu(m):
    """Refresh the menu and show the running build version."""
    return cmd_start(m)


@bot.message_handler(commands=["help"])
def cmd_help(m):
    safe_send(m.chat.id,
              "📖 <b>Help</b>\n\nType symbol: <code>RELIANCE</code>\nChart: <code>/chart INFY 3mo</code>\nResearch PDF: <code>/report DLF</code>\nBreakouts: tap 🔥 Stocks Buzz or <code>/buzz</code>\nIntraday Nifty 200 stocks: tap ⚡ Intraday Trade or <code>/intraday</code>\nIndex &amp; sector PDF: tap 🏭 Sector Report or <code>/sectors</code>\nBuy: <code>/buy RELIANCE 10 2500</code>\nSell: <code>/sell RELIANCE</code>")


def run_report(chat_id, query):
    """Shared worker for /report SYMBOL and the 📄 Report button flow."""
    try:
        ticker, cname = resolve_symbol(query)
        sym = ticker.replace(".NS", "").replace(".BO", "") if ticker else query.upper().replace(" ", "")

        # A genuine, fully validated completed-session PDF can be delivered
        # immediately. Cold symbols still use the existing real generator.
        from research_pdf_snapshot import load_verified_report
        cached = load_verified_report(sym)
        if cached is not None:
            ok, result, name = True, cached[0], (cname or sym)
            logger.info("MSK_PDF_CACHE_HIT symbol=%s session=%s", sym, cached[1])
        else:
            safe_send(
                chat_id,
                f"📄 Building institutional research report for "
                f"<b>{cname or sym}</b>… (source data and charts take time)")
            done_event = threading.Event()

            def _ping():
                if done_event.wait(15):
                    return
                try:
                    safe_send(chat_id, "⏳ Still compiling the report…")
                except Exception:
                    pass
                if done_event.wait(20):
                    return
                try:
                    safe_send(chat_id,
                              "⏳ Almost there — rendering charts and PDF…")
                except Exception:
                    pass

            threading.Thread(target=_ping, daemon=True).start()
            try:
                ok, result, name = generate_stock_report_pdf(sym)
            finally:
                done_event.set()
        if ok:
            try:
                # The actual customer button must use the SAME release gate as
                # the verified-owner PDF probe. A readable one-page PDF is not
                # an acceptable complete research report.
                from pdf_release_gate import validate_research_pdf
                verified_pdf = validate_research_pdf(
                    result, expected_symbol=sym, min_pages=8)
                logger.info(
                    "MSK_PDF_VALIDATED kind=research pages=%d bytes=%d "
                    "scope=structure_and_disclosure_only",
                    verified_pdf["pages"], verified_pdf["bytes"])
            except Exception as exc:
                logger.error(
                    "MSK_PDF_WITHHELD reason=release_gate type=%s symbol=%s",
                    type(exc).__name__, sym)
                try:
                    os.remove(result)
                except OSError:
                    pass
                safe_send(chat_id,
                          "⚠️ Research PDF withheld: full 8-page structure, "
                          "source disclosures or content verification failed.")
                return
            try:
                with open(result, "rb") as f:
                    sent = bot.send_document(
                        chat_id, f,
                        visible_file_name=f"{sym}_Research_Report.pdf",
                        caption=(f"📄 <b>{name or sym} — Equity Research Report</b>\n"
                                 f"Stance · Technicals · Fundamentals · Ownership · Peers · Trade plan · Risks\n"
                                 f"Generated {datetime.now().strftime('%d-%b-%Y %H:%M')}\n\n"
                                 f"⚠️ Educational only. Not SEBI-registered advice."),
                        parse_mode="HTML",
                    )
                    _record_telegram_api_ack(chat_id, sent, "document")
            finally:
                try:
                    os.remove(result)
                except Exception:
                    pass
        else:
            # The generator's error string may contain provider/HTTP internals.
            # Never show it, or unescaped request text, to a customer.
            logger.warning("MSK_RESEARCH_PDF_GENERATION_WITHHELD symbol=%s", sym)
            safe_send(chat_id,
                      "❌ Research PDF unavailable: verified report data could "
                      "not be prepared. No unverified PDF was sent.")
    except Exception as exc:
        # Error details can include provider URLs, tokens or private paths.
        # The error class is enough for safe production diagnostics.
        logger.warning("MSK_RESEARCH_PDF_FAILED type=%s", type(exc).__name__)
        safe_send(chat_id,
                  "❌ Research PDF unavailable. Please retry later; "
                  "no unverified PDF was sent.")


def deliver_daily_reports(chat_id, profiles):
    """Rebuild every requested segment and send in strict Large/Mid/Small order."""
    for profile in profiles:
        try:
            message = render_latest_profile(profile)
            receipt = safe_send(chat_id, message)
            if receipt is None:
                logger.warning("MSK_DAILY_REPORT_SEND_UNCONFIRMED segment=%s", profile)
        except Exception as exc:
            logger.warning("MSK_DAILY_REPORT_WITHHELD segment=%s reason=%s",
                           profile, type(exc).__name__)
            # Never invent actionable prices in a missing-data scenario.
            safe_send(chat_id,
                      "⛔ STATUS: SCREENING COMPLETE — NO QUALIFYING SETUP TODAY\\n"
                      "Latest completed-session data is not accessible. "
                      "No prices or BUY calls can be confirmed.")


@bot.message_handler(commands=["top5", "top_5", "fullreport", "all", "lc", "mc", "sc"])
def cmd_daily_report(m):
    profiles = parse_daily_report_request(getattr(m, "text", None))
    if not profiles or not _allow_heavy(m):
        return
    executor.submit(deliver_daily_reports, m.chat.id, profiles)


@bot.message_handler(
    # Existing emoji buttons keep their direct scan handler; both paths
    # still invoke deliver_daily_reports() with exactly the same formatter.
    func=lambda m: not str(getattr(m, "text", "")).startswith(("🏦", "⚖️", "🚀")) and
                   bool(parse_daily_report_request(getattr(m, "text", None))),
    content_types=["text"],
)
def plain_daily_report(m):
    profiles = parse_daily_report_request(getattr(m, "text", None))
    if not profiles or not _allow_heavy(m):
        return
    executor.submit(deliver_daily_reports, m.chat.id, profiles)


@bot.message_handler(commands=["report"])
def cmd_report(m):
    if not _allow_heavy(m):
        return
    parts = m.text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        executor.submit(deliver_daily_reports, m.chat.id, ("conservative",))
        return

    raw_q = parts[1].strip()
    cid = m.chat.id
    safe_send(cid, f"🔍 Looking up <b>{raw_q}</b>…")
    executor.submit(run_report, cid, raw_q)


# ── Find-then-choose flow ─────────────────────────────────────────────────
# Typing a stock name/symbol no longer dumps the full combined card straight
# away. Instead: resolve the symbol, confirm what was found with a live LTP,
# then offer Technical / Fundamental / Chart / Full Report as buttons so the
# user picks exactly what they want.
def send_symbol_menu(chat_id, query):
    """Resolve the requested instrument before fetching completed NSE candles."""
    request = str(query or "").strip().upper()
    if request.startswith("BSE:") or request.endswith((".BO", ":BSE")):
        safe_send(chat_id, "Analysis currently supports NSE instruments. Enter the NSE symbol if that is the listing you want.",
                  reply_markup=main_keyboard())
        return
    if request in {"BEES", "BEES ETF", "ETF BEES"}:
        safe_send(chat_id, "Which BeES ETF? Try NIFTYBEES, GOLDBEES, BANKBEES, JUNIORBEES or LIQUIDBEES.")
        return
    etf_symbol = bees_etf_symbol(query)
    try:
        if etf_symbol:
            ticker, company = f"{etf_symbol}.NS", f"Nippon India ETF {BEES_ETFS[etf_symbol]}"
        else:
            ticker, company = resolve_symbol(query)
        if not ticker or not ticker.endswith(".NS"):
            safe_send(chat_id, "❌ NSE ticker not unambiguous. Enter its full NSE symbol (e.g. SBIN).",
                      reply_markup=main_keyboard())
            return
        sym = ticker[:-3]
        snapshot = inspect_history(get_hist(sym, "1y"))
        if snapshot is None and etf_symbol:
            safe_send(chat_id, format_etf_profile(etf_symbol) +
                      "\n\n⚠️ Recent completed price history could not be verified.")
            return
        if snapshot is None:
            safe_send(chat_id,
                      f"⚠️ <b>{html.escape(sym, quote=False)}</b>: recent completed price history "
                      "could not be verified. No recommendation issued. Check the symbol or retry later.",
                      reply_markup=main_keyboard())
            return
        safe_send(chat_id, format_snapshot(sym, company, snapshot, is_etf=bool(etf_symbol)),
                  reply_markup=analysis_choice_keyboard(sym))
    except Exception as exc:
        logger.warning("Analysis symbol validation unavailable (%s)", type(exc).__name__)
        if etf_symbol:
            safe_send(chat_id, format_etf_profile(etf_symbol) +
                      "\n\n⚠️ Recent completed price history could not be verified.")
            return
        safe_send(chat_id, "⚠️ Instrument price history could not be verified.",
                  reply_markup=main_keyboard())

@bot.callback_query_handler(func=lambda call: True)
def handle_analysis_callback(call):
    try:
        action, _, sym = (call.data or "").partition("|")
    except Exception:
        action, sym = "", ""
    chat_id = call.message.chat.id
    state.clear(chat_id)
    sender_id = getattr(getattr(call, "from_user", None), "id", None)
    uid = sender_id if sender_id is not None else chat_id
    if not API_RATE_LIMITER.is_allowed(uid):
        try:
            bot.answer_callback_query(call.id, text="Rate limit reached; retry later.")
        except Exception:
            pass
        return

    if action == "builder":
        portfolio_builder_option(call, sym)
        return

    if not action or not sym or not re.fullmatch(r"[A-Za-z0-9&_.-]{1,30}", sym):
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return

    try:
        bot.answer_callback_query(call.id, text="Working on it…")
    except Exception:
        pass

    if action in ("fund", "pdf"):
        from analysis_quality import BEES_ETFS, format_etf_profile
        if sym in BEES_ETFS:
            safe_send(chat_id, format_etf_profile(sym))
            return

    if action == "tech":
        def _run(cid=chat_id, s=sym):
            try:
                ticker, cname = resolve_symbol(s)
                safe_send(cid, build_technical_text(s, cname))
            except Exception as e:
                logger.error(f"tech cb err: {e}", exc_info=True)
                safe_send(cid, f"❌ Error: {e}")
        executor.submit(_run)

    elif action == "fund":
        def _run(cid=chat_id, s=sym):
            try:
                ticker, cname = resolve_symbol(s)
                safe_send(cid, build_fundamental_text(s, cname))
            except Exception as e:
                logger.error(f"fund cb err: {e}", exc_info=True)
                safe_send(cid, f"❌ Error: {e}")
        executor.submit(_run)

    elif action == "chart":
        safe_send(chat_id, f"📈 Generating chart for <b>{sym}</b>… (~20s)")

        def _run(cid=chat_id, s=sym):
            try:
                ticker, cname = resolve_symbol(s)
                gen = get_chart_generator()
                success, meta, path = gen.generate(f"{s}.NS", cname or s)
                if success and path:
                    with open(path, "rb") as f:
                        sent = bot.send_photo(cid, f, caption=f"<b>📈 {cname or s}</b>\n\n{meta}", parse_mode="HTML")
                        _record_telegram_api_ack(cid, sent, "photo")
                else:
                    safe_send(cid, "⚠️ Chart failed, sending text instead:")
                    safe_send(cid, build_technical_text(s, cname))
            except Exception as e:
                logger.error(f"chart cb err: {e}", exc_info=True)
                safe_send(cid, f"❌ Error: {e}")
        executor.submit(_run)

    elif action == "pdf":
        safe_send(chat_id, f"📄 Preparing research report for <b>{sym}</b>…")
        executor.submit(run_report, chat_id, sym)

    elif action == "doctorpdf":
        # Offered ONLY on a privately delivered Doctor PDF. An explicit tap
        # requests a separate full 8+ page public-company report; never
        # schedule five heavy PDFs or reuse saved/private holdings.
        if (getattr(getattr(call.message, "chat", None), "type", None) != "private"
                or type(sender_id) is not int or sender_id != chat_id):
            safe_send(chat_id, "Full portfolio-linked research buttons work only in private chat.")
            return
        try:
            ticker, _ = resolve_symbol(sym)
        except Exception:
            ticker = None
        # A Doctor holding may be a BSE-only stock; never resolve a bare
        # callback to another exchange or an unverified security identity.
        if not ticker or not ticker.upper().endswith(".NS"):
            safe_send(chat_id, "NSE identity not confirmed for this full research report.")
            return
        safe_send(chat_id, f"📄 Preparing separate full research PDF for <b>{html.escape(sym)}</b>…")
        executor.submit(run_report, chat_id, sym)

    elif action == "menu":
        safe_send(chat_id, f"What would you like to see for <b>{sym}</b>?",
                  reply_markup=analysis_choice_keyboard(sym))
    else:
        safe_send(chat_id, "⚠️ This button is no longer supported. Use /start to refresh the MSK Bot menu.")


@bot.message_handler(commands=["chart"])
def cmd_chart(m):
    if not _allow_heavy(m):
        return
    parts = m.text.strip().split()
    if len(parts) < 2:
        safe_send(m.chat.id, "📈 Usage: <code>/chart SYMBOL [period]</code>")
        return

    raw_q = " ".join(parts[1:])
    per = None
    if parts[-1] in {"1mo", "3mo", "6mo", "1y", "2y"}:
        per = parts[-1]
        raw_q = " ".join(parts[1:-1])

    safe_send(m.chat.id, f"🔍 Looking up <b>{raw_q}</b>…")

    def _run(chat_id=m.chat.id, query=raw_q, period=per):
        try:
            ticker, cname = resolve_symbol(query)
            if not ticker:
                safe_send(chat_id, f"❌ Could not find <b>{query}</b>")
                return
            sym = ticker.replace(".NS", "").replace(".BO", "")
            safe_send(chat_id, f"📈 Generating chart for <b>{cname}</b>… (~20s)")
            gen = get_chart_generator()
            args = [ticker, cname] + ([period] if period else [])
            success, meta, path = gen.generate(*args)
            if success and path:
                with open(path, "rb") as f:
                    sent = bot.send_photo(chat_id, f, caption=f"<b>📈 {cname}</b>\n\n{meta}", parse_mode="HTML")
                    _record_telegram_api_ack(chat_id, sent, "photo")
            else:
                safe_send(chat_id, "⚠️ Chart failed, sending text:")
                safe_send(chat_id, build_adv(sym))
        except Exception as e:
            logger.error(f"Chart err: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")

    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "📈 Chart")
def chart_button(m):
    if not _allow_heavy(m):
        return
    safe_send(m.chat.id, "📈 Scanning Nifty 250 for best crossover… (~30s)")

    def _run(chat_id=m.chat.id):
        def ping():
            time.sleep(12)
            try:
                safe_send(chat_id, "⏳ Still scanning…")
            except Exception:
                pass
        threading.Thread(target=ping, daemon=True).start()
        try:
            gen = get_chart_generator()
            gen.send_to_telegram(bot, chat_id)
        except Exception as e:
            logger.error(f"Auto chart err: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")

    executor.submit(_run)


def _require_private_portfolio(m):
    """Never mutate or expose a group-owned portfolio through chat IDs."""
    chat = getattr(m, "chat", None)
    actor = getattr(m, "from_user", None)
    chat_id = getattr(chat, "id", None)
    actor_id = getattr(actor, "id", None)
    if (getattr(chat, "type", None) == "private" and
            type(chat_id) is int and chat_id > 0 and
            type(actor_id) is int and actor_id == chat_id):
        return True
    if type(chat_id) is int:
        safe_send(chat_id, "🔒 Portfolio changes and holdings are available only in your private bot chat.")
    return False


@bot.message_handler(commands=["buy"])
def cmd_buy(m):
    if not _allow_heavy(m):
        return
    if not _require_private_portfolio(m):
        return
    import math
    import html
    parts = m.text.strip().split()
    if len(parts) != 4:
        safe_send(m.chat.id, "Usage: <code>/buy SYM QTY PRICE</code>")
        return
    try:
        qty = int(parts[2])
        price = float(parts[3])
    except ValueError:
        safe_send(m.chat.id, "❌ Invalid format.")
        return
    if qty <= 0 or qty > 10**9 or not math.isfinite(price) or not 0 < price <= 10**12:
        safe_send(m.chat.id, "❌ Quantity and price must be finite and positive.")
        return
    ticker, _ = resolve_symbol(parts[1])
    sym = ticker.replace(".NS", "").replace(".BO", "") if ticker else parts[1].upper().replace(".NS", "")
    try:
        portfolio.add(m.chat.id, sym, qty, price)
    except (ValueError, RuntimeError, OSError):
        logger.exception("Portfolio buy was rejected or storage failed")
        safe_send(m.chat.id, "⚠️ Holding NOT saved; please retry or contact support.")
        return
    try:
        stored = portfolio.get(m.chat.id)
        position = stored.get(sym)
        if not position or float(position["qty"]) < qty:
            raise RuntimeError("Portfolio write/readback mismatch")
    except (ValueError, RuntimeError, OSError, TypeError, KeyError):
        logger.exception("Portfolio saved but post-write readback failed")
        safe_send(m.chat.id, "⚠️ Portfolio write could not be verified. Please contact support before repeating.")
        return
    storage = ("durable database" if os.getenv("PORTFOLIO_BACKEND", "legacy_json").lower() == "postgres"
               else "temporary server file; backup required")
    safe_send(m.chat.id,
              f"✅ Portfolio saved and read back ({storage}): <b>{qty}×{html.escape(sym)}</b> @ ₹{price:.2f}")


@bot.message_handler(commands=["sell"])
def cmd_sell(m):
    if not _allow_heavy(m):
        return
    if not _require_private_portfolio(m):
        return
    import html
    parts = m.text.strip().split()
    if len(parts) < 2:
        safe_send(m.chat.id, "Usage: <code>/sell SYM</code>")
        return
    sym = " ".join(parts[1:]).upper().replace(".NS", "").replace(".BO", "")
    try:
        removed = portfolio.remove(m.chat.id, sym)
    except (ValueError, RuntimeError, OSError):
        logger.exception("Portfolio sell was rejected or storage failed")
        safe_send(m.chat.id, "⚠️ Holding NOT removed; storage unavailable.")
        return
    if removed:
        try:
            if sym in portfolio.get(m.chat.id):
                raise RuntimeError("Portfolio delete/readback mismatch")
        except (ValueError, RuntimeError, OSError, TypeError):
            logger.exception("Portfolio removal readback failed")
            safe_send(m.chat.id, "⚠️ Portfolio removal could not be verified. Contact support.")
            return
        storage = ("durable database" if os.getenv("PORTFOLIO_BACKEND", "legacy_json").lower() == "postgres"
                   else "temporary server file; backup required")
        safe_send(m.chat.id, f"✅ Portfolio save verified ({storage}); removed <b>{html.escape(sym)}</b>.")
    else:
        safe_send(m.chat.id, f"❌ <b>{html.escape(sym)}</b> not found.")


@bot.message_handler(commands=["portfolio"])
def cmd_portfolio(m):
    if not _allow_heavy(m):
        return
    if not _require_private_portfolio(m):
        return
    safe_send(m.chat.id, "⏳ Loading…")

    def _run(chat_id=m.chat.id):
        try:
            safe_send(chat_id, build_portfolio_card(chat_id))
        except Exception as e:
            logger.error(f"Portfolio err: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")

    executor.submit(_run)


# Portfolio Doctor private acceptance handler. Disabled by default and never
# reads or writes legacy customer portfolio storage.
_portfolio_doctor_lock = threading.Lock()
_portfolio_doctor_running = set()
# Pending state contains only user IDs and expires after three minutes.
_portfolio_doctor_waiting = {}
# User IDs + expiry only. Does not retain any position, cost or file.
_portfolio_audit_mode = {}
# Bound memory and provider demand without storing any client holdings.
_portfolio_doctor_slots = threading.BoundedSemaphore(2)


def _doctor_allowed(m):
    uid = getattr(getattr(m, "from_user", None), "id", None)
    owner = os.getenv("MSK_PORTFOLIO_DOCTOR_TEST_USER_ID", "").strip()
    private_user = (getattr(getattr(m, "chat", None), "type", None) == "private"
                    and type(uid) is int and uid > 0 and uid == m.chat.id)
    owner_test = (os.getenv("MSK_PORTFOLIO_DOCTOR_TEST_ENABLED", "") == "1"
                  and owner.isdecimal() and str(uid) == owner)
    customer_access = os.getenv("MSK_PORTFOLIO_DOCTOR_ENABLED", "") == "1"
    return private_user and (customer_access or owner_test)


@bot.message_handler(commands=["audit"])
def portfolio_audit_start(m):
    """Owner-test-only upgraded PDF; leave the current /doctor untouched."""
    if not _doctor_allowed(m):
        safe_send(m.chat.id, "Portfolio Audit is available in enabled private chats only.")
        return
    with _portfolio_doctor_lock:
        if m.chat.id in _portfolio_doctor_running:
            safe_send(m.chat.id, "A private report is already running.")
            return
        expiry = time.monotonic() + 180
        _builder_form.pop(m.chat.id, None)  # Switching modes cancels stale Builder input.
        _portfolio_doctor_waiting[m.chat.id] = expiry
        _portfolio_audit_mode[m.chat.id] = expiry
    safe_send(m.chat.id,
              "Portfolio Audit test: send 1-5 NSE holdings as SYMBOL QTY AVERAGE_BUY, "
              "one per line, or upload CSV/XLSX. This 10-14 page private research PDF "
              "does not save holdings and uses verified completed closing prices. "
              "The existing /doctor report remains unchanged.")


@bot.message_handler(commands=["doctor", "portfolio_doctor"])
@bot.message_handler(func=lambda m: m.text == "🩺 Portfolio Doctor")
def portfolio_doctor_start(m):
    """Customer-facing stateless report, never accesses saved Portfolio holdings."""
    if not _doctor_allowed(m):
        safe_send(m.chat.id, "🩺 Portfolio Doctor is available in private chat only, when enabled.")
        return
    with _portfolio_doctor_lock:
        if m.chat.id in _portfolio_doctor_running:
            safe_send(m.chat.id, "A Portfolio Doctor report is already running.")
            return
        _portfolio_audit_mode.pop(m.chat.id, None)
        _builder_form.pop(m.chat.id, None)  # Holdings must never hit allocation parsing.
        _portfolio_doctor_waiting[m.chat.id] = time.monotonic() + 180
    safe_send(m.chat.id, "🩺 <b>Portfolio Doctor</b>\n"
              "Upload CSV/XLSX or paste 1–5 rows: <code>SBIN 100 840</code> then "
              "<code>BHEL 150 150</code> on a new line. Numbered rows such as "
              "<code>1. SBIN 100 840</code> also work; upper/lower case is accepted. "
              "Use Symbol Quantity AverageBuyPrice; .NS/.BO is optional. "
              "NSE is preferred; BSE requires an exact verified match. "
              "(maximum 5 stocks / 5 MB). A private educational PDF is sent only with "
              "verified recent closing prices. MSK does not save your holdings or report. "
              "Telegram retains chat messages and files under its own rules.")

@bot.message_handler(commands=["portfolio_doctor_test"])
def portfolio_doctor_test(m):
    """Legacy owner test command; public customers use /doctor or menu."""
    if not _doctor_allowed(m):
        safe_send(m.chat.id, "Portfolio Doctor is not available.")
        return
    with _portfolio_doctor_lock:
        _builder_form.pop(m.chat.id, None)
        _portfolio_audit_mode.pop(m.chat.id, None)
        _portfolio_doctor_waiting[m.chat.id] = time.monotonic() + 180
    safe_send(m.chat.id, "Private test: upload a CSV or XLSX with Symbol, Qty, Buy Price (maximum 5 MB; 5 stocks).")


def portfolio_doctor_text(m):
    """One-time typed positions, parsed without any holdings database."""
    from portfolio_doctor_live import parse_holdings, PortfolioDoctorError

    raw = m.text or ""
    payload = raw.encode("utf-8")
    if len(payload) > 2048 or not raw.strip():
        safe_send(m.chat.id, "Paste up to 5 rows, one per line, for example SBIN 100 840. You can retry in this session.")
        return
    try:
        parse_holdings("typed_holdings.csv", payload)
    except PortfolioDoctorError:
        safe_send(m.chat.id, "Invalid holdings. Use Symbol Quantity AverageBuyPrice, for example SBIN 100 840. You can retry in this session.")
        return
    portfolio_doctor_document(m, _typed_payload=payload)


@bot.message_handler(content_types=["document"])
def portfolio_doctor_document(m, _typed_payload=None):
    """No database, saved holdings or persistent file: receive -> PDF -> delete."""
    if not _doctor_allowed(m):
        return
    from portfolio_doctor_live import MAX_BYTES
    uid = m.chat.id
    with _portfolio_doctor_lock:
        expiry = _portfolio_doctor_waiting.get(uid, 0)
        if expiry <= time.monotonic():
            _portfolio_doctor_waiting.pop(uid, None)
            _portfolio_audit_mode.pop(uid, None)
            return
        if uid in _portfolio_doctor_running:
            safe_send(uid, "A Portfolio Doctor report is already running.")
            return
        if not _portfolio_doctor_slots.acquire(blocking=False):
            safe_send(uid, "Portfolio Doctor is busy. Resend your holdings shortly.")
            return
        audit_expiry = _portfolio_audit_mode.pop(uid, 0)
        audit_requested = audit_expiry > time.monotonic()
        _portfolio_doctor_waiting.pop(uid, None)
        _portfolio_doctor_running.add(uid)

    def finish_request(*, allow_retry=False):
        with _portfolio_doctor_lock:
            _portfolio_doctor_running.discard(uid)
            now = time.monotonic()
            if allow_retry and expiry > now and uid not in _builder_form:
                _portfolio_doctor_waiting[uid] = expiry
                if audit_requested and audit_expiry > now:
                    _portfolio_audit_mode[uid] = audit_expiry
            _portfolio_doctor_slots.release()

    def _run():
        import io
        from portfolio_doctor_live import PortfolioDoctorError, run_isolated_memory
        allow_retry = False
        try:
            if _typed_payload is not None:
                name, payload = "typed_holdings.csv", _typed_payload
            else:
                doc = m.document
                name = (doc.file_name or "").lower()
                if not name.endswith((".csv", ".xlsx")) or (doc.file_size or 0) > MAX_BYTES:
                    allow_retry = True
                    safe_send(uid, "Only CSV/XLSX up to 5 MB, with 1–5 holdings, are supported. Resend a corrected file or paste your holdings.")
                    return
                file_info = bot.get_file(doc.file_id)
                if file_info.file_size and file_info.file_size > MAX_BYTES:
                    allow_retry = True
                    safe_send(uid, "File exceeds the 5 MB limit. Resend a corrected file or paste your holdings.")
                    return
                payload = bot.download_file(file_info.file_path)
            if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_BYTES:
                allow_retry = True
                safe_send(uid, "Empty or oversized input. No report generated. Resend a corrected file or paste your holdings.")
                return
            safe_send(uid, "Checking recent market prices for your private, one-time report.")
            # PDF and holdings travel through worker stdin/stdout, never a local file.
            if audit_requested:
                pdf_bytes = run_isolated_memory(name, payload, timeout=150, audit=True)
            else:
                pdf_bytes = run_isolated_memory(name, payload, timeout=150)
            # A full 9-page company report is an existing separate product.
            # Offer it only on explicit tap, rather than automatically building
            # five slow PDFs inside the private/stateless Doctor worker.
            research_markup = None
            try:
                from portfolio_doctor_research_links import full_research_choices
                choices = full_research_choices(name, payload)
                if choices:
                    research_markup = types.InlineKeyboardMarkup(row_width=2)
                    for symbol, callback_data in choices:
                        research_markup.add(types.InlineKeyboardButton(
                            "📘 Full research: " + symbol, callback_data=callback_data))
            except Exception:
                # Optional navigation cannot withhold an otherwise verified PDF.
                research_markup = None
            with io.BytesIO(pdf_bytes) as document:
                document.name = ("MSK_Portfolio_Audit.pdf" if audit_requested
                                 else "MSK_Portfolio_Doctor.pdf")
                receipt = bot.send_document(
                    uid, document,
                    caption=(("MSK Portfolio Audit | Private, on-demand research PDF. "
                              if audit_requested else
                              "MSK Portfolio Doctor | Private educational PDF. ") +
                             ("Tap a stock below for separate detailed company research. "
                              if research_markup else "") +
                             "Educational only; not investment instructions."),
                    timeout=30, protect_content=True, reply_markup=research_markup)
            message_id = getattr(receipt, "message_id", None)
            sent = getattr(receipt, "document", None)
            if not isinstance(message_id, int) or message_id <= 0 or not getattr(sent, "file_id", None):
                raise PortfolioDoctorError("WORKER_FAILED")
            # Proof of delivery without retaining symbol, quantity, user, upload
            # hash, file ID, or Telegram message ID in an MSK database/log.
            logger.info("MSK_PORTFOLIO_DOCTOR_DELIVERY state=SENT receipt_verified=true")
        except PortfolioDoctorError as exc:
            logger.warning("MSK_PORTFOLIO_DOCTOR_HEALTH_ALERT error_code=%s", exc.code)
            if exc.code == "BAD_INPUT":
                allow_retry = True
                safe_send(uid, "Invalid holdings. Send up to 5 rows: Symbol Qty Buy Price. Resend a corrected file or paste your holdings.")
            elif exc.code in ("STALE_PRICES", "NO_PRICES"):
                safe_send(uid, "Verified recent closing prices unavailable. No estimates or PDF issued. Please retry later.")
            elif exc.code == "TIMEOUT":
                safe_send(uid, "Portfolio Doctor timed out. No holdings were saved. Please retry later.")
            else:
                safe_send(uid, "Portfolio Doctor could not generate a verified PDF. No holdings were saved.")
        except Exception as exc:
            logger.error("MSK_PORTFOLIO_DOCTOR_HEALTH_ALERT error_type=%s",
                         type(exc).__name__)
            safe_send(uid, "Portfolio Doctor temporarily unavailable. No client holdings were saved.")
        finally:
            finish_request(allow_retry=allow_retry)

    try:
        executor.submit(_run)
    except Exception:
        finish_request(allow_retry=True)
        safe_send(uid, "Unable to start Portfolio Doctor. Resend your holdings shortly.")


# Portfolio Doctor's optional new-capital builder is independent of saved holdings.
# It uses a single private message and a disposable in-memory worker.
def _builder_allowed(m):
    """Only private one-to-one Telegram chats; no permission escalation for /doctor."""
    uid = getattr(getattr(m, "from_user", None), "id", None)
    return (getattr(getattr(m, "chat", None), "type", None) == "private"
            and type(uid) is int and uid > 0 and uid == m.chat.id)


_builder_form = {}

def _builder_options(stage, choices):
    kb = types.InlineKeyboardMarkup(row_width=len(choices))
    kb.add(*[types.InlineKeyboardButton(label, callback_data="builder|" + stage + "|" + val)
             for label, val in choices])
    return kb


@bot.message_handler(func=lambda m: (m.text or "") == "🏗️ Portfolio Builder")
def portfolio_builder_button(m):
    """Only offer the three-input wizard when approved real research is ready."""
    from msk_ai_builder_automatic import automatic_available
    if not _builder_allowed(m):
        safe_send(m.chat.id, "Portfolio Builder is available in private chat only.")
        return
    uid = m.chat.id
    with _portfolio_doctor_lock:
        _builder_form.pop(uid, None)
        _portfolio_doctor_waiting.pop(uid, None)
        _portfolio_audit_mode.pop(uid, None)
    state.clear(uid)
    if not automatic_available():
        safe_send(uid,
                  "🏗️ Automatic Portfolio Builder is temporarily unavailable. "
                  "A verified, approved stock research basket is not yet connected. "
                  "You will NOT be asked to select symbols or percentages. "
                  "No unverified portfolio will be generated. "
                  "For existing holdings, select 🩺 Portfolio Doctor.")
        return
    with _portfolio_doctor_lock:
        if uid in _portfolio_doctor_running:
            safe_send(uid, "Another private report is already running.")
            return
        _builder_form[uid] = {"mode": "automatic", "step": "amount",
                              "until": time.monotonic() + 180}
    safe_send(uid, "🏗️ Automatic Portfolio Builder — Step 1/3\n"
              "Enter investment amount in rupees (₹1 to ₹1.5 crore). "
              "Stocks and allocations are selected from approved research. "
              "Type /cancel to stop.")

@bot.message_handler(commands=["builder_v2_preview"])
def portfolio_builder_private_beta(m):
    from msk_ai_builder_telegram import preview_allowed
    if not _builder_allowed(m) or not preview_allowed(m.chat.id):
        safe_send(m.chat.id, "Private Portfolio Builder Beta is unavailable.")
        return
    uid = m.chat.id
    with _portfolio_doctor_lock:
        if uid in _portfolio_doctor_running:
            safe_send(uid, "Another private report is already running.")
            return
        _portfolio_doctor_waiting.pop(uid, None)
        _builder_form[uid] = {"mode": "private_beta", "step": "amount",
                              "until": time.monotonic() + 180}
    state.clear(uid)
    safe_send(uid, "TEST MODE — Portfolio Builder Step 1/3. Enter capital in rupees (1 to 1.5 crore). Fictional stocks only.")


def send_private_builder_beta(uid, entry, horizon):
    from msk_ai_builder_telegram import preview_allowed, deliver_preview
    if not preview_allowed(uid):
        safe_send(uid, "Private Beta is unavailable.")
        return
    with _portfolio_doctor_lock:
        if _builder_form.get(uid) is not entry or entry["until"] <= time.monotonic():
            notice = "Beta session expired. Use /builder_v2_preview."
        elif uid in _portfolio_doctor_running or not _portfolio_doctor_slots.acquire(blocking=False):
            notice = "Report busy. Tap your horizon again shortly."
        else:
            notice = None
            _builder_form.pop(uid)
            _portfolio_doctor_running.add(uid)
    if notice:
        safe_send(uid, notice)
        return

    def release():
        with _portfolio_doctor_lock:
            _portfolio_doctor_running.discard(uid)
            _portfolio_doctor_slots.release()

    def run():
        try:
            receipt = deliver_preview(bot, uid, int(entry["amount"]), entry["risk"], int(horizon))
            logger.info("MSK_PRIVATE_BETA_DELIVERED message_id=%s", receipt.message_id)
        except Exception as exc:
            logger.warning("MSK_PRIVATE_BETA_UNCONFIRMED kind=%s", type(exc).__name__)
            safe_send(uid, "Beta delivery not confirmed. Check your chat before starting another request.")
        finally:
            release()
    try:
        executor.submit(run)
    except Exception:
        release()
        safe_send(uid, "Beta request could not start. Use /builder_v2_preview.")



def send_automatic_builder_pdf(uid, entry, horizon):
    """Consume a valid session exactly once before attempting one PDF send."""
    from msk_ai_builder_automatic import automatic_available, ResearchUnavailable
    from msk_ai_builder_telegram import deliver_automatic_pdf

    if not automatic_available():
        with _portfolio_doctor_lock:
            if _builder_form.get(uid) is entry:
                _builder_form.pop(uid, None)
        safe_send(uid, "NO VERIFIED PORTFOLIO AVAILABLE. Approved research or prices are missing.")
        return
    with _portfolio_doctor_lock:
        if _builder_form.get(uid) is not entry or entry["until"] <= time.monotonic():
            notice = "Builder session expired. Tap Portfolio Builder to restart."
        elif uid in _portfolio_doctor_running or not _portfolio_doctor_slots.acquire(blocking=False):
            notice = "Portfolio Builder is busy. Tap your horizon again shortly."
        else:
            notice = None
            _builder_form.pop(uid, None)
            _portfolio_doctor_running.add(uid)
    if notice:
        safe_send(uid, notice)
        return

    def release():
        with _portfolio_doctor_lock:
            _portfolio_doctor_running.discard(uid)
            _portfolio_doctor_slots.release()

    def run():
        try:
            receipt = deliver_automatic_pdf(
                bot, uid, int(entry["amount"]), entry["risk"], int(horizon))
            logger.info("MSK_AUTO_BUILDER_DELIVERY state=SENT receipt_verified=true message_id=%s",
                        receipt.message_id)
        except ResearchUnavailable as exc:
            logger.warning("MSK_AUTO_BUILDER_WITHHELD code=%s", str(exc))
            safe_send(uid, "NO VERIFIED PORTFOLIO AVAILABLE. No stocks or invented figures were sent.")
        except Exception as exc:
            logger.error("MSK_AUTO_BUILDER_DELIVERY_UNVERIFIED type=%s", type(exc).__name__)
            safe_send(uid, "Builder PDF delivery is unverified. Check this chat before trying again.")
        finally:
            release()
    try:
        executor.submit(run)
    except Exception:
        release()
        safe_send(uid, "Builder request could not start. No portfolio was sent.")

def portfolio_builder_option(call, value):
    uid = getattr(getattr(call, "from_user", None), "id", None)
    chat = getattr(getattr(call, "message", None), "chat", None)
    if chat is None or chat.type != "private" or type(uid) is not int or uid != chat.id:
        return
    stage, _, option = value.partition("|")
    with _portfolio_doctor_lock:
        entry = _builder_form.get(uid)
        if entry is None or entry["until"] <= time.monotonic():
            _builder_form.pop(uid, None)
            next_step = "expired"
        elif stage == "risk" and entry["step"] == "risk" and option in ("low", "medium", "high"):
            entry.update(risk=option, step="horizon", until=time.monotonic() + 180)
            next_step = "horizon"
        elif stage == "horizon" and entry["step"] == "horizon" and option in ("6", "18", "36", "60"):
            if entry.get("mode") == "private_beta":
                next_step = "private_beta"
            elif entry.get("mode") == "automatic":
                next_step = "automatic"
            else:
                entry.update(horizon=option, step="stocks", until=time.monotonic() + 180)
                next_step = "stocks"
        else:
            next_step = "invalid"
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    if next_step == "private_beta":
        send_private_builder_beta(uid, entry, option)
    elif next_step == "automatic":
        send_automatic_builder_pdf(uid, entry, option)
    elif next_step == "horizon":
        steps = 3 if entry.get("mode") in ("private_beta", "automatic") else 5
        safe_send(uid, f"Step 3/{steps} — Select time frame:",
                  reply_markup=_builder_options("horizon", [
                      ("6 months", "6"), ("18 months", "18"),
                      ("36 months", "36"), ("60 months", "60")]))
    elif next_step == "stocks":
        safe_send(uid, "Step 4/5 — Type 1–5 symbols and your chosen percentages:\n"
                  "<code>SBIN 25\nBHEL 20\nINFY 15</code>\n"
                  "Total percentages cannot exceed 100%. Stocks are NOT selected by AI.")
    else:
        safe_send(uid, "Selection expired or not valid. Tap Portfolio Builder to restart.")


def portfolio_builder_form_text(m, text):
    """Temporary form fields only; return True if builder consumed this input."""
    uid = m.chat.id
    with _portfolio_doctor_lock:
        entry = _builder_form.get(uid)
        if not entry:
            return False
        if uid in _portfolio_doctor_running:
            entry = None
            processing = True
        else:
            processing = False
        if entry and entry["until"] <= time.monotonic():
            _builder_form.pop(uid, None)
            entry = None
    if processing:
        safe_send(uid, "Your Portfolio Builder report is being processed.")
        return True
    if not entry:
        safe_send(uid, "Form expired. Tap Portfolio Builder again.")
        return True
    if text.strip().lower() in ("/cancel", "cancel"):
        with _portfolio_doctor_lock:
            _builder_form.pop(uid, None)
        safe_send(uid, "Builder cancelled. No details saved.")
        return True
    beta = entry.get("mode") == "private_beta"
    automatic = entry.get("mode") == "automatic"
    if automatic:
        from msk_ai_builder_automatic import automatic_available
        if not automatic_available():
            with _portfolio_doctor_lock:
                if _builder_form.get(uid) is entry:
                    _builder_form.pop(uid, None)
            safe_send(uid, "NO VERIFIED PORTFOLIO AVAILABLE. Approved research became unavailable.")
            return True
    if beta:
        from msk_ai_builder_telegram import preview_allowed
        if not _builder_allowed(m) or not preview_allowed(uid):
            with _portfolio_doctor_lock:
                if _builder_form.get(uid) is entry:
                    _builder_form.pop(uid, None)
            safe_send(uid, "Private Beta is unavailable.")
            return True
    step = entry["step"]
    if step == "amount":
        amount = text.strip().replace(",", "")
        maximum = 15000000 if (beta or automatic) else 1000000000000
        if not re.fullmatch(r"[0-9]{1,13}", amount) or not 1 <= int(amount) <= maximum:
            safe_send(uid, "Enter a valid rupee amount, like 1000000.")
            return True
        entry.update(amount=amount, step="risk", until=time.monotonic() + 180)
        steps = 3 if (beta or automatic) else 5
        safe_send(uid, f"Step 2/{steps} — Tap your risk preference (not an AI risk assessment):",
                  reply_markup=_builder_options("risk", [
                      ("Low", "low"), ("Mid", "medium"), ("High", "high")]))
        return True
    if step in ("risk", "horizon"):
        safe_send(uid, "Please tap a choice on the previous message.")
        return True
    if step == "stocks":
        from portfolio_doctor_builder_private import parse_builder_request, BuilderError
        rows = [line.strip() for line in text.splitlines() if line.strip()]
        try:
            if not 1 <= len(rows) <= 5:
                raise BuilderError()
            preview = ("/doctor_builder\nCAPITAL={amount} HORIZON={horizon} RISK={risk} "
                       "CASH=0 STOCKCAP=100 SECTORCAP=100\n{rows}").format(
                           amount=entry["amount"], horizon=entry["horizon"], risk=entry["risk"],
                           rows="\n".join(rows))
            parse_builder_request(preview)
        except BuilderError:
            safe_send(uid, "Enter 1–5 lines, e.g. <code>SBIN 25\nBHEL 20</code>, "
                      "with unique symbols and total allocation up to 100%.")
            return True
        entry.update(stocks="\n".join(rows), step="limits", until=time.monotonic() + 180)
        safe_send(uid, "Step 5/5 — Set your limits as three numbers:\n"
                  "Minimum cash (₹), max per stock (%), max per sector (%).\n"
                  "Example: <code>20000 35 45</code>\n"
                  "Type back to edit your stocks or /cancel to stop.")
        return True
    if step == "limits":
        if text.casefold() == "back":
            entry.update(step="stocks", until=time.monotonic() + 180)
            safe_send(uid, "Send your revised stocks and percentages, one per line. Total cannot exceed 100%.")
            return True
        fields = text.split()
        try:
            if len(fields) != 3 or any(not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", v) for v in fields):
                raise ValueError()
            cash, stock, sector = map(float, fields)
            if not 0 <= cash <= int(entry["amount"]) or not 0 < stock <= 100 or not 0 < sector <= 100:
                raise ValueError()
        except ValueError:
            safe_send(uid, "Enter numbers like <code>20000 35 45</code>; cash cannot exceed capital.")
            return True
        payload = ("/doctor_builder\nCAPITAL={amount} HORIZON={horizon} RISK={risk} "
                   "CASH={cash} STOCKCAP={stock} SECTORCAP={sector}\n{stocks}").format(
                       amount=entry["amount"], horizon=entry["horizon"], risk=entry["risk"],
                       cash=fields[0], stock=fields[1], sector=fields[2],
                       stocks=entry["stocks"]).encode("utf-8")
        portfolio_doctor_builder_command(m, payload_override=payload, form_entry=entry)
        return True
    return False


@bot.message_handler(commands=["doctor_builder"])
def portfolio_doctor_builder_command(m, payload_override=None, *, form_entry=None):
    if not _builder_allowed(m):
        safe_send(m.chat.id, "Portfolio Builder works only in a private bot chat.")
        return
    if os.getenv("MSK_PORTFOLIO_DOCTOR_BUILDER_ENABLED", "1") != "1":
        safe_send(m.chat.id, "Portfolio Builder is temporarily disabled. Basic /doctor remains available.")
        return

    uid = m.chat.id
    payload = payload_override if payload_override is not None else (m.text or "").encode("utf-8", "replace")
    from portfolio_doctor_builder_private import BuilderError, parse_builder_request, run_isolated_builder
    try:
        parse_builder_request(payload)
    except BuilderError:
        if form_entry is not None:
            safe_send(uid, "Check your cash and allocation limits. Resend the three limits or type back to edit stocks.")
            return
        safe_send(uid, "Send one private message in this format:\n"
                  "<code>/doctor_builder\n"
                  "CAPITAL=100000 HORIZON=36 RISK=medium CASH=20000 STOCKCAP=35 SECTORCAP=45\n"
                  "INFY 25\nHDFCBANK 30\nTCS 15</code>\n"
                  "The remaining amount stays as cash. Only 1–5 requested symbols; "
                  "no trades or previous holdings are used.")
        return
    with _portfolio_doctor_lock:
        if form_entry is not None and _builder_form.get(uid) is not form_entry:
            return
        if uid in _portfolio_doctor_running:
            safe_send(uid, "Your Portfolio Doctor request is already being processed.")
            return
        if not _portfolio_doctor_slots.acquire(blocking=False):
            safe_send(uid, "Portfolio Builder is busy. " +
                      ("Resend your three limits shortly." if form_entry is not None
                       else "Retry the one-message request."))
            return
        _portfolio_doctor_running.add(uid)

    if form_entry is not None:
        safe_send(uid, "Checking completed closing prices and your allocation limits.")

    def finish_request(delivered=False):
        with _portfolio_doctor_lock:
            if form_entry is not None and _builder_form.get(uid) is form_entry:
                if delivered:
                    _builder_form.pop(uid)
                else:
                    form_entry["until"] = time.monotonic() + 180
            _portfolio_doctor_running.discard(uid)
            _portfolio_doctor_slots.release()

    retry_hint = (" Resend your three limits or type back to edit stocks."
                  if form_entry is not None else " Review and resend your one-message request.")

    def _run():
        import io
        delivered = False
        try:
            pdf_bytes = run_isolated_builder(payload, timeout=120)
            bio = io.BytesIO(pdf_bytes)
            bio.name = "MSK_Optional_Portfolio_Builder.pdf"
            receipt = bot.send_document(
                uid, bio, caption="Hypothetical new-capital allocation only. No investment order.",
                protect_content=True)
            if (not isinstance(getattr(receipt, "message_id", None), int)
                    or not getattr(getattr(receipt, "document", None), "file_id", None)):
                raise BuilderError("WORKER_FAILED")
            delivered = True
            logger.info("MSK_PORTFOLIO_BUILDER_DELIVERY state=SENT receipt_verified=true")
        except BuilderError as exc:
            logger.warning("MSK_PORTFOLIO_BUILDER_HEALTH_ALERT code=%s", exc.code)
            if exc.code in ("NO_PRICES", "STALE_PRICES"):
                safe_send(uid, "A current completed-market close is missing or stale." + retry_hint)
            elif exc.code == "UNKNOWN_SECTOR":
                safe_send(uid, "One stock's sector could not be verified." + retry_hint)
            elif exc.code in ("BAD_INPUT", "CONSTRAINTS"):
                detail = html.escape(exc.detail) if exc.detail else "Inputs or allocation limits conflict."
                safe_send(uid, detail + retry_hint)
            else:
                safe_send(uid, "Portfolio Builder is temporarily unavailable." + retry_hint)
        except Exception as exc:
            logger.error("MSK_PORTFOLIO_BUILDER_HEALTH_ALERT type=%s", type(exc).__name__)
            safe_send(uid, "Unable to generate or deliver a verified Builder PDF." + retry_hint)
        finally:
            finish_request(delivered)

    try:
        executor.submit(_run)
    except Exception:
        finish_request()
        safe_send(uid, "Portfolio Builder could not start." + retry_hint)


# ── Button Handlers ──────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "🔙 Menu")
def back_menu(m):
    state.clear(m.chat.id)
    safe_send(m.chat.id, "📋 Menu", reply_markup=main_keyboard())


@bot.message_handler(func=lambda m: m.text == "💼 Portfolio")
def port_btn(m):
    # A cached Telegram keyboard can still send an old button label.
    # Do not touch broken or unrecovered customer portfolio storage.
    safe_send(m.chat.id, "⚠️ Portfolio is temporarily hidden while recovery "
              "is being verified. No holdings were changed by this request. "
              "Use /start to open the updated menu.")


@bot.message_handler(func=lambda m: m.text in [
    "🏦 Large Cap Top 5", "⚖️ Mid Cap Top 5", "🚀 Small Cap Top 5",
    "🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive",
])
def scan_btn(m):
    # New labels + cached old keyboards use current official NSE index membership.
    if not _allow_heavy(m):
        return
    profiles = {
        "🏦 Large Cap Top 5": "conservative",
        "⚖️ Mid Cap Top 5": "moderate",
        "🚀 Small Cap Top 5": "aggressive",
        "🏦 Conservative": "conservative",
        "⚖️ Moderate": "moderate",
        "🚀 Aggressive": "aggressive",
    }
    profile = profiles[m.text]
    try:
        # The same fresh re-rendering path is used by buttons and typed commands.
        executor.submit(deliver_daily_reports, m.chat.id, (profile,))
    except Exception as exc:
        logger.warning("MSK index Top5 submission unavailable (%s)", type(exc).__name__)
        safe_send(m.chat.id, "⚠️ Index scan unavailable. Please retry later.")




@bot.message_handler(func=lambda m: m.text in ("📊 Breadth", "🩺 Market Health"))
def breadth_btn(m):
    if not _allow_heavy(m):
        return
    safe_send(m.chat.id, "⏳ Preparing market health…")

    def _run(chat_id=m.chat.id):
        try:
            safe_send(chat_id, build_breadth())
        except Exception as e:
            logger.error(f"Breadth err: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")

    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text in ["🎯 Swing (Safe)", "🚀 Swing (Agr)"])
def swing_btn(m):
    if not _allow_heavy(m):
        return
    mode = "conservative" if "Safe" in m.text else "aggressive"
    safe_send(m.chat.id, f"⏳ Swing scanning… (~25s)")

    def _run(chat_id=m.chat.id, md=mode):
        try:
            res = get_swing_trades(mode=md)
            if len(res) <= 3800:
                safe_send(chat_id, res)
            else:
                chunk = ""
                for line in res.split("\n"):
                    if len(chunk) + len(line) + 1 > 3800:
                        safe_send(chat_id, chunk)
                        chunk = ""
                    chunk += line + "\n"
                if chunk.strip():
                    safe_send(chat_id, chunk)
        except Exception as e:
            logger.error(f"Swing err: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")

    executor.submit(_run)


@bot.message_handler(commands=["intraday"])
@bot.message_handler(func=lambda m: m.text == "⚡ Intraday Trade")
def intraday_btn(m):
    """Only an explicit command or button click starts a new intraday scan."""
    if not _allow_heavy(m):
        return
    if getattr(m.chat, "type", "private") != "private":
        safe_send(m.chat.id, "⚠️ Intraday reports are available in private bot chat.")
        return

    safe_send(m.chat.id, "Checking Nifty 200 stocks for intraday setups…")

    def _run(chat_id=m.chat.id):
        try:
            report = build_nifty200_report()
            if not isinstance(report, str) or not report.strip():
                raise ValueError("intraday_report_empty")
            safe_send(chat_id, report)
        except Exception as exc:
            logger.warning("INTRADAY_BUTTON_FAILED kind=%s", type(exc).__name__)
            safe_send(chat_id, "⛔ NO TRADE: Verified intraday data could not "
                      "be processed. No setup issued.")

    # No scheduled job, startup action, timer or unsolicited message.
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "🔍 Analysis")
def analysis_btn(m):
    if getattr(m.chat, "type", None) in ("group", "supergroup"):
        safe_send(m.chat.id, "Open a private chat with MSK Bot to use the Analysis menu.")
        return
    state.set(m.chat.id, "analysis")
    safe_send(m.chat.id, "🔍 Enter an NSE stock or ETF name or ticker, e.g. SBIN or Nifty BeES.")


@bot.message_handler(func=lambda m: m.text == "📄 Report")
def report_btn(m):
    state.set(m.chat.id, "report")
    safe_send(m.chat.id, "📄 Type a stock name or symbol for a full PDF report:")


# ── 🔥 Stocks Buzz — breakout radar over the top-1000 NSE stocks ─────────────
_buzz_lock = threading.Lock()
_buzz_running = False
_buzz_waiters = set()


def _buzz_keyboard(result):
    """One tap per top breakout → opens the Technical / Fundamental / Chart / PDF menu."""
    syms = stocks_buzz.top_symbols(result, 5)
    if not syms:
        return None
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(*[types.InlineKeyboardButton(f"🔎 {s}", callback_data=f"menu|{s}") for s in syms])
    return kb


def _deliver_buzz(chat_id, result):
    msgs = stocks_buzz.format_buzz(result)
    kb = _buzz_keyboard(result)
    for i, m in enumerate(msgs):
        last = i == len(msgs) - 1
        safe_send(chat_id, m, reply_markup=kb if (last and kb) else None)
    if kb:
        safe_send(chat_id, "👆 Tap a stock for Technical / Fundamental / Chart / full PDF report.")


def run_buzz(chat_id, *, force_refresh=False):
    global _buzz_running
    # Normal requests can reuse a verified completed-session snapshot.
    cached = None if force_refresh else stocks_buzz.get_cached()
    if cached:
        _deliver_buzz(chat_id, cached)
        return

    with _buzz_lock:
        _buzz_waiters.add(chat_id)
        if _buzz_running:
            return
        _buzz_running = True

    last_ping = time.monotonic()

    def _progress(done, total):
        nonlocal last_ping
        now = time.monotonic()
        if now - last_ping <= 40:
            return
        last_ping = now
        with _buzz_lock:
            targets = list(_buzz_waiters)
        for cid in targets:
            try:
                safe_send(cid, f"⏳ Stocks Buzz scan: downloaded {done}/{total} stocks…")
            except Exception as exc:
                logger.warning("Buzz progress delivery chat=%s: %s", cid, type(exc).__name__)

    result = None
    try:
        safe_send(chat_id, "🔥 <b>Stocks Buzz</b> — scanning NSE stocks for breakouts…\n"
                           "Verified completed-session results can be reused for later requests.")
        result = stocks_buzz.run_scan(progress_cb=_progress)
    except Exception:
        logger.exception("Stocks Buzz scan failed")

    # Requests can join while Telegram sends the previous batch. Release the
    # scan only when the queue is empty, in the same lock as the empty check.
    while True:
        with _buzz_lock:
            if not _buzz_waiters:
                _buzz_running = False
                return
            targets = list(_buzz_waiters)
            _buzz_waiters.clear()
        for cid in targets:
            try:
                if result is None:
                    safe_send(cid, "❌ Stocks Buzz could not complete the scan. Please retry shortly.")
                else:
                    _deliver_buzz(cid, result)
            except Exception:
                logger.exception("Buzz result delivery failed chat=%s", cid)

@bot.message_handler(commands=["buzz"])
def cmd_buzz(m):
    if not _allow_heavy(m):
        return
    executor.submit(run_buzz, m.chat.id)


@bot.message_handler(func=lambda m: m.text == "🔥 Stocks Buzz")
def buzz_btn(m):
    if not _allow_heavy(m):
        return
    executor.submit(run_buzz, m.chat.id)


# ── 🏭 Index & Sectoral Update report ─────────────────────────────────────────
_sector_lock = threading.Lock()
_sector_running = False
_sector_waiters = set()


def _deliver_sector(chat_id, path, summary):
    from pypdf import PdfReader
    size_bytes = os.path.getsize(path)
    page_count = len(PdfReader(path).pages)
    if size_bytes < 1024 or page_count < 1:
        raise ValueError("Sector PDF empty or unreadable")
    logger.info("MSK_PDF_VALIDATED kind=sector bytes=%d pages=%d",
                size_bytes, page_count)
    with open(path, "rb") as f:
        sent = bot.send_document(
            chat_id, f,
            visible_file_name=f"Index_Sectoral_Update_{datetime.now().strftime('%d%b%Y')}.pdf",
            caption=("🏭 <b>Index &amp; Sectoral Update</b>\n"
                     "Performance · rotation map · breadth · Nifty levels · positioning ideas\n\n"
                     "⚠️ Educational only. Not SEBI-registered advice."),
            parse_mode="HTML",
        )
        _record_telegram_api_ack(chat_id, sent, "document")
    if summary:
        import html as _h
        safe_send(chat_id, "🧭 <b>Key takeaways</b>\n" + "\n".join(f"• {_h.escape(x, quote=False)}" for x in summary[:6]))


def run_sector_report(chat_id):
    global _sector_running
    with _sector_lock:
        _sector_waiters.add(chat_id)
        if _sector_running:
            safe_send(chat_id, "⏳ The sector report is already being built — you'll get it here as soon as it's ready.")
            return
        _sector_running = True

    safe_send(chat_id, "🏭 Building the <b>Index &amp; Sectoral Update</b>… (~1-3 min: 15 indices + ~250 constituents)")
    last_ping = [time.time()]

    def _progress(done, total):
        if time.time() - last_ping[0] > 40:
            last_ping[0] = time.time()
            safe_send(chat_id, f"⏳ Downloaded {done}/{total} series…")

    try:
        ok, result, summary = sector_report.generate_sector_report_pdf(_progress)
        with _sector_lock:
            targets = list(_sector_waiters)
            _sector_waiters.clear()
        for cid in targets:
            try:
                if ok:
                    _deliver_sector(cid, result, summary)
                else:
                    safe_send(cid, f"❌ {result}")
            except Exception as e:
                logger.error(f"Sector deliver {cid}: {e}")
    except Exception as e:
        logger.error(f"Sector report err: {e}", exc_info=True)
        with _sector_lock:
            targets = list(_sector_waiters)
            _sector_waiters.clear()
        for cid in targets:
            safe_send(cid, f"❌ Sector report failed: {e}")
    finally:
        with _sector_lock:
            _sector_running = False


@bot.message_handler(commands=["sectors", "sector"])
def cmd_sectors(m):
    if not _allow_heavy(m):
        return
    executor.submit(run_sector_report, m.chat.id)


@bot.message_handler(func=lambda m: m.text == "🏭 Sector Report")
def sector_btn(m):
    if not _allow_heavy(m):
        return
    executor.submit(run_sector_report, m.chat.id)


@bot.message_handler(func=lambda m: m.text in ("📰 News", "🤖 AI"))
def retired_buttons(m):
    """Old keyboards stay cached in Telegram clients — answer politely and refresh the menu."""
    state.clear(m.chat.id)
    safe_send(m.chat.id,
              "ℹ️ That button has been retired.\n\n🔥 Try <b>Stocks Buzz</b> — it scans the top 1000 NSE "
              "stocks for fresh breakouts.",
              reply_markup=main_keyboard())


# ── Group-only natural-language stock review ─────────────────────────────────
def _group_stock_review(chat_id, message_id, query):
    """Reply to the requesting member; reuse the bot's technicals and chart engine."""
    try:
        ticker, cname = resolve_symbol(query)
        if not ticker:
            safe_send(chat_id, "⚠️ Stock not verified. Please send MSK review NSE:SYMBOL.",
                      reply_to_message_id=message_id)
            return
        sym = ticker.replace(".NS", "").replace(".BO", "")
        technical = build_technical_text(sym, cname)
        safe_send(chat_id, technical, reply_to_message_id=message_id)
        try:
            success, meta, path = get_chart_generator().generate(ticker, cname)
            if success and path:
                with open(path, "rb") as chart:
                    sent = bot.send_photo(chat_id, chart, caption=f"📈 {cname or sym} — Technical chart",
                                   reply_to_message_id=message_id)
                    _record_telegram_api_ack(chat_id, sent, "photo")
            else:
                logger.warning("Group chart unavailable for %s: %s", sym, str(meta)[:120])
        except Exception:
            logger.exception("Group chart generation failed for %s", sym)
    except Exception:
        logger.exception("Group review failed")
        safe_send(chat_id, "⚠️ Analysis temporarily unavailable. Please retry later.",
                  reply_to_message_id=message_id)


@bot.message_handler(
    func=lambda m: is_member_message(m)
    and bool(extract_stock_request(getattr(m, "text", None))),
    content_types=["text"],
)
def group_stock_review(m):
    if not is_member_message(m):
        return
    sender_id = getattr(getattr(m, "from_user", None), "id", None)
    if sender_id is None or not API_RATE_LIMITER.is_allowed(sender_id):
        return
    query = extract_stock_request(m.text)
    if query:
        logger.info("[Discussion] accepted member stock-review request for %s", query)
        try:
            executor.submit(_group_stock_review, m.chat.id, m.message_id, query)
        except RuntimeError:
            logger.exception("Group analysis task rejected by executor")
            safe_send(m.chat.id, "⚠️ Analysis temporarily unavailable. Please retry later.",
                      reply_to_message_id=m.message_id)



# ── Owner-only usage analytics (no quota added) ─────────────────────────────
@bot.message_handler(commands=["myid"])
def cmd_myid(m):
    if m.chat.type != "private":
        return
    safe_send(m.chat.id, f"Your Telegram user ID: <code>{m.from_user.id}</code>")


@bot.message_handler(commands=["ownerclaim"])
def cmd_ownerclaim(m):
    if m.chat.type != "private":
        return
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) != 2 or not usage_analytics.claim_owner(m.from_user.id, parts[1].strip()):
        safe_send(m.chat.id, "Owner setup unavailable or key incorrect. Contact the bot administrator.")
        return
    safe_send(m.chat.id, "✅ Owner dashboard enabled. Use /ownerstats and /ownerusers. "
              "Delete your /ownerclaim message because it contains a setup key.")


@bot.message_handler(commands=["ownerstats", "ownerusers"])
def cmd_owner_stats(m):
    if m.chat.type != "private" or not usage_analytics.is_owner(m.from_user.id):
        if m.chat.type == "private":
            safe_send(m.chat.id, "🔒 Owner access only.")
        return
    try:
        stats = usage_analytics.get_summary()
        command = (m.text or "").split()[0].split("@", 1)[0].lower()
        text = (usage_analytics.users_text(stats) if command == "/ownerusers"
                else usage_analytics.summary_text(stats))
        safe_send(m.chat.id, text)
    except Exception as exc:
        logger.warning("Owner usage report unavailable (%s)", type(exc).__name__)
        safe_send(m.chat.id, "⚠️ Analytics database temporarily unavailable.")


# ── Catch-all Text Handler ───────────────────────────────────────────────────
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(m):
    # Group conversations are not private bot input. Ignore unrelated chatter.
    if getattr(m.chat, 'type', None) in ('group', 'supergroup'):
        return
    uid = m.chat.id
    text = m.text.strip()

    if not API_RATE_LIMITER.is_allowed(uid):
        safe_send(uid, f"⚠️ Rate limited. Wait {RATE_LIMIT_WINDOW}s.")
        return

    # Never route private holdings or repeated inputs to public stock search.
    # An active Doctor session has priority over any stale Builder session.
    with _portfolio_doctor_lock:
        doctor_running = uid in _portfolio_doctor_running
        deadline = _portfolio_doctor_waiting.get(uid, 0)
        doctor_waiting = deadline > time.monotonic()
        if not doctor_waiting:
            _portfolio_doctor_waiting.pop(uid, None)
    if doctor_running:
        safe_send(uid, "A private Portfolio Doctor report is already running.")
        return
    if deadline and not doctor_waiting:
        safe_send(uid, "Portfolio Doctor session expired. Select Portfolio Doctor again.")
        return
    if doctor_waiting:
        if _doctor_allowed(m):
            portfolio_doctor_text(m)
        else:
            with _portfolio_doctor_lock:
                _portfolio_doctor_waiting.pop(uid, None)
            safe_send(uid, "Portfolio Doctor session is unavailable. No holdings were processed.")
        return

    if portfolio_builder_form_text(m, text):
        return

    # Lost in-memory state after a process restart must not strand the
    # explicitly configured PRIVATE owner tester. This does NOT extend
    # customer access, restore expired sessions, or use persistent holdings.
    from portfolio_doctor_live import looks_like_holdings_text
    if looks_like_holdings_text(text):
        owner_id = os.getenv("MSK_PORTFOLIO_DOCTOR_TEST_USER_ID", "").strip()
        owner_recovery = (
            not deadline
            and _doctor_allowed(m)
            and os.getenv("MSK_PORTFOLIO_DOCTOR_TEST_ENABLED", "") == "1"
            and owner_id.isdecimal()
            and str(getattr(m.from_user, "id", None)) == owner_id
            and getattr(m.chat, "type", None) == "private"
            and getattr(m.chat, "id", None) == getattr(m.from_user, "id", None)
        )
        if owner_recovery:
            with _portfolio_doctor_lock:
                _portfolio_doctor_waiting[uid] = time.monotonic() + 180
            logger.info("MSK_DOCTOR_OWNER_SESSION_RECOVERED reason=missing_session")
            portfolio_doctor_text(m)
            return
        safe_send(uid, "Holdings require an enabled private Portfolio Doctor session. "
                  "Select Portfolio Doctor again before sending them.")
        return

    # Builder settings must not be treated as a stock name.
    if text.upper().startswith("CAPITAL="):
        safe_send(uid, "These are Portfolio Builder settings, not an NSE ticker. "
                  "Tap 🏗️ Portfolio Builder for the complete one-message example.")
        return

    if state.get(uid) == "analysis":
        state.clear(uid)
        safe_send(uid, f"🔍 Looking up <b>{html.escape(text)}</b>…")
        executor.submit(send_symbol_menu, uid, text)
        return

    if state.get(uid) == "report":
        state.clear(uid)
        safe_send(uid, f"🔍 Looking up <b>{text}</b>…")
        executor.submit(run_report, uid, text)
        return

    raw_up = text.upper().removeprefix("NSE:").removeprefix("BSE:").removesuffix(".NS").removesuffix(".BO").removesuffix(":BSE")
    looks_ticker = 2 <= len(raw_up) <= 15 and all(c.isalnum() or c in "&-" for c in raw_up)
    looks_name = " " in text or len(raw_up) > 12

    if looks_ticker or looks_name or bees_etf_symbol(text):
        safe_send(uid, f"🔍 Looking up <b>{html.escape(text)}</b>…")
        executor.submit(send_symbol_menu, uid, text)
    else:
        if text.lower().strip("!.?") in {"hi", "hello", "hey", "hlo", "hii", "gm"}:
            safe_send(uid, "👋 Hello! Type a stock name to analyze.", reply_markup=main_keyboard())
        else:
            safe_send(uid, "💡 Type a stock name or use menu.", reply_markup=main_keyboard())


# ── Flask Webhook Routes ─────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    # Render's currently configured healthCheckPath is "/" (not /health/ready).
    # Make that existing path reflect actual intake readiness without making
    # a risky live Render configuration change before customer-data backup.
    _body, ready_status = health_ready()
    return jsonify({
        "status": "ok" if ready_status == 200 else "degraded",
        "name": "MSK Bot", "version": "6.3",
        "scope": "local_webhook_intake_only",
    }), ready_status


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"bot": "running", "ai": "available" if ai_available() else "no keys"})



# The owner dashboard is never publicly accessible and never echoes credentials.
@app.route("/owner/usage", methods=["GET"])
def owner_usage_dashboard():
    secret = os.getenv("MSK_OWNER_DASHBOARD_KEY", "")
    auth = request.authorization
    if (len(secret) < 24 or not auth
            or not hmac.compare_digest(auth.username or "", "owner")
            or not hmac.compare_digest(auth.password or "", secret)):
        resp = Response("Owner authentication required", status=401,
                        headers={"WWW-Authenticate": 'Basic realm="MSK Owner Analytics"'})
    else:
        try:
            summary = usage_analytics.get_summary()
            body = usage_analytics.dashboard_html(summary)
            resp = Response(body, status=200 if summary is not None else 503,
                            mimetype="text/html")
        except Exception as exc:
            logger.warning("Owner dashboard unavailable (%s)", type(exc).__name__)
            resp = Response("Analytics temporarily unavailable", status=503)
    resp.headers["Cache-Control"] = "no-store, private"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = ("default-src 'none'; "
        "style-src 'unsafe-inline'; frame-ancestors 'none'; form-action 'none'")
    return resp


# External uptime monitors should probe these independently of Telegram.
# Liveness verifies HTTP process execution, NOT downstream provider availability.
@app.route("/health/live", methods=["GET"])
def health_live():
    return jsonify({
        "status": "alive",
        "process_uptime_seconds": max(0, int(time.monotonic() - _PROCESS_START_MONOTONIC)),
        "scope": "this_process_only",
    }), 200


@app.route("/health/ready", methods=["GET"])
def health_ready():
    # A dead submission executor cannot accept webhook updates even while
    # the Flask process is responding. Do not claim Telegram delivery is OK.
    can_accept = (not getattr(executor, "_shutdown", True)
                  and not getattr(webhook_executor, "_shutdown", True))
    configured = not WEBHOOK_URL or bool(WEBHOOK_SECRET)
    ledger = globals().get("_durable_update_ledger")
    ledger_status = "not_enabled"
    if ledger is not None:
        try:
            ledger_status = "ready" if ledger.ping() else "unavailable"
        except Exception:
            ledger_status = "unavailable"
    ready = bool(can_accept and configured and ledger_status != "unavailable")
    return jsonify({
        "status": "ready" if ready else "degraded",
        "scope": "local_webhook_intake_only",
        "telegram_delivery": "not_verified",
        "durable_update_ledger": ledger_status,
    }), 200 if ready else 503



def _process_webhook(data_str):
    """Return dispatch success; it does NOT prove downstream Telegram delivery."""
    try:
        update = telebot.types.Update.de_json(data_str)
        if update:
            # Optional usage analytics remains best effort and is NOT a
            # subscriber entitlement or update-deduplication database.
            tracker = globals().get("usage_analytics")
            slots = globals().get("_usage_slots")
            queue = globals().get("usage_executor")
            if tracker is not None and slots is not None and queue is not None:
                if tracker.enabled() and slots.acquire(blocking=False):
                    try:
                        job = queue.submit(tracker.record_event, json.loads(data_str))
                        job.add_done_callback(lambda _: slots.release())
                    except Exception:
                        slots.release()
            bot.process_new_updates([update])
        return True
    except Exception as exc:
        logger.error("Webhook dispatch failed (%s)", type(exc).__name__)
        return False


def _complete_durable_webhook(future, ledger, update_id):
    """Always release capacity, and leave an auditable FAILED/CLAIMED row."""
    try:
        try:
            dispatched = bool(future.result())
        except Exception:
            dispatched = False
        ledger.finish(update_id, dispatched=dispatched)
    except Exception as exc:
        # Never delete a claim that may already have caused a /buy or /sell.
        # A DB outage here needs a manual reconciliation, not replay.
        logger.error("Durable webhook completion unverified (%s)", type(exc).__name__)
    finally:
        _webhook_slots.release()


@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    # Telegram sends this header when webhook is registered with secret_token.
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not WEBHOOK_SECRET or not hmac.compare_digest(supplied, WEBHOOK_SECRET):
        return "unauthorized", 403
    if request.content_length is not None and request.content_length > 256_000:
        return "payload too large", 413
    payload = request.get_json(silent=True)
    if (not isinstance(payload, dict) or
            type(payload.get("update_id")) is not int or
            not 0 <= payload["update_id"] <= 9223372036854775807):
        return "invalid Telegram update", 400
    # Log only message type and visibility; never user text, IDs or secrets.
    msg = payload.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("chat"), dict) and msg["chat"].get("type") in ("group", "supergroup"):
        logger.info("[Discussion webhook] inbound group message (has_text=%s, automatic_forward=%s)",
                    bool(msg.get("text")), bool(msg.get("is_automatic_forward")))
    uid = payload["update_id"]
    if not _webhook_slots.acquire(blocking=False):
        return "server busy", 503  # Telegram retries non-2xx.
    ledger = globals().get("_durable_update_ledger")
    reserved_in_memory = False
    claimed_in_db = False
    submitted = False
    try:
        if ledger is not None:
            # Do not create a subscription from messages without a verified
            # sender. New identities are always inactive / unentitled.
            sender_id = None
            for kind in ("message", "edited_message", "callback_query",
                         "inline_query", "my_chat_member", "chat_member"):
                event = payload.get(kind)
                if not isinstance(event, dict):
                    continue
                sender = event.get("from")
                candidate = sender.get("id") if isinstance(sender, dict) else None
                if type(candidate) is int and 0 < candidate <= 9223372036854775807:
                    sender_id = candidate
                    break
            try:
                claimed_in_db = ledger.claim(uid, user_id=sender_id)
            except Exception as exc:
                # No handlers run when durable storage is unavailable.
                logger.error("Durable webhook claim unavailable (%s)", type(exc).__name__)
                return "durable queue unavailable", 503
            if not claimed_in_db:
                return "ok", 200  # Already claimed, dispatched or failed.
        else:
            # Pre-release/legacy mode: this is NOT restart-safe. Never treat
            # the 1000-entry cache as durable release evidence.
            with _webhook_lock:
                if uid in _processed_updates_set:
                    return "ok", 200
                if len(_processed_updates) == _processed_updates.maxlen:
                    _processed_updates_set.discard(_processed_updates[0])
                _processed_updates.append(uid)
                _processed_updates_set.add(uid)
                reserved_in_memory = True

        try:
            future = webhook_executor.submit(_process_webhook, json.dumps(payload))
        except Exception:
            if ledger is not None and claimed_in_db:
                # submit() raising guarantees no handler was scheduled.
                try:
                    ledger.abandon_unscheduled(uid)
                except Exception as exc:
                    logger.error("Durable unscheduled claim retained (%s)",
                                 type(exc).__name__)
            if reserved_in_memory:
                with _webhook_lock:
                    _processed_updates_set.discard(uid)
                    try:
                        _processed_updates.remove(uid)
                    except ValueError:
                        pass
            logger.exception("Unable to schedule webhook update")
            return "busy", 503
        if ledger is not None:
            future.add_done_callback(
                lambda finished: _complete_durable_webhook(finished, ledger, uid)
            )
        else:
            future.add_done_callback(lambda _: _webhook_slots.release())
        submitted = True
        return "ok", 200
    finally:
        # Accepted submissions release capacity from their done callback.
        # All other exits (duplicate, DB failure, submit failure) release here.
        if not submitted:
            _webhook_slots.release()


# ── Runner ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🚀 Starting MSK Bot v6.3...")
    if WEBHOOK_URL:
        # The webhook destination must never contain the bot credential.
        if TOKEN in WEBHOOK_URL:
            raise RuntimeError("WEBHOOK_URL must not contain TELEGRAM_TOKEN")
        bot.set_webhook(url=f"{WEBHOOK_URL}{WEBHOOK_PATH}", secret_token=WEBHOOK_SECRET)
        if usage_analytics.enabled():
            threading.Thread(target=usage_analytics.daily_digest_loop,
                             args=(bot.send_message, _usage_stop),
                             name="msk-owner-daily", daemon=True).start()
        logger.info("Webhook registered at %s%s", WEBHOOK_URL, WEBHOOK_PATH)
        # A leftover test run ID must NEVER send Swing (or other) Telegram
        # reports by itself on an ordinary production startup/deployment.
        # A separate explicit QA-enable switch is required for one-off tests.
        if (os.getenv("MSK_LIVE_ACCEPTANCE_AUTORUN", "").strip() == "1"
                and os.getenv("MSK_LIVE_ACCEPTANCE_RUN_ID", "").strip()):
            from msk_live_acceptance import run_once as run_owner_acceptance
            threading.Thread(target=run_owner_acceptance, args=(globals(),),
                             name="msk-owner-acceptance", daemon=True).start()
        if os.getenv("MSK_SBIN_PDF_LIVE_RUN_ID"):
            from msk_sbin_pdf_probe import run_once as run_sbin_pdf_acceptance
            threading.Thread(target=run_sbin_pdf_acceptance, args=(globals(),),
                             name="msk-sbin-pdf-release", daemon=True).start()
        from waitress import serve
        logger.info("Production HTTP server listening on port %s", os.getenv("PORT", "8000"))
        serve(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), threads=12, connection_limit=200)
    else:
        logger.info("Running in polling mode...")
        bot.infinity_polling()
