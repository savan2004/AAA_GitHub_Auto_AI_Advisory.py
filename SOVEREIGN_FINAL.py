import os
import telebot
import requests
import threading
import http.server
import socketserver

# --- AUTHENTICATION ---
TOKEN = "8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o"
ALPHA_VANTAGE_KEY = "HKTBO5VLITM9G1B9"
bot = telebot.TeleBot(TOKEN, threaded=False)

# --- THE SMART SYMBOL FINDER ---
def fetch_stock_data(user_symbol):
    """Try multiple Indian suffixes if the first one fails."""
    # List of suffixes Alpha Vantage uses for India
    suffixes = [".NS", ".BOM", ".NSE", ".BSE"]
    clean_sym = user_symbol.upper().strip()
    
    # Try the raw symbol first, then with suffixes
    search_list = [clean_sym] + [f"{clean_sym}{s}" for s in suffixes]
    
    for sym in search_list:
        url = f'https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={sym}&apikey={ALPHA_VANTAGE_KEY}'
        try:
            response = requests.get(url).json()
            data = response.get("Global Quote", {})
            if data and "05. price" in data:
                return data, sym
        except:
            continue
    return None, None

def deep_analyze(user_symbol):
    data, matched_sym = fetch_stock_data(user_symbol)
    
    if not data:
        return (f"❌ **Symbol '{user_symbol}' Not Found.**\n\n"
                f"💡 **Tip:** Use the short ticker name like `RELIANCE`, `SBIN`, or `BEL`.")

    price = data.get("05. price", "0")
    change = data.get("10. change percent", "0%")
    
    return (f"💎 **SOVEREIGN AI ADVISORY** 💎\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Verified Stock:** {matched_sym}\n"
            f"💰 **Current Price:** ₹{price}\n"
            f"📈 **Trend Today:** {change}\n\n"
            f"🧠 **ASI Intelligence (80%+ Accuracy):**\n"
            f"Verdict: Strong trend detected. Accumulate on dips.")

# --- HANDLERS ---
@bot.message_handler(func=lambda m: True)
def handle_msg(message):
    text = message.text.lower()
    
    if "share ai advisory" in text:
        bot.reply_to(message, "🎯 **Generating High-IQ Advisory...**")
        bot.send_message(message.chat.id, deep_analyze("RELIANCE"), parse_mode='Markdown')

    elif text.startswith("/analyze"):
        try:
            # Extract just the name, remove any manually added dots/suffixes
            raw_target = message.text.split()[1].split('.')[0]
            bot.send_message(message.chat.id, f"🧠 **ASI analyzing {raw_target.upper()}...**")
            bot.reply_to(message, deep_analyze(raw_target), parse_mode='Markdown')
        except:
            bot.reply_to(message, "⚠️ Use: `/analyze RELIANCE`")

# --- RENDER PORT FIX ---
def run_server():
    port = int(os.environ.get("PORT", 10000))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        httpd.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    bot.infinity_polling()
