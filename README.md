# 🤖 AI Stock Advisory Bot - FIXED VERSION

**100% FREE** AI-powered Telegram bot for Indian stock market analysis.

## ✅ What's Fixed

### Error 409 - FIXED ✅
- Added `skip_pending=True` to bot initialization
- Proper webhook cleanup before polling
- Better error recovery with auto-restart

### AI Not Generating - FIXED ✅
- Improved GROQ AI initialization with test call
- Better error handling and fallbacks
- Cleaner JSON parsing from AI responses
- Graceful degradation when AI unavailable

### Data Fetching - FIXED ✅
- Updated yfinance to v0.2.28 (stable version)
- Better error handling for empty dataframes
- Minimum data requirements (20 days)
- Proper symbol normalization

### Watchlist Errors - FIXED ✅
- Individual stock error handling
- Prevents one error from breaking entire watchlist
- Clear error messages per stock
- Continues processing remaining stocks

## 🚀 Quick Deploy to Render

### 1. Environment Variables

Set these in Render Dashboard → Environment:

```bash
TELEGRAM_TOKEN=8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o
GROQ_API_KEY=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
NEWS_API_KEY=47fb3f33527944ed982e6e48cc856b23
PORT=10000
PYTHON_VERSION=3.9.18
```

### 2. Render Configuration

**Build Command:**
```bash
pip install -r requirements.txt
```

**Start Command:**
```bash
python main.py
```

**Instance Type:** Free

**Region:** Singapore (for India users)

## ⚡ Features

### 📊 Deep Stock Analysis
- **Technical**: RSI, MACD, Bollinger Bands, Pivots, Moving Averages
- **Fundamental**: PE, PB, ROE, Debt/Equity, Dividend Yield
- **Targets**: Short-term (1W, 1M, 3M) + Long-term (6M, 1Y, 2Y)
- **AI Insights**: Bullish/Bearish factors with recommendation

### 📋 Smart Watchlist
- Large Cap, Mid Cap, Small Cap tracking
- Real-time price updates
- RSI-based signals
- Quick overview of all holdings

### 🇮🇳 Market Overview
- Nifty 50, Bank Nifty, Sectoral indices
- Live percentage changes
- Color-coded trends

### 🤖 AI-Powered
- GROQ Llama 3.3 70B model
- Fast responses (1-2 seconds)
- Context-aware analysis
- 100% FREE (no API costs)

## 💡 Usage

### Start Bot
```
/start
```

### Analyze Stock
Just type the symbol:
```
RELIANCE
TCS
BEL
HDFCBANK
```

Or use the menu button "📊 Stock Analysis"

### Check Watchlist
Use menu button "📋 My Watchlist"

### Market Overview
Use menu button "🇮🇳 Market"

## 🔧 Customization

### Update Watchlist

Edit in `main.py`:

```python
WATCHLIST = {
    "LARGE_CAP": ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC"],
    "MID_CAP": ["DIXON", "TATAPOWER", "PERSISTENT"],
    "SMALL_CAP": ["MASTEK", "TANLA"]
}
```

Add your favorite stocks here!

## 🐛 Troubleshooting

### Bot not responding?
1. Check Render logs for errors
2. Verify TELEGRAM_TOKEN is correct
3. Ensure GROQ_API_KEY is set
4. Restart service in Render

### "No data" errors?
- Symbol must be valid NSE stock (add .NS is automatic)
- Try full name: "Tata Motors" instead of just "TATA"
- Some stocks may not have 1-year history

### AI not working?
- Check GROQ_API_KEY is valid
- Bot will still work with mathematical models
- News will use basic sentiment

### Error 409?
- Bot auto-clears webhooks on start
- If persists, manually clear in BotFather
- Restart Render service

## 📊 Example Analysis Output

```
╔═══════════════════════════════════════════╗
║   🤖 AI STOCK ANALYSIS                   ║
╚═══════════════════════════════════════════╝
📅 13-Feb-2026 15:30

🏢 COMPANY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏷 Reliance Industries Limited
📊 RELIANCE | 🏭 Energy
💰 MCap: ₹1850000.0Cr
💵 LTP: ₹2745.50 (+1.2%)
📈 52W: ₹2968.00 | 📉 ₹2220.00

📊 FUNDAMENTALS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• PE: 24.5x (Fair)
• PB: 2.1x | ROE: 12.3% ⚠️
• D/E: 0.65 ✅ | Yield: 0.35%

🔬 TECHNICALS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 Trend: 🟢 BULLISH
• RSI: 58.2 ✅
• MACD: 🟢 Bullish
• BB: ⚖️ Mid
...

🎯 SHORT TERM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 1W: ₹2820.00 (+2.7%)
📅 1M: ₹2890.00 (+5.3%)
📅 3M: ₹2985.00 (+8.7%)
🛑 SL: ₹2650.00 (-3.5%)

🚀 LONG TERM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 6M: ₹3157.00 (+15.0%)
📅 1Y: ₹3569.00 (+30.0%)
📅 2Y: ₹4393.00 (+60.0%)

🤖 AI INSIGHTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ BULLISH:
   • Strong quarterly results
   • Retail expansion ongoing
   • Jio 5G rollout complete

❌ RISKS:
   • Oil price volatility
   • Competition in retail
   • Regulatory concerns

📰 NEWS: RIL announces capex plans...

🎯 VERDICT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Score: 65/100 | 📈 BUY
🤖 AI: BUY

⚠️ Educational only. DYOR.
```

## ⚠️ Disclaimer

This bot is for **educational purposes only**. 

- Not financial advice
- Do your own research (DYOR)
- Markets involve risk
- Consult a registered advisor

## 📄 License

MIT License - Free to use and modify

## 🙏 Credits

- **AI**: GROQ (Llama 3.3 70B)
- **Data**: Yahoo Finance
- **News**: NewsAPI
- **Framework**: pyTelegramBotAPI

---

**Made with ❤️ for Indian Investors**

🚀 Deploy now and start getting AI-powered stock insights!
