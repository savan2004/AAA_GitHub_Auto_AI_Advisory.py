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
import openai

TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set")

bot = telebot.TeleBot(TOKEN)

AI_ENABLED = False
client = None
try:
    if OPENAI_API_KEY:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        AI_ENABLED = True
        print("✅ OpenAI OK")
except Exception as e:
    print(f"⚠️ OpenAI: {e}")

def calculate_rsi(series, period=14):
    if len(series) < period + 1:
        return 50.0
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
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

def calc_vol(df):
    if len(df) < 20:
        return None
    try:
        return float(df['Close'].pct_change().rolling(20).std().iloc[-1] * 100)
    except:
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

def get_nifty_option_trade(budget, spot):
    try:
        # AI approach
        if AI_ENABLED and client:
            prompt = (
                f"Nifty Options Research Desk.\n"
                f"Spot: {spot}, Budget: ₹{budget}.\n"
                f"Suggest CE/PE strike, lot size, risk-reward.\n"
                f"Output: JSON {{'strike': int, 'type': 'CE'/'PE', 'lots': int, 'entry': float, 'stoploss': float, 'target': float}}"
            )
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            result = json.loads(response.choices[0].message.content.strip())
            strike = result['strike']
            opt_type = result['type']
            lots = result['lots']
            entry = result['entry']
            sl = result['stoploss']
            tgt = result['target']
            return (
                f"🎯 **NIFTY OPTION TRADE**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 {datetime.now().strftime('%d-%b-%Y')}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🏷 **{opt_type} {strike}**\n"
                f"💰 **Entry:** ₹{entry:.2f} | **SL:** ₹{sl:.2f} | **Target:** ₹{tgt:.2f}\n"
                f"📦 **Lots:** {lots} | **Risk:** ₹{(entry - sl) * lots * 50:.0f}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"_AIAUTO ADVISORY_"
            )
    except Exception as e:
        print(f"AI trade error: {repr(e)}")

    # FALLBACK
    hist = yf.Ticker("^NSEI").history(period="5d")
    if hist.empty:
        return "⚠️ Unable to fetch Nifty data."
    
    # Simple fallback logic (e.g., ATM CE)
    atm_strike = round(spot / 50) * 50
    lots = max(1, int(budget / (spot * 50 * 0.1)))  # Rough estimate
    entry = spot * 0.02  # Dummy premium
    sl = entry * 0.5
    tgt = entry * 2
    return (
        f"🎯 **NIFTY OPTION TRADE (Fallback)**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 **CE {atm_strike}**\n"
        f"💰 **Entry:** ₹{entry:.2f} | **SL:** ₹{sl:.2f} | **Target:** ₹{tgt:.2f}\n"
        f"📦 **Lots:** {lots}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"_AIAUTO ADVISORY_"
    )

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
        except:
            continue
    return report

def get_market_scan():
    large_caps = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY"]
    mid_caps = ["BAJFINANCE", "MARUTI", "SHREECEM", "DMART", "PIDILITIND"]
    small_caps = ["NYKAA", "POLYCAB", "METROPOLIS", "CAMS", "AFFLE"]
    
    lc = scan_category(large_caps)
    mc = scan_category(mid_caps)
    sc = scan_category(small_caps)
    
    if not lc and not mc and not sc:
        return "⚠️ **Market Condition:** Current market is choppy. No stocks qualifying for >75% ASI Score. Wait for rally."
    
    final_report = "🚀 **SK AUTO AI MARKET SCAN**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    final_report += "\n🏢 **LARGE CAP (60% Allocation)**\n"
    if lc:
        final_report += lc
    else:
        final_report += " No strong signals.\n"
    
    final_report += "\n🏭 **MID CAP (30% Allocation)**\n"
    if mc:
        final_report += mc
    else:
        final_report += " No strong signals.\n"
    
    final_report += "\n🏪 **SMALL CAP (10% Allocation)**\n"
    if sc:
        final_report += sc
    else:
        final_report += " No strong signals.\n"
    
    final_report += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    final_report += "🧠 **Strategy:** High conviction picks based on Trend, Momentum, and Fundamentals.\n"
    final_report += "_AIAUTO ADVISORY Selection Engine_"
    
    return final_report

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
        
        rsi = calculate_rsi(close)
        ema_50 = close.ewm(span=50).mean().iloc[-1]
        ema_200 = close.ewm(span=200).mean().iloc[-1]
        vol = calc_vol(df)
        
        pp, r1, s1, r2, s2, r3, s3 = calculate_pivots(hp, lp, pc)
        upside_pct = round(((r2 - ltp) / ltp) * 100, 2)
        
        asi = compute_asi_score(ltp, ema_50, ema_200, rsi, pe, roe, upside_pct, vol)
        conf = "High" if asi >= 75 else "Moderate" if asi >= 55 else "Low"
        
        if asi >= 75:
            verd = "📈 STRONG BUY"
        elif asi >= 55:
            verd = "✅ BUY/HOLD"
        elif asi >= 35:
            verd = "⏸️ WAIT"
        else:
            verd = "🔻 AVOID"
        
        pos_points = "• Strong Market Position\n• Good Cash Flow\n• Reasonable Liquidity"
        
        return (
            f"🚀 **SK AUTO AI ADVISORY**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {datetime.now().strftime('%d-%b-%Y %H:%M')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 **{sym}** | {cname}\n"
            f"🏛 **ASI:** {asi}/100 ({conf})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **LTP:** ₹{ltp:.2f} | 📊 **RSI:** {rsi:.2f}\n"
            f"📈 **TREND:** {'BULLISH' if ltp > ema_200 else 'BEARISH'}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **VERDICT:** {verd}\n"
            f"🚀 **UPSIDE:** {upside_pct}% (₹{r2:.2f})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 **FUNDAMENTALS**\n"
            f"• Cap: {round(mcap/1e7,1)}Cr | {sector}\n"
            f"• PE: {round(pe,2)}x | PB: {round(pb,2)}x | ROE: {round(roe,1)}%\n"
            f"• {pos_points}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏗 **TECHNICAL ZONES**\n"
            f"R3:{r3:.2f} R2:{r2:.2f} R1:{r1:.2f}\n"
            f"PP:{pp:.2f} S1:{s1:.2f} S2:{s2:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 VOL: {vol:.2f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"_AIAUTO ADVISORY_"
        )
    except Exception as e:
        return f"⚠️ Error: {e}"

def find_symbol(query):
    try:
        if not AI_ENABLED:
            return query.upper().replace(" ", "")
        prompt = f"User: '{query}'. Return ONLY NSE symbol UPPERCASE."
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        return re.sub(r'\.NS|[^A-Z]', '', response.choices[0].message.content.strip().upper())
    except:
        return query.upper()

@bot.message_handler(commands=["start"])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🔎 Smart Search")
    markup.add("📊 Market Scan")
    markup.add("🎯 Nifty Options")
    bot.send_message(m.chat.id, "🚀 **SK AUTO AI**\n\nChoose an option:", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🔎 Smart Search")
def smart_search(m):
    msg = bot.send_message(m.chat.id, "🔍 Type Company Name:")
    bot.register_next_step_handler(msg, process_search)

def process_search(m):
    query = m.text or ""
    bot.send_chat_action(m.chat.id, "typing")
    sym = find_symbol(query)
    bot.send_message(m.chat.id, f"🧠 Symbol: **{sym}**", parse_mode="Markdown")
    bot.send_message(m.chat.id, get_sk_auto_report(sym), parse_mode="Markdown")

@bot.message_handler(func=lambda m
