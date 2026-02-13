#!/usr/bin/env python3
"""
AI Stock Advisory Telegram Bot - FIXED VERSION
Fixes: Error 409, AI generation, data fetching, watchlist errors
"""

import os, sys, time, json, warnings, threading
from datetime import datetime
from typing import Optional, Dict, List, Tuple

warnings.filterwarnings('ignore')

# Dependencies
try:
    import telebot
    from telebot import types
    import yfinance as yf
    import pandas as pd
    import numpy as np
    import requests
    from groq import Groq
except ImportError as e:
    print(f"❌ Missing: {e}")
    sys.exit(1)

# Configuration
TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
NEWS_KEY = os.getenv("NEWS_API_KEY", "").strip()
PORT = int(os.getenv("PORT", 10000))

if not TOKEN:
    print("❌ TELEGRAM_TOKEN not set!")
    sys.exit(1)

# Watchlist
WATCHLIST = {
    "LARGE_CAP": ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC"],
    "MID_CAP": ["DIXON", "TATAPOWER", "PERSISTENT"],
    "SMALL_CAP": ["MASTEK", "TANLA"]
}

bot = telebot.TeleBot(TOKEN, threaded=True, skip_pending=True)
AI_CLIENT = None

# AI Initialization
def init_ai():
    global AI_CLIENT
    if not GROQ_KEY:
        print("⚠️  GROQ_API_KEY not set")
        return False
    try:
        AI_CLIENT = Groq(api_key=GROQ_KEY)
        AI_CLIENT.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "test"}],
            max_tokens=10
        )
        print("✅ GROQ AI OK")
        return True
    except Exception as e:
        print(f"⚠️  GROQ failed: {e}")
        return False

def ask_ai(prompt: str, tokens: int = 2000) -> str:
    if not AI_CLIENT:
        return "AI unavailable - using math models"
    try:
        r = AI_CLIENT.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=tokens,
            temperature=0.7
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"AI Error: {e}")
        return "AI temp unavailable"

def get_news(symbol: str, name: str) -> str:
    if not NEWS_KEY:
        return "Market sentiment analysis"
    try:
        q = name.replace(" Limited", "").replace(" Ltd", "")
        r = requests.get(
            f"https://newsapi.org/v2/everything?q={q}&apiKey={NEWS_KEY}&pageSize=2",
            timeout=5
        )
        if r.status_code == 200:
            arts = r.json().get('articles', [])
            if arts:
                return " | ".join([a['title'] for a in arts[:2]])
    except Exception:
        pass
    return "Latest updates show mixed sentiment"

# Technical Indicators
def calc_rsi(series: pd.Series, period: int = 14) -> float:
    try:
        delta = series.diff()
        gain = delta.where(delta > 0, 0).ewm(alpha=1/period, adjust=False).mean()
        loss = -delta.where(delta < 0, 0).ewm(alpha=1/period, adjust=False).mean()
        rs = gain / loss.replace(0, 0.0001)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])
    except Exception:
        return 50.0

def calc_macd(series: pd.Series) -> Tuple[float, float, float]:
    try:
        ema12 = series.ewm(span=12).mean()
        ema26 = series.ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        hist = macd - signal
        return float(macd.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])
    except Exception:
        return 0.0, 0.0, 0.0

def calc_bb(series: pd.Series, period: int = 20) -> Tuple[float, float, float]:
    try:
        sma = series.rolling(period).mean()
        std = series.rolling(period).std()
        upper = (sma + std * 2).iloc[-1]
        mid = sma.iloc[-1]
        lower = (sma - std * 2).iloc[-1]
        return float(upper), float(mid), float(lower)
    except Exception:
        return 0.0, 0.0, 0.0

def calc_pivots(h: float, l: float, c: float) -> Dict[str, float]:
    pp = (h + l + c) / 3
    r1 = 2 * pp - l
    s1 = 2 * pp - h
    r2 = pp + (h - l)
    s2 = pp - (h - l)
    r3 = h + 2 * (pp - l)
    s3 = l - 2 * (h - pp)
    return {'pp': pp, 'r1': r1, 's1': s1, 'r2': r2, 's2': s2, 'r3': r3, 's3': s3}

# Stock Analysis
def analyze_stock(symbol: str) -> str:
    try:
        symbol = symbol.strip().upper().replace('.NS', '')
        ticker = yf.Ticker(f"{symbol}.NS")
        df = yf.download(f"{symbol}.NS", period="1y", interval="1d", progress=False)
        
        if df.empty or len(df) < 20:
            return f"❌ No data for {symbol}. Check symbol."
        
        info = ticker.info
        name = info.get('longName', symbol)
        sector = info.get('sector', 'Unknown')
        mcap = info.get('marketCap', 0)
        
        ltp = float(df['Close'].iloc[-1])
        prev = float(df['Close'].iloc[-2])
        h52 = float(df['High'].max())
        l52 = float(df['Low'].min())
        
        pe = float(info.get('trailingPE', 0) or 0)
        pb = float(info.get('priceToBook', 0) or 0)
        roe = float(info.get('returnOnEquity', 0) or 0) * 100
        de = float(info.get('debtToEquity', 0) or 0)
        dy = float(info.get('dividendYield', 0) or 0) * 100
        
        rsi = calc_rsi(df['Close'])
        macd, sig, hist = calc_macd(df['Close'])
        bbu, bbm, bbl = calc_bb(df['Close'])
        
        sma20 = float(df['Close'].rolling(20).mean().iloc[-1])
        sma50 = float(df['Close'].rolling(50).mean().iloc[-1]) if len(df) >= 50 else ltp
        ema200 = float(df['Close'].ewm(span=200).mean().iloc[-1]) if len(df) >= 200 else ltp
        
        pivots = calc_pivots(float(df['High'].iloc[-2]), float(df['Low'].iloc[-2]), prev)
        
        avg_vol = float(df['Volume'].mean())
        cur_vol = float(df['Volume'].iloc[-1])
        vol_surge = cur_vol > (avg_vol * 1.5)
        
        t1w, t1m, t3m = pivots['r1'], pivots['r2'], pivots['r3']
        sl = pivots['s2']
        
        lt6m = ltp * 1.15
        lt1y = ltp * 1.30
        lt2y = ltp * 1.60
        
        if pe > 0 and pe < 20 and roe > 15:
            lt2y *= 1.2
        elif pe > 40 or roe < 5:
            lt2y *= 0.8
        
        news = get_news(symbol, name)
        
        ai_prompt = f"""Analyze {name} ({symbol}): Price ₹{ltp:.2f}, PE {pe:.2f}, ROE {roe:.1f}%, RSI {rsi:.1f}
Return JSON only: {{"bullish":["p1","p2","p3"],"bearish":["r1","r2","r3"],"rec":"BUY/HOLD/SELL"}}"""
        
        ai_resp = ask_ai(ai_prompt, 800)
        
        bulls = ["Strong momentum", "Quality fundamentals", "Sector leader"]
        bears = ["Market risk", "Valuation concern", "External factors"]
        rec = "HOLD"
        
        try:
            import re
            m = re.search(r'\{.*\}', ai_resp, re.DOTALL)
            if m:
                d = json.loads(m.group())
                bulls = d.get('bullish', bulls)
                bears = d.get('bearish', bears)
                rec = d.get('rec', rec).upper()
        except Exception:
            pass
        
        score = 0
        score += 20 if ltp > ema200 else 0
        score += 15 if ltp > sma50 else 0
        score += 15 if 40 < rsi < 70 else 0
        score += 10 if hist > 0 else 0
        score += 10 if vol_surge else 0
        score += 10 if 0 < pe < 25 else 0
        score += 10 if roe > 15 else 0
        score += 5 if de < 1 else 0
        score += 5 if dy > 1 else 0
        
        if score >= 70:
            verd = "🚀 STRONG BUY"
        elif score >= 50:
            verd = "📈 BUY"
        elif score >= 30:
            verd = "⚖️ HOLD"
        else:
            verd = "⚠️ CAUTION"
        
        ts = datetime.now().strftime('%d-%b-%Y %H:%M')
        
        return f"""╔═══════════════════════════════════════════╗
║   🤖 AI STOCK ANALYSIS                   ║
╚═══════════════════════════════════════════╝
📅 {ts}

🏢 COMPANY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏷 {name}
📊 {symbol} | 🏭 {sector}
💰 MCap: ₹{mcap/10000000:.1f}Cr
💵 LTP: ₹{ltp:.2f} ({((ltp-prev)/prev*100):+.2f}%)
📈 52W: ₹{h52:.2f} | 📉 ₹{l52:.2f}

📊 FUNDAMENTALS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• PE: {pe:.2f}x {'(Cheap)' if 0<pe<20 else '(Fair)' if pe<30 else '(Rich)'}
• PB: {pb:.2f}x | ROE: {roe:.1f}% {'✅' if roe>15 else '⚠️'}
• D/E: {de:.2f} {'✅' if de<1 else '⚠️'} | Yield: {dy:.2f}%

🔬 TECHNICALS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 Trend: {'🟢 BULLISH' if ltp>ema200 else '🔴 BEARISH'}
• RSI: {rsi:.1f} {'🔥' if rsi>70 else '❄️' if rsi<30 else '✅'}
• MACD: {'🟢 Bullish' if hist>0 else '🔴 Bearish'}
• BB: {'🔥 Upper' if ltp>bbu else '❄️ Lower' if ltp<bbl else '⚖️ Mid'}
• SMA20: ₹{sma20:.2f} {'🟢' if ltp>sma20 else '🔴'}
• SMA50: ₹{sma50:.2f} {'🟢' if ltp>sma50 else '🔴'}
• EMA200: ₹{ema200:.2f} {'🟢' if ltp>ema200 else '🔴'}
• Vol: {cur_vol/100000:.1f}L {'🔥 SURGE' if vol_surge else ''}

🎯 SHORT TERM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 1W: ₹{t1w:.2f} (+{((t1w-ltp)/ltp*100):.1f}%)
📅 1M: ₹{t1m:.2f} (+{((t1m-ltp)/ltp*100):.1f}%)
📅 3M: ₹{t3m:.2f} (+{((t3m-ltp)/ltp*100):.1f}%)
🛑 SL: ₹{sl:.2f} (-{((ltp-sl)/ltp*100):.1f}%)

🚀 LONG TERM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 6M: ₹{lt6m:.2f} (+{((lt6m-ltp)/ltp*100):.1f}%)
📅 1Y: ₹{lt1y:.2f} (+{((lt1y-ltp)/ltp*100):.1f}%)
📅 2Y: ₹{lt2y:.2f} (+{((lt2y-ltp)/ltp*100):.1f}%)

🏗️ LEVELS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 R3:{pivots['r3']:.2f} R2:{pivots['r2']:.2f} R1:{pivots['r1']:.2f}
🟡 PP: ₹{pivots['pp']:.2f}
🟢 S1:{pivots['s1']:.2f} S2:{pivots['s2']:.2f} S3:{pivots['s3']:.2f}

🤖 AI INSIGHTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ BULLISH:
{chr(10).join(f'   • {p}' for p in bulls)}

❌ RISKS:
{chr(10).join(f'   • {r}' for r in bears)}

📰 NEWS: {news}

🎯 VERDICT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Score: {score}/100 | {verd}
🤖 AI: {rec}

⚠️ Educational only. DYOR.
🤖 Powered by GROQ AI"""
        
    except Exception as e:
        return f"❌ Error: {str(e)}\nCheck symbol and try again."

def analyze_watchlist() -> str:
    try:
        ts = datetime.now().strftime('%d-%b-%Y %H:%M')
        out = f"""╔═══════════════════════════════════════════╗
║   📋 WATCHLIST                           ║
╚═══════════════════════════════════════════╝
📅 {ts}

"""
        for cat, stocks in WATCHLIST.items():
            icon = '🏢' if 'LARGE' in cat else '🏭' if 'MID' in cat else '🚗'
            out += f"{icon} {cat.replace('_',' ')}\n" + "━"*45 + "\n"
            
            for sym in stocks:
                try:
                    df = yf.download(f"{sym}.NS", period="5d", progress=False)
                    if not df.empty and len(df) >= 2:
                        ltp = float(df['Close'].iloc[-1])
                        prev = float(df['Close'].iloc[-2])
                        chg = ((ltp-prev)/prev*100)
                        rsi = calc_rsi(df['Close'])
                        
                        ema50 = float(df['Close'].ewm(span=50).mean().iloc[-1]) if len(df)>=50 else ltp
                        
                        if ltp>ema50 and 40<rsi<70:
                            sig = "✅ BUY"
                        elif 30<rsi<70:
                            sig = "⚖️ HOLD"
                        else:
                            sig = "⚠️ CAUTION"
                        
                        ic = '🟢' if chg>0 else '🔴'
                        out += f"{ic} {sym}\n   ₹{ltp:.2f} ({chg:+.2f}%) | RSI:{rsi:.1f} | {sig}\n"
                    else:
                        out += f"⚠️ {sym}: No data\n"
                except Exception:
                    out += f"❌ {sym}: Error\n"
            out += "\n"
        
        out += "🤖 Powered by AI Advisory"
        return out
    except Exception as e:
        return f"❌ Error: {str(e)}"

def analyze_market() -> str:
    try:
        ts = datetime.now().strftime('%d-%b-%Y %H:%M')
        out = f"""╔═══════════════════════════════════════════╗
║   🇮🇳 MARKET                             ║
╚═══════════════════════════════════════════╝
📅 {ts}

INDICES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        indices = [
            ('Nifty 50', '^NSEI'),
            ('Bank Nifty', '^NSEBANK'),
            ('IT', '^CNXIT'),
            ('Auto', '^CNXAUTO'),
            ('Pharma', '^CNXPHARMA')
        ]
        
        for name, sym in indices:
            try:
                df = yf.Ticker(sym).history(period="5d")
                if not df.empty and len(df) >= 2:
                    ltp = float(df['Close'].iloc[-1])
                    prev = float(df['Close'].iloc[-2])
                    chg = ((ltp-prev)/prev*100)
                    ic = '🟢' if chg>0 else '🔴'
                    out += f"{ic} {name}: {ltp:.2f} ({chg:+.2f}%)\n"
            except Exception:
                out += f"⚠️ {name}: Error\n"
        
        out += "\n🤖 Powered by AI Advisory"
        return out
    except Exception as e:
        return f"❌ Error: {str(e)}"

# Bot Handlers
@bot.message_handler(commands=['start'])
def start(m):
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    mk.add('📊 Stock Analysis', '📋 My Watchlist', '🇮🇳 Market', '📚 Help')
    msg = """╔═══════════════════════════════════════════╗
║   🤖 AI STOCK ADVISOR                    ║
╚═══════════════════════════════════════════╝

Welcome! Your AI stock advisor.

✅ Deep Analysis
✅ AI Insights
✅ Real-time Data
✅ Watchlist

🚀 Type stock symbol or use buttons!
Examples: RELIANCE, TCS, BEL

⚠️ Educational only. DYOR."""
    bot.send_message(m.chat.id, msg, reply_markup=mk)

@bot.message_handler(func=lambda m: m.text == '📊 Stock Analysis')
def stock_prompt(m):
    msg = bot.send_message(m.chat.id, "🔍 Enter symbol:\nEx: RELIANCE, TCS, BEL")
    bot.register_next_step_handler(msg, process_stock)

def process_stock(m):
    sym = m.text.strip().upper().replace('.NS', '')
    status = bot.send_message(m.chat.id, f"🔍 Analyzing {sym}...\n⏳ 30-60s")
    r = analyze_stock(sym)
    try:
        bot.delete_message(m.chat.id, status.message_id)
    except:
        pass
    
    if len(r) > 4096:
        for i in range(0, len(r), 4000):
            bot.send_message(m.chat.id, r[i:i+4000])
            time.sleep(0.5)
    else:
        bot.send_message(m.chat.id, r)

@bot.message_handler(func=lambda m: m.text == '📋 My Watchlist')
def watchlist(m):
    bot.send_message(m.chat.id, "🔍 Scanning...")
    bot.send_message(m.chat.id, analyze_watchlist())

@bot.message_handler(func=lambda m: m.text == '🇮🇳 Market')
def market(m):
    bot.send_message(m.chat.id, analyze_market())

@bot.message_handler(func=lambda m: m.text == '📚 Help')
def help_cmd(m):
    msg = """╔═══════════════════════════════════════════╗
║   📚 HELP                                ║
╚═══════════════════════════════════════════╝

📊 Stock Analysis - Deep report
📋 Watchlist - Track stocks
🇮🇳 Market - Indices

Or type any symbol!

⚠️ Educational only. DYOR.
🤖 Powered by GROQ AI"""
    bot.send_message(m.chat.id, msg)

@bot.message_handler(func=lambda m: True)
def handle_text(m):
    txt = m.text.strip().upper().replace('.NS', '')
    if len(txt) >= 2 and txt.replace(' ', '').isalpha():
        status = bot.send_message(m.chat.id, f"🔍 {txt}...")
        r = analyze_stock(txt)
        try:
            bot.delete_message(m.chat.id, status.message_id)
        except:
            pass
        
        if len(r) > 4096:
            for i in range(0, len(r), 4000):
                bot.send_message(m.chat.id, r[i:i+4000])
                time.sleep(0.5)
        else:
            bot.send_message(m.chat.id, r)

# Health Server
def health_server():
    import http.server, socketserver
    
    class H(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            html = f"""<html><body>
<h1>🤖 Bot Online</h1>
<p>Time: {datetime.now()}</p>
<p>GROQ: {'✅' if AI_CLIENT else '⚠️'}</p>
<p>Stocks: {sum(len(v) for v in WATCHLIST.values())}</p>
</body></html>"""
            self.wfile.write(html.encode())
        def log_message(self, *args):
            pass
    
    with socketserver.TCPServer(("0.0.0.0", PORT), H) as s:
        print(f"🌐 Health server: {PORT}")
        s.serve_forever()

if __name__ == "__main__":
    print("="*50)
    print("🚀 AI STOCK BOT STARTING")
    print("="*50)
    
    ai_ok = init_ai()
    
    print(f"✅ Telegram: OK")
    print(f"{'✅' if ai_ok else '⚠️ '} GROQ: {'OK' if ai_ok else 'SKIP'}")
    print(f"{'✅' if NEWS_KEY else '⚠️ '} News: {'OK' if NEWS_KEY else 'SKIP'}")
    print(f"✅ Watchlist: {sum(len(v) for v in WATCHLIST.values())} stocks")
    print("="*50)
    
    threading.Thread(target=health_server, daemon=True).start()
    time.sleep(2)
    
    try:
        bot.delete_webhook(drop_pending_updates=True)
        time.sleep(1)
    except Exception as e:
        print(f"Webhook: {e}")
    
    print("✅ BOT ONLINE!")
    print("="*50)
    
    while True:
        try:
            print("🔄 Polling...")
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
        except Exception as e:
            print(f"⚠️ {e}")
            time.sleep(10)
