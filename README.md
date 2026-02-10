# ASI Trading Bot

## Overview
The ASI Trading Bot is a Telegram bot for Indian stock market research. It provides real-time LTP, AI-powered signals, and latest news for unlimited research.

## Requirements
- Python 3.11+
- Dependencies: See `requirements.txt`
- API Keys: Telegram Bot Token, Alpha Vantage API Key, NewsAPI Key

## Setup
1. Clone the repo: `git clone <your-repo-url>`
2. Run setup: `pip install -r requirements.txt`
3. Set environment variables: `TELEGRAM_TOKEN`, `ALPHA_VANTAGE_KEY`, `NEWSAPI_KEY`
4. Run: `python bot_main.py`

## Usage
- Telegram Bot: Start with `/start`. Commands: '🚀 NIFTY 50', '📈 BANK NIFTY', '📊 Signal for RELIANCE'.
- Unlimited Research: LTP + signals with news.

## Tests
Run tests: `python -m unittest discover tests/`

## Security
- Secrets via environment variables only.

## Deployment on Render
1. Create Web Service on Render, connect GitHub repo.
2. Set Build Command: `pip install -r requirements.txt`
3. Set Start Command: `python bot_main.py`
4. Add Environment Variables: TELEGRAM_TOKEN, ALPHA_VANTAGE_KEY, NEWSAPI_KEY
5. Deploy.

## License
[Add your license here, e.g., MIT]
