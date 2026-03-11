# main.py – Advanced AI Stock Advisor with CMP verification + RAG

import os
import logging
from datetime import date
from typing import Optional, Tuple, Dict

import requests
import pandas as pd
import yfinance as yf
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

from llm_wrapper import call_llm_with_limits  # Groq + Gemini with limits

# -------------------- CONFIG --------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")


# -------------------- CMP VERIFICATION (multi-source) --------------------
def _fetch_price_yfinance(symbol: str) -> Optional[float]:
    try:
        t = yf.Ticker(f"{symbol}.NS")
        info = t.info or {}
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if price and price > 0:
            return float(price)
    except Exception as e:
        logger.warning(f"YFinance price error for {symbol}: {e}")
    return None


def _fetch_price_alpha_vantage(symbol: str) -> Optional[float]:
    if not ALPHA_VANTAGE_KEY:
        return None
    try:
        params = {
            "function": "GLOBAL_QUOTE",
            "symbol": f"{symbol}.NS",
            "apikey": ALPHA_VANTAGE_KEY,
        }
        r = requests.get("https://www.alphavantage.co/query", params=params, timeout=10)
        data = r.json().get("Global Quote", {})
        price_str = data.get("05. price")
        if price_str:
            price = float(price_str)
            if price > 0:
                return price
    except Exception as e:
        logger.warning(f"AlphaVantage price error for {symbol}: {e}")
    return None


def _fetch_price_finnhub(symbol: str) -> Optional[float]:
    if not FINNHUB_API_KEY:
        return None
    try:
        params = {"symbol": f"{symbol}.NS", "token": FINNHUB_API_KEY}
        r = requests.get("https://finnhub.io/api/v1/quote", params=params, timeout=10)
        data = r.json()
        price = data.get("c")
        if price and price > 0:
            return float(price)
    except Exception as e:
        logger.warning(f"Finnhub price error for {symbol}: {e}")
    return None


def get_verified_price(symbol: str) -> Tuple[Optional[float], Dict[str, float]]:
    """
    Fetch CMP from multiple providers and return a consensus price + per-source map.
    """
    sources: Dict[str, float] = {}

    yf_price = _fetch_price_yfinance(symbol)
    if yf_price:
        sources["yfinance"] = yf_price

    av_price = _fetch_price_alpha_vantage(symbol)
    if av_price:
        sources["alpha_vantage"] = av_price

    fh_price = _fetch_price_finnhub(symbol)
    if fh_price:
        sources["finnhub"] = fh_price

    if not sources:
        return None, {}

    vals = sorted(sources.values())
    n = len(vals)
    if n % 2 == 1:
        consensus = vals[n // 2]
    else:
        consensus = (vals[n // 2 - 1] + vals[n // 2]) / 2

    return consensus, sources


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


def bollinger_bands(s: pd.Series, period: int = 20) -> Tuple[float, float, float]:
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


def calculate_targets(
    price: float,
    atr_val: float,
    trend: str,
    low_52w: Optional[float] = None,
    high_52w: Optional[float] = None,
) -> dict:
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
                long[k] = min(long[k], cap)
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
                long[k] = max(long[k], low_52w * 0.9)

    return {"short_term": short, "long_term": long, "stop_loss": sl}


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


# -------------------- RAG CONTEXT + FALLBACK COMMENTARY --------------------
def build_rag_context(
    symbol: str,
    company: str,
    ltp_hist: float,
    prev: float,
    verified_price: Optional[float],
    price_sources: Dict[str, float],
    fund: dict,
    ema20: float,
    ema50: float,
    ema200: float,
    rsi_val: float,
    macd_val: float,
    sig_val: float,
    bb_up: float,
    bb_mid: float,
    bb_lo: float,
    atr_val: float,
    trend: str,
    targets: dict,
    quality: int,
) -> str:
    lines = []
    lines.append(f"SYMBOL: {symbol}.NS")
    lines.append(f"COMPANY: {company}")
    lines.append(f"LTP_HISTORY: {ltp_hist:.2f}")
    if verified_price is not None:
        lines.append(f"LTP_VERIFIED: {verified_price:.2f}")
    if price_sources:
        src_str = ", ".join(f"{k}={v:.2f}" for k, v in price_sources.items())
        lines.append(f"PRICE_SOURCES: {src_str}")
    lines.append(f"PREV_CLOSE: {prev:.2f}")
    lines.append("")
    lines.append("FUNDAMENTALS:")
    lines.append(f"  Sector: {fund.get('sector','N/A')}")
    lines.append(f"  Industry: {fund.get('industry','N/A')}")
    lines.append(f"  MCap_Cr: {fund.get('market_cap',0)/1e7:.1f}")
    lines.append(f"  PE: {fund.get('pe_ratio',0):.2f}")
    lines.append(f"  PB: {fund.get('pb_ratio',0):.2f}")
    lines.append(f"  ROE_pct: {fund.get('roe',0):.1f}")
    lines.append(f"  DivYield_pct: {fund.get('dividend_yield',0):.2f}")
    lines.append(f"  52W_low: {fund.get('low_52w',0):.2f}")
    lines.append(f"  52W_high: {fund.get('high_52w',0):.2f}")
    lines.append("")
    lines.append("TECHNICALS:")
    lines.append(f"  RSI14: {rsi_val:.1f}")
    lines.append(f"  MACD: {macd_val:.2f}, Signal: {sig_val:.2f}")
    lines.append(f"  EMA20: {ema20:.2f}, EMA50: {ema50:.2f}, EMA200: {ema200:.2f}")
    lines.append(f"  BB: U{bb_up:.2f}, M{bb_mid:.2f}, L{bb_lo:.2f}")
    lines.append(f"  ATR14: {atr_val:.2f}")
    lines.append(f"  Trend_vs_200EMA: {trend}")
    lines.append("")
    lines.append("TARGETS:")
    lines.append(
        f"  ST_1W: {targets['short_term']['1W']:.2f}, "
        f"1M: {targets['short_term']['1M']:.2f}, "
        f"3M: {targets['short_term']['3M']:.2f}"
    )
    lines.append(
        f"  LT_6M: {targets['long_term']['6M']:.2f}, "
        f"1Y: {targets['long_term']['1Y']:.2f}, "
        f"2Y: {targets['long_term']['2Y']:.2f}"
    )
    lines.append(f"  StopLoss: {targets['stop_loss']:.2f}")
    lines.append("")
    lines.append(f"QUALITY_SCORE: {quality}/100")
    return "\n".join(lines)


def rule_based_commentary(
    symbol: str,
    company: str,
    ltp: float,
    prev: float,
    rsi_val: float,
    macd_val: float,
    sig_val: float,
    ema20: float,
    ema50: float,
    ema200: float,
    bb_up: float,
    bb_mid: float,
    bb_lo: float,
    atr_val: float,
    trend: str,
    quality: int,
) -> str:
    direction = "up" if ltp > prev else "down" if ltp < prev else "flat"
    rsi_note = (
        "overbought" if rsi_val > 70 else
        "oversold" if rsi_val < 30 else
        "neutral"
    )
    macd_note = "bullish" if macd_val > sig_val else "bearish"
    vol_note = "stable volatility"
    if ltp > 0 and atr_val / ltp * 100 > 5:
        vol_note = "high volatility"

    lines = []
    lines.append(
        f"{company} ({symbol}.NS) is currently trading {direction} versus the previous close, "
        f"with RSI in a {rsi_note} zone and MACD giving a {macd_note} signal around the current trend."
    )
    lines.append(
        f"Price action relative to EMA20/50/200 suggests a {trend.lower()} bias, while Bollinger Bands "
        f"(U{bb_up:.0f}, M{bb_mid:.0f}, L{bb_lo:.0f}) indicate {vol_note} near the current level."
    )
    lines.append(
        f"The internal quality score of {quality}/100 reflects a blend of fundamentals and trend strength; "
        f"treat this as a risk gauge, not a rating."
    )
    lines.append(
        "For cautious swing traders, consider entries closer to support with a clear stop below recent "
        "swing lows, and avoid oversizing positions around major news or results."
    )
    lines.append("Note: Educational example, not a recommendation.")
    return "\n".join(lines)


# -------------------- STOCK ANALYSIS + AI (RAG + ASI) --------------------
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

        ltp_hist = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(df) > 1 else ltp_hist

        fund = get_fundamental_info(sym)
        company = fund.get("company_name", sym)

        ema20 = ema(close, 20).iloc[-1]
        ema50 = ema(close, 50).iloc[-1]
        ema200 = ema(close, 200).iloc[-1]
        rsi_val = float(rsi(close, 14).iloc[-1])
        macd_val, sig_val = macd(close)
        bb_up, bb_mid, bb_lo = bollinger_bands(close)
        atr_val = atr(df)
        trend = "Bullish" if ltp_hist > ema200 else "Bearish"

        targets = calculate_targets(
            ltp_hist,
            atr_val,
            trend,
            low_52w=fund.get("low_52w"),
            high_52w=fund.get("high_52w"),
        )
        quality = calculate_quality_score(df, fund)

        verified_price, price_sources = get_verified_price(sym)
        ltp_for_display = verified_price if verified_price is not None else ltp_hist

        context = build_rag_context(
            sym,
            company,
            ltp_hist,
            prev,
            verified_price,
            price_sources,
            fund,
            ema20,
            ema50,
            ema200,
            rsi_val,
            macd_val,
            sig_val,
            bb_up,
            bb_mid,
            bb_lo,
            atr_val,
            trend,
            targets,
            quality,
        )

        ai_comment = "AI service not configured."
        if user_id is not None:
            prompt = (
                "You are an equity analyst for Indian markets (NSE). "
                "Use ONLY the structured data in the CONTEXT below. "
                "Do NOT invent prices or ratios; base your view strictly on this data.\n\n"
                "CONTEXT START\n"
                f"{context}\n"
                "CONTEXT END\n\n"
                "Task: Write a concise (max 180 words) swing-trading oriented commentary for a retail trader. "
                "Explain:\n"
                "- Overall trend and momentum\n"
                "- Key support/resistance zones\n"
                "- Risk factors and volatility\n"
                "- How a cautious swing trader can think about entries and stop loss\n"
                "Finish with: 'Note: Educational example, not a recommendation.'"
            )
            ai_comment = call_llm_with_limits(user_id, prompt, item_type="stock_ai")

            if (
                "AI service temporarily unavailable" in ai_comment
                or "AI engine" in ai_comment
                or "not configured" in ai_comment
            ):
                ai_comment = rule_based_commentary(
                    sym,
                    company,
                    ltp_for_display,
                    prev,
                    rsi_val,
                    macd_val,
                    sig_val,
                    ema20,
                    ema50,
                    ema200,
                    bb_up,
                    bb_mid,
                    bb_lo,
                    atr_val,
                    trend,
                    quality,
                )

        stars = "⭐" * (quality // 20) + "☆" * (5 - quality // 20)

        output = f"""📊 DEEP ANALYSIS: {sym}
━━━━━━━━━━━━━━━━━━━━
🏢 {company}
🏭 Sector: {fund.get('sector','N/A')} | Industry: {fund.get('industry','N/A')}
💰 LTP (verified): ₹{ltp_for_display:.2f} (Hist: ₹{ltp_hist:.2f}, Prev: ₹{prev:.2f})
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
