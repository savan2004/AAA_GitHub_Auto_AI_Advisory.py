# 🤖 AI Stock Advisory Telegram Bot

A comprehensive FREE AI-powered Telegram bot for Indian stock market analysis, providing deep technical and fundamental insights with short-term and long-term targets.

## ✨ Features

### 📊 Deep Stock Analysis
- **Technical Analysis**: RSI, MACD, Bollinger Bands, Pivot Points, Moving Averages, ATR
- **Fundamental Analysis**: P/E Ratio, P/B Ratio, ROE, Debt/Equity, Market Cap, Dividend Yield
- **Short Term Targets**: 1 week, 1 month, 3 months with stop loss levels
- **Long Term Targets**: 6 months, 1 year, 2 years projections
- **AI-Powered Sentiment**: Bullish/Bearish factors and market news

### 🇮🇳 Indian Market Analysis
- Major indices tracking (Nifty 50, Bank Nifty, Sectoral indices)
- Market breadth analysis (Advances/Declines)
- Top gainers and losers
- AI-powered market outlook
- Trading strategy recommendations

### 💎 Portfolio Scanner
- Scans 100+ stocks across market caps
- Quality score system (0-100)
- Diversified recommendations (Large: 60%, Mid: 30%, Small: 10%)
- Risk-adjusted selections

### 🛡️ Option Strategies
- Bull Call Spread
- Iron Condor
- Straddle
- Protective Put
- Risk management guidelines

## 🆓 100% FREE - No Paid APIs Required!

This bot uses **FREE AI providers** with automatic failover:

1. **GROQ** (Primary) - Llama 3.3 70B - Fast & Free
2. **Google Gemini** (Secondary) - Gemini Pro - Free tier
3. **HuggingFace** (Tertiary) - Mixtral 8x7B - Free inference
4. **Math Fallback** - Always available rule-based analysis

## 🚀 Quick Setup

### Prerequisites
- Python 3.9+
- Telegram Bot Token
- Free API Keys (see below)

### 1. Get Free API Keys

#### GROQ (Recommended - Fastest)
1. Visit: https://console.groq.com
2. Sign up for free account
3. Go to API Keys section
4. Create new API key
5. Copy the key (starts with `gsk_...`)

#### Google Gemini (Alternative)
1. Visit: https://makersuite.google.com/app/apikey
2. Click "Get API Key"
3. Create API key
4. Copy the key

#### HuggingFace (Alternative)
1. Visit: https://huggingface.co/settings/tokens
2. Create a "Read" token
3. Copy the token

#### Telegram Bot Token
1. Message @BotFather on Telegram
2. Send `/newbot`
3. Follow instructions
4. Copy bot token

### 2. Deploy on Render (Free Hosting)

#### Create Render Account
1. Go to https://render.com
2. Sign up with GitHub

#### Deploy Bot
1. Fork/Upload this repository to GitHub
2. In Render Dashboard, click "New +"
3. Select "Web Service"
4. Connect your GitHub repository
5. Configure:
   - **Name**: `ai-stock-advisory-bot` (or any name)
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python Main.py`
   - **Instance Type**: `Free`

#### Set Environment Variables
In Render Dashboard → Environment tab, add:

```bash
# Required
TELEGRAM_TOKEN=your_telegram_bot_token

# AI Providers (Add at least one - GROQ recommended)
GROQ_API_KEY=gsk_your_groq_key_here
GEMINI_API_KEY=your_gemini_key_here
HUGGINGFACE_TOKEN=hf_your_token_here

# Optional (for enhanced features)
NEWS_API_KEY=your_news_api_key
ALPHA_VANTAGE_KEY=your_alpha_vantage_key
```

6. Click "Create Web Service"
7. Wait 5-10 minutes for deployment
8. Bot will auto-start!

### 3. Local Development

```bash
# Clone repository
git clone <your-repo-url>
cd ai-advisory-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
cat > .env << EOF
TELEGRAM_TOKEN=your_telegram_bot_token
GROQ_API_KEY=your_groq_api_key
GEMINI_API_KEY=your_gemini_key
HUGGINGFACE_TOKEN=your_huggingface_token
EOF

# Run bot
python Main.py
```

## 📱 How to Use

### Start the Bot
1. Search for your bot on Telegram
2. Send `/start`
3. Use menu buttons or send stock names directly

### Commands

**Menu Options:**
- 📊 **Stock Analysis** - Deep analysis of any stock
- 🇮🇳 **Market Analysis** - Indian market overview
- 💎 **Portfolio Scanner** - Best stock recommendations
- 🛡️ **Option Strategies** - Learn option trading
- 📚 **Help** - Instructions and guide

**Direct Queries:**
Just type a company name or symbol:
- `RELIANCE`
- `Tata Motors`
- `HDFCBANK`
- `Infosys`

## 🔧 Technical Architecture

### Auto-Healing System
```python
# Automatic error recovery
- AI failover: GROQ → Gemini → HuggingFace → Math
- Auto-restart on crash
- Exponential backoff on API errors
- Graceful degradation
```

### Unlimited Token Support
- Chunked responses for long analysis
- Streaming for large data
- Context management
- Memory optimization

### Multi-Provider AI
```
Primary: GROQ (8000 tokens, fastest)
    ↓ (if fails)
Secondary: Gemini (unlimited, free tier)
    ↓ (if fails)
Tertiary: HuggingFace (2000 tokens)
    ↓ (if fails)
Fallback: Mathematical models (always works)
```

## 📊 Features Breakdown

### Stock Analysis Output
```
✅ Company Information
✅ Current Price & 52-week High/Low
✅ Technical Indicators (RSI, MACD, Bollinger Bands)
✅ Fundamental Metrics (PE, ROE, Debt/Equity)
✅ Moving Averages (20, 50, 200 SMA/EMA)
✅ Support & Resistance Levels
✅ Short Term Targets (1W, 1M, 3M)
✅ Long Term Targets (6M, 1Y, 2Y)
✅ Volume Analysis
✅ AI-Powered Sentiment
✅ Buy/Sell/Hold Recommendation
✅ Investment Strategy
```

### Market Analysis Output
```
✅ Major Indices (Nifty, Bank Nifty, Sectoral)
✅ Market Breadth (Advances/Declines)
✅ Top Gainers/Losers
✅ Volume Analysis
✅ AI Market Outlook
✅ Trading Strategy
✅ Key Support/Resistance
```

### Portfolio Scanner Output
```
✅ Large Cap Picks (60% allocation)
✅ Mid Cap Picks (30% allocation)
✅ Small Cap Picks (10% allocation)
✅ Quality Score (0-100)
✅ Technical Health Check
✅ Diversification Strategy
```

## 🔐 Security & Privacy

- ✅ No data storage (stateless)
- ✅ No user tracking
- ✅ Secure API key handling
- ✅ Environment variables
- ✅ No personal information collected

## 🛠️ Customization

### Add More Stocks
Edit stock universe in `get_smart_portfolio()`:
```python
large_caps = ['RELIANCE', 'TCS', ...]  # Add your picks
mid_caps = ['PERSISTENT', ...]         # Add your picks
small_caps = ['TANLA', ...]            # Add your picks
```

### Adjust Scoring Logic
Modify scoring in `get_deep_stock_analysis()`:
```python
# Technical Score
if ltp > ema_200: score += 20  # Adjust weights
if ltp > sma_50: score += 15
# ... customize further
```

### Change Target Calculations
Modify target formulas in `get_deep_stock_analysis()`:
```python
lt_target_1y = ltp * 1.30  # 30% for 1 year
lt_target_2y = ltp * 1.60  # 60% for 2 years
```

## ⚠️ Disclaimers

1. **Not Financial Advice**: This bot is for educational purposes only
2. **DYOR**: Always do your own research
3. **Risk Warning**: Stock market investments carry risk
4. **Consult Professional**: Consult a registered financial advisor
5. **No Guarantees**: Past performance doesn't guarantee future results

## 🐛 Troubleshooting

### Bot not responding?
- Check Render logs for errors
- Verify TELEGRAM_TOKEN is correct
- Ensure at least one AI provider is configured

### AI responses slow?
- GROQ is fastest (1-2 seconds)
- Gemini is moderate (3-5 seconds)
- HuggingFace can be slow (10-20 seconds)

### Stock not found?
- Verify NSE symbol is correct
- Try full company name
- Check if stock is listed on NSE

### Analysis incomplete?
- Check internet connectivity
- Verify yfinance is fetching data
- Some stocks may have limited data

## 📈 Roadmap

- [ ] Portfolio tracking
- [ ] Price alerts
- [ ] Backtesting
- [ ] More indicators (Fibonacci, Elliott Wave)
- [ ] Multi-language support
- [ ] Voice note responses
- [ ] Chart generation

## 🤝 Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Open a Pull Request

## 📄 License

MIT License - Free to use and modify

## 💬 Support

For issues, questions, or suggestions:
- Open an issue on GitHub
- Contact via Telegram: @YourBotUsername

## 🌟 Show Your Support

If this bot helped you:
- ⭐ Star the repository
- 🔄 Share with others
- 💡 Suggest improvements

---

**Made with ❤️ for Indian Investors**

🤖 Powered by Multi-AI System (GROQ + Gemini + HuggingFace)
📊 Data by Yahoo Finance
🚀 Free Forever!
