import os
import time
import json
import threading
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
import telebot
from groq import Groq
import google.generativeai as genai
import numpy as np
import requests

# ---------- CONFIG ----------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")  # Get from https://newsapi.org/

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode=None)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Print API key status for debugging
print(f"GROQ API Key: {'✅ Set' if GROQ_API_KEY else '❌ Not set'}")
print(f"GEMINI API Key: {'✅ Set' if GEMINI_API_KEY else '❌ Not set'}")
print(f"NEWS API Key: {'✅ Set' if NEWS_API_KEY else '❌ Not set'}")

# ---------- NEWS FETCHER ----------

def get_company_news(symbol: str, company_name: str = "") -> str:
    """Fetch latest news about the company or its sector"""
    try:
        if not NEWS_API_KEY:
            return "📰 News API key not configured."
        
        # Try to get company name from yfinance
        if not company_name:
            try:
                ticker = yf.Ticker(f"{symbol}.NS")
                info = ticker.info
                company_name = info.get('longName', info.get('shortName', symbol))
            except:
                company_name = symbol
        
        # Search for news
        url = f"https://newsapi.org/v2/everything"
        
        # Try company name first, then symbol
        queries = [company_name, f"{symbol} stock", f"{symbol} NSE"]
        all_articles = []
        
        for query in queries[:2]:  # Try first 2 queries
            params = {
                'q': query,
                'apiKey': NEWS_API_KEY,
                'language': 'en',
                'sortBy': 'publishedAt',
                'pageSize': 5,
                'from': (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            }
            
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get('articles'):
                    all_articles.extend(data['articles'][:3])
                    if len(all_articles) >= 3:
                        break
        
        if not all_articles:
            # Try sector news
            ticker = yf.Ticker(f"{symbol}.NS")
            info = ticker.info
            sector = info.get('sector', '')
            if sector:
                params = {
                    'q': f"{sector} sector India",
                    'apiKey': NEWS_API_KEY,
                    'language': 'en',
                    'sortBy': 'publishedAt',
                    'pageSize': 3
                }
                response = requests.get(url, params=params, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    all_articles = data.get('articles', [])[:3]
        
        # Format news
        if all_articles:
            news_text = "📰 LATEST NEWS\n"
            for i, article in enumerate(all_articles[:3], 1):
                title = article.get('title', 'No title')
                source = article.get('source', {}).get('name', 'Unknown')
                date = article.get('publishedAt', '')[:10]
                news_text += f"{i}. {title[:80]}...\n   📌 {source} | {date}\n"
            return news_text
        else:
            return "📰 No recent news found."
            
    except Exception as e:
        print(f"News fetch error: {e}")
        return "📰 News temporarily unavailable."

# ---------- TA HELPERS ----------

def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).rolling(period).mean()
    down = (-d.clip(upper=0)).rolling(period).mean()
    rs = up / down
    return 100 - (100 / (1 + rs))

def macd(s: pd.Series) -> tuple:
    exp1 = s.ewm(span=12, adjust=False).mean()
    exp2 = s.ewm(span=26, adjust=False).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    return macd_line.iloc[-1], signal_line.iloc[-1]

def bollinger_bands(s: pd.Series, period: int = 20) -> tuple:
    sma = s.rolling(window=period).mean().iloc[-1]
    std = s.rolling(window=period).std().iloc[-1]
    upper = sma + (std * 2)
    lower = sma - (std * 2)
    return upper, sma, lower

def atr(df: pd.DataFrame, period: int = 14) -> float:
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean().iloc[-1]

def pivot_points(df: pd.DataFrame) -> dict:
    last_candle = df.iloc[-1]
    high = last_candle['High']
    low = last_candle['Low']
    close = last_candle['Close']
    
    pp = (high + low + close) / 3
    r1 = (2 * pp) - low
    r2 = pp + (high - low)
    s1 = (2 * pp) - high
    s2 = pp - (high - low)
    
    return {
        'PP': pp, 'R1': r1, 'R2': r2,
        'S1': s1, 'S2': s2
    }

def get_fundamental_info(symbol: str) -> dict:
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.info
        
        return {
            'sector': info.get('sector', 'N/A'),
            'industry': info.get('industry', 'N/A'),
            'company_name': info.get('longName', info.get('shortName', symbol)),
            'market_cap': info.get('marketCap', 0),
            'pe_ratio': info.get('trailingPE', 0),
            'pb_ratio': info.get('priceToBook', 0),
            'roe': info.get('returnOnEquity', 0) * 100 if info.get('returnOnEquity') else 0,
            'dividend_yield': info.get('dividendYield', 0) * 100 if info.get('dividendYield') else 0,
            'high_52w': info.get('fiftyTwoWeekHigh', 0),
            'low_52w': info.get('fiftyTwoWeekLow', 0),
            'prev_close': info.get('regularMarketPreviousClose', 0),
            'volume': info.get('volume', 0),
            'avg_volume': info.get('averageVolume', 0)
        }
    except Exception as e:
        print(f"Error fetching fundamental info: {e}")
        return {}

def calculate_targets(current_price: float, atr_value: float, trend: str) -> dict:
    """Calculate short and long-term targets based on ATR"""
    targets = {}
    
    if trend == "Bullish":
        # Short-term targets (1W, 1M, 3M)
        targets['short_term'] = {
            '1W': current_price + (atr_value * 1.2),
            '1M': current_price + (atr_value * 3),
            '3M': current_price + (atr_value * 6)
        }
        # Long-term targets (6M, 1Y, 2Y)
        targets['long_term'] = {
            '6M': current_price + (atr_value * 12),
            '1Y': current_price + (atr_value * 20),
            '2Y': current_price + (atr_value * 35)
        }
        # Stop loss for swing trading
        targets['stop_loss'] = current_price - (atr_value * 2)
    else:
        # Bearish trend - targets below current price
        targets['short_term'] = {
            '1W': current_price - (atr_value * 1.2),
            '1M': current_price - (atr_value * 3),
            '3M': current_price - (atr_value * 6)
        }
        targets['long_term'] = {
            '6M': current_price - (atr_value * 12),
            '1Y': current_price - (atr_value * 20),
            '2Y': current_price - (atr_value * 35)
        }
        targets['stop_loss'] = current_price + (atr_value * 2)
    
    return targets

def calculate_quality_score(df: pd.DataFrame, fundamental: dict) -> int:
    """Calculate quality score out of 100"""
    score = 0
    close = df['Close']
    
    # Technical factors (40 points)
    # Trend (15 points)
    ema20 = ema(close, 20).iloc[-1]
    ema50 = ema(close, 50).iloc[-1]
    ema200 = ema(close, 200).iloc[-1]
    
    if close.iloc[-1] > ema20: score += 4
    if close.iloc[-1] > ema50: score += 5
    if close.iloc[-1] > ema200: score += 6
    
    # RSI (10 points)
    rsi_val = rsi(close, 14).iloc[-1]
    if 40 <= rsi_val <= 60: score += 10
    elif 30 <= rsi_val <= 70: score += 5
    
    # Volume (5 points)
    volume_avg = df['Volume'].rolling(20).mean().iloc[-1]
    current_volume = df['Volume'].iloc[-1]
    if current_volume > volume_avg * 1.5: score += 5
    elif current_volume > volume_avg: score += 3
    
    # ATR stability (10 points)
    atr_value = atr(df)
    atr_percentage = (atr_value / close.iloc[-1]) * 100
    if atr_percentage < 2: score += 10
    elif atr_percentage < 4: score += 7
    elif atr_percentage < 6: score += 4
    
    # Fundamental factors (60 points)
    if fundamental:
        # PE ratio (15 points)
        pe = fundamental.get('pe_ratio', 0)
        sector_avg_pe = 20  # Approximate sector average
        if pe and 0 < pe < sector_avg_pe * 0.8: score += 15
        elif pe and pe < sector_avg_pe: score += 10
        elif pe and pe < sector_avg_pe * 1.5: score += 5
        
        # ROE (15 points)
        roe = fundamental.get('roe', 0)
        if roe and roe > 20: score += 15
        elif roe and roe > 15: score += 12
        elif roe and roe > 10: score += 8
        elif roe and roe > 5: score += 4
        
        # PB ratio (10 points)
        pb = fundamental.get('pb_ratio', 0)
        if pb and 1 < pb < 3: score += 10
        elif pb and pb <= 1: score += 8
        elif pb and pb < 5: score += 5
        
        # Dividend yield (10 points)
        div = fundamental.get('dividend_yield', 0)
        if div and div > 3: score += 10
        elif div and div > 2: score += 7
        elif div and div > 1: score += 4
        
        # Market cap stability (10 points)
        mcap = fundamental.get('market_cap', 0)
        if mcap > 50000e7:  # Large cap (>50,000 Cr)
            score += 10
        elif mcap > 10000e7:  # Mid cap
            score += 7
        elif mcap > 1000e7:  # Small cap
            score += 4
    
    return min(score, 100)

def get_ai_sentiment(symbol: str, company_name: str, technical_data: dict, fundamental: dict, news: str) -> str:
    """Get AI-powered sentiment analysis with news context"""
    
    # Create a detailed prompt for AI
    prompt = f"""You are an expert Indian stock market analyst. Analyze {symbol} ({company_name}) based on this data:

TECHNICAL INDICATORS:
- Current Price: ₹{technical_data['ltp']:.2f}
- RSI(14): {technical_data['rsi']:.1f}
- MACD: {technical_data['macd']:.2f}
- Signal Line: {technical_data['signal']:.2f}
- Trend vs 200 EMA: {technical_data['trend']}

FUNDAMENTAL DATA:
- P/E Ratio: {fundamental.get('pe_ratio', 'N/A')}
- P/B Ratio: {fundamental.get('pb_ratio', 'N/A')}
- ROE: {fundamental.get('roe', 0):.1f}%
- Dividend Yield: {fundamental.get('dividend_yield', 0):.2f}%
- Market Cap: ₹{fundamental.get('market_cap', 0)/10000000:.1f} Cr
- Sector: {fundamental.get('sector', 'N/A')}

RECENT NEWS:
{news}

Based on ALL this data, provide:
1. THREE specific bullish factors with brief explanation (as bullet points starting with ✓)
2. THREE specific bearish factors with brief explanation (as bullet points starting with ⚠️)
3. Overall sentiment in ONE WORD (Strong Buy/Buy/Hold/Avoid/Sell)
4. Brief outlook for next 1-3 months (1 sentence)

Keep it concise and data-driven. Be specific about the company."""
    
    # Try AI calls
    ai_response = direct_ai_call(prompt, max_tokens=600)
    
    # If AI fails, generate enhanced rule-based sentiment
    if "UNAVAILABLE" in ai_response:
        return generate_enhanced_sentiment(technical_data, fundamental, news)
    
    return ai_response

def direct_ai_call(prompt: str, max_tokens: int = 600) -> str:
    """Direct AI call that runs when user requests analysis"""
    
    print("🔄 Running AI analysis...")
    
    # 1) Try GROQ (fastest)
    if GROQ_API_KEY:
        try:
            print("  Using GROQ API...")
            c = Groq(api_key=GROQ_API_KEY)
            r = c.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.35,
            )
            t = (r.choices[0].message.content or "").strip()
            if t:
                print("  ✅ GROQ analysis complete")
                return t
        except Exception as e:
            print(f"  ❌ Groq error: {e}")

    # 2) Try GEMINI
    if GEMINI_API_KEY:
        try:
            print("  Using Gemini API...")
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            t = (getattr(response, "text", "") or "").strip()
            if t:
                print("  ✅ Gemini analysis complete")
                return t
        except Exception as e:
            print(f"  ❌ Gemini error: {e}")

    # Return indicator that AI is unavailable
    return "AI_UNAVAILABLE"

def generate_enhanced_sentiment(technical_data: dict, fundamental: dict, news: str) -> str:
    """Generate enhanced sentiment with news context"""
    bullish = []
    bearish = []
    
    # Technical analysis
    rsi = technical_data['rsi']
    if rsi < 30:
        bullish.append(f"✓ RSI at {rsi:.1f} indicates oversold conditions - potential bounce")
    elif rsi > 70:
        bearish.append(f"⚠️ RSI at {rsi:.1f} shows overbought conditions - possible reversal")
    elif 40 <= rsi <= 60:
        bullish.append(f"✓ RSI at {rsi:.1f} in neutral zone with room for movement")
    
    # Trend analysis
    if technical_data['trend'] == "Bullish":
        bullish.append("✓ Price above 200 EMA indicates long-term bullish trend")
        if rsi < 70:
            bullish.append("✓ Uptrend with room for further upside")
    else:
        bearish.append("⚠️ Price below 200 EMA suggests long-term bearish trend")
    
    # MACD
    if technical_data['macd'] > technical_data['signal']:
        bullish.append("✓ MACD above signal line shows bullish momentum")
    else:
        bearish.append("⚠️ MACD below signal line indicates bearish momentum")
    
    # Fundamentals
    pe = fundamental.get('pe_ratio', 0)
    if pe and pe < 15:
        bullish.append(f"✓ Attractive P/E of {pe:.1f} below market average")
    elif pe and pe > 30:
        bearish.append(f"⚠️ High P/E of {pe:.1f} suggests premium valuation")
    
    roe = fundamental.get('roe', 0)
    if roe and roe > 15:
        bullish.append(f"✓ Strong ROE of {roe:.1f}% shows efficient management")
    elif roe and roe < 8:
        bearish.append(f"⚠️ Low ROE of {roe:.1f}% indicates poor returns")
    
    div = fundamental.get('dividend_yield', 0)
    if div and div > 2:
        bullish.append(f"✓ Good dividend yield of {div:.2f}% for income")
    
    # Extract sentiment from news (simple keyword analysis)
    news_lower = news.lower()
    positive_keywords = ['positive', 'growth', 'profit', 'gain', 'up', 'rise', 'bull', 'strong']
    negative_keywords = ['negative', 'loss', 'fall', 'down', 'decline', 'bear', 'weak', 'risk']
    
    pos_count = sum(1 for word in positive_keywords if word in news_lower)
    neg_count = sum(1 for word in negative_keywords if word in news_lower)
    
    if pos_count > neg_count + 1:
        bullish.append("✓ Recent news sentiment appears positive")
    elif neg_count > pos_count + 1:
        bearish.append("⚠️ Recent news contains negative signals")
    
    # Determine sentiment
    if len(bullish) >= len(bearish) + 2:
        sentiment = "BUY"
        outlook = "Positive momentum with strong fundamentals suggests upside potential."
    elif len(bullish) > len(bearish):
        sentiment = "HOLD with positive bias"
        outlook = "Mixed signals but overall constructive. Watch for breakout."
    elif len(bearish) > len(bullish) + 1:
        sentiment = "AVOID"
        outlook = "Multiple bearish signals suggest caution in near term."
    else:
        sentiment = "NEUTRAL"
        outlook = "Await clearer direction before taking position."
    
    # Format output
    output = "🤖 AI SENTIMENT ANALYSIS\n"
    output += "━━━━━━━━━━━━━━━━━━━━\n"
    
    output += "✓ BULLISH FACTORS\n"
    for factor in bullish[:3]:
        output += f"{factor}\n"
    
    output += "\n⚠️ BEARISH FACTORS\n"
    for factor in bearish[:3]:
        output += f"{factor}\n"
    
    output += f"\n📊 OVERALL SENTIMENT: {sentiment}\n"
    output += f"📈 OUTLOOK: {outlook}\n"
    
    return output

def stock_ai_advisory(symbol: str) -> str:
    sym = symbol.upper().strip()
    try:
        print(f"\n{'='*50}")
        print(f"Processing {sym}...")
        print('='*50)
        
        # Fetch data
        ticker = yf.Ticker(f"{sym}.NS")
        df = ticker.history(period="1y", interval="1d")
        
        if df.empty or "Close" not in df.columns:
            return f"❌ Could not fetch data for {sym}. Try again later."

        close = df["Close"]
        if len(close) < 60:
            return f"❌ Not enough price history for {sym}."

        # Technical calculations
        ltp = float(close.iloc[-1])
        prev_close = float(df['Close'].iloc[-2]) if len(df) > 1 else ltp
        
        # Get fundamental data
        fundamental = get_fundamental_info(sym)
        company_name = fundamental.get('company_name', sym)
        
        # Technical indicators
        ema20_val = float(ema(close, 20).iloc[-1])
        ema50_val = float(ema(close, 50).iloc[-1])
        ema200_val = float(ema(close, 200).iloc[-1])
        rsi_val = float(rsi(close, 14).iloc[-1])
        macd_val, signal_val = macd(close)
        
        bb_upper, bb_mid, bb_lower = bollinger_bands(close)
        atr_val = float(atr(df))
        pivots = pivot_points(df)
        
        trend = "Bullish" if ltp > ema200_val else "Bearish"
        
        # Calculate targets
        targets = calculate_targets(ltp, atr_val, trend)
        
        # Quality score
        quality_score = calculate_quality_score(df, fundamental)
        
        # Get news
        print("📰 Fetching news...")
        news = get_company_news(sym, company_name)
        
        # AI Sentiment - DIRECT CALL when user requests
        technical_data = {
            'ltp': ltp,
            'rsi': rsi_val,
            'macd': macd_val,
            'signal': signal_val,
            'trend': trend
        }
        
        print("🤖 Running AI analysis...")
        ai_sentiment = get_ai_sentiment(sym, company_name, technical_data, fundamental, news)
        
        # Format the final message with ALL components
        output = f"""📊 DEEP ANALYSIS: {sym}
━━━━━━━━━━━━━━━━━━━━
🏢 {company_name}
🏭 Sector: {fundamental.get('sector', 'N/A')} | Industry: {fundamental.get('industry', 'N/A')}
💰 LTP: ₹{ltp:.2f} (Prev Close: ₹{prev_close:.2f})
📈 52W Range: ₹{fundamental.get('low_52w', 0):.2f} - ₹{fundamental.get('high_52w', 0):.2f}
📊 Volume: {fundamental.get('volume', 0):,} | Avg: {fundamental.get('avg_volume', 0):,}

━━━━━━━━━━━━━━━━━━━━
📊 FUNDAMENTALS
🏦 MCap: ₹{fundamental.get('market_cap', 0)/10000000:.1f} Cr
📈 P/E: {fundamental.get('pe_ratio', 0):.2f} | P/B: {fundamental.get('pb_ratio', 0):.2f}
📊 ROE: {fundamental.get('roe', 0):.1f}% | Div Yield: {fundamental.get('dividend_yield', 0):.2f}%

━━━━━━━━━━━━━━━━━━━━
📌 TECHNICALS
RSI(14): {rsi_val:.1f} | MACD: {macd_val:.2f} vs Signal: {signal_val:.2f}
BB: U{bb_upper:.2f} | M{bb_mid:.2f} | L{bb_lower:.2f}
EMA20: {ema20_val:.2f} | EMA50: {ema50_val:.2f} | EMA200: {ema200_val:.2f}
ATR(14): {atr_val:.2f} | Trend vs 200EMA: {trend}

━━━━━━━━━━━━━━━━━━━━
🎯 PRICE TARGETS
Short-term (1W/1M/3M): ₹{targets['short_term']['1W']:.2f} / ₹{targets['short_term']['1M']:.2f} / ₹{targets['short_term']['3M']:.2f}
Long-term (6M/1Y/2Y): ₹{targets['long_term']['6M']:.2f} / ₹{targets['long_term']['1Y']:.2f} / ₹{targets['long_term']['2Y']:.2f}
🛑 Stop Loss (Swing): ₹{targets['stop_loss']:.2f}

━━━━━━━━━━━━━━━━━━━━
📊 QUALITY SCORE: {quality_score}/100
{'⭐' * (quality_score//20)} {'☆' * (5 - quality_score//20)}

━━━━━━━━━━━━━━━━━━━━
{news}

━━━━━━━━━━━━━━━━━━━━
{ai_sentiment}

━━━━━━━━━━━━━━━━━━━━
⚠️ DISCLAIMER: Educational purpose only. Not investment advice.
🔍 Data Source: Yahoo Finance & NewsAPI"""

        return output
        
    except Exception as e:
        print(f"stock_ai_advisory error: {e}")
        import traceback
        traceback.print_exc()
        return f"❌ Error analyzing {symbol}: {str(e)}"

# ---------- TELEGRAM BOT HANDLERS ----------

@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    print("Received /start from", m.chat.id)
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(telebot.types.KeyboardButton("🔍 Stock Analysis"))
    kb.add(telebot.types.KeyboardButton("📰 Market News"))
    kb.add(telebot.types.KeyboardButton("📊 Swing Trades"))
    bot.send_message(
        m.chat.id,
        "🤖 AI NSE Stock Advisor\n\n"
        "• Tap 'Stock Analysis' and send NSE symbol\n"
        "• Get REAL-TIME AI analysis with news\n"
        "• Technical + Fundamental + News + AI Sentiment\n\n"
        "Example symbols: RELIANCE, TCS, HDFC, INFY, SBIN",
        reply_markup=kb,
    )

@bot.message_handler(func=lambda m: m.text == "🔍 Stock Analysis")
def ask_symbol(m):
    print("User requested Stock Analysis:", m.chat.id)
    msg = bot.reply_to(m, "📝 Send NSE symbol (e.g., RELIANCE, TCS, HDFC):")
    bot.register_next_step_handler(msg, handle_symbol)

def handle_symbol(m):
    sym = (m.text or "").strip().upper()
    print("handle_symbol received:", sym, "from", m.chat.id)

    if not sym or not sym.isalnum():
        bot.reply_to(m, "❌ Send a valid NSE symbol like RELIANCE or TCS.")
        return
    
    # Send typing indicator and status messages
    bot.send_chat_action(m.chat.id, 'typing')
    status_msg = bot.reply_to(m, f"🔍 Analyzing {sym}...\n⏳ Fetching data & news...")
    
    try:
        txt = stock_ai_advisory(sym)
        
        # Delete status message
        bot.delete_message(m.chat.id, status_msg.message_id)
        
        # Split long messages if needed
        if len(txt) > 4000:
            for i in range(0, len(txt), 4000):
                bot.send_message(m.chat.id, txt[i:i+4000])
        else:
            bot.send_message(m.chat.id, txt)
            
    except Exception as e:
        print("handle_symbol error:", e)
        bot.edit_message_text("❌ Error generating analysis. Try again.", m.chat.id, status_msg.message_id)

@bot.message_handler(func=lambda m: m.text == "📰 Market News")
def market_news(m):
    print("User requested Market News:", m.chat.id)
    bot.send_chat_action(m.chat.id, 'typing')
    
    # Get general market news
    if NEWS_API_KEY:
        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                'q': 'Indian stock market OR NSE OR BSE',
                'apiKey': NEWS_API_KEY,
                'language': 'en',
                'sortBy': 'publishedAt',
                'pageSize': 5
            }
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                articles = data.get('articles', [])[:5]
                
                news_text = "📰 MARKET NEWS\n━━━━━━━━━━━━\n\n"
                for i, article in enumerate(articles, 1):
                    title = article.get('title', 'No title')
                    source = article.get('source', {}).get('name', 'Unknown')
                    news_text += f"{i}. {title}\n   📌 {source}\n\n"
                
                bot.reply_to(m, news_text)
            else:
                bot.reply_to(m, "❌ Unable to fetch news at the moment.")
        except:
            bot.reply_to(m, "❌ News service temporarily unavailable.")
    else:
        bot.reply_to(m, "📰 News API key not configured.")

@bot.message_handler(func=lambda m: m.text == "📊 Swing Trades")
def swing_trades(m):
    print("User requested Swing Trades:", m.chat.id)
    bot.reply_to(m, "🔄 Swing Trades feature coming soon!\n\nGet AI-powered swing trading recommendations with entry/exit levels.")

@bot.message_handler(func=lambda m: True)
def fallback(m):
    print("Fallback from", m.chat.id, "text:", m.text)
    bot.reply_to(m, "Use the menu buttons or /start command.")

# ---------- HTTP SERVER FOR HEALTH CHECKS ----------

class RequestHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: str, ct: str = "text/plain"):
        self.send_response(code)
        self.send_header("Content-type", ct)
        self.end_headers()
        self.wfile.write(payload.encode("utf-8"))

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/":
            self._send(200, "AI Stock Advisor Bot is running!")
        elif p.path == "/health":
            self._send(200, json.dumps({"status": "healthy", "timestamp": datetime.now().isoformat()}))
        elif p.path == "/simulate":
            sym = (parse_qs(p.query).get("symbol", ["RELIANCE"])[0] or "RELIANCE").upper()
            try:
                txt = stock_ai_advisory(sym)
                self._send(
                    200,
                    json.dumps({"symbol": sym, "analysis": txt}, ensure_ascii=False),
                    ct="application/json",
                )
            except Exception as e:
                print("simulate error:", e)
                self._send(500, json.dumps({"error": str(e)}))
        else:
            self._send(404, "Not Found")

def run_http():
    port = int(os.environ.get("PORT", 10000))
    srv = HTTPServer(("0.0.0.0", port), RequestHandler)
    print(f"🌐 HTTP server on port {port}")
    srv.serve_forever()

# ---------- MAIN ----------

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Starting AI NSE Stock Advisor Bot")
    print("=" * 60)
    print(f"📱 Telegram Bot: {'✅ Configured' if TELEGRAM_TOKEN else '❌ Missing'}")
    print(f"🤖 AI Services: GROQ {'✅' if GROQ_API_KEY else '❌'} | Gemini {'✅' if GEMINI_API_KEY else '❌'}")
    print(f"📰 News API: {'✅ Configured' if NEWS_API_KEY else '❌ Not configured'}")
    print("=" * 60)
    
    # Start HTTP server in background
    threading.Thread(target=run_http, daemon=True).start()
    
    # Start bot polling with auto-retry
    retry_count = 0
    while True:
        try:
            print("🔄 Bot polling started...")
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            retry_count += 1
            print(f"❌ Polling error (attempt {retry_count}): {e}")
            time.sleep(min(30 * retry_count, 300))  # Exponential backoff
