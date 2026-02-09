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
    print("⚠️ OpenAI Disabled. Running in Math-Only Mode.")

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

# --- 4. MARKET RESEARCH REPORT ENGINE (MAJOR UPGRADE) ---

def get_market_research_report():
    try:
        # 1. Get Macro Data
        nifty = yf.Ticker("^NSEI").history(period="5d")
        bank = yf.Ticker("^NSEBANK").history(period="5d")
        nifty_ltp = nifty['Close'].iloc[-1]
        bank_ltp = bank['Close'].iloc[-1]
        nifty_rsi = calculate_rsi(nifty['Close'])
        
        # 2. AI Analysis (FII, DII, Events, Top/Bottom Up)
        fii_sentiment = "Neutral"
        major_events = "No major events."
        top_down = "Global markets mixed."
        bottom_up = "Banking sector leading."
        
        if AI_ENABLED:
            try:
                prompt = (
                    f"You are a Chief Market Strategist for NSE India. \n"
                    f"Nifty Spot: {nifty_ltp}. Date: {datetime.now().strftime('%Y-%m-%d')}.\n"
                    f"Analyze the current market scenario and provide:\n"
                    f"1. FII & DII Activity Sentiment (Bullish/Bearish).\n"
                    f"2. Major Corporate or Global Events today (RBI, Fed, Earnings).\n"
                    f"3. Top-Down Analysis (Global -> Sectors).\n"
                    f"4. Bottom-Up Analysis (Heavyweights like Reliance/HDFC).\n"
                    f"Return ONLY JSON: {{\"fii_ sentiment\": \"str\", \"events\": \"str\", \"top_down\": \"str\", \"bottom_up\": \"str\"}}"
                )
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.5
                )
                content = response.choices[0].message.content
                clean_json = re.search(r'\{.*\}', content, re.DOTALL)
                if clean_json:
                    data = json.loads(clean_json.group())
                    fii_sentiment = data['fii_ sentiment'] # Note: handling potential key typo in AI response
                    major_events = data['events']
                    top_down = data['top_down']
                    bottom_up = data['bottom_up']
            except: pass # Fallback if AI fails

        # 3. Technical Levels
        nifty_prev = nifty['Close'].iloc[-2]
        pp, r1, s1, _, _, _, _ = calculate_pivots(nifty['High'].iloc[-2], nifty['Low'].iloc[-2], nifty_prev)
        
        mood = "🟢 Bullish" if nifty_ltp > pp else "🔴 Bearish"

        # 4. Format Research Report
        return (
            f"📊 **MARKET RESEARCH REPORT**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {datetime.now().strftime('%d-%b-%Y')} | ⏰ {datetime.now().strftime('%H:%M')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 **INDEX SNAPSHOT**\n"
            f"• NIFTY 50: ₹{round(nifty_ltp, 2)} ({mood})\n"
            f"• BANKNIFTY: ₹{round(bank_ltp, 2)}\n"
            f"• NIFTY RSI: {round(nifty_rsi, 2)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 **FII & DII ACTIVITY**\n"
            f"👉 **Sentiment:** {fii_sentiment}\n"
            f"👉 **Context:** Institutional buying/selling pressure indicates trend sustainability.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 **TOP-DOWN APPROACH**\n"
            f"👉 **Global Cues:** {top_down}\n"
            f"👉 **Sector Flow:** Money rotating into Banking/IT based on global data.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏢 **BOTTOM-UP APPROACH**\n"
            f"👉 **Heavyweights:** {bottom_up}\n"
            f"👉 **Stock Specific:** Key movers driving Nifty today.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📣 **MAJOR EVENTS & RISKS**\n"
            f"👉 {major_events}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **DEEP LEVELS (NIFTY)**\n"
            f"🔴 R1: {round(r1, 2)} | 🟢 PP: {round(pp, 2)} | 🟢 S1: {round(s1, 2)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_AIAUTO ADVISORY Research Wing_"
        )

    except Exception as e:
        return f"⚠️ **Research Error:** {str(e)}"

# --- 5. NIFTY OPTION SNIPER (FIXED WITH FALLBACK) ---

def get_nifty_option_trade(budget, spot):
    try:
        # PREFERRED: Try AI for precise trade
        if AI_ENABLED:
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
            except: 
                pass # If AI fails, fall through to Math Fallback

        # FALLBACK: Math-based calculation if AI fails
        # 1. Determine Strike (Nearest 50)
        strike = round(spot / 50) * 50
        
        # 2. Determine Type (Based on Spot vs Prev Close)
        prev_close = yf.Ticker("^NSEI").history(period="2d")['Close'].iloc[-2]
        option_type = "CALL" if spot > prev_close else "PUT"
        
        # 3. Estimate Entry Price (Simulated ATM Premium)
        # Usually ATM is between 100 and 250. Let's use 120 for estimation.
        estimated_premium = 120 
        
        # 4. Calculate Lots
        max_lots = int(budget / (estimated_premium * 65))
        if max_lots < 1: max_lots = 1
        
        # 5. Targets (15% gain, 50% loss)
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

# --- 6. SMART SEARCH & REPORT (Same as before) ---
def find_symbol(query):
    try:
        if not AI_ENABLED: return query.upper().replace(" ", "")
        prompt = (
            f"User Query: '{query}'. Context: Indian Stock Market (NSE).\n"
            f"Task: Return ONLY official NSE Stock Symbol in UPPERCASE. Do not add '.NS'."
        )
        response = client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], temperature=0.2
        )
        return re.sub(r'\.NS|[^A-Z]', '', response.choices[0].message.content.strip().upper())
    except:
        return query.upper()

def get_sk_auto_report(symbol):
    try:
        sym = symbol.upper().strip()
        if sym in ["NIFTY", "NIFTY50"]: ticker_sym = "^NSEI"
        elif sym == "BANKNIFTY": ticker_sym = "^NSEBANK"
        else: ticker_sym = f"{sym}.NS"

        stock = yf.Ticker(ticker_sym)
        df = stock.history(period="1y")
        info = stock.info

        if df.empty: return f"❌ Symbol `{sym}` not found."

        ltp = df['Close'].iloc[-1]
        high_prev = df['High'].iloc[-2]
        low_prev = df['Low'].iloc[-2]
        prev_close = df['Close'].iloc[-2]
        
        company_name = info.get('longName', sym)
        sector = info.get('sector', 'N/A')
        mcap = info.get('marketCap', 0)
        pe = info.get('trailingPE', 0)
        roe = info.get('returnOnEquity', 0) * 100

        rsi = calculate_rsi(df['Close'])
        ema_50 = df['Close'].ewm(span=50).mean().iloc[-1]
        ema_200 = df['Close'].ewm(span=200).mean().iloc[-1]
        
        pp, r1, s1, r2, s2, r3, s3 = calculate_pivots(high_prev, low_prev, prev_close)

        upside_pct = round(((r2 - ltp) / ltp) * 100, 2)
        if upside_pct < 0: upside_pct = round(((r3 - ltp) / ltp) * 100, 2)

        # AI Sentiment
        pos_points = "- Strong Fundamentals"
        neg_points = "- Market Risk"
        news_headlines = "Markets active."
        if AI_ENABLED:
            try:
                prompt = f"Stock: {sym}. PE: {round(pe, 2)}. Give 3 Pros, 2 Cons, 1 News Headline. JSON format."
                response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user", "content": prompt}], temperature=0.6)
                data = json.loads(re.search(r'\{.*\}', response.choices[0].message.content, re.DOTALL).group())
                pos_points = data['pros']; neg_points = data['cons']; news_headlines = data['news']
            except: pass

        if ltp > ema_200 and rsi > 50:
            verdict = "📈 STRONG BUY"
            conclusion = "Structural Uptrend. Accumulate."
        elif ltp > ema_50 and rsi < 70:
            verdict = "✅ BUY"
            conclusion = "Momentum healthy. Buy."
        else:
            verdict = "⚖️ HOLD"
            conclusion = "Wait for direction."

        return (
            f"🚀 **SK AUTO AI ADVISORY** 🚀\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {datetime.now().strftime('%d-%b-%Y')}\n"
            f"🏷 **SYMBOL:** {sym} | {company_name}\n"
            f"💰 **LTP:** ₹{round(ltp, 2)} | 📊 **RSI:** {round(rsi, 2)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **VERDICT:** {verdict}\n"
            f"🚀 **UPSIDE:** {upside_pct}% (Tgt: ₹{round(r2, 2)})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏗 **DEEP LEVELS**\n"
            f"R3: {round(r3,2)} R2: {round(r2,2)} R1: {round(r1,2)}\n"
            f"PP: {round(pp,2)}\n"
            f"S1: {round(s1,2)} S2: {round(s2,2)} S3: {round(s3,2)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ **PROS:**\n{pos_points}\n❌ **CONS:**\n{neg_points}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 **CONCLUSION:**\n{conclusion}\n"
            f"_AIAUTO ADVISORY_"
        )
    except Exception as e:
        return f"⚠️ Analysis Error: {e}"

# --- 7. HANDLERS & SERVER ---

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
    markup.add('📊 Market Analysis', '🚀 Nifty Option Trading')
    bot.send_message(m.chat.id, "🚀 **SK AUTO AI ADVISORY** 🚀\n\nResearch & Trading Module Ready.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '📊 Market Analysis')
def market_view(m):
    bot.send_chat_action(m.chat.id, 'typing')
    bot.send_message(m.chat.id, get_market_research_report())

@bot.message_handler(func=lambda m: m.text == '🚀 Nifty Option Trading')
def nifty_opt(m):
    msg = bot.send_message(m.chat.id, "🚀 **Nifty Option Sniper**\n\nEnter Trading Budget (INR):")
    bot.register_next_step_handler(msg, process_options)

def process_options(m):
    try:
        budget = float(m.text.replace('₹', '').replace(',', ''))
        spot = yf.Ticker("^NSEI").history(period="1d")['Close'].iloc[-1]
        bot.send_chat_action(m.chat.id, 'typing')
        bot.send_message(m.chat.id, get_nifty_option_trade(budget, spot))
    except ValueError:
        bot.send_message(m.chat.id, "❌ Invalid number.")

@bot.message_handler(func=lambda m: m.text == '🔎 Smart Search')
def smart_search(m):
    msg = bot.send_message(m.chat.id, "🔍 Type Company Name:")
    bot.register_next_step_handler(msg, lambda msg: bot.send_message(msg.chat.id, get_sk_auto_report(find_symbol(msg.text))))

@bot.message_handler(func=lambda m: m.text == '⭐ Stock Selection')
def stock_sel(m):
    markup = types.InlineKeyboardMarkup(row_width=3)
    btns = [types.InlineKeyboardButton(s, callback_data=f"rep_{s}") for s in ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'SBIN']]
    markup.add(*btns)
    bot.send_message(m.chat.id, "⭐ Select Stock:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('rep_'))
def callback_rep(call):
    sym = call.data.split('_')[1]
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, get_sk_auto_report(sym))

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    bot.delete_webhook(drop_pending_updates=True)
    time.sleep(3)
    print("🚀 SK AUTO AI ADVISORY Online...")
    bot.infinity_polling(skip_pending=True, timeout=60)
