# AAA_GitHub_Auto_AI_Advisory.py

Production-ready bot for stock analysis, news sentiment, and AI-driven portfolio suggestions.

## 🚀 Features
- **Smart Search**: Fundamentals + Technicals + AI Insight.
- **Market Scan**: Watchlist health checks.
- **AI Options**: Strategy suggestions with Greeks.
- **News Sentiment**: Fetches real-time news and analyzes mood.
- **Auto-Healing**: Automatic retries and fallback to Groq AI if OpenAI fails.

## 🛠 Deployment (Render)
1. Create a **Background Worker**.
2. Connect your GitHub repo.
3. Add **Environment Variables**:
   - `TELEGRAM_BOT_TOKEN`
   - `OPENAI_API_KEY`
   - `GROQ_API_KEY`
   - `NEWS_API_KEY`
   - `WATCHLIST_JSON`: `{"LARGE_CAP":["RELIANCE.NS"],"MID_CAP":["DIXON.NS"]}`4. Set Build Command: `pip install -r requirements.txt`
5. Set Start Command: `python main.py`

## 💻 Local Setup
1. Install dependencies: `pip install -r requirements.txt`.
2. Fill `config.json`.
3. Run `python main.py`.
