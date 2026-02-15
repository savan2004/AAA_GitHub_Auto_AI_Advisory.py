# main.py
import os
import time
import threading

import telebot
from telebot import types

from swing_trades import get_daily_swing_trades
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

bot = telebot.TeleBot(TELEGRAM_TOKEN)  # NO parse_mode => no Markdown errors

# --- placeholder analysis functions (use your existing ones if needed) ---

def market_analysis() -> str:
    # Integrate your existing safe market view if you want.
    return "Market View\n(Integrate your own market analysis here.)"

def deep_stock_analysis(symbol: str) -> str:
    # Integrate your existing deep analysis logic here.
    return f"Analysis for {symbol} (placeholder)."

def option_strategies_text() -> str:
    return (
        "OPTION STRATEGIES (EDUCATIONAL)\n"
        "- Bull Call Spread: Mildly bullish, limited risk & reward.\n"
        "- Bear Put Spread: Mildly bearish, limited risk.\n"
        "- Iron Condor: Range-bound view, time decay friendly.\n"
        "- Long Straddle: Big move expected, any direction.\n"
        "Always manage risk. Not a recommendation."
    )

# --- TELEGRAM HANDLERS ---

@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("Market View"),
        types.KeyboardButton("Stock Analysis"),
    )
    kb.add(
        types.KeyboardButton("Swing Trades"),
        types.KeyboardButton("Option Ideas"),
    )
    bot.send_message(
        m.chat.id,
        "AI Stock Advisory Bot\n\n"
        "Menu:\n"
        "- Market View\n"
        "- Stock Analysis\n"
        "- Swing Trades\n"
        "- Option Ideas\n\n"
        "All content is educational only.",
        reply_markup=kb,
    )

@bot.message_handler(func=lambda m: m.text == "Market View")
def handle_market(m):
    txt = market_analysis()
    bot.reply_to(m, txt)

@bot.message_handler(func=lambda m: m.text == "Stock Analysis")
def ask_symbol(m):
    msg = bot.reply_to(m, "Send NSE stock symbol (e.g. RELIANCE):")
    bot.register_next_step_handler(msg, handle_symbol_analysis)

def handle_symbol_analysis(m):
    sym = (m.text or "").strip().upper()
    if not sym:
        bot.reply_to(m, "Empty symbol. Try again.")
        return
    txt = deep_stock_analysis(sym)
    bot.reply_to(m, txt)

@bot.message_handler(func=lambda m: m.text == "Swing Trades")
def handle_swing(m):
    txt = get_daily_swing_trades()
    bot.reply_to(m, txt)

@bot.message_handler(func=lambda m: m.text == "Option Ideas")
def handle_options(m):
    bot.reply_to(m, option_strategies_text())

@bot.message_handler(func=lambda m: True)
def fallback(m):
    bot.reply_to(
        m,
        "Use the menu: Market View, Stock Analysis, Swing Trades, Option Ideas."
    )

# --- HEALTH SERVER FOR RENDER ---

def run_health_server():
    port = int(os.environ.get("PORT", 10000))

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bot is running")

    TCPServer.allow_reuse_address = True
    with TCPServer(("0.0.0.0", port), Handler) as httpd:
        httpd.serve_forever()

# --- MAIN LOOP ---

if __name__ == "__main__":
    print("Bot starting with Swing Trades...")
    threading.Thread(target=run_health_server, daemon=True).start()

    # optional local simulation
    try:
        print("SIM SWING TRADES (first 400 chars):")
        print(get_daily_swing_trades()[:400])
    except Exception as e:
        print("Swing simulation error:", e)

    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            print("Polling error:", e)
            time.sleep(10)
