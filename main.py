"""
main.py — AI Stock Advisory Telegram Bot (v6.2)
v6.2: removed News + AI chat buttons · added 🔥 Stocks Buzz (top-1000 breakout radar)
      · institutional-grade research PDF report.
"""

import os
import re
import json
import time
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, date
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import requests
import pandas as pd
import yfinance as yf
from flask import Flask, request, jsonify
import telebot
from telebot import types

# ── Local Module Imports ──────────────────────────────────────────────────────
from data_engine import get_hist, get_info, get_live_price, batch_quotes
from technical_indicators import (
    calc_rsi, calc_ema, calc_macd, calc_atr, calc_asi,
    calc_bollinger, trend_label, swing_signal, rsi_label,
)
from api_utils import API_RATE_LIMITER
from config import RATE_LIMIT_WINDOW, RATE_LIMIT_MAX_CALLS
from market_news import get_stock_news

from ai_engine import (
    ai_insights as engine_ai_insights,
    ai_available,
    test_ai_providers,
)
from swing_trades import get_swing_trades
from chart_integration import get_chart_generator
from report_generator import generate_stock_report_pdf
import stocks_buzz
import sector_report

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
WEBHOOK_PATH = f"/webhook/{TOKEN}"

app = Flask(__name__)
bot = telebot.TeleBot(TOKEN, threaded=False)
executor = ThreadPoolExecutor(max_workers=20)

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
    """Resolves user query to (ticker_with_exchange, company_name)."""
    q = query.upper().strip().replace(" ", "").replace(".NS", "").replace(".BO", "")
    q_raw = query.strip()

    # 1. Exact match
    if q in _SYMBOL_MAP:
        return f"{_SYMBOL_MAP[q]}.NS", _SYMBOL_MAP[q]

    # 2. Partial match
    matches = [s for s in _ALL_NSE_SYMS if s.startswith(q)]
    if len(matches) == 1:
        return f"{matches[0]}.NS", matches[0]
    if len(matches) > 1:
        best = sorted(matches, key=len)[0]
        return f"{best}.NS", best

    # 3. yfinance search (compatible with older yfinance versions)
    try:
        if hasattr(yf, 'Search'):
            results = yf.Search(q_raw, max_results=5).quotes
            for r in results:
                sym_raw = r.get("symbol", "")
                exch = r.get("exchange", "")
                if sym_raw and exch in ("NSI", "BSE"):
                    name = r.get("longname") or r.get("shortname") or sym_raw
                    if sym_raw.endswith(".NS"):
                        return sym_raw, name
                    elif sym_raw.endswith(".BO"):
                        return sym_raw.replace(".BO", ".NS"), name
                    else:
                        return f"{sym_raw}.NS", name
    except Exception:
        pass

    # 4. Direct ticker fallback
    try:
        _t = yf.Ticker(f"{q}.NS")
        _h = _t.history(period="2d", progress=False)
        if _h is not None and not _h.empty:
            _name = (_t.info or {}).get("longName") or q
            return f"{q}.NS", _name
    except Exception:
        pass

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


# ── Thread-Safe Portfolio Manager ────────────────────────────────────────────
class PortfolioManager:
    def __init__(self, file_path="portfolio_data.json"):
        self._data = {}
        self._file = file_path
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        try:
            if os.path.exists(self._file):
                with open(self._file) as f:
                    self._data = {int(k): v for k, v in json.load(f).items()}
        except Exception as e:
            logger.warning(f"Portfolio load error: {e}")

    def _save(self):
        try:
            with self._lock:
                with open(self._file, "w") as f:
                    json.dump(self._data, f, indent=2)
        except Exception as e:
            logger.warning(f"Portfolio save error: {e}")

    def get(self, uid):
        with self._lock:
            return self._data.setdefault(uid, {}).copy()

    def add(self, uid, sym, qty, price):
        with self._lock:
            p = self._data.setdefault(uid, {})
            if sym in p:
                oq, oa = p[sym]["qty"], p[sym]["avg"]
                nq = oq + qty
                p[sym] = {"qty": nq, "avg": round((oq * oa + qty * price) / nq, 2)}
            else:
                p[sym] = {"qty": qty, "avg": round(price, 2)}
        self._save()

    def remove(self, uid, sym):
        with self._lock:
            if uid in self._data and sym in self._data[uid]:
                del self._data[uid][sym]
                self._save()
                return True
        return False


portfolio = PortfolioManager()


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

    close = df["Close"]
    ltp = round(float(close.iloc[-1]), 2)
    prev = float(close.iloc[-2])
    chg = round((ltp - prev) / prev * 100, 2) if prev > 0 else 0.0
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
    de = fund.get("de") or safe_val(info, "debtToEquity")
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
    try:
        ai_text = engine_ai_insights(
            sym, ltp, rsi, macd, trend,
            str(pe if pe is not None else "N/A"),
            str(roe if roe is not None else "N/A")
        ) or ""
    except Exception:
        ai_text = "AI insights unavailable."

    chg_icon = "🟢" if chg >= 0 else "🔴"

    rows = [
        f"🏢 <b>{name}</b>  ({sym})",
        f"{chg_icon} LTP: ₹{ltp:,.2f}  <b>({chg:+.2f}%)</b>",
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

    close = df["Close"]
    ltp = round(float(close.iloc[-1]), 2)
    prev = float(close.iloc[-2])
    chg = round((ltp - prev) / prev * 100, 2) if prev > 0 else 0.0
    rsi = calc_rsi(close)
    macd, _, _ = calc_macd(close)
    ema20 = calc_ema(close, 20)
    ema50 = calc_ema(close, 50)
    atr = calc_atr(df)
    asi = calc_asi(df)
    trend = "BULLISH" if ltp > ema20 > ema50 else "BEARISH" if ltp < ema20 < ema50 else "NEUTRAL"
    t_icon = "🔼" if trend == "BULLISH" else "🔽" if trend == "BEARISH" else "↔️"

    n = min(252, len(close))
    w52h = round(float(close.rolling(n).max().iloc[-1]), 2)
    w52l = round(float(close.rolling(n).min().iloc[-1]), 2)
    dist52 = round((ltp - w52h) / w52h * 100, 1) if w52h else None

    name = cname or sym
    chg_icon = "🟢" if chg >= 0 else "🔴"
    rows = [
        f"📊 <b>{name}</b>  ({sym}) — Technical Analysis",
        f"{chg_icon} LTP: ₹{ltp:,.2f}  <b>({chg:+.2f}%)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📐 EMA20: ₹{ema20:,.2f}  |  EMA50: ₹{ema50:,.2f}",
        f"📏 52W H: ₹{w52h}  |  52W L: ₹{w52l}" + (f"  ({dist52:+.1f}% from peak)" if dist52 is not None else ""),
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔬 Trend: <b>{trend} {t_icon}</b>",
        f"📊 RSI: {rsi}  |  MACD: {'▲' if macd > 0 else '▼'} {macd}  |  ASI: {asi}",
        f"📉 ATR(14): ₹{atr if atr else 'N/A'}",
        "━━━━━━━━━━━━━━━━━━━━",
        _get_tgt_line(trend, ltp, atr),
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
    de = fund.get("de") or safe_val(info, "debtToEquity")
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
        frow("Debt/Equity", de) + (f"  |  Beta: {beta}" if beta else ""),
        frow("Div Yield", div_y, "%"),
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Educational only. Not SEBI-registered advice.</i>",
    ]
    return "\n".join(rows)


# ── Recommended Portfolios (🏦 Conservative / ⚖️ Moderate / 🚀 Aggressive) ──────
# Each button returns exactly 10 validated stocks. Flow (minimum API usage):
#   1 call  : Nifty 50 history      -> market regime (market_data.index_history)
#   1 call  : all pool prices (6mo) -> momentum / volatility / liquidity (market_data.bulk_history)
#   <=~13   : get_info() per stock, in rank order, STOP as soon as 10 pass (cached 60 min)
# Finished text is cached 3 h and shared by all clients -> repeat presses cost 0 calls.
# No LLM, no Finnhub, no new scheduler, no new service.
from html import escape as _esc
from data_engine import cached_get, cached_set

PORTFOLIO_SIZE = 10
PORTFOLIO_CACHE_TTL = 3 * 3600      # seconds, result cache shared by all clients
PORTFOLIO_DEADLINE_SEC = 150        # hard stop for one build -> clear error, never partial

_FIN_SECTORS = {"Banks", "NBFC"}                                   # D/E not meaningful
_DEFENSIVE = {"FMCG", "Pharma", "Power", "IT", "Mining", "Oil & Gas"}

# Curated candidate pools (disjoint, 16 each = 6 spare per profile). Edit freely.
PORTFOLIO_POOLS = {
    "conservative": [
        ("HINDUNILVR", "FMCG"), ("ITC", "FMCG"), ("NESTLEIND", "FMCG"), ("BRITANNIA", "FMCG"),
        ("DABUR", "FMCG"), ("TCS", "IT"), ("INFY", "IT"), ("HCLTECH", "IT"),
        ("HDFCBANK", "Banks"), ("KOTAKBANK", "Banks"), ("POWERGRID", "Power"), ("NTPC", "Power"),
        ("COALINDIA", "Mining"), ("ONGC", "Oil & Gas"), ("SUNPHARMA", "Pharma"), ("ASIANPAINT", "Paints"),
    ],
    "moderate": [
        ("RELIANCE", "Oil & Gas"), ("LT", "Capital Goods"), ("BHARTIARTL", "Telecom"), ("ICICIBANK", "Banks"),
        ("AXISBANK", "Banks"), ("BAJFINANCE", "NBFC"), ("MARUTI", "Auto"), ("M&M", "Auto"),
        ("BAJAJ-AUTO", "Auto"), ("EICHERMOT", "Auto"), ("TITAN", "Consumer"), ("CIPLA", "Pharma"),
        ("DRREDDY", "Pharma"), ("DIVISLAB", "Pharma"), ("ULTRACEMCO", "Cement"), ("PIDILITIND", "Chemicals"),
    ],
    "aggressive": [
        ("BEL", "Defence"), ("HAL", "Defence"), ("TRENT", "Retail"), ("POLYCAB", "Capital Goods"),
        ("ABB", "Capital Goods"), ("CUMMINSIND", "Capital Goods"), ("PERSISTENT", "IT"), ("COFORGE", "IT"),
        ("KPITTECH", "IT"), ("DIXON", "Electronics"), ("CHOLAFIN", "NBFC"), ("MUTHOOTFIN", "NBFC"),
        ("PFC", "Power Finance"), ("MAXHEALTH", "Healthcare"), ("LUPIN", "Pharma"), ("TVSMOTOR", "Auto"),
    ],
}

# Validation rules. mcap in INR, turnover = 20-day avg (price x volume) in INR.
PORTFOLIO_RULES = {
    "conservative": dict(label="🏦 CONSERVATIVE PORTFOLIO", min_mcap=5e11, min_price=100, min_turnover=25e7,
                         max_vol=0.32, max_dd=0.30, max_pe=70, min_roe=12, max_de=1.0, de_required=True,
                         min_growth=-0.15, max_beta=1.2),
    "moderate":     dict(label="⚖️ MODERATE PORTFOLIO", min_mcap=1.5e11, min_price=100, min_turnover=15e7,
                         max_vol=0.42, max_dd=0.35, max_pe=90, min_roe=12, max_de=1.5, de_required=False,
                         min_growth=-0.15, max_beta=1.5),
    "aggressive":   dict(label="🚀 AGGRESSIVE PORTFOLIO", min_mcap=5e10, min_price=50, min_turnover=10e7,
                         max_vol=0.65, max_dd=0.40, max_pe=150, min_roe=10, max_de=2.0, de_required=False,
                         min_growth=0.0, max_beta=None),
}


class PortfolioDataError(Exception):
    """Raised when a complete, validated 10-stock portfolio cannot be produced."""


def _pf_num(v):
    try:
        f = float(v)
        return f if f == f else None          # drops NaN
    except (TypeError, ValueError):
        return None


def _pf_pct(v):
    """Yahoo ratios arrive as decimals (0.18); convert to % (same rule as nifty500_collector)."""
    f = _pf_num(v)
    if f is None:
        return None
    return round(f * 100, 1) if abs(f) <= 1.5 else round(f, 1)


# ── Market regime (1 call, 30-min memo) ──────────────────────────────────────
_PF_REGIME = {"ts": 0.0, "val": None}


def _pf_regime():
    now = time.time()
    if _PF_REGIME["val"] and now - _PF_REGIME["ts"] < 1800:
        return _PF_REGIME["val"]
    from market_data import index_history
    df = index_history("^NSEI", "6mo")
    if df is None or len(df) < 64:
        raise PortfolioDataError("Market data (Nifty 50) is unavailable right now.")
    c = df["Close"].astype(float)
    last = float(c.iloc[-1])
    r1 = last / float(c.iloc[-22]) - 1
    r3 = last / float(c.iloc[-64]) - 1
    above50 = last > float(c.tail(50).mean())
    if above50 and r1 >= 0:
        label = "BULLISH"
    elif (not above50) and r1 < -0.02:
        label = "WEAK"
    else:
        label = "NEUTRAL"
    val = {"label": label, "last": last, "r1": r1, "r3": r3, "above50": above50}
    _PF_REGIME.update(ts=now, val=val)
    return val


# ── Technical / liquidity metrics from the single bulk download (0 extra calls) ─
def _pf_tech(df):
    try:
        if df is None or len(df) < 64:
            return None
        c = df["Close"].astype(float)
        v = df["Volume"].astype(float)
        last = float(c.iloc[-1])
        if last <= 0:
            return None
        prev = float(c.iloc[-2])
        vol = float(c.pct_change().dropna().tail(120).std() * (252 ** 0.5))
        return {
            "price": last,
            "chg": (last / prev - 1) * 100 if prev > 0 else 0.0,
            "r1": last / float(c.iloc[-22]) - 1,
            "r3": last / float(c.iloc[-64]) - 1,
            "vol": vol,
            "above50": last > float(c.tail(50).mean()),
            "dd": last / float(c.max()) - 1,                       # <= 0
            "turnover": float((c * v).tail(20).mean()),
            "vratio": float(v.tail(20).mean() / max(float(v.tail(60).mean()), 1.0)),
        }
    except Exception:
        return None


def _pf_prescore(profile, t, sector, regime, sector_r3):
    r3, r1, vol, dd = t["r3"] * 100, t["r1"] * 100, t["vol"] * 100, abs(t["dd"]) * 100
    if profile == "conservative":      # stability first; defensives get a bonus when market is not bullish
        return -vol + 0.2 * r3 - 0.3 * dd + (4 if regime["label"] != "BULLISH" and sector in _DEFENSIVE else 0)
    if profile == "moderate":          # balanced momentum vs risk
        return 0.4 * r3 + 0.2 * r1 - 0.4 * vol - 0.2 * dd
    sec_edge = (sector_r3.get(sector, regime["r3"]) - regime["r3"]) * 100   # sector strength vs Nifty
    return 0.5 * r3 + 0.3 * r1 + 4 * min(t["vratio"], 2.0) + (5 if t["above50"] else -5) + 0.3 * sec_edge


def _pf_tech_gate(profile, rules, t, regime):
    """Return a reject reason, or None if the stock passes the price/liquidity/risk gate."""
    if t["price"] < rules["min_price"]:
        return "penny/low price"
    if t["turnover"] < rules["min_turnover"]:
        return "illiquid"
    if t["vol"] > rules["max_vol"]:
        return "too volatile"
    if abs(t["dd"]) > rules["max_dd"]:
        return "deep drawdown"
    if profile == "aggressive":
        if t["r3"] <= -0.05:                                          # clearly broken trend
            return "negative momentum"
        if regime["label"] == "WEAK" and not t["above50"] and t["r3"] <= regime["r3"]:
            return "no relative strength in weak market"
    return None


# ── Fundamentals: get_info() (cached 60 min); Screener only if core fields missing ─
def _pf_fundamentals(sym):
    try:
        info = get_info(sym) or {}
    except Exception:
        info = {}
    price = _pf_num(info.get("price"))
    mcap = _pf_num(info.get("market_cap"))
    pe = _pf_num(info.get("pe"))
    roe = _pf_pct(info.get("roe"))
    de_raw = _pf_num(info.get("debtToEquity"))
    de = de_raw / 100.0 if de_raw is not None else None            # Yahoo gives D/E in %
    div = _pf_pct(info.get("dividend_yield"))
    growth = _pf_num(info.get("earningsGrowth"))                    # decimal YoY, optional
    beta = _pf_num(info.get("beta"))
    name = info.get("name")

    if pe is None or roe is None or mcap is None:                    # Yahoo v10 blocked/incomplete
        try:
            from fundamentals import _fetch_screener
            sc = _fetch_screener(sym) or {}
        except Exception:
            sc = {}
        pe = pe if pe is not None else _pf_num(sc.get("pe"))
        roe = roe if roe is not None else _pf_num(sc.get("roe"))     # Screener ROE already %
        mcap = mcap if mcap is not None else _pf_num(sc.get("mcap"))
        de = de if de is not None else _pf_num(sc.get("de"))         # Screener D/E already a ratio
        if div is None:
            div = _pf_num(sc.get("div_y"))
        name = name if name and name != sym else sc.get("name")

    if div is not None and div > 12:
        div = None                                                   # unit sanity guard
    if not price or not mcap or pe is None or pe <= 0 or roe is None:
        return None                                                  # fundamentals unavailable -> skip
    return {"price": price, "mcap": mcap, "pe": pe, "roe": roe, "de": de, "div": div,
            "growth": growth, "beta": beta, "name": name or sym}


def _pf_fund_gate(rules, sector, f):
    if f["mcap"] < rules["min_mcap"]:
        return "market cap too small"
    if f["pe"] > rules["max_pe"]:
        return "PE too high"
    if f["roe"] < rules["min_roe"]:
        return "ROE too low"
    if sector not in _FIN_SECTORS:
        if f["de"] is None and rules["de_required"]:
            return "debt data unavailable"
        if f["de"] is not None and f["de"] > rules["max_de"]:
            return "debt too high"
    if f["growth"] is not None and f["growth"] < rules["min_growth"]:
        return "earnings falling"
    if rules["max_beta"] and f["beta"] is not None and f["beta"] > rules["max_beta"]:
        return "high beta"
    return None


def _pf_reason(profile, sector, f, t, regime):
    cap = "Large-cap" if f["mcap"] >= 1e12 else ("Mid-cap" if f["mcap"] >= 3e11 else "Small/mid-cap")
    parts = [f"{cap} {sector}", f"ROE {f['roe']:.0f}%"]
    if f["de"] is not None and sector not in _FIN_SECTORS:
        parts.append(f"D/E {f['de']:.1f}")
    if profile == "conservative":
        if f["div"]:
            parts.append(f"div {f['div']:.1f}%")
        parts.append(f"low volatility {t['vol'] * 100:.0f}%")
    elif profile == "moderate":
        parts.append(f"3M {t['r3'] * 100:+.0f}%")
        parts.append(f"volatility {t['vol'] * 100:.0f}%")
    else:
        parts.append(f"3M {t['r3'] * 100:+.0f}% vs Nifty {regime['r3'] * 100:+.0f}%")
        parts.append(f"volume {t['vratio']:.1f}x avg")
    if f["growth"] is not None:
        parts.append(f"earnings {f['growth'] * 100:+.0f}% YoY")
    return " · ".join(parts)


def _build_portfolio(profile):
    from market_data import bulk_history
    t0 = time.time()
    rules, pool = PORTFOLIO_RULES[profile], PORTFOLIO_POOLS[profile]
    sectors = dict(pool)
    regime = _pf_regime()

    hist = bulk_history([s for s, _ in pool], period="6mo", cache_ttl=1800)    # ONE download
    tech = {s: m for s, m in ((s, _pf_tech(hist.get(s))) for s, _ in pool) if m}
    if len(tech) < PORTFOLIO_SIZE:
        raise PortfolioDataError("Price data is unavailable for too many stocks right now.")

    by_sec = {}
    for s, m in tech.items():
        by_sec.setdefault(sectors[s], []).append(m["r3"])
    sector_r3 = {k: sum(v) / len(v) for k, v in by_sec.items()}
    score = {s: _pf_prescore(profile, m, sectors[s], regime, sector_r3) for s, m in tech.items()}

    picked = []
    for s in sorted(tech, key=score.get, reverse=True):
        if len(picked) >= PORTFOLIO_SIZE or time.time() - t0 > PORTFOLIO_DEADLINE_SEC:
            break
        why = _pf_tech_gate(profile, rules, tech[s], regime)
        f = None
        if not why:
            f = _pf_fundamentals(s)
            why = "fundamentals unavailable" if f is None else _pf_fund_gate(rules, sectors[s], f)
        if why:
            logger.info(f"[Portfolio:{profile}] skip {s}: {why}")
            continue
        picked.append((s, f, tech[s]))

    if len(picked) < PORTFOLIO_SIZE:
        raise PortfolioDataError(
            f"Only {len(picked)} of {PORTFOLIO_SIZE} stocks passed data validation "
            f"(price/fundamentals missing or failed quality rules).")

    mood = {"BULLISH": "🟢 Bullish", "NEUTRAL": "🟡 Neutral", "WEAK": "🔴 Weak"}[regime["label"]]
    lines = [
        f"📊 <b>{rules['label']}</b>",
        f"📅 {date.today().strftime('%d-%b-%Y')}",
        f"🌐 Market: {mood} — Nifty {regime['last']:,.0f} ({regime['r1'] * 100:+.1f}% 1M, {regime['r3'] * 100:+.1f}% 3M)",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for i, (s, f, t) in enumerate(picked, 1):
        name = _esc(re.sub(r"\s+(Limited|Ltd\.?)$", "", str(f["name"]), flags=re.I))
        lines += [
            f"<b>{i}. {name}</b> ({_esc(s)}) · {_esc(sectors[s])}",
            f"   ₹{f['price']:,.2f} ({t['chg']:+.2f}%)  |  MCap {fmt_mcap(f['mcap'])}  |  PE {f['pe']:.1f}",
            f"   💡 {_esc(_pf_reason(profile, sectors[s], f, t, regime))}",
        ]
    lines += ["━━━━━━━━━━━━━━━━━━━━", "⚠️ <i>Educational only. Not SEBI-registered advice.</i>"]
    return "\n".join(lines)


_PF_LOCKS = {p: threading.Lock() for p in PORTFOLIO_POOLS}


def _pf_key(profile):
    return f"portfolio_{profile}_{date.today().isoformat()}"


def get_portfolio(profile):
    """Cached text or a fresh build. One build per profile at a time. Raises PortfolioDataError."""
    hit = cached_get(_pf_key(profile), PORTFOLIO_CACHE_TTL)
    if hit:
        return hit
    with _PF_LOCKS[profile]:
        hit = cached_get(_pf_key(profile), PORTFOLIO_CACHE_TTL)      # built while we waited
        if hit:
            return hit
        text = _build_portfolio(profile)
        cached_set(_pf_key(profile), text, PORTFOLIO_CACHE_TTL)      # errors are never cached
        return text


def _pf_profile_from_text(text):
    t = (text or "").lower()
    if "swing" in t or len(t) > 40:                    # buttons only, not free-text sentences
        return None
    for key in ("conservative", "moderate", "aggressive"):
        if key in t:
            return key
    return None


# ── Build Market Breadth ─────────────────────────────────────────────────────
def build_breadth():
    lines = ["📊 <b>MARKET BREADTH</b>", "━━━━━━━━━━━━━━━━━━━━"]
    indices = {"NIFTY 50": "^NSEI", "BANK NIFTY": "^NSEBANK", "NIFTY IT": "^CNXIT", "NIFTY MIDCAP": "^NSEMDCP50"}
    for name, tick in indices.items():
        try:
            d = yf.Ticker(tick).history(period="1mo")
            if d is None or len(d) < 5:
                continue
            l = round(float(d["Close"].iloc[-1]), 2)
            p = round(float(d["Close"].iloc[-2]), 2)
            c = round((l - p) / p * 100, 2) if p > 0 else 0.0
            icon = "🟢" if c >= 0 else "🔴"
            lines.append(f"{icon} <b>{name}</b>: {l:,.2f} ({c:+.2f}%)")
        except Exception:
            pass
    return "\n".join(lines) if len(lines) > 2 else "❌ Index data unavailable."


# ── Build Portfolio Card ─────────────────────────────────────────────────────
def build_portfolio_card(uid):
    p = portfolio.get(uid)
    if not p:
        return "📂 <b>Portfolio Empty</b>\n\nAdd: <code>/buy RELIANCE 10 2500</code>"

    t_inv = 0.0
    t_cur = 0.0
    rows = []
    winners = []
    losers = []

    for sym, pos in p.items():
        qty, avg = pos["qty"], pos["avg"]
        try:
            ltp_raw = get_live_price(sym)
            ltp = round(float(ltp_raw), 2) if ltp_raw is not None else avg
        except Exception:
            ltp = avg

        inv = qty * avg
        cur = qty * ltp
        pnl = round(cur - inv, 2)
        pct = round((ltp - avg) / avg * 100, 2) if avg > 0 else 0.0
        rows.append({"sym": sym, "qty": qty, "avg": avg, "ltp": ltp, "inv": inv, "cur": cur, "pnl": pnl, "pct": pct})
        t_inv += inv
        t_cur += cur
        (winners if pnl >= 0 else losers).append((sym, pnl, pct))

    t_pnl = round(t_cur - t_inv, 2)
    t_pct = round((t_cur - t_inv) / t_inv * 100, 2) if t_inv > 0 else 0.0
    icon = "🟢" if t_pnl >= 0 else "🔴"

    lines = [f"<b>━━━ 💼 PORTFOLIO ━━━</b>", f"📅 {date.today().strftime('%d-%b-%Y')}  |  {len(rows)} holdings", "", "<b>── HOLDINGS ──</b>"]

    for r in sorted(rows, key=lambda x: x["pnl"], reverse=True):
        wt = round(r["inv"] / t_inv * 100, 1) if t_inv > 0 else 0
        lines += [
            f"{'🟢' if r['pnl'] >= 0 else '🔴'} <b>{r['sym']}</b>",
            f"   Qty:{r['qty']} ({wt}%)  Avg:₹{r['avg']:,.2f} → LTP:₹{r['ltp']:,.2f}",
            f"   P&L: ₹{r['pnl']:+,.2f} ({r['pct']:+.2f}%)",
            "   ···"
        ]

    lines += ["", f"{icon} <b>Total P&L: ₹{t_pnl:+,.2f} ({t_pct:+.2f}%)</b>", "─" * 32, "➕ /buy SYM QTY PRICE  ➖ /sell SYM", "⚠️ <i>Educational only.</i>"]
    return "\n".join(lines)


# ── Keyboards ────────────────────────────────────────────────────────────────
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add("🔍 Analysis", "📊 Breadth", "🔥 Stocks Buzz")
    kb.add("🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive")
    kb.add("🎯 Swing (Safe)", "🚀 Swing (Agr)", "💼 Portfolio")
    kb.add("📈 Chart", "📄 Report", "📋 Status")
    kb.add("🏭 Sector Report")
    return kb


def analysis_choice_keyboard(sym):
    """Inline menu shown after a stock is found — lets the user pick exactly
    what they want instead of receiving one long combined message."""
    kb = types.InlineKeyboardMarkup(row_width=2)
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
def safe_send(chat_id, text, parse_mode="HTML", **kwargs):
    if text is None:
        return
    try:
        bot.send_message(chat_id, text, parse_mode=parse_mode, **kwargs)
    except Exception as e:
        err_str = str(e).lower()
        if "can't parse" in err_str or "bad request" in err_str:
            try:
                plain = re.sub(r"<[^>]+>", "", str(text))
                bot.send_message(chat_id, plain, **kwargs)
            except Exception:
                pass


# ── Command Handlers ─────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(m):
    state.clear(m.chat.id)
    safe_send(m.chat.id,
              "👋 <b>AutoAI Advisory Bot v6.2</b>\n\nType any stock name or symbol for analysis.\n"
              "🔥 <b>Stocks Buzz</b> scans the top 1000 NSE stocks for fresh breakouts.\n"
              "📄 <code>/report SYMBOL</code> builds an institutional-style research PDF.\n"
              "🏭 <b>Sector Report</b> = Index &amp; sectoral update PDF (rotation, breadth, Nifty levels).\n"
              "Use menu buttons below.",
              reply_markup=main_keyboard())


@bot.message_handler(commands=["help"])
def cmd_help(m):
    safe_send(m.chat.id,
              "📖 <b>Help</b>\n\nType symbol: <code>RELIANCE</code>\nChart: <code>/chart INFY 3mo</code>\nResearch PDF: <code>/report DLF</code>\nBreakouts: tap 🔥 Stocks Buzz or <code>/buzz</code>\nIndex &amp; sector PDF: tap 🏭 Sector Report or <code>/sectors</code>\nBuy: <code>/buy RELIANCE 10 2500</code>\nSell: <code>/sell RELIANCE</code>\nStatus: <code>/status</code>")


def run_report(chat_id, query):
    """Shared worker for /report SYMBOL and the 📄 Report button flow."""
    try:
        ticker, cname = resolve_symbol(query)
        sym = ticker.replace(".NS", "").replace(".BO", "") if ticker else query.upper().replace(" ", "")

        safe_send(chat_id, f"📄 Building institutional research report for <b>{cname or sym}</b>… (~60-90s: price action, financials, ownership, peers)")

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
                safe_send(chat_id, "⏳ Almost there — rendering charts and PDF…")
            except Exception:
                pass

        threading.Thread(target=_ping, daemon=True).start()

        try:
            ok, result, name = generate_stock_report_pdf(sym)
        finally:
            done_event.set()
        if ok:
            try:
                with open(result, "rb") as f:
                    bot.send_document(
                        chat_id, f,
                        visible_file_name=f"{sym}_Research_Report.pdf",
                        caption=(f"📄 <b>{name or sym} — Equity Research Report</b>\n"
                                 f"Stance · Technicals · Fundamentals · Ownership · Peers · Trade plan · Risks\n"
                                 f"Generated {datetime.now().strftime('%d-%b-%Y %H:%M')}\n\n"
                                 f"⚠️ Educational only. Not SEBI-registered advice."),
                        parse_mode="HTML",
                    )
            finally:
                try:
                    os.remove(result)
                except Exception:
                    pass
        else:
            safe_send(chat_id, f"❌ Could not generate report for <b>{query}</b>: {result}")
    except Exception as e:
        logger.error(f"Report err: {e}", exc_info=True)
        safe_send(chat_id, f"❌ Error generating report: {e}")


@bot.message_handler(commands=["report"])
def cmd_report(m):
    parts = m.text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        safe_send(m.chat.id,
                   "📄 Usage: <code>/report SYMBOL</code>\ne.g. <code>/report DLF</code>")
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
    try:
        ticker, cname = resolve_symbol(query)
        if ticker:
            sym = ticker.replace(".NS", "").replace(".BO", "")
        elif 2 <= len(query.upper().replace(".NS", "").replace(".BO", "")) <= 15:
            sym = query.upper().replace(".NS", "").replace(".BO", "")
        else:
            safe_send(chat_id, f"❌ Not found: <b>{query}</b>", reply_markup=main_keyboard())
            return

        ltp_line = ""
        try:
            p = get_live_price(sym)
            if p:
                ltp_line = f"\n💰 LTP: ₹{p:,.2f}"
        except Exception:
            pass

        safe_send(
            chat_id,
            f"✅ Found <b>{cname or sym}</b> ({sym}){ltp_line}\n\nWhat would you like to see?",
            reply_markup=analysis_choice_keyboard(sym),
        )
    except Exception as e:
        logger.error(f"Symbol menu err: {e}", exc_info=True)
        safe_send(chat_id, f"❌ Error: {e}")


@bot.callback_query_handler(func=lambda call: True)
def handle_analysis_callback(call):
    try:
        action, _, sym = (call.data or "").partition("|")
    except Exception:
        action, sym = "", ""
    chat_id = call.message.chat.id

    if not action or not sym:
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return

    try:
        bot.answer_callback_query(call.id, text="Working on it…")
    except Exception:
        pass

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
                        bot.send_photo(cid, f, caption=f"<b>📈 {cname or s}</b>\n\n{meta}", parse_mode="HTML")
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

    elif action == "menu":
        safe_send(chat_id, f"What would you like to see for <b>{sym}</b>?",
                  reply_markup=analysis_choice_keyboard(sym))


@bot.message_handler(commands=["status"])
def cmd_status(m):
    cid = m.chat.id
    safe_send(cid, "⏳ Checking status…")

    def _run(chat_id=cid):
        try:
            res = test_ai_providers()
            lines = [
                f"🤖 <b>STATUS v6.2</b>",
                "─── ── ── ── ── ──",
                f"Bot : ✅ Running",
                f"AI  : {res.get('_status', '?')}",
                f"Time: {datetime.now().strftime('%d-%b-%Y %H:%M')}"
            ]
            for p_name in ["GROQ", "Gemini", "OpenAI", "AskFuzz"]:
                v = res.get(p_name, "SKIP")
                ic = "✅" if str(v).startswith("OK") else ("⚪" if str(v) == "SKIP" else "❌")
                lines.append(f"  {ic} {p_name}: {str(v)[:35]}")
            safe_send(chat_id, "\n".join(lines), reply_markup=main_keyboard())
        except Exception as e:
            logger.error(f"Status err: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Status check failed: {e}")

    executor.submit(_run)


@bot.message_handler(commands=["chart"])
def cmd_chart(m):
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
                    bot.send_photo(chat_id, f, caption=f"<b>📈 {cname}</b>\n\n{meta}", parse_mode="HTML")
            else:
                safe_send(chat_id, "⚠️ Chart failed, sending text:")
                safe_send(chat_id, build_adv(sym))
        except Exception as e:
            logger.error(f"Chart err: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")

    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "📈 Chart")
def chart_button(m):
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


@bot.message_handler(commands=["buy"])
def cmd_buy(m):
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
    if qty <= 0 or price <= 0:
        safe_send(m.chat.id, "❌ Must be positive.")
        return
    ticker, _ = resolve_symbol(parts[1])
    sym = ticker.replace(".NS", "").replace(".BO", "") if ticker else parts[1].upper().replace(".NS", "")
    portfolio.add(m.chat.id, sym, qty, price)
    safe_send(m.chat.id, f"✅ Added <b>{qty}×{sym}</b> @ ₹{price:.2f}")


@bot.message_handler(commands=["sell"])
def cmd_sell(m):
    parts = m.text.strip().split()
    if len(parts) < 2:
        safe_send(m.chat.id, "Usage: <code>/sell SYM</code>")
        return
    sym = " ".join(parts[1:]).upper().replace(".NS", "").replace(".BO", "")
    if portfolio.remove(m.chat.id, sym):
        safe_send(m.chat.id, f"✅ Removed <b>{sym}</b>.")
    else:
        safe_send(m.chat.id, f"❌ <b>{sym}</b> not found.")


@bot.message_handler(commands=["portfolio"])
def cmd_portfolio(m):
    safe_send(m.chat.id, "⏳ Loading…")

    def _run(chat_id=m.chat.id):
        try:
            safe_send(chat_id, build_portfolio_card(chat_id))
        except Exception as e:
            logger.error(f"Portfolio err: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")

    executor.submit(_run)


# ── Button Handlers ──────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "🔙 Menu")
def back_menu(m):
    state.clear(m.chat.id)
    safe_send(m.chat.id, "📋 Menu", reply_markup=main_keyboard())


@bot.message_handler(func=lambda m: m.text == "💼 Portfolio")
def port_btn(m):
    cmd_portfolio(m)


@bot.message_handler(func=lambda m: m.text == "📋 Status")
def stat_btn(m):
    cmd_status(m)


def _pf_send(chat_id, text):
    if len(text) <= 3800:
        safe_send(chat_id, text)
        return
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > 3800:
            safe_send(chat_id, chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        safe_send(chat_id, chunk)


@bot.message_handler(func=lambda m: _pf_profile_from_text(m.text) is not None)
def scan_btn(m):
    prof = _pf_profile_from_text(m.text)
    chat_id = m.chat.id
    if cached_get(_pf_key(prof), PORTFOLIO_CACHE_TTL):          # instant, 0 API calls
        try:
            _pf_send(chat_id, get_portfolio(prof))
            return
        except Exception as e:
            logger.error(f"Portfolio cache err: {e}", exc_info=True)
    safe_send(chat_id, f"⏳ Building {PORTFOLIO_RULES[prof]['label'].title()}… "
                       f"first run takes 1–2 min, then it is instant for 3 hours.")

    def _run(cid=chat_id, p=prof):
        try:
            _pf_send(cid, get_portfolio(p))
        except PortfolioDataError as e:
            safe_send(cid, f"❌ <b>Portfolio unavailable</b>\n{_esc(str(e))}\n"
                           f"No partial list was sent. Please try again in a few minutes.")
        except Exception as e:
            logger.error(f"Portfolio err: {e}", exc_info=True)
            safe_send(cid, "❌ Portfolio could not be generated right now. Please try again in a few minutes.")

    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "📊 Breadth")
def breadth_btn(m):
    safe_send(m.chat.id, "⏳ Fetching…")

    def _run(chat_id=m.chat.id):
        try:
            safe_send(chat_id, build_breadth())
        except Exception as e:
            logger.error(f"Breadth err: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")

    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text in ["🎯 Swing (Safe)", "🚀 Swing (Agr)"])
def swing_btn(m):
    mode = "conservative" if "Safe" in m.text else "aggressive"
    safe_send(m.chat.id, f"⏳ Swing scanning… (~25s)")

    def _ping(chat_id=m.chat.id):
        time.sleep(15)
        try:
            safe_send(chat_id, "⏳ Still scanning…")
        except Exception:
            pass

    threading.Thread(target=_ping, daemon=True).start()

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


@bot.message_handler(func=lambda m: m.text == "🔍 Analysis")
def analysis_btn(m):
    state.set(m.chat.id, "analysis")
    safe_send(m.chat.id, "🔍 Type any stock name or symbol:")


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


def run_buzz(chat_id):
    global _buzz_running
    cached = stocks_buzz.get_cached()
    if cached:
        _deliver_buzz(chat_id, cached)
        return

    with _buzz_lock:
        _buzz_waiters.add(chat_id)
        if _buzz_running:
            safe_send(chat_id, "⏳ A scan is already running — you'll get the results here as soon as it finishes.")
            return
        _buzz_running = True

    safe_send(chat_id, "🔥 <b>Stocks Buzz</b> — scanning the top 1000 NSE stocks for breakouts…\n"
                       "⏱ First scan takes ~2-4 minutes; the next ones are instant (30-min cache).")
    last_ping = [time.time()]

    def _progress(done, total):
        if time.time() - last_ping[0] > 40:
            last_ping[0] = time.time()
            safe_send(chat_id, f"⏳ Downloaded {done}/{total} stocks…")

    try:
        result = stocks_buzz.run_scan(progress_cb=_progress)
        with _buzz_lock:
            targets = list(_buzz_waiters)
            _buzz_waiters.clear()
        for cid in targets:
            try:
                _deliver_buzz(cid, result)
            except Exception as e:
                logger.error(f"Buzz deliver {cid}: {e}")
    except Exception as e:
        logger.error(f"Buzz err: {e}", exc_info=True)
        with _buzz_lock:
            targets = list(_buzz_waiters)
            _buzz_waiters.clear()
        for cid in targets:
            safe_send(cid, f"❌ Stocks Buzz failed: {e}")
    finally:
        with _buzz_lock:
            _buzz_running = False


@bot.message_handler(commands=["buzz"])
def cmd_buzz(m):
    executor.submit(run_buzz, m.chat.id)


@bot.message_handler(func=lambda m: m.text == "🔥 Stocks Buzz")
def buzz_btn(m):
    executor.submit(run_buzz, m.chat.id)


# ── 🏭 Index & Sectoral Update report ─────────────────────────────────────────
_sector_lock = threading.Lock()
_sector_running = False
_sector_waiters = set()


def _deliver_sector(chat_id, path, summary):
    with open(path, "rb") as f:
        bot.send_document(
            chat_id, f,
            visible_file_name=f"Index_Sectoral_Update_{datetime.now().strftime('%d%b%Y')}.pdf",
            caption=("🏭 <b>Index &amp; Sectoral Update</b>\n"
                     "Performance · rotation map · breadth · Nifty levels · positioning ideas\n\n"
                     "⚠️ Educational only. Not SEBI-registered advice."),
            parse_mode="HTML",
        )
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
    executor.submit(run_sector_report, m.chat.id)


@bot.message_handler(func=lambda m: m.text == "🏭 Sector Report")
def sector_btn(m):
    executor.submit(run_sector_report, m.chat.id)


@bot.message_handler(func=lambda m: m.text in ("📰 News", "🤖 AI"))
def retired_buttons(m):
    """Old keyboards stay cached in Telegram clients — answer politely and refresh the menu."""
    state.clear(m.chat.id)
    safe_send(m.chat.id,
              "ℹ️ That button has been retired.\n\n🔥 Try <b>Stocks Buzz</b> — it scans the top 1000 NSE "
              "stocks for fresh breakouts.",
              reply_markup=main_keyboard())


# ── Catch-all Text Handler ───────────────────────────────────────────────────
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(m):
    uid = m.chat.id
    text = m.text.strip()

    if not API_RATE_LIMITER.is_allowed(uid):
        safe_send(uid, f"⚠️ Rate limited. Wait {RATE_LIMIT_WINDOW}s.")
        return

    if state.get(uid) == "analysis":
        safe_send(uid, f"🔍 Looking up <b>{text}</b>…")

        def _arun(chat_id=uid, q=text):
            try:
                send_symbol_menu(chat_id, q)
            finally:
                state.clear(chat_id)

        executor.submit(_arun)
        return

    if state.get(uid) == "report":
        state.clear(uid)
        safe_send(uid, f"🔍 Looking up <b>{text}</b>…")
        executor.submit(run_report, uid, text)
        return

    raw_up = text.upper().replace(".NS", "").replace(".BO", "")
    looks_ticker = 2 <= len(raw_up) <= 15 and all(c.isalnum() or c in "&-" for c in raw_up)
    looks_name = " " in text or len(raw_up) > 12

    if looks_ticker or looks_name:
        safe_send(uid, f"🔍 Looking up <b>{text}</b>…")
        executor.submit(send_symbol_menu, uid, text)
    else:
        if text.lower().strip("!.?") in {"hi", "hello", "hey", "hlo", "hii", "gm"}:
            safe_send(uid, "👋 Hello! Type a stock name to analyze.", reply_markup=main_keyboard())
        else:
            safe_send(uid, "💡 Type a stock name or use menu.", reply_markup=main_keyboard())


# ── Flask Webhook Routes ─────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok", "version": "6.2"})


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"bot": "running", "ai": "available" if ai_available() else "no keys"})


def _process_webhook(data_str):
    try:
        update = telebot.types.Update.de_json(data_str)
        if update:
            bot.process_new_updates([update])
    except Exception as e:
        logger.error(f"Webhook process err: {e}")


@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    data = request.get_data(as_text=True)
    try:
        payload = json.loads(data)
        uid = payload.get("update_id")
        if uid is not None:
            if uid in _processed_updates:
                return "ok", 200
            _processed_updates.append(uid)
    except (json.JSONDecodeError, TypeError):
        pass
    executor.submit(_process_webhook, data)
    return "ok", 200


# ── Runner ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🚀 Starting AutoAI Bot v6.2...")
    if WEBHOOK_URL:
        bot.set_webhook(url=f"{WEBHOOK_URL}{WEBHOOK_PATH}")
        logger.info(f"Webhook active: {WEBHOOK_URL}{WEBHOOK_PATH}")
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
    else:
        logger.info("Running in polling mode...")
        bot.infinity_polling()
