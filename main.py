"""
main.py — AAA Advisory Bot v5.3 (Fixed v6.0)

Full Production Bot with all fixes integrated:
- Fixed AI timeout crashes (ai_engine.py v6.0)
- Fixed news reliability (market_news.py v3.0)
- All 6 issues resolved
- Ready for production deployment
"""

import os
import logging
import threading
import time
from datetime import datetime, date, timedelta
from collections import defaultdict

try:
    import telebot
    from telebot import types
except ImportError:
    print("ERROR: pyTelegramBotAPI not installed. Run: pip install pyTelegramBotAPI==4.21.0")
    exit(1)

from config import (
    BOT_VERSION, LOG_LEVEL, TIER_LIMITS, TG_MAX_MSG_CHARS, TG_CHUNK_SIZE,
    CACHE_TTL_LIVE, NIFTY_PE_EXPENSIVE, NIFTY_PE_FAIR_HI, NIFTY_PE_FAIR_LO,
)
from api_utils import setup_logging, install_key_filter, API_RATE_LIMITER
from data_engine import (
    get_live_price, get_info, get_hist, calc_rsi, get_nifty_pe, batch_quotes
)
from technical_indicators import (
    calc_rsi, calc_ema, calc_macd, calc_atr, calc_adx, calc_bollinger,
    trend_label, rsi_label, swing_signal, ema_series
)
from ai_engine import (
    ai_available, ai_insights, ai_chat_respond, ai_topic_respond,
    fetch_news, fetch_market_news, get_live_market_context, test_ai_providers,
    debug_ai_status, add_to_chat, get_chat_history, clear_chat
)
from market_news import get_market_news, get_stock_news
from swing_trades import get_swing_trades
from fundamentals import get_fundamentals, fmt_cr

# ── Setup ──────────────────────────────────────────────────────────────────────
setup_logging(level=LOG_LEVEL, structured=False)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
if not TOKEN:
    logger.error("TELEGRAM_TOKEN not set in environment variables")
    exit(1)

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
logger.info(f"Bot initialized: {BOT_VERSION}")

# ── User State ──────────────────────────────────────────────────────────────────
_user_tier: dict = defaultdict(lambda: "free")
_user_state: dict = {}  # for multi-step conversations
_portfolio: dict = defaultdict(lambda: {"holdings": {}, "created_at": datetime.now()})

def get_user_tier(uid: int) -> str:
    """Get user tier (free/paid)."""
    return _user_tier.get(uid, "free")

def set_user_tier(uid: int, tier: str):
    """Set user tier."""
    if tier not in ("free", "paid"):
        return False
    _user_tier[uid] = tier
    logger.info(f"User {uid} tier set to: {tier}")
    return True

def check_rate_limit(uid: int) -> bool:
    """Check if user has exceeded rate limit."""
    if not API_RATE_LIMITER.is_allowed(uid):
        remaining = API_RATE_LIMITER.remaining(uid)
        return False
    return True

def get_daily_ai_limit(uid: int) -> int:
    """Get daily AI call limit for user tier."""
    tier = get_user_tier(uid)
    return TIER_LIMITS.get(tier, 50)

# ── Helper Functions ────────────────────────────────────────────────────────────

def safe_send(chat_id: int, text: str, reply_markup=None, parse_mode="HTML"):
    """
    FIX 6.0: Split long messages to avoid Telegram's 4096 char limit.
    """
    if not text:
        return
    
    text = text.strip()
    if len(text) <= TG_MAX_MSG_CHARS:
        try:
            bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"send_message failed: {e}")
    else:
        # Split into chunks
        chunks = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > TG_CHUNK_SIZE:
                if current:
                    chunks.append(current)
                current = line
            else:
                current += ("\n" + line) if current else line
        if current:
            chunks.append(current)
        
        for i, chunk in enumerate(chunks):
            try:
                markup = reply_markup if i == len(chunks) - 1 else None
                bot.send_message(chat_id, chunk, reply_markup=markup, parse_mode=parse_mode)
                time.sleep(0.5)  # Rate limit Telegram sends
            except Exception as e:
                logger.error(f"send_message chunk {i} failed: {e}")

# ── Portfolio Commands ──────────────────────────────────────────────────────────

@bot.message_handler(commands=["buy"])
def cmd_buy(message):
    """Buy stock: /buy RELIANCE 2500 10"""
    try:
        uid = message.from_user.id
        parts = message.text.split()
        if len(parts) < 4:
            safe_send(uid, "❌ Usage: /buy SYMBOL PRICE QTY\nExample: /buy RELIANCE 2500 10")
            return
        
        sym = parts[1].upper()
        price = float(parts[2])
        qty = int(parts[3])
        
        if sym not in _portfolio[uid]["holdings"]:
            _portfolio[uid]["holdings"][sym] = {"qty": 0, "avg_price": 0}
        
        h = _portfolio[uid]["holdings"][sym]
        total_cost = (h["avg_price"] * h["qty"]) + (price * qty)
        h["qty"] += qty
        h["avg_price"] = total_cost / h["qty"] if h["qty"] > 0 else 0
        
        safe_send(uid, f"✅ Bought {qty} of {sym} @ ₹{price}\n"
                       f"Avg Price: ₹{h['avg_price']:.2f}\n"
                       f"Total Qty: {h['qty']}")
    except Exception as e:
        logger.error(f"cmd_buy: {e}")
        safe_send(uid, f"❌ Error: {str(e)[:100]}")

@bot.message_handler(commands=["sell"])
def cmd_sell(message):
    """Sell stock: /sell RELIANCE 2700 5"""
    try:
        uid = message.from_user.id
        parts = message.text.split()
        if len(parts) < 4:
            safe_send(uid, "❌ Usage: /sell SYMBOL PRICE QTY\nExample: /sell RELIANCE 2700 5")
            return
        
        sym = parts[1].upper()
        price = float(parts[2])
        qty = int(parts[3])
        
        if sym not in _portfolio[uid]["holdings"] or _portfolio[uid]["holdings"][sym]["qty"] < qty:
            safe_send(uid, f"❌ Not enough {sym} to sell")
            return
        
        h = _portfolio[uid]["holdings"][sym]
        gain = (price - h["avg_price"]) * qty
        h["qty"] -= qty
        if h["qty"] == 0:
            del _portfolio[uid]["holdings"][sym]
        
        safe_send(uid, f"✅ Sold {qty} of {sym} @ ₹{price}\n"
                       f"Cost Basis: ₹{h['avg_price']:.2f}\n"
                       f"Gain/Loss: ₹{gain:,.2f} ({gain/(h['avg_price']*qty)*100:+.2f}%)")
    except Exception as e:
        logger.error(f"cmd_sell: {e}")
        safe_send(uid, f"❌ Error: {str(e)[:100]}")

@bot.message_handler(commands=["portfolio"])
def cmd_portfolio(message):
    """Show portfolio."""
    try:
        uid = message.from_user.id
        holdings = _portfolio[uid]["holdings"]
        
        if not holdings:
            safe_send(uid, "📊 Portfolio is empty.\n/buy SYMBOL PRICE QTY to add holdings")
            return
        
        lines = ["📊 <b>PORTFOLIO</b>\n━━━━━━━━━━━━━━━━━━━━"]
        total_cost = 0
        total_value = 0
        
        for sym, h in sorted(holdings.items()):
            try:
                ltp = get_live_price(sym)
                if not ltp:
                    continue
                
                cost = h["avg_price"] * h["qty"]
                value = ltp * h["qty"]
                gain = value - cost
                gain_pct = (gain / cost * 100) if cost > 0 else 0
                
                total_cost += cost
                total_value += value
                
                lines.append(f"\n{sym}")
                lines.append(f"  Qty: {h['qty']} @ ₹{h['avg_price']:.2f} avg")
                lines.append(f"  LTP: ₹{ltp:.2f}")
                lines.append(f"  Value: ₹{value:,.2f}")
                lines.append(f"  P&L: ₹{gain:,.2f} ({gain_pct:+.2f}%)")
            except Exception as e:
                logger.debug(f"portfolio {sym}: {e}")
        
        if total_cost > 0:
            total_gain = total_value - total_cost
            total_gain_pct = (total_gain / total_cost * 100)
            lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"Total Cost: ₹{total_cost:,.2f}")
            lines.append(f"Total Value: ₹{total_value:,.2f}")
            lines.append(f"Total P&L: ₹{total_gain:,.2f} ({total_gain_pct:+.2f}%)")
        
        safe_send(uid, "\n".join(lines))
    except Exception as e:
        logger.error(f"cmd_portfolio: {e}")
        safe_send(uid, f"❌ Error: {str(e)[:100]}")

# ── Stock Analysis Commands ─────────────────────────────────────────────────────

@bot.message_handler(commands=["stock"])
def cmd_stock(message):
    """Analyze stock: /stock RELIANCE"""
    try:
        uid = message.from_user.id
        parts = message.text.split()
        if len(parts) < 2:
            safe_send(uid, "❌ Usage: /stock SYMBOL\nExample: /stock RELIANCE")
            return
        
        sym = parts[1].upper()
        
        try:
            ltp = get_live_price(sym)
            info = get_info(sym) or {}
            df = get_hist(sym, "1y")
            
            if df is None or df.empty or ltp is None:
                safe_send(uid, f"❌ No data for {sym}")
                return
            
            rsi = calc_rsi(df["Close"])
            ema20 = calc_ema(df["Close"], 20)
            ema50 = calc_ema(df["Close"], 50)
            macd_line, signal, hist = calc_macd(df["Close"])
            atr = calc_atr(df)
            
            pe = info.get("pe")
            roe = info.get("roe")
            pb = info.get("pb")
            
            trend = trend_label(df["Close"])
            rsi_state = rsi_label(rsi)
            
            lines = [
                f"📈 <b>{sym}</b>",
                f"━━━━━━━━━━━━━━━━━━━━",
                f"💰 Price: ₹{ltp:.2f}",
                f"📊 RSI: {rsi:.1f} ({rsi_state})",
                f"📈 EMA20: ₹{ema20:.2f}",
                f"📉 EMA50: ₹{ema50:.2f}",
                f"🔄 MACD: {macd_line:.2f} (Signal: {signal:.2f})",
                f"🌊 ATR: ₹{atr:.2f}",
                f"📊 Trend: <b>{trend}</b>",
                f"━━━━━━━━━━━━━━━━━━━━",
                f"PE: {pe if pe else 'N/A'}",
                f"ROE: {roe if roe else 'N/A'}%",
                f"PB: {pb if pb else 'N/A'}",
            ]
            
            # AI Insights
            if ai_available():
                insight = ai_insights(sym, ltp, rsi, macd_line, trend, 
                                      str(pe or "N/A"), str(roe or "N/A"), atr)
                lines.append(f"\n{insight}")
            
            safe_send(uid, "\n".join(lines))
        except Exception as e:
            logger.error(f"stock analysis {sym}: {e}")
            safe_send(uid, f"❌ Analysis failed: {str(e)[:80]}")
    except Exception as e:
        logger.error(f"cmd_stock: {e}")
        safe_send(uid, f"❌ Error: {str(e)[:100]}")

# ── Screener Commands ───────────────────────────────────────────────────────────

@bot.message_handler(commands=["scan"])
def cmd_scan(message):
    """Scan market for setups."""
    try:
        uid = message.from_user.id
        if not check_rate_limit(uid):
            safe_send(uid, "⏳ Rate limit exceeded. Please wait a moment.")
            return
        
        safe_send(uid, "🔍 Scanning 50 stocks (this may take 30s)...")
        
        # Quick scan of top stocks
        stocks = [
            "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
            "SBIN", "BAJFINANCE", "ITC", "LT", "WIPRO"
        ]
        
        results = []
        for sym in stocks:
            try:
                ltp = get_live_price(sym)
                info = get_info(sym) or {}
                df = get_hist(sym, "6mo")
                
                if df is None or df.empty or ltp is None:
                    continue
                
                rsi = calc_rsi(df["Close"])
                trend = trend_label(df["Close"])
                chg = ((df["Close"].iloc[-1] - df["Close"].iloc[-2]) / df["Close"].iloc[-2] * 100)
                
                signal = swing_signal(rsi, trend, chg)
                if "✅" in signal or "⚡" in signal:
                    results.append(f"• {sym}: {signal}")
            except Exception:
                pass
        
        if results:
            msg = "✅ <b>SETUPS FOUND</b>\n\n" + "\n".join(results)
        else:
            msg = "⏳ No clear setups right now. Market is consolidating."
        
        safe_send(uid, msg)
    except Exception as e:
        logger.error(f"cmd_scan: {e}")
        safe_send(uid, f"❌ Error: {str(e)[:100]}")

@bot.message_handler(commands=["swing"])
def cmd_swing(message):
    """Get swing trade setups."""
    try:
        uid = message.from_user.id
        if not check_rate_limit(uid):
            safe_send(uid, "⏳ Rate limit exceeded. Please wait a moment.")
            return
        
        safe_send(uid, "🔄 Scanning swing trades (this may take 60s)...")
        result = get_swing_trades("conservative")
        safe_send(uid, result)
    except Exception as e:
        logger.error(f"cmd_swing: {e}")
        safe_send(uid, f"❌ Error: {str(e)[:100]}")

# ── Market Commands ────────────────────────────────────────────────────────────

@bot.message_handler(commands=["breadth"])
def cmd_breadth(message):
    """Market breadth."""
    try:
        uid = message.from_user.id
        lines = ["📊 <b>MARKET BREADTH</b>\n━━━━━━━━━━━━━━━━━━━━"]
        
        indices = [("^NSEI", "NIFTY 50"), ("^NSEBANK", "BANK NIFTY"), ("^CNXIT", "NIFTY IT")]
        
        for ticker, name in indices:
            try:
                import yfinance as yf
                df = yf.download(ticker, period="5d", progress=False, auto_adjust=True)
                if not df.empty and len(df) >= 2:
                    ltp = float(df["Close"].iloc[-1])
                    prev = float(df["Close"].iloc[-2])
                    chg = ((ltp - prev) / prev * 100)
                    h = float(df["High"].iloc[-1])
                    l = float(df["Low"].iloc[-1])
                    
                    # RSI
                    rsi = calc_rsi(df["Close"])
                    rsi_icon = "🔴" if rsi > 70 else ("🟢" if rsi < 30 else "🟡")
                    
                    lines.append(f"\n{name}")
                    lines.append(f"  LTP: ₹{ltp:,.0f} ({chg:+.2f}%)")
                    lines.append(f"  RSI: {rsi:.1f} {rsi_icon}")
                    lines.append(f"  H/L: ₹{h:,.0f} / ₹{l:,.0f}")
            except Exception as e:
                logger.debug(f"breadth {name}: {e}")
        
        # Nifty PE
        try:
            pe = get_nifty_pe()
            if pe:
                verdict = "🟢 CHEAP" if pe < 19 else ("🟡 FAIR" if pe < 22 else "🔴 EXPENSIVE")
                lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
                lines.append(f"Nifty PE: {pe:.1f} {verdict}")
        except Exception:
            pass
        
        safe_send(uid, "\n".join(lines))
    except Exception as e:
        logger.error(f"cmd_breadth: {e}")
        safe_send(uid, f"❌ Error: {str(e)[:100]}")

@bot.message_handler(commands=["news"])
def cmd_news(message):
    """Market news."""
    try:
        uid = message.from_user.id
        news = get_market_news(5)
        safe_send(uid, news)
    except Exception as e:
        logger.error(f"cmd_news: {e}")
        safe_send(uid, f"❌ Error fetching news: {str(e)[:80]}")

# ── AI Chat Commands ────────────────────────────────────────────────────────────

@bot.message_handler(commands=["ask"])
def cmd_ask(message):
    """Ask AI freely: /ask What stocks should I buy for growth?"""
    try:
        uid = message.from_user.id
        if not ai_available():
            safe_send(uid, "❌ AI not configured. Set GROQ_API_KEY in environment.")
            return
        
        prompt = message.text[5:].strip()
        if not prompt:
            safe_send(uid, "❌ Usage: /ask YOUR_QUESTION")
            return
        
        response = ai_chat_respond(uid, prompt)
        safe_send(uid, response)
    except Exception as e:
        logger.error(f"cmd_ask: {e}")
        safe_send(uid, f"❌ Error: {str(e)[:100]}")

# ── Help & Status ───────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start", "help"])
def cmd_help(message):
    """Show help."""
    uid = message.from_user.id
    help_text = f"""
<b>AAA Advisory Bot v{BOT_VERSION}</b>

<b>📊 Analysis:</b>
/stock SYMBOL — Analyze stock (RSI, MACD, AI insights)
/scan — Quick market scan
/swing — Swing trade setups
/breadth — Market breadth & indices

<b>💼 Portfolio:</b>
/buy SYMBOL PRICE QTY — Buy stock
/sell SYMBOL PRICE QTY — Sell stock
/portfolio — Show portfolio + P&L

<b>📰 Market:</b>
/news — Market news
/ask QUESTION — Ask AI anything

<b>⚙️ Settings:</b>
/status — System status
/tier — Check your tier
"""
    safe_send(uid, help_text)

@bot.message_handler(commands=["status"])
def cmd_status(message):
    """Show system status."""
    try:
        uid = message.from_user.id
        
        status = debug_ai_status()
        keys_status = status.get("keys", {})
        
        lines = ["⚙️ <b>SYSTEM STATUS</b>\n━━━━━━━━━━━━━━━━━━━━"]
        lines.append(f"Version: {BOT_VERSION}")
        lines.append(f"Time: {datetime.now().strftime('%H:%M:%S IST')}")
        lines.append(f"\n🔑 API Keys:")
        for key, val in keys_status.items():
            lines.append(f"  {key}: {val}")
        
        lines.append(f"\n💾 Cache:")
        lines.append(f"  Cached: {status.get('context_cached', False)}")
        lines.append(f"  Age: {status.get('context_age_sec', 0):.0f}s")
        
        safe_send(uid, "\n".join(lines))
    except Exception as e:
        logger.error(f"cmd_status: {e}")
        safe_send(uid, f"❌ Error: {str(e)[:100]}")

# ── Default Handler ─────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    """Handle free-form messages as AI chat."""
    try:
        uid = message.from_user.id
        if not ai_available():
            safe_send(uid, "❌ AI not configured.\n\nUse /help for available commands.")
            return
        
        response = ai_chat_respond(uid, message.text)
        safe_send(uid, response)
    except Exception as e:
        logger.error(f"handle_message: {e}")
        safe_send(uid, f"❌ Error: {str(e)[:100]}")

# ── Polling ─────────────────────────────────────────────────────────────────────

def main():
    """Start bot."""
    logger.info(f"🤖 Bot started: {BOT_VERSION}")
    logger.info(f"AI Available: {ai_available()}")
    
    try:
        bot.infinity_polling(skip_pending=True)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}")
        raise

if __name__ == "__main__":
    main()
