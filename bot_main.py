import threading
import telebot
from telebot import types
from config import Config
from data_manager import DataManager
from user_tracker import UserTracker
from rag_system import RAGSystem
from admin_panel import AdminPanel
# Removed openai to fix Render build

# Initialize components
bot = telebot.TeleBot(Config.TELEGRAM_TOKEN)
data_manager = DataManager()
user_tracker = UserTracker()
rag_system = RAGSystem()
admin_panel = AdminPanel(user_tracker, rag_system)

def get_signal(symbol: str, price: float) -> str:
    # Simple text-based signal (no AI for now to ensure deploy)
    if price > 1000:  # Basic logic example
        return "📈 Buy signal: Price above key level."
    elif price < 500:
        return "📉 Sell signal: Price below support."
    else:
        return "⚖️ Hold: Consolidating."

@bot.message_handler(commands=['start'])
def start(message):
    user_tracker.update_user(message.chat.id, message.from_user.username)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('🚀 NIFTY 50', '📈 BANK NIFTY', '📊 Signal for RELIANCE')
    bot.send_message(message.chat.id, "🏛 **ASI Bot Online**\nSelect an option.", reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_requests(message):
    user_id = message.chat.id
    if not user_tracker.can_query(user_id):
        bot.reply_to(message, "❌ Free limit reached. Upgrade to premium for ₹99/month.")
        return
    if message.text == '🚀 NIFTY 50':
        price = data_manager.get_ltp("NIFTY")
        if price:
            signal = get_signal("NIFTY", price)
            user_tracker.log_query(user_id, "NIFTY 50", signal)
            bot.reply_to(message, f"🏛 **NIFTY LTP:** ₹{price}\n{signal}")
        else:
            bot.reply_to(message, "❌ LTP unavailable")
    elif message.text == '📈 BANK NIFTY':
        price = data_manager.get_ltp("BANKNIFTY")
        if price:
            signal = get_signal("BANKNIFTY", price)
            user_tracker.log_query(user_id, "BANK NIFTY", signal)
            bot.reply_to(message, f"🏛 **BANKNIFTY LTP:** ₹{price}\n{signal}")
        else:
            bot.reply_to(message, "❌ LTP unavailable")
    elif message.text == '📊 Signal for RELIANCE':
        price = data_manager.get_ltp("RELIANCE")
        if price:
            signal = get_signal("RELIANCE", price)
            user_tracker.log_query(user_id, "RELIANCE Signal", signal)
            bot.reply_to(message, f"🏛 **RELIANCE LTP:** ₹{price}\n{signal}")
        else:
            bot.reply_to(message, "❌ LTP unavailable")
    else:
        bot.reply_to(message, "❓ Invalid command")

if __name__ == "__main__":
    # Run admin panel in a separate thread
    admin_thread = threading.Thread(target=admin_panel.run)
    admin_thread.start()
    # Start bot polling
    bot.polling(none_stop=True)
