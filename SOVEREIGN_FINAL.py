import os, telebot, yfinance as yf, threading, time, requests, pandas as pd, json, re
from telebot import types
from datetime import datetime

# --- 1. SECURE CONFIG ---
# On Render: Add these to 'Environment Variables'
TOKEN = os.getenv("TELEGRAM_TOKEN", "8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o")
GOOGLE_AI_KEY = os.getenv("GOOGLE_AI_KEY") 
APP_URL = os.getenv("APP_URL", "https://indianstockaibot-n2dv.onrender.com")

bot = telebot.TeleBot(TOKEN)

# --- 2. AI & ENGINE INITIALIZATION ---
import google.generativeai as genai
if GOOGLE_AI_KEY:
    genai.configure(api_key=GOOGLE_AI_KEY)
    ai_model = genai.GenerativeModel('gemini-1.5-flash')
else:
    print("⚠️ WARNING: GOOGLE_AI_KEY not found. AI features will fail.")

# --- 3. SMART QUANT LOGIC ---

def extract_json(text):
    """Cleanly extracts JSON even if AI adds extra prose."""
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(match.group()) if match else None
    except: return None

def get_ai_quant_signal(budget, spot_price):
    try:
        prompt = (
            f"Context: Nifty Spot {spot_price}. Budget ₹{budget}. Today {datetime.now().date()}.\n"
            "Task: Generate a Nifty Option Trade. \n"
            "Rules: Strike must be multiple of 50. Lot size 65. RR 1:3. \n"
            "Return ONLY JSON: {\"strike\":int, \"optionType\":\"CALL/PUT\", \"expiry\":\"DD-MMM\", \"entryPrice\":float, \"target\":float, \"stopLoss\":float, \"lots\":int, \"reasoning\":\"str\"}"
        )
        response = ai_model.generate_content(prompt)
        data = extract_json(response.text)
        
        if not data: return "⚠️ AI logic error. Try again."
        
        cap_used = round(data['entryPrice'] * 65 * data['lots'])
        return (f"🚀 **NIFTY QUANT SIGNAL**\n━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 **{data['strike']} {data['optionType']}** (@ {data['expiry']})\n"
                f"💰 **Entry:** ₹{data['entryPrice']} | **Target:** ₹{data['target']}\n"
                f"🛑 **SL:** ₹{data['stopLoss']} | **Lots:** {data['lots']}\n"
                f"🏦 **Capital:** ₹{cap_used}\n━━━━━━━━━━━━━━━━━━━━\n"
                f"💡 **Logic:** {data['reasoning']}")
    except Exception as e: return f"⚠️ Quant Error: {str(e)}"

# --- 4. SMART TECHNICAL ENGINE ---

def get_asi_report(symbol):
    try:
        sym = symbol.upper().strip()
        ticker = f"{sym}.NS" if sym not in ["NIFTY", "BANKNIFTY"] else ("^NSEI" if sym=="NIFTY" else "^NSEBANK")
        
        stock = yf.Ticker(ticker)
        df = stock.history(period="150d") # Faster pull
        if df.empty: return f"❌ `{sym}` not found."

        ltp = df['Close'].iloc[-1]
        ema_20 = df['Close'].ewm(span=20).mean().iloc[-1]
        
        # Wilder's RSI
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).ewm(alpha=1/14).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14).mean()
        rsi = 100 - (100 / (1 + (gain/loss))).iloc[-1]

        status = "🟢 BULLISH" if ltp > ema_20 and rsi < 70 else "🔴 BEARISH"
        return (f"🏛 **ASI REPORT: {sym}**\n━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 **LTP:** ₹{round(ltp, 2)}\n"
                f"📈 **RSI:** {round(rsi, 2)} | **Trend:** {status}\n"
                f"🎯 **Signal:** {'BUY' if rsi < 35 else 'SELL' if rsi > 70 else 'HOLD'}\n"
                f"⏰ _Data delayed 15m_")
    except: return "⚠️ Technical Error."

# --- 5. UNSTOPPABLE SERVER & POLLING ---

def run_health_server():
    import http.server, socketserver
    port = int(os.environ.get("PORT", 10000))
    class H(http.server.SimpleHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ALIVE")
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", port), H) as httpd: httpd.serve_forever()

@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('📈 Quant Sniper', '📄 Deep Report')
    bot.send_message(m.chat.id, "🏛 **Sovereign Machine Online**", reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def handle(m):
    if m.text == '📈 Quant Sniper':
        msg = bot.send_message(m.chat.id, "💰 Enter Budget (e.g. 5000):")
        bot.register_next_step_handler(msg, lambda msg: bot.send_message(msg.chat.id, get_ai_quant_signal(msg.text, yf.Ticker("^NSEI").history(period="1d")['Close'].iloc[-1])))
    elif m.text == '📄 Deep Report':
        msg = bot.send_message(m.chat.id, "📝 Enter Stock Name:")
        bot.register_next_step_handler(msg, lambda msg: bot.send_message(msg.chat.id, get_asi_report(msg.text)))
    else:
        bot.send_message(m.chat.id, get_asi_report(m.text))

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    while True:
        try:
            bot.remove_webhook()
            time.sleep(2)
            bot.infinity_polling(skip_pending=True, timeout=60)
        except: time.sleep(5)
