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
from config import RATE_LIMIT_WINDOW, RATE_LIMIT_MAX_CALLS
from market_news import get_market_news, get_stock_news
from collections import deque
from datetime import datetime, date
from flask import Flask, request, jsonify
import telebot
from telebot import types

from ai_engine import (
    ai_insights         as engine_ai_insights,
    ai_chat_respond,
    ai_topic_respond,        # Bug 3 Fix: topic calls bypass chat history
    ai_available,
    AI_CHAT_TOPICS,
    AI_CHAT_TOPIC_KEYS,
    add_to_chat,
    clear_chat,
    test_ai_providers,
    debug_ai_status,
)
from swing_trades import get_swing_trades
from chart_integration import get_chart_generator


# ── Smart Symbol Resolver ─────────────────────────────────────────────────────
# Allows users to type company names, partial names, or any NSE/BSE symbol
# instead of requiring the exact NSE ticker code.

# Build a local name→symbol map from nifty500_collector
try:
    from nifty500_collector import SECTOR_STOCKS as _SC
    _SYMBOL_MAP: dict = {}   # "RELIANCE INDUSTRIES" → "RELIANCE"
    _ALL_NSE_SYMS: list = []
    for _sec_syms in _SC.values():
        for _s in _sec_syms:
            _SYMBOL_MAP[_s.upper()] = _s          # exact symbol match
            _ALL_NSE_SYMS.append(_s)
except Exception:
    _SYMBOL_MAP = {}
    _ALL_NSE_SYMS = []


def resolve_symbol(query: str) -> tuple:
    """
    Resolves a user query (name/partial/ticker) to (nse_ticker, company_name).
    Returns (None, None) if not found.
    Strategy:
      1. Direct exact NSE ticker match (RELIANCE → RELIANCE.NS)
      2. Partial symbol match (REL → best match)
      3. yfinance search API for company name resolution
      4. Try appending .NS directly as last resort
    """
    import yfinance as yf
    q = query.upper().strip().replace(" ", "").replace(".NS", "").replace(".BO", "")
    q_raw = query.strip()

    # 1. Exact match in our known symbol map
    if q in _SYMBOL_MAP:
        sym = _SYMBOL_MAP[q]
        return f"{sym}.NS", sym

    # 2. Partial prefix match (e.g. "HDFC" → HDFCBANK)
    matches = [s for s in _ALL_NSE_SYMS if s.startswith(q)]
    if len(matches) == 1:
        return f"{matches[0]}.NS", matches[0]
    if len(matches) > 1:
        # Pick shortest (most exact)
        best = sorted(matches, key=len)[0]
        return f"{best}.NS", best

    # 3. yfinance search for company name
    try:
        results = yf.Search(q_raw, max_results=5).quotes
        for r in results:
            sym_raw = r.get("symbol", "")
            exch    = r.get("exchange", "")
            if sym_raw and exch in ("NSI", "BSE"):
                # Prefer .NS; convert .BO if needed
                if sym_raw.endswith(".NS"):
                    return sym_raw, r.get("longname") or r.get("shortname") or sym_raw
                elif sym_raw.endswith(".BO"):
                    nse_sym = sym_raw.replace(".BO", ".NS")
                    return nse_sym, r.get("longname") or r.get("shortname") or sym_raw
                else:
                    return f"{sym_raw}.NS", r.get("longname") or r.get("shortname") or sym_raw
    except Exception as _e:
        logger.debug(f"yfinance search failed for {q_raw}: {_e}")

    # 4. Last resort: try ticker directly
    try:
        _t = yf.Ticker(f"{q}.NS")
        _h = _t.history(period="2d", progress=False)
        if not _h.empty:
            _name = (_t.info or {}).get("longName") or q
            return f"{q}.NS", _name
    except Exception:
        pass

    return None, None

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
executor = ThreadPoolExecutor(max_workers=20)  # FIX: 5→20 — prevents queue starvation under load

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


# ── Portfolio — File-persisted so data survives bot restart ──────────────────
_portfolio: dict = {}
_PORT_FILE = "portfolio_data.json"


def _load_portfolio():
    """Load portfolio from disk on startup."""
    global _portfolio
    try:
        if os.path.exists(_PORT_FILE):
            with open(_PORT_FILE) as f:
                raw = json.load(f)
            # JSON keys are strings — convert uid keys to int
            _portfolio = {int(k): v for k, v in raw.items()}
            logger.info(f"Portfolio loaded: {len(_portfolio)} users")
    except Exception as e:
        logger.warning(f"Portfolio load failed: {e}")
        _portfolio = {}


def _save_portfolio():
    """Persist portfolio to disk."""
    try:
        with open(_PORT_FILE, "w") as f:
            json.dump(_portfolio, f, indent=2)
    except Exception as e:
        logger.warning(f"Portfolio save failed: {e}")


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
    _save_portfolio()


def remove_from_portfolio(uid: int, sym: str) -> bool:
    p = get_portfolio(uid)
    if sym in p:
        del p[sym]
        _save_portfolio()
        return True
    return False


# Load on import
_load_portfolio()


def build_portfolio_card(uid: int) -> str:
    p = get_portfolio(uid)
    if not p:
        return (
            "📂 <b>Your Portfolio is Empty</b>\n\n"
            "Add a position:\n<code>/buy RELIANCE 10 2500</code>\n\n"
            "Remove a position:\n<code>/sell RELIANCE</code>"
        )

    today_str  = date.today().strftime("%d-%b-%Y")
    total_inv  = 0.0
    total_cur  = 0.0
    rows       = []
    winners    = []
    losers     = []

    for sym, pos in p.items():
        qty, avg = pos["qty"], pos["avg"]
        try:
            ltp = get_live_price(sym) or avg
            ltp = round(float(ltp), 2)
        except Exception:
            ltp = avg
        inv    = qty * avg
        cur    = qty * ltp
        pnl    = round(cur - inv, 2)
        pct    = round((ltp - avg) / avg * 100, 2) if avg > 0 else 0.0
        weight = round(inv / 1, 2)   # will normalise after loop
        rows.append({"sym": sym, "qty": qty, "avg": avg, "ltp": ltp,
                     "inv": inv, "cur": cur, "pnl": pnl, "pct": pct})
        total_inv += inv
        total_cur += cur
        (winners if pnl >= 0 else losers).append((sym, pnl, pct))

    total_pnl  = round(total_cur - total_inv, 2)
    total_pct  = round((total_cur - total_inv) / total_inv * 100, 2) if total_inv else 0.0
    port_icon  = "🟢" if total_pnl >= 0 else "🔴"

    lines = [
        f"<b>━━━ 💼 PORTFOLIO REPORT ━━━</b>",
        f"📅 {today_str}  |  {len(rows)} holdings",
        f"",
        f"<b>── HOLDINGS ──</b>",
    ]

    # Sort by absolute P&L
    rows.sort(key=lambda x: x["pnl"], reverse=True)
    for r in rows:
        pnl_icon = "🟢" if r["pnl"] >= 0 else "🔴"
        wt       = round(r["inv"] / total_inv * 100, 1) if total_inv else 0
        lines += [
            f"{pnl_icon} <b>{r['sym']}</b>",
            f"   Qty    : {r['qty']} shares  ({wt}% of portfolio)",
            f"   Avg    : ₹{r['avg']:,.2f}  →  LTP: ₹{r['ltp']:,.2f}",
            f"   Invested: ₹{r['inv']:,.0f}  |  Current: ₹{r['cur']:,.0f}",
            f"   P&L    : ₹{r['pnl']:+,.2f}  ({r['pct']:+.2f}%)",
            f"   ···",
        ]

    lines += [
        f"",
        f"<b>── SUMMARY ──</b>",
        f"💰 Total Invested : ₹{total_inv:,.2f}",
        f"📈 Current Value  : ₹{total_cur:,.2f}",
        f"{port_icon} <b>Total P&L      : ₹{total_pnl:+,.2f}  ({total_pct:+.2f}%)</b>",
        f"",
    ]

    # Winners / Losers summary
    if winners:
        winners.sort(key=lambda x: x[1], reverse=True)
        lines.append(f"🏆 <b>Best Performer:</b> {winners[0][0]}  ₹{winners[0][1]:+,.0f}  ({winners[0][2]:+.1f}%)")
    if losers:
        losers.sort(key=lambda x: x[1])
        lines.append(f"⚠️ <b>Worst Performer:</b> {losers[0][0]}  ₹{losers[0][1]:+,.0f}  ({losers[0][2]:+.1f}%)")

    lines += [
        f"",
        f"{'─'*32}",
        f"➕ /buy SYM QTY PRICE   ➖ /sell SYM",
        f"⚠️ <i>Educational only. Not SEBI-registered advice.</i>",
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
def _get_tgt_line(trend: str, ltp: float, atr: float) -> str:
    """P0 Fix: direction-aware target/SL line — no chained ternary crash on NEUTRAL."""
    if trend == "BULLISH":
        return (f"🎯 Target: ₹{round(ltp+1.5*atr,2):,.2f} (+{round(1.5*atr/ltp*100,1)}%)"
                f"  |  SL: ₹{round(ltp-2*atr,2):,.2f} (-{round(2*atr/ltp*100,1)}%)")
    elif trend == "BEARISH":
        return (f"🎯 Target: ₹{round(ltp-1.5*atr,2):,.2f} (-{round(1.5*atr/ltp*100,1)}%)"
                f"  |  SL: ₹{round(ltp+2*atr,2):,.2f} (+{round(2*atr/ltp*100,1)}%)")
    else:
        return (f"🎯 R1: ₹{round(ltp+atr,2):,.2f}  |  S1: ₹{round(ltp-atr,2):,.2f}"
                f"  |  Range SL: ₹{round(ltp-2*atr,2):,.2f}")


def build_adv(sym: str) -> str:
    sym = sym.upper().replace(".NS", "")
    df  = get_hist(sym, "6mo")
    if df.empty:
        return f"❌ <b>{sym}</b> not found. Check the NSE symbol (e.g. RELIANCE, TCS)."

    close = df["Close"]
    ltp   = round(float(close.iloc[-1]), 2)
    prev  = float(close.iloc[-2]) if len(close) > 1 else ltp
    chg   = round((ltp - prev) / prev * 100, 2)
    rsi   = calc_rsi(close)
    macd, _macd_sig, _macd_hist = calc_macd(close)
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


# ── Screener ──────────────────────────────────────────────────────────────────
SCREENER_STOCKS = {
    "conservative": ["HDFCBANK", "TCS", "INFY", "ITC", "ONGC",
                     "SBIN", "WIPRO", "NTPC", "POWERGRID", "COALINDIA"],
    "moderate":     ["RELIANCE", "BHARTIARTL", "AXISBANK", "MARUTI", "LT",
                     "KOTAKBANK", "BAJFINANCE", "SUNPHARMA", "TITAN", "M&M"],
    "aggressive":   ["TATAMOTORS", "ADANIENT", "JSWSTEEL", "TATAPOWER",
                     "ZOMATO", "IRFC", "HAL", "BEL", "PFC", "ADANIPORTS"],
}

SCREENER_CRITERIA = {
    "conservative": "Low beta, dividend payers, large-cap — good for wealth preservation",
    "moderate":     "Growth + stability mix, mid-large cap — balanced risk/reward",
    "aggressive":   "High momentum, sector themes, high beta — for risk-tolerant traders",
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

    # FIX: Parallel fetch — was sequential (10 stocks × ~2s each = ~20s total).
    # Now all 10 fetched simultaneously → ~2-3s total.
    from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _ac

    def _fetch_one(sym):
        df = get_hist(sym, "6mo")
        if df.empty or len(df) < 28:
            return None
        close  = df["Close"]
        ltp    = round(float(close.iloc[-1]), 2)
        prev   = float(close.iloc[-2]) if len(close) > 1 else ltp
        chg    = round((ltp - prev) / prev * 100, 2)
        rsi    = calc_rsi(close)
        trend  = trend_label(close)
        signal = swing_signal(rsi, trend, chg)
        return {"sym": sym, "ltp": ltp, "chg": chg, "rsi": rsi, "trend": trend, "signal": signal}

    results = {}
    with _TPE(max_workers=10) as pool:
        futs = {pool.submit(_fetch_one, sym): sym for sym in syms}
        for fut in _ac(futs, timeout=15):
            sym = futs[fut]
            try:
                r = fut.result()
                if r:
                    results[sym] = r
            except Exception as e:
                logger.warning(f"Screener {sym}: {e}")

    hit = 0
    for sym in syms:
        r = results.get(sym)
        if not r:
            continue
        icon      = "🟢" if r["chg"] >= 0 else "🔴"
        rsi_badge = ("🔴OB" if r["rsi"] > 70 else ("🟢OS" if r["rsi"] < 30 else "🟡"))
        lines.append(
            f"{icon} <b>{sym}</b>  ₹{r['ltp']:,.2f} ({r['chg']:+.2f}%)\n"
            f"   RSI:{r['rsi']} {rsi_badge}  |  {r['trend']}  |  Signal: <b>{r['signal']}</b>"
        )
        hit += 1

    if hit == 0:
        lines.append("❌ Data unavailable. Try again in a moment.")
    criteria_note = SCREENER_CRITERIA.get(profile, "")
    if criteria_note:
        lines.append(f"\n📌 <i>{criteria_note}</i>")
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
            # Fix 5: fetch 1mo for valid RSI(14), use last 2 bars for day change
            d = yf.Ticker(ticker).history(period="1mo")
            if d is None or len(d) < 5:
                continue
            l   = round(float(d["Close"].iloc[-1]), 2)
            p   = round(float(d["Close"].iloc[-2]), 2)
            chg = round((l - p) / p * 100, 2) if p else 0.0
            wh  = round(float(d["High"].tail(5).max()), 2)
            wl  = round(float(d["Low"].tail(5).min()),  2)
            # RSI from full 1mo close — meaningful now
            try:
                from data_engine import calc_rsi as _crsi
                rsi_b = _crsi(d["Close"]) if len(d) >= 14 else 50.0
            except Exception:
                rsi_b = 50.0
            # EMA20 trend
            ema20_b = round(float(d["Close"].ewm(span=20,adjust=False).mean().iloc[-1]), 2)
            trend_b = "Bullish 🔼" if l > ema20_b else "Bearish 🔽"
            rsi_label_b = "OB" if rsi_b > 70 else ("OS" if rsi_b < 30 else "OK")
            icon = "🟢" if chg >= 0 else "🔴"
            lines.append(
                f"{icon} <b>{name}</b>: {l:,.2f} ({chg:+.2f}%)\n"
                f"   RSI:{rsi_b} [{rsi_label_b}] | {trend_b} | EMA20:{ema20_b:,.0f}\n"
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
    kb.add("📰 News", "📈 Chart", "📋 Status")
    return kb


def ai_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    topics = list(AI_CHAT_TOPICS.keys())
    # Add topics in pairs
    for i in range(0, len(topics)-1, 2):
        kb.add(topics[i], topics[i+1])
    if len(topics) % 2 == 1:
        kb.add(topics[-1])
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
        "👋 <b>AutoAI Advisory Bot v5.3</b>\n\n"
        "Type any stock name or NSE symbol for full analysis:\n"
        "<code>RELIANCE</code>  <code>TCS</code>  <code>HDFC Bank</code>  <code>Infosys</code>\n\n"
        "🔘 <b>Menu Buttons:</b>\n"
        "🔍 Analysis — Stock analysis by name or symbol\n"
        "📊 Breadth — Market indices overview\n"
        "🤖 AI — AI chat with live market data\n"
        "🏦⚖️🚀 Screeners — Conservative/Moderate/Aggressive\n"
        "🎯🚀 Swing — Trade setups (safe/aggressive)\n"
        "💼 Portfolio — Track your positions\n"
        "📰 News — Latest market news\n"
        "📈 Chart — Technical chart by name/symbol\n\n"
        "📌 <b>Commands:</b>\n"
        "/chart SYMBOL [period] — e.g. <code>/chart INFY 3mo</code>\n"
        "/buy SYM QTY PRICE | /sell SYM | /portfolio\n"
        "/status — AI provider health check\n"
        "/clear — Reset AI chat history\n"
        "/help — All commands",
        reply_markup=main_keyboard(),
    )


@bot.message_handler(commands=["help"])
def cmd_help(message):
    safe_send(
        message.chat.id,
        "📖 <b>COMMANDS</b>\n\n"
        "<b>Analysis:</b>\n"
        "  Type any NSE symbol — e.g. <code>TCS</code>, <code>INFY</code>\n\n"
        "<b>Chart:</b>\n"
        "  /chart SYMBOL — Technical chart (e.g. <code>/chart INFY</code>)\n"
        "  Tap 📈 Chart in menu — Auto-scans best crossover\n\n"
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
    safe_send(message.chat.id, "⏳ Checking AI providers…")
    def _run():
        results = test_ai_providers()
        status_icon = "✅" if results.get("_status","").startswith("✅") else "❌"
        ai_lines = []
        for provider in ["GROQ","Gemini","OpenAI","AskFuzz"]:
            v = results.get(provider,"SKIP")
            if v.startswith("OK"):   icon = "✅"
            elif v.startswith("SKIP"): icon = "⚪"
            else:                     icon = "❌"
            ai_lines.append(f"  {icon} {provider}: {v[:40]}")
        safe_send(
            message.chat.id,
            f"🤖 <b>BOT STATUS</b>\n"
            f"── ── ── ── ── ──\n"
            f"Bot : ✅ Running\n"
            f"AI  : {status_icon} {results.get('_status','Unknown')}\n"
            f"Time: {datetime.now().strftime('%d-%b-%Y %H:%M IST')}\n"
            f"── ── ── ── ── ──\n"
            f"<b>Provider Details:</b>\n" + "\n".join(ai_lines) +
            "\n── ── ── ── ── ──\n"
            "<i>⚪ = key not set  ✅ = working  ❌ = failed</i>",
            reply_markup=main_keyboard()   # Fix 5: restore keyboard after status
        )
    executor.submit(_run)


@bot.message_handler(commands=["chart"])
def cmd_chart(message):
    """Send a technical chart PNG for a given NSE symbol."""
    parts = message.text.strip().split()
    if len(parts) < 2:
        safe_send(
            message.chat.id,
            "📈 <b>Chart Usage:</b>\n"
            "<code>/chart SYMBOL</code>\n\n"
            "Examples:\n"
            "  <code>/chart INFY</code>\n"
            "  <code>/chart RELIANCE</code>\n"
            "  <code>/chart TCS</code>\n\n"
            "Or tap <b>📈 Chart</b> in the menu for the best auto-picked stock.",
        )
        return
    # Upgrade: smart resolver — supports "Infosys", "HDFC Bank", "INFY", partial names
    raw_query = " ".join(parts[1:])
    period = None
    if parts[-1] in {"1mo","3mo","6mo","1y","2y"}:
        period = parts[-1]
        raw_query = " ".join(parts[1:-1])
    safe_send(message.chat.id, f"🔍 Looking up <b>{raw_query}</b>…")
    def _run():
        ticker, company_name = resolve_symbol(raw_query)
        if not ticker:
            safe_send(message.chat.id,
                f"❌ Could not find <b>{raw_query}</b> on NSE/BSE.\n"
                "Try the NSE ticker: <code>/chart INFY</code>  <code>/chart RELIANCE</code>\n"
                "Or company name: <code>/chart Infosys</code>  <code>/chart HDFC Bank</code>")
            return
        sym = ticker.replace(".NS","").replace(".BO","")
        safe_send(message.chat.id, f"📈 Generating chart for <b>{company_name}</b> ({sym})… (~20s)")
        gen = get_chart_generator()
        args = [ticker, company_name]
        if period:
            args.append(period)
        success, meta_text, png_path = gen.generate(*args)
        if success and png_path:
            try:
                with open(png_path, "rb") as f:
                    bot.send_photo(message.chat.id, f,
                        caption=f"<b>📈 {company_name} ({sym})</b>\n\n{meta_text}",
                        parse_mode="HTML")
            except Exception as e:
                logger.error(f"Chart send failed: {e}")
                safe_send(message.chat.id, build_adv(sym))
        else:
            logger.warning(f"Chart failed for {sym}: {meta_text}")
            safe_send(message.chat.id, "⚠️ Chart unavailable, sending text analysis:")
            safe_send(message.chat.id, build_adv(sym))
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "📈 Chart")
def chart_button(message):
    """Auto-scan Nifty 200 for best crossover and send chart."""
    import time as _t
    safe_send(
        message.chat.id,
        "📈 Scanning Nifty 250 for best crossover setup…\n"
        "⏳ May take ~30s. Or type <code>/chart SYMBOL</code> for a specific stock.",
    )
    def _run():
        # Send a "still working" ping after 12s so user knows bot is alive
        def _ping():
            _t.sleep(12)
            try:
                safe_send(message.chat.id, "⏳ Still scanning… almost done.")
            except Exception:
                pass
        import threading as _th
        _th.Thread(target=_ping, daemon=True).start()
        gen = get_chart_generator()
        gen.send_to_telegram(bot, message.chat.id)
    executor.submit(_run)


@bot.message_handler(commands=["buy"])
def cmd_buy(message):
    parts = message.text.strip().split()
    if len(parts) != 4:
        safe_send(message.chat.id,
            "Usage: <code>/buy SYMBOL QUANTITY AVG_PRICE</code>\n"
            "Example: <code>/buy RELIANCE 10 2500</code>")
        return
    try:
        # P1 Fix 6: normalize symbol via resolve_symbol so "HDFC Bank" → "HDFCBANK"
        raw_sym = parts[1]
        qty     = int(parts[2])
        price   = float(parts[3])
    except ValueError:
        safe_send(message.chat.id, "❌ Invalid format. Example: /buy RELIANCE 10 2500")
        return
    if qty <= 0 or price <= 0:
        safe_send(message.chat.id, "❌ Quantity and price must be positive.")
        return
    # Normalize: try resolve then fallback to uppercase
    ticker, _ = resolve_symbol(raw_sym)
    sym = ticker.replace(".NS","").replace(".BO","") if ticker else raw_sym.upper().replace(".NS","")
    add_to_portfolio(message.chat.id, sym, qty, price)
    safe_send(message.chat.id,
        f"✅ Added <b>{qty} × {sym}</b> @ ₹{price:.2f} to portfolio.\n"
        f"View with /portfolio or tap 💼 Portfolio")


@bot.message_handler(commands=["sell"])
def cmd_sell(message):
    parts = message.text.strip().split()
    if len(parts) < 2:
        safe_send(message.chat.id,
            "Usage: <code>/sell SYMBOL</code>  e.g. <code>/sell RELIANCE</code>")
        return
    raw = " ".join(parts[1:]).upper().replace(".NS","").replace(".BO","")
    # Try resolve if not found directly
    p = get_portfolio(message.chat.id)
    if raw not in p:
        ticker, _ = resolve_symbol(raw)
        if ticker:
            raw = ticker.replace(".NS","").replace(".BO","").upper()
    if remove_from_portfolio(message.chat.id, raw):
        safe_send(message.chat.id, f"✅ Removed <b>{raw}</b> from portfolio.")
    else:
        safe_send(message.chat.id, f"❌ <b>{raw}</b> not found in portfolio.\n"
            f"Your holdings: {', '.join(p.keys()) if p else 'none'}")


@bot.message_handler(commands=["portfolio"])
def cmd_portfolio_cmd(message):
    safe_send(message.chat.id, "⏳ Fetching live prices for your portfolio…")
    def _run():
        safe_send(message.chat.id, build_portfolio_card(message.chat.id))
    executor.submit(_run)


@bot.message_handler(commands=["clear"])
def cmd_clear(message):
    clear_chat(message.chat.id)
    _state[message.chat.id] = None
    safe_send(message.chat.id,
        "🗑️ AI chat history cleared.\n"
        "You are now back in main mode.\n"
        "Tap <b>🤖 AI</b> to start a fresh AI conversation.",
        reply_markup=main_keyboard())


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


@bot.message_handler(func=lambda m: m.text in ["📈 Status", "📋 Status"])
def status_button(message):
    cmd_status(message)


@bot.message_handler(func=lambda m: m.text == "🤖 AI")
def enter_ai_mode(message):
    _state[message.chat.id] = "ai"
    safe_send(
        message.chat.id,
        "🤖 <b>AI Mode — Live Data Active</b>\n\n"
        "Ask anything about markets, stocks, options, or tap a topic below.\n\n"
        "Examples:\n"
        "  • <code>Reliance trade setup for 30 min</code>\n"
        "  • <code>INFY buy or sell?</code>\n"
        "  • <code>Nifty outlook for today</code>\n"
        "  • <code>Best sector to invest now</code>\n\n"
        "Tap <b>🔙 Menu</b> to return to main menu.",
        reply_markup=ai_keyboard(),
    )


@bot.message_handler(func=lambda m: m.text in AI_CHAT_TOPIC_KEYS)
def ai_topic_button(message):
    uid  = message.chat.id
    # Stock Analysis: ask user to type the symbol
    if message.text == "🔍 Stock Analysis":
        safe_send(uid,
            "🔍 <b>Stock Analysis</b>\n\n"
            "Type the stock name or symbol you want analyzed.\n"
            "Examples:\n"
            "  <code>Reliance trade setup for 30 min</code>\n"
            "  <code>INFY analysis</code>\n"
            "  <code>TCS buy or sell?</code>\n"
            "  <code>HDFC Bank levels</code>",
            reply_markup=ai_keyboard())
        _state[uid] = "ai"
        return
    # Bug 3 Fix: use ai_topic_respond — does NOT store in chat history
    # This keeps chat context clean and saves tokens
    topic_prompt = AI_CHAT_TOPICS[message.text]
    safe_send(uid, "⏳ Getting live data… (~8s)")
    try: bot.send_chat_action(uid, "typing")   # Siya: typing indicator
    except Exception: pass
    def _run():
        resp = ai_topic_respond(topic_prompt)
        safe_send(uid, resp or "⚠️ AI unavailable. Try again in a moment.", reply_markup=ai_keyboard())
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
    safe_send(message.chat.id, "⏳ Fetching market data…")
    def _run():
        safe_send(message.chat.id, build_breadth())
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text in ["🎯 Swing (Safe)", "🚀 Swing (Agr)"])
def swing_button(message):
    mode  = "conservative" if "Safe" in message.text else "aggressive"
    label = "6/8 conditions" if mode == "conservative" else "5/8 conditions"
    import time as _tsw
    safe_send(
        message.chat.id,
        f"⏳ Running swing scanner ({label})…\n"
        "Scanning 50 stocks: EMA, ADX, RSI, MACD, Volume, BB (may take ~25s)"
    )
    def _ping_swing(cid=message.chat.id):
        _tsw.sleep(15)
        try: safe_send(cid, "⏳ Still scanning… checking final stocks.")
        except Exception: pass
    import threading as _tsth
    _tsth.Thread(target=_ping_swing, daemon=True).start()
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
    safe_send(message.chat.id, "⏳ Fetching latest market news…")
    def _run():
        result = build_news()
        if not result or not result.strip():
            result = "📰 News unavailable right now. Try again in a moment."
        safe_send(message.chat.id, result)
    executor.submit(_run)


@bot.message_handler(func=lambda m: m.text == "🔍 Analysis")
def analysis_hint(message):
    _state[message.chat.id] = "analysis"
    safe_send(
        message.chat.id,
        "🔍 <b>Stock Analysis</b>\n\n"
        "Type any stock name or NSE symbol:\n"
        "• <code>RELIANCE</code> or <code>Reliance Industries</code>\n"
        "• <code>TCS</code> or <code>Tata Consultancy</code>\n"
        "• <code>HDFCBANK</code> or <code>HDFC Bank</code>\n\n"
        "Tap <b>🔙 Menu</b> to go back."
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
        safe_send(uid, "⏳ Thinking… (~8s)")
        try: bot.send_chat_action(uid, "typing")   # Siya: typing indicator
        except Exception: pass
        def _ai():
            resp = ai_chat_respond(uid, text)
            safe_send(uid, resp or "⚠️ AI unavailable. Try again in a moment.", reply_markup=ai_keyboard())
        executor.submit(_ai)
        return

    # Analysis mode — user tapped 🔍 Analysis button and is now typing a symbol
    if _state.get(uid) == "analysis":
        safe_send(uid, f"🔍 Looking up <b>{text}</b>…")
        def _analysis_run(q=text, u=uid):
            ticker, cname = resolve_symbol(q)
            if ticker:
                sym_clean = ticker.replace(".NS","").replace(".BO","")
                safe_send(u, f"📊 Analyzing <b>{cname}</b> ({sym_clean})…")
                safe_send(u, build_adv(sym_clean))
            else:
                sym_up = q.upper().replace(".NS","")
                if 2 <= len(sym_up) <= 15:
                    safe_send(u, build_adv(sym_up))
                else:
                    safe_send(u, f"❌ Could not find <b>{q}</b>. Try: <code>RELIANCE</code>  <code>TCS</code>",
                        reply_markup=main_keyboard())
            # Fix 4: reset state AFTER response sent, not before (prevents race condition)
            _state[u] = None
        executor.submit(_analysis_run)
        return

    # Upgrade: smart resolver — accept "Infosys", "HDFC Bank", "INFY", etc.
    raw = text.strip()
    raw_up = raw.upper().replace(".NS", "").replace(".BO", "")

    # Heuristic: looks like a ticker (short, alphanumeric) → try direct first
    _looks_ticker = 2 <= len(raw_up) <= 15 and all(c.isalnum() or c in "&-" for c in raw_up)
    # Looks like a company name (has spaces or is longer)
    _looks_name   = " " in raw or len(raw_up) > 12

    if _looks_ticker or _looks_name:
        safe_send(uid, f"🔍 Looking up <b>{raw}</b>…")
        def _adv(q=raw):
            ticker, cname = resolve_symbol(q)
            if ticker:
                sym_clean = ticker.replace(".NS","").replace(".BO","")
                safe_send(uid, f"📊 Analyzing <b>{cname}</b> ({sym_clean})…")
                safe_send(uid, build_adv(sym_clean))
            else:
                sym_clean = q.upper().replace(".NS","")
                if 2 <= len(sym_clean) <= 15:
                    safe_send(uid, build_adv(sym_clean))
                else:
                    safe_send(uid, f"❌ Could not find <b>{q}</b> on NSE/BSE.\n"
                        "Try: <code>RELIANCE</code>  <code>TCS</code>  <code>HDFC Bank</code>")
        executor.submit(_adv)
    else:
        # Fix 14: respond to greetings / unknown text instead of silent failure
        greetings = {"hi","hello","hey","hlo","hii","good morning","gm","good evening"}
        if raw.lower().strip("!.?") in greetings:
            safe_send(uid,
                "👋 Hello! I'm AutoAI Advisory.\n\n"
                "Type any stock name (e.g. <code>RELIANCE</code> or <code>HDFC Bank</code>) "
                "for a full analysis, or use the menu buttons below.",
                reply_markup=main_keyboard())
        elif any(kw in raw.lower() for kw in ["help","what can","how to","commands"]):
            cmd_help(message)
        else:
            safe_send(uid,
                "💡 Type a stock name or NSE symbol to analyze it.\n"
                "Example: <code>TCS</code>  <code>Infosys</code>  <code>RELIANCE</code>\n\n"
                "Or use the <b>menu buttons</b> below.",
                reply_markup=main_keyboard())


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


def _startup_warmup():
    """Pre-warm caches so first user never hits cold-path latency."""
    import time as _t
    _t.sleep(4)
    logger.info("[warmup] Starting cache pre-warm…")
    try:
        from ai_engine import get_live_market_context
        get_live_market_context(force=True)
        logger.info("[warmup] Market context ✅")
    except Exception as e:
        logger.warning(f"[warmup] ctx: {e}")
    for sym in ["RELIANCE", "TCS", "HDFCBANK", "INFY", "NIFTY50"]:
        try:
                            yf.download("^NSEI", period="5d", progress=False) if sym == "NIFTY50" else get_hist(sym, "6mo")
        except Exception:
            pass
    logger.info("[warmup] Cache pre-warm done ✅")


if __name__ == "__main__":
    import os as _os
    setup_logging(
        level=_os.getenv("LOG_LEVEL", "INFO"),
        structured=_os.getenv("STRUCTURED_LOGS", "").lower() == "true",
    )
    threading.Thread(target=_startup_warmup, daemon=True, name="warmup").start()
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting bot v5.3 on port {port}…")
    app.run(host="0.0.0.0", port=port)
