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
from datetime import datetime, date
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import hmac

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
from group_stock_requests import extract_stock_request
from group_member_filter import is_member_message

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
_processed_updates_set = set()
_webhook_lock = threading.Lock()
_webhook_slots = threading.BoundedSemaphore(value=40)


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
            if uid not in self._data or sym not in self._data[uid]:
                return False
            del self._data[uid][sym]
        # _save acquires the same lock; never call it while holding the lock.
        self._save()
        return True


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


# ── Screener Card ─────────────────────────────────────────────────────────
SCREENER_STOCKS = {
    "conservative": ["HDFCBANK", "TCS", "INFY", "ITC", "ONGC", "SBIN", "WIPRO", "NTPC", "POWERGRID", "COALINDIA"],
    "moderate": ["RELIANCE", "BHARTIARTL", "AXISBANK", "MARUTI", "LT", "KOTAKBANK", "BAJFINANCE", "SUNPHARMA", "TITAN", "M&M"],
    "aggressive": ["TATAMOTORS", "ADANIENT", "JSWSTEEL", "TATAPOWER", "ZOMATO", "IRFC", "HAL", "BEL", "PFC", "ADANIPORTS"],
}


def build_scan(profile):
    syms = SCREENER_STOCKS.get(profile, [])
    if not syms:
        return "❌ Unknown profile."
    labels = {"conservative": "🏦 CONSERVATIVE", "moderate": "⚖️ MODERATE", "aggressive": "🚀 AGGRESSIVE"}
    lines = [f"📊 <b>{labels.get(profile, 'SCREENER')}</b>", f"📅 {date.today().strftime('%d-%b-%Y')}", "━━━━━━━━━━━━━━━━━━━━"]

    def _fetch(sym):
        try:
            df = get_hist(sym, "6mo")
            if df is None or df.empty or len(df) < 28:
                return None
            c = df["Close"]
            ltp = round(float(c.iloc[-1]), 2)
            prev = float(c.iloc[-2])
            chg = round((ltp - prev) / prev * 100, 2) if prev > 0 else 0.0
            rsi_val = calc_rsi(c)
            trend_val = trend_label(c)
            signal_val = swing_signal(rsi_val, trend_val, chg)
            return {"sym": sym, "ltp": ltp, "chg": chg, "rsi": rsi_val, "trend": trend_val, "signal": signal_val}
        except Exception:
            return None

    results = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(_fetch, s): s for s in syms}
        for f in as_completed(futs, timeout=15):
            sym = futs[f]
            try:
                r = f.result()
                if r:
                    results[sym] = r
            except Exception:
                pass

    for s in syms:
        r = results.get(s)
        if not r:
            continue
        icon = "🟢" if r["chg"] >= 0 else "🔴"
        rsi_b = "🔴OB" if r["rsi"] > 70 else ("🟢OS" if r["rsi"] < 30 else "🟡")
        lines.append(f"{icon} <b>{s}</b>  ₹{r['ltp']:,.2f} ({r['chg']:+.2f}%)\n   RSI:{r['rsi']} {rsi_b}  |  {r['trend']}  |  <b>{r['signal']}</b>")

    if not results:
        lines.append("❌ Data unavailable.")
    lines.append("\n⚠️ Educational only.")
    return "\n".join(lines)


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
        # Never log the bot token or message content. This used to fail silently,
        # making missing discussion replies impossible to diagnose on Render.
        logger.warning("[Telegram delivery] send failed: HTTP %s (%s)",
                       getattr(e, "error_code", "unknown"), type(e).__name__)
        err_str = str(e).lower()
        if "can't parse" in err_str or "bad request" in err_str:
            try:
                plain = re.sub(r"<[^>]+>", "", str(text))
                bot.send_message(chat_id, plain, **kwargs)
            except Exception as fallback_error:
                logger.warning("[Telegram delivery] plain-text fallback failed: HTTP %s (%s)",
                               getattr(fallback_error, "error_code", "unknown"),
                               type(fallback_error).__name__)


# ── Command Handlers ─────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(m):
    state.clear(m.chat.id)
    safe_send(m.chat.id,
              "👋 <b>MSK Bot</b>\n\nType any stock name or symbol for analysis.\n"
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
    else:
        safe_send(chat_id, "⚠️ This button is no longer supported. Use /start to refresh the MSK Bot menu.")


@bot.message_handler(commands=["status"])
def cmd_status(m):
    cid = m.chat.id
    safe_send(cid, "⏳ Checking status…")

    def _run(chat_id=cid):
        try:
            res = test_ai_providers()
            lines = [
                f"🤖 <b>MSK Bot — STATUS</b>",
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


@bot.message_handler(func=lambda m: m.text in ["🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive"])
def scan_btn(m):
    p = {"🏦 Conservative": "conservative", "⚖️ Moderate": "moderate", "🚀 Aggressive": "aggressive"}[m.text]
    safe_send(m.chat.id, f"⏳ Scanning {m.text}…")

    def _run(chat_id=m.chat.id, prof=p):
        try:
            safe_send(chat_id, build_scan(prof))
        except Exception as e:
            logger.error(f"Screener err: {e}", exc_info=True)
            safe_send(chat_id, f"❌ Error: {e}")

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
                    bot.send_photo(chat_id, chart, caption=f"📈 {cname or sym} — Technical chart",
                                   reply_to_message_id=message_id)
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
    return jsonify({"status": "ok", "name": "MSK Bot", "version": "6.3"})


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
    # Telegram sends this header when webhook is registered with secret_token.
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not WEBHOOK_SECRET or not hmac.compare_digest(supplied, WEBHOOK_SECRET):
        return "unauthorized", 403
    if request.content_length is not None and request.content_length > 256_000:
        return "payload too large", 413
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("update_id"), int):
        return "invalid Telegram update", 400
    # Log only message type and visibility; never user text, IDs or secrets.
    msg = payload.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("chat"), dict) and msg["chat"].get("type") in ("group", "supergroup"):
        logger.info("[Discussion webhook] inbound group message (has_text=%s, automatic_forward=%s)",
                    bool(msg.get("text")), bool(msg.get("is_automatic_forward")))
    uid = payload["update_id"]
    if not _webhook_slots.acquire(blocking=False):
        return "server busy", 503  # Telegram retries non-2xx.
    try:
        with _webhook_lock:
            if uid in _processed_updates_set:
                _webhook_slots.release()
                return "ok", 200
            if len(_processed_updates) == _processed_updates.maxlen:
                _processed_updates_set.discard(_processed_updates[0])
            _processed_updates.append(uid)
            _processed_updates_set.add(uid)
        future = executor.submit(_process_webhook, json.dumps(payload))
        future.add_done_callback(lambda _: _webhook_slots.release())
    except Exception:
        with _webhook_lock:
            _processed_updates_set.discard(uid)
            try:
                _processed_updates.remove(uid)
            except ValueError:
                pass
        _webhook_slots.release()
        logger.exception("Unable to schedule webhook update")
        return "busy", 503
    return "ok", 200


# ── Runner ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🚀 Starting MSK Bot v6.3...")
    if WEBHOOK_URL:
        # The webhook destination must never contain the bot credential.
        if TOKEN in WEBHOOK_URL:
            raise RuntimeError("WEBHOOK_URL must not contain TELEGRAM_TOKEN")
        bot.set_webhook(url=f"{WEBHOOK_URL}{WEBHOOK_PATH}", secret_token=WEBHOOK_SECRET)
        logger.info("Webhook registered at %s%s", WEBHOOK_URL, WEBHOOK_PATH)
        from waitress import serve
        logger.info("Production HTTP server listening on port %s", os.getenv("PORT", "8000"))
        serve(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), threads=12, connection_limit=200)
    else:
        logger.info("Running in polling mode...")
        bot.infinity_polling()
