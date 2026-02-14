import os
import time
import json
import re
from datetime import datetime, timedelta

import telebot
from telebot import types
import yfinance as yf
import pandas as pd
import numpy as np
import requests

# Optional AI providers
try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    import google.generativeai as genai
except ImportError:
    genai = None

# --- 1. CONFIG & ENV ---

TELEGRAM_TOKEN = os.getenv("8461087780:AAE4l58egcDN7LRbqXAp7x7x0nkfX6jTGEc")
GROQ_API_KEY = os.getenv("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")
GEMINI_API_KEY = os.getenv("AIzaSyCPh8wPC-rmBIyTr5FfV3Mwjb33KeZdRUE")


if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set in environment.")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")

# Multi‑provider AI flags
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY and Groq else None
if GEMINI_API_KEY and genai:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel("gemini-1.5-flash")
else:
    gemini_model = None

HF_MODEL = "mistralai/Mistral-7B-Instruct-v0.2"  # simple text model


# --- 2. COMMON MARKET HELPERS ---


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
    # trend
    if ltp > ema200:
        score += 30
    # momentum
    if 45 <= rsi <= 60:
        score += 25
    elif 40 <= rsi < 45 or 60 < rsi <= 70:
        score += 10
    # valuation
    if pe and pe > 0:
        if pe < 15:
            score += 15
        elif 15 <= pe <= 25:
            score += 8
    # quality
    if roe and roe > 0:
        if roe >= 18:
            score += 20
        elif 12 <= roe < 18:
            score += 10
    return max(0, min(score, 100))


# --- 3. AI LAYER WITH FAILOVER ---


def ai_call(prompt: str, max_tokens: int = 600) -> str:
    """Multi-provider AI with failover: GROQ → Gemini → HuggingFace → fallback text."""
    # 1) GROQ
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

    # 2) Gemini
    if gemini_model:
        try:
            out = gemini_model.generate_content(prompt)
            return out.text.strip()
        except Exception as e:
            print("Gemini error:", repr(e))

    # 3) HuggingFace (simple text generation)
    if HUGGINGFACE_TOKEN:
        try:
            url = f"https://api-inference.huggingface.co/models/{HF_MODEL}"
            headers = {"Authorization": f"Bearer {HUGGINGFACE_TOKEN}"}
            payload = {"inputs": prompt, "parameters": {"max_new_tokens": max_tokens}}
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data:
                txt = data[0].get("generated_text", "")
                return txt.strip()
        except Exception as e:
            print("HF error:", repr(e))

    # 4) Fallback
    return (
        "AI providers not available. Using mathematical and rule-based analysis only. "
        "Consider trend (price vs. 200DMA), RSI, valuation, and sector view before taking any decision."
    )


# --- 4. DEEP STOCK ANALYSIS ---


def deep_stock_analysis(symbol: str) -> str:
    sym = symbol.upper().strip()
    ticker = f"{sym}.NS"

    df = safe_history(ticker, period="1y", interval="1d")
    if df is None:
        return f"❌ Could not fetch data for `{sym}`. Check the NSE symbol."

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

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
        f"Stock: {name} ({sym})\n"
        f"LTP: {ltp:.2f}, RSI: {rsi:.1f}, MACD: {macd:.2f}, MACD_signal: {macd_signal:.2f},\n"
        f"PE: {pe}, PB: {pb}, ROE: {roe:.1f}%, DivYield: {div_yield:.2f}%.\n"
        "Generate:\n"
        "1) 3 bullish points\n"
        "2) 3 bearish points\n"
        "3) 1-line sentiment (Strong Buy / Buy / Hold / Avoid / Sell)\n"
        "Format:\n"
        "Bullish:\n- ...\nBearish:\n- ...\nSentiment: ...\n"
    )
    sentiment_text = ai_call(sentiment_prompt, max_tokens=400)

    if "Sentiment:" in sentiment_text:
        sentiment_line = sentiment_text.split("Sentiment:")[-1].strip().splitlines()[0]
    else:
        if qi >= 75:
            sentiment_line = "Strong Buy (High quality and trend)."
        elif qi >= 55:
            sentiment_line = "Buy / Accumulate on dips."
        elif qi >= 40:
            sentiment_line = "Hold / Wait for better entries."
        else:
            sentiment_line = "Avoid / High risk."

    return (
        f"📊 **DEEP ANALYSIS: {sym}**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 *{name}* | Sector: {sector}\n"
        f"💰 **LTP:** ₹{ltp:.2f} (Prev: ₹{prev_close:.2f})\n"
        f"📈 52W High: ₹{df['High'].max():.2f} | 52W Low: ₹{df['Low'].min():.2f}\n"
        f"🏦 MCap: {mcap/1e7:.1f} Cr | P/E: {pe:.2f} | P/B: {pb:.2f} | ROE: {roe:.1f}% | Div: {div_yield:.2f}%\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **Technicals**\n"
        f"RSI: {rsi:.1f} | MACD: {macd:.2f} vs Signal: {macd_signal:.2f}\n"
        f"BB: U {bb_u:.2f} | M {bb_m:.2f} | L {bb_l:.2f}\n"
        f"EMA20: {ema20:.2f} | EMA50: {ema50:.2f} | EMA200: {ema200:.2f}\n"
        f"ATR(14): {atr:.2f}\n"
        f"Pivots: PP {pp:.2f} | R1 {r1:.2f} | R2 {r2:.2f} | S1 {s1:.2f} | S2 {s2:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 **Targets & Risk**\n"
        f"Short-term (1W / 1M / 3M): "
        f"₹{st_1w:.2f} / ₹{st_1m:.2f} / ₹{st_3m:.2f}\n"
        f"Long-term (6M / 1Y / 2Y): "
        f"₹{lt_6m:.2f} / ₹{lt_1y:.2f} / ₹{lt_2y:.2f}\n"
        f"Stop Loss (swing): ₹{sl:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Quality Score:** {qi}/100\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 **AI Sentiment & Factors**\n{sentiment_text}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Final View: {sentiment_line}\n"
        "⚠️ Educational only. Not SEBI registered.\n"
    )


# --- 5. MARKET ANALYSIS ---


def market_analysis() -> str:
    nifty = safe_history("^NSEI", period="5d")
    bank = safe_history("^NSEBANK", period="5d")
    if nifty is None or bank is None:
        return "⚠️ Unable to fetch Nifty/BankNifty data."

    nltp = float(nifty["Close"].iloc[-1])
    bltp = float(bank["Close"].iloc[-1])

    # Breadth approximation via NIFTYBEES & banking ETF if needed
    breadth_text = "Breadth data not fully available via free APIs; using index action instead."

    prompt = (
        f"Nifty: {nltp:.2f} last 5 closes: {list(nifty['Close'].round(2).tail(5))}\n"
        f"BankNifty: {bltp:.2f} last 5 closes: {list(bank['Close'].round(2).tail(5))}\n"
        "Give:\n"
        "1) Short market outlook for India (1-3 days)\n"
        "2) Trading stance (BTST, intraday, wait & watch)\n"
        "3) Key risk factors.\n"
        "Be concise, bullet points."
    )
    outlook = ai_call(prompt, max_tokens=350)

    return (
        "🇮🇳 **INDIAN MARKET ANALYSIS**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Nifty 50: {nltp:.2f}\n"
        f"Bank Nifty: {bltp:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Market Breadth: {breadth_text}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 AI Outlook:\n{outlook}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ Educational view only.\n"
    )


# --- 6. PORTFOLIO SCANNER ---


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
        return "⚠️ No qualifying stocks found. Market might be sideways/choppy."

    txt = "💎 **PORTFOLIO SCANNER (EDU)**\n"
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
    txt += "Use as a starting universe only. Not investment advice.\n"
    return txt


# --- 7. OPTION STRATEGIES (EDUCATIONAL) ---


def option_strategies_text() -> str:
    return (
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
        "3️⃣ Straddle (Long):\n"
        "- Buy ATM Call + Buy ATM Put\n"
        "- Expect big move either side, high premium cost.\n\n"
        "4️⃣ Protective Put:\n"
        "- Hold equity, buy OTM Put as insurance.\n"
        "- Limits downside, keeps upside open.\n\n"
        "Always manage position size and risk. Options are high risk.\n"
    )


# --- 8. TELEGRAM HANDLERS ---


@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📊 Stock Analysis", "🇮🇳 Market Analysis")
    kb.add("💎 Portfolio Scanner", "🛡️ Option Strategies")
    bot.send_message(
        m.chat.id,
        "👑 *AI Stock Advisory Bot*\n\n"
        "Select an option or type a stock symbol (e.g. RELIANCE, TCS, HDFCBANK).",
        reply_markup=kb,
    )


@bot.message_handler(func=lambda msg: msg.text == "📊 Stock Analysis")
def menu_stock(m):
    msg = bot.send_message(m.chat.id, "Send NSE symbol or company name (e.g. RELIANCE, TCS, HDFCBANK).")
    bot.register_next_step_handler(msg, handle_stock_symbol)


def handle_stock_symbol(m):
    sym = m.text.strip()
    bot.send_chat_action(m.chat.id, "typing")
    bot.send_message(m.chat.id, deep_stock_analysis(sym))


@bot.message_handler(func=lambda msg: msg.text == "🇮🇳 Market Analysis")
def menu_market(m):
    bot.send_chat_action(m.chat.id, "typing")
    bot.send_message(m.chat.id, market_analysis())


@bot.message_handler(func=lambda msg: msg.text == "💎 Portfolio Scanner")
def menu_portfolio(m):
    bot.send_chat_action(m.chat.id, "typing")
    bot.send_message(m.chat.id, portfolio_scanner())


@bot.message_handler(func=lambda msg: msg.text == "🛡️ Option Strategies")
def menu_options(m):
    bot.send_chat_action(m.chat.id, "typing")
    bot.send_message(m.chat.id, option_strategies_text())


@bot.message_handler(func=lambda m: True)
def fallback_symbol(m):
    """Direct symbol query like RELIANCE, TCS, etc."""
    text = m.text.strip()
    if re.fullmatch(r"[A-Za-z]{3,10}", text.replace(" ", "")):
        bot.send_chat_action(m.chat.id, "typing")
        bot.send_message(m.chat.id, deep_stock_analysis(text))
    else:
        bot.send_message(
            m.chat.id,
            "I did not understand. Use menu or send NSE symbol (e.g. RELIANCE, TCS).",
        )


# --- 9. MAIN LOOP ---


if __name__ == "__main__":
    print("🤖 AI Stock Advisory Bot starting...")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            print("Polling error, restarting in 5s:", repr(e))
            time.sleep(5)
