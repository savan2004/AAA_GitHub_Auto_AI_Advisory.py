# main.py – Fresh core for AI Stock Advisor

import os
import time
import logging
from datetime import date, datetime
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from groq import Groq
import google.genai as genai

# -------------------- CONFIG --------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")

# Tier limits
TIER_LIMITS = {
    "free": 50,
    "paid": 200,
}

FRESHNESS_SECONDS = 3600  # 1 hour

# -------------------- AI CLIENTS --------------------
genai_client = None
if GEMINI_API_KEY:
    try:
        genai_client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("Gemini configured successfully")
    except Exception as e:
        logger.error(f"Failed to configure Gemini: {e}")

# -------------------- USAGE & HISTORY --------------------
usage_store: Dict[int, Dict] = {}
history_store: Dict[int, List[Dict]] = defaultdict(list)


def get_today_str() -> str:
    return date.today().isoformat()


def can_use_llm(user_id: int) -> Tuple[bool, int, int]:
    record = usage_store.get(user_id)
    today = get_today_str()

    if record is None:
        tier = "free"
        limit = TIER_LIMITS[tier]
        usage_store[user_id] = {"date": today, "calls": 0, "tier": tier}
        return True, limit, limit

    if record["date"] != today:
        record["date"] = today
        record["calls"] = 0
        limit = TIER_LIMITS[record["tier"]]
        return True, limit, limit

    limit = TIER_LIMITS[record["tier"]]
    remaining = limit - record["calls"]
    return remaining > 0, remaining, limit


def register_llm_usage(user_id: int) -> None:
    record = usage_store.get(user_id)
    if record:
        record["calls"] += 1
    else:
        usage_store[user_id] = {"date": get_today_str(), "calls": 1, "tier": "free"}


def add_history_item(user_id: int, prompt: str, response: str, item_type: str = "analysis") -> int:
    item_id = int(time.time())
    item = {
        "id": item_id,
        "timestamp": item_id,
        "prompt": prompt,
        "response": response,
        "type": item_type,
    }
    history_store[user_id].append(item)
    if len(history_store[user_id]) > 20:
        history_store[user_id] = history_store[user_id][-20:]
    return item_id


# -------------------- SAFE LLM CALL --------------------
def safe_llm_call(prompt: str, max_tokens: int = 600) -> Tuple[bool, str]:
    # Try Groq first
    if GROQ_API_KEY:
        try:
            client = Groq(api_key=GROQ_API_KEY)
            models_to_try = ["llama3-8b-8192", "mixtral-8x7b-32768", "gemma2-9b-it"]
            for model in models_to_try:
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                        temperature=0.35,
                        timeout=10,
                    )
                    if resp and resp.choices:
                        return True, (resp.choices[0].message.content or "").strip()
                except Exception as e:
                    logger.warning(f"Groq model {model} failed: {e}")
            logger.warning("All Groq models failed")
        except Exception as e:
            logger.error(f"Groq client error: {e}")

    # Try Gemini
    if genai_client:
        try:
            gemini_models = [
                "models/gemini-1.5-pro",
                "models/gemini-1.5-flash",
                "gemini-1.5-pro",
                "gemini-1.5-flash",
            ]
            for model_name in gemini_models:
                try:
                    resp = genai_client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config={"max_output_tokens": max_tokens, "temperature": 0.35},
                    )
                    if resp and getattr(resp, "text", None):
                        return True, resp.text.strip()
                except Exception as e:
                    logger.warning(f"Gemini model {model_name} failed: {e}")
        except Exception as e:
            logger.error(f"Gemini client error: {e}")

    return False, ""


def call_llm_with_limits(user_id: int, prompt: str, item_type: str = "analysis") -> str:
    allowed, remaining, limit = can_use_llm(user_id)
    if not allowed:
        return (
            f"❌ You've used all {limit} AI analyses for today.\n\n"
            f"Please try again tomorrow or upgrade to Pro (200 calls/day)."
        )

    success, response = safe_llm_call(prompt)
    if not success:
        return (
            "⚠️ AI service temporarily unavailable. Your quota was not used.\n\n"
            "Technical analysis is still provided below."
        )

    register_llm_usage(user_id)
    add_history_item(user_id, prompt, response, item_type)
    if remaining - 1 <= 3:
        response += f"\n\n⚠️ You have {remaining-1} AI calls left today."
    return response


# -------------------- INDICATORS & FUNDAMENTALS --------------------
def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).rolling(period).mean()
    down = (-d.clip(upper=0)).rolling(period).mean()
    rs = up / down
    return 100 - (100 / (1 + rs))


def macd(s: pd.Series) -> Tuple[float, float]:
    exp1 = s.ewm(span=12, adjust=False).mean()
    exp2 = s.ewm(span=26, adjust=False).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1])


def bollinger_bands(s: pd.Series, period: int = 20):
    sma = s.rolling(window=period).mean().iloc[-1]
    std = s.rolling(window=period).std().iloc[-1]
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
        info = ticker.info
        return {
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "company_name": info.get("longName", info.get("shortName", symbol)),
            "market_cap": info.get("marketCap", 0),
            "pe_ratio": info.get("trailingPE", 0),
            "pb_ratio": info.get("priceToBook", 0),
            "roe": info.get("returnOnEquity", 0) * 100 if info.get("returnOnEquity") else 0,
            "dividend_yield": info.get("dividendYield", 0) * 100 if info.get("dividendYield") else 0,
            "high_52w": info.get("fiftyTwoWeekHigh", 0),
            "low_52w": info.get("fiftyTwoWeekLow", 0),
            "prev_close": info.get("regularMarketPreviousClose", 0),
            "volume": info.get("volume", 0),
            "avg_volume": info.get("averageVolume", 0),
        }
    except Exception as e:
        logger.error(f"Fundamental error for {symbol}: {e}")
        return {}


def calculate_targets(price: float, atr_val: float, trend: str,
                      low_52w: Optional[float] = None,
                      high_52w: Optional[float] = None) -> dict:
    if trend == "Bullish":
        short = {
            "1W": price + atr_val * 1.2,
            "1M": price + atr_val * 3,
            "3M": price + atr_val * 6,
        }
        long = {
            "6M": price + atr_val * 12,
            "1Y": price + atr_val * 20,
            "2Y": price + atr_val * 35,
        }
        sl = price - atr_val * 2
        if high_52w:
            cap = high_52w * 2
            for k in long:
                if long[k] > cap:
                    long[k] = cap
    else:
        short = {
            "1W": price - atr_val * 1.2,
            "1M": price - atr_val * 3,
            "3M": price - atr_val * 6,
        }
        long = {
            "6M": price - atr_val * 10,
            "1Y": price - atr_val * 15,
            "2Y": price - atr_val * 20,
        }
        sl = price + atr_val * 2
        floor = price * 0.1
        for k in short:
            short[k] = max(short[k], floor)
        for k in long:
            long[k] = max(long[k], floor)
        if low_52w:
            for k in long:
                if long[k] < low_52w * 0.9:
                    long[k] = low_52w * 0.9
    return {"short_term": short, "long_term": long, "stop_loss": sl}


def calculate_quality_score(df: pd.DataFrame, fund: dict) -> int:
    close = df["Close"]
    score = 0

    # Trend (15)
    ema20 = ema(close, 20).iloc[-1]
    ema50 = ema(close, 50).iloc[-1]
    ema200 = ema(close, 200).iloc[-1]
    if close.iloc[-1] > ema20:
        score += 4
    if close.iloc[-1] > ema50:
        score += 5
    if close.iloc[-1] > ema200:
        score += 6

    # RSI (10)
    rsi_val = rsi(close, 14).iloc[-1]
    if 40 <= rsi_val <= 60:
        score += 10
    elif 30 <= rsi_val <= 70:
        score += 5

    # Volume (5)
    vol_avg = df["Volume"].rolling(20).mean().iloc[-1]
    if df["Volume"].iloc[-1] > vol_avg * 1.5:
        score += 5
    elif df["Volume"].iloc[-1] > vol_avg:
        score += 3

    # ATR stability (10)
    atr_val = atr(df)
    atr_pct = (atr_val / close.iloc[-1]) * 100
    if atr_pct < 2:
        score += 10
    elif atr_pct < 4:
        score += 7
    elif atr_pct < 6:
        score += 4

    # Fundamentals (60)
    if fund:
        pe = fund.get("pe_ratio", 0)
        if pe and pe < 20:
            score += 15
        elif pe and pe < 30:
            score += 10
        elif pe and pe < 40:
            score += 5

        roe = fund.get("roe", 0)
        if roe > 20:
            score += 15
        elif roe > 15:
            score += 12
        elif roe > 10:
            score += 8
        elif roe > 5:
            score += 4

        pb = fund.get("pb_ratio", 0)
        if 1 < pb < 3:
            score += 10
        elif pb <= 1:
            score += 8
        elif pb < 5:
            score += 5

        div = fund.get("dividend_yield", 0)
        if div > 3:
            score += 10
        elif div > 2:
            score += 7
        elif div > 1:
            score += 4

        mcap = fund.get("market_cap", 0)
        if mcap > 50000e7:
            score += 10
        elif mcap > 10000e7:
            score += 7
        elif mcap > 1000e7:
            score += 4

    return min(score, 100)


# -------------------- STOCK ANALYSIS + AI --------------------
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
            ltp, atr_val, trend,
            low_52w=fund.get("low_52w"),
            high_52w=fund.get("high_52w"),
        )
        quality = calculate_quality_score(df, fund)

        # AI commentary
        ai_comment = "AI service not configured."
        if user_id is not None:
            prompt = (
                f"You are an equity analyst for Indian markets. Write a brief (max 180 words) "
                f"commentary for retail traders on {company} ({sym}, NSE).\n\n"
                f"Data:\n"
                f"LTP: {ltp:.2f}, Prev Close: {prev:.2f}\n"
                f"RSI(14): {rsi_val:.1f}\n"
                f"MACD: {macd_val:.2f} vs Signal: {sig_val:.2f}\n"
                f"EMA20: {ema20:.2f}, EMA50: {ema50:.2f}, EMA200: {ema200:.2f}\n"
                f"Bollinger: U{bb_up:.2f}, M{bb_mid:.2f}, L{bb_lo:.2f}\n"
                f"ATR(14): {atr_val:.2f}, Trend vs 200EMA: {trend}\n"
                f"P/E: {fund.get('pe_ratio', 0):.2f}, P/B: {fund.get('pb_ratio', 0):.2f}, "
                f"ROE: {fund.get('roe', 0):.1f}%, Div Yield: {fund.get('dividend_yield', 0):.2f}%\n"
                f"Quality score: {quality}/100\n\n"
                "Explain trend, momentum, risk, support/resistance zones and how a cautious swing trader should think about entries and stop loss."
            )
            ai_comment = call_llm_with_limits(user_id, prompt, item_type="stock_ai")

        stars = "⭐" * (quality // 20) + "☆" * (5 - quality // 20)

        output = f"""📊 DEEP ANALYSIS: {sym}
━━━━━━━━━━━━━━━━━━━━
🏢 {company}
🏭 Sector: {fund.get('sector','N/A')} | Industry: {fund.get('industry','N/A')}
💰 LTP: ₹{ltp:.2f} (Prev: ₹{prev:.2f})
📈 52W Range: ₹{fund.get('low_52w',0):.2f} - ₹{fund.get('high_52w',0):.2f}
📊 Volume: {fund.get('volume',0):,} | Avg: {fund.get('avg_volume',0):,}

━━━━━━━━━━━━━━━━━━━━
📊 FUNDAMENTALS
🏦 MCap: ₹{fund.get('market_cap',0)/10000000:.1f} Cr
📈 P/E: {fund.get('pe_ratio',0):.2f} | P/B: {fund.get('pb_ratio',0):.2f}
📊 ROE: {fund.get('roe',0):.1f}% | Div Yield: {fund.get('dividend_yield',0):.2f}%

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
        return output

    except Exception as e:
        logger.exception(f"Critical error in stock_ai_advisory for {symbol}")
        return (
            f"❌ Unable to analyze {symbol} at this time. "
            f"Please try again later.\n\nError: {str(e)}"
        )


# -------------------- TELEGRAM HANDLERS --------------------
@bot.message_handler(commands=["start", "help"])
def start_cmd(m):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🔍 Stock Analysis"))
    bot.send_message(
        m.chat.id,
        "🤖 AI Stock Advisor Pro\n\n"
        "• Stock Analysis: detailed tech + fundamental + AI\n\n"
        "Send NSE symbol when prompted (e.g. RELIANCE, TCS).",
        reply_markup=kb,
    )


@bot.message_handler(func=lambda m: m.text == "🔍 Stock Analysis")
def ask_symbol(m):
    msg = bot.reply_to(m, "📝 Send NSE symbol (e.g. RELIANCE, TCS):")
    bot.register_next_step_handler(msg, process_symbol)


def process_symbol(m):
    sym = m.text.strip().upper()
    if not all(c.isalnum() or c in "-&." for c in sym):
        bot.reply_to(m, "❌ Invalid symbol. Use letters only.")
        return

    bot.send_chat_action(m.chat.id, "typing")
    analysis = stock_ai_advisory(sym, user_id=m.from_user.id)
    bot.reply_to(m, analysis)


if __name__ == "__main__":
    logger.info("Bot starting...")
    bot.infinity_polling()
