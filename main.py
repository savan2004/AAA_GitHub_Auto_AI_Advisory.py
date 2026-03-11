import logging
import os
from typing import Optional, Tuple

import pandas as pd
import telebot
import yfinance as yf
from telebot.types import KeyboardButton, ReplyKeyboardMarkup

from llm_wrapper import call_llm_with_limits

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series) -> Tuple[float, float]:
    exp1 = series.ewm(span=12, adjust=False).mean()
    exp2 = series.ewm(span=26, adjust=False).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1])


def bollinger_bands(series: pd.Series, period: int = 20) -> Tuple[float, float, float]:
    sma = series.rolling(window=period).mean().iloc[-1]
    std = series.rolling(window=period).std().iloc[-1]
    upper = sma + 2 * std
    lower = sma - 2 * std
    return float(upper), float(sma), float(lower)


def atr(df: pd.DataFrame, period: int = 14) -> float:
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return float(tr.rolling(window=period).mean().iloc[-1])


def get_fundamental_info(symbol: str) -> dict:
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.info or {}
        return {
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "company_name": info.get("longName", info.get("shortName", symbol)),
            "market_cap": info.get("marketCap", 0) or 0,
            "pe_ratio": info.get("trailingPE", 0) or 0,
            "pb_ratio": info.get("priceToBook", 0) or 0,
            "roe": (info.get("returnOnEquity", 0) or 0) * 100,
            "dividend_yield": (info.get("dividendYield", 0) or 0) * 100,
            "high_52w": info.get("fiftyTwoWeekHigh", 0) or 0,
            "low_52w": info.get("fiftyTwoWeekLow", 0) or 0,
            "prev_close": info.get("regularMarketPreviousClose", 0) or 0,
            "volume": info.get("volume", 0) or 0,
            "avg_volume": info.get("averageVolume", 0) or 0,
        }
    except Exception as e:
        logger.error(f"Fundamental error for {symbol}: {e}")
        return {}


def calculate_targets(price: float, atr_val: float, trend: str, low_52w=None, high_52w=None) -> dict:
    if trend == "Bullish":
        short = {"1W": price + atr_val * 1.2, "1M": price + atr_val * 3, "3M": price + atr_val * 6}
        long = {"6M": price + atr_val * 12, "1Y": price + atr_val * 20, "2Y": price + atr_val * 35}
        stop_loss = price - atr_val * 2
        if high_52w:
            cap = high_52w * 2
            for key in long:
                long[key] = min(long[key], cap)
    else:
        short = {"1W": price - atr_val * 1.2, "1M": price - atr_val * 3, "3M": price - atr_val * 6}
        long = {"6M": price - atr_val * 10, "1Y": price - atr_val * 15, "2Y": price - atr_val * 20}
        stop_loss = price + atr_val * 2
        floor = price * 0.1
        for key in short:
            short[key] = max(short[key], floor)
        for key in long:
            long[key] = max(long[key], floor)
        if low_52w:
            for key in long:
                long[key] = max(long[key], low_52w * 0.9)

    return {"short_term": short, "long_term": long, "stop_loss": stop_loss}


def calculate_quality_score(df: pd.DataFrame, fund: dict) -> int:
    close = df["Close"]
    score = 0

    ema20 = ema(close, 20).iloc[-1]
    ema50 = ema(close, 50).iloc[-1]
    ema200 = ema(close, 200).iloc[-1]
    if close.iloc[-1] > ema20:
        score += 4
    if close.iloc[-1] > ema50:
        score += 5
    if close.iloc[-1] > ema200:
        score += 6

    rsi_val = rsi(close, 14).iloc[-1]
    if 40 <= rsi_val <= 60:
        score += 10
    elif 30 <= rsi_val <= 70:
        score += 5

    vol_avg = df["Volume"].rolling(20).mean().iloc[-1]
    if vol_avg > 0:
        if df["Volume"].iloc[-1] > vol_avg * 1.5:
            score += 5
        elif df["Volume"].iloc[-1] > vol_avg:
            score += 3

    atr_val = atr(df)
    atr_pct = (atr_val / close.iloc[-1]) * 100 if close.iloc[-1] else 0
    if atr_pct < 2:
        score += 10
    elif atr_pct < 4:
        score += 7
    elif atr_pct < 6:
        score += 4

    if fund:
        pe = fund.get("pe_ratio", 0)
        roe = fund.get("roe", 0)
        pb = fund.get("pb_ratio", 0)
        div = fund.get("dividend_yield", 0)
        mcap = fund.get("market_cap", 0)

        if pe and pe < 20:
            score += 15
        elif pe and pe < 30:
            score += 10
        elif pe and pe < 40:
            score += 5

        if roe > 20:
            score += 15
        elif roe > 15:
            score += 12
        elif roe > 10:
            score += 8
        elif roe > 5:
            score += 4

        if 1 < pb < 3:
            score += 10
        elif pb <= 1 and pb > 0:
            score += 8
        elif pb < 5 and pb > 0:
            score += 5

        if div > 3:
            score += 10
        elif div > 2:
            score += 7
        elif div > 1:
            score += 4

        if mcap > 50000e7:
            score += 10
        elif mcap > 10000e7:
            score += 7
        elif mcap > 1000e7:
            score += 4

    return min(score, 100)


def stock_ai_advisory(symbol: str, user_id: Optional[int] = None) -> str:
    sym = symbol.upper().strip()
    try:
        ticker = yf.Ticker(f"{sym}.NS")
        df = ticker.history(period="1y", interval="1d")

        if df.empty:
            return f"❌ No data found for {sym}. Please check the symbol and try again."

        close = df["Close"]
        if len(close) < 60:
            return f"❌ Insufficient history for {sym}. Need at least 60 days of data."

        ltp = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(df) > 1 else ltp

        fund = get_fundamental_info(sym)
        company = fund.get("company_name", sym)

        ema20 = ema(close, 20).iloc[-1]
        ema50 = ema(close, 50).iloc[-1]
        ema200 = ema(close, 200).iloc[-1]
        rsi_val = float(rsi(close, 14).iloc[-1])
        macd_val, sig_val = macd(close)
        bb_up, bb_mid, bb_lo = bollinger_bands(close)
        atr_val = atr(df)
        trend = "Bullish" if ltp > ema200 else "Bearish"

        targets = calculate_targets(
            ltp,
            atr_val,
            trend,
            low_52w=fund.get("low_52w"),
            high_52w=fund.get("high_52w"),
        )
        quality = calculate_quality_score(df, fund)

        ai_comment = "AI service not configured."
        if user_id is not None:
            prompt = (
                f"You are an equity analyst for Indian markets. Write a brief max 180 words commentary "
                f"for retail traders on {company} ({sym}, NSE).\n\n"
                f"LTP: {ltp:.2f}, Prev Close: {prev:.2f}\n"
                f"RSI(14): {rsi_val:.1f}\n"
                f"MACD: {macd_val:.2f} vs Signal: {sig_val:.2f}\n"
                f"EMA20: {ema20:.2f}, EMA50: {ema50:.2f}, EMA200: {ema200:.2f}\n"
                f"Bollinger: U{bb_up:.2f}, M{bb_mid:.2f}, L{bb_lo:.2f}\n"
                f"ATR(14): {atr_val:.2f}, Trend: {trend}\n"
                f"P/E: {fund.get('pe_ratio', 0):.2f}, P/B: {fund.get('pb_ratio', 0):.2f}, "
                f"ROE: {fund.get('roe', 0):.1f}%, Div Yield: {fund.get('dividend_yield', 0):.2f}%\n"
                f"Quality score: {quality}/100\n\n"
                f"Explain trend, momentum, risk, support/resistance, entry thinking, and stop loss."
            )
            ai_comment = call_llm_with_limits(user_id, prompt, item_type="stock_ai")

        stars = "⭐" * (quality // 20) + "☆" * (5 - quality // 20)

        return f"""📊 DEEP ANALYSIS: {sym}
━━━━━━━━━━━━━━━━━━━━
🏢 {company}
🏭 Sector: {fund.get('sector', 'N/A')} | Industry: {fund.get('industry', 'N/A')}
💰 LTP: ₹{ltp:.2f} (Prev: ₹{prev:.2f})
📈 52W Range: ₹{fund.get('low_52w', 0):.2f} - ₹{fund.get('high_52w', 0):.2f}
📊 Volume: {fund.get('volume', 0):,} | Avg: {fund.get('avg_volume', 0):,}

━━━━━━━━━━━━━━━━━━━━
📊 FUNDAMENTALS
🏦 MCap: ₹{fund.get('market_cap', 0) / 10000000:.1f} Cr
📈 P/E: {fund.get('pe_ratio', 0):.2f} | P/B: {fund.get('pb_ratio', 0):.2f}
📊 ROE: {fund.get('roe', 0):.1f}% | Div Yield: {fund.get('dividend_yield', 0):.2f}%

━━━━━━━━━━━━━━━━━━━━
📌 TECHNICALS
RSI(14): {rsi_val:.1f} | MACD: {macd_val:.2f} vs Signal: {sig_val:.2f}
BB: U{bb_up:.2f} | M{bb_mid:.2f} | L{bb_lo:.2f}
EMA20: {ema20:.2f} | EMA50: {ema50:.2f} | EMA200: {ema200:.2f}
ATR(14): {atr_val:.2f} | Trend vs 200EMA: {trend}

━━━━━━━━━━━━━━━━━━━━
🎯 PRICE TARGETS
Short-term (1W/1M/3M): ₹{targets['short_term']['1W']:.2f} / ₹{targets['short_term']['1M']:.2f} / ₹{targets['short_term']['3M']:.2f}
Long-term (6M/1Y/2Y): ₹{targets['long_term']['6M']:.2f} / ₹{targets['long_term']['1Y']:.2f} / ₹{targets['long_term']['2Y']:.2f}
🛑 Stop Loss: ₹{targets['stop_loss']:.2f}

━━━━━━━━━━━━━━━━━━━━
📊 QUALITY SCORE: {quality}/100 {stars}

━━━━━━━━━━━━━━━━━━━━
🤖 AI COMMENTARY
{ai_comment}

━━━━━━━━━━━━━━━━━━━━
⚠️ Educational purpose only."""
    except Exception as e:
        logger.exception(f"Critical error in stock_ai_advisory for {symbol}")
        return f"❌ Unable to analyze {symbol} at this time. Please try again later.\n\nError: {str(e)}"


@bot.message_handler(commands=["start", "help"])
def start_cmd(message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🔍 Stock Analysis"))
    bot.send_message(
        message.chat.id,
        "🤖 AI Stock Advisor Pro\n\n"
        "• Stock Analysis: detailed tech + fundamental + AI\n\n"
        "Send NSE symbol when prompted (e.g. RELIANCE, TCS).",
        reply_markup=kb,
    )


@bot.message_handler(func=lambda m: m.text == "🔍 Stock Analysis")
def ask_symbol(message):
    msg = bot.reply_to(message, "📝 Send NSE symbol (e.g. RELIANCE, TCS):")
    bot.register_next_step_handler(msg, process_symbol)


def process_symbol(message):
    sym = message.text.strip().upper()
    if not all(c.isalnum() or c in "-&." for c in sym):
        bot.reply_to(message, "❌ Invalid symbol. Use letters, numbers, -, &, . only.")
        return

    bot.send_chat_action(message.chat.id, "typing")
    analysis = stock_ai_advisory(sym, user_id=message.from_user.id)
    bot.reply_to(message, analysis)


if __name__ == "__main__":
    logger.info("Bot starting...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=20)
