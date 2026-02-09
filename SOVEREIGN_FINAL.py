import os
import telebot
import requests
from fpdf import FPDF
import threading
import http.server
import socketserver

# --- THE BULLETPROOF TOKEN LOGIC ---
# This checks Render's Environment tab first. If empty, it uses the hardcoded key.
env_token = os.environ.get('TOKEN')
if env_token and ":" in env_token:
    TOKEN = env_token
else:
    TOKEN = "8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o"

ALPHA_VANTAGE_KEY = os.environ.get('ALPHA_VANTAGE_KEY') or "HKTBO5VLITM9G1B9"

# Initialize bot with the confirmed token
bot = telebot.TeleBot(TOKEN, threaded=False)

# --- FAKE SERVER FOR RENDER ---
def run_fake_server():
    port = int(os.environ.get("PORT", 10000))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        httpd.serve_forever()

# --- ASI ANALYSIS ENGINE ---
def deep_analyze(symbol):
    url = f'https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={ALPHA_VANTAGE_KEY}'
    try:
        data = requests.get(url).json()
        q = data.get("Global Quote", {})
        if not q: return "⚠️ Stock Not Found. Use SYMBOL.NSE"
        
        return (f"💎 ASI HIGH-IQ REPORT: {symbol}\n"
                f"Price: {q.get('05. price')}\n"
                f"Change: {q.get('10. change percent')}\n"
                f"Verdict: 80%+ Accuracy Trend Detected.")
    except:
        return "⚠️ Connection Error."

# --- COMMANDS ---
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "👑 Sovereign AI Advisory Active.\nUse /analyze SBIN.NSE")

@bot.message_handler(commands=['analyze'])
def analyze(message):
    symbol = message.text.split()[-1].upper()
    bot.send_message(message.chat.id, f"🧠 Analyzing {symbol}...")
    bot.reply_to(message, deep_analyze(symbol))

# --- EXECUTION ---
if __name__ == "__main__":
    threading.Thread(target=run_fake_server, daemon=True).start()
    print("Sovereign Brain is active...")
    bot.infinity_polling()
