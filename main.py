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

TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-your-openai-key-here")

bot = telebot.TeleBot(TOKEN)

# --- 2. OPENAI CLIENT ---

try:
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    AI_ENABLED = True
except Exception:
    AI_ENABLED = False
    print("⚠️ OpenAI Disabled.")

# --- 3. TECHNICAL HELPERS ---


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs)).iloc[-1]


def calculate_pivots(high, low, close):
    pp = (high + low + close) / 3
    r1 = (2 * pp) - low
    s1 = (2 * pp) - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2 * (pp - low)
    s3 = low - 2 * (high - pp)
    return pp, r1, s1, r2, s2, r3, s3


def compute_asi_score(ltp, ema_50, ema_200, rsi, pe, roe, upside_pct):
    """Rule-based ASI score 0–100."""
    score = 0

    # Trend
    if ltp > ema_200:
        score += 30
    if ltp > ema_50:
        score += 20

    # Momentum
    if 45 <= rsi <= 60:
        score += 15
    elif 40 <= rsi < 45 or 60 < rsi <= 70:
        score += 10

    # Valuation (very rough bands)
    if pe and pe > 0:
        if pe < 15:
            score += 10
        elif 15 <= pe <= 30:
            score += 5

    # Profitability
    if roe and roe > 0:
        if roe >= 15:
            score += 10
        elif 10 <= roe < 15:
            score += 5

    # Risk‑reward from upside to R2
    if upside_pct >= 5:
        score += 10
    elif 2 <= upside_pct < 5:
        score += 5

    return min(score, 100)


STATIC_NOTES = {
    "DLF": "DLF is a leading Indian real-estate developer with cyclical earnings and high sensitivity to the rate and property cycle.",
    "RELIANCE": "Reliance Industries is a diversified conglomerate with energy, retail and telecom (Jio) platforms.",
    "HDFCBANK": "HDFC Bank is a large private-sector bank with strong liability franchise and consistent asset quality."
}

# --- 4. NIFTY OPTION TRADING LOGIC ---


def get_nifty_option_trade(budget, spot):
    try:
        # PREFERRED: Try AI for precise trade
        if AI_ENABLED:
            prompt = (
                f"Nifty Spot: {spot}. Budget: {budget}. Lot: 65.\n"
                f"Generate Nifty Option Trade. RR 1:3. Strike mult of 50.\n"
                f"Return JSON: {{'strike':int, 'type':'CALL/PUT', 'expiry':'DD-MMM', "
                f"'entry':float, 'target':float, 'sl':float, 'lots':int}}"
            )

            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.5
                )
                content = response.choices[0].message.content
                data = json.loads(re.search(r'\{.*\}', content, re.DOTALL).group())
                capital = round(data['entry'] * 65 * data['lots'])

                return (
                    f"🚀 **NIFTY QUANT SIGNAL (AI)**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 {data['strike']} {data['type']} | {data['expiry']}\n"
                    f"💰 Entry: ₹{data['entry']} | Target: ₹{data['target']}\n"
                    f"🛑 SL: ₹{data['sl']} | Lots: {data['lots']}\n"
                    f"🏦 Capital: ₹{capital}\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                )
            except Exception:
                pass  # fall through to math fallback

        # FALLBACK: Math-based calculation if AI fails
        strike = round(spot / 50) * 50
        prev_close = yf.Ticker("^NSEI").history(period="2d")['Close'].iloc[-2]
        option_type = "CALL" if spot > prev_close else "PUT"

        estimated_premium = 120
        max_lots = int(budget / (estimated_premium * 65))
        if max_lots < 1:
            max_lots = 1

        target = round(estimated_premium * 1.15)
        sl = round(estimated_premium * 0.5)
        capital = round(estimated_premium * 65 * max_lots)

        return (
            f"⚠️ **AI BUSY - USING MATH MODEL**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 {strike} {option_type}\n"
            f"💰 Est. Entry: ₹{estimated_premium} | Target: ₹{target}\n"
            f"🛑 SL: ₹{sl} | Lots: {max_lots}\n"
            f"🏦 Capital: ₹{capital}\n"
            f"📊 *Strategy: ATM*\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

    except Exception as e:
        return f"⚠️ **Option Error:** {str(e)}"


# --- 5. SMART PORTFOLIO (60/35/15 ALLOCATION) ---


def get_smart_portfolio():
    try:
        large_caps = [
            'RELIANCE', 'HDFCBANK', 'INFY', 'ICICIBANK', 'SBIN',
            'BHARTIARTL', 'ITC', 'TCS', 'KOTAKBANK', 'LT'
        ]
        mid_caps = [
            'PERSISTENT', 'MOTHERSON', 'MAXHEALTH', 'AUBANK', 'PEL',
            'LATENTVIEW', 'TRENT', 'TATACONSUM', 'CHOLAHLDNG', 'M&MFIN'
        ]
        small_caps = [
            'SUZLON', 'HEG', 'TANLA', 'BAJAJELEC', 'ORIENTELEC',
            'SHARDACROP', 'JINDALSTEL', 'PRAJINDS', 'DCMSHRIRAM', 'IIFLSEC'
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
                    ltp = df['Close'].iloc[-1]
                    rsi = calculate_rsi(df['Close'])
                    ema_50 = df['Close'].ewm(span=50).mean().iloc[-1]
                    ema_200 = df['Close'].ewm(span=200).mean().iloc[-1]

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
                        selected.append({
                            'sym': sym,
                            'score': score,
                            'ltp': f"{ltp:.2f}"
                        })
                except Exception:
                    continue

            selected.sort(key=lambda x: x['score'], reverse=True)
            return selected[:2]

        lc = scan_category(large_caps)
        mc = scan_category(mid_caps)
        sc = scan_category(small_caps)

        if not lc and not mc and not sc:
            return ("⚠️ **Market Condition:** Current market is choppy. "
                    "No stocks qualifying for >80% ASI Score. Wait for a rally.")

        final_report += "\n🏢 **LARGE CAP (60% Allocation)**\n"
        if lc:
            for i, stock in enumerate(lc, 1):
                final_report += f"{i}. **{stock['sym']}** | LTP: ₹{stock['ltp']}\n"
                final_report += f"   🏛 ASI Score: {stock['score']}/100\n"
        else:
            final_report += " No strong signals.\n"

        final_report += "\n🏫 **MID CAP (35% Allocation)**\n"
        if mc:
            for i, stock in enumerate(mc, 1):
                final_report += f"{i}. **{stock['sym']}** | LTP: ₹{stock['ltp']}\n"
                final_report += f"   🏛 ASI Score: {stock['score']}/100\n"
        else:
            final_report += " No strong signals.\n"

        final_report += "\n🚗 **SMALL CAP (15% Allocation)**\n"
        if sc:
            for i, stock in enumerate(sc, 1):
                final_report += f"{i}. **{stock['sym']}** | LTP: ₹{stock['ltp']}\n"
                final_report += f"   🏛 ASI Score: {stock['score']}/100\n"
        else:
            final_report += " No strong signals.\n"

        final_report += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        final_report += ("🧠 **Strategy:** High conviction picks based on Trend, "
                         "Momentum, and Fundamentals.\n")
        final_report += "_AIAUTO ADVISORY Selection Engine_"

        return final_report

    except Exception as e:
        return f"⚠️ Portfolio Error: {e}"


# --- 6. FULL DETAILED REPORT GENERATOR (DEEP ASI + RAG) ---


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

        ltp = float(df['Close'].iloc[-1])
        prev_close = float(df['Close'].iloc[-2])
        high_prev = float(df['High'].iloc[-2])
        low_prev = float(df['Low'].iloc[-2])

        company_name = info.get('longName', sym)
        sector = info.get('sector', 'N/A')
        mcap = info.get('marketCap', 0)
        pe = info.get('trailingPE', 0)
        pb = info.get('priceToBook', 0)
        roe = (info.get('returnOnEquity', 0) or 0) * 100

        rsi = calculate_rsi(df['Close'])
        ema_50 = df['Close'].ewm(span=50).mean().iloc[-1]
        ema_200 = df['Close'].ewm(span=200).mean().iloc[-1]

        pp, r1, s1, r2, s2, r3, s3 = calculate_pivots(high_prev, low_prev, prev_close)

        upside_pct = round(((r2 - ltp) / ltp) * 100, 2)
        if upside_pct < 0:
            upside_pct = round(((r3 - ltp) / ltp) * 100, 2)

        pos_points = "- Strong Market Position\n- Good Cash Flow\n- Reasonable Liquidity"
        neg_points = "- Sector Risk\n- Global Volatility\n- Macroeconomic sensitivity"
        news_headlines = "Markets trading flat."
        ai_conclusion = ""

        fundamental_context = (
            f"Company: {company_name} ({sym})\n"
            f"Sector: {sector}\n"
            f"Market Cap: {round(mcap/1e7, 1)} Cr\n"
            f"PE: {round(pe or 0, 2)}, PB: {round(pb or 0, 2)}, ROE: {round(roe or 0, 1)}%\n"
        )

        technical_context = (
            f"LTP: {ltp:.2f}\n"
            f"RSI: {rsi:.2f}\n"
            f"EMA50: {ema_50:.2f}, EMA200: {ema_200:.2f}\n"
            f"Pivots: PP={pp:.2f}, R1={r1:.2f}, R2={r2:.2f}, R3={r3:.2f}, "
            f"S1={s1:.2f}, S2={s2:.2f}, S3={s3:.2f}\n"
            f"Upside to R2: {upside_pct}%\n"
        )

        extra_note = STATIC_NOTES.get(sym, "")
        rag_context = fundamental_context + "\n" + technical_context
        if extra_note:
            rag_context += f"\nExtraNote: {extra_note}\n"

        if AI_ENABLED:
            try:
                prompt = (
                    "You are a Deep ASI equity research engine for Indian markets.\n"
                    "Use the following CONTEXT (fundamentals + technicals) to generate:\n"
                    "1) 3 precise POSITIVE points.\n"
                    "2) 3 precise NEGATIVE points.\n"
                    "3) 1-line NEWS/MACRO headline if relevant.\n"
                    "4) 2-3 line PROFESSIONAL CONCLUSION (trader + investor view).\n"
                    "Return JSON as:\n"
                    "{"
                    "\"pros\":\"line1\\nline2\\nline3\","
                    "\"cons\":\"line1\\nline2\\nline3\","
                    "\"news\":\"headline\","
                    "\"conclusion\":\"multi-line conclusion\""
                    "}\n\n"
                    f"CONTEXT:\n{rag_context}"
                )

                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.4
                )
                content = response.choices[0].message.content
                clean_json = re.search(r'\{.*\}', content, re.DOTALL)
                if clean_json:
                    ai_data = json.loads(clean_json.group())
                    pos_points = ai_data.get("pros", pos_points)
                    neg_points = ai_data.get("cons", neg_points)
                    news_headlines = ai_data.get("news", news_headlines)
                    ai_conclusion = ai_data.get("conclusion", "")
            except Exception:
                ai_conclusion = ""
        else:
            ai_conclusion = ""

        if ltp > ema_200 and rsi > 50:
            verdict_emoji = "📈"
            verdict_text = "STRONG BUY"
            base_conclusion = f"{company_name} is structurally bullish. Accumulate near support."
        elif ltp > ema_50 and rsi < 70:
            verdict_emoji = "✅"
            verdict_text = "BUY"
            base_conclusion = f"{company_name} is in an uptrend. Momentum is healthy."
        elif rsi > 75:
            verdict_emoji = "⚠️"
            verdict_text = "BOOK PROFIT"
            base_conclusion = f"{company_name} is overbought. Book partial profits."
        else:
            verdict_emoji = "⚖️"
            verdict_text = "HOLD / WAIT"
            base_conclusion = f"{company_name} is consolidating. Wait for direction."

        conclusion = ai_conclusion if ai_conclusion else base_conclusion

        asi_score = compute_asi_score(ltp, ema_50, ema_200, rsi, pe, roe, upside_pct)
        confidence_label = "High" if asi_score >= 75 else "Moderate" if asi_score >= 55 else "Low"

        return (
            f"🚀 **SK AUTO AI ADVISORY** 🚀\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **DATE:** {datetime.now().strftime('%d-%b-%Y')} | "
            f"⏰ **TIME:** {datetime.now().strftime('%H:%M')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 **SYMBOL:** {sym} | {company_name}\n"
            f"🏛 **ASI RANK:** {asi_score}/100 ({confidence_label} Confidence)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **LTP:** ₹{ltp:.2f} | 📊 **RSI:** {rsi:.2f}\n"
            f"📈 **TREND:** {'BULLISH (Above DMA 200)' if ltp > ema_200 else 'BEARISH'}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **VERDICT:** {verdict_emoji} **{verdict_text}** (Next 3–10 sessions)\n"
            f"🚀 **UPSIDE:** {upside_pct}% (Target: ₹{r2:.2f})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 **FUNDAMENTAL SNAPSHOT**\n"
            f"• Market Cap: {round(mcap/10000000, 1)} Cr | Sector: {sector}\n"
            f"• P/E Ratio: {round(pe or 0, 2)}x | ROE: {round(roe or 0, 1)}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏗 **DEEP TECHNICAL ZONES**\n"
            f"🔴 R3: {r3:.2f} | R2: {r2:.2f}\n"
            f"🔴 R1: {r1:.2f} | 🟢 PP: {pp:.2f}\n"
            f"🟢 S1: {s1:.2f} | S2: {s2:.2f} | S3: {s3:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 **COMPANY INFORMATION**\n"
            f"✅ **POSITIVE:**\n{pos_points}\n\n"
            f"❌ **NEGATIVE:**\n{neg_points}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📰 **LATEST NEWS:**\n👉 {news_headlines}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 **RESEARCH CONCLUSION:**\n{conclusion}\n"
            f"⚠️ **KEY RISKS:** Liquidity, news flow and macro events may affect trajectory.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_AIAUTO ADVISORY - Smart Investing_"
        )

    except Exception as e:
        return f"⚠️ **Analysis Error:** {str(e)}"


# --- 7. SMART SEARCH HELPER ---


def find_symbol(query):
    try:
        if not AI_ENABLED:
            return query.upper().replace(" ", "")
        prompt = (
            f"User Query: '{query}'. Indian Stock Market. "
            f"Return ONLY official NSE Symbol UPPERCASE. No .NS."
        )
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        return re.sub(r'\.NS|[^A-Z]', '', response.choices[0].message.content.strip().upper())
    except Exception:
        return query.upper()


# --- 8. HEALTH SERVER (Render) ---


def run_health_server():
    import http.server
    import socketserver

    port = int(os.environ.get("PORT", 10000))

    class H(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"SK AUTO AI ADVISORY ONLINE")

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", port), H) as httpd:
        httpd.serve_forever()


# --- 9. TELEGRAM HANDLERS ---


@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('💎 Smart Portfolio', '🛡️ Option Strategy')
    markup.add('📊 Market Analysis', '🔎 Smart Search')
    markup.add('🚀 Nifty Option Trading')
    bot.send_message(
        m.chat.id,
        "🚀 **SK AUTO AI ADVISORY** 🚀\n\nSelect Advanced Mode:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.message_handler(func=lambda m: m.text == '💎 Smart Portfolio')
def smart_port(m):
    bot.send_chat_action(m.chat.id, 'typing')
    bot.send_message(m.chat.id, "🔍 Scanning Nifty & Midcap Universe...")
    bot.send_message(m.chat.id, get_smart_portfolio(), parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text == '🛡️ Option Strategy')
def hedge_strat(m):
    bot.send_chat_action(m.chat.id, 'typing')
    bot.send_message(
        m.chat.id,
        "🛡️ **HEDGE STRATEGY**\n\n"
        "Use '🚀 Nifty Option Trading' for exact signals.\n\n"
        "**Hedge Logic:**\n"
        "Buy ATM Option + Sell OTM Option to reduce cost.",
        parse_mode="Markdown"
    )


@bot.message_handler(func=lambda m: m.text == '📊 Market Analysis')
def market_view(m):
    bot.send_chat_action(m.chat.id, 'typing')
    try:
        nifty = yf.Ticker("^NSEI").history(period="5d")
        bank = yf.Ticker("^NSEBANK").history(period="5d")
        nltp = nifty['Close'].iloc[-1]
        bltp = bank['Close'].iloc[-1]
        bot.send_message(
            m.chat.id,
            f"📊 **MARKET SNAPSHOT**\n"
            f"Nifty: {nltp:.2f}\n"
            f"BankNifty: {bltp:.2f}\n"
            f"_Mood: Bullish if above Pivot._",
            parse_mode="Markdown"
        )
    except Exception:
        bot.send_message(m.chat.id, "⚠️ Unable to fetch market snapshot right now.")


@bot.message_handler(func=lambda m: m.text == '🔎 Smart Search')
def smart_search(m):
    msg = bot.send_message(m.chat.id, "🔍 Type Company Name:")
    bot.register_next_step_handler(msg, process_smart_search)


def process_smart_search(m):
    query = m.text
    bot.send_chat_action(m.chat.id, 'typing')
    symbol = find_symbol(query)
    bot.send_message(m.chat.id, f"🧠 AI Identified: **{symbol}**", parse_mode="Markdown")
    bot.send_message(m.chat.id, get_sk_auto_report(symbol), parse_mode="Markdown")


def process_options(m):
    try:
        budget = float(m.text.replace('₹', '').replace(',', ''))
        spot = yf.Ticker("^NSEI").history(period="1d")['Close'].iloc[-1]
        bot.send_chat_action(m.chat.id, 'typing')
        bot.send_message(m.chat.id, f"🔍 Scanning for Budget: ₹{budget}...")
        bot.send_message(m.chat.id, get_nifty_option_trade(budget, spot), parse_mode="Markdown")
    except ValueError:
        bot.send_message(m.chat.id, "❌ Invalid number.")


@bot.message_handler(func=lambda m: m.text == '🚀 Nifty Option Trading')
def nifty_opt(m):
    msg = bot.send_message(
        m.chat.id,
        "🚀 **Nifty Option Sniper**\n\nEnter Trading Budget (INR):",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, process_options)


# --- 10. MAIN ---


if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    bot.delete_webhook(drop_pending_updates=True)
    time.sleep(3)
    print("🚀 SK AUTO AI ADVISORY Online...")
    bot.infinity_polling(skip_pending=True, timeout=60)
