import os
import time
import json
import threading
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer

import pandas as pd
import yfinance as yf
import telebot
from groq import Groq
import google.generativeai as genai
import numpy as np

# ---------- CONFIG ----------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode=None)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

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
            'market_cap': info.get('marketCap', 0),
            'pe_ratio': info.get('trailingPE', 0),
            'pb_ratio': info.get('priceToBook', 0),
            'roe': info.get('returnOnEquity', 0) * 100 if info.get('returnOnEquity') else 0,
            'dividend_yield': info.get('dividendYield', 0) * 100 if info.get('dividendYield') else 0,
            'high_52w': info.get('fiftyTwoWeekHigh', 0),
            'low_52w': info.get('fiftyTwoWeekLow', 0),
            'prev_close': info.get('regularMarketPreviousClose', 0)
        }
    except:
        return {}

def calculate_targets(current_price: float, atr_value: float, trend: str) -> dict:
    """Calculate short and long-term targets based on ATR"""
    targets = {}
    
    # Short-term targets (1W, 1M, 3M)
    targets['short_term'] = {
        '1W': current_price + (atr_value * 1.5),
        '1M': current_price + (atr_value * 4),
        '3M': current_price + (atr_value * 8)
    }
    
    # Long-term targets (6M, 1Y, 2Y)
    targets['long_term'] = {
        '6M': current_price + (atr_value * 15),
        '1Y': current_price + (atr_value * 25),
        '2Y': current_price + (atr_value * 40)
    }
    
    # Stop loss for swing trading
    targets['stop_loss'] = current_price - (atr_value * 2)
    
    return targets

def calculate_quality_score(df: pd.DataFrame, fundamental: dict) -> int:
    """Calculate quality score out of 100"""
    score = 0
    close = df['Close']
    
    # Technical factors (50 points)
    # Trend (20 points)
    ema20 = ema(close, 20).iloc[-1]
    ema50 = ema(close, 50).iloc[-1]
    ema200 = ema(close, 200).iloc[-1]
    
    if close.iloc[-1] > ema20: score += 5
    if close.iloc[-1] > ema50: score += 5
    if close.iloc[-1] > ema200: score += 10
    
    # RSI (10 points)
    rsi_val = rsi(close, 14).iloc[-1]
    if 40 <= rsi_val <= 60: score += 10
    elif 30 <= rsi_val <= 70: score += 5
    
    # Volume trend (10 points)
    volume_avg = df['Volume'].rolling(20).mean().iloc[-1]
    current_volume = df['Volume'].iloc[-1]
    if current_volume > volume_avg * 1.5: score += 10
    elif current_volume > volume_avg: score += 5
    
    # ATR stability (10 points)
    atr_value = atr(df)
    atr_percentage = (atr_value / close.iloc[-1]) * 100
    if atr_percentage < 3: score += 10
    elif atr_percentage < 5: score += 5
    
    # Fundamental factors (50 points)
    if fundamental:
        # PE ratio (15 points)
        pe = fundamental.get('pe_ratio', 0)
        if 10 < pe < 25: score += 15
        elif pe <= 10: score += 10
        elif pe < 40: score += 5
        
        # ROE (15 points)
        roe = fundamental.get('roe', 0)
        if roe > 20: score += 15
        elif roe > 15: score += 10
        elif roe > 10: score += 5
        
        # PB ratio (10 points)
        pb = fundamental.get('pb_ratio', 0)
        if 1 < pb < 3: score += 10
        elif pb <= 1: score += 5
        
        # Dividend yield (10 points)
        div = fundamental.get('dividend_yield', 0)
        if div > 3: score += 10
        elif div > 1: score += 5
    
    return min(score, 100)

def get_ai_sentiment(symbol: str, technical_data: dict, fundamental: dict) -> str:
    """Get AI-powered sentiment analysis"""
    prompt = f"""
Analyze {symbol} (NSE) stock based on:

Technical:
- Price: ₹{technical_data['ltp']:.2f}
- RSI: {technical_data['rsi']:.1f}
- MACD: {technical_data['macd']:.2f} vs Signal: {technical_data['signal']:.2f}
- Trend vs 200 EMA: {technical_data['trend']}

Fundamental:
- P/E: {fundamental.get('pe_ratio', 'N/A')}
- P/B: {fundamental.get('pb_ratio', 'N/A')}
- ROE: {fundamental.get('roe', 0):.1f}%
- Div Yield: {fundamental.get('dividend_yield', 0):.1f}%

Provide:
1. 3 bullish factors (bullet points)
2. 3 bearish factors (bullet points)
3. Overall sentiment (Bullish/Bearish/Neutral/Avoid)

Keep it concise under 150 words.
"""
    
    return ai_call(prompt, max_tokens=300)

def ai_call(prompt: str, max_tokens: int = 600) -> str:
    # 1) GROQ
    if GROQ_API_KEY:
        try:
            c = Groq(api_key=GROQ_API_KEY)
            r = c.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.35,
            )
            t = (r.choices[0].message.content or "").strip()
            if t:
                return t
        except Exception as e:
            print("Groq error:", e)

    # 2) GEMINI
    if GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            t = (getattr(response, "text", "") or "").strip()
            if t:
                return t
        except Exception as e:
            print("Gemini error:", e)

    # 3) FALLBACK
    return "AI analysis temporarily unavailable. Using technical indicators only."

def stock_ai_advisory(symbol: str) -> str:
    sym = symbol.upper().strip()
    try:
        # Fetch data
        ticker = yf.Ticker(f"{sym}.NS")
        df = ticker.history(period="1y", interval="1d")
        
        if df.empty or "Close" not in df.columns:
            return f"Could not fetch data for {sym}. Try again later."

        close = df["Close"]
        if len(close) < 60:
            return f"Not enough price history for {sym}."

        # Technical calculations
        ltp = float(close.iloc[-1])
        prev_close = float(df['Close'].iloc[-2]) if len(df) > 1 else ltp
        
        # Get fundamental data
        fundamental = get_fundamental_info(sym)
        
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
        
        # AI Sentiment
        technical_data = {
            'ltp': ltp,
            'rsi': rsi_val,
            'macd': macd_val,
            'signal': signal_val,
            'trend': trend
        }
        
        ai_sentiment = get_ai_sentiment(sym, technical_data, fundamental)
        
        # Format the final message
        output = f"""📊 DEEP ANALYSIS: {sym}
━━━━━━━━━━━━━━━━━━━━
🏢 {fundamental.get('sector', 'N/A')} | Sector: {fundamental.get('sector', 'N/A')}
💰 LTP: ₹{ltp:.2f} (Prev: ₹{prev_close:.2f})
📈 52W High: ₹{fundamental.get('high_52w', 0):.2f} | 52W Low: ₹{fundamental.get('low_52w', 0):.2f}
🏦 MCap: {fundamental.get('market_cap', 0)/10000000:.1f} Cr | P/E: {fundamental.get('pe_ratio', 0):.2f} | P/B: {fundamental.get('pb_ratio', 0):.2f} | ROE: {fundamental.get('roe', 0):.1f}% | Div: {fundamental.get('dividend_yield', 0):.2f}%
━━━━━━━━━━━━━━━━━━━━
📌 Technicals
RSI: {rsi_val:.1f} | MACD: {macd_val:.2f} vs Signal: {signal_val:.2f}
BB: U {bb_upper:.2f} | M {bb_mid:.2f} | L {bb_lower:.2f}
EMA20: {ema20_val:.2f} | EMA50: {ema50_val:.2f} | EMA200: {ema200_val:.2f}
ATR(14): {atr_val:.2f}
Pivots: PP {pivots['PP']:.2f} | R1 {pivots['R1']:.2f} | R2 {pivots['R2']:.2f} | S1 {pivots['S1']:.2f} | S2 {pivots['S2']:.2f}
━━━━━━━━━━━━━━━━━━━━
🎯 Targets & Risk
Short-term (1W / 1M / 3M): ₹{targets['short_term']['1W']:.2f} / ₹{targets['short_term']['1M']:.2f} / ₹{targets['short_term']['3M']:.2f}
Long-term (6M / 1Y / 2Y): ₹{targets['long_term']['6M']:.2f} / ₹{targets['long_term']['1Y']:.2f} / ₹{targets['long_term']['2Y']:.2f}
Stop Loss (swing): ₹{targets['stop_loss']:.2f}
━━━━━━━━━━━━━━━━━━━━
📊 Quality Score: {quality_score}/100
━━━━━━━━━━━━━━━━━━━━
🤖 AI Sentiment & Factors
{ai_sentiment}
━━━━━━━━━━━━━━━━━━━━
⚠️ Educational only. Not SEBI registered."""

        return output
        
    except Exception as e:
        print("stock_ai_advisory error:", e)
        return f"An error occurred: {e}"

# ---------- TELEGRAM ----------

@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    print("Received /start from", m.chat.id)
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(telebot.types.KeyboardButton("Stock Analysis"))
    kb.add(telebot.types.KeyboardButton("Swing Trades"))
    bot.send_message(
        m.chat.id,
        "AI NSE Advisory Bot\n\nTap 'Stock Analysis' and send NSE symbol.",
        reply_markup=kb,
    )

@bot.message_handler(func=lambda m: m.text == "Stock Analysis")
def ask_symbol(m):
    print("User requested Stock Analysis:", m.chat.id)
    msg = bot.reply_to(m, "Send NSE symbol (e.g. BEL, RELIANCE):")
    bot.register_next_step_handler(msg, handle_symbol)

def handle_symbol(m):
    sym = (m.text or "").strip().upper()
    print("handle_symbol received:", sym, "from", m.chat.id)

    if not sym or not sym.isalnum():
        bot.reply_to(m, "Send a valid NSE symbol like BEL or RELIANCE.")
        return
    
    # Send typing indicator
    bot.send_chat_action(m.chat.id, 'typing')
    
    try:
        txt = stock_ai_advisory(sym)
    except Exception as e:
        print("handle_symbol error:", e)
        txt = "Error generating AI advisory. Try again."
    
    # Split long messages if needed
    if len(txt) > 4000:
        for i in range(0, len(txt), 4000):
            bot.reply_to(m, txt[i:i+4000])
    else:
        bot.reply_to(m, txt)

@bot.message_handler(func=lambda m: m.text == "Swing Trades")
def swing_trades(m):
    print("User requested Swing Trades:", m.chat.id)
    bot.reply_to(m, "Swing Trades feature is under development.")

@bot.message_handler(func=lambda m: True)
def fallback(m):
    print("Fallback from", m.chat.id, "text:", m.text)
    bot.reply_to(m, "Use the 'Stock Analysis' or 'Swing Trades' button or /start.")

# ---------- HTTP ----------

class RequestHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: str, ct: str = "text/plain"):
        self.send_response(code)
        self.send_header("Content-type", ct)
        self.end_headers()
        self.wfile.write(payload.encode("utf-8"))

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/":
            self._send(200, "OK")
        elif p.path == "/simulate":
            sym = (parse_qs(p.query).get("symbol", ["BEL"])[0] or "BEL").upper()
            try:
                txt = stock_ai_advisory(sym)
                self._send(
                    200,
                    json.dumps({"symbol": sym, "analysis": txt}, ensure_ascii=False),
                    ct="application/json",
                )
            except Exception as e:
                print("simulate error:", e)
                self._send(500, "Simulation error")
        else:
            self._send(404, "Not Found")

def run_http():
    port = int(os.environ.get("PORT", 10000))
    srv = HTTPServer(("0.0.0.0", port), RequestHandler)
    print(f"HTTP server on {port}")
    srv.serve_forever()

# ---------- MAIN ----------

if __name__ == "__main__":
    print("Starting enhanced AI NSE Advisory Bot...")
    threading.Thread(target=run_http, daemon=True).start()
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60)
        except Exception as e:
            print("Polling error:", e)
            time.sleep(10)
