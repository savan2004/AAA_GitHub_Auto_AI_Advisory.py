import os
import telebot
import yfinance as yf
from telebot import types
from google import genai
from google.genai import types as ai_types
from dotenv import load_dotenv

load_dotenv()

# Configuration from Render Env Vars
bot = telebot.TeleBot(os.getenv("TELEGRAM_TOKEN"))
ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

@bot.message_handler(commands=['start', 'hi'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('🚀 Smart Search')
    bot.send_message(m.chat.id, "🏛 **Sovereign Machine Online**\nSearch any NSE stock for High-IQ AI Advisory.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '🚀 Smart Search')
def ask_symbol(m):
    msg = bot.send_message(m.chat.id, "📝 Enter Symbol (e.g., RELIANCE, TCS):")
    bot.register_next_step_handler(msg, get_stock_data)

def get_stock_data(m):
    symbol = m.text.upper().strip()
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.fast_info
        price = info['lastPrice']
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔍 Deep AI Analysis", callback_data=f"deep_{symbol}"))
        
        bot.send_message(m.chat.id, f"📈 **{symbol}**\n💰 Price: ₹{price:.2f}", reply_markup=markup)
    except:
        bot.reply_to(m, "❌ Invalid symbol. Please use NSE tickers.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('deep_'))
def handle_deep_analysis(call):
    symbol = call.data.split('_')[1]
    bot.answer_callback_query(call.id, "Analyzing...")
    
    # Professional AI prompt with Web Search
    prompt = f"Perform a high-IQ financial analysis for {symbol} (NSE India). Include SWOT, latest news, and a buy/sell/hold rating with 80%+ accuracy logic."
    
    response = ai_client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=ai_types.GenerateContentConfig(tools=[ai_types.Tool(google_search=ai_types.GoogleSearch())])
    )

    bot.send_message(call.message.chat.id, f"🏛 **AI ADVISORY: {symbol}**\n\n{response.text}", parse_mode='Markdown')

if __name__ == "__main__":
    bot.infinity_polling()
