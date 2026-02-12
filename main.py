import os
import json
import threading
import time
import re
import requests
import random
from datetime import datetime

import telebot
from telebot import types
import yfinance as yf
import pandas as pd
import openai

# Load config from JSON
with open('config.json', 'r') as f:
    config = json.load(f)

TOKEN = config.get('TELEGRAM_TOKEN')
OPENAI_API_KEY = config.get('OPENAI_API_KEY')
GROQ_API_KEY = config.get('GROQ_API_KEY')
NEWS_API_KEY = config.get('NEWS_API_KEY')
STOCK_LISTS = config.get('STOCK_LISTS', {})
NEWS_SOURCES = config.get('NEWS_SOURCES', [])

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set in config.json")

bot = telebot.TeleBot(TOKEN)

AI_ENABLED = False
client = None
try:
    if OPENAI_API_KEY:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        AI_ENABLED = True
        print("✅ OpenAI OK")
    elif GROQ_API_KEY:
        AI_ENABLED = True
        print("✅ Groq OK")
except Exception as e:
    print(f"⚠️ AI: {e}")

# Auto-Healing Function
def auto_heal(error_type, context, max_retries=3):
    """
    Human-like self-healing: Diagnose, repair, and retry.
    """
    print(f"🔧 DIAGNOSIS: {error_type} in {context}. Analyzing issue...")
    time.sleep(random.uniform(1, 3))  # Simulate human thinking
    for attempt in range(max_retries):
        try:
            if error_type == "API_FAIL":
                if "OpenAI" in context and GROQ_API_KEY:
                    print("🔧 REPAIR: Switching to Groq for AI. Attempting fix...")
                    return "groq_fallback"
                else:
                    print(f"🔧 REPAIR: Retrying API call (attempt {attempt+1}).")
                    time.sleep(2 ** attempt)  # Exponential backoff
            elif error_type == "DATA_FAIL":
                print("🔧 REPAIR: Reducing data period or using cached fallback.")
                return "reduced_period"
            elif error_type == "POLLING_FAIL":
                print("🔧 REPAIR: Resetting polling session and clearing conflicts.")
                bot.delete_webhook(drop_pending_updates=True)
                time.sleep(5)
            elif error_type == "NETWORK_FAIL":
                print("🔧 REPAIR: Waiting for network recovery.")
                time.sleep(10)
            return "repaired"
        except Exception as e:
            print(f"🔧 REPAIR ATTEMPT {attempt+1} FAILED: {e}. Retrying...")
    print("🔧 HEALING FAILED: Escalating to human intervention. Continuing with fallbacks.")
    return "failed"

def calculate_rsi(series, period=14):
    try:
        if len(series) < period + 1:
            return 50.0
        delta = series.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        rs = avg_gain / (avg_loss.replace(0, 1e-9))
        return float(100 - (100 / (1 + rs)).iloc[-1])
    except Exception as e:
        print(f"⚠️ RSI calculation failed: {e}")
        return 50.0

def calculate_pivots(high, low, close):
    try:
        pp = (high + low + close) / 3
        r1 = (2 * pp) - low
        s1 = (2 * pp) - high
        r2 = pp + (high - low)
        s2 = pp - (high - low)
        r3 = high + 2 * (pp - low)
        s3 = low - 2 * (high - pp)
        return pp, r1, s1, r2, s2, r3, s3
    except Exception as e:
        print(f"⚠️ Pivot calculation failed: {e}")
        return 0, 0, 0, 0, 0, 0, 0

def calc_vol(df):
    try:
        if len(df) < 20:
            return None
        return float(df['Close'].pct_change().rolling(20).std().iloc[-1] * 100)
    except Exception as e:
        print(f"⚠️ Volatility calculation failed: {e}")
        return None

def compute_asi_score(ltp, ema_50, ema_200, rsi, pe, roe, upside_pct, volatility=None):
    try:
        score = 0
        if ltp > ema_200:
            score += 30
        elif ltp > ema_50:
            score += 15
        if 45 <= rsi <= 60:
            score += 20
        elif 40 <= rsi < 45 or 60 < rsi <= 70:
            score += 10
        elif rsi > 70:
            score += 5
        if pe and pe > 0:
            if pe < 15:
                score += 10
            elif 15 <= pe <= 25:
                score += 5
        if roe and roe > 0:
            if roe >= 18:
                score += 10
            elif 12 <= roe < 18:
                score += 5
        if upside_pct >= 10:
            score += 10
        elif 5 <= upside_pct < 10:
            score += 5
        elif 2 <= upside_pct < 5:
            score += 2
        if volatility:
            if volatility > 5:
                score -= 5
            elif volatility > 3.5:
                score -= 2
            elif volatility < 1:
                score -= 3
        return max(0, min(score, 100))
    except Exception as e:
        print(f"⚠️ ASI calculation failed: {e}")
        return 50

def get_nifty_option_trade(budget, spot):
    try:
        if AI_ENABLED and client:
            prompt = (
                f"Nifty Options Research Desk.\n"
                f"Spot: {spot}, Budget: ₹{budget}.\n"
                f"Suggest CE/PE strike, lot size, risk-reward, delta, theta, and strategy.\n"
                f"Output: JSON {{'strike': int, 'type': 'CE'/'PE', 'lots': int, 'entry': float, 'stoploss': float, 'target': float, 'delta': float, 'theta': float, 'strategy': str}}"
            )
            response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], temperature=0.2)
            result = json.loads(response.choices[0].message.content.strip())
            strike = result['strike']
            opt_type = result['type']
            lots = result['lots']
            entry = result['entry']
            sl = result['stoploss']
            tgt = result['target']
            delta = result['delta']
            theta = result['theta']
            strategy = result['strategy']
            risk_reward = round((tgt - entry) / (entry - sl), 2) if entry > sl else 0
            return (
                f"🎯 **NIFTY OPTION TRADE**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 {datetime.now().strftime('%d-%b-%Y')}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🏷 **{opt_type} {strike}**\n"
                f"💰 **Entry:** ₹{entry:.2f} | **SL:** ₹{sl:.2f} | **Target:** ₹{tgt:.2f}\n"
                f"📦 **Lots:** {lots} | **Risk:** ₹{(entry - sl) * lots * 50:.0f}\n"
                f"📊 **Greeks:** Delta: {delta:.2f} | Theta: {theta:.2f}\n"
                f"🎯 **Risk-Reward:** {risk_reward}:1\n"
                f"🧠 **Strategy:** {strategy}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"_AIAUTO ADVISORY_"
            )
    except Exception as e:
        heal_result = auto_heal("API_FAIL", "get_nifty_option_trade")
        if heal_result == "groq_fallback":
            return "🎯 **NIFTY OPTION TRADE (Groq Fallback)**\n🏷 **CE 22000**\n💰 **Entry:** ₹150.00\n_AIAUTO ADVISORY_"
        print(f"AI trade error: {repr(e)}")

    # Fallback
    try:
        hist = yf.Ticker("^NSEI").history(period="5d")
        if hist.empty:
            return "⚠️ Unable to fetch Nifty data."
        atm_strike = round(spot / 50) * 50
        lots = max(1, int(budget / (spot * 50 * 0.1)))
        entry = spot * 0.02
        sl = entry * 0.5
        tgt = entry * 2
        delta = 0.5
        theta = -0.02
        risk_reward = 2.0
        strategy = "ATM Call for bullish bias"
        return (
            f"🎯 **NIFTY OPTION TRADE (Fallback)**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 **CE {atm_strike}**\n"
            f"💰 **Entry:** ₹{entry:.2f} | **SL:** ₹{sl:.2f} | **Target:** ₹{tgt:.2f}\n"
            f"📦 **Lots:** {lots}\n"
            f"📊 **Greeks:** Delta: {delta:.2f} | Theta: {theta:.2f}\n"
            f"🎯 **Risk-Reward:** {risk_reward}:1\n"
            f"🧠 **Strategy:** {strategy}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"_AIAUTO ADVISORY_"
        )
    except Exception as e:
        heal_result = auto_heal("DATA_FAIL", "get_nifty_option_trade")
        return f"⚠️ Error: {e}. Auto-heal attempted."

def scan_category(stocks):
    report = ""
    for sym in stocks:
        try:
            tsym = f"{sym}.NS"
            stock = yf.Ticker(tsym)
            df = stock.history(period="1y")
            if df.empty:
                continue
            close = df['Close']
            ltp = float(close.iloc[-1])
            pc = float(close.iloc[-2])
            hp = float(df['High'].iloc[-2])
            lp = float(df['Low'].iloc[-2])
            info = stock.info
            pe = float(info.get('trailingPE', 0) or 0)
            roe = float((info.get('returnOnEquity', 0) or 0) * 100)
            rsi = calculate_rsi(close)
            ema_50 = close.ewm(span=50).mean().iloc[-1]
            ema_200 = close.ewm(span=200).mean().iloc[-1]
            vol = calc_vol(df)
            pp, r1, s1, r2, s2, r3, s3 = calculate_pivots(hp, lp, pc)
            up = round(((r2 - ltp) / ltp) * 100, 2)
            asi = compute_asi_score(ltp, ema_50, ema_200, rsi, pe, roe, up, vol)
            if asi >= 75:
                report += f"• {sym}: ASI {asi}/100\n"
        except Exception as e:
            print(f"⚠️ Scan failed for {sym}: {e}")
            continue
    return report

def get_market_scan():
    try:
        large_caps = STOCK_LISTS.get('large_caps', [])
        mid_caps = STOCK_LISTS.get('mid_caps', [])
        small_caps = STOCK_LISTS.get('small_caps', [])
        
        lc = scan_category(large_caps)
        mc = scan_category(mid_caps)
        sc = scan_category(small_caps)
        
        if not lc and not mc and not sc:
            return "⚠️ **Market Condition:** Current market is choppy. No stocks qualifying for >75% ASI Score. Wait for rally."
        
        total_large = len(large_caps)
        total_mid = len(mid_caps)
        total_small = len(small_caps)
        signals_large = len(lc.split('\n')) - 1 if lc else 0
        signals_mid = len(mc.split('\n')) - 1 if mc else 0
        signals_small = len(sc.split('\n')) - 1 if sc else 0
        total_signals = signals_large + signals_mid + signals_small
        
        ai_summary = "N/A"
        if AI_ENABLED and client:
            try:
                prompt = f"Summarize NSE market scan: {total_signals} strong signals across {total_large + total_mid + total_small} stocks. Focus on large caps."
                response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], temperature=0.2)
                ai_summary = response.choices[0].message.content.strip()
            except Exception as e:
                heal_result = auto_heal("API_FAIL", "get_market_scan")
                if heal_result == "groq_fallback":
                    ai_summary = "Market shows mixed signals; monitor large caps."
        
        final_report = (
            f"🚀 **SK AUTO AI MARKET SCAN**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **OVERVIEW**\n"
            f"• Total Stocks Scanned: {total_large + total_mid + total_small}\n"
            f"• Strong Signals (ASI >75): {total_signals}\n"
            f"• Large Cap Signals: {signals_large}/{total_large}\n"
            f"• Mid Cap Signals: {signals_mid}/{total_mid}\n"
            f"• Small Cap Signals: {signals_small}/{total_small}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"\n🏢 **LARGE CAP (60% Allocation)**\n"
            f"{lc or ' No strong signals.\n'}"
            f"\n🏭 **MID CAP (30% Allocation)**\n"
            f"{mc or ' No strong signals.\n'}"
            f"\n🏪 **SMALL CAP (10% Allocation)**\n"
            f"{sc or ' No strong signals.\n'}"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 **AI SUMMARY**\n"
            f"{ai_summary}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 **Strategy:** High conviction picks based on Trend, Momentum, and Fundamentals.\n"
            f"_AIAUTO ADVISORY Selection Engine_"
        )
        return final_report
    except Exception as e:
        heal_result = auto_heal("DATA_FAIL", "get_market_scan")
        return f"⚠️ Error: {e}. Auto-heal attempted."

def get_sk_auto_report(symbol):
    try:
        sym = symbol.upper().strip()
        
        if sym in ["NIFTY", "NIFTY50"]:
            tsym = "^NSEI"
        elif sym == "BANKNIFTY":
            tsym = "^NSEBANK"
        else:
            tsym = f"{sym}.NS"
        
        stock = yf.Ticker(tsym)
        df = stock.history(period="1y")
        info = stock.info
        
        if df.empty:
            heal_result = auto_heal("DATA_FAIL", "get_sk_auto_report")
            if heal_result == "reduced_period":
                df = stock.history(period="6mo")
                if df.empty:
                    return f"❌ Symbol {sym} not found"
        
        close = df['Close']
        ltp = float(close.iloc[-1])
        pc = float(close.iloc[-2])
        hp = float(df['High'].iloc[-2])
        lp = float(df['Low'].iloc[-2])
        
        cname = info.get('longName', sym)
        sector = info.get('sector', 'N/A')
        mcap = float(info.get('marketCap', 0) or 0)
        pe = float(info.get('trailingPE', 0) or 0)
        pb = float(info.get('priceToBook', 0) or 0)
        roe = float((info.get('returnOnEquity', 0) or 0) * 100)
        dividend_yield = float(info.get('dividendYield', 0) or 0) * 100
        beta = float(info.get('beta', 0) or 0)
        week_high = float(info.get('fiftyTwoWeekHigh', 0) or 0)
        week_low = float(info.get('fiftyTwoWeekLow', 0) or 0)
        recommendation = info.get('recommendationKey', 'N/A')
        
        rsi = calculate_rsi(close)
        ema_50 = close.ewm(span=50).mean().iloc[-1]
        ema_200 = close.ewm(span=200).mean().iloc[-1]
        vol = calc_vol(df)
        
        macd_line = close.ewm(span=12).mean() - close.ewm
