"""
SK AUTO AI - Telegram Message Handlers
Separate handler logic from main bot logic
"""

from telebot import types
import yfinance as yf
from datetime import datetime
from config import TELEGRAM_TOKEN, AI_ENABLED, OPENAI_API_KEY
from market_analyzer import (
    get_sk_auto_report,
    get_market_scan,
    get_nifty_option_trade,
    find_symbol,
)


def setup_handlers(bot):
    """
    Initialize all bot handlers.
    Call this from main.py: setup_handlers(bot)
    """

    @bot.message_handler(commands=["start"])
    def start(message):
        """Start command - show main menu."""
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("🔎 Smart Search")
        markup.add("📊 Market Scan")
        markup.add("🎯 Nifty Options")
        
        bot.send_message(
            message.chat.id,
            "🚀 **SK AUTO AI ADVISORY**\n\n_Choose an option:_",
            reply_markup=markup,
            parse_mode="Markdown",
        )

    @bot.message_handler(func=lambda m: m.text == "🔎 Smart Search")
    def smart_search_handler(message):
        """Prompt for company name."""
        msg = bot.send_message(
            message.chat.id,
            "🔍 Type Company Name or Symbol:"
        )
        bot.register_next_step_handler(msg, process_search)

    def process_search(message):
        """Process search query."""
        try:
            query = (message.text or "").strip()
            if not query:
                bot.send_message(message.chat.id, "❌ Empty input. Try again.")
                return
            
            bot.send_chat_action(message.chat.id, "typing")
            sym = find_symbol(query)
            
            bot.send_message(
                message.chat.id,
                f"🧠 Symbol: **{sym}**",
                parse_mode="Markdown"
            )
            
            report = get_sk_auto_report(sym)
            bot.send_message(message.chat.id, report, parse_mode="Markdown")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error: {e}")

    @bot.message_handler(func=lambda m: m.text == "📊 Market Scan")
    def market_scan_handler(message):
        """Run full market scan."""
        try:
            bot.send_chat_action(message.chat.id, "typing")
            text = get_market_scan()
            bot.send_message(message.chat.id, text, parse_mode="Markdown")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error: {e}")

    @bot.message_handler(func=lambda m: m.text == "🎯 Nifty Options")
    def nifty_options_handler(message):
        """Prompt for Nifty options budget."""
        msg = bot.send_message(
            message.chat.id,
            "💰 Enter capital for Nifty options (₹):"
        )
        bot.register_next_step_handler(msg, process_nifty_options)

    def process_nifty_options(message):
        """Process Nifty options request."""
        try:
            budget = float((message.text or "0").replace(",", ""))
            if budget <= 0:
                bot.send_message(message.chat.id, "❌ Enter a valid amount > 0")
                return
            
            bot.send_chat_action(message.chat.id, "typing")
            
            # Get Nifty spot price
            nifty = yf.Ticker("^NSEI").history(period="1d")
            if nifty.empty:
                bot.send_message(message.chat.id, "⚠️ Unable to fetch Nifty spot.")
                return
            
            spot = float(nifty["Close"].iloc[-1])
            text = get_nifty_option_trade(budget, spot)
            bot.send_message(message.chat.id, text, parse_mode="Markdown")
        except ValueError:
            bot.send_message(message.chat.id, "❌ Invalid amount. Enter a number.")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error: {e}")

    @bot.message_handler(func=lambda m: True)
    def fallback_handler(message):
        """Fallback: treat any text as symbol search."""
        try:
            query = (message.text or "").strip()
            if not query or query.startswith("/"):
                return
            
            bot.send_chat_action(message.chat.id, "typing")
            sym = find_symbol(query)
            
            bot.send_message(
                message.chat.id,
                f"🧠 Symbol: **{sym}**",
                parse_mode="Markdown"
            )
            
            report = get_sk_auto_report(sym)
            bot.send_message(message.chat.id, report, parse_mode="Markdown")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error: {e}")
