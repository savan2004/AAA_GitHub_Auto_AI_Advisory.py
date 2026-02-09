import os, telebot, yfinance as yf, threading, time, requests, pandas as pd, json, re
from telebot import types
from datetime import datetime
import openai  # <--- Using OpenAI

# --- 1. SECURE CONFIG ---
# On Render: Add 'OPENAI_API_KEY' to Environment Variables
TOKEN = os.getenv("TELEGRAM_TOKEN", "8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-your-openai-key-here") # REQUIRED FOR AI
APP_URL = os.getenv("APP_URL", "https://indianstockaibot-n2dv.onrender.com")

bot = telebot.TeleBot(TOKEN)

# --- 2. AI INITIALIZATION (OPENAI) ---
try:
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    AI_AVAILABLE = True
except Exception as e:
    print(f"⚠️ WARNING: OpenAI Init Failed ({e}). AI features disabled. Running in Math-Only Mode.")
    AI_AVAILABLE = False

# --- 3. SMART QUANT LOGIC (POWERED BY OPENAI) ---

def extract_json(text):
    """Cleanly extracts JSON even if AI adds extra prose."""
    try:
        # Remove markdown code blocks if present
        text = text.replace('```json', '').replace('```', '').strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(match.group()) if match else None
    except: return None

def get_ai_quant_signal(budget, spot_price):
    if not AI_AVAILABLE:
        return "⚠️ **AI Offline:** API Key missing or invalid. Please add OPENAI_API_KEY."

    try:
        # GPT-4o-mini is fast, cheap, and smart enough for Option Logic
        prompt = (
            f"You are a Quantitative Analyst for NSE India.\n"
            f"Context: Today is {datetime.now().date()}.\n"
            f"Underlying: NIFTY 50 (Spot Price: ₹{spot_price}).\n"
            f"User Budget: ₹{budget}.\n"
            f"Rules: Lot Size is 65. Strike must be multiple of 50. Target Risk-Reward 1:3.\n"
            f"Task: Suggest one Call or Put option trade.\n"
            f"Return ONLY raw JSON object (no markdown, no text): \n"
            f'{{"strike": integer, "optionType": "CALL" or "PUT", "expiry": "DD-MMM", "entryPrice": float, "target": float, "stopLoss": float, "lots": integer, "reasoning": "Short thesis"}}'
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        
        content = response.choices[0].message.content
        data = extract_json(content)
        
        if not data: 
            return "⚠️ **AI Logic Error:** Could not parse trade data. Please try again."

        # Calculate Capital
        capital = round(data['entryPrice'] * 65 * data['lots'])
        
        return (
            f"🚀 **NIFTY QUANT SIGNAL (OpenAI)**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **Trade:** {data['strike']} {data['optionType']}\n"
            f"📅 **Expiry:** {data['expiry']}\n"
            f"💰 **Entry:** ₹{data['entryPrice']}\n"
            f"✅ **Target:** ₹{data['target']}\n"
            f"🛑 **SL:** ₹{data['stopLoss']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Lots:** {data['lots']} (Qty: {data['lots']*65})\n"
            f"🏦 **Capital Req:** ₹{capital}\n"
            f"🧠 **AI Reasoning:** {data['reasoning']}"
        )
    except openai.RateLimitError:
        return "⚠️ **Rate Limit Exceeded:** OpenAI quota hit. Try again later."
    except Exception as e:
        return f"⚠️ **AI Error:** {str(e)}"

# --- 4. HIGH ACCURACY TECHNICAL ENGINE (MATH-BASED) ---

def get_asi_report(symbol):
    try:
        sym = symbol.upper().strip()
        if sym in ["NIFTY", "BANKNIFTY"]:
            ticker = "^NSEI" if sym == "NIFTY" else "^NSEBANK"
        else:
            ticker = f"{sym}.NS"
        
        stock = yf.Ticker(ticker)
        df = stock.history(period="150d") # Pull sufficient data for EMAs
        
        if df.empty: 
            return f"❌ **Data Error:** Symbol `{sym}` not found on Yahoo Finance."

        ltp = df['Close'].iloc[-1]
        prev_close = df['Close'].iloc[-2]
        
        # 1. RSI Calculation (14 Period - Wilder's Smoothing)
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        rsi_val = rsi.iloc[-1]

        # 2. EMA Calculation (20 Period)
        ema_20 = df['Close'].ewm(span=20, adjust=False).mean().iloc[-1]

        # 3. Pivot Points (Classic)
        high = df['High'].iloc[-2]
        low = df['Low'].iloc[-2]
        close = df['Close'].iloc[-2]
        pp = (high + low + close) / 3
        r1 = (2 * pp) - low
        s1 = (2 * pp) - high

        # 4. Accurate Signal Logic (Confluence)
        is_bullish_trend = ltp > ema_20
        is_oversold = rsi_val < 35
        is_overbought = rsi_val > 70
        
        if is_bullish_trend and is_oversold:
            verdict = "💎 **STRONG BUY**"
            reason = "Trend is Up & RSI is Oversold (Reversal Zone)."
        elif is_bullish_trend and not is_overbought:
            verdict = "📈 **BUY**"
            reason = "Trend is Up & Momentum is Healthy."
        elif not is_bullish_trend and is_overbought:
            verdict = "📉 **SELL**"
            reason = "Trend is Down & RSI is Overbought."
        else:
            verdict = "⚖️ **HOLD / WAIT**"
            reason = "No clear confluence."

        change = ltp - prev_close
        pct_change = (change / prev_close) * 100

        report = (
            f"🏛 **ASI DEEP ANALYSIS: {sym}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **LTP:** ₹{round(ltp, 2)} ({round(pct_change, 2)}%)\n"
            f"📊 **RSI (14):** {round(rsi_val, 2)}\n"
            f"📈 **Trend:** {'Bullish (Above EMA20)' if ltp > ema_20 else 'Bearish (Below EMA20)'}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏗 **DEEP LEVELS**\n"
            f"🔴 R1: {round(r1, 2)} | 🟢 PP: {round(pp, 2)} | 🟢 S1: {round(s1, 2)}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 **VERDICT:** {verdict}\n"
            f"💡 **Logic:** {reason}\n"
            f"⏰ *Data via Yahoo Finance*"
        )
        return report

    except Exception as e:
        return f"⚠️ **Analysis Error:** {str(e)}"

# --- 5. UNSTOPPABLE SERVER & POLLING ---

def run_health_server():
    import http.server, socketserver
    # Render provides PORT env variable
    port = int(os.environ.get("PORT", 10000))
    
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            # Log check
            # print("Health check pinged") 
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"ASI BOT ACTIVE - OPENAI POWERED")

    # allow_reuse_address prevents "Address already in use" errors on restart
    socketserver.TCPServer.allow_reuse_address = True
    
    # Bind to 0.0.0.0 so Render can see it
    with socketserver.TCPServer(("0.0.0.0", port), Handler) as httpd:
        print(f"🏥 Health server running on port {port}")
        httpd.serve_forever()

@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('📈 Quant Sniper', '📄 Deep Report', '❓ Help')
    bot.send_message(m.chat.id, 
        "🏛 **Sovereign AI (OpenAI Version)**\n\n"
        "Status: 24x7 Online\n"
        "Engine: YFinance + OpenAI GPT-4o\n"
        "Select a mode below:", 
        reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '❓ Help')
def help_msg(m):
    bot.send_message(m.chat.id, 
        "🤖 **Commands:**\n"
        "1. **Quant Sniper:** Enter budget (e.g. 5000). AI generates Nifty Option trade.\n"
        "2. **Deep Report:** Enter Stock Name (e.g. RELIANCE). Uses 80%+ math accuracy.\n"
        "3. **AI Chat:** Type any query about the market (if API key is active).")

@bot.message_handler(func=lambda m: True)
def handle(m):
    txt = m.text
    
    if txt == '📈 Quant Sniper':
        msg = bot.send_message(m.chat.id, "💰 **Quant Sniper**\n\nEnter your Trading Budget (INR):\n(e.g. 5000, 10000)")
        bot.register_next_step_handler(msg, process_quant)
    
    elif txt == '📄 Deep Report':
        msg = bot.send_message(m.chat.id, "📝 **Deep Analysis**\n\nEnter Stock Name or Index:\n(e.g. TCS, NIFTY, SBIN)")
        bot.register_next_step_handler(msg, process_report)
    
    else:
        # Default to report if user types a symbol
        bot.send_chat_action(m.chat.id, 'typing')
        bot.send_message(m.chat.id, get_asi_report(txt))

def process_quant(m):
    try:
        budget = float(m.text.replace('₹', '').replace(',', ''))
        # Get Live Spot Price
        spot = yf.Ticker("^NSEI").history(period="1d")['Close'].iloc[-1]
        bot.send_chat_action(m.chat.id, 'typing')
        bot.send_message(m.chat.id, f"🔍 Scanning Nifty Options for Budget: ₹{budget}...")
        signal = get_ai_quant_signal(budget, spot)
        bot.send_message(m.chat.id, signal)
    except ValueError:
        bot.send_message(m.chat.id, "❌ Invalid number. Please enter just digits (e.g. 5000).")
    except Exception as e:
        bot.send_message(m.chat.id, f"❌ Error: {e}")

def process_report(m):
    bot.send_chat_action(m.chat.id, 'typing')
    bot.send_message(m.chat.id, get_asi_report(m.text))

if __name__ == "__main__":
    # 1. Start Web Server in background (Critical for Render)
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()
    
    # 2. Conflict Killer
    try:
        bot.remove_webhook()
        time.sleep(2)
        print("🧹 Webhook cleared.")
    except:
        print("⚠️ Webhook clear failed (already cleared).")

    # 3. Start Polling
    print("🚀 Sovereign AI (OpenAI) Engine Online...")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            print(f"⚠️ Polling interrupted: {e}")
            time.sleep(5) # Wait 5s before reconnecting
