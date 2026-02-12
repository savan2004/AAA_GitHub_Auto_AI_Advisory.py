"""
SK AUTO AI - Market Analysis Engine
Stock reports, market scans, option trades
"""

import json
import re
from datetime import datetime
import yfinance as yf

from config import AI_ENABLED, OPENAI_API_KEY, LARGE_CAPS, MID_CAPS, SMALL_CAPS
from utils import (
    calculate_rsi, calculate_ema, calculate_pivots, calculate_volatility,
    compute_asi_score, get_asi_verdict, get_confidence, get_trend_direction
)

client = None
if AI_ENABLED and OPENAI_API_KEY:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        print("✅ OpenAI connected")
    except Exception as e:
        print(f"⚠️ OpenAI error: {e}")


def find_symbol(query):
    """Find NSE symbol from company name"""
    try:
        if not AI_ENABLED or not client:
            return query.upper().replace(" ", "")
        
        prompt = f"User: '{query}'. Return ONLY NSE symbol UPPERCASE (e.g., RELIANCE)."
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        raw = response.choices[0].message.content.strip().upper()
        return re.sub(r"\\.NS|[^A-Z]", "", raw)
    except Exception:
        return query.upper().replace(" ", "")


def get_sk_auto_report(symbol):
    """Generate Deep ASI Analysis Report"""
    try:
        sym = symbol.upper().strip()
        
        # Handle indices
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
        
        # Extract data
        close = df["Close"]
        ltp = float(close.iloc[-1])
        pc = float(close.iloc[-2])
        hp = float(df["High"].iloc[-2])
        lp = float(df["Low"].iloc[-2])
        
        cname = info.get("longName", sym)
        sector = info.get("sector", "N/A")
        mcap = float(info.get("marketCap", 0) or 0)
        pe = float(info.get("trailingPE", 0) or 0)
        pb = float(info.get("priceToBook", 0) or 0)
        roe = float((info.get("returnOnEquity", 0) or 0) * 100)
        
        # Calculate technicals
        rsi = calculate_rsi(close)
        ema_50 = calculate_ema(close, 50)
        ema_200 = calculate_ema(close, 200)
        vol = calculate_volatility(df)
        
        # Calculate pivots
        pp, r1, s1, r2, s2, r3, s3 = calculate_pivots(hp, lp, pc)
        upside_pct = round(((r2 - ltp) / ltp) * 100, 2)
        
        # Calculate ASI
        asi = compute_asi_score(ltp, ema_50, ema_200, rsi, pe, roe, upside_pct, vol)
        conf = get_confidence(asi)
        verd = get_asi_verdict(asi)
        trend = get_trend_direction(ltp, ema_50, ema_200)
        
        return (
            f"🚀 **SK AUTO AI ADVISORY**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {datetime.now().strftime('%d-%b-%Y %H:%M')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 **{sym}** | {cname}\n"
            f"🏛 **ASI:** {asi}/100 ({conf})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **LTP:** ₹{ltp:.2f} | 📊 **RSI:** {rsi:.2f}\n"
            f"📈 **TREND:** {trend}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **VERDICT:** {verd}\n"
            f"🚀 **UPSIDE:** {upside_pct}% (Target: ₹{r2:.2f})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 **FUNDAMENTALS**\n"
            f"• Market Cap: {round(mcap/1e7, 1)}Cr | {sector}\n"
            f"• PE: {round(pe, 2)}x | PB: {round(pb, 2)}x | ROE: {round(roe, 1)}%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏗 **TECHNICAL ZONES**\n"
            f"R3:{r3:.2f} | R2:{r2:.2f} | R1:{r1:.2f}\n"
            f"PP:{pp:.2f} | S1:{s1:.2f} | S2:{s2:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Volatility: {vol:.2f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"_AIAUTO ADVISORY_"
        )
    except Exception as e:
        return f"⚠️ Error: {str(e)}"


def scan_category(stocks):
    """Scan stock category for ASI > 75"""
    report = ""
    for sym in stocks:
        try:
            tsym = f"{sym}.NS"
            stock = yf.Ticker(tsym)
            df = stock.history(period="1y")
            if df.empty:
                continue
            
            close = df["Close"]
            ltp = float(close.iloc[-1])
            pc = float(close.iloc[-2])
            hp = float(df["High"].iloc[-2])
            lp = float(df["Low"].iloc[-2])
            
            info = stock.info
            pe = float(info.get("trailingPE", 0) or 0)
            roe = float((info.get("returnOnEquity", 0) or 0) * 100)
            
            rsi = calculate_rsi(close)
            ema_50 = calculate_ema(close, 50)
            ema_200 = calculate_ema(close, 200)
            vol = calculate_volatility(df)
            
            pp, r1, s1, r2, s2, r3, s3 = calculate_pivots(hp, lp, pc)
            upside_pct = round(((r2 - ltp) / ltp) * 100, 2)
            
            asi = compute_asi_score(ltp, ema_50, ema_200, rsi, pe, roe, upside_pct, vol)
            if asi >= 75:
                report += f"• {sym}: ASI {asi}/100\n"
        except Exception:
            continue
    
    return report


def get_market_scan():
    """Full market scan across categories"""
    lc = scan_category(LARGE_CAPS)
    mc = scan_category(LARGE_CAPS)
    sc = scan_category(SMALL_CAPS)
    
    if not lc and not mc and not sc:
        return (
            "⚠️ **Market Condition:** Current market is choppy. "
            "No stocks with ASI > 75%. Wait for rally."
        )
    
    final_report = "🚀 **SK AUTO AI MARKET SCAN**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    final_report += "\n🏢 **LARGE CAP (60% Allocation)**\n"
    final_report += lc if lc else " No strong signals.\n"
    
    final_report += "\n🏭 **MID CAP (30% Allocation)**\n"
    final_report += mc if mc else " No strong signals.\n"
    
    final_report += "\n🏪 **SMALL CAP (10% Allocation)**\n"
    final_report += sc if sc else " No strong signals.\n"
    
    final_report += (
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🧠 **Strategy:** High conviction picks based on Trend, Momentum, Fundamentals.\n"
        "_AIAUTO ADVISORY Selection Engine_"
    )
    return final_report


def get_nifty_option_trade(budget, spot):
    """Generate Nifty options trade recommendation"""
    try:
        if AI_ENABLED and client:
            prompt = (
                f"Nifty Options Trading Desk.\n"
                f"Spot Price: {spot}\n"
                f"Capital: ₹{budget}\n"
                f"Suggest: CE/PE strike, lot size, entry, SL, target.\n"
                f"Output ONLY JSON: "
                f"{{'strike': int, 'type': 'CE' or 'PE', 'lots': int, 'entry': float, 'stoploss': float, 'target': float}}"
            )
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            result = json.loads(response.choices[0].message.content.strip())
            
            strike = result["strike"]
            opt_type = result["type"]
            lots = result["lots"]
            entry = result["entry"]
            sl = result["stoploss"]
            tgt = result["target"]
            risk = (entry - sl) * lots * 50
            
            return (
                f"🎯 **NIFTY OPTION TRADE**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 {datetime.now().strftime('%d-%b-%Y')}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🏷 **{opt_type} {strike}**\n"
                f"💰 **Entry:** ₹{entry:.2f} | **SL:** ₹{sl:.2f} | **Target:** ₹{tgt:.2f}\n"
                f"📦 **Lots:** {lots} | **Risk:** ₹{risk:.0f}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"_AIAUTO ADVISORY_"
            )
    except Exception as e:
        print(f"AI trade error: {e}")
    
    # FALLBACK
    atm_strike = round(spot / 50) * 50
    lots = max(1, int(budget / (spot * 50 * 0.1)))
    entry = spot * 0.02
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
