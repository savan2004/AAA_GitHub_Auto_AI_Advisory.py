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

# --- 1. CONFIG (GitHub + Render friendly) ---

TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set in environment.")
if not OPENAI_API_KEY:
    print("⚠️ OPENAI_API_KEY not set. AI features will be disabled.")

bot = telebot.TeleBot(TOKEN)

# --- 2. OPENAI CLIENT ---

AI_ENABLED = False
client = None
try:
    if OPENAI_API_KEY:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        AI_ENABLED = True
        print("✅ OpenAI client initialized.")
except Exception as e:
    print("⚠️ OpenAI init error:", repr(e))
    AI_ENABLED = False

# --- 3. TECHNICAL HELPERS ---


def calculate_rsi(series, period=14):
    """Calculate RSI using EMA method (robust fallback if data short)."""
    if len(series) < period + 1:
        return 50.0
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / (avg_loss.replace(0, 1e-9))
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def calculate_pivots(high, low, close):
    """Calculate pivot points for support/resistance."""
    pp = (high + low + close) / 3
    r1 = (2 * pp) - low
    s1 = (2 * pp) - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2 * (pp - low)
    s3 = low - 2 * (high - pp)
    return pp, r1, s1, r2, s2, r3, s3


def calculate_volatility(df):
    """Calculate 20-day rolling volatility in %."""
    if len(df) < 20:
        return None
    try:
        vol = float(df["Close"].pct_change().rolling(20).std().iloc[-1] * 100)
        return vol
    except Exception:
        return None


def compute_asi_score(ltp, ema_50, ema_200, rsi, pe, roe, upside_pct, volatility=None):
    """
    Enhanced ASI (Advanced Sovereign Intelligence) score 0–100.
    Weighted factors:
    - Trend quality: 30 pts
    - Momentum: 20 pts
    - Valuation: 10 pts
    - Quality (ROE): 10 pts
    - Reward-to-risk: 10 pts
    - Volatility adjustment: ±5 pts
    """
    score = 0

    # 1) TREND QUALITY (max 30)
    if ltp > ema_200:
        score += 30  # strong structural uptrend
    elif ltp > ema_50:
        score += 15  # short-term uptrend

    # 2) MOMENTUM (max 20)
    if 45 <= rsi <= 60:
        score += 20  # ideal accumulation zone
    elif 40 <= rsi < 45 or 60 < rsi <= 70:
        score += 10  # acceptable range
    elif rsi > 70:
        score += 5   # overbought but still bullish

    # 3) VALUATION (max 10)
    if pe and pe > 0:
        if pe < 15:
            score += 10  # undervalued
        elif 15 <= pe <= 25:
            score += 5   # fairly valued

    # 4) QUALITY via ROE (max 10)
    if roe and roe > 0:
        if roe >= 18:
            score += 10  # excellent
        elif 12 <= roe < 18:
            score += 5   # good

    # 5) RISK-REWARD via upside to R2 (max 10)
    if upside_pct >= 10:
        score += 10
    elif 5 <= upside_pct < 10:
        score += 5
    elif 2 <= upside_pct < 5:
        score += 2

    # 6) VOLATILITY ADJUSTMENT (±5)
    if volatility is not None:
        if volatility > 5:
            score -= 5
        elif volatility > 3.5:
            score -= 2
        elif volatility < 1:
            score -= 3

    return max(0, min(score, 100))


STATIC_NOTES = {
    "DLF": "DLF Ltd: Leading realty developer with cyclical earnings. Exposed to residential, commercial, and retail segments.",
    "RELIANCE": "Reliance Industries: Diversified conglomerate spanning energy, retail, telecom (Jio). Strong cash generation and dividend policy.",
    "HDFCBANK": "HDFC Bank: Large private-sector bank with strong liability franchise. Leading in mortgages and retail deposits.",
    "INFY": "Infosys: Tier-1 IT services company. Exposed to global IT spend, forex (USD/INR), and digital transformation trends.",
    "TCS": "TCS: India's largest IT company by market cap. Dominant in banking, financials, and global delivery.",
    "BANKNIFTY": "Bank Nifty Index: 12 large-cap bank stocks. Highly correlated with RBI policy, credit cycle, and institutional inflows.",
}


def get_verdict_and_conclusion(ltp, ema_50, ema_200, rsi, asi_score, company_name):
    """Return verdict emoji, text, and conclusion based on ASI score."""
    if asi_score >= 75:
        return (
            "📈",
            "STRONG BUY / ACCUMULATE",
            f"{company_name} shows structural strength. Accumulate on dips near S1/S2.",
        )
    elif 55 <= asi_score < 75:
        return (
            "✅",
            "BUY / HOLD ON STRENGTH",
            f"{company_name} is healthy. Buy on weakness below EMA50.",
        )
    elif 35 <= asi_score < 55:
        return (
            "⏸️",
            "HOLD / WAIT FOR CLARITY",
            f"{company_name} is consolidating. Wait for breakout above EMA200.",
        )
    else:
        return (
            "🔻",
            "AVOID / EXIT ON RISE",
            f"{company_name} shows weakness. Avoid fresh entry until above EMA200.",
        )


# --- 4. NIFTY OPTION TRADING ---


def get_nifty_option_trade(budget, spot):
    """Generate high-conviction Nifty option trade (AI first, fallback math)."""
    try:
        # AI-generated trade (preferred)
        if AI_ENABLED and client:
            prompt = (
                f"You are a Nifty options research desk.\n"
                f"Spot: {spot:.2f} | Budget: ₹{budget} | Lot: 65\n"
                f"Generate ONE high-conviction trade:\n"
                f"- RR minimum 1:3\n"
                f"- Strike = multiple of 50\n"
                f"- Type = CALL or PUT\n"
                f"- Consider volatility and Greeks\n\n"
                f"Return ONLY JSON:\n"
                f'{{"strike":int,"type":"CALL/PUT","expiry":"DD-MMM","entry":float,"target":float,"sl":float,"lots":int,"bias":"bullish/bearish/neutral","reason":"1-line reason"}}'
            )
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.5,
                    timeout=10,
                )
                content = response.choices[0].message.content
                match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    capital = round(data["entry"] * 65 * data["lots"])
                    rr = (
                        round(
                            (data["target"] - data["entry"])
                            / (data["entry"] - data["sl"]),
                            2,
                        )
                        if data["entry"] != data["sl"]
                        else 0
                    )

                    return (
                        "🚀 **NIFTY QUANT SIGNAL (AI-DRIVEN)**\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 **SPOT:** {spot:.2f}\n"
                        f"🎯 **STRIKE:** {data['strike']} {data['type']} | {data['expiry']}\n"
                        f"💰 **ENTRY:** ₹{data['entry']:.2f} | **TARGET:** ₹{data['target']:.2f}\n"
                        f"🛑 **SL:** ₹{data['sl']:.2f}\n"
                        f"📍 **LOTS:** {data['lots']} | **CAPITAL:** ₹{capital}\n"
                        f"📊 **RISK-REWARD:** 1:{rr}\n"
                        f"📈 **BIAS:** {data['bias'].upper()}\n"
                        f"💡 **THESIS:** {data['reason']}\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━"
                    )
            except Exception as e:
                print(f"AI option trade error: {repr(e)}")

        # FALLBACK: Math-based
        hist = yf.Ticker("^NSEI").history(period="5d")
        if hist.empty:
            return "⚠️ Unable to fetch Nifty data for option calc."

        prev_close = float(hist["Close"].iloc[-2])
        vol_pct = float(hist["Close"].pct_change().rolling(5).std().iloc[-1] * 100)

        strike = round(spot / 50) * 50
        option_type = "CALL" if spot > prev_close else "PUT"

        # ATM premium estimation
        atm_premium = max(50, spot * 0.005 + vol_pct * 4)
        max_lots = max(1, int(budget / (atm_premium * 65)))

        target = round(atm_premium * 1.3, 2)
        sl = round(atm_premium * 0.5, 2)
        capital = round(atm_premium * 65 * max_lots, 2)

        return (
            "⚠️ **MATH MODEL FALLBACK (AI unavailable)**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **SPOT:** {spot:.2f}\n"
            f"🎯 **STRIKE:** {strike} {option_type}\n"
            f"💰 **ENTRY:** ₹{atm_premium:.2f} | **TARGET:** ₹{target:.2f}\n"
            f"🛑 **SL:** ₹{sl:.2f}\n"
            f"📍 **LOTS:** {max_lots} | **CAPITAL:** ₹{capital}\n"
            f"📊 **VOL:** {vol_pct:.2f}% | **STRATEGY:** ATM\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    except Exception as e:
        return f"⚠️ **Option Sniper Error:** {str(e)}"


# --- 5. SMART PORTFOLIO ---


def get_smart_portfolio():
    """Scan large/mid/small caps and return high-ASI picks (delisted removed)."""
    try:
        large_caps = [
            "RELIANCE", "HDFCBANK", "INFY", "ICICIBANK", "SBIN",
            "BHARTIARTL", "ITC", "TCS", "KOTAKBANK", "LT"
        ]
        mid_caps = [
            "PERSISTENT", "MOTHERSON", "MAXHEALTH", "AUBANK",
            "LATENTVIEW", "TRENT", "TATACONSUM", "CHOLAHLDNG", "M&MFIN"
        ]
        # Removed: PEL, PRAJINDS, IIFLSEC (delisted/404 errors)
        small_caps = [
            "SUZLON", "HEG", "TANLA", "BAJAJELEC", "ORIENTELEC",
            "SHARDACROP", "JINDALSTEL", "DCMSHRIRAM"
        ]

        final_report = "💎 **SMART PORTFOLIO (ASI 75%+)**\n"
        final_report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

        def scan_category(stocks):
            selected = []
            for sym in stocks:
                try:
                    df = yf.Ticker(f"{sym}.NS").history(period="200d")
                    if df.empty or len(df) < 50:
                        continue
                    close = df["Close"]
                    ltp = float(close.iloc[-1])
                    rsi = calculate_rsi(close)
                    ema_50 = close.ewm(span=50).mean().iloc[-1]
                    ema_200 = close.ewm(span=200).mean().iloc[-1]
                    vol = calculate_volatility(df)

                    high_prev = float(df["High"].iloc[-2])
                    low_prev = float(df["Low"].iloc[-2])
                    prev_close = float(close.iloc[-2])
                    _, _, _, r2, _, _, _ = calculate_pivots(
                        high_prev, low_prev, prev_close
                    )
                    upside = ((r2 - ltp) / ltp) * 100 if ltp > 0 else 0

                    score = compute_asi_score(
                        ltp, ema_50, ema_200, rsi, None, None, upside, vol
                    )
                    if score >= 75:
                        selected.append(f"✅ {sym} (ASI: {score})")
                except Exception:
                    continue
            return selected

        final_report += (
            "🚀 **LARGE
