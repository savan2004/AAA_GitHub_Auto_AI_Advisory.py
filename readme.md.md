# AI Stock Advisor Bot - Self-Healing Telegram Bot

A production-ready Telegram bot for Indian stock market analysis with self-healing capabilities.

## Features

- 📊 **Stock Analysis** - Technical + Fundamental data for any NSE symbol
- 📈 **Market Breadth** - Live Nifty A/D ratio, sector performance (updates every 30 min)
- 💼 **Portfolio Suggestions** - 3 risk profiles with CFA-style scoring
- 📊 **Swing Trades** - 8-point scoring system for high-confidence setups
- 📰 **Market News** - Latest Indian market news via Finnhub
- 📋 **History** - Reuse previous queries (saves quota)
- 🔄 **Self-Healing** - Auto-restart on crashes, error recovery
- 📊 **Health Monitoring** - Built-in metrics and status reporting

## Quick Start

1. Clone this repository
2. Install dependencies: `pip install -r requirements.txt`
3. Set environment variables (see below)
4. Run: `python main.py`

## Environment Variables

```bash
# Required
TELEGRAM_TOKEN=your_bot_token_here

# AI Providers (at least one)
GROQ_API_KEY=your_groq_key
GEMINI_API_KEY=your_gemini_key
DEEPSEEK_API_KEY=your_deepseek_key

# Optional
FINNHUB_API_KEY=your_finnhub_key  # For news
ADMIN_CHAT_ID=your_telegram_id    # For alerts
PORT=8080                          # For Flask server