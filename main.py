from telebot import types
import yfinance as yf
from google import genai
from google.genai import types as ai_types
import config

# Initialize Gemini with Search Tool
ai_client = genai.Client(api_key=config.GEMINI_API_KEY)

# --- START & SEARCH HANDLERS ---
@bot.message_handler(commands=['start', 'hi'])
def start_bot(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🚀 Smart Search"))
    bot.send_message(m.chat.id, "🏛 **Sovereign Machine Online**\nClick below to find a stock.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🚀 Smart Search")
def ask_symbol(m):
    msg = bot.send_message(m.chat.id, "📝 Enter Stock Symbol (e.g., RELIANCE, HDFCBANK):")
    bot.register_next_step_handler(msg, get_quick_price)

# --- QUICK PRICE FETCH (YFINANCE) ---
def get_quick_price(m):
    symbol = m.text.upper().strip()
    try:
        # yfinance is fast and reliable for Indian stocks with .NS suffix
        ticker = yf.Ticker(f"{symbol}.NS")
        data = ticker.fast_info
        
        msg = (f"📈 **{symbol} Live Data**\n"
               f"💰 LTP: ₹{data['lastPrice']:.2f}\n"
               f"📍 Day High: ₹{data['dayHigh']:.2f}\n"
               f"📍 Day Low: ₹{data['dayLow']:.2f}")

        # Inline button for Deep Dive
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔍 Deep Analysis", callback_data=f"deep_{symbol}"))
        
        bot.send_message(m.chat.id, msg, reply_markup=markup, parse_mode='Markdown')
    except:
        bot.send_message(m.chat.id, "❌ Invalid Symbol. Please use NSE Tickers like SBIN or TCS.")

# --- AI DEEP DIVE (GEMINI GROUNDING) ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('deep_'))
def run_deep_analysis(call):
    symbol = call.data.split('_')[1]
    bot.edit_message_text(f"⚙️ AI analyzing {symbol} (Fundamentals, SWOT, News)...", 
                          call.message.chat.id, call.message.message_id)

    # Search-grounded prompt for professional analysis
    prompt = (f"Analyze {symbol} (NSE India) as of Feb 2026. "
              "Provide: 1. Company Summary, 2. Shareholding Pattern, "
              "3. Fundamentals (PE, Market Cap), 4. SWOT Analysis, "
              "5. Technical Outlook, 6. Latest News (last 7 days).")
    
    # Configure Gemini with Google Search tool
    config_search = ai_types.GenerateContentConfig(
        tools=[ai_types.Tool(google_search=ai_types.GoogleSearch())]
    )
    
    response = ai_client.models.generate_content(
        model="gemini-2.0-flash", 
        contents=prompt,
        config=config_search
    )

    bot.send_message(call.message.chat.id, f"🏛 **DEEP DIVE: {symbol}**\n\n{response.text}", parse_mode='Markdown')
