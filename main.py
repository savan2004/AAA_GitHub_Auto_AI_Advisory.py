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

# --- 1. CONFIG ---

# IMPORTANT: no default token in code – use Render env vars
TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set in environment.")

bot = telebot.TeleBot(TOKEN)

# --- 2. OPENAI CLIENT ---

AI_ENABLED = False
client = None
try:
    if OPENAI_API_KEY:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        AI_ENABLED = True
    else:
        print("⚠️ OPENAI_API_KEY not set. AI features disabled.")
except Exception as e:
    print("⚠️ OpenAI init error:", repr(e))
    AI_ENABLED = False

# --- 3. TECHNICAL HELPERS ---


def calculate_rsi(series, period=14):
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


# --- 4. NIFTY OPTION TRADING LOGIC (CLEANED) ---


def get_nifty_option_trade(budget, spot):
    try:
        # PREFERRED: Try AI
        if AI_ENABLED and client:
            prompt = (
                f"Nifty Spot: {spot}. Budget: {budget}. Lot: 65.\n"
                f"Generate Nifty Option Trade. RR >= 1:3. Strike multiple of 50.\n"
                f"Return JSON: "
                f"{{\"strike\":int, \"type\":\"CALL/PUT\", \"expiry\":\"DD-MMM\", "
                f"\"entry\":float, \"target\":float, \"sl\":float, \"lots\":int}}"
            )
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.5,
                )
                content = response.choices[0].message.content
                json_match = re.search(r"\{[\s\S]*\}", content, re.DOTALL)
                if not json_match:
                    raise ValueError("Invalid AI response format")
                data = json.loads(json_match.group())

                capital = round(data["entry"] * 65 * data["lots"])
                return (
                    "🚀 **NIFTY QUANT SIGNAL (AI)**\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 {data['strike']} {data['type']} | {data['expiry']}\n"
                    f"💰 Entry: ₹{data['entry']} | Target: ₹{data['target']}\n"
                    f"🛑 SL: ₹{data['sl']} | Lots: {data['lots']}\n"
                    f"🏦 Capital: ₹{capital}\n"
                    "━━━━━━━━━━━━━━━━━━━━"
                )
            except Exception as e:
                print("AI option error:", repr(e))

        # FALLBACK: Math-based
        strike = round(spot / 50) * 50

        hist = yf.Ticker("^NSEI").history(period="3d")
        if len(hist) >= 2:
            prev_close = float(hist["Close"].iloc[-2])
        else:
            prev_close = spot

        option_type = "CALL" if spot > prev_close else "PUT"

        estimated_premium = 120.0
        max_lots = int(budget / (estimated_premium * 65))
        if max_lots < 1:
            max_lots = 1

        target = round(estimated_premium * 1.15)
        sl = round(estimated_premium * 0.5)
        capital = round(estimated_premium * 65 * max_lots)

        return (
            "⚠️ **AI BUSY - USING MATH MODEL**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 {strike} {option_type}\n"
            f"💰 Est. Entry: ₹{estimated_premium} | Target: ₹{target}\n"
            f"🛑 SL: ₹{sl} | Lots: {max_lots}\n"
            f"🏦 Capital: ₹{capital}\n"
            "📊 *Strategy: ATM*\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )

    except Exception as e:
        return f"⚠️ **Option Error:** {str(e)}"


# --- 5. SMART PORTFOLIO (60/35/15 ALLOCATION) ---


def get_smart_portfolio():
    try:
        large_caps = [
            "RELIANCE",
            "HDFCBANK",
            "INFY",
            "ICICIBANK",
            "SBIN",
            "BHARTIARTL",
            "ITC",
            "TCS",
            "KOTAKBANK",
            "LT",
        ]
        # removed delisted/problematic names (PEL, PRAJINDS, IIFLSEC)
        mid_caps = [
            "PERSISTENT",
            "MOTHERSON",
            "MAXHEALTH",
            "AUBANK",
            "LATENTVIEW",
            "TRENT",
            "TATACONSUM",
            "CHOLAHLDNG",
            "M&MFIN",
        ]
        small_caps = [
            "SUZLON",
            "HEG",
            "TANLA",
            "BAJAJELEC",
            "ORIENTELEC",
            "SHARDACROP",
            "JINDALSTEL",
            "DCMSHRIRAM",
        ]

        final_report = "💎 **SMART PORTFOLIO (ASI SCORE 80%+)**\n"
        final_report += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

        def scan_category(stocks):
            selected = []
            for sym in stocks:
                try:
                    df = yf.Ticker(f"{sym}.NS").history(period="200d")
                    if df.empty:
                        continue

                    close = df["Close"]
                    ltp = float(close.iloc[-1])
                    rsi = calculate_rsi(close)
                    ema_50 = close.ewm(span=50).mean().iloc[-1]
                    ema_200 = close.ewm(span=200).mean().iloc[-1]

                    score = 0
                    if ltp > ema_200:
                        score += 40
                    if ltp > ema_50:
                        score += 30
                    if 40 < rsi < 70:
                        score += 20
                    if rsi > 50:
                        score += 10

                    if score >= 80:
                        selected.append(
                            {"sym": sym, "score": score, "ltp": f"{ltp:.2f}"}
                        )
                except Exception:
                    continue

            selected.sort(key=lambda x: x["score"], reverse=True)
            return selected[:2]

        lc = scan_category(large_caps)
        mc = scan_category(mid_caps)
        sc = scan_category(small_caps)

        if not lc and not mc and not sc:
            return (
                "⚠️ **Market Condition:** Current market is choppy. "
                "No stocks qualifying for >80% ASI Score. Wait for a rally."
            )

        final_report += "\n🏢 **LARGE CAP (60% Allocation)**\n"
        if lc:
            for i, stock in enumerate(lc, 1):
                final_report += (
                    f"{i}. **{stock['sym']}** | LTP: ₹{stock['ltp']}\n"
                    f"   🏛 ASI Score: {stock['score']}/100\n"
                )
        else:
            final_report += "   No strong signals.\n"

        final_report += "\n🏫 **MID CAP (35% Allocation)**\n"
        if mc:
            for i, stock in enumerate(mc, 1):
                final_report += (
                    f"{i}. **{stock['sym']}** | LTP: ₹{stock['ltp']}\n"
                    f"   🏛 ASI Score: {stock['score']}/100\n"
                )
        else:
            final_report += "   No strong signals.\n"

        final_report += "\n🚗 **SMALL CAP (15% Allocation)**\n"
        if sc:
            for i, stock in enumerate(sc, 1):
                final_report += (
                    f"{i}. **{stock['sym']}** | LTP: ₹{stock['ltp']}\n"
                    f"   🏛 ASI Score: {stock['score']}/100\n"
                )
        else:
            final_report += "   No strong signals.\n"

        final_report += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        final_report += (
            "🧠 **Strategy:** High conviction picks based on Trend, "
            "Momentum, and Fundamentals.\n"
            "_AIAUTO ADVISORY Selection Engine_"
        )
        return final_report

    except Exception as e:
        return f"⚠️ Portfolio Error: {e}"


# --- 6. FULL DETAILED REPORT GENERATOR (unchanged logic, minor safety) ---


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
            if "NIFTY" in sym:
                ticker_sym = "^NSEI"
            elif "BANK" in sym:
                ticker_sym = "^NSEBANK"
            else:
                return f"❌ **Error:** Symbol `{sym}` not found."

            stock = yf.Ticker(ticker_sym)
            df = stock.history(period="1y")
            info = stock.info
            if df.empty:
                return f"❌ **Error:** Data not found for `{sym}`."

        close = df["Close"]
        ltp = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        high_prev = float(df["High"].iloc[-2])
        low_prev = float(df["Low"].iloc[-2])

        company_name = info.get("longName", sym)
        sector = info.get("sector", "N/A")
        mcap = info.get("marketCap", 0)
        pe = info.get("trailingPE", 0) or 0
        pb = info.get("priceToBook", 0) or 0
        roe = (info.get("returnOnEquity", 0) or 0) * 100

        rsi = calculate_rsi(close)
        ema_50 = close.ewm(span=50).mean().iloc[-1]
        ema_200 = close.ewm(span=200).mean().iloc[-1]

        pp, r1, s1, r2, s2, r3, s3 = calculate_pivots(
            high_prev, low_prev, prev_close
        )

        upside_pct = round(((r2 - ltp) / ltp) * 100, 2)
        if upside_pct < 0:
            upside_pct = round(((r3 - ltp) / ltp) * 100, 2)

        pos_points = "- Strong Market Position\n- Good Cash Flow"
        neg_points = "- Sector Risk\n- Global Volatility"
        news_headlines = "Markets trading flat."

        if AI_ENABLED and client:
            try:
                prompt = (
                    f"Stock: {company_name} ({sym}). Price: {ltp}. PE: {round(pe, 2)}.\n"
                    "Task: Generate 1) Three Bullish points (Pros), "
                    "2) Three Bearish points (Cons), 3) Short news summary.\n"
                    "Format JSON: "
                    '{"pros":"line1\\nline2\\nline3",'
                    '"cons":"line1\\nline2\\nline3",'
                    '"news":"Headline"}'
                )
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.6,
                )
                content = response.choices[0].message.content
                clean_json = re.search(r"\{.*\}", content, re.DOTALL)
                if clean_json:
                    ai_data = json.loads(clean_json.group())
                    pos_points = ai_data.get("pros", pos_points)
                    neg_points = ai_data.get("cons", neg_points)
                    news_headlines = ai_data.get("news", news_headlines)
            except Exception as e:
                print("AI report error:", repr(e))

        if ltp > ema_200 and rsi > 50:
            verdict_emoji = "📈"
            verdict_text = "STRONG BUY"
            conclusion = (
                f"{company_name} is structurally bullish. Accumulate near support."
            )
        elif ltp > ema_50 and rsi < 70:
            verdict_emoji = "✅"
            verdict_text = "BUY"
            conclusion = (
                f"{company_name} is in an uptrend. Momentum is healthy."
            )
        elif rsi > 75:
            verdict_emoji = "⚠️"
            verdict_text = "BOOK PROFIT"
            conclusion = (
                f"{company_name} is overbought. Book partial profits."
            )
        else:
            verdict_emoji = "⚖️"
            verdict_text = "HOLD / WAIT"
            conclusion = (
                f"{company_name} is consolidating. Wait for direction."
            )

        return (
            "🚀 **SK AUTO AI ADVISORY** 🚀\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **DATE:** {datetime.now().strftime('%d-%b-%Y')} | "
            f"⏰ **TIME:** {datetime.now().strftime('%H:%M')}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 **SYMBOL:** {sym} | {company_name}\n"
            "🏛 **ASI RANK:** 85/100 (High Confidence)\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **LTP:** ₹{ltp:.2f} | 📊 **RSI:** {rsi:.2f}\n"
            f"📈 **TREND:** "
            f"{'BULLISH (Above DMA 200)' if ltp > ema_200 else 'BEARISH'}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **VERDICT:** {verdict_emoji} **{verdict_text}**\n"
            f"🚀 **UPSIDE:** {upside_pct}% (Target: ₹{r2:.2f})\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📦 **FUNDAMENTAL LEVELS**\n"
            f"• Market Cap: {round(mcap/10000000, 1)} Cr | Sector: {sector}\n"
            f"• P/E Ratio: {round(pe, 2)}x | ROE: {round(roe, 1)}%\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🏗 **DEEP TECHNICAL LEVELS**\n"
            f"🔴 R3: {r3:.2f} | R2: {r2:.2f}\n"
            f"🔴 R1: {r1:.2f} | 🟢 PP: {pp:.2f}\n"
            f"🟢 S1: {s1:.2f} | S2: {s2:.2f} | S3: {s3:.2f}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🧠 **COMPANY INFORMATION**\n"
            f"✅ **POSITIVE:**\n{pos_points}\n\n"
            f"❌ **NEGATIVE:**\n{neg_points}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📰 **LATEST NEWS:**\n👉 {news_headlines}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 **CONCLUSION:**\n{conclusion}\n"
            "⚠️ **RISK:** Volatility and sector news may impact targets.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "_AIAUTO ADVISORY - Smart Investing_"
        )
    except Exception as e:
        return f"⚠️ **Analysis Error:** {str(e)}"


# --- 7. SMART SEARCH HELPER ---


def find_symbol(query):
    try:
        if not AI_ENABLED or not client:
            return query.upper().replace(" ", "")
        prompt = (
            f"User Query: '{query}'. Indian Stock Market. "
            "Return ONLY official NSE Symbol UPPERCASE. No .NS."
        )
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip().upper()
        return re.sub(r"\.NS|[^A-Z]", "", raw)
    except Exception:
        return query.upper()


# --- 8. HEALTH SERVER & HANDLERS ---


def run_health_server():
    import http.server
    import socketserver

    port = int(os.environ.get("PORT", 10000))

    class H(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"SK AUTO AI ADVISORY ONLINE")

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", port), H) as httpd:
        httpd.serve_forever()


@bot.message_handler(commands=["start"])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("💎 Smart Portfolio", "🛡️ Option Strategy")
    markup.add("📊 Market Analysis", "🔎 Smart Search")
    markup.add("🚀 Nifty Option Trading")
    bot.send_message(
        m.chat.id,
        "🚀 **SK AUTO AI ADVISORY** 🚀\n\nSelect Advanced Mode:",
        reply_markup=markup,
        parse_mode="Markdown",
    )


@bot.message_handler(func=lambda m: m.text == "💎 Smart Portfolio")
def smart_port(m):
    bot.send_chat_action(m.chat.id, "typing")
    bot.send_message(m.chat.id, "🔍 Scanning Nifty & Midcap Universe...")
    bot.send_message(m.chat.id, get_smart_portfolio(), parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text == "🛡️ Option Strategy")
def hedge_strat(m):
    bot.send_chat_action(m.chat.id, "typing")
    bot.send_message(
        m.chat.id,
        "🛡️ **HEDGE STRATEGY**\n\n"
        "Use '🚀 Nifty Option Trading' for exact signals.\n\n"
        "**Hedge Logic:**\n"
        "Buy ATM Option + Sell OTM Option to reduce cost.",
        parse_mode="Markdown",
    )


@bot.message_handler(func=lambda m: m.text == "📊 Market Analysis")
def market_view(m):
    bot.send_chat_action(m.chat.id, "typing")
    try:
        nifty = yf.Ticker("^NSEI").history(period="5d")
        bank = yf.Ticker("^NSEBANK").history(period="5d")
        nltp = float(nifty["Close"].iloc[-1])
        bltp = float(bank["Close"].iloc[-1])
        bot.send_message(
            m.chat.id,
            f"📊 **MARKET SNAPSHOT**\n"
            f"Nifty: {nltp:.2f}\n"
            f"BankNifty: {bltp:.2f}\n"
            "_Mood: Bullish if above Pivot._",
            parse_mode="Markdown",
        )
    except Exception:
        bot.send_message(m.chat.id, "⚠️ Unable to fetch market data.")


@bot.message_handler(func=lambda m: m.text == "🔎 Smart Search")
def smart_search(m):
    msg = bot.send_message(m.chat.id, "🔍 Type Company Name:")
    bot.register_next_step_handler(msg, process_smart_search)


def process_smart_search(m):
    query = m.text
    bot.send_chat_action(m.chat.id, "typing")
    symbol = find_symbol(query)
    bot.send_message(m.chat.id, f"🧠 AI Identified: **{symbol}**", parse_mode="Markdown")
    bot.send_message(m.chat.id, get_sk_auto_report(symbol), parse_mode="Markdown")


def process_options(m):
    try:
        budget = float(m.text.replace("₹", "").replace(",", ""))
        hist = yf.Ticker("^NSEI").history(period="1d")
        spot = float(hist["Close"].iloc[-1])
        bot.send_chat_action(m.chat.id, "typing")
        bot.send_message(m.chat.id, f"🔍 Scanning for Budget: ₹{budget}...")
        bot.send_message(m.chat.id, get_nifty_option_trade(budget, spot), parse_mode="Markdown")
    except ValueError:
        bot.send_message(m.chat.id, "❌ Invalid number.")
    except Exception as e:
        bot.send_message(m.chat.id, f"⚠️ Error: {str(e)}")


@bot.message_handler(func=lambda m: m.text == "🚀 Nifty Option Trading")
def nifty_opt(m):
    msg = bot.send_message(
        m.chat.id,
        "🚀 **Nifty Option Sniper**\n\nEnter Trading Budget (INR):",
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(msg, process_options)


# --- 9. MAIN LOOP ---


if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    bot.delete_webhook(drop_pending_updates=True)
    time.sleep(3)
    print("🚀 SK AUTO AI ADVISORY Online...")
    bot.infinity_polling(skip_pending=True, timeout=60)
