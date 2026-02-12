import os
import threading
import time
import json
import re
from datetime import datetime

import telebot
from telebot import types
import yfinance as yf
import pandas as pd
import requests
import openai

# --- CONFIG ---

TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set in environment.")

bot = telebot.TeleBot(TOKEN)

# --- OPENAI CLIENT ---

AI_ENABLED = False
client = None
try:
    if OPENAI_API_KEY:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        AI_ENABLED = True
        print("✅ OpenAI initialized.")
except Exception as e:
    print(f"⚠️ OpenAI error: {repr(e)}")

# --- TECHNICAL HELPERS ---


def calculate_rsi(series, period=14):
    if len(series) < period + 1:
        return 50.0
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / (avg_loss.replace(0, 1e-9))
    return float(100 - (100 / (1 + rs)).iloc[-1])


def calculate_pivots(high, low, close):
    pp = (high + low + close) / 3
    r1 = (2 * pp) - low
    s1 = (2 * pp) - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2 * (pp - low)
    s3 = low - 2 * (high - pp)
    return pp, r1, s1, r2, s2, r3, s3


def calculate_volatility(df):
    if len(df) < 20:
        return None
    try:
        vol = float(df['Close'].pct_change().rolling(20).std().iloc[-1] * 100)
        return vol
    except Exception:
        return None


def compute_asi_score(ltp, ema_50, ema_200, rsi, pe, roe, upside_pct, volatility=None):
    """
    ASI (Advanced Sovereign Intelligence) Score 0-100.
    - Trend: 30 pts
    - Momentum: 20 pts
    - Valuation: 10 pts
    - Quality: 10 pts
    - Risk-Reward: 10 pts
    - Volatility: ±5 pts
    """
    score = 0

    # TREND (0-30)
    if ltp > ema_200:
        score += 30
    elif ltp > ema_50:
        score += 15

    # MOMENTUM (0-20)
    if 45 <= rsi <= 60:
        score += 20
    elif 40 <= rsi < 45 or 60 < rsi <= 70:
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
    if upside_pct >= 10:
        score += 10
    elif 5 <= upside_pct < 10:
        score += 5
    elif 2 <= upside_pct < 5:
        score += 2

    # VOLATILITY (±5)
    if volatility is not None:
        if volatility > 5:
            score -= 5
        elif volatility > 3.5:
            score -= 2
        elif volatility < 1:
            score -= 3

    return max(0, min(score, 100))


STATIC_NOTES = {
    "DLF": "Leading real-estate developer with cyclical earnings. Sensitive to rate cycles.",
    "RELIANCE": "Diversified conglomerate (energy, retail, telecom). Proxy for India growth.",
    "HDFCBANK": "Leading private bank with strong deposit franchise.",
    "INFY": "Large IT services company. Exposed to global IT spend and USD-INR.",
    "TCS": "India's largest IT firm. Dividend aristocrat with strong FCF.",
    "BANKNIFTY": "12 large-cap banks. Highly liquid, correlated with RBI policy.",
}


# --- NIFTY OPTION TRADING ---


def get_nifty_option_trade(budget, spot):
    try:
        # AI approach
        if AI_ENABLED and client:
            prompt = (
                f"Nifty Options Research Desk.\n"
                f"Spot: {spot:.2f} | Budget: ₹{budget} | Lot: 65\n"
                f"Generate ONE conviction trade: RR 1:3 min, Strike = multiple of 50.\n"
                f"Return ONLY JSON: {{'strike':int,'type':'CALL/PUT','expiry':'DD-MMM',"
                f"'entry':float,'target':float,'sl':float,'lots':int,'bias':'bullish/bearish',"
                f"'reason':'1-line reason'}}"
            )
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.5,
                    timeout=10,
                )
                content = response.choices[0].message.content
                match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    capital = round(data['entry'] * 65 * data['lots'])
                    rr = round((data['target'] - data['entry']) / max(data['entry'] - data['sl'], 0.01), 2)

                    return (
                        "🚀 **NIFTY QUANT SIGNAL (AI)**\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 Spot: {spot:.2f}\n"
                        f"🎯 **{data['strike']} {data['type']}** | {data['expiry']}\n"
                        f"💰 Entry: ₹{data['entry']:.2f} | Target: ₹{data['target']:.2f}\n"
                        f"🛑 SL: ₹{data['sl']:.2f}\n"
                        f"📍 Lots: {data['lots']} | Capital: ₹{capital}\n"
                        f"📊 RR: 1:{rr}\n"
                        f"📈 Bias: {data['bias'].upper()}\n"
                        f"💡 {data['reason']}\n"
                        "━━━━━━━━━━━━━━━━━━━━"
                    )
            except Exception as e:
                print(f"AI trade error: {repr(e)}")

        # FALLBACK
        hist = yf.Ticker("^NSEI").history(period="5d")
        if hist.empty:
            return "⚠️ Unable to fetch Nifty data."

        prev_close = float(hist['Close'].iloc[-2])
        vol = float(hist['Close'].pct_change().rolling(5).std().iloc[-1] * 100)

        strike = round(spot / 50) * 50
        opt_type = "CALL" if spot > prev_close else "PUT"
        premium = max(50, spot * 0.005 + vol * 4)
        lots = max(1, int(budget / (premium * 65)))

        return (
            "⚠️ **MATH FALLBACK**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Spot: {spot:.2f}\n"
            f"🎯 **{strike} {opt_type}**\n"
            f"💰 Entry: ₹{premium:.2f} | Target: ₹{premium*1.3:.2f}\n"
            f"📍 Lots: {lots}\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )

    except Exception as e:
        return f"⚠️ Option Error: {str(e)}"


# --- SMART PORTFOLIO ---


def get_smart_portfolio():
    try:
        large_caps = ['RELIANCE', 'HDFCBANK', 'INFY', 'ICICIBANK', 'SBIN', 'BHARTIARTL', 'ITC', 'TCS', 'KOTAKBANK', 'LT']
        mid_caps = ['PERSISTENT', 'MOTHERSON', 'MAXHEALTH', 'AUBANK', 'PEL', 'LATENTVIEW', 'TRENT', 'TATACONSUM', 'CHOLAHLDNG', 'M&MFIN']
        small_caps = ['SUZLON', 'HEG', 'TANLA', 'BAJAJELEC', 'ORIENTELEC', 'SHARDACROP', 'JINDALSTEL', 'PRAJINDS', 'DCMSHRIRAM', 'IIFLSEC']

        final_report = "💎 **SMART PORTFOLIO (ASI 75%+)**\n"
        final_report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

        def scan_category(stocks):
            selected = []
            for sym in stocks:
                try:
                    df = yf.Ticker(f"{sym}.NS").history(period="200d")
                    if df.empty or len(df) < 50:
                        continue
                    close = df['Close']
                    ltp = float(close.iloc[-1])
                    rsi = calculate_rsi(close)
                    ema_50 = close.ewm(span=50).mean().iloc[-1]
                    ema_200 = close.ewm(span=200).mean().iloc[-1]
                    vol = calculate_volatility(df)

                    high_prev = float(df['High'].iloc[-2])
                    low_prev = float(df['Low'].iloc[-2])
                    prev_close = float(close.iloc[-2])
                    pp, r1, s1, r2, s2, r3, s3 = calculate_pivots(high_prev, low_prev, prev_close)
                    upside = round(((r2 - ltp) / ltp) * 100, 2)

                    score = compute_asi_score(ltp, ema_50, ema_200, rsi, 0, 0, upside, vol)

                    if score >= 75:
                        selected.append({"sym": sym, "score": score, "ltp": f"{ltp:.2f}"})
                except Exception:
                    continue

            selected.sort(key=lambda x: x['score'], reverse=True)
            return selected[:2]

        lc = scan_category(large_caps)
        mc = scan_category(mid_caps)
        sc = scan_category(small_caps)

        if not lc and not mc and not sc:
            return "⚠️ **Market Condition:** Current market is choppy. No stocks qualifying for >75% ASI Score. Wait for rally."

        final_report += "\n🏢 **LARGE CAP (60% Allocation)**\n"
        if lc:
            for i, stock in enumerate(lc, 1):
                final_report += f"{i}. **{stock['sym']}** | LTP: ₹{stock['ltp']}\n   🏛 ASI Score: {stock['score']}/100\n"
        else:
            final_report += " No strong signals.\n"

        final_report += "\n🏫 **MID CAP (35% Allocation)**\n"
        if mc:
            for i, stock in enumerate(mc, 1):
                final_report += f"{i}. **{stock['sym']}** | LTP: ₹{stock['ltp']}\n   🏛 ASI Score: {stock['score']}/100\n"
        else:
            final_report += " No strong signals.\n"

        final_report += "\n🚗 **SMALL CAP (15% Allocation)**\n"
        if sc:
            for i, stock in enumerate(sc, 1):
                final_report += f"{i}. **{stock['sym']}** | LTP: ₹{stock['ltp']}\n   🏛 ASI Score: {stock['score']}/100\n"
        else:
            final_report += " No strong signals.\n"

        final_report += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        final_report += "🧠 **Strategy:** High conviction picks based on Trend, Momentum, and Fundamentals.\n"
        final_report += "_AIAUTO ADVISORY Selection Engine_"

        return final_report

    except Exception as e:
        return f"⚠️ Portfolio Error: {e}"


# --- DETAILED REPORT ---


def get_sk_auto_report(symbol):
    try:
        sym = symbol.upper().strip()

        if sym in ["NIFTY", "NIFTY50"]:
            ticker_sym = "^NSEI"
        elif sym == "BANKNIFTY":
            ticker_sym = "^NSEBANK"
        elif sym == "SENSEX":
            ticker_sym = "^BSESN"
        else:
            ticker_sym = f"{sym}.NS"

        stock = yf.Ticker(ticker_sym)
        df = stock.history(period="1y")
        info = stock.info

        if df.empty:
            return f"❌ Symbol `{sym}` not found."

        close = df['Close']
        ltp = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        high_prev = float(df['High'].iloc[-2])
        low_prev = float(df['Low'].iloc[-2])

        company_name = info.get('longName', sym)
        sector = info.get('sector', 'N/A')
        mcap = float(info.get('marketCap', 0) or 0)
        pe = float(info.get('trailingPE', 0) or 0)
