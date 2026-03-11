# main.py – AI Stock Advisor Pro | Fixed: live price, no double .NS, AI fallback, Render port
import os, time, logging, threading, re
from datetime import datetime, date
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf
import requests
import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from flask import Flask

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
TAVILY_API_KEY    = os.getenv("TAVILY_API_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
FINNHUB_API_KEY   = os.getenv("FINNHUB_API_KEY", "")
PORT              = int(os.getenv("PORT", 8080))

TIER_LIMITS       = {"free": 50, "paid": 200}
FRESHNESS_SECONDS = 3600

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")

# ─────────────────────────────────────────
# SYMBOL NORMALIZER  (fixes TCS.NS.NS bug)
# ─────────────────────────────────────────
def normalize_symbol(raw: str) -> str:
    sym = raw.strip().upper()
    for suffix in (".NS", ".NSE", ".BO", ".BSE"):
        if sym.endswith(suffix):
            sym = sym[: -len(suffix)]
    return sym

# ─────────────────────────────────────────
# LIVE PRICE  (multi-source, no cache)
# ─────────────────────────────────────────
def _price_yfinance(symbol: str) -> Optional[float]:
    try:
        t = yf.Ticker(f"{symbol}.NS")
        p = getattr(t.fast_info, "last_price", None)
        if p and float(p) > 0:
            return round(float(p), 2)
        info = t.info or {}
        p2 = info.get("regularMarketPrice") or info.get("currentPrice")
        if p2 and float(p2) > 0:
            return round(float(p2), 2)
    except Exception as e:
        logger.warning(f"YF live price error {symbol}: {e}")
    return None

def _price_alpha_vantage(symbol: str) -> Optional[float]:
    if not ALPHA_VANTAGE_KEY:
        return None
    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "GLOBAL_QUOTE", "symbol": f"{symbol}.NS",
                    "apikey": ALPHA_VANTAGE_KEY},
            timeout=8,
        )
        ps = r.json().get("Global Quote", {}).get("05. price")
        if ps and float(ps) > 0:
            return round(float(ps), 2)
    except Exception as e:
        logger.warning(f"AV price error {symbol}: {e}")
    return None

def _price_finnhub(symbol: str) -> Optional[float]:
    if not FINNHUB_API_KEY:
        return None
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": f"{symbol}.NS", "token": FINNHUB_API_KEY},
            timeout=8,
        )
        p = r.json().get("c")
        if p and float(p) > 0:
            return round(float(p), 2)
    except Exception as e:
        logger.warning(f"Finnhub price error {symbol}: {e}")
    return None

def get_live_price(symbol: str) -> Tuple[float, str]:
    """Returns (price, source). Tries fast_info → AV → Finnhub → EOD fallback."""
    p = _price_yfinance(symbol)
    if p:
        return p, "yfinance ✅"
    p = _price_alpha_vantage(symbol)
    if p:
        return p, "AlphaVantage ✅"
    p = _price_finnhub(symbol)
    if p:
        return p, "Finnhub ✅"
    try:
        df = yf.Ticker(f"{symbol}.NS").history(period="2d", auto_adjust=True)
        if not df.empty:
            return round(float(df["Close"].iloc[-1]), 2), "yfinance-EOD ⚠️"
    except:
        pass
    return 0.0, "unavailable"

# ─────────────────────────────────────────
# SAFE HISTORY  (no cache, fresh candles)
# ─────────────────────────────────────────
def safe_history(symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    try:
        logger.info(f"Fetching history: {symbol}.NS")
        df = yf.Ticker(f"{symbol}.NS").history(
            period=period, interval=interval,
            auto_adjust=True, actions=False, raise_errors=False,
        )
        if df is None or df.empty:
            logger.warning(f"{symbol}.NS: empty dataframe")
            return pd.DataFrame()
        df = df[df.index.normalize() <= pd.Timestamp(date.today())]
        return df.copy()
    except Exception as e:
        logger.warning(f"safe_history error {symbol}: {e}")
        return pd.DataFrame()

# ─────────────────────────────────────────
# USAGE TRACKING
# ─────────────────────────────────────────
usage_store: Dict[int, Dict] = {}

def get_today_str() -> str:
    return date.today().isoformat()

def can_use_llm(user_id: int) -> Tuple[bool, int, int]:
    record = usage_store.get(user_id)
    today  = get_today_str()
    if record is None:
        usage_store[user_id] = {"date": today, "calls": 0, "tier": "free"}
        lim = TIER_LIMITS["free"]
        return True, lim, lim
    if record["date"] != today:
        record["date"]  = today
        record["calls"] = 0
    lim  = TIER_LIMITS[record["tier"]]
    rem  = lim - record["calls"]
    return rem > 0, rem, lim

def register_llm_usage(user_id: int):
    r = usage_store.get(user_id)
    if r:
        r["calls"] += 1
    else:
        usage_store[user_id] = {"date": get_today_str(), "calls": 1, "tier": "free"}

# ─────────────────────────────────────────
# HISTORY TRACKING
# ─────────────────────────────────────────
history_store: Dict[int, List[Dict]] = defaultdict(list)

def add_history_item(uid: int, prompt: str, response: str, itype: str = "analysis") -> int:
    iid = int(time.time())
    history_store[uid].append({"id": iid, "timestamp": iid,
                                "prompt": prompt, "response": response, "type": itype})
    if len(history_store[uid]) > 20:
        history_store[uid] = history_store[uid][-20:]
    return iid

def get_recent_history(uid: int, limit: int = 10) -> List[Dict]:
    return history_store.get(uid, [])[-limit:][::-1]

def get_history_item(uid: int, iid: int) -> Optional[Dict]:
    for item in history_store.get(uid, []):
        if item["id"] == iid:
            return item
    return None

def is_history_fresh(item: Dict) -> bool:
    return (time.time() - item["timestamp"]) < FRESHNESS_SECONDS

# ─────────────────────────────────────────
# AI CLIENTS  (Groq → Gemini → rule-based)
# ─────────────────────────────────────────
def _groq_call(prompt: str, max_tokens: int) -> Optional[str]:
    if not GROQ_API_KEY:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        for model in ["llama-3.3-70b-versatile", "llama3-70b-8192",
                      "llama3-8b-8192", "gemma2-9b-it"]:
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content":
                         "You are a professional equity analyst for Indian NSE markets. "
                         "Give concise, data-driven commentary for retail swing traders. "
                         "Always end with: 'Note: Educational example, not a recommendation.'"},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens, temperature=0.35, timeout=15,
                )
                text = (resp.choices[0].message.content or "").strip()
                if text:
                    logger.info(f"Groq [{model}] OK")
                    return text
            except Exception as e:
                logger.warning(f"Groq {model} failed: {e}")
    except Exception as e:
        logger.error(f"Groq client error: {e}")
    return None

def _gemini_call(prompt: str, max_tokens: int) -> Optional[str]:
    if not GEMINI_API_KEY:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        for mname in ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]:
            try:
                model = genai.GenerativeModel(
                    model_name=mname,
                    generation_config={"max_output_tokens": max_tokens, "temperature": 0.35},
                )
                resp = model.generate_content(prompt)
                text = (getattr(resp, "text", "") or "").strip()
                if text:
                    logger.info(f"Gemini [{mname}] OK")
                    return text
            except Exception as e:
                logger.warning(f"Gemini {mname} failed: {e}")
    except Exception as e:
        logger.error(f"Gemini client error: {e}")
    return None

def actual_llm_call(prompt: str, max_tokens: int = 450) -> str:
    text = _groq_call(prompt, max_tokens)
    if text:
        return text
    text = _gemini_call(prompt, max_tokens)
    if text:
        return text
    return ""   # triggers rule-based fallback in caller

def call_llm_with_limits(uid: int, prompt: str, itype: str = "analysis") -> str:
    allowed, remaining, limit = can_use_llm(uid)
    if not allowed:
        return (f"❌ You've used all {limit} AI analyses today.\n"
                f"Try again tomorrow or upgrade to Pro (200 calls/day).")
    response = actual_llm_call(prompt)
    if not response:
        return "⚠️ AI service temporarily unavailable."
    register_llm_usage(uid)
    add_history_item(uid, prompt, response, itype)
    if remaining - 1 <= 3:
        response += f"\n\n⚠️ You have {remaining - 1} AI calls left today."
    return response

# ─────────────────────────────────────────
# TECHNICAL INDICATORS
# ─────────────────────────────────────────
def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    d  = s.diff()
    up = d.clip(lower=0).rolling(period).mean()
    dn = (-d.clip(upper=0)).rolling(period).mean()
    return 100 - (100 / (1 + up / dn))

def macd(s: pd.Series) -> Tuple[float, float]:
    ml = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    sl = ml.ewm(span=9, adjust=False).mean()
    return float(ml.iloc[-1]), float(sl.iloc[-1])

def bollinger_bands(s: pd.Series, period: int = 20) -> Tuple[float, float, float]:
    sma = s.rolling(period).mean().iloc[-1]
    std = s.rolling(period).std().iloc[-1]
    return round(sma + 2*std, 2), round(sma, 2), round(sma - 2*std, 2)

def atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])

def compute_key_levels(df: pd.DataFrame, ltp: float) -> dict:
    h, l, c = df["High"], df["Low"], df["Close"]
    lh, ll, lc = float(h.iloc[-1]), float(l.iloc[-1]), float(c.iloc[-1])
    pp = (lh + ll + lc) / 3
    r1 = 2*pp - ll;  r2 = pp + (lh - ll)
    s1 = 2*pp - lh;  s2 = pp - (lh - ll)
    r20  = float(h.rolling(20).max().iloc[-1])
    l20  = float(l.rolling(20).min().iloc[-1])
    e50  = float(c.ewm(span=50,  adjust=False).mean().iloc[-1])
    e200 = float(c.ewm(span=200, adjust=False).mean().iloc[-1])
    sup  = max([v for v in [s1, s2, l20, e50, e200] if v < ltp], default=round(ltp*0.97, 2))
    res  = min([v for v in [r1, r2, r20, e50, e200] if v > ltp], default=round(ltp*1.03, 2))
    return {
        "PP": round(pp, 2), "R1": round(r1, 2), "R2": round(r2, 2),
        "S1": round(s1, 2), "S2": round(s2, 2),
        "High_20D": round(r20, 2), "Low_20D": round(l20, 2),
        "Support": round(sup, 2), "Resistance": round(res, 2),
    }

def get_fundamental_info(symbol: str) -> dict:
    try:
        info = yf.Ticker(f"{symbol}.NS").info or {}
        return {
            "sector":         info.get("sector", "N/A"),
            "industry":       info.get("industry", "N/A"),
            "company_name":   info.get("longName", info.get("shortName", symbol)),
            "market_cap":     info.get("marketCap", 0) or 0,
            "pe_ratio":       info.get("trailingPE", 0) or 0,
            "pb_ratio":       info.get("priceToBook", 0) or 0,
            "roe":            (info.get("returnOnEquity", 0) or 0) * 100,
            "dividend_yield": (info.get("dividendYield", 0) or 0) * 100,
            "high_52w":       info.get("fiftyTwoWeekHigh", 0) or 0,
            "low_52w":        info.get("fiftyTwoWeekLow",  0) or 0,
            "prev_close":     info.get("regularMarketPreviousClose", 0) or 0,
            "volume":         info.get("volume", 0) or 0,
            "avg_volume":     info.get("averageVolume", 0) or 0,
        }
    except Exception as e:
        logger.error(f"Fundamental error {symbol}: {e}")
        return {}

def calculate_targets(price: float, av: float, trend: str,
                      low_52w: float = None, high_52w: float = None) -> dict:
    if trend == "Bullish":
        short = {"1W": price+av*1.2, "1M": price+av*3,  "3M": price+av*6}
        long_ = {"6M": price+av*12,  "1Y": price+av*20, "2Y": price+av*35}
        sl    = price - av * 2
        if high_52w:
            cap   = high_52w * 2
            long_ = {k: min(v, cap) for k, v in long_.items()}
    else:
        short = {"1W": price-av*1.2, "1M": price-av*3,  "3M": price-av*6}
        long_ = {"6M": price-av*10,  "1Y": price-av*15, "2Y": price-av*20}
        sl    = price + av * 2
        floor = price * 0.1
        short = {k: max(v, floor) for k, v in short.items()}
        long_ = {k: max(v, floor) for k, v in long_.items()}
        if low_52w:
            long_ = {k: max(v, low_52w * 0.9) for k, v in long_.items()}
    return {"short_term": {k: round(v, 2) for k, v in short.items()},
            "long_term":  {k: round(v, 2) for k, v in long_.items()},
            "stop_loss":  round(sl, 2)}

def calculate_quality_score(df: pd.DataFrame, fund: dict) -> int:
    close = df["Close"]; ltp = close.iloc[-1]; score = 0
    if ltp > ema(close, 20).iloc[-1]:  score += 4
    if ltp > ema(close, 50).iloc[-1]:  score += 5
    if ltp > ema(close, 200).iloc[-1]: score += 6
    rv = rsi(close, 14).iloc[-1]
    if 40 <= rv <= 60:  score += 10
    elif 30 <= rv <= 70: score += 5
    va = df["Volume"].rolling(20).mean().iloc[-1] or 1
    vl = df["Volume"].iloc[-1]
    score += 5 if vl > va*1.5 else (3 if vl > va else 0)
    ap = (atr(df) / ltp) * 100 if ltp else 0
    score += 10 if ap<2 else (7 if ap<4 else (4 if ap<6 else 0))
    if fund:
        pe  = fund.get("pe_ratio", 0)
        score += 15 if pe and pe<20 else (10 if pe and pe<30 else (5 if pe and pe<40 else 0))
        roe = fund.get("roe", 0)
        score += 15 if roe>20 else (12 if roe>15 else (8 if roe>10 else (4 if roe>5 else 0)))
        pb  = fund.get("pb_ratio", 0)
        score += 10 if 1<pb<3 else (8 if pb<=1 and pb>0 else (5 if pb<5 and pb>0 else 0))
        div = fund.get("dividend_yield", 0)
        score += 10 if div>3 else (7 if div>2 else (4 if div>1 else 0))
        mc  = fund.get("market_cap", 0)
        score += 10 if mc>50000e7 else (7 if mc>10000e7 else (4 if mc>1000e7 else 0))
    return min(score, 100)

# ─────────────────────────────────────────
# RULE-BASED COMMENTARY FALLBACK
# ─────────────────────────────────────────
def rule_based_commentary(sym, company, ltp, prev, rv, mv, sv,
                           e20, e50, e200, bu, bm, bl,
                           av, trend, quality, levels, targets) -> str:
    direction   = "up" if ltp > prev else "down"
    rsi_note    = "overbought" if rv > 70 else "oversold" if rv < 30 else "neutral"
    macd_note   = "bullish crossover" if mv > sv else "bearish crossover"
    risk_factor = "High" if (av/ltp*100) > 5 else ("Medium" if (av/ltp*100) > 3 else "Low")
    return (
        f"📌 Trend & Momentum:\n"
        f"{company} is trading {direction} vs prev close in a {trend.lower()} trend. "
        f"MACD shows a {macd_note} confirming momentum direction.\n\n"
        f"🎯 Key Levels:\n"
        f"• Support:    ₹{levels['Support']:.2f}\n"
        f"• Resistance: ₹{levels['Resistance']:.2f}\n"
        f"• Pivot:      ₹{levels['PP']:.2f}\n\n"
        f"⚡ Entry Strategy:\n"
        f"Consider entry near ₹{levels['Support']:.2f}–₹{levels['Support']+av:.2f} with "
        f"volume confirmation. RSI at {rv:.1f} is in a {rsi_note} zone.\n\n"
        f"🛑 Risk Management:\n"
        f"• Stop Loss:   ₹{targets['stop_loss']:.2f}\n"
        f"• Risk Factor: {risk_factor} (ATR: ₹{av:.2f})\n\n"
        f"🔮 Outlook (7-14 days):\n"
        f"{'Bullish bias; watch for breakout above ₹'+str(levels['Resistance']) if trend=='Bullish' else 'Bearish bias; watch for breakdown below ₹'+str(levels['Support'])} "
        f"targeting ₹{targets['short_term']['1M']:.2f} in 1 month.\n\n"
        f"⚠️ Note: Educational example, not a recommendation."
    )

# ─────────────────────────────────────────
# STOCK ANALYSIS (MAIN FUNCTION)
# ─────────────────────────────────────────
def stock_ai_advisory(symbol: str, user_id: Optional[int] = None) -> str:
    sym = normalize_symbol(symbol)
    try:
        logger.info(f"Analyzing {sym}.NS ...")

        # 1. Live CMP (multi-source)
        ltp, price_source = get_live_price(sym)
        if ltp == 0.0:
            return f"❌ Could not fetch live price for <b>{sym}</b>. Try again."

        # 2. History for indicators
        df = safe_history(sym)
        if df.empty:
            return f"❌ No historical data for <b>{sym}</b>. Symbol may be delisted."
        if len(df) < 60:
            return f"❌ Insufficient history for {sym} (need ≥60 days)."

        close = df["Close"].copy()
        close.iloc[-1] = ltp   # inject live price into last candle
        prev = float(df["Close"].iloc[-2]) if len(df) > 1 else ltp

        # 3. Indicators
        fund    = get_fundamental_info(sym)
        company = fund.get("company_name", sym)
        e20     = float(ema(close, 20).iloc[-1])
        e50     = float(ema(close, 50).iloc[-1])
        e200    = float(ema(close, 200).iloc[-1])
        rv      = float(rsi(close, 14).iloc[-1])
        mv, sv  = macd(close)
        bu, bm, bl = bollinger_bands(close)
        av      = atr(df)
        levels  = compute_key_levels(df, ltp)
        trend   = "Bullish" if ltp > e200 else "Bearish"
        targets = calculate_targets(ltp, av, trend,
                                    low_52w=fund.get("low_52w"),
                                    high_52w=fund.get("high_52w"))
        quality = calculate_quality_score(df, fund)
        stars   = "⭐" * (quality // 20) + "☆" * (5 - quality // 20)

        # 4. AI Commentary with structured prompt
        prompt = (
            f"You are a SEBI-registered equity analyst for Indian NSE markets.\n"
            f"Analyze {company} ({sym}.NS) for a retail swing trader.\n"
            f"Use ONLY the exact data below. Do NOT invent or change any number.\n\n"
            f"── LIVE MARKET DATA ({date.today().strftime('%d-%b-%Y')}) ──\n"
            f"LTP: ₹{ltp:.2f} | Prev Close: ₹{prev:.2f} | Trend: {trend}\n"
            f"RSI(14): {rv:.1f} | MACD: {mv:.2f} vs Signal: {sv:.2f}\n"
            f"EMA20: {e20:.2f} | EMA50: {e50:.2f} | EMA200: {e200:.2f}\n"
            f"BB: Upper={bu:.2f} Mid={bm:.2f} Lower={bl:.2f} | ATR: {av:.2f}\n"
            f"Nearest Support: ₹{levels['Support']:.2f} | Resistance: ₹{levels['Resistance']:.2f}\n"
            f"Pivot: PP={levels['PP']:.2f} | R1={levels['R1']:.2f} | S1={levels['S1']:.2f}\n"
            f"20D High: ₹{levels['High_20D']:.2f} | 20D Low: ₹{levels['Low_20D']:.2f}\n"
            f"P/E: {fund.get('pe_ratio',0):.2f} | P/B: {fund.get('pb_ratio',0):.2f} "
            f"| ROE: {fund.get('roe',0):.1f}% | Div: {fund.get('dividend_yield',0):.2f}%\n"
            f"Quality Score: {quality}/100\n\n"
            f"── REPLY IN EXACTLY THIS FORMAT ──\n"
            f"📌 Trend & Momentum:\n<2 sentences>\n\n"
            f"🎯 Key Levels:\n"
            f"• Support:    ₹{levels['Support']:.2f}\n"
            f"• Resistance: ₹{levels['Resistance']:.2f}\n"
            f"• Pivot:      ₹{levels['PP']:.2f}\n\n"
            f"⚡ Entry Strategy:\n<1-2 sentences on entry zone and confirmation>\n\n"
            f"🛑 Risk Management:\n"
            f"• Stop Loss:   ₹{targets['stop_loss']:.2f}\n"
            f"• Risk Factor: <High/Medium/Low based on ATR and RSI>\n\n"
            f"🔮 Outlook (7-14 days):\n<1 sentence bullish or bearish view>\n\n"
            f"⚠️ Note: Educational example, not a recommendation."
        )
        ai_raw  = actual_llm_call(prompt, max_tokens=450)
        ai_comment = ai_raw if ai_raw else rule_based_commentary(
            sym, company, ltp, prev, rv, mv, sv,
            e20, e50, e200, bu, bm, bl, av, trend, quality, levels, targets
        )

        return (
            f"📊 <b>DEEP ANALYSIS: {sym}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏢 {company}\n"
            f"🏭 Sector: {fund.get('sector','N/A')} | {fund.get('industry','N/A')}\n"
            f"💰 LTP: ₹{ltp:.2f}  ✅ {price_source}\n"
            f"📉 Prev Close: ₹{prev:.2f}\n"
            f"📈 52W: ₹{fund.get('low_52w',0):.2f} – ₹{fund.get('high_52w',0):.2f}\n"
            f"📊 Vol: {fund.get('volume',0):,} | Avg: {fund.get('avg_volume',0):,}\n"
            f"📅 As of: {date.today().strftime('%d-%b-%Y')}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>FUNDAMENTALS</b>\n"
            f"🏦 MCap: ₹{fund.get('market_cap',0)/10_000_000:.1f} Cr\n"
            f"📈 P/E: {fund.get('pe_ratio',0):.2f} | P/B: {fund.get('pb_ratio',0):.2f}\n"
            f"📊 ROE: {fund.get('roe',0):.1f}% | Div Yield: {fund.get('dividend_yield',0):.2f}%\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>TECHNICALS</b>\n"
            f"RSI(14): {rv:.1f} | MACD: {mv:.2f} vs Sig: {sv:.2f}\n"
            f"BB: U{bu} | M{bm} | L{bl}\n"
            f"EMA20: {e20:.2f} | EMA50: {e50:.2f} | EMA200: {e200:.2f}\n"
            f"ATR(14): {av:.2f} | Trend: {trend}\n"
            f"🟢 Support: ₹{levels['Support']:.2f}   🔴 Resistance: ₹{levels['Resistance']:.2f}\n"
            f"Pivot PP: ₹{levels['PP']:.2f} | R1: ₹{levels['R1']:.2f} | S1: ₹{levels['S1']:.2f}\n"
            f"20D High: ₹{levels['High_20D']:.2f} | 20D Low: ₹{levels['Low_20D']:.2f}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>PRICE TARGETS</b>\n"
            f"Short (1W/1M/3M): ₹{targets['short_term']['1W']} / "
            f"₹{targets['short_term']['1M']} / ₹{targets['short_term']['3M']}\n"
            f"Long  (6M/1Y/2Y): ₹{targets['long_term']['6M']} / "
            f"₹{targets['long_term']['1Y']} / ₹{targets['long_term']['2Y']}\n"
            f"🛑 Stop Loss: ₹{targets['stop_loss']}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>QUALITY SCORE: {quality}/100</b> {stars}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 <b>AI COMMENTARY</b>\n"
            f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            f"{ai_comment}\n"
            f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
            f"⚠️ <i>Educational purpose only. Not SEBI investment advice.</i>"
        )
    except Exception as e:
        logger.exception(f"Error in stock_ai_advisory for {symbol}")
        return f"❌ Analysis failed for {symbol}: {e}"

# ─────────────────────────────────────────
# MARKET BREADTH
# ─────────────────────────────────────────
NIFTY50 = [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","ITC","KOTAKBANK",
    "SBIN","BHARTIARTL","LT","WIPRO","HCLTECH","ASIANPAINT","MARUTI","TATAMOTORS",
    "TITAN","SUNPHARMA","ONGC","NTPC","M&M","POWERGRID","ULTRACEMCO","BAJFINANCE",
    "BAJAJFINSV","TATACONSUM","HDFCLIFE","SBILIFE","BRITANNIA","INDUSINDBK","CIPLA",
    "DRREDDY","DIVISLAB","GRASIM","HINDALCO","JSWSTEEL","TECHM","BPCL","IOC",
    "HEROMOTOCO","EICHERMOT","COALINDIA","SHREECEM","UPL","ADANIPORTS","AXISBANK",
    "BAJAJ-AUTO","NESTLE","TATASTEEL",
]

def get_advance_decline():
    adv = dec = unc = 0
    sp  = defaultdict(lambda: {"adv": 0, "dec": 0, "total": 0})
    for sym in NIFTY50:
        try:
            t    = yf.Ticker(f"{sym}.NS")
            hist = t.history(period="2d")
            if len(hist) < 2:
                continue
            chg = hist["Close"].iloc[-1] - hist["Close"].iloc[-2]
            if chg > 0:   adv += 1
            elif chg < 0: dec += 1
            else:         unc += 1
            sector = (t.info or {}).get("sector", "Other")
            if chg > 0:   sp[sector]["adv"]   += 1
            elif chg < 0: sp[sector]["dec"]   += 1
            sp[sector]["total"] += 1
        except:
            continue
    return adv, dec, unc, sp

def format_market_breadth() -> str:
    indices = {"NIFTY 50":"^NSEI","BANK NIFTY":"^NSEBANK",
               "NIFTY IT":"^CNXIT","NIFTY AUTO":"^CNXAUTO"}
    ind_data = {}
    for name, sym in indices.items():
        try:
            h = yf.Ticker(sym).history(period="2d")
            if not h.empty and len(h) >= 2:
                last = h["Close"].iloc[-1]; prev = h["Close"].iloc[-2]
                ind_data[name] = (last, ((last-prev)/prev)*100 if prev else 0)
            else:
                ind_data[name] = (0, 0)
        except:
            ind_data[name] = (0, 0)
    adv, dec, unc, sp = get_advance_decline()
    ts    = datetime.now().strftime("%d-%b-%Y %I:%M %p")
    lines = [f"📊 <b>Market Breadth (NSE)</b> – {ts}\n"]
    for name, (last, chg) in ind_data.items():
        arrow = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
        lines.append(f"{arrow} {name}: {last:,.2f} ({chg:+.2f}%)")
    lines.append(f"\n📈 Advances: {adv} | 📉 Declines: {dec} | ⚖️ Unchanged: {unc}")
    ratio = adv / dec if dec else adv
    lines.append(f"🔄 A/D Ratio: {ratio:.2f}\n\n🏭 <b>Sector Snapshot</b>")
    top5 = sorted(sp.items(), key=lambda x: x[1]["adv"]-x[1]["dec"], reverse=True)[:5]
    for sector, d in top5:
        net   = d["adv"] - d["dec"]
        arrow = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"
        lines.append(f"{arrow} {sector}: {d['adv']} up, {d['dec']} down")
    return "\n".join(lines)

# ─────────────────────────────────────────
# TAVILY NEWS
# ─────────────────────────────────────────
def get_tavily_news(query: str) -> list:
    if not TAVILY_API_KEY:
        return []
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_API_KEY, "query": query,
                  "search_depth": "basic", "max_results": 5},
            headers={"Content-Type": "application/json"}, timeout=10,
        )
        r.raise_for_status()
        return r.json().get("results", [])[:5]
    except Exception as e:
        logger.error(f"Tavily error: {e}")
        return []

def format_news(news_list: list, title: str) -> str:
    if not news_list:
        return f"📰 No recent news found for {title}."
    lines = [f"📰 <b>{title}</b>\n"]
    for i, item in enumerate(news_list, 1):
        h  = item.get("title", "No title")
        u  = item.get("url", "#")
        s  = item.get("source", "Unknown")
        dt = (item.get("published_date", "") or "")[:10]
        lines.append(f"{i}. <a href='{u}'>{h}</a>\n   📌 {s} | {dt}\n")
    return "\n".join(lines)

def get_market_news() -> str:
    return format_news(get_tavily_news("Indian stock market NSE BSE"), "Market News")

# ─────────────────────────────────────────
# PORTFOLIO SUGGESTION
# ─────────────────────────────────────────
CANDIDATES = [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","ITC","SBIN","BHARTIARTL",
    "KOTAKBANK","LT","WIPRO","HCLTECH","ASIANPAINT","MARUTI","TATAMOTORS",
    "TITAN","SUNPHARMA","ONGC",
]

def score_stock(symbol: str) -> Optional[dict]:
    try:
        t    = yf.Ticker(f"{symbol}.NS")
        info = t.info or {}
        hist = t.history(period="6mo")
        if hist.empty:
            return None
        close = hist["Close"]
        ltp   = float(close.iloc[-1])
        e200  = float(close.ewm(span=200).mean().iloc[-1])
        score = 5.0
        score += 1.5 if ltp > e200 else -1.0
        pe  = info.get("trailingPE", 25) or 25
        score += 1.5 if pe < 20 else (-1.0 if pe > 30 else 0)
        roe = (info.get("returnOnEquity", 0.1) or 0.1) * 100
        score += 1.5 if roe > 15 else (-1.0 if roe < 8 else 0)
        pb  = info.get("priceToBook", 2) or 2
        score += 0.5 if pb < 2 else (-0.5 if pb > 4 else 0)
        mc  = info.get("marketCap", 0) or 0
        score += 0.5 if mc > 50000e7 else (-0.5 if mc < 1000e7 else 0)
        div = info.get("dividendYield", 0) or 0
        score += 0.5 if div > 0.02 else 0
        score = max(0, min(10, score))
        rating = "Strong Buy" if score >= 8 else "Buy" if score >= 6 else "Hold" if score >= 4 else "Avoid"
        return {"symbol": symbol, "score": round(score, 1), "rating": rating,
                "mcap": mc, "sector": info.get("sector", "Other")}
    except Exception as e:
        logger.error(f"Score error {symbol}: {e}")
        return None

def suggest_portfolio(risk_profile: str = "moderate") -> list:
    scored = [d for sym in CANDIDATES if (d := score_stock(sym)) and d["score"] >= 4]
    scored.sort(key=lambda x: x["score"], reverse=True)
    if risk_profile == "conservative":
        filtered = [s for s in scored if s["mcap"] > 10000e7][:6]
    elif risk_profile == "aggressive":
        filtered = [s for s in scored if s["score"] >= 6][:8]
    else:
        filtered = [s for s in scored if s["score"] >= 5][:7]
    if not filtered:
        return []
    total = sum(s["score"] for s in filtered)
    for s in filtered:
        s["allocation"] = round((s["score"] / total) * 100, 1)
    return filtered

def format_portfolio(portfolio: list, risk_profile: str) -> str:
    if not portfolio:
        return "❌ No suitable stocks found for this risk profile."
    lines = [f"💼 <b>AI Portfolio ({risk_profile.capitalize()} Risk)</b>",
             "CFA-style scoring (technical + fundamental):\n"]
    for item in portfolio:
        lines.append(
            f"• {item['symbol']} – <b>{item['score']}/10</b> ({item['rating']})\n"
            f"  Allocation: {item['allocation']}% | {item.get('sector','N/A')}"
        )
    lines.append("\n⚠️ Educational purpose only. Consult your advisor.")
    return "\n".join(lines)

# ─────────────────────────────────────────
# SWING TRADES
# ─────────────────────────────────────────
try:
    from swing_trades import get_swing_trades
    SWING_AVAILABLE = True
except ImportError:
    SWING_AVAILABLE = False
    logger.warning("swing_trades.py not found.")

# ─────────────────────────────────────────
# TELEGRAM HANDLERS
# ─────────────────────────────────────────
@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🔍 Stock Analysis"), KeyboardButton("📊 Market Breadth"))
    kb.add(KeyboardButton("💼 Conservative"), KeyboardButton("💼 Moderate"), KeyboardButton("💼 Aggressive"))
    kb.add(KeyboardButton("📈 Swing (Conservative)"), KeyboardButton("📈 Swing (Aggressive)"))
    kb.add(KeyboardButton("📰 Market News"), KeyboardButton("📋 History"), KeyboardButton("📊 Usage"))
    bot.send_message(
        m.chat.id,
        "🤖 <b>AI Stock Advisor Pro</b>\n\n"
        "• 🔍 Stock Analysis – Live CMP + Tech + Fundamental + AI\n"
        "• 📊 Market Breadth – Nifty indices, A/D ratio, sectors\n"
        "• 💼 Portfolio – Conservative / Moderate / Aggressive\n"
        "• 📈 Swing Trades – Strict or flexible setups\n"
        "• 📰 Market News – Latest via Tavily\n"
        "• 📋 History – Reuse past queries (saves quota)\n"
        "• 📊 Usage – Daily AI call balance\n\n"
        "Select an option below 👇",
        reply_markup=kb,
    )

@bot.message_handler(func=lambda m: m.text == "🔍 Stock Analysis")
def ask_symbol(m):
    msg = bot.reply_to(m, "📝 Send NSE symbol (e.g. RELIANCE, TCS, SBIN, M&M):")
    bot.register_next_step_handler(msg, process_symbol)

def process_symbol(m):
    sym = normalize_symbol(m.text.strip())
    if not re.match(r"^[A-Z0-9\-\&\.]+$", sym):
        bot.reply_to(m, "❌ Invalid symbol. Use NSE code like RELIANCE or TCS.")
        return
    allowed, remaining, limit = can_use_llm(m.from_user.id)
    if not allowed:
        bot.reply_to(m, f"❌ You've used all {limit} analyses today. Try again tomorrow.")
        return
    bot.send_chat_action(m.chat.id, "typing")
    analysis = stock_ai_advisory(sym, user_id=m.from_user.id)
    register_llm_usage(m.from_user.id)
    add_history_item(m.from_user.id, f"Stock analysis: {sym}", analysis, "stock")
    if remaining - 1 <= 3:
        analysis += f"\n\n⚠️ {remaining - 1} AI calls left today."
    bot.reply_to(m, analysis, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📊 Market Breadth")
def market_breadth_cmd(m):
    bot.send_chat_action(m.chat.id, "typing")
    bot.reply_to(m, format_market_breadth(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text in ["💼 Conservative","💼 Moderate","💼 Aggressive"])
def portfolio_cmd(m):
    risk = m.text.split()[1].lower()
    bot.send_chat_action(m.chat.id, "typing")
    portfolio = suggest_portfolio(risk)
    text = format_portfolio(portfolio, risk)
    add_history_item(m.from_user.id, f"Portfolio suggestion ({risk})", text, "portfolio")
    bot.reply_to(m, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📈 Swing (Conservative)")
def swing_conservative(m):
    if not SWING_AVAILABLE:
        bot.reply_to(m, "⚠️ swing_trades.py not found."); return
    bot.send_chat_action(m.chat.id, "typing")
    bot.reply_to(m, get_swing_trades("conservative"))

@bot.message_handler(func=lambda m: m.text == "📈 Swing (Aggressive)")
def swing_aggressive(m):
    if not SWING_AVAILABLE:
        bot.reply_to(m, "⚠️ swing_trades.py not found."); return
    bot.send_chat_action(m.chat.id, "typing")
    bot.reply_to(m, get_swing_trades("aggressive"))

@bot.message_handler(func=lambda m: m.text == "📰 Market News")
def news_cmd(m):
    bot.send_chat_action(m.chat.id, "typing")
    bot.reply_to(m, get_market_news(), parse_mode="HTML", disable_web_page_preview=True)

@bot.message_handler(commands=["usage"])
@bot.message_handler(func=lambda m: m.text == "📊 Usage")
def usage_cmd(m):
    allowed, remaining, limit = can_use_llm(m.from_user.id)
    used = limit - remaining
    bot.reply_to(m, f"📊 AI Calls Used: {used}/{limit}\nRemaining today: {remaining}")

@bot.message_handler(commands=["history"])
@bot.message_handler(func=lambda m: m.text == "📋 History")
def show_history(m):
    items = get_recent_history(m.from_user.id, limit=5)
    if not items:
        bot.reply_to(m, "No recent history."); return
    markup = InlineKeyboardMarkup()
    for item in items:
        preview = item["prompt"][:32] + ("…" if len(item["prompt"]) > 32 else "")
        markup.add(InlineKeyboardButton(preview, callback_data=f"hist_{item['id']}"))
    bot.send_message(m.chat.id, "📋 Recent queries:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("hist_"))
def history_callback(call):
    uid, iid = call.from_user.id, int(call.data.split("_")[1])
    item = get_history_item(uid, iid)
    if not item:
        bot.answer_callback_query(call.id, "Item not found."); return
    if is_history_fresh(item):
        bot.send_message(uid, f"📎 [CACHED]\n\n{item['response']}\n\n_Saved your quota!_")
        bot.answer_callback_query(call.id)
    else:
        bot.answer_callback_query(call.id, "Fetching fresh data...")
        if item["type"] == "stock":
            sym  = normalize_symbol(item["prompt"].replace("Stock analysis:", "").strip())
            resp = stock_ai_advisory(sym, user_id=uid)
            register_llm_usage(uid)
            add_history_item(uid, item["prompt"], resp, "stock")
        elif item["type"] == "portfolio":
            risk = item["prompt"].replace("Portfolio suggestion (","").replace(")","").strip()
            resp = format_portfolio(suggest_portfolio(risk), risk)
            add_history_item(uid, item["prompt"], resp, "portfolio")
        else:
            resp = call_llm_with_limits(uid, item["prompt"], item["type"])
        bot.send_message(uid, resp, parse_mode="HTML")

# ─────────────────────────────────────────
# FLASK HEALTH SERVER  (Render port fix)
# ─────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.get("/")
def index():
    return "AI Stock Advisor Bot is running ✅", 200

@flask_app.get("/health")
def health():
    return {"status": "healthy", "time": datetime.now().isoformat()}, 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting AI Stock Advisor Pro on port {PORT} ...")
    bot.remove_webhook()
    time.sleep(1)
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Flask health server started")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=20)
        except Exception as e:
            logger.error(f"Polling crashed: {e}. Restarting in 5s...")
            time.sleep(5)
