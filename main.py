"""
main.py — AI Stock Advisory Bot (Fixed v4.0)

FIXES IN THIS VERSION:
  1. Stock Screener: Fixed yfinance MultiIndex access + expanded to 10 stocks
                     per profile + RSI/trend/signal per stock
  2. Fundamental Data: Full card with MarketCap, 52W H/L, EPS, Revenue,
                       Debt/Equity, Dividend, P/B, Beta
  3. Portfolio: Complete add/view/remove/P&L system (was 100% missing)
  4. AI Data: Wired to ai_engine.py get_live_market_context() which fetches
              Nifty+BankNifty+PE+Top8 stocks with RSI (vs old 2-index only)
  5. Swing Trade: Wired to swing_trades.py get_swing_trades() — 30 stocks,
                  8-condition scoring, full entry/SL/target card
"""

import os
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
import json
import numpy as np
import pandas as pd
import yfinance as yf

# === EXPERT FIX: Yahoo Finance Rate Limit Workaround ===
import random
from requests import Session
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

# Create custom session with retry logic and headers
def _create_yf_session():
    session = Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    # Rotate user agents to avoid detection
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
    ]
    session.headers['User-Agent'] = random.choice(user_agents)
    return session

# Global rate limiter
_last_yf_call = 0
_yf_call_delay = 2.0  # 2 second delay between calls

def _rate_limit_yf():
    """Enforce rate limiting between yfinance calls"""
    global _last_yf_call
    now = time.time()
    elapsed = now - _last_yf_call
    if elapsed < _yf_call_delay:
        time.sleep(_yf_call_delay - elapsed + random.uniform(0.1, 0.5))
    _last_yf_call = time.time()
from collections import deque
from datetime import datetime, date
from flask import Flask, request, jsonify
import telebot
from telebot import types

# ── Import our own modules ────────────────────────────────────────────────
# FIX 4: Use the rich live context from ai_engine instead of the bare
#         get_live_context() that only fetched Nifty + BankNifty
from ai_engine import (
    ai_insights         as engine_ai_insights,
    ai_chat_respond,
    fetch_news,
    get_live_market_context,
    ai_available,
    AI_CHAT_TOPICS,
    AI_CHAT_TOPIC_KEYS,
    add_to_chat,
    clear_chat,
    test_ai_providers,
    debug_ai_status,
)

# FIX 5: Import the real swing scanner (30 stocks, 8-condition scoring)
from swing_trades import get_swing_trades

# ── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ── Env ───────────────────────────────────────────────────────────────────
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is required")

WEBHOOK_URL  = os.getenv("WEBHOOK_URL", "").rstrip("/")
TAVILY_KEY   = os.getenv("TAVILY_API_KEY")
WEBHOOK_PATH = f"/webhook/{TOKEN}"

app      = Flask(__name__)
bot      = telebot.TeleBot(TOKEN, threaded=False, num_threads=4, colorful_logs=False)
executor = ThreadPoolExecutor(max_workers=5)

# ── Cache ──────────────────────────────────────────────────────────────────
_cache = {}
_state = {}
_processed_updates = set()
_lock  = threading.Lock()

# FIX 4: Short TTL for live price data so AI always sees fresh numbers
CACHE_TTL_LIVE = 7200  # 2 hours — aggressive caching to avoid Yahoo rate limits= 300    # 5 min  — live prices / index data
CACHE_TTL_FUND = 21600  # 6 hours — fundamentals (PE, ROE, revenue…)
def get_cached(key: str, ttl: int):
    with _lock:
        d = _cache.get(key)
        if d and time.time() - d["ts"] < ttl:
            return d["val"]
    return None

def set_cached(key: str, val):
    with _lock:
        _cache[key] = {"val": val, "ts": time.time()}

# ── FIX 3: Portfolio ──────────────────────────────────────────────────────
# { user_id: { "SYMBOL": {"qty": int, "avg": float} } }
_portfolio: dict = {}

def get_portfolio(uid: int) -> dict:
    return _portfolio.setdefault(uid, {})

def add_to_portfolio(uid: int, sym: str, qty: int, price: float):
    p = get_portfolio(uid)
    if sym in p:
        old_qty = p[sym]["qty"]
        old_avg = p[sym]["avg"]
        new_qty = old_qty + qty
        new_avg = round((old_qty * old_avg + qty * price) / new_qty, 2)
        p[sym]  = {"qty": new_qty, "avg": new_avg}
    else:
        p[sym] = {"qty": qty, "avg": round(price, 2)}

def remove_from_portfolio(uid: int, sym: str) -> bool:
    p = get_portfolio(uid)
    if sym in p:
        del p[sym]
        return True
    return False

def build_portfolio_card(uid: int) -> str:
    p = get_portfolio(uid)
    if not p:
        return (
            "📂 <b>Your Portfolio is Empty</b>\n\n"
            "Add a position:\n<code>/buy RELIANCE 10 2500</code>\n\n"
            "Remove a position:\n<code>/sell RELIANCE</code>"
        )
    lines = ["💼 <b>PORTFOLIO TRACKER</b>",
             f"📅 {date.today().strftime('%d-%b-%Y')}",
             "━━━━━━━━━━━━━━━━━━━━"]
    total_inv = 0.0
    total_cur = 0.0
    for sym, pos in p.items():
        qty, avg = pos["qty"], pos["avg"]
        df = get_hist(sym, "5d")
        ltp = round(float(df["Close"].iloc[-1]), 2) if not df.empty else avg
        inv = qty * avg
        cur = qty * ltp
        pnl = round(cur - inv, 2)
        pct = round((ltp - avg) / avg * 100, 2) if avg > 0 else 0.0
        icon = "🟢" if pnl >= 0 else "🔴"
        lines.append(
            f"{icon} <b>{sym}</b>  ×{qty}\n"
            f"   Avg: ₹{avg:,.2f}  |  LTP: ₹{ltp:,.2f}\n"
            f"   P&L: ₹{pnl:+,.2f}  ({pct:+.2f}%)"
        )
        total_inv += inv
        total_cur += cur
    total_pnl = round(total_cur - total_inv, 2)
    total_pct = round((total_cur - total_inv) / total_inv * 100, 2) if total_inv else 0.0
    icon = "🟢" if total_pnl >= 0 else "🔴"
    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        f"{icon} <b>Total P&L: ₹{total_pnl:+,.2f}  ({total_pct:+.2f}%)</b>",
        f"💰 Invested: ₹{total_inv:,.2f}",
        f"📊 Current:  ₹{total_cur:,.2f}",
        "",
        "➕ /buy SYM QTY PRICE   ➖ /sell SYM",
    ]
    return "\n".join(lines)

# ── Data Layer ─────────────────────────────────────────────────────────────
def retry_yf(func, *args, retries=3, delay=2, **kwargs):
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            msg = str(e).lower()
            wait = delay * (2 ** i) if ("429" in msg or "rate" in msg) else delay * (i + 1)
            if i < retries - 1:
                time.sleep(wait)
            else:
                raise

def get_hist(sym: str, period: str = "1y") -> pd.DataFrame:
    ttl = CACHE_TTL_LIVE if period in ("1d", "2d", "5d") else CACHE_TTL_FUND
    key = f"h_{sym}_{period}"
    cached = get_cached(key, ttl)
    if cached is not None:
        return cached
    try:
        _rate_limit_yf()  # Enforce delay between API calls
        ticker = yf.Ticker(f"{sym}.NS", session=_create_yf_session())
        df = retry_yf(ticker.history, period=period, auto_adjust=True)
        if df.empty or len(df) < 2:
            return pd.DataFrame()
        set_cached(key, df)
        return df
    except Exception as e:
        logger.error(f"get_hist {sym}: {e}")
        return pd.DataFrame()


def get_info(sym: str) -> dict:
    """FIX 2: Robust fundamental fetch — info + fast_info fallback."""
    key = f"i_{sym}"
    cached = get_cached(key, CACHE_TTL_FUND)
    if cached:
        return cached
    info = {}
    try:
        _rate_limit_yf()  # Enforce delay
        ticker = yf.Ticker(f"{sym}.NS", session=_create_yf_session())

        try:
            info = dict(ticker.info)
        except Exception:
            pass

        # fast_info is more reliable in newer yfinance builds
        try:
            fi = ticker.fast_info
            mapping = {
                "market_cap":           "marketCap",
                "fifty_two_week_high":  "fiftyTwoWeekHigh",
                "fifty_two_week_low":   "fiftyTwoWeekLow",
                "last_price":           "currentPrice",
                "previous_close":       "previousClose",
            }
            for src_attr, dst_key in mapping.items():
                val = getattr(fi, src_attr, None)
                if val is not None:
                    info.setdefault(dst_key, val)
        except Exception:
            pass

        set_cached(key, info)
    except Exception as e:
        logger.error(f"get_info {sym}: {e}")
    return info
# ── Indicators ────────────────────────────────────────────────────────────
def calc_rsi(c: pd.Series) -> float:
    if len(c) < 15:
        return 50.0
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean().replace(0, 1e-10)
    return round(float((100 - 100 / (1 + gain / loss)).iloc[-1]), 1)

def calc_macd(c: pd.Series) -> float:
    return round(float((c.ewm(span=12, adjust=False).mean()
                        - c.ewm(span=26, adjust=False).mean()).iloc[-1]), 2)

def calc_ema(c: pd.Series, span: int) -> float:
    return round(float(c.ewm(span=span, adjust=False).mean().iloc[-1]), 2)

def calc_atr(df: pd.DataFrame) -> float:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return round(float(tr.rolling(14).mean().iloc[-1]), 2)

def calc_asi(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    O, H, L, C = df["Open"], df["High"], df["Low"], df["Close"]
    Cp, Op = C.shift(1), O.shift(1)
    A = (H - Cp).abs();  B = (L - Cp).abs();  CD = (H - L).abs();  D = (Cp - Op).abs()
    R = pd.Series(0.0, index=df.index)
    cA = (A >= B) & (A >= CD); cB = (B >= A) & (B >= CD) & ~cA
    R[cA] = A[cA] + 0.5*B[cA] + 0.25*D[cA]
    R[cB] = B[cB] + 0.5*A[cB] + 0.25*D[cB]
    R[~(cA|cB)] = CD[~(cA|cB)] + 0.25*D[~(cA|cB)]
    R = R.replace(0, 1e-10)
    K = pd.concat([A, B], axis=1).max(axis=1)
    lm = (Cp * 0.20).replace(0, 1e-10)
    SI = 50 * ((C - Cp) + 0.5*(Cp - O) + 0.25*(Cp - Op)) / R * (K / lm)
    return round(float(SI.cumsum().iloc[-1]), 2)

def safe_val(d: dict, *keys, mul: float = 1.0):
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return round(float(v) * mul, 2)
            except Exception:
                pass
    return None

def fmt_cr(val) -> str:
    if val is None:
        return "N/A"
    try:
        cr = float(val) / 1e7
        if cr >= 1_00_000: return f"₹{cr/1_00_000:.2f}L Cr"
        if cr >= 1_000:    return f"₹{cr/1_000:.2f}K Cr"
        return f"₹{cr:.2f} Cr"
    except Exception:
        return "N/A"

# ── FIX 2: Full Advisory Card ──────────────────────────────────────────────
def build_adv(sym: str) -> str:
    sym = sym.upper().replace(".NS", "")
    df  = get_hist(sym, "1y")
    if df.empty:
        return f"❌ <b>{sym}</b> not found. Check the NSE symbol (e.g. RELIANCE, TCS)."

    close = df["Close"]
    ltp   = round(float(close.iloc[-1]), 2)
    prev  = float(close.iloc[-2]) if len(close) > 1 else ltp
    chg   = round((ltp - prev) / prev * 100, 2)
    rsi   = calc_rsi(close)
    macd  = calc_macd(close)
    ema20 = calc_ema(close, 20)
    ema50 = calc_ema(close, 50)
    atr   = calc_atr(df)
    asi   = calc_asi(df)
    trend = ("BULLISH" if ltp > ema20 > ema50
             else "BEARISH" if ltp < ema20 < ema50
             else "NEUTRAL")
    trend_icon = "🔼" if trend == "BULLISH" else "🔽" if trend == "BEARISH" else "↔️"

    # ── Fundamentals ──────────────────────────────────────────────────────
    info = get_info(sym)
    name = info.get("longName") or info.get("shortName") or sym
    pe     = safe_val(info, "trailingPE")
    fwd_pe = safe_val(info, "forwardPE")
    roe    = safe_val(info, "returnOnEquity", mul=100)
    eps    = safe_val(info, "trailingEps")
    mcap   = info.get("marketCap")
    rev    = info.get("totalRevenue")
    de     = safe_val(info, "debtToEquity")
    div_y  = safe_val(info, "dividendYield", mul=100)
    w52h   = safe_val(info, "fiftyTwoWeekHigh")
    w52l   = safe_val(info, "fiftyTwoWeekLow")
    beta   = safe_val(info, "beta")
    pb     = safe_val(info, "priceToBook")

    # Fallback 52W from price history
    n = min(252, len(close))
    if w52h is None: w52h = round(float(close.rolling(n).max().iloc[-1]), 2)
    if w52l is None: w52l = round(float(close.rolling(n).min().iloc[-1]), 2)
    dist52 = round((ltp - w52h) / w52h * 100, 1) if w52h else None

    # ── AI via ai_engine (FIX 4) ──────────────────────────────────────────
    ai_text = engine_ai_insights(sym, ltp, rsi, macd, trend, str(pe or "N/A"), str(roe or "N/A"))

    chg_icon = "🟢" if chg >= 0 else "🔴"
    rows = [
        f"🏢 <b>{name}</b>  ({sym})",
        f"{chg_icon} LTP: ₹{ltp:,.2f}  <b>({chg:+.2f}%)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📐 EMA20: ₹{ema20}  |  EMA50: ₹{ema50}",
        f"📏 52W H: ₹{w52h}  |  52W L: ₹{w52l}"
             + (f"  ({dist52:+.1f}% from peak)" if dist52 else ""),
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔬 Trend: <b>{trend} {trend_icon}</b>",
        f"📊 RSI: {rsi}  |  MACD: {'▲' if macd > 0 else '▼'} {macd}  |  ASI: {asi}",
        f"📉 ATR(14): ₹{atr}",
        "━━━━━━━━━━━━━━━━━━━━",
        "📋 <b>FUNDAMENTALS</b>",
        f"  Market Cap  : {fmt_cr(mcap)}",
        f"  Revenue     : {fmt_cr(rev)}",
        f"  PE  (TTM)   : {pe or 'N/A'}  |  Fwd PE: {fwd_pe or 'N/A'}",
        f"  Price/Book  : {pb or 'N/A'}",
        f"  ROE         : {roe or 'N/A'}%  |  EPS: ₹{eps or 'N/A'}",
        f"  Debt/Equity : {de or 'N/A'}  |  Beta: {beta or 'N/A'}",
        f"  Div Yield   : {div_y or 'N/A'}%",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🎯 Target: ₹{round(ltp + 1.5*atr, 2)}  |  SL: ₹{round(ltp - 2*atr, 2)}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🤖 <b>AI INSIGHTS</b>\n{ai_text}",
    ]
    return "\n".join(rows)

# ── FIX 1: Stock Screener — per-stock RSI/trend/signal, 10 stocks/profile ─
SCREENER_STOCKS = {
    "conservative": ["HDFCBANK","TCS","INFY","ITC","ONGC",
                     "SBIN","WIPRO","NTPC","POWERGRID","COALINDIA"],
    "moderate":     ["RELIANCE","BHARTIARTL","AXISBANK","MARUTI","LT",
                     "KOTAKBANK","BAJFINANCE","SUNPHARMA","TITAN","M&M"],
    "aggressive":   ["TATAMOTORS","ADANIENT","JSWSTEEL","TATAPOWER",
                     "ZOMATO","IRFC","HAL","BEL","PFC","ADANIPORTS"],
}

def build_scan(profile: str) -> str:
    syms = SCREENER_STOCKS.get(profile, [])
    if not syms:
        return "❌ Unknown screener profile."
    labels = {"conservative":"🏦 CONSERVATIVE","moderate":"⚖️ MODERATE","aggressive":"🚀 AGGRESSIVE"}
    lines = [f"📊 <b>{labels.get(profile,'SCREENER')}</b>",
             f"📅 {date.today().strftime('%d-%b-%Y')}",
             "━━━━━━━━━━━━━━━━━━━━"]
    for sym in syms:
        try:
            df = get_hist(sym, "3mo")
            if df.empty or len(df) < 15:
                continue
            close = df["Close"]
            ltp   = round(float(close.iloc[-1]), 2)
            prev  = float(close.iloc[-2])
            chg   = round((ltp - prev) / prev * 100, 2)
            rsi   = calc_rsi(close)
            ema20 = calc_ema(close, 20)
            ema50 = calc_ema(close, 50)
            trend = ("📈 Bull" if ltp > ema20 > ema50
                     else "📉 Bear" if ltp < ema20 < ema50
                     else "↔️ Neut")
            signal = ("⚡ OVERSOLD"  if rsi < 35
                      else "⚠️ OVERBOUGHT" if rsi > 68
                      else "✅ BUY ZONE"   if ltp > ema20 and chg > 0
                      else "⏳ WATCH")
            icon = "🟢" if chg >= 0 else "🔴"
            lines.append(
                f"{icon} <b>{sym}</b>: ₹{ltp:,.2f} ({chg:+.2f}%)\n"
                f"   RSI:{rsi} | {trend} | {signal}"
            )
        except Exception as e:
            logger.warning(f"Screener {sym}: {e}")
    if len(lines) <= 3:
        lines.append("❌ Data unavailable. Try again in a moment.")
    lines.append("\n⚠️ Educational only. Not SEBI advice.")
    return "\n".join(lines)

def build_breadth() -> str:
    lines = ["📊 <b>MARKET BREADTH</b>", "━━━━━━━━━━━━━━━━━━━━"]
    indices = {"NIFTY 50":"^NSEI","BANK NIFTY":"^NSEBANK",
               "NIFTY IT":"^CNXIT","NIFTY MIDCAP":"^NSEMDCP50"}
    for name, ticker in indices.items():
        try:
            d = yf.Ticker(ticker).history(period="5d")
            if len(d) >= 2:
                l   = round(float(d["Close"].iloc[-1]), 2)
                p   = round(float(d["Close"].iloc[-2]), 2)
                chg = round((l - p) / p * 100, 2)
                wh  = round(float(d["High"].max()), 2)
                wl  = round(float(d["Low"].min()), 2)
                icon = "🟢" if chg >= 0 else "🔴"
                lines.append(f"{icon} <b>{name}</b>: {l:,.2f} ({chg:+.2f}%)\n"
                             f"   5D Range: {wl:,} – {wh:,}")
        except Exception:
            pass
    return "\n".join(lines)

def build_news() -> str:
    if not TAVILY_KEY:
        return "📰 Set TAVILY_API_KEY for live news."
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_KEY,
                  "query": "India NSE Nifty stock market news today",
                  "max_results": 5},
            timeout=8,
        )
        items = r.json().get("results", [])
        if items:
            return "📰 <b>MARKET NEWS</b>\n━━━━━━━━━━━━━━━━━━━━\n" + \
                   "\n".join(f"• {x['title'][:90]}" for x in items)
    except Exception as e:
        logger.warning(f"News: {e}")
    return "📰 News unavailable right now."

# ── Keyboards ──────────────────────────────────────────────────────────────
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add("🔍 Analysis", "📊 Breadth", "🤖 AI")
    kb.add("🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive")
    # FIX 3: Portfolio added. FIX 5: two swing modes.
    kb.add("🎯 Swing (Safe)", "🚀 Swing (Agr)", "💼 Portfolio")
    kb.add("📰 News")
    return kb

def ai_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    for topic in AI_CHAT_TOPICS.keys():
        kb.add(topic)
    kb.add("🔙 Menu")
    return kb

# ── Handlers ───────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(message):
    _state[message.chat.id] = None
    bot.send_message(
        message.chat.id,
        "👋 <b>AI Stock Advisory Bot v4.0</b>\n\n"
        "Type any NSE symbol (e.g. <code>RELIANCE</code>) for full analysis.\n"
        "Use the menu for screeners, AI, swing trades, and portfolio.",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.send_message(
        message.chat.id,
        "📖 <b>COMMANDS</b>\n\n"
        "Symbol Analysis:\n"
        "  Type any NSE symbol — e.g. <code>TCS</code>, <code>INFY</code>\n\n"
        "Portfolio:\n"
        "  /buy SYMBOL QTY PRICE\n"
        "  /sell SYMBOL\n"
        "  /portfolio\n\n"
        "AI:\n"
        "  /clear — Reset AI chat history\n\n"
        "⚠️ Educational only. Not SEBI registered.",
        parse_mode="HTML",
    )

# ── FIX 3: Portfolio commands ──────────────────────────────────────────────
@bot.message_handler(commands=["buy"])
def cmd_buy(message):
    parts = message.text.strip().split()
    if len(parts) != 4:
        bot.send_message(message.chat.id,
            "Usage: <code>/buy SYMBOL QUANTITY AVG_PRICE</code>\n"
            "Example: <code>/buy RELIANCE 10 2500</code>", parse_mode="HTML")
        return
    try:
        sym   = parts[1].upper().replace(".NS", "")
        qty   = int(parts[2])
        price = float(parts[3])
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid format. Example: /buy RELIANCE 10 2500")
        return
    if qty <= 0 or price <= 0:
        bot.send_message(message.chat.id, "❌ Quantity and price must be positive.")
        return
    add_to_portfolio(message.chat.id, sym, qty, price)
    bot.send_message(message.chat.id,
        f"✅ Added <b>{qty} × {sym}</b> @ ₹{price:.2f} to portfolio.\n"
        f"View with: /portfolio",
        parse_mode="HTML")

@bot.message_handler(commands=["sell"])
def cmd_sell(message):
    parts = message.text.strip().split()
    if len(parts) < 2:
        bot.send_message(message.chat.id,
            "Usage: <code>/sell SYMBOL</code>  e.g. <code>/sell RELIANCE</code>", parse_mode="HTML")
        return
    sym = parts[1].upper().replace(".NS", "")
    if remove_from_portfolio(message.chat.id, sym):
        bot.send_message(message.chat.id, f"✅ Removed <b>{sym}</b> from portfolio.", parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, f"❌ <b>{sym}</b> not found in portfolio.", parse_mode="HTML")

@bot.message_handler(commands=["portfolio"])
def cmd_portfolio_cmd(message):
    bot.send_message(message.chat.id, "⏳ Fetching live prices for your portfolio…")
    def _run():
        bot.send_message(message.chat.id, build_portfolio_card(message.chat.id), parse_mode="HTML")
    executor.submit(_run)

@bot.message_handler(commands=["clear"])
def cmd_clear(message):
    clear_chat(message.chat.id)
    bot.send_message(message.chat.id, "🗑️ AI chat history cleared.")

# ── Menu buttons ───────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text == "🔙 Menu")
def back_to_main(message):
    _state[message.chat.id] = None
    bot.send_message(message.chat.id, "📋 Main Menu", reply_markup=main_keyboard())

# FIX 3: Portfolio button
@bot.message_handler(func=lambda m: m.text == "💼 Portfolio")
def portfolio_button(message):
    bot.send_message(message.chat.id, "⏳ Loading portfolio…")
    def _run():
        bot.send_message(message.chat.id, build_portfolio_card(message.chat.id), parse_mode="HTML")
    executor.submit(_run)

# FIX 4: AI — uses ai_engine.py for full live context (Nifty + PE + top stocks)
@bot.message_handler(func=lambda m: m.text == "🤖 AI")
def enter_ai_mode(message):
    _state[message.chat.id] = "ai"
    bot.send_message(
        message.chat.id,
        "🤖 <b>AI Mode</b>\nLive market data auto-injected. Ask anything or tap a topic.",
        parse_mode="HTML",
        reply_markup=ai_keyboard(),
    )

@bot.message_handler(func=lambda m: m.text in AI_CHAT_TOPIC_KEYS)
def ai_topic_button(message):
    uid  = message.chat.id
    text = AI_CHAT_TOPICS[message.text]
    bot.send_message(uid, "⏳ Thinking with live data…")
    def _run():
        # FIX 4: ai_chat_respond calls get_live_market_context() internally
        resp = ai_chat_respond(uid, text)
        bot.send_message(uid, resp or "❌ Empty response.", parse_mode="HTML", reply_markup=ai_keyboard())
    executor.submit(_run)

# FIX 1: Screener buttons — direct mapping, no split() parsing bug
@bot.message_handler(func=lambda m: m.text in ["🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive"])
def scan_button(message):
    profile_map = {
        "🏦 Conservative": "conservative",
        "⚖️ Moderate":     "moderate",
        "🚀 Aggressive":   "aggressive",
    }
    profile = profile_map[message.text]
    bot.send_message(message.chat.id, f"⏳ Scanning {message.text} stocks…")
    def _run():
        bot.send_message(message.chat.id, build_scan(profile), parse_mode="HTML")
    executor.submit(_run)

@bot.message_handler(func=lambda m: m.text == "📊 Breadth")
def breadth_button(message):
    bot.send_message(message.chat.id, build_breadth(), parse_mode="HTML")

# FIX 5: Swing Trade — wired to swing_trades.get_swing_trades()
# Old code: 4 stocks, RSI<35 only, blocks thread, no targets/SL
# New code: 30 stocks, 8-condition scoring, non-blocking, full trade card
@bot.message_handler(func=lambda m: m.text in ["🎯 Swing (Safe)", "🚀 Swing (Agr)"])
def swing_button(message):
    mode  = "conservative" if "Safe" in message.text else "aggressive"
    label = "6/8 conditions" if mode == "conservative" else "5/8 conditions"
    bot.send_message(
        message.chat.id,
        f"⏳ Running swing scanner ({label})…\n"
        "Checks: EMA trend, ADX, RSI, MACD, Volume, Bollinger Bands (may take ~20s)"
    )
    def _run():
        try:
            result = get_swing_trades(mode=mode)
            # Chunk if > 3800 chars (Telegram limit 4096)
            if len(result) <= 3800:
                bot.send_message(message.chat.id, result, parse_mode="HTML")
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
                    bot.send_message(message.chat.id, part, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Swing scan: {e}")
            bot.send_message(message.chat.id, f"❌ Swing scan failed: {e}")
    executor.submit(_run)

@bot.message_handler(func=lambda m: m.text == "🔍 Analysis")
def analysis_button(message):
    _state[message.chat.id] = "awaiting_symbol"
    bot.send_message(
        message.chat.id,
        "🔍 <b>Stock Analysis</b>\n\nPlease type the NSE symbol you want to analyse.\nExample: <code>RELIANCE</code>, <code>TCS</code>, <code>INFY</code>",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

@bot.message_handler(func=lambda m: m.text == "📰 News")
def news_button(message):
    bot.send_message(message.chat.id, build_news(), parse_mode="HTML")

# ── Catch-all: symbol analysis or AI chat ──────────────────────────────────
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    uid  = message.chat.id
    text = message.text.strip()

    if _state.get(uid) == "ai":
        bot.send_message(uid, "⏳ Thinking…")
        def _ai():
            # FIX 4: uses get_live_market_context() + conversation history
            resp = ai_chat_respond(uid, text)
            bot.send_message(uid, resp or "❌ AI unavailable.", parse_mode="HTML",
                             reply_markup=ai_keyboard())
        executor.submit(_ai)
        return
          
    if _state.get(uid) == "awaiting_symbol":
        _state[uid] = None
        sym = text.upper().replace(".NS", "")
        if 2 <= len(sym) <= 15 and all(c.isalnum() or c == "&" for c in sym):
            bot.send_message(uid, f"🔍 Analyzing <b>{sym}</b>...", parse_mode="HTML")
            def _adv():
                bot.send_message(uid, build_adv(sym), parse_mode="HTML")
            executor.submit(_adv)
        else:
            bot.send_message(uid, "❌ Please enter a valid NSE symbol (2-15 alphanumeric characters).")
        return

    sym = text.upper().replace(".NS", "")
    # Basic symbol validation: 2-15 chars, only alphanumeric + &
    if 2 <= len(sym) <= 15 and all(c.isalnum() or c == "&" for c in sym):
        bot.send_message(uid, f"🔍 Analyzing <b>{sym}</b>…", parse_mode="HTML")
        def _adv():
            bot.send_message(uid, build_adv(sym), parse_mode="HTML")
        executor.submit(_adv)

# ── Flask routes ───────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "ok",
        "version": "4.0_fixed",
        "fixes": ["screener_multiindex","fundamentals_full","portfolio","ai_live_context","swing_8conditions"],
    })

@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({
        "bot":  "running",
        "ai":   "available" if ai_available() else "no keys",
        "time": datetime.now().strftime("%d-%b-%Y %H:%M IST"),
    })

@app.route("/test_ai", methods=["GET"])
def route_test_ai():
    return jsonify(test_ai_providers())

@app.route("/debug_ai", methods=["GET"])
def route_debug_ai():
    return jsonify(debug_ai_status())

def process_update(update_json: str):
    from requests.exceptions import ConnectionError as ReqConnErr
    for _attempt in range(3):
        try:
            update = telebot.types.Update.de_json(update_json)
            bot.process_new_updates([update])
            return
        except (ReqConnErr, Exception) as e:
            if _attempt < 2:
                time.sleep(1.5)
            else:
                logger.error(f"process_update failed after 3 retries: {e}")

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    data = request.get_data().decode("utf-8")
    try:
        uid = json.loads(data).get("update_id")
        with _lock:
            if uid in _processed_updates:
                return "OK", 200
            _processed_updates.add(uid)
            if len(_processed_updates) > 500:
                _processed_updates.discard(min(_processed_updates))
    except Exception:
        pass
    executor.submit(process_update, data)
    return "OK", 200

if __name__ == "__main__":
    try:
        yf.Ticker("^NSEI").history(period="1d")
        logger.info("yfinance warm-up OK")
    except Exception:
        pass
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
