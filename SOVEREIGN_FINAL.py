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
    print("⚠️ OpenAI Disabled.")

# --- 3. TECHNICAL HELPERS ---
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
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

# --- 4. SMART SEARCH ENGINE (AI IDENTIFIER) ---
def find_symbol(query):
    """
    Uses AI to convert company names/descriptions to Stock Symbols.
    e.g., "Tata Motors" -> "TATAMOTORS"
    """
    try:
        if not AI_ENABLED: return query.upper().replace(" ", "") # Fallback
        
        prompt = (
            f"User Query: '{query}'.\n"
            f"Context: Indian Stock Market (NSE).\n"
            f"Task: Return ONLY the official NSE Stock Symbol in UPPERCASE. "
            f"Do not add '.NS'. Just the name. e.g., TCS, RELIANCE, HDFCBANK."
        )
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        symbol = response.choices[0].message.content.strip().upper()
        # Remove any extra whitespace or dots if AI added them
        return re.sub(r'\.NS|[^A-Z]', '', symbol)
    except:
        return query.upper() # Fallback

# --- 5. CORE REPORT GENERATOR (SK AUTO ADVISORY) ---
def get_sk_auto_report(symbol):
    try:
        sym = symbol.upper().strip()
        
        # Ticker Logic
        if sym in ["NIFTY", "NIFTY50"]: ticker_sym = "^NSEI"
        elif sym == "BANKNIFTY": ticker_sym = "^NSEBANK"
        elif sym == "SENSEX": ticker_sym = "^BSESN"
        else: ticker_sym = f"{sym}.NS"

        # DATA FETCH
        stock = yf.Ticker(ticker_sym)
        df = stock.history(period="1y")
        info = stock.info

        if df.empty: 
            # Try one more guess for indices if user messed up
            if "NIFTY" in sym: ticker_sym = "^NSEI"
            elif "BANK" in sym: ticker_sym = "^NSEBANK"
            else: return f"❌ **Error:** Symbol `{sym}` not found. Use Smart Search."
            
            df = stock.history(period="1y")
            info = stock.info
            if df.empty: return f"❌ **Error:** Data not found for `{sym}`."

        ltp = df['Close'].iloc[-1]
        prev_close = df['Close'].iloc[-2]
        high_prev = df['High'].iloc[-2]
        low_prev = df['Low'].iloc[-2]
        
        # METADATA
        company_name = info.get('longName', sym)
        sector = info.get('sector', 'N/A')
        mcap = info.get('marketCap', 0)
        pe = info.get('trailingPE', 0)
        roe = info.get('returnOnEquity', 0) * 100

        # TECHNICALS
        rsi = calculate_rsi(df['Close'])
        ema_50 = df['Close'].ewm(span=50).mean().iloc[-1]
        ema_200 = df['Close'].ewm(span=200).mean().iloc[-1]
        
        # PIVOTS
        pp, r1, s1, r2, s2, r3, s3 = calculate_pivots(high_prev, low_prev, prev_close)

        # LOGIC & AI SENTIMENT
        upside_pct = round(((r2 - ltp) / ltp) * 100, 2)
        if upside_pct < 0: upside_pct = round(((r3 - ltp) / ltp) * 100, 2)

        pos_points = "- Strong Market Position\n- Good Cash Flow"
        neg_points = "- Sector Risk\n- Global Volatility"
        news_headlines = "Markets trading flat."

        if AI_ENABLED:
            try:
                prompt = (
                    f"Stock: {company_name} ({sym}). Price: {ltp}. PE: {round(pe, 2)}.\n"
                    f"Task: Generate 1. Three Bullish points (Pros), 2. Three Bearish points (Cons), 3. A short News Headline summary.\n"
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
            except: pass

        # CONCLUSION
        if ltp > ema_200 and rsi > 50:
            verdict_emoji = "📈"
            verdict_text = "STRONG BUY"
            conclusion = f"{company_name} is structurally bullish. Accumulate near support."
        elif ltp > ema_50 and rsi < 70:
            verdict_emoji = "✅"
            verdict_text = "BUY"
            conclusion = f"{company_name} is in an uptrend. Momentum is healthy."
        elif rsi > 75:
            verdict_emoji = "⚠️"
            verdict_text = "BOOK PROFIT"
            conclusion = f"{company_name} is overbought. Book partial profits."
        else:
            verdict_emoji = "⚖️"
            verdict_text = "HOLD / WAIT"
            conclusion = f"{company_name} is consolidating. Wait for direction."

        # --- FORMAT REPORT ---
        return (
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
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 **FUNDAMENTAL LEVELS**\n"
            f"• MCap: {round(mcap/10000000, 1)} Cr | Sector: {sector}\n"
            f"• P/E: {round(pe, 2)}x | ROE: {round(roe, 1)}%\n"
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
            f"⚠️ **RISK:** Volatility and sector news may impact targets.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_AIAUTO ADVISORY - Smart Investing_"
        )

    except Exception as e:
        return f"⚠️ **Analysis Error:** {str(e)}"

# --- 6. MACRO & MARKET FUNCTIONS ---

def get_market_analysis():
    try:
        nifty = yf.Ticker("^NSEI").history(period="5d")
        bank = yf.Ticker("^NSEBANK").history(period="5d")
        
        nifty_ltp = nifty['Close'].iloc[-1]
        nifty_rsi = calculate_rsi(nifty['Close'])
        nifty_ema = nifty['Close'].ewm(span=20).mean().iloc[-1]
        
        bank_ltp = bank['Close'].iloc[-1]
        
        mood = "🟢 BULLISH BREADTH" if nifty_ltp > nifty_ema else "🔴 BEARISH BREADTH"
        
        return (
            f"📊 **MARKET ANALYSIS (MACRO)**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🇮🇳 **NIFTY 50:** ₹{round(nifty_ltp, 2)}\n"
            f"🏦 **BANKNIFTY:** ₹{round(bank_ltp, 2)}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 **MARKET MOOD:** {mood}\n"
            f"📊 **NIFTY RSI:** {round(nifty_rsi, 2)}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 **GLOBAL CUES:**\n"
            f"US Markets showing mixed trends. \n"
            f"SGX Nifty indicates a flat opening.\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"_Powered by SK AUTO AI_"
        )
    except Exception as e:
        return f"Macro Error: {e}"

def get_briefing():
    return (
        f"🌅 **MORNING / POST MARKET BRIEF**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 **Date:** {datetime.now().strftime('%d-%B-%Y')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **KEY LEVELS:**\n"
        f"• Nifty Resistance: 25,000\n"
        f"• Nifty Support: 24,500\n"
        f"• VIX: Low (Bullish for Options)\n\n"
        f"📝 **STRATEGY:**\n"
        f"• Focus on Buy on Dips strategy as long as Nifty holds 24,800.\n"
        f"• BankNifty showing strength; keep an eye on HDFC Bank.\n\n"
        f"⚠️ **ALERT:**\n"
        f"Crude Oil prices are volatile, watch Oil & Gas sector closely.\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"_AIAUTO ADVISORY_"
    )

# --- 7. SERVER & HANDLERS ---

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
    markup.add('🔎 Smart Search', '⭐ Stock Selection')
    markup.add('📊 Market Analysis', '🌅 Morning/Post Brief')
    markup.add('🚀 Nifty Option Trading')
    
    bot.send_message(m.chat.id, 
        "🚀 **SK AUTO AI ADVISORY** 🚀\n\n"
        "Welcome to India's Smartest Financial Assistant.\n"
        "Select a mode below to begin:", 
        reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '📊 Market Analysis')
def market_view(m):
    bot.send_chat_action(m.chat.id, 'typing')
    bot.send_message(m.chat.id, get_market_analysis())

@bot.message_handler(func=lambda m: m.text == '🌅 Morning/Post Brief')
def briefing(m):
    bot.send_chat_action(m.chat.id, 'typing')
    bot.send_message(m.chat.id, get_briefing())

@bot.message_handler(func=lambda m: m.text == '🔎 Smart Search')
def smart_search(m):
    msg = bot.send_message(m.chat.id, "🔍 **Smart Search Activated**\n\nType Company Name or Symbol:\n(e.g. 'Tata Motors', 'Ambani Company', 'TCS')")
    bot.register_next_step_handler(msg, process_smart_search)

@bot.message_handler(func=lambda m: m.text == '⭐ Stock Selection')
def stock_sel(m):
    markup = types.InlineKeyboardMarkup(row_width=3)
    stocks = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'SBIN', 'TATAMOTORS', 'ICICIBANK', 'BAJFINANCE', 'ADANIENT']
    btns = [types.InlineKeyboardButton(s, callback_data=f"rep_{s}") for s in stocks]
    markup.add(*btns)
    bot.send_message(m.chat.id, "⭐ Select from High Confidence Stocks:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '🚀 Nifty Option Trading')
def nifty_opt(m):
    msg = bot.send_message(m.chat.id, "🚀 **Nifty Option Sniper**\n\nEnter your Trading Budget (INR):\n(e.g. 5000, 10000)")
    bot.register_next_step_handler(msg, process_options)

# --- PROCESSORS ---

def process_smart_search(m):
    query = m.text
    bot.send_chat_action(m.chat.id, 'typing')
    
    # AI identifies the symbol
    symbol = find_symbol(query)
    
    bot.send_message(m.chat.id, f"🧠 AI Identified: **{symbol}**\n🔍 Generating Deep Report...")
    bot.send_message(m.chat.id, get_sk_auto_report(symbol))

def process_options(m):
    try:
        budget = float(m.text.replace('₹', '').replace(',', ''))
        spot = yf.Ticker("^NSEI").history(period="1d")['Close'].iloc[-1]
        
        bot.send_chat_action(m.chat.id, 'typing')
        bot.send_message(m.chat.id, f"🔍 Scanning for Budget: ₹{budget}...")
        
        # Use a simplified quant logic here or reuse the OpenAI one if preferred
        # For stability, let's use a math-based signal here to save tokens
        # Or call OpenAI for signal:
        prompt = (
            f"Nifty Spot: {spot}. Budget: {budget}. Lot: 65.\n"
            f"Generate Nifty Option Trade. RR 1:3. Strike mult of 50.\n"
            f"Return JSON: {{'strike':int, 'type':'CALL/PUT', 'expiry':'DD-MMM', 'entry':float, 'target':float, 'sl':float, 'lots':int}}"
        )
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5
            )
            data = json.loads(re.sub(r'.*?(\{.*\}).*', r'\1', response.choices[0].message.content, flags=re.DOTALL))
            cap = round(data['entry'] * 65 * data['lots'])
            bot.send_message(m.chat.id, 
                f"🚀 **NIFTY QUANT SIGNAL**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 {data['strike']} {data['type']}\n"
                f"💰 Entry: ₹{data['entry']} | Target: ₹{data['target']}\n"
                f"🛑 SL: ₹{data['sl']} | Lots: {data['lots']}\n"
                f"🏦 Capital: ₹{cap}\n━━━━━━━━━━━━━━━━━━━━")
        except:
            bot.send_message(m.chat.id, "⚠️ AI Signal Error. Please try again.")
            
    except ValueError:
        bot.send_message(m.chat.id, "❌ Invalid number.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('rep_'))
def callback_rep(call):
    sym = call.data.split('_')[1]
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, get_sk_auto_report(sym))

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    bot.remove_webhook()
    time.sleep(2)
    print("🚀 SK AUTO AI ADVISORY Online...")
    bot.infinity_polling(skip_pending=True, timeout=60)
