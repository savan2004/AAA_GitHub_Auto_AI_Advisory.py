import os
import time
import json
import logging
import threading
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd
import yfinance as yf
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from groq import Groq
import google.generativeai as genai
import requests
from cachetools import TTLCache
from flask import Flask, request

# -------------------- CONFIGURATION --------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
PORT = int(os.getenv("PORT", 8080))
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Cache and rate limiter
cache = TTLCache(maxsize=1000, ttl=300)
rate_limits = defaultdict(list)

# -------------------- HELPER FUNCTIONS --------------------
def check_rate_limit(user_id: int) -> bool:
    now = time.time()
    rate_limits[user_id] = [t for t in rate_limits[user_id] if now - t < 60]
    if len(rate_limits[user_id]) >= 10:
        return False
    rate_limits[user_id].append(now)
    return True

def safe_request(url, params=None, headers=None, timeout=10):
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Request failed: {url} - {e}")
        return None

# -------------------- TAVILY NEWS --------------------
def get_tavily_news(query: str, days: int = 7) -> list:
    if not TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY not set")
        return []
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
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        cutoff = datetime.now() - timedelta(days=days)
        filtered = []
        for r in results:
            pub_date = r.get("published_date")
            if pub_date:
                try:
                    pub_dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                    if pub_dt >= cutoff:
                        filtered.append(r)
                except:
                    filtered.append(r)
            else:
                filtered.append(r)
        return filtered[:5]
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

# -------------------- TECHNICAL INDICATORS (unchanged) --------------------
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

def calculate_targets(price: float, atr_val: float, trend: str) -> dict:
    if trend == "Bullish":
        short = {'1W': price + atr_val*1.2, '1M': price + atr_val*3, '3M': price + atr_val*6}
        long = {'6M': price + atr_val*12, '1Y': price + atr_val*20, '2Y': price + atr_val*35}
        sl = price - atr_val*2
    else:
        short = {'1W': price - atr_val*1.2, '1M': price - atr_val*3, '3M': price - atr_val*6}
        long = {'6M': price - atr_val*12, '1Y': price - atr_val*20, '2Y': price - atr_val*35}
        sl = price + atr_val*2
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

# -------------------- AI ADVISORY --------------------
def get_ai_analysis(symbol: str) -> str:
    cache_key = f"ai:{symbol}"
    if cache_key in cache:
        logger.info(f"Cache hit for {symbol}")
        return cache[cache_key]
    result = stock_ai_advisory(symbol)
    cache[cache_key] = result
    return result

def ai_call(prompt: str, max_tokens: int = 600) -> str:
    if GROQ_API_KEY:
        try:
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.35,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            logger.error(f"Groq error: {e}")
    if GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content(prompt)
            return (resp.text or "").strip()
        except Exception as e:
            logger.error(f"Gemini error: {e}")
    return "AI_UNAVAILABLE"

def generate_enhanced_sentiment(tech_data: dict, fund: dict, news: str) -> str:
    bullish, bearish = [], []
    rsi = tech_data['rsi']
    if rsi < 30:
        bullish.append(f"✓ RSI at {rsi:.1f} indicates oversold")
    elif rsi > 70:
        bearish.append(f"⚠️ RSI at {rsi:.1f} overbought")
    elif 40 <= rsi <= 60:
        bullish.append(f"✓ RSI at {rsi:.1f} in neutral zone")
    if tech_data['trend'] == "Bullish":
        bullish.append("✓ Price above 200 EMA (bullish trend)")
    else:
        bearish.append("⚠️ Price below 200 EMA (bearish trend)")
    if tech_data['macd'] > tech_data['signal']:
        bullish.append("✓ MACD above signal (bullish momentum)")
    else:
        bearish.append("⚠️ MACD below signal (bearish momentum)")
    pe = fund.get('pe_ratio',0)
    if pe and pe < 15:
        bullish.append(f"✓ P/E {pe:.1f} attractive")
    elif pe and pe > 30:
        bearish.append(f"⚠️ High P/E {pe:.1f}")
    roe = fund.get('roe',0)
    if roe > 15:
        bullish.append(f"✓ ROE {roe:.1f}% strong")
    elif roe < 8:
        bearish.append(f"⚠️ Low ROE {roe:.1f}%")
    div = fund.get('dividend_yield',0)
    if div > 2:
        bullish.append(f"✓ Dividend yield {div:.2f}%")
    # Simple news sentiment
    if news:
        if any(word in news.lower() for word in ['positive','growth','profit','up','rise']):
            bullish.append("✓ News sentiment appears positive")
        if any(word in news.lower() for word in ['negative','loss','fall','down','decline']):
            bearish.append("⚠️ News contains negative signals")
    sentiment = "BUY" if len(bullish) >= len(bearish)+2 else "HOLD" if len(bullish) >= len(bearish) else "AVOID"
    output = "🤖 AI SENTIMENT ANALYSIS\n━━━━━━━━━━━━━━━━━━━━\n"
    output += "\n".join(bullish[:3]) + "\n\n" + "\n".join(bearish[:3])
    output += f"\n\n📊 OVERALL: {sentiment}\n"
    return output

def stock_ai_advisory(symbol: str) -> str:
    sym = symbol.upper().strip()
    try:
        logger.info(f"Analyzing {sym}...")
        ticker = yf.Ticker(f"{sym}.NS")
        df = ticker.history(period="1y", interval="1d")
        if df.empty:
            return f"❌ No data for {sym}."
        close = df['Close']
        if len(close) < 60:
            return f"❌ Insufficient history for {sym}."
        ltp = float(close.iloc[-1])
        prev = float(df['Close'].iloc[-2]) if len(df) > 1 else ltp
        fund = get_fundamental_info(sym)
        company = fund.get('company_name', sym)
        # Technicals
        ema20 = ema(close,20).iloc[-1]
        ema50 = ema(close,50).iloc[-1]
        ema200 = ema(close,200).iloc[-1]
        rsi_val = rsi(close,14).iloc[-1]
        macd_val, sig_val = macd(close)
        bb_up, bb_mid, bb_lo = bollinger_bands(close)
        atr_val = atr(df)
        piv = pivot_points(df)
        trend = "Bullish" if ltp > ema200 else "Bearish"
        targets = calculate_targets(ltp, atr_val, trend)
        quality = calculate_quality_score(df, fund)
        # Fetch news from Tavily
        news_items = get_tavily_news(f"{company} {sym} stock", days=7)
        news_text = format_news(news_items, f"Recent News: {sym}")
        # AI sentiment
        tech_data = {'ltp':ltp, 'rsi':rsi_val, 'macd':macd_val, 'signal':sig_val, 'trend':trend}
        ai_sent = generate_enhanced_sentiment(tech_data, fund, news_text)
        # Format output
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
📊 QUALITY SCORE: {quality}/100 {'⭐' * (quality//20)}{'☆' * (5 - quality//20)}

━━━━━━━━━━━━━━━━━━━━
{news_text}

━━━━━━━━━━━━━━━━━━━━
{ai_sent}

━━━━━━━━━━━━━━━━━━━━
⚠️ Educational purpose only."""
        return output
    except Exception as e:
        logger.exception(f"Error in stock_ai_advisory for {symbol}")
        return f"❌ Analysis failed: {e}"

# -------------------- ENHANCED MARKET BREADTH --------------------
def get_nifty_constituents():
    """Fetch current Nifty 50 constituents using yfinance (hardcoded fallback)."""
    # This list is subject to change; consider updating periodically.
    # Source: https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050
    # For simplicity, we use a hardcoded list; in production, you could scrape or use an API.
    return [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "ITC",
        "KOTAKBANK", "SBIN", "BHARTIARTL", "LT", "WIPRO", "HCLTECH", "ASIANPAINT",
        "MARUTI", "TATAMOTORS", "TITAN", "SUNPHARMA", "ONGC", "NTPC", "M&M",
        "POWERGRID", "ULTRACEMCO", "BAJFINANCE", "BAJAJFINSV", "TATACONSUM",
        "HDFC", "HDFCLIFE", "SBILIFE", "BRITANNIA", "INDUSINDBK", "CIPLA",
        "DRREDDY", "DIVISLAB", "GRASIM", "HINDALCO", "JSWSTEEL", "TECHM",
        "BPCL", "IOC", "HEROMOTOCO", "EICHERMOT", "COALINDIA", "SHREECEM",
        "UPL", "ADANIPORTS", "AXISBANK", "BAJAJ-AUTO", "NESTLE", "TATASTEEL"
    ]

def get_advance_decline():
    """Compute advances and declines from Nifty 50 stocks."""
    constituents = get_nifty_constituents()
    advances = 0
    declines = 0
    unchanged = 0
    total = 0
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
            total += 1

            # Sector performance (optional)
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
    return advances, declines, unchanged, total, sector_perf

def get_fii_dii_flow():
    """Approximate FII/DII flow using Tavily news."""
    news = get_tavily_news("FII DII flow Indian market", days=1)
    if news:
        # Combine headlines to guess net flow
        text = " ".join([item.get('title','') for item in news])
        if "net buyers" in text.lower() or "buy" in text.lower():
            return "FIIs were net buyers"
        elif "net sellers" in text.lower() or "sell" in text.lower():
            return "FIIs were net sellers"
        else:
            return "FII/DII flows mixed"
    return "FII/DII data unavailable (use Tavily for latest)"

def format_market_breadth():
    indices = {
        "NIFTY 50": "^NSEI",
        "BANK NIFTY": "^NSEBANK",
        "NIFTY IT": "^CNXIT",
        "NIFTY AUTO": "^CNXAUTO"  # optional
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

    adv, dec, unc, total, sector_perf = get_advance_decline()
    fii_dii = get_fii_dii_flow()
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
    text += f"🔄 A/D Ratio: {ratio:.2f} (out of {total} stocks)\n\n"

    # Top sectors (optional)
    text += "🏭 <b>Sector Snapshot</b>\n"
    for sector, data in sorted(sector_perf.items(), key=lambda x: x[1]['adv']-x[1]['dec'], reverse=True)[:5]:
        net = data['adv'] - data['dec']
        arrow = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"
        text += f"{arrow} {sector}: {data['adv']} up, {data['dec']} down\n"

    text += f"\n💰 <b>FII/DII Flow (approx)</b>: {fii_dii}\n"
    return text

# -------------------- PORTFOLIO SUGGESTION (CFA-style) --------------------
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
    if not check_rate_limit(m.from_user.id):
        bot.reply_to(m, "⏳ Rate limit exceeded. Please wait.")
        return
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🔍 Stock Analysis"), KeyboardButton("📊 Market Breadth"))
    kb.add(KeyboardButton("💼 Conservative"), KeyboardButton("💼 Moderate"), KeyboardButton("💼 Aggressive"))
    kb.add(KeyboardButton("📰 Market News"))
    bot.send_message(
        m.chat.id,
        "🤖 <b>AI Stock Advisor Pro</b>\n\n"
        "• Stock Analysis: detailed tech+fundamental+AI with news\n"
        "• Market Breadth: Nifty indices, A/D ratio, sector snapshot, FII/DII flow (via news)\n"
        "• Portfolio: Choose risk profile (Conservative/Moderate/Aggressive)\n"
        "• Market News: latest headlines via Tavily\n\n"
        "Select an option below:",
        reply_markup=kb
    )

@bot.message_handler(func=lambda m: m.text == "🔍 Stock Analysis")
def ask_symbol(m):
    if not check_rate_limit(m.from_user.id):
        bot.reply_to(m, "⏳ Rate limit exceeded. Please wait.")
        return
    msg = bot.reply_to(m, "📝 Send NSE symbol (e.g. RELIANCE, TCS):")
    bot.register_next_step_handler(msg, process_symbol)

def process_symbol(m):
    sym = m.text.strip().upper()
    if not sym.isalnum():
        bot.reply_to(m, "❌ Invalid symbol. Use letters only.")
        return
    bot.send_chat_action(m.chat.id, 'typing')
    try:
        analysis = get_ai_analysis(sym)
        if len(analysis) > 4096:
            for x in range(0, len(analysis), 4096):
                bot.send_message(m.chat.id, analysis[x:x+4096])
        else:
            bot.reply_to(m, analysis)
    except Exception as e:
        logger.exception("Error in process_symbol")
        bot.reply_to(m, "❌ Analysis failed. Please try again later.")

@bot.message_handler(func=lambda m: m.text == "📊 Market Breadth")
def market_breadth_cmd(m):
    if not check_rate_limit(m.from_user.id):
        bot.reply_to(m, "⏳ Rate limit exceeded.")
        return
    bot.send_chat_action(m.chat.id, 'typing')
    text = format_market_breadth()
    bot.reply_to(m, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text in ["💼 Conservative", "💼 Moderate", "💼 Aggressive"])
def portfolio_cmd(m):
    if not check_rate_limit(m.from_user.id):
        bot.reply_to(m, "⏳ Rate limit exceeded.")
        return
    risk = m.text.split()[1].lower()
    bot.send_chat_action(m.chat.id, 'typing')
    portfolio = suggest_portfolio(risk)
    text = format_portfolio(portfolio, risk)
    bot.reply_to(m, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📰 Market News")
def news_cmd(m):
    if not check_rate_limit(m.from_user.id):
        bot.reply_to(m, "⏳ Rate limit exceeded.")
        return
    bot.send_chat_action(m.chat.id, 'typing')
    news = get_tavily_news("Indian stock market OR NSE OR BSE", days=3)
    text = format_news(news, "Market News")
    bot.reply_to(m, text, parse_mode="HTML", disable_web_page_preview=True)

# -------------------- FLASK HEALTH SERVER --------------------
app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return "Bot is running", 200

@app.route('/health', methods=['GET'])
def health():
    return {"status": "healthy", "time": datetime.now().isoformat()}, 200

def run_flask():
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# -------------------- MAIN --------------------
if __name__ == "__main__":
    logger.info("Starting AI Stock Advisor Pro (polling mode)")
    bot.remove_webhook()
    time.sleep(1)
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask health server on port {PORT}")
    while True:
        try:
            bot.infinity_polling()
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)
