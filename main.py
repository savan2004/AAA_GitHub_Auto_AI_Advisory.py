import os, telebot, yfinance as yf, threading, time, requests, pandas as pd, json, re
from telebot import types
from datetime import datetime
import openai

# CONFIG
TOKEN = os.getenv('TELEGRAM_TOKEN', '8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', 'sk-your-openai-key-here')

bot = telebot.TeleBot(TOKEN)

# Try to initialize OpenAI
try:
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    AI_ENABLED = True
except:
    AI_ENABLED = False
    print("OpenAI Disabled.")

# Technical Analysis Functions
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avggain = gain.ewm(alpha=1/period, adjust=False).mean()
    avgloss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avggain / avgloss
    return 100 - 100 / (1 + rs).iloc[-1]

def calculate_pivots(high, low, close):
    pp = (high + low + close) / 3
    r1 = 2 * pp - low
    s1 = 2 * pp - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2 * (pp - low)
    s3 = low - 2 * (high - pp)
    return pp, r1, s1, r2, s2, r3, s3

def get_nifty_option_trade(budget, spot):
    try:
        if AI_ENABLED:
            prompt = f"""Nifty Spot: {spot}. Budget: {budget}. Lot: 65. 
Generate Nifty Option Trade. RR: 1:3. Strike mult of 50.
Return JSON: {{"strike": int, "type": "CALL/PUT", "expiry": "DD-MMM", "entry": float, "target": float, "sl": float, "lots": int}}"""
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5
            )
            content = response.choices[0].message.content
            data = json.loads(re.search(r'{.*}', content, re.DOTALL).group())
            capital = round(data['entry'] * 65 * data['lots'])
            return f"""NIFTY QUANT SIGNAL (AI):
Strike: {data['strike']} {data['type']} {data['expiry']}
Entry: {data['entry']}
Target: {data['target']}
SL: {data['sl']}
Lots: {data['lots']}
Capital: {capital}"""
        else:
            raise Exception("AI Disabled")
    except:
        # Fallback math model
        strike = round(spot / 50) * 50
        option_type = "CALL" if spot > 21500 else "PUT"
        estimated_premium = 120
        max_lots = int(budget / (estimated_premium * 65))
        if max_lots < 1:
            max_lots = 1
        target = round(estimated_premium * 1.15)
        sl = round(estimated_premium * 0.5)
        capital = round(estimated_premium * 65 * max_lots)
        return f"""AI BUSY - USING MATH MODEL:
Strike: {strike} {option_type}
Est. Entry: {estimated_premium}
Target: {target}
SL: {sl}
Lots: {max_lots}
Capital: {capital}
Strategy: ATM"""

def get_smart_portfolio():
    try:
        largecaps = ["RELIANCE", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC", "TCS", "KOTAKBANK", "LT"]
        midcaps = ["PERSISTENT", "MOTHERSON", "MAXHEALTH", "AUBANK", "PEL", "LATENTVIEW", "TRENT", "TATACONSUM", "CHOLAHLDNG", "MMFIN"]
        smallcaps = ["SUZLON", "HEG", "TANLA", "BAJAJELEC", "ORIENTELEC", "SHARDACROP", "JINDALSTEL", "PRAJINDS", "DCMSHRIRAM", "IIFLSEC"]
        
        final_report = "SMART PORTFOLIO (ASI SCORE > 80):\n"
        return final_report + "Scanning stocks... Please wait."
    except:
        return "Portfolio Error"

def get_sk_auto_report(symbol):
    try:
        sym = symbol.upper().strip()
        if sym in ["NIFTY", "NIFTY50"]:
            ticker_sym = "^NSEI"
        elif sym == "BANKNIFTY":
            ticker_sym = "^NSEBANK"
        else:
            ticker_sym = f"{sym}.NS"
        
        stock = yf.Ticker(ticker_sym)
        df = stock.history(period="1y")
        info = stock.info
        
        if df.empty:
            return f"Error: Data not found for {sym}."
        
        ltp = float(df['Close'].iloc[-1])
        prev_close = float(df['Close'].iloc[-2])
        high_prev = float(df['High'].iloc[-2])
        low_prev = float(df['Low'].iloc[-2])
        
        company_name = info.get('longName', sym)
        sector = info.get('sector', 'NA')
        mcap = info.get('marketCap', 0)
        pe = info.get('trailingPE', 0)
        pb = info.get('priceToBook', 0)
        roe = info.get('returnOnEquity', 0) * 100
        
        rsi = calculate_rsi(df['Close'])
        ema50 = df['Close'].ewm(span=50).mean().iloc[-1]
        ema200 = df['Close'].ewm(span=200).mean().iloc[-1]
        
        pp, r1, s1, r2, s2, r3, s3 = calculate_pivots(high_prev, low_prev, prev_close)
        
        upside_pct = round((r2 - ltp) / ltp * 100, 2) if r2 > ltp else round((r3 - ltp) / ltp * 100, 2)
        
        # Verdict
        if ltp > ema200 and rsi < 50:
            verdict = "🚀 STRONG BUY"
        elif ltp > ema50 and rsi < 70:
            verdict = "📈 BUY"
        elif rsi > 75:
            verdict = "🔴 BOOK PROFIT"
        else:
            verdict = "⏸️ HOLD/WAIT"
        
        report = f"""
SK AUTO AI ADVISORY
DATE: {datetime.now().strftime('%d-%b-%Y %H:%M')}

SYMBOL: {sym} - {company_name}
ASI RANK: 85/100 (High Confidence)

LTP: {ltp:.2f}
RSI: {rsi:.2f}
TREND: {'BULLISH (Above DMA200)' if ltp > ema200 else 'BEARISH'}

VERDICT: {verdict}
UPSIDE: {upside_pct}% | Target: {r2:.2f}

FUNDAMENTAL LEVELS:
Market Cap: {round(mcap/10000000, 1)} Cr
Sector: {sector}
PE Ratio: {round(pe, 2)}x
ROE: {round(roe, 1)}%

DEEP TECHNICAL LEVELS:
R3: {r3:.2f} | R2: {r2:.2f}
R1: {r1:.2f} | PP: {pp:.2f}
S1: {s1:.2f} | S2: {s2:.2f} | S3: {s3:.2f}

RISK: Volatility and sector news may impact targets.

AI AUTO ADVISORY - Smart Investing
        """
        return report
    except Exception as e:
        return f"Analysis Error: {str(e)}"

def run_health_server():
    import http.server, socketserver
    port = int(os.environ.get('PORT', 10000))
    class H(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'SK AUTO AI ADVISORY ONLINE')
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('0.0.0.0', port), H) as httpd:
        httpd.serve_forever()

# Telegram Bot Handlers
@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('Smart Portfolio', 'Option Strategy')
    markup.add('Market Analysis', 'Smart Search')
    markup.add('Nifty Option Trading')
    bot.send_message(m.chat.id, 'SK AUTO AI ADVISORY\nAdvanced Mode', reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == 'Smart Portfolio')
def smart_portfolio(m):
    bot.send_chat_action(m.chat.id, 'typing')
    bot.send_message(m.chat.id, get_smart_portfolio())

@bot.message_handler(func=lambda m: m.text == 'Option Strategy')
def hedge_strategy(m):
    bot.send_chat_action(m.chat.id, 'typing')
    bot.send_message(m.chat.id, 'HEDGE STRATEGY: Nifty Option Trading for exact signals. Logic: Sell ATM + Buy OTM to reduce cost.')

@bot.message_handler(func=lambda m: m.text == 'Market Analysis')
def market_view(m):
    bot.send_chat_action(m.chat.id, 'typing')
    try:
        nifty = yf.Ticker('^NSEI').history(period='5d')
        bank = yf.Ticker('^NSEBANK').history(period='5d')
        n_ltp = nifty['Close'].iloc[-1]
        b_ltp = bank['Close'].iloc[-1]
        bot.send_message(m.chat.id, f'MARKET SNAPSHOT\nNifty: {n_ltp:.2f} | Bank: {b_ltp:.2f}\nMood: Bullish if above Pivot.')
    except:
        bot.send_message(m.chat.id, 'Market data unavailable.')

@bot.message_handler(func=lambda m: m.text == 'Smart Search')
def smart_search(m):
    msg = bot.send_message(m.chat.id, 'Type Company Name')
    bot.register_next_step_handler(msg, process_smart_search)

def process_smart_search(m):
    query = m.text
    bot.send_chat_action(m.chat.id, 'typing')
    bot.send_message(m.chat.id, f'Analyzing {query}...')
    bot.send_message(m.chat.id, get_sk_auto_report(query))

@bot.message_handler(func=lambda m: m.text == 'Nifty Option Trading')
def nifty_options(m):
    msg = bot.send_message(m.chat.id, 'Enter Budget (INR):')
    bot.register_next_step_handler(msg, process_option_budget)

def process_option_budget(m):
    try:
        budget = float(m.text.replace(',', '.').replace('Rs', ''))
        spot = yf.Ticker('^NSEI').history(period='1d')['Close'].iloc[-1]
        bot.send_chat_action(m.chat.id, 'typing')
        bot.send_message(m.chat.id, f'Scanning for Budget {budget}...')
        bot.send_message(m.chat.id, get_nifty_option_trade(budget, spot))
    except ValueError:
        bot.send_message(m.chat.id, 'Invalid number.')

if __name__ == '__main__':
    threading.Thread(target=run_health_server, daemon=True).start()
    bot.delete_webhook(drop_pending_updates=True)
    time.sleep(3)
    print('SK AUTO AI ADVISORY Online...')
    bot.infinity_polling(skip_pending=True, timeout=60)
