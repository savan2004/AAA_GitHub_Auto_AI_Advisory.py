"""
main.py — AAA Advisory Bot v6.0

Fresh production entrypoint with fixed imports, clean handlers,
safe Telegram chunking, and no dependency on missing get_nifty_pe().
"""

import os
import logging
import time
from datetime import datetime
from collections import defaultdict

try:
    import telebot
except ImportError:
    print("ERROR: pyTelegramBotAPI not installed. Run: pip install pyTelegramBotAPI==4.21.0")
    raise SystemExit(1)

from config import (
    BOT_VERSION,
    LOG_LEVEL,
    TIER_LIMITS,
    TG_MAX_MSG_CHARS,
    TG_CHUNK_SIZE,
)
from api_utils import setup_logging, API_RATE_LIMITER
from data_engine import get_live_price, get_info, get_hist
from technical_indicators import (
    calc_rsi,
    calc_ema,
    calc_macd,
    calc_atr,
    trend_label,
    rsi_label,
    swing_signal,
)
from ai_engine import (
    ai_available,
    ai_insights,
    ai_chat_respond,
    debug_ai_status,
)
from market_news import get_market_news
from swing_trades import get_swing_trades

setup_logging(level=LOG_LEVEL, structured=False)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
if not TOKEN:
    logger.error("TELEGRAM_TOKEN not set in environment variables")
    raise SystemExit(1)

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
logger.info("Bot initialized: %s", BOT_VERSION)

_user_tier = defaultdict(lambda: "free")
_user_state = {}
_portfolio = defaultdict(lambda: {"holdings": {}, "created_at": datetime.now()})


def get_user_tier(uid: int) -> str:
    return _user_tier.get(uid, "free")


def set_user_tier(uid: int, tier: str) -> bool:
    if tier not in ("free", "paid"):
        return False
    _user_tier[uid] = tier
    logger.info("User %s tier set to: %s", uid, tier)
    return True


def check_rate_limit(uid: int) -> bool:
    return API_RATE_LIMITER.is_allowed(uid)


def get_daily_ai_limit(uid: int) -> int:
    tier = get_user_tier(uid)
    return TIER_LIMITS.get(tier, 50)


def safe_send(chat_id: int, text: str, reply_markup=None, parse_mode: str = "HTML"):
    if not text:
        return

    text = str(text).strip()
    if len(text) <= TG_MAX_MSG_CHARS:
        try:
            bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            logger.error("send_message failed: %s", e)
        return

    chunks = []
    current = ""
    for line in text.split("\n"):
        next_len = len(current) + len(line) + 1
        if next_len > TG_CHUNK_SIZE and current:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line

    if current:
        chunks.append(current)

    for i, chunk in enumerate(chunks):
        try:
            markup = reply_markup if i == len(chunks) - 1 else None
            bot.send_message(chat_id, chunk, reply_markup=markup, parse_mode=parse_mode)
            time.sleep(0.35)
        except Exception as e:
            logger.error("send_message chunk %s failed: %s", i, e)


@bot.message_handler(commands=["buy"])
def cmd_buy(message):
    uid = message.chat.id
    try:
        parts = message.text.split()
        if len(parts) < 4:
            safe_send(uid, "❌ Usage: /buy SYMBOL PRICE QTY\nExample: /buy RELIANCE 2500 10")
            return

        sym = parts[1].upper()
        price = float(parts[2])
        qty = int(parts[3])

        if price <= 0 or qty <= 0:
            safe_send(uid, "❌ Price and quantity must be positive.")
            return

        if sym not in _portfolio[uid]["holdings"]:
            _portfolio[uid]["holdings"][sym] = {"qty": 0, "avg_price": 0.0}

        holding = _portfolio[uid]["holdings"][sym]
        total_cost = (holding["avg_price"] * holding["qty"]) + (price * qty)
        holding["qty"] += qty
        holding["avg_price"] = total_cost / holding["qty"]

        safe_send(
            uid,
            f"✅ Bought {qty} of {sym} @ ₹{price:.2f}\n"
            f"Avg Price: ₹{holding['avg_price']:.2f}\n"
            f"Total Qty: {holding['qty']}",
        )
    except Exception as e:
        logger.error("cmd_buy: %s", e)
        safe_send(uid, f"❌ Error: {str(e)[:100]}")


@bot.message_handler(commands=["sell"])
def cmd_sell(message):
    uid = message.chat.id
    try:
        parts = message.text.split()
        if len(parts) < 4:
            safe_send(uid, "❌ Usage: /sell SYMBOL PRICE QTY\nExample: /sell RELIANCE 2700 5")
            return

        sym = parts[1].upper()
        price = float(parts[2])
        qty = int(parts[3])

        if price <= 0 or qty <= 0:
            safe_send(uid, "❌ Price and quantity must be positive.")
            return

        if sym not in _portfolio[uid]["holdings"]:
            safe_send(uid, f"❌ No holdings found for {sym}")
            return

        holding = _portfolio[uid]["holdings"][sym]
        if holding["qty"] < qty:
            safe_send(uid, f"❌ Not enough {sym} to sell")
            return

        avg_price = holding["avg_price"]
        gain = (price - avg_price) * qty
        invested = avg_price * qty
        gain_pct = (gain / invested * 100) if invested > 0 else 0.0

        holding["qty"] -= qty
        if holding["qty"] == 0:
            del _portfolio[uid]["holdings"][sym]

        safe_send(
            uid,
            f"✅ Sold {qty} of {sym} @ ₹{price:.2f}\n"
            f"Cost Basis: ₹{avg_price:.2f}\n"
            f"Gain/Loss: ₹{gain:,.2f} ({gain_pct:+.2f}%)",
        )
    except Exception as e:
        logger.error("cmd_sell: %s", e)
        safe_send(uid, f"❌ Error: {str(e)[:100]}")


@bot.message_handler(commands=["portfolio"])
def cmd_portfolio(message):
    uid = message.chat.id
    try:
        holdings = _portfolio[uid]["holdings"]
        if not holdings:
            safe_send(uid, "📊 Portfolio is empty.\n/buy SYMBOL PRICE QTY to add holdings")
            return

        lines = ["📊 <b>PORTFOLIO</b>", "━━━━━━━━━━━━━━━━━━━━"]
        total_cost = 0.0
        total_value = 0.0

        for sym, holding in sorted(holdings.items()):
            try:
                ltp = get_live_price(sym)
                if ltp is None:
                    continue

                cost = holding["avg_price"] * holding["qty"]
                value = ltp * holding["qty"]
                pnl = value - cost
                pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0

                total_cost += cost
                total_value += value

                lines.extend(
                    [
                        f"",
                        f"{sym}",
                        f"  Qty: {holding['qty']} @ ₹{holding['avg_price']:.2f} avg",
                        f"  LTP: ₹{ltp:.2f}",
                        f"  Value: ₹{value:,.2f}",
                        f"  P&L: ₹{pnl:,.2f} ({pnl_pct:+.2f}%)",
                    ]
                )
            except Exception as e:
                logger.debug("portfolio %s: %s", sym, e)

        if total_cost > 0:
            total_pnl = total_value - total_cost
            total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
            lines.extend(
                [
                    "",
                    "━━━━━━━━━━━━━━━━━━━━",
                    f"Total Cost: ₹{total_cost:,.2f}",
                    f"Total Value: ₹{total_value:,.2f}",
                    f"Total P&L: ₹{total_pnl:,.2f} ({total_pnl_pct:+.2f}%)",
                ]
            )

        safe_send(uid, "\n".join(lines))
    except Exception as e:
        logger.error("cmd_portfolio: %s", e)
        safe_send(uid, f"❌ Error: {str(e)[:100]}")


@bot.message_handler(commands=["stock"])
def cmd_stock(message):
    uid = message.chat.id
    parts = message.text.split()
    if len(parts) < 2:
        safe_send(uid, "Usage: /stock SYMBOL\nExample: /stock RELIANCE")
        return
    sym = parts[1].upper()
    safe_send(uid, f"⏳ Analyzing <b>{sym}</b>… (~10s)")
    import threading
    def _run_stock(u=uid, s=sym):
        try:
            _stock_inner(u, s)
        except Exception as _e:
            logger.error("stock %s: %s", s, _e)
            safe_send(u, f"⚠️ Error analyzing {s}. Try again.")
    threading.Thread(target=_run_stock, daemon=True).start()


def _stock_inner(uid, sym):
    try:
        ltp = get_live_price(sym)
        info = get_info(sym) or {}
        df = get_hist(sym, "1y")

        if df is None or df.empty or ltp is None:
            safe_send(uid, f"❌ No data for {sym}")
            return

        close = df["Close"]
        rsi = calc_rsi(close)
        ema20 = calc_ema(close, 20)
        ema50 = calc_ema(close, 50)
        macd_line, signal, _ = calc_macd(close)
        atr = calc_atr(df)

        pe = info.get("pe")
        roe = info.get("roe")
        pb = info.get("pb")

        trend = trend_label(close)
        rsi_state = rsi_label(rsi)

        entry_low = max(0.0, ltp - 0.5 * atr)
        entry_high = ltp + 0.5 * atr
        stop_loss = max(0.0, ltp - 1.2 * atr)
        target = ltp + 2.0 * atr
        rr = ((target - ltp) / (ltp - stop_loss)) if (ltp - stop_loss) > 0 else 0.0
        setup = swing_signal(rsi, trend, 0)

        lines = [
            f"📈 <b>{sym}</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"💰 Price: ₹{ltp:.2f}",
            f"📊 RSI: {rsi:.1f} ({rsi_state})",
            f"📈 EMA20: ₹{ema20:.2f}",
            f"📉 EMA50: ₹{ema50:.2f}",
            f"🔄 MACD: {macd_line:.2f} (Signal: {signal:.2f})",
            f"🌊 ATR: ₹{atr:.2f}",
            f"📊 Trend: <b>{trend}</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"PE: {pe if pe is not None else 'N/A'}",
            f"ROE: {roe if roe is not None else 'N/A'}%",
            f"PB: {pb if pb is not None else 'N/A'}",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🎯 Entry Zone: ₹{entry_low:.2f} – ₹{entry_high:.2f}",
            f"🛑 Stop Loss: ₹{stop_loss:.2f}",
            f"🎯 Target: ₹{target:.2f}",
            f"⚖️ Risk:Reward: {rr:.2f}x",
            f"📌 Swing View: {setup}",
        ]

        if ai_available():
            try:
                insight = ai_insights(
                    sym,
                    ltp,
                    rsi,
                    macd_line,
                    trend,
                    str(pe if pe is not None else "N/A"),
                    str(roe if roe is not None else "N/A"),
                    atr,
                )
                if insight:
                    lines.append("")
                    lines.append(insight)
            except Exception as e:
                logger.debug("ai_insights %s: %s", sym, e)

        safe_send(uid, "\n".join(lines))
    except Exception as e:
        logger.error("cmd_stock: %s", e)
        safe_send(uid, f"❌ Analysis failed: {str(e)[:100]}")


@bot.message_handler(commands=["scan"])
def cmd_scan(message):
    uid = message.chat.id
    try:
        if not check_rate_limit(uid):
            safe_send(uid, "⏳ Rate limit exceeded. Please wait a moment.")
            return

        stocks = [
            "RELIANCE",
            "TCS",
            "HDFCBANK",
            "INFY",
            "ICICIBANK",
            "SBIN",
            "BAJFINANCE",
            "ITC",
            "LT",
            "WIPRO",
        ]

        safe_send(uid, f"🔍 Scanning {len(stocks)} stocks (this may take 30s)...")

        results = []
        for sym in stocks:
            try:
                ltp = get_live_price(sym)
                df = get_hist(sym, "6mo")
                if df is None or df.empty or ltp is None or len(df) < 2:
                    continue

                close = df["Close"]
                rsi = calc_rsi(close)
                trend = trend_label(close)
                chg = ((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) * 100
                signal = swing_signal(rsi, trend, chg)

                if "✅" in signal or "⚡" in signal or "BUY" in signal.upper():
                    results.append(f"• {sym}: {signal}")
            except Exception as e:
                logger.debug("scan %s: %s", sym, e)

        if results:
            safe_send(uid, "✅ <b>SETUPS FOUND</b>\n\n" + "\n".join(results))
        else:
            safe_send(uid, "⏳ No clear setups right now. Market is consolidating.")
    except Exception as e:
        logger.error("cmd_scan: %s", e)
        safe_send(uid, f"❌ Error: {str(e)[:100]}")


@bot.message_handler(commands=["swing"])
def cmd_swing(message):
    uid = message.chat.id
    try:
        if not check_rate_limit(uid):
            safe_send(uid, "⏳ Rate limit exceeded. Please wait a moment.")
            return

        safe_send(uid, "🔄 Scanning swing trades (this may take 60s)...")
        result = get_swing_trades("conservative")
        safe_send(uid, result)
    except Exception as e:
        logger.error("cmd_swing: %s", e)
        safe_send(uid, f"❌ Error: {str(e)[:100]}")


@bot.message_handler(commands=["breadth"])
def cmd_breadth(message):
    uid = message.chat.id
    try:
        import yfinance as yf

        lines = ["📊 <b>MARKET BREADTH</b>", "━━━━━━━━━━━━━━━━━━━━"]
        indices = [
            ("^NSEI", "NIFTY 50"),
            ("^NSEBANK", "BANK NIFTY"),
            ("^CNXIT", "NIFTY IT"),
        ]

        for ticker, name in indices:
            try:
                df = yf.download(ticker, period="5d", progress=False, auto_adjust=True)
                if df.empty or len(df) < 2:
                    continue

                ltp = float(df["Close"].iloc[-1])
                prev = float(df["Close"].iloc[-2])
                chg = ((ltp - prev) / prev) * 100
                day_high = float(df["High"].iloc[-1])
                day_low = float(df["Low"].iloc[-1])
                rsi = calc_rsi(df["Close"])
                ema20 = calc_ema(df["Close"], 20)
                trend = "Bullish" if ltp > ema20 else "Bearish"
                rsi_icon = "🔴" if rsi > 70 else "🟢" if rsi < 30 else "🟡"
                rsi_text = "Overbought" if rsi > 70 else "Oversold" if rsi < 30 else "Neutral"

                lines.extend(
                    [
                        "",
                        f"{name}",
                        f"  LTP: ₹{ltp:,.0f} ({chg:+.2f}%)",
                        f"  RSI: {rsi:.1f} {rsi_icon} {rsi_text}",
                        f"  EMA20: ₹{ema20:,.0f}",
                        f"  Trend: {trend}",
                        f"  H/L: ₹{day_high:,.0f} / ₹{day_low:,.0f}",
                    ]
                )
            except Exception as e:
                logger.debug("breadth %s: %s", name, e)

        safe_send(uid, "\n".join(lines))
    except Exception as e:
        logger.error("cmd_breadth: %s", e)
        safe_send(uid, f"❌ Error: {str(e)[:100]}")


@bot.message_handler(commands=["news"])
def cmd_news(message):
    uid = message.chat.id
    try:
        news = get_market_news(5)
        safe_send(uid, news)
    except Exception as e:
        logger.error("cmd_news: %s", e)
        safe_send(uid, f"❌ Error fetching news: {str(e)[:80]}")


@bot.message_handler(commands=["ask"])
def cmd_ask(message):
    uid = message.chat.id
    prompt = message.text[4:].strip() if len(message.text) > 4 else ""
    if not prompt:
        safe_send(uid, "Usage: /ask YOUR_QUESTION\nExample: /ask Nifty outlook for today")
        return

    safe_send(uid, "⏳ Thinking… (~8s)")

    import threading
    def _run():
        try:
            if not ai_available():
                safe_send(uid,
                    "⚠️ <b>AI keys not configured.</b>\n"
                    "Add GROQ_API_KEY in Render → Environment (free at console.groq.com)")
                return
            response = ai_chat_respond(uid, prompt)
            safe_send(uid, response or "⚠️ AI returned empty. Try again.")
        except Exception as e:
            logger.error("cmd_ask: %s", e)
            safe_send(uid, "⚠️ AI error. Please try again in a moment.")
    threading.Thread(target=_run, daemon=True).start()


def main():
    logger.info("🤖 Bot started: %s", BOT_VERSION)
    logger.info("AI Available: %s", ai_available())

    WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")
    TOKEN       = os.getenv("TELEGRAM_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    PORT        = int(os.getenv("PORT", "8000"))

    if WEBHOOK_URL:
        # ── WEBHOOK MODE (Render / production) ────────────────────────────────
        # Step 1: always delete any existing webhook first to avoid 409 conflict
        try:
            bot.delete_webhook(drop_pending_updates=True)
            logger.info("Old webhook deleted")
            time.sleep(1)
        except Exception as e:
            logger.warning("delete_webhook: %s", e)

        # Step 2: set new webhook
        webhook_endpoint = f"{WEBHOOK_URL}/webhook/{TOKEN}"
        try:
            bot.set_webhook(url=webhook_endpoint)
            logger.info("Webhook set: %s", webhook_endpoint)
        except Exception as e:
            logger.error("set_webhook failed: %s", e)
            raise

        # Step 3: run Flask to receive webhook updates
        from flask import Flask, request as flask_request, abort
        flask_app = Flask(__name__)

        @flask_app.route("/", methods=["GET"])
        def health():
            return {"status": "ok", "bot": BOT_VERSION, "ai": ai_available()}, 200

        # Use the module-level TOKEN for the route path
        _tok = TOKEN  # module-level, validated at startup
        @flask_app.route(f"/webhook/{_tok}", methods=["POST"])
        def webhook():
            if flask_request.headers.get("content-type") != "application/json":
                abort(400)
            try:
                json_str = flask_request.get_data(as_text=True)
                update   = telebot.types.Update.de_json(json_str)
                bot.process_new_updates([update])
            except Exception as _we:
                logger.error("webhook process error: %s", _we)
            return "OK", 200

        logger.info("Starting Flask on port %d (webhook mode)", PORT)
        flask_app.run(host="0.0.0.0", port=PORT, debug=False)

    else:
        # ── POLLING MODE (local development only) ─────────────────────────────
        # Delete webhook first so polling works cleanly
        try:
            bot.delete_webhook(drop_pending_updates=True)
            logger.info("Webhook cleared for polling mode")
            time.sleep(1)
        except Exception as e:
            logger.warning("delete_webhook: %s", e)

        logger.info("Starting polling mode (local dev)")
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error("Bot polling error: %s", e)
            raise


if __name__ == "__main__":
    main()
