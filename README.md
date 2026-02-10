
## Requirements
- Python 3.11+
- Dependencies: See `requirements.txt`
- API Keys: Telegram Bot Token, Gemini API Key, OpenAI API Key, Alpha Vantage API Key, NewsAPI Key

## Setup
1. Clone the repo: `git clone <your-repo-url>`
2. Run setup: `./setup.sh` (or `pip install -r requirements.txt`)
3. Set environment variables (e.g., `TELEGRAM_TOKEN=your_token`, `GEMINI_KEY=your_key`, etc.)
4. Run: `python bot_main.py` (starts bot and admin panel at `http://localhost:5000`)

## Usage
- **Telegram Bot**: Start with `/start`. Commands: '🚀 NIFTY 50', '📈 BANK NIFTY', '📊 Signal for RELIANCE'.
- **Free Tier**: 5 queries/day.
- **Premium Tier**: Unlimited (₹99/month, managed via admin panel).
- **Admin Panel**: Access at `http://localhost:5000` with password (`ADMIN_PASSWORD` env var). View users, queries, RAG data, and update pricing.
- **Auto-Advisory**: Run `python AAA_GitHub_Auto_AI_Advisory.py` for automated reports.

## Tests
Run all tests: `python -m unittest discover tests/`
- LTP retrieval
- Trading signals
- User quota tracking
- Admin panel functionality
- AI query completions

## Security
- Secrets via environment variables only.
- Parameterized SQL queries to prevent injection.
- API request throttling (1 req/sec).
- Input validation and retries for robustness.

## Deployment
- Use Render/Heroku for hosting.
- Set env vars in deployment settings.
- Bot handles crashes gracefully with auto-restart.

## License
[Add your license here, e.g., MIT]
