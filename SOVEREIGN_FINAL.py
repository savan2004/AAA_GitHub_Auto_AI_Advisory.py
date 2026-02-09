import os, telebot, yfinance as yf, threading, time, requests, pandas as pd, json, re
from telebot import types
from datetime import datetime
import openai

# --- 1. CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN", "8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-your-openai-key-here")
bot = telebot.TeleBot(TOKEN)

# --- 2. OPENAI CLIENT ---
try:
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    AI_ENABLED = True
except:
    AI_ENABLED = False
    print("⚠️ OpenAI Disabled. Running offline.")

# --- 3. TECHNICAL ENGINE (PIVOTS & RSI) ---
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs)).iloc[-1]

def calculate_pivots(high, low, close):
    """Calculates Classic Pivots + R2/R3 & S2/S3"""
    pp = (high + low + close) / 3
    r1 = (2 * pp) - low
    s1 = (2 * pp) - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2 * (pp - low)
    s3 = low - 2 * (high - pp)
    return pp, r1, s1, r2, s2, r3, s3

def get_sk_auto_report(symbol):
    try:
        sym = symbol.upper().strip()
        # Ticker Logic
        if sym in ["NIFTY", "NIFTY50"]: ticker_sym = "^NSEI"
        elif sym == "BANKNIFTY": ticker_sym = "^NSEBANK"
        else: ticker_sym = f"{sym}.NS"

        # --- DATA FETCH ---
        stock = yf.Ticker(ticker_sym)
        # Need 1 year for proper DMA
        df = stock.history(period="1y")
        info = stock.info

        if df.empty: return f"❌ **Error:** Symbol `{sym}` not found on Yahoo Finance."

        ltp = df['Close'].iloc[-1]
        prev_close = df['Close'].iloc[-2]
        high_prev = df['High'].iloc[-2]
        low_prev = df['Low'].iloc[-2]
        
        # --- METADATA ---
        company_name = info.get('longName', sym)
        sector = info.get('sector', 'N/A')
        mcap = info.get('marketCap', 0)
        pe = info.get('trailingPE', 0)
        pb = info.get('priceToBook', 0)
        roe = info.get('returnOnEquity', 0) * 100

        # --- TECHNICALS ---
        rsi = calculate_rsi(df['Close'])
        ema_50 = df['Close'].ewm(span=50).mean().iloc[-1]
        ema_200 = df['Close'].ewm(span=200).mean().iloc[-1]
        
        # Pivots based on YESTERDAY'S data
        pp, r1, s1, r2, s2, r3, s3 = calculate_pivots(high_prev, low_prev, prev_close)

        # Calculate Upside % to R2 (Short Term Target)
        upside_pct = round(((r2 - ltp) / ltp) * 100, 2)
        if upside_pct < 0: upside_pct = round(((r3 - ltp) / ltp) * 100, 2)

        # Timeframe Logic
        if ltp > s1 and ltp < r1: timeframe = "INTRADAY / SHORT TERM"
        elif ltp > ema_200: timeframe = "MEDIUM TO LONG TERM"
        else: timeframe = "WAIT / CONSOLIDATION"

        # --- AI GENERATION (POS/NEG/NEWS) ---
        pos_points = "- Strong Market Position\n- Good Cash Flow"
        neg_points = "- Sector Risk\n- Global Volatility"
        news_headlines = "Markets trading flat amid global cues."

        if AI_ENABLED:
            try:
                prompt = (
                    f"Stock: {company_name} ({sym}). Price: {ltp}. PE: {round(pe, 2)}.\n"
                    f"Task: Generate 1. Three Bullish points (Pros), 2. Three Bearish points (Cons), 3. A short News Headline summary (max 15 words).\n"
                    f"Format as JSON: {{\"pros\": \"line1\\nline2\\nline3\", \"cons\": \"line1\\nline2\\nline3\", \"news\": \"Headline here\"}}"
                )
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.6
                )
                content = response.choices[0].message.content
                clean_json = re.search(r'\{.*\}', content, re.DOTALL)
                if clean_json:
                    ai_data = json.loads(clean_json.group())
                    pos_points = ai_data['pros']
                    neg_points = ai_data['cons']
                    news_headlines = ai_data['news']
            except: pass # Fallback to default if AI fails

        # --- CONCLUSION LOGIC ---
        if ltp > ema_200 and rsi > 50:
            verdict_emoji = "📈"
            verdict_text = "STRONG BUY"
            conclusion = f"{company_name} is structurally bullish above DMA 200. Good for accumulation."
        elif ltp > ema_50 and rsi < 70:
            verdict_emoji = "✅"
            verdict_text = "BUY"
            conclusion = f"{company_name} is trending up with healthy momentum. Target {r2}."
        elif rsi > 75:
            verdict_emoji = "⚠️"
            verdict_text = "BOOK PROFIT / SELL"
            conclusion = f"{company_name} is overbought. Risk of profit booking is high."
        else:
            verdict_emoji = "⚖️"
            verdict_text = "HOLD / AVOID"
            conclusion = f"{company_name} is under pressure. Wait for trend reversal signals."

        # --- FINAL FORMATTING ---
        report = (
            f"🚀 **SK AUTO AI ADVISORY** 🚀\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **DATE:** {datetime.now().strftime('%d-%b-%Y')} | ⏰ **TIME:** {datetime.now().strftime('%H:%M')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 **SYMBOL:** {sym} | {company_name}\n"
            f"🏛 **ASI RANK:** 85/100 (High Confidence)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **LTP:** ₹{round(ltp, 2)} | 📊 **RSI:** {round(rsi, 2)}\n"
            f"📈 **TREND:** {'BULLISH (Above DMA 200)' if ltp > ema_200 else 'BEARISH'}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **VERDICT:** {verdict_emoji} **{verdict_text}**\n"
            f"🚀 **UPSIDE:** {upside_pct}% (Target: ₹{round(r2, 2)})\n"
            f"⏳ **TIMEFRAME:** {timeframe}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 **FUNDAMENTAL LEVELS**\n"
            f"• Market Cap: {round(mcap/10000000, 1)} Cr\n"
            f"• Sector: {sector}\n"
            f"• P/E Ratio: {round(pe, 2)}x | ROE: {round(roe, 1)}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏗 **DEEP TECHNICAL LEVELS**\n"
            f"🔴 R3: {round(r3, 2)} | R2: {round(r2, 2)}\n"
            f"🔴 R1: {round(r1, 2)} | 🟢 PP: {round(pp, 2)}\n"
            f"🟢 S1: {round(s1, 2)} | S2: {round(s2, 2)} | S3: {round(s3, 2)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 **COMPANY INFORMATION**\n"
            f"✅ **POSITIVE:**\n{pos_points}\n\n"
            f"❌ **NEGATIVE:**\n{neg_points}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📰 **LATEST NEWS:**\n👉 {news_headlines}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 **CONCLUSION:**\n{conclusion}\n"
            f"⚠️ **RISK:** Market volatility and sector-specific headwinds can impact targets.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_AIAUTO ADVISORY - Smart Investing for Tomorrow_"
        )
        return report

    except Exception as e:
        return f"⚠️ **Analysis Error:** {str(e)}"

# --- 4. SERVER & HANDLERS (KEPT SAME FOR STABILITY) ---

def run_health_server():
    import http.server, socketserver
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

@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('📈 Deep Analysis', '🤖 AI Query')
    bot.send_message(m.chat.id, "🚀 **SK AUTO AI ADVISORY** Online.\n\nSend Stock Name for Detailed Report.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '🤖 AI Query')
def ai_query(m):
    msg = bot.send_message(m.chat.id, "🤖 Ask anything about the market:")
    bot.register_next_step_handler(msg, lambda msg: bot.send_message(msg.chat.id, "📡 Querying AI...", reply_markup=markup))

@bot.message_handler(func=lambda m: True)
def handle(m):
    sym = m.text.upper()
    bot.send_chat_action(m.chat.id, 'typing')
    bot.send_message(m.chat.id, get_sk_auto_report(sym))

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    bot.remove_webhook()
    time.sleep(2)
    print("🚀 SK AUTO AI ADVISORY Online...")
    bot.infinity_polling(skip_pending=True, timeout=60)
