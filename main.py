import json
import re
import os
import time
import random
import threading
import pandas as pd
from datetime import datetime
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

import telebot
from telebot import types

from groq import Groq
import google.generativeai as genai
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer

# Tavily for better prompts/research
try:
    from tavily import TavilyClient
    TAVILY_AVAILABLE = True
    tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY", ""))
except ImportError:
    TAVILY_AVAILABLE = False
    tavily = None
    print("Tavily not available - pip install tavily-python")

# --- 1. CONFIG & ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Cache for data (simple dict, expires in 10min)
cache = {}
CACHE_EXPIRY = 600  # seconds

# Lock for yfinance
yf_lock = threading.Lock()

# --- 2. COMMON MARKET HELPERS ---
def get_cached(key, expiry=CACHE_EXPIRY):
    now = time.time()
    if key in cache and now - cache[key]['time'] < expiry:
        return cache[key]['data']
    return None

def set_cached(key, data):
    cache[key] = {'data': data, 'time': time.time()}

def safe_history(ticker, period="1y", interval="1d"):
    key = f"{ticker}_{period}_{interval}"
    data = get_cached(key)
    if data is not None:
        return data

    with yf_lock:
        max_retries = 5
        for attempt in range(max_retries):
            try:
                df = yf.Ticker(ticker).history(period=period, interval=interval)
                if not df.empty:
                    set_cached(key, df)
                    return df
            except YFRateLimitError:
                wait = (2 ** attempt) + random.uniform(0, 2)
                time.sleep(wait)
            except Exception:
                break
        return pd.DataFrame()

# Other helpers like quality_score remain same...
def quality_score(ltp, ema200, rsi, pe, roe):
    score = 0
    if ltp > ema200 * 0.95: score += 1
    if 30 < rsi < 70: score += 1
    if pe < 25: score += 1
    if roe > 15: score += 1
    return score / 4 * 100

# --- 3. AI LAYER: Enhanced with Tavily ---
def enhance_prompt_with_research(base_prompt, query):
    if not TAVILY_AVAILABLE:
        return base_prompt
    try:
        # Tavily research for fresh insights
        response = tavily.search(
            query=f"latest analysis {query} NSE stock India market news",
            search_depth="basic",
            max_results=3,
            include_answer=True
        )
        research = response.get('answer', '') + '\nSources: ' + ', '.join([r['url'] for r in response.get('results', [])[:2]])
        return f"{base_prompt}\n\nLatest Research Context:\n{research[:2000]}\n\nUse this for accurate, timely advice."
    except:
        return base_prompt

def ai_call(prompt: str, max_tokens: int = 600, research_query: str = None) -> str:
    if research_query:
        prompt = enhance_prompt_with_research(prompt, research_query)
    
    # Groq first
    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3
        )
        return resp.choices[0].message.content
    except:
        pass
    
    # Gemini fallback
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        resp = model.generate_content(prompt)
        return resp.text
    except:
        pass
    
    return "AI temporarily unavailable. Try again later."

# --- 4. DEEP STOCK ANALYSIS (Cached) ---
def deep_stock_analysis(symbol: str) -> str:
    sym = symbol.upper().strip()
    ticker = f"{sym}.NS"
    key = f"analysis_{sym}"
    data = get_cached(key, 1800)  # 30min cache
    if data:
        return data

    df = safe_history(ticker, "2y")
    if df.empty:
        return f"No data for {sym}. Try valid NSE symbol."

    ltp = df['Close'][-1]
    ema200 = df['Close'].ewm(span=200).mean()[-1]
    rsi = compute_rsi(df['Close'])
    rsi_val = rsi[-1] if not pd.isna(rsi[-1]) else 50
    info = yf.Ticker(ticker).info
    pe = info.get('trailingPE', 0)
    roe = info.get('returnOnEquity', 0) * 100

    score = quality_score(ltp, ema200, rsi_val, pe, roe)
    
    prompt = f"""
    Analyze {sym}.NS: LTP {ltp:.2f}, EMA200 {ema200:.2f}, RSI {rsi_val:.1f}, PE {pe:.1f}, ROE {roe:.1f}, Score {score:.0f}/100.
    Recent trend: {'Bullish' if ltp > ema200 else 'Bearish'}.
    Provide BUY/HOLD/SELL rec with reasons, risks, target. Educational only.
    """
    
    analysis = ai_call(prompt, 800, f"{sym} stock analysis")
    full = f"**{sym} Analysis**\nScore: {score:.0f}/100\n\n{analysis}"
    set_cached(key, full)
    return full

def compute_rsi(prices, window=14):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# --- 5. MARKET ANALYSIS (Cached) ---
def market_analysis() -> str:
    key = "market_analysis"
    data = get_cached(key)
    if data:
        return data

    nifty = safe_history("^NSEI", "5d")
    bank = safe_history("^NSEBANK", "5d")
    if nifty.empty or bank.empty:
        return "Market data unavailable."

    nifty_chg = ((nifty['Close'][-1] - nifty['Close'][0]) / nifty['Close'][0]) * 100
    bank_chg = ((bank['Close'][-1] - bank['Close'][0]) / bank['Close'][0]) * 100

    prompt = f"Nifty50: {nifty_chg:.2f}%, BankNifty: {bank_chg:.2f}%. Market outlook?"
    analysis = ai_call(prompt, 400, "NSE Nifty BankNifty latest")

    full = f"**Market Snapshot**\nNifty: {nifty_chg:.2f}%\nBankNifty: {bank_chg:.2f}%\n\n{analysis}"
    set_cached(key, full)
    return full

# --- 6. PORTFOLIO SCANNER (Batched) ---
def portfolio_scanner() -> str:
    key = "portfolio_scan"
    data = get_cached(key, 900)  # 15min
    if data:
        return data

    large_caps = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "ITC"]
    mid_caps = ["PERSISTENT", "MOTHERSON", "TRENT", "AUBANK", "TATACOMM"]

    # Batch download
    all_tickers = " ".join([f"{s}.NS" for s in large_caps + mid_caps])
    data = yf.download(all_tickers, period="3mo", group_by='ticker', threads=False, prepost=False)

    results = []
    for sym in large_caps + mid_caps:
        try:
            closes = data['Close'][sym].dropna()
            if len(closes) > 10:
                ltp = closes[-1]
                ema = closes.ewm(span=50).mean()[-1]
                score = 100 if ltp > ema else 0
                results.append(f"{sym}: {score:.0f}")
        except:
            pass

    prompt = f"Top picks from: {' | '.join(results[:10])}. Suggest 3-5 buys."
    recs = ai_call(prompt, 500, "best NSE stocks now")
    full = f"**Portfolio Scan**\n{recs}\n(Updated: {datetime.now().strftime('%H:%M')})"
    set_cached(key, full)
    return full

# Option strategies text remains same...
def option_strategies_text() -> str:
    return "🛡️ **OPTION STRATEGIES (EDUCATIONAL)**\n- Iron Condor: Range bound\n- Straddle: High vol expected\nAlways use stops. Not advice."

# --- 7. TELEGRAM HANDLERS ---
@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("📊 Market"), types.KeyboardButton("🔍 Scanner"))
    kb.add(types.KeyboardButton("💰 Portfolio"), types.KeyboardButton("🛡️ Options"))
    bot.send_message(m.chat.id, "🤖 AI Stock Bot\nEducational analysis only.\nPick:", reply_markup=kb)

@bot.message_handler(func=lambda m: True)
def handle_msg(m):
    text = m.text.upper()
    if "MARKET" in text:
        bot.reply_to(m, market_analysis())
    elif "SCANNER" in text or "PORTFOLIO" in text:
        bot.reply_to(m, portfolio_scanner())
    elif "OPTIONS" in text:
        bot.reply_to(m, option_strategies_text())
    else:
        # Assume stock symbol
        bot.reply_to(m, deep_stock_analysis(text))

# Fallback handler if needed
@bot.message_handler(func=lambda m: m.text == "Fallback")
def fallback_symbol(m):
    bot.reply_to(m, "Use /start for menu.")

# --- 8. HEALTH SERVER ---
def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bot running healthy")
    TCPServer.allow_reuse_address = True
    with TCPServer(("0.0.0.0", port), Handler) as httpd:
        httpd.serve_forever()

# --- 9. MAIN ---
if __name__ == "__main__":
    print("🤖 Enhanced AI Stock Bot starting...")
    threading.Thread(target=run_health_server, daemon=True).start()
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(30)
