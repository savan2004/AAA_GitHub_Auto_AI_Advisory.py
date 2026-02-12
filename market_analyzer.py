import os
import re
import json
import time
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import telebot
from telebot import types
import yfinance as yf
import pandas as pd
from dotenv import load_dotenv

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

load_dotenv()

# =============== CONFIG ===============
TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
PORT = int(os.getenv("PORT", "10000"))

if not TOKEN:
    raise RuntimeError("❌ TELEGRAM_TOKEN missing in .env")

bot = telebot.TeleBot(TOKEN)
client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

print("✅ Bot initialized")

# =============== TECH FUNCTIONS ===============

def calc_rsi(series, period=14):
    """Calculate RSI"""
    if len(series) < period + 1:
        return 50.0
    try:
        delta = series.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        rs = avg_gain / (avg_loss.replace(0, 1e-9))
        return float(100 - (100 / (1 + rs)).iloc[-1])
    except:
        return 50.0

def calc_ema(series, period):
    """Calculate EMA"""
    try:
        return float(series.ewm(span=period).mean().iloc[-1])
    except:
        return float(series.iloc[-1])

def calc_ma(series, period):
    """Calculate Simple MA"""
    try:
        return float(series.rolling(period).mean().iloc[-1])
    except:
        return float(series.iloc[-1])

def calc_pivots(h, l, c):
    """Calculate Pivot Points"""
    pp = (h + l + c) / 3
    return pp, (2*pp)-l, (2*pp)-h, pp+(h-l), pp-(h-l), h+2*(pp-l), l-2*(h-pp)

def calc_vol(df):
    """Calculate Volatility"""
    if len(df) < 20:
        return 0.0
    try:
        return float(df["Close"].pct_change().rolling(20).std().iloc[-1] * 100)
    except:
        return 0.0

def calc_asi(ltp, ema50, ema200, rsi, pe, roe, upside, vol=None):
    """Calculate ASI Score (0-100)"""
    score = 0
    
    # TREND (0-30)
    if ltp > ema200:
        score += 30
    elif ltp > ema50:
        score += 15
    
    # MOMENTUM (0-20)
    if 45 <= rsi <= 60:
        score += 20
    elif (40 <= rsi < 45) or (60 < rsi <= 70):
        score += 10
    elif rsi > 70:
        score += 5
    
    # VALUATION (0-10)
    if pe and pe > 0:
        if pe < 15:
            score += 10
        elif 15 <= pe <= 25:
            score += 5
    
    # QUALITY (0-10)
    if roe and roe > 0:
        if roe >= 18:
            score += 10
        elif 12 <= roe < 18:
            score += 5
    
    # RISK-REWARD (0-10)
    if upside >= 10:
        score += 10
    elif 5 <= upside < 10:
        score += 5
    elif 2 <= upside < 5:
        score += 2
    
    # VOLATILITY (±5)
    if vol is not None:
        if vol > 5:
            score -= 5
        elif vol > 3.5:
            score -= 2
        elif vol < 1:
            score -= 3
    
    return max(0, min(score, 100))

def get_verdict(asi):
    """Get verdict from ASI score"""
    if asi >= 75:
        return "📈 STRONG BUY"
    elif asi >= 55:
        return "✅ BUY/HOLD"
    elif asi >= 35:
        return "⏸️ WAIT"
    else:
        return "🔻 AVOID"

def get_trend_signal(ltp, ema50, ema200, ma20, close_series):
    """Get trend signal (Daily/Weekly/Monthly)"""
    # Check if price above EMAs = bullish
    if ltp > ema200 and ltp > ema50 and ltp > ma20:
        return "🔵 DAILY BULLISH | Weekly BULLISH"
    elif ltp > ema200 and ltp > ema50:
        return "🟣 DAILY BULLISH | Weekly NEUTRAL"
    elif ltp > ema50:
        return "🟡 DAILY NEUTRAL | Weekly BEARISH"
    else:
        return "🔴 DAILY BEARISH | Weekly BEARISH"

def get_upside_type(upside):
    """Classify upside by timeframe"""
    if upside >= 15:
        return f"{upside}% (Long-term Swing 1-3 Months)"
    elif upside >= 10:
        return f"{upside}% (Medium-term Swing 2-4 Weeks)"
    elif upside >= 5:
        return f"{upside}% (Short-term 1-2 Weeks)"
    else:
        return f"{upside}% (Intraday/Very Short-term)"

def get_ai_comment(asi, rsi, upside, pe, roe):
    """Generate AI comment (2-3 lines)"""
    try:
        if client and OPENAI_KEY:
            prompt = f"Stock: ASI {asi}, RSI {rsi:.1f}, Upside {upside}%, PE {pe:.1f}, ROE {roe:.1f}%. Give 2 short trading tips."
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=80
            )
            return resp.choices[0].message.content.strip()
    except:
        pass
    
    # Fallback comments
    if asi >= 75:
        return "Strong uptrend with good momentum. Entry point at support zones. Target R2/R3."
    elif asi >= 55:
        return "Neutral to bullish setup. Wait for confirmation at MA20. Use 5% SL."
    elif asi >= 35:
        return "Mixed signals. Better opportunities elsewhere. Monitor for reversal."
    else:
        return "Downtrend intact. Avoid until trend reversal. Wait for ASI > 50."

def find_symbol(query):
    """Find NSE symbol from query"""
    try:
        if client and OPENAI_KEY:
            prompt = f"User: '{query}'. Return ONLY NSE symbol UPPERCASE (like RELIANCE, TCS)."
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            raw = resp.choices[0].message.content.strip().upper()
            return re.sub(r"\\.NS|[^A-Z]", "", raw)
    except:
        pass
    return query.upper().replace(" ", "")

def get_stock_report(sym):
    """Generate enhanced stock analysis report"""
    try:
        sym = sym.upper().strip()
        
        # Handle indices
        if sym in ["NIFTY", "NIFTY50"]:
            tsym = "^NSEI"
        elif sym == "BANKNIFTY":
            tsym = "^NSEBANK"
        else:
            tsym = f"{sym}.NS"
        
        # Fetch data
        stock = yf.Ticker(tsym)
        df = stock.history(period="1y")
        info = stock.info
        
        if df.empty:
            return f"❌ Symbol {sym} not found"
        
        # Extract values
        close = df["Close"]
        ltp = float(close.iloc[-1])
        pc = float(close.iloc[-2])
        hp = float(df["High"].iloc[-2])
        lp = float(df["Low"].iloc[-2])
        
        # 52 Week High/Low
        week52_high = float(df["High"].tail(252).max())
        week52_low = float(df["Low"].tail(252).min())
        
        cname = info.get("longName", sym)
        about = info.get("longBusinessSummary", "N/A")[:120]  # First 120 chars
        sector = info.get("sector", "N/A")
        industry = info.get("industry", "N/A")
        mcap = float(info.get("marketCap", 0) or 0)
        pe = float(info.get("trailingPE", 0) or 0)
        pb = float(info.get("priceToBook", 0) or 0)
        roe = float((info.get("returnOnEquity", 0) or 0) * 100)
        
        # Calculate technicals
        rsi = calc_rsi(close)
        ema50 = calc_ema(close, 50)
        ema200 = calc_ema(close, 200)
        ma20 = calc_ma(close, 20)
        ma50 = calc_ma(close, 50)
        ma200 = calc_ma(close, 200)
        vol = calc_vol(df)
        
        # Pivot points
        pp, r1, s1, r2, s2, r3, s3 = calc_pivots(hp, lp, pc)
        upside = round(((r2 - ltp) / ltp) * 100, 2)
        
        # ASI Score
        asi = calc_asi(ltp, ema50, ema200, rsi, pe, roe, upside, vol)
        verd = get_verdict(asi)
        conf = "High" if asi >= 75 else "Moderate" if asi >= 55 else "Low"
        trend_signal = get_trend_signal(ltp, ema50, ema200, ma20, close)
        upside_type = get_upside_type(upside)
        ai_comment = get_ai_comment(asi, rsi, upside, pe, roe)
        
        # IST Timestamp
        ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d-%b-%Y %H:%M IST")
        
        return (
            f"🚀 **SK AUTO AI ADVISORY**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {ist}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 **{sym}** | {cname}\n"
            f"💼 **About:** {about}...\n"
            f"🏛 **ASI:** {asi}/100 ({conf})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **LTP:** ₹{ltp:.2f} | 📊 **RSI:** {rsi:.2f}\n"
            f"📈 **TREND SIGNAL:** {trend_signal}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **VERDICT:** {verd}\n"
            f"🚀 **UPSIDE:** {upside_type} (Target: ₹{r2:.2f})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 **FUNDAMENTALS**\n"
            f"• Sector: {sector} | Industry: {industry}\n"
            f"• Market Cap: {round(mcap/1e7, 1)}Cr\n"
            f"• PE: {round(pe, 2)}x | PB: {round(pb, 2)}x | ROE: {round(roe, 1)}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **52 WEEK RANGE**\n"
            f"• High: ₹{week52_high:.2f} | Low: ₹{week52_low:.2f}\n"
            f"• Current: {round(((ltp - week52_low)/(week52_high - week52_low))*100, 1)}% of range\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏗 **MOVING AVERAGES**\n"
            f"• MA20: ₹{ma20:.2f} | MA50: ₹{ma50:.2f} | MA200: ₹{ma200:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **TECHNICAL ZONES**\n"
            f"• R3: ₹{r3:.2f} | R2: ₹{r2:.2f} | R1: ₹{r1:.2f}\n"
            f"• PP: ₹{pp:.2f} | S1: ₹{s1:.2f} | S2: ₹{s2:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **VOLATILITY:** {vol:.2f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 **AI INSIGHTS:**\n"
            f"💡 {ai_comment}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_AIAUTO ADVISORY_"
        )
    except Exception as e:
        return f"⚠️ Error: {str(e)}"

def scan_stocks(stocks_list):
    """Scan stocks for ASI > 75"""
    report = ""
    for sym in stocks_list:
        try:
            stock = y
