import os, telebot, time, pyotp, sqlite3, re
from SmartApi import SmartConnect
from telebot import types
from datetime import datetime
import google.generativeai as genai

# --- 1. CONFIGURATION ---
# Replace these with your actual Angel One details
API_KEY = "C4FHABYE3VUS2JUDB3BAYU44VQ" # Your provided Key
CLIENT_CODE = "YOUR_CLIENT_CODE"      # e.g., S123456
CLIENT_PIN = "YOUR_PIN"              # Your 4-digit Angel PIN
TOTP_SECRET = "YOUR_TOTP_SECRET"      # The string from "Enable TOTP" screen
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
GEMINI_KEY = "YOUR_GEMINI_API_KEY"

# --- 2. INITIALIZE ENGINES ---
bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_KEY)
ai_model = genai.GenerativeModel('gemini-1.5-flash')

# Connect to Angel One
smartApi = SmartConnect(api_key=API_KEY)

def login_angel():
    try:
        totp = pyotp.TOTP(TOTP_SECRET).now()
        data = smartApi.generateSession(CLIENT_CODE, CLIENT_PIN, totp)
        if data['status']:
            print("✅ Angel One Connected (0-Delay Data Active)")
            return True
        else:
            print(f"❌ Login Failed: {data['message']}")
            return False
    except Exception as e:
        print(f"⚠️ Connection Error: {e}")
        return False

# --- 3. THE ASI DATA BRAIN ---

def get_live_asi_signal(symbol_token, symbol_name):
    """Fetches real-time data from Angel One and generates AI Advisory"""
    try:
        # 1. Get Live LTP (0 Delay)
        res = smartApi.ltpData("NSE", symbol_name, symbol_token)
        if not res['status']: return "❌ Unable to fetch live price."
        
        ltp = res['data']['ltp']
        
        # 2. Generate AI Advisory (ASI Prompt)
        prompt = (
            f"Generate High AI IQ level AI Advisory for {symbol_name} at price {ltp}. "
            "Predict tomorrow's Indian stock market movement. Accuracy must be 80%+. Use ASI logic."
        )
        ai_response = ai_model.generate_content(prompt).text

        return (f"🏛 **ASI ADVISORY: {symbol_name}**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 **Live Price:** ₹{ltp} (Real-time)\n"
                f"📅 **Date:** {datetime.now().strftime('%d-%b-%Y %H:%M')}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 **Deep Analysis:**\n{ai_response}")
    except Exception as e:
        return f"⚠️ ASI Logic Error: {str(e)}"

# --- 4. BOT HANDLERS ---

@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('🚀 NIFTY 50', '📈 BANK NIFTY')
    bot.send_message(m.chat.id, "🏛 **Sovereign Machine Online**\nConnected to Angel One (0-Delay).", reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def handle_requests(m):
    if m.text == '🚀 NIFTY 50':
        # Token for Nifty 50 is 99926000
        bot.reply_to(m, get_live_asi_signal("99926000", "NIFTY"))
    elif m.text == '📈 BANK NIFTY':
        # Token for BankNifty is 99926009
        bot.reply_to(m, get_live_asi_signal("99926009", "BANKNIFTY"))

# --- 5. UNSTOPPABLE RUNNER ---

if __name__ == "__main__":
    if login_angel():
        while True:
            try:
                bot.polling(none_stop=True, interval=0, timeout=20)
            except Exception as e:
                print(f"Connection lost, restarting: {e}")
                time.sleep(5)
    else:
        print("CRITICAL: Could not start bot without Angel One Session.")
