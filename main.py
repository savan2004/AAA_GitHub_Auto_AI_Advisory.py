import telebot
from telebot import types
from config import Config
from data_manager import DataManager

# Initialize components
bot = telebot.TeleBot(Config.TELEGRAM_TOKEN)
data_manager = DataManager()

def get_signal(symbol: str, price: float) -> str:
    # Research-based signal with news integration
    news = data_manager.get_news(symbol)
    news_summary = " | ".join(news[:2]) if news else "No recent news"
    if price > 1000:
        return f"📈 Buy signal: Price above key level (research: upward trend). News: {news_summary}"
    elif price < 500:
        return f"📉 Sell signal: Price below support (research: potential dip). News: {news_summary}"
    else:
        return f"⚖️ Hold: Consolidating (research: stable range). News: {news_summary}"

@bot.message_handler(commands=['start'])
def start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('🚀 NIFTY 50', '📈 BANK NIFTY', '📊 Signal for RELIANCE')
    bot.send_message(message.chat.id, "🏛 **ASI Bot Online**\nUnlimited research: LTP + signals with news.", reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_requests(message):
    if message.text == '🚀 NIFTY 50':
        price = data_manager.get_ltp("NIFTY")
        if price:
            signal = get_signal("NIFTY", price)
            bot.reply_to(message, f"🏛 **NIFTY LTP:** ₹{price}\n{signal}")
        else:
            bot.reply_to(message, "❌ LTP unavailable")
    elif message.text == '📈 BANK NIFTY':
        price = data_manager.get_ltp("BANKNIFTY")
        if price:
            signal = get_signal("BANKNIFTY", price)
            bot.reply_to(message, f"🏛 **BANKNIFTY LTP:** ₹{price}\n{signal}")
        else:
            bot.reply_to(message, "❌ LTP unavailable")
    elif message.text == '📊 Signal for RELIANCE':
        price = data_manager.get_ltp("RELIANCE")
        if price:
            signal = get_signal("RELIANCE", price)
            bot.reply_to(message, f"🏛 **RELIANCE LTP:** ₹{price}\n{signal}")
        else:
            bot.reply_to(message, "❌ LTP unavailable")
    else:
        bot.reply_to(message, "❓ Invalid command")

if __name__ == "__main__":
    bot.polling(none_stop=True)
