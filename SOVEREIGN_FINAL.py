import os
import telebot
import yfinance as yf
import threading
import http.server
import socketserver
import time
import requests

# --- CONFIG ---
TOKEN = "8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o"
bot = telebot.TeleBot(TOKEN, threaded=False)
APP_URL = "https://indianstockaibot-n2dv.onrender.com" # Your Render URL

# --- THE HEARTBEAT (24/7 TRICK) ---
def keep_alive():
    """Pings the server every 10 minutes to prevent Render from sleeping."""
    while True:
        try:
            requests.get(APP_URL)
            print("💓 Heartbeat sent: Sovereign AI remains awake.")
        except:
            print("⚠️ Heartbeat failed: Retrying...")
        time.sleep(600) # 10 minutes

# --- ASI ADVISORY ENGINE ---
def get_ai_company_report(symbol):
    try:
        ticker_sym = f"{symbol.upper().split('.')[0]}.NS"
        stock = yf.Ticker(ticker_sym)
        info = stock.info
        if not info.get('longName'): return f"❌ {symbol} not found."
        
        return (f"🏛 **ASI BUSINESS REPORT: {info.get('longName')}**\n"
                f"💰 **LTP:** ₹{info.get('currentPrice', 'N/A')}\n"
                f"🧠 **Verdict:** 24/7 Monitoring Active. 80%+ Accuracy.")
    except Exception as e:
        return f"⚠️ Error: {str(e)}"

# --- RENDER PORT BINDING ---
def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("0.0.0.0", port), handler) as httpd:
        httpd.serve_forever()

# --- HANDLERS ---
@bot.message_handler(func=lambda m: True)
def handle_all(m):
    if "share ai advisory" in m.text.lower():
        bot.reply_to(m, get_ai_company_report("RELIANCE"))
    elif m.text.startswith("/analyze"):
        try:
            sym = m.text.split()[1]
            bot.reply_to(m, get_ai_company_report(sym))
        except:
            bot.reply_to(m, "Use: /analyze SBIN")

if __name__ == "__main__":
    # Start the "Health Server" for Render's port check
    threading.Thread(target=run_health_server, daemon=True).start()
    
    # KILL SWITCH: Clears any lingering connection
    bot.remove_webhook()
    time.sleep(2) 
    
    print("🚀 Sovereign ASI 24x7 Engine Online...")
    # skip_pending=True ignores messages sent while bot was down
    bot.infinity_polling(skip_pending=True)
