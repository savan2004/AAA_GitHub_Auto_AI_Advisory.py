import os
import time
import re
import threading
from datetime import datetime

import telebot
from telebot import types
import yfinance as yf
import pandas as pd
import numpy as np
import requests

from groq import Groq
import google.genai as genai
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer

# =========================
# 1. CONFIG & ENV
# =========================

EDU_HEADER = "📜 *Educational Analysis Only* – Not SEBI-registered advice.\n"
EDU_FOOTER = "⚠️ Use this as research input, *not* a direct buy/sell/hold signal."

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set in environment.")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

gemini_client = None
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print("Gemini init error:", repr(e))


# =========================
# 2. COMMON HELPERS
# =========================

def safe_history(ticker, period="1y", interval="1d"):
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if df is None or df.empty:
            return None
        return df.dropna()
    except Exception:
        return None


def calc_rsi(series, period=14):
    if len(series) < period + 1:
        return 50.0
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    gain = pd.Series(gain).ewm(alpha=1 / period, adjust=False).mean()
    loss = pd.Series(loss).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / (loss.replace(0, 1e-9))
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def calc_macd(series, fast=12, slow=26, signal=9):
    if len(series) < slow + signal:
        return 0.0, 0.0
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return float(macd.iloc[-1]), float(signal_line.iloc[-1])


def calc_bb(series, period=20, mult=2):
    if len(series) < period:
        v = float(series.iloc[-1])
        return v, v, v
    ma = series.rolling(period).mean()
    sd = series.rolling(period).std()
    upper = ma + mult * sd
    lower = ma - mult * sd
    return float(upper.iloc[-1]), float(ma.iloc[-1]), float(lower.iloc[-1])


def calc_pivots(h, l, c):
    pp = (h + l + c) / 3
    r1 = 2 * pp - l
    s1 = 2 * pp - h
    r2 = pp + (h - l)
    s2 = pp - (h - l)
    r3 = h + 2 * (pp - l)
    s3 = l - 2 * (h - pp)
    return pp, r1, s1, r2, s2, r3, s3


def quality_score(ltp, ema200, rsi, pe, roe):
    score = 0
    if ltp > ema200:
        score += 30
    if 45 <= rsi <= 60:
        score += 25
    elif 40 <= rsi < 45 or 60 < rsi <= 70:
        score += 10
    if pe and pe > 0:
        if pe < 15:
            score += 15
        elif 15 <= pe <= 25:
            score += 8
    if roe and roe > 0:
        if roe >= 18:
            score += 20
        elif 12 <= roe < 18:
            score += 10
    return max(0, min(score, 100))


# =========================
# 3. AI LAYER: GROQ → GEMINI → FALLBACK
# =========================

def ai_call(prompt: str, max_tokens: int = 600) -> str:
    """Multi-provider AI with failover: GROQ → Gemini → fallback."""
    if groq_client:
        try:
            resp = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.5,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print("GROQ error:", repr(e))

    if gemini_client:
        try:
            result = gemini_client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
            )
            return result.text.strip()
        except Exception as e:
            print("Gemini error:", repr(e))

    return (
        "AI providers not available. Using mathematical and rule-based analysis only. "
        "Focus on trend vs 200DMA, RSI, valuation, and sector before taking decisions."
    )


# =========================
# 4. QUICK VIEW FOR STOCK
# =========================

def quick_stock_view(sym: str, name: str, sector: str, ltp: float, prev_close: float,
                     rsi: float, ema200: float, pe: float, roe: float, qi: int) -> str:
    change_pct = ((ltp - prev_close) / prev_close) * 100 if prev_close else 0
    trend = "Above 200DMA (uptrend)" if ltp > ema200 else "Below 200DMA (weak/sideways)"

    if qi >= 75:
        tag = "High quality, momentum aligned."
    elif qi >= 55:
        tag = "Reasonable quality, stable trend."
    elif qi >= 40:
        tag = "Mixed quality, use caution."
    else:
        tag = "Weak setup, avoid aggressive bets."

    return (
        f"{EDU_HEADER}"
        f"📌 *Quick View*: `{sym}` – {name}\n"
        f"Sector: {sector}\n"
        f"Price: ₹{ltp:.2f} ({change_pct:+.2f}% vs prev close)\n"
        f"Trend: {trend}\n"
        f"RSI: {rsi:.1f} | P/E: {pe:.1f} | ROE: {roe:.1f}%\n"
        f"Quality Score: {qi}/100 – {tag}\n\n"
        f"🧭 *Idea*: Treat this as a starting point for your own research.\n"
        f"{EDU_FOOTER}"
    )


# =========================
# 5. DEEP STOCK ANALYSIS
# =========================

def deep_stock_analysis(symbol: str) -> str:
    sym = symbol.upper().strip()
    ticker = f"{sym}.NS"

    df = safe_history(ticker, period="1y", interval="1d")
    if df is None:
        return f"{EDU_HEADER}❌ Could not fetch data for `{sym}`. Check the NSE symbol.\n{EDU_FOOTER}"

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    ltp = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])

    rsi = calc_rsi(close)
    macd, macd_signal = calc_macd(close)
    bb_u, bb_m, bb_l = calc_bb(close)
    atr = float((high - low).rolling(14).mean().iloc[-1]) if len(df) >= 14 else 0.0

    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

    prev_high = float(high.iloc[-2])
    prev_low = float(low.iloc[-2])
    pp, r1, s1, r2, s2, r3, s3 = calc_pivots(prev_high, prev_low, prev_close)

    info = yf.Ticker(ticker).info
    pe = info.get("trailingPE") or info.get("forwardPE") or 0
    pb = info.get("priceToBook") or 0
    roe = (info.get("returnOnEquity") or 0) * 100
    mcap = info.get("marketCap") or 0
    div_yield = (info.get("dividendYield") or 0) * 100
    name = info.get("longName", sym)
    sector = info.get("sector", "N/A")

    st_1w = ltp * 1.03
    st_1m = ltp * 1.07
    st_3m = ltp * 1.12
    lt_6m = ltp * 1.20
    lt_1y = ltp * 1.30
    lt_2y = ltp * 1.60
    sl = ltp * 0.92

    qi = quality_score(ltp, ema200, rsi, pe, roe)

    sentiment_prompt = (
        "You are an educational stock analysis assistant for Indian equities.\n"
        "If you are not sure about something, say 'uncertain' and do NOT invent numbers or news.\n\n"
        f"Stock: {name} ({sym})\n"
        f"LTP: {ltp:.2f}, RSI: {rsi:.1f}, MACD: {macd:.2f}, MACD_signal: {macd_signal:.2f},\n"
        f"PE: {pe}, PB: {pb}, ROE: {roe:.1f}%, DivYield: {div_yield:.2f}%.\n"
        "Generate:\n"
        "1) 3 bullish points\n"
        "2) 3 bearish points\n"
        "3) 1-line sentiment (Strong Buy / Buy / Hold / Avoid / Sell)\n"
        "Format exactly as:\n"
        "Bullish:\n- ...\n- ...\n- ...\n"
        "Bearish:\n- ...\n- ...\n- ...\n"
        "Sentiment: ...\n"
    )
    sentiment_text = ai_call(sentiment_prompt, max_tokens=400)
    sentiment_text = sentiment_text[:2000]

    if "Sentiment:" in sentiment_text:
        sentiment_line = (
            sentiment_text.split("Sentiment:")[-1].strip().splitlines()[0]
        )
    else:
        if qi >= 75:
            sentiment_line = "Strong Buy (High quality and trend)."
        elif qi >= 55:
            sentiment_line = "Buy / Accumulate on dips."
        elif qi >= 40:
            sentiment_line = "Hold / Wait for better entries."
        else:
            sentiment_line = "Avoid / High risk."

    full = (
        f"{EDU_HEADER}"
        f"📊 **DEEP ANALYSIS: {sym}**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 *{name}* | Sector: {sector}\n"
        f"💰 **LTP:** ₹{ltp:.2f} (Prev: ₹{prev_close:.2f})\n"
        f"📈 52W High: ₹{df['High'].max():.2f} | 52W Low: ₹{df['Low'].min():.2f}\n"
        f"🏦 MCap: {mcap/1e7:.1f} Cr | P/E: {pe:.2f} | P/B: {pb:.2f} | ROE: {roe:.1f}% | Div: {div_yield:.2f}%\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 **Technicals**\n"
        f"RSI: {rsi:.1f} | MACD: {macd:.2f} vs Signal: {macd_signal:.2f}\n"
        f"BB: U {bb_u:.2f} | M {bb_m:.2f} | L {bb_l:.2f}\n"
        f"EMA20: {ema20:.2f} | EMA50: {ema50:.2f} | EMA200: {ema200:.2f}\n"
        f"ATR(14): {atr:.2f}\n"
        f"Pivots: PP {pp:.2f} | R1 {r1:.2f} | R2 {r2:.2f} | S1 {s1:.2f} | S2 {s2:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 **Targets & Risk (Illustrative)**\n"
        f"Short-term (1W / 1M / 3M): "
        f"₹{st_1w:.2f} / ₹{st_1m:.2f} / ₹{st_3m:.2f}\n"
        f"Long-term (6M / 1Y / 2Y): "
        f"₹{lt_6m:.2f} / ₹{lt_1y:.2f} / ₹{lt_2y:.2f}\n"
        f"Stop Loss (swing): ₹{sl:.2f}\n"
        "These levels are *educational scenarios*, not trading calls.\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Quality Score:** {qi}/100\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 **AI Sentiment & Factors**\n{sentiment_text}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Final View: {sentiment_line}\n"
        f"{EDU_FOOTER}"
    )

    return full


# =========================
# 6. INDEX VIEWS
# =========================

def nifty_view() -> str:
    df = safe_history("^NSEI", period="1mo")
    if df is None or len(df) < 10:
        return f"{EDU_HEADER}⚠️ Unable to fetch Nifty data.\n{EDU_FOOTER}"

    close = df["Close"]
    ltp = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    rsi = calc_rsi(close)
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

    change_pct = ((ltp - prev_close) / prev_close) * 100 if prev_close else 0
    trend = "Uptrend (above 200DMA)" if ltp > ema200 else "Weak/Sideways (below 200DMA)"

    prompt = (
        "You are an educational index analyst for Indian markets.\n"
        "Do NOT give trading calls. Describe scenarios only.\n\n"
        f"Nifty levels: last closes: {list(close.round(2).tail(10))}\n"
        f"Latest: {ltp:.2f}, RSI: {rsi:.1f}, EMA20: {ema20:.2f}, EMA50: {ema50:.2f}, EMA200: {ema200:.2f}.\n"
        "Give:\n"
        "1) 2-3 bullet points on trend & momentum\n"
        "2) 2 key support/pressure zones (approx)\n"
        "3) Stance for intraday traders and positional swing traders.\n"
        "Keep it short and educational."
    )
    ai_txt = ai_call(prompt, max_tokens=300)[:1500]

    return (
        f"{EDU_HEADER}"
        "📈 **NIFTY VIEW (EDUCATIONAL)**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"LTP: {ltp:.2f} ({change_pct:+.2f}% vs prev close)\n"
        f"Trend: {trend}\n"
        f"RSI: {rsi:.1f} | EMA20: {ema20:.2f} | EMA50: {ema50:.2f} | EMA200: {ema200:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 AI Summary:\n{ai_txt}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{EDU_FOOTER}"
    )


def banknifty_view() -> str:
    df = safe_history("^NSEBANK", period="1mo")
    if df is None or len(df) < 10:
        return f"{EDU_HEADER}⚠️ Unable to fetch Bank Nifty data.\n{EDU_FOOTER}"

    close = df["Close"]
    ltp = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    rsi = calc_rsi(close)
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

    change_pct = ((ltp - prev_close) / prev_close) * 100 if prev_close else 0
    trend = "Uptrend (above 200DMA)" if ltp > ema200 else "Weak/Sideways (below 200DMA)"

    prompt = (
        "You are an educational index analyst for Indian markets.\n"
        "Do NOT give trading calls. Describe scenarios only.\n\n"
        f"Bank Nifty levels: last closes: {list(close.round(2).tail(10))}\n"
        f"Latest: {ltp:.2f}, RSI: {rsi:.1f}, EMA20: {ema20:.2f}, EMA50: {ema50:.2f}, EMA200: {ema200:.2f}.\n"
        "Give:\n"
        "1) 2-3 bullet points on trend & volatility\n"
        "2) 2 key zones where bulls/bears may react\n"
        "3) Stance for option buyers vs option sellers (educational only).\n"
        "Keep it short and educational."
    )
    ai_txt = ai_call(prompt, max_tokens=300)[:1500]

    return (
        f"{EDU_HEADER}"
        "🏦 **BANK NIFTY VIEW (EDUCATIONAL)**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"LTP: {ltp:.2f} ({change_pct:+.2f}% vs prev close)\n"
        f"Trend: {trend}\n"
        f"RSI: {rsi:.1f} | EMA20: {ema20:.2f} | EMA50: {ema50:.2f} | EMA200: {ema200:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 AI Summary:\n{ai_txt}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{EDU_FOOTER}"
    )


def index_strategies_view() -> str:
    return (
        f"{EDU_HEADER}"
        "🧭 **INDEX STRATEGIES (NIFTY / BANK NIFTY – EDUCATIONAL)**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 For *trend following* traders:\n"
        "- Look for price holding above 200DMA with RSI 45–60.\n"
        "- Use pullbacks towards EMA20/EMA50 as reference zones, not exact levels.\n\n"
        "📌 For *range traders*:\n"
        "- Identify recent swing high/low on daily chart as outer range.\n"
        "- Consider neutral option structures (Iron Condor) only when IV is elevated.\n\n"
        "📌 For *investors* using index funds:\n"
        "- Use larger corrections (10–20% off highs) for staggered SIP/top-up.\n"
        "- Focus on time in market, not perfect entry.\n\n"
        "All points are for learning purposes only; always use your own judgment.\n"
        f"{EDU_FOOTER}"
    )


# =========================
# 7. PORTFOLIO SCANNER
# =========================

def portfolio_scanner() -> str:
    large_caps = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "ITC"]
    mid_caps = ["PERSISTENT", "MOTHERSON", "TRENT", "AUBANK", "TATACOMM"]
    small_caps = ["TANLA", "SUZLON", "HEG", "JINDALSTEL", "DCMSHRIRAM"]

    def scan(list_syms):
        picks = []
        for sym in list_syms:
            try:
                df = safe_history(f"{sym}.NS", period="200d")
                if df is None or len(df) < 50:
                    continue
                c = df["Close"]
                ltp = float(c.iloc[-1])
                rsi = calc_rsi(c)
                ema200 = float(c.ewm(span=200, adjust=False).mean().iloc[-1])
                info = yf.Ticker(f"{sym}.NS").info
                pe = info.get("trailingPE") or info.get("forwardPE") or 0
                roe = (info.get("returnOnEquity") or 0) * 100
                score = quality_score(ltp, ema200, rsi, pe, roe)
                picks.append((sym, ltp, score))
            except Exception:
                continue
        picks.sort(key=lambda x: x[2], reverse=True)
        return picks[:3]

    lc = scan(large_caps)
    mc = scan(mid_caps)
    sc = scan(small_caps)

    if not lc and not mc and not sc:
        return f"{EDU_HEADER}⚠️ No qualifying stocks found; market may be sideways/choppy.\n{EDU_FOOTER}"

    txt = f"{EDU_HEADER}"
    txt += "💎 **PORTFOLIO SCANNER (EDU)**\n"
    txt += "Suggested allocation: Large 60% | Mid 30% | Small 10%\n"
    txt += "━━━━━━━━━━━━━━━━━━━━\n"

    txt += "🏢 **LARGE CAPS (60%)**\n"
    if lc:
        for s, l, q in lc:
            txt += f"- {s}: LTP ₹{l:.2f} | Quality {q}/100\n"
    else:
        txt += "- No strong large caps.\n"

    txt += "\n🏫 **MID CAPS (30%)**\n"
    if mc:
        for s, l, q in mc:
            txt += f"- {s}: LTP ₹{l:.2f} | Quality {q}/100\n"
    else:
        txt += "- No strong mid caps.\n"

    txt += "\n🚗 **SMALL CAPS (10%)**\n"
    if sc:
        for s, l, q in sc:
            txt += f"- {s}: LTP ₹{l:.2f} | Quality {q}/100\n"
    else:
        txt += "- No strong small caps.\n"

    txt += "\n━━━━━━━━━━━━━━━━━━━━\n"
    txt += f"{EDU_FOOTER}"
    return txt


# =========================
# 8. OPTION STRATEGIES (EDU)
# =========================

def option_strategies_text() -> str:
    return (
        f"{EDU_HEADER}"
        "🛡️ **OPTION STRATEGIES (EDUCATIONAL)**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "1️⃣ Bull Call Spread:\n"
        "- Buy ATM/ITM Call\n"
        "- Sell higher OTM Call\n"
        "- Limited risk, limited reward, bullish view.\n\n"
        "2️⃣ Iron Condor:\n"
        "- Sell OTM Call + Buy further OTM Call\n"
        "- Sell OTM Put + Buy further OTM Put\n"
        "- Range-bound market, limited risk.\n\n"
        "3️⃣ Long Straddle:\n"
        "- Buy ATM Call + Buy ATM Put\n"
        "- Expect big move either side, high premium.\n\n"
        "4️⃣ Protective Put:\n"
        "- Hold equity, buy OTM Put as insurance.\n"
        "- Limits downside, keeps upside open.\n\n"
        "Always manage position size and risk. Options are high risk.\n"
        f"{EDU_FOOTER}"
    )


# =========================
# 9. TELEGRAM HANDLERS
# =========================

@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📊 Stock Analysis", "🇮🇳 Market Analysis")
    kb.add("💎 Portfolio Scanner", "🛡️ Option Strategies")
    kb.add("📈 Nifty View", "🏦 Bank Nifty View")
    kb.add("🧭 Index Strategies")
    bot.send_message(
        m.chat.id,
        "👑 *AI Stock Advisory Bot*\n\n"
        "Select an option or type an NSE symbol (e.g. RELIANCE, TCS, HDFCBANK).\n"
        "For indices, use Nifty/Bank Nifty buttons.",
        reply_markup=kb,
    )


@bot.message_handler(func=lambda msg: msg.text == "📊 Stock Analysis")
def menu_stock(m):
    msg = bot.send_message(
        m.chat.id, "Send NSE symbol or company name (e.g. RELIANCE, TCS, HDFCBANK)."
    )
    bot.register_next_step_handler(msg, handle_stock_symbol)


def handle_stock_symbol(m):
    sym = m.text.strip()
    bot.send_chat_action(m.chat.id, "typing")
    bot.send_message(m.chat.id, deep_stock_analysis(sym))


@bot.message_handler(func=lambda msg: msg.text == "🇮🇳 Market Analysis")
def menu_market(m):
    bot.send_chat_action(m.chat.id, "typing")
    bot.send_message(m.chat.id, nifty_view() + "\n\n" + banknifty_view())


@bot.message_handler(func=lambda msg: msg.text == "💎 Portfolio Scanner")
def menu_portfolio(m):
    bot.send_chat_action(m.chat.id, "typing")
    bot.send_message(m.chat.id, portfolio_scanner())


@bot.message_handler(func=lambda msg: msg.text == "🛡️ Option Strategies")
def menu_options(m):
    bot.send_chat_action(m.chat.id, "typing")
    bot.send_message(m.chat.id, option_strategies_text())


@bot.message_handler(func=lambda msg: msg.text == "📈 Nifty View")
def menu_nifty(m):
    bot.send_chat_action(m.chat.id, "typing")
    bot.send_message(m.chat.id, nifty_view())


@bot.message_handler(func=lambda msg: msg.text == "🏦 Bank Nifty View")
def menu_banknifty(m):
    bot.send_chat_action(m.chat.id, "typing")
    bot.send_message(m.chat.id, banknifty_view())


@bot.message_handler(func=lambda msg: msg.text == "🧭 Index Strategies")
def menu_index_strat(m):
    bot.send_chat_action(m.chat.id, "typing")
    bot.send_message(m.chat.id, index_strategies_view())


@bot.message_handler(func=lambda m: True)
def fallback_symbol(m):
    text = m.text.strip()
    m_full = re.match(r"FULL\s+([A-Za-z ]{3,20})", text.upper())
    if m_full:
        sym = m_full.group(1).strip()
        bot.send_chat_action(m.chat.id, "typing")
        bot.send_message(m.chat.id, deep_stock_analysis(sym))
        return

    if re.fullmatch(r"[A-Za-z ]{3,20}", text):
        bot.send_chat_action(m.chat.id, "typing")
        bot.send_message(m.chat.id, deep_stock_analysis(text))
    else:
        bot.send_message(
            m.chat.id,
            "I did not understand.\n"
            "Use menu or send NSE symbol (e.g. RELIANCE, TCS).\n"
            "For full report, send: `FULL RELIANCE`",
        )


# =========================
# 10. HEALTH SERVER FOR RENDER
# =========================

def run_health_server():
    port = int(os.environ.get("PORT", 10000))

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bot is running")

    TCPServer.allow_reuse_address = True
    with TCPServer(("0.0.0.0", port), Handler) as httpd:
        httpd.serve_forever()


# =========================
# 11. MAIN
# =========================

if __name__ == "__main__":
    print("🤖 AI Stock Advisory Bot starting...")
    threading.Thread(target=run_health_server, daemon=True).start()
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            print("Polling error, restarting in 5s:", repr(e))
            time.sleep(5)
