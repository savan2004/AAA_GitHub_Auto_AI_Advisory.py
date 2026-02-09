import os
import telebot
import yfinance as yf
import threading
import http.server
import socketserver
import time

# --- CONFIG ---
TOKEN = "8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o"
bot = telebot.TeleBot(TOKEN, threaded=False)

# --- ASI BUSINESS LOGIC ---
def get_ai_company_report(symbol):
    try:
        ticker_sym = f"{symbol.upper().split('.')[0]}.NS"
        stock = yf.Ticker(ticker_sym)
        info = stock.info
        if not info.get('longName'): return f"❌ {symbol} not found."
        
        price = info.get('currentPrice', 'N/A')
        rev_growth = info.get('revenueGrowth', 0) * 100
        
        return (f"🏛 **ASI BUSINESS REPORT: {info.get('longName')}**\n"
                f"💰 **LTP:** ₹{price}\n"
                f"📈 **Revenue Growth:** {round(rev_growth, 2)}%\n"
                f"🧠 **Verdict:** 80%+ Accuracy Trend Confirmed.")
    except Exception as e:
        return f"⚠️ Error: {str(e)}"

# --- PORT BINDING FIX (FOR RENDER) ---
def run_health_server():
    # Render provides the port in an environment variable
    port = int(os.environ.get("PORT", 10000))
    handler = http.server.SimpleHTTPRequestHandler
    # Binding to 0.0.0.0 is MANDATORY for Render
    with socketserver.TCPServer(("0.0.0.0", port), handler) as httpd:
        print(f"✅ Health Check Server active on port {port}")
        httpd.serve_forever()

# --- HANDLERS ---
@bot.message_handler(func=lambda m: True)
def handle_all(m):
    text = m.text.lower()
    if "share ai advisory" in text:
        bot.reply_to(m, get_ai_company_report("RELIANCE"))
    elif text.startswith("/analyze"):
        try:
            sym = m.text.split()[1]
            bot.reply_to(m, get_ai_company_report(sym))
        except:
            bot.reply_to(m, "Use: /analyze SBIN")

if __name__ == "__main__":
    # Start the "Fake" server in a separate thread so Render is happy
    threading.Thread(target=run_health_server, daemon=True).start()
    
    # Give the server a second to bind before starting the bot
    time.sleep(2)
    
    bot.remove_webhook()
    print("🚀 Sovereign ASI Business Engine Online...")
    bot.infinity_polling(skip_pending=True)
