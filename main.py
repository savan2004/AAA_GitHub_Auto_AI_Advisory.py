# main.py
import os
import time
import logging
import threading
from datetime import datetime

import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# Import our new modules
import config
import limits
import history
from llm_wrapper import call_llm_with_limits

# (Optional) Keep your existing technical analysis functions here
# e.g., stock_ai_advisory, format_market_breadth, get_tavily_news, etc.
# For brevity, I'll assume they are present; otherwise copy them from previous versions.

# -------------------- CONFIGURATION --------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
PORT = int(os.getenv("PORT", 8080))
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")

# -------------------- YOUR EXISTING HELPER FUNCTIONS --------------------
# (Place your technical analysis functions here, e.g., stock_ai_advisory,
# format_market_breadth, get_tavily_news, etc. They may call call_llm_with_limits
# instead of direct LLM calls.)

def build_stock_prompt(symbol: str) -> str:
    """Example: construct a prompt for stock analysis."""
    return f"Provide a detailed analysis of {symbol} (NSE) with technicals and fundamentals."

def build_portfolio_prompt(risk: str) -> str:
    """Example: prompt for portfolio suggestion."""
    return f"Suggest a diversified portfolio for a {risk} risk investor using Indian stocks."

# (If you already have functions like stock_ai_advisory that do the heavy lifting,
#  you can modify them to call call_llm_with_limits. For simplicity, I'll show
#  a handler that directly uses the wrapper.)

# -------------------- TELEGRAM HANDLERS --------------------
@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🔍 Stock Analysis"), KeyboardButton("📊 Market Breadth"))
    kb.add(KeyboardButton("💼 Conservative"), KeyboardButton("💼 Moderate"), KeyboardButton("💼 Aggressive"))
    kb.add(KeyboardButton("📰 Market News"), KeyboardButton("📋 History"), KeyboardButton("📊 Usage"))
    bot.send_message(
        m.chat.id,
        "🤖 <b>AI Stock Advisor Pro</b>\n\n"
        "• Stock Analysis: detailed tech+fundamental+AI\n"
        "• Market Breadth: Nifty indices, A/D ratio, sector snapshot\n"
        "• Portfolio: Choose risk profile (Conservative/Moderate/Aggressive)\n"
        "• Market News: latest headlines\n"
        "• History: reuse previous queries (saves quota)\n"
        "• Usage: check daily AI call usage\n\n"
        "Select an option below:",
        reply_markup=kb
    )

@bot.message_handler(func=lambda m: m.text == "🔍 Stock Analysis")
def ask_symbol(m):
    msg = bot.reply_to(m, "📝 Send NSE symbol (e.g. RELIANCE, TCS):")
    bot.register_next_step_handler(msg, process_symbol)

def process_symbol(m):
    sym = m.text.strip().upper()
    if not sym.isalnum():
        bot.reply_to(m, "❌ Invalid symbol. Use letters only.")
        return
    bot.send_chat_action(m.chat.id, 'typing')
    prompt = build_stock_prompt(sym)
    response = call_llm_with_limits(m.from_user.id, prompt, item_type="stock")
    bot.reply_to(m, response)

@bot.message_handler(func=lambda m: m.text == "📊 Market Breadth")
def market_breadth_cmd(m):
    # This command may or may not use LLM; if it does, replace with wrapper.
    # Assuming you have a function format_market_breadth() that returns text.
    bot.send_chat_action(m.chat.id, 'typing')
    text = format_market_breadth()  # your existing function
    bot.reply_to(m, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text in ["💼 Conservative", "💼 Moderate", "💼 Aggressive"])
def portfolio_cmd(m):
    risk = m.text.split()[1].lower()
    bot.send_chat_action(m.chat.id, 'typing')
    prompt = build_portfolio_prompt(risk)
    response = call_llm_with_limits(m.from_user.id, prompt, item_type="portfolio")
    bot.reply_to(m, response)

@bot.message_handler(func=lambda m: m.text == "📰 Market News")
def news_cmd(m):
    # This may or may not use LLM. If using LLM, use wrapper.
    # Here we assume you have a function get_market_news().
    bot.send_chat_action(m.chat.id, 'typing')
    text = get_market_news()  # your function
    bot.reply_to(m, text, parse_mode="HTML")

@bot.message_handler(commands=["usage"])
@bot.message_handler(func=lambda m: m.text == "📊 Usage")
def usage_cmd(m):
    user_id = m.from_user.id
    allowed, remaining, limit = limits.can_use_llm(user_id)
    used = limit - remaining if allowed else limit
    text = f"📊 You have used {used} out of {limit} AI calls today.\nRemaining: {remaining}"
    bot.reply_to(m, text)

@bot.message_handler(commands=["history"])
@bot.message_handler(func=lambda m: m.text == "📋 History")
def show_history(m):
    user_id = m.from_user.id
    items = history.get_recent_history(user_id, limit=5)
    if not items:
        bot.reply_to(m, "No recent history.")
        return
    markup = InlineKeyboardMarkup()
    for item in items:
        preview = item["prompt"][:30] + ("…" if len(item["prompt"]) > 30 else "")
        button = InlineKeyboardButton(
            text=preview,
            callback_data=f"hist_{item['id']}"
        )
        markup.add(button)
    bot.send_message(m.chat.id, "📋 Your recent queries:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("hist_"))
def history_callback(call):
    user_id = call.from_user.id
    item_id = int(call.data.split("_")[1])
    item = history.get_history_item(user_id, item_id)
    if not item:
        bot.answer_callback_query(call.id, "Item not found.")
        return
    if history.is_history_fresh(item):
        bot.send_message(
            user_id,
            f"📎 [CACHED] {item['response']}\n\n_This result is still fresh (saved your quota)._"
        )
        bot.answer_callback_query(call.id)
    else:
        bot.answer_callback_query(call.id, "Fetching fresh analysis...")
        new_response = call_llm_with_limits(user_id, item["prompt"], item["type"])
        bot.send_message(user_id, new_response)

# -------------------- FLASK HEALTH SERVER (optional) --------------------
from flask import Flask
app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return "Bot is running", 200

@app.route('/health', methods=['GET'])
def health():
    return {"status": "healthy", "time": datetime.now().isoformat()}, 200

def run_flask():
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# -------------------- MAIN --------------------
if __name__ == "__main__":
    logger.info("Starting AI Stock Advisor Pro (polling mode)")
    bot.remove_webhook()
    time.sleep(1)
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask health server on port {PORT}")
    while True:
        try:
            bot.infinity_polling()
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)
