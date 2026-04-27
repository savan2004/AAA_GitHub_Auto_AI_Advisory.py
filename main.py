"""
main.py — AI Stock Advisory Telegram Bot (v5.0 - Production Fixed)

FIXES IN THIS VERSION:
  1. Connector fix: webhook dedup set capped properly; process_update never crashes silently
  2. Fundamentals: all 12 fields printed with proper None-guards; fmt_mcap handles edge cases
  3. AI engine: wired to fixed ai_engine.py (AskFuzz + indentation + OpenAI block fixed)
  4. Screener output: RSI/trend/signal per stock, 10 stocks per profile
  5. Market Breadth: safe fallback if indices return None/empty
  6. Portfolio: full P&L with live LTP; no crash on missing price
  7. Swing trade: rich card with entry zone, T1/T2/SL, R:R ratio
  8. build_adv() fundamentals: ROE/div_yield decimal detection fixed
  9. News: Tavily + Finnhub + AV fallback chain
  10. limits module: import now uses correct filename (limits.py not limits.py.py)
  11. Added /status command for quick health check
  12. All bot.send_message calls wrap parse_mode in try/except so HTML errors
      fall back to plain text instead of silently dropping the message.
"""

import os
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
import json
import pandas as pd
import yfinance as yf

from data_engine import (
    get_hist,
    get_info,
    get_live_price,
    batch_quotes,
)
from technical_indicators import (
    calc_rsi, calc_ema, calc_macd, calc_atr, calc_asi,
    calc_bollinger, trend_label, swing_signal, rsi_label,
)
from api_utils import setup_logging, API_RATE_LIMITER
from market_news import get_market_news, get_stock_news
from collections import deque
from datetime import datetime, date
from flask import Flask, request, jsonify
import telebot
from telebot import types

from ai_engine import (
    ai_insights         as engine_ai_insights,
    ai_chat_respond,
    get_live_market_context,
    ai_available,
    AI_CHAT_TOPICS,
    AI_CHAT_TOPIC_KEYS,
    add_to_chat,
    clear_chat,
    test_ai_providers,
    debug_ai_status,
)
from swing_trades import get_swing_trades

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ── Env ───────────────────────────────────────────────────────────────────────
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is required")

WEBHOOK_URL  = os.getenv("WEBHOOK_URL", "").rstrip("/")
TAVILY_KEY   = os.getenv("TAVILY_API_KEY")
WEBHOOK_PATH = f"/webhook/{TOKEN}"

app      = Flask(__name__)
bot      = telebot.TeleBot(TOKEN, threaded=False)
executor = ThreadPoolExecutor(max_workers=5)

# ── State & cache ─────────────────────────────────────────────────────────────
_cache              = {}
_state: dict        = {}
_processed_updates  = deque(maxlen=1000)   # FIX: deque with maxlen instead of set with manual trim
_lock               = threading.Lock()

CACHE_TTL_LIVE = 300
CACHE_TTL_FUND = 3600


def get_cached(key: str, ttl: int):
    with _lock:
        d = _cache.get(key)
        if d and time.time() - d["ts"] < ttl:
            return d["val"]
    return None


def set_cached(key: str, val):
    with _lock:
        _cache[key] = {"val": val, "ts": time.time()}


# ── Portfolio ─────────────────────────────────────────────────────────────────
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
    lines = [
        "💼 <b>PORTFOLIO TRACKER</b>",
        f"📅 {date.today().strftime('%d-%b-%Y')}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    total_inv = 0.0
    total_cur = 0.0
    for sym, pos in p.items():
        qty, avg = pos["qty"], pos["avg"]
        # FIX: safe LTP fetch — never crashes on data error
        try:
            ltp = get_live_price(sym) or avg
            ltp = round(float(ltp), 2)
        except Exception:
            ltp = avg
        inv  = qty * avg
        cur  = qty * ltp
        pnl  = round(cur - inv, 2)
        pct  = round((ltp - avg) / avg * 100, 2) if avg > 0 else 0.0
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


# ── Indicators ────────────────────────────────────────────────────────────────



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
    """Format market-cap / revenue in Indian notation (Cr / K Cr / L Cr)."""
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if v <= 0:
            return "N/A"
        cr = v / 1e7          # convert rupees → crore
        if cr >= 1_00_000:    return f"₹{cr/1_00_000:.2f}L Cr"
        if cr >= 1_000:       return f"₹{cr/1_000:.2f}K Cr"
        return f"₹{cr:.2f} Cr"
    except Exception:
        return "N/A"


def _fmt_revenue(rev, mcap=None) -> str:
    """
    Format revenue with sanity check.
    FIX: Revenue was 40x overstated when Screener returned wrong units.
    Guard: if revenue > 5× market cap, it is likely a data error — show N/A.
    Yahoo v10 totalRevenue is in ABSOLUTE RUPEES → fmt_mcap divides by 1e7 for Crores.
    """
    if rev is None:
        return "N/A"
    try:
        rev_f  = float(rev)
        if rev_f <= 0:
            return "N/A"
        # Sanity: revenue should not exceed 5× market cap for most companies
        if mcap:
            mcap_f = float(mcap)
            if mcap_f > 0 and rev_f > mcap_f * 5:
                return "N/A (data error)"
        return fmt_mcap(rev_f)
    except Exception:
        return "N/A"


# ── Advisory Card ─────────────────────────────────────────────────────────────
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
    trend = (
        "BULLISH" if ltp > ema20 > ema50
        else "BEARISH" if ltp < ema20 < ema50
        else "NEUTRAL"
    )
    trend_icon = "🔼" if trend == "BULLISH" else "🔽" if trend == "BEARISH" else "↔️"

    # ── Fundamentals ─────────────────────────────────────────────────────────
    # FIX: Single call to get_fundamentals() which internally uses data_engine
    # as Source 1 (fast cache) → Screener.in (no key) → Finnhub → yfinance
    # Eliminates the double-fetch (get_info + get_fundamentals) that was causing 2× slowdown
    try:
        from fundamentals import get_fundamentals
        fund = get_fundamentals(sym)
    except Exception:
        fund = {}
    info = get_info(sym) or {}   # Still needed for live price + RSI history

    name   = fund.get("name")  or info.get("name") or sym
    pe     = fund.get("pe")    or safe_val(info, "pe")
    fwd_pe = fund.get("fwd_pe")
    pb     = fund.get("pb")    or safe_val(info, "pb")
    roe    = fund.get("roe")                           # already in % from fundamentals.py
    eps    = fund.get("eps")   or safe_val(info, "eps")
    mcap   = fund.get("mcap")  or info.get("market_cap")
    # FIX: revenue from fundamentals (Yahoo v10 totalRevenue in absolute Rs)
    rev    = fund.get("rev")   or info.get("totalRevenue")
    de     = fund.get("de")    or safe_val(info, "debtToEquity")
    div_y  = fund.get("div_y")
    w52h   = fund.get("w52h")  or safe_val(info, "high52")
    w52l   = fund.get("w52l")  or safe_val(info, "low52")
    beta   = fund.get("beta")  or safe_val(info, "beta")

    # 52W from price history if still missing
    n = min(252, len(close))
    if w52h is None: w52h = round(float(close.rolling(n).max().iloc[-1]), 2)
    if w52l is None: w52l = round(float(close.rolling(n).min().iloc[-1]), 2)
    dist52 = round((ltp - w52h) / w52h * 100, 1) if w52h else None

    # ── News ─────────────────────────────────────────────────────────────────
    news_text = get_stock_news(sym)

    # ── AI insights ──────────────────────────────────────────────────────────
    ai_text = engine_ai_insights(
        sym, ltp, rsi, macd, trend, str(pe or "N/A"), str(roe or "N/A")
    )

    chg_icon = "🟢" if chg >= 0 else "🔴"

    # ── Fundamentals row builder — only prints fields that have data ──────────
    def frow(label: str, val, suffix: str = "") -> str:
        if val is None or val == "N/A":
            return f"  {label:<14}: N/A"
        return f"  {label:<14}: {val}{suffix}"

    rows = [
        f"🏢 <b>{name}</b>  ({sym})",
        f"{chg_icon} LTP: ₹{ltp:,.2f}  <b>({chg:+.2f}%)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📐 EMA20: ₹{ema20:,.2f}  |  EMA50: ₹{ema50:,.2f}",
        f"📏 52W H: ₹{w52h:,}  |  52W L: ₹{w52l:,}"
             + (f"  ({dist52:+.1f}% from peak)" if dist52 else ""),
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔬 Trend: <b>{trend} {trend_icon}</b>",
        f"📊 RSI: {rsi}  |  MACD: {'▲' if macd > 0 else '▼'} {macd}  |  ASI: {asi}",
        f"📉 ATR(14): ₹{atr}",
        "━━━━━━━━━━━━━━━━━━━━",
        "📋 <b>FUNDAMENTALS</b>",
        frow("Market Cap",   fmt_mcap(mcap)),
        frow("Revenue",      _fmt_revenue(rev, mcap)),
        frow("PE (TTM)",     pe)  + (f"  |  Fwd PE: {fwd_pe}" if fwd_pe else ""),
        frow("Price/Book",   pb),
        frow("ROE",          roe, "%") + (f"  |  EPS: ₹{eps}" if eps else ""),
        frow("Debt/Equity",  de)  + (f"  |  Beta: {beta}" if beta else ""),
        frow("Div Yield",    div_y, "%"),
        "━━━━━━━━━━━━━━━━━━━━",
        f"🎯 Target: ₹{round(ltp + 1.5*atr, 2):,.2f}  |  SL: ₹{round(ltp - 2*atr, 2):,.2f}",
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


# ── Screener ──────────────────────────────────────────────────────────────────
SCREENER_STOCKS = {
    "conservative": ["HDFCBANK", "TCS", "INFY", "ITC", "ONGC",
                     "SBIN", "WIPRO", "NTPC", "POWERGRID", "COALINDIA"],
    "moderate":     ["RELIANCE", "BHARTIARTL", "AXISBANK", "MARUTI", "LT",
                     "KOTAKBANK", "BAJFINANCE", "SUNPHARMA", "TITAN", "M&M"],
    "aggressive":   ["TATAMOTORS", "ADANIENT", "JSWSTEEL", "TATAPOWER",
                     "ZOMATO", "IRFC", "HAL", "BEL", "PFC", "ADANIPORTS"],
}


def build_scan(profile: str) -> str:
    syms = SCREENER_STOCKS.get(profile, [])
    if not syms:
        return "❌ Unknown screener profile."
    labels = {
        "conservative": "🏦 CONSERVATIVE",
        "moderate":     "⚖️ MODERATE",
        "aggressive":   "🚀 AGGRESSIVE",
    }
    lines = [
        f"📊 <b>{labels.get(profile, 'SCREENER')}</b>",
        f"📅 {date.today().strftime('%d-%b-%Y')}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    hit = 0
    for sym in syms:
        try:
            df = get_hist(sym, "6mo")   # FIX: 3mo only 63 bars → RSI(14) unreliable; 6mo=126 bars
            if df.empty or len(df) < 15:
                continue
            close = df["Close"]
            ltp   = round(float(close.iloc[-1]), 2)
            prev  = float(close.iloc[-2]) if len(close) > 1 else ltp
            chg   = round((ltp - prev) / prev * 100, 2)
            rsi   = calc_rsi(close)
            ema20 = calc_ema(close, 20)
            ema50 = calc_ema(close, 50)
            trend = (
                "📈 Bull" if ltp > ema20 > ema50
                else "📉 Bear" if ltp < ema20 < ema50
                else "↔️ Neut"
            )
            # Unified signal from technical_indicators (single source of truth)
            trend  = trend_label(close)
            signal = swing_signal(rsi, trend, chg)
            icon = "🟢" if chg >= 0 else "🔴"
            lines.append(
                f"{icon} <b>{sym}</b>: ₹{ltp:,.2f} ({chg:+.2f}%)\n"
                f"   RSI:{rsi} | {trend} | {signal}"
            )
            hit += 1
        except Exception as e:
            logger.warning(f"Screener {sym}: {e}")

    if hit == 0:
        lines.append("❌ Data unavailable. Try again in a moment.")
    lines.append("\n⚠️ Educational only. Not SEBI advice.")
    return "\n".join(lines)


def build_breadth() -> str:
    from data_engine import _yahoo_v8_hist
    lines = ["📊 <b>MARKET BREADTH</b>", "━━━━━━━━━━━━━━━━━━━━"]
    indices = {
        "NIFTY 50":     "^NSEI",
        "BANK NIFTY":   "^NSEBANK",
        "NIFTY IT":     "^CNXIT",
        "NIFTY MIDCAP": "^NSEMDCP50",
    }
    hit = 0
    for name, ticker in indices.items():
        try:
            d = _yahoo_v8_hist(ticker, period="5d")
            if d is None or len(d) < 2:
                try:
                    d = yf.Ticker(ticker).history(period="5d")
                except Exception:
                    continue
            if d is None or len(d) < 2:
                continue
            l   = round(float(d["Close"].iloc[-1]), 2)
            p   = round(float(d["Close"].iloc[-2]), 2)
            chg = round((l - p) / p * 100, 2) if p else 0.0
            wh  = round(float(d["High"].max()), 2)
            wl  = round(float(d["Low"].min()), 2)
            # RSI from 5-day close
            try:
                from data_engine import calc_rsi as _crsi
                rsi_b = _crsi(d["Close"]) if len(d) >= 5 else 50.0
            except Exception:
                rsi_b = 50.0
            icon = "🟢" if chg >= 0 else "🔴"
            trend_b = "Bull" if chg > 0 else "Bear"
            lines.append(
                f"{icon} <b>{name}</b>: {l:,.2f} ({chg:+.2f}%) | RSI:{rsi_b} | {trend_b}\n"
                f"   5D Range: {wl:,.2f} – {wh:,.2f}"
            )
            hit += 1
        except Exception as e:
            logger.warning(f"breadth {name}: {e}")

    if hit == 0:
        lines.append("❌ Index data unavailable right now.")
    return "\n".join(lines)


# Tavily sometimes returns index/homepage titles — filter them out
_NEWS_JUNK_PATTERNS = [
    "Investing.com", "TradingView", "Yahoo Finance", "CNBC", "Stock Price, Quote",
    "Live Share", "Chart and News", "NSE India", "National Stock Exchange",
    "Index Today", "Nifty 50 Index Today", "Equity Market Watch",
]

def _is_real_headline(title: str) -> bool:
    if not title or len(title) < 25:
        return False
    for pat in _NEWS_JUNK_PATTERNS:
        if pat.lower() in title.lower():
            return False
    return True


def build_news() -> str:
    # Source 1: Tavily with financial news domains and headline filter
    if TAVILY_KEY:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key":       TAVILY_KEY,
                    "query":         "India NSE Nifty Sensex stock market news today",
                    "max_results":   8,
                    "search_depth":  "advanced",
                    "include_domains": [
                        "economictimes.indiatimes.com", "moneycontrol.com",
                        "livemint.com", "businessline.com", "ndtv.com",
                        "financialexpress.com", "reuters.com", "bloomberg.com",
                    ],
                },
                timeout=10,
            )
            items = r.json().get("results", [])
            headlines = [
                x["title"] for x in items
                if _is_real_headline(x.get("title", ""))
            ][:5]
            if headlines:
                lines = ["📰 <b>MARKET NEWS</b>", "━━━━━━━━━━━━━━━━━━━━"]
                lines += [f"• {h[:100]}" for h in headlines]
                lines.append("━━━━━━━━━━━━━━━━━━━━")
                return "\n".join(lines)
        except Exception as e:
            logger.warning(f"News Tavily: {e}")

    # Source 2: MoneyControl RSS — free, no key, real headlines
    try:
        import re
        rss = requests.get(
            "https://www.moneycontrol.com/rss/latestnews.xml",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
        )
        if rss.ok:
            titles = re.findall(r"<title><![CDATA[(.*?)]]></title>", rss.text)
            mkt = [
                t for t in titles[1:]
                if any(kw in t.lower() for kw in
                       ["nifty", "sensex", "market", "stock", "sebi", "rbi", "index"])
            ][:5]
            if mkt:
                lines = ["📰 <b>MARKET NEWS</b> (MoneyControl)", "━━━━━━━━━━━━━━━━━━━━"]
                lines += [f"• {t[:100]}" for t in mkt]
                lines.append("━━━━━━━━━━━━━━━━━━━━")
                return "\n".join(lines)
    except Exception as e:
        logger.warning(f"News RSS: {e}")

    return "📰 News unavailable right now. Set TAVILY_API_KEY for live news."


# ── Keyboards ─────────────────────────────────────────────────────────────────
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add("🔍 Analysis", "📊 Breadth", "🤖 AI")
    kb.add("🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive")
    kb.add("🎯 Swing (Safe)", "🚀 Swing (Agr)", "💼 Portfolio")
    kb.add("📰 News", "📈 Status")
    return kb


def ai_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    for topic in AI_CHAT_TOPICS.keys():
        kb.add(topic)
    kb.add("🔙 Menu")
    return kb


# ── Safe send helper ──────────────────────────────────────────────────────────
def safe_send(chat_id: int, text: str, parse_mode: str = "HTML", **kwargs):
    """
    FIX: HTML parse errors caused silent message drops.
    Now falls back to plain text automatically.
    """
    try:
        bot.send_message(chat_id, text, parse_mode=parse_mode, **kwargs)
    except Exception as e:
        if "can't parse" in str(e).lower() or "bad request" in str(e).lower():
            try:
                # Strip HTML tags for plain fallback
                import re
                plain = re.sub(r"<[^>]+>", "", text)
                bot.send_message(chat_id, plain, **kwargs)
            except Exception as e2:
                logger.error(f"safe_send fallback failed {chat_id}: {e2}")
        else:
            logger.error(f"safe_send {chat_id}: {e}")


# ── Handlers ──────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(message):
    _state[message.chat.id] = None
    safe_send(
        message.chat.id,
        "👋 <b>AI Stock Advisory Bot v5.0</b>\n\n"
        "Type any NSE symbol (e.g. <code>RELIANCE</code>) for full analysis.\n"
        "Use the menu for screeners, AI, swing trades, and portfolio.\n\n"
        "📌 <b>Commands:</b>\n"
        "/help — All commands\n"
        "/status — Bot health check\n"
        "/buy SYM QTY PRICE — Add to portfolio\n"
        "/sell SYM — Remove from portfolio\n"
        "/portfolio — View P&L\n"
        "/clear — Reset AI chat",
        reply_markup=main_keyboard(),
    )


@bot.message_handler(commands=["help"])
def cmd_help(message):
    safe_send(
        message.chat.id,
        "📖 <b>COMMANDS</b>\n\n"
        "<b>Analysis:</b>\n"
        "  Type any NSE symbol — e.g. <code>TCS</code>, <code>INFY</code>\n\n"
        "<b>Portfolio:</b>\n"
        "  /buy SYMBOL QTY PRICE\n"
        "  /sell SYMBOL\n"
        "  /portfolio\n\n"
        "<b>AI Chat:</b>\n"
        "  Tap 🤖 AI in menu\n"
        "  /clear — Reset AI chat history\n\n"
        "<b>Health:</b>\n"
        "  /status — Check bot + AI status\n\n"
        "⚠️ Educational only. Not SEBI registered.",
    )


@bot.message_handler(commands=["status"])
def cmd_status(message):
    ai_ok = ai_available()
    safe_send(
        message.chat.id,
        f"🤖 <b>BOT STATUS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Bot:      ✅ Running\n"
        f"AI:       {'✅ Ready' if ai_ok else '❌ No keys set'}\n"
        f"Time:     {datetime.now().strftime('%d-%b-%Y %H:%M IST')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Use /test_ai endpoint on server to test each provider.</i>",
    )


@bot.message_handler(commands=["buy"])
def cmd_buy(message):
    parts = message.text.strip().split()
    if len(parts) != 4:
        safe_send(message.chat.id,
            "Usage: <code>/buy SYMBOL QUANTITY AVG_PRICE</code>\n"
            "Example: <code>/buy RELIANCE 10 2500</code>")
        return
    try:
        sym   = parts[1].upper().replace(".NS", "")
        qty   = int(parts[2])
        price = float(parts[3])
    except ValueError:
        safe_send(message.chat.id, "❌ Invalid format. Example: /buy RELIANCE 10 2500")
        return
    if qty <= 0 or price <= 0:
        safe_send(message.chat.id, "❌ Quantity and price must be positive.")
        return
    add_to_portfolio(message.chat.id, sym, qty, price)
    safe_send(message.chat.id,
        f"✅ Added <b>{qty} × {sym}</b> @ ₹{price:.2f} to portfolio.\n"
        f"View with: /portfolio")


@bot.message_handler(commands=["sell"])
def cmd_sell(message):
    parts = message.text.strip().split()
    if len(parts) < 2:
        safe_send(message.chat.id,
            "Usage: <code>/sell SYMBOL</code>  e.g. <code>/sell RELIANCE</code>")
        return
    sym = parts[1].upper().replace(".NS", "")
    if remove_from_portfolio(message.chat.id, sym):
        safe_send(message.chat.id, f"✅ Removed <b>{sym}</b> from portfolio.")
    else:
        safe_send(message.chat.id, f"❌ <b>{sym}</b> not found in portfolio.")


@bot.message_handler(commands=["portfolio"])
def cmd_portfolio_cmd(message):
    safe_send(message.chat.id, "⏳ Fetching live prices for your portfolio…")
    def _run():
        safe_send(message.chat.id, build_portfolio_card(message.chat.id))
    executor.submit(_run)


@bot.message_handler(commands=["clear"])
def cmd_clear(message):
    clear_chat(message.chat.id)
    safe_send(message.chat.id, "🗑️ AI chat history cleared.")


@bot.message_handler(func=lambda m: m.text == "🔙 Menu")
def back_to_main(message):
    _state[message.chat.id] = None
    safe_send(message.chat.id, "📋 Main Menu", reply_markup=main_keyboard())


@bot.message_handler(func=lambda m: m.text == "💼 Portfolio")
def portfolio_button(message):
    safe_send(message.chat.id, "⏳ Loading portfolio…")
    def _run():
        safe_send(message.chat.id, build_portfolio_card(message.chat.id))
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "📈 Status")
def status_button(message):
    cmd_status(message)


@bot.message_handler(func=lambda m: m.text == "🤖 AI")
def enter_ai_mode(message):
    _state[message.chat.id] = "ai"
    safe_send(
        message.chat.id,
        "🤖 <b>AI Mode</b>\nLive market data auto-injected. Ask anything or tap a topic.",
        reply_markup=ai_keyboard(),
    )


@bot.message_handler(func=lambda m: m.text in AI_CHAT_TOPIC_KEYS)
def ai_topic_button(message):
    uid  = message.chat.id
    text = AI_CHAT_TOPICS[message.text]
    safe_send(uid, "⏳ Thinking with live data…")
    def _run():
        resp = ai_chat_respond(uid, text)
        safe_send(uid, resp or "❌ Empty response.", reply_markup=ai_keyboard())
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text in ["🏦 Conservative", "⚖️ Moderate", "🚀 Aggressive"])
def scan_button(message):
    profile_map = {
        "🏦 Conservative": "conservative",
        "⚖️ Moderate":     "moderate",
        "🚀 Aggressive":   "aggressive",
    }
    profile = profile_map[message.text]
    safe_send(message.chat.id, f"⏳ Scanning {message.text} stocks…")
    def _run():
        safe_send(message.chat.id, build_scan(profile))
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "📊 Breadth")
def breadth_button(message):
    safe_send(message.chat.id, build_breadth())


@bot.message_handler(func=lambda m: m.text in ["🎯 Swing (Safe)", "🚀 Swing (Agr)"])
def swing_button(message):
    mode  = "conservative" if "Safe" in message.text else "aggressive"
    label = "6/8 conditions" if mode == "conservative" else "5/8 conditions"
    safe_send(
        message.chat.id,
        f"⏳ Running swing scanner ({label})…\n"
        "Checks: EMA trend, ADX, RSI, MACD, Volume, Bollinger Bands (may take ~20s)"
    )
    def _run():
        try:
            result = get_swing_trades(mode=mode)
            # Chunk messages > 3800 chars (Telegram hard limit 4096)
            if len(result) <= 3800:
                safe_send(message.chat.id, result)
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
                    safe_send(message.chat.id, part)
        except Exception as e:
            logger.error(f"Swing scan: {e}")
            safe_send(message.chat.id, f"❌ Swing scan failed: {e}")
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "📰 News")
def news_button(message):
    safe_send(message.chat.id, get_market_news())


@bot.message_handler(func=lambda m: m.text == "🔍 Analysis")
def analysis_hint(message):
    safe_send(
        message.chat.id,
        "🔍 <b>Stock Analysis</b>\n\n"
        "Type any NSE symbol to get a full analysis card.\n"
        "Examples: <code>RELIANCE</code>  <code>TCS</code>  <code>HDFCBANK</code>"
    )


# ── Catch-all: symbol analysis or AI chat ─────────────────────────────────────
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    uid  = message.chat.id
    text = message.text.strip()
    # Copilot Fix #6: per-user rate limiting — abuse protection
    if not API_RATE_LIMITER.is_allowed(uid):
        rem = API_RATE_LIMITER.remaining(uid)
        safe_send(uid, f"⚠️ Too many requests. Please wait {RATE_LIMIT_WINDOW}s. (Limit: {RATE_LIMIT_MAX_CALLS}/min)")
        return

    if _state.get(uid) == "ai":
        safe_send(uid, "⏳ Thinking…")
        def _ai():
            resp = ai_chat_respond(uid, text)
            safe_send(uid, resp or "❌ AI unavailable.", reply_markup=ai_keyboard())
        executor.submit(_ai)
        return

    sym = text.upper().replace(".NS", "")
    if 2 <= len(sym) <= 15 and all(c.isalnum() or c == "&" for c in sym):
        safe_send(uid, f"🔍 Analyzing <b>{sym}</b>…")
        def _adv():
            safe_send(uid, build_adv(sym))
        executor.submit(_adv)


# ── Flask routes ───────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    from api_utils import LIVE_CACHE, FUND_CACHE, NEWS_CACHE, HIST_CACHE, CTX_CACHE
    return jsonify({
        "status":  "ok",
        "version": "5.3_copilot_fixed",
        "fixes":   [
            "askfuzz_real_api", "openai_indentation", "fundamentals_all_fields",
            "safe_send_html_fallback", "webhook_dedup_deque", "news_date_rolling",
            "limits_filename", "chat_history_trim",
        ],
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


@app.route("/cache_stats", methods=["GET"])
def route_cache_stats():
    """Copilot Fix #2/#10: Cache health endpoint for monitoring."""
    from api_utils import LIVE_CACHE, FUND_CACHE, NEWS_CACHE, HIST_CACHE, CTX_CACHE
    return jsonify({
        "live":  LIVE_CACHE.stats(),
        "fund":  FUND_CACHE.stats(),
        "news":  NEWS_CACHE.stats(),
        "hist":  HIST_CACHE.stats(),
        "ctx":   CTX_CACHE.stats(),
    })


@app.route("/rate_limit_status/<int:user_id>", methods=["GET"])
def route_rate_limit(user_id: int):
    """Copilot Fix #6: per-user rate limit status."""
    remaining = API_RATE_LIMITER.remaining(user_id)
    return jsonify({"user_id": user_id, "remaining_calls": remaining,
                    "window_sec": RATE_LIMIT_WINDOW, "max_calls": RATE_LIMIT_MAX_CALLS})


def process_update(update_json: str):
    try:
        update = telebot.types.Update.de_json(update_json)
        bot.process_new_updates([update])
    except Exception as e:
        logger.error(f"process_update: {e}")


@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    data = request.get_data().decode("utf-8")
    try:
        uid = json.loads(data).get("update_id")
        if uid is not None:
            with _lock:
                if uid in _processed_updates:
                    return "OK", 200
                _processed_updates.append(uid)   # deque auto-trims at maxlen=1000
    except Exception:
        pass
    executor.submit(process_update, data)
    return "OK", 200


if __name__ == "__main__":
    # Warm-up yfinance connection
    try:
        yf.Ticker("^NSEI").history(period="1d")
        logger.info("yfinance warm-up OK")
    except Exception:
        pass
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting bot on port {port} …")
    app.run(host="0.0.0.0", port=port)
