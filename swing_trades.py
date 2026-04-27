"""
swing_trades.py — Swing Trade Scanner (v5.1 Fixed)

FIXES:
  1. Condition strings with < > now HTML-escaped (eaten by Telegram HTML mode)
  2. Rich trade card: Entry Zone, R:R ratio, full conditions list, volume in Lakhs
  3. ai_call() routed through ai_engine._call_ai (single provider pipeline)
"""

import os
import logging
from datetime import date
import pandas as pd

logger = logging.getLogger(__name__)

from data_engine import get_hist
from technical_indicators import (
    calc_rsi, calc_ema, calc_macd, calc_atr, calc_adx, calc_bollinger,
    ema_series, rsi_series,
)
from config import (
    RSI_PERIOD, ADX_PERIOD, ATR_PERIOD,
    HIST_PERIOD_SWING, TG_CHUNK_SIZE,
)

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    import google.generativeai as genai
except ImportError:
    genai = None


def safe_history(ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    sym = ticker.replace(".NS", "").replace(".NSE", "")
    return get_hist(sym, period=period)


def swing_score(df: pd.DataFrame, side: str = "LONG") -> dict:
    if df.empty or len(df) < 100:
        return {"score": 0, "details": [], "ltp": None}

    close = df["Close"]
    ltp   = float(close.iloc[-1])
    n     = len(close)

    ema50   = float(ema_series(close, min(50,  n-1)).iloc[-1])
    ema200  = float(ema_series(close, min(200, n-1)).iloc[-1])
    bb_mid, bb_upper, bb_lower = calc_bollinger(close, 20, 2)
    adx_last, plus_di_last, minus_di_last = calc_adx(df, ADX_PERIOD)



    rsi_val       = calc_rsi(close, RSI_PERIOD)
    macd_last, signal_last, _ = calc_macd(close)
    vol_avg     = float(df["Volume"].rolling(20).mean().iloc[-1])
    vol_last    = float(df["Volume"].iloc[-1])
    recent_high = float(close.rolling(20).max().iloc[-1])
    recent_low  = float(close.rolling(20).min().iloc[-1])

    conditions = []
    score      = 0

    # FIX: < > replaced with HTML entities so Telegram does not strip them
    if side == "LONG":
        if ltp > ema50 > ema200:
            conditions.append("Trend: price &gt; 50EMA &gt; 200EMA"); score += 1
        if bb_lower < ltp < bb_mid:
            conditions.append("BB: price in lower-mid band"); score += 1
        if adx_last > 25 and plus_di_last > minus_di_last:
            conditions.append(f"ADX {adx_last:.1f} &gt; 25, +DI &gt; -DI"); score += 1
        if vol_last > vol_avg:
            conditions.append(f"Volume {vol_last/1e5:.1f}L &gt; avg {vol_avg/1e5:.1f}L"); score += 1
        if 40 < rsi_val < 70:
            conditions.append(f"RSI {rsi_val:.1f} in 40–70 zone"); score += 1
        if macd_last > signal_last:
            conditions.append("MACD above signal line"); score += 1
        if ltp > recent_high * 0.97:
            conditions.append("Near 20-day high (breakout zone)"); score += 1
        if abs(ltp - ema50) / ema50 < 0.03:
            conditions.append("Hugging 50EMA support"); score += 1
    else:  # SHORT
        if ltp < ema50 < ema200:
            conditions.append("Trend: price &lt; 50EMA &lt; 200EMA"); score += 1
        if bb_mid < ltp < bb_upper:
            conditions.append("BB: price in mid-upper band"); score += 1
        if adx_last > 25 and minus_di_last > plus_di_last:
            conditions.append(f"ADX {adx_last:.1f} &gt; 25, -DI &gt; +DI"); score += 1
        if vol_last > vol_avg:
            conditions.append(f"Volume {vol_last/1e5:.1f}L &gt; avg {vol_avg/1e5:.1f}L"); score += 1
        if 30 < rsi_val < 60:
            conditions.append(f"RSI {rsi_val:.1f} in 30–60 zone"); score += 1
        if macd_last < signal_last:
            conditions.append("MACD below signal line"); score += 1
        if ltp < recent_low * 1.03:
            conditions.append("Near 20-day low (breakdown zone)"); score += 1
        if abs(ltp - ema50) / ema50 < 0.03:
            conditions.append("Near 50EMA resistance"); score += 1

    h, l, c = df["High"], df["Low"], df["Close"]
    tr      = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr_val = float(tr.rolling(14).mean().iloc[-1])

    return {
        "score": score, "details": conditions, "ltp": ltp,
        "ema50": ema50, "ema200": ema200, "adx": adx_last,
        "rsi": rsi_val, "macd": macd_last, "signal": signal_last,
        "volume": vol_last, "avg_volume": vol_avg,
        "bb_mid": bb_mid, "bb_upper": bb_upper, "bb_lower": bb_lower,
        "recent_high": recent_high, "recent_low": recent_low, "atr_val": atr_val,
    }


def ai_call(prompt: str, max_tokens: int = 400) -> str:
    try:
        from ai_engine import _call_ai
        text, _ = _call_ai(
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            system="You are a concise Indian equity swing analyst. Use numbers.",
        )
        return text or ""
    except Exception as e:
        logger.warning(f"ai_call swing: {e}")
        return ""


CANDIDATES = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS", "LT.NS",
    "WIPRO.NS", "HCLTECH.NS", "ASIANPAINT.NS", "MARUTI.NS", "TATAMOTORS.NS",
    "TITAN.NS", "SUNPHARMA.NS", "ONGC.NS", "NTPC.NS", "M&M.NS",
    "BAJFINANCE.NS", "AXISBANK.NS", "TECHM.NS", "DRREDDY.NS", "DIVISLAB.NS",
    "HINDALCO.NS", "JSWSTEEL.NS", "TATASTEEL.NS", "BPCL.NS", "EICHERMOT.NS",
]


def _display_sym(sym: str) -> str:
    return sym.replace(".NS", "")


def _trade_card(p: dict, side: str) -> str:
    sym     = _display_sym(p["symbol"])
    ltp     = p["ltp"]
    score   = p["score"]
    atr_val = p.get("atr_val") or ltp * 0.02
    rsi_v   = round(p["rsi"], 1)
    adx_v   = round(p["adx"], 1)

    entry_lo = round(ltp * 0.995, 2)
    entry_hi = round(ltp * 1.005, 2)

    if side == "LONG":
        sl      = round(ltp - 2.0 * atr_val, 2)
        tgt1    = round(ltp + 2.0 * atr_val, 2)
        tgt2    = round(ltp + 4.0 * atr_val, 2)
        risk    = round(ltp - sl, 2)
        reward  = round(tgt1 - ltp, 2)
        icon    = "🟢"
    else:
        sl      = round(ltp + 2.0 * atr_val, 2)
        tgt1    = round(ltp - 2.0 * atr_val, 2)
        tgt2    = round(ltp - 4.0 * atr_val, 2)
        risk    = round(sl - ltp, 2)
        reward  = round(ltp - tgt1, 2)
        icon    = "🔴"

    rr = round(reward / risk, 1) if risk > 0 else 0
    conds = "\n".join(f"      · {c}" for c in p["details"])

    return (
        f"{icon} <b>{sym}</b> [{side}]  Score: <b>{score}/8</b>\n"
        f"   LTP: ₹{ltp:,.2f}  |  RSI: {rsi_v}  |  ADX: {adx_v}\n"
        f"   📥 Entry Zone : ₹{entry_lo:,.2f} – ₹{entry_hi:,.2f}\n"
        f"   🎯 Target 1   : ₹{tgt1:,.2f}\n"
        f"   🎯 Target 2   : ₹{tgt2:,.2f}\n"
        f"   🛑 Stop Loss  : ₹{sl:,.2f}\n"
        f"   ⚖️  Risk:Reward: 1:{rr}\n"
        f"   ✅ Conditions:\n{conds}"
    )


def get_swing_trades(mode: str = "conservative") -> str:
    threshold = 6 if mode == "conservative" else 5
    today     = date.today().strftime("%d-%b-%Y")
    long_picks, short_picks = [], []

    for sym in CANDIDATES:
        try:
            df = safe_history(sym, period="1y", interval="1d")
            if df.empty or len(df) < 200:
                continue
            for side in ["LONG", "SHORT"]:
                result = swing_score(df, side)
                if result["score"] >= threshold:
                    result["symbol"] = sym
                    result["side"]   = side
                    (long_picks if side == "LONG" else short_picks).append(result)
        except Exception as e:
            logger.warning(f"swing {sym}: {e}")

    long_picks.sort( key=lambda x: x["score"], reverse=True)
    short_picks.sort(key=lambda x: x["score"], reverse=True)

    label = "Conservative (6+/8)" if mode == "conservative" else "Aggressive (5+/8)"
    lines = [
        f"📈 <b>Swing Trades — {label}</b>",
        f"📅 {today}  |  Threshold: {threshold}/8  |  Universe: {len(CANDIDATES)} stocks\n",
    ]

    if not long_picks and not short_picks:
        all_r = []
        for sym in CANDIDATES:
            try:
                df = safe_history(sym, period="1y", interval="1d")
                if df.empty or len(df) < 100: continue
                for side in ["LONG", "SHORT"]:
                    r = swing_score(df, side)
                    if r["ltp"]:
                        r["symbol"] = sym; r["side"] = side
                        all_r.append(r)
            except Exception:
                continue
        all_r.sort(key=lambda x: x["score"], reverse=True)
        lines.append(f"⚠️ No setups met {threshold}/8 threshold today.\n")
        lines.append("📊 <b>Watch List (closest to qualifying):</b>")
        for p in all_r[:5]:
            lines.append(
                f"  • <b>{_display_sym(p['symbol'])}</b> ({p['side']}) "
                f"Score:{p['score']}/8 | ₹{p['ltp']:.2f} | RSI:{round(p['rsi'],1)}"
            )
        lines.append("\n⚠️ Educational only. Not SEBI-registered advice.")
        return "\n".join(lines)

    if long_picks:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🟢 <b>LONG SETUPS</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        for p in long_picks[:5]:
            lines.append(_trade_card(p, "LONG"))
            lines.append("")

    if short_picks:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔴 <b>SHORT SETUPS</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        for p in short_picks[:5]:
            lines.append(_trade_card(p, "SHORT"))
            lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚠️ <i>Educational only. Not SEBI-registered advice.</i>")
    return "\n".join(lines)
