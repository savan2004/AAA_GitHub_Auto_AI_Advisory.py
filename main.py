# main.py
import os
import time
import logging
import threading
from datetime import datetime, date
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf
import requests
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from groq import Groq
import google.generativeai as genai
from flask import Flask

# Import swing trade module
from swing_trades import get_swing_trades

# -------------------- CONFIGURATION --------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
PORT = int(os.getenv("PORT", 8080))
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")

# Tier limits
TIER_LIMITS = {
    "free": 50,
    "paid": 200,
}
FRESHNESS_SECONDS = 3600  # 1 hour

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")

# Configure AI clients (with error handling)
ai_configured = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        ai_configured = True
        logger.info("Gemini configured successfully")
    except Exception as e:
        logger.error(f"Failed to configure Gemini: {e}")

# -------------------- USAGE TRACKING (in-memory) --------------------
usage_store: Dict[int, Dict] = {}  # user_id -> {"date": str, "calls": int, "tier": str}

def get_today_str() -> str:
    return date.today().isoformat()

def can_use_llm(user_id: int) -> Tuple[bool, int, int]:
    record = usage_store.get(user_id)
    today = get_today_str()

    if record is None:
        tier = "free"
        limit = TIER_LIMITS[tier]
        usage_store[user_id] = {"date": today, "calls": 0, "tier": tier}
        return True, limit, limit

    if record["date"] != today:
        record["date"] = today
        record["calls"] = 0
        limit = TIER_LIMITS[record["tier"]]
        return True, limit, limit

    limit = TIER_LIMITS[record["tier"]]
    remaining = limit - record["calls"]
    allowed = remaining > 0
    return allowed, remaining, limit

def register_llm_usage(user_id: int) -> None:
    record = usage_store.get(user_id)
    if record:
        record["calls"] += 1
    else:
        usage_store[user_id] = {"date": get_today_str(), "calls": 1, "tier": "free"}

# -------------------- HISTORY TRACKING (in-memory) --------------------
history_store: Dict[int, List[Dict]] = defaultdict(list)

def add_history_item(user_id: int, prompt: str, response: str, item_type: str = "analysis") -> int:
    item_id = int(time.time())
    item = {
        "id": item_id,
        "timestamp": item_id,
        "prompt": prompt,
        "response": response,
        "type": item_type,
    }
    history_store[user_id].append(item)
    if len(history_store[user_id]) > 20:
        history_store[user_id] = history_store[user_id][-20:]
    return item_id

def get_recent_history(user_id: int, limit: int = 10) -> List[Dict]:
    items = history_store.get(user_id, [])
    return items[-limit:][::-1]

def get_history_item(user_id: int, item_id: int) -> Optional[Dict]:
    for item in history_store.get(user_id, []):
        if item["id"] == item_id:
            return item
    return None

def is_history_fresh(item: Dict, max_age: int = FRESHNESS_SECONDS) -> bool:
    now = time.time()
    age = now - item["timestamp"]
    return age < max_age

# -------------------- SAFE LLM CALL (NEVER CRASHES) --------------------
def safe_llm_call(prompt: str, max_tokens: int = 600) -> Tuple[bool, str]:
    """
    Returns (success: bool, response: str)
    Never raises exceptions - always returns a tuple.
    """
    # Try Groq first
    if GROQ_API_KEY:
        try:
            client = Groq(api_key=GROQ_API_KEY)
            # Try different models in order of reliability
            models_to_try = ["llama3-8b-8192", "mixtral-8x7b-32768", "gemma2-9b-it"]
            
            for model in models_to_try:
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                        temperature=0.35,
                        timeout=10  # Add timeout to prevent hanging
                    )
                    if resp and resp.choices:
                        return True, (resp.choices[0].message.content or "").strip()
                except Exception as e:
                    logger.warning(f"Groq model {model} failed: {e}")
                    continue
            
            # If all models fail
            logger.warning("All Groq models failed")
        except Exception as e:
            logger.error(f"Groq client error: {e}")

    # Try Gemini
    if GEMINI_API_KEY:
        try:
            # Try different model names
            gemini_models = [
                "models/gemini-1.5-pro",
                "models/gemini-1.5-flash",
                "gemini-1.5-pro",
                "gemini-1.5-flash"
            ]
            
            for model_name in gemini_models:
                try:
                    model = genai.GenerativeModel(model_name)
                    resp = model.generate_content(
                        prompt,
                        generation_config={
                            "max_output_tokens": max_tokens,
                            "temperature": 0.35,
                        }
                    )
                    if resp and resp.text:
                        return True, resp.text.strip()
                except Exception as e:
                    logger.warning(f"Gemini model {model_name} failed: {e}")
                    continue
        except Exception as e:
            logger.error(f"Gemini client error: {e}")

    # If all fail, return False with empty string
    return False, ""

def call_llm_with_limits(user_id: int, prompt: str, item_type: str = "analysis") -> str:
    allowed, remaining, limit = can_use_llm(user_id)

    if not allowed:
        return (
            f"❌ You've used all {limit} AI analyses for today.\n\n"
            f"Please try again tomorrow or upgrade to Pro (200 calls/day)."
        )

    # Call LLM safely
    success, response = safe_llm_call(prompt)

    if not success:
        # Don't register usage for failed calls
        if remaining - 1 <= 3:
            return (
                f"⚠️ AI service temporarily unavailable. Your quota was not used.\n\n"
                f"You still have {remaining} AI calls left today.\n\n"
                f"_Technical analysis is still provided below._"
            )
        else:
            return (
                f"⚠️ AI service temporarily unavailable. Your quota was not used.\n\n"
                f"_Technical analysis is still provided below._"
            )

    # Success - register usage and store history
    register_llm_usage(user_id)
    add_history_item(user_id, prompt, response, item_type)

    if remaining - 1 <= 3:
        response += f"\n\n⚠️ You have {remaining-1} AI calls left today."

    return response

# -------------------- TECHNICAL INDICATORS --------------------
def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).rolling(period).mean()
    down = (-d.clip(upper=0)).rolling(period).mean()
    rs = up / down
    return 100 - (100 / (1 + rs))

def macd(s: pd.Series) -> tuple:
    exp1 = s.ewm(span=12, adjust=False).mean()
    exp2 = s.ewm(span=26, adjust=False).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    return macd_line.iloc[-1], signal_line.iloc[-1]

def bollinger_bands(s: pd.Series, period: int = 20) -> tuple:
    sma = s.rolling(window=period).mean().iloc[-1]
    std = s.rolling(window=period).std().iloc[-1]
    upper = sma + (std * 2)
    lower = sma - (std * 2)
    return upper, sma, lower

def atr(df: pd.DataFrame, period: int = 14) -> float:
    high = df['High']
    low = df['Low']
    close = df['Close']
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean().iloc[-1]

def pivot_points(df: pd.DataFrame) -> dict:
    last = df.iloc[-1]
    high, low, close = last['High'], last['Low'], last['Close']
    pp = (high + low + close) / 3
    r1 = (2 * pp) - low
    r2 = pp + (high - low)
    s1 = (2 * pp) - high
    s2 = pp - (high - low)
    return {'PP': pp, 'R1': r1, 'R2': r2, 'S1': s1, 'S2': s2}

def get_fundamental_info(symbol: str) -> dict:
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.info
        return {
            'sector': info.get('sector', 'N/A'),
            'industry': info.get('industry', 'N/A'),
            'company_name': info.get('longName', info.get('shortName', symbol)),
            'market_cap': info.get('marketCap', 0),
            'pe_ratio': info.get('trailingPE', 0),
            'pb_ratio': info.get('priceToBook', 0),
            'roe': info.get('returnOnEquity', 0) * 100 if info.get('returnOnEquity') else 0,
            'dividend_yield': info.get('dividendYield', 0) * 100 if info.get('dividendYield') else 0,
            'high_52w': info.get('fiftyTwoWeekHigh', 0),
            'low_52w': info.get('fiftyTwoWeekLow', 0),
            'prev_close': info.get('regularMarketPreviousClose', 0),
            'volume': info.get('volume', 0),
            'avg_volume': info.get('averageVolume', 0)
        }
    except Exception as e:
        logger.error(f"Fundamental error for {symbol}: {e}")
        return {}

def calculate_targets(price: float, atr_val: float, trend: str,
                      low_52w: float = None, high_52w: float = None) -> dict:
    """Improved targets with safety caps."""
    if trend == "Bullish":
        short = {
            '1W': price + atr_val * 1.2,
            '1M': price + atr_val * 3,
            '3M': price + atr_val * 6
        }
        long = {
            '6M': price + atr_val * 12,
            '1Y': price + atr_val * 20,
            '2Y': price + atr_val * 35
        }
        sl = price - atr_val * 2
        if high_52w:
            cap = high_52w * 2
            for k in long:
                if long[k] > cap:
                    long[k] = cap
    else:
        short = {
            '1W': price - atr_val * 1.2,
            '1M': price - atr_val * 3,
            '3M': price - atr_val * 6
        }
        long = {
            '6M': price - atr_val * 10,
            '1Y': price - atr_val * 15,
            '2Y': price - atr_val * 20
        }
        sl = price + atr_val * 2
        floor = price * 0.1
        for k in short:
            short[k] = max(short[k], floor)
        for k in long:
            long[k] = max(long[k], floor)
        if low_52w:
            for k in long:
                if long[k] < low_52w * 0.9:
                    long[k] = low_52w * 0.9
    return {'short_term': short, 'long_term': long, 'stop_loss': sl}

def calculate_quality_score(df: pd.DataFrame, fund: dict) -> int:
    close = df['Close']
    score = 0
    # Trend (15)
    ema20 = ema(close,20).iloc[-1]
    ema50 = ema(close,50).iloc[-1]
    ema200 = ema(close,200).iloc[-1]
    if close.iloc[-1] > ema20: score += 4
    if close.iloc[-1] > ema50: score += 5
    if close.iloc[-1] > ema200: score += 6
    # RSI (10)
    rsi_val = rsi(close,14).iloc[-1]
    if 40 <= rsi_val <= 60: score += 10
    elif 30 <= rsi_val <= 70: score += 5
    # Volume (5)
    vol_avg = df['Volume'].rolling(20).mean().iloc[-1]
    if df['Volume'].iloc[-1] > vol_avg*1.5: score += 5
    elif df['Volume'].iloc[-1] > vol_avg: score += 3
    # ATR stability (10)
    atr_val = atr(df)
    atr_pct = (atr_val / close.iloc[-1]) * 100
    if atr_pct < 2: score += 10
    elif atr_pct < 4: score += 7
    elif atr_pct < 6: score += 4
    # Fundamentals (60)
    if fund:
        pe = fund.get('pe_ratio',0)
        if pe and pe < 20: score += 15
        elif pe and pe < 30: score += 10
        elif pe and pe < 40: score += 5
        roe = fund.get('roe',0)
        if roe > 20: score += 15
        elif roe > 15: score += 12
        elif roe > 10: score += 8
        elif roe > 5: score += 4
        pb = fund.get('pb_ratio',0)
        if 1 < pb < 3: score += 10
        elif pb <= 1: score += 8
        elif pb < 5: score += 5
        div = fund.get('dividend_yield',0)
        if div > 3: score += 10
        elif div > 2: score += 7
        elif div > 1: score += 4
        mcap = fund.get('market_cap',0)
        if mcap > 50000e7: score += 10
        elif mcap > 10000e7: score += 7
        elif mcap > 1000e7: score += 4
    return min(score, 100)

# -------------------- STOCK ANALYSIS (NEVER FAILS) --------------------
def stock_ai_advisory(symbol: str) -> str:
    sym = symbol.upper().strip()
    try:
        logger.info(f"Analyzing {sym}...")
        ticker = yf.Ticker(f"{sym}.NS")
        df = ticker.history(period="1y", interval="1d")
        if df.empty:
            return f"❌ No data found for {sym}. Please check the symbol and try again."
        
        close = df['Close']
        if len(close) < 60:
            return f"❌ Insufficient history for {sym}. Need at least 60 days of data."
        
        ltp = float(close.iloc[-1])
        prev = float(df['Close'].iloc[-2]) if len(df) > 1 else ltp
        fund = get_fundamental_info(sym)
        company = fund.get('company_name', sym)

        # Calculate all technical indicators
        ema20 = ema(close,20).iloc[-1]
        ema50 = ema(close,50).iloc[-1]
        ema200 = ema(close,200).iloc[-1]
        rsi_val = rsi(close,14).iloc[-1]
        macd_val, sig_val = macd(close)
        bb_up, bb_mid, bb_lo = bollinger_bands(close)
        atr_val = atr(df)
        piv = pivot_points(df)
        trend = "Bullish" if ltp > ema200 else "Bearish"
        targets = calculate_targets(ltp, atr_val, trend,
                                    low_52w=fund.get('low_52w'),
                                    high_52w=fund.get('high_52w'))
        quality = calculate_quality_score(df, fund)

        # Try to get AI commentary (but never fail if it doesn't work)
        ai_comment = ""
        success, ai_response = safe_llm_call(
            f"Provide a brief bullish/bearish sentiment analysis for {sym} (NSE) based on: "
            f"RSI {rsi_val:.1f}, trend {trend}, P/E {fund.get('pe_ratio',0):.1f}, ROE {fund.get('roe',0):.1f}%.",
            max_tokens=200
        )
        if success:
            ai_comment = f"\n\n🤖 AI COMMENTARY\n{ai_response}"

        # Build the response - always include technical analysis, optionally include AI
        output = f"""📊 DEEP ANALYSIS: {sym}
━━━━━━━━━━━━━━━━━━━━
🏢 {company}
🏭 Sector: {fund.get('sector','N/A')} | Industry: {fund.get('industry','N/A')}
💰 LTP: ₹{ltp:.2f} (Prev: ₹{prev:.2f})
📈 52W Range: ₹{fund.get('low_52w',0):.2f} - ₹{fund.get('high_52w',0):.2f}
📊 Volume: {fund.get('volume',0):,} | Avg: {fund.get('avg_volume',0):,}

━━━━━━━━━━━━━━━━━━━━
📊 FUNDAMENTALS
🏦 MCap: ₹{fund.get('market_cap',0)/10000000:.1f} Cr
📈 P/E: {fund.get('pe_ratio',0):.2f} | P/B: {fund.get('pb_ratio',0):.2f}
📊 ROE: {fund.get('roe',0):.1f}% | Div Yield: {fund.get('dividend_yield',0):.2f}%

━━━━━━━━━━━━━━━━━━━━
📌 TECHNICALS
RSI(14): {rsi_val:.1f} | MACD: {macd_val:.2f} vs Signal: {sig_val:.2f}
BB: U{bb_up:.2f} | M{bb_mid:.2f} | L{bb_lo:.2f}
EMA20: {ema20:.2f} | EMA50: {ema50:.2f} | EMA200: {ema200:.2f}
ATR(14): {atr_val:.2f} | Trend vs 200EMA: {trend}

━━━━━━━━━━━━━━━━━━━━
🎯 PRICE TARGETS
Short-term (1W/1M/3M): ₹{targets['short_term']['1W']:.2f} / ₹{targets['short_term']['1M']:.2f} / ₹{targets['short_term']['3M']:.2f}
Long-term (6M/1Y/2Y): ₹{targets['long_term']['6M']:.2f} / ₹{targets['long_term']['1Y']:.2f} / ₹{targets['long_term']['2Y']:.2f}
🛑 Stop Loss: ₹{targets['stop_loss']:.2f}

━━━━━━━━━━━━━━━━━━━━
📊 QUALITY SCORE: {quality}/100 {'⭐' * (quality//20)}{'☆' * (5 - quality//20)}{ai_comment}

━━━━━━━━━━━━━━━━━━━━
⚠️ Educational purpose only."""
        return output
        
    except Exception as e:
        logger.exception(f"Critical error in stock_ai_advisory for {symbol}")
        return f"❌ Unable to analyze {symbol} at this time. Please try again later.\n\nError: {str(e)}"

# -------------------- MARKET BREADTH --------------------
def get_nifty_constituents():
    return [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "ITC",
        "KOTAKBANK", "SBIN", "BHARTIARTL", "LT", "WIPRO", "HCLTECH", "ASIANPAINT",
        "MARUTI", "TATAMOTORS", "TITAN", "SUNPHARMA", "ONGC", "NTPC", "M&M",
        "POWERGRID", "ULTRACEMCO", "BAJFINANCE", "BAJAJFINSV", "TATACONSUM",
        "HDFCLIFE", "SBILIFE", "BRITANNIA", "INDUSINDBK", "CIPLA", "DRREDDY",
        "DIVISLAB", "GRASIM", "HINDALCO", "JSWSTEEL", "TECHM", "BPCL", "IOC",
        "HEROMOTOCO", "EICHERMOT", "COALINDIA", "SHREECEM", "UPL", "ADANIPORTS",
        "AXISBANK", "BAJAJ-AUTO", "NESTLE", "TATASTEEL"
    ]

def get_advance_decline():
    constituents = get_nifty_constituents()
    advances = declines = unchanged = 0
    sector_perf = defaultdict(lambda: {'adv':0, 'dec':0, 'total':0})
    for sym in constituents:
        try:
            ticker = yf.Ticker(f"{sym}.NS")
            hist = ticker.history(period="2d")
            if len(hist) < 2:
                continue
            prev_close = hist['Close'].iloc[-2]
            last_price = hist['Close'].iloc[-1]
            change = last_price - prev_close
            if change > 0:
                advances += 1
            elif change < 0:
                declines += 1
            else:
                unchanged += 1

            info = ticker.info
            sector = info.get('sector', 'Other')
            if change > 0:
                sector_perf[sector]['adv'] += 1
            elif change < 0:
                sector_perf[sector]['dec'] += 1
            sector_perf[sector]['total'] += 1
        except Exception as e:
            logger.error(f"Error processing {sym}: {e}")
            continue
    return advances, declines, unchanged, sector_perf

def format_market_breadth():
    indices = {
        "NIFTY 50": "^NSEI",
        "BANK NIFTY": "^NSEBANK",
        "NIFTY IT": "^CNXIT",
        "NIFTY AUTO": "^CNXAUTO"
    }
    ind_data = {}
    for name, sym in indices.items():
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="1d")
            if not hist.empty:
                last = hist['Close'].iloc[-1]
                prev = hist['Close'].iloc[-2] if len(hist) > 1 else last
                change = ((last - prev) / prev) * 100 if prev != 0 else 0
                ind_data[name] = (last, change)
            else:
                ind_data[name] = (0, 0)
        except:
            ind_data[name] = (0, 0)

    adv, dec, unc, sector_perf = get_advance_decline()
    timestamp = datetime.now().strftime("%d-%b-%Y %I:%M %p")

    text = f"📊 <b>Market Breadth (NSE)</b> – {timestamp}\n\n"
    for name, (last, chg) in ind_data.items():
        arrow = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
        text += f"{arrow} {name}: {last:,.2f} ({chg:+.2f}%)\n"

    text += f"\n📈 Advances: {adv}\n📉 Declines: {dec}\n⚖️ Unchanged: {unc}\n"
    if dec > 0:
        ratio = adv / dec
    else:
        ratio = adv
    text += f"🔄 A/D Ratio: {ratio:.2f} (out of {adv+dec+unc} stocks)\n\n"

    text += "🏭 <b>Sector Snapshot</b>\n"
    sorted_sectors = sorted(sector_perf.items(), key=lambda x: x[1]['adv']-x[1]['dec'], reverse=True)[:5]
    for sector, data in sorted_sectors:
        net = data['adv'] - data['dec']
        arrow = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"
        text += f"{arrow} {sector}: {data['adv']} up, {data['dec']} down\n"
    return text

# -------------------- TAVILY NEWS --------------------
def get_tavily_news(query: str, days: int = 7) -> list:
    if not TAVILY_API_KEY:
        return []
    try:
        url = "https://api.tavily.com/search"
        headers = {"Content-Type": "application/json"}
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": False,
            "include_raw_content": False
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])[:5]
    except Exception as e:
        logger.error(f"Tavily error: {e}")
        return []

def format_news(news_list: list, title: str) -> str:
    if not news_list:
        return f"📰 No recent news found for {title}."
    text = f"📰 <b>{title}</b>\n\n"
    for i, item in enumerate(news_list, 1):
        title = item.get("title", "No title")
        url = item.get("url", "#")
        source = item.get("source", "Unknown")
        date = item.get("published_date", "")[:10]
        text += f"{i}. <a href='{url}'>{title}</a>\n   📌 {source} | {date}\n\n"
    return text

def get_market_news() -> str:
    news = get_tavily_news("Indian stock market OR NSE OR BSE", days=3)
    return format_news(news, "Market News")

# -------------------- PORTFOLIO SUGGESTION --------------------
def score_stock(symbol: str) -> dict:
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.info
        hist = ticker.history(period="6mo")
        if hist.empty:
            return None
        close = hist['Close']
        latest = close.iloc[-1]
        ema200 = close.ewm(span=200).mean().iloc[-1]
        score = 5.0
        if latest > ema200:
            score += 1.5
        else:
            score -= 1.0
        pe = info.get('trailingPE', 25)
        if pe and pe < 20:
            score += 1.5
        elif pe and pe > 30:
            score -= 1.0
        roe = info.get('returnOnEquity', 0.1) * 100
        if roe > 15:
            score += 1.5
        elif roe < 8:
            score -= 1.0
        pb = info.get('priceToBook', 2)
        if pb < 2:
            score += 0.5
        elif pb > 4:
            score -= 0.5
        mcap = info.get('marketCap', 0)
        if mcap > 50000e7:
            score += 0.5
        elif mcap < 1000e7:
            score -= 0.5
        div = info.get('dividendYield', 0)
        if div and div > 0.02:
            score += 0.5
        score = max(0, min(10, score))
        rating = "Strong Buy" if score >= 8 else "Buy" if score >= 6 else "Hold" if score >= 4 else "Avoid"
        return {
            "symbol": symbol,
            "score": round(score, 1),
            "rating": rating,
            "mcap": mcap,
            "sector": info.get('sector', 'Other')
        }
    except Exception as e:
        logger.error(f"Score error for {symbol}: {e}")
        return None

def suggest_portfolio(risk_profile: str = "moderate"):
    candidates = [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "ITC", "SBIN",
        "BHARTIARTL", "KOTAKBANK", "LT", "WIPRO", "HCLTECH", "ASIANPAINT",
        "MARUTI", "TATAMOTORS", "TITAN", "SUNPHARMA", "ONGC"
    ]
    scored = []
    for sym in candidates:
        data = score_stock(sym)
        if data and data["score"] >= 4:
            scored.append(data)
    scored.sort(key=lambda x: x["score"], reverse=True)

    if risk_profile == "conservative":
        filtered = [s for s in scored if s["mcap"] > 10000e7][:6]
    elif risk_profile == "aggressive":
        filtered = [s for s in scored if s["score"] >= 6][:8]
    else:
        filtered = [s for s in scored if s["score"] >= 5][:7]

    if not filtered:
        return []
    total_score = sum(s["score"] for s in filtered)
    for s in filtered:
        s["allocation"] = round((s["score"] / total_score) * 100, 1)
    return filtered

def format_portfolio(portfolio, risk_profile: str):
    if not portfolio:
        return "❌ No suitable stocks found for this risk profile."
    text = f"💼 <b>AI-Powered Portfolio ({risk_profile.capitalize()} Risk)</b>\n"
    text += "Based on CFA-style scoring (technical + fundamental):\n\n"
    for item in portfolio:
        text += f"• {item['symbol']} – <b>{item['score']}/10</b> ({item['rating']})\n"
        text += f"  Allocation: {item['allocation']}% | {item.get('sector','N/A')}\n"
    text += "\n⚠️ Educational purpose only. Consult your advisor."
    return text

# -------------------- TELEGRAM HANDLERS --------------------
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
        "• Stock Analysis: detailed tech+fundamental+AI\n"
        "• Market Breadth: Nifty indices, A/D ratio, sector snapshot\n"
        "• Portfolio: Choose risk profile (Conservative/Moderate/Aggressive)\n"
        "• Swing Trades: Conservative = strict 8/8, Aggressive = includes scores 6–7\n"
        "• Market News: latest headlines via Tavily\n"
        "• History: reuse previous queries (saves quota)\n"
        "• Usage: check daily AI call usage\n\n"
        "Select an option below:",
        reply_markup=kb
    )

@bot.message_handler(func=lambda m: m.text == "🔍 Stock Analysis")
def ask_symbol(m):
    msg = bot.reply_to(m, "📝 Send NSE symbol (e.g. RELIANCE, TCS):")
    bot.register_next_step_handler(msg, process_symbol)

def process_symbol(m):
    sym = m.text.strip().upper()
    if not sym.isalnum():
        bot.reply_to(m, "❌ Invalid symbol. Use letters only.")
        return
    
    bot.send_chat_action(m.chat.id, 'typing')
    
    # Check usage
    allowed, remaining, limit = can_use_llm(m.from_user.id)
    
    # Get analysis (always works, even if AI fails)
    analysis = stock_ai_advisory(sym)
    
    # If AI was used successfully, register usage
    # Note: The AI call inside stock_ai_advisory already handles its own registration
    # We're just tracking the overall analysis request
    if allowed and "AI service temporarily unavailable" not in analysis:
        register_llm_usage(m.from_user.id)
        add_history_item(m.from_user.id, f"Stock analysis: {sym}", analysis, "stock")
    
    if remaining - 1 <= 3 and allowed:
        analysis += f"\n\n⚠️ You have {remaining
