def get_stock_data(m):
    symbol = m.text.upper().strip()
    
    # Auto-add .NS if the user forgets
    if not symbol.endswith(".NS"):
        search_symbol = f"{symbol}.NS"
    else:
        search_symbol = symbol

    try:
        ticker = yf.Ticker(search_symbol)
        info = ticker.fast_info
        price = info['lastPrice']
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔍 Deep AI Analysis", callback_data=f"deep_{symbol}"))
        
        bot.send_message(m.chat.id, f"📈 **{symbol}**\n💰 Price: ₹{price:.2f}", reply_markup=markup)
    except:
        bot.reply_to(m, "❌ Invalid symbol. Please use NSE tickers like SBIN or RELIANCE.")
