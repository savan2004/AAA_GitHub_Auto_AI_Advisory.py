import telebot
import google.generativeai as genai
from telebot import types
import time
from config import TELEGRAM_TOKEN, GEMINI_API_KEY, SYMBOLS, AI_MODEL_NAME
from data_engine import MarketData, RAGSystem

# --- INITIALIZATION ---
bot = telebot.TeleBot(TELEGRAM_TOKEN)
rag = RAGSystem()
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(AI_MODEL_NAME)

print("✅ Sovereign Bot Initialized...")

def generate_deep_analysis(symbol_name, ticker_symbol):
    """Orchestrates Data Fetching, RAG Retrieval, and AI Generation"""
    
    # 1. Fetch Live Data
    data = MarketData.get_stock_data(ticker_symbol)
    if not data:
        return "⚠️ Error: Could not fetch market data. Market might be closed or symbol invalid."
    
    # 2. Fetch News
    news = MarketData.get_market_news(symbol_name)
    
    # 3. Fetch Historical Context (RAG)
    history = rag.get_context(symbol_name)
    
    # 4. Construct ASI Prompt
    prompt = f"""
    You are an Elite AI Market Analyst (ASI - Artificial Super Intelligence).
    Analyze {symbol_name} based on the following real-time data:
    
    💰 **Price:** ₹{data['price']}
    📈 **Technicals:** RSI: {data['rsi']} (Over 70=Overbought, Under 30=Oversold), SMA-20: {data['sma_20']}
    📰 **Fresh News:** \n{news}
    📜 **Past Context:** \n{history}
    
    **Task:**
    1. Provide a Trend Verdict (BULLISH / BEARISH / NEUTRAL).
    2. Give a calculated prediction for the next market session.
    3. Explain the "Why" using the news and technicals provided.
    
    Keep it concise, professional, and actionable for a trader.
    """
    
    try:
        # 5. Generate Response
        response = model.generate_content(prompt)
        ai_text = response.text
        
        # 6. Save to Memory
        rag.save_log(symbol_name, data['price'], ai_text)
        
        return (f"🏛 **ASI DEEP ANALYSIS: {symbol_name}**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 **LTP:** ₹{data['price']}\n"
                f"📊 **RSI:** {data['rsi']}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{ai_text}")
    except Exception as e:
        return f"⚠️ AI Generation Failed: {str(e)}"

# --- BOT HANDLERS ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn1 = types.KeyboardButton('🚀 NIFTY 50')
    btn2 = types.KeyboardButton('📈 BANK NIFTY')
    btn3 = types.KeyboardButton('⛽ RELIANCE')
    btn4 = types.KeyboardButton('🚙 TATA MOTORS')
    markup.add(btn1, btn2, btn3, btn4)
    
    bot.send_message(
        message.chat.id, 
        "🏛 **Sovereign System Online**\n\n"
        "I am connected to:\n"
        "✅ Yahoo Finance (Live Data)\n"
        "✅ NewsAPI (Global Sentiment)\n"
        "✅ Gemini Pro (Deep Reasoning)\n\n"
        "Select an asset to analyze:", 
        reply_markup=markup
    )

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    symbol_map = {
        '🚀 NIFTY 50': ('NIFTY 50', SYMBOLS['NIFTY 50']),
        '📈 BANK NIFTY': ('BANK NIFTY', SYMBOLS['BANK NIFTY']),
        '⛽ RELIANCE': ('RELIANCE', SYMBOLS['RELIANCE']),
        '🚙 TATA MOTORS': ('TATA MOTORS', SYMBOLS['TATA MOTORS'])
    }
    
    if message.text in symbol_map:
        name, ticker = symbol_map[message.text]
        bot.send_message(message.chat.id, f"🔍 **Scanning Sovereign Matrix for {name}...**\n_Please wait while I crunch technicals and news..._", parse_mode="Markdown")
        
        # Send Typing action
        bot.send_chat_action(message.chat.id, 'typing')
        
        # Generate Analysis
        analysis = generate_deep_analysis(name, ticker)
        
        # Send Result (Split if too long)
        if len(analysis) > 4000:
            bot.send_message(message.chat.id, analysis[:4000])
            bot.send_message(message.chat.id, analysis[4000:])
        else:
            bot.send_message(message.chat.id, analysis, parse_mode="Markdown")
            
    else:
        bot.reply_to(message, "⚠️ Unknown Command. Please use the buttons.")

# --- RUNNER ---
if __name__ == "__main__":
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            print(f"❌ Connection Lost: {e}")
            time.sleep(5)
