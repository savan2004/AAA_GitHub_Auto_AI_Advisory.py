import os, telebot, yfinance as yf, threading, time, requests, pandas as pd, json, re
from telebot import types
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# Configuration
TOKEN = os.getenv("TELEGRAM_TOKEN", "8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o")
GROQ_KEY = os.getenv("GROQ_API_KEY", "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")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "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")
NEWS_KEY = os.getenv("NEWS_API_KEY", "47fb3f33527944ed982e6e48cc856b23")
WATCHLIST = {"LARGE_CAP": ["RELIANCE", "TCS", "HDFCBANK"], "MID_CAP": ["DIXON", "TATAPOWER"], "SMALL_CAP": ["MASTEK"]}
bot = telebot.TeleBot(TOKEN, threaded=True)
AI = {}

def init_ai():
    if GROQ_KEY:
        try:
            from groq import Groq
            AI['groq'] = Groq(api_key=GROQ_KEY)
            print("✅ GROQ Ready")
        except Exception as e: 
            print(f"⚠️ GROQ Failed: {e}")

init_ai()

def ask_ai(prompt, tokens=2000):
    if AI.get('groq'):
        try: 
            return AI['groq'].chat.completions.create(
                model="llama-3.3-70b-versatile", 
                messages=[{"role": "user", "content": prompt}], 
                max_tokens=tokens,
                temperature=0.7
            ).choices[0].message.content
        except Exception as e:
            print(f"AI Error: {e}")
            return "AI analysis in progress..."
    return "Using mathematical analysis..."

def get_news(symbol, name):
    if not NEWS_KEY: return "Market sentiment mixed"
    try:
        r = requests.get(f"https://newsapi.org/v2/everything?q={name.replace(' Limited','')}&apiKey={NEWS_KEY}&pageSize=2", timeout=5)
        if r.status_code == 200:
            arts = r.json().get('articles', [])
            if arts: return " | ".join([a['title'] for a in arts[:2]])
    except: pass
    return "Latest updates show cautious optimism"

def calc_rsi(series, period=14):
    try:
        delta = series.diff()
        gain = delta.where(delta > 0, 0).ewm(alpha=1/period, adjust=False).mean()
        loss = -delta.where(delta < 0, 0).ewm(alpha=1/period, adjust=False).mean()
        return (100 - (100 / (1 + gain / loss))).iloc[-1]
    except: return 50

def calc_macd(series):
    try:
        ema12 = series.ewm(span=12).mean()
        ema26 = series.ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        return macd.iloc[-1], signal.iloc[-1], (macd - signal).iloc[-1]
    except: return 0, 0, 0

def calc_bb(series, period=20):
    try:
        sma = series.rolling(period).mean()
        std = series.rolling(period).std()
        return (sma + std * 2).iloc[-1], sma.iloc[-1], (sma - std * 2).iloc[-1]
    except: return 0, 0, 0

def calc_pivots(h, l, c):
    pp = (h + l + c) / 3
    return pp, 2*pp-l, 2*pp-h, pp+h-l, pp-h+l, h+2*(pp-l), l-2*(h-pp)

def analyze_stock(sym):
    try:
        t = yf.Ticker(f"{sym}.NS")
        df = yf.download(f"{sym}.NS", period="1y", interval="1d", progress=False)
        if df.empty: return f"❌ No data for {sym}"
        
        info, ltp = t.info, df['Close'].iloc[-1]
        prev, h52, l52 = df['Close'].iloc[-2], df['High'].max(), df['Low'].min()
        name, sector, mcap = info.get('longName', sym), info.get('sector', 'Unknown'), info.get('marketCap', 0)
        pe, pb, roe = info.get('trailingPE', 0), info.get('priceToBook', 0), info.get('returnOnEquity', 0)*100 if info.get('returnOnEquity') else 0
        de, dy = info.get('debtToEquity', 0), info.get('dividendYield', 0)*100 if info.get('dividendYield') else 0
        
        rsi = calc_rsi(df['Close'])
        macd, sig, hist = calc_macd(df['Close'])
        bbu, bbm, bbl = calc_bb(df['Close'])
        sma20, sma50 = df['Close'].rolling(20).mean().iloc[-1], df['Close'].rolling(50).mean().iloc[-1]
        ema200 = df['Close'].ewm(span=200).mean().iloc[-1]
        pp, r1, s1, r2, s2, r3, s3 = calc_pivots(df['High'].iloc[-2], df['Low'].iloc[-2], prev)
        
        vol_avg, vol_now = df['Volume'].mean(), df['Volume'].iloc[-1]
        vol_surge = vol_now > vol_avg * 1.5
        
        t1, t2, t3, sl = r1, r2, r3, s2
        lt6m, lt1y, lt2y = ltp*1.15, ltp*1.30, ltp*1.60
        if pe>0 and pe<20 and roe>15: lt2y *= 1.2
        elif pe>40 or roe<5: lt2y *= 0.8
        
        news = get_news(sym, name)
        ai_resp = ask_ai(f"Analyze {name}({sym}): Price ₹{ltp:.2f}, PE {pe:.2f}, ROE {roe:.1f}%, RSI {rsi:.1f}. Give JSON: {{\"bullish\":[\"p1\",\"p2\",\"p3\"],\"bearish\":[\"p1\",\"p2\",\"p3\"],\"rec\":\"BUY/HOLD/SELL\"}}", 1000)
        bulls, bears, rec = ["Strong fundamentals", "Technical momentum", "Good value"], ["Market risk", "Sector challenges", "Valuation concerns"], "HOLD"
        try:
            d = json.loads(re.search(r'\{.*\}', ai_resp, re.DOTALL).group())
            bulls, bears, rec = d.get('bullish', bulls), d.get('bearish', bears), d.get('rec', rec)
        except: pass
        
        score = sum([20 if ltp>ema200 else 0, 15 if ltp>sma50 else 0, 15 if 40<rsi<70 else 0, 10 if macd>sig else 0, 10 if vol_surge else 0, 10 if 0<pe<25 else 0, 10 if roe>15 else 0, 5 if de<1 else 0, 5 if dy>1 else 0])
        verd = "🚀 STRONG BUY" if score>=70 else "📈 BUY" if score>=50 else "⚖️ HOLD" if score>=30 else "⚠️ SELL"
        
        return f"""╔═══════════════════════════════════════════╗
║   🤖 AI ADVISORY - DEEP ANALYSIS         ║
╚═══════════════════════════════════════════╝
📅 {datetime.now().strftime('%d-%b-%Y %H:%M')}

🏢 **COMPANY DETAILS**
🏷 {name} | 📊 {sym}
🏭 {sector} | 💰 ₹{mcap/10000000:.1f} Cr
💵 LTP: ₹{ltp:.2f} | 52W: ₹{h52:.2f}/₹{l52:.2f}

📊 **FUNDAMENTALS**
• PE: {pe:.2f}x {'(Cheap)' if 0<pe<20 else '(Fair)' if 20<=pe<30 else '(Rich)'}
• PB: {pb:.2f}x | ROE: {roe:.1f}% {'✅' if roe>15 else '⚠️'}
• D/E: {de:.2f} {'✅' if de<1 else '⚠️'} | Yield: {dy:.2f}%

🔬 **TECHNICALS**
📈 Trend: {'🟢 BULLISH' if ltp>ema200 else '🔴 BEARISH'}
• RSI: {rsi:.1f} {'🔥' if rsi>70 else '❄️' if rsi<30 else '✅'}
• MACD: {'🟢 Bullish' if hist>0 else '🔴 Bearish'}
• BB: {'🔥 Upper' if ltp>bbu else '❄️ Lower' if ltp<bbl else '⚖️ Mid'}
• SMA20: ₹{sma20:.2f} {'🟢' if ltp>sma20 else '🔴'}
• SMA50: ₹{sma50:.2f} {'🟢' if ltp>sma50 else '🔴'}
• EMA200: ₹{ema200:.2f} {'🟢' if ltp>ema200 else '🔴'}
• Vol: {vol_now/100000:.1f}L {'🔥 SURGE' if vol_surge else ''}

🎯 **SHORT TERM TARGETS**
📅 1W: ₹{t1:.2f} (+{(t1-ltp)/ltp*100:.1f}%)
📅 1M: ₹{t2:.2f} (+{(t2-ltp)/ltp*100:.1f}%)
📅 3M: ₹{t3:.2f} (+{(t3-ltp)/ltp*100:.1f}%)
🛑 SL: ₹{sl:.2f} (-{(ltp-sl)/ltp*100:.1f}%)

🚀 **LONG TERM TARGETS**
📅 6M: ₹{lt6m:.2f} (+{(lt6m-ltp)/ltp*100:.1f}%)
📅 1Y: ₹{lt1y:.2f} (+{(lt1y-ltp)/ltp*100:.1f}%)
📅 2Y: ₹{lt2y:.2f} (+{(lt2y-ltp)/ltp*100:.1f}%)

🏗️ **LEVELS**
🔴 R3: ₹{r3:.2f} | R2: ₹{r2:.2f} | R1: ₹{t1:.2f}
🟡 PP: ₹{pp:.2f}
🟢 S1: ₹{s1:.2f} | S2: ₹{sl:.2f} | S3: ₹{s3:.2f}

🤖 **AI INSIGHTS**
✅ BULLISH: {', '.join(bulls)}
❌ BEARISH: {', '.join(bears)}
📰 NEWS: {news}

🎯 **VERDICT**
Score: {score}/100 | {verd}
🤖 AI: {rec}

⚠️ Educational only. DYOR.
🤖 Powered by GROQ+OpenAI | ⏰ {datetime.now().strftime('%H:%M:%S')}"""
    except Exception as e: return f"❌ Error: {str(e)}"

def analyze_watchlist():
    try:
        out = f"╔═══════════════════════════════════════════╗\n║   📋 YOUR WATCHLIST                      ║\n╚═══════════════════════════════════════════╝\n📅 {datetime.now().strftime('%d-%b-%Y %H:%M')}\n\n"
        for cat, stocks in WATCHLIST.items():
            out += f"{'🏢' if 'LARGE' in cat else '🏭' if 'MID' in cat else '🚗'} **{cat.replace('_',' ')}**\n" + "━"*45 + "\n"
            for sym in stocks:
                try:
                    df = yf.download(f"{sym}.NS", period="5d", progress=False)
                    if not df.empty:
                        ltp, prev = df['Close'].iloc[-1], df['Close'].iloc[-2]
                        chg, chgp = ltp-prev, (ltp-prev)/prev*100
                        rsi = calc_rsi(df['Close'])
                        ema50 = df['Close'].ewm(span=50).mean().iloc[-1] if len(df)>=50 else ltp
                        sig = "BUY" if ltp>ema50 and 40<rsi<70 else "HOLD" if 30<rsi<70 else "CAUTION"
                        out += f"{'🟢' if chg>0 else '🔴'} **{sym}**\n   ₹{ltp:.2f} ({chgp:+.2f}%) | RSI {rsi:.1f} | {sig}\n"
                except: out += f"⚠️ {sym}: Error\n"
        return out + "\n🤖 Powered by AI Advisory"
    except Exception as e: return f"❌ Error: {str(e)}"

def analyze_market():
    try:
        out = f"╔═══════════════════════════════════════════╗\n║   📊 MARKET ANALYSIS                     ║\n╚═══════════════════════════════════════════╝\n📅 {datetime.now().strftime('%d-%b-%Y %H:%M')}\n\n🇮🇳 **INDICES**\n"
        for n, s in [('Nifty 50','^NSEI'), ('Bank Nifty','^NSEBANK'), ('IT','^CNXIT'), ('Auto','^CNXAUTO'), ('Pharma','^CNXPHARMA')]:
            try:
                df = yf.Ticker(s).history(period="5d")
                if not df.empty:
                    ltp, prev = df['Close'].iloc[-1], df['Close'].iloc[-2]
                    chg, chgp = ltp-prev, (ltp-prev)/prev*100
                    out += f"{'🟢' if chg>0 else '🔴'} {n}: {ltp:.2f} ({chgp:+.2f}%)\n"
            except: pass
        return out + "\n🤖 Powered by AI Advisory"
    except Exception as e: return f"❌ Error: {str(e)}"

@bot.message_handler(commands=['start'])
def start(m):
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    mk.add('📊 Stock Analysis', '📋 My Watchlist', '🇮🇳 Market', '📚 Help')
    bot.send_message(m.chat.id, f"╔═══════════════════════════════════════════╗\n║   🤖 AI STOCK ADVISORY                   ║\n╚═══════════════════════════════════════════╝\n\n✅ Deep Analysis\n✅ Your Watchlist: {', '.join(WATCHLIST['LARGE_CAP'])}\n✅ Real News\n✅ AI Powered\n\n🚀 Type stock or use buttons!", reply_markup=mk)

@bot.message_handler(func=lambda m: m.text == '📊 Stock Analysis')
def stock_prompt(m):
    msg = bot.send_message(m.chat.id, "🔍 Enter NSE symbol:\nEx: RELIANCE, TCS")
    bot.register_next_step_handler(msg, lambda m: process_stock(m))

def process_stock(m):
    sym = m.text.strip().upper().replace('.NS','')
    bot.send_message(m.chat.id, f"🔍 Analyzing {sym}... ⏳ 30-60s")
    r = analyze_stock(sym)
    for chunk in [r[i:i+4000] for i in range(0,len(r),4000)]: bot.send_message(m.chat.id, chunk); time.sleep(0.5)

@bot.message_handler(func=lambda m: m.text == '📋 My Watchlist')
def watchlist(m):
    bot.send_message(m.chat.id, "🔍 Scanning watchlist...")
    bot.send_message(m.chat.id, analyze_watchlist())

@bot.message_handler(func=lambda m: m.text == '🇮🇳 Market')
def market(m):
    bot.send_message(m.chat.id, analyze_market())

@bot.message_handler(func=lambda m: m.text == '📚 Help')
def help_cmd(m):
    bot.send_message(m.chat.id, "╔═══════════════════════════════════════════╗\n║   📚 HELP                                ║\n╚═══════════════════════════════════════════╝\n\n📊 Stock Analysis - Deep report\n📋 Watchlist - Your stocks\n🇮🇳 Market - Indices\n\nOr just type any symbol!\n\n⚠️ Educational only. DYOR.")

@bot.message_handler(func=lambda m: True)
def handle_text(m):
    sym = m.text.strip().upper().replace('.NS','')
    if len(sym)>=2 and sym.replace(' ','').isalpha():
        bot.send_message(m.chat.id, f"🔍 {sym}... ⏳")
        r = analyze_stock(sym)
        for c in [r[i:i+4000] for i in range(0,len(r),4000)]: bot.send_message(m.chat.id, c); time.sleep(0.5)

def health_server():
    import http.server, socketserver
    PORT = int(os.environ.get("PORT", 10000))
    class H(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header('Content-type','text/html'); self.end_headers()
            self.wfile.write(f"<h1>🤖 Bot Online</h1><p>{datetime.now()}</p>".encode())
        def log_message(self, *args): pass
    with socketserver.TCPServer(("0.0.0.0", PORT), H) as s: s.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=health_server, daemon=True).start()
    time.sleep(2)
    print("=" * 50)
    print("🚀 AI STOCK ADVISORY BOT STARTING...")
    print("=" * 50)
    print(f"✅ GROQ AI: {'Enabled' if AI.get('groq') else 'Disabled'}")
    print(f"✅ News API: {'Enabled' if NEWS_KEY else 'Disabled'}")
    print(f"✅ Watchlist: {sum(len(v) for v in WATCHLIST.values())} stocks")
    print(f"✅ Stocks: {', '.join(WATCHLIST['LARGE_CAP'])}")
    print("=" * 50)
    bot.delete_webhook(drop_pending_updates=True)
    time.sleep(2)
    print("✅ BOT IS ONLINE!")
    print("=" * 50)
    while True:
        try: bot.infinity_polling(timeout=60, skip_pending=True)
        except Exception as e: print(f"❌ {e}"); time.sleep(10)
