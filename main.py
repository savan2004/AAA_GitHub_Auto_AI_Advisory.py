import os
import time
import telebot
import yfinance as yf
import pandas as pd
from telebot import types
from openai import OpenAI
import google.generativeai as genai
from dotenv import load_dotenv

# Load Environment Variables
load_dotenv()

# --- 1. CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

bot = telebot.TeleBot(TOKEN)
oa_client = OpenAI(api_key=OPENAI_KEY)
genai.configure(api_key=GEMINI_KEY)

# --- 2. THE ASI BRAIN (High IQ Advisory) ---
def get_asi_signal(symbol, ltp):
    prompt = (
        f"Generate High AI IQ Advisory for {symbol} at ₹{ltp}. "
        "Must be 80%+ accuracy. Prediction should be accurate based on "
        "20 years of market experience. Use ASI logic. "
        "Provide: ENTRY, TARGET, STOP-LOSS."
    )
    
    try:
        # Primary: OpenAI GPT-4o
        response = oa_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "You are a Super-Intelligence Market Advisor."},
                      {"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception:
        # Fallback: Gemini
        model = genai.GenerativeModel('gemini-1.5-flash')
        return model.generate_content(prompt).text

# --- 3. TELEGRAM HANDLERS ---
@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('🚀 NIFTY 50', '📈 BANK NIFTY', '📊 RELIANCE')
    bot.send_message(m.chat.id, 
        "🏛 **Hi, Welcome to SK Ai Auto Advisory**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Status: 24/7 ASI Connected\n"
        "Ready for High-Accuracy Signals.", reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def handle_market(m):
    symbol = m.text.replace('🚀 ', '').replace('📈 ', '').replace('📊 ', '')
    ticker_map = {"NIFTY 50": "^NSEI", "BANK NIFTY": "^NSEBANK"}
    symbol = ticker_map.get(symbol, symbol)

    try:
        data = yf.Ticker(f"{symbol}.NS" if "^" not in symbol else symbol)
        ltp = round(data.fast_info['last_price'], 2)
        
        bot.send_chat_action(m.chat.id, 'typing')
        advice = get_asi_signal(symbol, ltp)
        
        bot.send_message(m.chat.id, f"🏛 **SK ADVISORY: {symbol}**\n\n💰 Price: ₹{ltp}\n\n{advice}", parse_mode="Markdown")
    except:
        bot.reply_to(m, "⚠️ Market Data Timeout. Retrying...")

# --- 4. AUTO-HEALING & CONFLICT SHIELD ---
def run_bot():
    while True:
        try:
            # Clears the 409 Conflict error instantly
            bot.delete_webhook(drop_pending_updates=True)
            bot.polling(none_stop=True, interval=0, timeout=60)
        except Exception as e:
            print(f"Auto-Healing triggered. Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    run_bot()
