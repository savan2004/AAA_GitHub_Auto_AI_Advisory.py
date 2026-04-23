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
    calc_rsi  as de_calc_rsi,
    calc_ema  as de_calc_ema,
)
from collections import deque
from datetime import datetime, date
from flask import Flask, request, jsonify
import telebot
from telebot import types

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
    A  = (H - Cp).abs()
    B  = (L - Cp).abs()
    CD = (H - L).abs()
    D  = (Cp - Op).abs()
    R  = pd.Series(0.0, index=df.index)
    cA = (A >= B) & (A >= CD)
    cB = (B >= A) & (B >= CD) & ~cA
    R[cA] = A[cA] + 0.5 * B[cA] + 0.25 * D[cA]
    R[cB] = B[cB] + 0.5 * A[cB] + 0.25 * D[cB]
    R[~(cA | cB)] = CD[~(cA | cB)] + 0.25 * D[~(cA | cB)]
    R  = R.replace(0, 1e-10)
    K  = pd.concat([A, B], axis=1).max(axis=1)
    lm = (Cp * 0.20).replace(0, 1e-10)
    SI = 50 * ((C - Cp) + 0.5 * (Cp - O) + 0.25 * (Cp - Op)) / R * (K / lm)
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
    info = get_info(sym) or {}
    name = info.get("name") or info.get("longName") or info.get("shortName") or sym

    pe     = safe_val(info, "pe", "trailingPE")
    fwd_pe = safe_val(info, "forwardPE")

    # ROE: data_engine returns decimal (0.185 = 18.5%) — Finnhub returns % (18.5)
    roe_raw = safe_val(info, "roe", "returnOnEquity")
    if roe_raw is not None:
        roe = round(roe_raw, 1) if abs(roe_raw) > 1 else round(roe_raw * 100, 1)
    else:
        roe = None

    eps  = safe_val(info, "eps", "trailingEps")
    mcap = info.get("market_cap") or info.get("marketCap")
    rev  = info.get("totalRevenue")
    de   = safe_val(info, "debtToEquity")

    # Dividend yield: data_engine returns decimal (0.025 = 2.5%)
    div_raw = safe_val(info, "dividend_yield", "dividendYield")
    if div_raw is not None:
        div_y = round(div_raw, 2) if div_raw > 1 else round(div_raw * 100, 2)
    else:
        div_y = None

    w52h = safe_val(info, "high52", "fiftyTwoWeekHigh")
    w52l = safe_val(info, "low52",  "fiftyTwoWeekLow")
    beta = safe_val(info, "beta")
    pb   = safe_val(info, "pb", "priceToBook")

    # Fundamentals fallback via fundamentals.py (Finnhub path)
    if pe is None or roe is None:
        try:
            from fundamentals import get_fundamentals
            fund = get_fundamentals(sym)
            if pe    is None: pe    = fund.get("pe")
            if roe   is None: roe   = fund.get("roe")   # already in % from fundamentals.py
            if eps   is None: eps   = fund.get("eps")
            if pb    is None: pb    = fund.get("pb")
            if beta  is None: beta  = fund.get("beta")
            if mcap  is None: mcap  = fund.get("mcap")
            if rev   is None: rev   = fund.get("rev")
            if de    is None: de    = fund.get("de")
            if div_y is None: div_y = fund.get("div_y")
            if w52h  is None: w52h  = fund.get("w52h")
            if w52l  is None: w52l  = fund.get("w52l")
            if name  == sym:  name  = fund.get("name", sym)
        except Exception as fe:
            logger.debug(f"fundamentals fallback {sym}: {fe}")

    # 52W from price history if still missing
    n = min(252, len(close))
    if w52h is None: w52h = round(float(close.rolling(n).max().iloc[-1]), 2)
    if w52l is None: w52l = round(float(close.rolling(n).min().iloc[-1]), 2)
    dist52 = round((ltp - w52h) / w52h * 100, 1) if w52h else None

    # ── News ─────────────────────────────────────────────────────────────────
    news_text = fetch_news(sym)

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
        frow("Revenue",      fmt_mcap(rev)),
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
            df = get_hist(sym, "3mo")
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
            # FIX: Smarter signal — RSI-primary, trend-confirmed
            if rsi < 35:
                signal = "⚡ OVERSOLD — bounce watch"
            elif rsi > 72:
                signal = "⚠️ OVERBOUGHT — pullback risk"
            elif ltp > ema20 > ema50 and rsi > 50 and chg > 0:
                signal = "✅ UPTREND — strong momentum"
            elif ltp < ema20 < ema50 and rsi < 50:
                signal = "🔻 DOWNTREND — avoid"
            elif ltp > ema20 and 45 < rsi < 65 and chg > 0:
                signal = "✅ BUY ZONE"
            else:
                signal = "⏳ WAIT — no clear signal"
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
    safe_send(message.chat.id, build_news())


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
    return jsonify({
        "status":  "ok",
        "version": "5.0_fixed",
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
